import os
from dataclasses import dataclass

# Matches docker-compose.yml, so the app works out of the box without any env vars.
DEFAULT_DATABASE_URL = "postgresql+psycopg://geo:geo@localhost:55432/geo"

MB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    max_upload_bytes: int = 50 * MB
    # Zip-bomb limits: a small zip can expand to gigabytes, so we check before reading members.
    max_zip_members: int = 100
    max_uncompressed_bytes: int = 200 * MB
    max_compression_ratio: int = 100


def load_settings() -> Settings:
    """Read settings from environment variables; anything not set keeps its default above."""
    defaults = Settings()
    upload_mb = _positive_int("MAX_UPLOAD_MB", defaults.max_upload_bytes // MB)
    uncompressed_mb = _positive_int("MAX_UNCOMPRESSED_MB", defaults.max_uncompressed_bytes // MB)
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        max_upload_bytes=upload_mb * MB,
        max_zip_members=_positive_int("MAX_ZIP_MEMBERS", defaults.max_zip_members),
        max_uncompressed_bytes=uncompressed_mb * MB,
        max_compression_ratio=_positive_int(
            "MAX_COMPRESSION_RATIO", defaults.max_compression_ratio
        ),
    )


def _positive_int(name: str, default: int) -> int:
    # A typo in a limit should stop the server at startup, not silently fall back to a default.
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.isdigit() or int(value) == 0:
        raise ValueError(f"{name} must be a whole number greater than 0, got {value!r}.")
    return int(value)


settings = load_settings()
