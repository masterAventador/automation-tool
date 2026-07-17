import json
from pathlib import Path

import pytest

from automation_tool.control_plane.bootstrap.openapi import (
    OpenApiDriftError,
    export_openapi,
    main,
    render_openapi_document,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = REPOSITORY_ROOT / "contracts/openapi/control-plane.v1.json"


def test_openapi_export_is_deterministic_and_does_not_require_database_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_DATABASE_URL", raising=False)

    first = render_openapi_document()
    second = render_openapi_document()
    schema = json.loads(first)

    assert first == second
    assert first.endswith("\n")
    assert schema["openapi"].startswith("3.")
    assert schema["paths"]["/api/v1/health"]["get"]["operationId"] == "getSystemHealth"
    assert schema["paths"]["/api/v1/version"]["get"]["operationId"] == "getSystemVersion"


def test_openapi_export_write_and_check_modes_detect_drift(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"

    export_openapi(output)
    export_openapi(output, check=True)
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(OpenApiDriftError, match="OpenAPI snapshot is out of date"):
        export_openapi(output, check=True)


def test_committed_openapi_snapshot_matches_the_application() -> None:
    assert SNAPSHOT.read_text(encoding="utf-8") == render_openapi_document()


def test_openapi_cli_writes_and_reports_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "nested/openapi.json"

    main(["--output", str(output)])
    main(["--output", str(output), "--check"])
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as captured:
        main(["--output", str(output), "--check"])

    assert captured.value.code == 2
    assert "OpenAPI snapshot is out of date" in capsys.readouterr().err
