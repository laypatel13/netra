"""
Database engine, session, and declarative base for netra.

Reads DATABASE_URL from the environment (see .env.example).

  - Supabase (primary):  Set DATABASE_URL to your Supabase connection string.
    PostGIS must be enabled via the Supabase SQL Editor:
      CREATE EXTENSION IF NOT EXISTS postgis;
  - Local Docker (fallback):  If DATABASE_URL is not set, falls back to the
    local postgis/postgis container defined in docker-compose.yml.
    Start it with:  docker compose up -d
"""
import os
import logging

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://netra:netra@localhost:5432/netra")

_logger = logging.getLogger("netra.database")
if "localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL:
    _logger.info("Using LOCAL Docker PostgreSQL (%s)", DATABASE_URL.split("@")[-1])
else:
    _logger.info("Using REMOTE database (%s)", DATABASE_URL.split("@")[-1])

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency - yields a DB session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
