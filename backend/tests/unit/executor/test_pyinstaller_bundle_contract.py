from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SPEC_PATH = BACKEND_ROOT / "automation-tool-executor.spec"
AUTHORING_PACKAGE = (
    BACKEND_ROOT / "src/automation_tool/executor/motion_authoring"
)
WORKFLOW_CONTRACT = (
    REPOSITORY_ROOT / "contracts/video/motion-authoring-workflow.v1.json"
)
# A versioned contract filename as it appears in the package's source, with or
# without the directory it sits in: `"video/motion-render-canvas.v1.json"` and
# `"motion-authoring-refusal.v1.json"` are both reads that must be packaged.
CONTRACT_REFERENCE = re.compile(r'"(?:[a-z]+/)?([a-z0-9-]+\.v\d+\.json)"')
# One entry of the spec's `motion_authoring_resources` list.
SPEC_RESOURCE = re.compile(r'"((?:contracts|vendor)/[^"]+)"')


def test_pyinstaller_and_playwright_are_locked_in_their_runtime_scopes() -> None:
    project = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    development_dependencies = project["dependency-groups"]["dev"]
    executor_dependencies = project["dependency-groups"]["executor"]

    assert any(dependency.startswith("pyinstaller") for dependency in development_dependencies)
    assert "pyinstaller" not in project["project"]["dependencies"]
    assert "playwright==1.61.0" in executor_dependencies
    assert not any(
        dependency.startswith("playwright") for dependency in project["project"]["dependencies"]
    )
    assert not any(dependency.startswith("playwright") for dependency in development_dependencies)

    # fontTools and Brotli have two production roles. The catalog build uses the
    # exact pair to produce the locked WOFF2 bytes, while the local editing
    # executor reads packaged face cmaps at runtime to choose a glyph-complete
    # fallback. Both groups therefore pin the same versions; letting either side
    # float would make build output or runtime font acceptance environment-bound.
    catalog_build_dependencies = project["dependency-groups"]["catalog-build"]
    locked_font_dependencies = ["brotli==1.2.0", "fonttools==4.63.0"]
    assert sorted(
        dependency
        for dependency in executor_dependencies
        if dependency.startswith(("brotli", "fonttools"))
    ) == locked_font_dependencies
    assert sorted(catalog_build_dependencies) == locked_font_dependencies
    assert not any(
        dependency.startswith(("brotli", "fonttools"))
        for dependency in project["project"]["dependencies"]
    )
    assert project["tool"]["uv"]["default-groups"] == ["catalog-build", "dev", "executor"]
    assert (
        project["project"]["scripts"]["automation-tool-build-executor-manifest"]
        == "automation_tool.executor.package_manifest:main"
    )
    assert (
        project["project"]["scripts"]["automation-tool-build-macos-executor"]
        == "automation_tool.executor.macos_candidate:main"
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
    assert "remove_direct_url_metadata" in source


def test_executor_spec_packages_the_closed_authoring_refusal_contract() -> None:
    source = SPEC_PATH.read_text(encoding="utf-8")

    assert '"contracts/video/motion-authoring-refusal.v1.json"' in source


def _spec_packaged_basenames() -> set[str]:
    spec = SPEC_PATH.read_text(encoding="utf-8")
    return {Path(entry).name for entry in SPEC_RESOURCE.findall(spec)}


def test_the_spec_packages_every_contract_the_authoring_agent_reads() -> None:
    """The spec's resource list is hand-written; this makes forgetting it loud.

    `motion_authoring_resources` is a list of paths typed out by hand, and the
    agent reads those files at startup — so a new read that nobody adds there
    produces a package that cannot start the one-sentence path, while every
    test still passes, because tests run from the repository checkout where the
    file is simply present. That is precisely the shape this project has been
    burned by: the acceptance and the shipped artifact disagreeing about what
    exists. Deriving the expected list from the sources instead of naming one
    contract at a time means the next read is covered without anyone
    remembering to cover it.
    """
    referenced: set[str] = set()
    for source in sorted(AUTHORING_PACKAGE.glob("*.py")):
        referenced |= set(CONTRACT_REFERENCE.findall(source.read_text(encoding="utf-8")))
    assert referenced, "no contract reads found; the detection pattern has rotted"

    missing = sorted(referenced - _spec_packaged_basenames())
    assert missing == [], (
        f"the authoring agent reads {missing} but automation-tool-executor.spec "
        "does not package them; the built Executor would fail to start the "
        "one-sentence path while every repository test still passes"
    )


def test_the_spec_packages_every_file_the_locked_workflow_reference_pins() -> None:
    """The same hole, one level down.

    `load_locked_authoring_workflow` reads the files the workflow contract
    pins, verifying each against a digest. A file added to that contract but
    not to the spec fails closed at startup — correctly, but only for the user.
    """
    contract = json.loads(WORKFLOW_CONTRACT.read_text(encoding="utf-8"))
    pinned = {Path(entry["path"]).name for entry in contract["files"]}
    assert pinned, "the workflow contract pins no files"

    missing = sorted(pinned - _spec_packaged_basenames())
    assert missing == [], (
        f"the locked workflow reference pins {missing} but the spec does not "
        "package them"
    )


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
    assert "run_p9_01_acceptance.py" in source
