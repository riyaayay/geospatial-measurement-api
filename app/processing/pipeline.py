"""Glue: create the files row, read features, measure them, save everything."""

import hashlib
import logging

from geoalchemy2.shape import from_shape
from pyproj import CRS
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Feature, GeoFile
from app.processing.crs import WGS84, crs_label
from app.processing.kml_reader import read_kml
from app.processing.measure import measure_feature
from app.processing.raw_feature import RawFeature
from app.processing.shapefile_reader import read_shapefile
from app.processing.upload import extract_kml_from_kmz, extract_shapefile_parts

logger = logging.getLogger(__name__)


def process_upload(db: Session, filename: str, file_type: str, data: bytes) -> GeoFile:
    """Process an upload that already passed the request checks (type, size, not empty).

    From here on every failure is stored as a FAILED row, so the client can look it up.
    """
    geo_file = GeoFile(
        filename=filename,
        file_type=file_type,
        status="PENDING",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(geo_file)
    db.commit()

    geo_file.status = "PROCESSING"
    db.commit()

    try:
        source_crs, raw_features = _read_features(file_type, data)
        if not raw_features:
            raise AppError("NO_FEATURES", "The file was read successfully but has no features.")
        db.add_all(_build_feature(geo_file.id, raw, source_crs) for raw in raw_features)
        geo_file.crs = crs_label(source_crs)
        geo_file.feature_count = len(raw_features)
        geo_file.status = "COMPLETED"
        # Features and COMPLETED are saved together: a crash halfway leaves no half-written file.
        db.commit()
    except AppError as exc:
        db.rollback()
        logger.warning("File %s (%s) FAILED: %s %s", geo_file.id, filename, exc.code, exc.message)
        _mark_failed(db, geo_file, exc.code, exc.message)
        exc.file_id = str(geo_file.id)
        raise
    except Exception:
        db.rollback()
        logger.exception("Unexpected error while processing file %s", geo_file.id)
        _mark_failed(db, geo_file, "INTERNAL_ERROR", "Unexpected error while processing the file.")
        raise
    logger.info(
        "File %s (%s) COMPLETED: %d features, CRS %s",
        geo_file.id,
        filename,
        geo_file.feature_count,
        geo_file.crs,
    )
    return geo_file


def _read_features(file_type: str, data: bytes) -> tuple[CRS, list[RawFeature]]:
    if file_type == "shapefile":
        return read_shapefile(extract_shapefile_parts(data))
    # KML is always WGS 84 lon/lat (KML 2.2 spec), so there is no CRS to read.
    if file_type == "kmz":
        return WGS84, read_kml(extract_kml_from_kmz(data))
    return WGS84, read_kml(data)


def _build_feature(file_id, raw: RawFeature, source_crs: CRS) -> Feature:
    geometry, m = measure_feature(raw, source_crs)
    return Feature(
        file_id=file_id,
        feature_index=raw.index,
        geometry_type=raw.geometry_type,
        geom=from_shape(geometry, srid=4326) if geometry is not None else None,
        properties=raw.properties,
        measure_status=m.status,
        area_m2=m.area_m2,
        length_m=m.length_m,
        measure_note=m.note,
        projection=m.projection,
    )


def _mark_failed(db: Session, geo_file: GeoFile, code: str, message: str) -> None:
    geo_file.status = "FAILED"
    geo_file.error_code = code
    geo_file.error_message = message
    db.commit()
