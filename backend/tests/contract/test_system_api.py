from uuid import UUID

from fastapi.testclient import TestClient

from automation_tool import __version__
from automation_tool.control_plane import create_app
from automation_tool.protocol.version import (
    API_VERSION,
    CURRENT_EXECUTOR_PROTOCOL,
    MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
)


def test_health_contract_is_small_deterministic_and_not_cacheable() -> None:
    response = TestClient(create_app(database=None)).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "control-plane",
        "version": __version__,
    }
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"])


def test_version_contract_declares_api_and_executor_protocol_compatibility() -> None:
    response = TestClient(create_app(database=None)).get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {
        "service": "control-plane",
        "version": __version__,
        "apiVersion": API_VERSION,
        "executorProtocol": {
            "current": CURRENT_EXECUTOR_PROTOCOL,
            "minimumCompatible": MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
            "maximumCompatible": MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
        },
    }
    assert response.headers["cache-control"] == "no-store"


def test_system_routes_are_get_only_and_framework_error_is_normalized() -> None:
    client = TestClient(create_app(database=None))

    for path in ("/api/v1/health", "/api/v1/version"):
        response = client.post(path)
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "request_rejected"
        assert response.json()["error"]["retryable"] is False


def test_openapi_contains_the_versioned_system_contract_only_once() -> None:
    schema = create_app(database=None).openapi()

    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/version" in schema["paths"]
    assert "/health" not in schema["paths"]
    assert "/version" not in schema["paths"]
    assert set(schema["paths"]["/api/v1/health"]) == {"get"}
    assert set(schema["paths"]["/api/v1/version"]) == {"get"}
