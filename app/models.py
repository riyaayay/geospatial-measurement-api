import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DateTime,
    Double,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GeoFile(Base):
    """One uploaded file. Status: PENDING -> PROCESSING -> COMPLETED or FAILED."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "shapefile", "kml" or "kmz"
    # Of the uploaded bytes (the zip itself for a Shapefile). The hash proves which exact file was
    # measured. Nullable only because rows created before these columns existed don't have them.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    crs: Mapped[str | None] = mapped_column(String(255))  # source CRS label, e.g. "EPSG:4326"
    feature_count: Mapped[int | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Feature(Base):
    __tablename__ = "features"
    # One row per feature per file; this index also serves "ORDER BY feature_index" paging.
    __table_args__ = (UniqueConstraint("file_id", "feature_index"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    feature_index: Mapped[int]  # 0-based position in the source file
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    # Stored in WGS 84 whatever the source CRS: GeoJSON output expects it, and one SRID per
    # column keeps PostGIS functions simple. GeoAlchemy2 also adds a GiST spatial index.
    geom = mapped_column(Geometry("GEOMETRY", srid=4326), nullable=True)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    measure_status: Mapped[str] = mapped_column(String(16))
    area_m2: Mapped[float | None] = mapped_column(Double)
    length_m: Mapped[float | None] = mapped_column(Double)
    measure_note: Mapped[str | None] = mapped_column(Text)
    projection: Mapped[str | None] = mapped_column(String(255))
