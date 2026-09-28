"""
Lightweight vehicle tracker using Ultralytics' built-in ByteTrack/BOT-SORT.

Wraps the existing VehicleDetector's YOLO model to produce tracked detections
with camera-local, session-local track IDs. Track IDs are temporary bookkeeping
for grouping consecutive-frame observations of the same physical vehicle - they
are NEVER persisted as vehicle identities.

Usage:
    tracker = VehicleTracker(conf_threshold=0.25)
    tracked = tracker.track(frame)
    for tv in tracked:
        print(tv.track_id, tv.label, tv.confidence)

Falls back to per-frame detection (track_id=-1) if tracking fails for any
reason, so the pipeline never crashes because the tracker broke.
"""
from dataclasses import dataclass

from ultralytics import YOLO

# Same labels as the plain detector, including the "truck" -> "auto"
# rickshaw reclassification, so tracked and untracked paths never disagree.
from detector import VEHICLE_CLASS_IDS, _reclassify_auto_rickshaw

# Configurable tracking parameters
TRACK_MAX_AGE_FRAMES = 30       # frames before a lost track is deleted
TRACK_MAX_AGE_SECONDS = 5.0     # seconds before a lost track is deleted
TRACK_CONF_THRESHOLD = 0.25     # minimum confidence for tracking


@dataclass
class TrackedVehicle:
    """A single tracked vehicle detection in one frame."""
    track_id: int           # camera-local, session-local - NOT a persistent identity
    x1: int
    y1: int
    x2: int
    y2: int
    label: str              # "car", "motorcycle", "bus", "truck", "auto"
    confidence: float


class VehicleTracker:
    """
    Uses Ultralytics model.track() for multi-object tracking with ByteTrack.

    Each call to track() feeds the next frame and returns all currently-tracked
    vehicles with their assigned track IDs.  The tracker maintains internal
    state across calls - call reset() when switching cameras or after a
    reconnect to clear stale state.
    """

    def __init__(
        self,
        model_name: str = "yolov8s.pt",
        conf_threshold: float = TRACK_CONF_THRESHOLD,
        max_age: int = TRACK_MAX_AGE_FRAMES,
    ):
        self.model = YOLO(model_name)
        self.conf_threshold = conf_threshold
        self.max_age = max_age
        # Vehicle class IDs for COCO filtering
        self._vehicle_classes = list(VEHICLE_CLASS_IDS.keys())
        # Fallback IoU-based tracking when ByteTrack is unavailable
        self._fallback_mode = False
        self._fallback_next_id = 1
        self._fallback_tracks: list[dict] = []  # [{id, bbox, age}]
        self._FALLBACK_IOU_THRESH = 0.25
        self._FALLBACK_MAX_AGE = 30

    def track(self, frame) -> list[TrackedVehicle]:
        """
        Feed a frame and return tracked vehicle detections.

        Falls back to IoU-based tracking if ByteTrack is unavailable
        (e.g. missing lapx dependency).
        """
        if not self._fallback_mode:
            try:
                results = self.model.track(
                    frame,
                    verbose=False,
                    conf=self.conf_threshold,
                    classes=self._vehicle_classes,
                    persist=True,           # maintain tracks across frames
                    tracker="bytetrack.yaml",
                    imgsz=480,
                )
                return self._parse_tracked_results(results)
            except Exception as e:
                import logging
                logging.getLogger("netra.tracker").warning(
                    "ByteTrack unavailable (%s), using fallback IoU tracker", e
                )
                self._fallback_mode = True

        # Fallback: detect + simple IoU matching
        try:
            results = self.model.predict(
                frame,
                verbose=False,
                conf=self.conf_threshold,
                classes=self._vehicle_classes,
                imgsz=480,
            )
            detections = self._parse_untracked_results(results)
            return self._fallback_iou_track(detections)
        except Exception:
            return []

    def _fallback_iou_track(self, detections: list[TrackedVehicle]) -> list[TrackedVehicle]:
        """Simple IoU-based tracking when ByteTrack is not available."""
        det_boxes = [(d.x1, d.y1, d.x2, d.y2) for d in detections]
        matched_tracks = set()
        matched_dets = set()
        results = []

        # Match existing tracks to new detections
        pairs = []
        for t_idx, trk in enumerate(self._fallback_tracks):
            for d_idx, dbox in enumerate(det_boxes):
                iou_val = self._iou(trk["bbox"], dbox)
                if iou_val >= self._FALLBACK_IOU_THRESH:
                    pairs.append((iou_val, t_idx, d_idx))

        pairs.sort(key=lambda p: p[0], reverse=True)
        for _, t_idx, d_idx in pairs:
            if t_idx in matched_tracks or d_idx in matched_dets:
                continue
            matched_tracks.add(t_idx)
            matched_dets.add(d_idx)
            d = detections[d_idx]
            trk = self._fallback_tracks[t_idx]
            trk["bbox"] = (d.x1, d.y1, d.x2, d.y2)
            trk["age"] = 0
            results.append(TrackedVehicle(
                track_id=trk["id"],
                x1=d.x1, y1=d.y1, x2=d.x2, y2=d.y2,
                label=d.label, confidence=d.confidence,
            ))

        # Create new tracks for unmatched detections
        for d_idx, d in enumerate(detections):
            if d_idx not in matched_dets:
                new_id = self._fallback_next_id
                self._fallback_next_id += 1
                self._fallback_tracks.append({
                    "id": new_id,
                    "bbox": (d.x1, d.y1, d.x2, d.y2),
                    "age": 0,
                })
                results.append(TrackedVehicle(
                    track_id=new_id,
                    x1=d.x1, y1=d.y1, x2=d.x2, y2=d.y2,
                    label=d.label, confidence=d.confidence,
                ))

        # Age unmatched tracks and prune
        surviving = []
        for t_idx, trk in enumerate(self._fallback_tracks):
            if t_idx not in matched_tracks:
                trk["age"] += 1
            if trk["age"] <= self._FALLBACK_MAX_AGE:
                surviving.append(trk)
        self._fallback_tracks = surviving

        return results

    @staticmethod
    def _iou(box_a, box_b) -> float:
        """IoU between two (x1, y1, x2, y2) boxes."""
        x1 = max(box_a[0], box_b[0])
        y1 = max(box_a[1], box_b[1])
        x2 = min(box_a[2], box_b[2])
        y2 = min(box_a[3], box_b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter == 0:
            return 0.0
        area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
        area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
        return inter / (area_a + area_b - inter)

    def _parse_tracked_results(self, results) -> list[TrackedVehicle]:
        """Parse results from model.track() - boxes have .id for track IDs."""
        vehicles = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in VEHICLE_CLASS_IDS:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                # track ID may be None if tracker hasn't assigned one yet
                track_id = int(box.id[0]) if box.id is not None else -1

                vehicles.append(TrackedVehicle(
                    track_id=track_id,
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    label=_reclassify_auto_rickshaw(VEHICLE_CLASS_IDS[cls_id], x1, y1, x2, y2),
                    confidence=float(box.conf[0]),
                ))
        return vehicles

    def _parse_untracked_results(self, results) -> list[TrackedVehicle]:
        """Parse results from model.predict() - no track IDs available."""
        vehicles = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in VEHICLE_CLASS_IDS:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                vehicles.append(TrackedVehicle(
                    track_id=-1,
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    label=_reclassify_auto_rickshaw(VEHICLE_CLASS_IDS[cls_id], x1, y1, x2, y2),
                    confidence=float(box.conf[0]),
                ))
        return vehicles

    def reset(self):
        """Clear tracker state - call after camera reconnect or scene cut."""
        self.model.predictor = None
        self._fallback_mode = False
        self._fallback_tracks.clear()
        self._fallback_next_id = 1

