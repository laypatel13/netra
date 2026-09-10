import { Camera } from "lucide-react";
import { asset } from "../lib/api.js";
import { confidence, pts, vehicleLabel } from "../lib/format.js";

/**
 * A reconstructed route, as an ordered list of sightings.
 *
 * Timestamps are stream PTS offsets, not clock time (PLAN.md Section 8) -
 * labelled as such so nobody reads "2:14" as an hour of the day.
 */
export default function StopList({ stops = [], className = "" }) {
  return (
    <ol className={`flex flex-col gap-2 ${className}`}>
      {stops.map((stop, i) => (
        <li
          key={`${stop.camera_id}-${i}`}
          className="flex items-center gap-3 rounded-xl border border-line bg-surface-2/60 p-2.5"
        >
          <span className="tnum grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand text-2xs font-semibold text-on-brand">
            {i + 1}
          </span>

          {stop.thumbnail_url ? (
            <img
              src={asset(stop.thumbnail_url)}
              alt={`Sighting ${i + 1} at camera ${stop.camera_id}`}
              width={80}
              height={58}
              loading="lazy"
              className="h-[58px] w-20 shrink-0 rounded-lg border border-line object-cover"
            />
          ) : (
            <span className="grid h-[58px] w-20 shrink-0 place-items-center rounded-lg border border-line bg-surface-3 text-ink-3">
              <Camera className="h-4 w-4" aria-hidden="true" />
            </span>
          )}

          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium text-ink">
              <span className="font-mono">{stop.camera_id}</span>
              {stop.vehicle_type && (
                <span className="ml-2 font-sans font-normal text-ink-2">
                  {vehicleLabel(stop.vehicle_color, stop.vehicle_type)}
                </span>
              )}
            </p>
            <p className="tnum mt-0.5 text-2xs text-ink-3">
              PTS {pts(stop.timestamp_ms)} · confidence {confidence(stop.confidence)}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
