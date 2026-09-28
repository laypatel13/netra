"""
Drop and recreate every table in DATABASE_URL.

create_all() never alters existing tables, so after a model change the
local database has to be rebuilt (PLAN.md Section 5a). This deletes all
data, so it refuses to run without --yes.

    python reset_db.py --yes
"""
import sys

from app.database import Base, engine
import app.models  # noqa: F401 - importing registers every table on Base.metadata

if "--yes" not in sys.argv:
    sys.exit(f"This drops every table in {engine.url.render_as_string(hide_password=True)}. Re-run with --yes to confirm.")

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
print("Database reset.")
