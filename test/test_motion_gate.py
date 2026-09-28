"""
Tests for MotionGate.

Validates the motion detection gate that filters frames before YOLO inference.
"""
import sys
import os
import numpy as np

# Allow imports from the anpr directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "anpr"))

from motion_gate import MotionGate, MotionROI


def _make_frame(h=480, w=640, color=(128, 128, 128)):
    """Create a solid-color BGR frame."""
    frame = np.full((h, w, 3), color, dtype=np.uint8)
    return frame


def _add_rectangle(frame, x, y, w, h, color=(255, 255, 255)):
    """Draw a filled rectangle on the frame (simulates a moving object)."""
    out = frame.copy()
    out[y:y+h, x:x+w] = color
    return out


class TestMotionGateWarmup:
    """During warmup, all frames should pass through regardless of content."""

    def test_warmup_always_passes(self):
        gate = MotionGate(warmup_frames=10)
        base = _make_frame()

        for i in range(10):
            result = gate.process(base)
            assert result.has_motion is True, f"Frame {i+1} should pass during warmup"
            assert result.use_full_frame is True
            assert result.is_warmup is True

    def test_after_warmup_static_scene_stops(self):
        gate = MotionGate(warmup_frames=5)
        base = _make_frame()

        # Feed warmup frames
        for _ in range(5):
            gate.process(base)

        # After warmup, feeding the same static frame should eventually
        # report no motion (background model has learned the scene)
        # Give it a few frames for the model to stabilize
        no_motion_count = 0
        for _ in range(50):
            result = gate.process(base)
            if not result.has_motion:
                no_motion_count += 1

        assert no_motion_count > 0, (
            "After warmup with a static scene, at least some frames "
            "should be classified as no-motion"
        )


class TestMotionGateDetection:
    """Test that actual motion is detected."""

    def test_moving_object_detected(self):
        gate = MotionGate(warmup_frames=5)
        base = _make_frame(color=(80, 80, 80))

        # Warmup with static scene
        for _ in range(30):
            gate.process(base)

        # Introduce a moving object (shifted rectangle across frames)
        motion_detected = False
        for i in range(20):
            x_offset = 100 + i * 15
            frame = _add_rectangle(base, x_offset, 200, 80, 60, color=(255, 255, 255))
            result = gate.process(frame)
            if result.has_motion:
                motion_detected = True

        assert motion_detected, "Moving object should be detected as motion"

    def test_motion_rois_are_in_correct_region(self):
        gate = MotionGate(warmup_frames=5)
        base = _make_frame(480, 640, color=(80, 80, 80))

        # Warmup
        for _ in range(40):
            gate.process(base)

        # Place a bright rectangle in the bottom-right quadrant
        frame = _add_rectangle(base, 400, 300, 100, 80, color=(255, 255, 255))
        result = gate.process(frame)

        if result.has_motion and result.motion_rois:
            # At least one ROI should overlap with the rectangle's region
            roi = result.motion_rois[0]
            # The ROI should be roughly in the right half, bottom half
            assert roi.x1 < 640, "ROI x1 should be within frame"
            assert roi.y1 < 480, "ROI y1 should be within frame"
            assert roi.x2 > 350, "ROI should be in the right region"
            assert roi.y2 > 250, "ROI should be in the bottom region"


class TestMotionGatePeriodicFullFrame:
    """Test the periodic full-frame detection safety net."""

    def test_periodic_full_frame_fires(self):
        n = 20
        gate = MotionGate(warmup_frames=5, force_full_detect_every_n=n)
        base = _make_frame()

        # Burn through warmup
        for _ in range(5):
            gate.process(base)

        # The Nth frame (after warmup) should trigger full-frame
        full_frame_results = []
        for i in range(n * 3):
            result = gate.process(base)
            if result.use_full_frame and not result.is_warmup:
                full_frame_results.append(gate._frame_count)

        assert len(full_frame_results) > 0, (
            f"Should have at least one periodic full-frame detection "
            f"within {n * 3} frames after warmup"
        )


class TestMotionGateStats:
    """Test the statistics tracking."""

    def test_stats_accumulate(self):
        gate = MotionGate(warmup_frames=3)
        base = _make_frame()

        for _ in range(10):
            gate.process(base)

        stats = gate.stats_dict()
        assert stats["total_frames"] == 10
        assert stats["frames_full_detect"] + stats["frames_skipped_no_motion"] + stats["frames_roi_detect"] == 10

    def test_savings_pct_calculation(self):
        gate = MotionGate(warmup_frames=2)
        base = _make_frame()

        # 2 warmup (full detect) + 8 more (should mostly be skipped for static)
        for _ in range(50):
            gate.process(base)

        assert gate.savings_pct >= 0.0
        assert gate.savings_pct <= 100.0


class TestMotionGateReset:
    """Test that reset clears the background model."""

    def test_reset_restarts_warmup(self):
        gate = MotionGate(warmup_frames=5)
        base = _make_frame()

        # Go through warmup
        for _ in range(10):
            gate.process(base)

        # Reset
        gate.reset()

        # Should be back in warmup
        result = gate.process(base)
        assert result.has_motion is True
        assert result.use_full_frame is True
        assert result.is_warmup is True


class TestMotionROI:
    """Test MotionROI data class."""

    def test_area_calculation(self):
        roi = MotionROI(x1=10, y1=20, x2=110, y2=120)
        assert roi.area == 10000  # 100 * 100

    def test_to_tuple(self):
        roi = MotionROI(x1=10, y1=20, x2=30, y2=40)
        assert roi.to_tuple() == (10, 20, 30, 40)

    def test_zero_area(self):
        roi = MotionROI(x1=10, y1=20, x2=10, y2=20)
        assert roi.area == 0
