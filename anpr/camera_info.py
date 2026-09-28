"""
Camera information abstraction for NETRA pipeline.

Provides a clean internal representation of a camera source, decoupling
the rest of the pipeline from raw catalogue JSON structures.

Camera sources can be:
  - Live CCTV streams (RTSP/HLS/MP4 from the Sentinel catalogue)
  - Local video files (for testing)

Timestamp Semantics (Phase 1 documentation):
    1. Source PTS (pts_ms):
       From cv2.CAP_PROP_POS_MSEC — relative to stream/file start.
       NOT comparable across cameras. Each camera/stream has its own
       PTS epoch (typically when the RTSP connection started, or file
       byte 0). Do NOT use for cross-camera chronology.

    2. Ingestion timestamp (ingested_at):
       System wall-clock (time.time()) when the frame was grabbed from
       the capture device. Comparable across cameras on the same machine.
       Use for cross-camera ordering in Phase 1.

    3. Processing timestamp (processed_at):
       System wall-clock when the frame completed detector inference.
       Always >= ingested_at. The gap (processed_at - ingested_at)
       indicates processing latency.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StreamProtocol(str, Enum):
    """Supported stream/source types."""
    RTSP = "rtsp"
    HLS = "hls"
    MP4 = "mp4"       # progressive download / direct URL
    LOCAL = "local"    # local file on disk


@dataclass
class CameraInfo:
    """
    Internal representation of a camera source.

    All pipeline components consume this instead of raw catalogue dicts.
    """
    camera_id: str
    stream_url: str
    protocol: StreamProtocol
    enabled: bool = True

    # Location metadata (from catalogue or manual config)
    source_host: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Stream metadata (from catalogue, may be unknown)
    resolution: Optional[str] = None      # e.g. "1920x1080"
    fps: Optional[float] = None           # source FPS if known
    codec: Optional[str] = None

    # Additional catalogue metadata preserved as-is
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_catalogue_entry(
        cls,
        entry: dict,
        streams: dict,
        source_host: str,
    ) -> "CameraInfo":
        """
        Create a CameraInfo from a feed catalogue entry.

        Parameters:
            entry: the camera dict from /feeds/catalogue response
            streams: the resolved stream URLs dict (rtsp, hls, mp4 keys)
            source_host: the upstream host this camera comes from
        """
        camera_id = entry.get("camera_id", str(entry.get("id", "")))

        # Pick the best available stream URL in priority order
        stream_url = ""
        protocol = StreamProtocol.RTSP
        for proto_key, proto_enum in [
            ("rtsp", StreamProtocol.RTSP),
            ("mp4", StreamProtocol.MP4),
            ("hls", StreamProtocol.HLS),
        ]:
            url = streams.get(proto_key)
            if url:
                stream_url = url
                protocol = proto_enum
                break

        # Parse resolution
        width = entry.get("width", 0)
        height = entry.get("height", 0)
        resolution = f"{width}x{height}" if width and height else None

        return cls(
            camera_id=camera_id,
            stream_url=stream_url,
            protocol=protocol,
            enabled=entry.get("live", entry.get("live_status", True)),
            source_host=source_host,
            location_name=entry.get("name", entry.get("location")),
            resolution=resolution,
            codec=entry.get("codec"),
            extra=entry,
        )

    @classmethod
    def from_local_video(
        cls,
        video_path: str,
        camera_id: str = "video_test",
    ) -> "CameraInfo":
        """
        Create a CameraInfo for a local video file.

        The local video passes through the same pipeline abstraction
        as a real camera stream.
        """
        return cls(
            camera_id=camera_id,
            stream_url=video_path,
            protocol=StreamProtocol.LOCAL,
            enabled=True,
            source_host="local",
            location_name=f"Local video: {video_path}",
        )

    def to_legacy_dict(self) -> dict:
        """
        Convert back to the raw dict format expected by existing
        open_capture() / CameraWorker code during the transition period.

        This allows incremental adoption — new code uses CameraInfo,
        old code can still receive the dict it expects.
        """
        d = {
            "camera_id": self.camera_id,
            "streams": {},
        }

        if self.protocol == StreamProtocol.LOCAL:
            d["local_path"] = self.stream_url
        elif self.protocol == StreamProtocol.RTSP:
            d["streams"]["rtsp"] = self.stream_url
        elif self.protocol == StreamProtocol.HLS:
            d["streams"]["hls"] = self.stream_url
        elif self.protocol == StreamProtocol.MP4:
            d["streams"]["mp4"] = self.stream_url

        return d
