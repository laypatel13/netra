"""
Synthetic Evaluation Dataset Generator — Phase 10.

Creates a deterministic, reproducible synthetic camera network with
historical transitions for walk-forward backtesting.

All generated data uses mode=SIMULATED with provenance="synthetic_evaluation"
to clearly separate it from any real operational data.
"""
import uuid
import random
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from sqlalchemy.orm import Session

from app.models import (
    Camera,
    InvestigationTarget,
    VehicleTrack,
    VehicleObservation,
    CameraGraphEdge,
    CrossCameraLinkCandidate,
    CameraTransitionRecord,
    RouteChain,
    RouteChainObservation,
    RouteChainLink,
    InvestigationState,
    RouteChainStatus,
    ObservationStatus,
    TimestampSource,
    TimestampQuality,
    HumanReviewState,
    MachineAssessment,
    TransitionEligibility,
    Mode,
    GraphVersion,
)
from app.investigation_service import get_current_graph_version, compute_fingerprint
from app.prediction_service import evaluate_transition_eligibility, record_transition


# Deterministic seed for reproducibility
EVAL_SEED = 42

# Camera network topology:
#
#   cam_E1 ──→ cam_E2 ──→ cam_E3
#     │            │           │
#     ↓            ↓           ↓
#   cam_E4 ──→ cam_E5 ──→ cam_E6
#
# Primary corridor: E1 → E2 → E3 (high traffic)
# Secondary corridor: E1 → E4 → E5 (medium traffic)
# Cross-links: E2 → E5, E5 → E3 (low traffic)
# Rare: E4 → E6, E3 → E6

CAMERA_IDS = ["cam_E1", "cam_E2", "cam_E3", "cam_E4", "cam_E5", "cam_E6"]

# (source, destination, min_travel_s, max_travel_s, mean_delta_s, std_delta_s, weight)
# weight controls how many transitions are generated for this edge
EDGE_DEFINITIONS = [
    ("cam_E1", "cam_E2", 60, 300, 120, 30, 40),    # primary: high traffic
    ("cam_E2", "cam_E3", 60, 300, 150, 40, 35),    # primary: high traffic
    ("cam_E1", "cam_E4", 90, 360, 180, 50, 20),    # secondary
    ("cam_E4", "cam_E5", 60, 240, 100, 25, 18),    # secondary
    ("cam_E2", "cam_E5", 120, 480, 240, 60, 8),    # cross-link
    ("cam_E5", "cam_E3", 90, 360, 200, 45, 6),     # cross-link
    ("cam_E5", "cam_E6", 60, 300, 130, 35, 5),     # rare
    ("cam_E4", "cam_E6", 180, 600, 350, 80, 3),    # rare
    ("cam_E3", "cam_E6", 60, 240, 110, 30, 4),     # rare
    ("cam_E1", "cam_E5", 180, 600, 300, 70, 2),    # very rare direct
]

# Known evaluation routes (sequences of camera IDs)
EVAL_ROUTES = [
    ["cam_E1", "cam_E2", "cam_E3"],                 # primary corridor
    ["cam_E1", "cam_E4", "cam_E5", "cam_E6"],       # secondary + branch
    ["cam_E1", "cam_E2", "cam_E5", "cam_E3"],       # primary + cross-link
]

SESSION_IDS = [f"eval_session_{i}" for i in range(8)]
INVESTIGATION_IDS = [f"eval_investigation_{i}" for i in range(3)]


def _ensure_cameras(db: Session):
    """Create evaluation cameras if they don't exist."""
    for cam_id in CAMERA_IDS:
        existing = db.query(Camera).filter_by(camera_id=cam_id).first()
        if not existing:
            db.add(Camera(camera_id=cam_id, department="evaluation"))
    db.flush()


def _ensure_graph_edges(db: Session) -> Dict[Tuple[str, str], CameraGraphEdge]:
    """Create camera graph edges and return a lookup map."""
    edge_map = {}
    for src, dst, min_t, max_t, _, _, _ in EDGE_DEFINITIONS:
        existing = (
            db.query(CameraGraphEdge)
            .filter_by(
                source_camera_id=src,
                destination_camera_id=dst,
                mode=Mode.SIMULATED,
            )
            .first()
        )
        if existing:
            edge_map[(src, dst)] = existing
        else:
            edge = CameraGraphEdge(
                source_camera_id=src,
                destination_camera_id=dst,
                min_travel_time=min_t,
                max_travel_time=max_t,
                provenance="synthetic_evaluation",
                mode=Mode.SIMULATED,
                enabled=1,
            )
            db.add(edge)
            db.flush()
            edge_map[(src, dst)] = edge
    return edge_map


def _generate_transitions(
    db: Session,
    target: InvestigationTarget,
    edge_map: Dict[Tuple[str, str], CameraGraphEdge],
    graph_version: int,
) -> int:
    """
    Generate synthetic transition records.
    Returns the total number of transitions created.
    """
    rng = random.Random(EVAL_SEED)
    total = 0
    base_time = datetime(2026, 1, 1, 8, 0, 0)  # start time for synthetic data
    track_counter = 1000

    for src, dst, min_t, max_t, mean_delta, std_delta, weight in EDGE_DEFINITIONS:
        edge = edge_map.get((src, dst))
        if not edge:
            continue

        for i in range(weight):
            session_id = rng.choice(SESSION_IDS)
            investigation_id = rng.choice(INVESTIGATION_IDS)

            # Generate a plausible time delta from a truncated normal-ish distribution
            delta = max(min_t, min(max_t, rng.gauss(mean_delta, std_delta)))

            obs_time_a = base_time + timedelta(hours=total * 0.5, minutes=rng.randint(0, 30))
            obs_time_b = obs_time_a + timedelta(seconds=delta)

            # Source observation
            obs_a = VehicleObservation(
                camera_id=src,
                track_id=track_counter,
                session_id=session_id,
                status=ObservationStatus.FINALIZED,
                timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
                timestamp_quality=TimestampQuality.VALID,
                mode=Mode.SIMULATED,
                observed_at=obs_time_a,
                evidence_completeness=0.85 + rng.random() * 0.15,
                plate=f"EVAL{rng.randint(100, 999)}",
                color=rng.choice(["red", "blue", "white", "black", "silver"]),
            )
            track_counter += 1

            # Destination observation
            obs_b = VehicleObservation(
                camera_id=dst,
                track_id=track_counter,
                session_id=session_id,
                status=ObservationStatus.FINALIZED,
                timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
                timestamp_quality=TimestampQuality.VALID,
                mode=Mode.SIMULATED,
                observed_at=obs_time_b,
                evidence_completeness=0.85 + rng.random() * 0.15,
                plate=f"EVAL{rng.randint(100, 999)}",
                color=rng.choice(["red", "blue", "white", "black", "silver"]),
            )
            track_counter += 1

            db.add_all([obs_a, obs_b])
            db.flush()

            # Tracks
            track_a = VehicleTrack(
                camera_id=src,
                track_id=obs_a.track_id,
                target_id=target.id,
                status="completed",
            )
            track_b = VehicleTrack(
                camera_id=dst,
                track_id=obs_b.track_id,
                target_id=target.id,
                status="completed",
            )
            db.add_all([track_a, track_b])
            db.flush()

            # Cross-camera link candidate
            link = CrossCameraLinkCandidate(
                source_observation_id=obs_a.id,
                destination_observation_id=obs_b.id,
                camera_edge_id=edge.id,
                temporal_feasibility="valid",
                attribute_comparisons={"type": "match", "color": "approximate"},
                evidence_completeness=0.8 + rng.random() * 0.2,
                link_score=0.7 + rng.random() * 0.3,
                machine_assessment=MachineAssessment.POSSIBLE,
                mode=Mode.SIMULATED,
                human_review_state=HumanReviewState.UNREVIEWED,
                timestamp_quality=TimestampQuality.VALID,
            )
            db.add(link)
            db.flush()

            # Durable transition record
            record_transition(
                db, link, obs_a, obs_b,
                graph_version=graph_version,
                session_id=session_id,
                investigation_id=investigation_id,
            )

            total += 1

    db.commit()
    return total


def _generate_evaluation_routes(
    db: Session,
    target: InvestigationTarget,
    edge_map: Dict[Tuple[str, str], CameraGraphEdge],
    graph_version: int,
) -> List[str]:
    """
    Generate known RouteChains for walk-forward evaluation.
    Returns list of route_chain_ids.
    """
    rng = random.Random(EVAL_SEED + 100)
    route_chain_ids = []

    route_base_time = datetime(2026, 6, 1, 10, 0, 0)

    for route_idx, cam_sequence in enumerate(EVAL_ROUTES):
        observations = []
        links = []
        current_time = route_base_time + timedelta(days=route_idx)

        for step_idx, cam_id in enumerate(cam_sequence):
            obs = VehicleObservation(
                camera_id=cam_id,
                track_id=5000 + route_idx * 100 + step_idx,
                session_id=f"eval_route_session_{route_idx}",
                status=ObservationStatus.FINALIZED,
                timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
                timestamp_quality=TimestampQuality.VALID,
                mode=Mode.SIMULATED,
                observed_at=current_time,
                evidence_completeness=0.95,
                plate=f"ROUTE{route_idx}",
                color="white",
            )
            db.add(obs)
            db.flush()

            track = VehicleTrack(
                camera_id=cam_id,
                track_id=obs.track_id,
                target_id=target.id,
                status="completed",
            )
            db.add(track)
            db.flush()

            observations.append(obs)

            # Create link to previous observation
            if step_idx > 0:
                prev_obs = observations[step_idx - 1]
                edge_key = (prev_obs.camera_id, cam_id)
                edge = edge_map.get(edge_key)
                if edge:
                    link = CrossCameraLinkCandidate(
                        source_observation_id=prev_obs.id,
                        destination_observation_id=obs.id,
                        camera_edge_id=edge.id,
                        temporal_feasibility="valid",
                        attribute_comparisons={"type": "match", "color": "match", "plate": "match"},
                        evidence_completeness=0.95,
                        link_score=0.95,
                        machine_assessment=MachineAssessment.POSSIBLE,
                        mode=Mode.SIMULATED,
                        human_review_state=HumanReviewState.UNREVIEWED,
                        timestamp_quality=TimestampQuality.VALID,
                    )
                    db.add(link)
                    db.flush()
                    links.append(link)

            # Advance time by a realistic delta
            edge_def = next(
                (e for e in EDGE_DEFINITIONS if e[0] == cam_id),
                None,
            )
            if edge_def:
                current_time += timedelta(seconds=rng.gauss(edge_def[3], edge_def[5]))
            else:
                current_time += timedelta(minutes=3)

        # Create RouteChain
        obs_ids = [str(o.id) for o in observations]
        link_ids = [str(l.id) for l in links]
        fingerprint = compute_fingerprint(
            str(target.id), obs_ids, link_ids, Mode.SIMULATED, graph_version
        )

        chain = RouteChain(
            target_id=target.id,
            status=RouteChainStatus.ACTIVE,
            mode=Mode.SIMULATED,
            evidence_completeness=0.95,
            explanation=f"Synthetic evaluation route {route_idx}",
            provenance="synthetic_evaluation",
            route_fingerprint=fingerprint,
            graph_version=graph_version,
        )
        db.add(chain)
        db.flush()

        # Link observations and links to chain
        for seq, obs in enumerate(observations):
            db.add(RouteChainObservation(
                route_chain_id=chain.id,
                observation_id=obs.id,
                sequence_order=seq,
            ))

        for seq, link in enumerate(links):
            db.add(RouteChainLink(
                route_chain_id=chain.id,
                link_candidate_id=link.id,
                sequence_order=seq,
            ))

        db.flush()
        route_chain_ids.append(str(chain.id))

    db.commit()
    return route_chain_ids


def seed_evaluation_data(db: Session, target_id: Optional[str] = None) -> Dict:
    """
    Main entry point: seeds a complete synthetic evaluation dataset.

    Returns a summary dict with counts and IDs for verification.
    """
    _ensure_cameras(db)

    # Create or reuse target
    if target_id:
        target = db.query(InvestigationTarget).filter_by(id=target_id).first()
        if not target:
            raise ValueError(f"Target {target_id} not found")
    else:
        target = InvestigationTarget(
            plate_number="EVAL_BASELINE",
            description="Synthetic evaluation target for Phase 10 baseline",
            category="investigation",
            priority="medium",
        )
        db.add(target)
        db.flush()

    # Ensure graph version exists for SIMULATED mode
    gv_record = (
        db.query(GraphVersion)
        .filter_by(mode=Mode.SIMULATED)
        .first()
    )
    if not gv_record:
        gv_record = GraphVersion(version=1, reason="synthetic_evaluation_init", mode=Mode.SIMULATED)
        db.add(gv_record)
        db.flush()
    graph_version = gv_record.version

    # Create InvestigationState
    state = db.query(InvestigationState).filter_by(target_id=target.id).first()
    if not state:
        state = InvestigationState(
            target_id=target.id,
            mode=Mode.SIMULATED,
            graph_version=graph_version,
        )
        db.add(state)
        db.flush()

    edge_map = _ensure_graph_edges(db)

    transition_count = _generate_transitions(db, target, edge_map, graph_version)
    route_chain_ids = _generate_evaluation_routes(db, target, edge_map, graph_version)

    # Point state at the last observation of the last route for prediction testing
    last_route_id = route_chain_ids[-1]
    last_chain = db.query(RouteChain).filter_by(id=last_route_id).first()
    if last_chain:
        last_obs_ref = (
            db.query(RouteChainObservation)
            .filter_by(route_chain_id=last_chain.id)
            .order_by(RouteChainObservation.sequence_order.desc())
            .first()
        )
        if last_obs_ref:
            last_obs = db.query(VehicleObservation).filter_by(id=last_obs_ref.observation_id).first()
            if last_obs:
                state.last_seen_observation_id = last_obs.id
                state.last_seen_at = last_obs.observed_at
                state.last_seen_camera_id = last_obs.camera_id
                state.last_observation_at = last_obs.observed_at

    db.commit()

    return {
        "target_id": str(target.id),
        "graph_version": graph_version,
        "cameras_created": len(CAMERA_IDS),
        "edges_created": len(EDGE_DEFINITIONS),
        "transitions_created": transition_count,
        "route_chains_created": len(route_chain_ids),
        "route_chain_ids": route_chain_ids,
        "mode": "SIMULATED",
        "provenance": "synthetic_evaluation",
    }
