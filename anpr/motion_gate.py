"""
Motion-gated frame filtering for NETRA pipeline.

Uses OpenCV's MOG2 background subtractor to cheaply determine whether a
frame contains any significant motion before running expensive YOLO
inference.  Operates entirely on CPU — zero GPU usage.

The gate runs on a downscaled grayscale copy of the frame (¼ resolution)
for speed.  When motion is detected, it returns bounding rectangles of
the moving regions in original-frame coordinates so the detector can
run inference only on those crops.

Design principles:
  - NEVER miss a real vehicle.  False positives (detecting motion where
    there is none) are acceptable; false negatives (missing a moving
    vehicle) are not.  The periodic full-frame sweep is the safety net.
  - Camera-specific.  Each camera gets its own MotionGate instance with
    its own background model.  A PTZ camera has different "background"
    than a fixed one.
  - Warmup period.  The first N frames always pass through so the
    background model can stabilize.
  - Thread-safe within one camera thread (no cross-thread sharing).

Usage:
    gate = MotionGate()
    result = gate.process(frame)
    if not result.has_motion:
        # skip YOLO entirely
        continue
    if result.use_full_frame:
        detections = detector.detect(frame)
    else:
        detections = detector.detect_rois(frame, result.motion_rois)
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("netra.motion_gate")

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

# Downscale factor for motion analysis (4 = ¼ resolution)
DOWNSCALE_FACTOR = 4

# Warmup: first N frames always pass through (background model stabilization)
WARMUP_FRAMES = 30

# Minimum contour area as a fraction of the (downscaled) frame area.
# Smaller values = more sensitive.  0.002 = 0.2% of frame.
MIN_CONTOUR_AREA_FRACTION = 0.002

# Morphological kernel size for noise removal (pixels in downscaled space)
MORPH_KERNEL_SIZE = 5

# Padding around motion ROIs (fraction of ROI dimension) to ensure the
# detector sees the full vehicle, not just the bumper that moved.
ROI_PADDING_FRACTION = 0.25

# Merge distance: ROIs closer than this (fraction of frame diagonal)
# are merged into a single larger ROI.
ROI_MERGE_DISTANCE_FRACTION = 0.08

# Force a full-frame detection every N processed frames, regardless of
# motion.  Safety net for slowly-creeping vehicles that the background
# model might absorb.
FORCE_FULL_DETECT_EVERY_N = 150

# MOG2 configuration
MOG2_HISTORY = 300          # frames of history for background model
MOG2_VAR_THRESHOLD = 25     # variance threshold for foreground detection
MOG2_DETECT_SHADOWS = False # shadow detection adds cost; skip for speed


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MotionROI:
    """A rectangular region of detected motion in original-frame coordinates."""
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass
class MotionResult:
    """Result of motion analysis for a single frame."""
    has_motion: bool                    # any significant motion detected?
    use_full_frame: bool                # should we run full-frame YOLO?
    motion_rois: list[MotionROI]        # bounding regions of motion (original coords)
    motion_fraction: float              # fraction of frame that is moving (0.0–1.0)
    frame_index: int                    # which frame this result is for
    is_warmup: bool = False             # still in warmup period?

    @property
    def roi_count(self) -> int:
        return len(self.motion_rois)


# ---------------------------------------------------------------------------
# MotionGate
# ---------------------------------------------------------------------------

class MotionGate:
    """
    Lightweight motion-based frame filter using MOG2 background subtraction.

    Each camera thread owns its own MotionGate instance.  The background
    model adapts over time, so fixed cameras will learn their static
    background (parked cars, road markings) and only trigger on new motion.

    Thread-safety: NOT thread-safe — each camera thread owns its own instance.
    """

    def __init__(
        self,
        downscale_factor: int = DOWNSCALE_FACTOR,
        warmup_frames: int = WARMUP_FRAMES,
        min_contour_area_fraction: float = MIN_CONTOUR_AREA_FRACTION,
        morph_kernel_size: int = MORPH_KERNEL_SIZE,
        roi_padding_fraction: float = ROI_PADDING_FRACTION,
        roi_merge_distance_fraction: float = ROI_MERGE_DISTANCE_FRACTION,
        force_full_detect_every_n: int = FORCE_FULL_DETECT_EVERY_N,
    ):
        self.downscale_factor = downscale_factor
        self.warmup_frames = warmup_frames
        self.min_contour_area_fraction = min_contour_area_fraction
        self.roi_padding_fraction = roi_padding_fraction
        self.roi_merge_distance_fraction = roi_merge_distance_fraction
        self.force_full_detect_every_n = force_full_detect_every_n

        # MOG2 background subtractor
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=MOG2_HISTORY,
            varThreshold=MOG2_VAR_THRESHOLD,
            detectShadows=MOG2_DETECT_SHADOWS,
        )

        # Morphological kernel for noise removal
        self._morph_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (morph_kernel_size, morph_kernel_size),
        )

        # Frame counter
        self._frame_count = 0

        # Stats
        self.total_frames = 0
        self.frames_with_motion = 0
        self.frames_skipped = 0
        self.frames_full_detect = 0
        self.frames_roi_detect = 0

    def process(self, frame: np.ndarray) -> MotionResult:
        """
        Analyze a frame for motion and return a MotionResult.

        The caller should use the result to decide whether to run YOLO
        and, if so, whether to run it on the full frame or only on ROIs.
        """
        self._frame_count += 1
        self.total_frames += 1
        h_orig, w_orig = frame.shape[:2]

        # --- Periodic full-frame sweep (safety net) ---
        if self._frame_count % self.force_full_detect_every_n == 0:
            # Still feed the frame to the background model to keep it current
            small = self._downscale(frame)
            self._bg_subtractor.apply(
                cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if len(small.shape) == 3 else small
            )
            self.frames_full_detect += 1
            return MotionResult(
                has_motion=True,
                use_full_frame=True,
                motion_rois=[],
                motion_fraction=0.0,
                frame_index=self._frame_count,
            )

        # --- Warmup period: always pass through ---
        if self._frame_count <= self.warmup_frames:
            small = self._downscale(frame)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if len(small.shape) == 3 else small
            self._bg_subtractor.apply(gray)
            self.frames_full_detect += 1
            return MotionResult(
                has_motion=True,
                use_full_frame=True,
                motion_rois=[],
                motion_fraction=0.0,
                frame_index=self._frame_count,
                is_warmup=True,
            )

        # --- Normal motion detection ---
        small = self._downscale(frame)
        h_small, w_small = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if len(small.shape) == 3 else small

        # Apply background subtraction
        fg_mask = self._bg_subtractor.apply(gray)

        # Morphological operations to remove noise
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self._morph_kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self._morph_kernel)

        # Threshold — MOG2 may produce grayscale values for shadows
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        # Calculate motion fraction
        total_pixels = h_small * w_small
        motion_pixels = cv2.countNonZero(fg_mask)
        motion_fraction = motion_pixels / total_pixels if total_pixels > 0 else 0.0

        # Find contours of motion regions
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter by minimum area
        min_area = total_pixels * self.min_contour_area_fraction
        significant_contours = [c for c in contours if cv2.contourArea(c) >= min_area]

        if not significant_contours:
            self.frames_skipped += 1
            return MotionResult(
                has_motion=False,
                use_full_frame=False,
                motion_rois=[],
                motion_fraction=motion_fraction,
                frame_index=self._frame_count,
            )

        # Convert contours to bounding rectangles in downscaled coordinates
        raw_rois = []
        for c in significant_contours:
            x, y, w, h = cv2.boundingRect(c)
            raw_rois.append((x, y, x + w, y + h))

        # Merge close ROIs
        merged = self._merge_rois(raw_rois, h_small, w_small)

        # Pad and scale back to original frame coordinates
        motion_rois = []
        for (x1, y1, x2, y2) in merged:
            roi = self._pad_and_scale_roi(
                x1, y1, x2, y2,
                h_small, w_small,
                h_orig, w_orig,
            )
            motion_rois.append(roi)

        self.frames_with_motion += 1
        self.frames_roi_detect += 1

        return MotionResult(
            has_motion=True,
            use_full_frame=False,
            motion_rois=motion_rois,
            motion_fraction=motion_fraction,
            frame_index=self._frame_count,
        )

    def _downscale(self, frame: np.ndarray) -> np.ndarray:
        """Downscale frame by the configured factor."""
        if self.downscale_factor <= 1:
            return frame
        h, w = frame.shape[:2]
        new_w = max(1, w // self.downscale_factor)
        new_h = max(1, h // self.downscale_factor)
        return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _merge_rois(
        self,
        rois: list[tuple[int, int, int, int]],
        frame_h: int,
        frame_w: int,
    ) -> list[tuple[int, int, int, int]]:
        """
        Merge ROIs that are close together into larger bounding boxes.

        Uses a simple iterative approach: keep merging pairs of overlapping
        or close-together boxes until no more merges are possible.
        """
        if not rois:
            return []

        diag = (frame_h ** 2 + frame_w ** 2) ** 0.5
        merge_dist = diag * self.roi_merge_distance_fraction

        boxes = list(rois)
        merged = True
        while merged:
            merged = False
            new_boxes = []
            used = set()
            for i in range(len(boxes)):
                if i in used:
                    continue
                bx = boxes[i]
                for j in range(i + 1, len(boxes)):
                    if j in used:
                        continue
                    if self._boxes_close(bx, boxes[j], merge_dist):
                        # Merge
                        bx = (
                            min(bx[0], boxes[j][0]),
                            min(bx[1], boxes[j][1]),
                            max(bx[2], boxes[j][2]),
                            max(bx[3], boxes[j][3]),
                        )
                        used.add(j)
                        merged = True
                new_boxes.append(bx)
                used.add(i)
            boxes = new_boxes

        return boxes

    @staticmethod
    def _boxes_close(
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        dist: float,
    ) -> bool:
        """Check if two boxes overlap or are within `dist` pixels of each other."""
        # Expand box a by dist on each side and check overlap with b
        ax1, ay1, ax2, ay2 = a[0] - dist, a[1] - dist, a[2] + dist, a[3] + dist
        bx1, by1, bx2, by2 = b
        return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)

    def _pad_and_scale_roi(
        self,
        x1: int, y1: int, x2: int, y2: int,
        h_small: int, w_small: int,
        h_orig: int, w_orig: int,
    ) -> MotionROI:
        """Add padding and scale ROI from downscaled to original coordinates."""
        w_roi = x2 - x1
        h_roi = y2 - y1
        pad_w = int(w_roi * self.roi_padding_fraction)
        pad_h = int(h_roi * self.roi_padding_fraction)

        # Pad in downscaled space
        x1 = max(0, x1 - pad_w)
        y1 = max(0, y1 - pad_h)
        x2 = min(w_small, x2 + pad_w)
        y2 = min(h_small, y2 + pad_h)

        # Scale to original frame coordinates
        scale_x = w_orig / w_small
        scale_y = h_orig / h_small

        return MotionROI(
            x1=int(x1 * scale_x),
            y1=int(y1 * scale_y),
            x2=int(x2 * scale_x),
            y2=int(y2 * scale_y),
        )

    def reset(self):
        """Reset the motion gate — call after camera reconnect or scene cut."""
        self._frame_count = 0
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=MOG2_HISTORY,
            varThreshold=MOG2_VAR_THRESHOLD,
            detectShadows=MOG2_DETECT_SHADOWS,
        )

    @property
    def savings_pct(self) -> float:
        """Percentage of frames saved by motion gating."""
        if self.total_frames == 0:
            return 0.0
        return (self.frames_skipped / self.total_frames) * 100.0

    def stats_dict(self) -> dict:
        """Return a dict of motion gate statistics."""
        return {
            "total_frames": self.total_frames,
            "frames_with_motion": self.frames_with_motion,
            "frames_skipped_no_motion": self.frames_skipped,
            "frames_full_detect": self.frames_full_detect,
            "frames_roi_detect": self.frames_roi_detect,
            "savings_pct": round(self.savings_pct, 1),
        }
