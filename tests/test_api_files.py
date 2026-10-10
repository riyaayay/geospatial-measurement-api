"""End-to-end tests of the three endpoints against the PostGIS test database."""

import hashlib
import uuid

import pytest
import shapely
from pyproj import Geod
from shapely.geometry import LineString, Polygon
from sqlalchemy import text

from tests.builders import (
    KML_SQUARE_1KM,
    SQUARE_1KM,
    kml_document,
    kml_polygon,
    make_zip,
    placemark,
    shapefile_parts,
)

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

GEOD = Geod(ellps="WGS84")
LINE_1KM = "0,0 0.009,0"


def upload(client, filename: str, data: bytes):
    return client.post("/api/files/", files={"file": (filename, data, "application/octet-stream")})


def survey_kml() -> bytes:
    return kml_document(
        placemark(kml_polygon(KML_SQUARE_1KM), name="field")
        + placemark(f"<LineString><coordinates>{LINE_1KM}</coordinates></LineString>", name="road")
        + placemark("<Point><coordinates>0.001,0.001</coordinates></Point>", name="well")
    )


def geod_area(coords) -> float:
    return abs(GEOD.geometry_area_perimeter(shapely.orient_polygons(Polygon(coords)))[0])


# --- POST /api/files/ ---------------------------------------------------------------------------


def test_upload_shapefile_zip(client):
    response = upload(client, "parcels.zip", make_zip(shapefile_parts()))

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["filename"] == "parcels.zip"
    assert body["file_type"] == "shapefile"
    assert body["feature_count"] == 1
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["error"] is None
    assert body["created_at"]


def test_upload_kml(client):
    response = upload(client, "survey.kml", survey_kml())

    assert response.status_code == 201
    body = response.json()
    assert (body["file_type"], body["feature_count"], body["crs"], body["status"]) == (
        "kml",
        3,
        "EPSG:4326",
        "COMPLETED",
    )


def test_upload_kmz(client):
    kmz = make_zip({"doc.kml": survey_kml(), "files/icon.png": b"\x89PNG"})
    response = upload(client, "survey.kmz", kmz)

    assert response.status_code == 201
    body = response.json()
    assert (body["file_type"], body["feature_count"], body["crs"], body["status"]) == (
        "kmz",
        3,
        "EPSG:4326",
        "COMPLETED",
    )
    field = client.get(f"/api/files/{body['id']}/measurements/").json()["items"][0]
    assert field["measurement"]["area_m2"] == pytest.approx(geod_area(SQUARE_1KM), rel=0.0005)
    assert body["size_bytes"] == len(kmz)  # the uploaded KMZ, not the KML inside it


def test_file_size_and_sha256_are_stored(client):
    data = survey_kml()
    file_id = upload(client, "survey.kml", data).json()["id"]

    info = client.get(f"/api/files/{file_id}/").json()

    assert info["size_bytes"] == len(data)
    assert info["sha256"] == hashlib.sha256(data).hexdigest()


def test_uploaded_filename_is_stripped_of_paths(client):
    response = upload(client, "../../etc/survey.kml", survey_kml())
    assert response.json()["filename"] == "survey.kml"


def test_content_error_stores_a_failed_file_and_returns_its_id(client):
    response = upload(client, "parcels.zip", make_zip(shapefile_parts(prj=None)))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_CRS"
    file_id = error["file_id"]

    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "MISSING_CRS"
    assert info["feature_count"] is None
    assert info["size_bytes"] > 0  # known even when the content couldn't be processed


def test_corrupt_zip_also_gets_a_failed_row(client):
    # PLAN amendment A6: the row is created before zip sniffing.
    response = upload(client, "parcels.zip", b"definitely not a zip")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CORRUPT_ZIP"
    assert "file_id" in response.json()["error"]


def test_kml_without_placemarks_is_rejected_as_no_features(client):
    response = upload(client, "empty.kml", kml_document(""))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_FEATURES"


@pytest.mark.parametrize(
    ("filename", "data", "status", "code"),
    [
        ("notes.txt", b"hello", 415, "UNSUPPORTED_FILE_TYPE"),
        ("survey.kml", b"", 400, "EMPTY_FILE"),
    ],
)
def test_request_errors_store_nothing(client, db, filename, data, status, code):
    response = upload(client, filename, data)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "file_id" not in response.json()["error"]
    assert db.execute(text("SELECT count(*) FROM files")).scalar() == 0


def test_missing_file_field_is_a_validation_error(client):
    response = client.post("/api/files/", data={"something": "else"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_feature_does_not_fail_the_file(client):
    def build(w):
        w.field("name", "C")
        w.poly([[(0, 0), (0.01, 0.01), (0.01, 0), (0, 0.01), (0, 0)]])  # bowtie
        w.record("bad")
        w.poly([SQUARE_1KM])
        w.record("good")

    response = upload(client, "mixed.zip", make_zip(shapefile_parts(build=build)))
    assert response.json()["status"] == "COMPLETED"

    body = client.get(f"/api/files/{response.json()['id']}/measurements/").json()
    assert [i["measurement"]["status"] for i in body["items"]] == ["INVALID", "MEASURED"]
    summary = body["summary"]
    assert (summary["measured"], summary["not_applicable"], summary["failed"]) == (1, 0, 1)


# --- GET /api/files/{id}/ -----------------------------------------------------------------------


def test_file_info_matches_the_assignment_example_shape(client):
    created = upload(client, "survey.kml", survey_kml()).json()

    response = client.get(f"/api/files/{created['id']}/")

    assert response.status_code == 200
    body = response.json()
    assert body == created
    for key in ("id", "filename", "feature_count", "crs", "status"):
        assert key in body


def test_paths_work_without_the_trailing_slash(client):
    # A redirect would be invisible to `curl -F`, which doesn't follow redirects by default.
    created = client.post(
        "/api/files", files={"file": ("survey.kml", survey_kml())}, follow_redirects=False
    )
    assert created.status_code == 201
    file_id = created.json()["id"]

    for path in (f"/api/files/{file_id}", f"/api/files/{file_id}/measurements"):
        assert client.get(path, follow_redirects=False).status_code == 200


def test_slashless_paths_are_not_duplicated_in_the_docs(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert sorted(p for p in paths if p.startswith("/api/files")) == [
        "/api/files/",
        "/api/files/{file_id}/",
        "/api/files/{file_id}/measurements/",
    ]


@pytest.mark.parametrize("file_id", [str(uuid.uuid4()), "abc123", "1; DROP TABLE files"])
def test_unknown_or_malformed_id_is_404(client, file_id):
    response = client.get(f"/api/files/{file_id}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


# --- GET /api/files/{id}/measurements/ ----------------------------------------------------------


def test_measurements_for_a_kml(client):
    file_id = upload(client, "survey.kml", survey_kml()).json()["id"]

    response = client.get(f"/api/files/{file_id}/measurements/")

    assert response.status_code == 200
    body = response.json()
    assert (body["file_id"], body["crs"], body["geometry_crs"]) == (
        file_id,
        "EPSG:4326",
        "EPSG:4326",
    )
    assert (body["total"], body["limit"], body["offset"]) == (3, 100, 0)

    field, road, well = body["items"]
    assert [i["feature_index"] for i in body["items"]] == [0, 1, 2]

    assert field["geometry_type"] == "Polygon"
    assert field["crs"] == "EPSG:4326"
    assert field["properties"] == {"name": "field"}
    assert field["measurement"]["status"] == "MEASURED"
    assert field["measurement"]["area_m2"] == pytest.approx(geod_area(SQUARE_1KM), rel=0.0005)
    assert field["measurement"]["length_m"] is None
    assert field["measurement"]["projection"].startswith("+proj=laea")

    expected_length = GEOD.geometry_length(LineString([(0, 0), (0.009, 0)]))
    assert road["measurement"]["length_m"] == pytest.approx(expected_length, rel=0.0005)

    assert well["measurement"]["status"] == "NOT_APPLICABLE"
    assert well["measurement"]["area_m2"] is None

    summary = body["summary"]
    assert summary["total_area_m2"] == pytest.approx(field["measurement"]["area_m2"])
    assert summary["total_length_m"] == pytest.approx(road["measurement"]["length_m"])
    assert (summary["measured"], summary["not_applicable"], summary["failed"]) == (2, 1, 0)


def test_geometry_is_included_by_default_and_can_be_turned_off(client):
    # The assignment lists geometry among the minimum per-feature fields, so it is on by default.
    file_id = upload(client, "survey.kml", survey_kml()).json()["id"]

    default = client.get(f"/api/files/{file_id}/measurements/").json()["items"][0]
    without = client.get(f"/api/files/{file_id}/measurements/?include_geometry=false").json()[
        "items"
    ][0]

    assert default["geometry"]["type"] == "Polygon"
    assert shapely.geometry.shape(default["geometry"]).equals(Polygon(SQUARE_1KM))
    assert without["geometry"] is None


def test_kml_linear_ring_keeps_its_type_and_is_measured_as_a_length(client):
    ring = f"<LinearRing><coordinates>{KML_SQUARE_1KM}</coordinates></LinearRing>"
    file_id = upload(client, "ring.kml", kml_document(placemark(ring))).json()["id"]

    [item] = client.get(f"/api/files/{file_id}/measurements/").json()["items"]

    assert item["geometry_type"] == "LinearRing"
    assert item["geometry"]["type"] == "LineString"  # GeoJSON has no LinearRing type
    assert item["measurement"]["status"] == "MEASURED"
    expected = GEOD.geometry_length(LineString(SQUARE_1KM))
    assert item["measurement"]["length_m"] == pytest.approx(expected, rel=0.0005)


def test_measurements_are_paginated_in_file_order(client):
    points = "".join(
        placemark(f"<Point><coordinates>{i},0</coordinates></Point>", name=f"p{i}")
        for i in range(5)
    )
    file_id = upload(client, "points.kml", kml_document(points)).json()["id"]

    body = client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=2").json()

    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 2)
    assert [i["feature_index"] for i in body["items"]] == [2, 3]
    assert [i["properties"]["name"] for i in body["items"]] == ["p2", "p3"]


@pytest.mark.parametrize("query", ["limit=0", "limit=5000", "offset=-1", "limit=abc"])
def test_bad_paging_parameters_are_400(client, query):
    file_id = upload(client, "survey.kml", survey_kml()).json()["id"]
    response = client.get(f"/api/files/{file_id}/measurements/?{query}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_measurements_of_a_failed_file_are_409(client):
    file_id = upload(client, "parcels.zip", make_zip(shapefile_parts(prj=None))).json()["error"][
        "file_id"
    ]
    response = client.get(f"/api/files/{file_id}/measurements/")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "FILE_NOT_READY"
    assert "FAILED" in error["message"]


def test_measurements_of_unknown_file_are_404(client):
    response = client.get(f"/api/files/{uuid.uuid4()}/measurements/")
    assert response.status_code == 404


# --- independent cross-check with PostGIS -------------------------------------------------------


def test_postgis_geography_agrees_with_our_measurements(client, db):
    """PostGIS measures on the ellipsoid itself: a second, independent implementation."""
    big_field = kml_polygon("10,60 11.8,60 11.8,60.9 10,60.9 10,60")  # ~100 km box at 60N
    kml = kml_document(
        placemark(big_field)
        + placemark(kml_polygon(KML_SQUARE_1KM))
        + placemark(f"<LineString><coordinates>{LINE_1KM}</coordinates></LineString>")
    )
    upload(client, "check.kml", kml)

    rows = db.execute(
        text(
            "SELECT area_m2, length_m, ST_Area(geom::geography), ST_Length(geom::geography) "
            "FROM features WHERE measure_status = 'MEASURED' ORDER BY feature_index"
        )
    ).all()
    assert len(rows) == 3
    for area_m2, length_m, pg_area, pg_length in rows:
        if area_m2 is not None:
            assert area_m2 == pytest.approx(pg_area, rel=0.0005)
        else:
            assert length_m == pytest.approx(pg_length, rel=0.0005)


def test_shapefile_attributes_and_index_are_stored(client, db):
    upload(client, "parcels.zip", make_zip(shapefile_parts()))
    row = db.execute(
        text("SELECT feature_index, geometry_type, properties, ST_SRID(geom) FROM features")
    ).one()
    assert row == (0, "Polygon", {"name": "plot A"}, 4326)


# --- consistent errors everywhere ---------------------------------------------------------------


def test_unknown_route_uses_the_standard_error_shape(client):
    response = client.get("/api/nope/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
