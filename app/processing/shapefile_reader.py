import codecs
import datetime
import io

import shapefile  # the pyshp package
import shapely.geometry
from pyproj import CRS
from shapely.errors import ShapelyError

from app.errors import AppError
from app.processing.crs import parse_prj
from app.processing.raw_feature import RawFeature
from app.processing.upload import ShapefileParts

Z_TYPES = {shapefile.POINTZ, shapefile.POLYLINEZ, shapefile.POLYGONZ, shapefile.MULTIPOINTZ}


def read_shapefile(parts: ShapefileParts) -> tuple[CRS, list[RawFeature]]:
    crs = parse_prj(parts.prj)
    try:
        with shapefile.Reader(
            shp=io.BytesIO(parts.shp),
            shx=io.BytesIO(parts.shx),
            dbf=io.BytesIO(parts.dbf),
            encoding=_dbf_encoding(parts.cpg),
            # A wrongly declared encoding should give odd characters, not a failed upload.
            encodingErrors="replace",
        ) as reader:
            shape_records = list(reader.iterShapeRecords())
    except Exception as exc:
        # pyshp has no single exception for damaged files (we saw struct.error, KeyError and
        # ShapefileException). Only pyshp runs inside this block, so this can't hide our own bugs.
        raise AppError(
            "CORRUPT_SHAPEFILE",
            f"The Shapefile '{parts.name}' is damaged and could not be read "
            f"({type(exc).__name__}: {exc}).",
        ) from exc
    return crs, [_to_raw_feature(i, sr) for i, sr in enumerate(shape_records)]


def _dbf_encoding(cpg: str | None) -> str:
    if not cpg:
        return "utf-8"
    name = cpg.strip()
    if name.isdigit():
        name = f"cp{name}"  # ESRI tools write Windows code pages as a bare number, e.g. "1252"
    try:
        return codecs.lookup(name).name
    except LookupError:
        return "utf-8"


def _json_safe(value):
    # Date fields come back as datetime.date, which can't be stored in a JSON column.
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _to_raw_feature(index: int, shape_record) -> RawFeature:
    shp = shape_record.shape
    properties = {key: _json_safe(v) for key, v in shape_record.record.as_dict().items()}

    if shp.shapeType == shapefile.NULL:
        return RawFeature(
            index, None, properties, None, "EMPTY", "The feature has no geometry (null shape)."
        )
    if shp.shapeType == shapefile.MULTIPATCH:
        return RawFeature(
            index,
            None,
            properties,
            "MultiPatch",
            "UNSUPPORTED",
            "MultiPatch (3D surface) geometries are not supported.",
        )
    try:
        # __geo_interface__ is pyshp's GeoJSON-like dict; it already drops Z values.
        geometry = shapely.geometry.shape(shp.__geo_interface__)
    except (ValueError, IndexError, TypeError, ShapelyError) as exc:
        return RawFeature(
            index,
            None,
            properties,
            shp.shapeTypeName,
            "INVALID",
            f"Could not build geometry: {exc}",
        )
    note = "Z values ignored; measured in 2D." if shp.shapeType in Z_TYPES else None
    return RawFeature(index, geometry, properties, geometry.geom_type, note=note)
