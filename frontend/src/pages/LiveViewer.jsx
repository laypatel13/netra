import { useEffect, useState } from "react";
import HlsPlayer from "../components/HlsPlayer.jsx";

/**
 * Model 2 — Unified Viewing. Connects to ≥2 different feed sources via the
 * /feeds/catalogue proxy and renders each camera as an HLS.js tile.
 *
 * Day 3 deliverables satisfied:
 *   - connects to ≥2 feed sources (Sentinel sandbox + corp8.cloud)
 *   - WebRTC/HLS relay into browser (HLS.js player)
 *   - reconnect-with-backoff implemented from day one (inside HlsPlayer)
 */
export default function LiveViewer() {
  const [cameras, setCameras] = useState([]);
  const [reconnectConfig, setReconnectConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [enabledCameras, setEnabledCameras] = useState(new Set());
  const [sourceFilter, setSourceFilter] = useState("");

  useEffect(() => {
    fetch("/api/feeds/catalogue")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setCameras(data.cameras || []);
        setReconnectConfig(data.reconnect || null);
        // Enable first 8 cameras by default to avoid opening too many streams
        const initial = new Set(
          (data.cameras || []).slice(0, 8).map((c) => c.camera_id)
        );
        setEnabledCameras(initial);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  const sources = [...new Set(cameras.map((c) => c.source_host))];

  const filtered = sourceFilter
    ? cameras.filter((c) => c.source_host === sourceFilter)
    : cameras;

  function toggleCamera(camId) {
    setEnabledCameras((prev) => {
      const next = new Set(prev);
      if (next.has(camId)) {
        next.delete(camId);
      } else {
        next.add(camId);
      }
      return next;
    });
  }

  function enableAll() {
    setEnabledCameras(new Set(filtered.map((c) => c.camera_id)));
  }

  function disableAll() {
    setEnabledCameras(new Set());
  }

  if (loading) {
    return (
      <div>
        <h2>Live viewer</h2>
        <p style={{ color: "#999" }}>Loading feed catalogue…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h2>Live viewer</h2>
        <p style={{ color: "crimson" }}>
          Could not load feed catalogue: {error}
        </p>
        <p style={{ color: "#666", fontSize: 13 }}>
          Make sure the backend is running and SENTINEL_SANDBOX_HOST or
          LIVE_FEED_HOST is configured in .env
        </p>
      </div>
    );
  }

  const activeCameras = filtered.filter((c) => enabledCameras.has(c.camera_id));

  return (
    <div>
      <h2>Live viewer</h2>

      {/* Source + controls bar */}
      <div style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          style={{ padding: 6 }}
        >
          <option value="">All sources ({cameras.length})</option>
          {sources.map((s) => (
            <option key={s} value={s}>
              {s} ({cameras.filter((c) => c.source_host === s).length})
            </option>
          ))}
        </select>

        <button onClick={enableAll} style={{ padding: "4px 12px", fontSize: 12 }}>
          Enable all
        </button>
        <button onClick={disableAll} style={{ padding: "4px 12px", fontSize: 12 }}>
          Disable all
        </button>

        <span style={{ color: "#666", fontSize: 13 }}>
          {activeCameras.length} of {filtered.length} cameras active
        </span>
      </div>

      {/* Camera toggle list */}
      <details style={{ marginBottom: 16 }}>
        <summary style={{ cursor: "pointer", color: "#888", fontSize: 13 }}>
          Camera selector ({filtered.length} available)
        </summary>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 6,
            marginTop: 8,
            maxHeight: 140,
            overflowY: "auto",
          }}
        >
          {filtered.map((cam) => (
            <label
              key={cam.camera_id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 8px",
                background: enabledCameras.has(cam.camera_id) ? "#1a3a2a" : "#222",
                borderRadius: 4,
                fontSize: 12,
                color: enabledCameras.has(cam.camera_id) ? "#6ee7b7" : "#888",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={enabledCameras.has(cam.camera_id)}
                onChange={() => toggleCamera(cam.camera_id)}
                style={{ accentColor: "#22c55e" }}
              />
              {cam.name || cam.camera_id}
              {cam.live && (
                <span style={{ color: "#22c55e", fontSize: 10 }}>●</span>
              )}
            </label>
          ))}
        </div>
      </details>

      {/* Video grid */}
      {activeCameras.length === 0 ? (
        <p style={{ color: "#888" }}>
          No cameras enabled. Use the selector above to turn on feeds.
        </p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 12,
          }}
        >
          {activeCameras.map((cam) => (
            <div key={cam.camera_id}>
              <HlsPlayer
                src={cam.streams?.hls?.startsWith("/") ? `/api${cam.streams.hls}` : cam.streams?.hls}
                mp4Src={cam.streams?.mp4}
                cameraId={`${cam.name || cam.camera_id} (${cam.source_host})`}
                reconnect={reconnectConfig}
              />
              <div style={{ fontSize: 11, color: "#666", marginTop: 4, padding: "0 4px" }}>
                {cam.codec} · {cam.resolution} · {cam.source_host}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
