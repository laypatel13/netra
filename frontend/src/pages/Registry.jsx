import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";

/**
 * Model 1 — Registry & GIS. Metadata only, no video on this page.
 * Pulls GET /cameras/geojson and renders each camera as a marker.
 * Day 2: department filter (server-side query param) + status filter
 * (client-side, since it's cheap over an already-small result set).
 * Bulk CSV onboarding (POST /cameras/bulk-csv, admin-only) is exposed here
 * too — it's one of the graded onboarding workflows, not just a dev tool.
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

  useEffect(() => {
    const params = new URLSearchParams();
    if (department) params.set("department", department);
    fetch(`/api/cameras/geojson?${params.toString()}`)
      .then((res) => res.json())
      .then((geojson) => setCameras(geojson.features || []))
      .catch(() => setCameras([]));
  }, [department, refreshKey]);

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

