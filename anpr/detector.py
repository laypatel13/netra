"""
Vehicle detector — thin wrapper around a pretrained YOLOv8 (COCO) model.

COCO has no "license plate" class, so this only localizes vehicles
(car/motorcycle/bus/truck). Plate text itself is found by running OCR's own
text-detection over the vehicle crop — see plate_reader.py. That combination
(generic vehicle detector + OCR's built-in text detector) avoids needing a
custom-trained plate-detection model, which isn't feasible on an 11-day
hackathon timeline.

Running OCR on the full frame instead of a vehicle crop mostly finds noise
(signage, shopfronts, banners) — cropping to vehicles first is what keeps
false positives down before the expensive OCR step even runs.
"""
from dataclasses import dataclass

from ultralytics import YOLO

# COCO class ids for the vehicle types we care about.
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


@dataclass
class VehicleBox:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    confidence: float


class VehicleDetector:
    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.4):
        # First run downloads weights from ultralytics' GitHub releases —
        # needs network access to github.com / release-assets.githubusercontent.com.
        self.model = YOLO(model_name)
        self.conf_threshold = conf_threshold

    def detect(self, frame) -> list[VehicleBox]:
        """No fixed input shape assumed — ultralytics handles per-frame
        resizing internally, so mixed resolutions across cameras (PLAN.md
        Section 8) don't need special-casing here."""
        results = self.model.predict(frame, verbose=False, conf=self.conf_threshold)
        boxes = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in VEHICLE_CLASS_IDS:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                boxes.append(VehicleBox(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    label=VEHICLE_CLASS_IDS[cls_id],
                    confidence=float(box.conf[0]),
                ))
        return boxes
