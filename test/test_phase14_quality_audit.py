import uuid
from datetime import datetime, timedelta
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models import (
    Camera,
    InvestigationTarget,
    CameraGraphEdge,
    VehicleObservation,
    VehicleTrack,
    CameraTransitionRecord,
    InvestigationEvent,
    Mode,
    TimestampSource,
    TimestampQuality,
    TransitionEligibility
)
from app.main import app
from app.investigation_service import process_new_observation
from app.database import Base, engine, SessionLocal

@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()

@pytest.fixture(autouse=True)
def clean_db(db_session: Session):
    for table in reversed(Base.metadata.sorted_tables):
        db_session.execute(table.delete())
    db_session.commit()
    yield

def _setup_end_to_end_env(db: Session, mode: Mode = Mode.REAL, prefix: str = ""):
    cam1 = Camera(camera_id=f"cam_e2e_1_{prefix}", name=f"E2E 1 {prefix}", department="Traffic")
    cam2 = Camera(camera_id=f"cam_e2e_2_{prefix}", name=f"E2E 2 {prefix}", department="Traffic")
    db.add_all([cam1, cam2])
    db.flush()

    edge = CameraGraphEdge(
        source_camera_id=cam1.camera_id,
        destination_camera_id=cam2.camera_id,
        min_travel_time=10,
        max_travel_time=300,
        enabled=1,
        mode=mode,
    )
    db.add(edge)
    
    target = InvestigationTarget(
        id=uuid.uuid4(),
        status="active",
        priority="high"
    )
    db.add(target)
    db.flush()
    return cam1, cam2, edge, target

def _create_observation(db: Session, target_id: uuid.UUID, cam_id: str, ts: datetime, mode: Mode, source: TimestampSource = TimestampSource.ABSOLUTE_TIMESTAMP, quality: TimestampQuality = TimestampQuality.VALID) -> VehicleObservation:
    track_id = 1
    
    track = VehicleTrack(
        camera_id=cam_id,
        track_id=track_id,
        target_id=target_id,
    )
    db.add(track)
    
    obs = VehicleObservation(
        camera_id=cam_id,
        track_id=track_id,
        session_id="session_e2e_1",
        timestamp_source=source,
        timestamp_quality=quality,
        mode=mode,
        observed_at=ts,
        evidence_completeness=1.0,
        vehicle_type="car",
        color="red",
    )
    db.add(obs)
    db.commit()
    return obs

client = TestClient(app)

def test_audit_duplicate_idempotency(db_session: Session):
    cam1, cam2, edge, target = _setup_end_to_end_env(db_session, Mode.REAL, "audit_dup")
    now = datetime.utcnow()
    
    obs_a = _create_observation(db_session, target.id, cam1.camera_id, now, Mode.REAL)
    process_new_observation(db_session, str(obs_a.id))
    
    obs_b = _create_observation(db_session, target.id, cam2.camera_id, now + timedelta(seconds=20), Mode.REAL)
    process_new_observation(db_session, str(obs_b.id))
    
    # Process AGAIN to see if duplicates are created
    process_new_observation(db_session, str(obs_b.id))
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id
    ).all()
    
    # Idempotency guarantees should mean only ONE transition exists
    assert len(transitions) == 1

def test_audit_stale_data_and_sparse_edges(db_session: Session):
    # If we have < 100 transitions, < 10 sessions, < 5 edges, it should be NOT_READY
    # even if everything is REAL and ELIGIBLE
    
    cam1, cam2, edge, target = _setup_end_to_end_env(db_session, Mode.REAL, "audit_sparse")
    now = datetime.utcnow()
    
    obs_a = _create_observation(db_session, target.id, cam1.camera_id, now, Mode.REAL)
    process_new_observation(db_session, str(obs_a.id))
    
    obs_b = _create_observation(db_session, target.id, cam2.camera_id, now + timedelta(seconds=20), Mode.REAL)
    process_new_observation(db_session, str(obs_b.id))
    
    report = client.get("/investigations/prediction-readiness").json()
    assert report["status"] == "NOT_READY"
    
    reasons = " ".join(report["reasons"])
    assert "Insufficient REAL transitions" in reasons
    assert "Insufficient session diversity" in reasons
    assert "Insufficient spatial/edge diversity" in reasons

def test_audit_provenance_and_rejection_reasons(db_session: Session):
    cam1, cam2, edge, target = _setup_end_to_end_env(db_session, Mode.REAL, "audit_prov")
    now = datetime.utcnow()
    
    obs_a = _create_observation(db_session, target.id, cam1.camera_id, now, Mode.REAL)
    process_new_observation(db_session, str(obs_a.id))
    
    # Unreliable timestamp
    obs_b = _create_observation(db_session, target.id, cam2.camera_id, now + timedelta(seconds=20), Mode.REAL, quality=TimestampQuality.UNRELIABLE)
    process_new_observation(db_session, str(obs_b.id))
    
    report = client.get("/investigations/prediction-readiness").json()
    
    metrics = report["metrics"]
    assert metrics["real_ineligible_count"] >= 1
    
    rejections = report["distributions"]["rejection_reasons"]
    assert "unverified_cross_camera_timing" in rejections
    assert rejections["unverified_cross_camera_timing"] >= 1
    
    # Ensure transition persists with INELIGIBLE
    transitions = db_session.query(CameraTransitionRecord).filter_by(
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id
    ).all()
    assert len(transitions) == 1
    t = transitions[0]
    assert t.transition_status == TransitionEligibility.INELIGIBLE
    assert t.exclusion_reason == "unverified_cross_camera_timing"
    # Provenance
    assert t.session_id is not None
    assert t.source_camera_id == cam1.camera_id
