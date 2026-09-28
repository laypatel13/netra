import pytest
from uuid import uuid4
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db, engine, Base
from app.models import (
    Camera,
    VehicleObservation,
    CameraGraphEdge,
    CameraTransitionRecord,
    CrossCameraLinkCandidate,
    TransitionEligibility,
    TimestampQuality,
    TimestampSource,
    Mode,
    RecordingSession,
    ProvenanceType,
    InvestigationTarget,
    VehicleTrack,
    TrackStatus,
    TargetStatus,
)

client = TestClient(app)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = next(get_db())
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

def setup_cameras(db_session, num=2):
    cams = []
    for i in range(num):
        c = Camera(
            camera_id=f"cam{i}_p19",
            name=f"Camera {i} Phase 19",
            department="police",
            connectivity_status="online"
        )
        db_session.add(c)
        cams.append(c)
    db_session.commit()
    return cams

def setup_edge(db_session, src, dst, min_t, max_t):
    edge = CameraGraphEdge(
        source_camera_id=src.camera_id,
        destination_camera_id=dst.camera_id,
        min_travel_time=min_t,
        max_travel_time=max_t,
        distance_meters=100.0,
        enabled=1,
        mode=Mode.REAL
    )
    db_session.add(edge)
    db_session.commit()
    db_session.refresh(edge)
    return edge

def create_recording_session(db_session, camera_id, session_id, p_type, sync_status="synchronized", is_simulated=False):
    rec = RecordingSession(
        session_id=session_id,
        camera_id=camera_id,
        provenance_type=p_type,
        synchronization_status=sync_status,
        timestamp_quality=TimestampQuality.VALID,
        mode=Mode.SIMULATED if is_simulated else Mode.REAL
    )
    db_session.add(rec)
    db_session.commit()
    db_session.refresh(rec)
    return rec

def ingest_observation(camera_id, session_id, track_id, ts, is_simulated=False):
    payload = {
        "camera_id": camera_id,
        "session_id": session_id,
        "track_id": track_id,
        "status": "finalized",
        "timestamp_source": "absolute_timestamp",
        "is_simulated": is_simulated,
        "observed_at": ts,
        "source_pts_start": ts,
        "source_pts_end": ts,
        "vehicle_type": "car",
        "color": "red",
        "is_playback_repetition": False,
        "is_time_synchronized": True,
        "ingested_at": datetime.utcnow().timestamp()
    }
    resp = client.post("/investigations/observations", json=payload)
    assert resp.status_code == 200, resp.json()
    
    # We must fetch the observation from the database to get its ID since the API just returns "status: ok"
    session = next(get_db())
    obs = session.query(VehicleObservation).filter_by(camera_id=camera_id, session_id=session_id, track_id=track_id).first()
    session.close()
    return obs.id


def setup_target_and_tracks(db_session: Session, cam1_id: str, cam2_id: str):
    target = InvestigationTarget(status=TargetStatus.active)
    db_session.add(target)
    db_session.flush()
    
    t1 = VehicleTrack(camera_id=cam1_id, track_id=1, target_id=target.id, status=TrackStatus.completed)
    t2 = VehicleTrack(camera_id=cam2_id, track_id=2, target_id=target.id, status=TrackStatus.completed)
    db_session.add(t1)
    db_session.add(t2)
    db_session.commit()
    return target

def test_synchronized_provenance_creates_eligible_transition(db_session: Session):
    cams = setup_cameras(db_session)
    setup_edge(db_session, cams[0], cams[1], 10, 100)
    setup_target_and_tracks(db_session, cams[0].camera_id, cams[1].camera_id)
    
    sess_id = "test_sync_sess"
    create_recording_session(db_session, cams[0].camera_id, sess_id, ProvenanceType.LIVE_SYNCHRONIZED)
    create_recording_session(db_session, cams[1].camera_id, sess_id, ProvenanceType.LIVE_SYNCHRONIZED)
    
    obs_a_id = ingest_observation(cams[0].camera_id, sess_id, 1, 1000.0)
    obs_b_id = ingest_observation(cams[1].camera_id, sess_id, 2, 1050.0)
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a_id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.ELIGIBLE

def test_independent_recordings_cannot_form_transition(db_session: Session):
    cams = setup_cameras(db_session)
    setup_edge(db_session, cams[0], cams[1], 10, 100)
    setup_target_and_tracks(db_session, cams[0].camera_id, cams[1].camera_id)
    
    sess_id = "test_unsync_sess"
    create_recording_session(db_session, cams[0].camera_id, sess_id, ProvenanceType.HISTORICAL_UNSYNCHRONIZED, "unsynchronized")
    create_recording_session(db_session, cams[1].camera_id, sess_id, ProvenanceType.HISTORICAL_UNSYNCHRONIZED, "unsynchronized")
    
    obs_a_id = ingest_observation(cams[0].camera_id, sess_id, 1, 1000.0)
    obs_b_id = ingest_observation(cams[1].camera_id, sess_id, 2, 1050.0)
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a_id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.INELIGIBLE
    assert transitions[0].exclusion_reason == "incompatible_provenance"

def test_unknown_provenance_ineligible(db_session: Session):
    cams = setup_cameras(db_session)
    setup_edge(db_session, cams[0], cams[1], 10, 100)
    setup_target_and_tracks(db_session, cams[0].camera_id, cams[1].camera_id)
    
    sess_id = "test_unknown_sess"
    # Do not create recording sessions to simulate missing provenance
    
    obs_a_id = ingest_observation(cams[0].camera_id, sess_id, 1, 1000.0)
    obs_b_id = ingest_observation(cams[1].camera_id, sess_id, 2, 1050.0)
    
    transitions = db_session.query(CameraTransitionRecord).filter_by(source_observation_id=obs_a_id).all()
    assert len(transitions) == 1
    assert transitions[0].transition_status == TransitionEligibility.INELIGIBLE
    assert transitions[0].exclusion_reason == "unknown_provenance"

def test_readiness_reports_provenance_metrics(db_session: Session):
    cams = setup_cameras(db_session)
    setup_edge(db_session, cams[0], cams[1], 10, 100)
    
    sess_id = "test_sync_sess_2"
    create_recording_session(db_session, cams[0].camera_id, sess_id, ProvenanceType.HISTORICAL_SYNCHRONIZED)
    create_recording_session(db_session, cams[1].camera_id, sess_id, ProvenanceType.HISTORICAL_SYNCHRONIZED)
    
    ingest_observation(cams[0].camera_id, sess_id, 1, 1000.0)
    ingest_observation(cams[1].camera_id, sess_id, 2, 1050.0)
    
    resp = client.get("/investigations/prediction-readiness")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    dists = resp.json()["distributions"]
    
    assert metrics["synchronized_session_count"] == 2
    assert metrics["unique_recording_sessions"] == 2
    assert "HISTORICAL_SYNCHRONIZED" in dists["provenance_breakdown"]
    assert dists["provenance_breakdown"]["HISTORICAL_SYNCHRONIZED"] == 2
