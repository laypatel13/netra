import { useId } from "react";

const control =
  "w-full rounded-xl border border-line-control bg-surface px-3 text-sm text-ink " +
  "transition-colors duration-150 placeholder:text-ink-3 " +
  "hover:border-brand disabled:cursor-not-allowed disabled:opacity-60";

/**
 * Every input gets a real, visible <label> - placeholder-only labelling was
 * one of the concrete problems with the first version of these forms.
 */
export default function Field({ label, hint, error, required, children, className = "" }) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-err` : null]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-ink-2">
        {label}
        {required && (
          <span className="ml-0.5 text-danger" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="sr-only"> (required)</span>}
      </label>

      {children({
        id,
        required,
        "aria-describedby": describedBy || undefined,
        "aria-invalid": error ? true : undefined,
      })}

      {hint && !error && (
        <p id={`${id}-hint`} className="text-2xs leading-normal text-ink-3">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} role="alert" className="text-2xs font-medium text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function Input({ className = "", ...props }) {
  return <input className={`${control} h-10 ${className}`} {...props} />;
}

export function Select({ className = "", children, ...props }) {
  return (
    <select className={`${control} h-10 cursor-pointer pr-8 ${className}`} {...props}>
      {children}
    </select>
  );
}

export function FileInput({ className = "", ...props }) {
  return (
    <input
      type="file"
      className={
        "w-full cursor-pointer rounded-xl border border-dashed border-line-control bg-surface-2 p-2.5 text-[13px] text-ink-2 " +
        "file:mr-3 file:cursor-pointer file:rounded-lg file:border-0 file:bg-brand file:px-3 file:py-1.5 " +
        "file:text-[13px] file:font-medium file:text-on-brand hover:file:bg-brand-hover " +
        className
      }
      {...props}
    />
  );
}
