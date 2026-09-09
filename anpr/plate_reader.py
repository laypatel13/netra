"""
Plate reader — runs OCR over a vehicle crop and filters results down to
strings that actually look like an Indian number plate.

EasyOCR does its own text-region detection internally, so this doesn't need
a separate plate-localization model. It's given a vehicle crop (not the
full frame) to keep unrelated text (signage, shopfronts) out of the search
area, and every OCR candidate is validated against a plate-format regex
before being accepted — this never returns an unvalidated raw OCR guess,
since that's exactly what would flood the detection store with junk.
"""
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import easyocr

# Standard Indian plate format, e.g. GJ01AB1234 — 2 letters (state), 1-2
# digits (RTO code), 1-3 letters (series), 4 digits (number). Loose enough
# to catch older/newer formats, strict enough to reject OCR noise.
#
# Deliberately unanchored: OCR on real footage sometimes picks up a
# character or two of boundary noise around the actual plate (a sticker,
# a frame edge, a reflection) alongside a perfectly legible plate. Given
# how hard a legible plate already is to get in this footage (PLAN.md
# Section 0b), requiring the *entire* OCR string to be exactly the plate
# format throws away genuine positives for no real safety benefit — the
# 8-10 character shape here is specific enough that finding it as a
# substring is still a strong signal, not a loosened one.
PLATE_PATTERN = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}")

# Surveying real footage from cctv.corp8.cloud (wide-angle traffic/junction
# cams, not close-up ANPR-purpose cameras) showed most vehicle crops are far
# too small for plate text to be legible at all — commonly under 150px wide
# for the whole vehicle, so the plate region within it is a handful of
# pixels. Upscaling before OCR is a standard, cheap mitigation: EasyOCR's
# own text detector needs the plate to span enough pixels to find text
# regions in the first place, not just to read them clearly.
MIN_CROP_WIDTH_FOR_OCR = 300


@dataclass
class PlateReading:
    text: str
    confidence: float


class PlateReader:
    def __init__(self, gpu: bool = False):
        # First run downloads recognition/detection weights from EasyOCR's
        # GitHub releases — needs network access to github.com.
        self.reader = easyocr.Reader(["en"], gpu=gpu)

    def read(self, vehicle_crop) -> Optional[PlateReading]:
        """
        Returns the best plate-format-matching text found in the crop, or
        None if nothing in it looks like a real plate.
        """
        height, width = vehicle_crop.shape[:2]
        if 0 < width < MIN_CROP_WIDTH_FOR_OCR:
            scale = MIN_CROP_WIDTH_FOR_OCR / width
            vehicle_crop = cv2.resize(
                vehicle_crop, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_CUBIC
            )

        results = self.reader.readtext(vehicle_crop)
        best: Optional[PlateReading] = None
        for _bbox, text, conf in results:
            normalized = re.sub(r"[^A-Z0-9]", "", text.upper())
            match = PLATE_PATTERN.search(normalized)
            if match:
                # Extract just the matched plate substring, not the whole
                # OCR string — that's the point of searching instead of
                # matching the full text.
                candidate = match.group()
                if best is None or conf > best.confidence:
                    best = PlateReading(text=candidate, confidence=float(conf))
        return best
