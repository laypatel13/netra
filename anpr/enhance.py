"""
Conservative image enhancement for vehicle crops.

Intended to improve downstream OCR/recognition - NOT to reconstruct missing
information.  The original raw crop is NEVER overwritten; both raw and enhanced
versions are stored separately so a human verifier always has the unmodified
evidence.

Enhancement stages (all optional, individually configurable):
1. Upscale (bicubic or INTER_CUBIC)
2. Denoise (fastNlMeansDenoisingColored)
3. CLAHE on L channel (LAB space)
4. Mild unsharp mask

IMPORTANT truthfulness requirement:
- Never describe this as "AI reconstructing the original plate"
- Never claim "super-resolution recovered missing pixels"
- This is "image enhancement to improve recognition clarity"
- The raw evidence is always the ground truth
"""
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

# Global enable/disable
ENABLE_ENHANCEMENT = True

# Upscale
UPSCALE_FACTOR = 2
UPSCALE_INTERPOLATION = cv2.INTER_CUBIC

# Denoise
DENOISE_STRENGTH = 6        # filter strength (higher = smoother, risks losing detail)
DENOISE_COLOR_STRENGTH = 6

# CLAHE (Contrast Limited Adaptive Histogram Equalization)
CLAHE_CLIP_LIMIT = 2.0
CLAHE_GRID_SIZE = (8, 8)

# Unsharp mask
UNSHARP_SIGMA = 1.0
UNSHARP_STRENGTH = 0.5      # blending weight of the sharpened component


@dataclass
class EnhancementResult:
    """Result of enhancement - always contains the raw crop."""
    raw_crop: np.ndarray
    enhanced_crop: Optional[np.ndarray]
    enhancement_applied: bool
    stages_applied: list[str]
    error: Optional[str] = None


def _upscale(crop: np.ndarray, factor: int) -> np.ndarray:
    """Resize by the given factor using cubic interpolation."""
    h, w = crop.shape[:2]
    return cv2.resize(
        crop,
        (w * factor, h * factor),
        interpolation=UPSCALE_INTERPOLATION,
    )


def _denoise(crop: np.ndarray) -> np.ndarray:
    """Apply non-local means denoising (color-aware)."""
    return cv2.fastNlMeansDenoisingColored(
        crop,
        None,
        DENOISE_STRENGTH,
        DENOISE_COLOR_STRENGTH,
        7,   # template window size
        21,  # search window size
    )


def _clahe(crop: np.ndarray) -> np.ndarray:
    """Apply CLAHE on the L channel in LAB space."""
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(
        clipLimit=CLAHE_CLIP_LIMIT,
        tileGridSize=CLAHE_GRID_SIZE,
    )
    l_channel = clahe.apply(l_channel)

    lab = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _unsharp_mask(crop: np.ndarray) -> np.ndarray:
    """Mild unsharp mask to enhance edges without introducing artifacts."""
    blurred = cv2.GaussianBlur(crop, (0, 0), UNSHARP_SIGMA)
    sharpened = cv2.addWeighted(
        crop, 1.0 + UNSHARP_STRENGTH,
        blurred, -UNSHARP_STRENGTH,
        0,
    )
    return sharpened


def enhance_crop(
    raw_crop: np.ndarray,
    enable: bool = ENABLE_ENHANCEMENT,
    upscale_factor: int = UPSCALE_FACTOR,
    do_denoise: bool = False,
    do_clahe: bool = True,
    do_sharpen: bool = True,
) -> EnhancementResult:
    """
    Apply conservative enhancement to a vehicle crop.

    Returns an EnhancementResult containing both the raw crop (unchanged)
    and the enhanced derivative.  Falls back to raw-only on any failure.

    Parameters:
        raw_crop: original BGR vehicle crop
        enable: master switch - if False, returns raw only
        upscale_factor: resize factor (1 = no resize)
        do_denoise: apply denoising
        do_clahe: apply CLAHE contrast normalization
        do_sharpen: apply mild unsharp mask
    """
    if raw_crop is None or raw_crop.size == 0:
        return EnhancementResult(
            raw_crop=raw_crop,
            enhanced_crop=None,
            enhancement_applied=False,
            stages_applied=[],
            error="Empty crop",
        )

    if not enable:
        return EnhancementResult(
            raw_crop=raw_crop,
            enhanced_crop=None,
            enhancement_applied=False,
            stages_applied=[],
        )

    try:
        enhanced = raw_crop.copy()
        stages = []

        # Stage 1: Upscale
        if upscale_factor > 1:
            enhanced = _upscale(enhanced, upscale_factor)
            stages.append(f"upscale_{upscale_factor}x")

        # Stage 2: Denoise
        if do_denoise:
            enhanced = _denoise(enhanced)
            stages.append("denoise")

        # Stage 3: CLAHE
        if do_clahe:
            enhanced = _clahe(enhanced)
            stages.append("clahe")

        # Stage 4: Unsharp mask
        if do_sharpen:
            enhanced = _unsharp_mask(enhanced)
            stages.append("unsharp_mask")

        return EnhancementResult(
            raw_crop=raw_crop,
            enhanced_crop=enhanced,
            enhancement_applied=True,
            stages_applied=stages,
        )

    except Exception as e:
        # Enhancement failed - return raw crop only, never crash
        return EnhancementResult(
            raw_crop=raw_crop,
            enhanced_crop=None,
            enhancement_applied=False,
            stages_applied=[],
            error=str(e),
        )
