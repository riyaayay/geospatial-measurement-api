import pytest
from pyproj import CRS

from app.errors import AppError
from app.processing.crs import WGS84, crs_label, parse_prj


def esri_prj(epsg_or_proj) -> str:
    return CRS(epsg_or_proj).to_wkt("WKT1_ESRI")


@pytest.mark.parametrize(
    ("source", "label"),
    [
        (4326, "EPSG:4326"),
        (32643, "EPSG:32643"),  # UTM zone 43N (western India)
        (3857, "EPSG:3857"),  # Web Mercator
        (2227, "EPSG:2227"),  # California zone 3, US survey feet
        (4267, "EPSG:4267"),  # NAD27
    ],
)
def test_esri_prj_files_are_recognised_as_epsg_codes(source, label):
    crs = parse_prj(esri_prj(source))
    assert crs_label(crs) == label


def test_crs_without_an_epsg_code_gets_a_descriptive_label():
    crs = parse_prj(esri_prj("+proj=laea +lat_0=10 +lon_0=20 +datum=WGS84"))
    assert "custom" in crs_label(crs).lower()


@pytest.mark.parametrize("text", ["this is not wkt", "", "   "])
def test_unparseable_prj_is_invalid_crs(text):
    with pytest.raises(AppError) as excinfo:
        parse_prj(text)
    assert excinfo.value.code == "INVALID_CRS"
    assert excinfo.value.status_code == 422


def test_vertical_only_crs_is_rejected_because_it_has_no_horizontal_coordinates():
    with pytest.raises(AppError) as excinfo:
        parse_prj(esri_prj(5703))  # NAVD88 height
    assert excinfo.value.code == "INVALID_CRS"


def test_wgs84_constant_is_epsg_4326():
    assert crs_label(WGS84) == "EPSG:4326"
