"""
Plate reader - runs OCR over a vehicle crop and filters results down to
strings that actually look like an Indian number plate.

EasyOCR does its own text-region detection internally, but for the investigation
pipeline we also add a plate-region proposal stage (OpenCV heuristics) to narrow
the search area further before falling back to full-crop OCR.
"""
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import easyocr
import numpy as np

# Standard Indian plate format, e.g. GJ01AB1234
PLATE_PATTERN = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}")

MIN_CROP_WIDTH_FOR_OCR = 300


@dataclass
class PlateReading:
    text: str
    confidence: float


class PlateReader:
    def __init__(self, gpu: bool = False):
        self.reader = easyocr.Reader(["en"], gpu=gpu, quantize=True)

    def read(self, vehicle_crop) -> Optional[PlateReading]:
        """
        Original behavior: returns the best plate-format-matching text found
        in the full vehicle crop.
        """
        candidates = self._read_crop(vehicle_crop)
        if not candidates:
            return None
        # Return the one with highest confidence
        return max(candidates, key=lambda c: c.confidence)

    def read_with_regions(self, vehicle_crop, enhance_func=None) -> list[PlateReading]:
        """
        Investigation mode: uses a plate-region proposal stage.
        If OpenCV heuristics find plate-shaped rectangles, crops them, optionally
        enhances them, and runs OCR.
        If no regions found or OCR yields nothing, falls back to full vehicle crop.
        Returns a list of all valid plate candidates found.
        """
        candidates = []
        regions = self._find_plate_regions(vehicle_crop)

        # Try proposed regions first
        for region in regions:
            if enhance_func:
                res = enhance_func(region)
                crop_to_read = res.enhanced_crop if res.enhanced_crop is not None else res.raw_crop
            else:
                crop_to_read = region

            candidates.extend(self._read_crop(crop_to_read))

        if candidates:
            return candidates

        # Fallback to bottom half of vehicle crop (where plates typically are)
        # to avoid reading text on truck bodies or background signage, and to speed up OCR.
        h, w = vehicle_crop.shape[:2]
        bottom_half = vehicle_crop[int(h*0.5):, :]
        
        if enhance_func:
            res = enhance_func(bottom_half)
            crop_to_read = res.enhanced_crop if res.enhanced_crop is not None else res.raw_crop
        else:
            crop_to_read = bottom_half

        return self._read_crop(crop_to_read)

    def _read_crop(self, crop) -> list[PlateReading]:
        if crop is None or crop.size == 0:
            return []

        height, width = crop.shape[:2]
        if 0 < width < MIN_CROP_WIDTH_FOR_OCR:
            scale = MIN_CROP_WIDTH_FOR_OCR / width
            crop = cv2.resize(
                crop, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_CUBIC
            )

        results = self.reader.readtext(crop, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        valid = []
        for _bbox, text, conf in results:
            normalized = re.sub(r"[^A-Z0-9]", "", text.upper())
            match = PLATE_PATTERN.search(normalized)
            if match:
                candidate = match.group()
                valid.append(PlateReading(text=candidate, confidence=float(conf)))
        return valid

    def _find_plate_regions(self, vehicle_crop) -> list[np.ndarray]:
        """Use OpenCV heuristics to find plate-shaped rectangular regions."""
        if vehicle_crop is None or vehicle_crop.size == 0:
            return []

        gray = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2GRAY) if len(vehicle_crop.shape) == 3 else vehicle_crop
        bfilter = cv2.bilateralFilter(gray, 11, 17, 17)
        edged = cv2.Canny(bfilter, 30, 200)

        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]

        regions = []
        for contour in contours:
            approx = cv2.approxPolyDP(contour, 10, True)
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                aspect_ratio = w / float(h)
                # Indian plates are usually rectangular, aspect ratio between 1.5 and 6
                if 1.5 < aspect_ratio < 6.0 and w > 40 and h > 15:
                    # Add padding
                    pad_w = int(w * 0.15)
                    pad_h = int(h * 0.15)
                    y1 = max(0, y - pad_h)
                    y2 = min(vehicle_crop.shape[0], y + h + pad_h)
                    x1 = max(0, x - pad_w)
                    x2 = min(vehicle_crop.shape[1], x + w + pad_w)
                    regions.append(vehicle_crop[y1:y2, x1:x2])
        return regions
