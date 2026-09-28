"""
Shared pytest setup for the backend + anpr test suites.

The database tests wipe every table between tests, so they refuse to run
against whatever DATABASE_URL happens to be in backend/.env (that's the dev
database, or worse, Supabase). Point them at a throwaway PostGIS instead:

    docker run -d --rm --name netra-testdb -p 55433:5432 \
        -e POSTGRES_USER=netra -e POSTGRES_PASSWORD=netra -e POSTGRES_DB=netra_test \
        postgis/postgis:16-3.4
    cd backend
    NETRA_TEST_DATABASE_URL=postgresql://netra:netra@localhost:55433/netra_test \
        pytest ../test

Without NETRA_TEST_DATABASE_URL the database tests are skipped; the pure
unit tests (OCR consensus, motion gate, quality, ...) still run.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "anpr"))

TEST_DATABASE_URL = os.getenv("NETRA_TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # Must be set before app.database is imported anywhere; load_dotenv()
    # there does not override an existing environment variable.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set NETRA_TEST_DATABASE_URL to a throwaway PostGIS database (see test/conftest.py)",
)


@pytest.fixture(scope="session")
def _engine():
    if not TEST_DATABASE_URL:
        pytest.skip("set NETRA_TEST_DATABASE_URL to a throwaway PostGIS database (see test/conftest.py)")
    from sqlalchemy import text
    from app.database import Base, engine
    import app.models  # noqa: F401 - importing registers every table on Base.metadata

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_session(_engine):
    """A session on a freshly emptied schema."""
    from app.database import Base, SessionLocal

    with _engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)
