"""
Incremental investigation state engine: cross-camera links and route chains.

Transaction strategy:
  Event claiming uses a short INSERT ... ON CONFLICT to atomically claim the event_id.
  Processing runs in a separate transaction. If processing fails, the event is
  marked FAILED with retry semantics. This avoids holding a long-lived transaction
  across expensive linking/chain-extension work.
"""
import sys
import os
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Set

from sqlalchemy.orm import Session, aliased
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, desc, case, func

from app.models import (
    VehicleObservation,
    CrossCameraLinkCandidate,
    CameraGraphEdge,
    GraphVersion,
    RouteChain,
    RouteChainObservation,
    RouteChainLink,
    RouteChainStatus,
    InvestigationState,
    HumanReviewState,
    MachineAssessment,
    TimestampQuality,
    Mode,
    EvidenceReview,
    EvidenceReviewAction,
    EvidenceReviewEntityType,
    VehicleTrack,
    InvestigationEvent,
    InvestigationEventType,
    EventProcessingStatus,
    Camera,
)

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "anpr"))
from linking import compare_observations  # noqa: E402 - needs the sys.path entry above

MAX_TIME_WINDOW_HOURS = 2
# Fastest plausible average speed between two cameras when no configured
# CameraGraphEdge exists. Straight-line distance / this speed gives the
# minimum believable travel time; anything faster is a different vehicle
# (or an OCR misread), not the same one.
MAX_PLAUSIBLE_SPEED_KMH = 120.0
# Taken off that minimum: stream latency differs per camera and registry
# coordinates are approximate (seed_data places cameras from their names),
# so a genuine fast hop between nearby cameras must not read as impossible.
TIMING_SLACK_SECONDS = 60.0
# Upper bound on cascade: never traverse more than this many entities
MAX_CASCADE_ENTITIES = 500


def compute_fingerprint(
    target_id: str,
    obs_ids: List[str],
    link_ids: List[str],
    mode: Mode,
    graph_version: int,
) -> str:
    """Deterministic route fingerprint based on observations and links."""
    data = f"{target_id}:{','.join(obs_ids)}:{','.join(link_ids)}:{mode.value}:{graph_version}"
    return hashlib.sha256(data.encode()).hexdigest()


def get_current_graph_version(db: Session, mode: Mode) -> int:
    """
    Returns the current global graph version for the given mode.
    If no version record exists yet, creates version 1.
    """
    latest = (
        db.query(GraphVersion)
        .filter(GraphVersion.mode == mode)
        .order_by(desc(GraphVersion.version))
        .first()
    )
    if latest:
        return latest.version
    # Bootstrap: create initial version
    gv = GraphVersion(version=1, reason="initial", mode=mode)
    db.add(gv)
    db.flush()
    return 1


def bump_graph_version(db: Session, mode: Mode, reason: str, edge_id=None) -> int:
    """
    Increments the global graph version. Called when edges are created/modified/disabled.
    Returns the new version number.
    """
    current = get_current_graph_version(db, mode)
    new_version = current + 1
    gv = GraphVersion(
        version=new_version,
        reason=reason,
        affected_edge_id=edge_id,
        mode=mode,
    )
    db.add(gv)
    db.flush()
    return new_version


# ─────────────────────────────────────────────────────────
# Atomic event claiming + safe retry
# ─────────────────────────────────────────────────────────

def _claim_event(
    db: Session,
    event_id: str,
    event_type: InvestigationEventType,
    entity_id: str,
) -> Optional[InvestigationEvent]:
    """
    Atomically claim an event_id. Returns the event if we should process it,
    or None if it's already PROCESSED or not yet retryable.

    Uses a short transaction: INSERT or re-read + status check.
    If two threads race, the loser gets IntegrityError and re-reads.
    """
    # Fast path: check if already processed
    existing = db.query(InvestigationEvent).filter_by(event_id=event_id).first()
    if existing:
        if existing.status == EventProcessingStatus.PROCESSED:
            return None
        if existing.status == EventProcessingStatus.FAILED:
            if existing.next_retry_at and existing.next_retry_at > datetime.utcnow():
                return None
            # Eligible for retry
            existing.attempt_count += 1
            existing.status = EventProcessingStatus.PENDING
            db.commit()
            return existing
        # PENDING - another worker is processing. Skip.
        return None

    # Try to insert
    new_event = InvestigationEvent(
        event_id=event_id,
        event_type=event_type,
        entity_id=entity_id,
        status=EventProcessingStatus.PENDING,
        attempt_count=1,
    )
    db.add(new_event)
    try:
        db.commit()
        return new_event
    except IntegrityError:
        # Another thread inserted first - roll back and re-read
        db.rollback()
        existing = db.query(InvestigationEvent).filter_by(event_id=event_id).first()
        if existing and existing.status == EventProcessingStatus.PROCESSED:
            return None
        # The other thread is processing it. We yield.
        return None


def _mark_processed(db: Session, event: InvestigationEvent):
    """Mark event as successfully processed."""
    event.status = EventProcessingStatus.PROCESSED
    event.processed_at = datetime.utcnow()
    db.commit()


def _mark_failed(db: Session, event_id: str, error: str):
    """Mark event as failed with retry backoff."""
    event = db.query(InvestigationEvent).filter_by(event_id=event_id).first()
    if event:
        event.status = EventProcessingStatus.FAILED
        event.last_error = str(error)[:2000]
        event.next_retry_at = datetime.utcnow() + timedelta(minutes=1)
        db.commit()


def _target_observations(db: Session, target_id):
    """Observations belonging to a target, via their candidate track.

    ``candidate_id`` is the only reliable link: camera-local track numbers
    restart with every pipeline session, so matching on (camera_id, track_id)
    would pull in unrelated vehicles from earlier runs.
    """
    target_tracks = db.query(VehicleTrack.id).filter(VehicleTrack.target_id == target_id)
    return db.query(VehicleObservation).filter(
        VehicleObservation.candidate_id.in_(target_tracks.scalar_subquery())
    )


def _travel_bounds(db: Session, src_camera_id: str, dst_camera_id: str, edge) -> Optional[tuple]:
    """(min_seconds, max_seconds) a vehicle could take between two cameras.

    A configured CameraGraphEdge wins. Otherwise fall back to the registry's
    own coordinates: straight-line distance at MAX_PLAUSIBLE_SPEED_KMH is the
    fastest believable transit, and the search window caps the slowest. Returns
    None when either camera has no location, since timing can't be judged.
    """
    if edge is not None:
        if not edge.enabled:
            return None
        return edge.min_travel_time, edge.max_travel_time

    # One query on the Geography columns so PostGIS returns metres; passing
    # the loaded WKB values back in would measure in degrees instead.
    src, dst = aliased(Camera), aliased(Camera)
    distance_m = (
        db.query(func.ST_Distance(src.location, dst.location))
        .filter(src.camera_id == src_camera_id, dst.camera_id == dst_camera_id)
        .scalar()
    )
    if distance_m is None:  # either camera missing or has no location
        return None
    min_seconds = max(0.0, distance_m / (MAX_PLAUSIBLE_SPEED_KMH * 1000 / 3600) - TIMING_SLACK_SECONDS)
    return min_seconds, MAX_TIME_WINDOW_HOURS * 3600


# ─────────────────────────────────────────────────────────
# Main entry points
# ─────────────────────────────────────────────────────────

def process_new_observation(db: Session, observation_id: str):
    """
    Incremental update when a new observation is added.
    Uses atomic event claiming with processing in a separate
    transaction boundary to avoid long-lived locks.
    """
    event_id = f"NEW_OBSERVATION:{observation_id}"
    event = _claim_event(
        db, event_id, InvestigationEventType.NEW_OBSERVATION, observation_id
    )
    if event is None:
        return  # Already processed or another worker owns it

    try:
        _do_process_observation(db, observation_id, event)
    except Exception as e:
        db.rollback()
        _mark_failed(db, event_id, str(e))


def _do_process_observation(
    db: Session, observation_id: str, event: InvestigationEvent
):
    obs = db.query(VehicleObservation).filter_by(id=observation_id).first()
    if not obs:
        raise ValueError("Observation not found")

    # Only observations produced from a persisted candidate track can belong
    # to a target; see _target_observations.
    track = (
        db.query(VehicleTrack).filter_by(id=obs.candidate_id).first()
        if obs.candidate_id
        else None
    )
    if not track or not track.target_id:
        _mark_processed(db, event)
        return

    target_id = track.target_id

    # Update or create investigation state
    state = db.query(InvestigationState).filter_by(target_id=target_id).first()
    if not state:
        gv = get_current_graph_version(db, obs.mode)
        state = InvestigationState(target_id=target_id, mode=obs.mode, graph_version=gv)
        db.add(state)
        db.flush()

    state.last_processed_at = datetime.utcnow()
    state.state_version += 1

    # Use real graph version from the global tracker
    current_gv = get_current_graph_version(db, obs.mode)
    state.graph_version = current_gv

    # Find temporally relevant adjacent observations
    time_window_start = obs.observed_at - timedelta(hours=MAX_TIME_WINDOW_HOURS)
    time_window_end = obs.observed_at + timedelta(hours=MAX_TIME_WINDOW_HOURS)

    recent_obs = (
        _target_observations(db, target_id)
        .filter(
            VehicleObservation.observed_at >= time_window_start,
            VehicleObservation.observed_at <= time_window_end,
            VehicleObservation.mode == obs.mode,
            VehicleObservation.human_review_state != HumanReviewState.REJECTED,
        )
        .all()
    )

    all_edges = (
        db.query(CameraGraphEdge)
        .filter(CameraGraphEdge.mode == obs.mode)
        .all()
    )
    edge_map = {
        (e.source_camera_id, e.destination_camera_id): e for e in all_edges
    }

    new_links = []
    for other_obs in recent_obs:
        if other_obs.id == obs.id:
            continue

        if obs.observed_at < other_obs.observed_at:
            src, dst = obs, other_obs
        elif other_obs.observed_at < obs.observed_at:
            src, dst = other_obs, obs
        else:
            continue  # same timestamp - skip
            
        if src.camera_id == dst.camera_id:
            continue  # cross-camera links must be cross-camera

        edge = edge_map.get((src.camera_id, dst.camera_id))

        existing_link = (
            db.query(CrossCameraLinkCandidate)
            .filter_by(
                source_observation_id=src.id,
                destination_observation_id=dst.id,
            )
            .first()
        )
        if existing_link:
            continue

        bounds = _travel_bounds(db, src.camera_id, dst.camera_id, edge)
        res = compare_observations(src, dst, bounds)
        if res:
            link = CrossCameraLinkCandidate(
                source_observation_id=src.id,
                destination_observation_id=dst.id,
                camera_edge_id=edge.id if edge else None,
                temporal_feasibility=res.temporal_feasibility,
                attribute_comparisons=res.attribute_comparisons,
                evidence_completeness=res.evidence_completeness,
                link_score=res.link_score,
                explanation=res.explanation,
                machine_assessment=MachineAssessment(res.status),
                mode=obs.mode,
            )
            db.add(link)
            db.flush()
            # Every link is kept for the reviewer, but only temporally
            # valid, non-contradicted ones may extend a route.
            if res.temporal_feasibility == "valid" and res.status == "possible":
                new_links.append(link)

    db.flush()

    # Extend RouteChains incrementally
    if not new_links:
        fingerprint = compute_fingerprint(
            str(target_id), [str(obs.id)], [], obs.mode, current_gv
        )
        existing_chain = (
            db.query(RouteChain).filter_by(route_fingerprint=fingerprint).first()
        )
        if not existing_chain:
            chain = RouteChain(
                target_id=target_id,
                route_fingerprint=fingerprint,
                graph_version=current_gv,
                mode=obs.mode,
                status=RouteChainStatus.ACTIVE,
                evidence_completeness=obs.evidence_completeness,
            )
            db.add(chain)
            db.flush()
            db.add(
                RouteChainObservation(
                    route_chain_id=chain.id,
                    observation_id=obs.id,
                    sequence_order=1,
                )
            )

    for link in new_links:
        if link.source_observation_id != obs.id:
            chains_to_extend = (
                db.query(RouteChain)
                .join(RouteChainObservation)
                .filter(
                    RouteChain.target_id == target_id,
                    RouteChain.is_superseded == 0,
                    RouteChainObservation.observation_id
                    == link.source_observation_id,
                )
                .all()
            )
            for base_chain in chains_to_extend:
                _extend_chain(
                    db,
                    base_chain,
                    link,
                    link.destination_observation_id,
                    current_gv,
                )

        if link.destination_observation_id != obs.id:
            chains_to_extend = (
                db.query(RouteChain)
                .join(RouteChainObservation)
                .filter(
                    RouteChain.target_id == target_id,
                    RouteChain.is_superseded == 0,
                    RouteChainObservation.observation_id
                    == link.destination_observation_id,
                )
                .all()
            )
            for base_chain in chains_to_extend:
                _extend_chain_backward(
                    db,
                    base_chain,
                    link,
                    link.source_observation_id,
                    current_gv,
                )

    db.flush()

    # Update LAST OBSERVED with correct ordering
    _update_last_observed(db, target_id)

    _mark_processed(db, event)


# ─────────────────────────────────────────────────────────
# Chain extension helpers
# ─────────────────────────────────────────────────────────


def _extend_chain(
    db: Session,
    base_chain: RouteChain,
    new_link: CrossCameraLinkCandidate,
    new_obs_id: str,
    graph_version: int,
):
    obs_seq = (
        db.query(RouteChainObservation)
        .filter_by(route_chain_id=base_chain.id)
        .order_by(RouteChainObservation.sequence_order)
        .all()
    )
    link_seq = (
        db.query(RouteChainLink)
        .filter_by(route_chain_id=base_chain.id)
        .order_by(RouteChainLink.sequence_order)
        .all()
    )

    obs_ids = [str(o.observation_id) for o in obs_seq]
    link_ids = [str(l.link_candidate_id) for l in link_seq]

    # Only extend a chain from its tail; appending after some other
    # observation would put the new link out of sequence.
    if not obs_ids or obs_ids[-1] != str(new_link.source_observation_id) or str(new_obs_id) in obs_ids:
        return

    obs_ids.append(str(new_obs_id))
    link_ids.append(str(new_link.id))

    fingerprint = compute_fingerprint(
        str(base_chain.target_id), obs_ids, link_ids, base_chain.mode, graph_version
    )
    if db.query(RouteChain).filter_by(route_fingerprint=fingerprint).first():
        return

    new_chain = RouteChain(
        target_id=base_chain.target_id,
        route_fingerprint=fingerprint,
        graph_version=graph_version,
        mode=base_chain.mode,
        status=RouteChainStatus.EXTENDED,
    )
    db.add(new_chain)
    db.flush()

    for o in obs_seq:
        db.add(
            RouteChainObservation(
                route_chain_id=new_chain.id,
                observation_id=o.observation_id,
                sequence_order=o.sequence_order,
            )
        )
    db.add(
        RouteChainObservation(
            route_chain_id=new_chain.id,
            observation_id=new_obs_id,
            sequence_order=len(obs_seq) + 1,
        )
    )

    for l in link_seq:
        db.add(
            RouteChainLink(
                route_chain_id=new_chain.id,
                link_candidate_id=l.link_candidate_id,
                sequence_order=l.sequence_order,
            )
        )
    db.add(
        RouteChainLink(
            route_chain_id=new_chain.id,
            link_candidate_id=new_link.id,
            sequence_order=len(link_seq) + 1,
        )
    )

    base_chain.is_superseded = 1
    base_chain.status = RouteChainStatus.SUPERSEDED


def _extend_chain_backward(
    db: Session,
    base_chain: RouteChain,
    new_link: CrossCameraLinkCandidate,
    new_obs_id: str,
    graph_version: int,
):
    obs_seq = (
        db.query(RouteChainObservation)
        .filter_by(route_chain_id=base_chain.id)
        .order_by(RouteChainObservation.sequence_order)
        .all()
    )
    link_seq = (
        db.query(RouteChainLink)
        .filter_by(route_chain_id=base_chain.id)
        .order_by(RouteChainLink.sequence_order)
        .all()
    )

    # Mirror of _extend_chain: only prepend in front of the chain's head.
    if not obs_seq or str(obs_seq[0].observation_id) != str(new_link.destination_observation_id):
        return
    if any(str(o.observation_id) == str(new_obs_id) for o in obs_seq):
        return

    obs_ids = [str(new_obs_id)] + [str(o.observation_id) for o in obs_seq]
    link_ids = [str(new_link.id)] + [str(l.link_candidate_id) for l in link_seq]

    fingerprint = compute_fingerprint(
        str(base_chain.target_id), obs_ids, link_ids, base_chain.mode, graph_version
    )
    if db.query(RouteChain).filter_by(route_fingerprint=fingerprint).first():
        return

    new_chain = RouteChain(
        target_id=base_chain.target_id,
        route_fingerprint=fingerprint,
        graph_version=graph_version,
        mode=base_chain.mode,
        status=RouteChainStatus.EXTENDED,
    )
    db.add(new_chain)
    db.flush()

    db.add(
        RouteChainObservation(
            route_chain_id=new_chain.id,
            observation_id=new_obs_id,
            sequence_order=1,
        )
    )
    for o in obs_seq:
        db.add(
            RouteChainObservation(
                route_chain_id=new_chain.id,
                observation_id=o.observation_id,
                sequence_order=o.sequence_order + 1,
            )
        )

    db.add(
        RouteChainLink(
            route_chain_id=new_chain.id,
            link_candidate_id=new_link.id,
            sequence_order=1,
        )
    )
    for l in link_seq:
        db.add(
            RouteChainLink(
                route_chain_id=new_chain.id,
                link_candidate_id=l.link_candidate_id,
                sequence_order=l.sequence_order + 1,
            )
        )

    base_chain.is_superseded = 1
    base_chain.status = RouteChainStatus.SUPERSEDED


# ─────────────────────────────────────────────────────────
# Verification / human review
# ─────────────────────────────────────────────────────────


def process_verification_event(
    db: Session,
    entity_type: EvidenceReviewEntityType,
    entity_id: str,
    action: EvidenceReviewAction,
    reviewer: str,
    review_note: str = None,
):
    """
    Handles human review events and propagates invalidation cascades.
    Uses atomic event claiming for idempotency.
    """
    event_id = (
        f"VERIFICATION:{entity_type.value}:{entity_id}:{action.value}:{reviewer}"
    )
    event = _claim_event(db, event_id, InvestigationEventType.VERIFICATION, entity_id)
    if event is None:
        return

    try:
        _do_process_verification(
            db, entity_type, entity_id, action, reviewer, review_note, event
        )
    except Exception as e:
        db.rollback()
        _mark_failed(db, event_id, str(e))


def _do_process_verification(
    db: Session,
    entity_type: EvidenceReviewEntityType,
    entity_id: str,
    action: EvidenceReviewAction,
    reviewer: str,
    review_note: str,
    event: InvestigationEvent,
):
    review = EvidenceReview(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        reviewer=reviewer,
        review_note=review_note,
    )
    db.add(review)
    db.flush()

    target_ids_to_update: Set = set()

    if entity_type == EvidenceReviewEntityType.VEHICLE_OBSERVATION:
        obs = db.query(VehicleObservation).filter_by(id=entity_id).first()
        if not obs:
            raise ValueError("Observation not found")

        review.previous_state = obs.human_review_state.value

        mapped = HumanReviewState.UNREVIEWED
        if action == EvidenceReviewAction.ACCEPT:
            mapped = HumanReviewState.ACCEPTED
        elif action == EvidenceReviewAction.REJECT:
            mapped = HumanReviewState.REJECTED

        obs.human_review_state = mapped
        review.new_state = mapped.value

        if action == EvidenceReviewAction.REJECT:
            # Cascade to links touching this observation
            links = (
                db.query(CrossCameraLinkCandidate)
                .filter(
                    or_(
                        CrossCameraLinkCandidate.source_observation_id == obs.id,
                        CrossCameraLinkCandidate.destination_observation_id == obs.id,
                    )
                )
                .all()
            )
            for l in links:
                # A reviewer can reject a link without erasing the original
                # machine assessment.  Review state and model provenance are
                # independent axes throughout the investigation model.
                l.human_review_state = HumanReviewState.REJECTED
                _invalidate_dependent_chains(db, l.id, target_ids_to_update)

            # Invalidate chains using this observation directly.
            # Set status=INVALIDATED but do NOT set is_superseded.
            # is_superseded is only for chain-extension supersession, not rejection.
            chains = (
                db.query(RouteChain)
                .join(RouteChainObservation)
                .filter(RouteChainObservation.observation_id == obs.id)
                .all()
            )
            for c in chains:
                c.status = RouteChainStatus.INVALIDATED
                # do NOT set c.is_superseded = 1 here
                target_ids_to_update.add(c.target_id)

    elif entity_type == EvidenceReviewEntityType.CROSS_CAMERA_LINK:
        link = (
            db.query(CrossCameraLinkCandidate).filter_by(id=entity_id).first()
        )
        if not link:
            raise ValueError("Link not found")

        review.previous_state = link.human_review_state.value

        mapped = HumanReviewState.UNREVIEWED
        if action == EvidenceReviewAction.ACCEPT:
            mapped = HumanReviewState.ACCEPTED
        elif action == EvidenceReviewAction.REJECT:
            mapped = HumanReviewState.REJECTED

        link.human_review_state = mapped
        review.new_state = mapped.value

        if action == EvidenceReviewAction.REJECT:
            _invalidate_dependent_chains(db, link.id, target_ids_to_update)

    elif entity_type == EvidenceReviewEntityType.ROUTE_CHAIN:
        chain = db.query(RouteChain).filter_by(id=entity_id).first()
        if not chain:
            raise ValueError("Chain not found")

        review.previous_state = chain.human_review_state.value

        mapped = HumanReviewState.UNREVIEWED
        if action == EvidenceReviewAction.ACCEPT:
            mapped = HumanReviewState.ACCEPTED
        elif action == EvidenceReviewAction.REJECT:
            mapped = HumanReviewState.REJECTED

        chain.human_review_state = mapped
        review.new_state = mapped.value
        target_ids_to_update.add(chain.target_id)

    db.flush()

    for tid in target_ids_to_update:
        st = db.query(InvestigationState).filter_by(target_id=tid).first()
        if st:
            st.state_version += 1
            st.last_processed_at = datetime.utcnow()
        _update_last_observed(db, tid)

    _mark_processed(db, event)


# ─────────────────────────────────────────────────────────
# Invalidation helpers (bounded, dependency-aware)
# ─────────────────────────────────────────────────────────


def _invalidate_dependent_chains(
    db: Session, link_id: str, target_ids_to_update: Set
):
    """
    Invalidate all route chains that include the given link.

    Cascade depth: O(1). A link appears in route_chain_links; we query those
    chains directly. There is no recursive chain-of-chains traversal.
    Bounded by MAX_CASCADE_ENTITIES to prevent runaway queries on adversarial data.
    """
    chains = (
        db.query(RouteChain)
        .join(RouteChainLink)
        .filter(RouteChainLink.link_candidate_id == link_id)
        .limit(MAX_CASCADE_ENTITIES)
        .all()
    )
    for c in chains:
        c.status = RouteChainStatus.INVALIDATED
        # do NOT set is_superseded here - that's for extension only
        target_ids_to_update.add(c.target_id)


# ─────────────────────────────────────────────────────────
# last_seen with correct ordering
# ─────────────────────────────────────────────────────────


def _update_last_observed(db: Session, target_id):
    """
    Update the investigation state with the most recent valid observation.

    Uses explicit CASE WHEN for timestamp_quality ordering,
    NOT alphabetical enum sort (which would incorrectly prefer UNRELIABLE
    over VALID because 'u' > 'v' is False but PostgreSQL enum order is
    declaration order, which is unpredictable across migrations).
    """
    state = db.query(InvestigationState).filter_by(target_id=target_id).first()
    if not state:
        return

    # Explicit CASE ordering - VALID=1 sorts above UNRELIABLE=0
    quality_priority = case(
        (VehicleObservation.timestamp_quality == TimestampQuality.VALID, 1),
        else_=0,
    )

    latest_obs = (
        _target_observations(db, target_id)
        .filter(
            VehicleObservation.human_review_state != HumanReviewState.REJECTED,
        )
        .order_by(quality_priority.desc(), desc(VehicleObservation.observed_at))
        .first()
    )

    if latest_obs:
        state.last_seen_observation_id = latest_obs.id
        state.last_observation_at = latest_obs.observed_at
        state.last_seen_at = latest_obs.observed_at
        state.last_seen_camera_id = latest_obs.camera_id
    else:
        state.last_seen_observation_id = None
        state.last_observation_at = None
