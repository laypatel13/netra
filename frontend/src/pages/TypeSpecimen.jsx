import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, BellRing, Camera, Percent, ShieldAlert } from "lucide-react";
import NetraLogo from "../components/NetraLogo.jsx";
import ThemeToggle from "../components/ui/ThemeToggle.jsx";
import Button from "../components/ui/Button.jsx";
import Badge, { StatusPill } from "../components/ui/Badge.jsx";
import Stat, { Meter } from "../components/ui/Stat.jsx";
import { Card, CardBody, CardHeader } from "../components/ui/Card.jsx";
import Field, { Input } from "../components/ui/Field.jsx";
import Segmented from "../components/ui/Segmented.jsx";
import AlertRow from "../components/AlertRow.jsx";
import StopList from "../components/StopList.jsx";

/**
 * Type specimen - an internal decision tool at /type, not a product page.
 *
 * It renders the REAL Netra components (Stat, AlertRow, StopList, Badge,
 * Button, Field…) and re-points their font tokens at each candidate stack, so
 * what you compare is the actual product in that typeface rather than a
 * mock-up that flatters it. The candidate webfonts are injected only when this
 * page mounts, so the shipped app's payload is untouched.
 */

const FONT_HREF =
  "https://fonts.googleapis.com/css2?" +
  [
    "family=Literata:opsz,wght@7..72,400..700",
    "family=Newsreader:opsz,wght@6..72,400..700",
    "family=Space+Grotesk:wght@400;500;600;700",
    "family=Plus+Jakarta+Sans:wght@500;600;700",
  ].join("&") +
  "&display=swap";

const DIRECTIONS = [
  {
    id: "literata",
    name: "Literata",
    tag: "Institutional serif",
    display: 'Literata, Georgia, serif',
    foundry: "TypeTogether, for Google",
    families: "Literata · Poppins · JetBrains Mono",
    verdict:
      "Recommended. Low-contrast, sturdy serifs sit alongside Poppins' even stroke instead of fighting it, and the optical-size axis redraws proportions between a 24px title and a 60px hero. Reads as record and document - which is what a police registry is.",
  },
  {
    id: "newsreader",
    name: "Newsreader",
    tag: "Editorial serif",
    display: 'Newsreader, Georgia, serif',
    foundry: "Production Type",
    families: "Newsreader · Poppins · JetBrains Mono",
    verdict:
      "More contrast and more elegance than Literata - stronger on the landing hero, arguably too refined on a control-room page title. Also carries an optical-size axis.",
  },
  {
    id: "grotesk",
    name: "Space Grotesk",
    tag: "Technical grotesque",
    display: '"Space Grotesk", system-ui, sans-serif',
    foundry: "Florian Karsten",
    families: "Space Grotesk · Poppins · JetBrains Mono",
    verdict:
      "The no-serif option. Its odd, slightly mechanical forms contrast with Poppins' regularity without a category jump. Lower risk, less distinctive.",
  },
  {
    id: "jakarta",
    name: "Plus Jakarta",
    tag: "Baseline",
    display: '"Plus Jakarta Sans", system-ui, sans-serif',
    foundry: "Tokotype",
    families: "Plus Jakarta Sans · Poppins · JetBrains Mono",
    verdict:
      "What the display tier was before this change. Geometric like Poppins, so hierarchy comes only from size and weight - the pairing the serif is meant to replace.",
  },
].map((d) => ({
  ...d,
  // Poppins is settled for UI and small; only the display face is under test.
  ui: "Poppins, system-ui, sans-serif",
  mono: '"JetBrains Mono", ui-monospace, monospace',
  indic: "Poppins, sans-serif",
  indicNote: "Devanagari via Poppins - no Gujarati",
}));

const WEIGHTS = {
  light: { heading: 600, strong: 600, medium: 500, hero: 600 },
  heavy: { heading: 800, strong: 700, medium: 600, hero: 800 },
};

/* Sample data - self-contained, so the specimen needs no backend. */
const ALERTS = [
  {
    tier: "exact_plate",
    detection: { camera_id: "cam28", plate_number: "GJ01AB1234", thumbnail_url: null },
    watchlist_entry: { category: "stolen" },
  },
  {
    tier: "attributes",
    detection: { camera_id: "cam14", vehicle_color: "silver_gray", vehicle_type: "car", thumbnail_url: null },
    watchlist_entry: { category: "suspect" },
  },
];

const STOPS = [
  { camera_id: "cam05", timestamp_ms: 84000, confidence: 0.91, vehicle_type: "car", vehicle_color: "silver_gray" },
  { camera_id: "cam13", timestamp_ms: 331000, confidence: 0.78, vehicle_type: "car", vehicle_color: "silver_gray" },
];

const CAMERAS = [
  { id: "cam28", name: "37 Bilimora", dept: "Urban Development", status: "online" },
  { id: "cam17", name: "17 Rajkot Bus Port CCTV", dept: "Ports & Transport", status: "unknown" },
  { id: "cam05", name: "05 Visat teen Rasta", dept: "Gujarat Police", status: "offline" },
];

function Panel({ title, hint, children }) {
  return (
    <section className="border-t border-line py-8">
      <div className="mb-4">
        <h2 className="text-[15px] font-medium text-ink">{title}</h2>
        {hint && <p className="mt-1 text-[13px] text-ink-3">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

export default function TypeSpecimen() {
  const [dirId, setDirId] = useState("literata");
  const [weight, setWeight] = useState("light");

  // Candidate webfonts load only while this page is mounted.
  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = FONT_HREF;
    document.head.appendChild(link);
    return () => link.remove();
  }, []);

  const dir = useMemo(() => DIRECTIONS.find((d) => d.id === dirId), [dirId]);
  const w = WEIGHTS[weight];

  return (
    <div className="min-h-dvh bg-bg">
      {/* Scoped overrides: re-point the product's own font tokens at the
          candidate, so the real components below render in it. */}
      <style>{`
        [data-spec] { font-family: var(--spec-ui); }
        [data-spec] .font-sans { font-family: var(--spec-ui); }
        [data-spec] .font-display {
          font-family: var(--spec-display);
          font-optical-sizing: auto;
        }
        [data-spec] h1, [data-spec] h2, [data-spec] h3, [data-spec] h4 {
          font-weight: var(--spec-heading-w);
        }
        [data-spec] .font-mono { font-family: var(--spec-mono); }
        [data-spec] .font-semibold { font-weight: var(--spec-strong-w); }
        [data-spec] .font-medium { font-weight: var(--spec-medium-w); }
        [data-spec] .spec-indic { font-family: var(--spec-indic); }
        [data-spec] { --font-small: var(--spec-small); }
        [data-spec] .spec-condensed { font-stretch: 82%; }
      `}</style>

      <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-5 py-3">
          <Link to="/" className="rounded-lg">
            <NetraLogo size={36} />
          </Link>
          <span className="rounded-full border border-line px-2.5 py-0.5 text-2xs font-medium text-ink-3">
            Type specimen
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Segmented
              label="Weight"
              value={weight}
              onChange={setWeight}
              options={[
                { value: "light", label: "Lighter" },
                { value: "heavy", label: "Heavier" },
              ]}
            />
            <ThemeToggle />
            <Link to="/app">
              <Button size="sm" variant="secondary">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                App
              </Button>
            </Link>
          </div>
        </div>

        <div className="mx-auto flex max-w-5xl flex-wrap gap-2 px-5 pb-3">
          {DIRECTIONS.map((d) => {
            const active = d.id === dirId;
            return (
              <button
                key={d.id}
                type="button"
                onClick={() => setDirId(d.id)}
                aria-pressed={active}
                className={
                  "cursor-pointer rounded-xl border px-3.5 py-2 text-left transition-colors duration-150 " +
                  (active
                    ? "border-brand/40 bg-brand-soft"
                    : "border-line-control bg-surface hover:bg-surface-2")
                }
              >
                <span className={`block text-[13px] font-medium ${active ? "text-brand" : "text-ink"}`}>
                  {d.name}
                </span>
                <span className="block text-2xs text-ink-3">{d.tag}</span>
              </button>
            );
          })}
        </div>
      </header>

      <main
        data-spec
        style={{
          "--spec-display": dir.display,
          "--spec-ui": dir.ui,
          "--spec-mono": dir.mono,
          "--spec-indic": dir.indic || dir.ui,
          "--spec-small": "Poppins, system-ui, sans-serif",
          "--spec-heading-w": w.heading,
          "--spec-strong-w": w.strong,
          "--spec-medium-w": w.medium,
        }}
        className="mx-auto max-w-5xl px-5 pb-24"
      >
        {/* ------------------------------------------------------ Spec sheet */}
        <div className="mt-8 rounded-2xl border border-line bg-surface p-5 shadow-card">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="font-display text-2xl text-ink" style={{ fontWeight: w.heading }}>
              {dir.name}
            </h1>
            <span className="text-[13px] text-ink-3">{dir.foundry}</span>
          </div>
          <p className="mt-1 font-mono text-[13px] text-ink-2">{dir.families}</p>
          <p className="mt-3 max-w-2xl text-[13px] leading-relaxed text-ink-2">{dir.verdict}</p>
          <div className="mt-3">
            <Badge tone={dir.indic ? "ok" : "warn"}>{dir.indicNote}</Badge>
          </div>
        </div>

        {/* ----------------------------------------------------------- Hero */}
        <Panel title="Landing hero" hint="The one place the type gets to have a voice.">
          <div className="rounded-2xl border border-line bg-surface px-6 py-12 text-center">
            <Badge tone="gold" className="mb-5">
              Gujarat Police Innovation Challenge 2026 · Sentinel
            </Badge>
            <h2
              className="font-display text-[2.4rem] leading-[1.05] tracking-[-0.02em] text-ink sm:text-[3.4rem]"
              style={{ fontWeight: w.hero }}
            >
              One network. Every camera.{" "}
              <span className="mark">
                <span>Every vehicle.</span>
              </span>
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-ink-2">
              Netra turns 26 fragmented, department-owned CCTV systems into one searchable network -
              trace a vehicle across every camera it passed, and get alerted the moment a watchlisted
              one is seen.
            </p>
            <div className="mt-7 flex flex-wrap justify-center gap-3">
              <Button size="lg" className="rounded-full">Open the control room</Button>
              <Button size="lg" variant="secondary" className="rounded-full">See how it works</Button>
            </div>
          </div>
        </Panel>

        {/* ---------------------------------------------------------- Stats */}
        <Panel title="Control room stats" hint="Tabular figures under a polled update - watch the numerals, not the labels.">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Cameras onboarded" value="30" icon={Camera} />
            <Stat label="Coverage" value="68%" icon={Percent} tone="warn" />
            <Stat label="Departments missing" value="18" icon={ShieldAlert} tone="danger" hint="8 onboarded" />
            <Stat label="Active alerts" value="13" icon={BellRing} tone="danger" hint="among recent detections" />
          </div>
        </Panel>

        {/* --------------------------------------------------- Plate legibility */}
        <Panel
          title="Plate legibility"
          hint="The functional test. An operator reads these and acts on them - zero must never look like O, one must never look like l."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl border border-line bg-surface p-5">
              <p className="mb-3 text-2xs font-medium uppercase tracking-[0.08em] text-ink-3">Plate, at size</p>
              <p className="font-mono text-3xl tracking-wide text-ink">GJ01AB1234</p>
              <p className="mt-3 font-mono text-lg tracking-wide text-ink-2">MH12DE1433 · DL8CAF5030</p>
            </div>
            <div className="rounded-2xl border border-line bg-surface p-5">
              <p className="mb-3 text-2xs font-medium uppercase tracking-[0.08em] text-ink-3">
                Ambiguous glyphs
              </p>
              <p className="font-mono text-3xl text-ink">0O 1lI 5S 8B</p>
              <p className="mt-3 font-mono text-[13px] leading-relaxed text-ink-2">
                cam05 · cam13 · cam28
                <br />
                PTS 1:24 · confidence 91%
              </p>
            </div>
          </div>
        </Panel>

        {/* --------------------------------------------------------- Alerts */}
        <Panel title="Alert feed" hint="Real component. Tier badges, plate in mono, category in body.">
          <Card>
            <CardHeader title="Alert feed" description="Matches among recent detections, polled every 5 seconds." />
            <CardBody>
              <ul className="flex flex-col gap-2">
                {ALERTS.map((a, i) => (
                  <AlertRow key={i} alert={a} />
                ))}
              </ul>
            </CardBody>
          </Card>
        </Panel>

        {/* ----------------------------------------------------------- Route */}
        <Panel title="Route reconstruction" hint="Camera IDs in mono against body text - the mixed-family test.">
          <StopList stops={STOPS} />
        </Panel>

        {/* ----------------------------------------------------- Dense table */}
        <Panel
          title="Dense table"
          hint="Where the UI face earns its keep. On Anek, the header row uses the width axis to condense."
        >
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-[13px]">
                <thead>
                  <tr className="spec-condensed border-b border-line text-left text-2xs uppercase tracking-[0.06em] text-ink-3">
                    <th className="px-5 py-2.5 font-medium">Camera ID</th>
                    <th className="px-5 py-2.5 font-medium">Name</th>
                    <th className="px-5 py-2.5 font-medium">Department</th>
                    <th className="px-5 py-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {CAMERAS.map((c) => (
                    <tr key={c.id} className="border-b border-line/60 last:border-0">
                      <td className="px-5 py-2.5 font-mono text-ink">{c.id}</td>
                      <td className="px-5 py-2.5 text-ink-2">{c.name}</td>
                      <td className="px-5 py-2.5 text-ink-2">{c.dept}</td>
                      <td className="px-5 py-2.5"><StatusPill status={c.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Panel>

        {/* ------------------------------------------------------ Small sizes */}
        <Panel
          title="Small sizes"
          hint="11-15px is where a control room actually lives. The ≤13px rows are the ones the small-text toggle swaps - 15px is unaffected, so you can see the seam."
        >
          <div className="rounded-2xl border border-line bg-surface p-5">
            {[
              ["15px - body default", "text-[15px]"],
              ["13px - the workhorse UI size", "text-[13px]"],
              ["11px - uppercase micro-label", "text-2xs uppercase tracking-[0.08em]"],
            ].map(([label, cls]) => (
              <p key={label} className={`${cls} mb-3 text-ink-2 last:mb-0`}>
                {label} · A suspect vehicle passed camera 13 an hour later, and camera 15 after that.
              </p>
            ))}
            <div className="mt-5 max-w-sm">
              <Field label="Plate number" hint="Mono, uppercase, wide tracking.">
                {(a) => <Input {...a} defaultValue="GJ01AB1234" className="font-mono tracking-wide" />}
              </Field>
            </div>
            <Meter value={68} label="Coverage" className="mt-5" />
          </div>
        </Panel>

        {/* ----------------------------------------------------------- Indic */}
        <Panel
          title="Gujarati & Devanagari"
          hint="Whether the registry can render a Gujarati camera name or a Hindi UI at all."
        >
          <div className="rounded-2xl border border-line bg-surface p-5">
            {dir.indic ? (
              <>
                <p className="spec-indic text-2xl text-ink">ગુજરાત પોલીસ · નેત્ર</p>
                <p className="spec-indic mt-2 text-lg text-ink-2">गुजरात पुलिस · नेत्र</p>
                <p className="mt-3 text-[13px] text-ink-3">Rendered in {dir.indicNote.toLowerCase()}.</p>
              </>
            ) : (
              <>
                <p className="text-2xl text-ink-3">ગુજરાત પોલીસ · નેત્ર</p>
                <p className="mt-3 text-[13px] text-warn">
                  Falling back to a system font - this direction has no Indic coverage, so the script
                  would not match the rest of the interface.
                </p>
              </>
            )}
          </div>
        </Panel>
      </main>
    </div>
  );
}
