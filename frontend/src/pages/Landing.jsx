import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BellRing,
  Camera,
  Building2,
  Fingerprint,
  Layers,
  MapPin,
  Radar,
  Route,
  ScanLine,
  ShieldCheck,
  Video,
} from "lucide-react";
import NetraLogo from "../components/NetraLogo.jsx";
import ThemeToggle from "../components/ui/ThemeToggle.jsx";
import Button from "../components/ui/Button.jsx";
import Badge from "../components/ui/Badge.jsx";
import Reveal from "../components/Reveal.jsx";
import { api } from "../lib/api.js";
import { count } from "../lib/format.js";

/* -- Floating capability cards --------------------------------------------
   Each card carries an icon standing for one real part of the pipeline -
   camera, map, plate, route, alert - rather than an abstract shape. They sit
   upright (no tilt) so the icons read straight, and they're anchored to the
   fixed-width rail below rather than to viewport percentages, which is what
   made them drift apart on a wide monitor.                                  */

function Plinth({
  icon: Icon,
  tone = "brand",
  className = "",
  delay = 0,
  duration = 6,
  distance = 18,
  label,
}) {
  const tones = {
    brand: "text-brand",
    gold: "text-gold",
    ok: "text-ok",
    danger: "text-danger",
  };
  // Positioning and animation are kept on separate elements on purpose: the
  // drift keyframes animate `transform`, which would otherwise clobber the
  // `-translate-x-1/2` that centres the bottom card and push it off-axis.
  return (
    <div
      className={`pointer-events-none absolute ${className}`}
      aria-hidden="true"
      title={label}
    >
      <div
        className="animate-drift motion-reduce:animate-none"
        style={{
          animationDelay: `${delay}ms`,
          animationDuration: `${duration}s`,
          "--drift-y": `-${distance}px`,
        }}
      >
        <div
          className={`grid h-[76px] w-[104px] place-items-center rounded-[22px] border border-line/70 bg-surface shadow-lift ${tones[tone]}`}
        >
          <Icon className="h-8 w-8" />
        </div>
      </div>
    </div>
  );
}

/* -- Marketing nav -------------------------------------------------------- */

const NAV_LINKS = [
  { href: "#problem", label: "The problem" },
  { href: "#how", label: "How it works" },
  { href: "#capabilities", label: "Capabilities" },
  { href: "#honest", label: "What it can't do" },
];

function TopNav() {
  return (
    <header className="sticky top-0 z-40 px-4 pt-4 sm:px-6">
      <div className="mx-auto flex max-w-5xl items-center gap-3 rounded-full border border-line bg-surface/85 py-2 pl-4 pr-2 shadow-card backdrop-blur-md">
        <Link to="/" className="rounded-lg">
          <NetraLogo size={36} />
        </Link>

        <nav className="ml-4 hidden items-center gap-1 md:flex" aria-label="Page sections">
          {NAV_LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="rounded-full px-3 py-1.5 text-[13px] font-medium text-ink-2 transition-colors duration-150 hover:bg-surface-2 hover:text-ink"
            >
              {l.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <Link to="/app">
            <Button size="sm" className="rounded-full px-4">
              Open control room
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </Link>
        </div>
      </div>
    </header>
  );
}

/* -- Live figures ---------------------------------------------------------
   Pulled from the running backend so the landing page never advertises a
   number the platform can't actually show. Falls back to an em dash.       */

function useCoverage() {
  const [summary, setSummary] = useState(null);
  useEffect(() => {
    let alive = true;
    api("/cameras/gap-analysis")
      .then((d) => alive && setSummary(d?.summary ?? null))
      .catch(() => alive && setSummary(null));
    return () => {
      alive = false;
    };
  }, []);
  return summary;
}

function Figure({ value, label, sub }) {
  return (
    <div className="px-2 py-4 text-center sm:px-4">
      <div className="tnum font-display text-3xl font-semibold text-ink sm:text-4xl">{value}</div>
      <div className="mt-1.5 text-[13px] font-medium text-ink-2">{label}</div>
      {sub && <div className="mt-0.5 text-2xs text-ink-3">{sub}</div>}
    </div>
  );
}

/* -- Page ----------------------------------------------------------------- */

export default function Landing() {
  const summary = useCoverage();

  return (
    <div className="min-h-dvh bg-bg">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-brand focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-on-brand"
      >
        Skip to main content
      </a>

      <TopNav />

      <main id="main">
        {/* ---------------------------------------------------------------- Hero */}
        <section className="relative overflow-hidden px-4 pb-16 pt-16 sm:px-6 sm:pt-24 md:pb-36">
          {/* Soft ground glow, keeps the hero from reading as a flat slab */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute left-1/2 top-0 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-brand/[0.06] blur-3xl"
          />

          {/* A fixed-width centred rail, so the gap between the cards and the
              headline stays constant instead of widening with the viewport. */}
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 mx-auto max-w-[1060px]">
            {/* Two left, three right, alternating down the page - if the two
                sides shared rows the group would read as a table instead of a
                scatter. Both columns sit flush to the rail up top and tuck
                inward as they descend, which follows the headline's own shape:
                the h1 is at its widest near the top, so a card that moves in
                there crowds it. */}
            <Plinth icon={ScanLine} tone="brand" label="ANPR" className="left-0 top-[30%] hidden xl:block" delay={0} duration={5.4} distance={22} />
            <Plinth icon={MapPin} tone="danger" label="GIS registry" className="left-[4%] top-[58%] hidden lg:block" delay={900} duration={6.8} distance={16} />

            <Plinth icon={BellRing} tone="gold" label="Alerts" className="right-0 top-[18%] hidden xl:block" delay={450} duration={6.1} distance={19} />
            <Plinth icon={Camera} tone="brand" label="Cameras" className="right-0 top-[42%] hidden xl:block" delay={1800} duration={5.0} distance={24} />
            <Plinth icon={Route} tone="ok" label="Route tracing" className="right-[3%] top-[66%] hidden lg:block" delay={1300} duration={7.4} distance={15} />
          </div>

          <div className="relative mx-auto max-w-3xl text-center">
            <Reveal>
              <Badge tone="neutral" icon={ShieldCheck} className="mb-6">
                Gujarat Police Innovation Challenge 2026 · Sentinel
              </Badge>
            </Reveal>

            <Reveal delay={80}>
              <h1 className="font-display text-[2.6rem] font-semibold leading-[1.26] tracking-[-0.02em] text-ink sm:text-6xl/[1.26]">
                One network.
                <br className="hidden sm:block" /> Every camera.{" "}
                <span className="mark">
                  <span>Every vehicle.</span>
                </span>
              </h1>
            </Reveal>

            <Reveal delay={160}>
              <p className="mx-auto mt-6 max-w-xl text-base leading-relaxed text-ink-2 sm:text-lg">
                Netra turns 26 fragmented, department-owned CCTV systems into one searchable
                network - trace a vehicle across every camera it passed, and get alerted the
                moment a watchlisted one is seen.
              </p>
            </Reveal>

            <Reveal delay={240}>
              <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
                <Link to="/app">
                  <Button size="lg" className="rounded-full">
                    Open the control room
                    <ArrowRight className="h-4.5 w-4.5" aria-hidden="true" />
                  </Button>
                </Link>
                <a href="#how">
                  <Button size="lg" variant="secondary" className="rounded-full">
                    See how it works
                  </Button>
                </a>
              </div>
            </Reveal>

            <Reveal delay={320}>
              <p className="mt-5 text-[13px] text-ink-3">
                Running software against live sandbox feeds - not a mockup.
              </p>
            </Reveal>
          </div>
        </section>

        {/* ------------------------------------------------------------ Figures */}
        <section className="px-4 pb-20 sm:px-6">
          <Reveal className="mx-auto max-w-5xl">
            <div className="grid grid-cols-2 divide-line rounded-3xl border border-line bg-surface shadow-card sm:grid-cols-4 sm:divide-x">
              <Figure value="26" label="Departments" sub="fragmented, independent systems" />
              <Figure
                value={summary ? count(summary.total_cameras) : "-"}
                label="Cameras onboarded"
                sub="live from the registry"
              />
              <Figure value="~1,000 km" label="Geographic spread" sub="edge to edge of the network" />
              <Figure value="80,000" label="Scale target" sub="documented roadmap" />
            </div>
          </Reveal>
        </section>

        {/* ------------------------------------------------------------ Problem */}
        <section id="problem" className="scroll-mt-24 border-t border-line bg-surface px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-5xl">
            <Reveal className="max-w-2xl">
              <h2 className="font-display text-3xl font-semibold leading-tight text-ink sm:text-4xl">
                Every camera system is its own island.
              </h2>
              <p className="mt-4 text-base leading-relaxed text-ink-2">
                A suspect vehicle is seen on Camera 1 during a crime. Right now, nobody knows it
                also passed Camera 13 an hour later, and Camera 15 after that - because nobody is
                watching all of them at once.
              </p>
            </Reveal>

            <div className="mt-10 grid gap-4 sm:grid-cols-3">
              {[
                {
                  icon: Building2,
                  title: "No shared inventory",
                  body: "Analog and IP, cloud and local, 7-15 day retention - 26 departments with no single list of what exists or where.",
                },
                {
                  icon: Radar,
                  title: "No cross-camera view",
                  body: "Each sighting dies inside the system that recorded it. Stitching a route together is manual, slow, and usually never happens.",
                },
                {
                  icon: BellRing,
                  title: "No automatic alerting",
                  body: "A stolen vehicle can drive past a working camera and nobody is told, because the match is only ever made by a person querying by hand.",
                },
              ].map((c, i) => (
                <Reveal key={c.title} delay={i * 90}>
                  <div className="h-full rounded-2xl border border-line bg-bg p-5">
                    <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-danger-soft text-danger">
                      <c.icon className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <h3 className="text-[15px] font-medium text-ink">{c.title}</h3>
                    <p className="mt-2 text-[13px] leading-relaxed text-ink-3">{c.body}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------- How it works */}
        <section id="how" className="scroll-mt-24 px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-5xl">
            <Reveal className="max-w-2xl">
              <Badge tone="brand" className="mb-4">End to end</Badge>
              <h2 className="font-display text-3xl font-semibold leading-tight text-ink sm:text-4xl">
                Onboard, watch, detect, trace.
              </h2>
              <p className="mt-4 text-base leading-relaxed text-ink-2">
                No department has to replace or expose its infrastructure. Netra reads what already
                exists and adds the layer nobody has: one place where the sightings meet.
              </p>
            </Reveal>

            <ol className="mt-10 grid gap-4 md:grid-cols-2">
              {[
                {
                  n: "01",
                  icon: Layers,
                  title: "Onboard the estate",
                  body: "Bulk CSV, JSON, or the catalogue API. Every camera lands in a GIS registry with department, type, storage and retention - and a gap-analysis report immediately shows which departments have no coverage at all.",
                },
                {
                  n: "02",
                  icon: Video,
                  title: "Watch it live, in one grid",
                  body: "Authenticated HLS relayed through the backend, RTSP over TCP for inference. Tiles stagger their connections and reconnect with capped backoff, so a transient join failure never kills a feed permanently.",
                },
                {
                  n: "03",
                  icon: ScanLine,
                  title: "Detect what passes",
                  body: "YOLO finds vehicles, OCR reads the plate when it's legible, and a thumbnail plus type and colour are recorded either way. Timestamps come from the stream's PTS, never wall-clock.",
                },
                {
                  n: "04",
                  icon: Route,
                  title: "Trace it, or be told",
                  body: "Query a plate - or just \"red car\" - and get the path drawn across the map in order. If the vehicle is already on a watchlist, the alert fires the instant it's seen, with the tier stated plainly.",
                },
              ].map((s, i) => (
                <Reveal as="li" key={s.n} delay={i * 80}>
                  <div className="flex h-full gap-4 rounded-2xl border border-line bg-surface p-5 shadow-card">
                    <div className="flex flex-col items-center gap-2">
                      <span className="tnum text-sm font-semibold text-ink-2">{s.n}</span>
                      <span className="h-full w-px bg-line" aria-hidden="true" />
                    </div>
                    <div className="min-w-0">
                      <span className="mb-2.5 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-brand-soft text-brand">
                        <s.icon className="h-5 w-5" aria-hidden="true" />
                      </span>
                      <h3 className="text-[15px] font-medium text-ink">{s.title}</h3>
                      <p className="mt-2 text-[13px] leading-relaxed text-ink-3">{s.body}</p>
                    </div>
                  </div>
                </Reveal>
              ))}
            </ol>
          </div>
        </section>

        {/* ------------------------------------------------------- Capabilities */}
        <section id="capabilities" className="scroll-mt-24 border-y border-line bg-surface px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-5xl">
            <Reveal className="max-w-2xl">
              <h2 className="font-display text-3xl font-semibold leading-tight text-ink sm:text-4xl">
                Two reference models, built properly.
              </h2>
              <p className="mt-4 text-base leading-relaxed text-ink-2">
                Model 1 is mandatory. Model 2 is where the analytics live. Netra implements both,
                with the audit trail and role checks a police deployment actually needs.
              </p>
            </Reveal>

            <div className="mt-10 grid gap-4 md:grid-cols-2">
              <Reveal>
                <div className="h-full rounded-2xl border border-line bg-bg p-6">
                  <Badge tone="brand" className="mb-4">Model 1 - mandatory</Badge>
                  <h3 className="text-xl font-semibold text-ink">Registry &amp; GIS foundation</h3>
                  <ul className="mt-4 flex flex-col gap-2.5">
                    {[
                      "Manual, bulk CSV and bulk JSON onboarding",
                      "Every camera on a live GIS map, filterable by department and status",
                      "Gap-analysis report - per-department coverage, stale cameras, missing departments",
                      "Role-based access control with a full audit trail",
                    ].map((t) => (
                      <li key={t} className="flex gap-2.5 text-[13px] leading-relaxed text-ink-2">
                        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden="true" />
                        {t}
                      </li>
                    ))}
                  </ul>
                </div>
              </Reveal>

              <Reveal delay={90}>
                <div className="h-full rounded-2xl border border-line bg-bg p-6">
                  <Badge tone="neutral" className="mb-4">Model 2</Badge>
                  <h3 className="text-xl font-semibold text-ink">Unified viewing &amp; analytics</h3>
                  <ul className="mt-4 flex flex-col gap-2.5">
                    {[
                      "Multi-source live grid over authenticated HLS, with reconnect-with-backoff",
                      "ANPR plus vehicle type and colour on every sighting, with a thumbnail",
                      "Cross-camera route reconstruction drawn on the map, ordered by PTS",
                      "Tiered watchlist alerts - exact plate, or description-based and labelled as such",
                    ].map((t) => (
                      <li key={t} className="flex gap-2.5 text-[13px] leading-relaxed text-ink-2">
                        <Fingerprint className="mt-0.5 h-4 w-4 shrink-0 text-ink-3" aria-hidden="true" />
                        {t}
                      </li>
                    ))}
                  </ul>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------ Honesty */}
        <section id="honest" className="scroll-mt-24 px-4 py-20 sm:px-6">
          <Reveal className="mx-auto max-w-3xl">
            <div className="rounded-3xl border border-line bg-surface-2 p-7 sm:p-9">
              <Badge tone="warn" className="mb-5">What it can't do</Badge>
              <h2 className="font-display text-2xl font-semibold leading-tight text-ink sm:text-3xl">
                Wide-angle CCTV rarely gives you a readable plate. We built for that.
              </h2>
              <div className="mt-5 flex flex-col gap-4 text-[15px] leading-relaxed text-ink-2">
                <p>
                  Tested against nine live sandbox cameras, the vehicle detector worked - 150+
                  correctly labelled vehicles - and not one legible plate came back. That isn't a
                  bug in the pipeline. E-challan systems use dedicated, IR-illuminated ANPR cameras
                  aimed at a single lane; department CCTV watches a whole intersection from a
                  distance. They are different instruments.
                </p>
                <p>
                  So Netra tracks on vehicle type and colour as well as plate. A witness who only
                  saw <span className="font-semibold text-ink">“a red car”</span> can still narrow
                  the field across every camera in the network.
                </p>
                <p className="rounded-xl border border-line bg-surface p-4 text-[13px] text-ink-2">
                  <span className="font-semibold text-ink">Stated plainly, in the product:</span>{" "}
                  a description match is a narrowing tool, not an identification. Other vehicles
                  share the same type and colour. Every attribute-tier alert is labelled
                  “possible match” and ships with a thumbnail so a human makes the call.
                </p>
              </div>
            </div>
          </Reveal>
        </section>

        {/* ----------------------------------------------------------- Final CTA */}
        <section className="px-4 pb-24 sm:px-6">
          <Reveal className="mx-auto max-w-4xl">
            <div className="relative overflow-hidden rounded-3xl bg-brand px-6 py-14 text-center sm:px-12">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute -right-12 -top-12 h-56 w-56 rounded-full bg-on-brand/15 blur-2xl"
              />
              <h2 className="relative font-display text-3xl font-semibold leading-tight text-on-brand sm:text-4xl">
                Open the control room.
              </h2>
              <p className="relative mx-auto mt-4 max-w-lg text-[15px] leading-relaxed text-on-brand/80">
                Live feeds, the GIS registry, the coverage report, and the alert feed - all of it
                running against real data.
              </p>
              <div className="relative mt-8 flex flex-wrap justify-center gap-3">
                <Link to="/app">
                  <Button size="lg" variant="secondary" className="rounded-full border-transparent hover:border-transparent">
                    Launch Netra
                    <ArrowRight className="h-4.5 w-4.5" aria-hidden="true" />
                  </Button>
                </Link>
                <Link to="/app/registry">
                  <Button
                    size="lg"
                    variant="secondary"
                    className="rounded-full border-transparent bg-white/10 text-on-brand shadow-none hover:bg-white/20 hover:border-transparent"
                  >
                    View the map
                  </Button>
                </Link>
              </div>
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="border-t border-line bg-surface px-4 py-10 sm:px-6">
        <div className="mx-auto flex max-w-5xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <NetraLogo size={36} tagline />
            <p className="mt-3 max-w-sm text-[13px] leading-relaxed text-ink-3">
              Netra - “eyes”. Built for the Sentinel CCTV Integration Hackathon, Gujarat Police
              Innovation Challenge 2026.
            </p>
          </div>
          <nav className="flex flex-wrap gap-x-5 gap-y-2 text-[13px]" aria-label="Footer">
            {[
              { to: "/app", label: "Control room" },
              { to: "/app/registry", label: "Registry & GIS" },
              { to: "/app/live", label: "Live viewer" },
              { to: "/app/gap-analysis", label: "Gap analysis" },
              { to: "/app/watchlist", label: "Watchlist" },
            ].map((l) => (
              <Link key={l.to} to={l.to} className="rounded text-ink-2 transition-colors hover:text-ink">
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
}
