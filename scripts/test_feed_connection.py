"""
Day 1 validation script — confirm we can actually connect to and hold a
stream from the Sentinel sandbox and/or https://live.corp8.cloud/ before
building anything else on top of it.

Implements the mandatory protocol rules from plan.md Section 8:
  - RTSP forced over TCP (never UDP)
  - Timing read from PTS (CAP_PROP_POS_MSEC), never wall-clock/arrival time
  - Reconnect with exponential backoff (~2s -> cap 30s) instead of a tight loop
  - Decoder warnings on join are logged, not treated as fatal
  - Camera list pulled from /api/ingest, never hardcoded

Also auto-falls back to HLS if RTSP (port 8554) isn't reachable — confirmed
on this project that some networks silently block that port while HTTP(S)
traffic (which HLS rides on) goes through fine. Prefers RTSP when available
since it's better for PTS-accurate ANPR timing.

Usage:
    python test_feed_connection.py --host <sandbox-or-corp8-host> [--camera-id 1] [--frames 200]

If no --camera-id is given, it uses the first live camera from the catalogue.
"""
import argparse
import logging
import os
import sys
import time

import cv2
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("netra.feed-test")

# Force TCP transport for every OpenCV/FFmpeg RTSP connection in this process.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

MIN_BACKOFF_S = 2
MAX_BACKOFF_S = 30


def fetch_catalogue(host: str) -> list[dict]:
    """Pull the camera catalogue — never hardcode camera IDs or URL patterns."""
    url = f"http://{host}/api/ingest"
    log.info("Fetching catalogue from %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # Catalogue shape may vary by deployment — adjust the key below once you've
    # seen a real response from this host.
    cameras = data if isinstance(data, list) else data.get("cameras", [])
    log.info("Catalogue returned %d cameras", len(cameras))
    return cameras


def pick_camera(cameras: list[dict], camera_id: str | None) -> dict:
    if camera_id:
        for cam in cameras:
            if str(cam.get("id")) == str(camera_id):
                return cam
        raise SystemExit(f"camera_id {camera_id} not found in catalogue")
    live = [c for c in cameras if c.get("live_status", c.get("live", True))]
    if not live:
        raise SystemExit("no live cameras found in catalogue")
    return live[0]


def build_rtsp_url(host: str, camera: dict) -> str:
    # Prefer a URL the catalogue gives us directly; fall back to the documented pattern.
    if camera.get("rtsp_url"):
        return camera["rtsp_url"]
    return f"rtsp://{host}:8554/stream/{camera.get('id')}"


def build_hls_url(host: str, camera: dict) -> str:
    """
    Fallback path. Some networks silently block non-standard TCP ports like
    8554 (confirmed on this project across two independent networks) while
    port 80/443 traffic — which HLS uses — goes through fine. The catalogue's
    hls_live_url is a relative path; resolve it against the host.
    """
    path = camera.get("hls_live_url", f"/live/stream/{camera.get('id')}/index.m3u8")
    if path.startswith("http"):
        return path
    return f"http://{host}{path}"


def try_open(url: str, timeout_s: int = 8) -> cv2.VideoCapture | None:
    """Attempt a single connection with a short timeout, without the outer retry/backoff loop."""
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    start = time.time()
    while not cap.isOpened() and (time.time() - start) < timeout_s:
        time.sleep(0.5)
    if cap.isOpened():
        return cap
    cap.release()
    return None


def run_capture_loop(rtsp_url: str, hls_url: str, max_frames: int) -> None:
    """
    Try RTSP first (better for PTS-accurate ANPR timing) with a short probe
    timeout, and fall back to HLS if RTSP isn't reachable — rather than
    hanging on RTSP's much longer internal timeout every single retry.
    Once a working URL is found, reconnect-with-backoff applies to that URL
    for the rest of the run.
    """
    log.info("Probing RTSP: %s", rtsp_url)
    cap = try_open(rtsp_url)
    active_url = rtsp_url
    using_hls = False

    if cap is None:
        log.warning("RTSP not reachable on this network — falling back to HLS: %s", hls_url)
        cap = try_open(hls_url, timeout_s=15)
        active_url = hls_url
        using_hls = True

    if cap is None:
        log.error("Neither RTSP nor HLS could be opened. Check network/firewall or report to organizers.")
        return

    log.info("Connected via %s. Reading frames (timing from PTS, not arrival time)...",
              "HLS" if using_hls else "RTSP")

    backoff = MIN_BACKOFF_S
    frames_read = 0
    last_pts = None

    while frames_read < max_frames:
        if cap is None:
            log.info("Reconnecting to %s", active_url)
            cap = cv2.VideoCapture(active_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log.warning("Failed to open stream — retrying in %ss", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                cap = None
                continue
            backoff = MIN_BACKOFF_S

        while frames_read < max_frames:
            ok, _frame = cap.read()
            if not ok:
                log.warning("Frame read failed — treating as a drop, not a crash. Reconnecting.")
                break  # fall through to outer reconnect-with-backoff loop

            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            frames_read += 1

            if last_pts is not None:
                delta = pts_ms - last_pts
                # A negative or huge delta right after connecting is expected —
                # the gateway replays a buffered GOP faster than real time.
                # DON'T use this delta to compute velocity/dwell time for real
                # logic without smoothing past the first few frames.
                if frames_read <= 5:
                    log.info("frame=%d pts_ms=%.1f delta=%.1f (early frame — buffered replay, ignore for timing)",
                              frames_read, pts_ms, delta)
                else:
                    log.info("frame=%d pts_ms=%.1f delta=%.1f", frames_read, pts_ms, delta)
            last_pts = pts_ms

        cap.release()
        cap = None  # forces the outer loop to reconnect if frames remain

    log.info("Done — read %d frames successfully via %s.", frames_read, "HLS" if using_hls else "RTSP")


def main():
    parser = argparse.ArgumentParser(description="Validate Sentinel sandbox / corp8.cloud feed connectivity")
    parser.add_argument("--host", required=True, help="e.g. sandbox.sentinel.gujarat.gov.in or live.corp8.cloud")
    parser.add_argument("--camera-id", default=None, help="Specific camera id from the catalogue; default = first live one")
    parser.add_argument("--frames", type=int, default=200, help="Number of frames to read before exiting")
    args = parser.parse_args()

    try:
        cameras = fetch_catalogue(args.host)
        camera = pick_camera(cameras, args.camera_id)
    except requests.RequestException as e:
        log.error("Could not reach /api/ingest on %s: %s", args.host, e)
        log.error("If this host doesn't expose that exact path, check its docs and adjust fetch_catalogue().")
        sys.exit(1)

    rtsp_url = build_rtsp_url(args.host, camera)
    hls_url = build_hls_url(args.host, camera)
    log.info("Selected camera: %s", camera.get("id", "unknown"))
    run_capture_loop(rtsp_url, hls_url, args.frames)


if __name__ == "__main__":
    main()