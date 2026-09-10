/**
 * Netra design tokens.
 *
 * Every colour here resolves to a CSS custom property defined in
 * src/styles/index.css as an "R G B" channel triplet, so Tailwind's opacity
 * modifiers (bg-surface/60) keep working while the actual values swap between
 * light and dark under [data-theme]. Components should never hardcode a hex -
 * that's what broke visual consistency in the first version of this frontend.
 */
const withOpacity = (v) => `rgb(var(${v}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: withOpacity("--c-bg"),
        surface: withOpacity("--c-surface"),
        "surface-2": withOpacity("--c-surface-2"),
        "surface-3": withOpacity("--c-surface-3"),

        ink: withOpacity("--c-ink"),
        "ink-2": withOpacity("--c-ink-2"),
        "ink-3": withOpacity("--c-ink-3"),

        line: withOpacity("--c-border"),
        "line-strong": withOpacity("--c-border-strong"),
        "line-control": withOpacity("--c-control-border"),

        brand: withOpacity("--c-brand"),
        "brand-hover": withOpacity("--c-brand-hover"),
        "brand-soft": withOpacity("--c-brand-soft"),
        "on-brand": withOpacity("--c-on-brand"),

        // The signal-yellow family. `highlight` is the fill, `gold-ink` is the
        // ink that goes on it (and reads as text on any surface), `gold` is the
        // decorative border/tint companion - non-text use only. Names predate
        // the Quiet Canvas palette; they resolve to yellow, not gold.
        azure: withOpacity("--c-azure"),
        gold: withOpacity("--c-gold"),
        "gold-ink": withOpacity("--c-gold-ink"),
        highlight: withOpacity("--c-highlight"),

        ok: withOpacity("--c-ok"),
        "ok-soft": withOpacity("--c-ok-soft"),
        warn: withOpacity("--c-warn"),
        "warn-soft": withOpacity("--c-warn-soft"),
        danger: withOpacity("--c-danger"),
        "danger-soft": withOpacity("--c-danger-soft"),
      },
      fontFamily: {
        // Display is a serif on purpose: it's the only thing that creates real
        // hierarchy against Poppins' monoline geometry. Reserved for >=24px.
        display: ["Literata", "Georgia", "Times New Roman", "serif"],
        sans: ["Poppins", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        small: ["Poppins", "system-ui", "-apple-system", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      borderRadius: {
        md: "0.625rem",
        lg: "0.875rem",
        xl: "1.125rem",
        "2xl": "1.5rem",
        "3xl": "1.875rem",
      },
      boxShadow: {
        xs: "var(--shadow-xs)",
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)",
        pop: "var(--shadow-pop)",
      },
      maxWidth: { shell: "96rem" },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "none" },
        },
        "pulse-dot": { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.25" } },
        // Distance is a per-element variable so the floating cards can each
        // travel a different amount - identical motion across all of them
        // reads as a mechanism rather than as floating.
        drift: {
          "0%,100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(var(--drift-y, -18px))" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: {
        "fade-up": "fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
        drift: "drift 6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
