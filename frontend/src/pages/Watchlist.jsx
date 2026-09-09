import { useEffect, useState } from "react";

const VEHICLE_TYPES = ["car", "motorcycle", "bus", "truck"];
const CATEGORIES = ["stolen", "suspect", "blacklisted"];

/**
 * Watchlist management + real-time-ish alert feed (PLAN.md Section 0c).
 *
 * An entry needs plate_number OR (vehicle_type AND vehicle_color) — the
 * attribute mode exists for exactly the "suspect vehicle, no known plate"
 * case (PLAN.md Section 0b). Alerts are tiered: an exact-plate match is
 * precise, an attributes match is a narrowing tool, not identification —
 * shown with distinct badges so nobody reads them as equally certain.
 */
export default function Watchlist() {
  const [mode, setMode] = useState("plate"); // "plate" | "attributes"
  const [plateNumber, setPlateNumber] = useState("");
  const [vehicleType, setVehicleType] = useState(VEHICLE_TYPES[0]);
  const [vehicleColor, setVehicleColor] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const [entries, setEntries] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [alertsError, setAlertsError] = useState(null);

  function loadEntries() {
    fetch("/api/watchlist")
      .then((res) => res.json())
      .then(setEntries)
      .catch(() => setEntries([]));
  }

  function loadAlerts() {
    fetch("/api/watchlist/alerts/recent")
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data) => {
        setAlerts(data);
        setAlertsError(null);
      })
      .catch((e) => setAlertsError(e.message));
  }

  useEffect(() => {
    loadEntries();
    loadAlerts();
    // Short-poll for a real-time-ish feed — no websocket layer needed at
    // this scale (PLAN.md Section 0c).
    const interval = setInterval(loadAlerts, 5000);
    return () => clearInterval(interval);
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);

    const body =
      mode === "plate"
        ? { plate_number: plateNumber, category }
        : { vehicle_type: vehicleType, vehicle_color: vehicleColor, category };

    try {
      const res = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setSubmitError(detail?.detail?.[0]?.msg || detail?.detail || `HTTP ${res.status}`);
        return;
      }
      setPlateNumber("");
      setVehicleColor("");
      loadEntries();
    } catch {
      setSubmitError("Could not reach the backend — is it running?");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h2>Watchlist</h2>

      {/* Add entry */}
      <form
        onSubmit={handleSubmit}
        style={{
          marginBottom: 24,
          padding: 12,
          border: "1px solid #333",
          borderRadius: 8,
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ padding: 6 }}>
          <option value="plate">By plate number</option>
          <option value="attributes">By vehicle description (no plate known)</option>
        </select>

        {mode === "plate" ? (
          <input
            placeholder="Plate number"
            value={plateNumber}
            onChange={(e) => setPlateNumber(e.target.value)}
            required
            style={{ padding: 6 }}
          />
        ) : (
          <>
            <select value={vehicleType} onChange={(e) => setVehicleType(e.target.value)} style={{ padding: 6 }}>
              {VEHICLE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <input
              placeholder="Color (e.g. red)"
              value={vehicleColor}
              onChange={(e) => setVehicleColor(e.target.value)}
              required
              style={{ padding: 6 }}
            />
          </>
        )}

        <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ padding: 6 }}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <button type="submit" disabled={submitting} style={{ padding: "6px 12px" }}>
          {submitting ? "Adding…" : "Add to watchlist"}
        </button>
        {submitError && <span style={{ color: "crimson", fontSize: 13 }}>{submitError}</span>}
      </form>

      {/* Current entries */}
      <h3 style={{ marginBottom: 8 }}>Entries ({entries.length})</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 24 }}>
        {entries.map((e) => (
          <span
            key={e.id}
            style={{
              background: "#1e1e2e",
              border: "1px solid #333",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 13,
            }}
          >
            {e.plate_number ? (
              <strong>{e.plate_number}</strong>
            ) : (
              <span>{e.vehicle_color} {e.vehicle_type}</span>
            )}
            {" — "}
            <span style={{ color: "#f59e0b" }}>{e.category}</span>
          </span>
        ))}
        {entries.length === 0 && <p style={{ color: "#888" }}>No entries yet.</p>}
      </div>

      {/* Alerts feed */}
      <h3 style={{ marginBottom: 8 }}>Recent alerts</h3>
      {alertsError && <p style={{ color: "crimson", fontSize: 13 }}>Could not load alerts: {alertsError}</p>}
      {alerts.length === 0 && !alertsError && (
        <p style={{ color: "#888", fontSize: 13 }}>No watchlist matches among recent detections.</p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {alerts.map((a, i) => (
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
            {a.detection.thumbnail_url && (
              <img
                src={`/api${a.detection.thumbnail_url}`}
                alt=""
                style={{ width: 80, height: 60, objectFit: "cover", borderRadius: 4 }}
              />
            )}
            <div style={{ flex: 1, fontSize: 13 }}>
              <span
                style={{
                  display: "inline-block",
                  padding: "2px 8px",
                  borderRadius: 10,
                  fontSize: 11,
                  fontWeight: 600,
                  marginRight: 8,
                  background: a.tier === "exact_plate" ? "#14532d" : "#7c2d12",
                  color: a.tier === "exact_plate" ? "#86efac" : "#fdba74",
                }}
              >
                {a.tier === "exact_plate" ? "EXACT PLATE MATCH" : "POSSIBLE MATCH — BY DESCRIPTION"}
              </span>
              {a.detection.plate_number || `${a.detection.vehicle_color || "?"} ${a.detection.vehicle_type || "vehicle"}`}
              {" on "}
              <strong>{a.detection.camera_id}</strong>
              {" — watchlist: "}
              <span style={{ color: "#f59e0b" }}>{a.watchlist_entry.category}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
