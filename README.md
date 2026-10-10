# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (.zip)** or a **KML** file, extracts every feature, and returns
its **area** (polygons) or **length** (lines) in metres. Points are listed but not measured. Measurements are
never computed in degrees. Each feature is reprojected to a projected CRS centred on itself, an equal-area
projection, before measuring ([CRS handling](#crs-handling)).

- **Stack:** Python 3.11, FastAPI, PostgreSQL 16 + PostGIS 3.4 (SQLAlchemy + GeoAlchemy2), shapely, pyproj, pyshp
- **Tests:** 213 tests (pytest). Measurements are checked against `pyproj.Geod` and PostGIS `geography`
  functions within 0.05%.

---

## Setup

### Prerequisites
- Python 3.11+
- Docker Desktop (runs PostGIS)

### Run it

```bash
# 1. Start PostGIS (port 55432, so it won't clash with a local Postgres)
docker compose up -d --wait

# 2. Create a virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements-dev.txt

# 3. Start the API (tables are created automatically on startup)
uvicorn app.main:app --reload
```

The API runs at <http://localhost:8000>. Interactive Swagger docs are at <http://localhost:8000/docs>.

Configuration is optional. The defaults match `docker-compose.yml`. Every setting can be overridden with an
environment variable (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://geo:geo@localhost:55432/geo` | PostGIS connection |
| `MAX_UPLOAD_MB` | 50 | Largest accepted upload |
| `MAX_UNCOMPRESSED_MB` | 200 | Largest total size a Shapefile zip may expand to |
| `MAX_ZIP_MEMBERS` | 100 | Most files allowed in a zip |
| `MAX_COMPRESSION_RATIO` | 100 | Highest compression ratio for a zip member of 1 MB or more (zip-bomb check) |

A limit that isn't a whole number above 0 stops the server at startup with a message naming the variable.

### Try it with sample files

```bash
python scripts/make_samples.py                     # writes samples/parcels_utm.zip, survey.kml, survey.kmz, no_prj.zip
curl -F "file=@samples/survey.kml" http://localhost:8000/api/files/
```

### Run the tests

```bash
pytest                 # everything; needs PostGIS running (docker compose up -d)
pytest -m "not db"     # only the tests that don't need a database (no Docker required)
ruff check . && ruff format --check .
```

Tests that need PostGIS use a separate `geo_test` database, created by `docker/initdb/20_test_db.sql`, so they never
touch your data. They fail loudly with a connection error if PostGIS isn't running.

### Troubleshooting

**`ImportError: DLL load failed ... An Application Control policy has blocked this file`** (Windows)

Windows **Smart App Control** blocks unsigned native DLLs. On the development machine it blocked SQLAlchemy's optional
C extension. SQLAlchemy works without it, so reinstall it as pure Python:

```powershell
$env:DISABLE_SQLALCHEMY_CEXT = "1"
pip install --force-reinstall --no-deps --no-binary sqlalchemy SQLAlchemy==2.1.4
Remove-Item Env:DISABLE_SQLALCHEMY_CEXT
```

Smart App Control is also why this project doesn't use GDAL, pyogrio or geopandas ([D9](DECISIONS.md)).

**Port already in use:** change the left-hand port in `docker-compose.yml` (`"55432:5432"`) and set `DATABASE_URL`
to match.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a `.zip` (Shapefile), `.kml` or `.kmz` |
| `GET` | `/api/files/{id}/` | File information and processing status |
| `GET` | `/api/files/{id}/measurements/` | Per-feature measurements (paginated) |
| `GET` | `/health` | Is the API up and PostGIS reachable? |

The trailing slash is optional: `/api/files` and `/api/files/` behave the same, so a `curl` without `-L` never
gets an empty redirect.

All examples below are real responses from the sample files.

### `POST /api/files/`

Multipart upload with a single field named `file`. The file is processed during the request, so the response
already has the final status.

```bash
curl -F "file=@samples/parcels_utm.zip" http://localhost:8000/api/files/
```
**201 Created**
```json
{
  "id": "e9dae87b-0c8e-44ca-b97c-8953e4952cfc",
  "filename": "parcels_utm.zip",
  "file_type": "shapefile",
  "size_bytes": 1066,
  "sha256": "b41903418af8ae3631b4671e3a2b3dfbb3ce620ce6cdd455eb04c300d0269143",
  "feature_count": 2,
  "crs": "EPSG:32643",
  "status": "COMPLETED",
  "error": null,
  "created_at": "2026-10-09T13:13:41.226181Z"
}
```

### `GET /api/files/{id}/`

Returns the same shape. `crs` is the file's **source** CRS: here, UTM zone 43N in metres. `size_bytes` and `sha256`
describe the uploaded bytes (the zip itself for a Shapefile or KMZ), so you can check exactly which file was measured;
they are stored for `FAILED` uploads too. (Your `sha256` for the sample will differ: zip files store timestamps.)

```bash
curl http://localhost:8000/api/files/e9dae87b-0c8e-44ca-b97c-8953e4952cfc/
```

### `GET /api/files/{id}/measurements/`

| Query parameter | Default | Meaning |
|---|---|---|
| `limit` | 100 | Page size, 1–1000 |
| `offset` | 0 | Features to skip (ordered by `feature_index`) |
| `include_geometry` | `true` | Include each feature's geometry as GeoJSON (EPSG:4326); `false` leaves it `null` for smaller responses |

```bash
curl "http://localhost:8000/api/files/bef1419e-51c2-4e18-9d41-0213c9192253/measurements/?limit=4"
```
**200 OK** (from `samples/survey.kml`)
```json
{
  "file_id": "bef1419e-51c2-4e18-9d41-0213c9192253",
  "crs": "EPSG:4326",
  "geometry_crs": "EPSG:4326",
  "total": 4, "limit": 4, "offset": 0,
  "summary": {"total_area_m2": 997034.83, "total_length_m": 1001.88, "measured": 2,
              "not_applicable": 1, "failed": 1},
  "items": [
    {"feature_index": 0, "geometry_type": "Polygon", "crs": "EPSG:4326",
     "properties": {"name": "field", "owner": "Asha"},
     "measurement": {"status": "MEASURED", "area_m2": 997034.83, "length_m": null,
                     "projection": "+proj=laea +lat_0=0.004500 +lon_0=0.004500 +datum=WGS84 +units=m",
                     "note": null},
     "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.009, 0.0], [0.009, 0.009], [0.0, 0.009], [0.0, 0.0]]]}},
    {"feature_index": 1, "geometry_type": "LineString", "crs": "EPSG:4326",
     "properties": {"name": "road"},
     "measurement": {"status": "MEASURED", "area_m2": null, "length_m": 1001.88,
                     "projection": "+proj=laea +lat_0=0.000000 +lon_0=0.004500 +datum=WGS84 +units=m",
                     "note": null},
     "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.009, 0.0]]}},
    {"feature_index": 2, "geometry_type": "Point", "crs": "EPSG:4326",
     "properties": {"name": "well"},
     "measurement": {"status": "NOT_APPLICABLE", "area_m2": null, "length_m": null, "projection": null,
                     "note": "Points have no area or length."},
     "geometry": {"type": "Point", "coordinates": [0.001, 0.001]}},
    {"feature_index": 3, "geometry_type": "GeometryCollection", "crs": "EPSG:4326",
     "properties": {"name": "gate and fence"},
     "measurement": {"status": "UNSUPPORTED", "area_m2": null, "length_m": null, "projection": null,
                     "note": "GeometryCollection is not supported for measurement."},
     "geometry": {"type": "GeometryCollection", "geometries": [
       {"type": "Point", "coordinates": [0.002, 0.0]},
       {"type": "LineString", "coordinates": [[0.0, 0.009], [0.009, 0.009]]}]}}
  ]
}
```
(Numbers are rounded here for readability; the API returns full precision.)

- `crs` is the source CRS of the file. `geometry_crs` is always `EPSG:4326`, because returned geometries are WGS 84
  whatever the source.
- `measurement.status` is one of `MEASURED`, `NOT_APPLICABLE` (points), `UNSUPPORTED`, `INVALID` or `EMPTY`. `note`
  explains anything that isn't `MEASURED`.
- `projection` is the exact projection used for that feature, for transparency.
- `summary` totals and counts cover the whole file, not just the current page. `not_applicable` counts points
  (nothing to measure, which is expected); `failed` counts features that should have been measured but were
  `EMPTY`, `INVALID` or `UNSUPPORTED`.

With `include_geometry=false`, every item has `"geometry": null`, which keeps responses small when only the numbers
are needed.

### Errors

Every error has the same shape. `file_id` is present when a `FAILED` record was stored, so the client can look it up.
The interactive docs at `/docs` show this shape (`ErrorResponse`) and each endpoint's error codes.

```bash
curl -F "file=@samples/no_prj.zip" http://localhost:8000/api/files/
```
**422 Unprocessable Content**
```json
{"error": {"code": "MISSING_CRS",
           "message": "The Shapefile has no parcels.prj file, so its coordinate system is unknown. Add the .prj file exported by your GIS tool.",
           "file_id": "2bf600f6-d1c2-4e23-80ba-24d91ff98d52"}}
```

| HTTP | Code | When | Record stored? |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | Missing `file` field, bad `limit`/`offset` | no |
| 400 | `EMPTY_FILE` | 0-byte upload | no |
| 413 | `FILE_TOO_LARGE` | Over 50 MB (checked from `Content-Length` and by counting bytes) | no |
| 415 | `UNSUPPORTED_FILE_TYPE` | Not `.zip` / `.kml` / `.kmz` | no |
| 422 | `CORRUPT_ZIP` | `.zip` or `.kmz` that isn't a zip, or is damaged | FAILED |
| 422 | `UNSAFE_ZIP` | (`.zip` and `.kmz`) Path traversal (`../`, absolute, drive letters), encrypted, zip bomb (ratio, total size, file count) | FAILED |
| 422 | `MISSING_SHAPEFILE_PART` | No `.shp`, or missing `.shx`/`.dbf` (the message names which) | FAILED |
| 422 | `MULTIPLE_SHAPEFILES` | More than one `.shp` in the zip | FAILED |
| 422 | `MISSING_KML` / `MULTIPLE_KML` | KMZ with no `.kml`, or several and no `doc.kml` | FAILED |
| 422 | `MISSING_CRS` / `INVALID_CRS` | No `.prj`, or one that isn't a usable 2D CRS | FAILED |
| 422 | `CORRUPT_SHAPEFILE` | `.shp`/`.shx`/`.dbf` can't be read | FAILED |
| 422 | `INVALID_KML` | Not XML, not `<kml>`, or contains XML entity declarations (XXE / billion laughs) | FAILED |
| 422 | `NO_FEATURES` | Readable file with zero features | FAILED |
| 404 | `FILE_NOT_FOUND` / `NOT_FOUND` | Unknown or malformed id / unknown route | — |
| 409 | `FILE_NOT_READY` | Measurements requested for a file that isn't `COMPLETED` | — |
| 503 | `DATABASE_UNAVAILABLE` | PostGIS unreachable (3 s connect timeout per address, so it fails in seconds and never hangs) | — |
| 500 | `INTERNAL_ERROR` | A bug. Logged with a stack trace; the client sees a generic message. | FAILED |

Problems with a **single feature** (invalid, empty or unsupported geometry) never fail the file. They show up in that
feature's `measurement.status`.

---

## Architecture

### Application structure

```
app/
├── main.py                  # FastAPI app: routers, error handlers, size-limit middleware, logging, table creation
├── config.py                # settings from environment variables (DATABASE_URL, upload and zip limits)
├── db.py                    # SQLAlchemy engine (with connect timeout), session per request
├── models.py                # tables: files, features (PostGIS geometry column)
├── schemas.py               # Pydantic response models: the API contract
├── errors.py                # AppError + handlers -> {"error": {"code", "message", "file_id?"}}
├── api/
│   ├── files.py             # the three endpoints (plain `def`, run in FastAPI's thread pool)
│   └── health.py            # GET /health
└── processing/              # plain functions, no web or DB code: easy to unit test
    ├── upload.py            # file type, size limit, safe zip inspection -> ShapefileParts / KML bytes
    ├── shapefile_reader.py  # pyshp -> list[RawFeature] + CRS from .prj
    ├── kml_reader.py        # defusedxml -> list[RawFeature] (CRS is always EPSG:4326)
    ├── raw_feature.py       # RawFeature: what both readers return
    ├── crs.py               # .prj parsing, reproject to WGS 84, per-feature LAEA projection
    ├── measure.py           # one geometry -> Measurement(status, area_m2, length_m, ...)
    └── pipeline.py          # glue: files row, read, measure, save, status transitions
tests/                       # 260+ tests; every fixture is generated in code (tests/builders.py)
scripts/make_samples.py      # sample files for trying the API by hand
scripts/compare_projections.py  # reproduces the CRS comparison table below
docker-compose.yml           # PostGIS only; docker/initdb creates the geo_test database
```

### File-processing flow

```mermaid
flowchart TD
    C[Client] -->|multipart 'file'| MW{Content-Length over 50 MB?}
    MW -->|yes| E413[413 FILE_TOO_LARGE]
    MW -->|no| P[POST /api/files/]
    P --> V[Request checks: extension, size by counting bytes, not empty]
    V -->|fails| E4[4xx error, nothing stored]
    V --> R[(files row: PENDING → PROCESSING)]
    R --> T{file type}
    T -->|.zip| Z[Safe zip inspection in memory: paths, bombs, encryption, required parts]
    Z --> SR[pyshp reads .shp/.shx/.dbf; .prj → source CRS]
    T -->|.kmz| KZ[Same safe zip inspection; pick doc.kml or the only .kml]
    KZ --> KR
    T -->|.kml| KR[defusedxml parses KML; CRS = EPSG:4326]
    SR --> M[For each feature: reproject + measure]
    KR --> M
    M --> S[(features rows + files COMPLETED, one transaction)]
    S --> OK[201 + file info]
    Z -. content error .-> F[(files row FAILED + error code)] --> E422[422 with file_id]
    KZ -. content error .-> F
    SR -. content error .-> F
    KR -. content error .-> F
```

Notes:
- **Nothing is extracted to disk.** Zip members are read into memory after the safety checks, so a malicious path such
  as `../../etc/x` can't write anything. We reject such paths anyway, with a clear error. Shapefile zips and KMZ files
  go through the same checks (`_open_safe_zip`).
- **KMZ** is a zip holding a KML (plus images). We read the root `doc.kml` (Google Earth's convention), or the only
  `.kml` if there is just one; several and no `doc.kml` is refused rather than guessed. Images are ignored.
- **Temporary files:** we create none. Starlette receives the upload into its own `SpooledTemporaryFile` (in memory up to
  1 MB, then a temp file) and closes it after the response.
- **Synchronous processing:** the upload is processed inside the request, but the `status` field (PENDING → PROCESSING →
  COMPLETED / FAILED) is still stored. A background queue could be added later without changing the API.

### Measurement calculation flow

For each feature (`processing/measure.py`):

1. **Reproject to WGS 84 lon/lat** (`to_wgs84`). Every file takes this step, whatever its CRS (see below). Coordinates
   pyproj can't convert (it returns `inf`) make the feature `INVALID`.
2. **Sort by type:** Point/MultiPoint → `NOT_APPLICABLE`; GeometryCollection and other types → `UNSUPPORTED`; empty →
   `EMPTY`; self-intersecting or otherwise invalid → `INVALID`, with shapely's reason (for example
   `Self-intersection[0.5 0.5]`). Invalid shapes are **reported, not repaired**, because repairing silently changes the
   user's area.
3. **Sanity checks:** lon/lat outside ±180/±90 means the `.prj` doesn't match the data, so the feature is `INVALID`. A
   feature spanning more than 180° of longitude crosses the antimeridian (±180°): in plain lon/lat it looks like it
   wraps the globe. Its negative longitudes are shifted by +360° (−179° becomes 181°, next to 179°) **for measuring
   only**; the stored geometry keeps normal ±180° values. The note says so. If it is still wider than 180° after the
   shift (Antarctica surrounds the South Pole), it is `UNSUPPORTED`. On Natural Earth's 1:10m countries, this measures
   Russia, the USA, Fiji, New Zealand and Kiribati within 0.0002% of PostGIS's geodesic area.
4. **Project** with a Lambert Azimuthal Equal-Area projection centred on the feature's centroid. The coordinates are now
   in metres.
5. **Measure** with shapely: `.area` for Polygon/MultiPolygon (holes subtracted), `.length` for
   LineString/MultiLineString.

The WGS 84 geometry is stored in PostGIS (`geometry(Geometry, 4326)`), along with the measurement and the exact
projection string used.

### CRS handling

**Why reproject at all?** Latitude/longitude are angles, not distances. One degree of longitude is about 111 km at the
equator and 0 km at the poles, so "square degrees" mean nothing. Our 1 km test square is 0.000081 square degrees but
997,035 m².

**1. Detecting the source CRS**
- **Shapefile:** the `.prj` file (ESRI WKT) is parsed with `pyproj.CRS.from_wkt` and labelled `EPSG:<code>` when pyproj
  recognises it. A missing `.prj` is **rejected** (`MISSING_CRS`) instead of guessing: a guessed CRS gives
  plausible-looking but wrong numbers. A `.prj` that isn't a 2D CRS (for example a vertical "height" CRS) is
  `INVALID_CRS`.
- **KML:** the OGC KML 2.2 standard fixes coordinates to WGS 84 lon/lat, so the CRS is always EPSG:4326.

**2. Choosing the projected CRS: per-feature Lambert Azimuthal Equal-Area (LAEA)**

Each feature gets its own projection:
`+proj=laea +lat_0=<centroid lat> +lon_0=<centroid lon> +datum=WGS84 +units=m`

- **LAEA is area-preserving (equal-area).** Every region keeps its true area after projection, which is exactly what an
  area measurement needs.
- **Centring it on the feature** keeps distance distortion tiny near the centre, so lengths are accurate too.
- It works anywhere on Earth, including the poles, with no lookup tables.

I compared four strategies against `pyproj.Geod` (geodesic calculations on the WGS 84 ellipsoid):

| Test feature | UTM area | EPSG:6933 area | **LAEA area** | UTM length | **LAEA length** |
|---|---|---|---|---|---|
| 1 km square at (0°, 0°), on a UTM zone edge | +0.196% | 0.0000% | **0.0000%** | +0.098% | **0.0000%** |
| 1 km square at (3°, 0°), UTM central meridian | −0.080% | 0.0000% | **0.0000%** | −0.040% | **0.0000%** |
| 50 km box at 50°N across the 6°E zone boundary | +0.033% | +0.001% | **−0.001%** | +0.017% | **+0.0001%** |
| 100 km box at 60°N | −0.053% | +0.010% | **−0.003%** | −0.025% | **+0.0005%** |
| 500 km box at 20°N | +0.070% | −0.034% | **−0.076%** | +0.086% | **+0.013%** |
| 1 km square at 87°N | no UTM zone | 0.0001% | **0.0000%** | — | **0.0000%** |

Reproduce this table with `python scripts/compare_projections.py`.

- **Per-feature UTM** (the textbook choice) is up to 0.2% off at zone edges and has no zones beyond 84°N / 80°S.
- **One UTM zone per file** gets worse for files spanning several zones.
- **EPSG:6933** (global equal-area) gets area right but distorts length (it isn't conformal).
- **Per-feature LAEA** had the smallest error in every case.

**Where the remaining small error comes from:** not from the projection. The two methods draw the *edges*
differently. Shapely measures a polygon whose corners are joined by **straight lines on the flat projected map**.
`pyproj.Geod` joins the same corners with **geodesics** (shortest paths on the ellipsoid). Those are slightly different
shapes, and the difference grows with edge length: about 0% for 1 km features and about 0.08% for 500 km ones.

**Continent-scale lines are a few tenths of a percent short.** LAEA keeps *areas* exact at any size, but no flat map
keeps every *distance* exact: away from the projection centre, LAEA shortens distances in some directions. Up to
~500 km this stays under 0.1% (above), but lines thousands of km long add it up. Measured against `pyproj.Geod` (the first and
last rows are pinned by tests in `tests/test_measure.py`):

| Feature | Geodesic | Our result vs Geod |
|---|---|---|
| Straight line 0°→30°N along 31°E | 3,320 km | −0.29% |
| Straight line 0°→30°E along 30°N | 2,895 km | −0.21% |
| Straight diagonal (0°, 0°)→(25°, 25°) | 3,863 km | −0.38% |
| Nile (Natural Earth 1:110m rivers) | 4,673 km | −0.13% |
| 30° × 30° box, **area** | 10.6 million km² | −0.0000% |

Real rivers wind back on themselves and stay closer to their centre than a straight line, so they do better. Areas
are unaffected, which we also checked on Natural Earth's 1:10m countries: all measured countries agree with PostGIS
geodesic area within 0.001%. This is a documented limit rather than fixed (DECISIONS D38): the assignment asks for
measurement in a projected CRS, and features thousands of km long are rare for this kind of service.

**3. Sources that are already projected**

Every source follows the same path: **source CRS → WGS 84 → local LAEA**. We never measure directly in the file's own
projected CRS, because:
- *Projected doesn't mean accurate for measuring.* Web Mercator (EPSG:3857) inflates areas by about 4× at 60°N. A test
  builds exactly this trap and checks that we avoid it.
- *Units are handled automatically.* A file in US survey feet (EPSG:2227) comes out in m² with no manual conversion
  factor. The UTM sample above (`EPSG:32643`) matches Geod to 0.0000%.

**4. Validation**
- Unit tests compare every measurement with `pyproj.Geod`. Tolerance: **0.05% for features up to 100 km, 0.1% up to
  500 km** (measured worst cases: 0.003% and 0.076%).
- Integration tests compare our stored values with PostGIS `ST_Area(geom::geography)` / `ST_Length(...)`, a second,
  independent implementation, within 0.05%.

**Accuracy limits:** see [Limitations](#limitations).

---

## Design decisions

Each decision, with context, options and trade-offs, is in [DECISIONS.md](DECISIONS.md). Summary:

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Framework | FastAPI | Django + DRF | Lighter; Pydantic models double as the API contract; automatic OpenAPI docs |
| Storage | PostgreSQL + PostGIS | SQLite with GeoJSON text | Real spatial type and index, plus an independent `geography` cross-check |
| Processing | Synchronous, with a stored status | BackgroundTasks; Celery/RQ | Simplest correct option; the status field keeps the queue upgrade path open |
| Reading files | pyshp + our own KML parser (defusedxml) | geopandas / pyogrio (GDAL) | GDAL was blocked on the dev machine; the parser is small and fully explainable |
| Projected CRS | Per-feature LAEA | Per-feature UTM, one UTM per file, EPSG:6933 | Area-preserving, smallest measured error, works at the poles |
| Already-projected sources | Always go via WGS 84 → LAEA | Measure in the source CRS | Source projections can distort (Web Mercator); units handled automatically |
| Missing `.prj` | Reject (`MISSING_CRS`) | Assume EPSG:4326 | A wrong guess gives confident wrong numbers |
| Invalid geometry | Report as `INVALID` | `make_valid()` | Repairing changes the user's area silently (PostGIS even returns 0 m² for a bowtie) |
| Bad content | 422 + stored FAILED record | 4xx, nothing stored | Failures stay inspectable via `GET`; the status field means something |
| Geometry storage | WGS 84 (SRID 4326) | Keep source CRS | GeoJSON (RFC 7946) expects WGS 84; one SRID per column |
| Endpoints | Plain `def` | `async def` | All libraries used are blocking; `def` runs in a thread pool instead of blocking the event loop |
| Zip handling | Read members in memory | `extractall()` to a temp dir | Path traversal can't write files; no cleanup needed |
| Measurements response | Paginated, geometry on by default (`include_geometry=false` to drop it) | Everything at once; geometry opt-in | Large files stay fast; geometry can be big |

---

## Testing

- **213 tests**: 137 run without a database (`pytest -m "not db"`), and 76 use PostGIS.
- **All fixtures are generated in code** (`tests/builders.py`): Shapefiles with pyshp, KML as strings, and attack zips
  (path traversal, bombs, a patched "encrypted" flag). No binary test files are committed.
- **Measurement correctness:** `pyproj.Geod` is the reference (0.05% up to 100 km, 0.1% up to 500 km), plus PostGIS
  `geography` as a second independent check. Cases include zone edges, 60°N, 87°N, the southern hemisphere, holes,
  multi-parts, and Web Mercator and US-feet sources.
- **Error matrix** (`tests/test_error_matrix.py`): every error case above is checked through the real API, including
  whether a FAILED record is stored.
- **Robustness:** random bytes, truncated and bit-flipped files (fixed seed) never cause a 500. A 10,000-vertex polygon
  is measured.

---

## Limitations

- **Very long lines:** lengths are within 0.1% up to ~500 km, but continent-scale lines come out short: about 0.2–0.4%
  for straight lines of 3,000–4,000 km, 0.13% for the Nile (table in [CRS handling](#crs-handling)). Areas are not
  affected at any size. A geodesic length (`pyproj.Geod`) would remove this (future scope).
- **Antimeridian and poles:** a feature wider than 180° of longitude is assumed to cross ±180° the short way (the
  usual reason). A feature that really goes more than halfway round the world the long way would be measured the short
  way; the note flags every shifted feature. Features around a pole (Antarctica) are `UNSUPPORTED`.
- **A `.prj` that doesn't match the data:** out-of-range lon/lat is caught (`INVALID`), but projected coordinates far
  outside their CRS's area can convert to plausible-looking but wrong lon/lat. That can't be detected without trusting
  something other than the `.prj`.
- **Datum shifts** (for example NAD27 → WGS 84) may use PROJ's approximate transform when grid files aren't installed:
  accurate to metres, which is negligible for area and length.
- **Formats:** no GeoJSON or GeoPackage. Only one Shapefile per zip. KML `gx:Track` and `Model` are `UNSUPPORTED`. In a
  KMZ only one KML is read: `NetworkLink`s to other files or URLs are not followed, and image overlays are ignored.
  Altitude/Z values are ignored (measurements are 2D map values).
- **Scale:** files up to 50 MB are processed in memory inside the request (about 1 ms per feature). There's no auth, no
  listing or deleting of files, and no schema migrations (tables are created on startup). A database created before
  `size_bytes`/`sha256` were added needs `ALTER TABLE files ADD COLUMN IF NOT EXISTS size_bytes BIGINT, ADD COLUMN IF
  NOT EXISTS sha256 VARCHAR(64);` (or drop the tables and restart).

---

## Learning and Future Scope

### What I learned

- **Coordinates are angles, not distances.** Measuring in lon/lat is meaningless. Every projection trades off area,
  shape and distance, so the right one depends on what you measure. For area, an equal-area projection centred on the
  feature turned out better than the textbook UTM answer, and I could show it with numbers rather than opinion.
- **"Projected" doesn't mean "accurate".** Web Mercator looks like metres but inflates areas about 4× at 60°N.
- **Test the oracle too.** A failing test turned out to be a wrong reference: `pyproj.Geod` adds a hole instead of
  subtracting it if the hole is drawn in the same direction as the outer ring.
- **Uploaded files are hostile input:** zip slip, zip bombs, XML entity expansion, lying size headers. Each one needs
  its own defence, and each defence needs a test.
- **Libraries don't always raise what you expect.** shapely raised `GEOSException` (not `ValueError`) for a one-point
  line, and pyshp raises `KeyError` on damaged files. Tests with bad input found both before users would have.
- **Defaults can hang a service.** libpq has no connect timeout by default, so a down database made requests hang.
  It's fixed with an explicit timeout.
- **Working on a locked-down machine:** Windows Smart App Control blocked GDAL, so I read Shapefiles with pyshp and
  wrote a small KML parser. That kept the code simple enough to explain line by line.

### Future scope
- **Background processing** (Celery/RQ/arq) for large files. The `PENDING/PROCESSING` statuses already support it.
- **Geodesic length** (`pyproj.Geod`) for lines longer than ~500 km, where LAEA's distance distortion passes 0.1%;
  densifying long polygon edges.
- **Polar features:** measure features that surround a pole (Antarctica), e.g. with a polar equal-area projection.
- **Spatial queries** using the existing GiST index: bounding-box and intersects filters, nearest features.
- **More formats:** GeoJSON, GeoPackage (via GDAL where available), multiple Shapefiles per zip, KMZ files with
  several KML layers.
- **Streaming** for very large files instead of reading them into memory.
- **Auth and ownership** of uploaded files, plus listing and deleting files.
- **Alembic migrations**, a Dockerfile for the API itself, and CI (GitHub Actions running ruff and pytest with a
  PostGIS service).
