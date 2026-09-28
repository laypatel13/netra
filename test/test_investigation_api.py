"""
/investigations API: the pipeline ingest path end to end, reviewer actions,
RBAC on destructive endpoints, and input validation. Needs PostgreSQL, see
conftest.py.
"""
import time
from datetime import datetime
from pathlib import Path

from conftest import requires_db
from app.models import (
    Camera,
    EvidenceReview,
    EvidenceReviewAction,
    EvidenceReviewEntityType,
    InvestigationTarget,
    TimestampQuality,
    TimestampSource,
    TrackStatus,
    VehicleObservation,
    VehicleTrack,
)

pytestmark = requires_db

ADMIN = {"X-Role": "admin"}
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def _register_camera(client, camera_id, lat, lng):
    response = client.post(
        "/cameras",
        json={"camera_id": camera_id, "name": camera_id, "latitude": lat, "longitude": lng,
              "department": "Gujarat Police", "camera_type": "ip"},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


def _pipeline_sighting(client, target_id, camera_id, track_id, seen_at, raw_path="data/evidence/x/raw.jpg"):
    """The two POSTs anpr/investigation_pipeline.py makes for a target match."""
    candidate = client.post("/investigations/ingest", json={
        "camera_id": camera_id, "track_id": track_id, "target_id": target_id,
        "started_at": seen_at - 4, "ended_at": seen_at, "final_score": 0.9, "tier": "strong",
        "score_breakdown": {}, "ocr_consensus": {"best_plate": "GJ01AB1234"},
        "evidence": [{"frame_index": 1, "quality_score": 0.8, "raw_path": raw_path}],
    })
    assert candidate.status_code == 200, candidate.text
    observation = client.post("/investigations/observations", json={
        "camera_id": camera_id, "session_id": "session-1", "track_id": track_id,
        "candidate_id": candidate.json()["track_id"], "status": "finalized",
        "timestamp_source": "absolute_timestamp", "observed_at": seen_at, "ingested_at": seen_at,
        "plate": "GJ01AB1234", "vehicle_type": "car", "color": "white",
        "source_pts_start": 1000.0 * track_id, "source_pts_end": 1000.0 * track_id + 500,
    })
    assert observation.status_code == 200, observation.text
    return candidate.json()["track_id"]


def test_pipeline_ingest_reconstructs_route_with_real_timestamps(client):
    _register_camera(client, "cam04", 23.0170, 72.5620)
    _register_camera(client, "cam02", 23.0350, 72.5650)
    _register_camera(client, "cam01", 23.0280, 72.5850)
    target_id = client.post("/investigations/targets", json={"plate_number": "GJ01AB1234"}, headers=ADMIN).json()["id"]

    start = time.time() - 1800
    for i, (camera_id, offset) in enumerate([("cam04", 0), ("cam02", 300), ("cam01", 700)]):
        _pipeline_sighting(client, target_id, camera_id, i + 1, start + offset)

    chains = client.get(f"/investigations/{target_id}/history").json()["route_chains"]
    assert len(chains) == 1
    assert chains[0]["cameras_visited"] == ["cam04", "cam02", "cam01"]
    assert all(link["temporal_feasibility"] == "valid" for link in chains[0]["links"])
    first_seen = datetime.fromisoformat(chains[0]["timestamps"][0])
    assert abs(first_seen.timestamp() - datetime.utcfromtimestamp(start).timestamp()) < 1


def test_destructive_endpoints_require_admin(client, db_session):
    target = InvestigationTarget(plate_number="GJ01AB1234")
    db_session.add(target)
    db_session.commit()

    assert client.post("/investigations/targets", json={"plate_number": "X"}).status_code == 403
    assert client.delete(f"/investigations/targets/{target.id}").status_code == 403
    assert client.post("/investigations/purge").status_code == 403
    assert client.delete("/investigations/alerts").status_code == 403
    assert client.patch(f"/investigations/targets/{target.id}/status", json={"status": "resolved"}).status_code == 403


def test_delete_target_never_removes_files_outside_evidence_dir(client, db_session):
    """raw_path comes from an unauthenticated endpoint; deleting a target
    must not become an arbitrary file delete."""
    _register_camera(client, "cam04", 23.0170, 72.5620)
    target_id = client.post("/investigations/targets", json={"plate_number": "GJ01AB1234"}, headers=ADMIN).json()["id"]
    _pipeline_sighting(client, target_id, "cam04", 1, time.time(), raw_path="../backend/app/main.py")

    response = client.delete(f"/investigations/targets/{target_id}?delete_data=true", headers=ADMIN)
    assert response.status_code == 200
    assert (BACKEND_DIR / "app" / "main.py").exists()
    assert db_session.query(VehicleTrack).count() == 0


def test_invalid_input_is_rejected_not_a_server_error(client):
    bad_enum = client.post("/investigations/observations", json={
        "camera_id": "cam01", "session_id": "s", "track_id": 1,
        "status": "FINALIZED", "timestamp_source": "ABSOLUTE", "observed_at": 1,
    })
    assert bad_enum.status_code == 422
    assert client.get("/investigations/not-a-uuid/history").status_code == 422


def test_pipeline_status_with_live_heartbeat(client):
    assert client.post("/investigations/heartbeat", json={"pipeline_id": "main", "cameras_active": 3}).status_code == 200
    status = client.get("/investigations/pipeline-status").json()
    assert status["online"] is True
    assert status["total_cameras"] == 3


def test_candidate_review_keeps_machine_score(client, db_session):
    _register_camera(client, "cam04", 23.0170, 72.5620)
    target_id = client.post("/investigations/targets", json={"plate_number": "GJ01AB1234"}, headers=ADMIN).json()["id"]
    candidate_id = _pipeline_sighting(client, target_id, "cam04", 1, time.time())

    response = client.post(
        f"/investigations/candidates/{candidate_id}/verify",
        json={"action": "verify", "verifier": "operator_1"},
        headers=ADMIN,
    )
    assert response.status_code == 200
    track = db_session.query(VehicleTrack).filter_by(id=candidate_id).one()
    assert track.status == TrackStatus.verified
    assert track.final_score == 0.9
    assert track.tier == "strong"


def test_candidate_detail_is_read_only(client, db_session):
    db_session.add(Camera(camera_id="cam_1", department="test"))
    target = InvestigationTarget(plate_number="GJ01AB1234")
    db_session.add(target)
    db_session.flush()
    track = VehicleTrack(camera_id="cam_1", track_id=1, target_id=target.id, final_score=0.85, status=TrackStatus.completed)
    db_session.add(track)
    db_session.commit()

    assert client.get(f"/investigations/candidates/{track.id}").status_code == 200
    db_session.refresh(track)
    assert track.final_score == 0.85
    assert track.status == TrackStatus.completed


def test_local_frame_clock_observation_is_not_cross_camera_timing(client, db_session):
    """Per-stream clocks must never be promoted to a comparable camera clock."""
    response = client.post("/investigations/observations", json={
        "camera_id": "cam_local_clock", "session_id": "worker-session-1", "track_id": 1,
        "status": "finalized", "timestamp_source": "frame_clock",
        "observed_at": datetime.utcnow().timestamp(), "source_pts_start": 1000, "source_pts_end": 2000,
    })
    assert response.status_code == 200
    observation = db_session.query(VehicleObservation).filter_by(camera_id="cam_local_clock").one()
    assert observation.timestamp_source == TimestampSource.FRAME_CLOCK
    assert observation.timestamp_quality == TimestampQuality.UNRELIABLE


def test_timeline_does_not_include_reviews_from_other_targets(client, db_session):
    """Investigation timelines are target-scoped, not a global audit stream."""
    db_session.add(Camera(camera_id="cam_timeline", department="test"))
    first, second = InvestigationTarget(plate_number="A1"), InvestigationTarget(plate_number="B2")
    db_session.add_all([first, second])
    db_session.flush()
    first_track = VehicleTrack(camera_id="cam_timeline", track_id=1, target_id=first.id)
    second_track = VehicleTrack(camera_id="cam_timeline", track_id=2, target_id=second.id)
    db_session.add_all([first_track, second_track])
    db_session.flush()
    first_obs = VehicleObservation(camera_id="cam_timeline", session_id="s", track_id=1, candidate_id=first_track.id)
    second_obs = VehicleObservation(camera_id="cam_timeline", session_id="s", track_id=2, candidate_id=second_track.id)
    db_session.add_all([first_obs, second_obs])
    db_session.flush()
    db_session.add_all([
        EvidenceReview(entity_type=EvidenceReviewEntityType.VEHICLE_OBSERVATION, entity_id=str(first_obs.id),
                       action=EvidenceReviewAction.ACCEPT, reviewer="analyst_a"),
        EvidenceReview(entity_type=EvidenceReviewEntityType.VEHICLE_OBSERVATION, entity_id=str(second_obs.id),
                       action=EvidenceReviewAction.REJECT, reviewer="analyst_b"),
    ])
    db_session.commit()

    response = client.get(f"/investigations/{first.id}/timeline")
    assert response.status_code == 200
    review_events = [e for e in response.json()["timeline"] if e["type"] == "REVIEW_EVENT"]
    assert [e["entity_id"] for e in review_events] == [str(first_obs.id)]
