import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy.exc import OperationalError

from app import config
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.db import Base, engine
from app.errors import error_response, register_error_handlers
from app.processing.upload import file_too_large

# uvicorn configures only its own loggers; without this, our app.* log lines (including the
# stack traces of unexpected errors) would be silently dropped.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Allowance for the multipart boundary and part headers around the file itself.
MULTIPART_OVERHEAD_BYTES = 16 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (no migrations yet, DECISIONS D15). If PostGIS is down we still
    # start, so /health can report the problem instead of the server crash-looping.
    try:
        Base.metadata.create_all(engine)
    except OperationalError:
        logger.error("Database unavailable at startup; tables not created. Is PostGIS running?")
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description="Upload a Shapefile (.zip) or KML and get the area/length of each feature.",
    version="0.1.0",
    lifespan=lifespan,
)
register_error_handlers(app)
app.include_router(health_router)
app.include_router(files_router)


@app.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    """Refuse a declared-too-large request before its body is received.

    This is only a cheap early exit for honest clients: the header can lie (or be missing),
    so read_upload() still counts the real bytes.
    """
    declared = request.headers.get("content-length", "")
    limit = config.settings.max_upload_bytes
    if declared.isdigit() and int(declared) > limit + MULTIPART_OVERHEAD_BYTES:
        error = file_too_large(limit)
        return error_response(error.status_code, error.code, error.message)
    return await call_next(request)
