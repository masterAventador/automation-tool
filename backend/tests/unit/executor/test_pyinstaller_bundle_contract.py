from __future__ import annotations

import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SPEC_PATH = BACKEND_ROOT / "automation-tool-executor.spec"


def test_pyinstaller_is_locked_as_a_development_only_dependency() -> None:
    project = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    development_dependencies = project["dependency-groups"]["dev"]

    assert any(dependency.startswith("pyinstaller") for dependency in development_dependencies)
    assert "pyinstaller" not in project["project"]["dependencies"]
    assert "playwright" not in project["project"]["dependencies"]
    assert "playwright" not in development_dependencies


def test_executor_spec_builds_a_console_onedir_from_the_formal_module_entry() -> None:
    source = SPEC_PATH.read_text(encoding="utf-8")

    assert 'source_root = backend_root / "src"' in source
    assert "automation_tool/executor/__main__.py" in source
    assert "exclude_binaries=True" in source
    assert "COLLECT(" in source
    assert 'name="automation-tool-executor"' in source
    assert "console=True" in source
    assert "playwright" not in source.lower()


def test_desktop_ci_smokes_the_executor_bundle_on_both_supported_platforms() -> None:
    source = (REPOSITORY_ROOT / ".github/workflows/desktop.yml").read_text(encoding="utf-8")

    assert "executor-bundle:" in source
    assert "runner: [macos-latest, windows-latest]" in source
    assert "uv sync --locked --dev" in source
    assert "test_pyinstaller_bundle.py" in source
    assert "backend/automation-tool-executor.spec" in source
