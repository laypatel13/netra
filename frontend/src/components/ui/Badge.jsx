const tones = {
  neutral: "bg-surface-2 text-ink-2 border-line",
  brand: "bg-brand-soft text-brand border-brand/25",
  ok: "bg-ok-soft text-ok border-ok/25",
  warn: "bg-warn-soft text-warn border-warn/25",
  danger: "bg-danger-soft text-danger border-danger/25",
  gold: "bg-highlight/20 text-gold-ink border-gold/35",
};

export default function Badge({ tone = "neutral", icon: Icon, className = "", children }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-medium ${tones[tone]} ${className}`}
    >
      {Icon && <Icon className="h-3 w-3" aria-hidden="true" />}
      {children}
    </span>
  );
}

const statusTone = { online: "ok", offline: "danger", unknown: "warn" };

/**
 * Camera connectivity. The dot alone would encode meaning in colour only, so
 * the label always ships with it (WCAG 1.4.1).
 */
export function StatusPill({ status, className = "" }) {
  const tone = statusTone[status] || "neutral";
  const dot = { ok: "bg-ok", danger: "bg-danger", warn: "bg-warn", neutral: "bg-ink-3" }[tone];
  return (
    <Badge tone={tone} className={className}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {status || "unknown"}
    </Badge>
  );
}
