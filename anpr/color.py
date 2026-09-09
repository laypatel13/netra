"""
Dominant-color extraction for a vehicle crop (PLAN.md Section 0b).

Used to tag every detected vehicle with a coarse color even when no plate
is legible, so it can still be tracked/matched by "type + color" (e.g. "red
car") instead of being dropped entirely.

Known limitation, stated plainly rather than hidden: color read off a small
CCTV crop at night or under sodium streetlight/headlight glare is
unreliable — a white car under orange sodium light can easily read as
"orange". This is exactly why every detection also stores a thumbnail
(see pipeline.py) — the computed label is a coarse hint for search/alerts,
the thumbnail is what a human actually verifies a match against. Do not
treat this as a precise or trustworthy-alone signal.
"""
from dataclasses import dataclass

import cv2
import numpy as np

# Small named palette, defined in HSV. Order matters — checked top to
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


@dataclass
class ColorReading:
    name: str
    confidence: float  # fraction of sampled pixels that agreed with the winning bucket


def dominant_color(crop) -> ColorReading:
    """
    Given a BGR vehicle crop, return the dominant named color.

    Approach: convert to HSV, drop pixels that are too bright (glare/sky/
    headlights) or too dark (shadow/underexposed) to carry real color
    information, then bucket the remaining pixels by hue (or
    black/white/gray by low saturation) and return the most common bucket.
    """
    if crop is None or crop.size == 0:
        return ColorReading(name="unknown", confidence=0.0)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # Keep only pixels with real color information — not blown-out glare,
    # not near-black shadow.
    valid = (v > 40) & (v < 240)
    if not np.any(valid):
        return ColorReading(name="unknown", confidence=0.0)

    h, s, v = h[valid], s[valid], v[valid]
    total = h.size

    # Achromatic first: low saturation means white/gray/black regardless of hue.
    achromatic = s < 40
    if np.count_nonzero(achromatic) / total > 0.6:
        v_achromatic = v[achromatic]
        bright = np.count_nonzero(v_achromatic > 170) / v_achromatic.size
        dark = np.count_nonzero(v_achromatic < 90) / v_achromatic.size
        if bright > 0.5:
            return ColorReading(name="white", confidence=float(bright))
        if dark > 0.5:
            return ColorReading(name="black", confidence=float(dark))
        return ColorReading(name="silver_gray", confidence=float(np.count_nonzero(achromatic) / total))

    # Chromatic: bucket the saturated pixels by hue.
    chromatic_mask = ~achromatic
    h_chromatic = h[chromatic_mask]
    if h_chromatic.size == 0:
        return ColorReading(name="unknown", confidence=0.0)

    best_name, best_count = "unknown", 0
    for name, ranges in _HUE_BUCKETS:
        mask = np.zeros(h_chromatic.shape, dtype=bool)
        for lo, hi in ranges:
            mask |= (h_chromatic >= lo) & (h_chromatic < hi)
        count = int(np.count_nonzero(mask))
        if count > best_count:
            best_name, best_count = name, count

    return ColorReading(name=best_name, confidence=best_count / h_chromatic.size if h_chromatic.size else 0.0)
