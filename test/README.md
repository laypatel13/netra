# Testing & onboarding guide

This is the place to start if you're new to this codebase and want to run it locally, understand what actually works today, and verify it yourself rather than take it on faith. It assumes you've skimmed `PLAN.md` (architecture, endpoints, the Sentinel sandbox rules) but doesn't assume you were there when any of it was built.

## 1. What this project actually is, in five minutes

**netra** is a CCTV registry + tracking platform built for the Sentinel Gujarat hackathon. Two halves:

- **Model 1 - Registry & GIS** (`backend/app/routers/cameras.py`, `frontend/src/pages/Registry.jsx`): a metadata-only database of cameras (which department owns it, where it is, is it online) with a map view. No video here.
- **Model 2 - Live viewing + tracking** (`backend/app/routers/feeds.py`, `detections.py`, `watchlist.py`, `anpr/`): connects to the actual camera feeds, runs vehicle detection + plate OCR on them, and lets you trace a vehicle's path across cameras and get alerted if it matches a watchlist.

The single most important thing to understand going in: **the cameras in the sandbox (`cctv.corp8.cloud`) are generic wide-angle surveillance CCTV, not purpose-built ANPR hardware.** Real automated-fine systems (Ahmedabad's e-challan, for instance) use dedicated close-range, IR-illuminated cameras aimed at a specific spot. These are ordinary security cameras watching whole intersections from a distance. That single fact explains almost every non-obvious design decision below - see `PLAN.md` Section 0b for the full writeup.

## 2. Why things work the way they do (read this before you "fix" something)

A few things that look like they could be simplified, but aren't accidents:

- **Every detected vehicle is recorded, not just ones with a legible plate.** Given the camera quality problem above, requiring a plate would mean recording almost nothing. Every vehicle gets a `vehicle_type` (from the detector, always available) and a `vehicle_color` (computed, see below); `plate_number` is filled in only when OCR actually finds one. This is what lets you trace "the red car" even when no plate was ever read.
- **Every detection also saves a thumbnail image**, not just the computed color label. Computed color is unreliable under night/artificial lighting - we've seen a genuinely silver/gray car get labeled "orange" because of sodium streetlight glow. A human glancing at the thumbnail catches this instantly; the label alone wouldn't. Don't remove the thumbnail thinking the color field makes it redundant.
- **Watchlist matches are tiered** (`exact_plate` vs `attributes`) and this distinction is load-bearing, not cosmetic. An attribute match ("a black car") is a narrowing tool - plenty of real vehicles share a type+color - not a unique identification. The UI must never present these two tiers as equally certain.
- **Cross-camera ordering uses `created_at` (row-insert wall-clock time), not `timestamp_ms` (PTS)**, even though PTS is what the Sentinel sandbox rules mandate for timing. That's not a contradiction: PTS is correct for *within-stream* timing (frame-to-frame intervals, avoiding the buffered-replay artifact right after connecting) but it is *not* a shared clock across two different cameras' independent RTSP connections. `created_at` is the only thing that's actually comparable across cameras. See the docstring in `backend/app/routers/detections.py` for the full reasoning if you're tempted to "fix" this back to PTS.
- **`/cameras/sync-status` leaves a camera's status alone if the upstream catalogue doesn't return a `live` field**, rather than treating "missing" as "confirmed offline." The real `cctv.corp8.cloud` catalogue doesn't currently return that field at all - treating its absence as a false negative would silently mark every camera offline, which actually happened once before this was fixed.
- **`HlsPlayer.jsx` retries forever with capped backoff and has no permanent "gave up" state** (other than the video element itself being unmounted). A camera that fails to connect once is not dead - the Sentinel sandbox explicitly expects clients to keep retrying.

## 3. Running everything locally

You'll need: Python 3.11+, Node, Docker (for local Postgres - skip if you have a Supabase `DATABASE_URL`), and Sentinel sandbox credentials (`CCTV_EMAIL`/`CCTV_PASSWORD`) in `backend/.env`.

**Database** (skip if using Supabase):
```bash
cd backend
docker compose up -d          # if port 5432 is already taken locally, run: DB_PORT=5433 docker compose up -d
```

**Backend:**
```bash
cd backend
cp .env.example .env          # fill in CCTV_EMAIL / CCTV_PASSWORD, and DATABASE_URL if the default port collided
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010   # pick any free port; 8000 collides with other local projects on some machines
```
Tables are created automatically on first boot (`Base.metadata.create_all()` in `main.py`). There's no Alembic in this project - if you change `models.py`, you need to manually `DROP TABLE <changed tables> CASCADE;` against the dev DB and restart the backend so it recreates them with the new schema. This is a real, known rough edge, not an oversight.

**Frontend:**
```bash
cd frontend
npm install
BACKEND_PORT=8010 npm run dev   # must match whatever port you ran the backend on
```
Open http://localhost:5173. The landing/overview page is at `/`; the operational app lives under `/app`.

> If you had `npm run dev` already running from before the frontend redesign, **restart it** - Tailwind was added to the build, and Vite only picks up `postcss.config.js` at startup. A server started earlier will serve the app completely unstyled.

**ANPR pipeline** (separate Python environment - heavier dependencies: torch, ultralytics, easyocr):
```bash
cd anpr
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python pipeline.py --backend http://127.0.0.1:8010 --camera-ids cam01,cam13,cam15 --max-cameras 3
```
**Be careful with this one.** It's genuinely CPU-bound (YOLO + EasyOCR on CPU), and running many camera threads at once will peg your CPU and heat up a laptop for as long as it runs - this has happened before in this project. Start with 1-3 cameras and a `--max-frames` cap while you're getting oriented:
```bash
python pipeline.py --backend http://127.0.0.1:8010 --camera-ids cam14 --max-cameras 1 --max-frames 30 --dry-run
```
Drop `--dry-run` once you're confident it's behaving, and only then consider more cameras or removing `--max-frames`. `--process-every-n`, `--dedup-cooldown-s`, and `--conf-threshold` are all tunable - see `python pipeline.py --help`.

## 4. Seeding representative data

`seed_data/cameras_seed.csv` - all 30 real camera IDs from the sandbox catalogue, with departments and coordinates assigned from each camera's actual location hints in its name (not random) - e.g. `cam17` ("Rajkot Bus Port CCTV") → Ports & Transport near Rajkot. Upload it via the Registry page's "Bulk onboard (CSV)" control, or:
```bash
curl -X POST http://127.0.0.1:8010/cameras/bulk-csv -H "X-Role: admin" -F "file=@test/seed_data/cameras_seed.csv"
```

`seed_data/watchlist_seed.json` - a handful of representative watchlist entries: two plate-based, and three attribute-based using colors/types actually observed live at `cam14` during testing (so they have a real chance of firing if you run the ANPR pipeline against that camera). `smoke_test.sh` (below) seeds these automatically.

## 5. Automated health check

```bash
bash test/smoke_test.sh
```
Scripts the full API surface: RBAC enforcement, bulk onboarding, watchlist seeding, recording a plate-based and an attribute-based detection, thumbnail serving, both watchlist alert tiers actually firing, attribute search with time filters, and the delete-endpoints' safety guards. Safe to re-run - seeding steps tolerate already-existing data instead of failing. Exits non-zero if anything unexpected happens, so it's a real regression check, not just a demo script.

`test/connectivity/` holds standalone scripts for debugging feed connectivity in isolation, without the rest of the stack - useful when you specifically suspect the RTSP/HLS connection itself rather than anything in the backend:
- `test_feed_connection.py` - the main one; validates RTSP connect, PTS timing, and reconnect-with-backoff against the live sandbox.
- `test_cv2_https.py`, `test_proxy.py`, `test_proxy2.py` - smaller, more targeted scripts from when the `cctv.corp8.cloud` auth flow was first being figured out (login, HLS manifest fetch, encryption key reachability). Take `--email`/`--password` as CLI args.

## 6. Manual, click-through UI checklist

Run this after the automated check passes, to confirm the actual user-facing experience:

1. **Registry** (`/app/registry`) - after seeding, should show 30 markers spread across Gujarat, not clustered in one spot. Filter by department; the count above the map should update. Upload the CSV again - it should report onboarding 0 new cameras (already-registered ones are skipped, not duplicated or errored).
2. **Gap Analysis** (`/app/gap-analysis`) - should show real department coverage (several departments, not all 26 - that's expected and correct, it's meant to surface the gap) and a "stale cameras" list (all 30, until/unless `/cameras/sync-status` finds a `live` field to sync from, which the real sandbox currently doesn't provide - see Section 2 above; this is not a bug).
3. **Live Viewer** (`/app/live`) - enable a handful of cameras. Expect a mix of `CONNECTING`/`LIVE` status badges; a tile should never get permanently stuck on a red `FAILED` badge - it should keep cycling back through `RECONNECTING` (this was a real bug, fixed - see `PLAN.md` Section 0a).
4. **Control room** (`/app`) - coverage summary strip, a small live preview grid, and the "Trace a vehicle" search. Try both modes:
   - By plate: search a plate you've posted a detection for (e.g. via `smoke_test.sh`) - should show a numbered list of stops with thumbnails.
   - By description: search a type+color you know has sightings - same, plus the "narrowing tool, not identification" note should be visible.
5. **Watchlist** (`/app/watchlist`) - add an entry both ways (plate-only, and type+color-only - the form should refuse an empty submission of both). The alerts feed below should show tiered badges: green "Exact plate match" vs. amber "Possible match - by description". It polls every 5 seconds - post a new matching detection and watch it appear without a manual refresh.
6. **Registry route-on-map** - search a plate or description that has 2+ sightings on *different* cameras (the automated check only creates sightings on one camera; use `curl` to post a couple more against different `camera_id`s from the seed CSV if you want to see a real multi-point route). Should draw a gold polyline connecting the cameras in chronological order, with numbered, clickable stop markers showing a thumbnail in their popup.

7. **Theme + responsive** - the moon/sun control in the top bar toggles light/dark and the choice survives a reload (it's stored in `localStorage`; with nothing stored the app follows the OS setting). Check the map in both: the OSM basemap is tinted dark so it doesn't blast a night-shift operator. Narrow the window below 1024px - the sidebar becomes a hamburger drawer that closes on Escape, on backdrop click, and on navigation.

The old top-level URLs (`/registry`, `/live`, `/gap-analysis`, `/watchlist`) still work - they redirect to their `/app/...` equivalents.

If any of these don't match what's described, that's a real regression worth flagging - not expected behavior.
