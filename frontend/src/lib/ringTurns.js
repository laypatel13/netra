/**
 * The adaptation counter behind the Netra mark's ring.
 *
 * Deliberately a module-scoped store rather than component state. Three things
 * depend on it living above the route tree:
 *
 *   1. Navigating "/" -> "/app" unmounts the landing page's logo and mounts the
 *      app shell's. A counter held inside the component would start from zero
 *      there and the first entry into the app would show no motion at all.
 *   2. The shell renders the logo three times (sidebar, mobile drawer, top bar);
 *      they must all sit at the same angle.
 *   3. It keeps the trigger in exactly one place - the route-change effect.
 *
 * Read it with useSyncExternalStore; advance it from App's RouteChangeEffects.
 */
let turns = 0;
const listeners = new Set();

export function advanceRing() {
  turns += 1;
  listeners.forEach((notify) => notify());
}

export function subscribeRing(notify) {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

export function getRingTurns() {
  return turns;
}
