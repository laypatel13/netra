import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../anpr')))

from ocr_consensus import FrameOCRResult, compute_consensus, character_vote


def test_ocr_consensus_unanimous():
    readings = [
        FrameOCRResult(frame_index=1, plate="GJ01AB1234", confidence=0.9),
        FrameOCRResult(frame_index=2, plate="GJ01AB1234", confidence=0.85),
        FrameOCRResult(frame_index=3, plate="GJ01AB1234", confidence=0.95),
    ]
    consensus = compute_consensus(readings)
    assert consensus.best_plate == "GJ01AB1234"
    assert consensus.supporting_frames == 3
    assert not consensus.has_disagreement


def test_ocr_consensus_disagreement():
    readings = [
        FrameOCRResult(frame_index=1, plate="GJ01AB1234", confidence=0.9),
        FrameOCRResult(frame_index=2, plate="GJ01AB1284", confidence=0.85),
        FrameOCRResult(frame_index=3, plate="GJ01AB1234", confidence=0.7),
    ]
    consensus = compute_consensus(readings)
    assert consensus.best_plate == "GJ01AB1234"
    assert consensus.supporting_frames == 2
    assert consensus.has_disagreement
    assert len(consensus.all_candidates) == 2


def test_ocr_consensus_empty():
    readings = [
        FrameOCRResult(frame_index=1, plate=None, confidence=0.0),
        FrameOCRResult(frame_index=2, plate=None, confidence=0.0),
    ]
    consensus = compute_consensus(readings)
    assert consensus.best_plate is None
    assert consensus.frames_with_plate == 0


def test_character_vote():
    # Voting across 3 plates
    plates = [
        "GJ01AB1234",
        "GJ01AB1284",
        "GJ01XB1234"
    ]
    # At pos 4 (X vs A vs A) -> A wins
    # At pos 8 (8 vs 3 vs 3) -> 3 wins
    # Expect: GJ01AB1234
    voted = character_vote(plates)
    assert voted == "GJ01AB1234"

def test_character_vote_mismatched_lengths():
    plates = [
        "GJ01AB1234",
        "GJ01AB123"
    ]
    assert character_vote(plates) is None
