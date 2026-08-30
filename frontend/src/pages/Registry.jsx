import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";

/**
 * Model 1 — Registry & GIS. Metadata only, no video on this page.
 * Pulls GET /cameras/geojson and renders each camera as a marker.
 * Filters by department are a straightforward next step once there's
 * real data to filter.
 */
export default function Registry() {
  const [cameras, setCameras] = useState([]);

  useEffect(() => {
    fetch("/api/cameras/geojson")
      .then((res) => res.json())
      .then((geojson) => setCameras(geojson.features || []))
      .catch(() => setCameras([]));
  }, []);

  const center = [23.0225, 72.5714]; // Ahmedabad — reasonable default map center

  return (
    <div>
      <h2>Camera registry &amp; GIS</h2>
      <p style={{ color: "#666" }}>
        {cameras.length} camera{cameras.length === 1 ? "" : "s"} onboarded
      </p>

      <div style={{ height: 480, width: "100%" }}>
        <MapContainer center={center} zoom={7} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            attribution='&copy; OpenStreetMap contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {cameras.map((f, i) => (
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
