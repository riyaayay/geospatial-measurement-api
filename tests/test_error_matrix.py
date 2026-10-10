"""PLAN.md section 7, row by row, through the real API. Bad input must never give a 500."""

import dataclasses
import math
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import config
from app.db import get_db, make_engine
from app.main import app
from tests.builders import (
    KML_SQUARE_1KM,
    kml_document,
    kml_polygon,
    make_zip,
    mark_first_member_encrypted,
    placemark,
    shapefile_parts,
)


def upload(client, filename: str, data: bytes):
    return client.post("/api/files/", files={"file": (filename, data, "application/octet-stream")})


def without(parts: dict, name: str) -> dict:
    return {k: v for k, v in parts.items() if k != name}


GOOD = shapefile_parts()

# (id, filename, data, expected status, expected code, is a FAILED row stored?)
MATRIX = [
    ("wrong-extension", "notes.txt", b"hello", 415, "UNSUPPORTED_FILE_TYPE", False),
    ("kmz-not-a-zip", "map.kmz", b"PK...", 422, "CORRUPT_ZIP", True),
    ("kmz-without-kml", "map.kmz", make_zip({"readme.txt": b"x"}), 422, "MISSING_KML", True),
    (
        "kmz-zip-slip",
        "map.kmz",
        make_zip({"doc.kml": b"<kml/>", "../evil.txt": b"x"}),
        422,
        "UNSAFE_ZIP",
        True,
    ),
    ("empty-file", "survey.kml", b"", 400, "EMPTY_FILE", False),
    ("not-a-zip", "parcels.zip", b"plain text", 422, "CORRUPT_ZIP", True),
    ("truncated-zip", "parcels.zip", make_zip(GOOD)[:100], 422, "CORRUPT_ZIP", True),
    ("zip-slip", "parcels.zip", make_zip(GOOD | {"../../evil.txt": b"x"}), 422, "UNSAFE_ZIP", True),
    (
        "zip-bomb",
        "parcels.zip",
        make_zip(GOOD | {"bomb.bin": b"\0" * 5_000_000}),
        422,
        "UNSAFE_ZIP",
        True,
    ),
    (
        "encrypted",
        "parcels.zip",
        mark_first_member_encrypted(make_zip(GOOD)),
        422,
        "UNSAFE_ZIP",
        True,
    ),
    (
        "too-many-files",
        "parcels.zip",
        make_zip(GOOD | {f"extra/{i}.txt": b"x" for i in range(100)}),
        422,
        "UNSAFE_ZIP",
        True,
    ),
    ("no-shp", "parcels.zip", make_zip({"readme.txt": b"x"}), 422, "MISSING_SHAPEFILE_PART", True),
    (
        "no-shx",
        "parcels.zip",
        make_zip(without(GOOD, "parcels.shx")),
        422,
        "MISSING_SHAPEFILE_PART",
        True,
    ),
    (
        "no-dbf",
        "parcels.zip",
        make_zip(without(GOOD, "parcels.dbf")),
        422,
        "MISSING_SHAPEFILE_PART",
        True,
    ),
    (
        "two-shapefiles",
        "parcels.zip",
        make_zip(GOOD | shapefile_parts("roads")),
        422,
        "MULTIPLE_SHAPEFILES",
        True,
    ),
    ("no-prj", "parcels.zip", make_zip(shapefile_parts(prj=None)), 422, "MISSING_CRS", True),
    (
        "garbage-prj",
        "parcels.zip",
        make_zip(shapefile_parts(prj="not a crs")),
        422,
        "INVALID_CRS",
        True,
    ),
    (
        "corrupt-shp",
        "parcels.zip",
        make_zip(GOOD | {"parcels.shp": GOOD["parcels.shp"][:150]}),
        422,
        "CORRUPT_SHAPEFILE",
        True,
    ),
    ("not-xml", "survey.kml", b"hello", 422, "INVALID_KML", True),
    ("not-kml", "survey.kml", b"<gpx><trk/></gpx>", 422, "INVALID_KML", True),
    (
        "xml-entity-attack",
        "survey.kml",
        b'<!DOCTYPE k [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><kml>&b;</kml>',
        422,
        "INVALID_KML",
        True,
    ),
    ("no-features", "survey.kml", kml_document(""), 422, "NO_FEATURES", True),
]


@pytest.mark.db
@pytest.mark.usefixtures("db")
@pytest.mark.filterwarnings("ignore:Declared file size")
@pytest.mark.parametrize(
    ("filename", "data", "status", "code", "stores_row"),
    [row[1:] for row in MATRIX],
    ids=[row[0] for row in MATRIX],
)
def test_error_matrix(client, db, filename, data, status, code, stores_row):
    response = upload(client, filename, data)

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]

    if stores_row:
        info = client.get(f"/api/files/{error['file_id']}/").json()
        assert (info["status"], info["error"]["code"]) == ("FAILED", code)
    else:
        assert "file_id" not in error
        assert db.execute(text("SELECT count(*) FROM files")).scalar() == 0


# --- size limits through the API ----------------------------------------------------------------


@pytest.fixture
def small_upload_limit(monkeypatch):
    """Shrink the upload limit to 1,000 bytes for one test."""
    tiny = dataclasses.replace(config.settings, max_upload_bytes=1000)
    monkeypatch.setattr(config, "settings", tiny)


@pytest.mark.usefixtures("small_upload_limit")
def test_oversized_request_is_rejected_early_from_content_length(client):
    # The body isn't even multipart: if it reached the endpoint it would be a 400 (no `file`
    # field). Getting 413 proves the middleware answered from the header alone.
    response = client.post("/api/files/", content=b"x" * 100_000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.db
@pytest.mark.usefixtures("db", "small_upload_limit")
def test_file_over_limit_is_caught_by_counting_bytes(client, db):
    # 5,000 bytes passes the Content-Length allowance, so the real byte count must catch it.
    response = upload(client, "big.kml", b"x" * 5_000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert db.execute(text("SELECT count(*) FROM files")).scalar() == 0


# --- infrastructure failures --------------------------------------------------------------------


def test_database_down_gives_503_not_a_hang_or_500(client):
    # 127.0.0.1 (not "localhost") so only one address is tried: the test takes ~3 s, not ~6 s.
    dead = sessionmaker(bind=make_engine("postgresql+psycopg://geo:geo@127.0.0.1:1/geo"))

    def get_dead_db():
        with dead() as session:
            yield session

    app.dependency_overrides[get_db] = get_dead_db
    response = client.get("/api/files/2d5c9f7e-1111-4222-8333-944455556666/")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


@pytest.mark.db
@pytest.mark.usefixtures("db")
def test_unexpected_bug_gives_500_and_marks_the_file_failed(monkeypatch, db):
    def broken(*args, **kwargs):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr("app.processing.pipeline.measure_feature", broken)
    # raise_server_exceptions=False: see the 500 response the real client would get.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = upload(
            client, "survey.kml", kml_document(placemark(kml_polygon(KML_SQUARE_1KM)))
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Something went wrong on our side."}
    }
    assert "simulated bug" not in response.text  # internals are logged, never shown
    status, code = db.execute(text("SELECT status, error_code FROM files")).one()
    assert (status, code) == ("FAILED", "INTERNAL_ERROR")


# --- robustness: garbage in, clean error out ----------------------------------------------------


def garbage_inputs():
    rng = random.Random(42)  # fixed seed: the same "random" files every run
    valid_zip = make_zip(GOOD)
    valid_kml = kml_document(placemark(kml_polygon(KML_SQUARE_1KM)))
    cases = []
    for size in (1, 10, 100, 1_000, 10_000):
        blob = rng.randbytes(size)
        cases += [("random.zip", blob), ("random.kml", blob), ("random.kmz", blob)]
    cases += [("cut.zip", valid_zip[:n]) for n in range(10, len(valid_zip), 97)]
    cases += [("cut.kml", valid_kml[:n]) for n in range(5, len(valid_kml), 41)]
    valid_kmz = make_zip({"doc.kml": valid_kml})
    cases += [("cut.kmz", valid_kmz[:n]) for n in range(10, len(valid_kmz), 97)]
    cases += [
        ("flipped.zip", bytes(b ^ 0xFF if i % 50 == 0 else b for i, b in enumerate(valid_zip)))
    ]
    return cases


@pytest.mark.db
@pytest.mark.usefixtures("db")
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize(("filename", "data"), garbage_inputs())
def test_garbage_never_causes_a_server_error(filename, data):
    with TestClient(app, raise_server_exceptions=False) as client:
        response = upload(client, filename, data)
    assert response.status_code in (201, 400, 413, 415, 422), response.text
    assert "error" in response.json() or response.status_code == 201


@pytest.mark.db
@pytest.mark.usefixtures("db")
def test_large_polygon_with_10000_vertices_is_measured(client):
    angles = [2 * math.pi * i / 10_000 for i in range(10_000)]
    ring = " ".join(f"{0.05 * math.cos(a):.7f},{0.05 * math.sin(a):.7f}" for a in angles)
    first = ring.split(" ")[0]
    response = upload(client, "circle.kml", kml_document(placemark(kml_polygon(f"{ring} {first}"))))
    assert response.status_code == 201

    items = client.get(f"/api/files/{response.json()['id']}/measurements/").json()["items"]
    assert items[0]["measurement"]["status"] == "MEASURED"
    assert items[0]["measurement"]["area_m2"] > 0
