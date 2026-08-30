/**
 * Model 2 — Unified Viewing. Direct RTSP/ONVIF connection per camera,
 * relayed to the browser via WebRTC (WHEP) or HLS — never RTSP directly
 * in-browser. See plan.md Section 4 for endpoint patterns.
 *
 * This is a placeholder grid. Wire each tile to an HLS.js or WHEP player
 * once the backend relay (Day 3) is in place.
 */
export default function LiveViewer() {
  return (
    <div>
      <h2>Live viewer</h2>
      <p style={{ color: "#666" }}>
        Feed relay not wired up yet — this is where the WebRTC/HLS grid goes
        once the Day 3 unified-viewer relay is built.
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: 12,
        }}
      >
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            style={{
              aspectRatio: "16/9",
              background: "#222",
              color: "#888",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 8,
            }}
          >
            camera {i} — not connected
          </div>
        ))}
      </div>
    </div>
  );
}
