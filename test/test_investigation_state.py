"""
Investigation state engine: incremental route chains, cross-camera linking
and review cascades. Needs PostgreSQL + PostGIS, see conftest.py.
"""
from datetime import datetime, timedelta

from geoalchemy2.elements import WKTElement

from conftest import requires_db
from app.investigation_service import process_new_observation, process_verification_event
from app.models import (
    Camera,
    CameraGraphEdge,
    CameraType,
    CrossCameraLinkCandidate,
    EvidenceReview,
    EvidenceReviewAction,
    EvidenceReviewEntityType,
    InvestigationState,
    InvestigationTarget,
    ObservationStatus,
    RouteChain,
    RouteChainStatus,
    TimestampQuality,
    TimestampSource,
    TrackStatus,
    VehicleObservation,
    VehicleTrack,
)

pytestmark = requires_db

# Real sandbox camera positions from test/seed_data/cameras_seed.csv.
PALDI = (23.0170, 72.5620)       # cam04, Ahmedabad
JANPATH = (23.0350, 72.5650)     # cam02, Ahmedabad, about 2 km from Paldi
JUNAGADH = (21.5200, 70.4600)    # cam06, about 270 km from Ahmedabad


def _camera(db, camera_id, latlng=None):
    location = WKTElement(f"POINT({latlng[1]} {latlng[0]})", srid=4326) if latlng else None
    db.add(Camera(camera_id=camera_id, department="police", camera_type=CameraType.ip, location=location))
    db.flush()


def _sighting(db, target, camera_id, track_id, observed_at, plate="GJ01AB1234"):
    """What the pipeline produces for a target match: a candidate track plus
    an observation that points at it through candidate_id."""
    track = VehicleTrack(camera_id=camera_id, track_id=track_id, target_id=target.id, status=TrackStatus.completed)
    db.add(track)
    db.flush()
    obs = VehicleObservation(
        camera_id=camera_id,
        session_id="session-1",
        track_id=track_id,
        candidate_id=track.id,
        status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        observed_at=observed_at,
        plate=plate,
        vehicle_type="car",
        color="white",
    )
    db.add(obs)
    db.commit()
    return obs


def _target(db, plate="GJ01AB1234"):
    target = InvestigationTarget(plate_number=plate)
    db.add(target)
    db.flush()
    return target


def _active_chains(db, target):
    return db.query(RouteChain).filter_by(target_id=target.id, is_superseded=0).all()


def _chain_cameras(db, chain):
    from app.models import RouteChainObservation
    rows = (
        db.query(VehicleObservation.camera_id)
        .join(RouteChainObservation, RouteChainObservation.observation_id == VehicleObservation.id)
        .filter(RouteChainObservation.route_chain_id == chain.id)
        .order_by(RouteChainObservation.sequence_order)
    )
    return [camera_id for (camera_id,) in rows]


def test_incremental_state_and_deduplication(db_session):
    target = _target(db_session, "XYZ123")
    _camera(db_session, "cam_1")
    _camera(db_session, "cam_2")
    db_session.add(CameraGraphEdge(
        source_camera_id="cam_1", destination_camera_id="cam_2", min_travel_time=60, max_travel_time=300,
    ))
    t1 = datetime.utcnow()

    obs1 = _sighting(db_session, target, "cam_1", 1, t1, plate="XYZ123")
    process_new_observation(db_session, str(obs1.id))

    state = db_session.query(InvestigationState).filter_by(target_id=target.id).first()
    assert state is not None
    assert state.last_seen_observation_id == obs1.id
    assert len(db_session.query(RouteChain).filter_by(target_id=target.id).all()) == 1

    # The same event again is a no-op, not a second chain.
    process_new_observation(db_session, str(obs1.id))
    assert len(db_session.query(RouteChain).filter_by(target_id=target.id).all()) == 1

    obs2 = _sighting(db_session, target, "cam_2", 2, t1 + timedelta(minutes=2), plate="XYZ123")
    process_new_observation(db_session, str(obs2.id))

    db_session.refresh(state)
    assert state.last_seen_observation_id == obs2.id
    chains = db_session.query(RouteChain).filter_by(target_id=target.id).all()
    assert len(chains) == 2
    active = [c for c in chains if c.is_superseded == 0]
    assert len(active) == 1
    assert _chain_cameras(db_session, active[0]) == ["cam_1", "cam_2"]


def test_route_links_cameras_by_registered_location(db_session):
    """No configured edges at all (the real sandbox case): the registry's own
    coordinates must be enough to reconstruct the route."""
    target = _target(db_session)
    _camera(db_session, "cam04", PALDI)
    _camera(db_session, "cam02", JANPATH)
    t0 = datetime.utcnow() - timedelta(minutes=30)

    for i, (camera_id, minutes) in enumerate([("cam04", 0), ("cam02", 6)]):
        obs = _sighting(db_session, target, camera_id, i + 1, t0 + timedelta(minutes=minutes))
        process_new_observation(db_session, str(obs.id))

    active = _active_chains(db_session, target)
    assert len(active) == 1
    assert _chain_cameras(db_session, active[0]) == ["cam04", "cam02"]
    link = db_session.query(CrossCameraLinkCandidate).one()
    assert link.temporal_feasibility == "valid"
    assert link.camera_edge_id is None


def test_out_of_order_arrival_extends_route_backward(db_session):
    target = _target(db_session)
    _camera(db_session, "cam04", PALDI)
    _camera(db_session, "cam02", JANPATH)
    t0 = datetime.utcnow() - timedelta(minutes=30)

    later = _sighting(db_session, target, "cam02", 2, t0 + timedelta(minutes=6))
    process_new_observation(db_session, str(later.id))
    earlier = _sighting(db_session, target, "cam04", 1, t0)
    process_new_observation(db_session, str(earlier.id))

    active = _active_chains(db_session, target)
    assert len(active) == 1
    assert _chain_cameras(db_session, active[0]) == ["cam04", "cam02"]


def test_impossibly_fast_transit_is_not_linked(db_session):
    """270 km in two minutes is a misread or a different vehicle, not a route."""
    target = _target(db_session)
    _camera(db_session, "cam04", PALDI)
    _camera(db_session, "cam06", JUNAGADH)
    t0 = datetime.utcnow() - timedelta(minutes=30)

    for i, (camera_id, minutes) in enumerate([("cam04", 0), ("cam06", 2)]):
        obs = _sighting(db_session, target, camera_id, i + 1, t0 + timedelta(minutes=minutes))
        process_new_observation(db_session, str(obs.id))

    assert len(_active_chains(db_session, target)) == 2
    link = db_session.query(CrossCameraLinkCandidate).one()
    assert link.temporal_feasibility == "impossible"


def test_camera_without_location_gives_unknown_timing(db_session):
    target = _target(db_session)
    _camera(db_session, "cam04", PALDI)
    _camera(db_session, "cam_nowhere")
    t0 = datetime.utcnow() - timedelta(minutes=30)

    for i, (camera_id, minutes) in enumerate([("cam04", 0), ("cam_nowhere", 5)]):
        obs = _sighting(db_session, target, camera_id, i + 1, t0 + timedelta(minutes=minutes))
        process_new_observation(db_session, str(obs.id))

    assert len(_active_chains(db_session, target)) == 2
    assert db_session.query(CrossCameraLinkCandidate).one().temporal_feasibility == "unknown"


def test_reused_track_number_from_another_session_is_ignored(db_session):
    """Track ids restart every pipeline session. An unrelated vehicle that
    happens to share (camera_id, track_id) with a target's candidate must not
    join the target's route."""
    target = _target(db_session)
    _camera(db_session, "cam04", PALDI)
    _camera(db_session, "cam02", JANPATH)
    t0 = datetime.utcnow() - timedelta(minutes=30)

    obs = _sighting(db_session, target, "cam04", 7, t0)
    process_new_observation(db_session, str(obs.id))

    stranger = VehicleObservation(
        camera_id="cam02", session_id="older-session", track_id=7,
        status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        observed_at=t0 + timedelta(minutes=5), plate="XX00ZZ0000",
    )
    db_session.add(stranger)
    db_session.commit()
    process_new_observation(db_session, str(stranger.id))

    assert db_session.query(CrossCameraLinkCandidate).count() == 0
    state = db_session.query(InvestigationState).filter_by(target_id=target.id).one()
    assert state.last_seen_observation_id == obs.id


def test_invalidation_cascade(db_session):
    target = _target(db_session, "REJ123")
    _camera(db_session, "cam_A")
    obs = _sighting(db_session, target, "cam_A", 1, datetime.utcnow(), plate="REJ123")
    process_new_observation(db_session, str(obs.id))
    assert len(_active_chains(db_session, target)) == 1

    process_verification_event(
        db_session,
        EvidenceReviewEntityType.VEHICLE_OBSERVATION,
        str(obs.id),
        EvidenceReviewAction.REJECT,
        "human_reviewer",
    )

    reviews = db_session.query(EvidenceReview).filter_by(entity_id=str(obs.id)).all()
    assert len(reviews) == 1
    assert reviews[0].action == EvidenceReviewAction.REJECT
    invalidated = db_session.query(RouteChain).filter_by(target_id=target.id, status=RouteChainStatus.INVALIDATED).all()
    assert len(invalidated) == 1
    state = db_session.query(InvestigationState).filter_by(target_id=target.id).first()
    assert state.last_seen_observation_id is None


def test_last_seen_api(db_session, client):
    target = _target(db_session, "API123")
    _camera(db_session, "cam_API")
    obs = _sighting(db_session, target, "cam_API", 1, datetime.utcnow(), plate="API123")
    process_new_observation(db_session, str(obs.id))

    response = client.get(f"/investigations/{target.id}/last_seen")
    assert response.status_code == 200
    data = response.json()["last_seen"]
    assert data["tag"] == "LAST_OBSERVED"
    assert data["observation_id"] == str(obs.id)
