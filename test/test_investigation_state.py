import pytest
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal
from app.main import app
from app.models import (
    InvestigationTarget, TargetStatus, TargetPriority,
    Camera, CameraType, ConnectivityStatus,
    VehicleTrack, TrackStatus,
    VehicleObservation, ObservationStatus, TimestampSource, TimestampQuality,
    CameraGraphEdge,
    InvestigationState, EvidenceReview,
    RouteChain, RouteChainStatus
)
from app.investigation_service import process_new_observation, process_verification_event
from app.models import EvidenceReviewAction, EvidenceReviewEntityType, VerificationAction

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    # Don't drop all for now to avoid breaking other tests, just clear out tables below

@pytest.fixture(autouse=True)
def clean_db(db_session: Session):
    for table in reversed(Base.metadata.sorted_tables):
        db_session.execute(table.delete())
    db_session.commit()
    yield

def test_incremental_state_and_deduplication(db_session: Session):
    # Setup Target
    target = InvestigationTarget(plate_number="XYZ123")
    db_session.add(target)
    
    # Setup Cameras
    cam1 = Camera(camera_id="cam_1", department="police", camera_type=CameraType.ip)
    cam2 = Camera(camera_id="cam_2", department="police", camera_type=CameraType.ip)
    db_session.add_all([cam1, cam2])
    db_session.flush()
    
    # Setup Edge
    edge = CameraGraphEdge(
        source_camera_id="cam_1",
        destination_camera_id="cam_2",
        min_travel_time=60,
        max_travel_time=300
    )
    db_session.add(edge)
    
    # Track 1
    track1 = VehicleTrack(camera_id="cam_1", track_id=1, target_id=target.id, status=TrackStatus.completed)
    db_session.add(track1)
    
    # Obs 1
    t1 = datetime.utcnow()
    obs1 = VehicleObservation(
        camera_id="cam_1", track_id=1, status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        observed_at=t1, plate="XYZ123"
    )
    db_session.add(obs1)
    db_session.commit()
    
    # Process Obs 1
    process_new_observation(db_session, str(obs1.id))
    
    # Verify state created
    state = db_session.query(InvestigationState).filter_by(target_id=target.id).first()
    assert state is not None
    assert state.last_seen_observation_id == obs1.id
    
    # Verify 1 RouteChain created (isolated)
    chains = db_session.query(RouteChain).filter_by(target_id=target.id).all()
    assert len(chains) == 1
    
    # Process Obs 1 again (idempotent duplicate event)
    process_new_observation(db_session, str(obs1.id))
    chains2 = db_session.query(RouteChain).filter_by(target_id=target.id).all()
    assert len(chains2) == 1 # Deduplication prevents explosion
    
    # Obs 2
    track2 = VehicleTrack(camera_id="cam_2", track_id=2, target_id=target.id, status=TrackStatus.completed)
    db_session.add(track2)
    
    obs2 = VehicleObservation(
        camera_id="cam_2", track_id=2, status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        observed_at=t1 + timedelta(minutes=2), plate="XYZ123"
    )
    db_session.add(obs2)
    db_session.commit()
    
    # Process Obs 2
    process_new_observation(db_session, str(obs2.id))
    
    # State should update
    db_session.refresh(state)
    assert state.last_seen_observation_id == obs2.id
    
    # We should have a new chain that supersedes the old one
    chains = db_session.query(RouteChain).filter_by(target_id=target.id).all()
    assert len(chains) == 2
    
    active_chains = [c for c in chains if c.is_superseded == 0]
    assert len(active_chains) == 1


def test_invalidation_cascade(db_session: Session):
    target = InvestigationTarget(plate_number="REJ123")
    db_session.add(target)
    db_session.flush()
    
    cam1 = Camera(camera_id="cam_A", department="police")
    track1 = VehicleTrack(camera_id="cam_A", track_id=1, target_id=target.id)
    obs1 = VehicleObservation(camera_id="cam_A", track_id=1, observed_at=datetime.utcnow())
    
    db_session.add_all([target, cam1, track1, obs1])
    db_session.commit()
    
    process_new_observation(db_session, str(obs1.id))
    
    # Verify active chain
    all_chains = db_session.query(RouteChain).filter_by(target_id=target.id).all()
    print(f"DEBUG: All chains: {all_chains}")
    active_chains = [c for c in all_chains if c.is_superseded == 0]
    print(f"DEBUG: Active chains: {active_chains}")
    assert len(active_chains) == 1
    
    # Reject observation
    process_verification_event(
        db_session, 
        EvidenceReviewEntityType.VEHICLE_OBSERVATION, 
        str(obs1.id), 
        EvidenceReviewAction.REJECT, 
        "human_reviewer"
    )
    
    # Check Review Log
    reviews = db_session.query(EvidenceReview).filter_by(entity_id=str(obs1.id)).all()
    assert len(reviews) == 1
    assert reviews[0].action == EvidenceReviewAction.REJECT
    
    # Chain should be invalidated
    invalidated_chains = db_session.query(RouteChain).filter_by(target_id=target.id, status=RouteChainStatus.INVALIDATED).all()
    assert len(invalidated_chains) == 1
    
    # Last seen should be reset
    state = db_session.query(InvestigationState).filter_by(target_id=target.id).first()
    assert state.last_seen_observation_id is None


def test_last_seen_api(db_session: Session):
    # Tests the /last_seen endpoint
    target = InvestigationTarget(plate_number="API123")
    db_session.add(target)
    db_session.commit()
    
    cam = Camera(camera_id="cam_API", department="police")
    track = VehicleTrack(camera_id="cam_API", track_id=1, target_id=target.id)
    obs = VehicleObservation(camera_id="cam_API", track_id=1, observed_at=datetime.utcnow())
    
    db_session.add_all([cam, track, obs])
    db_session.commit()
    process_new_observation(db_session, str(obs.id))
    
    from app.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    
    try:
        response = client.get(f"/investigations/{target.id}/last_seen")
        assert response.status_code == 200
        data = response.json()
        assert data["last_seen"] is not None
        assert data["last_seen"]["tag"] == "LAST_OBSERVED"
        assert data["last_seen"]["observation_id"] == str(obs.id)
    finally:
        app.dependency_overrides.clear()
