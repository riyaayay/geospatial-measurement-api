"""Reproduce the CRS comparison table in the README (section "CRS handling").

    python scripts/compare_projections.py

For each test shape, compares area/length measured in three projected CRSs against pyproj.Geod
(geodesic calculation on the WGS 84 ellipsoid). The LAEA column uses the app's own functions,
so it measures exactly what the API does.
"""

import sys
from pathlib import Path

import numpy as np
import shapely
from pyproj import Geod, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info
from shapely.geometry import LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `app` imports from the repo
from app.processing.crs import local_projection, project  # noqa: E402

GEOD = Geod(ellps="WGS84")


def box(lon1, lat1, lon2, lat2):
    return Polygon([(lon1, lat1), (lon2, lat1), (lon2, lat2), (lon1, lat2)])


CASES = {
    "1 km square at (0E, 0N), UTM zone edge": box(0, 0, 0.009, 0.009),
    "1 km square at (3E, 0N), UTM central meridian": box(3, 0, 3.009, 0.009),
    "50 km box at 50N across the 6E zone boundary": box(5.65, 50, 6.35, 50.45),
    "100 km box at 60N": box(10, 60, 11.8, 60.9),
    "500 km box at 20N": box(69, 20, 73.8, 24.5),
    "1 km square at 87N": box(0, 87, 0.17, 87.009),
}


def with_crs(geom, crs):
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return shapely.transform(geom, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


def utm_for(geom):
    c = geom.centroid
    zones = query_utm_crs_info(
        datum_name="WGS 84", area_of_interest=AreaOfInterest(c.x, c.y, c.x, c.y)
    )
    return f"EPSG:{zones[0].code}" if zones else None  # no UTM zones beyond 84N / 80S


def error(value, reference):
    return f"{100 * (value - reference) / reference:+.4f}%"


def main():
    header = [
        "Test feature",
        "UTM area",
        "EPSG:6933 area",
        "LAEA area",
        "UTM length",
        "LAEA length",
    ]
    print(" | ".join(header))
    for name, poly in CASES.items():
        ring = LineString(poly.exterior.coords)
        geod_area = abs(GEOD.geometry_area_perimeter(poly)[0])
        geod_length = GEOD.geometry_length(ring)
        utm = utm_for(poly)
        laea = local_projection(poly.centroid)
        row = [
            name,
            error(with_crs(poly, utm).area, geod_area) if utm else "no UTM zone",
            error(with_crs(poly, "EPSG:6933").area, geod_area),
            error(project(poly, laea).area, geod_area),
            error(with_crs(ring, utm).length, geod_length) if utm else "-",
            error(project(ring, local_projection(ring.centroid)).length, geod_length),
        ]
        print(" | ".join(row))


if __name__ == "__main__":
    main()
