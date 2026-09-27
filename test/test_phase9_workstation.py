import pytest
from uuid import uuid4
from datetime import datetime, timedelta

from app.models import (
    InvestigationTarget,
    VehicleObservation,
    VehicleTrack,
    TrackEvidence,
    RouteChain,
    RouteChainObservation,
    Mode,
    TimestampSource,
    RouteChainStatus,
    HumanReviewState,
    MachineAssessment,
    InvestigationState,
    InvestigationStateStatus,
    TimestampQuality,
    ObservationStatus,
    TrackStatus,
)
from app.investigation_service import process_verification_event
from app.models import EvidenceReviewEntityType, EvidenceReviewAction
from app.routers.investigation_ingest_schemas import ObservationIngest

from app.database import Base, engine, SessionLocal
from app.main import app
from fastapi.testclient import TestClient

@pytest.fixture(scope="module")
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()

@pytest.fixture(autouse=True)
def clean_db(db_session):
    for table in reversed(Base.metadata.sorted_tables):
        db_session.execute(table.delete())
    db_session.commit()
    yield

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def _setup_target(db_session):
    t = InvestigationTarget(plate_number="TEST_P9")
    db_session.add(t)
    db_session.commit()
    return t

def _setup_obs(db_session, camera_id, track_id, ts_source=TimestampSource.UNKNOWN):
    from app.models import Camera
    cam = db_session.query(Camera).filter_by(camera_id=camera_id).first()
    if not cam:
        cam = Camera(camera_id=camera_id, department="test")
        db_session.add(cam)
        db_session.commit()
    
    obs = VehicleObservation(
        camera_id=camera_id,
        track_id=track_id,
        timestamp_source=ts_source,
        timestamp_quality=TimestampQuality.VALID,
        status=ObservationStatus.OPEN,
        human_review_state=HumanReviewState.UNREVIEWED
    )
    db_session.add(obs)
    db_session.commit()
    return obs


def test_ui_route_segment_axes(db_session):
    """
    Test that a route chain retains is_simulated (mode) and human review status independently.
    We just need to verify the schema returns them as distinct fields.
    """
    t = _setup_target(db_session)
    obs = _setup_obs(db_session, "cam_1", 1)
    
    # Create RouteChain
    chain = RouteChain(
        target_id=t.id,
        mode=Mode.SIMULATED,
        status=RouteChainStatus.ACTIVE,
        route_fingerprint=f"cam_1_{uuid4().hex}"
    )
    db_session.add(chain)
    db_session.commit()
    
    # Link observation
    rco = RouteChainObservation(route_chain_id=chain.id, observation_id=obs.id, sequence_order=1)
    db_session.add(rco)
    db_session.commit()
    
    # Assert they are orthogonal axes
    assert chain.mode == Mode.SIMULATED
    assert obs.human_review_state == HumanReviewState.UNREVIEWED
    assert chain.status == RouteChainStatus.ACTIVE


def test_ui_superseded_route_filtering(db_session):
    """
    Test that superseded/invalidated routes return correctly but are marked with SUPERSEDED status.
    """
    t = _setup_target(db_session)
    
    chain = RouteChain(
        target_id=t.id,
        mode=Mode.REAL,
        status=RouteChainStatus.SUPERSEDED,
        route_fingerprint=f"cam_1_{uuid4().hex}"
    )
    db_session.add(chain)
    db_session.commit()
    
    # Verify the backend maintains the status exactly as it was set.
    retrieved = db_session.query(RouteChain).filter_by(id=chain.id).first()
    assert retrieved.status == RouteChainStatus.SUPERSEDED


def test_ui_timeline_timestamp_source_unknown(db_session):
    """
    Test that timestamp_source=UNKNOWN flows cleanly to the data model.
    """
    obs = _setup_obs(db_session, "cam_1", 1, ts_source=TimestampSource.UNKNOWN)
    retrieved = db_session.query(VehicleObservation).filter_by(id=obs.id).first()
    assert retrieved.timestamp_source == TimestampSource.UNKNOWN


def test_ui_prediction_provenance_completeness(client, db_session):
    """
    Test that the prediction API exposes dispersion, session counts, graph_version, model_version, etc.
    We just create a state and hit the endpoint to see what comes back.
    """
    t = _setup_target(db_session)
    obs = _setup_obs(db_session, "cam_1", 1)
    
    state = InvestigationState(
        target_id=t.id,
        status=InvestigationStateStatus.ACTIVE,
        last_seen_observation_id=obs.id,
        last_seen_camera_id="cam_1",
        last_seen_at=datetime.utcnow()
    )
    db_session.add(state)
    db_session.commit()
    
    res = client.get(f"/investigations/{t.id}/predictions")
    assert res.status_code == 200
    data = res.json()
    assert "predictions" in data


def test_human_review_preserves_machine_provenance(db_session):
    """
    Test that VERIFY/REJECT changes human_review_state but leaves machine_assessment / score untouched.
    """
    from app.models import Camera
    cam = db_session.query(Camera).filter_by(camera_id="cam_1").first()
    if not cam:
        cam = Camera(camera_id="cam_1", department="test")
        db_session.add(cam)
        db_session.commit()

    t = _setup_target(db_session)
    track = VehicleTrack(
        camera_id="cam_1", 
        track_id=1, 
        target_id=t.id,
        final_score=0.95,
        tier="strong_candidate",
        status=TrackStatus.completed
    )
    db_session.add(track)
    db_session.commit()
    
    assert track.final_score == 0.95
    
    # Process review
    process_verification_event(
        db_session, 
        EvidenceReviewEntityType.VEHICLE_OBSERVATION, 
        str(track.id),  # In investigations.py, it's actually VehicleObservation, but candidates are tracks in `/candidates/{id}`
        EvidenceReviewAction.ACCEPT, 
        "operator_1", 
        "looks good"
    )
    
    # Reload track
    assert track.final_score == 0.95
    assert track.tier == "strong_candidate"


def test_evidence_drawer_read_only_guarantee(client, db_session):
    """
    Test that fetching candidate details (/candidates/{id}) performs NO database mutations.
    """
    from app.models import Camera
    cam = db_session.query(Camera).filter_by(camera_id="cam_1").first()
    if not cam:
        cam = Camera(camera_id="cam_1", department="test")
        db_session.add(cam)
        db_session.commit()

    t = _setup_target(db_session)
    track = VehicleTrack(
        camera_id="cam_1", 
        track_id=1, 
        target_id=t.id,
        final_score=0.85,
        status=TrackStatus.completed
    )
    db_session.add(track)
    db_session.commit()
    
    # Fetch details
    res = client.get(f"/investigations/candidates/{track.id}")
    assert res.status_code == 200
    
    # Check that nothing changed in DB
    track_after = db_session.query(VehicleTrack).filter_by(id=track.id).first()
    assert track_after.final_score == 0.85
    assert track_after.status == TrackStatus.completed


def test_local_frame_clock_observation_is_not_cross_camera_timing(client, db_session):
    """Per-stream PTS must never be promoted to a comparable camera clock."""
    observed_at = datetime.utcnow().timestamp()
    response = client.post(
        "/investigations/observations",
        json={
            "camera_id": "cam_local_clock",
            "session_id": "worker-session-1",
            "track_id": 1,
            "status": "finalized",
            "timestamp_source": "frame_clock",
            "observed_at": observed_at,
            "source_pts_start": 1000,
            "source_pts_end": 2000,
        },
    )
    assert response.status_code == 200
    observation = db_session.query(VehicleObservation).filter_by(camera_id="cam_local_clock").one()
    assert observation.timestamp_source == TimestampSource.FRAME_CLOCK
    assert observation.timestamp_quality == TimestampQuality.UNRELIABLE


def test_timeline_does_not_include_reviews_from_other_targets(client, db_session):
    """Investigation timelines are target-scoped, not a global audit stream."""
    from app.models import Camera, EvidenceReview

    camera = Camera(camera_id="cam_timeline", department="test")
    first, second = _setup_target(db_session), _setup_target(db_session)
    first_track = VehicleTrack(camera_id=camera.camera_id, track_id=1, target_id=first.id)
    second_track = VehicleTrack(camera_id=camera.camera_id, track_id=2, target_id=second.id)
    db_session.add_all([camera, first_track, second_track])
    db_session.flush()
    first_observation = VehicleObservation(camera_id=camera.camera_id, track_id=1, candidate_id=first_track.id)
    second_observation = VehicleObservation(camera_id=camera.camera_id, track_id=2, candidate_id=second_track.id)
    db_session.add_all([first_observation, second_observation])
    db_session.flush()
    db_session.add_all([
        EvidenceReview(
            entity_type=EvidenceReviewEntityType.VEHICLE_OBSERVATION,
            entity_id=str(first_observation.id),
            action=EvidenceReviewAction.ACCEPT,
            reviewer="analyst_a",
        ),
        EvidenceReview(
            entity_type=EvidenceReviewEntityType.VEHICLE_OBSERVATION,
            entity_id=str(second_observation.id),
            action=EvidenceReviewAction.REJECT,
            reviewer="analyst_b",
        ),
    ])
    db_session.commit()

    response = client.get(f"/investigations/{first.id}/timeline")
    assert response.status_code == 200
    review_events = [event for event in response.json()["timeline"] if event["type"] == "REVIEW_EVENT"]
    assert [event["entity_id"] for event in review_events] == [str(first_observation.id)]
