import { Moon, Sun } from "lucide-react";
import { useTheme } from "../../lib/theme.jsx";

export default function ThemeToggle({ className = "" }) {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={dark}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={
        "inline-flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full " +
        "border border-line bg-surface text-ink-2 transition-colors duration-150 " +
        "hover:bg-surface-2 hover:text-ink " + className
      }
    >
      {dark ? <Sun className="h-4 w-4" aria-hidden="true" /> : <Moon className="h-4 w-4" aria-hidden="true" />}
      <span className="sr-only">{dark ? "Switch to light theme" : "Switch to dark theme"}</span>
    </button>
  );
}
