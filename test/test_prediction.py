import pytest
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal
from app.main import app
from app.models import (
    InvestigationTarget,
    Camera,
    VehicleTrack,
    VehicleObservation,
    CameraGraphEdge,
    ObservationStatus,
    TimestampSource,
    TimestampQuality,
    VerificationAction,
    EvidenceReviewEntityType,
    EvidenceReviewAction,
    CameraTransitionRecord,
    TransitionEligibility,
)
from app.investigation_service import process_new_observation, process_verification_event
from app.prediction_service import PredictionService

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


def test_prediction_sufficient_data(db_session: Session):
    # Setup Target and Cameras
    target = InvestigationTarget(plate_number="PRED123")
    cam_a = Camera(camera_id="cam_A", department="police")
    cam_b = Camera(camera_id="cam_B", department="police")
    cam_c = Camera(camera_id="cam_C", department="police")
    db_session.add_all([target, cam_a, cam_b, cam_c])
    db_session.flush()

    # Edge A -> B, A -> C
    db_session.add(
        CameraGraphEdge(
            source_camera_id="cam_A",
            destination_camera_id="cam_B",
            min_travel_time=30,
            max_travel_time=300,
        )
    )
    db_session.add(
        CameraGraphEdge(
            source_camera_id="cam_A",
            destination_camera_id="cam_C",
            min_travel_time=30,
            max_travel_time=300,
        )
    )
    db_session.commit()

    # Create 6 historical transitions from A -> B to pass sample threshold
    base_time = datetime.utcnow() - timedelta(days=2)
    for i in range(6):
        obs_a = VehicleObservation(
            camera_id="cam_A",
            track_id=100 + i,
            status=ObservationStatus.FINALIZED,
            observed_at=base_time + timedelta(hours=i),
            timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
            timestamp_quality=TimestampQuality.VALID,
            evidence_completeness=0.9,
            plate="PRED123",
            color="red",
        )
        obs_b = VehicleObservation(
            camera_id="cam_B",
            track_id=200 + i,
            status=ObservationStatus.FINALIZED,
            observed_at=base_time + timedelta(hours=i, minutes=2),
            timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
            timestamp_quality=TimestampQuality.VALID,
            evidence_completeness=0.9,
            plate="PRED123",
            color="red",
        )

        t_a = VehicleTrack(camera_id="cam_A", track_id=100 + i, target_id=target.id)
        t_b = VehicleTrack(camera_id="cam_B", track_id=200 + i, target_id=target.id)

        db_session.add_all([t_a, t_b, obs_a, obs_b])
        db_session.commit()

        process_new_observation(db_session, str(obs_a.id))
        process_new_observation(db_session, str(obs_b.id))

    # A recent observation at A
    recent_time = datetime.utcnow()
    recent_obs = VehicleObservation(
        camera_id="cam_A",
        track_id=300,
        status=ObservationStatus.FINALIZED,
        observed_at=recent_time,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="PRED123",
        color="red",
    )
    recent_track = VehicleTrack(camera_id="cam_A", track_id=300, target_id=target.id)
    db_session.add_all([recent_obs, recent_track])
    db_session.commit()
    process_new_observation(db_session, str(recent_obs.id))

    # Test Prediction API
    from app.database import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = client.get(f"/investigations/{target.id}/predictions")
        assert response.status_code == 200
        data = response.json()
        # F16: predictions response now includes last_observed
        assert "last_observed" in data
        assert len(data["predictions"]) >= 1

        # Find the HYPOTHESIS prediction (was "PREDICTED" before F8 fix)
        hypothesis_preds = [
            p for p in data["predictions"] if p["status"] == "HYPOTHESIS"
        ]
        assert len(hypothesis_preds) >= 1

        pred = hypothesis_preds[0]
        assert pred["status"] == "HYPOTHESIS"
        assert pred["candidate_camera_id"] == "cam_B"
        # F8: field is now hypothesis_score, not prediction_score
        assert pred["hypothesis_score"] > 0
        assert "estimated_time_window" in pred
        # F5: separated support fields
        assert "eligible_transition_count" in pred
        assert "unique_session_count" in pred
        # F10: provenance
        assert "generated_at" in pred
        assert "model_version" in pred
        # F17: window method
        assert pred["estimated_time_window"]["window_method"] == "interquartile_range"
    finally:
        app.dependency_overrides.clear()


def test_prediction_insufficient_data(db_session: Session):
    target = InvestigationTarget(plate_number="PRED456")
    cam_a = Camera(camera_id="cam_A", department="police")
    cam_b = Camera(camera_id="cam_B", department="police")
    db_session.add_all([target, cam_a, cam_b])
    db_session.flush()
    db_session.add(
        CameraGraphEdge(
            source_camera_id="cam_A",
            destination_camera_id="cam_B",
            min_travel_time=30,
            max_travel_time=300,
        )
    )
    db_session.commit()

    # Only 1 transition
    obs_a = VehicleObservation(
        camera_id="cam_A",
        track_id=1,
        status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow() - timedelta(minutes=10),
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="PRED456",
        color="red",
    )
    obs_b = VehicleObservation(
        camera_id="cam_B",
        track_id=2,
        status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow() - timedelta(minutes=8),
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="PRED456",
        color="red",
    )
    t_a = VehicleTrack(camera_id="cam_A", track_id=1, target_id=target.id)
    t_b = VehicleTrack(camera_id="cam_B", track_id=2, target_id=target.id)
    db_session.add_all([t_a, t_b, obs_a, obs_b])
    db_session.commit()

    process_new_observation(db_session, str(obs_a.id))
    process_new_observation(db_session, str(obs_b.id))

    # Trigger prediction by placing target at A again
    recent_obs = VehicleObservation(
        camera_id="cam_A",
        track_id=3,
        status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow(),
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="PRED456",
        color="red",
    )
    recent_track = VehicleTrack(camera_id="cam_A", track_id=3, target_id=target.id)
    db_session.add_all([recent_obs, recent_track])
    db_session.commit()
    process_new_observation(db_session, str(recent_obs.id))

    preds = PredictionService.predict_next_cameras(db_session, str(target.id))
    assert len(preds) == 1
    assert preds[0]["status"] == "INSUFFICIENT_DATA"
    assert preds[0]["candidate_camera_id"] == "cam_B"
    # F5: hypothesis_score (not prediction_score), and separated support
    assert preds[0]["hypothesis_score"] == 0.0
    assert preds[0]["eligible_transition_count"] == 1


def test_human_rejection_invalidates_transition(db_session: Session):
    target = InvestigationTarget(plate_number="REJ999")
    cam_a = Camera(camera_id="cam_A", department="police")
    cam_b = Camera(camera_id="cam_B", department="police")
    db_session.add_all([target, cam_a, cam_b])
    db_session.flush()
    db_session.add(
        CameraGraphEdge(
            source_camera_id="cam_A",
            destination_camera_id="cam_B",
            min_travel_time=30,
            max_travel_time=300,
        )
    )
    db_session.commit()

    obs_a = VehicleObservation(
        camera_id="cam_A",
        track_id=1,
        status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow() - timedelta(minutes=10),
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="REJ999",
        color="red",
    )
    obs_b = VehicleObservation(
        camera_id="cam_B",
        track_id=2,
        status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow() - timedelta(minutes=8),
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
        evidence_completeness=0.9,
        plate="REJ999",
        color="red",
    )
    db_session.add_all(
        [
            VehicleTrack(camera_id="cam_A", track_id=1, target_id=target.id),
            VehicleTrack(camera_id="cam_B", track_id=2, target_id=target.id),
            obs_a,
            obs_b,
        ]
    )
    db_session.commit()

    process_new_observation(db_session, str(obs_a.id))
    process_new_observation(db_session, str(obs_b.id))

    trans = db_session.query(CameraTransitionRecord).first()
    assert trans.transition_status == TransitionEligibility.ELIGIBLE

    # Reject Observation B
    process_verification_event(
        db_session,
        EvidenceReviewEntityType.VEHICLE_OBSERVATION,
        str(obs_b.id),
        EvidenceReviewAction.REJECT,
        "human",
    )

    # Transition should now be ineligible
    db_session.refresh(trans)
    assert trans.transition_status == TransitionEligibility.INELIGIBLE
    assert trans.exclusion_reason == "rejected_link_or_observation"
