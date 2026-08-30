import { useEffect, useState } from "react";

/**
 * Day 3 — Gap Analysis Report page.
 *
 * Fetches the rich coverage report from GET /cameras/gap-analysis and renders:
 *   - Summary stats (total cameras, coverage %, departments onboarded vs missing)
 *   - Per-department cards with online/offline/unknown counts and coverage bar
 *   - Stale cameras list (never synced)
 *   - Missing departments list (known 26 minus what's onboarded)
 *   - Export as JSON button
 */
export default function GapAnalysis() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/cameras/gap-analysis")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setReport(data);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  function exportJson() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "gap_analysis_report.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return (
      <div>
        <h2>Gap analysis report</h2>
        <p style={{ color: "#999" }}>Loading report…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h2>Gap analysis report</h2>
        <p style={{ color: "crimson" }}>Could not load report: {error}</p>
      </div>
    );
  }

  const { summary, per_department, stale_cameras, missing_departments } = report;
  const deptEntries = Object.entries(per_department || {}).sort(
    (a, b) => b[1].total - a[1].total
  );

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Gap analysis report</h2>
        <button onClick={exportJson} style={{ padding: "6px 16px", fontSize: 13 }}>
          Export JSON
        </button>
      </div>

      {/* Summary cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: 12,
          marginBottom: 24,
        }}
      >
        <SummaryCard label="Total cameras" value={summary.total_cameras} />
        <SummaryCard
          label="Coverage"
          value={`${summary.coverage_pct}%`}
          color={summary.coverage_pct >= 75 ? "#22c55e" : summary.coverage_pct >= 40 ? "#f59e0b" : "#ef4444"}
        />
        <SummaryCard label="Online" value={summary.online} color="#22c55e" />
        <SummaryCard label="Offline" value={summary.offline} color="#ef4444" />
        <SummaryCard label="Unknown" value={summary.unknown} color="#f59e0b" />
        <SummaryCard label="Depts onboarded" value={summary.departments_onboarded} />
        <SummaryCard label="Depts missing" value={summary.departments_missing} color={summary.departments_missing > 0 ? "#ef4444" : "#22c55e"} />
      </div>

      {/* Per-department breakdown */}
      <h3>Department breakdown</h3>
      {deptEntries.length === 0 ? (
        <p style={{ color: "#888" }}>No cameras onboarded yet.</p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 12,
            marginBottom: 24,
          }}
        >
          {deptEntries.map(([dept, stats]) => (
            <DeptCard key={dept} dept={dept} stats={stats} />
          ))}
        </div>
      )}

      {/* Missing departments */}
      {missing_departments && missing_departments.length > 0 && (
        <>
          <h3 style={{ color: "#ef4444" }}>
            Missing departments ({missing_departments.length})
          </h3>
          <p style={{ color: "#999", fontSize: 13, marginTop: 0 }}>
            Known Gujarat government departments with zero onboarded cameras.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 24 }}>
            {missing_departments.map((d) => (
              <span
                key={d}
                style={{
                  background: "#3b1c1c",
                  color: "#fca5a5",
                  padding: "4px 10px",
                  borderRadius: 4,
                  fontSize: 12,
                }}
              >
                {d}
              </span>
            ))}
          </div>
        </>
      )}

      {/* Stale cameras */}
      {stale_cameras && stale_cameras.length > 0 && (
        <>
          <h3 style={{ color: "#f59e0b" }}>
            Stale cameras ({stale_cameras.length})
          </h3>
          <p style={{ color: "#999", fontSize: 13, marginTop: 0 }}>
            Cameras in &quot;unknown&quot; status — never synced via /sync-status.
          </p>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 13,
              marginBottom: 24,
            }}
          >
            <thead>
              <tr style={{ borderBottom: "1px solid #333", textAlign: "left" }}>
                <th style={{ padding: 6 }}>Camera ID</th>
                <th style={{ padding: 6 }}>Name</th>
                <th style={{ padding: 6 }}>Department</th>
              </tr>
            </thead>
            <tbody>
              {stale_cameras.map((cam) => (
                <tr key={cam.camera_id} style={{ borderBottom: "1px solid #222" }}>
                  <td style={{ padding: 6, fontFamily: "monospace" }}>{cam.camera_id}</td>
                  <td style={{ padding: 6 }}>{cam.name || "—"}</td>
                  <td style={{ padding: 6 }}>{cam.department}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}


function SummaryCard({ label, value, color = "#e2e8f0" }) {
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
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 11, color: "#999", marginTop: 4, textTransform: "uppercase", letterSpacing: "0.5px" }}>
        {label}
      </div>
    </div>
  );
}


function DeptCard({ dept, stats }) {
  const pct = stats.coverage_pct || 0;
  const barColor = pct >= 75 ? "#22c55e" : pct >= 40 ? "#f59e0b" : "#ef4444";

  return (
    <div
      style={{
        background: "#1e1e2e",
        border: "1px solid #333",
        borderRadius: 8,
        padding: 14,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>{dept}</div>
      <div style={{ display: "flex", gap: 12, fontSize: 12, marginBottom: 8 }}>
        <span style={{ color: "#22c55e" }}>● {stats.online} online</span>
        <span style={{ color: "#ef4444" }}>● {stats.offline} offline</span>
        <span style={{ color: "#f59e0b" }}>● {stats.unknown} unknown</span>
      </div>
      {/* Coverage bar */}
      <div style={{ background: "#333", borderRadius: 4, height: 6, overflow: "hidden" }}>
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: barColor,
            borderRadius: 4,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <div style={{ fontSize: 11, color: "#888", marginTop: 4, textAlign: "right" }}>
        {pct}% coverage ({stats.total} total)
      </div>
    </div>
  );
}
