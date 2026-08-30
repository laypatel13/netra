import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";

/**
 * Model 1 — Registry & GIS. Metadata only, no video on this page.
 * Pulls GET /cameras/geojson and renders each camera as a marker.
 * Day 2: department filter (server-side query param) + status filter
 * (client-side, since it's cheap over an already-small result set).
 */
export default function Registry() {
  const [cameras, setCameras] = useState([]);
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    const params = new URLSearchParams();
    if (department) params.set("department", department);
    fetch(`/api/cameras/geojson?${params.toString()}`)
      .then((res) => res.json())
      .then((geojson) => setCameras(geojson.features || []))
      .catch(() => setCameras([]));
  }, [department]);

  const filtered = status
    ? cameras.filter((f) => f.properties.connectivity_status === status)
    : cameras;

  const center = [23.0225, 72.5714]; // Ahmedabad — reasonable default map center

  return (
    <div>
      <h2>Camera registry &amp; GIS</h2>

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
        </MapContainer>
      </div>
    </div>
  );
}

