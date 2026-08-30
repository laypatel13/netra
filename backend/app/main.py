"""
netra — FastAPI entrypoint.

Model 1 (registry/GIS) + Model 2 (live viewing/ANPR/watchlist/tracking).
See plan.md at the repo root for full context before extending this.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import cameras, detections, watchlist

# Dev convenience only — swap for Alembic migrations before anything resembling production.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="netra",
    description="Unified CCTV registry + live viewing/ANPR platform for Sentinel Gujarat.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before submission — see plan.md RBAC note
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router)
app.include_router(detections.router)
app.include_router(watchlist.router)


@app.get("/")
def root():
    return {"service": "netra", "status": "ok"}
