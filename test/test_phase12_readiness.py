"""
Phase 12 — Data Collection Quality Gates and Readiness Checks.

Validates that only high-quality REAL transitions are accepted into the pipeline
and that the readiness report accurately gates advanced ML models.
"""
import pytest
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal
from app.main import app
from app.models import (
    VehicleObservation,
    CrossCameraLinkCandidate,
    CameraTransitionRecord,
    CameraGraphEdge,
    Camera,
    Mode,
    TimestampQuality,
    TimestampSource,
    HumanReviewState,
    MachineAssessment,
    TransitionEligibility,
)
from app.prediction_service import PredictionService, record_transition
from app.evaluation_data import seed_evaluation_data

client = TestClient(app)


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


def _setup_basic_cameras_and_edge(db: Session):
    cam1 = Camera(camera_id="cam_readiness_1", name="C1", department="Traffic")
    cam2 = Camera(camera_id="cam_readiness_2", name="C2", department="Traffic")
    db.add_all([cam1, cam2])
    db.flush()

    edge = CameraGraphEdge(
        source_camera_id=cam1.camera_id,
        destination_camera_id=cam2.camera_id,
        min_travel_time=10,
        max_travel_time=300,
        enabled=1,
    )
    db.add(edge)
    db.flush()
    return cam1, cam2, edge


def _create_obs(db: Session, cam_id: str, time: datetime, mode: Mode = Mode.REAL):
    obs = VehicleObservation(
        camera_id=cam_id,
        track_id=1,
        session_id="session_readiness_1",
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        mode=mode,
        observed_at=time,
        evidence_completeness=1.0,
    )
    db.add(obs)
    db.flush()
    return obs


def _create_link(db: Session, obs_a, obs_b, edge):
    link = CrossCameraLinkCandidate(
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id,
        camera_edge_id=edge.id,
        temporal_feasibility="valid",
        attribute_comparisons={"type": "match"},
        evidence_completeness=0.9,
        link_score=0.9,
        machine_assessment=MachineAssessment.POSSIBLE,
        mode=obs_b.mode,
        human_review_state=HumanReviewState.UNREVIEWED,
    )
    db.add(link)
    db.flush()
    return link


def test_reject_backward_time_travel(db_session: Session):
    cam1, cam2, edge = _setup_basic_cameras_and_edge(db_session)
    now = datetime.now()
    
    # obs_b is BEFORE obs_a
    obs_a = _create_obs(db_session, cam1.camera_id, now)
    obs_b = _create_obs(db_session, cam2.camera_id, now - timedelta(seconds=10))
    link = _create_link(db_session, obs_a, obs_b, edge)

    trans = record_transition(
        db_session, link, obs_a, obs_b, graph_version=1
    )
    db_session.flush()
    
    assert trans is not None
    assert trans.transition_status == TransitionEligibility.INELIGIBLE
    assert trans.exclusion_reason == "backward_time_travel"


def test_reject_disabled_edge(db_session: Session):
    cam1, cam2, edge = _setup_basic_cameras_and_edge(db_session)
    edge.enabled = 0
    db_session.flush()

    now = datetime.now()
    obs_a = _create_obs(db_session, cam1.camera_id, now)
    obs_b = _create_obs(db_session, cam2.camera_id, now + timedelta(seconds=10))
    link = _create_link(db_session, obs_a, obs_b, edge)

    trans = record_transition(
        db_session, link, obs_a, obs_b, graph_version=1
    )
    db_session.flush()
    
    assert trans is not None
    assert trans.transition_status == TransitionEligibility.INELIGIBLE
    assert trans.exclusion_reason == "disabled_edge"


def test_reject_simulated_real_mixing(db_session: Session):
    cam1, cam2, edge = _setup_basic_cameras_and_edge(db_session)
    
    now = datetime.now()
    # obs_a is SIMULATED, obs_b is REAL
    obs_a = _create_obs(db_session, cam1.camera_id, now, mode=Mode.SIMULATED)
    obs_b = _create_obs(db_session, cam2.camera_id, now + timedelta(seconds=10), mode=Mode.REAL)
    link = _create_link(db_session, obs_a, obs_b, edge)

    trans = record_transition(
        db_session, link, obs_a, obs_b, graph_version=1
    )
    db_session.flush()
    
    assert trans is not None
    assert trans.transition_status == TransitionEligibility.INELIGIBLE
    assert trans.exclusion_reason == "simulation_mismatch"


def test_readiness_calculation(db_session: Session):
    # Seed synthetic data (provides SIMULATED transitions)
    seed_evaluation_data(db_session)
    
    # Check readiness endpoint
    response = client.get("/investigations/prediction-readiness")
    assert response.status_code == 200
    report = response.json()
    
    # We only have SIMULATED data, so REAL count is 0
    assert report["metrics"]["real_eligible_count"] == 0
    assert report["metrics"]["simulated_count"] > 0
    assert report["status"] == "NOT_READY"
    
    # Verify the reasons contain our specific constraints
    reasons = report["reasons"]
    assert any("Insufficient REAL transitions" in r for r in reasons)
    assert any("Insufficient session diversity" in r for r in reasons)
