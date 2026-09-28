import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, BadgeCheck, Check, Pencil, Plus, ShieldAlert, ShieldCheck, Trash2, X } from "lucide-react";
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
const CONFIRM_DELETE_TIMEOUT_MS = 4000;

const MODES = [
  { value: "plate", label: "By plate" },
  { value: "attributes", label: "By description" },
];

function draftFromEntry(entry) {
  return {
    mode: entry.plate_number ? "plate" : "attributes",
    plate_number: entry.plate_number || "",
    vehicle_type: entry.vehicle_type || VEHICLE_TYPES[0],
    vehicle_color: entry.vehicle_color || "",
    category: entry.category,
  };
}

/**
 * Watchlist management + the live alert feed (PLAN.md Section 0c).
 *
 * An entry needs a plate OR a type+colour pair - the description mode exists
 * for the "suspect vehicle, no known plate" case that wide-angle CCTV forces
 * on us (Section 0b). Alerts are tiered and never styled alike: an exact plate
 * read is evidence, a description match is a lead - which is also why the
 * alert feed below is split into two lists rather than one interleaved one.
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

  const [actor, setActor] = useState("");

  const [entries, setEntries] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [alertsError, setAlertsError] = useState(null);

  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(null);
  const [savingId, setSavingId] = useState(null);
  const [rowError, setRowError] = useState({});
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const confirmTimer = useRef(null);

  const [confirmDeleteAlertId, setConfirmDeleteAlertId] = useState(null);
  const [deletingAlertId, setDeletingAlertId] = useState(null);
  const [alertRowError, setAlertRowError] = useState(null);
  const confirmAlertTimer = useRef(null);

  const adminHeaders = {
    "Content-Type": "application/json",
    "X-Role": "admin",
    ...(actor.trim() ? { "X-Actor": actor.trim() } : {}),
  };

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

  useEffect(() => () => clearTimeout(confirmTimer.current), []);
  useEffect(() => () => clearTimeout(confirmAlertTimer.current), []);

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

  function startEdit(entry) {
    setConfirmDeleteId(null);
    setRowError((r) => ({ ...r, [entry.id]: null }));
    setEditingId(entry.id);
    setEditDraft(draftFromEntry(entry));
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft(null);
  }

  async function saveEdit(id) {
    setSavingId(id);
    setRowError((r) => ({ ...r, [id]: null }));

    const body =
      editDraft.mode === "plate"
        ? { plate_number: editDraft.plate_number.trim(), category: editDraft.category }
        : {
            vehicle_type: editDraft.vehicle_type,
            vehicle_color: editDraft.vehicle_color.trim(),
            category: editDraft.category,
          };

    try {
      await api(`/watchlist/${id}`, {
        method: "PUT",
        headers: adminHeaders,
        body: JSON.stringify(body),
      });
      setEditingId(null);
      setEditDraft(null);
      loadEntries();
      loadAlerts();
    } catch (err) {
      setRowError((r) => ({ ...r, [id]: err }));
    } finally {
      setSavingId(null);
    }
  }

  function handleDeleteClick(id) {
    if (confirmDeleteId === id) {
      clearTimeout(confirmTimer.current);
      setConfirmDeleteId(null);
      doDelete(id);
      return;
    }
    setConfirmDeleteId(id);
    clearTimeout(confirmTimer.current);
    confirmTimer.current = setTimeout(() => setConfirmDeleteId(null), CONFIRM_DELETE_TIMEOUT_MS);
  }

  async function doDelete(id) {
    setDeletingId(id);
    setRowError((r) => ({ ...r, [id]: null }));
    try {
      await api(`/watchlist/${id}`, { method: "DELETE", headers: adminHeaders });
      if (editingId === id) cancelEdit();
      loadEntries();
      loadAlerts();
    } catch (err) {
      setRowError((r) => ({ ...r, [id]: err }));
    } finally {
      setDeletingId(null);
    }
  }

  function handleDeleteAlertClick(detectionId) {
    if (confirmDeleteAlertId === detectionId) {
      clearTimeout(confirmAlertTimer.current);
      setConfirmDeleteAlertId(null);
      doDeleteAlert(detectionId);
      return;
    }
    setConfirmDeleteAlertId(detectionId);
    clearTimeout(confirmAlertTimer.current);
    confirmAlertTimer.current = setTimeout(() => setConfirmDeleteAlertId(null), CONFIRM_DELETE_TIMEOUT_MS);
  }

  async function doDeleteAlert(detectionId) {
    setDeletingAlertId(detectionId);
    setAlertRowError(null);
    try {
      await api(`/detections/${detectionId}`, { method: "DELETE", headers: adminHeaders });
      loadAlerts();
    } catch (err) {
      setAlertRowError(err);
    } finally {
      setDeletingAlertId(null);
    }
  }

  const exactAlerts = alerts.filter((a) => a.tier === "exact_plate");
  const attributeAlerts = alerts.filter((a) => a.tier !== "exact_plate");

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
              <Field label="Actor" hint="Optional - recorded in the audit trail alongside edits and deletes.">
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

              {entries.length === 0 ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="Watchlist is empty"
                  description="Add a plate or a vehicle description above and matches will start arriving on the right."
                  className="mt-4"
                />
              ) : (
                <ul className="mt-4 flex flex-col gap-2">
                  {entries.map((e) => {
                    const editing = editingId === e.id;
                    const confirming = confirmDeleteId === e.id;

                    if (editing) {
                      return (
                        <li key={e.id} className="rounded-xl border border-brand/40 bg-surface-2/60 p-3">
                          <div className="flex flex-col gap-3">
                            <Segmented
                              value={editDraft.mode}
                              onChange={(m) => setEditDraft((d) => ({ ...d, mode: m }))}
                              options={MODES}
                              label="Entry type"
                              className="self-start"
                            />

                            {editDraft.mode === "plate" ? (
                              <Field label="Plate number" required>
                                {(a) => (
                                  <Input
                                    {...a}
                                    value={editDraft.plate_number}
                                    onChange={(ev) =>
                                      setEditDraft((d) => ({ ...d, plate_number: ev.target.value.toUpperCase() }))
                                    }
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
                                    <Select
                                      {...a}
                                      value={editDraft.vehicle_type}
                                      onChange={(ev) => setEditDraft((d) => ({ ...d, vehicle_type: ev.target.value }))}
                                    >
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
                                      value={editDraft.vehicle_color}
                                      onChange={(ev) => setEditDraft((d) => ({ ...d, vehicle_color: ev.target.value }))}
                                      autoComplete="off"
                                    />
                                  )}
                                </Field>
                              </div>
                            )}

                            <Field label="Category" required>
                              {(a) => (
                                <Select
                                  {...a}
                                  value={editDraft.category}
                                  onChange={(ev) => setEditDraft((d) => ({ ...d, category: ev.target.value }))}
                                >
                                  {CATEGORIES.map((c) => (
                                    <option key={c} value={c}>
                                      {c}
                                    </option>
                                  ))}
                                </Select>
                              )}
                            </Field>

                            <div className="flex gap-2">
                              <Button size="sm" onClick={() => saveEdit(e.id)} loading={savingId === e.id}>
                                {savingId !== e.id && <Check className="h-4 w-4" aria-hidden="true" />}
                                Save
                              </Button>
                              <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={savingId === e.id}>
                                <X className="h-4 w-4" aria-hidden="true" />
                                Cancel
                              </Button>
                            </div>

                            {rowError[e.id] && <ErrorState error={rowError[e.id]} />}
                          </div>
                        </li>
                      );
                    }

                    return (
                      <li
                        key={e.id}
                        className="flex items-center gap-2 rounded-xl border border-line bg-surface-2/60 px-3 py-2"
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-2">
                          <span
                            className={
                              (e.plate_number ? "font-mono font-semibold tracking-wide text-ink" : "font-medium text-ink") +
                              " truncate"
                            }
                          >
                            {e.plate_number || vehicleLabel(e.vehicle_color, e.vehicle_type)}
                          </span>
                          <Badge tone="gold" className="shrink-0">
                            {e.category}
                          </Badge>
                        </div>

                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-9 w-9 shrink-0"
                          onClick={() => startEdit(e)}
                          aria-label={`Edit ${e.plate_number || vehicleLabel(e.vehicle_color, e.vehicle_type)}`}
                        >
                          <Pencil className="h-4 w-4" aria-hidden="true" />
                        </Button>

                        <Button
                          variant={confirming ? "danger" : "ghost"}
                          size="icon"
                          className="h-9 w-9 shrink-0"
                          onClick={() => handleDeleteClick(e.id)}
                          loading={deletingId === e.id}
                          aria-label={
                            confirming
                              ? `Confirm delete of ${e.plate_number || vehicleLabel(e.vehicle_color, e.vehicle_type)}`
                              : `Delete ${e.plate_number || vehicleLabel(e.vehicle_color, e.vehicle_type)}`
                          }
                          title={confirming ? "Click again to confirm" : "Delete"}
                        >
                          {deletingId !== e.id && <Trash2 className="h-4 w-4" aria-hidden="true" />}
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        {/* ------------------------------------------------------------ Alerts */}
        <div className="flex flex-col gap-4 self-start">
          {alertRowError && <ErrorState error={alertRowError} />}

          <Card>
            <CardHeader
              title="Exact plate matches"
              description="A plate read exactly against a watchlisted plate - evidence, not a lead."
              actions={
                exactAlerts.length > 0 ? (
                  <Badge tone="ok" icon={BadgeCheck}>
                    {count(exactAlerts.length)}
                  </Badge>
                ) : null
              }
            />
            <CardBody>
              <p className="sr-only" aria-live="polite">
                {exactAlerts.length > 0
                  ? `${exactAlerts.length} exact plate ${exactAlerts.length === 1 ? "match" : "matches"}.`
                  : "No exact plate matches among recent detections."}
              </p>

              {alertsError && <ErrorState error={alertsError} onRetry={loadAlerts} className="mb-4" />}

              {exactAlerts.length === 0 && !alertsError ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="No exact plate matches"
                  description="Stays quiet until a watchlisted plate is actually read by a camera."
                />
              ) : (
                <ul className="flex flex-col gap-2">
                  {exactAlerts.map((a, i) => {
                    const detectionId = a.detection?.id ?? i;
                    return (
                      <AlertRow
                        key={detectionId}
                        alert={a}
                        onDelete={() => handleDeleteAlertClick(detectionId)}
                        deleting={deletingAlertId === detectionId}
                        confirming={confirmDeleteAlertId === detectionId}
                      />
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Possible matches - by description"
              description="Type + colour only, no legible plate - a narrowing tool, not identification."
              actions={
                attributeAlerts.length > 0 ? (
                  <Badge tone="danger" icon={ShieldAlert}>
                    {count(attributeAlerts.length)} active
                  </Badge>
                ) : null
              }
            />
            <CardBody>
              <p className="sr-only" aria-live="polite">
                {attributeAlerts.length > 0
                  ? `${attributeAlerts.length} possible ${attributeAlerts.length === 1 ? "match" : "matches"} by description.`
                  : "No description-based matches among recent detections."}
              </p>

              {attributeAlerts.length === 0 && !alertsError ? (
                <EmptyState
                  icon={AlertTriangle}
                  title="No description-based matches"
                  description="This feed stays quiet until a watchlisted type+colour is actually seen. Run the ANPR pipeline against a camera to generate detections."
                />
              ) : (
                <ul className="flex flex-col gap-2">
                  {attributeAlerts.map((a, i) => {
                    const detectionId = a.detection?.id ?? i;
                    return (
                      <AlertRow
                        key={detectionId}
                        alert={a}
                        onDelete={() => handleDeleteAlertClick(detectionId)}
                        deleting={deletingAlertId === detectionId}
                        confirming={confirmDeleteAlertId === detectionId}
                      />
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
