import datetime

import pytest
import shapefile
from shapely.geometry import Polygon

from app.errors import AppError
from app.processing.shapefile_reader import read_shapefile
from app.processing.upload import extract_shapefile_parts
from tests.builders import SQUARE_1KM, make_zip, shapefile_parts


def read(**kwargs):
    """Build a Shapefile in memory, zip it, and run it through the real M1 + M2 path."""
    extra = kwargs.pop("extra_files", {})
    zip_bytes = make_zip(shapefile_parts(**kwargs) | extra)
    return read_shapefile(extract_shapefile_parts(zip_bytes))


OUTER = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
HOLE = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]


def test_reads_one_polygon_with_its_attributes_and_crs():
    crs, features = read()

    assert crs.to_epsg() == 4326
    assert len(features) == 1
    feature = features[0]
    assert feature.index == 0
    assert feature.geometry_type == "Polygon"
    assert feature.geometry.equals(Polygon(SQUARE_1KM))
    assert feature.properties == {"name": "plot A"}
    assert feature.problem is None


def test_polygon_hole_is_kept():
    def build(w):
        w.field("name", "C")
        w.shape({"type": "Polygon", "coordinates": [OUTER, HOLE]})
        w.record("holey")

    _, [feature] = read(build=build)
    assert feature.geometry.area == pytest.approx(100 - 4)  # in source units; just checks the hole


@pytest.mark.parametrize(
    ("shape_type", "geojson", "expected_type"),
    [
        (
            shapefile.POLYGON,
            {
                "type": "MultiPolygon",
                "coordinates": [[OUTER], [[(20, 20), (20, 21), (21, 21), (20, 20)]]],
            },
            "MultiPolygon",
        ),
        (shapefile.POLYLINE, {"type": "LineString", "coordinates": [(0, 0), (1, 1)]}, "LineString"),
        (
            shapefile.POLYLINE,
            {"type": "MultiLineString", "coordinates": [[(0, 0), (1, 1)], [(5, 5), (6, 6)]]},
            "MultiLineString",
        ),
        (
            shapefile.MULTIPOINT,
            {"type": "MultiPoint", "coordinates": [(1, 2), (3, 4)]},
            "MultiPoint",
        ),
    ],
)
def test_geometry_types_are_converted(shape_type, geojson, expected_type):
    def build(w):
        w.field("name", "C")
        w.shape(geojson)
        w.record("x")

    _, [feature] = read(shape_type=shape_type, build=build)
    assert feature.geometry_type == expected_type
    assert feature.geometry.geom_type == expected_type


def test_point_is_converted():
    # Separate test: pyshp's Writer.shape() can't write a Point given as a GeoJSON dict.
    def build(w):
        w.field("name", "C")
        w.point(1, 2)
        w.record("well")

    _, [feature] = read(shape_type=shapefile.POINT, build=build)
    assert feature.geometry_type == "Point"
    assert (feature.geometry.x, feature.geometry.y) == (1, 2)


def test_features_keep_file_order_as_their_index():
    def build(w):
        w.field("name", "C")
        for i in range(5):
            w.point(i, i)
            w.record(f"p{i}")

    _, features = read(shape_type=shapefile.POINT, build=build)
    assert [f.index for f in features] == [0, 1, 2, 3, 4]
    assert [f.properties["name"] for f in features] == ["p0", "p1", "p2", "p3", "p4"]


def test_null_shape_is_reported_as_empty_but_keeps_attributes():
    def build(w):
        w.field("name", "C")
        w.null()
        w.record("nothing here")

    _, [feature] = read(build=build)
    assert feature.geometry is None
    assert feature.geometry_type is None
    assert feature.problem == "EMPTY"
    assert feature.properties == {"name": "nothing here"}


def test_multipatch_is_reported_as_unsupported():
    def build(w):
        w.field("name", "C")
        w.multipatch([[(0, 0, 0), (1, 0, 0), (1, 1, 1), (0, 0, 0)]], partTypes=[shapefile.RING])
        w.record("roof")

    _, [feature] = read(shape_type=shapefile.MULTIPATCH, build=build)
    assert feature.geometry is None
    assert feature.geometry_type == "MultiPatch"
    assert feature.problem == "UNSUPPORTED"


def test_z_values_are_dropped_with_a_note():
    def build(w):
        w.field("name", "C")
        w.linez([[(0, 0, 10), (1, 1, 20)]])
        w.record("road")

    _, [feature] = read(shape_type=shapefile.POLYLINEZ, build=build)
    assert feature.geometry_type == "LineString"
    assert not feature.geometry.has_z
    assert "Z" in feature.note


def test_attribute_values_are_json_safe():
    def build(w):
        w.field("name", "C")
        w.field("surveyed", "D")
        w.field("active", "L")
        w.field("area_ha", "N", decimal=2)
        w.point(1, 1)
        w.record("well", datetime.date(2026, 1, 2), True, 3.25)

    _, [feature] = read(shape_type=shapefile.POINT, build=build)
    assert feature.properties == {
        "name": "well",
        "surveyed": "2026-01-02",
        "active": True,
        "area_ha": 3.25,
    }


def name_point(w):
    w.field("name", "C")
    w.point(1, 1)
    w.record("Zürich café")


@pytest.mark.parametrize("cpg", [b"windows-1252", b"1252"])
def test_cpg_file_decodes_non_utf8_attributes(cpg):
    _, [feature] = read(
        shape_type=shapefile.POINT,
        build=name_point,
        encoding="cp1252",
        extra_files={"parcels.cpg": cpg},
    )
    assert feature.properties["name"] == "Zürich café"


def test_without_cpg_attributes_are_read_as_utf8():
    _, [feature] = read(shape_type=shapefile.POINT, build=name_point)
    assert feature.properties["name"] == "Zürich café"


def test_unknown_cpg_value_falls_back_to_utf8_instead_of_failing():
    _, [feature] = read(
        shape_type=shapefile.POINT, build=name_point, extra_files={"parcels.cpg": b"klingon"}
    )
    assert feature.properties["name"] == "Zürich café"


def test_wrongly_encoded_attributes_do_not_crash():
    # latin-1 bytes read as UTF-8 (no .cpg): undecodable bytes become U+FFFD instead of an error.
    _, [feature] = read(shape_type=shapefile.POINT, build=name_point, encoding="cp1252")
    assert feature.properties["name"].startswith("Z")


# pyshp warns about the damaged header before failing; that warning is expected here.
@pytest.mark.filterwarnings("ignore:Declared file size")
@pytest.mark.parametrize(
    "damage",
    [
        lambda parts: parts | {"parcels.shp": parts["parcels.shp"][:150]},
        lambda parts: parts | {"parcels.shp": b"\x00" * 100 + b"garbage" * 20},
        lambda parts: parts | {"parcels.dbf": b"nonsense" * 10},
        lambda parts: parts | {"parcels.shx": b"zz" * 60},
    ],
    ids=["truncated-shp", "garbage-shp", "garbage-dbf", "garbage-shx"],
)
def test_corrupt_shapefile_is_rejected(damage):
    zip_bytes = make_zip(damage(shapefile_parts()))
    with pytest.raises(AppError) as excinfo:
        read_shapefile(extract_shapefile_parts(zip_bytes))
    assert excinfo.value.code == "CORRUPT_SHAPEFILE"


def test_unparseable_prj_is_rejected():
    with pytest.raises(AppError) as excinfo:
        read(prj="not a projection")
    assert excinfo.value.code == "INVALID_CRS"
