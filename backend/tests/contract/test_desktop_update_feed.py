import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.desktop_updates import DesktopUpdateCatalog

SIGNATURE = "dHJ1c3RlZC1taW5pc2lnbi1zaWduYXR1cmU="


def release(
    version: str,
    *,
    policy: str = "optional",
    target: str = "darwin",
    arch: str = "aarch64",
    sha256: str = "a" * 64,
) -> dict[str, object]:
    return {
        "version": version,
        "channel": "stable",
        "policy": policy,
        "target": target,
        "arch": arch,
        "url": f"https://downloads.example.test/{target}/{arch}/app-{version}",
        "signature": SIGNATURE,
        "sha256": sha256,
        "sizeBytes": 1024,
        "notes": f"Release {version}",
        "publishedAt": "2026-07-22T00:00:00Z",
    }


def test_feed_returns_the_highest_matching_official_dynamic_response_without_auth() -> None:
    catalog = DesktopUpdateCatalog.from_documents(
        [
            release("1.1.0"),
            release("1.2.0", policy="forced", sha256="b" * 64),
            release("9.0.0", target="windows", arch="x86_64"),
        ]
    )
    client = TestClient(create_app(database=None, desktop_update_catalog=catalog))

    response = client.get("/desktop-updates/v1/stable/darwin/aarch64/1.0.0")

    assert response.status_code == 200
    assert response.json() == {
        "version": "1.2.0",
        "url": "https://downloads.example.test/darwin/aarch64/app-1.2.0",
        "signature": SIGNATURE,
        "notes": "Release 1.2.0",
        "pub_date": "2026-07-22T00:00:00Z",
        "update_contract": {
            "version": 1,
            "channel": "stable",
            "policy": "forced",
            "artifact": {
                "target": "darwin",
                "arch": "aarch64",
                "sha256": "b" * 64,
                "size_bytes": 1024,
            },
        },
    }
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.headers["content-type"] == "application/json"
    assert "authorization" not in response.request.headers


@pytest.mark.parametrize(
    ("path", "status_code"),
    [
        ("/desktop-updates/v1/stable/darwin/aarch64/1.2.0", 204),
        ("/desktop-updates/v1/stable/darwin/aarch64/2.0.0", 204),
        ("/desktop-updates/v1/stable/windows/aarch64/1.0.0", 204),
        ("/desktop-updates/v1/beta/darwin/aarch64/1.0.0", 204),
    ],
)
def test_feed_returns_no_content_for_current_or_unmatched_releases(
    path: str, status_code: int
) -> None:
    catalog = DesktopUpdateCatalog.from_documents([release("1.2.0")])
    response = TestClient(create_app(database=None, desktop_update_catalog=catalog)).get(path)

    assert response.status_code == status_code
    assert response.content == b""
    assert response.headers["cache-control"] == "public, max-age=60"


@pytest.mark.parametrize(
    "path",
    [
        "/desktop-updates/v1/Stable/darwin/aarch64/1.0.0",
        "/desktop-updates/v1/stable/linux/aarch64/1.0.0",
        "/desktop-updates/v1/stable/darwin/arm64/1.0.0",
        "/desktop-updates/v1/stable/darwin/aarch64/01.0.0",
        "/desktop-updates/v1/stable/darwin/aarch64/not-a-version",
    ],
)
def test_feed_rejects_noncanonical_or_unsupported_path_values(path: str) -> None:
    response = TestClient(
        create_app(database=None, desktop_update_catalog=DesktopUpdateCatalog.empty())
    ).get(path)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation"
    assert path not in response.text


@pytest.mark.parametrize(
    "mutation",
    [
        {"unknown": True},
        {"version": "01.0.0"},
        {"channel": "Stable"},
        {"policy": "silent"},
        {"target": "linux"},
        {"arch": "arm64"},
        {"url": "http://downloads.example.test/app"},
        {"url": "https://user@downloads.example.test/app"},
        {"signature": ""},
        {"sha256": "A" * 64},
        {"sizeBytes": 0},
        {"sizeBytes": 1024 * 1024 * 1024 + 1},
        {"notes": "unsafe\u202evalue"},
        {"publishedAt": "not-a-time"},
    ],
)
def test_catalog_rejects_malformed_or_private_release_documents(
    mutation: dict[str, object],
) -> None:
    document = release("1.2.0")
    document.update(mutation)

    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        DesktopUpdateCatalog.from_documents([document])


def test_feed_is_deliberately_outside_the_business_openapi_and_has_no_database_dependency() -> None:
    app = create_app(database=None, desktop_update_catalog=DesktopUpdateCatalog.empty())

    assert (
        "/desktop-updates/v1/{channel}/{target}/{arch}/{current_version}"
        not in app.openapi()["paths"]
    )
    assert app.state.desktop_update_catalog is not None
