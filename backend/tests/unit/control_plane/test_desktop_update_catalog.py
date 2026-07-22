import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.application.desktop_updates import (
    DesktopUpdateCatalog,
    DesktopUpdateRelease,
    _SemVersion,
    validate_canonical_semver,
)
from automation_tool.control_plane.bootstrap.desktop_updates import (
    desktop_update_catalog_from_environment,
)

CATALOG_ENV = "AUTOMATION_TOOL_DESKTOP_UPDATE_CATALOG_FILE"


def release(version: str, **overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "version": version,
        "channel": "stable",
        "policy": "optional",
        "target": "darwin",
        "arch": "aarch64",
        "url": f"https://downloads.example.test/app-{version}",
        "signature": "dHJ1c3RlZA==",
        "sha256": "a" * 64,
        "sizeBytes": 4,
    }
    document.update(overrides)
    return document


def selected_version(catalog: DesktopUpdateCatalog, current: str) -> str | None:
    selected = catalog.find_update(
        channel="stable",
        target="darwin",
        arch="aarch64",
        current_version=current,
    )
    return None if selected is None else selected.version


def test_semver_selection_follows_the_complete_prerelease_precedence_chain() -> None:
    versions = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    for lower, higher in pairwise(versions):
        assert _SemVersion.parse(lower) < _SemVersion.parse(higher)
        assert _SemVersion.parse(higher) > _SemVersion.parse(lower)
    assert _SemVersion.parse("1.0.0+build.01") == _SemVersion.parse("1.0.0+build.02")
    assert _SemVersion.parse("2.0.0") > _SemVersion.parse("1.999.999")
    assert _SemVersion.parse("1.0.0").__lt__(object()) is NotImplemented
    assert _SemVersion.parse("1.0.0") != object()

    catalog = DesktopUpdateCatalog.from_documents([release(version) for version in versions])
    assert selected_version(catalog, "1.0.0-alpha") == "1.0.0"
    assert selected_version(catalog, "1.0.0") is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1",
        "1.0",
        "01.0.0",
        "1.00.0",
        "1.0.00",
        "1.0.0+",
        "1.0.0+bad!identifier",
        "1.0.0-",
        "1.0.0-01",
        "1.0.0-alpha..1",
        "1.0.0-alpha_1",
    ],
)
def test_semver_rejects_every_noncanonical_core_and_identifier(value: str) -> None:
    with pytest.raises(ValueError, match="invalid semantic version"):
        validate_canonical_semver(value)

    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        DesktopUpdateCatalog.from_documents([release(value)])


def test_optional_response_fields_are_omitted_and_strict_text_time_and_signature_are_bounded() -> (
    None
):
    parsed = DesktopUpdateRelease.model_validate(release("1.0.0", publishedAt=None))
    assert parsed.feed_document() == {
        "version": "1.0.0",
        "url": "https://downloads.example.test/app-1.0.0",
        "signature": "dHJ1c3RlZA==",
        "update_contract": {
            "version": 1,
            "channel": "stable",
            "policy": "optional",
            "artifact": {
                "target": "darwin",
                "arch": "aarch64",
                "sha256": "a" * 64,
                "size_bytes": 4,
            },
        },
    }

    for mutation in [
        {"notes": ""},
        {"notes": "x" * 8193},
        {"notes": "unsafe\x00text"},
        {"notes": "unsafe\x7ftext"},
        {"notes": "unsafe\u2066text"},
        {"publishedAt": "2026-07-22T00:00:00"},
        {"signature": "x" * 4097},
        {"signature": "not base64!"},
        {"url": "https:///missing-host"},
        {"url": "https://downloads.example.test/app#fragment"},
        {"url": "ftp://downloads.example.test/app"},
    ]:
        with pytest.raises(ValueError, match="desktop update catalog rejected"):
            DesktopUpdateCatalog.from_documents([release("1.0.0", **mutation)])


def test_duplicate_release_identity_and_non_mapping_documents_are_rejected() -> None:
    duplicate = release("1.0.0")
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        DesktopUpdateCatalog.from_documents([duplicate, duplicate.copy()])
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        DesktopUpdateCatalog.from_documents([release("1.0.0+build.1"), release("1.0.0+build.2")])
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        DesktopUpdateCatalog.from_documents(["not-a-document"])  # type: ignore[list-item]


def write_catalog(path: Path, documents: object) -> None:
    path.write_text(json.dumps(documents), encoding="utf-8")


def test_environment_loader_defaults_empty_and_loads_one_absolute_bounded_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CATALOG_ENV, raising=False)
    assert selected_version(desktop_update_catalog_from_environment(), "0.1.0") is None

    catalog_path = tmp_path / "desktop-updates.json"
    write_catalog(catalog_path, [release("1.0.0")])
    monkeypatch.setenv(CATALOG_ENV, str(catalog_path))
    assert selected_version(desktop_update_catalog_from_environment(), "0.1.0") == "1.0.0"


@pytest.mark.parametrize(
    "document",
    [
        {},
        ["not-a-mapping"],
        [release("01.0.0")],
    ],
)
def test_environment_loader_rejects_invalid_json_shapes_or_release_contracts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document: object
) -> None:
    catalog_path = tmp_path / "desktop-updates.json"
    write_catalog(catalog_path, document)
    monkeypatch.setenv(CATALOG_ENV, str(catalog_path))

    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()


def test_environment_loader_rejects_relative_symlink_directory_oversize_and_io_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    relative = Path("relative-catalog.json")
    monkeypatch.setenv(CATALOG_ENV, str(relative))
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()

    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.setenv(CATALOG_ENV, str(directory))
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()

    real = tmp_path / "real.json"
    write_catalog(real, [])
    linked = tmp_path / "linked.json"
    linked.symlink_to(real)
    monkeypatch.setenv(CATALOG_ENV, str(linked))
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (64 * 1024 + 1))
    monkeypatch.setenv(CATALOG_ENV, str(oversized))
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    monkeypatch.setenv(CATALOG_ENV, str(malformed))
    with pytest.raises(ValueError, match="desktop update catalog rejected"):
        desktop_update_catalog_from_environment()

    readable = tmp_path / "readable.json"
    write_catalog(readable, [])
    monkeypatch.setenv(CATALOG_ENV, str(readable))

    def fail_read(_path: Path) -> bytes:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(ValueError, match=r"^desktop update catalog rejected$") as rejected:
        desktop_update_catalog_from_environment()
    assert "private" not in str(rejected.value)


def test_semver_validator_rejects_non_string_runtime_input() -> None:
    with pytest.raises(ValueError, match="invalid semantic version"):
        validate_canonical_semver(1)  # type: ignore[arg-type]


def test_catalog_constructor_wraps_a_type_error_without_reflecting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(_cls: type[DesktopUpdateRelease], _document: Any) -> DesktopUpdateRelease:
        raise TypeError("private catalog detail")

    monkeypatch.setattr(DesktopUpdateRelease, "model_validate", classmethod(reject))
    with pytest.raises(ValueError, match=r"^desktop update catalog rejected$") as rejected:
        DesktopUpdateCatalog.from_documents([release("1.0.0")])
    assert "private" not in str(rejected.value)
