import os

# Must run before `app` is imported: app.config reads DATABASE_URL at import time,
# and tests must never touch the development database.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://geo:geo@localhost:55432/geo_test"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def _tables():
    # Fails loudly (connection error) if PostGIS isn't running: run `docker compose up -d`.
    Base.metadata.create_all(engine)


@pytest.fixture
def db(_tables):
    """A clean database for one test, plus a session for checking what was stored."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE features, files"))
    with SessionLocal() as session:
        yield session
