import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer } from "react-leaflet";
import L from "leaflet";
import { MapPinOff, Upload } from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { Card, CardBody, CardHeader } from "../components/ui/Card.jsx";
import Button from "../components/ui/Button.jsx";
import Badge, { StatusPill } from "../components/ui/Badge.jsx";
import Field, { FileInput, Input, Select } from "../components/ui/Field.jsx";
import { EmptyState, ErrorState } from "../components/ui/Feedback.jsx";
import VehicleQueryForm from "../components/VehicleQueryForm.jsx";
import StopList from "../components/StopList.jsx";
import { api, asset } from "../lib/api.js";
import { confidence, count, pts, vehicleLabel } from "../lib/format.js";

const AHMEDABAD = [23.0225, 72.5714];

/**
 * Custom marker so the map doesn't depend on Leaflet's default PNG icons
 * (which don't survive bundling without extra wiring) and so connectivity
 * status is legible at a glance. Colours come from theme tokens, so the pins
 * re-tint with the rest of the UI instead of staying stuck in light mode.
 */
const STATUS_VAR = {
  online: "--c-ok",
  offline: "--c-danger",
  unknown: "--c-warn",
};

const iconCache = new Map();
function cameraIcon(status) {
  const key = status || "unknown";
  if (!iconCache.has(key)) {
    const v = STATUS_VAR[key] || "--c-ink-3";
    iconCache.set(
      key,
      L.divIcon({
        className: "",
        iconSize: [18, 18],
        iconAnchor: [9, 9],
        popupAnchor: [0, -10],
        html:
          `<span style="display:block;width:18px;height:18px;border-radius:9999px;` +
          `background:rgb(var(${v}));border:1.75px solid rgb(var(--c-surface));` +
          `box-shadow:0 0 0 1px rgb(var(--c-border-strong)),0 2px 6px rgb(0 0 0 / .35)"></span>`,
      })
    );
  }
  return iconCache.get(key);
}

/**
 * Model 1 - Registry & GIS. Camera inventory on a map, department/status
 * filters, bulk CSV onboarding (a graded workflow, not a dev tool), and a
 * reconstructed route drawn over the top.
 *
 * Route camera coordinates come from a separate, always-unfiltered fetch so
 * the filters above can't silently break route rendering.
 */
export default function Registry() {
  const [cameras, setCameras] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const [csvFile, setCsvFile] = useState(null);
  const [actor, setActor] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);

  const [cameraCoords, setCameraCoords] = useState({});
  const [route, setRoute] = useState(null);
  const [routeError, setRouteError] = useState(null);
  const [searching, setSearching] = useState(false);

  const loadCameras = useCallback(() => {
    const params = new URLSearchParams();
    if (department) params.set("department", department);
    setLoadError(null);
    api(`/cameras/geojson?${params.toString()}`)
      .then((geojson) => setCameras(geojson.features || []))
      .catch((e) => {
        setCameras([]);
        setLoadError(e);
      });
  }, [department]);

  useEffect(loadCameras, [loadCameras, refreshKey]);

  useEffect(() => {
    api("/cameras/geojson")
      .then((geojson) => {
        const coords = {};
        for (const f of geojson.features || []) {
          coords[f.properties.camera_id] = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
        }
        setCameraCoords(coords);
      })
      .catch(() => setCameraCoords({}));
  }, [refreshKey]);

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
          : { message: "No sightings found. Check the plate or description, and that the camera is onboarded here." }
      );
    } finally {
      setSearching(false);
    }
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!csvFile) return;
    setUploading(true);
    setUploadResult(null);
    setUploadError(null);

    const formData = new FormData();
    formData.append("file", csvFile);

    try {
      const onboarded = await api("/cameras/bulk-csv", {
        method: "POST",
        headers: { "X-Role": "admin", ...(actor ? { "X-Actor": actor } : {}) },
        body: formData,
      });
      const n = Array.isArray(onboarded) ? onboarded.length : 0;
      setUploadResult(`Onboarded ${n} camera${n === 1 ? "" : "s"}.`);
      setCsvFile(null);
      e.target.reset();
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setUploadError(err);
    } finally {
      setUploading(false);
    }
  }

  const filtered = useMemo(
    () => (status ? cameras.filter((f) => f.properties.connectivity_status === status) : cameras),
    [cameras, status]
  );

  // A stop whose camera isn't onboarded here is dropped from the polyline
  // rather than breaking it - and the count is surfaced, not hidden.
  const routePositions = (route?.stops || []).map((s) => cameraCoords[s.camera_id]).filter(Boolean);
  const missingStops = (route?.stops?.length || 0) - routePositions.length;

  return (
    <>
      <PageHeader
        title="Camera registry & GIS"
        description="Model 1 - the mandatory metadata foundation. Every onboarded camera, on one map, with the route of a traced vehicle drawn over it."
        actions={<Badge tone="brand">Model 1</Badge>}
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        {/* ------------------------------------------------------------- Map */}
        <div className="order-2 flex flex-col gap-4 xl:order-1">
          <Card className="overflow-hidden">
            <CardHeader
              title="Coverage map"
              description={`${count(filtered.length)} camera${filtered.length === 1 ? "" : "s"} shown${
                status || department ? " (filtered)" : ""
              }`}
              actions={
                <div className="flex items-center gap-3 text-2xs text-ink-3">
                  {["online", "offline", "unknown"].map((s) => (
                    <span key={s} className="inline-flex items-center gap-1.5">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ background: `rgb(var(${STATUS_VAR[s]}))` }}
                        aria-hidden="true"
                      />
                      {s}
                    </span>
                  ))}
                </div>
              }
            />

            {loadError ? (
              <CardBody>
                <ErrorState error={loadError} onRetry={loadCameras} />
              </CardBody>
            ) : (
              <div className="h-[clamp(360px,58vh,620px)] w-full">
                <MapContainer center={AHMEDABAD} zoom={7} className="h-full w-full">
                  <TileLayer
                    attribution="&copy; OpenStreetMap contributors"
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  />

                  {filtered.map((f) => (
                    <Marker
                      key={f.properties.camera_id}
                      position={[f.geometry.coordinates[1], f.geometry.coordinates[0]]}
                      icon={cameraIcon(f.properties.connectivity_status)}
                    >
                      <Popup>
                        <p className="font-mono text-[13px] font-semibold text-ink">
                          {f.properties.camera_id}
                        </p>
                        <p className="mt-0.5 text-2xs text-ink-3">{f.properties.department}</p>
                        <div className="mt-2">
                          <StatusPill status={f.properties.connectivity_status} />
                        </div>
                      </Popup>
                    </Marker>
                  ))}

                  {routePositions.length > 1 && (
                    <Polyline
                      positions={routePositions}
                      pathOptions={{ color: "#1A73E8", weight: 2.5, opacity: 0.9 }}
                    />
                  )}

                  {route?.stops?.map((stop, i) => {
                    const pos = cameraCoords[stop.camera_id];
                    if (!pos) return null;
                    return (
                      <CircleMarker
                        key={`stop-${i}`}
                        center={pos}
                        radius={9}
                        pathOptions={{
                          color: "#FFFFFF",
                          weight: 1.5,
                          fillColor: "#1A73E8",
                          fillOpacity: 1,
                        }}
                      >
                        <Popup>
                          <p className="text-[13px] font-semibold text-ink">
                            Stop {i + 1} -{" "}
                            <span className="font-mono">{stop.camera_id}</span>
                          </p>
                          <p className="tnum mt-0.5 text-2xs text-ink-3">
                            PTS {pts(stop.timestamp_ms)} · confidence {confidence(stop.confidence)}
                          </p>
                          {stop.vehicle_type && (
                            <p className="mt-0.5 text-2xs text-ink-2">
                              {vehicleLabel(stop.vehicle_color, stop.vehicle_type)}
                            </p>
                          )}
                          {stop.thumbnail_url && (
                            <img
                              src={asset(stop.thumbnail_url)}
                              alt={`Sighting at ${stop.camera_id}`}
                              className="mt-2 w-32 rounded-lg border border-line"
                            />
                          )}
                        </Popup>
                      </CircleMarker>
                    );
                  })}
                </MapContainer>
              </div>
            )}
          </Card>

          {route && (
            <Card>
              <CardHeader
                title={route.plate_number ? `Route for ${route.plate_number}` : `Candidate sightings - ${route.query}`}
                description={
                  missingStops > 0
                    ? `${missingStops} stop${missingStops === 1 ? "" : "s"} reference a camera that isn't onboarded here - listed below, but not drawn on the map.`
                    : `${route.stops?.length ?? 0} sighting${route.stops?.length === 1 ? "" : "s"}, in recorded order.`
                }
                actions={
                  <Button size="sm" variant="ghost" onClick={() => setRoute(null)}>
                    Clear route
                  </Button>
                }
              />
              <CardBody>
                {route.stops?.length ? (
                  <StopList stops={route.stops} />
                ) : (
                  <EmptyState icon={MapPinOff} title="Empty route" description="No stops in this result." />
                )}
              </CardBody>
            </Card>
          )}
        </div>

        {/* ------------------------------------------------------- Side panel */}
        <div className="order-1 flex flex-col gap-4 xl:order-2">
          <Card>
            <CardHeader title="Trace a vehicle" description="Draws the reconstructed path onto the map." />
            <CardBody>
              <VehicleQueryForm onSearch={handleSearch} loading={searching} submitLabel="Trace" />
              {routeError && <ErrorState error={routeError} className="mt-4" />}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Filters" />
            <CardBody className="flex flex-col gap-3">
              <Field label="Department" hint="Server-side filter - matches the registry's department field.">
                {(a) => (
                  <Input
                    {...a}
                    value={department}
                    onChange={(e) => setDepartment(e.target.value)}
                    placeholder="e.g. Traffic Police"
                  />
                )}
              </Field>
              <Field label="Connectivity status">
                {(a) => (
                  <Select {...a} value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="">All statuses</option>
                    <option value="online">Online</option>
                    <option value="offline">Offline</option>
                    <option value="unknown">Unknown</option>
                  </Select>
                )}
              </Field>
              {(department || status) && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="self-start"
                  onClick={() => {
                    setDepartment("");
                    setStatus("");
                  }}
                >
                  Clear filters
                </Button>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Bulk onboarding"
              description="Upload a CSV of cameras. Rows with a bad status, type or coordinate are skipped, not failed - the rest still import."
            />
            <CardBody>
              <form onSubmit={handleUpload} className="flex flex-col gap-3">
                <Field label="Camera CSV" required>
                  {(a) => (
                    <FileInput
                      {...a}
                      accept=".csv"
                      onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                    />
                  )}
                </Field>
                <Field label="Actor" hint="Optional - recorded in the audit trail alongside the import.">
                  {(a) => (
                    <Input
                      {...a}
                      value={actor}
                      onChange={(e) => setActor(e.target.value)}
                      placeholder="officer name or ID"
                      autoComplete="off"
                    />
                  )}
                </Field>
                <Button type="submit" disabled={!csvFile} loading={uploading} className="self-start">
                  {!uploading && <Upload className="h-4 w-4" aria-hidden="true" />}
                  Upload
                </Button>

                {uploadResult && (
                  <p role="status" className="rounded-xl border border-ok/30 bg-ok-soft px-3.5 py-2.5 text-[13px] font-medium text-ok">
                    {uploadResult}
                  </p>
                )}
                {uploadError && <ErrorState error={uploadError} />}
              </form>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
