"""Checks on the uploaded file itself, before any geospatial parsing happens."""

import io
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import BinaryIO

from app.config import Settings, settings
from app.errors import AppError

FILE_TYPES = {".zip": "shapefile", ".kml": "kml", ".kmz": "kmz"}

ENCRYPTED_FLAG = 0x1
# Below this size a high compression ratio is normal (a .dbf padded with spaces can exceed 100:1),
# and such a member can't expand into anything dangerous anyway.
RATIO_CHECK_MIN_BYTES = 1024 * 1024


@dataclass
class ShapefileParts:
    """The raw files that make up one Shapefile, read from the zip into memory."""

    name: str
    shp: bytes
    shx: bytes
    dbf: bytes
    prj: str
    cpg: str | None


def clean_filename(filename: str | None) -> str:
    # PureWindowsPath splits on both "/" and "\", so "../x.kml" and "C:\dir\x.kml" become "x.kml".
    return PureWindowsPath(filename or "").name[:255]


def detect_file_type(filename: str) -> str:
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix in FILE_TYPES:
        return FILE_TYPES[suffix]
    raise AppError(
        "UNSUPPORTED_FILE_TYPE",
        f"Unsupported file type '{suffix or 'none'}'. "
        "Upload a .zip containing a Shapefile, a .kml file or a .kmz file.",
        status_code=415,
    )


def read_upload(fileobj: BinaryIO, max_bytes: int = settings.max_upload_bytes) -> bytes:
    # Read one byte past the limit: if we get it, the file is too big. The Content-Length
    # header is not trusted because the client controls it.
    data = fileobj.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise file_too_large(max_bytes)
    if not data:
        raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)
    return data


def file_too_large(max_bytes: int) -> AppError:
    if max_bytes >= 1024 * 1024:
        limit = f"{max_bytes // (1024 * 1024)} MB"
    else:
        limit = f"{max_bytes} bytes"
    return AppError(
        "FILE_TOO_LARGE", f"The file is larger than the {limit} limit.", status_code=413
    )


def extract_shapefile_parts(data: bytes, config: Settings = settings) -> ShapefileParts:
    """Open a Shapefile zip safely and return its parts."""
    with _open_safe_zip(data, config) as (zf, members):
        return _read_parts(zf, members)


def extract_kml_from_kmz(data: bytes, config: Settings = settings) -> bytes:
    """Open a KMZ (a zip holding a KML, plus images) safely and return the KML's bytes.

    Google Earth's convention is a root-level doc.kml; otherwise we accept a single .kml anywhere.
    Several .kml files and no doc.kml would mean guessing which one is the map, so we refuse.
    (DECISIONS D37)
    """
    with _open_safe_zip(data, config) as (zf, members):
        kml_files = [
            m for m in members if not _is_junk(m.filename) and m.filename.lower().endswith(".kml")
        ]
        doc_kml = next((m for m in kml_files if m.filename.lower() == "doc.kml"), None)
        if doc_kml is not None:
            return zf.read(doc_kml)
        if not kml_files:
            raise AppError("MISSING_KML", "The KMZ file does not contain a .kml file.")
        if len(kml_files) > 1:
            names = ", ".join(m.filename for m in kml_files)
            raise AppError(
                "MULTIPLE_KML",
                f"The KMZ contains several .kml files ({names}) and no doc.kml, "
                "so it is unclear which one to read. Upload one .kml file instead.",
            )
        return zf.read(kml_files[0])


@contextmanager
def _open_safe_zip(
    data: bytes, config: Settings
) -> Iterator[tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]]:
    """Open a zip and run the safety checks before anything is read from it.

    Shared by Shapefile zips and KMZ files. Nothing is extracted to disk: members are read into
    memory, so a malicious path inside the zip ("zip slip") can never write a file. We still
    reject such paths with a clear error.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            _check_zip_is_safe(members, config)
            yield zf, members
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
        # Also covers errors while reading members inside the `with` block of the caller.
        # NotImplementedError: a compression method Python can't read (e.g. Deflate64).
        raise AppError("CORRUPT_ZIP", f"The zip file is damaged or unreadable ({exc}).") from exc


def _unsafe(message: str) -> AppError:
    return AppError("UNSAFE_ZIP", message)


def _check_zip_is_safe(members: list[zipfile.ZipInfo], config: Settings) -> None:
    if len(members) > config.max_zip_members:
        raise _unsafe(
            f"The zip contains {len(members)} files; the limit is {config.max_zip_members}."
        )
    total_size = 0
    for m in members:
        if _is_unsafe_path(m.filename):
            raise _unsafe(f"The zip contains an unsafe path: '{m.filename}'.")
        if m.flag_bits & ENCRYPTED_FLAG:
            raise _unsafe("Password-protected zip files are not supported.")
        # file_size is what the zip *declares*; zipfile refuses to return more than that
        # (CRC error), so checking declared sizes is enough.
        if (
            m.file_size >= RATIO_CHECK_MIN_BYTES
            and m.file_size > config.max_compression_ratio * max(m.compress_size, 1)
        ):
            raise _unsafe(
                f"'{m.filename}' has a suspiciously high compression ratio (possible zip bomb)."
            )
        total_size += m.file_size
    if total_size > config.max_uncompressed_bytes:
        limit_mb = config.max_uncompressed_bytes // (1024 * 1024)
        raise _unsafe(f"The zip would expand to more than {limit_mb} MB.")


def _is_unsafe_path(name: str) -> bool:
    path = name.replace("\\", "/")
    return (
        path.startswith("/")
        or PureWindowsPath(path).drive != ""  # "C:/..." or "//server/share"
        or ".." in path.split("/")
    )


def _is_junk(name: str) -> bool:
    # macOS Finder adds "__MACOSX/._name" resource forks and ".DS_Store"; they are not data.
    parts = name.replace("\\", "/").split("/")
    return parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts)


def _read_parts(zf: zipfile.ZipFile, members: list[zipfile.ZipInfo]) -> ShapefileParts:
    real = [m for m in members if not _is_junk(m.filename)]
    shp_members = [m for m in real if m.filename.lower().endswith(".shp")]
    if not shp_members:
        hint = ""
        if any(m.filename.lower().endswith(".kml") for m in real):
            hint = " It contains a KML file: rename the zip to .kmz, or upload the .kml itself."
        raise AppError("MISSING_SHAPEFILE_PART", f"The zip does not contain a .shp file.{hint}")
    if len(shp_members) > 1:
        names = ", ".join(m.filename for m in shp_members)
        raise AppError(
            "MULTIPLE_SHAPEFILES",
            f"The zip contains more than one Shapefile ({names}). Upload one Shapefile per zip.",
        )

    shp = shp_members[0]
    stem = shp.filename[: -len(".shp")]  # keeps the folder, e.g. "export/parcels"
    name = PurePosixPath(stem).name
    # Sibling files must share the stem; GIS tools are inconsistent about case.
    by_name = {m.filename.lower(): m for m in real}

    def sibling(ext: str) -> zipfile.ZipInfo | None:
        return by_name.get(stem.lower() + ext)

    missing = [name + ext for ext in (".shx", ".dbf") if sibling(ext) is None]
    if missing:
        raise AppError(
            "MISSING_SHAPEFILE_PART",
            f"The Shapefile is incomplete: missing {', '.join(missing)}. "
            "A Shapefile needs .shp, .shx and .dbf files with the same name.",
        )
    prj = sibling(".prj")
    if prj is None:
        raise AppError(
            "MISSING_CRS",
            f"The Shapefile has no {name}.prj file, so its coordinate system is unknown. "
            "Add the .prj file exported by your GIS tool.",
        )
    cpg = sibling(".cpg")

    return ShapefileParts(
        name=name,
        shp=zf.read(shp),
        shx=zf.read(sibling(".shx")),
        dbf=zf.read(sibling(".dbf")),
        prj=zf.read(prj).decode("utf-8", errors="replace"),
        cpg=zf.read(cpg).decode("ascii", errors="replace").strip() if cpg else None,
    )
