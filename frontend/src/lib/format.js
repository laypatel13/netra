/** Presentation-only formatters. Locale-aware where a number is user-facing. */

const numberFmt = new Intl.NumberFormat("en-IN");

export const count = (n) =>
  typeof n === "number" && Number.isFinite(n) ? numberFmt.format(n) : "-";

export const pct = (n) =>
  typeof n === "number" && Number.isFinite(n) ? `${n}%` : "-";

/**
 * Detection timestamps are presentation-timestamps (PTS) in milliseconds from
 * the start of the stream, not wall-clock - see PLAN.md Section 8. Render them
 * as an elapsed offset so nobody misreads a PTS value as a time of day.
 */
export function pts(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "-";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (v) => String(v).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export const confidence = (c) =>
  typeof c === "number" && Number.isFinite(c) ? `${Math.round(c * 100)}%` : "-";

/** "silver_gray car" reads badly in a UI; "Silver gray car" doesn't. */
export const vehicleLabel = (color, type) => {
  const parts = [color?.replace(/_/g, " "), type].filter(Boolean);
  if (parts.length === 0) return "Unidentified vehicle";
  const s = parts.join(" ");
  return s.charAt(0).toUpperCase() + s.slice(1);
};
