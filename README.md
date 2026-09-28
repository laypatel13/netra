# Netra

*Netra - "eyes" - the eyes of the police.*

Unified CCTV registry + live viewing/ANPR platform built for the Sentinel Gujarat CCTV Integration Hackathon (Gujarat Police Innovation Challenge 2026).

**One-line pitch:** Turns 26 fragmented, department-owned CCTV systems into one searchable network - pull up a vehicle's plate (or just its type and colour), see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching any department's existing infrastructure.

Full project context, architecture, endpoints, tech stack, and rules of engagement live in [`PLAN.md`](./PLAN.md). Day-by-day build schedule lives in [`TIMELINE.md`](./TIMELINE.md). How the system stays read-only towards departmental VMS is in [`ARCHITECTURE.md`](./ARCHITECTURE.md). **Read `PLAN.md` before writing any code** - it has the mandatory model choice, the Sentinel sandbox protocol rules (RTSP/TCP, PTS timing, reconnect behavior), and an explicit "do not" list.

**New to this project?** [`test/README.md`](./test/README.md) is the manual-testing and onboarding guide - what each piece does and why, how to run everything locally, and a click-through checklist for the actual UI.

## Structure

```
netra/
  PLAN.md              # full project context - read first
  TIMELINE.md          # day-by-day schedule
  ARCHITECTURE.md      # non-interference architecture + data flow
  render.yaml          # Render deployment
  backend/             # FastAPI + PostgreSQL/PostGIS
    app/
      main.py
      database.py
      models.py
      schemas.py
      serializers.py
      dependencies.py            # header-based RBAC (require_admin)
      investigation_service.py   # incremental cross-camera route engine
      routers/
        cameras.py        # Model 1 - registry, gap analysis, runtime camera health
        feeds.py          # Model 2 - authenticated feed catalogue + HLS proxy
        detections.py     # Model 2 - vehicle detections (plate + attributes) + route reconstruction
        watchlist.py      # watchlist + tiered alert matching
        investigations.py # Model 2 - investigation targets, evidence, route chains, pipeline heartbeat
        audit.py          # audit trail
        auth.py           # demo login for the deployed frontend
    reset_db.py           # drop + recreate the schema after model changes (--yes)
    docker-compose.yml
    requirements.txt
    .env.example
  frontend/             # React + Leaflet
    src/
      pages/
        Dashboard.jsx     # unified control room
        Registry.jsx      # GIS registry map + manual/bulk onboarding + route-on-map
        LiveViewer.jsx    # live feed viewer
        GapAnalysis.jsx   # coverage report
        Watchlist.jsx     # watchlist management + live alert feed
        Investigation.jsx # investigation workstation: targets, route, timeline, evidence review
  anpr/                 # YOLO detection + OCR + color extraction pipeline
    pipeline.py              # entry point: live cameras or --video, optional --investigation
    detector.py
    plate_reader.py
    color.py
    multi_camera.py          # multi-camera orchestrator with a shared inference queue
    investigation_pipeline.py# per-track evidence -> OCR consensus -> scoring -> backend
    tracker.py               # ByteTrack wrapper (camera-local track ids)
    evidence_buffer.py       # per-track observation buffer + best-frame selection
    quality.py, enhance.py   # frame quality scoring + crop enhancement
    ocr_consensus.py         # multi-frame plate consensus
    scoring.py, target_filter.py  # evidence fusion + 3-valued target matching
    linking.py               # cross-camera link feasibility (used by the backend)
    motion_gate.py, frame_sampler.py, camera_manager.py
  test/                  # start here for manual testing - see test/README.md
    README.md
    connectivity/        # standalone scripts to debug feed connectivity in isolation
    seed_data/           # representative cameras.csv + watchlist.json
    smoke_test.sh        # scripted end-to-end API health check
    conftest.py, test_*.py  # pytest suites (DB tests need a throwaway PostGIS, see conftest.py)
```

## Day 1 quick start

**Backend**
```bash
cd backend
cp .env.example .env
# Edit .env - paste your Supabase DATABASE_URL (or leave as-is for local Docker)
# If using Docker fallback:  docker compose up -d
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8030
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

**Validate live feed connectivity (do this first)**
```bash
cd test/connectivity
pip install opencv-python requests
python test_feed_connection.py --email you@example.com --password XXXX-XXXX-XXXX
```
This forces RTSP over TCP, reads timing from PTS (not wall-clock), and reconnects with backoff - matching the mandatory protocol rules in `PLAN.md` Section 8. If this script doesn't cleanly connect and hold a stream, that's the top priority to fix before building anything else.

**Everything else** (seeding representative data, running the ANPR pipeline, a scripted health check, and a manual click-through checklist for the UI) is in [`test/README.md`](./test/README.md).

## Investigation mode

The watchlist answers "has this vehicle been seen?". Investigation mode answers the hackathon-day test case: given a plate (or a type + colour), **reconstruct the route it took across cameras, with a timestamped, location-wise history**.

```bash
cd anpr
python pipeline.py --backend http://localhost:8030 --camera-ids cam01,cam02,cam04 --investigation   # live sandbox feeds
python pipeline.py --backend http://localhost:8030 --video sample.mp4 --investigation                # local file, same code path
```

Create a target on the Investigation page, then:

1. The pipeline tracks each vehicle per camera and buffers several frames of it instead of reading one frame.
2. When a tracked vehicle could match an active target, the best frames are enhanced and OCR'd, the reads are combined into one consensus plate, and the evidence is scored into a match tier (`exact_plate`, `strong_candidate`, `attribute_candidate`).
3. The candidate and its evidence crops go to `/investigations/ingest`; a per-camera observation goes to `/investigations/observations`.
4. The backend links the new observation to the target's earlier sightings on other cameras. A link counts only if the travel time is physically possible: at most 120 km/h between the cameras' registered map locations, or within a configured camera-graph edge. Valid links extend a route chain.
5. The Investigation page shows the reconstructed route, a timeline and every evidence frame. An operator can verify or reject each candidate, and a rejection invalidates any route built on it.

Observation times are the wall-clock time the vehicle was last seen, because per-stream PTS starts near zero on every connection and can't order sightings across cameras (same reasoning as `detections.py`'s use of `created_at`).
