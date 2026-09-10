import { forwardRef } from "react";
import { Loader2 } from "lucide-react";

const base =
  "inline-flex items-center justify-center gap-2 rounded-xl font-medium " +
  "transition-[background-color,border-color,color,box-shadow,transform] duration-150 " +
  "cursor-pointer select-none whitespace-nowrap " +
  "active:translate-y-px " +
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0";

const variants = {
  primary: "bg-brand text-on-brand shadow-xs hover:bg-brand-hover",
  secondary: "border border-line-control bg-surface text-ink shadow-xs hover:bg-surface-2 hover:border-brand",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  danger: "bg-danger text-white hover:brightness-110 dark:text-[rgb(var(--c-surface))]",
  gold: "bg-highlight text-[rgb(var(--c-on-highlight))] shadow-xs hover:brightness-95",
};

const sizes = {
  sm: "h-9 px-3 text-[13px]",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-[15px]",
  icon: "h-10 w-10",
};

const Button = forwardRef(function Button(
  { variant = "primary", size = "md", loading = false, disabled, className = "", children, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
});

export default Button;
