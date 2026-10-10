import pytest
from shapely.geometry import Polygon

from app.errors import AppError
from app.processing.kml_reader import read_kml
from tests.builders import KML_SQUARE_1KM, SQUARE_1KM, kml_document, kml_polygon, placemark


def read_one(geometry: str, **kwargs):
    [feature] = read_kml(kml_document(placemark(geometry, **kwargs)))
    return feature


# --- geometries ---------------------------------------------------------------------------------


def test_reads_a_polygon_placemark():
    feature = read_one(kml_polygon(KML_SQUARE_1KM), name="field")

    assert feature.index == 0
    assert feature.geometry_type == "Polygon"
    assert feature.geometry.equals(Polygon(SQUARE_1KM))
    assert feature.properties == {"name": "field"}
    assert feature.problem is None


def test_polygon_inner_ring_becomes_a_hole():
    feature = read_one(kml_polygon("0,0 10,0 10,10 0,10 0,0", "2,2 4,2 4,4 2,4 2,2"))
    assert len(feature.geometry.interiors) == 1
    assert feature.geometry.area == pytest.approx(96)


OUTER_10 = "0,0 10,0 10,10 0,10 0,0"
EMPTY_HOLE = (
    "<innerBoundaryIs><LinearRing><coordinates> </coordinates></LinearRing></innerBoundaryIs>"
)
HOLE_WITHOUT_COORDINATES = "<innerBoundaryIs><LinearRing></LinearRing></innerBoundaryIs>"


@pytest.mark.parametrize(
    "empty_hole", [EMPTY_HOLE, HOLE_WITHOUT_COORDINATES], ids=["blank", "no-coordinates-tag"]
)
def test_empty_inner_ring_is_skipped_with_a_note(empty_hole):
    # The outer ring is fine, so the polygon is still measured; the note says what was dropped.
    polygon = kml_polygon(OUTER_10, "2,2 4,2 4,4 2,4 2,2").replace("</Polygon>", "")
    feature = read_one(f"{polygon}{empty_hole}</Polygon>")

    assert feature.problem is None
    assert feature.geometry_type == "Polygon"
    assert len(feature.geometry.interiors) == 1
    assert feature.geometry.area == pytest.approx(96)
    assert "inner boundary" in feature.note


def test_reads_linestring_and_point():
    line = read_one("<LineString><coordinates>0,0 0.009,0</coordinates></LineString>")
    point = read_one("<Point><coordinates>1.5,2.5</coordinates></Point>")

    assert line.geometry_type == "LineString"
    assert list(line.geometry.coords) == [(0, 0), (0.009, 0)]
    assert point.geometry_type == "Point"
    assert (point.geometry.x, point.geometry.y) == (1.5, 2.5)


def test_coordinates_may_be_spread_over_lines_and_tabs():
    feature = read_one("<LineString><coordinates>\n\t0,0\n\t1,1\n</coordinates></LineString>")
    assert list(feature.geometry.coords) == [(0, 0), (1, 1)]


def test_bare_linear_ring_is_treated_as_a_closed_line():
    feature = read_one(f"<LinearRing><coordinates>{KML_SQUARE_1KM}</coordinates></LinearRing>")
    assert feature.geometry_type == "LinearRing"  # what the file says, not what we built
    assert feature.geometry.geom_type == "LineString"
    assert feature.geometry.is_closed
    assert "LinearRing" in feature.note


@pytest.mark.parametrize(
    ("parts", "expected_type"),
    [
        (kml_polygon(KML_SQUARE_1KM) + kml_polygon("5,5 6,5 6,6 5,6 5,5"), "MultiPolygon"),
        ("<Point><coordinates>1,1</coordinates></Point>" * 2, "MultiPoint"),
        ("<LineString><coordinates>0,0 1,1</coordinates></LineString>" * 2, "MultiLineString"),
        (
            "<Point><coordinates>1,1</coordinates></Point>"
            "<LineString><coordinates>0,0 1,1</coordinates></LineString>",
            "GeometryCollection",
        ),
        (  # nested MultiGeometry is flattened
            kml_polygon(KML_SQUARE_1KM)
            + f"<MultiGeometry>{kml_polygon('5,5 6,5 6,6 5,6 5,5')}</MultiGeometry>",
            "MultiPolygon",
        ),
    ],
    ids=["polygons", "points", "lines", "mixed", "nested"],
)
def test_multigeometry(parts, expected_type):
    feature = read_one(f"<MultiGeometry>{parts}</MultiGeometry>")
    assert feature.geometry_type == expected_type


def test_altitude_is_dropped_with_a_note():
    feature = read_one("<LineString><coordinates>0,0,120 1,1,130</coordinates></LineString>")
    assert not feature.geometry.has_z
    assert "ltitude" in feature.note


def test_zero_altitude_from_google_earth_is_not_worth_a_note():
    feature = read_one("<LineString><coordinates>0,0,0 1,1,0</coordinates></LineString>")
    assert feature.note is None


# --- document structure and properties ----------------------------------------------------------


def test_placemarks_in_nested_folders_are_found_in_document_order():
    point = "<Point><coordinates>1,1</coordinates></Point>"
    inner = f"<Folder>{placemark(point, name='c')}</Folder>"
    body = (
        placemark(point, name="a")
        + f"<Folder>{placemark(point, name='b')}{inner}</Folder>"
        + placemark(point, name="d")
    )
    features = read_kml(kml_document(body))
    assert [(f.index, f.properties["name"]) for f in features] == [
        (0, "a"),
        (1, "b"),
        (2, "c"),
        (3, "d"),
    ]


def test_properties_come_from_name_description_extended_data_and_id():
    kml = kml_document(
        '<Placemark id="pm-7"><name>field</name><description>wheat, 2026</description>'
        '<ExtendedData><Data name="owner"><value>Asha</value></Data>'
        '<SchemaData schemaUrl="#s"><SimpleData name="crop">wheat</SimpleData></SchemaData>'
        "</ExtendedData><Point><coordinates>1,1</coordinates></Point></Placemark>"
    )
    [feature] = read_kml(kml)
    assert feature.properties == {
        "kml_id": "pm-7",
        "name": "field",
        "description": "wheat, 2026",
        "owner": "Asha",
        "crop": "wheat",
    }


@pytest.mark.parametrize("namespace", ["http://earth.google.com/kml/2.1", ""])
def test_older_or_missing_namespace_is_accepted(namespace):
    point = "<Point><coordinates>1,1</coordinates></Point>"
    [feature] = read_kml(kml_document(placemark(point), namespace=namespace))
    assert feature.geometry_type == "Point"


def test_empty_document_has_no_features():
    assert read_kml(kml_document("")) == []


# --- per-feature problems (the file is still accepted) ------------------------------------------


def test_unsupported_geometry_is_reported_not_crashed():
    feature = read_one(
        '<gx:Track xmlns:gx="http://www.google.com/kml/ext/2.2"><when>2026-01-01</when></gx:Track>'
    )
    assert feature.geometry is None
    assert feature.geometry_type == "Track"
    assert feature.problem == "UNSUPPORTED"


def test_placemark_without_geometry_is_empty():
    [feature] = read_kml(kml_document("<Placemark><name>just a label</name></Placemark>"))
    assert feature.problem == "EMPTY"
    assert feature.properties == {"name": "just a label"}


def test_empty_coordinates_are_empty():
    feature = read_one("<LineString><coordinates>  </coordinates></LineString>")
    assert feature.problem == "EMPTY"


@pytest.mark.parametrize(
    ("geometry", "hint"),
    [
        ("<Point><coordinates>abc,def</coordinates></Point>", "abc,def"),
        ("<Point><coordinates>10</coordinates></Point>", "10"),
        ("<Point><coordinates>nan,1</coordinates></Point>", "nan,1"),
        ("<Point><coordinates>10,95</coordinates></Point>", "range"),
        ("<Point><coordinates>500000,4000000</coordinates></Point>", "range"),
        ("<LineString><coordinates>0,0</coordinates></LineString>", "LineString"),
    ],
    ids=["text", "one-number", "nan", "lat-95", "projected-metres", "one-point-line"],
)
def test_bad_coordinates_make_the_feature_invalid(geometry, hint):
    feature = read_one(geometry)
    assert feature.geometry is None
    assert feature.problem == "INVALID"
    assert hint in feature.note


@pytest.mark.parametrize(
    "coordinates", ["0,0, 0.01,0", "0, 0 0.01, 0"], ids=["between-points", "inside-a-point"]
)
def test_space_after_a_comma_is_rejected_with_a_helpful_note(coordinates):
    # The KML spec forbids spaces inside a tuple, and the two hand-edited styles contradict
    # each other for 3D data ("0,0, 5,1"), so we explain instead of guessing. (DECISIONS D32)
    feature = read_one(f"<LineString><coordinates>{coordinates}</coordinates></LineString>")
    assert feature.problem == "INVALID"
    assert "no spaces after commas" in feature.note


# --- whole-file errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blob",
    [
        b"this is not xml",
        b"<kml><Document>",  # truncated
        b'<?xml version="1.0"?><gpx><trk/></gpx>',  # valid XML, wrong format
    ],
    ids=["not-xml", "truncated", "wrong-root"],
)
def test_non_kml_is_rejected(blob):
    with pytest.raises(AppError) as excinfo:
        read_kml(blob)
    assert excinfo.value.code == "INVALID_KML"
    assert excinfo.value.status_code == 422


def test_entity_expansion_attack_is_rejected():
    billion_laughs = (
        b'<?xml version="1.0"?><!DOCTYPE k [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<kml>&b;</kml>"
    )
    with pytest.raises(AppError) as excinfo:
        read_kml(billion_laughs)
    assert excinfo.value.code == "INVALID_KML"
    assert "security" in excinfo.value.message
