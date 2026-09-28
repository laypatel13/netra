"""
ANPR pipeline for CCTV streams and local video files.

Connects to camera feeds resolved by the backend's own /feeds/catalogue
rather than re-fetching cameras.json directly - this stays consistent with
what HlsPlayer.jsx already uses, and gets the authenticated RTSP/HLS URLs
and the reconnect-with-backoff config for free instead of duplicating that
logic a third time. --video runs a local file through exactly the same path.

Every detected vehicle is POSTed to /detections with a PTS-derived
timestamp, never wall-clock time (PLAN.md Section 8). With --investigation,
tracked vehicles additionally go through the multi-frame evidence path:
tracking -> evidence buffer -> best-frame OCR -> consensus -> scoring ->
/investigations/ingest (see investigation_pipeline.py).

Usage:
    python pipeline.py --backend http://localhost:8000 --camera-ids cam01,cam13,cam15
    python pipeline.py --backend http://localhost:8000 --camera-ids all --max-cameras 5
    python pipeline.py --camera-ids cam01,cam02 --investigation
    python pipeline.py --video sample.mp4 --investigation --investigation-debug
    python pipeline.py --dry-run --camera-ids cam01 --max-frames 200
    python pipeline.py --camera-ids cam01 --process-every-n 3 --dedup-cooldown-s 10 --conf-threshold 0.3
        (tuning flags - adjust these against real behavior rather than editing constants)

Design notes:
  - One thread per camera, but this is genuinely CPU-bound, not I/O-bound -
    YOLO detection + EasyOCR on CPU is slow enough (measured: on the order
    of hundreds of ms per processed frame) that it cannot keep up with a
    live 15-30fps stream. FrameSampler throttles via grab()+retrieve()
    (skip cheaply, only fully decode+process 1 in N frames) instead of
    running detection on every single frame and falling further and further
    behind real time. Keep --max-cameras modest for a live demo rather than
    trying to run all 30 at once.
  - No fixed-shape batching across cameras - each camera's frames are
    processed independently at their own native resolution/codec/frame rate.
  - De-duplication: the same plate - or, with no legible plate, the same
    (vehicle_type, vehicle_color) combination - seen on consecutive frames
    of the same camera isn't re-reported every frame, only on first sighting
    or after a cooldown window measured in stream time (PTS), which also
    works for --video. A PTS jump backwards (file loop, stream restart)
    clears that state.
  - Every detected vehicle is recorded, not just ones with a legible plate
    (PLAN.md Section 0b) - vehicle_type + a thumbnail are always captured;
    plate_number and vehicle_color are populated when available.
"""
import argparse
import logging
import os
import signal
import threading
import time
from typing import Optional

import cv2
import requests

from color import dominant_color
from detector import VehicleDetector
from plate_reader import PlateReader
from frame_sampler import FrameSampler
from motion_gate import MotionGate

# Investigation modules - fail loudly if --investigation is used but modules are broken
_INVESTIGATION_AVAILABLE = False
try:
    from tracker import VehicleTracker
    from evidence_buffer import BufferManager, Observation
    from investigation_pipeline import InvestigationPipeline
    from quality import compute_quality
    _INVESTIGATION_AVAILABLE = True
except ImportError as e:
    _INVESTIGATION_IMPORT_ERROR = str(e)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
)
log = logging.getLogger("netra.anpr")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

DEFAULT_DEDUP_COOLDOWN_S = 15.0
DEFAULT_PROCESS_EVERY_N_FRAMES = 5
DEFAULT_CONF_THRESHOLD = 0.25

# Video test camera - deterministic, reused across runs
VIDEO_TEST_CAMERA_ID = "video_test"


def ensure_video_test_camera(backend_url: str) -> bool:
    """Ensure the video_test camera exists in the registry. Reuse if present."""
    try:
        # Check if it already exists
        resp = requests.get(f"{backend_url}/cameras", timeout=5)
        if resp.ok:
            cameras = resp.json()
            for cam in cameras:
                if cam.get("camera_id") == VIDEO_TEST_CAMERA_ID:
                    log.info("Camera '%s' already registered", VIDEO_TEST_CAMERA_ID)
                    return True

        # Create it
        payload = {
            "camera_id": VIDEO_TEST_CAMERA_ID,
            "name": "Video Test Camera",
            "latitude": 23.0225,
            "longitude": 72.5714,
            "department": "Testing",
            "camera_type": "ip",
            "connectivity_status": "online",
        }
        resp = requests.post(
            f"{backend_url}/cameras",
            json=payload,
            headers={"X-Role": "admin"},
            timeout=5,
        )
        if resp.ok or resp.status_code == 409:
            log.info("Camera '%s' registered successfully", VIDEO_TEST_CAMERA_ID)
            return True
        else:
            log.warning("Failed to register video_test camera: %s", resp.text)
            return False
    except Exception as e:
        log.warning("Could not register video_test camera: %s", e)
        return False


def fetch_camera_catalogue(backend_url: str, host: Optional[str] = None) -> dict:
    params = {"host": host} if host else {}
    resp = requests.get(f"{backend_url}/feeds/catalogue", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


WARMUP_FRAMES = 15  # frames to discard after connect - decoder warnings/garbage
                     # before the first IDR frame are normal (PLAN.md Section 8),
                     # confirmed empirically: the very first frame read after
                     # connect is sometimes solid-gray decode garbage.


def open_capture(camera: dict, backend_url: str) -> Optional[cv2.VideoCapture]:
    """
    Open a capture for a camera: a local video file (the 'local_path' key,
    set by --video) or its live streams.

    Try RTSP first (best for PTS accuracy). `mp4` is None for the primary
    cctv.corp8.cloud host (no such endpoint exists - see PLAN.md Section 8),
    so this only reaches it for other/legacy hosts that do provide one.
    `hls` is a backend-relative proxy path (e.g. /feeds/cam01/hls-proxy/...),
    same as the frontend consumes - must be made absolute against
    backend_url before cv2/ffmpeg can open it.

    Discards a handful of frames right after connecting: OpenCV reports
    `isOpened()` as soon as the RTSP handshake completes, before the decoder
    has actually received a keyframe, so the first few reads can come back
    as valid-looking-but-garbage frames instead of erroring.
    """
    # Local video file - same path, no protocol negotiation needed
    local_path = camera.get("local_path")
    if local_path:
        cap = cv2.VideoCapture(local_path)
        if cap.isOpened():
            log.info("Camera %s connected via local file: %s", camera["camera_id"], local_path)
            return cap
        cap.release()
        log.error("Failed to open local video: %s", local_path)
        return None

    # RTSP / MP4 / HLS stream
    streams = camera.get("streams", {})
    for key in ("rtsp", "mp4", "hls"):
        url = streams.get(key)
        if not url:
            continue
        if key == "hls" and url.startswith("/"):
            url = f"{backend_url}{url}"
        
        # Force RTSP over TCP as per Sentinel Integration Guide
        if key == "rtsp":
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        start = time.time()
        while not cap.isOpened() and (time.time() - start) < 8:
            time.sleep(0.5)
        if cap.isOpened():
            for _ in range(WARMUP_FRAMES):
                cap.read()
            log.info("Camera %s connected via %s", camera["camera_id"], key)
            return cap
        cap.release()
    return None


def post_detection(
    backend_url: str, camera_id: str, timestamp_ms: float, confidence: float,
    dry_run: bool, thumbnail_jpeg: bytes,
    plate: Optional[str] = None, vehicle_type: Optional[str] = None,
    vehicle_color: Optional[str] = None,
) -> None:
    """POST a detection to the backend - same for video and CCTV."""
    fields = {
        "camera_id": camera_id,
        "timestamp_ms": str(timestamp_ms),
        "confidence": str(confidence),
    }
    if plate:
        fields["plate_number"] = plate
    if vehicle_type:
        fields["vehicle_type"] = vehicle_type
    if vehicle_color:
        fields["vehicle_color"] = vehicle_color

    if dry_run:
        log.info("[DRY RUN] would POST %s (+ %d-byte thumbnail)", fields, len(thumbnail_jpeg))
        return
    try:
        resp = requests.post(
            f"{backend_url}/detections",
            data=fields,
            files={"thumbnail": ("thumb.jpg", thumbnail_jpeg, "image/jpeg")},
            timeout=5,
        )
        if resp.status_code == 404:
            log.warning("Camera %s not onboarded - onboard it before running ANPR", camera_id)
        else:
            resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Failed to POST detection for camera %s: %s", camera_id, e)


def process_camera(
    camera: dict, backend_url: str, detector: VehicleDetector, reader: PlateReader,
    backoff_cfg: dict, dry_run: bool, max_frames: Optional[int] = None,
    dedup_cooldown_s: float = DEFAULT_DEDUP_COOLDOWN_S,
    process_every_n: int = DEFAULT_PROCESS_EVERY_N_FRAMES,
    investigation_mode: bool = False,
    debug_counters: Optional[dict] = None,
) -> None:
    camera_id = camera["camera_id"]
    counters = debug_counters or {}
    backoff = backoff_cfg["initial_ms"] / 1000.0
    last_seen_plate: dict[str, float] = {}
    last_seen_attrs: dict[tuple[str, str], float] = {}
    last_pts_ms: float = -1.0
    frames_processed = 0
    sampler = FrameSampler(process_every_n=process_every_n)
    
    # Investigation setup - each camera thread gets its own isolated tracker
    tracker = None
    inv_pipeline = None
    buffer_mgr = None
    motion_gate = MotionGate()
    if investigation_mode:
        if not _INVESTIGATION_AVAILABLE:
            log.error("Investigation mode requested but modules not available: %s", _INVESTIGATION_IMPORT_ERROR)
            return
        tracker = VehicleTracker(conf_threshold=detector.conf_threshold)
        inv_pipeline = InvestigationPipeline(backend_url, camera_id, reader, debug_counters=counters)
        buffer_mgr = BufferManager(camera_id)
        inv_pipeline.sync_targets()

    while max_frames is None or frames_processed < max_frames:
        cap = open_capture(camera, backend_url)
        if cap is None:
            # For local video, don't retry - the file is done or broken
            if camera.get("local_path"):
                log.info("[%s] Video file ended or failed to open", camera_id)
                break
            log.warning("[%s] RECONNECTING - retrying in %.0fs", camera_id, backoff)
            time.sleep(backoff)
            backoff = min(backoff * backoff_cfg["multiplier"], backoff_cfg["max_ms"] / 1000.0)
            continue
        backoff = backoff_cfg["initial_ms"] / 1000.0
        log.info("[%s] CONNECTED", camera_id)
        sampler.reset()

        while max_frames is None or frames_processed < max_frames:
            ok = cap.grab()
            if not ok:
                if camera.get("local_path"):
                    log.info("[%s] Video file finished (%d frames processed)", camera_id, frames_processed)
                    # Process remaining investigation buffers
                    if investigation_mode and buffer_mgr:
                        for b in list(buffer_mgr._buffers.values()):
                            if b.frame_count > 0:
                                inv_pipeline.process_expired_buffer(b)
                        buffer_mgr._buffers.clear()
                    cap.release()
                    # Final heartbeat
                    if investigation_mode and inv_pipeline:
                        inv_pipeline.send_heartbeat()
                    log.info("[%s] Looping video file", camera_id)
                    break
                log.warning("[%s] FRAME_TIMEOUT - reconnecting", camera_id)
                break

            counters["frames_read"] = counters.get("frames_read", 0) + 1
            
            if not sampler.tick():
                continue
            ok, frame = cap.retrieve()
            if not ok:
                continue

            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)  # PTS, not wall-clock - PLAN.md Section 8

            # --- Scene discontinuity handling ---
            if last_pts_ms >= 0 and pts_ms < last_pts_ms:
                log.info("[%s] Scene discontinuity detected (PTS dropped from %.0f to %.0f) - clearing dedup state", camera_id, last_pts_ms, pts_ms)
                last_seen_plate.clear()
                last_seen_attrs.clear()
            last_pts_ms = pts_ms
            
            frames_processed += 1
            counters["frames_processed"] = counters.get("frames_processed", 0) + 1

            # Send heartbeat periodically
            if investigation_mode and inv_pipeline:
                inv_pipeline.sync_targets()
                inv_pipeline.send_heartbeat()

            # --- Motion gate check ---
            motion_result = motion_gate.process(frame)
            if not motion_result.has_motion:
                counters["frames_skipped_no_motion"] = counters.get("frames_skipped_no_motion", 0) + 1
                # Still expire stale track buffers
                if investigation_mode and buffer_mgr and inv_pipeline:
                    expired = buffer_mgr.cleanup_expired()
                    for b in expired:
                        inv_pipeline.process_expired_buffer(b)
                continue

            if motion_result.use_full_frame or not motion_result.motion_rois:
                counters["frames_full_detect"] = counters.get("frames_full_detect", 0) + 1
            else:
                counters["frames_motion_roi"] = counters.get("frames_motion_roi", 0) + 1

            if investigation_mode and tracker:
                # Use ByteTrack-based tracking - tracker.track() runs its own
                # YOLO internally, so the motion gate ROI optimization doesn't
                # apply here.  The motion gate still saves us from running the
                # tracker on frames with zero motion.
                tracked_vehicles = tracker.track(frame)
                
                # Process expired track buffers
                expired = buffer_mgr.cleanup_expired()
                for b in expired:
                    inv_pipeline.process_expired_buffer(b)
                    
                # Process ready buffers (enough evidence collected, with minimum age)
                for b in buffer_mgr.get_ready():
                    if time.time() - b.created_at > 1.0:  # minimum 1s age
                        inv_pipeline.process_expired_buffer(b)
                        buffer_mgr.remove(b.track_id)
            else:
                # Standard per-frame detection (non-investigation mode)
                if motion_result.use_full_frame or not motion_result.motion_rois:
                    tracked_vehicles = detector.detect(frame)
                else:
                    tracked_vehicles = detector.detect_rois(frame, motion_result.motion_rois, imgsz=320)

            for vbox in tracked_vehicles:
                crop = frame[vbox.y1:vbox.y2, vbox.x1:vbox.x2]
                if crop.size == 0:
                    continue
                    
                counters["vehicles_detected"] = counters.get("vehicles_detected", 0) + 1
                color = dominant_color(crop)

                if investigation_mode and hasattr(vbox, "track_id") and vbox.track_id != -1:
                    counters["tracks_created"] = len(buffer_mgr._buffers) if buffer_mgr else 0
                    
                    # Collect evidence for the track buffer
                    track_buf = buffer_mgr.get_or_create(vbox.track_id)
                    q = compute_quality(crop)
                    
                    obs = Observation(
                        frame_index=sampler.frame_count,
                        pts_ms=pts_ms,
                        bbox=(vbox.x1, vbox.y1, vbox.x2, vbox.y2),
                        confidence=vbox.confidence,
                        crop=crop.copy(),
                        quality=q,
                        vehicle_type=vbox.label,
                        vehicle_color=color.color,
                        color_unknown_reason=color.unknown_reason
                    )
                    track_buf.add(obs)
                    counters["evidence_buffers"] = buffer_mgr.active_count
                    
                    # Standard detection emission - only on first frame of track
                    if track_buf.frame_count > 1:
                        continue

                # --- Standard Detection Emission ---
                reading = reader.read(crop)
                now_s = pts_ms / 1000.0

                if reading is not None:
                    cooldown_ok = (
                        reading.text not in last_seen_plate
                        or (now_s - last_seen_plate[reading.text]) > dedup_cooldown_s
                    )
                    if not cooldown_ok:
                        continue
                    last_seen_plate[reading.text] = now_s
                    combined_confidence = round((vbox.confidence + reading.confidence) / 2, 3)
                    log.info("Camera %s: plate %s (conf %.2f)", camera_id, reading.text, combined_confidence)
                else:
                    attr_key = (vbox.label, color.color)
                    cooldown_ok = (
                        attr_key not in last_seen_attrs
                        or (now_s - last_seen_attrs[attr_key]) > dedup_cooldown_s
                    )
                    if not cooldown_ok:
                        continue
                    last_seen_attrs[attr_key] = now_s
                    combined_confidence = round(vbox.confidence, 3)
                    log.info("Camera %s: %s %s, no legible plate (conf %.2f)", camera_id, color.color, vbox.label, combined_confidence)

                ok, encoded = cv2.imencode(".jpg", crop)
                if not ok:
                    continue

                post_detection(
                    backend_url, camera_id, pts_ms, combined_confidence, dry_run,
                    thumbnail_jpeg=encoded.tobytes(),
                    plate=reading.text if reading else None,
                    vehicle_type=vbox.label,
                    vehicle_color=color.color,
                )

        cap.release()


def print_debug_counters(counters: dict):
    """Print investigation debug diagnostic summary."""
    print("\n" + "=" * 60)
    print("INVESTIGATION DEBUG DIAGNOSTICS")
    print("=" * 60)
    print(f"  FRAMES READ:          {counters.get('frames_read', 0)}")
    print(f"  FRAMES PROCESSED:     {counters.get('frames_processed', 0)}")
    print(f"  FRAMES SKIPPED (NO MOTION): {counters.get('frames_skipped_no_motion', 0)}")
    print(f"  FRAMES ROI DETECT:    {counters.get('frames_motion_roi', 0)}")
    print(f"  FRAMES FULL DETECT:   {counters.get('frames_full_detect', 0)}")
    total_gate = counters.get('frames_skipped_no_motion', 0) + counters.get('frames_motion_roi', 0) + counters.get('frames_full_detect', 0)
    savings = (counters.get('frames_skipped_no_motion', 0) / total_gate * 100) if total_gate > 0 else 0
    print(f"  MOTION GATE SAVINGS:  {savings:.1f}%")
    print(f"  VEHICLES DETECTED:    {counters.get('vehicles_detected', 0)}")
    print(f"  TRACKS CREATED:       {counters.get('tracks_created', 0)}")
    print(f"  ACTIVE TARGETS:       {counters.get('active_targets', 0)}")
    print(f"  TARGET EVALUATIONS:   {counters.get('target_evaluations', 0)}")
    print(f"  MATCHES:              {counters.get('matches', 0)}")
    print(f"  UNKNOWN CANDIDATES:   {counters.get('unknown_candidates', 0)}")
    print(f"  EVIDENCE BUFFERS:     {counters.get('evidence_buffers', 0)}")
    print(f"  OCR ATTEMPTS:         {counters.get('ocr_attempts', 0)}")
    print(f"  OCR SUCCESS:          {counters.get('ocr_success', 0)}")
    print(f"  CANDIDATES CREATED:   {counters.get('candidates_created', 0)}")
    print(f"  API POSTS:            {counters.get('api_posts', 0)}")
    print(f"  API FAILURES:         {counters.get('api_failures', 0)}")
    print("=" * 60)

    # Diagnostic chain
    if counters.get("vehicles_detected", 0) == 0:
        print("  DIAGNOSIS: YOLO/video problem - no vehicles detected")
    elif counters.get("tracks_created", 0) == 0:
        print("  DIAGNOSIS: Tracking problem - vehicles detected but no tracks")
    elif counters.get("target_evaluations", 0) == 0:
        print("  DIAGNOSIS: Target/pipeline wiring - tracks exist but no target evaluations")
    elif counters.get("matches", 0) == 0 and counters.get("unknown_candidates", 0) == 0:
        print("  DIAGNOSIS: Target filter problem - evaluations run but no matches/unknowns")
    elif counters.get("evidence_buffers", 0) == 0:
        print("  DIAGNOSIS: Buffer problem - matches found but no evidence collected")
    elif counters.get("ocr_attempts", 0) == 0:
        print("  DIAGNOSIS: Finalization/orchestration - buffers exist but no OCR ran")
    elif counters.get("candidates_created", 0) == 0 and counters.get("api_posts", 0) > 0:
        print("  DIAGNOSIS: Backend ingest problem - API posts sent but failed")
    elif counters.get("candidates_created", 0) == 0:
        print("  DIAGNOSIS: Scoring/ingest - OCR ran but no candidates created")
    elif counters.get("candidates_created", 0) > 0:
        print("  DIAGNOSIS: Pipeline working - candidates created successfully")
    print("")


def main():
    parser = argparse.ArgumentParser(description="netra ANPR pipeline")
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--host", default=None, help="Camera source host - defaults to the backend's configured host(s)")
    parser.add_argument("--camera-ids", default="all", help="Comma-separated camera ids, or 'all'")
    parser.add_argument("--max-cameras", type=int, default=5, help="Cap concurrent camera threads")
    parser.add_argument("--dry-run", action="store_true", help="Log detections instead of POSTing them")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop each camera after N frames (for testing)")
    parser.add_argument(
        "--video", type=str, default=None,
        help="Path to a local MP4/AVI video file - uses exact same pipeline path as CCTV",
    )
    parser.add_argument(
        "--process-every-n", type=int, default=DEFAULT_PROCESS_EVERY_N_FRAMES,
        help="Fully decode+process 1 of every N grabbed frames (default: %(default)s)",
    )
    parser.add_argument(
        "--dedup-cooldown-s", type=float, default=DEFAULT_DEDUP_COOLDOWN_S,
        help="Don't re-report the same plate/attributes within this many seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--conf-threshold", type=float, default=DEFAULT_CONF_THRESHOLD,
        help="Vehicle detector confidence threshold (default: %(default)s)",
    )
    parser.add_argument("--investigation", action="store_true",
                        help="Enable multi-frame targeted vehicle investigation mode")
    parser.add_argument("--investigation-debug", action="store_true",
                        help="Print investigation diagnostic counters periodically")
    parser.add_argument(
        "--inference-workers", type=int, default=1,
        help="Number of shared YOLO inference workers for multi-camera mode (default: 1)",
    )
    args = parser.parse_args()

    # Shared debug counters (thread-safe enough for diagnostics - int increments are atomic on CPython)
    debug_counters = {"cameras_active": 0}

    if args.video:
        # --- Local video mode (single-camera test) ---
        # --video takes precedence over --max-cameras.  This is always a
        # single-camera run, even in investigation mode.
        log.info("=== VIDEO MODE: %s ===", args.video)
        
        if not os.path.isfile(args.video):
            log.error("Video file not found: %s", args.video)
            return

        # Ensure the deterministic video_test camera exists
        if not args.dry_run:
            ensure_video_test_camera(args.backend)

        camera = {
            "camera_id": VIDEO_TEST_CAMERA_ID,
            "local_path": args.video,
        }
        backoff_cfg = {"initial_ms": 1000, "multiplier": 2, "max_ms": 30000}
        debug_counters["cameras_active"] = 1

        log.info("Loading vehicle detector and plate reader...")
        detector = VehicleDetector(conf_threshold=args.conf_threshold)
        reader = PlateReader()

        # Start a periodic debug counter printer
        stop_debug = threading.Event()
        if args.investigation_debug:
            def debug_printer():
                while not stop_debug.is_set():
                    stop_debug.wait(10)
                    if not stop_debug.is_set():
                        print_debug_counters(debug_counters)
            t_debug = threading.Thread(target=debug_printer, name="debug-printer", daemon=True)
            t_debug.start()

        process_camera(
            camera, args.backend, detector, reader, backoff_cfg, args.dry_run,
            args.max_frames,
            dedup_cooldown_s=args.dedup_cooldown_s,
            process_every_n=args.process_every_n,
            investigation_mode=args.investigation,
            debug_counters=debug_counters,
        )

        stop_debug.set()
        if args.investigation_debug:
            print_debug_counters(debug_counters)

    elif args.investigation:
        # --- Multi-camera investigation mode ---
        # Uses the shared inference queue architecture from multi_camera.py.
        # One YOLO model shared across all cameras, camera-local trackers,
        # global investigation targets.
        from multi_camera import MultiCameraOrchestrator

        catalogue = fetch_camera_catalogue(args.backend, args.host)
        cameras = catalogue["cameras"]
        backoff_cfg = catalogue["reconnect"]

        if args.camera_ids != "all":
            wanted = set(args.camera_ids.split(","))
            cameras = [c for c in cameras if c["camera_id"] in wanted]

        cameras = cameras[: args.max_cameras]
        if not cameras:
            log.error("No matching cameras found in catalogue")
            return

        log.info("Found %d cameras for investigation mode", len(cameras))

        # PlateReader is shared (it's thread-safe - EasyOCR holds a model
        # that we only call from the camera worker's result processing,
        # which is serialized per camera).
        log.info("Loading plate reader (first run downloads model weights)...")
        reader = PlateReader()

        orchestrator = MultiCameraOrchestrator(
            cameras=cameras,
            backend_url=args.backend,
            backoff_cfg=backoff_cfg,
            process_every_n=args.process_every_n,
            max_frames=args.max_frames,
            inference_workers=args.inference_workers,
            conf_threshold=args.conf_threshold,
            investigation_debug=args.investigation_debug,
            plate_reader=reader,
        )

        # Register signal handler for clean shutdown
        def _shutdown_handler(signum, frame):
            log.info("Received signal %s - stopping orchestrator", signum)
            orchestrator.stop()

        signal.signal(signal.SIGINT, _shutdown_handler)

        orchestrator.start()
        orchestrator.wait()

    else:
        # --- Live CCTV mode (standard, non-investigation) ---
        catalogue = fetch_camera_catalogue(args.backend, args.host)
        cameras = catalogue["cameras"]
        backoff_cfg = catalogue["reconnect"]

        if args.camera_ids != "all":
            wanted = set(args.camera_ids.split(","))
            cameras = [c for c in cameras if c["camera_id"] in wanted]

        cameras = cameras[: args.max_cameras]
        if not cameras:
            log.error("No matching cameras found in catalogue")
            return

        debug_counters["cameras_active"] = len(cameras)

        log.info("Loading vehicle detector and plate reader (first run downloads model weights)...")
        detector = VehicleDetector(conf_threshold=args.conf_threshold)
        reader = PlateReader()

        # Start a periodic debug counter printer
        stop_debug = threading.Event()
        if args.investigation_debug:
            def debug_printer():
                while not stop_debug.is_set():
                    stop_debug.wait(10)
                    if not stop_debug.is_set():
                        print_debug_counters(debug_counters)
            t_debug = threading.Thread(target=debug_printer, name="debug-printer", daemon=True)
            t_debug.start()

        # Track threads for clean shutdown
        stop_event = threading.Event()
        threads = []
        for camera in cameras:
            t = threading.Thread(
                target=process_camera,
                args=(camera, args.backend, detector, reader, backoff_cfg, args.dry_run, args.max_frames),
                kwargs={
                    "dedup_cooldown_s": args.dedup_cooldown_s,
                    "process_every_n": args.process_every_n,
                    "investigation_mode": False,
                    "debug_counters": debug_counters,
                },
                name=f"cam-{camera['camera_id']}",
                daemon=True,
            )
            threads.append(t)
            t.start()

        # Register signal handler for clean shutdown
        def _shutdown_handler(signum, frame):
            log.info("Received signal %s - stopping all camera workers", signum)
            stop_event.set()
            stop_debug.set()

        signal.signal(signal.SIGINT, _shutdown_handler)

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt - workers stopping")
            stop_event.set()

        stop_debug.set()
        if args.investigation_debug:
            print_debug_counters(debug_counters)


if __name__ == "__main__":
    main()

