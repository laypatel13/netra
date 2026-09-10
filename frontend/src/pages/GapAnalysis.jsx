import { useCallback, useEffect, useState } from "react";
import { Building2, Download, Percent, Camera, CircleSlash } from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { Card, CardBody, CardHeader, SectionHeading } from "../components/ui/Card.jsx";
import Stat, { Meter } from "../components/ui/Stat.jsx";
import Button from "../components/ui/Button.jsx";
import Badge from "../components/ui/Badge.jsx";
import { EmptyState, ErrorState, SkeletonGrid } from "../components/ui/Feedback.jsx";
import { api } from "../lib/api.js";
import { count, pct } from "../lib/format.js";

/**
 * Model 1 - coverage report. Surfaces what the registry *doesn't* have as
 * prominently as what it does: a department with zero cameras is the finding,
 * not an empty row to scroll past.
 */
export default function GapAnalysis() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api("/cameras/gap-analysis")
      .then(setReport)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  function exportJson() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "gap_analysis_report.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return (
      <>
        <PageHeader title="Gap analysis" description="Loading the coverage report…" />
        <SkeletonGrid items={7} className="grid-cols-2 lg:grid-cols-4" />
      </>
    );
  }

  if (error) {
    return (
      <>
        <PageHeader title="Gap analysis" />
        <ErrorState error={error} onRetry={load} />
      </>
    );
  }

  const { summary, per_department, stale_cameras, missing_departments } = report;
  const deptEntries = Object.entries(per_department || {}).sort((a, b) => b[1].total - a[1].total);

  return (
    <>
      <PageHeader
        title="Gap analysis"
        description="Per-department coverage across the registry, plus the departments and cameras the network still can't see."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="brand">Model 1</Badge>
            <Button size="sm" variant="secondary" onClick={exportJson}>
              <Download className="h-4 w-4" aria-hidden="true" />
              Export JSON
            </Button>
          </div>
        }
      />

      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total cameras" value={count(summary.total_cameras)} icon={Camera} />
        <Stat
          label="Coverage"
          value={pct(summary.coverage_pct)}
          icon={Percent}
          tone={summary.coverage_pct >= 75 ? "ok" : summary.coverage_pct >= 40 ? "warn" : "danger"}
        />
        <Stat label="Departments onboarded" value={count(summary.departments_onboarded)} icon={Building2} />
        <Stat
          label="Departments missing"
          value={count(summary.departments_missing)}
          icon={CircleSlash}
          tone={summary.departments_missing > 0 ? "danger" : "ok"}
        />
        <Stat label="Online" value={count(summary.online)} tone="ok" />
        <Stat label="Offline" value={count(summary.offline)} tone="danger" />
        <Stat
          label="Unknown"
          value={count(summary.unknown)}
          tone="warn"
          hint="never synced"
        />
      </div>

      {/* ------------------------------------------------- Department breakdown */}
      <SectionHeading
        title="Department breakdown"
        description="Sorted by camera count. The bar shows that department's own coverage."
      />
      {deptEntries.length === 0 ? (
        <EmptyState
          title="No cameras onboarded yet"
          description="Import a camera CSV from the registry page to populate this report."
          className="mb-8"
        />
      ) : (
        <div className="mb-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {deptEntries.map(([dept, stats]) => (
            <div key={dept} className="rounded-2xl border border-line bg-surface p-4 shadow-card">
              <div className="flex items-start justify-between gap-3">
                <h3 className="text-[14px] font-medium leading-snug text-ink">{dept}</h3>
                <span className="tnum shrink-0 text-[13px] font-medium text-ink-2">
                  {pct(stats.coverage_pct || 0)}
                </span>
              </div>

              <div className="tnum mt-3 flex flex-wrap gap-x-4 gap-y-1 text-2xs">
                <span className="inline-flex items-center gap-1.5 text-ok">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
                  {count(stats.online)} online
                </span>
                <span className="inline-flex items-center gap-1.5 text-danger">
                  <span className="h-1.5 w-1.5 rounded-full bg-danger" aria-hidden="true" />
                  {count(stats.offline)} offline
                </span>
                <span className="inline-flex items-center gap-1.5 text-warn">
                  <span className="h-1.5 w-1.5 rounded-full bg-warn" aria-hidden="true" />
                  {count(stats.unknown)} unknown
                </span>
              </div>

              <Meter value={stats.coverage_pct || 0} label={`${dept} coverage`} className="mt-3" />
              <p className="tnum mt-1.5 text-2xs text-ink-3">
                {count(stats.total)} camera{stats.total === 1 ? "" : "s"} total
              </p>
            </div>
          ))}
        </div>
      )}

      {/* ------------------------------------------------- Missing departments */}
      {missing_departments?.length > 0 && (
        <Card className="mb-6">
          <CardHeader
            title={`Missing departments (${missing_departments.length})`}
            description="Known Gujarat government departments with zero onboarded cameras. This gap is the finding - it's what the registry exists to surface."
          />
          <CardBody>
            <ul className="flex flex-wrap gap-2">
              {missing_departments.map((d) => (
                <li key={d}>
                  <Badge tone="danger">{d}</Badge>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {/* ------------------------------------------------------ Stale cameras */}
      {stale_cameras?.length > 0 && (
        <Card>
          <CardHeader
            title={`Stale cameras (${stale_cameras.length})`}
            description="Status “unknown” - never synced. The sandbox feed doesn't publish a live-status field, so these stay unknown rather than being reported as confirmed offline."
          />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-[13px]">
              <caption className="sr-only">Cameras that have never reported a connectivity status</caption>
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase tracking-[0.06em] text-ink-3">
                  <th scope="col" className="px-5 py-2.5 font-semibold">Camera ID</th>
                  <th scope="col" className="px-5 py-2.5 font-semibold">Name</th>
                  <th scope="col" className="px-5 py-2.5 font-semibold">Department</th>
                </tr>
              </thead>
              <tbody>
                {stale_cameras.map((cam) => (
                  <tr key={cam.camera_id} className="border-b border-line/60 last:border-0 hover:bg-surface-2/60">
                    <td className="px-5 py-2.5 font-mono text-ink">{cam.camera_id}</td>
                    <td className="px-5 py-2.5 text-ink-2">{cam.name || "-"}</td>
                    <td className="px-5 py-2.5 text-ink-2">{cam.department}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
