"""
Model 2 — Feed catalogue proxy.

The browser can't consume RTSP directly. The Sentinel sandbox already exposes
WHEP and HLS endpoints per camera. This router:
  1. Proxies the /api/ingest catalogue from upstream sources
  2. Returns WHEP/HLS URLs for each camera so the frontend can connect
  3. Exposes reconnect-with-backoff configuration to the frontend

The frontend LiveViewer calls GET /feeds/catalogue on mount and connects
HLS.js players to the returned URLs.
"""
import os
import time
import logging
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/feeds", tags=["feeds"])
logger = logging.getLogger("netra.feeds")

# --- Reconnect-with-backoff constants (plan.md Section 8) ---
BACKOFF_INITIAL_MS = 2000
BACKOFF_MAX_MS = 30000
BACKOFF_MULTIPLIER = 2.0

import json
import os
import tempfile

# --- In-memory & Disk catalogue cache ---
_catalogue_cache: dict[str, dict] = {}  # keyed by host
_cache_ttl_seconds = 60

def _get_disk_cache_path(host: str) -> str:
    safe_host = host.replace(":", "_").replace("/", "_")
    # Store in backend/data/ instead of a random temp directory
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"catalogue_{safe_host}.json")


def _fetch_catalogue(host: str) -> list[dict]:
    """
    Fetch and cache the camera catalogue from an upstream /api/ingest endpoint.
    Cache is per-host with a TTL to avoid hammering the upstream on every
    page load. Implements stale-while-revalidate for transient failures, backed
    by a disk cache to survive server restarts.
    """
    now = time.time()
    disk_path = _get_disk_cache_path(host)
    
    # Check memory cache first
    cached = _catalogue_cache.get(host)
    
    # If not in memory, try to load from disk
    if not cached and os.path.exists(disk_path):
        try:
            with open(disk_path, "r") as f:
                cached = json.load(f)
                _catalogue_cache[host] = cached
        except Exception as e:
            logger.warning(f"Failed to read disk cache for {host}: {e}")

    if cached and now - cached["ts"] < _cache_ttl_seconds:
        return cached["data"]

    # Try HTTPS first, fall back to HTTP for sandbox/local
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/api/ingest"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            cameras = data if isinstance(data, list) else data.get("cameras", [])
            cache_entry = {"ts": now, "data": cameras}
            _catalogue_cache[host] = cache_entry
            
            # Save to disk
            try:
                with open(disk_path, "w") as f:
                    json.dump(cache_entry, f)
            except Exception as e:
                logger.warning(f"Failed to write disk cache for {host}: {e}")
                
            logger.info("Fetched %d cameras from %s", len(cameras), url)
            return cameras
        except requests.RequestException:
            continue

    if cached:
        logger.warning(f"Could not reach {host} (transient failure), serving stale cache")
        return cached["data"]

    raise HTTPException(
        status_code=502,
        detail=f"Could not reach catalogue at {host} — tried HTTPS and HTTP",
    )


def _resolve_stream_urls(host: str, camera_entry: dict) -> dict:
    """
    Given a raw catalogue entry from /api/ingest, resolve stream URLs.

    Field names from corp8.cloud catalogue:
      rtsp_url     — full RTSP URL (port 8554, often firewalled)
      webrtc_url   — full WHEP URL (port 8889, often firewalled)
      hls_live_url — relative path like "/live/stream/1/index.m3u8" (gateway may be down)

    Corp8's actual working endpoint is the progressive MP4 at /stream/{id}
    which serves video/mp4 with range requests — this is what their own
    camera.js falls back to.
    """
    cam_id = str(camera_entry.get("id", ""))
    scheme = "https" if "corp8.cloud" in host else "http"

    # RTSP — usually provided as a full URL
    rtsp = camera_entry.get("rtsp_url") or f"rtsp://{host}:8554/stream/{cam_id}"

    # WHEP — catalogue uses "webrtc_url"
    whep = camera_entry.get("webrtc_url") or camera_entry.get("whep_url") or f"http://{host}:8889/stream/{cam_id}/whep"

    # HLS — catalogue uses "hls_live_url" and it's a relative path
    hls_raw = camera_entry.get("hls_live_url") or camera_entry.get("hls_url") or f"/live/stream/{cam_id}/index.m3u8"
    if hls_raw.startswith("/"):
        hls = f"{scheme}://{host}{hls_raw}"
    else:
        hls = hls_raw

    # Progressive MP4 — the /stream/{id} endpoint serves video/mp4 with range
    # requests. This is the most reliable fallback (works on corp8.cloud even
    # when HLS/WHEP/RTSP ports are unreachable).
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
        description="Upstream host to query. Defaults to SENTINEL_SANDBOX_HOST env var.",
    ),
):
    """
    Proxies the Sentinel /api/ingest catalogue and enriches each camera entry
    with resolved RTSP, WHEP, and HLS stream URLs.

    Also returns reconnect-with-backoff parameters so the frontend knows the
    retry strategy without hardcoding it.
    """
    # Determine which hosts to query
    hosts = []
    if host:
        hosts.append(host)
    else:
        sandbox = os.getenv("SENTINEL_SANDBOX_HOST", "").strip()
        live = os.getenv("LIVE_FEED_HOST", "live.corp8.cloud").strip()
        if sandbox:
            hosts.append(sandbox)
        if live:
            hosts.append(live)

    if not hosts:
        raise HTTPException(
            status_code=400,
            detail="No feed host configured — set SENTINEL_SANDBOX_HOST or pass ?host=",
        )

    all_cameras = []
    for h in hosts:
        try:
            catalogue = _fetch_catalogue(h)
            for entry in catalogue:
                cam_id = str(entry.get("id", ""))
                urls = _resolve_stream_urls(h, entry)
                all_cameras.append({
                    "camera_id": cam_id,
                    "source_host": h,
                    "name": entry.get("name", f"Camera {cam_id}"),
                    "live": entry.get("live", entry.get("live_status", False)),
                    "codec": entry.get("codec", "unknown"),
                    "resolution": entry.get("resolution", "unknown"),
                    "streams": urls,
                    "location": entry.get("location"),
                })
        except HTTPException:
            logger.warning("Could not reach host %s — skipping", h)
            continue

    return {
        "cameras": all_cameras,
        "total": len(all_cameras),
        "sources": hosts,
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
    h = host or os.getenv("SENTINEL_SANDBOX_HOST", "") or os.getenv("LIVE_FEED_HOST", "live.corp8.cloud")
    if not h:
        raise HTTPException(status_code=400, detail="No host configured")
    catalogue = _fetch_catalogue(h)
    for entry in catalogue:
        if str(entry.get("id", "")) == camera_id:
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
    h = host or os.getenv("SENTINEL_SANDBOX_HOST", "") or os.getenv("LIVE_FEED_HOST", "live.corp8.cloud")
    if not h:
        raise HTTPException(status_code=400, detail="No host configured")
    catalogue = _fetch_catalogue(h)
    for entry in catalogue:
        if str(entry.get("id", "")) == camera_id:
            urls = _resolve_stream_urls(h, entry)
            return {"camera_id": camera_id, "whep_url": urls["whep"]}
    raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found in catalogue at {h}")
