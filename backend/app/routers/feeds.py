"""
Model 2 — Feed catalogue proxy.

The browser can't consume RTSP directly. This router:
  1. Authenticates with cctv.corp8.cloud (POST /auth/login → session cookie)
  2. Fetches /cameras.json from the authenticated session
  3. Returns HLS/RTSP/WHEP URLs for each camera so the frontend can connect
  4. Proxies HLS manifest/segments through the backend (avoids CORS + auth)
  5. Exposes reconnect-with-backoff configuration to the frontend

Access model (from the Sentinel Integrator's Guide):
  - HLS:     served over CDN host (cctv.corp8.cloud), behind session cookie
             → proxied through /feeds/{id}/hls-proxy/ to avoid browser CORS
  - RTSP:    rtsp://email:password@103.250.160.189:8554/stream/<id>  (TCP only)
  - WebRTC:  http://email:password@103.250.160.189:8889/stream/<id>/whep
  The @ in the email must be percent-encoded as %40.

Camera IDs are cam01–cam30 (not numeric).
"""
import json
import re
import os
import time
import logging
import threading
from typing import Optional
from urllib.parse import quote as url_quote

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/feeds", tags=["feeds"])
logger = logging.getLogger("netra.feeds")

# --- Reconnect-with-backoff constants (plan.md Section 8) ---
BACKOFF_INITIAL_MS = 2000
BACKOFF_MAX_MS = 30000
BACKOFF_MULTIPLIER = 2.0

# --- Session management for authenticated cctv.corp8.cloud access ---
_session_lock = threading.Lock()
_cctv_session: Optional[requests.Session] = None
_session_ts: float = 0
_SESSION_TTL = 3600  # re-login every hour


def _get_cctv_credentials() -> tuple[str, str]:
    """Read CCTV credentials from environment."""
    email = os.getenv("CCTV_EMAIL", "").strip()
    password = os.getenv("CCTV_PASSWORD", "").strip()
    if not email or not password:
        raise HTTPException(
            status_code=500,
            detail="CCTV_EMAIL and CCTV_PASSWORD must be set in .env",
        )
    return email, password


def _get_cctv_session() -> requests.Session:
    """
    Authenticate with cctv.corp8.cloud and return a session with the
    login cookie. Thread-safe, cached with TTL.
    """
    global _cctv_session, _session_ts

    with _session_lock:
        now = time.time()
        if _cctv_session and (now - _session_ts) < _SESSION_TTL:
            return _cctv_session

        email, password = _get_cctv_credentials()
        host = os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        login_url = f"https://{host}/auth/login"

        try:
            resp = session.post(
                login_url,
                data={"email": email, "password": password},
                timeout=15,
                allow_redirects=True,
            )
            # The login page redirects on success. Check we got a valid session.
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"CCTV login failed (HTTP {resp.status_code}). Check CCTV_EMAIL/CCTV_PASSWORD.",
                )
            logger.info("Authenticated with %s as %s", host, email)
        except requests.RequestException as e:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach {login_url}: {e}",
            )

        _cctv_session = session
        _session_ts = now
        return session


def _get_rtsp_auth_prefix() -> str:
    """
    Build the email:password@ prefix for RTSP/WebRTC URLs.
    The @ in the email must be percent-encoded as %40.
    """
    email, password = _get_cctv_credentials()
    encoded_email = url_quote(email, safe="")  # encodes @ → %40
    return f"{encoded_email}:{password}"


# --- Catalogue cache ---
_catalogue_cache: dict[str, dict] = {}
_cache_ttl_seconds = 60


def _get_disk_cache_path(host: str) -> str:
    safe_host = host.replace(":", "_").replace("/", "_")
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"catalogue_{safe_host}.json")


def _fetch_catalogue(host: str) -> list[dict]:
    """
    Fetch and cache the camera catalogue from cctv.corp8.cloud/cameras.json.

    The new Sentinel domain serves cameras.json (not /api/ingest) and requires
    an authenticated session. Falls back to /api/ingest for any non-cctv host.
    """
    now = time.time()
    disk_path = _get_disk_cache_path(host)

    # Check memory cache first
    cached = _catalogue_cache.get(host)

    # If not in memory, try disk
    if not cached and os.path.exists(disk_path):
        try:
            with open(disk_path, "r") as f:
                cached = json.load(f)
                _catalogue_cache[host] = cached
        except Exception as e:
            logger.warning("Failed to read disk cache for %s: %s", host, e)

    if cached and now - cached["ts"] < _cache_ttl_seconds:
        return cached["data"]

    cameras = None
    cctv_host = os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()

    if host == cctv_host:
        # New authenticated endpoint: /cameras.json
        session = _get_cctv_session()
        url = f"https://{host}/cameras.json"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            # cameras.json returns a flat array of camera objects
            cameras = data if isinstance(data, list) else data.get("cameras", [])
            logger.info("Fetched %d cameras from %s (authenticated)", len(cameras), url)
        except requests.RequestException as e:
            logger.warning("Failed to fetch %s: %s", url, e)
    else:
        # Legacy path for any other host (shouldn't be used now, but safe)
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}/api/ingest"
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                cameras = data if isinstance(data, list) else data.get("cameras", [])
                logger.info("Fetched %d cameras from %s", len(cameras), url)
                break
            except requests.RequestException:
                continue

    if cameras is not None:
        cache_entry = {"ts": now, "data": cameras}
        _catalogue_cache[host] = cache_entry
        try:
            with open(disk_path, "w") as f:
                json.dump(cache_entry, f)
        except Exception as e:
            logger.warning("Failed to write disk cache for %s: %s", host, e)
        return cameras

    # Serve stale cache on transient failure
    if cached:
        logger.warning("Could not reach %s (transient failure), serving stale cache", host)
        return cached["data"]

    raise HTTPException(
        status_code=502,
        detail=f"Could not reach catalogue at {host}",
    )


def _resolve_stream_urls(host: str, camera_entry: dict) -> dict:
    """
    Given a catalogue entry from cameras.json, build the stream URLs.

    New cctv.corp8.cloud layout (from Integrator's Guide):
      HLS:    https://cctv.corp8.cloud/<id>/index.m3u8   (CDN, session cookie)
      RTSP:   rtsp://email:password@103.250.160.189:8554/stream/<id>
      WebRTC: http://email:password@103.250.160.189:8889/stream/<id>/whep
    """
    cctv_host = os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    direct_ip = os.getenv("CCTV_DIRECT_IP", "103.250.160.189").strip()

    # Camera ID — the new domain uses cam01–cam30 format
    cam_id = str(camera_entry.get("id", camera_entry.get("camera_id", "")))

    if host == cctv_host:
        # Authenticated URLs for the new domain
        auth_prefix = _get_rtsp_auth_prefix()

        # HLS goes through the backend proxy to avoid CORS + auth issues in the browser
        hls = f"/feeds/{cam_id}/hls-proxy/index.m3u8"
        rtsp = f"rtsp://{auth_prefix}@{direct_ip}:8554/stream/{cam_id}"
        whep = f"http://{auth_prefix}@{direct_ip}:8889/stream/{cam_id}/whep"
        mp4 = f"https://{cctv_host}/{cam_id}"  # potential progressive fallback
    else:
        # Legacy format (kept for safety but shouldn't be reached)
        scheme = "https" if "corp8.cloud" in host else "http"
        rtsp = camera_entry.get("rtsp_url") or f"rtsp://{host}:8554/stream/{cam_id}"
        whep = camera_entry.get("webrtc_url") or f"http://{host}:8889/stream/{cam_id}/whep"
        hls_raw = camera_entry.get("hls_live_url") or f"/live/stream/{cam_id}/index.m3u8"
        hls = f"{scheme}://{host}{hls_raw}" if hls_raw.startswith("/") else hls_raw
        mp4 = f"{scheme}://{host}/stream/{cam_id}"

    return {
        "rtsp": rtsp,
        "whep": whep,
        "hls": hls,
        "mp4": mp4,
    }


@router.get(
    "/catalogue",
    summary="List available feeds with stream URLs",
    response_description="Array of cameras with resolved RTSP/WHEP/HLS URLs plus reconnect config.",
)
def get_feed_catalogue(
    host: Optional[str] = Query(
        default=None,
        description="Override the upstream host. Defaults to CCTV_HOST env var.",
    ),
):
    """
    Fetches the camera catalogue from cctv.corp8.cloud (authenticated) and
    enriches each entry with resolved RTSP, WHEP, and HLS stream URLs.
    """
    h = host or os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    if not h:
        raise HTTPException(
            status_code=400,
            detail="No feed host configured — set CCTV_HOST in .env or pass ?host=",
        )

    catalogue = _fetch_catalogue(h)
    all_cameras = []
    for entry in catalogue:
        cam_id = str(entry.get("id", entry.get("camera_id", "")))
        urls = _resolve_stream_urls(h, entry)

        # Normalize field names — cameras.json may use different keys
        name = entry.get("name", entry.get("location", f"Camera {cam_id}"))
        live = entry.get("live", entry.get("live_status", False))
        codec = entry.get("codec", "unknown") or "unknown"
        width = entry.get("width", 0)
        height = entry.get("height", 0)
        resolution = f"{width}x{height}" if width and height else "unknown"

        all_cameras.append({
            "camera_id": cam_id,
            "source_host": h,
            "name": name,
            "live": live,
            "codec": codec,
            "resolution": resolution,
            "streams": urls,
            "location": entry.get("location"),
        })

    return {
        "cameras": all_cameras,
        "total": len(all_cameras),
        "sources": [h],
        "reconnect": {
            "initial_ms": BACKOFF_INITIAL_MS,
            "max_ms": BACKOFF_MAX_MS,
            "multiplier": BACKOFF_MULTIPLIER,
        },
    }


@router.get(
    "/{camera_id}/hls",
    summary="Resolve HLS URL for a specific camera",
    response_description="HLS playlist URL for use with HLS.js or native <video>.",
)
def get_hls_url(
    camera_id: str,
    host: Optional[str] = Query(default=None),
):
    """Returns the HLS playlist URL for a given camera ID."""
    h = host or os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    if not h:
        raise HTTPException(status_code=400, detail="No host configured")
    catalogue = _fetch_catalogue(h)
    for entry in catalogue:
        entry_id = str(entry.get("id", entry.get("camera_id", "")))
        if entry_id == camera_id:
            urls = _resolve_stream_urls(h, entry)
            return {"camera_id": camera_id, "hls_url": urls["hls"]}
    raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found in catalogue at {h}")


@router.get(
    "/{camera_id}/whep",
    summary="Resolve WHEP URL for a specific camera",
    response_description="WHEP endpoint URL for WebRTC playback.",
)
def get_whep_url(
    camera_id: str,
    host: Optional[str] = Query(default=None),
):
    """Returns the WHEP (WebRTC) URL for a given camera ID."""
    h = host or os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    if not h:
        raise HTTPException(status_code=400, detail="No host configured")
    catalogue = _fetch_catalogue(h)
    for entry in catalogue:
        entry_id = str(entry.get("id", entry.get("camera_id", "")))
        if entry_id == camera_id:
            urls = _resolve_stream_urls(h, entry)
            return {"camera_id": camera_id, "whep_url": urls["whep"]}
    raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found in catalogue at {h}")


# ---------- HLS Proxy ----------
# The browser can't fetch HLS directly from cctv.corp8.cloud because:
#   1. CORS: no Access-Control-Allow-Origin header on the CDN
#   2. Auth: the browser doesn't have the session cookie for cctv.corp8.cloud
# Solution: proxy manifests and segments through the backend, which holds
# the authenticated session.


@router.get(
    "/{camera_id}/hls-proxy/{path:path}",
    summary="Proxy HLS manifest/segments from cctv.corp8.cloud",
    response_class=StreamingResponse,
)
def hls_proxy(camera_id: str, path: str):
    """
    Proxies HLS requests (m3u8 manifests and .ts segments) through the
    backend's authenticated session to avoid CORS and auth issues in the
    browser.

    The HLS.js player in the frontend hits:
      /feeds/cam04/hls-proxy/index.m3u8
      /feeds/cam04/hls-proxy/segment123.ts
    and this endpoint fetches them from cctv.corp8.cloud with the session
    cookie, then streams them back.
    """
    cctv_host = os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    session = _get_cctv_session()

    if path == "enc.key":
        upstream_url = f"https://{cctv_host}/enc.key"
    else:
        upstream_url = f"https://{cctv_host}/{camera_id}/{path}"

    try:
        resp = session.get(upstream_url, timeout=15, stream=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HLS proxy failed for %s: %s", upstream_url, e)
        raise HTTPException(status_code=502, detail=f"Could not fetch {upstream_url}: {e}")

    content_type = resp.headers.get("content-type", "application/octet-stream")

    # For m3u8 manifests, rewrite any absolute URLs to go through the proxy
    if path.endswith(".m3u8"):
        body = resp.content.decode("utf-8", errors="replace")
        # Rewrite absolute URLs like https://cctv.corp8.cloud/cam04/segment.ts
        # to relative paths that the proxy will catch
        body = body.replace(f"https://{cctv_host}/{camera_id}/", "")
        body = body.replace(f"http://{cctv_host}/{camera_id}/", "")
        # Rewrite the absolute encryption key path to a relative one
        body = body.replace('URI="/enc.key"', 'URI="enc.key"')
        return StreamingResponse(
            iter([body.encode("utf-8")]),
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "no-cache, no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    # For .ts segments — stream through directly
    def iter_content():
        for chunk in resp.iter_content(chunk_size=65536):
            yield chunk

    return StreamingResponse(
        iter_content(),
        media_type=content_type,
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
        },
    )
