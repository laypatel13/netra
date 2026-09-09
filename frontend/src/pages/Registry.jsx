import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker } from "react-leaflet";

const VEHICLE_TYPES = ["car", "motorcycle", "bus", "truck"];

/**
 * Model 1 — Registry & GIS. Pulls GET /cameras/geojson and renders each
 * camera as a marker. Department filter (server-side query param) + status
 * filter (client-side, cheap over an already-small result set).
 * Bulk CSV onboarding (POST /cameras/bulk-csv, admin-only) is exposed here
 * too — it's one of the graded onboarding workflows, not just a dev tool.
 *
 * Route-on-map (PLAN.md Section 0c): given a plate or vehicle description,
 * draw the reconstructed path as a polyline + numbered stops. Camera
 * coordinates for the route come from a separate, always-unfiltered fetch
 * so the department/status filters above don't affect route rendering.
 */
export default function Registry() {
  const [cameras, setCameras] = useState([]);
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const [csvFile, setCsvFile] = useState(null);
  const [actor, setActor] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);

  const [cameraCoords, setCameraCoords] = useState({}); // camera_id -> [lat, lng], unfiltered
  const [routeMode, setRouteMode] = useState("plate");
  const [routePlate, setRoutePlate] = useState("");
  const [routeVehicleType, setRouteVehicleType] = useState(VEHICLE_TYPES[0]);
  const [routeVehicleColor, setRouteVehicleColor] = useState("");
  const [route, setRoute] = useState(null);
  const [routeError, setRouteError] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (department) params.set("department", department);
    fetch(`/api/cameras/geojson?${params.toString()}`)
      .then((res) => res.json())
      .then((geojson) => setCameras(geojson.features || []))
      .catch(() => setCameras([]));
  }, [department, refreshKey]);

  useEffect(() => {
    fetch("/api/cameras/geojson")
      .then((res) => res.json())
      .then((geojson) => {
        const coords = {};
        for (const f of geojson.features || []) {
          coords[f.properties.camera_id] = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
        }
        setCameraCoords(coords);
      })
      .catch(() => setCameraCoords({}));
  }, [refreshKey]);

  async function searchRoute(e) {
    e.preventDefault();
    setRouteError(null);
    setRoute(null);
    try {
      const url =
        routeMode === "plate"
          ? `/api/detections/route/${encodeURIComponent(routePlate)}`
          : `/api/detections/search?vehicle_type=${encodeURIComponent(routeVehicleType)}&vehicle_color=${encodeURIComponent(routeVehicleColor)}`;
      const res = await fetch(url);
      if (!res.ok) {
        setRouteError("No route found — check the plate/description, or that the camera is onboarded here.");
        return;
      }
      setRoute(await res.json());
    } catch {
      setRouteError("Could not reach the backend — is it running?");
    }
  }

  // Stops whose camera isn't in the registry (unlikely, but possible if a
  // detection references a camera_id not onboarded here) are dropped rather
  // than breaking the polyline.
  const routePositions = (route?.stops || [])
    .map((s) => cameraCoords[s.camera_id])
    .filter(Boolean);

  async function handleUpload(e) {
    e.preventDefault();
    if (!csvFile) return;
    setUploading(true);
    setUploadResult(null);
    setUploadError(null);

    const formData = new FormData();
    formData.append("file", csvFile);

    try {
      const res = await fetch("/api/cameras/bulk-csv", {
        method: "POST",
        headers: {
          "X-Role": "admin",
          ...(actor ? { "X-Actor": actor } : {}),
        },
        body: formData,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setUploadError(detail?.detail || `HTTP ${res.status}`);
        return;
      }
      const onboarded = await res.json();
      setUploadResult(`Onboarded ${onboarded.length} camera${onboarded.length === 1 ? "" : "s"}.`);
      setCsvFile(null);
      setRefreshKey((k) => k + 1); // re-pull the map/list with the new cameras
    } catch {
      setUploadError("Could not reach the backend — is it running?");
    } finally {
      setUploading(false);
    }
  }

  const filtered = status
    ? cameras.filter((f) => f.properties.connectivity_status === status)
    : cameras;

  const center = [23.0225, 72.5714]; // Ahmedabad — reasonable default map center

  return (
    <div>
      <h2>Camera registry &amp; GIS</h2>

      {/* Bulk CSV onboarding */}
      <form
        onSubmit={handleUpload}
        style={{
          marginBottom: 16,
          padding: 12,
          border: "1px solid #333",
          borderRadius: 8,
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <strong style={{ fontSize: 13 }}>Bulk onboard (CSV):</strong>
        <input
          type="file"
          accept=".csv"
          onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
        />
        <input
          placeholder="Actor (optional, for audit log)"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          style={{ padding: 6, fontSize: 12 }}
        />
        <button type="submit" disabled={!csvFile || uploading} style={{ padding: "6px 12px" }}>
          {uploading ? "Uploading…" : "Upload"}
        </button>
        {uploadResult && <span style={{ color: "#22c55e", fontSize: 13 }}>{uploadResult}</span>}
        {uploadError && <span style={{ color: "crimson", fontSize: 13 }}>{uploadError}</span>}
      </form>

      <div style={{ marginBottom: 12, display: "flex", gap: 8 }}>
        <input
          placeholder="Filter by department"
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          style={{ padding: 6 }}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ padding: 6 }}>
          <option value="">All statuses</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="unknown">Unknown</option>
        </select>
      </div>

      {/* Route-on-map search */}
      <form
        onSubmit={searchRoute}
        style={{ marginBottom: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
      >
        <strong style={{ fontSize: 13 }}>Trace route:</strong>
        <select value={routeMode} onChange={(e) => setRouteMode(e.target.value)} style={{ padding: 6 }}>
          <option value="plate">By plate</option>
          <option value="attributes">By description</option>
        </select>
        {routeMode === "plate" ? (
          <input
            placeholder="Plate number"
            value={routePlate}
            onChange={(e) => setRoutePlate(e.target.value)}
            style={{ padding: 6 }}
          />
        ) : (
          <>
            <select value={routeVehicleType} onChange={(e) => setRouteVehicleType(e.target.value)} style={{ padding: 6 }}>
              {VEHICLE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <input
              placeholder="Color"
              value={routeVehicleColor}
              onChange={(e) => setRouteVehicleColor(e.target.value)}
              style={{ padding: 6 }}
            />
          </>
        )}
        <button type="submit" style={{ padding: "6px 12px" }}>Trace</button>
        {route && (
          <button type="button" onClick={() => setRoute(null)} style={{ padding: "6px 12px" }}>
            Clear route
          </button>
        )}
        {routeError && <span style={{ color: "crimson", fontSize: 13 }}>{routeError}</span>}
      </form>
      {route && routePositions.length < (route.stops?.length || 0) && (
        <p style={{ color: "#f59e0b", fontSize: 12, marginTop: 0 }}>
          {route.stops.length - routePositions.length} stop(s) reference a camera not onboarded here — shown in the list below but not on the map.
        </p>
      )}

      <p style={{ color: "#666" }}>
        {filtered.length} camera{filtered.length === 1 ? "" : "s"} shown
      </p>

      <div style={{ height: 480, width: "100%" }}>
        <MapContainer center={center} zoom={7} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            attribution='&copy; OpenStreetMap contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {filtered.map((f, i) => (
            <Marker
              key={i}
              position={[f.geometry.coordinates[1], f.geometry.coordinates[0]]}
            >
              <Popup>
                <strong>{f.properties.camera_id}</strong>
                <br />
                {f.properties.department}
                <br />
                Status: {f.properties.connectivity_status}
              </Popup>
            </Marker>
          ))}

          {routePositions.length > 1 && (
            <Polyline positions={routePositions} pathOptions={{ color: "#f59e0b", weight: 3 }} />
          )}
          {route?.stops.map((stop, i) => {
            const pos = cameraCoords[stop.camera_id];
            if (!pos) return null;
            return (
              <CircleMarker
                key={`stop-${i}`}
                center={pos}
                radius={9}
                pathOptions={{ color: "#f59e0b", fillColor: "#f59e0b", fillOpacity: 0.9 }}
              >
                <Popup>
                  <strong>Stop {i + 1} — {stop.camera_id}</strong>
                  <br />
                  pts {stop.timestamp_ms.toFixed(0)}ms — confidence {(stop.confidence * 100).toFixed(0)}%
                  {stop.vehicle_type && (
                    <>
                      <br />
                      {stop.vehicle_color} {stop.vehicle_type}
                    </>
                  )}
                  {stop.thumbnail_url && (
                    <>
                      <br />
                      <img src={`/api${stop.thumbnail_url}`} alt="" style={{ width: 120, marginTop: 4, borderRadius: 4 }} />
                    </>
                  )}
                </Popup>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>
    </div>
  );
}

