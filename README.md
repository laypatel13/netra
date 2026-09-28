# Netra

*Netra — "eyes" — the eyes of the police.*

Unified CCTV registry + live viewing / ANPR / investigation platform built for the Sentinel Gujarat CCTV Integration Hackathon (Gujarat Police Innovation Challenge 2026).

**One-line pitch:** Turns 26 fragmented, department-owned CCTV systems into one searchable network — pull up a vehicle's plate (or just its type and color), see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching any department's existing infrastructure.

Full project context, architecture, and constraints live in [`PLAN.md`](./PLAN.md). Day-by-day build schedule lives in [`TIMELINE.md`](./TIMELINE.md).

---

## Architecture

| Layer | Technology |
|---|---|
| Backend API | FastAPI (Python 3.14) + SQLAlchemy 2.0 |
| Database | PostgreSQL + PostGIS (Supabase prod / local Docker dev) |
| Frontend | React 18 + Vite + Tailwind CSS |
| GIS Mapping | Leaflet + react-leaflet |
| Vehicle Detection | YOLOv8 (ultralytics) |
| Plate OCR | EasyOCR |
| Color Extraction | OpenCV HSV histogram analysis |
| Live Streaming | HLS.js (browser) / RTSP over TCP (pipeline) |
| Deployment | Render (render.yaml) |

---

## Structure

```
netra/
  PLAN.md                  # full project context — read first
  TIMELINE.md              # day-by-day schedule
  README.md                # this file

  backend/                 # FastAPI + PostgreSQL/PostGIS
    app/
      main.py              # FastAPI entrypoint, CORS, static mounts
      database.py          # SQLAlchemy engine + session factory
      models.py            # 20+ ORM models (Camera → RouteChain)
      schemas.py           # Pydantic request/response schemas
      serializers.py       # Detection → read-model converters
      dependencies.py      # RBAC helpers (get_actor, require_admin)
      investigation_service.py   # Route-chain + cross-camera state engine
      prediction_service.py      # ML transition prediction readiness
      evaluation_service.py      # Dataset evaluation metrics
      route_engine.py            # Camera graph traversal
      routers/
        cameras.py         # Model 1 — registry (manual/bulk/CSV)
        feeds.py           # Model 2 — authenticated HLS proxy + RTSP catalogue
        detections.py      # Model 2 — vehicle detections + route reconstruction
        watchlist.py       # Watchlist CRUD + tiered alert matching
        investigations.py  # Investigation targets, candidates, observations,
                           #   timeline, route history, predictions, heartbeat,
                           #   purge, recording sessions
        investigation_ingest_schemas.py  # Pydantic schemas for pipeline → backend
        audit.py           # Audit trail for registry actions
        auth.py            # Demo login for deployed frontend
    docker-compose.yml     # Local PostGIS container
    requirements.txt       # Python dependencies
    .env.example           # Configuration template
    seed_investigation.py  # Seed sample investigation data

  frontend/                # React + Vite + Tailwind
    src/
      App.jsx              # Router + layout shell
      main.jsx             # React DOM entry
      pages/
        Landing.jsx        # Public marketing/overview page at /
        Login.jsx          # Demo auth gate
        Dashboard.jsx      # Unified control room
        Registry.jsx       # GIS map + route-on-map visualization
        LiveViewer.jsx     # Multi-camera HLS grid
        Investigation.jsx  # Investigation workstation (targets, candidates,
                           #   evidence viewer, timeline, route history,
                           #   predictions, play/pause/delete controls)
        Watchlist.jsx      # Watchlist management + live alert feed
        GapAnalysis.jsx    # Coverage gap report
        DatasetHealth.jsx  # Pipeline/dataset quality metrics
        TypeSpecimen.jsx   # Design system specimen page
        NotFound.jsx       # 404
      components/
        AppShell.jsx       # Sidebar navigation shell
        HlsPlayer.jsx     # HLS video player with reconnect-with-backoff
        AlertRow.jsx       # Watchlist alert display row
        NetraLogo.jsx      # SVG logo component
        PageHeader.jsx     # Page header wrapper
        Reveal.jsx         # Scroll-reveal animation
        StopList.jsx       # Route stop list
        VehicleQueryForm.jsx  # Vehicle search form
        ui/                # Reusable UI primitives
          Badge.jsx, Button.jsx, Card.jsx, Feedback.jsx,
          Field.jsx, Segmented.jsx, Stat.jsx, ThemeToggle.jsx
      lib/
        api.js             # Fetch wrapper for backend API
        auth.js            # Auth state management
        format.js          # Date/number formatters
        theme.jsx          # Theme provider (light/dark)
        ringTurns.js       # Animation utility
      styles/
        index.css          # Design tokens + Tailwind base

  anpr/                    # Vehicle detection + OCR + investigation pipeline
    pipeline.py            # Main entry — single-cam video or multi-cam CCTV
    multi_camera.py        # Multi-camera orchestrator (shared inference queue)
    investigation_pipeline.py  # Evidence gathering, scoring, target matching
    detector.py            # YOLOv8 vehicle detector wrapper
    plate_reader.py        # EasyOCR plate reader
    color.py               # HSV-based dominant color extraction
    quality.py             # Frame quality scoring
    enhance.py             # Crop enhancement (CLAHE, sharpening)
    evidence_buffer.py     # TrackBuffer / Observation / frame selection
    scoring.py             # Evidence fusion + match tier computation
    target_filter.py       # 3-valued logic target matching
    ocr_consensus.py       # Multi-frame OCR consensus
    tracker.py             # Simple centroid tracker
    motion_gate.py         # MOG2 motion-based frame skipping
    frame_sampler.py       # Adaptive frame sampling
    camera_info.py         # Camera metadata + stream URL resolution
    camera_manager.py      # Connection management + backoff
    linking.py             # Cross-camera link candidate generation
    requirements.txt       # Python dependencies (separate venv)

  test/                    # Testing toolkit
    README.md              # Manual testing + onboarding guide
    smoke_test.sh          # 14-check end-to-end API health check
    connectivity/          # Standalone feed connectivity scripts
    seed_data/             # Representative cameras.csv + watchlist.json
    test_*.py              # 15+ test suites (phases 8-19)

  render.yaml              # Render deployment config
  .gitignore
```

---

## Quick Start

### Prerequisites

- Python 3.11+ (3.14 tested)
- Node.js 18+ and npm
- PostgreSQL with PostGIS (or use Docker)

### Backend

```bash
cd backend
cp .env.example .env
# Edit .env — paste your Supabase DATABASE_URL or leave default for local Docker
# If using Docker:  docker compose up -d
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### ANPR Pipeline

The pipeline runs in its own Python virtual environment:

```bash
cd anpr
python -m venv venv
# Windows: venv\Scripts\activate  |  Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
```

**Investigation mode (multi-camera, live CCTV):**
```bash
python pipeline.py --investigation
```

**Local video testing:**
```bash
python pipeline.py --video sample.mp4 --investigation
```

**Standard single-camera ANPR (non-investigation):**
```bash
python pipeline.py
```

---

## Key Features

### Model 1 — Centralised CCTV Registry & GIS
- Camera onboarding (manual, bulk JSON, bulk CSV)
- Interactive GIS map with department/status filters
- Gap-analysis report (coverage, stale cameras, missing departments)
- RBAC (admin/department/viewer) + audit trail

### Model 2 — Unified Viewing & Analytics
- Live multi-camera HLS grid with reconnect-with-backoff
- Real-time ANPR (YOLOv8 + EasyOCR)
- Vehicle attribute tracking (type + color + thumbnail)
- Watchlist with tiered matching (exact plate → attribute-based)
- Cross-camera route reconstruction on GIS map

### Investigation Pipeline (Phase 2)
- Multi-camera investigation targets with play/pause/delete
- Multi-frame evidence buffering and quality-ranked frame selection
- OCR consensus across multiple observations
- Evidence fusion scoring with match tier classification
- Cross-camera route chain reconstruction
- Pipeline heartbeat monitoring
- Predictive transition analysis
- Full investigation workstation UI

---

## Environment Variables

See [`backend/.env.example`](./backend/.env.example) for all configuration options:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `CCTV_HOST` | Sentinel camera CDN host |
| `CCTV_DIRECT_IP` | RTSP/WebRTC direct IP |
| `CCTV_EMAIL` | Sentinel portal credentials |
| `CCTV_PASSWORD` | Sentinel access password |
| `CORS_ORIGINS` | Allowed frontend origins |
| `DEMO_EMAIL` / `DEMO_PASSWORD` | Judge login credentials |

---

## API Documentation

With the backend running, visit:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## Testing

```bash
# API smoke test (requires backend running)
cd test && bash smoke_test.sh

# Python unit tests
cd test && python -m pytest test_*.py -v

# ANPR pipeline tests
cd anpr && python -m pytest test_*.py -v
```

See [`test/README.md`](./test/README.md) for the full manual-testing and onboarding guide.

---

## Deployment

Render deployment is pre-configured via [`render.yaml`](./render.yaml). Set the environment variables in the Render dashboard and deploy.

---

## License

MIT
