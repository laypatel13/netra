import { Suspense, lazy, useEffect, useRef } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { advanceRing } from "./lib/ringTurns.js";
import { isAuthenticated } from "./lib/auth.js";
import AppShell from "./components/AppShell.jsx";
import Landing from "./pages/Landing.jsx";
import Login from "./pages/Login.jsx";
import NotFound from "./pages/NotFound.jsx";
import { SkeletonGrid } from "./components/ui/Feedback.jsx";

/**
 * The operational pages pull in Leaflet and hls.js - together the bulk of the
 * bundle. Split them out so a first visit to the landing page doesn't pay for
 * a map engine and a video player it never renders.
 */
const Dashboard = lazy(() => import("./pages/Dashboard.jsx"));
const Registry = lazy(() => import("./pages/Registry.jsx"));
const LiveViewer = lazy(() => import("./pages/LiveViewer.jsx"));
const GapAnalysis = lazy(() => import("./pages/GapAnalysis.jsx"));
const Watchlist = lazy(() => import("./pages/Watchlist.jsx"));

/** Internal type-specimen tool - deliberately not in the product navigation. */
const TypeSpecimen = lazy(() => import("./pages/TypeSpecimen.jsx"));

/**
 * On a client-side route change the browser keeps both scroll position and
 * focus where they were, which strands screen-reader and keyboard users at the
 * bottom of the previous page. Reset both.
 */
function RouteChangeEffects() {
  const { pathname } = useLocation();
  const lastPath = useRef(null);

  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById("main")?.focus({ preventScroll: true });

    // Advance the mark's ring one notch per navigation.
    //
    // Guarded on the pathname itself rather than a "have I run before" boolean:
    // StrictMode replays effects on mount in development, and a boolean guard
    // gets consumed by the first run, so the replay slipped through and burned a
    // turn at startup - the logo span once on every page load. Keying on the
    // path makes this idempotent: a replay for the same route is a no-op, and
    // the very first route only records itself.
    if (lastPath.current === pathname) return;
    const isFirstRoute = lastPath.current === null;
    lastPath.current = pathname;
    if (!isFirstRoute) advanceRing();
  }, [pathname]);

  return null;
}

/** Gates the deployed demo behind the seeded login (frontend-only - see lib/auth.js). */
function RequireAuth({ children }) {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return children;
}

const withShell = (element) => (
  <RequireAuth>
    <AppShell>
      <Suspense fallback={<SkeletonGrid items={4} className="sm:grid-cols-2" />}>{element}</Suspense>
    </AppShell>
  </RequireAuth>
);

export default function App() {
  return (
    <>
      <RouteChangeEffects />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />

        <Route path="/app" element={withShell(<Dashboard />)} />
        <Route path="/app/registry" element={withShell(<Registry />)} />
        <Route path="/app/live" element={withShell(<LiveViewer />)} />
        <Route path="/app/gap-analysis" element={withShell(<GapAnalysis />)} />
        <Route path="/app/watchlist" element={withShell(<Watchlist />)} />

        <Route
          path="/type"
          element={
            <Suspense fallback={null}>
              <TypeSpecimen />
            </Suspense>
          }
        />

        {/* The pre-redesign URLs are documented in test/README.md - keep them working. */}
        <Route path="/dashboard" element={<Navigate to="/app" replace />} />
        <Route path="/registry" element={<Navigate to="/app/registry" replace />} />
        <Route path="/live" element={<Navigate to="/app/live" replace />} />
        <Route path="/gap-analysis" element={<Navigate to="/app/gap-analysis" replace />} />
        <Route path="/watchlist" element={<Navigate to="/app/watchlist" replace />} />

        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}
