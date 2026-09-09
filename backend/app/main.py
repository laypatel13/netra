"""
netra — FastAPI entrypoint.

Model 1 (registry/GIS) + Model 2 (live viewing/ANPR/watchlist/tracking).
See PLAN.md at the repo root for full context before extending this.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import cameras, detections, watchlist, audit, feeds

# Dev convenience only — swap for Alembic migrations before anything resembling production.
Base.metadata.create_all(bind=engine)

OPENAPI_TAGS = [
    {
        "name": "registry",
        "description": "**Model 1** — Centralised CCTV Registry & GIS Foundation. Camera onboarding (manual, bulk JSON, bulk CSV), GIS map data, gap-analysis reports, and health-status sync.",
    },
    {
        "name": "feeds",
        "description": "**Model 2** — Live feed catalogue proxy. Resolves RTSP/WHEP/HLS stream URLs from upstream Sentinel sandbox and corp8.cloud sources. Frontend players connect here.",
    },
    {
        "name": "detections",
        "description": "**Model 2** — ANPR detection ingestion and cross-camera route reconstruction. The ANPR pipeline POSTs every plate read here; the route endpoint stitches sightings into a chronological path.",
    },
    {
        "name": "watchlist",
        "description": "Representative watchlist (stolen/suspect/blacklisted vehicles). Uses own dataset — real VAHAN/eGujCop integration is documented in the HLD only, not built.",
    },
    {
        "name": "audit",
        "description": "Audit trail for registry actions. Demonstrates the RBAC/auditability bonus consideration. Admin-only read access.",
    },
]

app = FastAPI(
    title="netra",
    description=(
        "**Unified CCTV Registry & Live Viewing/ANPR Platform** for Sentinel Gujarat.\n\n"
        "Turns 26 fragmented, department-owned CCTV systems into one searchable network — "
        "pull up a plate, see every camera it passed and when, and get auto-alerted if it "
        "matches a stolen/wanted/blacklisted watchlist.\n\n"
        "- **Model 1**: Centralised CCTV Registry & GIS Foundation (mandatory)\n"
        "- **Model 2**: Unified Viewing & Selective Analytics (RTSP/WHEP/HLS + ANPR)\n\n"
        "See the [PLAN.md](https://github.com/) in the repo root for full architecture context."
    ),
    version="0.2.0",
    contact={"name": "Team Netra", "url": "https://sentinel.gujarat.gov.in"},
    license_info={"name": "MIT"},
    openapi_tags=OPENAPI_TAGS,
)

# Was allow_origins=["*"] — tightened per the PLAN.md RBAC TODO. Configurable
# via env var since the dev origin (Vite on :5173) and the eventual hosted
# frontend origin (once Supabase-deployed) differ; comma-separated for more
# than one. Defaults to the local dev origin so nothing breaks out of the box.
_cors_origins = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router)
app.include_router(feeds.router)
app.include_router(detections.router)
app.include_router(watchlist.router)
app.include_router(audit.router)


@app.get("/", tags=["health"])
def root():
    return {"service": "netra", "status": "ok", "version": "0.2.0"}

