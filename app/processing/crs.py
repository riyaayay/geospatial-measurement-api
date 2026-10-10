import numpy as np
import shapely
from pyproj import CRS, Proj, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from app.errors import AppError

# KML coordinates are always WGS 84 longitude/latitude (OGC KML 2.2 spec).
WGS84 = CRS.from_epsg(4326)


def parse_prj(text: str) -> CRS:
    """Turn the contents of a Shapefile's .prj file into a pyproj CRS."""
    try:
        crs = CRS.from_wkt(text)
    except CRSError as exc:
        raise AppError(
            "INVALID_CRS", f"The .prj file is not a coordinate system we can understand ({exc})."
        ) from exc
    # A vertical CRS (e.g. "NAVD88 height") parses fine but has no x/y to measure.
    if not (crs.is_geographic or crs.is_projected):
        raise AppError(
            "INVALID_CRS",
            f"The .prj file describes a {crs.type_name}, which has no horizontal coordinates.",
        )
    return crs


def crs_label(crs: CRS) -> str:
    """A short, human-readable name for the API, e.g. "EPSG:4326"."""
    epsg = crs.to_epsg()
    if epsg is not None:
        return f"EPSG:{epsg}"
    if crs.name and crs.name != "unknown":
        return f"{crs.name} (no EPSG code)"
    return f"Custom {crs.type_name} (no EPSG code)"


def to_wgs84(geometry: BaseGeometry, source_crs: CRS) -> BaseGeometry:
    """Reproject from the file's CRS to WGS 84 lon/lat. Raises ValueError if that fails.

    We always transform, even for "4326" files: an ESRI-written 4326 .prj is not `==` to
    EPSG:4326 in pyproj, and transforming identical systems is a cheap no-op anyway.
    """
    # always_xy: keep (x=lon, y=lat) order; EPSG:4326 officially lists latitude first.
    transformer = Transformer.from_crs(source_crs, WGS84, always_xy=True)
    result = _apply(geometry, transformer.transform)
    # pyproj returns inf for coordinates it can't convert, rather than raising.
    if not np.isfinite(shapely.get_coordinates(result)).all():
        raise ValueError("Some coordinates could not be converted from the file's CRS to WGS 84.")
    return result


def local_projection(center: Point) -> str:
    """Lambert Azimuthal Equal-Area centred on `center` (lon/lat): see PLAN.md section 4.2.

    Equal-area means areas are preserved; centring it on the feature keeps distance
    distortion tiny. 6 decimals (~0.1 m) is plenty for choosing a centre.
    """
    return f"+proj=laea +lat_0={center.y:.6f} +lon_0={center.x:.6f} +datum=WGS84 +units=m"


def project(geometry: BaseGeometry, projection: str) -> BaseGeometry:
    """Apply a projection to a WGS 84 lon/lat geometry; the result is in metres.

    pyproj.Proj is the projection formula itself (degrees in, metres out). That is valid here
    because the input is already WGS 84, the same datum as the projection, and it is ~15x
    faster to create than a Transformer, which matters because we build one per feature.
    """
    return _apply(geometry, Proj(projection))


def _apply(geometry: BaseGeometry, func) -> BaseGeometry:
    # shapely hands us all coordinates as one (N, 2) array; func works on x and y arrays.
    return shapely.transform(geometry, lambda xy: np.column_stack(func(xy[:, 0], xy[:, 1])))
