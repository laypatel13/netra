"""
Day 1 validation script — confirm we can connect to and hold a stream from
cctv.corp8.cloud before building anything else on top of it.

Implements the mandatory protocol rules from plan.md Section 8:
  - RTSP forced over TCP (never UDP)
  - RTSP/WebRTC URLs include email:password@ authentication
  - Timing read from PTS (CAP_PROP_POS_MSEC), never wall-clock/arrival time
  - Reconnect with exponential backoff (~2s -> cap 30s)
  - Decoder warnings on join are logged, not treated as fatal
  - Camera list pulled from /cameras.json, never hardcoded

Access model (Sentinel Integrator's Guide):
  HLS:    https://cctv.corp8.cloud/<id>/index.m3u8   (session cookie)
  RTSP:   rtsp://email:password@103.250.160.189:8554/stream/<id>
  WebRTC: http://email:password@103.250.160.189:8889/stream/<id>/whep

Usage:
    python test_feed_connection.py --email you@example.com --password XXXX-XXXX-XXXX [--camera-id cam04] [--frames 200]

If no --camera-id is given, it uses the first live camera from the catalogue.
"""
import argparse
import logging
import os
import sys
import time
from urllib.parse import quote as url_quote

import cv2
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("netra.feed-test")

# Force TCP transport for every OpenCV/FFmpeg RTSP connection in this process.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

CCTV_HOST = "cctv.corp8.cloud"
DIRECT_IP = "103.250.160.189"

MIN_BACKOFF_S = 2
MAX_BACKOFF_S = 30


def login_session(email: str, password: str) -> requests.Session:
    """Authenticate with cctv.corp8.cloud and return a session with cookies."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    resp = session.post(
        f"https://{CCTV_HOST}/auth/login",
        data={"email": email, "password": password},
        timeout=15,
        allow_redirects=True,
    )
    if resp.status_code >= 400:
        log.error("Login failed (HTTP %d). Check your email/password.", resp.status_code)
        sys.exit(1)
    log.info("Authenticated with %s as %s", CCTV_HOST, email)
    return session


def fetch_catalogue(session: requests.Session) -> list[dict]:
    """Pull the camera catalogue from cameras.json — never hardcode camera IDs."""
    url = f"https://{CCTV_HOST}/cameras.json"
    log.info("Fetching catalogue from %s", url)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    cameras = data if isinstance(data, list) else data.get("cameras", [])
    log.info("Catalogue returned %d cameras", len(cameras))
    return cameras


def pick_camera(cameras: list[dict], camera_id: str | None) -> dict:
    if camera_id:
        for cam in cameras:
            cid = str(cam.get("id", cam.get("camera_id", "")))
            if cid == camera_id:
                return cam
        raise SystemExit(f"camera_id {camera_id} not found in catalogue")
    live = [c for c in cameras if c.get("live", c.get("live_status", True))]
    if not live:
        raise SystemExit("no live cameras found in catalogue")
    return live[0]


def build_rtsp_url(email: str, password: str, camera: dict) -> str:
    """RTSP with email:password@ auth on the direct IP."""
    cam_id = str(camera.get("id", camera.get("camera_id", "")))
    encoded_email = url_quote(email, safe="")
    return f"rtsp://{encoded_email}:{password}@{DIRECT_IP}:8554/stream/{cam_id}"


def build_hls_url(camera: dict) -> str:
    """HLS on the CDN host (requires session cookie — OpenCV uses its own HTTP stack)."""
    cam_id = str(camera.get("id", camera.get("camera_id", "")))
    return f"https://{CCTV_HOST}/{cam_id}/index.m3u8"


def try_open(url: str, timeout_s: int = 8) -> cv2.VideoCapture | None:
    """Attempt a single connection with a short timeout."""
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
    Try RTSP first (better for PTS-accurate ANPR timing), fall back to HLS.
    """
    log.info("Probing RTSP: %s", rtsp_url)
    cap = try_open(rtsp_url)
    active_url = rtsp_url
    using_hls = False

    if cap is None:
        log.warning("RTSP not reachable — falling back to HLS: %s", hls_url)
        cap = try_open(hls_url, timeout_s=15)
        active_url = hls_url
        using_hls = True

    if cap is None:
        log.error("Neither RTSP nor HLS could be opened. Check credentials/network.")
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
                break

            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            frames_read += 1

            if last_pts is not None:
                delta = pts_ms - last_pts
                if frames_read <= 5:
                    log.info("frame=%d pts_ms=%.1f delta=%.1f (early frame — buffered replay, ignore for timing)",
                              frames_read, pts_ms, delta)
                else:
                    log.info("frame=%d pts_ms=%.1f delta=%.1f", frames_read, pts_ms, delta)
            last_pts = pts_ms

        cap.release()
        cap = None

    log.info("Done — read %d frames successfully via %s.", frames_read, "HLS" if using_hls else "RTSP")


def main():
    parser = argparse.ArgumentParser(description="Validate cctv.corp8.cloud feed connectivity")
    parser.add_argument("--email", required=True, help="Registered email on Sentinel")
    parser.add_argument("--password", required=True, help="Access password (XXXX-XXXX-XXXX)")
    parser.add_argument("--camera-id", default=None, help="Specific camera id (e.g. cam04); default = first live one")
    parser.add_argument("--frames", type=int, default=200, help="Number of frames to read before exiting")
    args = parser.parse_args()

    try:
        session = login_session(args.email, args.password)
        cameras = fetch_catalogue(session)
        camera = pick_camera(cameras, args.camera_id)
    except requests.RequestException as e:
        log.error("Could not reach cctv.corp8.cloud: %s", e)
        sys.exit(1)

    cam_id = str(camera.get("id", camera.get("camera_id", "")))
    rtsp_url = build_rtsp_url(args.email, args.password, camera)
    hls_url = build_hls_url(camera)
    log.info("Selected camera: %s (%s)", cam_id, camera.get("name", camera.get("location", "unknown")))
    run_capture_loop(rtsp_url, hls_url, args.frames)


if __name__ == "__main__":
    main()
