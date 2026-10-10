# PLAN: Geospatial File Measurement API

Status: **APPROVED by Riya on 2026-10-09** with "defaults OK" for all open questions and amendments A1–A7 (listed at the
end of this file, already applied in the sections below).

Source of truth: `docs/ASSIGNMENT.md`. Working rules: `CLAUDE.md`. Decision log: `DECISIONS.md`.

---

## 0. Environment facts (verified in `scratch/`, 2026-10-09)

| Item | Result |
|---|---|
| Python | 3.11.9, pip 26 (in venv) |
| git / Docker | git 2.54, Docker 29.7 (Docker Desktop must be started manually) |
| `gh`, `pandoc` | Not installed. GitHub publishing will be done by hand; pandoc isn't needed. |
| **Windows Smart App Control** | **ON.** It blocks unsigned native DLLs: **GDAL (pyogrio/fiona), pandas (so geopandas), SQLAlchemy's C extension** |
| Works natively | shapely 2.2.0, pyproj 3.7.2 (PROJ 9.5.1), pyshp 3.1.6, defusedxml 0.7.1, FastAPI 0.143, pydantic-core, psycopg 3.3 (binary), uvicorn, pytest 9.1, httpx, ruff 0.16 |
| SQLAlchemy | 2.1.4 works on this machine **only when installed as pure Python** (`DISABLE_SQLALCHEMY_CEXT=1 pip install --no-binary sqlalchemy sqlalchemy`); GeoAlchemy2 0.20 works on top of it. The README's main setup still uses a normal `pip install`, and this step goes in a Windows Smart App Control troubleshooting section (A1). |
| pyshp | Reads `.shp/.shx/.dbf` from **in-memory** zip members, so nothing is extracted to disk. Its `__geo_inte
rface__` drops Z values. Null shapes come back as `NULL`. |
| defusedxml | Bad XML raises `ParseError`; DTD/entity tricks raise `EntitiesForbidden` (protects against XXE and billion-laughs attacks) |
| PostGIS | `postgis/postgis:16-3.4` (PostGIS 3.4.3) works with GeoAlchemy2 + psycopg: GiST index `idx_features_geom` is auto-created, JSONB stores non-ASCII fine, and EMPTY and NULL geometries both store. A 1 km equator square gives ours 997,034.83 m², Geod 997,034.84, `ST_Area(geography)` 997,034.84. **A self-intersecting bowtie gives `ST_Area` = 0.0 with no error**, which is why invalid geometries are reported (D14) and the PostGIS cross-check only runs on MEASURED features. |
| Ports | 5432 and 5433 are taken by two local Postgres servers, and 54323–54422 is reserved by Windows. **Compose maps PostGIS to host port 55432.** |

These facts drove decisions D9 (pure-Python readers) and D10 (LAEA), which were confirmed by Riya.

---

## 1. Goals and non-goals

### Requirement checklist (assignment → where it is satisfied)

| # | Assignment requirement | Where it is satisfied | Milestone |
|---|---|---|---|
| 1 | Django+DRF **or** FastAPI | FastAPI (D1) | M0 |
| 2 | `POST /api/files/` accepts a `.zip` Shapefile | `app/api/files.py`, `app/processing/upload.py`, `shapefile_reader.py` | M1, M2, M4 |
| 2 | ...and accepts `.kml` | `kml_reader.py` | M2, M4 |
| 3 | Read data, extract features | `pipeline.py` → readers → `features` table | M2, M4 |
| 3 | Per feature: ID/index | `feature_index` (0-based order in file) | M2 |
| 3 | Per feature: geometry type | `geometry_type` column | M2 |
| 3 | Per feature: geometry | PostGIS `geom` (EPSG:4326), returned as GeoJSON with `?include_geometry=true` | M4 |
| 3 | Per feature: CRS | `crs` on file + on each feature item in the response | M2, M4 |
| 3 | Per feature: properties | `properties` JSONB | M2 |
| 3 | Unsupported geometry handled gracefully | per-feature `measurement.status = UNSUPPORTED` + note, never a crash | M3 |
| 4 | Polygon → area | `measure.py` (Polygon and MultiPolygon) | M3 |
| 4 | LineString → length | `measure.py` (LineString, MultiLineString, LinearRing) | M3 |
| 4 | Point → no measurement | `status = NOT_APPLICABLE` | M3 |
| 5 | No area/distance in degrees; reproject to a projected CRS | per-feature LAEA in metres (D10), section 4 | M3 |
| 5 | Own strategy for projected CRS | section 4, README "CRS handling" | M3, M6 |
| 6 | `POST /api/files/` uploads **and processes** | synchronous processing (D3) | M4 |
| 6 | `GET /api/files/{id}/` with id, filename, feature_count, crs, status | `FileOut` schema | M4 |
| 6 | `GET /api/files/{id}/measurements/` | `MeasurementPage` schema | M4 |
| 7 | README: Setup, API + examples, Architecture (structure, file flow, measurement flow, CRS), Design Decisions | `README.md` | M6 |
| 8 | Public GitHub repo, link shared | instructions; created **only after Riya says so** | M7 |
| 8 | README: learning and future scope | README section | M6 |

### Non-goals (stated in the README as future scope)
KMZ, GeoJSON/GeoPackage input, background queue, auth, file deletion/listing, Alembic migrations, streaming huge files, editing features, raster data.

---

## 2. Architecture

```
geospatial-measurement-api/
├── app/
│   ├── main.py              # create FastAPI app, register routers + error handlers, create tables on startup
│   ├── config.py            # settings from env vars (DATABASE_URL, size limits) with safe defaults
│   ├── db.py                # SQLAlchemy engine, session factory, get_db() dependency
│   ├── models.py            # ORM: GeoFile, Feature (GeoAlchemy2 Geometry column)
│   ├── schemas.py           # Pydantic response models (FileOut, MeasurementItem, MeasurementPage, ErrorOut)
│   ├── errors.py            # AppError(code, message, http_status) + one handler → consistent error JSON
│   ├── api/
│   │   └── files.py         # the 3 endpoints + GET /health
│   └── processing/
│       ├── upload.py        # size limit, extension check, content sniffing, safe zip inspection
│       ├── shapefile_reader.py  # zip members → list[RawFeature] + source CRS (from .prj)
│       ├── kml_reader.py    # KML bytes → list[RawFeature] (CRS always EPSG:4326)
│       ├── crs.py           # parse .prj, label CRS, build transformer to WGS84, build local LAEA
│       ├── measure.py       # one geometry → Measurement(status, area_m2, length_m, note)
│       └── pipeline.py      # orchestration + file status transitions
├── tests/
│   ├── builders.py          # make_shapefile_zip(), make_kml(), make_evil_zip() (fixtures generated in code)
│   ├── conftest.py          # test DB setup, TestClient, per-test table cleanup
│   ├── test_upload.py  test_shapefile_reader.py  test_kml_reader.py
│   ├── test_crs.py  test_measure.py
│   └── test_api_upload.py  test_api_file_info.py  test_api_measurements.py  test_error_matrix.py
├── docker/initdb/01-test-db.sql   # creates the geo_test database + postgis extension
├── docker-compose.yml       # PostGIS only (D6)
├── requirements.txt  pyproject.toml (ruff + pytest config)  .env.example  .gitignore
├── README.md  PLAN.md  DECISIONS.md  INTERVIEW_NOTES.md (not part of submission)
```

**Endpoints are plain `def`, not `async def` (A3).** All the work (pyshp, pyproj, psycopg/SQLAlchemy) is blocking. FastAPI
runs plain `def` endpoints in a thread pool, so they don't block the event loop. `async def` with blocking calls inside
would freeze the whole server during each upload.

`RawFeature` is a small dataclass `(index: int, geometry: shapely geometry | None, properties: dict)`. Both readers
return the same shape, so everything after reading is format-agnostic.

**Why this split:** each module has a single job that's easy to test without the others. The readers don't know about
the DB, and `measure.py` doesn't know about files. The API layer is thin. The `processing/` modules are plain
functions, so Riya can call them from a Python shell during the interview.

### Request flow

```mermaid
flowchart TD
    C[Client] -->|multipart file| P[POST /api/files/]
    P --> U[upload.py: size ≤ 50 MB, .zip/.kml, sniff content]
    U -->|bad request| E4[4xx error JSON, nothing stored]
    U --> R[(files row: PENDING → PROCESSING)]
    R --> RD{type?}
    RD -->|.zip| SR[shapefile_reader: safe zip read + .prj → CRS]
    RD -->|.kml| KR[kml_reader: defusedxml parse, CRS = EPSG:4326]
    SR --> M[for each feature: to WGS84 → local LAEA → area/length]
    KR --> M
    M --> S[(features rows + files row COMPLETED)]
    SR -.content error.-> F[(files row FAILED + error) → 422 with file_id]
    KR -.content error.-> F
    S --> OK[201 FileOut]
    G1[GET /api/files/id/] --> DB[(PostGIS)]
    G2[GET /api/files/id/measurements/?limit&offset&include_geometry] --> DB
```

---

## 3. File-processing flow

1. **Receive.** `UploadFile` is copied in 1 MB chunks into a `tempfile.SpooledTemporaryFile`. We count bytes as we copy,
   and **stop at `MAX_UPLOAD_BYTES` (50 MB) → 413 `FILE_TOO_LARGE`**. We don't trust the `Content-Length` header.
   The spooled file is closed in a `finally` block, which is our temp cleanup. No uploaded file is ever kept on disk.
2. **Validate the request (no DB row yet).** Only problems with the request itself are checked here, never the file's contents:
   - missing `file` field → 400 `VALIDATION_ERROR`
   - 0 bytes → 400 `EMPTY_FILE`
   - extension not `.zip`/`.kml` (case-insensitive) → 415 `UNSUPPORTED_FILE_TYPE` (`.kmz` gets a hint)
3. **Create a `files` row with status `PENDING`, then set it to `PROCESSING`.** From here on, every content problem marks the
   row `FAILED` with `error_code`/`error_message`, and the API returns **422 with the `file_id`** so the client can still
   `GET` the failed record (D11). The first content check runs here: a `.zip` must pass `zipfile.is_zipfile` (`PK` magic),
   otherwise `CORRUPT_ZIP`. For `.kml`, the equivalent check happens in the parser (step 4). (A6: the row is created *before*
   sniffing so this matches the error matrix.)
4. **Read features.**
   - **Shapefile zip** (`shapefile_reader.py`). Before reading anything:
     - Zip-slip guard: reject any member whose name is absolute, has a drive letter, or contains `..` → `UNSAFE_ZIP`.
       We never extract to disk, so path traversal can't actually write files. The guard is defence in depth and gives a clear error.
     - Zip-bomb guard: ≤ 100 members, ≤ 200 MB total declared uncompressed size, per-member compression ratio ≤ 100:1;
       we read members with a hard byte cap as well (declared sizes can lie) → `UNSAFE_ZIP`.
     - Encrypted members → `UNSAFE_ZIP`. Ignore `__MACOSX/` and hidden files.
     - Exactly one `.shp` (nested folders allowed). 0 → `MISSING_SHAPEFILE_PART`, >1 → `MULTIPLE_SHAPEFILES` (D12).
     - Same-stem `.shx` and `.dbf` must exist → `MISSING_SHAPEFILE_PART` (message names the missing part).
     - `.prj` must exist → `MISSING_CRS` (D4); `.prj` that pyproj can't parse → `INVALID_CRS`.
     - Optional `.cpg` sets the `.dbf` text encoding (default UTF-8, `encodingErrors="replace"`).
     - Read with `shapefile.Reader(shp=BytesIO, shx=BytesIO, dbf=BytesIO)`. Any pyshp exception → `CORRUPT_SHAPEFILE`.
     - Unsupported shape types (MultiPatch) and null shapes become features with `geometry=None`, and are reported per
       feature rather than failing the file.
   - **KML** (`kml_reader.py`, ~80 lines):
     - `defusedxml.ElementTree.fromstring`. A parse error → `INVALID_KML`. Entities/DTD → `INVALID_KML` (unsafe).
     - The root local-name must be `kml`, otherwise `INVALID_KML`.
     - Walk every `Placemark` in document order (any depth of `Document`/`Folder`).
     - Geometry: `Point`, `LineString`, `LinearRing`, `Polygon` (outer + inner rings), `MultiGeometry` (recursive → Multi* if
       all parts share a type, else GeometryCollection). Anything else (`gx:Track`, `Model`) → `geometry=None` with note
       "unsupported KML geometry <tag>".
     - Coordinates: whitespace-separated `lon,lat[,alt]`. Altitude is dropped (D13). Malformed numbers give that feature
       `INVALID` instead of failing the file.
     - Properties: `name`, `description`, `ExtendedData/Data`, `SchemaData/SimpleData`, and the placemark `id` attribute if present.
5. **Zero features** → `NO_FEATURES` (422) (D12).
6. **Measure and persist.** For each feature: measure (section 4), convert the geometry to EPSG:4326 for storage, and insert the
   `features` rows. Then set the file to `COMPLETED` with `feature_count`, in **one transaction**, so a crash halfway leaves no
   half-written features.
7. **Unexpected exception** (a bug, not bad input): log the stack trace, mark the file `FAILED` with `INTERNAL_ERROR`, return 500.
   Tests feed many kinds of bad input to prove bad *input* never gets here.

Status transitions: `PENDING → PROCESSING → COMPLETED | FAILED`. They are visible in `GET /api/files/{id}/`. With synchronous
processing, a client only ever sees COMPLETED or FAILED. The states still exist so a background queue can drop in later
without changing the API (D3).

---

## 4. CRS and measurement strategy (the core)

### 4.1 Detecting the source CRS
- **Shapefile:** the `.prj` file holds ESRI WKT, which `pyproj.CRS.from_wkt` parses. We label it `EPSG:<code>` when
  `crs.to_epsg()` finds a match (verified: ESRI WKT for WGS 84 resolves to 4326), otherwise `crs.name`.
  If `.prj` is missing we reject the file. Guessing could produce areas that look plausible but are wrong (D4).
- **KML:** the OGC KML 2.2 spec fixes coordinates to WGS 84 lon/lat (EPSG:4326), so no detection is needed. Altitude is ignored.

### 4.2 Choosing the projected CRS: **per-feature Lambert Azimuthal Equal-Area (LAEA)** (D10)
For every feature:
1. Transform the source geometry to **EPSG:4326** (`Transformer.from_crs(src, 4326, always_xy=True)`).
   If the source is already 4326, this is a no-op.
2. Take the centroid in lon/lat. Computing it in degrees is fine here because it only picks the projection centre and is
   never used for measurement.
3. Build `+proj=laea +lat_0=<lat> +lon_0=<lon> +datum=WGS84 +units=m` and transform the geometry into it.
4. `area_m2 = geom.area`, `length_m = geom.length` (shapely, now in metres).

**Why LAEA:** it's an *area-preserving (equal-area)* projection: any region on the ellipsoid keeps its area after projection.
Centring it on the feature keeps distance distortion tiny near the centre.

**Where the small remaining error comes from (A5):** the projection is not the problem. The two methods draw the *edges*
differently. Shapely measures a polygon whose vertices are joined by **straight lines in the projected plane**. `pyproj.Geod`
joins the same vertices with **geodesics** (shortest paths on the ellipsoid). Those are slightly different shapes, so
their areas and lengths differ a little. The difference grows with edge length, which is why it's ~0% for a 1 km square
and ~0.08% for a 500 km box. Densifying long edges would shrink it further (future scope).

We measured each candidate against `pyproj.Geod` (WGS84 ellipsoid):

| Test feature | UTM area | EPSG:6933 area | **LAEA area** | UTM length | **LAEA length** |
|---|---|---|---|---|---|
| 1 km square @ (0°, 0°), UTM zone edge | +0.196% | 0.0000% | **0.0000%** | +0.098% | **0.0000%** |
| 1 km square @ (3°, 0°), UTM central meridian | −0.080% | 0.0000% | **0.0000%** | −0.040% | **0.0000%** |
| 50 km box @ 50°N straddling 6°E | +0.033% | +0.001% | **−0.001%** | +0.017% | **+0.0001%** |
| 100 km box @ 60°N | −0.053% | +0.010% | **−0.003%** | −0.025% | **+0.0005%** |
| 500 km box @ 20°N | +0.070% | −0.034% | **−0.076%** | +0.086% | **+0.013%** |
| 1 km square @ 87°N | no UTM zone | 0.0001% | **0.0000%** | n/a | **0.0000%** |

(Source: `scratch/exp3_local_proj.py`.)

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Per-feature UTM from centroid | Textbook, named EPSG codes | Up to 0.2% error at zone edges (scale factor 0.9996–1.001); no zone above 84°N / below 80°S, so it needs a fallback | Rejected |
| One UTM zone per file | Every feature in a file uses the same CRS | Errors grow fast for files spanning several zones | Rejected |
| Global equal-area EPSG:6933 | One CRS, accurate area | Distorts **length** (it isn't conformal), worse far from ±30° | Rejected |
| **Per-feature LAEA** | Area-preserving, smallest measured error (≤0.003% up to 100 km, 0.08% at 500 km for area; under 0.02% for length); works at the poles; no lookup tables | The CRS is a proj string, not an EPSG code (we return it in the response so it's transparent) | **Chosen** |

**Accuracy limits (documented in the README):**
- Error grows with feature size. Continent-sized features (thousands of km) can be off by more than 0.1% for length. For those, a
  geodesic calculation would be better (future scope).
- Features crossing the antimeridian (±180°) get a wrong centroid in lon/lat. This is detected (bbox width > 180°) and reported
  as `UNSUPPORTED` with a clear note rather than measured wrongly.
- Datum shifts (e.g. NAD27 → WGS84) may use PROJ's "ballpark" transform without grid files, which is accurate to metres, not
  centimetres. That's negligible for area/length.

### 4.3 Sources that are already projected
We use the **same path for every source**: source CRS → EPSG:4326 → local LAEA. There's no "use as-is" shortcut, because:
- "Projected" doesn't mean "good for measuring". Web Mercator (EPSG:3857) inflates areas by about 4× at 60°N.
- Units are handled automatically. A source in US survey feet (EPSG:2227, factor 0.3048006) ends up in metres after
  the transform, so we never multiply by unit factors by hand.

Tests prove both: a square defined in EPSG:3857 and one in EPSG:2227 both measure correctly in m².

### 4.4 What gets measured

| Geometry | Result |
|---|---|
| Polygon, MultiPolygon | `status=MEASURED`, `area_m2` (holes subtracted) |
| LineString, MultiLineString, LinearRing | `status=MEASURED`, `length_m` |
| Point, MultiPoint | `status=NOT_APPLICABLE`, no values |
| GeometryCollection (mixed KML MultiGeometry), MultiPatch, unknown KML geometry | `status=UNSUPPORTED` + note |
| Empty / null shape | `status=EMPTY` + note |
| Invalid (self-intersecting ring, ring < 4 points, NaN coordinates) | `status=INVALID` + `shapely.is_valid_reason()` text (e.g. "Self-intersection[0.5 0.5]") |
| 3D coordinates | Z dropped (pyshp already drops it; the KML reader drops altitude); measured in 2D, note "Z values ignored" |

**Invalid geometries are reported, not auto-fixed** (D14). `make_valid` turned a bowtie into two triangles with a
different area, and silently changing the user's data hides the problem. A feature-level failure never fails the whole file.

Only `MEASURED` features have numbers. Units are always `m²` / `m`, and field names include the unit (`area_m2`, `length_m`).

### 4.5 Validation approach
- **Unit tests** compare `measure.py` with `pyproj.Geod(ellps="WGS84").geometry_area_perimeter / geometry_length`, which is the
  reference value. We never assert against a round number like 1,000,000 m², because a 0.009° square is really 997,035 m² (A4).
  **Tolerance (relative to Geod): 0.05% for features up to 100 km, 0.1% for features up to 500 km.** The measured worst cases
  are 0.003% and 0.076%.
- **Integration cross-check** with PostGIS: `ST_Area(geom::geography)` and `ST_Length(geom::geography)` on the stored rows
  must match our values within 0.1%. This is a second, independent implementation, which is the honest way to use PostGIS here.
- **Negative check:** a test asserts that `area_m2` is *not* the degree area (8.1e-05), which guards against regressions.

---

## 5. Data model (PostgreSQL 16 + PostGIS 3.4)

**`files`**

| column | type | notes |
|---|---|---|
| id | UUID PK | default `uuid4`; returned as string |
| filename | VARCHAR(255) | original name, sanitised (basename only) |
| file_type | VARCHAR(16) | `shapefile` / `kml` |
| status | VARCHAR(16) | PENDING / PROCESSING / COMPLETED / FAILED |
| crs | VARCHAR(255) NULL | source CRS label, e.g. `EPSG:4326` |
| feature_count | INT NULL | |
| error_code, error_message | VARCHAR NULL | set when FAILED |
| created_at, updated_at | TIMESTAMPTZ | |

**`features`**

| column | type | notes |
|---|---|---|
| id | BIGINT PK | internal |
| file_id | UUID FK → files.id ON DELETE CASCADE | |
| feature_index | INT | 0-based order in the source file; **UNIQUE(file_id, feature_index)**, which is also the pagination index |
| geometry_type | VARCHAR(32) | e.g. `Polygon`; `null` for null shapes |
| geom | `geometry(GEOMETRY, 4326)` NULL | stored in WGS84 (GeoJSON RFC 7946 expects it); GiST index (GeoAlchemy2 default) for future spatial queries |
| properties | JSONB | attributes |
| measure_status | VARCHAR(16) | MEASURED / NOT_APPLICABLE / UNSUPPORTED / INVALID / EMPTY |
| area_m2, length_m | DOUBLE PRECISION NULL | |
| measure_note | TEXT NULL | |
| projection | VARCHAR(255) NULL | the LAEA proj string used (transparency) |

The source CRS lives on the file because every feature in a file shares it. Tables are created on startup with
`Base.metadata.create_all()`; Alembic is future scope (D15).

**Why PostGIS when measurement happens in Python (D2):** it gives a real spatial column type, server-side validation,
a spatial index for future queries, and an independent `geography` cross-check in tests. Measurement stays in Python so the
logic is unit-testable without a DB and Riya can explain it step by step. **Alternative:** SQLite with GeoJSON text. That's
simpler and needs no Docker, but has no spatial types (rejected by Riya).

---

## 6. API contract

All errors use one shape:
```json
{"error": {"code": "MISSING_CRS", "message": "The Shapefile has no .prj file, so its coordinate system is unknown.", "file_id": "3f2c..."}}
```
`file_id` is only present when a `files` row exists. FastAPI's own validation errors and 404s are re-shaped into this format.

### `POST /api/files/`
Request: `multipart/form-data`, field `file`.
```bash
curl -F "file=@parcels.zip" http://localhost:8000/api/files/
```
**201**
```json
{"id": "3f2c9a1e-...", "filename": "parcels.zip", "file_type": "shapefile", "feature_count": 3,
 "crs": "EPSG:4326", "status": "COMPLETED", "error": null, "created_at": "2026-10-09T10:00:00Z"}
```
Errors: 400 `VALIDATION_ERROR` / `EMPTY_FILE`; 413 `FILE_TOO_LARGE`; 415 `UNSUPPORTED_FILE_TYPE`;
422 `CORRUPT_ZIP` / `UNSAFE_ZIP` / `MISSING_SHAPEFILE_PART` / `MULTIPLE_SHAPEFILES` / `MISSING_CRS` / `INVALID_CRS` /
`CORRUPT_SHAPEFILE` / `INVALID_KML` / `NO_FEATURES` (all with `file_id`); 500 `INTERNAL_ERROR` (bugs only).

### `GET /api/files/{id}/`
**200** has the same `FileOut` shape as above (matches the assignment example plus `file_type`, `error`, `created_at`).
A FAILED file returns 200 with `status: "FAILED"` and `error: {"code", "message"}`.
Errors: 404 `FILE_NOT_FOUND` (also for a malformed id, so we don't leak "invalid uuid" internals).

### `GET /api/files/{id}/measurements/?limit=100&offset=0&include_geometry=false`
`limit` is 1–1000 (default 100), `offset` ≥ 0. Ordered by `feature_index`.
**200**
```json
{
  "file_id": "3f2c9a1e-...", "crs": "EPSG:4326",
  "total": 3, "limit": 100, "offset": 0,
  "summary": {"total_area_m2": 997034.8, "total_length_m": 1001.9, "measured": 2, "not_measured": 1},
  "items": [
    {"feature_index": 0, "geometry_type": "Polygon", "crs": "EPSG:4326",
     "properties": {"name": "plot A"},
     "measurement": {"status": "MEASURED", "area_m2": 997034.8, "length_m": null,
                     "projection": "+proj=laea +lat_0=0.0045 +lon_0=0.0045 +datum=WGS84 +units=m", "note": null},
     "geometry": {"type": "Polygon", "coordinates": [[[0,0],[0.009,0],[0.009,0.009],[0,0.009],[0,0]]]}},
    {"feature_index": 2, "geometry_type": "Point", "crs": "EPSG:4326", "properties": {"name": "well"},
     "measurement": {"status": "NOT_APPLICABLE", "area_m2": null, "length_m": null, "projection": null,
                     "note": "Points have no area or length"}}
  ]
}
```
`geometry` (GeoJSON, EPSG:4326) is present only when `include_geometry=true`.
Errors: 404 `FILE_NOT_FOUND`; 409 `FILE_NOT_READY` (status is not COMPLETED, message includes the status/error);
400 `VALIDATION_ERROR` (bad limit/offset).

### Small extras (each justified)
- `GET /health` → `{"status": "ok", "database": "ok"}`. Shows at a glance whether PostGIS is up. It's the first thing to check
  in an interview demo, and the M0 test uses it.
- `summary` in measurements: one SQL aggregate, and it answers the obvious question ("total area?") without paging.
- FastAPI's automatic `/docs` (Swagger) is kept and documented as the interactive way to try the API.

---

## 7. Error-handling matrix

| Situation | Detected in | HTTP | code | DB row? |
|---|---|---|---|---|
| No `file` field | FastAPI validation | 400 | VALIDATION_ERROR | no |
| Empty (0-byte) upload | upload.py | 400 | EMPTY_FILE | no |
| Wrong extension (.txt, .geojson, .kmz) | upload.py | 415 | UNSUPPORTED_FILE_TYPE | no |
| Upload > 50 MB | upload.py (streamed count) | 413 | FILE_TOO_LARGE | no |
| `.zip` that isn't a zip / corrupt zip | pipeline (after row created) / zipfile.BadZipFile | 422 | CORRUPT_ZIP | FAILED |
| Zip slip (`../x.shp`, `/etc/x`, `C:\x`) | shapefile_reader | 422 | UNSAFE_ZIP | FAILED |
| Zip bomb (ratio/total/count limits) | shapefile_reader | 422 | UNSAFE_ZIP | FAILED |
| Encrypted zip | shapefile_reader | 422 | UNSAFE_ZIP | FAILED |
| No `.shp` / missing `.shx` or `.dbf` | shapefile_reader | 422 | MISSING_SHAPEFILE_PART | FAILED |
| More than one `.shp` | shapefile_reader | 422 | MULTIPLE_SHAPEFILES | FAILED |
| Missing `.prj` | shapefile_reader | 422 | MISSING_CRS | FAILED |
| Unparseable `.prj` | crs.py | 422 | INVALID_CRS | FAILED |
| Truncated/corrupt `.shp`/`.dbf` | shapefile_reader (pyshp error) | 422 | CORRUPT_SHAPEFILE | FAILED |
| Not XML / not `<kml>` root / DTD entities | kml_reader | 422 | INVALID_KML | FAILED |
| Zero features | pipeline | 422 | NO_FEATURES | FAILED |
| Unsupported / empty / invalid / 3D geometry | measure.py | 201 (file OK) | per-feature status | COMPLETED |
| Unknown or malformed file id | api | 404 | FILE_NOT_FOUND | — |
| Measurements for a FAILED file | api | 409 | FILE_NOT_READY | — |
| Bad `limit`/`offset` | FastAPI validation | 400 | VALIDATION_ERROR | — |
| DB down | db / health | 503 | DATABASE_UNAVAILABLE | — |
| Anything else (a bug) | global handler | 500 | INTERNAL_ERROR (logged) | FAILED if row exists |

---

## 8. Test plan

All fixtures are **generated in code** (`tests/builders.py`) with pyshp, `zipfile`, and KML strings. No binary test files are committed.

| Area | Tests (examples) | Needs DB? |
|---|---|---|
| `measure.py` | 1 km equator square and 1 km line vs Geod (≤0.05%); polygon with hole = outer − hole; MultiPolygon sum; the 6 cases from table 4.2 vs Geod (≤0.05% up to 100 km, ≤0.1% at 500 km); 87°N works; result ≠ degree area; Point → NOT_APPLICABLE; GeometryCollection → UNSUPPORTED; empty → EMPTY; bowtie → INVALID with reason; Z → measured + note; antimeridian → UNSUPPORTED | no |
| `crs.py` | ESRI WKT of 4326 → "EPSG:4326"; UTM/Web-Mercator/US-feet prj recognised; garbage prj → INVALID_CRS; LAEA string centred on centroid; 3857 and 2227 squares measure correctly in m² | no |
| `upload.py` + zip safety | size limit (51 MB stream) → 413; wrong extension → 415; non-zip `.zip` → CORRUPT_ZIP; `../evil.shp`, absolute path, drive letter → UNSAFE_ZIP; high-ratio member → UNSAFE_ZIP; too many members; encrypted flag; nested folder OK; `__MACOSX` ignored; missing each part; two `.shp`; missing `.prj` | no |
| `shapefile_reader.py` | point/line/polygon/Z files; null shape; attributes incl. non-ASCII with `.cpg`; truncated `.shp` → CORRUPT_SHAPEFILE | no |
| `kml_reader.py` | Placemarks in nested Folders; Polygon with inner ring; MultiGeometry homogeneous → Multi*, mixed → GeometryCollection; ExtendedData & SimpleData; altitude dropped; bad coordinates → INVALID feature; not XML / wrong root / entity bomb → INVALID_KML; empty Document → zero features | no |
| API: upload | shapefile zip → 201 COMPLETED with correct count + CRS; KML → 201 with `EPSG:4326`; every 4xx row from the matrix returns the exact code + shape; FAILED rows retrievable by `file_id` | yes |
| API: file info | 200 shape matches assignment example; 404 for unknown and malformed id; FAILED file shows error | yes |
| API: measurements | values match Geod within 0.1%; **PostGIS `ST_Area/ST_Length(geography)` cross-check within 0.1%**; pagination (limit/offset/total, ordered); `include_geometry` toggles GeoJSON; 409 for FAILED; 400 for limit=0/5000 | yes |
| Robustness | random bytes as `.zip` and `.kml`, a 1-feature KML with 10,000 vertices: never a 500 | yes |

**Pass criteria for every milestone:** the full suite passes (`pytest -q`), `ruff check` and `ruff format --check` are clean,
measurement tolerances hold as stated, and real output is shown to Riya.

**DB marker (A2):** every test that needs PostGIS is marked `@pytest.mark.db` (the marker is registered in `pyproject.toml`).
`pytest -m "not db"` runs all the pure-logic tests with no Docker. Plain `pytest` runs everything; DB tests use a separate
`geo_test` database and **fail loudly** if PostGIS isn't running (they don't silently skip).

---

## 9. Milestones (about 2–3 days)

Every milestone follows the same loop: failing tests → implement → full suite → show output → commit locally → short walkthrough
(what / why / rejected alternative) → **pause for Riya**.

| M | Deliverables | Tests that must pass | Pause point: what I'll ask |
|---|---|---|---|
| **M0** scaffold (Day 1) | `git init`, `.gitignore`, project `.venv`, `requirements.txt` (with the pure-Python SQLAlchemy install step), `pyproject.toml`, `docker-compose.yml` + `initdb` script, `config.py`, `db.py`, `main.py` with `/health` | `test_health_ok` (needs PostGIS up) | Confirm compose works on her machine and she's happy with the layout |
| **M1** ingestion (Day 1) | `upload.py`, zip safety + part discovery, `errors.py`, `builders.py` | upload/zip-safety unit tests | Review limits (50 MB / 200 MB / 100:1 / 100 members) and error codes |
| **M2** feature extraction (Day 1–2) | `shapefile_reader.py`, `kml_reader.py`, `RawFeature`, CRS detection in `crs.py` | reader + CRS-detection tests | Review KML coverage (what we deliberately don't support) |
| **M3** CRS + measurement (Day 2) | `crs.py` (LAEA), `measure.py` | all measurement tests incl. Geod cross-checks | **Most important walkthrough:** the CRS strategy. Riya explains it back in her own words. |
| **M4** API + persistence (Day 2) | `models.py`, `schemas.py`, `pipeline.py`, `api/files.py`, error handlers | API tests + PostGIS cross-check | Check response shapes before docs are written |
| **M5** hardening (Day 3) | full error matrix, robustness tests, logging, DB-down handling, ruff clean | entire suite | Show the matrix and decide whether anything is still missing |
| **M6** docs + E2E (Day 3) | `README.md` (all sections), final `DECISIONS.md`, end-to-end demo: uvicorn + curl upload of a generated zip and KML, all 3 endpoints. **Optional, if time allows (A7):** a `Dockerfile` for the API plus an `api` service in compose, documented as an alternative to the venv | entire suite | Riya reads the README; ask for edits; ask whether to do the Dockerfile |
| **M7** interview prep (Day 3) | `INTERVIEW_NOTES.md` (20 Q&A + 3 weakest points), requirement checklist, GitHub publishing instructions | entire suite | **Ask before creating or pushing the `riyaayay` repo** |

---

## 10. Risks and unknowns

| Risk | Impact | Mitigation |
|---|---|---|
| Smart App Control blocks a native wheel we add later | Import fails | Pure-Python stack (D9); every new dependency gets an import test in `scratch/` first; SQLAlchemy installed without C extension (step documented in README) |
| Our KML parser misses KML features | Some files can't be measured | Scope documented; unknown elements → per-feature UNSUPPORTED (never a crash); KMZ/gx in future scope |
| Docker/PostGIS not available on the interviewer's machine | Integration tests and the app can't run | README covers one-command `docker compose up -d`; unit tests (most of the logic) run without a DB |
| Projection accuracy for very large or antimeridian-crossing features | Wrong numbers | Measured error table in README; antimeridian detected → UNSUPPORTED; Geod alternative noted in future scope |
| Datum shifts without grid files | Error of metres, not centimetres | Negligible for area/length; documented |
| pyshp quirks (ring orientation, encodings) | Wrong holes / mojibake | Tests for holes and `.cpg` encodings |
| Large files held in memory (synchronous) | Slow requests / memory | 50 MB cap; streaming + queue in future scope |

---

## 11. Alternatives considered (summary; details in DECISIONS.md)

| Decision | Chosen | Main alternative | Why |
|---|---|---|---|
| Framework | FastAPI | Django + DRF | Riya knows it; less framework to explain |
| Storage | PostgreSQL + PostGIS | SQLite + GeoJSON text | Real spatial types, index, independent cross-check |
| Processing | Synchronous + status field | BackgroundTasks / Celery | Simplest correct option; status keeps the queue upgrade path open |
| File reading | pyshp + own KML parser | geopandas/pyogrio (GDAL) | GDAL blocked by Smart App Control; readable code |
| Projection | Per-feature LAEA | UTM, EPSG:6933 | Area-preserving, smallest measured error, works everywhere (table 4.2) |
| Missing `.prj` | Reject | Assume 4326 | Wrong guesses give plausible-looking wrong numbers |
| Invalid geometry | Report as INVALID | `make_valid` | Auto-fixing changes the user's data |
| Geometry storage CRS | EPSG:4326 | Keep source CRS | GeoJSON output expects WGS84; one SRID per column |
| Content errors | 422 + persisted FAILED row | 4xx with nothing stored | The status field becomes meaningful; failures stay inspectable |

---

## 12. README outline

1. **Overview**: what the service does, in 3 lines
2. **Setup**: prerequisites (Python 3.11, Docker Desktop); `docker compose up -d`; create venv; normal
   `pip install -r requirements.txt`; `.env`; `uvicorn app.main:app --reload`; run tests (`pytest`, and `pytest -m "not db"`
   without Docker); `/docs`
   - **Troubleshooting: Windows Smart App Control** (A1): symptom (`DLL load failed ... Application Control policy`), cause,
     and fix (reinstall SQLAlchemy without its C extension); also the port-conflict note (55432)
3. **API**: each endpoint with curl example + response; error format + code table
4. **Architecture**: folder structure; file-processing flow (diagram); measurement flow; **CRS handling** (detection,
   LAEA choice, the measured comparison table, projected sources, accuracy limits). It must state that LAEA is
   **area-preserving** and that the remaining error against Geod comes from **straight projected edges versus geodesic
   edges**, not from the projection (A5).
5. **Design decisions**: the table from section 11, each with alternatives
6. **Testing**: how fixtures are generated; the Geod + PostGIS cross-checks and tolerance
7. **Limitations**
8. **Learning and Future Scope**
   - Learning: why degrees can't be measured; how projections trade off area/shape/distance; why "projected" ≠ "accurate"
     (Web Mercator); safe file handling (zip slip/bomb, XXE); working around a locked-down Windows machine
   - Future: background queue (Celery/RQ/arq) for big files; geodesic measurement for continent-scale features; spatial
     queries using the GiST index (bbox/intersects endpoints); KMZ/GeoJSON/GeoPackage input; auth + per-user files;
     streaming parsing; Alembic migrations; file listing/deletion; Docker image for the API; CI (GitHub Actions)

---

## Resolved questions (Riya: "defaults OK", 2026-10-09)

1. Content errors → 422 **and** a persisted FAILED row with `file_id`.
2. Zero features → reject with `NO_FEATURES`.
3. Multiple `.shp` in one zip → reject with `MULTIPLE_SHAPEFILES`.
4. Limits → 50 MB upload / 200 MB uncompressed / 100:1 ratio / 100 members / 1,000 page size.
5. Tolerance → superseded by A4 (0.05% up to 100 km, 0.1% up to 500 km).

## Amendments at approval (Riya, 2026-10-09)

| # | Amendment | Applied in |
|---|---|---|
| A1 | README setup uses a normal `pip install`; the pure-Python SQLAlchemy step moves to a Windows Smart App Control troubleshooting section | §0, §12 |
| A2 | `db` pytest marker, so `pytest -m "not db"` runs without Docker | §8 |
| A3 | Plain `def` endpoints, not `async def` | §2 |
| A4 | Assert against Geod, not 1,000,000 m²; tolerance 0.05% up to 100 km and 0.1% up to 500 km | §4.5, §8 |
| A5 | README says LAEA is area-preserving and explains the residual error as straight-versus-geodesic edges | §4.2, §12 |
| A6 | Create the `files` row before `CORRUPT_ZIP` sniffing, matching the error matrix | §3, §7 |
| A7 | Optional API `Dockerfile` in M6 if time allows | §9 |
