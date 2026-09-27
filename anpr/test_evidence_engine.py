import pytest
from evidence_buffer import TrackBuffer, Observation, ConsensusState
from quality import QualityMetrics
from scoring import compute_evidence_score, MatchTier
from target_filter import filter_candidate, MatchResult

def test_dropped_frame_temporal_span():
    """Test that temporal span uses PTS rather than frame count."""
    buffer = TrackBuffer(camera_id="test", track_id=1)
    
    # Simulating 5 frames across 2 seconds (e.g. dropped frames)
    # First frame at 1000ms
    # Note: adding an empty numpy array for the crop argument
    import numpy as np
    buffer.add(Observation(10, 1000.0, (0,0,10,10), 0.9, np.array([]), QualityMetrics(0.8, 0, 0, 0, 0.8), "car", "red", None))
    # Last frame at 3000ms
    buffer.add(Observation(11, 3000.0, (0,0,10,10), 0.9, np.array([]), QualityMetrics(0.8, 0, 0, 0, 0.8), "car", "red", None))
    
    consensus = buffer.dominant_color
    assert consensus.temporal_span == 2.0  # (3000 - 1000) / 1000
    assert consensus.dominant_color == "red"

def test_color_gating_red_vs_white():
    """Test RED vs WHITE results in NON_MATCH."""
    res = filter_candidate(
        target_plate=None,
        target_type="car",
        target_color="red",
        candidate_type="car",
        candidate_color="white",
    )
    assert res.should_investigate == False
    assert res.color_match == MatchResult.NON_MATCH
    
    score = compute_evidence_score(
        target_plate=None, target_type="car", target_color="red",
        ocr_best_plate=None, ocr_consensus_score=0.0,
        detected_type="car", detected_color="white",
        image_quality=0.8, evidence_frame_count=2
    )
    assert score.tier == MatchTier.NO_MATCH
    assert "Explicit color mismatch" in score.explanation

def test_color_gating_red_vs_unknown():
    """Test RED vs UNKNOWN continues but flags completeness."""
    res = filter_candidate(
        target_plate=None,
        target_type="car",
        target_color="red",
        candidate_type="car",
        candidate_color="unknown",
    )
    assert res.should_investigate == True
    assert res.color_match == MatchResult.UNKNOWN
    
    score = compute_evidence_score(
        target_plate=None, target_type="car", target_color="red",
        ocr_best_plate=None, ocr_consensus_score=0.0,
        detected_type="car", detected_color="unknown",
        image_quality=0.8, evidence_frame_count=2,
        color_unknown_reason="low_confidence"
    )
    assert score.tier != MatchTier.NO_MATCH
    assert "Color unknown (low_confidence)" in score.explanation
    
def test_required_color():
    """Required-but-unknown color remains insufficient evidence, not a lie."""
    res = filter_candidate(
        target_plate=None,
        target_type="car",
        target_color="red",
        candidate_type="car",
        candidate_color="unknown",
        color_required=True
    )
    assert res.should_investigate == True
    assert res.color_match == MatchResult.UNKNOWN
