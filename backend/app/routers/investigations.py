"""
Model 2 - targeted vehicle investigations (multi-frame evidence + cross-camera routes).

The ANPR pipeline (anpr/investigation_pipeline.py) posts candidates,
observations and heartbeats here; the Investigation page reads targets,
candidates, route chains and the timeline back out.

Pipeline ingest endpoints stay unauthenticated, same as POST /detections.
Every operator action that changes or deletes investigation data requires
X-Role: admin (app/dependencies.py).
"""
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.investigation_service import process_new_observation, process_verification_event
from app.models import (
    Camera,
    CameraType,
    ConnectivityStatus,
    CrossCameraLinkCandidate,
    EvidenceReview,
    EvidenceReviewAction,
    EvidenceReviewEntityType,
    InvestigationState,
    InvestigationTarget,
    Mode,
    ObservationStatus,
    PipelineHeartbeat,
    RouteChain,
    RouteChainLink,
    RouteChainObservation,
    TargetStatus,
    TimestampQuality,
    TimestampSource,
    TrackEvidence,
    TrackStatus,
    VehicleObservation,
    VehicleTrack,
)
from app.schemas import (
    CandidateIngest,
    CandidateRead,
    EvidenceRead,
    HeartbeatPayload,
    InvestigationTargetCreate,
    InvestigationTargetRead,
    ObservationIngest,
    ReviewRequest,
    TargetStatusUpdate,
    VerificationAction,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])

# A pipeline that hasn't sent a heartbeat in this long is treated as offline.
HEARTBEAT_TIMEOUT = timedelta(seconds=15)

# Evidence crops live under backend/data/evidence and are served by the
# static mount in main.py. TrackEvidence stores paths relative to backend/.
BACKEND_DIR = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = BACKEND_DIR / "data" / "evidence"


def _value(v):
    """Enum -> its value, anything else unchanged (JSON-friendly output)."""
    return v.value if hasattr(v, "value") else v


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _get_target_or_404(db: Session, target_id: UUID) -> InvestigationTarget:
    target = db.query(InvestigationTarget).filter(InvestigationTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _ensure_camera(db: Session, camera_id: str) -> None:
    """Placeholder registry row so a worker that starts before its camera is
    onboarded doesn't fail the foreign key. Onboarding later fills it in."""
    if not db.query(Camera).filter(Camera.camera_id == camera_id).first():
        db.add(Camera(
            camera_id=camera_id,
            name=f"Camera {camera_id}",
            department="unknown",
            camera_type=CameraType.ip,
            connectivity_status=ConnectivityStatus.unknown,
        ))
        db.flush()


def _remove_evidence_file(relative_path: Optional[str]) -> None:
    """Delete an evidence crop, but only if it really is inside EVIDENCE_DIR.

    Paths arrive from the unauthenticated ingest endpoint, so a stored path
    like ../../app/main.py must never turn a target deletion into an
    arbitrary file deletion.
    """
    if not relative_path:
        return
    path = (BACKEND_DIR / relative_path).resolve()
    if EVIDENCE_DIR.resolve() not in path.parents:
        return
    path.unlink(missing_ok=True)


def _observation_summary(obs: VehicleObservation) -> dict:
    return {
        "observation_id": str(obs.id),
        "camera_id": obs.camera_id,
        "observed_at": _iso(obs.observed_at),
        "timestamp_source": _value(obs.timestamp_source),
        "label": "LAST_OBSERVED",
    }


def _last_observed(db: Session, target_id: UUID) -> Optional[VehicleObservation]:
    state = db.query(InvestigationState).filter(InvestigationState.target_id == target_id).first()
    if not state or not state.last_seen_observation_id:
        return None
    return db.query(VehicleObservation).filter_by(id=state.last_seen_observation_id).first()


def _recent_heartbeats(db: Session) -> List[PipelineHeartbeat]:
    cutoff = datetime.utcnow() - HEARTBEAT_TIMEOUT
    return db.query(PipelineHeartbeat).filter(PipelineHeartbeat.last_heartbeat >= cutoff).all()


# ---- Targets ----

@router.post("/targets", response_model=InvestigationTargetRead)
def create_target(
    target: InvestigationTargetCreate,
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
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
    status: Optional[TargetStatus] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    query = db.query(InvestigationTarget)
    if status:
        query = query.filter(InvestigationTarget.status == status)
    return query.order_by(InvestigationTarget.created_at.desc()).all()


@router.get("/targets/{target_id}", response_model=InvestigationTargetRead)
def get_target(target_id: UUID, db: Session = Depends(get_db)):
    return _get_target_or_404(db, target_id)


@router.patch("/targets/{target_id}/status")
def update_target_status(
    target_id: UUID,
    payload: TargetStatusUpdate,
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
    target = _get_target_or_404(db, target_id)
    target.status = TargetStatus(payload.status)
    if target.status == TargetStatus.resolved:
        target.resolved_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


@router.delete("/targets/{target_id}")
def delete_target(
    target_id: UUID,
    delete_data: bool = Query(False, description="Also delete the target's candidates and evidence frames"),
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
    target = _get_target_or_404(db, target_id)
    track_ids = [t.id for t in db.query(VehicleTrack.id).filter(VehicleTrack.target_id == target_id)]

    if delete_data and track_ids:
        evidence = db.query(TrackEvidence).filter(TrackEvidence.track_id.in_(track_ids)).all()
        for ev in evidence:
            _remove_evidence_file(ev.raw_path)
            _remove_evidence_file(ev.enhanced_path)
        db.query(TrackEvidence).filter(TrackEvidence.track_id.in_(track_ids)).delete(synchronize_session=False)
        # Observations stay (cross-camera links reference them); only their
        # candidate pointer is cleared.
        db.query(VehicleObservation).filter(VehicleObservation.candidate_id.in_(track_ids)).update(
            {"candidate_id": None}, synchronize_session=False
        )
        db.query(VehicleTrack).filter(VehicleTrack.id.in_(track_ids)).delete(synchronize_session=False)
    elif track_ids:
        db.query(VehicleTrack).filter(VehicleTrack.id.in_(track_ids)).update(
            {"target_id": None}, synchronize_session=False
        )

    db.query(InvestigationState).filter(InvestigationState.target_id == target_id).delete(synchronize_session=False)

    chain_ids = [c.id for c in db.query(RouteChain.id).filter(RouteChain.target_id == target_id)]
    if chain_ids:
        db.query(RouteChainLink).filter(RouteChainLink.route_chain_id.in_(chain_ids)).delete(synchronize_session=False)
        db.query(RouteChainObservation).filter(RouteChainObservation.route_chain_id.in_(chain_ids)).delete(synchronize_session=False)
        db.query(RouteChain).filter(RouteChain.id.in_(chain_ids)).delete(synchronize_session=False)

    db.delete(target)
    db.commit()
    return {"status": "ok", "deleted": True}


@router.post("/purge")
def purge_investigations(db: Session = Depends(get_db), _role: str = Depends(require_admin)):
    """Delete every investigation target, candidate and evidence frame."""
    db.query(RouteChainLink).delete(synchronize_session=False)
    db.query(RouteChainObservation).delete(synchronize_session=False)
    db.query(RouteChain).delete(synchronize_session=False)
    db.query(InvestigationState).delete(synchronize_session=False)
    db.query(VehicleObservation).filter(VehicleObservation.candidate_id.isnot(None)).update(
        {"candidate_id": None}, synchronize_session=False
    )
    db.query(TrackEvidence).delete(synchronize_session=False)
    db.query(VehicleTrack).delete(synchronize_session=False)
    db.query(InvestigationTarget).delete(synchronize_session=False)
    db.commit()

    shutil.rmtree(EVIDENCE_DIR, ignore_errors=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "purged": True}


# ---- Candidates (per-camera evidence tracks) ----

@router.get("/targets/{target_id}/candidates", response_model=List[CandidateRead])
def list_candidates_for_target(
    target_id: UUID,
    status: Optional[TrackStatus] = Query(None, description="Filter by track status"),
    db: Session = Depends(get_db),
):
    query = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id)
    if status:
        query = query.filter(VehicleTrack.status == status)
    return query.order_by(VehicleTrack.started_at.desc()).all()


@router.get("/candidates/{candidate_id}", response_model=CandidateRead)
def get_candidate_details(candidate_id: UUID, db: Session = Depends(get_db)):
    """Candidate plus its evidence frames."""
    track = db.query(VehicleTrack).filter(VehicleTrack.id == candidate_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Candidate not found")
    evidence = (
        db.query(TrackEvidence)
        .filter(TrackEvidence.track_id == candidate_id)
        .order_by(TrackEvidence.frame_index)
        .all()
    )
    result = CandidateRead.model_validate(track)
    result.evidence = [EvidenceRead.model_validate(e) for e in evidence]
    return result


@router.post("/candidates/{candidate_id}/verify", response_model=CandidateRead)
def verify_candidate(
    candidate_id: UUID,
    action: VerificationAction,
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
    """Review a candidate through its directly linked observation."""
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
            detail="Candidate has no linked observation, so it can't be reviewed safely.",
        )

    process_verification_event(
        db,
        EvidenceReviewEntityType.VEHICLE_OBSERVATION,
        str(observation.id),
        EvidenceReviewAction.ACCEPT if action.action == "verify" else EvidenceReviewAction.REJECT,
        action.verifier,
        action.review_note,
    )

    # A display projection of the append-only observation review; machine
    # scores and raw evidence are never altered.
    track.status = TrackStatus.verified if action.action == "verify" else TrackStatus.rejected
    track.verified_by = action.verifier
    track.verified_at = datetime.utcnow()
    track.review_note = action.review_note
    db.commit()
    db.refresh(track)
    return track


# ---- Pipeline ingest ----

@router.post("/ingest")
def ingest_candidate(candidate: CandidateIngest, db: Session = Depends(get_db)):
    """Ingest a candidate track and its evidence frames from the ANPR pipeline."""
    _ensure_camera(db, candidate.camera_id)

    track = VehicleTrack(
        camera_id=candidate.camera_id,
        track_id=candidate.track_id,
        target_id=candidate.target_id,
        started_at=datetime.utcfromtimestamp(candidate.started_at),
        ended_at=datetime.utcfromtimestamp(candidate.ended_at),
        final_score=candidate.final_score,
        tier=candidate.tier,
        score_breakdown=candidate.score_breakdown,
        ocr_consensus=candidate.ocr_consensus,
        evidence_completeness=candidate.evidence_completeness,
        status=TrackStatus.completed,
    )
    db.add(track)
    db.flush()

    for ev in candidate.evidence:
        db.add(TrackEvidence(
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
        ))

    db.commit()
    return {"status": "ok", "track_id": track.id}


@router.post("/observations")
def ingest_observation(obs: ObservationIngest, db: Session = Depends(get_db)):
    """Ingest (or update) a finalized per-camera observation, then extend routes."""
    _ensure_camera(db, obs.camera_id)

    timestamp_source = TimestampSource(obs.timestamp_source)
    # Only an absolute clock is comparable across cameras. Frame clocks and
    # per-stream PTS are kept for provenance but marked unreliable for
    # cross-camera chronology.
    timestamp_quality = (
        TimestampQuality.VALID
        if timestamp_source == TimestampSource.ABSOLUTE_TIMESTAMP
        else TimestampQuality.UNRELIABLE
    )
    source_fingerprint = (
        f"{obs.camera_id}:{obs.session_id}:{obs.source_pts_start}"
        if obs.source_pts_start is not None
        else None
    )

    # Upsert on the source fingerprint, else on (camera, session, track).
    existing = None
    if source_fingerprint:
        existing = db.query(VehicleObservation).filter(
            VehicleObservation.source_fingerprint == source_fingerprint
        ).first()
    if not existing:
        existing = db.query(VehicleObservation).filter(
            VehicleObservation.camera_id == obs.camera_id,
            VehicleObservation.session_id == obs.session_id,
            VehicleObservation.track_id == obs.track_id,
        ).first()

    record = existing or VehicleObservation(
        camera_id=obs.camera_id,
        session_id=obs.session_id,
        track_id=obs.track_id,
    )
    record.status = ObservationStatus(obs.status)
    record.timestamp_source = timestamp_source
    record.timestamp_quality = timestamp_quality
    record.observed_at = datetime.utcfromtimestamp(obs.observed_at)
    record.source_pts_start = obs.source_pts_start
    record.source_pts_end = obs.source_pts_end
    record.vehicle_type = obs.vehicle_type
    record.color = obs.color
    record.color_confidence = obs.color_confidence
    record.plate = obs.plate
    record.plate_confidence = obs.plate_confidence
    record.evidence_completeness = obs.evidence_completeness
    record.overall_confidence = obs.overall_confidence
    record.is_playback_repetition = obs.is_playback_repetition
    record.is_time_synchronized = obs.is_time_synchronized
    record.source_fingerprint = source_fingerprint
    record.ingested_at = datetime.utcfromtimestamp(obs.ingested_at)
    record.mode = Mode.SIMULATED if obs.is_simulated else Mode.REAL
    if obs.candidate_id:
        record.candidate_id = obs.candidate_id
    if not existing:
        db.add(record)
    db.flush()

    process_new_observation(db, record.id)
    db.commit()
    return {"status": "ok"}


@router.post("/heartbeat")
def pipeline_heartbeat(payload: HeartbeatPayload, db: Session = Depends(get_db)):
    """Periodic proof of life from the pipeline, with per-camera health."""
    hb = db.query(PipelineHeartbeat).filter(PipelineHeartbeat.pipeline_id == payload.pipeline_id).first()
    if not hb:
        hb = PipelineHeartbeat(pipeline_id=payload.pipeline_id)
        db.add(hb)

    hb.cameras_configured = payload.cameras_configured
    hb.cameras_connected = payload.cameras_connected
    hb.cameras_active = payload.cameras_active
    hb.vehicles_detected = payload.vehicles_detected
    hb.tracks_created = payload.tracks_created
    hb.active_targets = payload.active_targets
    hb.candidates_created = payload.candidates_created
    hb.ocr_attempts = payload.ocr_attempts
    hb.ocr_success = payload.ocr_success
    hb.api_failures = payload.api_failures
    hb.last_heartbeat = datetime.utcnow()
    hb.per_camera_health = [c.model_dump() for c in payload.per_camera] if payload.per_camera else None

    db.commit()
    return {"status": "ok"}


# ---- Live status and alerts ----

@router.get("/targets/{target_id}/status")
def get_target_status(target_id: UUID, db: Session = Depends(get_db)):
    """Live scanning status for a target: candidate counts plus pipeline health."""
    target = _get_target_or_404(db, target_id)
    tracks = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id)

    heartbeats = _recent_heartbeats(db)
    hb = heartbeats[0] if heartbeats else None

    return {
        "target_id": str(target_id),
        "status": target.status.value,
        "total_candidates": tracks.count(),
        "pending_review": tracks.filter(VehicleTrack.status == TrackStatus.completed).count(),
        "verified": tracks.filter(VehicleTrack.status == TrackStatus.verified).count(),
        "cameras_with_hits": tracks.with_entities(VehicleTrack.camera_id).distinct().count(),
        "scanning": target.status == TargetStatus.active and hb is not None,
        "pipeline_online": hb is not None,
        "pipeline_stats": {
            "cameras_configured": hb.cameras_configured,
            "cameras_connected": hb.cameras_connected,
            "cameras_active": hb.cameras_active,
            "vehicles_detected": hb.vehicles_detected,
            "tracks_created": hb.tracks_created,
            "candidates_created": hb.candidates_created,
            "ocr_attempts": hb.ocr_attempts,
            "ocr_success": hb.ocr_success,
            "last_heartbeat": _iso(hb.last_heartbeat),
            "per_camera": hb.per_camera_health,
        } if hb else None,
    }


@router.delete("/alerts")
def clear_alerts(db: Session = Depends(get_db), _role: str = Depends(require_admin)):
    """Clear the live feed by marking every pending candidate as rejected."""
    db.query(VehicleTrack).filter(VehicleTrack.status == TrackStatus.completed).update(
        {"status": TrackStatus.rejected}, synchronize_session=False
    )
    db.commit()
    return {"status": "ok"}


@router.get("/alerts/recent")
def get_recent_alerts(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """Most recent candidates across all active targets."""
    rows = (
        db.query(VehicleTrack, InvestigationTarget)
        .join(InvestigationTarget, VehicleTrack.target_id == InvestigationTarget.id)
        .filter(InvestigationTarget.status == TargetStatus.active)
        .order_by(desc(VehicleTrack.started_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "candidate_id": str(t.id),
            "camera_id": t.camera_id,
            "track_id": t.track_id,
            "score": t.final_score,
            "tier": t.tier,
            "status": t.status.value,
            "started_at": _iso(t.started_at),
            "ocr_plate": t.ocr_consensus.get("best_plate") if t.ocr_consensus else None,
            "target": {
                "id": str(target.id),
                "plate_number": target.plate_number,
                "vehicle_type": _value(target.vehicle_type),
                "vehicle_color": target.vehicle_color,
                "category": target.category.value,
                "priority": target.priority.value,
            },
        }
        for t, target in rows
    ]


@router.get("/pipeline-status")
def get_pipeline_status(db: Session = Depends(get_db)):
    """Whether any pipeline is currently running, based on heartbeat recency."""
    recent = _recent_heartbeats(db)
    return {
        "online": bool(recent),
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
                "last_heartbeat": _iso(hb.last_heartbeat),
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


# ---- Observations ----

@router.get("/observations/history")
def get_observation_history(
    camera_id: Optional[str] = Query(None),
    start_time: Optional[float] = Query(None, description="Unix seconds, UTC"),
    end_time: Optional[float] = Query(None, description="Unix seconds, UTC"),
    vehicle_type: Optional[str] = Query(None),
    color: Optional[str] = Query(None),
    plate: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(VehicleObservation)
    if camera_id:
        query = query.filter(VehicleObservation.camera_id == camera_id)
    if start_time is not None:
        query = query.filter(VehicleObservation.observed_at >= datetime.utcfromtimestamp(start_time))
    if end_time is not None:
        query = query.filter(VehicleObservation.observed_at <= datetime.utcfromtimestamp(end_time))
    if vehicle_type:
        query = query.filter(VehicleObservation.vehicle_type == vehicle_type)
    if color:
        query = query.filter(VehicleObservation.color == color)
    if plate:
        query = query.filter(VehicleObservation.plate.ilike(f"%{plate}%"))

    return [
        {
            "id": str(obs.id),
            "camera_id": obs.camera_id,
            "session_id": obs.session_id,
            "track_id": obs.track_id,
            "status": _value(obs.status),
            "timestamp_source": _value(obs.timestamp_source),
            "mode": _value(obs.mode),
            "observed_at": _iso(obs.observed_at),
            "source_pts_start": obs.source_pts_start,
            "source_pts_end": obs.source_pts_end,
            "vehicle_type": _value(obs.vehicle_type),
            "color": obs.color,
            "color_confidence": obs.color_confidence,
            "plate": obs.plate,
            "plate_confidence": obs.plate_confidence,
            "evidence_completeness": obs.evidence_completeness,
            "overall_confidence": obs.overall_confidence,
        }
        for obs in query.order_by(desc(VehicleObservation.observed_at)).limit(limit)
    ]


@router.get("/observations/{observation_id}/evidence")
def get_observation_evidence(observation_id: UUID, db: Session = Depends(get_db)):
    """Evidence frames for an observation, only through its direct candidate link.

    Observations without ``candidate_id`` return an empty result rather than
    guessing from matching camera/track attributes.
    """
    observation = db.query(VehicleObservation).filter(VehicleObservation.id == observation_id).first()
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


@router.post("/observations/{observation_id}/verify")
def verify_observation(
    observation_id: UUID,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
    if not db.query(VehicleObservation).filter(VehicleObservation.id == observation_id).first():
        raise HTTPException(status_code=404, detail="Observation not found")
    process_verification_event(
        db,
        EvidenceReviewEntityType.VEHICLE_OBSERVATION,
        str(observation_id),
        EvidenceReviewAction(req.action),
        req.reviewer,
        req.review_note,
    )
    return {"status": "ok"}


@router.post("/links/{link_id}/verify")
def verify_link(
    link_id: UUID,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    _role: str = Depends(require_admin),
):
    if not db.query(CrossCameraLinkCandidate).filter(CrossCameraLinkCandidate.id == link_id).first():
        raise HTTPException(status_code=404, detail="Link not found")
    process_verification_event(
        db,
        EvidenceReviewEntityType.CROSS_CAMERA_LINK,
        str(link_id),
        EvidenceReviewAction(req.action),
        req.reviewer,
        req.review_note,
    )
    return {"status": "ok"}


# ---- Route reconstruction ----

def _chain_observations(db: Session, chain: RouteChain) -> List[VehicleObservation]:
    return (
        db.query(VehicleObservation)
        .join(RouteChainObservation, RouteChainObservation.observation_id == VehicleObservation.id)
        .filter(RouteChainObservation.route_chain_id == chain.id)
        .order_by(RouteChainObservation.sequence_order)
        .all()
    )


def _chain_links(db: Session, chain: RouteChain) -> List[CrossCameraLinkCandidate]:
    return (
        db.query(CrossCameraLinkCandidate)
        .join(RouteChainLink, RouteChainLink.link_candidate_id == CrossCameraLinkCandidate.id)
        .filter(RouteChainLink.route_chain_id == chain.id)
        .order_by(RouteChainLink.sequence_order)
        .all()
    )


@router.get("/{target_id}/history")
def get_target_history(target_id: UUID, db: Session = Depends(get_db)):
    """Reconstructed routes for a target: the current (non-superseded) route chains."""
    target = _get_target_or_404(db, target_id)
    chains = db.query(RouteChain).filter(RouteChain.target_id == target_id, RouteChain.is_superseded == 0).all()

    result_chains = []
    for c in chains:
        observations = [
            {
                "id": str(obs.id),
                "camera_id": obs.camera_id,
                "observed_at": _iso(obs.observed_at),
                "status": _value(obs.status),
                "verified_action": _value(obs.human_review_state),
            }
            for obs in _chain_observations(db, c)
        ]
        links = [
            {
                "id": str(link.id),
                "source_id": str(link.source_observation_id),
                "destination_id": str(link.destination_observation_id),
                "temporal_feasibility": link.temporal_feasibility,
                "machine_assessment": _value(link.machine_assessment),
                "link_score": link.link_score,
                "verified_action": _value(link.human_review_state),
            }
            for link in _chain_links(db, c)
        ]
        result_chains.append({
            "route_chain_id": str(c.id),
            "status": _value(c.status),
            "mode": _value(c.mode),
            "cameras_visited": [o["camera_id"] for o in observations],
            "timestamps": [o["observed_at"] for o in observations],
            "observations": observations,
            "links": links,
        })

    return {
        "target": {
            "id": str(target.id),
            "vehicle_type": _value(target.vehicle_type),
            "color": target.vehicle_color,
            "plate": target.plate_number,
            "status": _value(target.status),
        },
        "route_chains": result_chains,
    }


@router.get("/{target_id}/timeline")
def get_target_timeline(target_id: UUID, db: Session = Depends(get_db)):
    """Chronological investigation timeline: observations, route segments and reviews."""
    target = _get_target_or_404(db, target_id)
    state = db.query(InvestigationState).filter(InvestigationState.target_id == target_id).first()

    timeline = [{"type": "TARGET_CREATED", "timestamp": _iso(target.created_at)}]

    # Reviews are scoped to this target's own entities, so another
    # investigation's analyst activity never leaks into this timeline.
    track_ids = [t for (t,) in db.query(VehicleTrack.id).filter(VehicleTrack.target_id == target.id)]
    observation_ids = {
        str(o)
        for (o,) in db.query(VehicleObservation.id).filter(VehicleObservation.candidate_id.in_(track_ids))
    } if track_ids else set()
    chain_ids = {str(c) for (c,) in db.query(RouteChain.id).filter(RouteChain.target_id == target.id)}
    link_ids = {
        str(l)
        for (l,) in db.query(RouteChainLink.link_candidate_id)
        .join(RouteChain, RouteChain.id == RouteChainLink.route_chain_id)
        .filter(RouteChain.target_id == target.id)
    }
    review_entity_ids = observation_ids | chain_ids | link_ids
    if review_entity_ids:
        for r in db.query(EvidenceReview).filter(EvidenceReview.entity_id.in_(review_entity_ids)):
            timeline.append({
                "type": "REVIEW_EVENT",
                "action": _value(r.action),
                "entity_type": _value(r.entity_type),
                "entity_id": str(r.entity_id),
                "reviewer": r.reviewer,
                "timestamp": _iso(r.created_at),
            })

    chains = db.query(RouteChain).filter(RouteChain.target_id == target_id, RouteChain.is_superseded == 0).all()
    for c in chains:
        timeline.append({
            "type": "ROUTE_HYPOTHESIS",
            "chain_id": str(c.id),
            "status": _value(c.status),
            "timestamp": _iso(c.created_at),
        })
        links = _chain_links(db, c)
        for i, obs in enumerate(_chain_observations(db, c)):
            timeline.append({
                "type": "OBSERVATION",
                "observation_id": str(obs.id),
                "camera_id": obs.camera_id,
                "timestamp": _iso(obs.observed_at),
                "timestamp_source": _value(obs.timestamp_source),
                "mode": _value(obs.mode),
                "human_review_state": _value(obs.human_review_state),
            })
            if i < len(links):
                link = links[i]
                timeline.append({
                    "type": "ROUTE_SEGMENT",
                    "state": "OBSERVED_LINK",
                    "link_id": str(link.id),
                    "score": link.link_score,
                    "timestamp": _iso(link.created_at),
                    "machine_assessment": _value(link.machine_assessment),
                    "human_review_state": _value(link.human_review_state),
                    "mode": _value(link.mode),
                })

    timeline.sort(key=lambda x: x.get("timestamp") or "")

    last = _last_observed(db, target_id)
    last_observed = None
    if last:
        last_observed = _observation_summary(last)
        last_observed["age_seconds"] = (
            (datetime.utcnow() - last.observed_at).total_seconds() if last.observed_at else None
        )

    return {
        "target": {"id": str(target.id), "status": _value(target.status)},
        "state": {
            "status": _value(state.status),
            "state_version": state.state_version,
            "last_processed_at": _iso(state.last_processed_at),
            "search_truncated": bool(state.search_truncated),
        } if state else None,
        "last_observed": last_observed,
        "timeline": timeline,
    }


@router.get("/{target_id}/last_seen")
def get_target_last_seen(target_id: UUID, db: Session = Depends(get_db)):
    """Last observation of a target. A historical sighting, never a claim of current location."""
    _get_target_or_404(db, target_id)
    obs = _last_observed(db, target_id)
    if not obs:
        return {"last_seen": None}
    return {
        "last_seen": {
            "observation_id": str(obs.id),
            "camera_id": obs.camera_id,
            "timestamp": _iso(obs.observed_at),
            "timestamp_source": _value(obs.timestamp_source),
            "evidence_completeness": obs.evidence_completeness,
            "status": _value(obs.status),
            "human_review_state": _value(obs.human_review_state),
            "is_stale": True,
            "tag": "LAST_OBSERVED",
        }
    }
