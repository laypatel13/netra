import { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";

/**
 * Reusable video player component with HLS → progressive MP4 fallback,
 * plus reconnect-with-backoff (PLAN.md Section 8).
 *
 * Playback priority:
 *   1. HLS via hls.js (if src provided and Hls.isSupported())
 *   2. Native HLS (Safari - if src provided)
 *   3. Progressive MP4 (mp4Src), only when the catalogue actually provides one
 *   4. If none of the above work, keep retrying HLS itself with capped
 *      backoff - this is the normal path for cctv.corp8.cloud, which has no
 *      progressive-MP4 endpoint. A join failure is never terminal (PLAN.md
 *      Section 8): reconnect forever, don't give up on a tile permanently.
 *
 * Props:
 *   src           - HLS playlist URL
 *   mp4Src        - Progressive MP4 stream URL (e.g. https://live.corp8.cloud/stream/1)
 *   cameraId      - displayed in the tile overlay
 *   reconnect     - { initial_ms, max_ms, multiplier }
 *   startDelayMs  - delay before the first connection attempt, for staggering
 *                   a grid of tiles that mount at the same time
 */

const STATUS = {
  CONNECTING: "connecting",
  LIVE: "live",
  RECONNECTING: "reconnecting",
  FAILED: "failed",
};

/**
 * Status presentation only - the connection logic below is unchanged.
 * Each status carries a word as well as a colour, so the tile still reads
 * correctly for an operator who can't distinguish amber from green.
 */
const STATUS_STYLES = {
  [STATUS.CONNECTING]: { dot: "bg-warn", text: "text-warn", label: "Connecting" },
  [STATUS.LIVE]: { dot: "bg-ok", text: "text-ok", label: "Live" },
  [STATUS.RECONNECTING]: { dot: "bg-warn", text: "text-warn", label: "Reconnecting" },
  [STATUS.FAILED]: { dot: "bg-danger", text: "text-danger", label: "Failed" },
};

export default function HlsPlayer({
  src,
  mp4Src,
  cameraId = "",
  reconnect = { initial_ms: 2000, max_ms: 30000, multiplier: 2 },
  startDelayMs = 0,
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
    if (!video) {
      // Element is gone (component unmounting) - nothing left to retry.
      setStatus(STATUS.FAILED);
      return;
    }
    if (!mp4Src) {
      // No fallback stream available - keep retrying HLS with backoff rather
      // than giving up permanently (PLAN.md Section 8: reconnect forever with
      // capped backoff, never treat a transient join failure as terminal).
      setStatus(STATUS.RECONNECTING);
      hlsFailedRef.current = false;
      scheduleRetry(connect);
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
      // No HLS support at all - try MP4 directly
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
            // HLS gateway is unreachable - fall back to MP4
            hls.destroy();
            hlsRef.current = null;
            hlsFailedRef.current = true;
            connectMp4();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            // Decoder warnings on join are normal (PLAN.md Section 8)
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
    // Stagger the very first connection per tile so opening a grid of many
    // cameras at once doesn't slam the shared authenticated session with a
    // burst of simultaneous first-segment fetches (PLAN.md Section 8: "pace
    // load"). Reconnects after that use the normal backoff timer, not this.
    const startTimeout = setTimeout(connect, startDelayMs);
    return () => {
      clearTimeout(startTimeout);
      cleanup();
    };
  }, [connect, cleanup, startDelayMs]);

  const style = STATUS_STYLES[status] || STATUS_STYLES[STATUS.CONNECTING];

  return (
    <div className="group relative aspect-video overflow-hidden rounded-xl border border-line bg-[rgb(12_12_14)]">
      <video
        ref={videoRef}
        muted
        playsInline
        className="block h-full w-full object-cover"
      />

      {/* Placeholder behind the video until a frame arrives, so a connecting
          tile reads as "working on it" instead of a dead black rectangle. */}
      {status !== STATUS.LIVE && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[rgb(12_12_14)]">
          <span className="skeleton h-10 w-10 rounded-full opacity-40" />
        </div>
      )}

      {/* Status badge */}
      <div className="absolute right-2 top-2 flex items-center gap-1.5 rounded-full bg-black/65 px-2.5 py-1 backdrop-blur-sm">
        <span
          className={`h-1.5 w-1.5 rounded-full ${style.dot} ${
            status === STATUS.LIVE ? "animate-pulse-dot motion-reduce:animate-none" : ""
          }`}
          aria-hidden="true"
        />
        <span className={`text-[10px] font-semibold uppercase tracking-[0.08em] ${style.text}`}>
          {style.label}
        </span>
      </div>

      {/* Camera ID overlay */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-3 pb-2 pt-8">
        <p className="truncate text-[12px] font-medium text-white/90" title={cameraId || "Unknown camera"}>
          {cameraId || "Unknown camera"}
        </p>
      </div>

      <span className="sr-only">
        Camera {cameraId || "unknown"}: {style.label.toLowerCase()}
      </span>
    </div>
  );
}
