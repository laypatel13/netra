import { useState } from "react";

/**
 * Unified control room — the convergence point of Model 1 (registry/GIS)
 * and Model 2 (live viewing/analytics). Wire up:
 *   - camera list summary (GET /cameras)
 *   - active alerts feed (Day 5 — watchlist matches)
 *   - plate search -> GET /detections/route/{plate} for the route view
 */
export default function Dashboard() {
  const [plate, setPlate] = useState("");
  const [route, setRoute] = useState(null);
  const [error, setError] = useState(null);

  async function searchPlate(e) {
    e.preventDefault();
    setError(null);
    setRoute(null);
    try {
      const res = await fetch(`/api/detections/route/${encodeURIComponent(plate)}`);
      if (!res.ok) {
        setError(`No route found for "${plate}"`);
        return;
      }
      setRoute(await res.json());
    } catch {
      setError("Could not reach the backend — is it running?");
    }
  }

  return (
    <div>
      <h2>Control room</h2>

      <form onSubmit={searchPlate} style={{ marginBottom: 16 }}>
        <input
          value={plate}
          onChange={(e) => setPlate(e.target.value)}
          placeholder="Search by plate number"
          style={{ padding: 8, marginRight: 8 }}
        />
        <button type="submit" style={{ padding: 8 }}>
          Trace route
        </button>
      </form>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {route && (
        <div>
          <h3>Route for {route.plate_number}</h3>
          <ol>
            {route.stops.map((stop, i) => (
              <li key={i}>
                Camera {stop.camera_id} — pts {stop.timestamp_ms.toFixed(0)}ms — confidence{" "}
                {(stop.confidence * 100).toFixed(0)}%
              </li>
            ))}
          </ol>
        </div>
      )}

      <p style={{ color: "#666", marginTop: 32 }}>
        Active alerts feed and camera-status summary go here (Day 5+).
      </p>
    </div>
  );
}
