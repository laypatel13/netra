"""
Multi-camera investigation orchestrator.

Coordinates concurrent camera capture, shared YOLO inference, camera-specific
tracking, and the investigation pipeline across all eligible CCTV feeds.

Architecture:

    Camera capture workers (1 thread per camera)
            ↓  put (camera_id, frame, pts_ms, grab_index) into slot
    Per-camera frame slots (newest-frame-wins, size=1 each)
            ↓  inference worker polls round-robin
    YOLO inference worker(s) (default: 1, configurable)
            ↓  returns detections list
    Camera-specific IOUTracker  (one per camera - NEVER shared)
            ↓  tracked vehicles
    InvestigationPipeline  (per camera, but targets are global)
            ↓
    Backend  /investigations/ingest

IMPORTANT:
    - Only the inference worker(s) load the YOLO model.
    - Trackers are camera-local.  track_id is meaningful only within
      (camera_id, pipeline_session_id).
    - Evidence buffers are keyed by (camera_id, track_id).
    - A single investigation target is evaluated against ALL cameras.
    - One camera failing does NOT stop others.
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from color import dominant_color
from detector import VehicleDetector, VehicleBox
from camera_manager import ConnectionState
from frame_sampler import FrameSampler
from motion_gate import MotionGate

log = logging.getLogger("netra.multicam")

# ---------------------------------------------------------------------------
# Per-camera health/stats
# ---------------------------------------------------------------------------

@dataclass
class CameraHealth:
    """
    Mutable stats for a single camera worker - written by one thread only.

    Tracks connection state, dropped frames,
    reconnect count, last frame times, and current error.
    """
    camera_id: str
    status: str = "offline"  # online / offline / reconnecting / degraded
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    frames_read: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    vehicles_detected: int = 0
    tracks_active: int = 0
    matches: int = 0
    unknown: int = 0
    last_frame_ts: float = 0.0
    last_frame_received: float = 0.0
    last_frame_processed: float = 0.0
    reconnect_count: int = 0
    current_error: Optional[str] = None
    worker_alive: bool = False
    # Motion gate stats
    frames_skipped_no_motion: int = 0
    frames_motion_roi: int = 0
    frames_full_detect: int = 0


# ---------------------------------------------------------------------------
# Global pipeline stats (aggregated across all cameras)
# ---------------------------------------------------------------------------

@dataclass
class GlobalStats:
    """Thread-safe-enough aggregates (int increments are atomic on CPython)."""
    cameras_configured: int = 0
    cameras_connected: int = 0
    cameras_active: int = 0
    frames_read: int = 0
    frames_processed: int = 0
    vehicles_detected: int = 0
    tracks_active: int = 0
    active_targets: int = 0
    candidate_count: int = 0
    ocr_attempts: int = 0
    ocr_success: int = 0
    api_posts: int = 0
    api_failures: int = 0
    target_evaluations: int = 0
    matches: int = 0
    evidence_buffers: int = 0
    tracks_created: int = 0
    # Motion gate stats
    frames_skipped_no_motion: int = 0
    frames_motion_roi: int = 0
    frames_full_detect: int = 0

    @property
    def motion_gate_savings_pct(self) -> float:
        total = self.frames_skipped_no_motion + self.frames_motion_roi + self.frames_full_detect
        if total == 0:
            return 0.0
        return (self.frames_skipped_no_motion / total) * 100.0

    def to_dict(self) -> dict:
        return {
            "cameras_configured": self.cameras_configured,
            "cameras_connected": self.cameras_connected,
            "cameras_active": self.cameras_active,
            "frames_read": self.frames_read,
            "frames_processed": self.frames_processed,
            "vehicles_detected": self.vehicles_detected,
            "tracks_active": self.tracks_active,
            "active_targets": self.active_targets,
            "candidate_count": self.candidate_count,
            "ocr_attempts": self.ocr_attempts,
            "ocr_success": self.ocr_success,
            "api_posts": self.api_posts,
            "api_failures": self.api_failures,
            "target_evaluations": self.target_evaluations,
            "matches": self.matches,
            "evidence_buffers": self.evidence_buffers,
            "tracks_created": self.tracks_created,
            "frames_skipped_no_motion": self.frames_skipped_no_motion,
            "frames_motion_roi": self.frames_motion_roi,
            "frames_full_detect": self.frames_full_detect,
            "motion_gate_savings_pct": round(self.motion_gate_savings_pct, 1),
        }

    def as_legacy_counters(self) -> dict:
        """Return a dict matching the old debug_counters shape for backward compat."""
        return self.to_dict()


# ---------------------------------------------------------------------------
# Simple IoU-based tracker (no YOLO - accepts pre-computed detections)
# ---------------------------------------------------------------------------

@dataclass
class _TrackedObject:
    track_id: int
    bbox: tuple  # (x1, y1, x2, y2)
    label: str
    confidence: float
    age: int = 0          # frames since last update
    total_seen: int = 1   # total frames this track has been observed


@dataclass
class TrackedDetection:
    """Output of the SimpleIOUTracker - same interface as tracker.TrackedVehicle."""
    track_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    confidence: float


def _iou(box_a, box_b) -> float:
    """Compute intersection-over-union between two (x1, y1, x2, y2) boxes."""
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


class SimpleIOUTracker:
    """
    Lightweight IoU-based multi-object tracker.

    Accepts pre-computed VehicleBox detections (from the shared inference
    worker) and assigns camera-local track IDs using greedy IoU matching.

    This replaces VehicleTracker for the multi-camera architecture so that
    only the inference worker needs a YOLO model instance.

    Thread-safety: NOT thread-safe - each camera thread owns its own instance.
    """

    IOU_THRESHOLD = 0.25
    MAX_AGE = 30  # frames before dropping a lost track

    def __init__(self):
        self._tracks: list[_TrackedObject] = []
        self._next_id: int = 1

    def update(self, detections: list[VehicleBox]) -> list[TrackedDetection]:
        """
        Match new detections against existing tracks and return tracked results.

        Uses greedy IoU matching: for each detection, find the best-matching
        existing track.  Unmatched detections start new tracks.  Tracks not
        matched for MAX_AGE frames are dropped.
        """
        det_boxes = [(d.x1, d.y1, d.x2, d.y2) for d in detections]
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        assignments: list[tuple[int, int]] = []  # (track_idx, det_idx)

        # Greedy matching - compute all IoU pairs, assign best first
        pairs = []
        for t_idx, trk in enumerate(self._tracks):
            for d_idx, dbox in enumerate(det_boxes):
                iou_val = _iou(trk.bbox, dbox)
                if iou_val >= self.IOU_THRESHOLD:
                    pairs.append((iou_val, t_idx, d_idx))

        pairs.sort(key=lambda p: p[0], reverse=True)
        for _, t_idx, d_idx in pairs:
            if t_idx in matched_tracks or d_idx in matched_dets:
                continue
            matched_tracks.add(t_idx)
            matched_dets.add(d_idx)
            assignments.append((t_idx, d_idx))

        # Update matched tracks
        for t_idx, d_idx in assignments:
            d = detections[d_idx]
            self._tracks[t_idx].bbox = (d.x1, d.y1, d.x2, d.y2)
            self._tracks[t_idx].label = d.label
            self._tracks[t_idx].confidence = d.confidence
            self._tracks[t_idx].age = 0
            self._tracks[t_idx].total_seen += 1

        # Create new tracks for unmatched detections
        for d_idx, d in enumerate(detections):
            if d_idx not in matched_dets:
                new_trk = _TrackedObject(
                    track_id=self._next_id,
                    bbox=(d.x1, d.y1, d.x2, d.y2),
                    label=d.label,
                    confidence=d.confidence,
                )
                self._tracks.append(new_trk)
                self._next_id += 1

        # Age unmatched tracks and prune
        surviving = []
        for t_idx, trk in enumerate(self._tracks):
            if t_idx not in matched_tracks:
                trk.age += 1
            if trk.age <= self.MAX_AGE:
                surviving.append(trk)
        self._tracks = surviving

        # Build output - return only tracks that were seen this frame
        results = []
        # Re-check via track_id since list indices may have shifted
        seen_ids = set()
        for t_idx, d_idx in assignments:
            trk = self._tracks[t_idx] if t_idx < len(self._tracks) else None
            if trk:
                seen_ids.add(trk.track_id)

        for trk in self._tracks:
            if trk.age == 0:  # seen this frame
                results.append(TrackedDetection(
                    track_id=trk.track_id,
                    x1=trk.bbox[0], y1=trk.bbox[1],
                    x2=trk.bbox[2], y2=trk.bbox[3],
                    label=trk.label,
                    confidence=trk.confidence,
                ))

        # Also include newly created tracks
        for d_idx, d in enumerate(detections):
            if d_idx not in matched_dets:
                # Find the track we just created for it
                for trk in self._tracks:
                    if (trk.bbox == (d.x1, d.y1, d.x2, d.y2)
                            and trk.age == 0
                            and trk.track_id not in {r.track_id for r in results}):
                        results.append(TrackedDetection(
                            track_id=trk.track_id,
                            x1=d.x1, y1=d.y1, x2=d.x2, y2=d.y2,
                            label=d.label,
                            confidence=d.confidence,
                        ))
                        break

        return results

    def reset(self):
        """Clear all tracks - call after camera reconnect or scene cut."""
        self._tracks.clear()
        self._next_id = 1


# ---------------------------------------------------------------------------
# Inference Queue - fair, bounded, newest-frame-wins per camera
# ---------------------------------------------------------------------------

@dataclass
class FrameItem:
    """A frame waiting for inference."""
    camera_id: str
    frame: np.ndarray
    pts_ms: float
    grab_index: int
    loop_count: int = 0
    timestamp: float = field(default_factory=time.time)   # legacy
    ingested_at: float = field(default_factory=time.time)  # wall-clock when grabbed


class InferenceQueue:
    """
    Thread-safe bounded queue with per-camera newest-frame-wins semantics.

    Each camera gets exactly one slot.  When a camera submits a new frame
    before the old one was consumed, the old one is replaced.  The inference
    worker reads cameras round-robin so no single camera monopolizes GPU time.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._slots: dict[str, Optional[FrameItem]] = {}
        self._camera_order: list[str] = []
        self._round_robin_idx: int = 0
        self._event = threading.Event()  # signaled when any slot has a frame

    def register_camera(self, camera_id: str):
        with self._lock:
            if camera_id not in self._slots:
                self._slots[camera_id] = None
                self._camera_order.append(camera_id)

    def put(self, item: FrameItem):
        """Submit a frame - replaces any existing frame for this camera."""
        with self._lock:
            self._slots[item.camera_id] = item
        self._event.set()

    def get(self, timeout: float = 1.0) -> Optional[FrameItem]:
        """
        Get the next frame to process, round-robin across cameras.

        Returns None on timeout.
        """
        if not self._event.wait(timeout=timeout):
            return None

        with self._lock:
            if not self._camera_order:
                self._event.clear()
                return None

            n = len(self._camera_order)
            for _ in range(n):
                idx = self._round_robin_idx % n
                cam_id = self._camera_order[idx]
                self._round_robin_idx = (idx + 1) % n
                item = self._slots.get(cam_id)
                if item is not None:
                    self._slots[cam_id] = None
                    # Check if any slots still have frames
                    if not any(v is not None for v in self._slots.values()):
                        self._event.clear()
                    return item

            self._event.clear()
            return None

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for v in self._slots.values() if v is not None)


# ---------------------------------------------------------------------------
# Inference Worker - owns YOLO models + per-camera motion gates
# ---------------------------------------------------------------------------

class InferenceWorker(threading.Thread):
    """
    Dedicated thread that owns the YOLO model(s) and processes frames from
    the InferenceQueue.

    Now includes per-camera MotionGate instances.  The flow is:
      1. Run motion detection (CPU-only, ~0.5ms)
      2. If no motion → skip YOLO entirely, dispatch empty detections
      3. If motion with ROIs → run yolov8n on ROI crops only
      4. If full-frame needed → run yolov8s on entire frame

    After detecting vehicles in a frame, it dispatches the detections to
    the appropriate camera's result callback.
    """

    def __init__(
        self,
        inference_queue: InferenceQueue,
        result_callbacks: dict,  # camera_id → callable(detections, frame_item, skipped)
        model_name: str = "yolov8s.pt",
        roi_model_name: str = "yolov8n.pt",
        conf_threshold: float = 0.25,
        worker_id: int = 0,
        global_stats: Optional[GlobalStats] = None,
    ):
        super().__init__(name=f"inference-{worker_id}", daemon=True)
        self.queue = inference_queue
        self.callbacks = result_callbacks
        self.model_name = model_name
        self.roi_model_name = roi_model_name
        self.conf_threshold = conf_threshold
        self._stop_event = threading.Event()
        self.global_stats = global_stats

        # Per-camera motion gates (created on first frame from each camera)
        self._motion_gates: dict[str, MotionGate] = {}

    def _get_motion_gate(self, camera_id: str) -> MotionGate:
        """Get or create the motion gate for a camera."""
        if camera_id not in self._motion_gates:
            self._motion_gates[camera_id] = MotionGate()
            log.info("[InferenceWorker-%s] Created MotionGate for camera %s",
                     self.name, camera_id)
        return self._motion_gates[camera_id]

    def run(self):
        log.info("[InferenceWorker-%s] Loading YOLO models...", self.name)
        log.info("[InferenceWorker-%s]   Full-frame model: %s", self.name, self.model_name)
        log.info("[InferenceWorker-%s]   ROI model:        %s", self.name, self.roi_model_name)
        detector_full = VehicleDetector(
            model_name=self.model_name,
            conf_threshold=self.conf_threshold,
        )
        detector_roi = VehicleDetector(
            model_name=self.roi_model_name,
            conf_threshold=self.conf_threshold,
        )
        log.info("[InferenceWorker-%s] Models loaded, processing frames.", self.name)

        while not self._stop_event.is_set():
            item = self.queue.get(timeout=0.5)
            if item is None:
                continue

            try:
                gate = self._get_motion_gate(item.camera_id)
                motion = gate.process(item.frame)

                callback = self.callbacks.get(item.camera_id)
                if not callback:
                    continue

                if not motion.has_motion:
                    # No motion - skip YOLO entirely
                    callback([], item, True)  # skipped=True
                    if self.global_stats:
                        self.global_stats.frames_skipped_no_motion += 1
                    continue

                if motion.use_full_frame or not motion.motion_rois:
                    # Full-frame detection (periodic sweep or warmup)
                    detections = detector_full.detect(item.frame)
                    if self.global_stats:
                        self.global_stats.frames_full_detect += 1
                else:
                    # ROI-only detection with the lightweight nano model
                    detections = detector_roi.detect_rois(
                        item.frame,
                        motion.motion_rois,
                        imgsz=320,
                    )
                    if self.global_stats:
                        self.global_stats.frames_motion_roi += 1

                callback(detections, item, False)  # skipped=False

            except Exception as e:
                log.error("[InferenceWorker] Error processing frame from %s: %s",
                          item.camera_id, e, exc_info=True)

    def reset_motion_gate(self, camera_id: str):
        """Reset motion gate for a camera - call after reconnect/scene cut."""
        if camera_id in self._motion_gates:
            self._motion_gates[camera_id].reset()
            log.info("[InferenceWorker] Reset MotionGate for camera %s", camera_id)

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Camera Worker - captures frames and submits to inference queue
# ---------------------------------------------------------------------------

WARMUP_FRAMES = 15

class CameraWorker(threading.Thread):
    """
    One per camera.  Captures frames, applies per-camera throttling,
    submits to the shared inference queue.

    After receiving detections back (via the result callback), runs
    camera-local tracking → investigation pipeline.
    """

    def __init__(
        self,
        camera: dict,
        backend_url: str,
        inference_queue: InferenceQueue,
        health: CameraHealth,
        global_stats: GlobalStats,
        process_every_n: int = 5,
        max_frames: Optional[int] = None,
        backoff_cfg: Optional[dict] = None,
        plate_reader=None,
        investigation_debug: bool = False,
    ):
        cam_id = camera["camera_id"]
        super().__init__(name=f"cam-{cam_id}", daemon=True)

        self.camera = camera
        self.camera_id = cam_id
        self.backend_url = backend_url
        self.inference_queue = inference_queue
        self.health = health
        self.gstats = global_stats
        self.process_every_n = process_every_n
        self.max_frames = max_frames
        self.backoff_cfg = backoff_cfg or {"initial_ms": 2000, "multiplier": 2, "max_ms": 30000}
        self.investigation_debug = investigation_debug

        self._stop_event = threading.Event()
        self._result_lock = threading.Lock()
        self._pending_results: list[tuple] = []  # [(detections, frame_item), ...]

        # Camera-local tracker and investigation components (created on first use)
        self.trackers = {}
        self.buffer_managers = {}
        self.tracker: Optional[SimpleIOUTracker] = None
        self.inv_pipeline = None
        self.buffer_mgr = None
        self.plate_reader = plate_reader
        self.loop_count = 0

        # Per-camera debug counters (legacy format for InvestigationPipeline compat)
        self._counters: dict = {
            "cameras_active": 1,
            "frames_read": 0,
            "frames_processed": 0,
            "vehicles_detected": 0,
            "tracks_created": 0,
            "active_targets": 0,
            "target_evaluations": 0,
            "matches": 0,
            "unknown_candidates": 0,
            "evidence_buffers": 0,
            "ocr_attempts": 0,
            "ocr_success": 0,
            "candidates_created": 0,
            "api_posts": 0,
            "api_failures": 0,
        }

    def on_detections(self, detections: list[VehicleBox], frame_item: FrameItem, skipped: bool = False):
        """Called by the InferenceWorker when detections are ready for this camera."""
        with self._result_lock:
            self._pending_results.append((detections, frame_item, skipped))

    def _init_investigation(self):
        """Lazily create tracker, buffer manager, investigation pipeline."""
        if self.tracker is not None:
            return

        from evidence_buffer import BufferManager
        from investigation_pipeline import InvestigationPipeline

        self.tracker = SimpleIOUTracker()
        self.buffer_mgr = BufferManager(self.camera_id)
        # Create the unified investigation pipeline for this camera
        # Pass the global session ID so all cameras share the same chronological context
        self.inv_pipeline = InvestigationPipeline(
            backend_url=self.backend_url,
            camera_id=self.camera_id,
            plate_reader=self.plate_reader,
            session_id=self.session_id,
            debug_counters=self._counters,
        )
        self.inv_pipeline.sync_targets()

    def run(self):
        """Main camera loop: capture → throttle → submit to inference queue."""
        self._init_investigation()
        self.health.worker_alive = True
        sampler = FrameSampler(process_every_n=self.process_every_n)
        backoff = self.backoff_cfg["initial_ms"] / 1000.0
        frames_processed = 0

        try:
            while not self._stop_event.is_set() and (self.max_frames is None or frames_processed < self.max_frames):
                self.health.connection_state = ConnectionState.CONNECTING
                cap = self._open_capture()
                if cap is None:
                    if self.camera.get("local_path"):
                        log.info("[%s] Video file ended or failed to open", self.camera_id)
                        break
                    self.health.status = "reconnecting"
                    self.health.connection_state = ConnectionState.RECONNECTING
                    self.health.reconnect_count += 1
                    self.health.current_error = "Stream unreachable"
                    log.warning("[%s] RECONNECTING attempt=%d - retrying in %.0fs",
                                self.camera_id, self.health.reconnect_count, backoff)
                    # Sleep in short increments so we can respond to stop events
                    sleep_until = time.time() + backoff
                    while time.time() < sleep_until and not self._stop_event.is_set():
                        time.sleep(0.5)
                    backoff = min(backoff * self.backoff_cfg["multiplier"],
                                  self.backoff_cfg["max_ms"] / 1000.0)
                    continue

                # Connected successfully
                backoff = self.backoff_cfg["initial_ms"] / 1000.0
                self.health.status = "online"
                self.health.connection_state = ConnectionState.CONNECTED
                self.health.current_error = None
                self.gstats.cameras_connected += 1
                sampler.reset()
                log.info("[%s] CONNECTED", self.camera_id)
                
                pts_state = "UNKNOWN_PTS"
                last_pts_ms = -1.0
                frozen_count = 0
                zero_pts_count = 0

                while not self._stop_event.is_set() and (self.max_frames is None or frames_processed < self.max_frames):
                    ok = cap.grab()
                    if not ok:
                        if self.camera.get("local_path"):
                            log.info("[%s] Video file finished (%d frames processed)",
                                     self.camera_id, frames_processed)
                            # Process remaining investigation buffers
                            if self.buffer_mgr and self.inv_pipeline:
                                for b in list(self.buffer_mgr._buffers.values()):
                                    if b.frame_count > 0:
                                        self.inv_pipeline.process_expired_buffer(b)
                                self.buffer_mgr._buffers.clear()
                            if self.inv_pipeline:
                                self.inv_pipeline.send_heartbeat()
                            # Loop video
                            log.info("[%s] Looping video file", self.camera_id)
                            break
                        log.warning("[%s] FRAME_TIMEOUT - reconnecting", self.camera_id)
                        self.health.status = "reconnecting"
                        self.health.connection_state = ConnectionState.RECONNECTING
                        self.health.current_error = "Frame read failed"
                        break
                        
                    pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    
                    if pts_state != "UNAVAILABLE_PTS":
                        if pts_ms <= 0.0:
                            zero_pts_count += 1
                            if zero_pts_count > 10:
                                pts_state = "UNAVAILABLE_PTS"
                                log.warning("[%s] PTS unavailable/invalid (stuck at <=0). Using frame-flow for liveness.", self.camera_id)
                        else:
                            pts_state = "VALID_PTS"
                            zero_pts_count = 0
                            
                        if pts_state == "VALID_PTS":
                            if pts_ms == last_pts_ms:
                                frozen_count += 1
                                if frozen_count > 30: # 30 consecutive identical PTS usually means stream is frozen
                                    log.warning("[%s] FROZEN_STREAM detected (PTS stuck at %.1f) - reconnecting", self.camera_id, pts_ms)
                                    self.health.status = "reconnecting"
                                    self.health.connection_state = ConnectionState.RECONNECTING
                                    self.health.current_error = "Frozen stream (stuck PTS)"
                                    break
                            else:
                                frozen_count = 0
                            
                            # Detect loop: If PTS jumps backwards by more than 5 seconds
                            if last_pts_ms > 0 and pts_ms < last_pts_ms - 5000:
                                self.loop_count += 1
                                log.warning("[%s] PLAYBACK_LOOP detected! PTS jumped backwards from %.1f to %.1f. Loop count: %d", 
                                            self.camera_id, last_pts_ms, pts_ms, self.loop_count)

                            last_pts_ms = pts_ms

                    self.health.frames_read += 1
                    self.health.last_frame_received = time.time()
                    self.gstats.frames_read += 1

                    if not sampler.tick():
                        self.health.frames_dropped += 1
                        continue

                    ok, frame = cap.retrieve()
                    if not ok:
                        continue

                    pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    now = time.time()
                    frames_processed += 1
                    self.health.frames_processed += 1
                    self.health.last_frame_processed = now
                    self.gstats.frames_processed += 1

                    # Submit frame to shared inference queue
                    item = FrameItem(
                        camera_id=self.camera_id,
                        frame=frame,
                        pts_ms=pts_ms,
                        grab_index=sampler.frame_count,
                        loop_count=self.loop_count,
                        ingested_at=now,
                    )
                    self.inference_queue.put(item)

                    # Process any pending detection results from the inference worker
                    self._process_pending_results()

                    # Periodic investigation housekeeping
                    if self.inv_pipeline:
                        self.inv_pipeline.sync_targets()
                        self.inv_pipeline.send_heartbeat()
                        self._counters["cameras_active"] = self.gstats.cameras_connected

                cap.release()

        except Exception as e:
            log.error("[%s] Worker crashed: %s", self.camera_id, e, exc_info=True)
            self.health.connection_state = ConnectionState.ERROR
            self.health.current_error = str(e)
        finally:
            # Mark offline and release resources
            self.health.status = "offline"
            self.health.connection_state = ConnectionState.STOPPED
            self.health.worker_alive = False
            log.info("[%s] STOPPED", self.camera_id)

    def _process_pending_results(self):
        """Process detection results that the inference worker dispatched to us."""
        from evidence_buffer import Observation
        from quality import compute_quality

        with self._result_lock:
            results = list(self._pending_results)
            self._pending_results.clear()

        for detections, frame_item, skipped in results:
            # If frame was skipped by motion gate, still do housekeeping
            if skipped:
                self.health.frames_skipped_no_motion += 1
                # Still expire stale track buffers so they don't accumulate
                if self.buffer_mgr and self.inv_pipeline:
                    expired = self.buffer_mgr.cleanup_expired()
                    for b in expired:
                        self.inv_pipeline.process_expired_buffer(b)
                continue

            frame = frame_item.frame
            pts_ms = frame_item.pts_ms
            grab_index = frame_item.grab_index

            # Track detections
            tracked_vehicles = self.tracker.update(detections)
            self.health.tracks_active = len(self.tracker._tracks)
            self.gstats.tracks_active += 0  # just reading

            # Process expired track buffers
            if self.buffer_mgr and self.inv_pipeline:
                expired = self.buffer_mgr.cleanup_expired()
                for b in expired:
                    self.inv_pipeline.process_expired_buffer(b)

                # Process ready buffers (enough evidence, minimum age)
                for b in self.buffer_mgr.get_ready():
                    if time.time() - b.created_at > 1.0:
                        self.inv_pipeline.process_expired_buffer(b)
                        self.buffer_mgr.remove(b.track_id)

            for tv in tracked_vehicles:
                crop = frame[tv.y1:tv.y2, tv.x1:tv.x2]
                if crop.size == 0:
                    continue

                self.health.vehicles_detected += 1
                self.gstats.vehicles_detected += 1
                self._counters["vehicles_detected"] = self._counters.get("vehicles_detected", 0) + 1

                color = dominant_color(crop)

                if tv.track_id != -1 and self.buffer_mgr:
                    self._counters["tracks_created"] = len(self.buffer_mgr._buffers) if self.buffer_mgr else 0
                    self.health.tracks_active = self.buffer_mgr.active_count

                    track_buf = self.buffer_mgr.get_or_create(tv.track_id)
                    q = compute_quality(crop)

                    obs = Observation(
                        frame_index=grab_index,
                        pts_ms=pts_ms,
                        bbox=(tv.x1, tv.y1, tv.x2, tv.y2),
                        confidence=tv.confidence,
                        crop=crop.copy(),
                        quality=q,
                        vehicle_type=tv.label,
                        vehicle_color=color.color if color.usable else None,
                        color_unknown_reason=color.unknown_reason,
                        loop_count=frame_item.loop_count,
                    )
                    track_buf.add(obs)
                    self._counters["evidence_buffers"] = self.buffer_mgr.active_count
                    self.gstats.evidence_buffers = sum(1 for _ in [])  # updated below

            # Sync counters to global stats
            self.gstats.ocr_attempts = max(self.gstats.ocr_attempts, self._counters.get("ocr_attempts", 0))
            self.gstats.ocr_success = max(self.gstats.ocr_success, self._counters.get("ocr_success", 0))
            self.gstats.candidate_count = max(self.gstats.candidate_count, self._counters.get("candidates_created", 0))
            self.gstats.api_posts = max(self.gstats.api_posts, self._counters.get("api_posts", 0))
            self.gstats.api_failures = max(self.gstats.api_failures, self._counters.get("api_failures", 0))
            self.gstats.matches = max(self.gstats.matches, self._counters.get("matches", 0))
            self.gstats.active_targets = self._counters.get("active_targets", 0)
            self.gstats.target_evaluations = max(self.gstats.target_evaluations, self._counters.get("target_evaluations", 0))

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Open a video capture for this camera (local file or RTSP/HLS)."""
        local_path = self.camera.get("local_path")
        if local_path:
            cap = cv2.VideoCapture(local_path)
            if cap.isOpened():
                log.info("Camera %s connected via local file: %s", self.camera_id, local_path)
                return cap
            cap.release()
            log.error("Failed to open local video: %s", local_path)
            return None

        streams = self.camera.get("streams", {})
        for key in ("rtsp", "mp4", "hls"):
            url = streams.get(key)
            if not url:
                continue
            if key == "hls" and url.startswith("/"):
                url = f"{self.backend_url}{url}"
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            start = time.time()
            while not cap.isOpened() and (time.time() - start) < 8:
                time.sleep(0.5)
            if cap.isOpened():
                for _ in range(WARMUP_FRAMES):
                    cap.read()
                log.info("Camera %s connected via %s", self.camera_id, key)
                return cap
            cap.release()
        return None

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Multi-Camera Orchestrator
# ---------------------------------------------------------------------------

class MultiCameraOrchestrator:
    """
    Coordinates the entire multi-camera investigation pipeline.

    Usage:
        orch = MultiCameraOrchestrator(cameras, backend_url, ...)
        orch.start()
        orch.wait()   # blocks until all cameras finish or stop is called
    """

    def __init__(
        self,
        cameras: list[dict],
        backend_url: str,
        backoff_cfg: dict,
        process_every_n: int = 5,
        max_frames: Optional[int] = None,
        inference_workers: int = 1,
        conf_threshold: float = 0.25,
        investigation_debug: bool = False,
        plate_reader=None,
    ):
        self.cameras = cameras
        self.backend_url = backend_url
        self.backoff_cfg = backoff_cfg
        self.process_every_n = process_every_n
        self.max_frames = max_frames
        self.inference_workers_count = inference_workers
        self.conf_threshold = conf_threshold
        self.investigation_debug = investigation_debug
        self.plate_reader = plate_reader

        self.session_id = str(uuid.uuid4())

        # Shared state
        self.global_stats = GlobalStats(cameras_configured=len(cameras))
        self.camera_health: dict[str, CameraHealth] = {}
        self.inference_queue = InferenceQueue()
        self.camera_workers: list[CameraWorker] = []
        self.inference_worker_threads: list[InferenceWorker] = []
        self._stop_event = threading.Event()
        self._result_callbacks: dict = {}  # camera_id → callback

    def start(self):
        """Start all camera workers and inference workers."""
        log.info("=" * 60)
        log.info("MULTI-CAMERA INVESTIGATION MODE")
        log.info("=" * 60)
        log.info("  Cameras configured:  %d", len(self.cameras))
        log.info("  Inference workers:   %d", self.inference_workers_count)
        log.info("  Process every N:     %d", self.process_every_n)
        log.info("  Backend:             %s", self.backend_url)
        log.info("=" * 60)

        # Create camera workers
        for cam in self.cameras:
            cam_id = cam["camera_id"]
            health = CameraHealth(camera_id=cam_id)
            self.camera_health[cam_id] = health
            self.inference_queue.register_camera(cam_id)

            worker = CameraWorker(
                camera=cam,
                backend_url=self.backend_url,
                inference_queue=self.inference_queue,
                health=health,
                global_stats=self.global_stats,
                process_every_n=self.process_every_n,
                max_frames=self.max_frames,
                backoff_cfg=self.backoff_cfg,
                plate_reader=self.plate_reader,
            )
            worker.session_id = self.session_id
            self.camera_workers.append(worker)
            self._result_callbacks[cam_id] = worker.on_detections

            log.info("  Created worker for camera: %s", cam_id)

        # Start inference workers (with motion-gated two-tier YOLO)
        for i in range(self.inference_workers_count):
            iw = InferenceWorker(
                inference_queue=self.inference_queue,
                result_callbacks=self._result_callbacks,
                conf_threshold=self.conf_threshold,
                worker_id=i,
                global_stats=self.global_stats,
            )
            self.inference_worker_threads.append(iw)
            iw.start()

        # Start camera workers (staggered to avoid connection burst)
        for i, worker in enumerate(self.camera_workers):
            worker.start()
            if i < len(self.camera_workers) - 1:
                time.sleep(0.25)  # stagger connections

        log.info("All %d camera workers and %d inference workers started.",
                 len(self.camera_workers), self.inference_workers_count)

        # Start debug printer if requested
        if self.investigation_debug:
            self._debug_thread = threading.Thread(
                target=self._debug_printer, name="debug-printer", daemon=True
            )
            self._debug_thread.start()

        # Start heartbeat worker
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker, name="heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def wait(self):
        """Block until all camera workers finish."""
        try:
            for w in self.camera_workers:
                w.join()
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt - stopping all workers")
            self.stop()

    def stop(self):
        """Stop all workers."""
        self._stop_event.set()
        for w in self.camera_workers:
            w.stop()
        for iw in self.inference_worker_threads:
            iw.stop()

    def _heartbeat_worker(self):
        """Periodically send the global heartbeat payload to the backend."""
        import requests
        while not self._stop_event.is_set():
            try:
                payload = self.get_heartbeat_payload()
                requests.post(f"{self.backend_url}/investigations/heartbeat", json=payload, timeout=3.0)
            except Exception as e:
                log.error(f"Heartbeat error: {e}")
            self._stop_event.wait(5.0)

    def _debug_printer(self):
        """Periodically print per-camera and global investigation stats."""
        while not self._stop_event.is_set():
            self._stop_event.wait(10)
            if self._stop_event.is_set():
                break
            self._print_debug()

        # Final print
        self._print_debug()

    def _print_debug(self):
        """Print structured debug output."""
        connected = sum(1 for h in self.camera_health.values() if h.status == "online")
        self.global_stats.cameras_connected = connected
        self.global_stats.cameras_active = connected

        print("\n" + "=" * 60)
        print("[INVESTIGATION]")
        print(f"  Cameras configured:  {self.global_stats.cameras_configured}")
        print(f"  Cameras connected:   {connected}")
        print(f"  Active targets:      {self.global_stats.active_targets}")
        print(f"  Inference workers:   {self.inference_workers_count}")
        print(f"  Inference queue:     {self.inference_queue.pending_count()} pending")
        print("-" * 60)

        # Per-camera breakdown
        for cam_id in sorted(self.camera_health.keys()):
            h = self.camera_health[cam_id]
            state_str = h.connection_state.value if hasattr(h, 'connection_state') else h.status
            status_icon = "●" if state_str in ("connected", "online") else ("↻" if state_str in ("reconnecting",) else "○")
            print(f"\n  {status_icon} {cam_id.upper()} = {state_str.upper()}")
            print(f"    frames_read={h.frames_read}")
            print(f"    processed={h.frames_processed}")
            print(f"    dropped={h.frames_dropped}")
            print(f"    skipped(no motion)={h.frames_skipped_no_motion}")
            print(f"    vehicles={h.vehicles_detected}")
            print(f"    tracks={h.tracks_active}")
            print(f"    reconnects={h.reconnect_count}")
            if h.current_error:
                print(f"    error={h.current_error}")

        # Global aggregates
        gs = self.global_stats
        print("\n  GLOBAL:")
        print(f"    ocr_attempts={gs.ocr_attempts}")
        print(f"    ocr_success={gs.ocr_success}")
        print(f"    candidates_created={gs.candidate_count}")
        print(f"    api_posts={gs.api_posts}")
        print(f"    api_failures={gs.api_failures}")
        print(f"    target_evaluations={gs.target_evaluations}")
        print(f"    matches={gs.matches}")
        print("  MOTION GATE:")
        print(f"    frames_skipped_no_motion={gs.frames_skipped_no_motion}")
        print(f"    frames_motion_roi={gs.frames_motion_roi}")
        print(f"    frames_full_detect={gs.frames_full_detect}")
        print(f"    savings={gs.motion_gate_savings_pct:.1f}%")
        print("=" * 60)

    def get_heartbeat_payload(self) -> dict:
        """Build the heartbeat payload for the backend, including per-camera health."""
        connected = sum(1 for h in self.camera_health.values() if h.status == "online")
        gs = self.global_stats

        per_camera = []
        for cam_id, h in self.camera_health.items():
            per_camera.append({
                "camera_id": cam_id,
                "status": h.status,
                "connection_state": h.connection_state.value if hasattr(h, 'connection_state') else h.status,
                "frames_read": h.frames_read,
                "frames_processed": h.frames_processed,
                "frames_dropped": h.frames_dropped,
                "vehicles_detected": h.vehicles_detected,
                "tracks_active": h.tracks_active,
                "matches": h.matches,
                "reconnect_count": h.reconnect_count,
                "last_frame_time": h.last_frame_received,
                "last_frame_received": h.last_frame_received,
                "current_error": h.current_error,
                "worker_alive": h.worker_alive,
            })

        return {
            "pipeline_id": "main",
            "cameras_configured": gs.cameras_configured,
            "cameras_connected": connected,
            "cameras_active": connected,
            "vehicles_detected": gs.vehicles_detected,
            "tracks_created": gs.tracks_created,
            "active_targets": gs.active_targets,
            "candidates_created": gs.candidate_count,
            "ocr_attempts": gs.ocr_attempts,
            "ocr_success": gs.ocr_success,
            "api_failures": gs.api_failures,
            "per_camera": per_camera,
        }
