from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.errors import AppError, register_error_handlers

# A tiny app just for these tests, so the error format is checked without the database.
demo = FastAPI()
register_error_handlers(demo)


@demo.get("/boom")
def boom():
    raise AppError("MISSING_CRS", "No .prj file.", status_code=422, file_id="abc")


@demo.get("/needs-limit")
def needs_limit(limit: int = Query(ge=1)):
    return {"limit": limit}


client = TestClient(demo)


def test_app_error_uses_the_standard_error_shape():
    response = client.get("/boom")

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "MISSING_CRS", "message": "No .prj file.", "file_id": "abc"}
    }


def test_request_validation_errors_become_400_validation_error():
    response = client.get("/needs-limit?limit=0")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "limit" in body["error"]["message"]
    assert "file_id" not in body["error"]
