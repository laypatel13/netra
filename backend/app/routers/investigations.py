"""
API Router for Targeted Vehicle Investigations.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.routers.investigation_ingest_schemas import CandidateIngest, HeartbeatPayload, ObservationIngest, RecordingSessionIngest
from app.models import (
    InvestigationTarget,
    TargetStatus,
    TargetPriority,
    VehicleTrack,
    TrackStatus,
    TrackEvidence,
    PipelineHeartbeat,
    RouteChain,
    RouteChainObservation,
    RouteChainLink,
    CrossCameraLinkCandidate,
    EvidenceReview,
    EvidenceReviewAction,
    EvidenceReviewEntityType,
    InvestigationState,
    HumanReviewState,
    Mode,
    TimestampSource,
    TimestampQuality,
    VehicleObservation,
)
from app.investigation_service import process_new_observation, process_verification_event
from app.prediction_service import PredictionService
from app.evaluation_service import EvaluationService
from app.evaluation_data import seed_evaluation_data
from app.schemas import (
    InvestigationTargetCreate,
    InvestigationTargetRead,
    CandidateRead,
    EvidenceRead,
    VerificationAction,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("/targets", response_model=InvestigationTargetRead)
def create_target(target: InvestigationTargetCreate, db: Session = Depends(get_db)):
    """Create a new vehicle investigation target."""
    db_target = InvestigationTarget(
        plate_number=target.plate_number,
        vehicle_type=target.vehicle_type,
        vehicle_color=target.vehicle_color,
        type_required=1 if target.type_required else 0,
        color_required=1 if target.color_required else 0,
        make=target.make,
        description=target.description,
        category=target.category,
        priority=target.priority,
    )
    db.add(db_target)
    db.commit()
    db.refresh(db_target)
    return db_target


@router.get("/targets", response_model=List[InvestigationTargetRead])
def list_targets(
    status: TargetStatus = Query(None, description="Filter by status"),
    db: Session = Depends(get_db)
):
    """List all investigation targets."""
    query = db.query(InvestigationTarget)
    if status:
        query = query.filter(InvestigationTarget.status == status)
    return query.order_by(InvestigationTarget.created_at.desc()).all()


@router.get("/targets/{target_id}", response_model=InvestigationTargetRead)
def get_target(target_id: UUID, db: Session = Depends(get_db)):
    """Get a specific investigation target."""
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return target



from pydantic import BaseModel
class TargetStatusUpdate(BaseModel):
    status: TargetStatus

@router.patch("/targets/{target_id}/status")
def update_target_status(target_id: UUID, payload: TargetStatusUpdate, db: Session = Depends(get_db)):
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    target.status = payload.status
    if payload.status == TargetStatus.resolved:
        target.resolved_at = datetime.utcnow()
        
    db.commit()
    return {"status": "ok"}


@router.delete("/targets/{target_id}")
def delete_target(
    target_id: UUID,
    delete_data: bool = Query(False, description="Whether to also delete all associated evidence data"),
    db: Session = Depends(get_db)
):
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    tracks = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id).all()
    track_ids = [t.id for t in tracks]

    if delete_data:
        if track_ids:
            # Delete evidence frames and their physical files
            evidence_records = db.query(TrackEvidence).filter(TrackEvidence.track_id.in_(track_ids)).all()
            for ev in evidence_records:
                if ev.raw_path:
                    try:
                        os.remove(ev.raw_path)
                    except OSError:
                        pass
                if ev.enhanced_path:
                    try:
                        os.remove(ev.enhanced_path)
                    except OSError:
                        pass
                        
            db.query(TrackEvidence).filter(TrackEvidence.track_id.in_(track_ids)).delete(synchronize_session=False)
            # Unlink vehicle observations instead of deleting to avoid foreign key violations on cross-camera links
            db.query(VehicleObservation).filter(VehicleObservation.candidate_id.in_(track_ids)).update({"candidate_id": None}, synchronize_session=False)
            # Delete the tracks themselves
            db.query(VehicleTrack).filter(VehicleTrack.id.in_(track_ids)).delete(synchronize_session=False)
    else:
        # Just unlink the tracks by updating Python objects to ensure it flushes correctly
        for track in tracks:
            track.target_id = None
        db.flush()

    # Clean up InvestigationState
    db.query(InvestigationState).filter(InvestigationState.target_id == target_id).delete(synchronize_session=False)

    # Clean up RouteChain components
    chains = db.query(RouteChain).filter(RouteChain.target_id == target_id).all()
    chain_ids = [c.id for c in chains]
    if chain_ids:
        db.query(RouteChainLink).filter(RouteChainLink.route_chain_id.in_(chain_ids)).delete(synchronize_session=False)
        db.query(RouteChainObservation).filter(RouteChainObservation.route_chain_id.in_(chain_ids)).delete(synchronize_session=False)
        db.query(RouteChain).filter(RouteChain.id.in_(chain_ids)).delete(synchronize_session=False)

    # Finally delete the target
    db.delete(target)
    db.commit()
    return {"status": "ok", "deleted": True}


@router.post("/purge")
def purge_investigations(db: Session = Depends(get_db)):
    """Delete all investigation targets and their evidence."""
    # Clean up RouteChain components
    db.query(RouteChainLink).delete(synchronize_session=False)
    db.query(RouteChainObservation).delete(synchronize_session=False)
    db.query(RouteChain).delete(synchronize_session=False)
    
    # Clean up InvestigationState
    db.query(InvestigationState).delete(synchronize_session=False)

    # Unlink observations
    db.query(VehicleObservation).filter(VehicleObservation.candidate_id.isnot(None)).update({"candidate_id": None}, synchronize_session=False)
    
    # Delete physical files
    import shutil
    evidence_dir = os.path.join("data", "evidence")
    if os.path.exists(evidence_dir):
        try:
            shutil.rmtree(evidence_dir)
        except OSError:
            pass

    # Delete evidence and tracks
    db.query(TrackEvidence).delete(synchronize_session=False)
    db.query(VehicleTrack).delete(synchronize_session=False)
    
    # Delete targets
    db.query(InvestigationTarget).delete(synchronize_session=False)
    
    db.commit()
    return {"status": "ok", "purged": True}


@router.get("/targets/{target_id}/candidates", response_model=List[CandidateRead])
def list_candidates_for_target(
    target_id: UUID,
    status: TrackStatus = Query(None, description="Filter by track status"),
    db: Session = Depends(get_db)
):
    """List all candidate vehicle tracks for a specific target."""
    query = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id)
    
    if status:
        query = query.filter(VehicleTrack.status == status)
        
    # Exclude NO_MATCH implicitly unless specifically requested?
    # For now, just return what's in the DB since the pipeline filters NO_MATCH out
    tracks = query.order_by(VehicleTrack.started_at.desc()).all()
    return tracks


@router.get("/candidates/{candidate_id}", response_model=CandidateRead)
def get_candidate_details(candidate_id: UUID, db: Session = Depends(get_db)):
    """Get candidate details including its evidence frames."""
    track = db.query(VehicleTrack).filter(VehicleTrack.id == candidate_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Candidate not found")
        
    # Fetch evidence
    evidence = db.query(TrackEvidence).filter(TrackEvidence.track_id == candidate_id).order_by(TrackEvidence.frame_index).all()
    
    # We can manually inject evidence into the Pydantic model response
    result = CandidateRead.model_validate(track)
    result.evidence = [EvidenceRead.model_validate(e) for e in evidence]
    
    return result


@router.post("/candidates/{candidate_id}/verify", response_model=CandidateRead)
def verify_candidate(
    candidate_id: UUID,
    action: VerificationAction,
    db: Session = Depends(get_db)
):
    """Review a candidate through its deterministically linked observation."""
    track = db.query(VehicleTrack).filter(VehicleTrack.id == candidate_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Candidate not found")

    observation = (
        db.query(VehicleObservation)
        .filter(VehicleObservation.candidate_id == track.id)
        .order_by(VehicleObservation.observed_at.desc())
        .first()
    )
    if not observation:
        raise HTTPException(
            status_code=409,
            detail="Candidate has no deterministically linked observation; it cannot be reviewed safely.",
        )

    review_action = (
        EvidenceReviewAction.ACCEPT
        if action.action == "verify"
        else EvidenceReviewAction.REJECT
    )
    process_verification_event(
        db,
        EvidenceReviewEntityType.VEHICLE_OBSERVATION,
        str(observation.id),
        review_action,
        action.verifier,
        action.review_note,
    )

    # These fields are a candidate-display projection of the append-only
    # observation review; they do not alter machine scores or raw evidence.
    track.status = (
        TrackStatus.verified if action.action == "verify" else TrackStatus.rejected
    )
    track.verified_by = action.verifier
    track.verified_at = datetime.utcnow()
    track.review_note = action.review_note
    db.commit()
    db.refresh(track)
    return track


@router.post("/ingest")
def ingest_candidate(candidate: CandidateIngest, db: Session = Depends(get_db)):
    """Ingest a candidate and its evidence from the ANPR pipeline."""
    # Convert float timestamps to datetime
    started_at = datetime.utcfromtimestamp(candidate.started_at)
    ended_at = datetime.utcfromtimestamp(candidate.ended_at)
    
    # Ensure camera exists to satisfy foreign key constraint
    from app.models import Camera, CameraType, ConnectivityStatus
    cam = db.query(Camera).filter(Camera.camera_id == candidate.camera_id).first()
    if not cam:
        cam = Camera(
            camera_id=candidate.camera_id,
            name=f"Camera {candidate.camera_id}",
            department="police",
            camera_type=CameraType.ip,
            connectivity_status=ConnectivityStatus.online
        )
        db.add(cam)
        db.flush()
    
    track = VehicleTrack(
        camera_id=candidate.camera_id,
        track_id=candidate.track_id,
        target_id=candidate.target_id,
        started_at=started_at,
        ended_at=ended_at,
        final_score=candidate.final_score,
        tier=candidate.tier,
        score_breakdown=candidate.score_breakdown,
        ocr_consensus=candidate.ocr_consensus,
        evidence_completeness=candidate.evidence_completeness,
        status=TrackStatus.completed
    )
    db.add(track)
    db.flush()  # get track.id
    
    for ev in candidate.evidence:
        db_ev = TrackEvidence(
            track_id=track.id,
            frame_index=ev.frame_index,
            quality_score=ev.quality_score,
            raw_path=ev.raw_path,
            enhanced_path=ev.enhanced_path,
            ocr_candidate=ev.ocr_candidate,
            ocr_confidence=ev.ocr_confidence,
            vehicle_type=ev.vehicle_type,
            vehicle_color=ev.vehicle_color,
            timestamp=ev.timestamp,
            source_pts=ev.source_pts,
            evidence_state=ev.evidence_state,
            unknown_reason=ev.unknown_reason,
            extraction_method=ev.extraction_method,
            mode=Mode.SIMULATED if ev.is_simulated else Mode.REAL,
        )
        db.add(db_ev)
        
    db.commit()
    return {"status": "ok", "track_id": track.id}


@router.get("/targets/{target_id}/status")
def get_target_status(target_id: UUID, db: Session = Depends(get_db)):
    """Live scanning status for a target - counts candidates and cameras."""
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    total_candidates = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id).count()
    pending = db.query(VehicleTrack).filter(
        VehicleTrack.target_id == target_id,
        VehicleTrack.status == TrackStatus.completed
    ).count()
    verified = db.query(VehicleTrack).filter(
        VehicleTrack.target_id == target_id,
        VehicleTrack.status == TrackStatus.verified
    ).count()

    # Unique cameras that have seen this target
    camera_hits = db.query(VehicleTrack.camera_id).filter(
        VehicleTrack.target_id == target_id
    ).distinct().count()

    # Check if pipeline is actually running via heartbeat
    from datetime import timedelta
    heartbeat_timeout = timedelta(seconds=15)
    recent_heartbeat = (
        db.query(PipelineHeartbeat)
        .filter(PipelineHeartbeat.last_heartbeat >= datetime.utcnow() - heartbeat_timeout)
        .first()
    )
    pipeline_online = recent_heartbeat is not None

    return {
        "target_id": str(target_id),
        "status": target.status.value,
        "total_candidates": total_candidates,
        "pending_review": pending,
        "verified": verified,
        "cameras_with_hits": camera_hits,
        "scanning": target.status.value == "active" and pipeline_online,
        "pipeline_online": pipeline_online,
        "pipeline_stats": {
            "cameras_configured": recent_heartbeat.cameras_configured if recent_heartbeat else 0,
            "cameras_connected": recent_heartbeat.cameras_connected if recent_heartbeat else 0,
            "cameras_active": recent_heartbeat.cameras_active if recent_heartbeat else 0,
            "vehicles_detected": recent_heartbeat.vehicles_detected if recent_heartbeat else 0,
            "tracks_created": recent_heartbeat.tracks_created if recent_heartbeat else 0,
            "candidates_created": recent_heartbeat.candidates_created if recent_heartbeat else 0,
            "ocr_attempts": recent_heartbeat.ocr_attempts if recent_heartbeat else 0,
            "ocr_success": recent_heartbeat.ocr_success if recent_heartbeat else 0,
            "last_heartbeat": recent_heartbeat.last_heartbeat.isoformat() if recent_heartbeat else None,
            "per_camera": recent_heartbeat.per_camera_health if recent_heartbeat else None,
        } if recent_heartbeat else None,
    }


@router.delete("/alerts")
def clear_alerts(db: Session = Depends(get_db)):
    """Clear the live feed by marking all pending tracks as rejected."""
    db.query(VehicleTrack).filter(VehicleTrack.status == TrackStatus.completed).update({"status": TrackStatus.rejected})
    db.commit()
    return {"status": "ok"}

@router.get("/alerts/recent")
def get_recent_alerts(limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    """Merged alert feed - recent candidates across all active targets, sorted by score."""
    from sqlalchemy import desc
    tracks = (
        db.query(VehicleTrack)
        .join(InvestigationTarget, VehicleTrack.target_id == InvestigationTarget.id)
        .filter(InvestigationTarget.status == TargetStatus.active)
        .order_by(desc(VehicleTrack.started_at))
        .limit(limit)
        .all()
    )

    results = []
    for t in tracks:
        target = db.query(InvestigationTarget).filter(InvestigationTarget.id == t.target_id).first()
        results.append({
            "candidate_id": str(t.id),
            "camera_id": t.camera_id,
            "track_id": t.track_id,
            "score": t.final_score,
            "tier": t.tier,
            "status": t.status.value,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "ocr_plate": t.ocr_consensus.get("best_plate") if t.ocr_consensus else None,
            "target": {
                "id": str(target.id),
                "plate_number": target.plate_number,
                "vehicle_type": target.vehicle_type.value if target.vehicle_type else None,
                "vehicle_color": target.vehicle_color,
                "category": target.category.value,
                "priority": target.priority.value,
            } if target else None,
        })

    return results


@router.post("/heartbeat")
def pipeline_heartbeat(payload: HeartbeatPayload, db: Session = Depends(get_db)):
    """Pipeline sends periodic heartbeats to prove it's alive."""
    per_cam = [c.model_dump() for c in payload.per_camera] if payload.per_camera else None

    existing = db.query(PipelineHeartbeat).filter(
        PipelineHeartbeat.pipeline_id == payload.pipeline_id
    ).first()

    if existing:
        existing.cameras_configured = payload.cameras_configured
        existing.cameras_connected = payload.cameras_connected
        existing.cameras_active = payload.cameras_active
        existing.vehicles_detected = payload.vehicles_detected
        existing.tracks_created = payload.tracks_created
        existing.active_targets = payload.active_targets
        existing.candidates_created = payload.candidates_created
        existing.ocr_attempts = payload.ocr_attempts
        existing.ocr_success = payload.ocr_success
        existing.api_failures = payload.api_failures
        existing.last_heartbeat = datetime.utcnow()
        existing.per_camera_health = per_cam
    else:
        hb = PipelineHeartbeat(
            pipeline_id=payload.pipeline_id,
            cameras_configured=payload.cameras_configured,
            cameras_connected=payload.cameras_connected,
            cameras_active=payload.cameras_active,
            vehicles_detected=payload.vehicles_detected,
            tracks_created=payload.tracks_created,
            active_targets=payload.active_targets,
            candidates_created=payload.candidates_created,
            ocr_attempts=payload.ocr_attempts,
            ocr_success=payload.ocr_success,
            api_failures=payload.api_failures,
            last_heartbeat=datetime.utcnow(),
            per_camera_health=per_cam,
        )
        db.add(hb)

    db.commit()
    return {"status": "ok"}


@router.get("/prediction-readiness")
def get_prediction_readiness(db: Session = Depends(get_db)):
    """
    Returns the dataset readiness report for advanced ML prediction models.
    Evaluates real-world transitions, sessions, and diversity.
    """
    return PredictionService.get_dataset_readiness_report(db)

@router.get("/pipeline-status")
def get_pipeline_status(db: Session = Depends(get_db)):
    """Check if ANY pipeline is currently running, based on heartbeat recency."""
    from datetime import timedelta
    heartbeat_timeout = timedelta(seconds=15)
    cutoff = datetime.utcnow() - heartbeat_timeout

    recent = (
        db.query(PipelineHeartbeat)
        .filter(PipelineHeartbeat.last_heartbeat >= cutoff)
        .all()
    )

    if not recent:
        return {
            "online": False,
            "pipelines": [],
            "total_cameras": 0,
            "total_vehicles_detected": 0,
            "total_tracks": 0,
            "total_candidates": 0,
        }

    return {
        "online": True,
        "pipelines": [
            {
                "pipeline_id": hb.pipeline_id,
                "cameras_configured": hb.cameras_configured,
                "cameras_connected": hb.cameras_connected,
                "cameras_active": hb.cameras_active,
                "vehicles_detected": hb.vehicles_detected,
                "tracks_created": hb.tracks_created,
                "candidates_created": hb.candidates_created,
                "ocr_attempts": hb.ocr_attempts,
                "ocr_success": hb.ocr_success,
                "api_failures": hb.api_failures,
                "last_heartbeat": hb.last_heartbeat.isoformat(),
                "stats": hb.stats,
                "per_camera": hb.per_camera_health,
            }
            for hb in recent
        ],
        "total_cameras_configured": sum(hb.cameras_configured for hb in recent),
        "total_cameras_connected": sum(hb.cameras_connected for hb in recent),
        "total_cameras": sum(hb.cameras_active for hb in recent),
        "total_vehicles_detected": sum(hb.vehicles_detected for hb in recent),
        "total_tracks": sum(hb.tracks_created for hb in recent),
        "total_candidates": sum(hb.candidates_created for hb in recent),
    }

from app.models import VehicleObservation, RecordingSession, ProvenanceType

@router.post("/sessions")
def ingest_recording_session(sess: RecordingSessionIngest, db: Session = Depends(get_db)):
    """Ingest provenance metadata for a camera recording session."""
    existing = db.query(RecordingSession).filter_by(session_id=sess.session_id, camera_id=sess.camera_id).first()
    if existing:
        return {"status": "ok", "id": existing.id, "message": "already_exists"}
    
    new_sess = RecordingSession(
        session_id=sess.session_id,
        camera_id=sess.camera_id,
        source_date_time=datetime.utcfromtimestamp(sess.source_date_time) if sess.source_date_time else None,
        timestamp_quality=sess.timestamp_quality,
        provenance_type=sess.provenance_type,
        source_identifier=sess.source_identifier,
        synchronization_status=sess.synchronization_status,
        start_time=datetime.utcfromtimestamp(sess.start_time) if sess.start_time else None,
        end_time=datetime.utcfromtimestamp(sess.end_time) if sess.end_time else None,
        graph_version=sess.graph_version,
        mode=sess.mode
    )
    db.add(new_sess)
    db.commit()
    db.refresh(new_sess)
    return {"status": "ok", "id": new_sess.id}

@router.post("/observations")
def ingest_observation(obs: ObservationIngest, db: Session = Depends(get_db)):
    """Ingest a historical vehicle observation from the ANPR pipeline."""
    # Unix timestamps are UTC.  Keep the database's existing naive-UTC
    # convention rather than accidentally storing the host's local time.
    observed_at = datetime.utcfromtimestamp(obs.observed_at)

    # An observation must not rely on SQLite's disabled foreign-key checks.
    # The catalogue normally supplies this row; this safe placeholder protects
    # a live worker that starts before its camera has been synced.
    from app.models import Camera, CameraType, ConnectivityStatus
    camera = db.query(Camera).filter(Camera.camera_id == obs.camera_id).first()
    if not camera:
        camera = Camera(
            camera_id=obs.camera_id,
            name=f"Camera {obs.camera_id}",
            department="unknown",
            camera_type=CameraType.ip,
            connectivity_status=ConnectivityStatus.unknown,
        )
        db.add(camera)
        db.flush()
    
    source_fingerprint = f"{obs.camera_id}:{obs.session_id}:{obs.source_pts_start}" if obs.source_pts_start is not None else None

    # We allow UPSERT behavior based on source_fingerprint OR (track_id and camera_id and session_id)
    existing = None
    if source_fingerprint:
        existing = db.query(VehicleObservation).filter(VehicleObservation.source_fingerprint == source_fingerprint).first()
        
    if not existing:
        existing = db.query(VehicleObservation).filter(
            VehicleObservation.camera_id == obs.camera_id,
            VehicleObservation.session_id == obs.session_id,
            VehicleObservation.track_id == obs.track_id
        ).first()
    
    if existing:
        timestamp_source = TimestampSource(obs.timestamp_source)
        existing.status = obs.status
        existing.timestamp_source = timestamp_source
        existing.timestamp_quality = (
            TimestampQuality.VALID
            if timestamp_source == TimestampSource.ABSOLUTE_TIMESTAMP
            else TimestampQuality.UNRELIABLE
        )
        existing.source_pts_start = obs.source_pts_start
        existing.source_pts_end = obs.source_pts_end
        existing.vehicle_type = obs.vehicle_type
        existing.color = obs.color
        existing.color_confidence = obs.color_confidence
        existing.plate = obs.plate
        existing.plate_confidence = obs.plate_confidence
        existing.evidence_completeness = obs.evidence_completeness
        existing.overall_confidence = obs.overall_confidence
        existing.is_playback_repetition = obs.is_playback_repetition
        existing.is_time_synchronized = obs.is_time_synchronized
        existing.source_fingerprint = source_fingerprint
        existing.ingested_at = datetime.utcfromtimestamp(obs.ingested_at)
        existing.observed_at = observed_at
        
        # We explicitly preserve SIMULATED mode from the client, but default to REAL
        existing.mode = Mode.SIMULATED if obs.is_simulated else Mode.REAL
        if obs.candidate_id:
            existing.candidate_id = obs.candidate_id
    else:
        timestamp_source = TimestampSource(obs.timestamp_source)
        # FRAME_CLOCK and SOURCE_PTS are local timing domains.  They are
        # retained for provenance but cannot validate cross-camera chronology.
        timestamp_quality = (
            TimestampQuality.VALID
            if timestamp_source == TimestampSource.ABSOLUTE_TIMESTAMP
            else TimestampQuality.UNRELIABLE
        )
        recording_id = None
        if obs.session_id:
            rec = db.query(RecordingSession).filter_by(session_id=obs.session_id, camera_id=obs.camera_id).first()
            if rec:
                recording_id = rec.id

        new_obs = VehicleObservation(
            camera_id=obs.camera_id,
            session_id=obs.session_id,
            recording_id=recording_id,
            track_id=obs.track_id,
            candidate_id=obs.candidate_id,
            status=obs.status,
            timestamp_source=timestamp_source,
            timestamp_quality=timestamp_quality,
            observed_at=observed_at,
            source_pts_start=obs.source_pts_start,
            source_pts_end=obs.source_pts_end,
            vehicle_type=obs.vehicle_type,
            color=obs.color,
            color_confidence=obs.color_confidence,
            plate=obs.plate,
            plate_confidence=obs.plate_confidence,
            evidence_completeness=obs.evidence_completeness,
            overall_confidence=obs.overall_confidence,
            is_playback_repetition=obs.is_playback_repetition,
            is_time_synchronized=obs.is_time_synchronized,
            source_fingerprint=source_fingerprint,
            ingested_at=datetime.utcfromtimestamp(obs.ingested_at),
            mode=Mode.SIMULATED if obs.is_simulated else Mode.REAL
        )
        db.add(new_obs)
        db.flush()
        
    # Trigger event-driven processing synchronously for now
    process_new_observation(db, existing.id if existing else new_obs.id)
    db.commit()
    return {"status": "ok"}


@router.get("/observations/{observation_id}/evidence")
def get_observation_evidence(observation_id: UUID, db: Session = Depends(get_db)):
    """Return evidence only when an observation has a direct candidate link.

    Historical records without ``candidate_id`` intentionally return an empty
    result.  The workstation must expose that limitation rather than guessing
    from matching camera/track attributes.
    """
    observation = (
        db.query(VehicleObservation).filter(VehicleObservation.id == observation_id).first()
    )
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")

    if not observation.candidate_id:
        return {
            "observation_id": str(observation.id),
            "candidate_id": None,
            "evidence_available": False,
            "evidence": [],
        }

    evidence = (
        db.query(TrackEvidence)
        .filter(TrackEvidence.track_id == observation.candidate_id)
        .order_by(TrackEvidence.frame_index)
        .all()
    )
    return {
        "observation_id": str(observation.id),
        "candidate_id": str(observation.candidate_id),
        "evidence_available": True,
        "evidence": [EvidenceRead.model_validate(item).model_dump(mode="json") for item in evidence],
    }


@router.get("/observations/history")
def get_observation_history(
    camera_id: Optional[str] = Query(None),
    start_time: Optional[float] = Query(None),
    end_time: Optional[float] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    color: Optional[str] = Query(None),
    plate: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    db: Session = Depends(get_db)
):
    """Query historical vehicle observations."""
    from sqlalchemy import desc
    
    query = db.query(VehicleObservation)
    
    if camera_id:
        query = query.filter(VehicleObservation.camera_id == camera_id)
    
    if start_time:
        query = query.filter(VehicleObservation.observed_at >= datetime.fromtimestamp(start_time))
        
    if end_time:
        query = query.filter(VehicleObservation.observed_at <= datetime.fromtimestamp(end_time))
        
    if vehicle_type:
        query = query.filter(VehicleObservation.vehicle_type == vehicle_type)
        
    if color:
        query = query.filter(VehicleObservation.color == color)
        
    if plate:
        query = query.filter(VehicleObservation.plate.ilike(f"%{plate}%"))
        
    if status:
        query = query.filter(VehicleObservation.status == status)
        
    observations = query.order_by(desc(VehicleObservation.observed_at)).limit(limit).all()
    
    return [
        {
            "id": str(obs.id),
            "camera_id": obs.camera_id,
            "session_id": obs.session_id,
            "track_id": obs.track_id,
            "status": obs.status.value if hasattr(obs.status, 'value') else obs.status,
            "timestamp_source": obs.timestamp_source.value if hasattr(obs.timestamp_source, 'value') else obs.timestamp_source,
            "mode": obs.mode.value if hasattr(obs.mode, "value") else obs.mode,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            "source_pts_start": obs.source_pts_start,
            "source_pts_end": obs.source_pts_end,
            "vehicle_type": obs.vehicle_type.value if hasattr(obs.vehicle_type, 'value') else obs.vehicle_type,
            "color": obs.color,
            "color_confidence": obs.color_confidence,
            "plate": obs.plate,
            "plate_confidence": obs.plate_confidence,
            "evidence_completeness": obs.evidence_completeness,
            "overall_confidence": obs.overall_confidence
        }
        for obs in observations
    ]


@router.get("/{target_id}/history")
def get_target_history(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Historical Route Reconstruction for a target.
    Reads persisted RouteChains instead of computing on the fly.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    chains = db.query(RouteChain).filter(
        RouteChain.target_id == target_id,
        RouteChain.is_superseded == 0
    ).all()
    
    result_chains = []
    for c in chains:
        obs_seq = db.query(RouteChainObservation).filter_by(route_chain_id=c.id).order_by(RouteChainObservation.sequence_order).all()
        link_seq = db.query(RouteChainLink).filter_by(route_chain_id=c.id).order_by(RouteChainLink.sequence_order).all()
        
        observations = []
        for o_ref in obs_seq:
            obs = db.query(VehicleObservation).filter_by(id=o_ref.observation_id).first()
            if obs:
                observations.append({
                    "id": str(obs.id),
                    "camera_id": obs.camera_id,
                    "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
                    "status": obs.status.value if hasattr(obs.status, 'value') else obs.status,
                    "verified_action": obs.human_review_state.value if obs.human_review_state else "pending"
                })
                
        links = []
        for l_ref in link_seq:
            l = db.query(CrossCameraLinkCandidate).filter_by(id=l_ref.link_candidate_id).first()
            if l:
                links.append({
                    "id": str(l.id),
                    "source_id": str(l.source_observation_id),
                    "destination_id": str(l.destination_observation_id),
                    "temporal_feasibility": l.temporal_feasibility,
                    "machine_assessment": l.machine_assessment.value if l.machine_assessment else "unknown",
                    "link_score": l.link_score,
                    "verified_action": l.human_review_state.value if l.human_review_state else "pending"
                })
                
        result_chains.append({
            "route_chain_id": str(c.id),
            "status": c.status.value if hasattr(c.status, 'value') else c.status,
            "mode": c.mode.value if hasattr(c.mode, "value") else c.mode,
            "cameras_visited": [o["camera_id"] for o in observations],
            "timestamps": [o["observed_at"] for o in observations],
            "observations": observations,
            "links": links
        })
    
    return {
        "target": {
            "id": str(target.id),
            "vehicle_type": target.vehicle_type.value if hasattr(target.vehicle_type, 'value') else target.vehicle_type,
            "color": target.vehicle_color,
            "plate": target.plate_number,
            "status": target.status.value if hasattr(target.status, 'value') else target.status
        },
        "route_chains": result_chains
    }


@router.get("/{target_id}/timeline")
def get_target_timeline(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Read-optimized endpoint returning a chronological investigation timeline
    including observation events, explicit gaps, and verification events.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    state = db.query(InvestigationState).filter(InvestigationState.target_id == target_id).first()
    
    timeline = []
    
    # Target creation
    timeline.append({
        "type": "TARGET_CREATED",
        "timestamp": target.created_at.isoformat() if target.created_at else None
    })
    
    # Reviews must be scoped to this investigation.  A global audit query here
    # would leak another target's analyst activity into this timeline.
    target_track_uuid_ids = {
        track_id
        for (track_id,) in db.query(VehicleTrack.id)
        .filter(VehicleTrack.target_id == target.id)
        .all()
    }
    target_observation_ids = {
        str(observation_id)
        for (observation_id,) in db.query(VehicleObservation.id)
        .filter(VehicleObservation.candidate_id.in_(target_track_uuid_ids) if target_track_uuid_ids else False)
        .all()
    }
    target_chain_ids = {
        str(chain_id)
        for (chain_id,) in db.query(RouteChain.id)
        .filter(RouteChain.target_id == target.id)
        .all()
    }
    target_link_ids = {
        str(link_id)
        for (link_id,) in db.query(RouteChainLink.link_candidate_id)
        .join(RouteChain, RouteChain.id == RouteChainLink.route_chain_id)
        .filter(RouteChain.target_id == target.id)
        .all()
    }
    review_entity_ids = target_observation_ids | target_chain_ids | target_link_ids
    reviews = (
        db.query(EvidenceReview)
        .filter(EvidenceReview.entity_id.in_(review_entity_ids) if review_entity_ids else False)
        .all()
    )
    for r in reviews:
        # naive filter: ideally we query reviews linked to target entities
        timeline.append({
            "type": "REVIEW_EVENT",
            "action": r.action.value if hasattr(r.action, 'value') else r.action,
            "entity_type": r.entity_type.value if hasattr(r.entity_type, 'value') else r.entity_type,
            "entity_id": str(r.entity_id),
            "reviewer": r.reviewer,
            "timestamp": r.created_at.isoformat() if r.created_at else None
        })
        
    # Active Route Chains
    chains = db.query(RouteChain).filter(RouteChain.target_id == target_id, RouteChain.is_superseded == 0).all()
    
    for c in chains:
        timeline.append({
            "type": "ROUTE_HYPOTHESIS",
            "chain_id": str(c.id),
            "status": c.status.value if hasattr(c.status, 'value') else c.status,
            "timestamp": c.created_at.isoformat() if c.created_at else None
        })
        
        # Segments
        obs_seq = db.query(RouteChainObservation).filter_by(route_chain_id=c.id).order_by(RouteChainObservation.sequence_order).all()
        link_seq = db.query(RouteChainLink).filter_by(route_chain_id=c.id).order_by(RouteChainLink.sequence_order).all()
        
        for i, o_ref in enumerate(obs_seq):
            obs = db.query(VehicleObservation).filter_by(id=o_ref.observation_id).first()
            if obs:
                timeline.append({
                    "type": "OBSERVATION",
                    "observation_id": str(obs.id),
                    "camera_id": obs.camera_id,
                    "timestamp": obs.observed_at.isoformat() if obs.observed_at else None,
                    "timestamp_source": obs.timestamp_source.value if hasattr(obs.timestamp_source, "value") else obs.timestamp_source,
                    "mode": obs.mode.value if hasattr(obs.mode, "value") else obs.mode,
                    "human_review_state": obs.human_review_state.value if hasattr(obs.human_review_state, "value") else obs.human_review_state,
                })
            
            # Gaps / Links
            if i < len(link_seq):
                l_ref = link_seq[i]
                l = db.query(CrossCameraLinkCandidate).filter_by(id=l_ref.link_candidate_id).first()
                if l:
                    timeline.append({
                        "type": "ROUTE_SEGMENT",
                        "state": "OBSERVED_LINK",
                        "link_id": str(l.id),
                        "score": l.link_score,
                        "timestamp": l.created_at.isoformat() if l.created_at else None,
                        "machine_assessment": l.machine_assessment.value if hasattr(l.machine_assessment, "value") else l.machine_assessment,
                        "human_review_state": l.human_review_state.value if hasattr(l.human_review_state, "value") else l.human_review_state,
                        "mode": l.mode.value if hasattr(l.mode, "value") else l.mode,
                    })
                else:
                    # Gaps can be represented if link doesn't exist but observation does
                    timeline.append({
                        "type": "ROUTE_SEGMENT",
                        "state": "UNOBSERVED_GAP"
                    })
                    
    timeline.sort(key=lambda x: x.get("timestamp") or "")
    
    last_observed = None
    if state and state.last_seen_observation_id:
        lo = db.query(VehicleObservation).filter_by(id=state.last_seen_observation_id).first()
        if lo:
            age = (datetime.utcnow() - lo.observed_at).total_seconds() if lo.observed_at else 0
            last_observed = {
                "observation_id": str(lo.id),
                "camera_id": lo.camera_id,
                "observed_at": lo.observed_at.isoformat() if lo.observed_at else None,
                "age_seconds": age,
                "timestamp_source": lo.timestamp_source.value if hasattr(lo.timestamp_source, 'value') else lo.timestamp_source,
                "label": "LAST_OBSERVED"
            }
    
    return {
        "target": {
            "id": str(target.id),
            "status": target.status.value if hasattr(target.status, 'value') else target.status
        },
        "state": {
            "status": state.status.value if state and hasattr(state.status, 'value') else "UNKNOWN",
            "state_version": state.state_version if state else 0,
            "last_processed_at": state.last_processed_at.isoformat() if state and state.last_processed_at else None,
            "search_truncated": bool(state.search_truncated) if state else False
        } if state else None,
        "last_observed": last_observed,
        "timeline": timeline
    }


@router.get("/{target_id}/last_seen")
def get_target_last_seen(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Returns the Last Seen observation for an investigation target from persisted state.
    Does NOT mean current location. Always treated as STALE/LAST_OBSERVED.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    state = db.query(InvestigationState).filter(InvestigationState.target_id == target_id).first()
    if not state or not state.last_seen_observation_id:
        return {"last_seen": None}
        
    obs = db.query(VehicleObservation).filter_by(id=state.last_seen_observation_id).first()
    if not obs:
        return {"last_seen": None}
        
    return {
        "last_seen": {
            "observation_id": str(obs.id),
            "camera_id": obs.camera_id,
            "timestamp": obs.observed_at.isoformat() if obs.observed_at else None,
            "timestamp_source": obs.timestamp_source.value if hasattr(obs.timestamp_source, 'value') else obs.timestamp_source,
            "evidence_completeness": obs.evidence_completeness,
            "status": obs.status.value if hasattr(obs.status, 'value') else obs.status,
            "human_review_state": obs.human_review_state.value if obs.human_review_state else "pending",
            "is_stale": True,
            "tag": "LAST_OBSERVED"
        }
    }


@router.get("/{target_id}/predictions")
def get_target_predictions(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Returns prediction hypotheses based on historical transitions.
    Does NOT claim current location or calibrated probability.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    predictions = PredictionService.predict_next_cameras(db, target_id)
    
    # F16: Include last_observed context so API consumer can distinguish
    # historical observations from predictions
    state = db.query(InvestigationState).filter(InvestigationState.target_id == target_id).first()
    last_observed = None
    if state and state.last_seen_observation_id:
        lo = db.query(VehicleObservation).filter_by(id=state.last_seen_observation_id).first()
        if lo:
            last_observed = {
                "observation_id": str(lo.id),
                "camera_id": lo.camera_id,
                "observed_at": lo.observed_at.isoformat() if lo.observed_at else None,
                "timestamp_source": lo.timestamp_source.value if hasattr(lo.timestamp_source, 'value') else lo.timestamp_source,
                "label": "LAST_OBSERVED"
            }
    
    return {
        "target_id": str(target_id),
        "last_observed": last_observed,
        "predictions": predictions
    }


@router.get("/{target_id}/evaluate")
def evaluate_target_predictions(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Run walk-forward backtesting against all known routes for a target.
    Returns structured metrics including per-edge breakdown and
    a recommendation on whether advanced ML is justified.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    report = EvaluationService.generate_evaluation_report(db, target_id)
    return report


@router.post("/{target_id}/seed-evaluation-data")
def seed_target_evaluation_data(
    target_id: str,
    db: Session = Depends(get_db)
):
    """
    Generate synthetic evaluation dataset for walk-forward backtesting.
    All data uses mode=SIMULATED with provenance='synthetic_evaluation'.
    Dev/staging only — do not use in production.
    """
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    result = seed_evaluation_data(db, target_id=target_id)
    return result

class VerificationRequest(BaseModel):
    action: str  # verified, rejected
    reviewer: str
    review_note: Optional[str] = None


@router.post("/observations/{observation_id}/verify")
def verify_observation(
    observation_id: str,
    req: VerificationRequest,
    db: Session = Depends(get_db)
):
    obs = db.query(VehicleObservation).filter(VehicleObservation.id == observation_id).first()
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
        
    try:
        action_enum = EvidenceReviewAction(req.action.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid action")
        
    process_verification_event(
        db, 
        EvidenceReviewEntityType.VEHICLE_OBSERVATION, 
        observation_id, 
        action_enum, 
        req.reviewer, 
        req.review_note
    )
    return {"status": "ok"}


@router.post("/links/{link_id}/verify")
def verify_link(
    link_id: str,
    req: VerificationRequest,
    db: Session = Depends(get_db)
):
    link = db.query(CrossCameraLinkCandidate).filter(CrossCameraLinkCandidate.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
        
    try:
        action_enum = EvidenceReviewAction(req.action.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid action")
        
    process_verification_event(
        db, 
        EvidenceReviewEntityType.CROSS_CAMERA_LINK, 
        link_id, 
        action_enum, 
        req.reviewer, 
        req.review_note
    )
    return {"status": "ok"}

