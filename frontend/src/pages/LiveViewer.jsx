import { useCallback, useEffect, useMemo, useState } from "react";
import { Radio, VideoOff } from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { Card, CardBody, CardHeader } from "../components/ui/Card.jsx";
import Button from "../components/ui/Button.jsx";
import Badge from "../components/ui/Badge.jsx";
import Field, { Input, Select } from "../components/ui/Field.jsx";
import { EmptyState, ErrorState, SkeletonGrid } from "../components/ui/Feedback.jsx";
import HlsPlayer from "../components/HlsPlayer.jsx";
import { api } from "../lib/api.js";
import { count } from "../lib/format.js";

const DEFAULT_ENABLED = 8;

/**
 * Model 2 - Unified Viewing. Connects to more than one feed source through the
 * /feeds/catalogue proxy and renders each camera as an HLS tile.
 *
 * Tiles stagger their first connection (250ms apart) because opening a grid of
 * them in the same tick used to slam the one shared authenticated session and
 * kill most of the grid - see PLAN.md Section 0a. Don't remove startDelayMs.
 */
export default function LiveViewer() {
  const [cameras, setCameras] = useState([]);
  const [reconnectConfig, setReconnectConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [enabled, setEnabled] = useState(new Set());
  const [sourceFilter, setSourceFilter] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api("/feeds/catalogue")
      .then((data) => {
        const list = data.cameras || [];
        setCameras(list);
        setReconnectConfig(data.reconnect || null);
        setEnabled(new Set(list.slice(0, DEFAULT_ENABLED).map((c) => c.camera_id)));
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const sources = useMemo(() => [...new Set(cameras.map((c) => c.source_host))], [cameras]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cameras.filter(
      (c) =>
        (!sourceFilter || c.source_host === sourceFilter) &&
        (!q ||
          (c.name || "").toLowerCase().includes(q) ||
          (c.camera_id || "").toLowerCase().includes(q))
    );
  }, [cameras, sourceFilter, query]);

  const active = filtered.filter((c) => enabled.has(c.camera_id));

  function toggle(id) {
    setEnabled((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  if (loading) {
    return (
      <>
        <PageHeader title="Live viewer" description="Loading the feed catalogue…" />
        <SkeletonGrid items={6} className="sm:grid-cols-2 xl:grid-cols-3" />
      </>
    );
  }

  if (error) {
    return (
      <>
        <PageHeader title="Live viewer" />
        <ErrorState error={error} onRetry={load} />
        <p className="mt-3 text-[13px] text-ink-3">
          Check that the backend is running and that <code className="font-mono text-ink-2">SENTINEL_SANDBOX_HOST</code>{" "}
          or <code className="font-mono text-ink-2">LIVE_FEED_HOST</code> is configured in{" "}
          <code className="font-mono text-ink-2">backend/.env</code>.
        </p>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Live viewer"
        description="Multiple feed sources, aggregated into one grid over authenticated HLS. Every tile reconnects with capped backoff - a failed join is never terminal."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="brand">Model 2</Badge>
            <Badge tone="neutral" icon={Radio}>
              {count(sources.length)} source{sources.length === 1 ? "" : "s"}
            </Badge>
          </div>
        }
      />

      <Card className="mb-4">
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Source" className="min-w-[14rem]">
            {(a) => (
              <Select {...a} value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
                <option value="">All sources ({cameras.length})</option>
                {sources.map((s) => (
                  <option key={s} value={s}>
                    {s} ({cameras.filter((c) => c.source_host === s).length})
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Find a camera" className="min-w-[14rem] flex-1">
            {(a) => (
              <Input
                {...a}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Name or ID"
              />
            )}
          </Field>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setEnabled(new Set(filtered.map((c) => c.camera_id)))}
            >
              Enable all
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEnabled(new Set())}>
              Disable all
            </Button>
          </div>

          <p className="tnum ml-auto text-[13px] text-ink-3">
            <span className="font-semibold text-ink">{count(active.length)}</span> of{" "}
            {count(filtered.length)} cameras active
          </p>
        </CardBody>
      </Card>

      <Card className="mb-6">
        <CardHeader
          title="Camera selector"
          description="Turn tiles on only as needed - every active tile holds an open stream against a shared session."
        />
        <CardBody>
          {filtered.length === 0 ? (
            <p className="text-[13px] text-ink-3">No cameras match this filter.</p>
          ) : (
            <div className="flex max-h-44 flex-wrap gap-2 overflow-y-auto">
              {filtered.map((cam) => {
                const on = enabled.has(cam.camera_id);
                return (
                  <button
                    key={cam.camera_id}
                    type="button"
                    role="switch"
                    aria-checked={on}
                    onClick={() => toggle(cam.camera_id)}
                    className={
                      "inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] transition-colors duration-150 " +
                      (on
                        ? "border-brand/40 bg-brand-soft font-medium text-brand"
                        : "border-line bg-surface text-ink-3 hover:border-line-strong hover:text-ink")
                    }
                  >
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 rounded-full ${on ? "bg-brand" : "bg-ink-3/50"}`}
                    />
                    {cam.name || cam.camera_id}
                  </button>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>

      {active.length === 0 ? (
        <EmptyState
          icon={VideoOff}
          title="No cameras enabled"
          description="Use the selector above to bring feeds online."
          action={
            <Button size="sm" onClick={() => setEnabled(new Set(filtered.slice(0, DEFAULT_ENABLED).map((c) => c.camera_id)))}>
              Enable first {DEFAULT_ENABLED}
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {active.map((cam, i) => (
            <div key={cam.camera_id}>
              <HlsPlayer
                src={cam.streams?.hls?.startsWith("/") ? `/api${cam.streams.hls}` : cam.streams?.hls}
                mp4Src={cam.streams?.mp4}
                cameraId={`${cam.name || cam.camera_id} (${cam.source_host})`}
                reconnect={reconnectConfig}
                startDelayMs={i * 250}
              />
              <p className="mt-2 truncate px-1 text-2xs text-ink-3">
                {/* The catalogue reports "unknown" for codec/resolution on some
                    hosts - printing that back is noise, so drop it. */}
                {[cam.codec, cam.resolution, cam.source_host]
                  .filter((v) => v && v !== "unknown")
                  .join(" · ")}
              </p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
