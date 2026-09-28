import pytest
import datetime
import uuid
from unittest.mock import MagicMock
from app.models import CameraTransitionRecord, TransitionEligibility, TimestampQuality, Mode
from app.prediction_service import PredictionService

def create_mock_transition(src, dst, session_id, ts_quality=TimestampQuality.VALID, is_eligible=True, mode=Mode.REAL):
    t = MagicMock(spec=CameraTransitionRecord)
    t.source_camera_id = src
    t.destination_camera_id = dst
    t.session_id = session_id
    t.investigation_id = "inv_" + session_id
    t.camera_edge_id = f"edge_{src}_{dst}"
    t.transition_status = TransitionEligibility.ELIGIBLE if is_eligible else TransitionEligibility.INELIGIBLE
    t.timestamp_quality = ts_quality
    t.mode = mode
    t.created_at = datetime.datetime.utcnow()
    t.exclusion_reason = None if is_eligible else "test_exclusion"
    t.graph_version = 1
    return t

def test_diversity_metrics(monkeypatch):
    # Mock transitions
    transitions = [
        create_mock_transition("camA", "camB", "sess_1"),
        create_mock_transition("camB", "camC", "sess_1"),
        create_mock_transition("camA", "camB", "sess_2", ts_quality=TimestampQuality.UNRELIABLE),
        create_mock_transition("camB", "camC", "sess_2"),
        create_mock_transition("camC", "camD", "sess_2"),
        create_mock_transition("camD", "camE", "sess_3", is_eligible=False),
        create_mock_transition("camA", "camE", "sess_4", mode=Mode.SIMULATED)
    ]
    
    # Mock DB query chain: db.query(CameraTransitionRecord).all()
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.all.return_value = transitions
    
    report = PredictionService.get_dataset_readiness_report(mock_db)
    
    metrics = report["metrics"]
    assert metrics["real_eligible_count"] == 5
    assert metrics["unique_sessions"] == 2
    assert metrics["unique_edges"] == 3 # A->B, B->C, C->D
    assert metrics["camera_coverage"] == 4 # A, B, C, D
    
    # Repeated route concentration: A->B (2), B->C (2), C->D (1)
    # Max edge count is 2. Total eligible is 5.
    assert metrics["repeated_route_concentration"] == 2 / 5.0
    
    # Unreliable proportion: 1 unreliable in session 2 (A->B)
    assert metrics["unreliable_timestamp_proportion"] == 1 / 5.0
    
    # Per session route diversity:
    # sess_1 has 2 unique edges
    # sess_2 has 3 unique edges
    # average = 2.5
    assert metrics["per_session_route_diversity"] == 2.5
    
    assert report["status"] == "NOT_READY"
    assert "Insufficient REAL transitions" in report["reasons"][0]

