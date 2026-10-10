"""A small KML reader: Placemarks with Point, LineString, LinearRing, Polygon and MultiGeometry.

We parse the XML ourselves (GDAL's KML driver is blocked on the dev machine, see DECISIONS D9).
Tags are matched by their local name, ignoring the namespace, so KML 2.0/2.1/2.2 files and
files without a namespace all work.
"""

import math

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from app.errors import AppError
from app.processing.raw_feature import RawFeature

GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
# Recognised as geometry so we can name them in the "unsupported" note.
UNSUPPORTED_TAGS = {"Model", "Track", "MultiTrack"}
MULTI_TYPES = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}


class _EmptyGeometry(Exception):
    pass


class _UnsupportedGeometry(Exception):
    pass


def read_kml(data: bytes) -> list[RawFeature]:
    try:
        # defusedxml refuses entity tricks (billion laughs, external entities / XXE).
        root = fromstring(data)
    except DefusedXmlException as exc:
        raise AppError(
            "INVALID_KML",
            "The KML file contains XML entity declarations, "
            "which are refused for security reasons.",
        ) from exc
    except ParseError as exc:
        raise AppError("INVALID_KML", f"The file is not valid XML ({exc}).") from exc
    if _local(root.tag) != "kml":
        raise AppError(
            "INVALID_KML", f"The file is XML but not KML (root element is <{_local(root.tag)}>)."
        )

    # root.iter() walks the whole tree in document order, so Placemarks inside any depth of
    # Document/Folder are found, and their index matches the order in the file.
    placemarks = [el for el in root.iter() if _local(el.tag) == "Placemark"]
    return [_read_placemark(i, pm) for i, pm in enumerate(placemarks)]


def _local(tag: str) -> str:
    """'{http://www.opengis.net/kml/2.2}Placemark' -> 'Placemark'."""
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    return next((c for c in el if _local(c.tag) == name), None)


def _read_placemark(index: int, pm) -> RawFeature:
    properties = _properties(pm)
    geom_el = next((c for c in pm if _local(c.tag) in GEOMETRY_TAGS | UNSUPPORTED_TAGS), None)
    if geom_el is None:
        return RawFeature(index, None, properties, None, "EMPTY", "The placemark has no geometry.")

    tag = _local(geom_el.tag)
    try:
        geometry = _parse_geometry(geom_el)
    except _EmptyGeometry:
        return RawFeature(index, None, properties, tag, "EMPTY", "The geometry has no coordinates.")
    except _UnsupportedGeometry as exc:
        return RawFeature(index, None, properties, tag, "UNSUPPORTED", str(exc))
    # shapely is inconsistent: short rings raise ValueError, a one-point line raises GEOSException.
    except (ValueError, ShapelyError) as exc:
        return RawFeature(index, None, properties, tag, "INVALID", f"Invalid geometry: {exc}")

    notes = []
    if tag == "LinearRing":
        notes.append("KML LinearRing treated as a closed LineString.")
    if any(not _has_coordinates(el) for el in pm.iter() if _local(el.tag) == "innerBoundaryIs"):
        notes.append("Empty inner boundary (hole) ignored.")
    if _has_altitude(pm):
        notes.append("Altitude values ignored; measured in 2D.")
    # The geometry is a closed LineString (GeoJSON has no LinearRing type), but the type we report
    # is the one the file used. (DECISIONS D33)
    geometry_type = "LinearRing" if tag == "LinearRing" else geometry.geom_type
    return RawFeature(index, geometry, properties, geometry_type, note=" ".join(notes) or None)


def _properties(pm) -> dict:
    properties = {}
    if pm.get("id"):
        properties["kml_id"] = pm.get("id")
    for child in pm:
        if _local(child.tag) in ("name", "description"):
            properties[_local(child.tag)] = (child.text or "").strip()
    for el in pm.iter():
        if _local(el.tag) == "Data" and el.get("name"):
            value = _child(el, "value")
            properties[el.get("name")] = (value.text or "").strip() if value is not None else ""
        elif _local(el.tag) == "SimpleData" and el.get("name"):
            properties[el.get("name")] = (el.text or "").strip()
    return properties


def _parse_geometry(el) -> BaseGeometry:
    tag = _local(el.tag)
    if tag == "Point":
        coords = _coordinates(el)
        if len(coords) != 1:
            raise ValueError(f"a Point needs exactly one coordinate, got {len(coords)}")
        return Point(coords[0])
    if tag in ("LineString", "LinearRing"):
        coords = _coordinates(el)
        if len(coords) < 2:
            raise ValueError(f"a {tag} needs at least 2 coordinates, got {len(coords)}")
        return LineString(coords)
    if tag == "Polygon":
        outer = _child(el, "outerBoundaryIs")
        if outer is None:
            raise _EmptyGeometry()
        # An empty hole removes nothing, so it is skipped rather than failing a good outer ring
        # (the note is added in _read_placemark). (DECISIONS D29)
        holes = [
            _coordinates(inner)
            for inner in el
            if _local(inner.tag) == "innerBoundaryIs" and _has_coordinates(inner)
        ]
        return Polygon(_coordinates(outer), holes)
    if tag == "MultiGeometry":
        return _parse_multi(el)
    raise _UnsupportedGeometry(f"KML geometry <{tag}> is not supported for measurement.")


def _parse_multi(el) -> BaseGeometry:
    parts = []
    for child in el:
        if _local(child.tag) in GEOMETRY_TAGS | UNSUPPORTED_TAGS:
            geom = _parse_geometry(child)
            # Flatten nested MultiGeometry, so polygons nested inside one still form a MultiPolygon.
            parts.extend(geom.geoms if isinstance(geom, BaseMultipartGeometry) else [geom])
    if not parts:
        raise _EmptyGeometry()
    types = {p.geom_type for p in parts}
    if len(types) == 1 and next(iter(types)) in MULTI_TYPES:
        return MULTI_TYPES[next(iter(types))](parts)
    return GeometryCollection(parts)


def _coordinate_tokens(el) -> list[str]:
    """The 'lon,lat[,alt]' strings of the first <coordinates> under `el` ([] if none)."""
    coords_el = next((c for c in el.iter() if _local(c.tag) == "coordinates"), None)
    return (coords_el.text or "").split() if coords_el is not None else []


def _has_coordinates(el) -> bool:
    return bool(_coordinate_tokens(el))


def _coordinates(el) -> list[tuple[float, float]]:
    """Parse '<coordinates>lon,lat[,alt] lon,lat[,alt] ...</coordinates>' under `el`."""
    tokens = _coordinate_tokens(el)
    if not tokens:
        raise _EmptyGeometry()
    coords = []
    for token in tokens:
        values = token.split(",")
        try:
            numbers = [float(v) for v in values]
        except ValueError:
            numbers = []
        if len(numbers) not in (2, 3) or not all(math.isfinite(n) for n in numbers):
            message = f"coordinate '{token}' is not 'longitude,latitude[,altitude]'"
            if token.startswith(",") or token.endswith(","):
                # A space after a comma split one tuple in two ("0, 0") or glued a comma to a tuple
                # ("0,0, 1,1"). The spec forbids both and they are ambiguous: explain, don't guess.
                message += (
                    "; KML coordinates need no spaces after commas: write 'lon,lat[,alt]' tuples "
                    "separated by spaces"
                )
            raise ValueError(message)
        lon, lat = numbers[0], numbers[1]
        # KML is always lon/lat; values like 500000,4000000 mean projected data was put in a KML.
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(
                f"coordinate '{token}' is outside the valid range "
                "(longitude -180..180, latitude -90..90)"
            )
        coords.append((lon, lat))  # altitude dropped on purpose (DECISIONS D13)
    return coords


def _has_altitude(pm) -> bool:
    # Google Earth writes ",0" for every point; only real heights are worth a note.
    for el in pm.iter():
        if _local(el.tag) == "coordinates":
            for token in (el.text or "").split():
                values = token.split(",")
                if len(values) == 3:
                    try:
                        if float(values[2]) != 0:
                            return True
                    except ValueError:
                        pass
    return False
