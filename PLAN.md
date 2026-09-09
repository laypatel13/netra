# PLAN.md — Sentinel Gujarat CCTV Integration Hackathon

**Read this whole file before writing any code or giving any suggestions on this project.**
This document is the single source of truth for context, architecture, endpoints, tech stack, and constraints. If you are an AI assistant picking this up mid-project, this file should be enough to get you fully oriented without asking the team to re-explain anything.

---

## 0. Current Status (as of Sep 9)

**Deadline: Sep 15 — 6 days out.** Confirmed directly from sentinel.gujarat.gov.in (home, /faqs, /problems, /resource):

> "Registration and Submission closes in Deadline: 15 September 2026"

**This is a two-phase competition — Sep 15 is the deadline that actually matters for us:**
- **Phase 1 (Sandbox Round)** — everyone builds against the organizer's test feed (cctv.corp8.cloud), competing within their category. This is what this whole plan targets. Submissions close **Sep 15**, shortlisting announced that same evening.
- **Phase 2 (Production)** — only the **top 6 teams across both categories** get invited to a live demo on **Sep 22–23**, "using live camera feeds in a production-scale environment" (a different, larger feed set than the sandbox). Only relevant if we're shortlisted — don't let it distract from the Sep 15 target.
- We're registered under **Category 1 (student track)**. Nothing on the site suggests categories change the technical requirements, only who you're ranked against for shortlisting.
- No rubric weightage is published anywhere — the 7 evaluation areas (Section 11) are qualitative only, don't go looking for a percentage breakdown.

| Area | Status |
|---|---|
| Backend skeleton (FastAPI + SQLAlchemy + GeoAlchemy2) | Done |
| Database: Supabase (primary) + local Docker Postgres/PostGIS (dev fallback) | Wired — `DATABASE_URL` env var switches between them, `Base.metadata.create_all()` auto-creates schema on either. Not yet pointed at a live Supabase project — see Section 5a. |
| Camera registry (manual / bulk JSON / bulk CSV onboarding, RBAC, audit log) | Done |
| GIS map (Leaflet, department/status filters) | Done — camera markers only, no route polyline yet (see Gaps) |
| Gap-analysis report (per-department coverage, stale cameras, missing depts) | Done, has its own page (`/gap-analysis`) and a summary strip on the Dashboard |
| Feed catalogue proxy (authenticated HLS via backend, direct RTSP/WHEP) | Done |
| Live viewer (multi-camera HLS grid with reconnect-with-backoff) | Done — was silently failing to keep more than 1–2 tiles alive; **fixed this session**, see Section 0a |
| Unified control room Dashboard | Was a bare plate-search stub; **rebuilt this session** into a real control room (coverage summary, live preview grid, plate search) |
| ANPR pipeline (YOLO detect + OCR + PTS timestamp + POST /detections) | Exists (`anpr/pipeline.py`) but needs a rework pass — not yet solid enough to trust for the test case |
| Watchlist + match-on-detection | Backend logic exists (`watchlist.py`, wired into `detections.py`); no real-time alert delivery to the UI yet (still logs a warning server-side only) |
| Cross-camera route reconstruction | `/detections/route/{plate}` works and is wired into the Dashboard's plate search; **not yet drawn on the GIS map** — currently text/list only |
| Docs (PPT, HLD), demo recordings | Not started |

### 0a. Bugs fixed this session (read before touching feeds/viewer code again)

- **Root cause of "only 1–2 cameras show video"**: `feeds.py` was handing the frontend a fabricated `mp4` fallback URL (`https://cctv.corp8.cloud/<id>`) for the primary CDN host. That endpoint doesn't exist per the Integrator's Guide (only HLS/RTSP/WHEP are documented), and even if it did, a plain browser `fetch`/`<video src>` to it carries no session cookie, so it would always fail. `HlsPlayer.jsx` treated a failed fallback as **permanent** (`STATUS.FAILED`, no further retries) — so any tile that hit a transient HLS error during the initial connection burst (very likely when 8 tiles all open at once against one shared authenticated session) died forever, while the 1–2 that happened to connect cleanly on the first try stayed up.
  - Fix: `feeds.py` no longer returns a fake `mp4` URL for the cctv_host case (`mp4: null`).
  - Fix: `HlsPlayer.jsx` now retries HLS itself with capped backoff when there's no real fallback, instead of giving up — matching the mandatory "reconnect forever, never treat a join failure as terminal" rule in Section 8.
  - Fix: `LiveViewer.jsx` and the Dashboard preview grid now stagger each tile's first connection attempt (~250ms apart) instead of firing all of them in the same tick, to avoid slamming the one shared login session with a burst of simultaneous first-segment fetches.
- **Bad enum values silently 500'd**: `CameraCreate`/`WatchlistCreate` accepted any string for `camera_type`/`connectivity_status`/`storage_type`/`category`, so a typo would pass Pydantic validation and then blow up as a raw DB error when SQLAlchemy tried to write it into an `Enum` column — including mid-batch in bulk CSV import, which could abort an otherwise-good import. Fixed with `Literal[...]` validation in `schemas.py` and equivalent checks in the CSV import path (bad rows are now skipped cleanly, like already-bad lat/lng rows were).
- **`/cameras/sync-status` was marking every camera "offline" incorrectly**: `cctv.corp8.cloud`'s `/cameras.json` doesn't return a `live`/`live_status` field at all (confirmed live, not just in the stale disk cache). The old code treated a *missing* field the same as *present-and-false*, so every synced camera got written to the registry as confirmed offline — a false negative, not real health data. Fixed to leave a camera's `connectivity_status` untouched when the upstream API doesn't supply the field, and to report `cameras_skipped_no_live_field` in the response so this is visible instead of silent. Practical effect: until Sentinel's feed actually returns this field (maybe only in the Phase 2 production environment), registry cameras will legitimately stay "unknown"/stale — that's honest, not a bug on our end, and it's unrelated to whether the video stream itself plays (confirmed separately via the Live Viewer).

### 0b. Known gaps — pick these up next, in roughly this order

1. **ANPR pipeline rework** (`TIMELINE.md` Day 1) — this is the next big chunk of work.
2. **Real-time alerts** (`TIMELINE.md` Day 2) — watchlist matching happens server-side on every detection but nothing pushes it to the browser. **This is now confirmed required, not optional**: the official own-feed demo checklist explicitly calls for "watchlist matching, alert generation" (Section 10). Cheapest path for hackathon scope: short-poll a new `GET /watchlist/alerts/recent` endpoint from the Dashboard rather than building a websocket layer — good enough to demo, doesn't need new infra.
3. **Route-on-map** (`TIMELINE.md` Day 2) — `/detections/route/{plate}` returns the right data; it just isn't drawn as a polyline on the Registry GIS map yet. Cheapest path: reuse the same Leaflet map instance, add a `Polyline` + numbered markers for the stops returned by that endpoint.
4. **Supabase provisioning** (see 5a) — code is ready, a live project isn't created yet. Do this before Day 3's integration test at the latest.

---

## 1. What This Project Is

**Hackathon:** Gujarat Police Innovation Challenge 2026 ("Sentinel"), organized by Gujarat Police with technology partner i-Hub Gujarat and knowledge partners NFSU and DA-IICT.

**Official site:** https://sentinel.gujarat.gov.in

**One-line pitch:** Turns 26 fragmented, department-owned CCTV systems into one searchable network — pull up a vehicle's plate, see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching or replacing any department's existing infrastructure.

**Concrete example of what this solves:** A suspect vehicle is seen on Camera 1 during a crime. Right now, nobody knows it also passed Camera 13 an hour later and Camera 15 after that — each camera system is its own island with nobody watching all of them at once. This system stitches those sightings together: the moment ANPR reads the plate anywhere in the network, it's logged with a timestamp. Query the plate and get the full path (Camera 1 → Camera 13 → Camera 15) drawn on a map with times. If the plate is already on a watchlist, an alert fires automatically the instant it's seen — no manual query needed.

**Core objective (official):** Design a secure, scalable, interoperable, cost-effective solution integrating CCTV cameras from 26 Government Departments (currently fragmented, independent systems — mix of analog and IP, cloud and local storage, 7–15+ day retention, cameras up to ~1,000 km apart) into a unified video management and analytics platform.

**Deadline:** Sep 15.

---

## 2. Chosen Architecture

Four reference models exist (see Section 3). We are building:

- **Model 1 — Centralised CCTV Registry & GIS Foundation (MANDATORY)**
  Metadata-only registry and GIS mapping layer. No video streaming or recording here. Every submission must include this.
- **Model 2 — Unified Viewing & Selective Analytics**
  Direct RTSP/ONVIF/vendor-API connection to camera sources, aggregated into one viewer. No middleware/federation layer (that's Model 3 — explicitly not building that).

**Why this combination:** Model 1 is non-negotiable. Of the remaining choices, Model 2 requires no middleware/federation build (Model 3) and no full statewide Central VMS with face recognition/crowd counting (Model 4) — it's the most tractable scope for a small team on a tight timeline while still being a legitimate, complete submission.

### System diagram (textual)

```
Camera catalogue (/api/ingest)
        |
        +--> MODEL 1: Registry & GIS (mandatory)
        |       - Camera onboarding (bulk / manual / API)
        |       - GIS map + gap-analysis reports
        |
        +--> MODEL 2: Live viewing & analytics
                - RTSP ingest + ANPR (TCP, PTS-timed)
                - Watchlist match + alerts
                        |
                        v
              Detection store (plate, PTS timestamp, camera ID, confidence)
                        |
              +---------+---------+
              |                   |
      Watchlist match      Route reconstruction
      --> real-time alert  --> rendered on GIS map
                        |
                        v
        Unified control room dashboard
        (viewer, routes, alerts, search)
```

The **detection store is the single source of truth** feeding both the alerting path and the tracking/GIS path. Its schema is locked (see Section 6) — don't change it without checking both branches that depend on it.

---

## 3. All Four Reference Models (context, for completeness)

| Model | Name | What it does | Status |
|---|---|---|---|
| 1 | Centralised CCTV Registry & GIS Foundation | Metadata/asset registry + GIS map. No video. | **Building — mandatory** |
| 2 | Unified Viewing & Selective Analytics | Direct feed aggregation via RTSP/ONVIF/vendor SDK/API, no middleware | **Building** |
| 3 | VMS Federation & Middleware | Middleware layer integrating multiple VMS platforms via APIs/SDKs | Not building |
| 4 | Central VMS & AI Platform | Single consolidated Central VMS, statewide scale, face recognition, crowd/vehicle counting | Not building |

Hybrid/fully custom architectures are also permitted by the rules, but we are using the straightforward Model 1 + Model 2 combination.

---

## 4. Endpoints & Data Sources

### Sentinel Camera Grid (cctv.corp8.cloud)

**Access model:** HLS is served over the CDN host (`cctv.corp8.cloud`, behind your access password / session cookie). RTSP & WebRTC carry media over TCP/UDP that the CDN cannot proxy, so they are served directly on the public static IP `103.250.160.189`. Credentials required for all streams.

Always pull the camera list from the catalogue — **never hardcode camera IDs**, they can change:

```
curl -s https://cctv.corp8.cloud/cameras.json   # requires session cookie (POST /auth/login first)
```

Returns every camera's id (cam01–cam30) and name. In practice the live response has been sparser than the Integrator's Guide implies (no `live`/`codec`/`resolution`/`location` fields observed) — `feeds.py` already defaults those to sensible "unknown" values rather than assuming they're present.

| Protocol | Endpoint pattern | Use for |
|---|---|---|
| HLS | `https://cctv.corp8.cloud/<id>/index.m3u8` | Dashboards, mobile, restricted networks, remote AI |
| RTSP | `rtsp://<email>:<password>@103.250.160.189:8554/stream/<id>` | AI inference (OpenCV, GStreamer, FFmpeg, DeepStream) |
| WebRTC (WHEP) | `http://<email>:<password>@103.250.160.189:8889/stream/<id>/whep` | Low-latency browser preview |

**Authentication notes:**
- The `@` in your email must be percent-encoded as `%40` (e.g. `alice%40example.com`)
- HLS uses a session cookie obtained from `POST /auth/login`
- RTSP/WebRTC embed email:password directly in the URL
- Only emails on the approved access list can connect
- There is no progressive-MP4 endpoint — don't build a fallback path that assumes one (see Section 0a)

### Government databases (future integration only — NOT built in this hackathon)

VAHAN, SARTHI, eGujCop (Gujarat Police CCTNS), AFIS, NAFIS. The submission only needs to demonstrate **integration readiness** for these (mentioned in the HLD/scalability write-up), not actual connections. Use a **representative watchlist database of your own** for the demo — this is explicitly permitted by the official rules.

---

## 5. Tech Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | PostgreSQL + PostGIS — Supabase in production, local Docker in dev (see 5a) |
| Frontend | React |
| GIS mapping | Leaflet |
| ANPR — detection | YOLO |
| ANPR — OCR | PaddleOCR or EasyOCR |
| Streaming (browser) | HLS.js (HLS relay through the backend's authenticated proxy) |
| Streaming (inference) | RTSP over TCP via OpenCV/GStreamer/FFmpeg |
| Auth | Department-wise role-based access control (RBAC) |
| API docs | OpenAPI / Swagger |
| Messaging/scale (mentioned in scalability doc only, not built) | Kafka |

These match the officially suggested stacks for Models 1 and 2 — not mandatory, but there's no reason to deviate.

### 5a. Supabase vs. local Docker — decision

**Use Supabase for the deployed/submitted version; keep local Docker for day-to-day dev.** This isn't an either/or — the code already supports both through one env var (`DATABASE_URL` in `backend/app/database.py`), so there's no migration to do later, just a connection string to set.

Why Supabase is the right call here, specifically for a 6-day hackathon finish:
- **You need a URL to submit anyway** (hosted platform URL is an optional-but-valuable submission item, and the government-feed live demo needs *something* reachable). Supabase gives you a managed, always-on Postgres without standing up your own server just for the database.
- **PostGIS is a checkbox, not a chore** — Supabase ships PostGIS as an enablable extension (`CREATE EXTENSION IF NOT EXISTS postgis;` in the SQL Editor, already documented in `database.py`'s docstring), so nothing about the `Geography` columns in `models.py` needs to change.
- **Schema creation is already automatic** — `main.py` calls `Base.metadata.create_all(bind=engine)` on startup, so pointing `DATABASE_URL` at a fresh Supabase project and starting the backend once is the entire migration step. No Alembic, no manual SQL needed for first setup.
- **Free tier is enough for this scale** — a few thousand detection rows and 30 cameras is trivial for Supabase's free-tier Postgres.

Caveats to actually watch for before the live demo:
- **Use the pooled connection string** (port 6543, "Transaction" mode), not the direct connection (port 5432) — FastAPI opens a new connection per request via SQLAlchemy's pool, and Supabase's free tier has a low direct-connection cap that a demo with concurrent viewers can hit.
- **Free-tier projects pause after a week of inactivity.** If the project sits idle between now and the live demo, hit it once beforehand to wake it — a cold start mid-demo is an easy own-goal.
- Keep the local Docker fallback working throughout — it's the safety net if Supabase has an outage or rate-limits you right before the demo.

Remaining step: actually create the Supabase project, run `CREATE EXTENSION IF NOT EXISTS postgis;`, and drop the pooled connection string into `.env`. That's a 10-minute task whenever you're ready to do it.

---

## 6. Data Model

### Camera registry (Model 1)

| Field | Notes |
|---|---|
| camera_id | from `/api/ingest`, not self-generated |
| location (lat/lng) | for GIS map |
| department | one of the 26 government departments |
| camera_type | analog / IP |
| ownership | |
| connectivity_status | for health monitoring |
| storage_type | cloud / local |
| retention_period | days |

### Detection store (Model 2 — shared by watchlist and tracking)

| Field | Notes |
|---|---|
| plate_number | OCR output |
| timestamp | **must be derived from stream PTS, never wall-clock/arrival time** |
| camera_id | foreign key to registry |
| confidence | OCR/detection confidence score |

### Watchlist

| Field | Notes |
|---|---|
| plate_number | |
| category | stolen / suspect / blacklisted |
| source | your own representative dataset — real government DB integration not required |
| date_added | |

---

## 7. Core Workflows

1. **Onboarding** — camera metadata pulled from `/api/ingest`, entered via bulk CSV / manual form / API, stored in registry. **Done.**
2. **Live viewing** — direct RTSP/ONVIF connection per camera, relayed to browser via HLS. **Done.**
3. **ANPR detection** — per-frame vehicle detection (YOLO) → plate crop → OCR → confidence score → write to detection store with PTS timestamp. **Exists, needs a rework pass** (Section 0b).
4. **Watchlist matching** — every new detection checked against watchlist table; match triggers a real-time alert. **Match logic done; real-time delivery to the UI not built** (Section 0b).
5. **Cross-camera tracking** — given a plate number, query all detection events, order by PTS timestamp, reconstruct the route across cameras. **Done** (`/detections/route/{plate}`).
6. **GIS visualization** — render camera markers, live alerts, and reconstructed vehicle routes on the Leaflet map. **Camera markers done; route polyline not built yet** (Section 0b).

---

## 8. Sentinel Sandbox Protocol Rules (critical — read before writing ingestion code)

These come directly from the official integration reference. Violating them causes the most common client-side failures.

- **Force RTSP over TCP** (`rtsp_transport=tcp`). UDP is accepted but fails across NAT/firewalls and produces silently corrupted frames that look like model bugs, not connection errors.
- **Never trust reported frame rate** (`CAP_PROP_FPS` or equivalent). It often doesn't match actual delivery rate. Measure the real rate yourself or ignore it.
- **Drive all timing from PTS, never from arrival time.** On connect, the gateway replays a buffered group-of-pictures, so the first 1–2 seconds can arrive faster than real time. Timing by arrival produces impossible velocities immediately after every connection. Trackers/Kalman filters must be fed PTS deltas.
- **Frame intervals are not uniform.** Pipelines must tolerate inter-frame gaps without treating them as a disconnect.
- **Reconnect automatically with exponential backoff** (~2s → cap ~30s), **and never give up permanently on a transient join failure.** A tile/stream that fails to connect once is not dead — keep retrying with capped backoff indefinitely. (This was the exact bug fixed in Section 0a: don't let a missing/broken fallback path turn a transient failure into a terminal one.) Feeds are supervised and restart periodically. Never reconnect in a tight loop.
- **Decoder warnings on join are normal** (e.g. "Error constructing the frame RPS", "Could not find ref with POC") until the first IDR frame arrives. Do not treat as fatal.
- **The grid is not uniform.** Cameras differ in resolution, codec (H.264/H.265), frame rate, bitrate. Read per-camera properties from `/api/ingest` and size batching/buffers/decoders per camera — no fixed-shape batch inference across all cameras.
- **Expect a scene discontinuity.** Each feed is a looping recording; at the loop point the scene cuts like a camera reboot. Long-lived state (background models, re-ID galleries, track IDs) must recover from a hard cut, not assume infinite continuity.
- **There is no file download.** The grid is consumed live only. `/stream/<id>` answers range requests for a media player — pulling it with curl/wget yields a partial file that looks complete but isn't. Build against live capture from day one. There is also no progressive-MP4 endpoint on cctv.corp8.cloud — don't build a fallback path around one existing.
- **Consume only.** Never push to the gateway, never call its control API.
- **Pace load.** Each connected client gets its own copy of the stream — only open cameras you're actively processing, close ones you're done with. When opening many tiles/streams at once against one shared authenticated session, stagger the first connection attempts rather than firing them all simultaneously — a burst of simultaneous first-segment fetches is a plausible cause of transient join failures.

---

## 9. Explicit "Do Not" List

- **Do not build Model 3 (middleware/federation) or Model 4 (Central VMS, face recognition, crowd/vehicle counting, statewide infra)** — out of scope for this build.
- **Do not hardcode a camera list** — always source from the catalogue.
- **Do not use UDP for RTSP** — TCP only.
- **Do not time anything by wall-clock or frame-arrival time** — PTS only, everywhere (detection timestamps, route ordering, velocity/dwell calculations).
- **Do not trust `CAP_PROP_FPS`** or equivalent reported frame rate.
- **Do not treat decoder warnings at stream join as fatal errors.**
- **Do not treat a single failed connection attempt as terminal** — always keep retrying with capped backoff (see Section 0a for the bug this caused).
- **Do not build a fallback path around a progressive-MP4 endpoint on cctv.corp8.cloud** — it doesn't exist, and even a same-shaped URL on that host isn't authenticated for a plain browser request.
- **Do not build real integrations with VAHAN/SARTHI/eGujCop/AFIS/NAFIS** — a representative watchlist DB is sufficient and explicitly permitted; only mention integration readiness in the HLD.
- **Do not submit mockups, animations, or concept videos as demos** — evaluators explicitly reject non-functional demonstrations. Everything shown must be real, working software.
- **Do not attempt to actually build 80,000-camera-scale infrastructure** — the scalability requirement is a written plan/roadmap, not something to implement.
- **Do not push streams to the gateway or call its control API** — consume only.
- **Do not attempt to download/save copies of the sandbox footage** — there is no file download; build against live streams only.
- **Do not let bonus features eat into time needed for mandatory deliverables** (working test case, demos, HLD, PPT) — bonus points never compensate for missing mandatory requirements. With 6 days left, this matters more than ever — see `TIMELINE.md`.

---

## 10. Submission Requirements Checklist

- [ ] Solution Presentation (PPT/PDF): model chosen + justification, overview, architecture, end-to-end workflow, AI analytics approach, alert methodology, tech stack, scalability/security considerations, expected impact
- [ ] Technical Proposal / HLD document: architecture diagrams, integration approach for heterogeneous cameras/VMS, video ingestion architecture for dispersed locations, ANPR + cross-camera tracking approach, scalability approach for ~80,000 cameras, department-level technical requirements
- [ ] Own-feed demo video (2–3 min screen recording): onboarding, live/recorded viewing, ANPR detection, **watchlist matching, and alert generation** (confirmed required on sentinel.gujarat.gov.in/faqs — this isn't optional polish, see Section 0b item 2)
- [ ] Government-feed live demo: onboarding, viewing, analytics output — screen-recorded, plus an output report of detected plates + timestamps
- [ ] Submission via unlisted YouTube link OR Google Drive/OneDrive link (viewer access enabled)
- [ ] Optional: hosted platform URL with test credentials — recommended given Supabase is already wired in, see Section 5a
- [ ] Optional: GitHub/GitLab repo link

---

## 11. Evaluation Criteria

1. Successful test case (onboarding + analytics on the government-provided feed)
2. Solution presentation clarity and completeness
3. Solution architecture — technical soundness, feasibility, security, interoperability, HLD clarity
4. Working platform and demonstration maturity
5. Video analytics output quality (ANPR/detection/timestamps/reports)
6. Scalability and PoC readiness (~80,000 cameras)
7. Submission completeness

**Bonus consideration** (does not compensate for missing mandatory requirements): innovative hybrid/customised architecture, advanced cross-camera tracking, additional reliable analytics beyond mandatory ANPR, edge-processing/bandwidth optimization, enhanced cybersecurity/RBAC/auditability, operational dashboards, automated alerts, health monitoring, integration-ready APIs.

---

## 12. Scalability Plan (written only — this is a documentation deliverable, not something to build)

Must address, in the HLD/PPT:

- Central, regional, and edge-compute requirements
- GPU/accelerator capacity for video analytics
- Network-bandwidth planning and low-bandwidth strategies
- Hot/warm/cold storage tiers based on retention periods
- Load balancing, horizontal scaling, monitoring/logging/health checks
- High availability, backup, and disaster recovery
- Phased statewide rollout plan toward ~80,000 cameras

---

## 13. Team Process Notes

- Detection store schema is shared infrastructure — don't change it without checking both the watchlist/alerts branch and the cross-camera tracking branch that depend on it.
- Feed-connectivity bugs are the highest-leverage thing to fix fast — everything downstream (ANPR, tracking, demo) depends on the viewer actually holding a stream. The Section 0a bug is exactly this class of issue.
- Demo recordings are a graded submission item, not an afterthought — budget dedicated time for them (see `TIMELINE.md`), don't cram them into the integration-test day.

See `TIMELINE.md` for the day-by-day execution schedule.
