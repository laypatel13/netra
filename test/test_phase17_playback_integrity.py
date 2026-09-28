import pytest
from datetime import datetime, timedelta
import uuid
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from typing import Tuple

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

@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session(setup_db):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    
    # Override dependency
    app.dependency_overrides[get_db] = lambda: session
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

def _create_observation(db: Session, target_id: str, camera_id: str, pts_end: float, is_playback: bool, ingested_at: datetime) -> VehicleObservation:
    obs = VehicleObservation(
        camera_id=camera_id,
        session_id="test_session_17",
        track_id=1,
        candidate_id=target_id, # Actually this is VehicleTrack.id now!
        status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.SOURCE_PTS,
        mode=Mode.REAL,
        observed_at=datetime.utcfromtimestamp(pts_end / 1000.0),
        source_pts_start=pts_end - 1000.0,
        source_pts_end=pts_end,
        vehicle_type="car",
        color="red",
        is_playback_repetition=is_playback,
        ingested_at=ingested_at
    )
    db.add(obs)
    db.commit()
    return obs

def test_playback_repetition_rejected(db_session: Session):
    # Setup camera and edge
    cam1 = Camera(camera_id="cam1_p17", name="C1", department="D1", camera_type="ip")
    cam2 = Camera(camera_id="cam2_p17", name="C2", department="D1", camera_type="ip")
    db_session.add_all([cam1, cam2])
    db_session.commit()
    
    edge = CameraGraphEdge(
        source_camera_id="cam1_p17",
        destination_camera_id="cam2_p17",
        min_travel_time=10.0,
        max_travel_time=30.0,
        enabled=1,
        provenance="test"
    )
    db_session.add(edge)
    db_session.commit()
    
    target_id = str(uuid.uuid4())
    target = InvestigationTarget(id=target_id, description="red car")
    db_session.add(target)
    
    # Create tracks for cameras
    track1 = VehicleTrack(camera_id="cam1_p17", track_id=1, target_id=target_id)
    track2 = VehicleTrack(camera_id="cam2_p17", track_id=1, target_id=target_id)
    db_session.add_all([track1, track2])
    db_session.commit()
    
    # 1. Normal real transition (eligible)
    pts1 = 15000.0 # 15 sec
    pts2 = 35000.0 # 35 sec (20 sec travel time, within 10-30 bounds)
    now = datetime.utcnow()
    
    obs_a = _create_observation(db_session, track1.id, cam1.camera_id, pts1, False, now)
    process_new_observation(db_session, str(obs_a.id))
    
    obs_b = _create_observation(db_session, track2.id, cam2.camera_id, pts2, False, now + timedelta(seconds=20))
    process_new_observation(db_session, str(obs_b.id))
    
    # 2. Playback repetition
    pts3 = 25000.0 # Chronologically after obs_a so it tries C1 -> C2
    # Ingestion time continues forward
    obs_c = _create_observation(db_session, track2.id, cam2.camera_id, pts3, True, now + timedelta(seconds=40))
    process_new_observation(db_session, str(obs_c.id))
    
    # Check transitions
    transitions = db_session.query(CameraTransitionRecord).filter(
        CameraTransitionRecord.source_camera_id == "cam1_p17"
    ).all()
    
    assert len(transitions) == 2
    
    t1 = [t for t in transitions if t.destination_observation_id == obs_b.id][0]
    assert t1.transition_status == TransitionEligibility.ELIGIBLE, f"Expected ELIGIBLE, got {t1.transition_status} ({t1.exclusion_reason})"
    
    t2 = [t for t in transitions if t.destination_observation_id == obs_c.id][0]
    assert t2.transition_status == TransitionEligibility.INELIGIBLE
    assert t2.exclusion_reason == "playback_repetition"
    
    # Test readiness report
    response = client.get("/investigations/prediction-readiness")
    assert response.status_code == 200
    report = response.json()
    metrics = report["metrics"]
    
    assert metrics["suspected_loop_count"] >= 1
    assert "playback_repetition" in report["distributions"]["rejection_reasons"]
    # The source time span should only include obs_a and obs_b
    span = metrics["source_time_span_seconds"]
    assert span == 20.0 # 35 - 15 = 20
