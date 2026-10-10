"""Response shapes. They're kept separate from the ORM models, so the API contract is explicit."""

from datetime import datetime

from pydantic import BaseModel


class ErrorInfo(BaseModel):
    code: str
    message: str


class ErrorDetail(ErrorInfo):
    file_id: str | None = None  # only present when a FAILED file record was stored


class ErrorResponse(BaseModel):
    """The body of every error response (built in app/errors.py), documented for /docs."""

    error: ErrorDetail


class FileOut(BaseModel):
    id: str
    filename: str
    file_type: str
    size_bytes: int | None
    sha256: str | None  # hex digest of the uploaded bytes
    feature_count: int | None
    crs: str | None
    status: str
    error: ErrorInfo | None
    created_at: datetime


class MeasurementOut(BaseModel):
    status: str
    area_m2: float | None
    length_m: float | None
    projection: str | None
    note: str | None


class FeatureMeasurement(BaseModel):
    feature_index: int
    geometry_type: str | None
    crs: str | None  # the source CRS of the file
    properties: dict
    measurement: MeasurementOut
    geometry: dict | None  # GeoJSON in EPSG:4326; null when include_geometry=false


class Summary(BaseModel):
    total_area_m2: float
    total_length_m: float
    measured: int
    # Points: nothing to measure, which is expected and not a problem. (DECISIONS D31)
    not_applicable: int
    failed: int  # EMPTY, INVALID or UNSUPPORTED: each item's measurement.note says why


class MeasurementPage(BaseModel):
    file_id: str
    crs: str | None
    geometry_crs: str  # returned geometries are always WGS 84, whatever the source CRS
    total: int
    limit: int
    offset: int
    summary: Summary
    items: list[FeatureMeasurement]
