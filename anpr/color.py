"""
Dominant-color extraction for a vehicle crop (PLAN.md Section 0b).

Used to tag every detected vehicle with a coarse color even when no plate
is legible, so it can still be tracked/matched by "type + color" (e.g. "red
car") instead of being dropped entirely.

Known limitation, stated plainly rather than hidden: color read off a small
CCTV crop at night or under sodium streetlight/headlight glare is
unreliable - a white car under orange sodium light can easily read as
"orange". This is exactly why every detection also stores a thumbnail
(see pipeline.py) - the computed label is a coarse hint for search/alerts,
the thumbnail is what a human actually verifies a match against. Do not
treat this as a precise or trustworthy-alone signal.
"""
from dataclasses import dataclass

import cv2
import numpy as np

# Small named palette, defined in HSV. Order matters - checked top to
# bottom, first match wins. Achromatic buckets (black/white/gray) are
# checked via saturation/value; chromatic buckets via hue range.
_HUE_BUCKETS = [
    ("red", [(0, 10), (170, 180)]),
    ("orange", [(10, 22)]),
    ("yellow", [(22, 38)]),
    ("green", [(38, 85)]),
    ("blue", [(85, 130)]),
    ("purple", [(130, 155)]),
    ("pink", [(155, 170)]),
]


from typing import Optional

@dataclass
class ColorObservation:
    color: str
    confidence: float  # fraction of sampled pixels that agreed with the winning bucket
    usable: bool
    unknown_reason: Optional[str] = None


def dominant_color(crop) -> ColorObservation:
    """
    Given a BGR vehicle crop, return the dominant named color.

    Approach: convert to HSV, drop pixels that are too bright (glare/sky/
    headlights) or too dark (shadow/underexposed) to carry real color
    information, then bucket the remaining pixels by hue (or
    black/white/gray by low saturation) and return the most common bucket.
    """
    if crop is None or crop.size == 0:
        return ColorObservation(color="unknown", confidence=0.0, usable=False, unknown_reason="not_visible")

    # Focus extraction on the central region of the bounding box to ignore
    # road, sky, and shadows.
    # We explicitly exclude the bottom 25% (road/shadows) and top 25% (sky/background).
    h, w = crop.shape[:2]
    y1, y2 = int(h * 0.25), int(h * 0.75)
    x1, x2 = int(w * 0.25), int(w * 0.75)
    center_crop = crop[y1:y2, x1:x2]
    if center_crop.size == 0:
        center_crop = crop

    # Downsample heavily to speed up processing (O(1) relative to original size)
    # and blur out specular highlights / noise naturally.
    thumbnail = cv2.resize(center_crop, (32, 32), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(thumbnail, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # Keep only pixels with real color information - not blown-out glare,
    # not near-black shadow.
    valid = (v > 40) & (v < 240)
    if not np.any(valid):
        return ColorObservation(color="unknown", confidence=0.0, usable=False, unknown_reason="extraction_failed")

    h, s, v = h[valid], s[valid], v[valid]
    total = h.size

    achromatic = s < 40
    chromatic_mask = ~achromatic
    
    # Priority 1: Chromatic
    # If a significant portion of the valid pixels are chromatic, the car is likely colored.
    # Even 15% of chromatic pixels is usually enough to define a car's color, 
    # since windshields, grilles, and glare take up most of the surface area.
    chromatic_fraction = np.count_nonzero(chromatic_mask) / total
    
    if chromatic_fraction > 0.15:
        h_chromatic = h[chromatic_mask]
        best_name, best_count = "unknown", 0
        for name, ranges in _HUE_BUCKETS:
            mask = np.zeros(h_chromatic.shape, dtype=bool)
            for lo, hi in ranges:
                mask |= (h_chromatic >= lo) & (h_chromatic < hi)
            count = int(np.count_nonzero(mask))
            if count > best_count:
                best_name, best_count = name, count
                
        # To be confident, the winning hue bucket must represent a noticeable part of the car
        confidence = best_count / total
        if confidence > 0.10: 
            return ColorObservation(color=best_name, confidence=confidence, usable=True)

    # Priority 2: Achromatic (Fallback)
    # If we didn't find a strong color, assume the car is white/black/silver
    if np.count_nonzero(achromatic) / total > 0.5:
        v_achromatic = v[achromatic]
        bright = np.count_nonzero(v_achromatic > 170) / v_achromatic.size
        dark = np.count_nonzero(v_achromatic < 90) / v_achromatic.size
        if bright > 0.5:
            return ColorObservation(color="white", confidence=float(bright), usable=True)
        if dark > 0.5:
            return ColorObservation(color="black", confidence=float(dark), usable=True)
        return ColorObservation(color="silver_gray", confidence=float(np.count_nonzero(achromatic) / total), usable=True)

    return ColorObservation(color="unknown", confidence=0.0, usable=False, unknown_reason="low_confidence")
