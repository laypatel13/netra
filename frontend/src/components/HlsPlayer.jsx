import { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";

/**
 * Reusable video player component with HLS → progressive MP4 fallback,
 * plus reconnect-with-backoff (plan.md Section 8).
 *
 * Playback priority:
 *   1. HLS via hls.js (if src provided and Hls.isSupported())
 *   2. Native HLS (Safari — if src provided)
 *   3. Progressive MP4 (mp4Src — range-request streaming, the corp8.cloud fallback)
 *
 * Props:
 *   src        — HLS playlist URL
 *   mp4Src     — Progressive MP4 stream URL (e.g. https://live.corp8.cloud/stream/1)
 *   cameraId   — displayed in the tile overlay
 *   reconnect  — { initial_ms, max_ms, multiplier }
 */

const STATUS = {
  CONNECTING: "connecting",
  LIVE: "live",
  RECONNECTING: "reconnecting",
  FAILED: "failed",
};

const STATUS_COLORS = {
  [STATUS.CONNECTING]: "#f59e0b",
  [STATUS.LIVE]: "#22c55e",
  [STATUS.RECONNECTING]: "#f97316",
  [STATUS.FAILED]: "#ef4444",
};

export default function HlsPlayer({
  src,
  mp4Src,
  cameraId = "",
  reconnect = { initial_ms: 2000, max_ms: 30000, multiplier: 2 },
}) {
  const videoRef = useRef(null);
  const hlsRef = useRef(null);
  const retryTimeoutRef = useRef(null);
  const backoffRef = useRef(reconnect.initial_ms);
  const hlsFailedRef = useRef(false);
  const [status, setStatus] = useState(STATUS.CONNECTING);

  const cleanup = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
  }, []);

  const scheduleRetry = useCallback((connectFn) => {
    const delay = backoffRef.current;
    backoffRef.current = Math.min(
      delay * reconnect.multiplier,
      reconnect.max_ms
    );
    retryTimeoutRef.current = setTimeout(connectFn, delay);
  }, [reconnect.multiplier, reconnect.max_ms]);

  /** Try progressive MP4 as fallback */
  const connectMp4 = useCallback(() => {
    const video = videoRef.current;
    if (!mp4Src || !video) {
      setStatus(STATUS.FAILED);
      return;
    }
    setStatus(STATUS.CONNECTING);
    video.src = mp4Src;
    video.load();

    const onLoaded = () => {
      setStatus(STATUS.LIVE);
      backoffRef.current = reconnect.initial_ms;
      video.play().catch(() => {});
      video.removeEventListener("loadedmetadata", onLoaded);
      video.removeEventListener("error", onError);
    };
    const onError = () => {
      setStatus(STATUS.RECONNECTING);
      video.removeEventListener("loadedmetadata", onLoaded);
      video.removeEventListener("error", onError);
      scheduleRetry(connectMp4);
    };
    video.addEventListener("loadedmetadata", onLoaded);
    video.addEventListener("error", onError);
  }, [mp4Src, reconnect.initial_ms, scheduleRetry]);

  /** Try HLS first, fall back to MP4 on failure */
  const connect = useCallback(() => {
    cleanup();
    const video = videoRef.current;
    if (!video) return;

    // If HLS previously failed fatally, go straight to MP4
    if (hlsFailedRef.current || !src) {
      connectMp4();
      return;
    }

    // Native HLS (Safari)
    if (!Hls.isSupported() && video.canPlayType("application/vnd.apple.mpegurl")) {
      setStatus(STATUS.CONNECTING);
      video.src = src;
      video.addEventListener("loadedmetadata", () => {
        setStatus(STATUS.LIVE);
        backoffRef.current = reconnect.initial_ms;
        video.play().catch(() => {});
      }, { once: true });
      video.addEventListener("error", () => {
        // HLS failed natively, fall back to MP4
        hlsFailedRef.current = true;
        connectMp4();
      }, { once: true });
      return;
    }

    if (!Hls.isSupported()) {
      // No HLS support at all — try MP4 directly
      connectMp4();
      return;
    }

    setStatus(STATUS.CONNECTING);
    const hls = new Hls({
      enableWorker: true,
      lowLatencyMode: true,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 6,
      maxBufferLength: 10,
      maxMaxBufferLength: 30,
    });
    hlsRef.current = hls;

    hls.loadSource(src);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      setStatus(STATUS.LIVE);
      backoffRef.current = reconnect.initial_ms;
      video.play().catch(() => {});
    });

    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) {
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            // HLS gateway is unreachable — fall back to MP4
            hls.destroy();
            hlsRef.current = null;
            hlsFailedRef.current = true;
            connectMp4();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            // Decoder warnings on join are normal (plan.md Section 8)
            hls.recoverMediaError();
            break;
          default:
            hls.destroy();
            hlsRef.current = null;
            hlsFailedRef.current = true;
            connectMp4();
            break;
        }
      }
    });
  }, [src, reconnect.initial_ms, cleanup, connectMp4]);

  useEffect(() => {
    connect();
    return cleanup;
  }, [connect, cleanup]);

  return (
    <div
      style={{
        position: "relative",
        background: "#111",
        borderRadius: 8,
        overflow: "hidden",
        aspectRatio: "16/9",
      }}
    >
      <video
        ref={videoRef}
        muted
        playsInline
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />

      {/* Status badge */}
      <div
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          display: "flex",
          alignItems: "center",
          gap: 6,
          background: "rgba(0,0,0,0.7)",
          padding: "3px 10px",
          borderRadius: 12,
          fontSize: 11,
          fontWeight: 600,
          color: STATUS_COLORS[status],
          textTransform: "uppercase",
          letterSpacing: "0.5px",
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: STATUS_COLORS[status],
            display: "inline-block",
            animation: status === STATUS.LIVE ? "pulse-dot 2s infinite" : "none",
          }}
        />
        {status}
      </div>

      {/* Camera ID overlay */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          background: "linear-gradient(transparent, rgba(0,0,0,0.8))",
          padding: "16px 10px 8px",
          fontSize: 12,
          color: "#ddd",
          fontWeight: 500,
        }}
      >
        {cameraId || "Unknown"}
      </div>

      <style>{`
        @keyframes pulse-dot {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
