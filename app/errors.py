import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """An expected problem with the user's input: carries a stable error code and HTTP status.

    Raising this anywhere (even deep inside a reader) ends up as a clean JSON error, never a 500.
    """

    def __init__(self, code: str, message: str, status_code: int = 422, file_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.file_id = file_id


def error_response(
    status_code: int, code: str, message: str, file_id: str | None = None
) -> JSONResponse:
    error = {"code": code, "message": message}
    # Only present when a files row exists, so the client can GET the FAILED record.
    if file_id is not None:
        error["file_id"] = str(file_id)
    return JSONResponse(status_code=status_code, content={"error": error})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.file_id)

    # FastAPI's default is a 422 with a list of pydantic errors; we reshape it into our format
    # and use 400, keeping 422 for "the file was received but its contents are unusable".
    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
        return error_response(400, "VALIDATION_ERROR", f"{field}: {first['msg']}")

    # Unknown routes and wrong methods: Starlette's default body is {"detail": ...}.
    @app.exception_handler(StarletteHTTPException)
    def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return error_response(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), exc.detail)

    # Can't reach PostGIS (down, restarting, connect timeout): a server-side problem, but a
    # temporary one, so 503 tells clients to retry rather than report a bug.
    @app.exception_handler(OperationalError)
    def handle_database_down(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
        return error_response(
            503, "DATABASE_UNAVAILABLE", "The database is unavailable. Please try again shortly."
        )

    # Last resort for bugs (never for bad input): log the details, show the client nothing internal.
    @app.exception_handler(Exception)
    def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, "INTERNAL_ERROR", "Something went wrong on our side.")
