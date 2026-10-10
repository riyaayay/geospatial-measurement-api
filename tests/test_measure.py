"""Measurement tests. The reference ("oracle") is always pyproj.Geod on the WGS84 ellipsoid.

Tolerances (PLAN.md 4.5, DECISIONS D19): 0.05% for features up to ~100 km, 0.1% up to ~500 km.
"""

import numpy as np
import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.processing.crs import WGS84, local_projection, to_wgs84
from app.processing.measure import measure, measure_feature
from app.processing.raw_feature import RawFeature

GEOD = Geod(ellps="WGS84")
TOL_100KM = 0.0005  # 0.05 %
TOL_500KM = 0.001  # 0.1 %


def geod_area(geom) -> float:
    # Geod sums *signed* ring areas, so a hole drawn in the same direction as its outer ring
    # would be added instead of subtracted. orient_polygons fixes ring directions first.
    # (shapely's own .area, which the app uses, doesn't depend on direction.)
    return abs(GEOD.geometry_area_perimeter(shapely.orient_polygons(geom))[0])


def geod_length(geom) -> float:
    return GEOD.geometry_length(geom)


def box(lon1, lat1, lon2, lat2) -> Polygon:
    return Polygon([(lon1, lat1), (lon2, lat1), (lon2, lat2), (lon1, lat2)])


def reproject(geom, source, target):
    t = Transformer.from_crs(source, target, always_xy=True)
    return shapely.transform(geom, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


# --- the headline numbers -----------------------------------------------------------------------

SQUARE_1KM = box(0, 0, 0.009, 0.009)


def test_1km_square_at_the_equator_matches_geod():
    m = measure(SQUARE_1KM)

    assert m.status == "MEASURED"
    assert m.area_m2 == pytest.approx(geod_area(SQUARE_1KM), rel=TOL_100KM)
    assert m.length_m is None


def test_area_is_never_computed_in_degrees():
    m = measure(SQUARE_1KM)
    assert SQUARE_1KM.area == pytest.approx(8.1e-05)  # the wrong answer, in "square degrees"
    assert m.area_m2 > 900_000


def test_1km_line_matches_geod():
    line = LineString([(0, 0), (0.009, 0)])
    m = measure(line)

    assert m.status == "MEASURED"
    assert m.length_m == pytest.approx(geod_length(line), rel=TOL_100KM)
    assert m.area_m2 is None


# The comparison cases from PLAN.md 4.2: the table that justified choosing LAEA over UTM.
CASES_UP_TO_100KM = {
    "1km square on a UTM zone edge (0E)": box(0, 0, 0.009, 0.009),
    "1km square on a UTM central meridian (3E)": box(3, 0, 3.009, 0.009),
    "50km box at 50N straddling the 6E zone boundary": box(5.65, 50, 6.35, 50.45),
    "100km box at 60N": box(10, 60, 11.8, 60.9),
    "1km square at 87N (no UTM zone exists)": box(0, 87, 0.17, 87.009),
    "1km square in the southern hemisphere": box(-47.9, -15.8, -47.89, -15.79),
}


@pytest.mark.parametrize("poly", CASES_UP_TO_100KM.values(), ids=CASES_UP_TO_100KM.keys())
def test_area_and_perimeter_within_005_percent_up_to_100km(poly):
    area = measure(poly).area_m2
    perimeter = measure(LineString(poly.exterior.coords)).length_m

    assert area == pytest.approx(geod_area(poly), rel=TOL_100KM)
    assert perimeter == pytest.approx(geod_length(LineString(poly.exterior.coords)), rel=TOL_100KM)


def test_500km_box_within_01_percent():
    poly = box(69, 20, 73.8, 24.5)  # roughly the size of Gujarat
    ring = LineString(poly.exterior.coords)

    assert measure(poly).area_m2 == pytest.approx(geod_area(poly), rel=TOL_500KM)
    assert measure(ring).length_m == pytest.approx(geod_length(ring), rel=TOL_500KM)


# --- continent-scale features (G4): pins the numbers documented in the README -------------------


def test_3300km_line_is_short_by_under_035_percent():
    # LAEA keeps areas exact but stretches distances away from its centre, so a straight line this
    # long comes out about 0.29% short (README "Accuracy limits"; DECISIONS D38).
    line = LineString([(31, lat) for lat in np.arange(0, 30.01, 0.1)])  # 0 to 30N along 31E
    ours, reference = measure(line).length_m, geod_length(line)

    assert reference == pytest.approx(3_320_000, rel=0.01)
    assert ours < reference
    assert ours == pytest.approx(reference, rel=0.0035)


def test_30_by_30_degree_area_is_still_exact():
    # The same distance distortion does not affect area: LAEA is equal-area at any size.
    poly = shapely.segmentize(box(0, 0, 30, 30), 0.1)  # ~10.6 million km2, short edges
    assert measure(poly).area_m2 == pytest.approx(geod_area(poly), rel=0.0001)


# --- geometry types -----------------------------------------------------------------------------


def test_polygon_hole_is_subtracted():
    outer = box(0, 0, 0.02, 0.02)
    hole = box(0.005, 0.005, 0.01, 0.01)
    with_hole = Polygon(outer.exterior.coords, [hole.exterior.coords])

    m = measure(with_hole)
    assert m.area_m2 == pytest.approx(geod_area(with_hole), rel=TOL_100KM)
    assert m.area_m2 == pytest.approx(measure(outer).area_m2 - measure(hole).area_m2, rel=TOL_100KM)


def test_multipolygon_area_is_the_sum_of_its_parts():
    a, b = box(0, 0, 0.009, 0.009), box(1, 1, 1.009, 1.009)
    m = measure(MultiPolygon([a, b]))
    assert m.area_m2 == pytest.approx(geod_area(a) + geod_area(b), rel=TOL_100KM)


def test_multilinestring_length_is_the_sum_of_its_parts():
    a, b = LineString([(0, 0), (0.009, 0)]), LineString([(1, 1), (1, 1.009)])
    m = measure(MultiLineString([a, b]))
    assert m.length_m == pytest.approx(geod_length(a) + geod_length(b), rel=TOL_100KM)


def test_linear_ring_is_measured_as_a_length():
    ring = LinearRing(SQUARE_1KM.exterior.coords)
    m = measure(ring)
    assert m.status == "MEASURED"
    assert m.length_m == pytest.approx(geod_length(LineString(ring.coords)), rel=TOL_100KM)


@pytest.mark.parametrize("geom", [Point(1, 1), MultiPoint([(1, 1), (2, 2)])])
def test_points_have_no_measurement(geom):
    m = measure(geom)
    assert m.status == "NOT_APPLICABLE"
    assert m.area_m2 is None and m.length_m is None and m.projection is None


def test_geometry_collection_is_unsupported():
    m = measure(GeometryCollection([Point(0, 0), SQUARE_1KM]))
    assert m.status == "UNSUPPORTED"
    assert "GeometryCollection" in m.note


@pytest.mark.parametrize("wkt", ["POLYGON EMPTY", "LINESTRING EMPTY", "POINT EMPTY"])
def test_empty_geometry(wkt):
    assert measure(shapely.from_wkt(wkt)).status == "EMPTY"


def test_self_intersecting_polygon_is_invalid_not_auto_fixed():
    bowtie = Polygon([(0, 0), (0.01, 0.01), (0.01, 0), (0, 0.01)])
    m = measure(bowtie)
    assert m.status == "INVALID"
    assert "Self-intersection" in m.note
    assert m.area_m2 is None


def test_lonlat_out_of_range_is_invalid_and_hints_at_the_prj():
    # Typical mistake: projected metres in a file whose .prj says EPSG:4326.
    m = measure(box(500000, 4000000, 501000, 4001000))
    assert m.status == "INVALID"
    assert ".prj" in m.note


# --- antimeridian (G1) ---------------------------------------------------------------------------
# Geod accepts longitudes above 180, so the "shifted" shapes below are valid references.


def test_polygon_crossing_the_antimeridian_is_measured_the_short_way():
    # Drawn from 179.5E to 179.5W across +-180; in plain lon/lat it looks 359 degrees wide.
    m = measure(box(-179.5, 10, 179.5, 10.5))
    assert m.status == "MEASURED"
    assert m.area_m2 == pytest.approx(geod_area(box(179.5, 10, 180.5, 10.5)), rel=TOL_100KM)
    assert "antimeridian" in m.note


def test_multipolygon_with_parts_on_both_sides_is_measured():
    # Like Fiji or Russia in Natural Earth: each part is within +-180, but they sit on both sides.
    east, west = box(178, -18, 180, -17), box(-180, -17, -179, -16)
    m = measure(MultiPolygon([east, west]))
    assert m.status == "MEASURED"
    assert m.area_m2 == pytest.approx(geod_area(east) + geod_area(west), rel=TOL_500KM)


def test_line_crossing_the_antimeridian_is_measured_the_short_way():
    m = measure(LineString([(179, 0), (-179, 0)]))
    assert m.status == "MEASURED"
    # About 222 km, not the 39,800 km of going the long way round.
    assert m.length_m == pytest.approx(geod_length(LineString([(179, 0), (181, 0)])), rel=TOL_500KM)


def test_polygon_around_a_pole_is_still_unsupported():
    # Like Antarctica: it covers every longitude, so no shift makes it narrower than 180 degrees.
    m = measure(Polygon([(-180, -90), (180, -90), (180, -60), (-180, -60)]))
    assert m.status == "UNSUPPORTED"
    assert "pole" in m.note


def test_stored_geometry_is_not_shifted():
    crossing = box(-179.5, 10, 179.5, 10.5)
    geometry, m = measure_feature(RawFeature(0, crossing, {}, "Polygon"), WGS84)
    assert m.status == "MEASURED"
    assert geometry.equals(crossing)  # GeoJSON output keeps longitudes within +-180


def test_projection_is_lambert_azimuthal_equal_area_centred_on_the_feature():
    m = measure(box(10, 60, 11.8, 60.9))
    assert "+proj=laea" in m.projection
    assert "+lat_0=60.45" in m.projection
    assert "+lon_0=10.9" in m.projection


def test_local_projection_string_matches_what_measure_reports():
    assert measure(SQUARE_1KM).projection == local_projection(SQUARE_1KM.centroid)


# --- source CRS handling ------------------------------------------------------------------------


def test_to_wgs84_handles_esri_flavoured_4326():
    esri_4326 = CRS.from_wkt(CRS(4326).to_wkt("WKT1_ESRI"))
    assert to_wgs84(SQUARE_1KM, esri_4326).equals_exact(SQUARE_1KM, tolerance=1e-12)


def test_web_mercator_source_is_measured_correctly():
    # Web Mercator inflates areas by ~1/cos^2(lat): about 4x at 60N. We must not use it directly.
    square_lonlat = box(10, 60, 10.02, 60.01)
    square_3857 = reproject(square_lonlat, "EPSG:4326", "EPSG:3857")
    assert square_3857.area / geod_area(square_lonlat) > 3.9  # the trap

    geometry, m = measure_feature(RawFeature(0, square_3857, {}, "Polygon"), CRS(3857))
    assert m.area_m2 == pytest.approx(geod_area(square_lonlat), rel=TOL_100KM)
    assert geometry.equals_exact(square_lonlat, tolerance=1e-9)


def test_us_survey_feet_source_is_converted_to_metres():
    square_lonlat = box(-122.42, 37.77, -122.41, 37.78)  # San Francisco
    square_ft = reproject(square_lonlat, "EPSG:4326", "EPSG:2227")

    _, m = measure_feature(RawFeature(0, square_ft, {}, "Polygon"), CRS(2227))
    assert m.area_m2 == pytest.approx(geod_area(square_lonlat), rel=TOL_100KM)


def test_measure_feature_passes_reader_problems_through():
    raw = RawFeature(3, None, {"name": "x"}, "Track", "UNSUPPORTED", "KML <Track> unsupported.")
    geometry, m = measure_feature(raw, WGS84)
    assert geometry is None
    assert (m.status, m.note) == ("UNSUPPORTED", "KML <Track> unsupported.")


def test_measure_feature_keeps_the_readers_note():
    raw = RawFeature(
        0, LineString([(0, 0), (0.009, 0)]), {}, "LineString", note="Z values ignored."
    )
    _, m = measure_feature(raw, WGS84)
    assert m.status == "MEASURED"
    assert "Z values ignored." in m.note


def test_coordinates_that_cannot_be_converted_are_invalid():
    huge = box(1e30, 1e30, 2e30, 2e30)  # pyproj returns inf for these
    geometry, m = measure_feature(RawFeature(0, huge, {}, "Polygon"), CRS(32643))
    assert geometry is None
    assert m.status == "INVALID"
