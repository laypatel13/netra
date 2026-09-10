import { AlertTriangle, BadgeCheck, Camera } from "lucide-react";
import Badge from "./ui/Badge.jsx";
import { asset } from "../lib/api.js";
import { vehicleLabel } from "../lib/format.js";

/**
 * One watchlist match.
 *
 * The tier is the whole point: an exact plate read is evidence, an attribute
 * match is a lead. They are never styled alike, and the attribute tier says
 * "possible" in words rather than relying on the colour of the badge.
 */
export default function AlertRow({ alert, compact = false }) {
  const exact = alert.tier === "exact_plate";
  const d = alert.detection || {};
  const subject = d.plate_number || vehicleLabel(d.vehicle_color, d.vehicle_type);

  return (
    <li
      className={
        "flex items-center gap-3 rounded-xl border p-2.5 " +
        (exact ? "border-ok/30 bg-ok-soft/40" : "border-warn/30 bg-warn-soft/40")
      }
    >
      {d.thumbnail_url ? (
        <img
          src={asset(d.thumbnail_url)}
          alt={`Detection on camera ${d.camera_id}`}
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
        <Badge tone={exact ? "ok" : "warn"} icon={exact ? BadgeCheck : AlertTriangle}>
          {exact ? "Exact plate match" : "Possible match - by description"}
        </Badge>

        <p className="mt-1.5 truncate text-[13px] font-semibold text-ink">
          <span className={d.plate_number ? "font-mono tracking-wide" : ""}>{subject}</span>
        </p>

        {!compact && (
          <p className="mt-0.5 text-2xs leading-snug text-ink-3">
            Camera <span className="font-mono text-ink-2">{d.camera_id}</span> · watchlist:{" "}
            <span className="font-medium text-gold-ink">{alert.watchlist_entry?.category}</span>
          </p>
        )}
      </div>
    </li>
  );
}
