import pytest

from app.config import MB, Settings, load_settings

LIMIT_VARIABLES = [
    "MAX_UPLOAD_MB",
    "MAX_ZIP_MEMBERS",
    "MAX_UNCOMPRESSED_MB",
    "MAX_COMPRESSION_RATIO",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in LIMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_limits_have_the_documented_defaults():
    settings = load_settings()
    assert settings.max_upload_bytes == Settings().max_upload_bytes == 50 * MB
    assert (settings.max_zip_members, settings.max_compression_ratio) == (100, 100)
    assert settings.max_uncompressed_bytes == 200 * MB


def test_every_limit_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    monkeypatch.setenv("MAX_ZIP_MEMBERS", "10")
    monkeypatch.setenv("MAX_UNCOMPRESSED_MB", "20")
    monkeypatch.setenv("MAX_COMPRESSION_RATIO", "50")

    settings = load_settings()

    assert settings.max_upload_bytes == 5 * MB
    assert settings.max_zip_members == 10
    assert settings.max_uncompressed_bytes == 20 * MB
    assert settings.max_compression_ratio == 50


@pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5"])
def test_a_bad_limit_fails_at_startup_with_a_clear_message(monkeypatch, value):
    monkeypatch.setenv("MAX_UPLOAD_MB", value)
    with pytest.raises(ValueError, match="MAX_UPLOAD_MB"):
        load_settings()
