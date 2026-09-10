import { AlertTriangle, Inbox, PlugZap } from "lucide-react";
import Button from "./Button.jsx";

export function EmptyState({ icon: Icon = Inbox, title, description, action, className = "" }) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-2xl border border-dashed border-line-strong bg-surface-2/50 px-6 py-10 text-center ${className}`}
    >
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-surface-3 text-ink-3">
        <Icon className="h-5 w-5" aria-hidden="true" />
      </span>
      <p className="text-[15px] font-medium text-ink">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-ink-3">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * Failures state the cause and offer a way forward - an error with no recovery
 * path just strands the operator mid-investigation.
 */
export function ErrorState({ error, onRetry, className = "" }) {
  const offline = error?.offline;
  const Icon = offline ? PlugZap : AlertTriangle;
  return (
    <div
      role="alert"
      className={`flex flex-wrap items-center gap-3 rounded-2xl border border-danger/30 bg-danger-soft px-4 py-3 ${className}`}
    >
      <Icon className="h-4.5 w-4.5 shrink-0 text-danger" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-danger">
          {offline ? "Backend unreachable" : "Something went wrong"}
        </p>
        <p className="mt-0.5 text-[13px] text-ink-2">
          {error?.message || "Unknown error."}
          {offline && " Start it with: uvicorn app.main:app --reload"}
        </p>
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function SkeletonRows({ rows = 3, className = "" }) {
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-14 w-full rounded-xl" />
      ))}
    </div>
  );
}

export function SkeletonGrid({ items = 4, className = "" }) {
  return (
    <div className={`grid gap-3 ${className}`} aria-hidden="true">
      {Array.from({ length: items }).map((_, i) => (
        <div key={i} className="skeleton h-24 w-full rounded-2xl" />
      ))}
    </div>
  );
}
