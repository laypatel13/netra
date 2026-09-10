import { useSyncExternalStore } from "react";
import { getRingTurns, subscribeRing } from "../lib/ringTurns.js";

/**
 * Netra identity.
 *
 * "Netra" means eyes, so the mark is an eye that doubles as a lens: a pointed
 * aperture with a ringed pupil, encircled by a single unbroken ring carrying
 * one marker node.
 *
 * The ring turns a quarter of a revolution on every navigation and shifts to
 * the next colour as it goes - the idea is Mahoraga's dharma wheel, which
 * clicks round one notch each time he adapts. Here the adaptation is the
 * operator moving through the system, so the mark keeps a running count of
 * where they've been.
 *
 * The tile and the eye are fixed hexes, not theme tokens: a logo has to look
 * identical in light and dark, on a letterhead and in a slide. The ring is the
 * single deliberate exception, and it varies by turn count rather than by
 * theme. Everything around the mark (the wordmark) still uses theme tokens.
 *
 * NOTE: this is Netra's own mark. The Gujarat Police emblem and the official
 * Sentinel logo are not reproduced here - drop the official assets into
 * public/ and render them alongside this if the submission calls for it.
 */
const TILE = "#1C1C1F";

const DEGREES_PER_TURN = 90;

/**
 * One colour per quarter-turn, so four notches carry the ring through a full
 * revolution AND a full colour cycle - the wheel and the palette come home
 * together. Only the ring and its node change; the tile and white eye stay
 * fixed, or the logo would stop being recognisable as one mark.
 * The teal is a nod to the colour in the original mockup.
 */
const RING_STATES = ["#F0B429", "#5AA0FF", "#2DD4A7", "#FF7B6E"];

/**
 * Geometry note: drawn on a 48-unit viewBox. Being an SVG it never actually
 * pixelates - but detail finer than the pixel grid at a 30-38px render can only
 * resolve as a smudge, which reads the same way. So the rule here is that every
 * element must survive the smallest size it ships at: nothing below roughly
 * 2 CSS px, and no antialiased cap ends where a continuous stroke will do.
 */
export function NetraMark({ size = 36, className = "" }) {
  const turns = useSyncExternalStore(subscribeRing, getRingTurns, getRingTurns);
  const ring = RING_STATES[turns % RING_STATES.length];

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="48" height="48" rx="10.5" fill={TILE} />

      {/* The ring turns; the eye never does.

          Origin is pinned to the viewBox centre rather than `fill-box` +
          `center`. The group holds a full circle plus a single node at the top,
          so its bounding box runs y 7.2 to 38.6 - a centre of 22.9, over a unit
          high. `fill-box` would rotate about that and make the ring orbit
          instead of spin. `view-box` measures against the 48x48 viewBox, so
          24,24 is exactly the middle of the tile.

          Animating a cumulative angle through a transition (rather than
          re-triggering a keyframe) means the browser interpolates from whatever
          the current angle is, so rapid navigation re-targets cleanly with no
          snap-back. */}
      <g
        style={{
          transform: `rotate(${turns * DEGREES_PER_TURN}deg)`,
          transformBox: "view-box",
          transformOrigin: "24px 24px",
          transition: "transform 600ms cubic-bezier(0.2, 0.8, 0.2, 1)",
        }}
      >
        {/* Solid, unbroken ring. The dashed version had eight round cap ends,
            each antialiased over a sub-pixel edge at 30px - that softness was
            most of what read as "low quality". A continuous stroke has none. */}
        <circle
          cx="24"
          cy="24"
          r="14.6"
          fill="none"
          stroke={ring}
          strokeOpacity="0.6"
          strokeWidth="1.4"
          style={{ transition: "stroke 600ms cubic-bezier(0.2, 0.8, 0.2, 1)" }}
        />
        {/* A solid ring is perfectly rotationally symmetric, so without this
            node the turn would be completely invisible. It is the only thing
            that makes the rotation readable. */}
        <circle
          cx="24"
          cy="9.4"
          r="2.2"
          fill={ring}
          style={{ transition: "fill 600ms cubic-bezier(0.2, 0.8, 0.2, 1)" }}
        />
      </g>

      {/* Eye: a pointed lens with a ringed pupil.
          The lens is a true vesica - two quadratic arcs meeting at sharp points
          - rather than the earlier rounded almond, and the pupil is a ring
          rather than a solid dot, both taken from the reference mark.
          Proportions follow it too: the pupil is about two thirds of the lens
          height and the ring about a seventh of it, which puts the ring at
          1.3-1.6 CSS px across the 36-44px sizes this ships at. Any thinner and
          it would fall under the pixel grid and smear. */}
      <g>
        <path d="M13 24Q24 11.8 35 24Q24 36.2 13 24Z" fill="#FFFFFF" />
        <circle cx="24" cy="24" r="4" fill={TILE} />
        <circle cx="24" cy="24" r="2.3" fill="#FFFFFF" />
      </g>
    </svg>
  );
}

export default function NetraLogo({ size = 36, showWordmark = true, tagline = false, className = "" }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <NetraMark size={size} />
      {showWordmark && (
        <span className="flex flex-col leading-none">
          <span className="font-display text-[1.3rem] font-semibold tracking-tight text-ink">
            netra
          </span>
          {tagline && (
            <span className="mt-1 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
              Sentinel Gujarat
            </span>
          )}
        </span>
      )}
      <span className="sr-only">Netra - unified CCTV registry and analytics</span>
    </span>
  );
}
