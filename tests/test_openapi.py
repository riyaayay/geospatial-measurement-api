"""The generated API docs (/docs, /openapi.json) must describe the errors we really return."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ErrorResponse

ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


def operations(spec: dict):
    for path, methods in spec["paths"].items():
        if path.startswith("/api/files"):
            for method, operation in methods.items():
                yield f"{method.upper()} {path}", operation


def test_fastapis_default_validation_error_is_not_documented(spec):
    # We never return {"detail": [...]}: validation errors are 400 in our own error shape.
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    for name, operation in operations(spec):
        schemas = [
            r.get("content", {}).get("application/json", {}).get("schema")
            for r in operation["responses"].values()
        ]
        assert {"$ref": "#/components/schemas/HTTPValidationError"} not in schemas, name


def test_every_error_response_uses_the_error_shape(spec):
    for name, operation in operations(spec):
        errors = {code: r for code, r in operation["responses"].items() if not code.startswith("2")}
        assert "default" in errors, name
        for code, response in errors.items():
            assert response["content"]["application/json"]["schema"] == ERROR_SCHEMA, (name, code)


@pytest.mark.parametrize(
    ("operation_name", "codes"),
    [
        ("POST /api/files/", {"400", "413", "415", "422", "503"}),
        ("GET /api/files/{file_id}/", {"404", "503"}),
        ("GET /api/files/{file_id}/measurements/", {"400", "404", "409", "503"}),
    ],
)
def test_each_endpoint_documents_its_error_codes(spec, operation_name, codes):
    operation = dict(operations(spec))[operation_name]
    assert codes <= set(operation["responses"])


def test_error_schema_matches_a_real_error_response():
    # 415 is decided before the database is touched, so this runs without PostGIS.
    with TestClient(app) as client:
        response = client.post("/api/files/", files={"file": ("notes.txt", b"hello")})
    assert response.status_code == 415
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "UNSUPPORTED_FILE_TYPE"
    assert body.error.file_id is None
