import { useEffect, useState } from "react";
import PageHeader from "../components/PageHeader.jsx";
import { SkeletonGrid, ErrorState } from "../components/ui/Feedback.jsx";
import { Card, CardHeader, CardBody } from "../components/ui/Card.jsx";
import Stat from "../components/ui/Stat.jsx";
import { api } from "../lib/api.js";

export default function DatasetHealth() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api("/investigations/prediction-readiness")
      .then((data) => {
        if (alive) {
          setReport(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (alive) {
          console.error(err);
          setError(err);
          setLoading(false);
        }
      });
    return () => { alive = false; };
  }, []);

  if (loading) {
    return <SkeletonGrid items={4} className="sm:grid-cols-2 lg:grid-cols-4 p-4 lg:p-8" />;
  }

  if (error) {
    return <ErrorState error={error} className="p-4 lg:p-8" />;
  }

  if (!report || !report.metrics) {
    return <div className="p-4 lg:p-8 text-gray-400">Loading or invalid data...</div>;
  }

  const { metrics, distributions, status, reasons } = report;

  return (
    <>
      <PageHeader title="Dataset Health" description="Operational visibility into dataset readiness for ML prediction." />
      <div className="p-4 lg:p-8">
        <div className="mb-8">
          <h2 className="text-xl font-semibold mb-2">Readiness Status: <span className={status === "READY_FOR_BENCHMARKING" ? "text-green-500" : "text-amber-500"}>{status.replace(/_/g, ' ')}</span></h2>
          <ul className="list-disc pl-5">
            {reasons.map((reason, i) => (
              <li key={i} className="text-gray-400">{reason}</li>
            ))}
          </ul>
        </div>

        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 mt-8">Transition Funnel</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <Stat label="Total Transitions" value={metrics.total_transitions} />
          <Stat label="Real Eligible" value={metrics.real_eligible_count} />
          <Stat label="Real Ineligible" value={metrics.real_ineligible_count} />
          <Stat label="Real Unknown" value={metrics.real_unknown_count} />
          <Stat label="Rejected Transitions" value={metrics.rejected_count} />
          <Stat label="Collection Rate (per hour)" value={metrics.real_transitions_per_hour ? metrics.real_transitions_per_hour.toFixed(2) : "0.00"} />
          <Stat label="Usable Source Time (hrs)" value={metrics.source_time_span_seconds ? (metrics.source_time_span_seconds / 3600.0).toFixed(2) : "0.00"} />
        </div>

        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 mt-8">Temporal Sync & Provenance</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <Stat label="Unique Recording Sessions" value={metrics.unique_recording_sessions || 0} />
          <Stat label="Synchronized Sessions" value={metrics.synchronized_session_count || 0} />
          <Stat label="Unsynchronized Sessions" value={metrics.unsynchronized_session_count || 0} />
          <Stat label="Unknown Provenance" value={metrics.unknown_provenance_count || 0} />
        </div>

        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 mt-8">Deduplication & Diversity</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <Stat label="Unique Source Events" value={metrics.unique_source_event_count || 0} />
          <Stat label="Repeated Events (Replays)" value={metrics.repeated_event_count || 0} />
          <Stat label="Suspected Playback Loops" value={metrics.suspected_loop_count || 0} />
          <Stat label="Camera Coverage" value={metrics.camera_coverage || 0} />
          <Stat label="Route Diversity / Session" value={metrics.per_session_route_diversity ? metrics.per_session_route_diversity.toFixed(2) : 0} />
          <Stat label="Route Concentration" value={metrics.repeated_route_concentration ? (metrics.repeated_route_concentration * 100).toFixed(1) + '%' : '0%'} />
          <Stat label="Unreliable Timestamps" value={metrics.unreliable_timestamp_proportion ? (metrics.unreliable_timestamp_proportion * 100).toFixed(1) + '%' : '0%'} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          <Card>
            <CardHeader title="Rejection Reasons" />
            <CardBody>
            {Object.entries(distributions.rejection_reasons || {}).length === 0 ? (
              <p className="text-gray-500">No rejections found.</p>
            ) : (
              <ul className="space-y-2">
                {Object.entries(distributions.rejection_reasons).map(([reason, count]) => (
                  <li key={reason} className="flex justify-between border-b border-gray-700/50 pb-2">
                    <span className="text-gray-400 text-sm truncate pr-2">{reason}</span>
                    <span className="font-mono text-gray-200">{count}</span>
                  </li>
                ))}
              </ul>
            )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Timestamp Quality" />
            <CardBody>
            {Object.entries(distributions.timestamp_quality || {}).length === 0 ? (
              <p className="text-gray-500">No eligible data.</p>
            ) : (
              <ul className="space-y-2">
                {Object.entries(distributions.timestamp_quality).map(([quality, count]) => (
                  <li key={quality} className="flex justify-between border-b border-gray-700/50 pb-2">
                    <span className="text-gray-400 capitalize">{quality}</span>
                    <span className="font-mono text-gray-200">{count}</span>
                  </li>
                ))}
              </ul>
            )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Provenance Breakdown" />
            <CardBody>
            {Object.entries(distributions.provenance_breakdown || {}).length === 0 ? (
              <p className="text-gray-500">No provenance data.</p>
            ) : (
              <ul className="space-y-2">
                {Object.entries(distributions.provenance_breakdown).map(([prov, count]) => (
                  <li key={prov} className="flex justify-between border-b border-gray-700/50 pb-2">
                    <span className="text-gray-400 text-sm truncate pr-2" title={prov}>{prov}</span>
                    <span className="font-mono text-gray-200">{count}</span>
                  </li>
                ))}
              </ul>
            )}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
