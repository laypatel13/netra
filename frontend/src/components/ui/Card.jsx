export function Card({ className = "", children, ...props }) {
  return (
    <div className={`rounded-2xl border border-line bg-surface shadow-card ${className}`} {...props}>
      {children}
    </div>
  );
}

export function CardHeader({ title, description, actions, className = "" }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4 ${className}`}>
      <div className="min-w-0">
        <h3 className="text-[15px] font-medium text-ink">{title}</h3>
        {description && <p className="mt-1 max-w-prose text-[13px] text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className = "", children }) {
  return <div className={`p-5 ${className}`}>{children}</div>;
}

/** Page-level section heading, used above grids that aren't inside a Card. */
export function SectionHeading({ title, description, actions, className = "" }) {
  return (
    <div className={`mb-4 flex flex-wrap items-end justify-between gap-3 ${className}`}>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {description && <p className="mt-1 max-w-prose text-[13px] text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
