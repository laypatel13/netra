"""
Vehicle detector - thin wrapper around pretrained YOLOv8 (COCO) models.

COCO has no "license plate" class, so this only localizes vehicles
(car/motorcycle/bus/truck). Plate text itself is found by running OCR's own
text-detection over the vehicle crop - see plate_reader.py. That combination
(generic vehicle detector + OCR's built-in text detector) avoids needing a
custom-trained plate-detection model, which isn't feasible on an 11-day
hackathon timeline.

Running OCR on the full frame instead of a vehicle crop mostly finds noise
(signage, shopfronts, banners) - cropping to vehicles first is what keeps
false positives down before the expensive OCR step even runs.

detect_rois() runs detection on motion regions only, for the motion-gated
path in multi_camera.py; full-frame sweeps use the larger default model.
"""
from dataclasses import dataclass
import numpy as np
from ultralytics import YOLO

# COCO class ids for the vehicle types we care about.
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Auto-rickshaws (three-wheelers) have no COCO class of their own, so the
# pretrained model has to force them into the closest fit among the classes
# above - and empirically that's "truck", not "car" or "motorcycle": the
# boxy rear canopy reads as a small truck bed to a model that's never seen a
# rickshaw. A real truck, viewed from the side/three-quarter CCTV angles
# this footage uses, is reliably much wider than it is tall; a compact
# auto-rickshaw's box is closer to square. That aspect ratio is a cheap,
# honest disambiguator - not a certainty, same spirit as the vehicle_color
# averaging in color.py, and the same fix applies: the saved thumbnail is
# what a human actually verifies a "truck" vs. "auto" call against.
AUTO_RICKSHAW_MAX_ASPECT_RATIO = 1.15


def _reclassify_auto_rickshaw(label: str, x1: int, y1: int, x2: int, y2: int) -> str:
    if label != "truck":
        return label
    width, height = x2 - x1, y2 - y1
    if height <= 0:
        return label
    return "auto" if (width / height) <= AUTO_RICKSHAW_MAX_ASPECT_RATIO else label


# Minimum ROI dimensions (pixels) - ROIs smaller than this are ignored
# because YOLO can't reliably detect vehicles at tiny scales.
MIN_ROI_DIM = 64


@dataclass
class VehicleBox:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    confidence: float


class VehicleDetector:
    def __init__(self, model_name: str = "yolov8s.pt", conf_threshold: float = 0.4):
        # First run downloads weights from ultralytics' GitHub releases -
        # needs network access to github.com / release-assets.githubusercontent.com.
        self.model = YOLO(model_name)
        self.conf_threshold = conf_threshold

    def detect(self, frame) -> list[VehicleBox]:
        """
        Full-frame detection.  No fixed input shape assumed - ultralytics
        handles per-frame resizing internally, so mixed resolutions across
        cameras (PLAN.md Section 8) don't need special-casing here.
        """
        results = self.model.predict(frame, verbose=False, conf=self.conf_threshold, imgsz=480)
        return self._parse_results(results)

    def detect_rois(
        self,
        frame: np.ndarray,
        rois: list,
        imgsz: int = 320,
    ) -> list[VehicleBox]:
        """
        Run detection only on specific regions of interest (motion ROIs).

        Each ROI is cropped from the frame, run through YOLO independently,
        and the resulting bounding boxes are mapped back to full-frame
        coordinates.  This is much cheaper than running YOLO on the full
        frame when only a small portion has motion.

        Args:
            frame: Full BGR frame.
            rois: List of MotionROI objects (or anything with x1/y1/x2/y2
                  attributes or to_tuple() returning (x1, y1, x2, y2)).
            imgsz: YOLO input size for ROI crops (smaller = faster).

        Returns:
            list[VehicleBox]: Detections in full-frame coordinates.
        """
        if not rois:
            return []

        h_frame, w_frame = frame.shape[:2]
        all_detections: list[VehicleBox] = []

        for roi in rois:
            # Extract ROI coordinates
            if hasattr(roi, 'x1'):
                rx1, ry1, rx2, ry2 = roi.x1, roi.y1, roi.x2, roi.y2
            elif hasattr(roi, 'to_tuple'):
                rx1, ry1, rx2, ry2 = roi.to_tuple()
            else:
                rx1, ry1, rx2, ry2 = roi  # plain tuple

            # Clamp to frame bounds
            rx1 = max(0, rx1)
            ry1 = max(0, ry1)
            rx2 = min(w_frame, rx2)
            ry2 = min(h_frame, ry2)

            # Skip tiny ROIs
            roi_w = rx2 - rx1
            roi_h = ry2 - ry1
            if roi_w < MIN_ROI_DIM or roi_h < MIN_ROI_DIM:
                continue

            # Crop the ROI
            crop = frame[ry1:ry2, rx1:rx2]
            if crop.size == 0:
                continue

            # Run YOLO on the crop
            results = self.model.predict(
                crop,
                verbose=False,
                conf=self.conf_threshold,
                imgsz=imgsz,
            )

            # Map detections back to full-frame coordinates
            for box_data in self._parse_results(results):
                all_detections.append(VehicleBox(
                    x1=box_data.x1 + rx1,
                    y1=box_data.y1 + ry1,
                    x2=box_data.x2 + rx1,
                    y2=box_data.y2 + ry1,
                    label=box_data.label,
                    confidence=box_data.confidence,
                ))

        return all_detections

    def _parse_results(self, results) -> list[VehicleBox]:
        """Parse YOLO results into VehicleBox list."""
        boxes = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in VEHICLE_CLASS_IDS:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                label = _reclassify_auto_rickshaw(VEHICLE_CLASS_IDS[cls_id], x1, y1, x2, y2)
                boxes.append(VehicleBox(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    label=label,
                    confidence=float(box.conf[0]),
                ))
        return boxes
