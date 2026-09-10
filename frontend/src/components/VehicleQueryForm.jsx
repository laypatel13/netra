import { useState } from "react";
import { Search } from "lucide-react";
import Button from "./ui/Button.jsx";
import Field, { Input, Select } from "./ui/Field.jsx";
import Segmented from "./ui/Segmented.jsx";

export const VEHICLE_TYPES = ["car", "motorcycle", "bus", "truck"];

const MODES = [
  { value: "plate", label: "By plate" },
  { value: "attributes", label: "By description" },
];

/**
 * The two ways into the detection store - a known plate, or "what the witness
 * saw". Shared by the control room and the GIS map so the two never drift
 * apart in wording or behaviour.
 */
export default function VehicleQueryForm({ onSearch, loading = false, submitLabel = "Trace route", children }) {
  const [mode, setMode] = useState("plate");
  const [plate, setPlate] = useState("");
  const [vehicleType, setVehicleType] = useState(VEHICLE_TYPES[0]);
  const [vehicleColor, setVehicleColor] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    onSearch({ mode, plate: plate.trim(), vehicleType, vehicleColor: vehicleColor.trim() });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Segmented value={mode} onChange={setMode} options={MODES} label="Search mode" className="self-start" />

      <div className="flex flex-wrap items-end gap-3">
        {mode === "plate" ? (
          <Field label="Plate number" className="min-w-[13rem] flex-1">
            {(a) => (
              <Input
                {...a}
                value={plate}
                onChange={(e) => setPlate(e.target.value.toUpperCase())}
                placeholder="GJ01AB1234"
                autoComplete="off"
                spellCheck="false"
                className="font-mono tracking-wide"
              />
            )}
          </Field>
        ) : (
          <>
            <Field label="Vehicle type" className="min-w-[9rem] flex-1">
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
            <Field label="Colour" className="min-w-[9rem] flex-1">
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
          </>
        )}

        <div className="flex items-center gap-2">
          <Button type="submit" loading={loading}>
            {!loading && <Search className="h-4 w-4" aria-hidden="true" />}
            {submitLabel}
          </Button>
          {children}
        </div>
      </div>

      {mode === "attributes" && (
        <p className="rounded-xl border border-warn/30 bg-warn-soft/50 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
          <span className="font-semibold text-ink">A narrowing tool, not identification.</span> Other
          vehicles share the same type and colour - check the thumbnails before acting on a match.
        </p>
      )}
    </form>
  );
}
