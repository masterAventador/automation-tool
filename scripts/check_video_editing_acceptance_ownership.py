#!/usr/bin/env python3
"""Audit the unique executable ownership of the LE-17 Tauri editing spec."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

SPEC_NAME = "video-editing.spec.ts"
SPEC_REFERENCE = f"./e2e-tauri/{SPEC_NAME}"
OWNER_NAME = "wdio.video-editing.conf.ts"
TAURI_CONFIGURATION = "src-tauri/tauri.video-editing-e2e.conf.json"
BUILD_SCRIPT = "build:tauri:video-editing-test"
ACCEPTANCE_SCRIPT = "test:le17-video-editing-app"
ACCEPTANCE_DRIVER = "../scripts/run_le_17_acceptance.py"
_SPECS_ARRAY = re.compile(r"\bspecs\s*:\s*\[(?P<body>.*?)\]", re.DOTALL)
_STRING = re.compile(r"(['\"])(?P<value>.*?)\1")


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _owns_spec(source: str) -> bool:
    return any(
        match.group("value") == SPEC_REFERENCE
        for array in _SPECS_ARRAY.finditer(source)
        for match in _STRING.finditer(array.group("body"))
    )


def _option(tokens: list[str], name: str) -> str | None:
    try:
        index = tokens.index(name)
    except ValueError:
        return None
    return tokens[index + 1] if index + 1 < len(tokens) else None


def audit_repository(repository_root: Path) -> list[str]:
    errors: list[str] = []
    frontend = repository_root / "frontend"
    if _read_text(frontend / "e2e-tauri" / SPEC_NAME) is None:
        errors.append("video-editing.spec.ts is missing")

    owners = sorted(
        path.name
        for path in frontend.glob("wdio*.conf.ts")
        if (source := _read_text(path)) is not None and _owns_spec(source)
    )
    if not owners:
        errors.append("video-editing.spec.ts has no WDIO owner")
    elif len(owners) > 1:
        errors.append(
            "video-editing.spec.ts has duplicate WDIO owners: " + ",".join(owners)
        )
    elif owners != [OWNER_NAME]:
        errors.append(f"video-editing.spec.ts owner must be {OWNER_NAME}")

    tauri_path = frontend / TAURI_CONFIGURATION
    tauri_source = _read_text(tauri_path)
    if tauri_source is None:
        errors.append("video-editing Tauri configuration is missing")
    else:
        try:
            tauri = json.loads(tauri_source)
        except json.JSONDecodeError:
            tauri = None
        if not isinstance(tauri, dict):
            errors.append("video-editing Tauri configuration is invalid")

    package_source = _read_text(frontend / "package.json")
    try:
        package = json.loads(package_source) if package_source is not None else None
    except json.JSONDecodeError:
        package = None
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        errors.append("frontend package scripts are unavailable")
        return errors

    build = scripts.get(BUILD_SCRIPT)
    try:
        build_tokens = shlex.split(build) if isinstance(build, str) else []
    except ValueError:
        build_tokens = []
    if _option(build_tokens, "--features") != "control-plane-e2e":
        errors.append("video-editing build must use only control-plane-e2e")
    if _option(build_tokens, "--config") != TAURI_CONFIGURATION:
        errors.append("video-editing build names the wrong Tauri configuration")

    acceptance = scripts.get(ACCEPTANCE_SCRIPT)
    try:
        acceptance_tokens = (
            shlex.split(acceptance) if isinstance(acceptance, str) else []
        )
    except ValueError:
        acceptance_tokens = []
    if ACCEPTANCE_DRIVER not in acceptance_tokens:
        errors.append("video-editing acceptance driver has no package execution owner")
    runner = _read_text(repository_root / ACCEPTANCE_DRIVER.removeprefix("../"))
    if runner is None:
        errors.append("video-editing acceptance driver is missing")
    elif OWNER_NAME not in runner:
        errors.append("video-editing acceptance driver does not execute its WDIO owner")
    return errors


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    errors = audit_repository(repository_root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("LE-17 video-editing acceptance ownership is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
