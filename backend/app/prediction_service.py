"""
Prediction Service — Phase 8.1 hardened implementation.

Terminology:
  - hypothesis_score: NOT a probability. A monotonic function of sample support.
  - dispersion: Statistical spread (IQR) of observed time deltas.
  - eligible_support: Number of ELIGIBLE transitions used.
  - unique_session_count: Distinct sessions contributing transitions.
  - unique_investigation_count: Distinct investigations contributing transitions.

The temporal cutoff uses CameraTransitionRecord.created_at, which represents
when the system learned about the transition. This is correct for walk-forward
backtesting: at time T, the system could only have used transitions it had
already ingested (F6).
"""
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.models import (
    VehicleObservation,
    CrossCameraLinkCandidate,
    CameraTransitionRecord,
    TransitionEligibility,
    InvestigationState,
    CameraGraphEdge,
    TimestampQuality,
    TimestampSource,
    HumanReviewState,
    MachineAssessment,
    Mode,
    RecordingSession,
    ProvenanceType,
)

# F5: Configurable threshold — does NOT automatically mean "reliable"
MIN_SAMPLE_THRESHOLD = 5


def evaluate_transition_eligibility(
    db: Session,
    link: CrossCameraLinkCandidate,
    obs_a: VehicleObservation,
    obs_b: VehicleObservation,
) -> tuple:
    """
    Strict eligibility policy. Returns (TransitionEligibility, reason_string).
    """
    if link.human_review_state == HumanReviewState.REJECTED:
        return TransitionEligibility.INELIGIBLE, "rejected_link"

    if (
        obs_a.human_review_state == HumanReviewState.REJECTED
        or obs_b.human_review_state == HumanReviewState.REJECTED
    ):
        return TransitionEligibility.INELIGIBLE, "rejected_observation"

    if getattr(obs_a, 'is_playback_repetition', False) or getattr(obs_b, 'is_playback_repetition', False):
        return TransitionEligibility.INELIGIBLE, "playback_repetition"

    if obs_a.mode != obs_b.mode:
        return TransitionEligibility.INELIGIBLE, "simulation_mismatch"

    # F16: Ensure session integrity! Observations from different streams/runs cannot link.
    if obs_a.session_id != obs_b.session_id:
        return TransitionEligibility.INELIGIBLE, "session_mismatch"

    # F19: Require valid recording session provenance
    rec_a = None
    rec_b = None
    if obs_a.recording_id:
        rec_a = db.query(RecordingSession).filter_by(id=obs_a.recording_id).first()
    if obs_b.recording_id:
        rec_b = db.query(RecordingSession).filter_by(id=obs_b.recording_id).first()

    if not rec_a or not rec_b:
        return TransitionEligibility.INELIGIBLE, "unknown_provenance"
    
    if rec_a.provenance_type != rec_b.provenance_type:
        return TransitionEligibility.INELIGIBLE, "incompatible_provenance"
        
    if rec_a.provenance_type not in (ProvenanceType.LIVE_SYNCHRONIZED, ProvenanceType.HISTORICAL_SYNCHRONIZED):
        return TransitionEligibility.INELIGIBLE, "incompatible_provenance"

    if rec_a.synchronization_status != "synchronized" or rec_b.synchronization_status != "synchronized":
        return TransitionEligibility.INELIGIBLE, "unsynchronized_session"

    # F16: Validate edge exists and is enabled before checking temporal bounds
    if not link.camera_edge_id:
        return TransitionEligibility.INELIGIBLE, "missing_edge"
        
    edge = db.query(CameraGraphEdge).filter_by(id=link.camera_edge_id).first()
    if not edge:
        return TransitionEligibility.INELIGIBLE, "unknown_edge"
    if edge.enabled == 0:
        return TransitionEligibility.INELIGIBLE, "disabled_edge"

    if link.temporal_feasibility == "impossible":
        return TransitionEligibility.INELIGIBLE, "impossible_timing"

    if obs_a.observed_at and obs_b.observed_at and obs_b.observed_at <= obs_a.observed_at:
        return TransitionEligibility.INELIGIBLE, "backward_time_travel"

    if link.temporal_feasibility == "unverified_cross_camera_time":
        return TransitionEligibility.INELIGIBLE, "unverified_cross_camera_time"

    if link.temporal_feasibility != "valid":
        return TransitionEligibility.INELIGIBLE, "unverified_cross_camera_timing"

    if (
        obs_a.timestamp_source not in (TimestampSource.ABSOLUTE_TIMESTAMP, TimestampSource.SOURCE_PTS)
        or obs_b.timestamp_source not in (TimestampSource.ABSOLUTE_TIMESTAMP, TimestampSource.SOURCE_PTS)
        or obs_a.timestamp_source != obs_b.timestamp_source
    ):
        return TransitionEligibility.INELIGIBLE, "non_comparable_timestamp_source"

    if (
        obs_a.timestamp_quality == TimestampQuality.UNRELIABLE
        or obs_b.timestamp_quality == TimestampQuality.UNRELIABLE
    ):
        return TransitionEligibility.INELIGIBLE, "unreliable_timestamp"

    if link.evidence_completeness < 0.5:
        return TransitionEligibility.INELIGIBLE, "insufficient_evidence"

    return TransitionEligibility.ELIGIBLE, None


def record_transition(
    db: Session,
    link: CrossCameraLinkCandidate,
    obs_a: VehicleObservation,
    obs_b: VehicleObservation,
    graph_version: int,
    session_id: str = None,
    investigation_id: str = None,
):
    """
    Creates a durable CameraTransitionRecord.
    F4: Populates session_id and investigation_id for statistical deduplication.
    """
    status, reason = evaluate_transition_eligibility(db, link, obs_a, obs_b)

    if not obs_a.observed_at or not obs_b.observed_at:
        return None

    time_delta = (obs_b.observed_at - obs_a.observed_at).total_seconds()

    # Idempotency: unique index on (src_obs, dst_obs, edge, graph_version)
    existing = (
        db.query(CameraTransitionRecord)
        .filter_by(
            source_observation_id=obs_a.id,
            destination_observation_id=obs_b.id,
            camera_edge_id=link.camera_edge_id,
            graph_version=graph_version,
        )
        .first()
    )
    if existing:
        return existing

    record = CameraTransitionRecord(
        source_camera_id=obs_a.camera_id,
        destination_camera_id=obs_b.camera_id,
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id,
        camera_edge_id=link.camera_edge_id,
        observed_time_delta=time_delta,
        timestamp_source=obs_b.timestamp_source,
        timestamp_quality=obs_b.timestamp_quality,
        temporal_feasibility=link.temporal_feasibility,
        attribute_evidence=link.attribute_comparisons,
        evidence_completeness=link.evidence_completeness,
        link_score=link.link_score,
        machine_assessment=link.machine_assessment,
        human_review_state=link.human_review_state,
        transition_status=status,
        exclusion_reason=reason,
        graph_version=graph_version,
        mode=obs_b.mode,
        # F4: statistical provenance
        session_id=session_id or obs_b.session_id,
        investigation_id=investigation_id,
    )
    db.add(record)
    return record


class PredictionService:
    @staticmethod
    def get_total_eligible_from_source(
        db: Session,
        source_camera_id: str,
        mode: Mode,
        data_cutoff: Optional[datetime] = None,
        exclude_session_id: Optional[str] = None,
    ) -> int:
        """
        Returns the total count of ELIGIBLE transitions from a source camera
        (across all destinations). Used to compute relative frequency.
        """
        query = db.query(
            func.count(CameraTransitionRecord.id)
        ).filter(
            CameraTransitionRecord.source_camera_id == source_camera_id,
            CameraTransitionRecord.transition_status == TransitionEligibility.ELIGIBLE,
            CameraTransitionRecord.mode == mode,
        )
        if data_cutoff:
            query = query.filter(CameraTransitionRecord.created_at < data_cutoff)
        if exclude_session_id:
            query = query.filter(CameraTransitionRecord.session_id != exclude_session_id)
        result = query.scalar()
        return result or 0

    @staticmethod
    def get_transition_statistics(
        db: Session,
        source_camera_id: str,
        mode: Mode,
        data_cutoff: Optional[datetime] = None,
        exclude_session_id: Optional[str] = None,
    ):
        """
        Aggregates eligible transition statistics out of a source camera.

        F4: Includes unique_session_count and unique_investigation_count
            to prevent conflating repeated observations from the same session
            with independent vehicle samples.

        F6: Temporal cutoff uses created_at (when the system learned about
            the transition), which is correct for walk-forward evaluation.
        """
        query = db.query(
            CameraTransitionRecord.destination_camera_id,
            func.count(CameraTransitionRecord.id).label("sample_count"),
            func.avg(CameraTransitionRecord.observed_time_delta).label("avg_time"),
            func.min(CameraTransitionRecord.observed_time_delta).label("min_time"),
            func.max(CameraTransitionRecord.observed_time_delta).label("max_time"),
            # F4: unique session/investigation counts
            func.count(func.distinct(CameraTransitionRecord.session_id)).label(
                "unique_session_count"
            ),
            func.count(func.distinct(CameraTransitionRecord.investigation_id)).label(
                "unique_investigation_count"
            ),
        ).filter(
            CameraTransitionRecord.source_camera_id == source_camera_id,
            CameraTransitionRecord.transition_status == TransitionEligibility.ELIGIBLE,
            CameraTransitionRecord.mode == mode,
        )

        if data_cutoff:
            query = query.filter(CameraTransitionRecord.created_at < data_cutoff)
        if exclude_session_id:
            query = query.filter(CameraTransitionRecord.session_id != exclude_session_id)

        stats = query.group_by(CameraTransitionRecord.destination_camera_id).all()
        return stats

    @staticmethod
    def get_transition_dispersion(
        db: Session,
        source_camera_id: str,
        destination_camera_id: str,
        mode: Mode,
        data_cutoff: Optional[datetime] = None,
        exclude_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Compute IQR dispersion for a specific camera pair.
        Separated from main stats query for databases that may not support
        percentile_cont in GROUP BY.
        """
        query = db.query(
            CameraTransitionRecord.observed_time_delta
        ).filter(
            CameraTransitionRecord.source_camera_id == source_camera_id,
            CameraTransitionRecord.destination_camera_id == destination_camera_id,
            CameraTransitionRecord.transition_status == TransitionEligibility.ELIGIBLE,
            CameraTransitionRecord.mode == mode,
        )
        if data_cutoff:
            query = query.filter(CameraTransitionRecord.created_at < data_cutoff)
        if exclude_session_id:
            query = query.filter(CameraTransitionRecord.session_id != exclude_session_id)

        deltas = sorted([r[0] for r in query.all()])
        if len(deltas) < 2:
            return {"median": None, "p25": None, "p75": None, "iqr": None}

        n = len(deltas)
        p25_idx = max(0, int(n * 0.25))
        p50_idx = max(0, int(n * 0.50))
        p75_idx = min(n - 1, int(n * 0.75))

        return {
            "median": deltas[p50_idx],
            "p25": deltas[p25_idx],
            "p75": deltas[p75_idx],
            "iqr": deltas[p75_idx] - deltas[p25_idx],
        }

    @staticmethod
    def predict_next_cameras(
        db: Session,
        target_id: str,
        data_cutoff: Optional[datetime] = None,
        exclude_session_id: Optional[str] = None,
        baseline_version: str = "3.0",
    ) -> List[Dict[str, Any]]:
        state = (
            db.query(InvestigationState).filter_by(target_id=target_id).first()
        )
        if not state or not state.last_seen_observation_id:
            return []

        last_obs = (
            db.query(VehicleObservation)
            .filter_by(id=state.last_seen_observation_id)
            .first()
        )
        if not last_obs:
            return []

        generated_at = datetime.utcnow()

        stats = PredictionService.get_transition_statistics(
            db, last_obs.camera_id, last_obs.mode, data_cutoff, exclude_session_id
        )

        # Phase 10: total eligible from this source for relative frequency
        total_from_source = PredictionService.get_total_eligible_from_source(
            db, last_obs.camera_id, last_obs.mode, data_cutoff, exclude_session_id
        )

        predictions = []
        for stat in stats:
            dest_cam = stat.destination_camera_id
            samples = stat.sample_count
            unique_sessions = stat.unique_session_count
            unique_investigations = stat.unique_investigation_count

            # Phase 10: relative frequency — what fraction of all transitions
            # from this source go to this destination
            relative_frequency = (
                samples / total_from_source if total_from_source > 0 else 0.0
            )

            # F5: Base prediction dict with separated support/dispersion fields
            base = {
                "prediction_id": str(uuid.uuid4()),
                "source_observation_id": str(last_obs.id),
                "candidate_camera_id": dest_cam,
                # F10: Full provenance
                "generated_at": generated_at.isoformat(),
                "data_cutoff": data_cutoff.isoformat() if data_cutoff else None,
                "model_version": baseline_version,
                "graph_version": state.graph_version,
                "mode": last_obs.mode.value,
                # F4/F5: Separated support metrics — NOT probability
                "raw_transition_count": samples,
                "eligible_transition_count": samples,
                "unique_session_count": unique_sessions,
                "unique_investigation_count": unique_investigations,
                "min_sample_threshold": MIN_SAMPLE_THRESHOLD,
                # Phase 10/11: relative frequency context
                "total_from_source": total_from_source,
                "relative_frequency": round(relative_frequency, 4),
            }

            if samples < MIN_SAMPLE_THRESHOLD:
                base.update(
                    {
                        "estimated_time_window": None,
                        "hypothesis_score": 0.0,
                        "dispersion": None,
                        "status": "INSUFFICIENT_DATA",
                        "limitations": [
                            f"Sample count ({samples}) below configured threshold ({MIN_SAMPLE_THRESHOLD})"
                        ],
                    }
                )
            else:
                # Compute dispersion for this specific pair
                disp = PredictionService.get_transition_dispersion(
                    db, last_obs.camera_id, dest_cam, last_obs.mode, data_cutoff, exclude_session_id
                )

                median_t = disp["median"] if disp["median"] is not None else float(stat.avg_time)
                p25_t = disp["p25"] if disp["p25"] is not None else float(stat.min_time)
                p75_t = disp["p75"] if disp["p75"] is not None else float(stat.max_time)
                iqr = disp["iqr"] if disp["iqr"] is not None else (p75_t - p25_t)

                est_min = last_obs.observed_at + timedelta(seconds=p25_t)
                est_max = last_obs.observed_at + timedelta(seconds=p75_t)

                # Phase 11: Switch scoring based on baseline_version
                support_adequacy = min(1.0, samples / 50.0)
                
                if baseline_version == "2.1":
                    score = support_adequacy
                else:
                    # 3.0
                    score = support_adequacy * relative_frequency

                base.update(
                    {
                        "estimated_time_window": {
                            "start": est_min.isoformat(),
                            "end": est_max.isoformat(),
                            "median_seconds": median_t,
                            "dispersion_seconds": iqr,
                            # F17: window method provenance
                            "window_method": "interquartile_range",
                        },
                        "hypothesis_score": score,
                        "dispersion": {
                            "p25_seconds": p25_t,
                            "median_seconds": median_t,
                            "p75_seconds": p75_t,
                            "iqr_seconds": iqr,
                        },
                        "status": "HYPOTHESIS",
                        "limitations": [
                            "Hypothesis score is NOT a probability",
                            "No direct identity confirmation",
                            "Prediction is not a confirmed location",
                            "Timestamp uncertainty not quantified",
                        ],
                    }
                )

            predictions.append(base)

        return predictions

    @staticmethod
    def get_dataset_readiness_report(db: Session) -> Dict[str, Any]:
        """
        Generates a readiness report assessing the dataset's suitability for advanced ML.
        """
        all_transitions = db.query(CameraTransitionRecord).all()
        
        total_transitions = len(all_transitions)
        real_transitions = [t for t in all_transitions if t.mode == Mode.REAL]
        simulated_transitions = [t for t in all_transitions if t.mode == Mode.SIMULATED]
        
        real_eligible = [t for t in real_transitions if t.transition_status == TransitionEligibility.ELIGIBLE]
        real_ineligible = [t for t in real_transitions if t.transition_status == TransitionEligibility.INELIGIBLE]
        real_unknown = [t for t in real_transitions if t.transition_status == TransitionEligibility.UNKNOWN]

        sim_eligible = [t for t in simulated_transitions if t.transition_status == TransitionEligibility.ELIGIBLE]
        sim_ineligible = [t for t in simulated_transitions if t.transition_status == TransitionEligibility.INELIGIBLE]
        sim_unknown = [t for t in simulated_transitions if t.transition_status == TransitionEligibility.UNKNOWN]

        
        unique_sessions = len(set(t.session_id for t in real_eligible if t.session_id))
        unique_investigations = len(set(t.investigation_id for t in real_eligible if t.investigation_id))
        
        # F19 Provenance Metrics
        all_recordings = db.query(RecordingSession).all()
        synchronized_session_count = sum(1 for r in all_recordings if r.provenance_type in (ProvenanceType.LIVE_SYNCHRONIZED, ProvenanceType.HISTORICAL_SYNCHRONIZED) and r.synchronization_status == "synchronized")
        unsynchronized_session_count = sum(1 for r in all_recordings if r.synchronization_status != "synchronized")
        unknown_provenance_count = sum(1 for r in all_recordings if r.provenance_type == ProvenanceType.UNKNOWN)
        unique_recording_sessions = len(all_recordings)

        provenance_breakdown = {}
        for r in all_recordings:
            ptype = r.provenance_type.value
            provenance_breakdown[ptype] = provenance_breakdown.get(ptype, 0) + 1

        ineligible_reasons = {}
        for t in real_ineligible:
            reason = t.exclusion_reason or "unknown"
            ineligible_reasons[reason] = ineligible_reasons.get(reason, 0) + 1

        # Diversity calculations
        edge_counts = {}
        session_edges = {}
        unique_cameras = set()
        unreliable_count = 0
        
        for t in real_eligible:
            edge_id = f"{t.source_camera_id}->{t.destination_camera_id}"
            edge_counts[edge_id] = edge_counts.get(edge_id, 0) + 1
            
            if t.session_id:
                if t.session_id not in session_edges:
                    session_edges[t.session_id] = set()
                session_edges[t.session_id].add(edge_id)
                
            unique_cameras.add(t.source_camera_id)
            unique_cameras.add(t.destination_camera_id)
            
            q = t.timestamp_quality.value if hasattr(t.timestamp_quality, 'value') else t.timestamp_quality
            if q == "unreliable":
                unreliable_count += 1
                
        unique_edges = len(edge_counts)
        camera_coverage = len(unique_cameras)
        
        max_edge_count = max(edge_counts.values()) if edge_counts else 0
        repeated_route_concentration = (max_edge_count / len(real_eligible)) if real_eligible else 0.0
        
        unreliable_timestamp_proportion = (unreliable_count / len(real_eligible)) if real_eligible else 0.0
        
        avg_edges_per_session = sum(len(edges) for edges in session_edges.values()) / len(session_edges) if session_edges else 0.0
        
        # Calculate latest data timestamp from ALL real transitions, not just eligible ones
        latest_data_timestamp = None
        earliest_data_timestamp = None
        latest_time = None
        earliest_time = None
        if real_transitions:
            latest_time = max(t.created_at for t in real_transitions if t.created_at)
            earliest_time = min(t.created_at for t in real_transitions if t.created_at)
            if latest_time:
                latest_data_timestamp = latest_time.isoformat()
            if earliest_time:
                earliest_data_timestamp = earliest_time.isoformat()
        
        # F16: Compute real transitions per hour
        real_transitions_per_hour = 0.0
        if latest_time and earliest_time and latest_time > earliest_time:
            hours = (latest_time - earliest_time).total_seconds() / 3600.0
            if hours > 0:
                real_transitions_per_hour = len(real_eligible) / hours
        elif len(real_eligible) > 0:
            # If all data arrived in the same second, just say 1 per hour as a fallback or 0
            pass
        
        quality_dist = {}
        for t in real_eligible:
            q = t.timestamp_quality.value if hasattr(t.timestamp_quality, 'value') else t.timestamp_quality
            quality_dist[q] = quality_dist.get(q, 0) + 1
            
        version_dist = {}
        for t in real_eligible:
            v = str(t.graph_version)
            version_dist[v] = version_dist.get(v, 0) + 1

        rejection_reasons = {}
        for t in real_ineligible:
            reason = t.exclusion_reason or "unknown"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            
        suspected_loop_count = rejection_reasons.get("playback_repetition", 0)
        unverified_sync_count = rejection_reasons.get("unverified_cross_camera_time", 0)
        missing_edge = rejection_reasons.get("missing_edge", 0)
        unknown_edge = rejection_reasons.get("unknown_edge", 0)
        disabled_edge = rejection_reasons.get("disabled_edge", 0)
        
        # Calculate true source-time span from valid observations
        all_real_obs = db.query(VehicleObservation).filter_by(mode=Mode.REAL).all()
        unique_source_events = len(set(obs.source_fingerprint for obs in all_real_obs if obs.source_fingerprint))
        repeated_event_count = len(all_real_obs) - unique_source_events if all_real_obs else 0
        
        source_time_span_seconds = 0.0
        valid_obs = [obs for obs in all_real_obs if not obs.is_playback_repetition]
        if valid_obs:
            max_source = max((obs.observed_at for obs in valid_obs if obs.observed_at), default=None)
            min_source = min((obs.observed_at for obs in valid_obs if obs.observed_at), default=None)
            if max_source and min_source:
                source_time_span_seconds = (max_source - min_source).total_seconds()
            
        rejected_count = len([t for t in all_transitions if t.transition_status == TransitionEligibility.INELIGIBLE])
        
        # Readiness logic
        is_ready = True
        reasons = []
        
        if len(real_eligible) < 100:
            is_ready = False
            reasons.append(f"Insufficient REAL transitions (found {len(real_eligible)}, need 100)")
            
        if unique_sessions < 10:
            is_ready = False
            reasons.append(f"Insufficient session diversity (found {unique_sessions}, need 10)")
            
        if unique_edges < 5:
            is_ready = False
            reasons.append(f"Insufficient spatial/edge diversity (found {unique_edges}, need 5)")

        status = "READY_FOR_BENCHMARKING" if is_ready else "NOT_READY"
        if is_ready:
            reasons.append(
                "Dataset meets minimum configured readiness criteria (100 transitions, 10 sessions, 5 unique edges). "
                "WARNING: This is a configured operational minimum, NOT proof of statistical sufficiency."
            )
            
        return {
            "status": status,
            "reasons": reasons,
            "metrics": {
                "total_transitions": total_transitions,
                "real_count": len(real_transitions),
                "simulated_count": len(simulated_transitions),
                "real_eligible_count": len(real_eligible),
                "real_ineligible_count": len(real_ineligible),
                "real_unknown_count": len(real_unknown),
                "real_transitions_per_hour": real_transitions_per_hour,
                "sim_eligible_count": len(sim_eligible),
                "sim_ineligible_count": len(sim_ineligible),
                "sim_unknown_count": len(sim_unknown),
                "unique_sessions": unique_sessions,
                "unique_investigations": unique_investigations,
                "unique_edges": unique_edges,
                "rejected_count": rejected_count,
                "earliest_data_timestamp": earliest_data_timestamp,
                "latest_data_timestamp": latest_data_timestamp,
                "source_time_span_seconds": source_time_span_seconds,
                "suspected_loop_count": suspected_loop_count,
                "synchronized_transition_count": len(real_eligible),
                "synchronized_session_count": synchronized_session_count,
                "unsynchronized_session_count": unsynchronized_session_count,
                "unknown_provenance_count": unknown_provenance_count,
                "unique_recording_sessions": unique_recording_sessions,
                "unsynchronized_transition_count": unverified_sync_count,
                "unique_source_event_count": unique_source_events,
                "repeated_event_count": repeated_event_count,
                "replay_duplicate_count": suspected_loop_count,
                "disconnected_unvalidated_edge_info": {
                    "missing_edge": missing_edge,
                    "unknown_edge": unknown_edge,
                    "disabled_edge": disabled_edge
                },
                "camera_coverage": camera_coverage,
                "repeated_route_concentration": round(repeated_route_concentration, 4),
                "unreliable_timestamp_proportion": round(unreliable_timestamp_proportion, 4),
                "per_session_route_diversity": round(avg_edges_per_session, 4),
            },
            "distributions": {
                "timestamp_quality": quality_dist,
                "graph_versions": version_dist,
                "rejection_reasons": rejection_reasons,
                "per_edge_sample_counts": edge_counts,
                "eligible_transitions_by_edge": edge_counts,
                "provenance_breakdown": provenance_breakdown,
            }
        }
