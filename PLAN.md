# PLAN.md - Sentinel Gujarat CCTV Integration Hackathon

**Read this whole file before writing any code or giving any suggestions on this project.**
This document is the single source of truth for context, architecture, endpoints, tech stack, and constraints. If you are an AI assistant picking this up mid-project, this file should be enough to get you fully oriented without asking the team to re-explain anything.

---

## 0. Current Status (as of Sep 9)

**Deadline: Sep 15 - 6 days out.** Confirmed directly from sentinel.gujarat.gov.in (home, /faqs, /problems, /resource):

> "Registration and Submission closes in Deadline: 15 September 2026"

**This is a two-phase competition - Sep 15 is the deadline that actually matters for us:**
- **Phase 1 (Sandbox Round)** - everyone builds against the organizer's test feed (cctv.corp8.cloud), competing within their category. This is what this whole plan targets. Submissions close **Sep 15**, shortlisting announced that same evening.
- **Phase 2 (Production)** - only the **top 6 teams across both categories** get invited to a live demo on **Sep 22-23**, "using live camera feeds in a production-scale environment" (a different, larger feed set than the sandbox). Only relevant if we're shortlisted - don't let it distract from the Sep 15 target.
- We're registered under **Category 1 (student track)**. Nothing on the site suggests categories change the technical requirements, only who you're ranked against for shortlisting.
- No rubric weightage is published anywhere - the 7 evaluation areas (Section 11) are qualitative only, don't go looking for a percentage breakdown.

| Area | Status |
|---|---|
| Backend skeleton (FastAPI + SQLAlchemy + GeoAlchemy2) | Done |
| Database: Supabase (primary) + local Docker Postgres/PostGIS (dev fallback) | Wired - `DATABASE_URL` env var switches between them, `Base.metadata.create_all()` auto-creates schema on either. Not yet pointed at a live Supabase project - see Section 5a. |
| Camera registry (manual / bulk JSON / bulk CSV onboarding, RBAC, audit log) | Done |
| GIS map (Leaflet, department/status filters) | Done, **plus route-on-map** (polyline + numbered stop markers with thumbnails) |
| Gap-analysis report (per-department coverage, stale cameras, missing depts) | Done, has its own page (`/gap-analysis`) and a summary strip on the Dashboard |
| Feed catalogue proxy (authenticated HLS via backend, direct RTSP/WHEP) | Done |
| Live viewer (multi-camera HLS grid with reconnect-with-backoff) | Done - was silently failing to keep more than 1-2 tiles alive; **fixed this session**, see Section 0a |
| Unified control room Dashboard | Rebuilt into a real control room (coverage summary, live preview grid, plate **and vehicle-description** search) |
| ANPR pipeline (YOLO detect + OCR + PTS timestamp + POST /detections) | Hardened (frame throttling, decoder warm-up skip, crop upscaling, absolute HLS URLs); plate legibility itself is a real, separate problem (0b) |
| Vehicle attribute tracking (type + color, complementing plate ANPR) | **Core build done** - nullable plate on Detection/Watchlist, `anpr/color.py` extraction, thumbnail capture+serving, `/detections/search`, generalized watchlist matching. Verified end-to-end against live cctv.corp8.cloud footage. Stretch items (GIS-plausibility ranking, partial-plate tier) not done - see 0c |
| Watchlist + match-on-detection | Done - exact-plate and attribute-tiered matching, `GET /watchlist/alerts/recent` short-polled from a new Watchlist page (entry management + live alert feed) |
| Cross-camera route reconstruction | Done for both plate (`/detections/route/{plate}`) and attributes (`/detections/search`), ordered by `created_at`, drawn on the GIS map in Registry.jsx |
| Frontend design system + UI rebuild | **Done this session** - Tailwind token layer (navy/khaki-gold civic palette), light+dark with a toggle, a public landing page at `/`, the five operational pages rebuilt under `/app/*`. See Section 0g |
| Docs (PPT, HLD), demo recordings | Not started |

### 0a. Bugs fixed this session (read before touching feeds/viewer code again)

- **Root cause of "only 1-2 cameras show video"**: `feeds.py` was handing the frontend a fabricated `mp4` fallback URL (`https://cctv.corp8.cloud/<id>`) for the primary CDN host. That endpoint doesn't exist per the Integrator's Guide (only HLS/RTSP/WHEP are documented), and even if it did, a plain browser `fetch`/`<video src>` to it carries no session cookie, so it would always fail. `HlsPlayer.jsx` treated a failed fallback as **permanent** (`STATUS.FAILED`, no further retries) - so any tile that hit a transient HLS error during the initial connection burst (very likely when 8 tiles all open at once against one shared authenticated session) died forever, while the 1-2 that happened to connect cleanly on the first try stayed up.
  - Fix: `feeds.py` no longer returns a fake `mp4` URL for the cctv_host case (`mp4: null`).
  - Fix: `HlsPlayer.jsx` now retries HLS itself with capped backoff when there's no real fallback, instead of giving up - matching the mandatory "reconnect forever, never treat a join failure as terminal" rule in Section 8.
  - Fix: `LiveViewer.jsx` and the Dashboard preview grid now stagger each tile's first connection attempt (~250ms apart) instead of firing all of them in the same tick, to avoid slamming the one shared login session with a burst of simultaneous first-segment fetches.
- **Bad enum values silently 500'd**: `CameraCreate`/`WatchlistCreate` accepted any string for `camera_type`/`connectivity_status`/`storage_type`/`category`, so a typo would pass Pydantic validation and then blow up as a raw DB error when SQLAlchemy tried to write it into an `Enum` column - including mid-batch in bulk CSV import, which could abort an otherwise-good import. Fixed with `Literal[...]` validation in `schemas.py` and equivalent checks in the CSV import path (bad rows are now skipped cleanly, like already-bad lat/lng rows were).
- **`/cameras/sync-status` was marking every camera "offline" incorrectly**: `cctv.corp8.cloud`'s `/cameras.json` doesn't return a `live`/`live_status` field at all (confirmed live, not just in the stale disk cache). The old code treated a *missing* field the same as *present-and-false*, so every synced camera got written to the registry as confirmed offline - a false negative, not real health data. Fixed to leave a camera's `connectivity_status` untouched when the upstream API doesn't supply the field, and to report `cameras_skipped_no_live_field` in the response so this is visible instead of silent. Practical effect: until Sentinel's feed actually returns this field (maybe only in the Phase 2 production environment), registry cameras will legitimately stay "unknown"/stale - that's honest, not a bug on our end, and it's unrelated to whether the video stream itself plays (confirmed separately via the Live Viewer).

### 0b. Major finding: plate legibility is a real problem, and the pivot it led to

Empirically tested the hardened ANPR pipeline against 9+ real cctv.corp8.cloud cameras (including the toll plaza `cam12` and `cam13`, one of the three official test-case cameras) - the vehicle detector works correctly (verified 150+ real, correctly-labeled vehicle detections), but **zero legible plates were found across every camera sampled**. This isn't a code bug: these are generic wide-angle surveillance cameras (exactly what the hackathon brief describes - heterogeneous department CCTV), not purpose-built ANPR hardware.

Confirmed via research: real Ahmedabad/Gandhinagar e-challan systems use **dedicated 4-5MP, IR-illuminated ANPR cameras** mounted close to and aimed specifically at a violation zone (stop line, a fixed lane) - fundamentally different from a wide-angle overview camera watching an entire intersection from a distance. CCTV records the scene; ANPR-grade hardware is a separate, purpose-built capture system. Sources: [How ANPR Cameras and CCTV Are Used in India's e-Challan System](https://www.cars24.com/article/anpr-cameras-and-cctv-use-in-indian-e-challan-system/), [Choosing the Right Camera for ANPR - TechNexion](https://www.technexion.com/resources/choosing-the-right-camera-for-automatic-number-plate-recognition/), [How ANPR & e-Challan System Works in India](https://parkplus.io/blog/challan/how-anpr-echallan-system-works-in-india).

**The pivot:** don't limit cross-camera tracking to plate numbers. A suspect vehicle ("a red car," "a long truck") can be usefully tracked by coarse visual attributes - type and color - even with no legible plate, the same way real investigations narrow suspects by vehicle description when a plate isn't known. This is explicitly bonus-worthy per Section 11 ("advanced cross-camera tracking," "additional reliable analytics beyond mandatory ANPR") and turns the plate-legibility problem into a demonstrated architectural strength instead of a weakness to hide. **Frame this honestly as a narrowing tool, not identification** - "red car" alone matches many vehicles; it complements plate ANPR for exactly the case where a plate isn't known, cross-referenced with time/route plausibility, not a unique-ID guarantee. That framing is itself the sophisticated answer, not a caveat to bury.

Design, once implemented:
- `Detection.plate_number` becomes nullable; add `vehicle_type` (car/motorcycle/bus/truck - already detected by YOLO for free) and `vehicle_color` (dominant color extracted from the crop, small named palette) to both `Detection` and `WatchlistEntry`. A watchlist entry needs plate OR type+color, not neither.
- Every vehicle sighting gets recorded (type+color always, plate when legible) - mirrors how plate detections already work, and is what actually makes "trace this vehicle's route" possible for one that wasn't already flagged before cameras saw it.
- **Save a thumbnail crop with every detection**, not just a computed color label - color extraction is unreliable under sodium streetlight/headlight glare (observed directly in this session's samples), so a human-checkable image is the real fallback when the automated label might be wrong.
- Watchlist matching generalizes to check plate-based **and** attribute-based entries; alerts are tiered (exact plate > attributes-only) and deduped over a time window so the feed doesn't flood with "possible red car" noise every time a common-colored car passes a camera.
- Stretch, only if time remains: rank attribute-based candidate sightings by GIS plausibility (camera lat/lng already in the registry - reject a "match" that would require impossible travel speed between two cameras) and keep partial/low-confidence OCR reads as a middle tier instead of today's all-or-nothing plate regex match.

### 0c. Day 2 core - done, verified end-to-end against live footage

Vehicle attribute tracking, real-time alerts, and route-on-map (the three items from `TIMELINE.md` Day 2's core list) are all built and verified:
- `anpr/color.py` extracts a dominant color per vehicle; `anpr/pipeline.py` records every detected vehicle (plate when legible, type+color+thumbnail always) via multipart upload - confirmed live against `cam14` (real detections: "orange car", "yellow car", "silver_gray car", correctly stored with thumbnails, no plate).
- Backend: nullable plate on `Detection`/`WatchlistEntry`, `GET /detections/search` (attribute candidate sightings), `GET /detections/{id}/thumbnail`, generalized `check_detection_against_watchlist` (exact-plate and attribute tiers), `GET /watchlist/alerts/recent`.
- Frontend: new Watchlist page (entry management, mode toggle plate/attributes, live-polled tiered alert feed), Dashboard search generalized to plate-or-attributes with thumbnails, Registry GIS map now draws the route as a polyline + numbered `CircleMarker` stops with popups (thumbnail included) - verified visually in-browser, no console errors.

### 0d. Tier-1 hardening pass - done

A pre-Day-3 review pass (re-reading the actual current code, not guessing) found and fixed: `POST /cameras` was missing the admin check every other mutating registry endpoint has; no way to delete a stray camera/watchlist entry without raw SQL (added `DELETE /cameras/{id}` and `DELETE /watchlist/{id}`, admin-only, camera delete refuses if detections exist rather than cascading history away); `/detections/search` was missing the `since`/`until` filters `TIMELINE.md` had already named but never implemented; the plate regex required the *entire* OCR string to match instead of finding a plate-shaped substring, throwing away genuine reads that had boundary noise; pipeline tuning constants were hardcoded instead of CLI flags; CORS was wide open; no index existed on `(vehicle_type, vehicle_color)` for the new search endpoint. All verified via curl/live pipeline runs, not just code-reviewed.

### 0e. A dedicated `test/` toolkit for the team

Consolidated the project's scattered ad hoc debug scripts (`scripts/test_feed_connection.py`, `anpr/test_cv2_https.py`, `backend/test_proxy.py`, `backend/test_proxy2.py`) into `test/connectivity/`, added reusable seed data (`test/seed_data/cameras_seed.csv` - the 30 real cameras, previously only a throwaway scratch file; `watchlist_seed.json` - representative entries including colors/types actually observed live), a scripted end-to-end health check (`test/smoke_test.sh` - 14 checks, idempotent, re-runnable), and `test/README.md` - a from-scratch onboarding/testing guide for anyone on the team who wasn't in this build session: what the pieces are, *why* the non-obvious decisions were made (created_at vs. PTS, why every vehicle is recorded, why thumbnails exist, why alerts are tiered), how to run everything locally, and a manual click-through UI checklist.

### 0f. Day 3 - done early, see `TIMELINE.md` for the real findings

Ran the full rehearsal using the new toolkit rather than piece-by-piece checks: a **real, live, non-synthetic** attribute-tier watchlist alert (seeded entry + real `cam14` footage → real match, confirmed on the Watchlist page with real thumbnails) - the strongest evidence yet for evaluation criterion #1. Reconnect-with-backoff verified directly, with a real finding: an unreachable host's connection attempt itself takes ~30s (FFmpeg's own timeout) before our backoff sleep even starts. Two things honestly flagged as not fully verified live: cross-camera plate-route reconstruction (proven via synthetic multi-camera data, since live traffic can't be scripted to drive one vehicle across disconnected feeds) and scene-discontinuity behavior (architecturally reasoned to be safe - no persistent cross-frame state - but not directly observed within the test window).

### 0g. Frontend rebuild - design system, landing page, themed app

The frontend had no stylesheet at all: every page was inline `style={{}}` objects, and the palette was half-finished (dark `#1e1e2e` cards on a default-white body), which is why it read as unfinished regardless of how well the backend worked. Rebuilt on a real token layer:

- **Tokens, not hexes.** Tailwind is now in the build (`tailwind.config.js` + `postcss.config.js`); every colour resolves to a CSS custom property in `frontend/src/styles/index.css` as an `R G B` triplet, so opacity modifiers still work while values swap under `[data-theme]`. Components must not hardcode a colour - the one deliberate exception is `NetraLogo.jsx`, because a logo has to look identical in both themes.
- **Palette:** deep navy institutional base, khaki-gold accent, saffron for the single attention colour. Every text pair is verified against WCAG AA in *both* themes; two real failures were found and fixed (the light success green was 4.35:1 on its own tint, and form-control borders were the only thing marking a field's extent at 1.7:1, below the 3:1 non-text requirement - controls now use a separate `--c-control-border` token while decorative card borders stay subtle).
- **Light and dark**, with a toggle. Theme resolves before first paint via an inline script in `index.html` so a dark-mode operator never gets flashed a white screen; it follows the OS until the operator chooses, then remembers. The OSM basemap is tinted in dark mode.
- **Landing page at `/`** - the marketing/overview front door: what the problem is, how the pipeline works end to end, both models, and an explicit "what it can't do" section putting the plate-legibility finding (0b) up front as an engineering decision rather than a caveat to bury. Live camera counts are pulled from the registry so the page can't advertise a number the platform can't show.
- **App under `/app/*`** with a sidebar shell (hamburger drawer below 1024px), route-change focus management, a skip link, and a watchlist alert-count badge polled at shell level so a match is visible from any page. The old top-level URLs redirect, so links in `test/README.md` and anywhere else still work.
- **`HlsPlayer.jsx`'s connection logic was not touched** - only its markup. The staggered first connection and the capped-backoff reconnect from 0a are load-bearing; don't "simplify" them.
- Route-level code splitting: the landing page no longer downloads Leaflet and hls.js (initial JS went from ~1.0 MB to ~211 kB, with hls.js loaded only when a video page mounts).

**Remaining, in roughly this order:**
1. **Supabase provisioning** (see 5a) - code is ready, a live project isn't created yet. The Day 3 rehearsal above ran against local Docker Postgres only.
2. **Stretch items from 0b** (only if time allows): GIS-plausibility ranking for attribute-based candidates; partial/low-confidence plate tier.
3. ~~Frontend visual/UX polish~~ - done, see 0g. Any official Gujarat Police / Sentinel brand assets still need to be dropped in: the app ships Netra's own mark and deliberately does not reproduce a government emblem from memory.

---

## 1. What This Project Is

**Hackathon:** Gujarat Police Innovation Challenge 2026 ("Sentinel"), organized by Gujarat Police with technology partner i-Hub Gujarat and knowledge partners NFSU and DA-IICT.

**Official site:** https://sentinel.gujarat.gov.in

**One-line pitch:** Turns 26 fragmented, department-owned CCTV systems into one searchable network - pull up a vehicle's plate (or, when the plate isn't legible, just its type and color), see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching or replacing any department's existing infrastructure.

**Concrete example of what this solves:** A suspect vehicle is seen on Camera 1 during a crime. Right now, nobody knows it also passed Camera 13 an hour later and Camera 15 after that - each camera system is its own island with nobody watching all of them at once. This system stitches those sightings together: the moment ANPR reads the plate anywhere in the network, it's logged with a timestamp. Query the plate and get the full path (Camera 1 → Camera 13 → Camera 15) drawn on a map with times. If the plate is already on a watchlist, an alert fires automatically the instant it's seen - no manual query needed. **And when there's no legible plate at all** - a distant or low-quality feed, or a witness who only saw "a red car" - the same tracking works off vehicle type and color instead, narrowing the field the way a real investigation would, not pretending to uniquely identify the vehicle (see Section 0b).

**Core objective (official):** Design a secure, scalable, interoperable, cost-effective solution integrating CCTV cameras from 26 Government Departments (currently fragmented, independent systems - mix of analog and IP, cloud and local storage, 7-15+ day retention, cameras up to ~1,000 km apart) into a unified video management and analytics platform.

**Deadline:** Sep 15.

---

## 2. Chosen Architecture

Four reference models exist (see Section 3). We are building:

- **Model 1 - Centralised CCTV Registry & GIS Foundation (MANDATORY)**
  Metadata-only registry and GIS mapping layer. No video streaming or recording here. Every submission must include this.
- **Model 2 - Unified Viewing & Selective Analytics**
  Direct RTSP/ONVIF/vendor-API connection to camera sources, aggregated into one viewer. No middleware/federation layer (that's Model 3 - explicitly not building that).

**Why this combination:** Model 1 is non-negotiable. Of the remaining choices, Model 2 requires no middleware/federation build (Model 3) and no full statewide Central VMS with face recognition/crowd counting (Model 4) - it's the most tractable scope for a small team on a tight timeline while still being a legitimate, complete submission.

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

The **detection store is the single source of truth** feeding both the alerting path and the tracking/GIS path. Its schema is locked (see Section 6) - don't change it without checking both branches that depend on it.

---

## 3. All Four Reference Models (context, for completeness)

| Model | Name | What it does | Status |
|---|---|---|---|
| 1 | Centralised CCTV Registry & GIS Foundation | Metadata/asset registry + GIS map. No video. | **Building - mandatory** |
| 2 | Unified Viewing & Selective Analytics | Direct feed aggregation via RTSP/ONVIF/vendor SDK/API, no middleware | **Building** |
| 3 | VMS Federation & Middleware | Middleware layer integrating multiple VMS platforms via APIs/SDKs | Not building |
| 4 | Central VMS & AI Platform | Single consolidated Central VMS, statewide scale, face recognition, crowd/vehicle counting | Not building |

Hybrid/fully custom architectures are also permitted by the rules, but we are using the straightforward Model 1 + Model 2 combination.

---

## 4. Endpoints & Data Sources

### Sentinel Camera Grid (cctv.corp8.cloud)

**Access model:** HLS is served over the CDN host (`cctv.corp8.cloud`, behind your access password / session cookie). RTSP & WebRTC carry media over TCP/UDP that the CDN cannot proxy, so they are served directly on the public static IP `103.250.160.189`. Credentials required for all streams.

Always pull the camera list from the catalogue - **never hardcode camera IDs**, they can change:

```
curl -s https://cctv.corp8.cloud/cameras.json   # requires session cookie (POST /auth/login first)
```

Returns every camera's id (cam01-cam30) and name. In practice the live response has been sparser than the Integrator's Guide implies (no `live`/`codec`/`resolution`/`location` fields observed) - `feeds.py` already defaults those to sensible "unknown" values rather than assuming they're present.

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
- There is no progressive-MP4 endpoint - don't build a fallback path that assumes one (see Section 0a)

### Government databases (future integration only - NOT built in this hackathon)

VAHAN, SARTHI, eGujCop (Gujarat Police CCTNS), AFIS, NAFIS. The submission only needs to demonstrate **integration readiness** for these (mentioned in the HLD/scalability write-up), not actual connections. Use a **representative watchlist database of your own** for the demo - this is explicitly permitted by the official rules.

---

## 5. Tech Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | PostgreSQL + PostGIS - Supabase in production, local Docker in dev (see 5a) |
| Frontend | React |
| GIS mapping | Leaflet |
| ANPR - detection | YOLO |
| ANPR - OCR | PaddleOCR or EasyOCR |
| Streaming (browser) | HLS.js (HLS relay through the backend's authenticated proxy) |
| Streaming (inference) | RTSP over TCP via OpenCV/GStreamer/FFmpeg |
| Auth | Department-wise role-based access control (RBAC) |
| API docs | OpenAPI / Swagger |
| Messaging/scale (mentioned in scalability doc only, not built) | Kafka |

These match the officially suggested stacks for Models 1 and 2 - not mandatory, but there's no reason to deviate.

### 5a. Supabase vs. local Docker - decision

**Use Supabase for the deployed/submitted version; keep local Docker for day-to-day dev.** This isn't an either/or - the code already supports both through one env var (`DATABASE_URL` in `backend/app/database.py`), so there's no migration to do later, just a connection string to set.

Why Supabase is the right call here, specifically for a 6-day hackathon finish:
- **You need a URL to submit anyway** (hosted platform URL is an optional-but-valuable submission item, and the government-feed live demo needs *something* reachable). Supabase gives you a managed, always-on Postgres without standing up your own server just for the database.
- **PostGIS is a checkbox, not a chore** - Supabase ships PostGIS as an enablable extension (`CREATE EXTENSION IF NOT EXISTS postgis;` in the SQL Editor, already documented in `database.py`'s docstring), so nothing about the `Geography` columns in `models.py` needs to change.
- **Schema creation is already automatic** - `main.py` calls `Base.metadata.create_all(bind=engine)` on startup, so pointing `DATABASE_URL` at a fresh Supabase project and starting the backend once is the entire migration step. No Alembic, no manual SQL needed for first setup.
- **Free tier is enough for this scale** - a few thousand detection rows and 30 cameras is trivial for Supabase's free-tier Postgres.

Caveats to actually watch for before the live demo:
- **Use the pooled connection string** (port 6543, "Transaction" mode), not the direct connection (port 5432) - FastAPI opens a new connection per request via SQLAlchemy's pool, and Supabase's free tier has a low direct-connection cap that a demo with concurrent viewers can hit.
- **Free-tier projects pause after a week of inactivity.** If the project sits idle between now and the live demo, hit it once beforehand to wake it - a cold start mid-demo is an easy own-goal.
- Keep the local Docker fallback working throughout - it's the safety net if Supabase has an outage or rate-limits you right before the demo.

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

### Detection store (Model 2 - shared by watchlist and tracking)

| Field | Notes |
|---|---|
| plate_number | OCR output - **nullable** (0b): not every sighting has a legible plate |
| vehicle_type | car / motorcycle / bus / truck - from the YOLO vehicle detector, always available |
| vehicle_color | dominant color extracted from the crop, small named palette (0b) - nullable, unreliable under glare so pair with the thumbnail, not trusted alone |
| thumbnail_path | saved crop image - the human-checkable fallback when the computed color label might be wrong (0b) |
| timestamp | **must be derived from stream PTS, never wall-clock/arrival time** - used for within-camera timing only; cross-camera ordering uses `created_at` instead (0a, see detections.py docstring) |
| camera_id | foreign key to registry |
| confidence | OCR/detection confidence score |

Every vehicle sighting is recorded now, not just ones with a legible plate (0b) - plate_number/vehicle_color may be null, but vehicle_type and a thumbnail are always captured.

### Watchlist

| Field | Notes |
|---|---|
| plate_number | nullable (0b) - an entry needs plate_number OR (vehicle_type AND vehicle_color), not neither |
| vehicle_type / vehicle_color | for attribute-based entries when the plate isn't known (0b) - e.g. "red car last seen near cam04" |
| category | stolen / suspect / blacklisted |
| source | your own representative dataset - real government DB integration not required |
| date_added | |

---

## 7. Core Workflows

1. **Onboarding** - camera metadata pulled from `/api/ingest`, entered via bulk CSV / manual form / API, stored in registry. **Done.**
2. **Live viewing** - direct RTSP/ONVIF connection per camera, relayed to browser via HLS. **Done.**
3. **ANPR detection** - per-frame vehicle detection (YOLO) → plate crop → OCR → confidence score → write to detection store. Pipeline hardened this session; plate legibility itself is a real, separate problem (0b).
4. **Vehicle attribute tracking** - every detected vehicle also gets a type + dominant color + thumbnail recorded, whether or not a plate was legible, as a coarse tracking signal that complements plate ANPR. **New, in progress** (0b).
5. **Watchlist matching** - every new detection checked against the watchlist, on plate (exact) or attributes (coarse, tiered lower). Match triggers a real-time alert. **Plate-match logic done; attribute matching + real-time delivery to the UI not built yet** (0c).
6. **Cross-camera tracking** - given a plate number OR a type+color combination, query matching detection events, order chronologically (by `created_at` - see Section 6), reconstruct the route/candidate-sightings across cameras. **Plate-based route done** (`/detections/route/{plate}`); **attribute-based search not built yet** (0c).
7. **GIS visualization** - render camera markers, live alerts, and reconstructed vehicle routes on the Leaflet map. **Camera markers done; route polyline not built yet** (0c).

---

## 8. Sentinel Sandbox Protocol Rules (critical - read before writing ingestion code)

These come directly from the official integration reference. Violating them causes the most common client-side failures.

- **Force RTSP over TCP** (`rtsp_transport=tcp`). UDP is accepted but fails across NAT/firewalls and produces silently corrupted frames that look like model bugs, not connection errors.
- **Never trust reported frame rate** (`CAP_PROP_FPS` or equivalent). It often doesn't match actual delivery rate. Measure the real rate yourself or ignore it.
- **Drive all timing from PTS, never from arrival time.** On connect, the gateway replays a buffered group-of-pictures, so the first 1-2 seconds can arrive faster than real time. Timing by arrival produces impossible velocities immediately after every connection. Trackers/Kalman filters must be fed PTS deltas.
- **Frame intervals are not uniform.** Pipelines must tolerate inter-frame gaps without treating them as a disconnect.
- **Reconnect automatically with exponential backoff** (~2s → cap ~30s), **and never give up permanently on a transient join failure.** A tile/stream that fails to connect once is not dead - keep retrying with capped backoff indefinitely. (This was the exact bug fixed in Section 0a: don't let a missing/broken fallback path turn a transient failure into a terminal one.) Feeds are supervised and restart periodically. Never reconnect in a tight loop.
- **Decoder warnings on join are normal** (e.g. "Error constructing the frame RPS", "Could not find ref with POC") until the first IDR frame arrives. Do not treat as fatal.
- **The grid is not uniform.** Cameras differ in resolution, codec (H.264/H.265), frame rate, bitrate. Read per-camera properties from `/api/ingest` and size batching/buffers/decoders per camera - no fixed-shape batch inference across all cameras.
- **Expect a scene discontinuity.** Each feed is a looping recording; at the loop point the scene cuts like a camera reboot. Long-lived state (background models, re-ID galleries, track IDs) must recover from a hard cut, not assume infinite continuity.
- **There is no file download.** The grid is consumed live only. `/stream/<id>` answers range requests for a media player - pulling it with curl/wget yields a partial file that looks complete but isn't. Build against live capture from day one. There is also no progressive-MP4 endpoint on cctv.corp8.cloud - don't build a fallback path around one existing.
- **Consume only.** Never push to the gateway, never call its control API.
- **Pace load.** Each connected client gets its own copy of the stream - only open cameras you're actively processing, close ones you're done with. When opening many tiles/streams at once against one shared authenticated session, stagger the first connection attempts rather than firing them all simultaneously - a burst of simultaneous first-segment fetches is a plausible cause of transient join failures.

---

## 9. Explicit "Do Not" List

- **Do not build Model 3 (middleware/federation) or Model 4 (Central VMS, face recognition, crowd/vehicle counting, statewide infra)** - out of scope for this build.
- **Do not hardcode a camera list** - always source from the catalogue.
- **Do not use UDP for RTSP** - TCP only.
- **Do not time anything by wall-clock or frame-arrival time** - PTS only, everywhere (detection timestamps, route ordering, velocity/dwell calculations).
- **Do not trust `CAP_PROP_FPS`** or equivalent reported frame rate.
- **Do not treat decoder warnings at stream join as fatal errors.**
- **Do not treat a single failed connection attempt as terminal** - always keep retrying with capped backoff (see Section 0a for the bug this caused).
- **Do not build a fallback path around a progressive-MP4 endpoint on cctv.corp8.cloud** - it doesn't exist, and even a same-shaped URL on that host isn't authenticated for a plain browser request.
- **Do not build real integrations with VAHAN/SARTHI/eGujCop/AFIS/NAFIS** - a representative watchlist DB is sufficient and explicitly permitted; only mention integration readiness in the HLD.
- **Do not submit mockups, animations, or concept videos as demos** - evaluators explicitly reject non-functional demonstrations. Everything shown must be real, working software.
- **Do not attempt to actually build 80,000-camera-scale infrastructure** - the scalability requirement is a written plan/roadmap, not something to implement.
- **Do not push streams to the gateway or call its control API** - consume only.
- **Do not attempt to download/save copies of the sandbox footage** - there is no file download; build against live streams only.
- **Do not let bonus features eat into time needed for mandatory deliverables** (working test case, demos, HLD, PPT) - bonus points never compensate for missing mandatory requirements. With 6 days left, this matters more than ever - see `TIMELINE.md`.

---

## 10. Submission Requirements Checklist

- [ ] Solution Presentation (PPT/PDF): model chosen + justification, overview, architecture, end-to-end workflow, AI analytics approach, alert methodology, tech stack, scalability/security considerations, expected impact
- [ ] Technical Proposal / HLD document: architecture diagrams, integration approach for heterogeneous cameras/VMS, video ingestion architecture for dispersed locations, ANPR + cross-camera tracking approach, scalability approach for ~80,000 cameras, department-level technical requirements
- [ ] Own-feed demo video (2-3 min screen recording): onboarding, live/recorded viewing, ANPR detection, **watchlist matching, and alert generation** (confirmed required on sentinel.gujarat.gov.in/faqs - this isn't optional polish, see Section 0b item 2)
- [ ] Government-feed live demo: onboarding, viewing, analytics output - screen-recorded, plus an output report of detected plates + timestamps
- [ ] Submission via unlisted YouTube link OR Google Drive/OneDrive link (viewer access enabled)
- [ ] Optional: hosted platform URL with test credentials - recommended given Supabase is already wired in, see Section 5a
- [ ] Optional: GitHub/GitLab repo link

---

## 11. Evaluation Criteria

1. Successful test case (onboarding + analytics on the government-provided feed)
2. Solution presentation clarity and completeness
3. Solution architecture - technical soundness, feasibility, security, interoperability, HLD clarity
4. Working platform and demonstration maturity
5. Video analytics output quality (ANPR/detection/timestamps/reports)
6. Scalability and PoC readiness (~80,000 cameras)
7. Submission completeness

**Bonus consideration** (does not compensate for missing mandatory requirements): innovative hybrid/customised architecture, advanced cross-camera tracking, additional reliable analytics beyond mandatory ANPR, edge-processing/bandwidth optimization, enhanced cybersecurity/RBAC/auditability, operational dashboards, automated alerts, health monitoring, integration-ready APIs.

---

## 12. Scalability Plan (written only - this is a documentation deliverable, not something to build)

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

- Detection store schema is shared infrastructure - don't change it without checking both the watchlist/alerts branch and the cross-camera tracking branch that depend on it.
- Feed-connectivity bugs are the highest-leverage thing to fix fast - everything downstream (ANPR, tracking, demo) depends on the viewer actually holding a stream. The Section 0a bug is exactly this class of issue.
- Demo recordings are a graded submission item, not an afterthought - budget dedicated time for them (see `TIMELINE.md`), don't cram them into the integration-test day.

See `TIMELINE.md` for the day-by-day execution schedule.
