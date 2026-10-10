"""Build test files in code, so every fixture is small, reproducible and has known values."""

import io
import struct
import zipfile
from collections.abc import Callable

import shapefile
from pyproj import CRS

# The .prj text a GIS tool writes for plain lon/lat WGS 84.
WGS84_PRJ = CRS("EPSG:4326").to_wkt("WKT1_ESRI")

# ~1 km x 1 km at the equator (0.009 degrees of latitude is about 1 km).
SQUARE_1KM = [(0.0, 0.0), (0.0, 0.009), (0.009, 0.009), (0.009, 0.0), (0.0, 0.0)]


def one_square(w: shapefile.Writer) -> None:
    w.field("name", "C")
    w.poly([SQUARE_1KM])
    w.record("plot A")


def shapefile_parts(
    stem: str = "parcels",
    prj: str | None = WGS84_PRJ,
    shape_type: int = shapefile.POLYGON,
    build: Callable[[shapefile.Writer], None] = one_square,
    encoding: str = "utf-8",
) -> dict[str, bytes]:
    """A real Shapefile as {filename: bytes}.

    `build(writer)` adds the fields, shapes and records; the default is one 1 km square.
    Pass prj=None to leave out the .prj file.
    """
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type, encoding=encoding) as w:
        build(w)
    parts = {
        f"{stem}.shp": shp.getvalue(),
        f"{stem}.shx": shx.getvalue(),
        f"{stem}.dbf": dbf.getvalue(),
    }
    if prj is not None:
        parts[f"{stem}.prj"] = prj.encode()
    return parts


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def mark_first_member_encrypted(zip_bytes: bytes) -> bytes:
    """Set the "encrypted" flag by patching bytes: Python's zipfile can't write encrypted zips."""
    raw = bytearray(zip_bytes)
    central_dir = raw.find(b"PK\x01\x02")  # first central-directory entry
    (flags,) = struct.unpack_from("<H", raw, central_dir + 8)
    struct.pack_into("<H", raw, central_dir + 8, flags | 0x1)
    return bytes(raw)


def kml_document(body: str, namespace: str = "http://www.opengis.net/kml/2.2") -> bytes:
    """Wrap Placemarks/Folders in a minimal KML document."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<kml xmlns="{namespace}"><Document>{body}</Document></kml>'
    ).encode()


def placemark(geometry: str, name: str = "feature", extra: str = "") -> str:
    return f"<Placemark><name>{name}</name>{extra}{geometry}</Placemark>"


def kml_polygon(outer: str, *inners: str) -> str:
    holes = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{ring}</coordinates></LinearRing>"
        "</innerBoundaryIs>"
        for ring in inners
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{holes}</Polygon>"
    )


# The same 1 km square as SQUARE_1KM, in KML "lon,lat" text.
KML_SQUARE_1KM = "0,0 0.009,0 0.009,0.009 0,0.009 0,0"
