import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import HlsPlayer from "../components/HlsPlayer.jsx";

const PREVIEW_TILE_COUNT = 4;

/**
 * Unified control room — the convergence point of Model 1 (registry/GIS)
 * and Model 2 (live viewing/analytics), per PLAN.md Section 2's system
 * diagram. Pulls together:
 *   - coverage summary (GET /cameras/gap-analysis)
 *   - a small live preview grid (GET /feeds/catalogue)
 *   - plate or vehicle-description search -> GET /detections/route/{plate}
 *     or /detections/search for the route/candidate-sightings view
 *   - watchlist size (GET /watchlist) — full management + live alerts feed
 *     lives on the Watchlist page
 */
const VEHICLE_TYPES = ["car", "motorcycle", "bus", "truck"];

export default function Dashboard() {
  const [searchMode, setSearchMode] = useState("plate"); // "plate" | "attributes"
  const [plate, setPlate] = useState("");
  const [vehicleType, setVehicleType] = useState(VEHICLE_TYPES[0]);
  const [vehicleColor, setVehicleColor] = useState("");
  const [route, setRoute] = useState(null);
  const [routeError, setRouteError] = useState(null);

  const [gap, setGap] = useState(null);
  const [gapError, setGapError] = useState(null);
  const [previewCameras, setPreviewCameras] = useState([]);
  const [reconnectConfig, setReconnectConfig] = useState(null);
  const [watchlistCount, setWatchlistCount] = useState(null);

  useEffect(() => {
    fetch("/api/cameras/gap-analysis")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setGap)
      .catch((e) => setGapError(e.message));

    fetch("/api/feeds/catalogue")
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data) => {
        setPreviewCameras((data.cameras || []).slice(0, PREVIEW_TILE_COUNT));
        setReconnectConfig(data.reconnect || null);
      })
      .catch(() => setPreviewCameras([]));

    fetch("/api/watchlist")
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then((list) => setWatchlistCount(list.length))
      .catch(() => setWatchlistCount(null));
  }, []);

  async function searchRoute(e) {
    e.preventDefault();
    setRouteError(null);
    setRoute(null);
    try {
      const url =
        searchMode === "plate"
          ? `/api/detections/route/${encodeURIComponent(plate)}`
          : `/api/detections/search?vehicle_type=${encodeURIComponent(vehicleType)}&vehicle_color=${encodeURIComponent(vehicleColor)}`;
      const res = await fetch(url);
      if (!res.ok) {
        setRouteError(
          searchMode === "plate"
            ? `No route found for "${plate}"`
            : `No sightings found for ${vehicleColor} ${vehicleType}`
        );
        return;
      }
      setRoute(await res.json());
    } catch {
      setRouteError("Could not reach the backend — is it running?");
    }
  }

  return (
    <div>
      <h2>Control room</h2>

      {/* Coverage summary strip */}
      {gapError ? (
        <p style={{ color: "crimson", fontSize: 13 }}>Could not load coverage summary: {gapError}</p>
      ) : gap ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 12,
            marginBottom: 24,
          }}
        >
          <StatCard label="Cameras onboarded" value={gap.summary.total_cameras} />
          <StatCard
            label="Coverage"
            value={`${gap.summary.coverage_pct}%`}
            color={gap.summary.coverage_pct >= 75 ? "#22c55e" : gap.summary.coverage_pct >= 40 ? "#f59e0b" : "#ef4444"}
          />
          <StatCard
            label="Depts missing"
            value={gap.summary.departments_missing}
            color={gap.summary.departments_missing > 0 ? "#ef4444" : "#22c55e"}
          />
          <StatCard label="Watchlist entries" value={watchlistCount ?? "—"} />
          <Link to="/gap-analysis" style={{ alignSelf: "center", fontSize: 13, color: "#60a5fa" }}>
            Full gap-analysis report →
          </Link>
        </div>
      ) : (
        <p style={{ color: "#999", fontSize: 13 }}>Loading coverage summary…</p>
      )}

      {/* Live preview grid */}
      <h3 style={{ marginBottom: 8 }}>Live preview</h3>
      {previewCameras.length === 0 ? (
        <p style={{ color: "#888", fontSize: 13 }}>
          No feeds available — check the backend / <code>CCTV_EMAIL</code> &amp;{" "}
          <code>CCTV_PASSWORD</code> config, or open the{" "}
          <Link to="/live">full Live Viewer</Link>.
        </p>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 10,
              marginBottom: 8,
            }}
          >
            {previewCameras.map((cam, i) => (
              <HlsPlayer
                key={cam.camera_id}
                src={cam.streams?.hls?.startsWith("/") ? `/api${cam.streams.hls}` : cam.streams?.hls}
                mp4Src={cam.streams?.mp4}
                cameraId={cam.name || cam.camera_id}
                reconnect={reconnectConfig}
                startDelayMs={i * 250}
              />
            ))}
          </div>
          <p style={{ marginTop: 0, marginBottom: 24 }}>
            <Link to="/live" style={{ fontSize: 13, color: "#60a5fa" }}>
              Open full Live Viewer ({gap?.summary.total_cameras ?? "all"} cameras) →
            </Link>
          </p>
        </>
      )}

      {/* Plate / attribute search / route trace */}
      <h3 style={{ marginBottom: 8 }}>Trace a vehicle</h3>
      <form onSubmit={searchRoute} style={{ marginBottom: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select value={searchMode} onChange={(e) => setSearchMode(e.target.value)} style={{ padding: 8 }}>
          <option value="plate">By plate number</option>
          <option value="attributes">By vehicle description (no plate known)</option>
        </select>

        {searchMode === "plate" ? (
          <input
            value={plate}
            onChange={(e) => setPlate(e.target.value)}
            placeholder="Search by plate number"
            style={{ padding: 8 }}
          />
        ) : (
          <>
            <select value={vehicleType} onChange={(e) => setVehicleType(e.target.value)} style={{ padding: 8 }}>
              {VEHICLE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <input
              value={vehicleColor}
              onChange={(e) => setVehicleColor(e.target.value)}
              placeholder="Color (e.g. red)"
              style={{ padding: 8 }}
            />
          </>
        )}

        <button type="submit" style={{ padding: 8 }}>
          Trace route
        </button>
      </form>
      {searchMode === "attributes" && (
        <p style={{ color: "#888", fontSize: 12, marginTop: 0, marginBottom: 16 }}>
          A narrowing tool, not identification — other vehicles may share the same type/color.
          Cross-check against the thumbnails below.
        </p>
      )}

      {routeError && <p style={{ color: "crimson" }}>{routeError}</p>}

      {route && (
        <div style={{ marginBottom: 16 }}>
          <h4>{route.plate_number ? `Route for ${route.plate_number}` : `Candidate sightings: ${route.query}`}</h4>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {route.stops.map((stop, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                  background: "#1e1e2e",
                  border: "1px solid #333",
                  borderRadius: 8,
                  padding: 10,
                }}
              >
                <div style={{ width: 24, textAlign: "center", color: "#666", fontSize: 13 }}>{i + 1}</div>
                {stop.thumbnail_url && (
                  <img
                    src={`/api${stop.thumbnail_url}`}
                    alt=""
                    style={{ width: 70, height: 52, objectFit: "cover", borderRadius: 4 }}
                  />
                )}
                <div style={{ fontSize: 13 }}>
                  Camera <strong>{stop.camera_id}</strong> — pts {stop.timestamp_ms.toFixed(0)}ms — confidence{" "}
                  {(stop.confidence * 100).toFixed(0)}%
                  {stop.vehicle_type && (
                    <>
                      {" — "}
                      {stop.vehicle_color} {stop.vehicle_type}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
          <p style={{ marginTop: 8 }}>
            <Link to="/registry" style={{ fontSize: 13, color: "#60a5fa" }}>
              View this route on the GIS map →
            </Link>
          </p>
        </div>
      )}

      <p style={{ color: "#666", marginTop: 16, fontSize: 13 }}>
        <Link to="/watchlist" style={{ color: "#60a5fa" }}>Manage the watchlist and see the live alerts feed →</Link>
      </p>
    </div>
  );
}

function StatCard({ label, value, color = "#e2e8f0" }) {
  return (
    <div
      style={{
        background: "#1e1e2e",
        border: "1px solid #333",
        borderRadius: 8,
        padding: 14,
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 11, color: "#999", marginTop: 4, textTransform: "uppercase", letterSpacing: "0.5px" }}>
        {label}
      </div>
    </div>
  );
}
