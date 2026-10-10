import pytest
from sqlalchemy.orm import sessionmaker

from app.db import get_db, make_engine
from app.main import app


@pytest.mark.db
def test_health_reports_ok_when_postgis_is_reachable(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_503_when_database_is_down(client):
    # Nothing listens on port 1. Using the app's own engine settings means this test also proves
    # the connect timeout works: without it, this test hangs instead of getting a 503.
    dead_engine = make_engine("postgresql+psycopg://geo:geo@127.0.0.1:1/geo")
    dead_session = sessionmaker(bind=dead_engine)

    def get_dead_db():
        with dead_session() as session:
            yield session

    app.dependency_overrides[get_db] = get_dead_db

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
