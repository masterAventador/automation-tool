from __future__ import annotations

import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SPEC_PATH = BACKEND_ROOT / "automation-tool-executor.spec"


def test_pyinstaller_and_playwright_are_locked_in_their_runtime_scopes() -> None:
    project = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    development_dependencies = project["dependency-groups"]["dev"]

    assert any(dependency.startswith("pyinstaller") for dependency in development_dependencies)
    assert "pyinstaller" not in project["project"]["dependencies"]
    assert "playwright" in project["project"]["dependencies"]
    assert "playwright" not in development_dependencies
    assert (
        project["project"]["scripts"]["automation-tool-build-executor-manifest"]
        == "automation_tool.executor.package_manifest:main"
    )


def test_executor_spec_builds_a_console_onedir_from_the_formal_module_entry() -> None:
    source = SPEC_PATH.read_text(encoding="utf-8")

    assert 'source_root = backend_root / "src"' in source
    assert "automation_tool/executor/__main__.py" in source
    assert "exclude_binaries=True" in source
    assert "COLLECT(" in source
    assert 'name="automation-tool-executor"' in source
    assert "console=True" in source
    assert 'collect_all("playwright")' in source
    assert '"automation_tool.executor.browser_runtime"' in source


def test_every_executor_spec_materializes_safe_internal_pyinstaller_symlinks() -> None:
    spec_paths = (
        SPEC_PATH,
        BACKEND_ROOT / "tests/fixtures/automation-tool-executor-b515.spec",
        BACKEND_ROOT / "tests/fixtures/automation-tool-executor-h816f.spec",
    )

    for spec_path in spec_paths:
        source = spec_path.read_text(encoding="utf-8")
        assert "materialize_internal_package_symlinks" in source
        assert "materialize_internal_package_symlinks(Path(bundle.name))" in source


def test_desktop_ci_smokes_the_executor_bundle_on_both_supported_platforms() -> None:
    source = (REPOSITORY_ROOT / ".github/workflows/desktop.yml").read_text(encoding="utf-8")

    assert "executor-bundle:" in source
    assert "runner: [macos-latest, windows-latest]" in source
    assert "uv sync --locked --dev" in source
    assert "test_package_manifest.py" in source
    assert "test_executor_manifest_cli.py" in source
    assert "test_pyinstaller_bundle.py" in source
    assert "test_packaged_browser_probe.py" in source
    assert "dtolnay/rust-toolchain" in source
    assert "frontend/src-tauri -> target" in source
    assert "backend/automation-tool-executor.spec" in source
    assert "contracts/fixtures/executor-package-v1/**" in source
