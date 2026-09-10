import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import {
  BarChart3,
  ExternalLink,
  LayoutDashboard,
  Map as MapIcon,
  Menu,
  ShieldAlert,
  Video,
  X,
} from "lucide-react";
import NetraLogo from "./NetraLogo.jsx";
import ThemeToggle from "./ui/ThemeToggle.jsx";
import { api } from "../lib/api.js";

const NAV = [
  { to: "/app", end: true, label: "Control room", icon: LayoutDashboard, model: null },
  { to: "/app/registry", label: "Registry & GIS", icon: MapIcon, model: "1" },
  { to: "/app/live", label: "Live viewer", icon: Video, model: "2" },
  { to: "/app/gap-analysis", label: "Gap analysis", icon: BarChart3, model: "1" },
  { to: "/app/watchlist", label: "Watchlist", icon: ShieldAlert, model: "2" },
];

const ALERT_POLL_MS = 10000;

/**
 * Alert count for the sidebar badge. Polled at the shell level so an operator
 * sitting on the map still sees a match land, instead of only noticing it if
 * they happen to be on the Watchlist page.
 */
function useAlertCount() {
  const [n, setN] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api("/watchlist/alerts/recent")
        .then((d) => alive && setN(Array.isArray(d) ? d.length : 0))
        .catch(() => alive && setN(null));
    load();
    const t = setInterval(load, ALERT_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return n;
}

function NavItems({ alertCount, onNavigate }) {
  return (
    <nav className="flex flex-col gap-1" aria-label="Sections">
      {NAV.map(({ to, end, label, icon: Icon, model }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors duration-150 " +
            (isActive
              ? "bg-brand-soft font-semibold text-brand"
              : "font-medium text-ink-2 hover:bg-surface-2 hover:text-ink")
          }
        >
          {({ isActive }) => (
            <>
              <span
                aria-hidden="true"
                className={
                  "absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-150 " +
                  (isActive ? "opacity-100" : "opacity-0")
                }
              />
              <Icon className="h-4.5 w-4.5 shrink-0" aria-hidden="true" />
              <span className="flex-1 truncate">{label}</span>

              {label === "Watchlist" && alertCount > 0 && (
                <span className="tnum inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-2xs font-semibold text-white dark:text-[rgb(var(--c-surface))]">
                  {alertCount > 99 ? "99+" : alertCount}
                </span>
              )}
              {model && (
                <span className="shrink-0 rounded border border-line px-1.5 py-px text-[10px] font-semibold text-ink-3">
                  M{model}
                </span>
              )}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function SidebarFooter() {
  return (
    <div className="mt-auto border-t border-line px-3 pt-4">
      <Link
        to="/"
        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink"
      >
        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        Back to overview
      </Link>
      <p className="px-2 pt-2 text-2xs leading-relaxed text-ink-3">
        Models 1 &amp; 2 - Registry &amp; GIS, Unified viewing &amp; analytics.
      </p>
    </div>
  );
}

export default function AppShell({ children }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const alertCount = useAlertCount();
  const location = useLocation();
  const closeBtnRef = useRef(null);
  const mainRef = useRef(null);

  const close = useCallback(() => setDrawerOpen(false), []);

  // Close the drawer on navigation, and on Escape (every overlay needs an
  // escape route).
  useEffect(() => {
    close();
  }, [location.pathname, close]);

  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    closeBtnRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [drawerOpen, close]);

  return (
    <div className="min-h-dvh bg-bg">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-brand focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-on-brand"
      >
        Skip to main content
      </a>

      {/* Screen-reader announcement for background alert activity. Polite, and
          deliberately a whole sentence rather than a bare number. */}
      <p className="sr-only" aria-live="polite">
        {alertCount > 0
          ? `${alertCount} watchlist ${alertCount === 1 ? "alert" : "alerts"} among recent detections.`
          : ""}
      </p>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[264px] flex-col border-r border-line bg-surface lg:flex">
        <div className="px-5 py-5">
          <Link to="/" className="inline-block rounded-lg">
            <NetraLogo size={44} tagline />
          </Link>
        </div>
        <div className="px-3">
          <NavItems alertCount={alertCount} />
        </div>
        <SidebarFooter />
        <div className="px-5 pb-5" />
      </aside>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-[rgb(10_10_12)]/70 backdrop-blur-sm"
            onClick={close}
            aria-hidden="true"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-[86%] max-w-xs flex-col border-r border-line bg-surface shadow-pop"
          >
            <div className="flex items-center justify-between px-5 py-5">
              <NetraLogo size={36} />
              <button
                ref={closeBtnRef}
                type="button"
                onClick={close}
                className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border border-line text-ink-2 hover:bg-surface-2 hover:text-ink"
              >
                <X className="h-4 w-4" aria-hidden="true" />
                <span className="sr-only">Close navigation</span>
              </button>
            </div>
            <div className="px-3">
              <NavItems alertCount={alertCount} onNavigate={close} />
            </div>
            <SidebarFooter />
            <div className="px-5 pb-5" />
          </div>
        </div>
      )}

      <div className="lg:pl-[264px]">
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur-md sm:px-6">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border border-line text-ink-2 hover:bg-surface-2 hover:text-ink lg:hidden"
          >
            <Menu className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">Open navigation</span>
          </button>

          <Link to="/" className="rounded-lg lg:hidden">
            <NetraLogo size={36} />
          </Link>

          <div className="hidden min-w-0 flex-1 lg:block">
            <p className="truncate text-[13px] text-ink-3">
              Unified CCTV registry &amp; analytics -{" "}
              <span className="font-medium text-ink-2">Sentinel Gujarat</span>
            </p>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
          </div>
        </header>

        <main id="main" ref={mainRef} tabIndex={-1} className="px-4 py-6 focus:outline-none sm:px-6 lg:px-8 lg:py-8">
          <div className="mx-auto max-w-shell">{children}</div>
        </main>
      </div>
    </div>
  );
}
