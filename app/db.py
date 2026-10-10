from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def make_engine(url: str) -> Engine:
    # pool_pre_ping: if Postgres restarted, a stale pooled connection is replaced
    # instead of failing a request.
    # connect_timeout: libpq waits forever by default, so a down database would hang every
    # request (and /health) instead of failing fast with a 503.
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    with SessionLocal() as session:
        yield session
