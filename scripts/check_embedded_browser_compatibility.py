#!/usr/bin/env python3
"""Fail closed when embedded Chromium metadata drifts from Playwright."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NoReturn

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    REPOSITORY_ROOT / "contracts/browser/embedded-chromium-compatibility.v1.json"
)
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/browser/fixtures"


class CompatibilityError(ValueError):
    """Raised when a compatibility or component record is not exact."""


def fail(message: str) -> NoReturn:
    raise CompatibilityError(message)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        fail(f"required regular JSON file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"invalid UTF-8 JSON: {path}: {error}")
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def require_exact_keys(
    value: Mapping[str, object], expected: set[str], name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        fail(f"{name} keys drifted: missing={missing}, extra={extra}")


def require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        fail(f"{name} must be an object")
    return value


def validate_contract(contract: Mapping[str, object]) -> None:
    require_exact_keys(
        contract,
        {
            "schema_version",
            "policy",
            "verified_at",
            "production_runtime",
            "test_harness",
            "supported_targets",
            "sources",
        },
        "contract",
    )
    if contract["schema_version"] != 1 or contract["policy"] != "fail_closed":
        fail("unsupported contract schema or policy")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(contract["verified_at"])):
        fail("verified_at must be an ISO date")

    runtime = require_mapping(contract["production_runtime"], "production_runtime")
    require_exact_keys(
        runtime,
        {"playwright_python", "driver_browsers_json_sha256", "chromium"},
        "production_runtime",
    )
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(runtime["playwright_python"])):
        fail("playwright_python must be an exact semantic version")
    if not re.fullmatch(r"[0-9a-f]{64}", str(runtime["driver_browsers_json_sha256"])):
        fail("driver_browsers_json_sha256 must be a lowercase SHA-256")
    chromium = require_mapping(runtime["chromium"], "production_runtime.chromium")
    require_exact_keys(
        chromium,
        {"name", "title", "browser_version", "revision", "install_by_default"},
        "production_runtime.chromium",
    )
    if chromium["name"] != "chromium" or chromium["install_by_default"] is not True:
        fail("production Chromium entry is not installable by default")
    if not re.fullmatch(r"\d+(?:\.\d+){3}", str(chromium["browser_version"])):
        fail("Chromium browser_version must be complete")
    if not re.fullmatch(r"\d+", str(chromium["revision"])):
        fail("Chromium revision must be numeric")

    harness = require_mapping(contract["test_harness"], "test_harness")
    require_exact_keys(
        harness,
        {"playwright_node", "production_browser_authority"},
        "test_harness",
    )
    if harness["production_browser_authority"] is not False:
        fail("Node test harness must not select the production Chromium")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(harness["playwright_node"])):
        fail("playwright_node must be an exact semantic version")

    sources = require_mapping(contract["sources"], "sources")
    require_exact_keys(
        sources,
        {"python_release", "node_release", "chromium_metadata"},
        "sources",
    )
    if sources["chromium_metadata"] != "playwright/driver/package/browsers.json":
        fail("Chromium metadata authority differs from the installed Playwright driver")

    targets = contract["supported_targets"]
    if not isinstance(targets, list) or len(targets) != 3:
        fail("supported_targets must contain the three release targets")
    expected_targets = {
        ("macos-arm64", "macos", "arm64"),
        ("macos-x86_64", "macos", "x86_64"),
        ("windows-x86_64", "windows", "x86_64"),
    }
    actual_targets: set[tuple[object, object, object]] = set()
    for target_value in targets:
        target = require_mapping(target_value, "supported_targets item")
        require_exact_keys(target, {"id", "os", "arch"}, "supported_targets item")
        actual_targets.add((target["id"], target["os"], target["arch"]))
    if actual_targets != expected_targets:
        fail(f"supported release targets drifted: {sorted(actual_targets, key=str)}")


def find_locked_version(lock: Mapping[str, object], package_name: str) -> str:
    packages = lock.get("package")
    if not isinstance(packages, list):
        fail("backend uv.lock has no package list")
    matches = [
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == package_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
        fail(f"backend uv.lock must contain exactly one {package_name} version")
    return str(matches[0]["version"])


def validate_dependency_locks(contract: Mapping[str, object]) -> None:
    runtime = require_mapping(contract["production_runtime"], "production_runtime")
    expected_python = str(runtime["playwright_python"])
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = pyproject["project"]["dependencies"]
    dependency_groups = require_mapping(
        pyproject.get("dependency-groups"), "backend dependency-groups"
    )
    executor_dependencies = dependency_groups.get("executor")
    expected_dependency = f"playwright=={expected_python}"
    if (
        not isinstance(executor_dependencies, list)
        or [
            dependency
            for dependency in executor_dependencies
            if isinstance(dependency, str) and dependency.startswith("playwright")
        ]
        != [expected_dependency]
    ):
        fail(
            "backend executor dependency group must pin the production "
            "Playwright version exactly"
        )
    if any(
        isinstance(dependency, str) and dependency.startswith("playwright")
        for dependency in dependencies
    ):
        fail("backend core dependencies must not install the Executor's Playwright")
    backend_lock = tomllib.loads(
        (REPOSITORY_ROOT / "backend/uv.lock").read_text(encoding="utf-8")
    )
    if find_locked_version(backend_lock, "playwright") != expected_python:
        fail("backend uv.lock Playwright version differs from the contract")

    harness = require_mapping(contract["test_harness"], "test_harness")
    expected_node = str(harness["playwright_node"])
    package = load_json(REPOSITORY_ROOT / "frontend/package.json")
    dev_dependencies = require_mapping(
        package.get("devDependencies"), "devDependencies"
    )
    if dev_dependencies.get("@playwright/test") != expected_node:
        fail("frontend package.json must pin @playwright/test exactly")
    lock_text = (REPOSITORY_ROOT / "frontend/pnpm-lock.yaml").read_text(
        encoding="utf-8"
    )
    escaped_node = re.escape(expected_node)
    lock_pattern = re.compile(
        rf"(?ms)^      '@playwright/test':\n"
        rf"        specifier: {escaped_node}\n"
        rf"        version: {escaped_node}$"
    )
    if lock_pattern.search(lock_text) is None:
        fail("frontend pnpm lock importer differs from the exact test-harness version")


def validate_installed_driver(contract: Mapping[str, object]) -> None:
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None:
        fail("installed Playwright Python package is unavailable")
    browsers_path = Path(spec.origin).parent / "driver/package/browsers.json"
    data = load_json(browsers_path)
    digest = hashlib.sha256(browsers_path.read_bytes()).hexdigest()
    runtime = require_mapping(contract["production_runtime"], "production_runtime")
    if digest != runtime["driver_browsers_json_sha256"]:
        fail("installed Playwright browsers.json digest differs from the contract")
    browsers = data.get("browsers")
    if not isinstance(browsers, list):
        fail("installed Playwright browsers.json has no browser list")
    matches = [
        item
        for item in browsers
        if isinstance(item, dict) and item.get("name") == "chromium"
    ]
    if len(matches) != 1:
        fail("installed Playwright must have exactly one Chromium entry")
    actual = {
        "name": matches[0].get("name"),
        "title": matches[0].get("title"),
        "browser_version": matches[0].get("browserVersion"),
        "revision": matches[0].get("revision"),
        "install_by_default": matches[0].get("installByDefault"),
    }
    expected = dict(require_mapping(runtime["chromium"], "production_runtime.chromium"))
    if actual != expected:
        fail(f"installed Playwright Chromium metadata differs: actual={actual}")


def validate_component(
    component: Mapping[str, object], contract: Mapping[str, object]
) -> None:
    require_exact_keys(
        component,
        {"schema_version", "component", "target", "playwright_python", "chromium"},
        "component manifest",
    )
    if component["schema_version"] != contract["schema_version"]:
        fail("component schema_version differs from the contract")
    if component["component"] != "embedded-chromium":
        fail("component type is not embedded-chromium")
    runtime = require_mapping(contract["production_runtime"], "production_runtime")
    if component["playwright_python"] != runtime["playwright_python"]:
        fail("component Playwright version differs from the production runtime")
    if component["chromium"] != runtime["chromium"]:
        fail(
            "component Chromium version or revision differs from the production runtime"
        )
    target = require_mapping(component["target"], "component target")
    require_exact_keys(target, {"id", "os", "arch"}, "component target")
    supported_targets = contract["supported_targets"]
    if not isinstance(supported_targets, list) or target not in supported_targets:
        fail("component target is not a supported release target")


def expect_failure(name: str, action: Callable[[], object]) -> None:
    try:
        action()
    except CompatibilityError:
        return
    fail(f"self-test expected rejection but passed: {name}")


def run_self_test(contract: Mapping[str, object]) -> None:
    for name in (
        "component-valid-macos-arm64.json",
        "component-valid-macos-x86_64.json",
        "component-valid-windows-x86_64.json",
    ):
        validate_component(load_json(FIXTURE_ROOT / name), contract)
    for name in (
        "component-invalid-version.json",
        "component-invalid-revision.json",
        "component-invalid-platform.json",
    ):
        component = load_json(FIXTURE_ROOT / name)
        expect_failure(
            name, lambda component=component: validate_component(component, contract)
        )

    valid = load_json(FIXTURE_ROOT / "component-valid-macos-arm64.json")
    extra = dict(valid)
    extra["download_url"] = "https://example.invalid/browser.zip"
    expect_failure("unknown component key", lambda: validate_component(extra, contract))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component-manifest", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract = load_json(CONTRACT_PATH)
    validate_contract(contract)
    validate_dependency_locks(contract)
    validate_installed_driver(contract)
    if args.component_manifest is not None:
        validate_component(load_json(args.component_manifest), contract)
    if args.self_test:
        run_self_test(contract)
    print("embedded Chromium compatibility check passed")


if __name__ == "__main__":
    try:
        main()
    except CompatibilityError as error:
        raise SystemExit(
            f"embedded Chromium compatibility check failed: {error}"
        ) from error
