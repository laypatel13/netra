const tones = {
  neutral: "text-ink",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  brand: "text-brand",
};

/**
 * A single number worth watching. Values are tabular so a polled figure
 * doesn't shift the card's layout every time it ticks.
 */
export default function Stat({ label, value, tone = "neutral", icon: Icon, hint, loading = false }) {
  return (
    <div className="flex min-h-[104px] flex-col rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="flex items-start gap-2 text-2xs font-medium uppercase leading-tight tracking-[0.08em] text-ink-3">
        {Icon && <Icon className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
        <span>{label}</span>
      </div>
      {loading ? (
        <div className="skeleton mt-2 h-8 w-16" />
      ) : (
        <div className={`tnum mt-1.5 font-display text-[1.75rem] font-semibold leading-none ${tones[tone]}`}>
          {value}
        </div>
      )}
      {hint && <p className="mt-1.5 text-2xs text-ink-3">{hint}</p>}
    </div>
  );
}

/** Coverage bar. The percentage is always written out - never colour alone. */
export function Meter({ value = 0, label, className = "" }) {
  const pct = Math.max(0, Math.min(100, value));
  const tone = pct >= 75 ? "bg-ok" : pct >= 40 ? "bg-warn" : "bg-danger";
  return (
    <div className={className}>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-surface-3"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className={`h-full rounded-full transition-[width] duration-500 ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
