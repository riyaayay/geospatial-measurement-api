"""Generate small sample files for trying the API by hand.

    python scripts/make_samples.py        # writes into ./samples/

- parcels_utm.zip  Shapefile in UTM zone 43N (EPSG:32643, metres): two fields near Ahmedabad,
                   one with a courtyard (hole). Shows that projected sources are handled.
- survey.kml       A field (polygon), a road (line), a well (point) and a mixed MultiGeometry.
- no_prj.zip       A Shapefile without its .prj file, to see the MISSING_CRS error.
"""

import io
import zipfile
from pathlib import Path

import shapefile  # pyshp
from pyproj import CRS, Transformer

OUT = Path("samples")
UTM_43N = CRS.from_epsg(32643)


def to_utm(ring):
    t = Transformer.from_crs("EPSG:4326", UTM_43N, always_xy=True)
    return [t.transform(lon, lat) for lon, lat in ring]


def square(lon, lat, size_deg):
    return [
        (lon, lat),
        (lon, lat + size_deg),
        (lon + size_deg, lat + size_deg),
        (lon + size_deg, lat),
        (lon, lat),
    ]


def shapefile_zip(prj: str | None) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as w:
        w.field("name", "C")
        w.field("crop", "C")
        w.poly([to_utm(square(72.57, 23.02, 0.01))])
        w.record("North field", "cotton")
        courtyard = list(reversed(square(72.595, 23.005, 0.003)))  # holes run the other way
        w.poly([to_utm(square(72.59, 23.0, 0.012)), to_utm(courtyard)])
        w.record("Farmhouse plot", "wheat")
    files = {
        "parcels.shp": shp.getvalue(),
        "parcels.shx": shx.getvalue(),
        "parcels.dbf": dbf.getvalue(),
    }
    if prj is not None:
        files["parcels.prj"] = prj.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Survey</name>
  <Placemark><name>field</name>
    <ExtendedData><Data name="owner"><value>Asha</value></Data></ExtendedData>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      0,0 0.009,0 0.009,0.009 0,0.009 0,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Folder><name>Infrastructure</name>
    <Placemark><name>road</name>
      <LineString><coordinates>0,0 0.009,0</coordinates></LineString></Placemark>
    <Placemark><name>well</name>
      <Point><coordinates>0.001,0.001</coordinates></Point></Placemark>
  </Folder>
  <Placemark><name>gate and fence</name>
    <MultiGeometry>
      <Point><coordinates>0.002,0</coordinates></Point>
      <LineString><coordinates>0,0.009 0.009,0.009</coordinates></LineString>
    </MultiGeometry></Placemark>
</Document></kml>
"""

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "parcels_utm.zip").write_bytes(shapefile_zip(UTM_43N.to_wkt("WKT1_ESRI")))
    (OUT / "survey.kml").write_text(KML, encoding="utf-8")
    # The same KML as a KMZ: Google Earth names the main file doc.kml.
    with zipfile.ZipFile(OUT / "survey.kmz", "w", zipfile.ZIP_DEFLATED) as kmz:
        kmz.writestr("doc.kml", KML)
    (OUT / "no_prj.zip").write_bytes(shapefile_zip(prj=None))
    for path in sorted(OUT.iterdir()):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
