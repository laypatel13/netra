import pytest
from datetime import datetime, timedelta
import uuid
from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app.models import (
    Camera, CameraType, CameraGraphEdge, Mode, TimestampQuality, TimestampSource,
    VehicleObservation, VehicleType, CameraTransitionRecord, CrossCameraLinkCandidate,
    TransitionEligibility, HumanReviewState
)
from app.investigation_service import process_new_observation


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


from app.models import VehicleTrack, InvestigationTarget

def _create_observation(db, target_id, camera_id, timestamp, session_id, mode=Mode.REAL):
    track_id = 1
    track = VehicleTrack(
        camera_id=camera_id,
        track_id=track_id,
        target_id=target_id,
    )
    db.add(track)
    
    obs = VehicleObservation(
        camera_id=camera_id,
        observed_at=timestamp,
        mode=mode,
        session_id=session_id,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        vehicle_type=VehicleType.car,
        color="red",
        track_id=track_id,
        human_review_state=HumanReviewState.UNREVIEWED
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs


def test_session_mismatch_creates_ineligible_transition(db_session: Session):
    # Create two cameras
    c1 = Camera(camera_id="CAM-001", department="test", camera_type=CameraType.ip)
    c2 = Camera(camera_id="CAM-002", department="test", camera_type=CameraType.ip)
    db_session.add_all([c1, c2])
    
    # Create valid edge
    e1 = CameraGraphEdge(source_camera_id="CAM-001", destination_camera_id="CAM-002",
                         min_travel_time=10, max_travel_time=300, mode=Mode.REAL, enabled=1)
    db_session.add(e1)
    db_session.commit()

    target_id = str(uuid.uuid4())
    target = InvestigationTarget(id=target_id, status="active", priority="high")
    db_session.add(target)
    db_session.commit()
    now = datetime.utcnow()
    
    # Obs 1 in session A
    session_a = str(uuid.uuid4())
    obs_a = _create_observation(db_session, target_id, "CAM-001", now, session_a)
    process_new_observation(db_session, str(obs_a.id))
    
    # Obs 2 in session B (temporally valid)
    session_b = str(uuid.uuid4())
    obs_b = _create_observation(db_session, target_id, "CAM-002", now + timedelta(seconds=60), session_b)
    process_new_observation(db_session, str(obs_b.id))
    
    # Should create an INELIGIBLE transition due to session mismatch
    transitions = db_session.query(CameraTransitionRecord).all()
    assert len(transitions) == 1
    t = transitions[0]
    assert t.transition_status == TransitionEligibility.INELIGIBLE
    assert t.exclusion_reason == "session_mismatch"


def test_missing_edge_creates_ineligible_transition(db_session: Session):
    # Create two cameras with NO EDGE
    c1 = Camera(camera_id="CAM-003", department="test", camera_type=CameraType.ip)
    c2 = Camera(camera_id="CAM-004", department="test", camera_type=CameraType.ip)
    db_session.add_all([c1, c2])
    db_session.commit()

    target_id = str(uuid.uuid4())
    target = InvestigationTarget(id=target_id, status="active", priority="high")
    db_session.add(target)
    db_session.commit()
    
    now = datetime.utcnow()
    session_a = str(uuid.uuid4())
    
    # Obs 1
    obs_1 = _create_observation(db_session, target_id, "CAM-003", now, session_a)
    process_new_observation(db_session, str(obs_1.id))
    
    # Obs 2 (temporally valid, but no edge)
    obs_2 = _create_observation(db_session, target_id, "CAM-004", now + timedelta(seconds=60), session_a)
    process_new_observation(db_session, str(obs_2.id))
    
    # Should create an INELIGIBLE transition due to missing edge
    transitions = db_session.query(CameraTransitionRecord).all()
    assert len(transitions) == 1
    t = transitions[0]
    assert t.transition_status == TransitionEligibility.INELIGIBLE
    assert t.exclusion_reason == "missing_edge"
    assert t.camera_edge_id is None
