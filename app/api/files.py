import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.errors import AppError
from app.models import Feature, GeoFile
from app.processing.pipeline import process_upload
from app.processing.upload import clean_filename, detect_file_type, read_upload
from app.schemas import (
    ErrorInfo,
    ErrorResponse,
    FeatureMeasurement,
    FileOut,
    MeasurementOut,
    MeasurementPage,
    Summary,
)

router = APIRouter(prefix="/api/files")


def error_responses(descriptions: dict[int, str]) -> dict:
    """OpenAPI entries for an endpoint's errors, all in our `{"error": {...}}` shape.

    "default" covers every other status (such as 500). Declaring it also stops FastAPI from
    documenting its own 422 `{"detail": [...]}` body, which this API never returns: our
    validation errors are 400. (DECISIONS D39)
    """
    descriptions = descriptions | {
        503: "PostGIS is unreachable (DATABASE_UNAVAILABLE); retry shortly",
        "default": "Any other error, e.g. 500 INTERNAL_ERROR",
    }
    return {
        code: {"model": ErrorResponse, "description": text} for code, text in descriptions.items()
    }


# Plain `def` endpoints (not `async def`): pyshp, pyproj and psycopg all block, and FastAPI runs
# plain functions in a thread pool so they don't freeze the event loop. (DECISIONS D18)
#
# Each endpoint is registered with and without the trailing slash. Otherwise FastAPI answers the
# slash-less path with an empty 307 redirect, which `curl` doesn't follow by default, so the call
# looks like it did nothing. The slash-less copy is hidden from /docs. (DECISIONS D30)


@router.post(
    "/",
    status_code=201,
    response_model=FileOut,
    responses=error_responses(
        {
            400: "No `file` field (VALIDATION_ERROR) or an empty file (EMPTY_FILE)",
            413: "Larger than the upload limit (FILE_TOO_LARGE)",
            415: "Not a .zip, .kml or .kmz file (UNSUPPORTED_FILE_TYPE)",
            422: "The contents are unusable (e.g. CORRUPT_ZIP, MISSING_CRS, INVALID_KML); "
            "a FAILED record is stored and `error.file_id` points to it",
        }
    ),
)
@router.post("", status_code=201, response_model=FileOut, include_in_schema=False)
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = clean_filename(file.filename)
    file_type = detect_file_type(filename)
    data = read_upload(file.file, config.settings.max_upload_bytes)
    geo_file = process_upload(db, filename, file_type, data)
    return file_out(geo_file)


@router.get(
    "/{file_id}/",
    response_model=FileOut,
    responses=error_responses({404: "No file with this id (FILE_NOT_FOUND)"}),
)
@router.get("/{file_id}", response_model=FileOut, include_in_schema=False)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return file_out(get_file_or_404(db, file_id))


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementPage,
    responses=error_responses(
        {
            400: "Bad `limit` or `offset` (VALIDATION_ERROR)",
            404: "No file with this id (FILE_NOT_FOUND)",
            409: "The file is not COMPLETED, e.g. it FAILED (FILE_NOT_READY)",
        }
    ),
)
@router.get("/{file_id}/measurements", response_model=MeasurementPage, include_in_schema=False)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    # On by default: the assignment lists geometry among the minimum per-feature fields.
    # Clients that only need the numbers can turn it off to keep responses small. (DECISIONS D28)
    include_geometry: bool = True,
    db: Session = Depends(get_db),
):
    geo_file = get_file_or_404(db, file_id)
    if geo_file.status != "COMPLETED":
        reason = f": {geo_file.error_message}" if geo_file.error_message else ""
        raise AppError(
            "FILE_NOT_READY",
            f"Measurements are not available: the file's status is {geo_file.status}{reason}",
            status_code=409,
            file_id=str(geo_file.id),
        )

    features = db.scalars(
        select(Feature)
        .where(Feature.file_id == geo_file.id)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    ).all()
    # Totals over the whole file, not just this page: one aggregate query.
    total_area, total_length, measured, not_applicable = db.execute(
        select(
            func.coalesce(func.sum(Feature.area_m2), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
            func.count().filter(Feature.measure_status == "MEASURED"),
            func.count().filter(Feature.measure_status == "NOT_APPLICABLE"),
        ).where(Feature.file_id == geo_file.id)
    ).one()

    return MeasurementPage(
        file_id=str(geo_file.id),
        crs=geo_file.crs,
        geometry_crs="EPSG:4326",
        total=geo_file.feature_count,
        limit=limit,
        offset=offset,
        summary=Summary(
            total_area_m2=total_area,
            total_length_m=total_length,
            measured=measured,
            not_applicable=not_applicable,
            # The rest (EMPTY, INVALID, UNSUPPORTED) are features we wanted to measure but couldn't.
            failed=geo_file.feature_count - measured - not_applicable,
        ),
        items=[feature_out(f, geo_file.crs, include_geometry) for f in features],
    )


def get_file_or_404(db: Session, file_id: str) -> GeoFile:
    # A malformed id is reported as "not found" too: the client only needs to know it doesn't exist.
    try:
        key = uuid.UUID(file_id)
    except ValueError:
        key = None
    geo_file = db.get(GeoFile, key) if key else None
    if geo_file is None:
        raise AppError("FILE_NOT_FOUND", f"No file with id '{file_id}'.", status_code=404)
    return geo_file


def file_out(geo_file: GeoFile) -> FileOut:
    error = None
    if geo_file.error_code:
        error = ErrorInfo(code=geo_file.error_code, message=geo_file.error_message or "")
    return FileOut(
        id=str(geo_file.id),
        filename=geo_file.filename,
        file_type=geo_file.file_type,
        size_bytes=geo_file.size_bytes,
        sha256=geo_file.sha256,
        feature_count=geo_file.feature_count,
        crs=geo_file.crs,
        status=geo_file.status,
        error=error,
        created_at=geo_file.created_at,
    )


def feature_out(feature: Feature, crs: str | None, include_geometry: bool) -> FeatureMeasurement:
    geometry = None
    if include_geometry and feature.geom is not None:
        geometry = mapping(to_shape(feature.geom))
    return FeatureMeasurement(
        feature_index=feature.feature_index,
        geometry_type=feature.geometry_type,
        crs=crs,
        properties=feature.properties,
        measurement=MeasurementOut(
            status=feature.measure_status,
            area_m2=feature.area_m2,
            length_m=feature.length_m,
            projection=feature.projection,
            note=feature.measure_note,
        ),
        geometry=geometry,
    )
