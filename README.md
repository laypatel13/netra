# Netra

*Netra — "eyes" — the eyes of the police.*

Unified CCTV registry + live viewing/ANPR platform built for the Sentinel Gujarat CCTV Integration Hackathon (Gujarat Police Innovation Challenge 2026).

**One-line pitch:** Turns 26 fragmented, department-owned CCTV systems into one searchable network — pull up a vehicle's plate, see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching any department's existing infrastructure.

Full project context, architecture, endpoints, tech stack, and rules of engagement live in [`PLAN.md`](./PLAN.md). Day-by-day build schedule lives in [`TIMELINE.md`](./TIMELINE.md). **Read `PLAN.md` before writing any code** — it has the mandatory model choice, the Sentinel sandbox protocol rules (RTSP/TCP, PTS timing, reconnect behavior), and an explicit "do not" list.

**New to this project?** [`test/README.md`](./test/README.md) is the manual-testing and onboarding guide — what each piece does and why, how to run everything locally, and a click-through checklist for the actual UI.

## Structure

```
netra/
  PLAN.md              # full project context — read first
  TIMELINE.md          # day-by-day schedule
  backend/             # FastAPI + PostgreSQL/PostGIS
    app/
      main.py
      database.py
      models.py
      schemas.py
      serializers.py
      routers/
        cameras.py      # Model 1 — registry
        feeds.py        # Model 2 — authenticated feed catalogue + HLS proxy
        detections.py   # Model 2 — vehicle detections (plate + attributes) + route reconstruction
        watchlist.py    # watchlist + tiered alert matching
        audit.py        # audit trail
    docker-compose.yml
    requirements.txt
    .env.example
  frontend/             # React + Leaflet
    src/
      pages/
        Dashboard.jsx    # unified control room
        Registry.jsx     # GIS registry map + route-on-map
        LiveViewer.jsx    # live feed viewer
        GapAnalysis.jsx  # coverage report
        Watchlist.jsx    # watchlist management + live alert feed
  anpr/                 # YOLO detection + OCR + color extraction pipeline
    pipeline.py
    detector.py
    plate_reader.py
    color.py
  test/                  # start here for manual testing — see test/README.md
    README.md
    connectivity/        # standalone scripts to debug feed connectivity in isolation
    seed_data/           # representative cameras.csv + watchlist.json
    smoke_test.sh         # scripted end-to-end API health check
```

## Day 1 quick start

**Backend**
```bash
cd backend
cp .env.example .env
# Edit .env — paste your Supabase DATABASE_URL (or leave as-is for local Docker)
# If using Docker fallback:  docker compose up -d
pip install -r requirements.txt
uvicorn app.main:app --reload
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
This forces RTSP over TCP, reads timing from PTS (not wall-clock), and reconnects with backoff — matching the mandatory protocol rules in `PLAN.md` Section 8. If this script doesn't cleanly connect and hold a stream, that's the top priority to fix before building anything else.

**Everything else** (seeding representative data, running the ANPR pipeline, a scripted health check, and a manual click-through checklist for the UI) is in [`test/README.md`](./test/README.md).
