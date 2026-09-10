/**
 * Two-or-three-way mode switch. A <select> hid the fact that "search by
 * description" exists at all - the whole point of the attribute pivot is that
 * an operator can see both routes into the data side by side.
 */
export default function Segmented({ value, onChange, options, label, className = "" }) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={`inline-flex rounded-xl border border-line-control bg-surface-2 p-1 ${className}`}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className={
              "cursor-pointer rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors duration-150 " +
              (active
                ? "bg-surface text-ink shadow-xs"
                : "text-ink-3 hover:text-ink")
            }
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
