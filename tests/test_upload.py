import dataclasses
import io

import pytest

from app.config import settings
from app.errors import AppError
from app.processing.upload import (
    clean_filename,
    detect_file_type,
    extract_kml_from_kmz,
    extract_shapefile_parts,
    read_upload,
)
from tests.builders import (
    WGS84_PRJ,
    kml_document,
    make_zip,
    mark_first_member_encrypted,
    shapefile_parts,
)

DOC_KML = kml_document("")


def error_code(excinfo: pytest.ExceptionInfo[AppError]) -> str:
    return excinfo.value.code


# --- file name and type -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("parcels.zip", "shapefile"),
        ("survey.kml", "kml"),
        ("SURVEY.KML", "kml"),
        ("map.kmz", "kmz"),
        ("MAP.KMZ", "kmz"),
    ],
)
def test_detect_file_type_accepts_zip_and_kml(filename, expected):
    assert detect_file_type(filename) == expected


@pytest.mark.parametrize("filename", ["notes.txt", "data.geojson", "noextension", ""])
def test_detect_file_type_rejects_other_extensions_with_415(filename):
    with pytest.raises(AppError) as excinfo:
        detect_file_type(filename)
    assert error_code(excinfo) == "UNSUPPORTED_FILE_TYPE"
    assert excinfo.value.status_code == 415


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("survey.kml", "survey.kml"),
        ("../../etc/survey.kml", "survey.kml"),
        ("C:\\Users\\me\\survey.kml", "survey.kml"),
        (None, ""),
    ],
)
def test_clean_filename_keeps_only_the_base_name(raw, expected):
    assert clean_filename(raw) == expected


# --- size limits --------------------------------------------------------------------------------


def test_read_upload_returns_bytes_up_to_the_limit():
    assert read_upload(io.BytesIO(b"12345"), max_bytes=5) == b"12345"


def test_read_upload_rejects_files_over_the_limit_with_413():
    with pytest.raises(AppError) as excinfo:
        read_upload(io.BytesIO(b"123456"), max_bytes=5)
    assert error_code(excinfo) == "FILE_TOO_LARGE"
    assert excinfo.value.status_code == 413


def test_read_upload_rejects_empty_files_with_400():
    with pytest.raises(AppError) as excinfo:
        read_upload(io.BytesIO(b""), max_bytes=5)
    assert error_code(excinfo) == "EMPTY_FILE"
    assert excinfo.value.status_code == 400


# --- shapefile zip: happy paths -----------------------------------------------------------------


def test_extracts_all_parts_from_a_valid_zip():
    parts = extract_shapefile_parts(make_zip(shapefile_parts("parcels")))

    assert parts.name == "parcels"
    assert parts.shp.startswith(b"\x00\x00\x27\x0a")  # Shapefile magic number 9994
    assert parts.shx and parts.dbf
    assert parts.prj == WGS84_PRJ
    assert parts.cpg is None


def test_shapefile_inside_a_folder_is_found():
    members = {f"export/2026/{name}": data for name, data in shapefile_parts().items()}
    assert extract_shapefile_parts(make_zip(members)).name == "parcels"


def test_extensions_are_matched_case_insensitively():
    members = {name.upper(): data for name, data in shapefile_parts().items()}
    assert extract_shapefile_parts(make_zip(members)).name == "PARCELS"


def test_macos_resource_fork_files_are_ignored():
    members = shapefile_parts() | {"__MACOSX/._parcels.shp": b"junk", ".DS_Store": b"junk"}
    assert extract_shapefile_parts(make_zip(members)).name == "parcels"


def test_cpg_file_gives_the_attribute_encoding():
    members = shapefile_parts() | {"parcels.cpg": b"windows-1252\n"}
    assert extract_shapefile_parts(make_zip(members)).cpg == "windows-1252"


def test_small_highly_compressible_member_is_not_a_bomb():
    # Real .dbf files are often padded with spaces and compress >100:1; below 1 MB that's normal.
    members = shapefile_parts() | {"readme.txt": b" " * 500_000}
    assert extract_shapefile_parts(make_zip(members)).name == "parcels"


# --- shapefile zip: corrupt and unsafe ----------------------------------------------------------


# The safety checks are shared by Shapefile zips and KMZ files, so each runs against both.
@pytest.fixture(params=["shapefile", "kmz"])
def zip_kind(request):
    """(the extract function, the members of a valid zip of that kind)."""
    if request.param == "shapefile":
        return extract_shapefile_parts, shapefile_parts()
    return extract_kml_from_kmz, {"doc.kml": DOC_KML}


@pytest.mark.parametrize(
    "blob",
    [b"this is not a zip", b"PK\x03\x04" + b"junk" * 10, make_zip(shapefile_parts())[:60]],
    ids=["plain-text", "fake-magic", "truncated"],
)
def test_corrupt_zip_is_rejected(zip_kind, blob):
    extract, _ = zip_kind
    with pytest.raises(AppError) as excinfo:
        extract(blob)
    assert error_code(excinfo) == "CORRUPT_ZIP"
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize(
    "evil_name", ["../evil.txt", "a/../../evil.txt", "/etc/evil.txt", "C:/evil.txt", "..\\evil.txt"]
)
def test_zip_slip_paths_are_rejected(zip_kind, evil_name):
    extract, good = zip_kind
    with pytest.raises(AppError) as excinfo:
        extract(make_zip(good | {evil_name: b"x"}))
    assert error_code(excinfo) == "UNSAFE_ZIP"
    assert "evil.txt" in excinfo.value.message


def test_encrypted_zip_is_rejected(zip_kind):
    extract, good = zip_kind
    with pytest.raises(AppError) as excinfo:
        extract(mark_first_member_encrypted(make_zip(good)))
    assert error_code(excinfo) == "UNSAFE_ZIP"


def test_zip_bomb_by_compression_ratio_is_rejected(zip_kind):
    extract, good = zip_kind
    with pytest.raises(AppError) as excinfo:
        extract(make_zip(good | {"bomb.bin": b"\0" * 5_000_000}))  # ~1000:1 ratio
    assert error_code(excinfo) == "UNSAFE_ZIP"
    assert "compression" in excinfo.value.message


def test_zip_with_too_much_uncompressed_data_is_rejected(zip_kind):
    extract, good = zip_kind
    tight = dataclasses.replace(settings, max_uncompressed_bytes=100)
    with pytest.raises(AppError) as excinfo:
        extract(make_zip(good | {"more.txt": b"x" * 200}), config=tight)
    assert error_code(excinfo) == "UNSAFE_ZIP"


def test_zip_with_too_many_files_is_rejected(zip_kind):
    extract, good = zip_kind
    tight = dataclasses.replace(settings, max_zip_members=3)
    extra = {f"files/icon{i}.png": b"x" for i in range(4)}
    with pytest.raises(AppError) as excinfo:
        extract(make_zip(good | extra), config=tight)
    assert error_code(excinfo) == "UNSAFE_ZIP"


# --- shapefile zip: missing or extra parts ------------------------------------------------------


def test_zip_without_any_shp_is_rejected():
    with pytest.raises(AppError) as excinfo:
        extract_shapefile_parts(make_zip({"readme.txt": b"hello"}))
    assert error_code(excinfo) == "MISSING_SHAPEFILE_PART"


def test_zip_holding_a_kml_suggests_kmz():
    with pytest.raises(AppError) as excinfo:
        extract_shapefile_parts(make_zip({"survey.kml": DOC_KML}))
    assert error_code(excinfo) == "MISSING_SHAPEFILE_PART"
    assert ".kmz" in excinfo.value.message


@pytest.mark.parametrize("missing", ["parcels.shx", "parcels.dbf"])
def test_zip_missing_a_required_part_names_it(missing):
    members = shapefile_parts()
    del members[missing]
    with pytest.raises(AppError) as excinfo:
        extract_shapefile_parts(make_zip(members))
    assert error_code(excinfo) == "MISSING_SHAPEFILE_PART"
    assert missing in excinfo.value.message


def test_zip_with_two_shapefiles_is_rejected():
    members = shapefile_parts("roads") | shapefile_parts("parcels")
    with pytest.raises(AppError) as excinfo:
        extract_shapefile_parts(make_zip(members))
    assert error_code(excinfo) == "MULTIPLE_SHAPEFILES"


def test_zip_without_prj_is_rejected_as_missing_crs():
    with pytest.raises(AppError) as excinfo:
        extract_shapefile_parts(make_zip(shapefile_parts(prj=None)))
    assert error_code(excinfo) == "MISSING_CRS"
    assert ".prj" in excinfo.value.message


# --- KMZ: which KML is read ---------------------------------------------------------------------


def test_kmz_doc_kml_is_read_and_images_are_ignored():
    blob = make_zip({"doc.kml": DOC_KML, "files/icon.png": b"\x89PNG", "files/photo.jpg": b"jpg"})
    assert extract_kml_from_kmz(blob) == DOC_KML


def test_kmz_root_doc_kml_wins_over_other_kml_files():
    other = kml_document("<!-- other -->")
    assert extract_kml_from_kmz(make_zip({"layer.kml": other, "doc.kml": DOC_KML})) == DOC_KML


def test_kmz_with_one_kml_of_another_name_is_read():
    assert extract_kml_from_kmz(make_zip({"export/map.kml": DOC_KML})) == DOC_KML


def test_kmz_macos_junk_is_ignored():
    blob = make_zip({"__MACOSX/._map.kml": b"junk", "map.kml": DOC_KML})
    assert extract_kml_from_kmz(blob) == DOC_KML


def test_kmz_without_kml_is_rejected():
    with pytest.raises(AppError) as excinfo:
        extract_kml_from_kmz(make_zip({"files/icon.png": b"\x89PNG"}))
    assert error_code(excinfo) == "MISSING_KML"
    assert excinfo.value.status_code == 422


def test_kmz_with_several_kml_and_no_doc_kml_is_rejected():
    with pytest.raises(AppError) as excinfo:
        extract_kml_from_kmz(make_zip({"a.kml": DOC_KML, "b.kml": DOC_KML}))
    assert error_code(excinfo) == "MULTIPLE_KML"
    assert "a.kml" in excinfo.value.message and "b.kml" in excinfo.value.message
