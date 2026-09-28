import pytest
from datetime import datetime, timedelta
import uuid
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, get_db
from app.models import (
    VehicleObservation, 
    CameraTransitionRecord, 
    ObservationStatus, 
    TimestampSource, 
    Mode, 
    TransitionEligibility, 
    Camera, 
    CameraGraphEdge, 
    InvestigationTarget,
    VehicleTrack
)
from app.investigation_service import process_new_observation

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="module")
def db_session():
    session = Session(bind=engine)
    app.dependency_overrides[get_db] = lambda: session
    yield session
    session.close()

def _create_observation(db: Session, target_id: str, camera_id: str, pts_end: float, is_playback: bool, is_time_sync: bool, ingested_at: datetime, track_id: int = 1, session_id: str = "test_session_18", mode: Mode = Mode.REAL) -> VehicleObservation:
    obs = VehicleObservation(
        camera_id=camera_id,
        session_id=session_id,
        track_id=track_id,
        candidate_id=target_id,
        status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.SOURCE_PTS,
        mode=mode,
        observed_at=datetime.utcfromtimestamp(pts_end / 1000.0) if pts_end is not None else datetime.utcnow(),
        source_pts_start=pts_end - 1000.0 if pts_end is not None else None,
        source_pts_end=pts_end,
        vehicle_type="car",
        color="red",
        is_playback_repetition=is_playback,
        is_time_synchronized=is_time_sync,
        source_fingerprint=f"{camera_id}:{session_id}:{pts_end-1000.0}" if pts_end is not None else None,
        ingested_at=ingested_at
    )
    db.add(obs)
    db.commit()
    return obs

def test_unsynchronized_cross_camera_pts_not_eligible(db_session: Session):
    cam1 = Camera(camera_id="cam1_p18", name="C1", department="D1", camera_type="ip")
    cam2 = Camera(camera_id="cam2_p18", name="C2", department="D1", camera_type="ip")
    db_session.add_all([cam1, cam2])
    db_session.commit()
    
    edge = CameraGraphEdge(source_camera_id="cam1_p18", destination_camera_id="cam2_p18", min_travel_time=10.0, max_travel_time=30.0, enabled=1)
    db_session.add(edge)
    
    target_id = str(uuid.uuid4())
    db_session.add(InvestigationTarget(id=target_id, description="red car"))
    track1 = VehicleTrack(camera_id="cam1_p18", track_id=10, target_id=target_id)
    track2 = VehicleTrack(camera_id="cam2_p18", track_id=11, target_id=target_id)
    db_session.add_all([track1, track2])
    db_session.commit()
    
    now = datetime.utcnow()
    # is_time_sync=False
    obs_a = _create_observation(db_session, track1.id, "cam1_p18", 15000.0, False, False, now, 10)
    process_new_observation(db_session, str(obs_a.id))
    obs_b = _create_observation(db_session, track2.id, "cam2_p18", 35000.0, False, False, now, 11)
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a.id, destination_observation_id=obs_b.id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.INELIGIBLE
    assert transitions[0].exclusion_reason == "unverified_cross_camera_time"

def test_synchronized_cross_camera_transition_eligible(db_session: Session):
    target_id = str(uuid.uuid4())
    db_session.add(InvestigationTarget(id=target_id, description="red car"))
    track1 = VehicleTrack(camera_id="cam1_p18", track_id=20, target_id=target_id)
    track2 = VehicleTrack(camera_id="cam2_p18", track_id=21, target_id=target_id)
    db_session.add_all([track1, track2])
    db_session.commit()
    
    now = datetime.utcnow()
    # is_time_sync=True
    obs_a = _create_observation(db_session, track1.id, "cam1_p18", 15000.0, False, True, now, 20, "sync_session")
    process_new_observation(db_session, str(obs_a.id))
    obs_b = _create_observation(db_session, track2.id, "cam2_p18", 35000.0, False, True, now, 21, "sync_session")
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a.id, destination_observation_id=obs_b.id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.ELIGIBLE

def test_playback_repetition_not_training_data(db_session: Session):
    target_id = str(uuid.uuid4())
    db_session.add(InvestigationTarget(id=target_id, description="red car"))
    track1 = VehicleTrack(camera_id="cam1_p18", track_id=30, target_id=target_id)
    track2 = VehicleTrack(camera_id="cam2_p18", track_id=31, target_id=target_id)
    db_session.add_all([track1, track2])
    db_session.commit()
    
    now = datetime.utcnow()
    obs_a = _create_observation(db_session, track1.id, "cam1_p18", 15000.0, False, True, now, 30, "rep_session")
    process_new_observation(db_session, str(obs_a.id))
    
    # is_playback=True
    obs_b = _create_observation(db_session, track2.id, "cam2_p18", 35000.0, True, True, now, 31, "rep_session")
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a.id, destination_observation_id=obs_b.id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.INELIGIBLE
    assert transitions[0].exclusion_reason == "playback_repetition"

def test_duplicate_processing_is_idempotent(db_session: Session):
    payload = {
        "camera_id": "cam1_p18",
        "session_id": "dup_session",
        "track_id": 40,
        "status": "finalized",
        "timestamp_source": "source_pts",
        "is_simulated": False,
        "observed_at": 11000.0,
        "source_pts_start": 10000.0,
        "source_pts_end": 11000.0,
        "vehicle_type": "car",
        "color": "red",
        "is_playback_repetition": False,
        "is_time_synchronized": True,
        "ingested_at": datetime.utcnow().timestamp()
    }
    
    response1 = client.post("/investigations/observations", json=payload)
    assert response1.status_code == 200, response1.json()
    
    # Send identical payload
    response2 = client.post("/investigations/observations", json=payload)
    assert response2.status_code == 200
    
    # Should only be one observation
    obs = db_session.query(VehicleObservation).filter_by(session_id="dup_session").all()
    assert len(obs) == 1

def test_disconnected_cameras_not_inferred_as_edges(db_session: Session):
    cam3 = Camera(camera_id="cam3_p18", name="C3", department="D1", camera_type="ip")
    db_session.add(cam3)
    db_session.commit()
    
    # No edge between cam1 and cam3
    
    target_id = str(uuid.uuid4())
    db_session.add(InvestigationTarget(id=target_id, description="red car"))
    track1 = VehicleTrack(camera_id="cam1_p18", track_id=50, target_id=target_id)
    track3 = VehicleTrack(camera_id="cam3_p18", track_id=51, target_id=target_id)
    db_session.add_all([track1, track3])
    db_session.commit()
    
    now = datetime.utcnow()
    obs_a = _create_observation(db_session, track1.id, "cam1_p18", 15000.0, False, True, now, 50, "disc_session")
    process_new_observation(db_session, str(obs_a.id))
    obs_b = _create_observation(db_session, track3.id, "cam3_p18", 35000.0, False, True, now, 51, "disc_session")
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a.id, destination_observation_id=obs_b.id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.INELIGIBLE
    assert transitions[0].exclusion_reason == "missing_edge"

def test_real_simulated_isolation(db_session: Session):
    target_id = str(uuid.uuid4())
    db_session.add(InvestigationTarget(id=target_id, description="red car"))
    track1 = VehicleTrack(camera_id="cam1_p18", track_id=60, target_id=target_id)
    track2 = VehicleTrack(camera_id="cam2_p18", track_id=61, target_id=target_id)
    db_session.add_all([track1, track2])
    db_session.commit()
    
    now = datetime.utcnow()
    obs_a = _create_observation(db_session, track1.id, "cam1_p18", 15000.0, False, True, now, 60, "iso_session", Mode.REAL)
    process_new_observation(db_session, str(obs_a.id))
    obs_b = _create_observation(db_session, track2.id, "cam2_p18", 35000.0, False, True, now, 61, "iso_session", Mode.SIMULATED)
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a.id, destination_observation_id=obs_b.id).all()
    assert len(transitions) == 0

def test_readiness_reports_replay_and_sync_metrics(db_session: Session):
    response = client.get("/investigations/prediction-readiness")
    assert response.status_code == 200
    report = response.json()
    metrics = report["metrics"]
    
    assert "synchronized_transition_count" in metrics
    assert "unsynchronized_transition_count" in metrics
    assert "unique_source_event_count" in metrics
    assert "repeated_event_count" in metrics
    assert "replay_duplicate_count" in metrics
    assert "disconnected_unvalidated_edge_info" in metrics
    
    # We should have missing_edge = 1 from test_disconnected_cameras_not_inferred_as_edges
    assert metrics["disconnected_unvalidated_edge_info"]["missing_edge"] >= 1
    
    # We should have unsynchronized = 1 from test_unsynchronized_cross_camera_pts_not_eligible
    assert metrics["unsynchronized_transition_count"] >= 1
    
    assert metrics["replay_duplicate_count"] >= 1
