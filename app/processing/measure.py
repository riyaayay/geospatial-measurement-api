"""Area and length in metres for one feature. Never measured in degrees (see PLAN.md section 4)."""

from dataclasses import dataclass

import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.processing.crs import local_projection, project, to_wgs84
from app.processing.raw_feature import RawFeature

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
POINT_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: str  # MEASURED, NOT_APPLICABLE, UNSUPPORTED, INVALID or EMPTY
    area_m2: float | None = None
    length_m: float | None = None
    projection: str | None = None  # the exact projection used, for transparency
    note: str | None = None


def measure_feature(raw: RawFeature, source_crs: CRS) -> tuple[BaseGeometry | None, Measurement]:
    """Reproject one feature to WGS 84 and measure it.

    Returns the WGS 84 geometry (what we store) and its measurement.
    """
    if raw.geometry is None:
        # The reader already found the problem (null shape, unsupported KML tag, bad numbers).
        return None, Measurement(raw.problem or "EMPTY", note=raw.note)
    try:
        geometry = to_wgs84(raw.geometry, source_crs)
    except ValueError as exc:
        return None, Measurement("INVALID", note=str(exc))
    measurement = measure(geometry)
    measurement.note = " ".join(n for n in (raw.note, measurement.note) if n) or None
    return geometry, measurement


def measure(geometry: BaseGeometry) -> Measurement:
    """Measure a geometry given in WGS 84 lon/lat."""
    kind = geometry.geom_type
    if geometry.is_empty:
        return Measurement("EMPTY", note="The geometry is empty.")
    if kind in POINT_TYPES:
        return Measurement("NOT_APPLICABLE", note="Points have no area or length.")
    if kind not in AREA_TYPES | LENGTH_TYPES:
        return Measurement("UNSUPPORTED", note=f"{kind} is not supported for measurement.")
    if not geometry.is_valid:
        # Reported, not repaired: make_valid would silently change the user's shape and area.
        return Measurement("INVALID", note=f"Invalid geometry: {shapely.is_valid_reason(geometry)}")

    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        return Measurement(
            "INVALID",
            note="Coordinates are outside the valid longitude/latitude range; "
            "the .prj file probably does not match the data.",
        )
    note = None
    if max_lon - min_lon > 180:
        # A feature crossing +-180 (Fiji, Russia, a Pacific flight path) looks like it wraps the
        # whole globe in plain lon/lat, so its centroid would be on the wrong side of the Earth.
        # Moving the western longitudes to 180..360 joins it up again. (DECISIONS D36)
        geometry = _shift_west_longitudes(geometry)
        min_lon, _, max_lon, _ = geometry.bounds
        if max_lon - min_lon > 180 or not geometry.is_valid:
            return Measurement(
                "UNSUPPORTED",
                note="The feature spans more than 180 degrees of longitude even across the "
                "antimeridian (+-180), for example because it surrounds a pole; "
                "that is not supported yet.",
            )
        note = "Crosses the antimeridian (+-180): measured with longitudes shifted to 0..360."

    # The centroid is computed in degrees, which is fine: it only picks the projection centre.
    projection = local_projection(geometry.centroid)
    projected = project(geometry, projection)
    if kind in AREA_TYPES:
        return Measurement("MEASURED", area_m2=projected.area, projection=projection, note=note)
    return Measurement("MEASURED", length_m=projected.length, projection=projection, note=note)


def _shift_west_longitudes(geometry: BaseGeometry) -> BaseGeometry:
    """Add 360 to every negative longitude, so -179 becomes 181 and sits next to 179."""

    def shift(coords):
        coords = coords.copy()
        coords[coords[:, 0] < 0, 0] += 360
        return coords

    return shapely.transform(geometry, shift)
