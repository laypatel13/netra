"""
ANPR pipeline (TIMELINE.md Day 1).

Connects to camera feeds resolved by the backend's own /feeds/catalogue
rather than re-fetching cameras.json directly - this stays consistent with
what HlsPlayer.jsx already uses, and gets the authenticated RTSP/HLS URLs
and the reconnect-with-backoff config for free instead of duplicating that
logic a third time.

Stream URLs now include authentication (email:password@ in RTSP/WebRTC URLs
on the direct IP 103.250.160.189). The backend's /feeds/catalogue proxy
handles login and URL construction - this pipeline just consumes the
resolved URLs it returns.

Camera IDs are cam01-cam30 (not numeric). The pipeline reads them from the
catalogue, never hardcoded.

Detects vehicles, reads plates, and POSTs each valid detection to
/detections with a PTS-derived timestamp - never wall-clock time. See
PLAN.md Section 8.

Usage:
    python pipeline.py --backend http://localhost:8000 --camera-ids cam01,cam13,cam15
    python pipeline.py --backend http://localhost:8000 --camera-ids all --max-cameras 5
    python pipeline.py --dry-run --camera-ids cam01 --max-frames 200
    python pipeline.py --camera-ids cam01 --process-every-n 3 --dedup-cooldown-s 10 --conf-threshold 0.3
        (tuning flags - adjust these against real behavior rather than editing constants)

Design notes:
  - One thread per camera, but this is genuinely CPU-bound, not I/O-bound -
    YOLO detection + EasyOCR on CPU is slow enough (measured: on the order
    of hundreds of ms per processed frame) that it cannot keep up with a
    live 15-30fps stream. PROCESS_EVERY_N_FRAMES throttles via grab()+
    retrieve() (skip cheaply, only fully decode+process 1 in N frames)
    instead of running detection on every single frame and falling further
    and further behind real time. Many concurrent camera threads will
    contend for CPU (Python's GIL is released during the heavy torch/OpenCV
    compute, so there's some real parallelism, but not linear scaling) -
    keep --max-cameras modest for a live demo rather than trying to run
    all 30 at once.
  - No fixed-shape batching across cameras - each camera's frames are
    processed independently at their own native resolution/codec/frame rate.
  - De-duplication: the same plate - or, with no legible plate, the same
    (vehicle_type, vehicle_color) combination - seen on consecutive frames
    of the same camera isn't re-reported every frame, only on first sighting
    or after a cooldown window. Cooldown uses wall-clock time deliberately
    (it's bookkeeping for how often *we* re-report), while every detection's
    stored timestamp_ms stays PTS-derived.
  - Every detected vehicle is recorded now, not just ones with a legible
    plate (PLAN.md Section 0b) - vehicle_type + a thumbnail are always
    captured; plate_number and vehicle_color are populated when available.
"""
import argparse
import logging
import os
import threading
import time
from typing import Optional

import cv2
import requests

from color import dominant_color
from detector import VehicleDetector
from plate_reader import PlateReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
)
log = logging.getLogger("netra.anpr")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Defaults for --dedup-cooldown-s / --process-every-n / --conf-threshold
# below - CLI-configurable rather than hardcoded so they can be tuned
# against real behavior during the Day 3 rehearsal without a code edit.
DEFAULT_DEDUP_COOLDOWN_S = 15.0
DEFAULT_PROCESS_EVERY_N_FRAMES = 5
DEFAULT_CONF_THRESHOLD = 0.25  # found more real vehicles than the detector's own 0.4 default in this session's testing


def fetch_camera_catalogue(backend_url: str, host: Optional[str] = None) -> dict:
    params = {"host": host} if host else {}
    resp = requests.get(f"{backend_url}/feeds/catalogue", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


WARMUP_FRAMES = 15  # frames to discard after connect - decoder warnings/garbage
                     # before the first IDR frame are normal (PLAN.md Section 8),
                     # confirmed empirically: the very first frame read after
                     # connect is sometimes solid-gray decode garbage.


def open_capture(camera: dict, backend_url: str) -> Optional[cv2.VideoCapture]:
    """
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
    streams = camera["streams"]
    for key in ("rtsp", "mp4", "hls"):
        url = streams.get(key)
        if not url:
            continue
        if key == "hls" and url.startswith("/"):
            url = f"{backend_url}{url}"
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
    """
    Every detected vehicle gets POSTed now, not just ones with a legible
    plate (PLAN.md Section 0b) - plate/vehicle_color may be None.
    Multipart upload (fields + JPEG thumbnail) instead of pure JSON, same
    pattern already used by the backend's /cameras/bulk-csv endpoint.
    """
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
            log.warning(
                "Camera %s not onboarded in the registry - onboard it before running ANPR against it",
                camera_id,
            )
        else:
            resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Failed to POST detection for camera %s: %s", camera_id, e)


def process_camera(
    camera: dict, backend_url: str, detector: VehicleDetector, reader: PlateReader,
    backoff_cfg: dict, dry_run: bool, max_frames: Optional[int] = None,
    dedup_cooldown_s: float = DEFAULT_DEDUP_COOLDOWN_S,
    process_every_n: int = DEFAULT_PROCESS_EVERY_N_FRAMES,
) -> None:
    camera_id = camera["camera_id"]
    backoff = backoff_cfg["initial_ms"] / 1000.0
    # Two separate cooldown maps: plate-keyed (precise) and attribute-keyed
    # (camera_id is implicit - this dict is already per-camera) for vehicles
    # with no legible plate. Both are wall-clock bookkeeping for how often
    # *we* re-report, never used as the stored timestamp (PLAN.md Section 8).
    last_seen_plate: dict[str, float] = {}
    last_seen_attrs: dict[tuple[str, str], float] = {}  # (vehicle_type, vehicle_color) -> last reported
    frames_processed = 0

    while max_frames is None or frames_processed < max_frames:
        cap = open_capture(camera, backend_url)
        if cap is None:
            log.warning("Camera %s unreachable on any stream type - retrying in %.0fs", camera_id, backoff)
            time.sleep(backoff)
            backoff = min(backoff * backoff_cfg["multiplier"], backoff_cfg["max_ms"] / 1000.0)
            continue
        backoff = backoff_cfg["initial_ms"] / 1000.0

        grab_index = 0
        while max_frames is None or frames_processed < max_frames:
            ok = cap.grab()
            if not ok:
                log.warning("Camera %s frame read failed - reconnecting", camera_id)
                break

            # YOLO+OCR on CPU can't keep up with 15-30fps live video - fully
            # decoding and processing every frame would make the pipeline
            # fall further and further behind real time. grab() is cheap
            # (skips the full decode most backends would otherwise do), so
            # skipping via grab()+retrieve() instead of read() on every
            # frame keeps the stream roughly caught up to live.
            grab_index += 1
            if grab_index % process_every_n != 0:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                continue

            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)  # PTS, not wall-clock - PLAN.md Section 8
            frames_processed += 1

            for vbox in detector.detect(frame):
                crop = frame[vbox.y1:vbox.y2, vbox.x1:vbox.x2]
                if crop.size == 0:
                    continue

                # Every detected vehicle is recorded now, not just ones with
                # a legible plate (PLAN.md Section 0b) - most cctv.corp8.cloud
                # footage doesn't yield one. Plate stays the precise signal
                # when available; type+color is the fallback that keeps the
                # vehicle traceable either way.
                reading = reader.read(crop)
                color = dominant_color(crop)
                now = time.time()

                if reading is not None:
                    cooldown_ok = (
                        reading.text not in last_seen_plate
                        or (now - last_seen_plate[reading.text]) > dedup_cooldown_s
                    )
                    if not cooldown_ok:
                        continue
                    last_seen_plate[reading.text] = now
                    combined_confidence = round((vbox.confidence + reading.confidence) / 2, 3)
                    log.info("Camera %s: plate %s (conf %.2f)", camera_id, reading.text, combined_confidence)
                else:
                    attr_key = (vbox.label, color.name)
                    cooldown_ok = (
                        attr_key not in last_seen_attrs
                        or (now - last_seen_attrs[attr_key]) > dedup_cooldown_s
                    )
                    if not cooldown_ok:
                        continue
                    last_seen_attrs[attr_key] = now
                    combined_confidence = round(vbox.confidence, 3)
                    log.info("Camera %s: %s %s, no legible plate (conf %.2f)", camera_id, color.name, vbox.label, combined_confidence)

                ok, encoded = cv2.imencode(".jpg", crop)
                if not ok:
                    continue

                post_detection(
                    backend_url, camera_id, pts_ms, combined_confidence, dry_run,
                    thumbnail_jpeg=encoded.tobytes(),
                    plate=reading.text if reading else None,
                    vehicle_type=vbox.label,
                    vehicle_color=color.name if color.name != "unknown" else None,
                )

        cap.release()


def main():
    parser = argparse.ArgumentParser(description="netra Day 4 - ANPR pipeline")
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--host", default=None, help="Camera source host - defaults to the backend's configured host(s)")
    parser.add_argument("--camera-ids", default="all", help="Comma-separated camera ids, or 'all'")
    parser.add_argument("--max-cameras", type=int, default=5, help="Cap concurrent camera threads")
    parser.add_argument("--dry-run", action="store_true", help="Log detections instead of POSTing them")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop each camera after N frames (for testing)")
    parser.add_argument(
        "--process-every-n", type=int, default=DEFAULT_PROCESS_EVERY_N_FRAMES,
        help="Fully decode+process 1 of every N grabbed frames (default: %(default)s) - tune down for a slower/less busy camera, up if the pipeline can't keep pace",
    )
    parser.add_argument(
        "--dedup-cooldown-s", type=float, default=DEFAULT_DEDUP_COOLDOWN_S,
        help="Don't re-report the same plate/attributes on the same camera within this many seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--conf-threshold", type=float, default=DEFAULT_CONF_THRESHOLD,
        help="Vehicle detector confidence threshold (default: %(default)s)",
    )
    args = parser.parse_args()

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

    log.info("Loading vehicle detector and plate reader (first run downloads model weights)...")
    detector = VehicleDetector(conf_threshold=args.conf_threshold)
    reader = PlateReader()

    threads = []
    for camera in cameras:
        t = threading.Thread(
            target=process_camera,
            args=(camera, args.backend, detector, reader, backoff_cfg, args.dry_run, args.max_frames),
            kwargs={
                "dedup_cooldown_s": args.dedup_cooldown_s,
                "process_every_n": args.process_every_n,
            },
            name=f"cam-{camera['camera_id']}",
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
