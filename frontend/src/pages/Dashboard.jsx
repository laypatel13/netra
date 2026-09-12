import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BellRing,
  Building2,
  Camera,
  Percent,
  ShieldAlert,
  VideoOff,
} from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { Card, CardBody, CardHeader, SectionHeading } from "../components/ui/Card.jsx";
import Stat from "../components/ui/Stat.jsx";
import Button from "../components/ui/Button.jsx";
import { EmptyState, ErrorState, SkeletonGrid } from "../components/ui/Feedback.jsx";
import HlsPlayer from "../components/HlsPlayer.jsx";
import VehicleQueryForm from "../components/VehicleQueryForm.jsx";
import StopList from "../components/StopList.jsx";
import AlertRow from "../components/AlertRow.jsx";
import { api, asset } from "../lib/api.js";
import { count, pct } from "../lib/format.js";

const PREVIEW_TILE_COUNT = 4;
const ALERT_POLL_MS = 5000;

/**
 * Unified control room - the convergence point of Model 1 (registry/GIS) and
 * Model 2 (live viewing/analytics), per PLAN.md Section 2's system diagram.
 * Coverage summary, a live preview grid, plate-or-description vehicle search,
 * and the head of the alert feed, so an operator can see the state of the
 * whole network without leaving this page.
 */
export default function Dashboard() {
  const [gap, setGap] = useState(null);
  const [gapError, setGapError] = useState(null);

  const [previewCameras, setPreviewCameras] = useState(null);
  const [reconnectConfig, setReconnectConfig] = useState(null);

  const [watchlistCount, setWatchlistCount] = useState(null);
  const [alerts, setAlerts] = useState([]);

  const [route, setRoute] = useState(null);
  const [routeError, setRouteError] = useState(null);
  const [searching, setSearching] = useState(false);

  const loadCoverage = useCallback(() => {
    setGapError(null);
    api("/cameras/gap-analysis")
      .then(setGap)
      .catch(setGapError);
  }, []);

  useEffect(() => {
    loadCoverage();

    api("/feeds/catalogue")
      .then((data) => {
        setPreviewCameras((data.cameras || []).slice(0, PREVIEW_TILE_COUNT));
        setReconnectConfig(data.reconnect || null);
      })
      .catch(() => setPreviewCameras([]));

    api("/watchlist")
      .then((list) => setWatchlistCount(Array.isArray(list) ? list.length : null))
      .catch(() => setWatchlistCount(null));
  }, [loadCoverage]);

  // Short-poll the alert head - no websocket layer needed at this scale.
  useEffect(() => {
    let alive = true;
    const load = () =>
      api("/watchlist/alerts/recent")
        .then((d) => alive && setAlerts(Array.isArray(d) ? d : []))
        .catch(() => {});
    load();
    const t = setInterval(load, ALERT_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  async function handleSearch({ mode, plate, vehicleType, vehicleColor }) {
    setRouteError(null);
    setRoute(null);
    setSearching(true);
    try {
      const path =
        mode === "plate"
          ? `/detections/route/${encodeURIComponent(plate)}`
          : `/detections/search?vehicle_type=${encodeURIComponent(vehicleType)}&vehicle_color=${encodeURIComponent(vehicleColor)}`;
      setRoute(await api(path));
    } catch (err) {
      setRouteError(
        err.offline
          ? err
          : {
              message:
                mode === "plate"
                  ? `No sightings recorded for “${plate || "-"}”. The plate may not have been read on any onboarded camera.`
                  : `No sightings recorded for a ${vehicleColor || "-"} ${vehicleType}. Try a broader colour, or a different type.`,
            }
      );
    } finally {
      setSearching(false);
    }
  }

  const s = gap?.summary;

  return (
    <>
      <PageHeader
        title="Control room"
        description="One view of the whole network - coverage, live feeds, vehicle tracing and watchlist activity."
        actions={
          <Link to="/app/gap-analysis">
            <Button variant="secondary" size="sm">
              Full coverage report
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </Link>
        }
      />

      {/* ------------------------------------------------------- Coverage stats */}
      {gapError ? (
        <ErrorState error={gapError} onRetry={loadCoverage} className="mb-6" />
      ) : (
        <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat
            label="Cameras onboarded"
            value={count(s?.total_cameras)}
            icon={Camera}
            loading={!gap}
          />
          <Stat
            label="Coverage"
            value={pct(s?.coverage_pct)}
            icon={Percent}
            loading={!gap}
            tone={!s ? "neutral" : s.coverage_pct >= 75 ? "ok" : s.coverage_pct >= 40 ? "warn" : "danger"}
          />
          <Stat
            label="Departments missing"
            value={count(s?.departments_missing)}
            icon={Building2}
            loading={!gap}
            tone={!s ? "neutral" : s.departments_missing > 0 ? "danger" : "ok"}
            hint={s ? `${count(s.departments_onboarded)} onboarded` : undefined}
          />
          <Stat label="Watchlist entries" value={count(watchlistCount)} icon={ShieldAlert} />
          <Stat
            label="Active alerts"
            value={count(alerts.length)}
            icon={BellRing}
            tone={alerts.length > 0 ? "danger" : "neutral"}
            hint="among recent detections"
          />
        </div>
      )}

      {/* ------------------------------------------- Trace a vehicle + alert head */}
      <div className="mb-8 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Trace a vehicle"
            description="Search the detection store by plate, or by what the vehicle looked like when no plate was legible."
          />
          <CardBody>
            <VehicleQueryForm onSearch={handleSearch} loading={searching}>
              {route && (
                <Button type="button" variant="ghost" onClick={() => setRoute(null)}>
                  Clear
                </Button>
              )}
            </VehicleQueryForm>

            {routeError && <ErrorState error={routeError} className="mt-4" />}

            {route && (
              <div className="mt-5">
                <SectionHeading
                  title={
                    route.plate_number
                      ? `Route for ${route.plate_number}`
                      : `Candidate sightings - ${route.query}`
                  }
                  description={`${route.stops?.length ?? 0} sighting${route.stops?.length === 1 ? "" : "s"}, ordered as recorded.`}
                  actions={
                    <Link to="/app/registry">
                      <Button size="sm" variant="secondary">
                        View on map
                        <ArrowRight className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </Link>
                  }
                  className="mb-3"
                />
                {route.stops?.length ? (
                  <StopList stops={route.stops} />
                ) : (
                  <EmptyState title="No sightings in this result" description="The query returned an empty route." />
                )}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Live alerts"
            description="Polled every 5 seconds."
            actions={
              <Link to="/app/watchlist" className="rounded text-[13px] font-medium text-azure hover:underline">
                Manage
              </Link>
            }
          />
          <CardBody>
            {alerts.length === 0 ? (
              <EmptyState
                icon={ShieldAlert}
                title="No matches right now"
                description="Watchlist matches among recent detections will appear here the moment they're recorded."
              />
            ) : (
              <ul className="flex flex-col gap-2">
                {alerts.slice(0, 5).map((a, i) => (
                  <AlertRow key={`${a.detection?.id ?? i}`} alert={a} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {/* ---------------------------------------------------------- Live preview */}
      <SectionHeading
        title="Live preview"
        description={`First ${PREVIEW_TILE_COUNT} cameras from the feed catalogue.`}
        actions={
          <Link to="/app/live">
            <Button size="sm" variant="secondary">
              Open full live viewer
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </Link>
        }
      />

      {previewCameras === null ? (
        <SkeletonGrid items={PREVIEW_TILE_COUNT} className="sm:grid-cols-2 xl:grid-cols-4" />
      ) : previewCameras.length === 0 ? (
        <EmptyState
          icon={VideoOff}
          title="No feeds available"
          description="The feed catalogue came back empty. Check that the backend is running and that CCTV_EMAIL and CCTV_PASSWORD are set in backend/.env."
          action={
            <Link to="/app/live">
              <Button size="sm" variant="secondary">
                Open live viewer
              </Button>
            </Link>
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {previewCameras.map((cam, i) => (
            <HlsPlayer
              key={cam.camera_id}
              src={asset(cam.streams?.hls)}
              mp4Src={cam.streams?.mp4}
              cameraId={cam.name || cam.camera_id}
              reconnect={reconnectConfig}
              startDelayMs={i * 250}
            />
          ))}
        </div>
      )}
    </>
  );
}
