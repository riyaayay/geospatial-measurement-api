from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry


@dataclass
class RawFeature:
    """One feature as read from a file, before measurement. Both readers produce this.

    If the reader could not build a geometry, `geometry` is None and `problem` says why
    ("EMPTY", "UNSUPPORTED" or "INVALID"), so one bad feature never fails the whole file.
    """

    index: int
    geometry: BaseGeometry | None  # in the file's own CRS
    properties: dict
    geometry_type: str | None  # as found in the file, even when geometry is None
    problem: str | None = None
    note: str | None = None
