"""
Frame quality scoring for evidence selection.

The goal is NOT to determine whether an image is aesthetically good - it's to
determine which frames are most useful for recognition/OCR.  A sharp, well-
exposed, large vehicle crop where the plate region is visible is worth more
than a blurry, dark, distant one even if the former is compositionally boring.

All scores are normalized to [0, 1].  The overall quality score is a weighted
combination of components, with weights exposed as module-level constants for
easy tuning.
"""
from dataclasses import dataclass

import cv2
import numpy as np

# Component weights for the overall quality score
W_SHARPNESS = 0.35
W_BLUR = 0.15
W_EXPOSURE = 0.20
W_SIZE = 0.30

# Sharpness: Laplacian variance thresholds for normalization
SHARPNESS_MIN = 10.0       # below this → quality ≈ 0
SHARPNESS_MAX = 500.0      # above this → quality ≈ 1

# Size: crop area thresholds (in pixels²)
SIZE_MIN = 2_000           # below → tiny/useless
SIZE_MAX = 100_000         # above → excellent coverage

# Exposure: target mean brightness (0-255 grayscale)
EXPOSURE_IDEAL = 127.0
EXPOSURE_TOLERANCE = 60.0  # how far from ideal before penalty starts


@dataclass
class QualityMetrics:
    """Per-frame quality breakdown."""
    sharpness: float        # 0-1
    blur: float             # 0-1 (1 = sharp, 0 = blurred)
    exposure: float         # 0-1 (1 = well-exposed)
    size: float             # 0-1 (1 = large crop)
    overall: float          # 0-1 weighted combination


def _normalize(value: float, lo: float, hi: float) -> float:
    """Clamp and normalize value to [0, 1]."""
    if hi <= lo:
        return 0.0
    return float(np.clip((value - lo) / (hi - lo), 0.0, 1.0))


def sharpness_score(crop) -> float:
    """
    Laplacian variance - higher means sharper edges.

    This is the standard measure used in autofocus systems and image-quality
    assessments.  A vehicle crop with crisp edges (body lines, plate
    characters, reflections) will score higher than a motion-blurred one.
    """
    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return _normalize(lap_var, SHARPNESS_MIN, SHARPNESS_MAX)


def blur_score(crop) -> float:
    """
    Motion blur estimate using horizontal vs vertical Sobel ratio.

    Motion blur in traffic footage is predominantly horizontal (vehicles move
    laterally across the frame).  A blurred image has weak high-frequency
    horizontal gradients relative to vertical ones.
    """
    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop

    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

    energy_x = np.mean(np.abs(sobel_x))
    energy_y = np.mean(np.abs(sobel_y))

    if energy_y < 1e-6:
        return 0.5  # can't determine - neutral

    # Ratio of horizontal to vertical gradients; close to 1 = balanced = sharp
    ratio = energy_x / energy_y
    # Penalize when horizontal energy is much lower (motion blur)
    return float(np.clip(ratio, 0.0, 1.0))


def exposure_score(crop) -> float:
    """
    Penalize severely underexposed or overexposed crops.

    The ideal brightness for OCR/recognition is mid-range.  Sodium streetlights,
    headlight glare, and deep shadow all degrade recognition.
    """
    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    mean_brightness = float(np.mean(gray))

    deviation = abs(mean_brightness - EXPOSURE_IDEAL)
    # Score: 1.0 at ideal, drops linearly, clamped at 0
    score = 1.0 - (deviation / (EXPOSURE_IDEAL + EXPOSURE_TOLERANCE))
    return float(np.clip(score, 0.0, 1.0))


def size_score(crop) -> float:
    """
    Larger vehicle crops contain more pixel data for recognition.

    A vehicle that fills the frame is vastly more useful for plate reading
    than a distant dot.
    """
    if crop is None or crop.size == 0:
        return 0.0
    h, w = crop.shape[:2]
    area = h * w
    return _normalize(area, SIZE_MIN, SIZE_MAX)


def compute_quality(crop) -> QualityMetrics:
    """
    Compute all quality metrics for a vehicle crop.

    Returns a QualityMetrics dataclass with individual component scores and
    a weighted overall score.
    """
    if crop is None or crop.size == 0:
        return QualityMetrics(
            sharpness=0.0, blur=0.0, exposure=0.0, size=0.0, overall=0.0
        )

    s = sharpness_score(crop)
    b = blur_score(crop)
    e = exposure_score(crop)
    z = size_score(crop)

    overall = (
        W_SHARPNESS * s +
        W_BLUR * b +
        W_EXPOSURE * e +
        W_SIZE * z
    )

    return QualityMetrics(
        sharpness=round(s, 4),
        blur=round(b, 4),
        exposure=round(e, 4),
        size=round(z, 4),
        overall=round(overall, 4),
    )
