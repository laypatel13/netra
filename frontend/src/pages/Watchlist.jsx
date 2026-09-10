import { useCallback, useEffect, useState } from "react";
import { Plus, ShieldAlert, ShieldCheck } from "lucide-react";
import PageHeader from "../components/PageHeader.jsx";
import { Card, CardBody, CardHeader } from "../components/ui/Card.jsx";
import Button from "../components/ui/Button.jsx";
import Badge from "../components/ui/Badge.jsx";
import Field, { Input, Select } from "../components/ui/Field.jsx";
import Segmented from "../components/ui/Segmented.jsx";
import { EmptyState, ErrorState } from "../components/ui/Feedback.jsx";
import AlertRow from "../components/AlertRow.jsx";
import { VEHICLE_TYPES } from "../components/VehicleQueryForm.jsx";
import { api } from "../lib/api.js";
import { count, vehicleLabel } from "../lib/format.js";

const CATEGORIES = ["stolen", "suspect", "blacklisted"];
const ALERT_POLL_MS = 5000;

const MODES = [
  { value: "plate", label: "By plate" },
  { value: "attributes", label: "By description" },
];

/**
 * Watchlist management + the live alert feed (PLAN.md Section 0c).
 *
 * An entry needs a plate OR a type+colour pair - the description mode exists
 * for the "suspect vehicle, no known plate" case that wide-angle CCTV forces
 * on us (Section 0b). Alerts are tiered and never styled alike: an exact plate
 * read is evidence, a description match is a lead.
 */
export default function Watchlist() {
  const [mode, setMode] = useState("plate");
  const [plateNumber, setPlateNumber] = useState("");
  const [vehicleType, setVehicleType] = useState(VEHICLE_TYPES[0]);
  const [vehicleColor, setVehicleColor] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [justAdded, setJustAdded] = useState(null);

  const [entries, setEntries] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [alertsError, setAlertsError] = useState(null);

  const loadEntries = useCallback(() => {
    api("/watchlist")
      .then((d) => setEntries(Array.isArray(d) ? d : []))
      .catch(() => setEntries([]));
  }, []);

  const loadAlerts = useCallback(
    () =>
      api("/watchlist/alerts/recent")
        .then((d) => {
          setAlerts(Array.isArray(d) ? d : []);
          setAlertsError(null);
        })
        .catch(setAlertsError),
    []
  );

  useEffect(() => {
    loadEntries();
    loadAlerts();
    const t = setInterval(loadAlerts, ALERT_POLL_MS);
    return () => clearInterval(t);
  }, [loadEntries, loadAlerts]);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setJustAdded(null);

    const body =
      mode === "plate"
        ? { plate_number: plateNumber.trim(), category }
        : { vehicle_type: vehicleType, vehicle_color: vehicleColor.trim(), category };

    try {
      await api("/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setJustAdded(
        mode === "plate" ? plateNumber.trim() : vehicleLabel(vehicleColor.trim(), vehicleType)
      );
      setPlateNumber("");
      setVehicleColor("");
      loadEntries();
    } catch (err) {
      setSubmitError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Watchlist"
        description="Vehicles to be told about. A match fires the moment a camera in the network sees one - no manual query needed."
        actions={<Badge tone="brand">Model 2</Badge>}
      />

      <div className="grid gap-4 xl:grid-cols-[400px_1fr]">
        {/* ------------------------------------------------------ Add + entries */}
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader
              title="Add an entry"
              description="A plate, or a description when the plate isn't known."
            />
            <CardBody>
              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <Segmented
                  value={mode}
                  onChange={setMode}
                  options={MODES}
                  label="Entry type"
                  className="self-start"
                />

                {mode === "plate" ? (
                  <Field label="Plate number" required>
                    {(a) => (
                      <Input
                        {...a}
                        value={plateNumber}
                        onChange={(e) => setPlateNumber(e.target.value.toUpperCase())}
                        placeholder="GJ01AB1234"
                        autoComplete="off"
                        spellCheck="false"
                        className="font-mono tracking-wide"
                      />
                    )}
                  </Field>
                ) : (
                  <div className="flex gap-3">
                    <Field label="Vehicle type" required className="flex-1">
                      {(a) => (
                        <Select {...a} value={vehicleType} onChange={(e) => setVehicleType(e.target.value)}>
                          {VEHICLE_TYPES.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </Select>
                      )}
                    </Field>
                    <Field label="Colour" required className="flex-1">
                      {(a) => (
                        <Input
                          {...a}
                          value={vehicleColor}
                          onChange={(e) => setVehicleColor(e.target.value)}
                          placeholder="red"
                          autoComplete="off"
                        />
                      )}
                    </Field>
                  </div>
                )}

                <Field label="Category" required>
                  {(a) => (
                    <Select {...a} value={category} onChange={(e) => setCategory(e.target.value)}>
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>

                {mode === "attributes" && (
                  <p className="rounded-xl border border-warn/30 bg-warn-soft/50 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
                    Description entries raise <span className="font-semibold text-ink">possible</span>{" "}
                    matches, deduped over a time window. Expect leads to check, not identifications.
                  </p>
                )}

                <Button type="submit" loading={submitting} className="self-start">
                  {!submitting && <Plus className="h-4 w-4" aria-hidden="true" />}
                  Add to watchlist
                </Button>

                {justAdded && (
                  <p role="status" className="rounded-xl border border-ok/30 bg-ok-soft px-3.5 py-2.5 text-[13px] font-medium text-ok">
                    Added {justAdded} to the watchlist.
                  </p>
                )}
                {submitError && <ErrorState error={submitError} />}
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title={`Entries (${count(entries.length)})`} />
            <CardBody>
              {entries.length === 0 ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="Watchlist is empty"
                  description="Add a plate or a vehicle description above and matches will start arriving on the right."
                />
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {entries.map((e) => (
                    <li
                      key={e.id}
                      className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface-2/60 px-3 py-1.5 text-[13px]"
                    >
                      <span className={e.plate_number ? "font-mono font-semibold tracking-wide text-ink" : "font-medium text-ink"}>
                        {e.plate_number || vehicleLabel(e.vehicle_color, e.vehicle_type)}
                      </span>
                      <Badge tone="gold">{e.category}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        {/* ------------------------------------------------------------ Alerts */}
        <Card className="self-start">
          <CardHeader
            title="Alert feed"
            description="Matches among recent detections, polled every 5 seconds."
            actions={
              alerts.length > 0 ? (
                <Badge tone="danger" icon={ShieldAlert}>
                  {count(alerts.length)} active
                </Badge>
              ) : null
            }
          />
          <CardBody>
            {/* Announced as a whole sentence, politely, without stealing focus. */}
            <p className="sr-only" aria-live="polite">
              {alerts.length > 0
                ? `${alerts.length} watchlist ${alerts.length === 1 ? "match" : "matches"} among recent detections.`
                : "No watchlist matches among recent detections."}
            </p>

            {alertsError && <ErrorState error={alertsError} onRetry={loadAlerts} className="mb-4" />}

            {alerts.length === 0 && !alertsError ? (
              <EmptyState
                icon={ShieldCheck}
                title="No matches among recent detections"
                description="This feed stays quiet until a watchlisted vehicle is actually seen. Run the ANPR pipeline against a camera to generate detections."
              />
            ) : (
              <ul className="flex flex-col gap-2">
                {alerts.map((a, i) => (
                  <AlertRow key={a.detection?.id ?? i} alert={a} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
