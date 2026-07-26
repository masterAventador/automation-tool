#!/usr/bin/env python3
"""Audit execution ownership for every Python acceptance driver.

This gate deliberately distinguishes executing a driver from reading its source.
It derives executable owners from ``frontend/package.json`` and real Python
``subprocess`` call sites. Drivers that cannot have an automatic owner must be
listed once in the blocker registry with their conditions and resources.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTRY_NAME = "acceptance_driver_ownership.v1.json"
REGISTRY_VERSION = "acceptance-driver-ownership.v1"
DRIVER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])run_[A-Za-z0-9_]+_acceptance\.py(?![A-Za-z0-9_])"
)
SOURCE_READER_SUFFIXES = {".js", ".mjs", ".py", ".rs", ".ts"}
SUBPROCESS_METHODS = {"call", "check_call", "check_output", "Popen", "run"}


@dataclass(frozen=True)
class DriverConclusion:
    """One derived ownership conclusion."""

    driver: str
    execution_owners: tuple[str, ...]
    blocker_profile: str | None
    source_contract_readers: tuple[str, ...]
    non_executing_references: tuple[str, ...]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _driver_names(repository_root: Path) -> set[str]:
    return {
        path.name
        for path in (repository_root / "scripts").glob("run_*_acceptance.py")
        if path.is_file() and not path.is_symlink()
    }


def _package_owners(repository_root: Path, drivers: set[str]) -> dict[str, set[str]]:
    package = _read_json(repository_root / "frontend/package.json")
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        return {}
    owners: dict[str, set[str]] = defaultdict(set)
    for script_name, command in scripts.items():
        if not isinstance(script_name, str) or not isinstance(command, str):
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            driver = Path(token.replace("\\", "/")).name
            if driver not in drivers:
                continue
            prior_commands = {
                Path(prior.replace("\\", "/")).name.casefold()
                for prior in tokens[:index]
            }
            if prior_commands & {"python", "python3", "python.exe"}:
                owners[driver].add(f"frontend/package.json#{script_name}")
    return owners


def _is_subprocess_call(node: ast.Call) -> bool:
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "subprocess"
        and function.attr in SUBPROCESS_METHODS
    )


def _source_segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _assigned_symbols(tree: ast.AST, source: str, driver: str) -> set[str]:
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None or driver not in _source_segment(source, value):
            continue
        targets: Iterable[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
        else:
            targets = (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                symbols.add(target.id)
    return symbols


def _subprocess_wrappers(tree: ast.AST) -> dict[str, tuple[tuple[int, str], ...]]:
    wrappers: dict[str, tuple[tuple[int, str], ...]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        parameter_names = {parameter.arg for parameter in parameters}
        if node.args.vararg is not None:
            parameter_names.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            parameter_names.add(node.args.kwarg.arg)
        forwarded: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not _is_subprocess_call(child):
                continue
            forwarded.update(
                name.id
                for name in ast.walk(child)
                if isinstance(name, ast.Name) and name.id in parameter_names
            )
        if forwarded:
            wrappers[node.name] = tuple(
                (index, parameter.arg)
                for index, parameter in enumerate(parameters)
                if parameter.arg in forwarded
            )
    return wrappers


def _python_executes_driver(source: str, driver: str) -> bool:
    """Conservatively prove a subprocess path; imports/read_text never qualify."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    symbols = _assigned_symbols(tree, source, driver)
    wrappers = _subprocess_wrappers(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_source = _source_segment(source, node)
        if _is_subprocess_call(node):
            if driver in call_source or any(
                isinstance(child, ast.Name) and child.id in symbols
                for child in ast.walk(node)
            ):
                return True
            continue
        if isinstance(node.func, ast.Name) and node.func.id in wrappers:
            forwarded_parameters = wrappers[node.func.id]
            forwarded_names = {name for _, name in forwarded_parameters}
            positional = " ".join(
                _source_segment(source, node.args[index])
                for index, _ in forwarded_parameters
                if index < len(node.args)
            )
            keywords = " ".join(
                _source_segment(source, keyword.value)
                for keyword in node.keywords
                if keyword.arg in forwarded_names
            )
            if driver in f"{positional} {keywords}":
                return True
    return False


def _python_owners(repository_root: Path, drivers: set[str]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for path in (repository_root / "scripts").glob("*.py"):
        if not path.is_file() or path.is_symlink() or path.name in drivers:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        mentioned = set(DRIVER_PATTERN.findall(source)) & drivers
        for driver in mentioned:
            if _python_executes_driver(source, driver):
                owners[driver].add(
                    f"python-subprocess:{path.relative_to(repository_root)}"
                )
    for path in (repository_root / "scripts").glob("run_*_acceptance.py"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for driver in (set(DRIVER_PATTERN.findall(source)) & drivers) - {path.name}:
            if _python_executes_driver(source, driver):
                owners[driver].add(
                    f"python-subprocess:{path.relative_to(repository_root)}"
                )
    return owners


def _reference_paths(repository_root: Path) -> Iterable[Path]:
    roots = (
        repository_root / "scripts",
        repository_root / "backend/tests",
        repository_root / "frontend/tests",
        repository_root / "frontend/src-tauri/tests",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in SOURCE_READER_SUFFIXES
                and not DRIVER_PATTERN.fullmatch(path.name)
            ):
                yield path


def _python_reads_driver_source(source: str, driver: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    module_name = driver.removesuffix(".py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == module_name for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            return True
    symbols = _assigned_symbols(tree, source, driver)
    reader_names = {
        "open",
        "read",
        "read_bytes",
        "read_file",
        "read_text",
        "spec_from_file_location",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else ""
        )
        if name not in reader_names:
            continue
        if driver in _source_segment(source, node) or any(
            isinstance(child, ast.Name) and child.id in symbols
            for child in ast.walk(node)
        ):
            return True
    return False


def _matching_parenthesis(source: str, opening: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
        elif block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 1
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', "'", "`"}:
            quote = char
        elif char == "/" and following == "/":
            line_comment = True
            index += 1
        elif char == "/" and following == "*":
            block_comment = True
            index += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _javascript_reads_driver_source(source: str, driver: str) -> bool:
    reader_call = re.compile(
        r"\b(?:read|readFile|readProjectFile|readRepositoryFile)\s*\("
    )
    for match in reader_call.finditer(source):
        opening = source.find("(", match.start(), match.end())
        closing = _matching_parenthesis(source, opening)
        if closing is not None and driver in source[opening : closing + 1]:
            return True
    dynamic_reader = re.compile(
        r"\b(?:read|readFile|readProjectFile|readRepositoryFile)\s*\(\s*"
        r"(?:new\s+URL\s*\(\s*)?(?P<variable>[A-Za-z_$][\w$]*)"
    )
    return any(
        re.search(
            rf"\b(?:const|let|var)\s+{re.escape(match.group('variable'))}\b|"
            rf"\b(?:runner|script|path)\s*:\s*[\"']{re.escape(driver)}[\"']",
            source,
        )
        is not None
        for match in dynamic_reader.finditer(source)
    )


def _reads_driver_source(path: Path, source: str, driver: str) -> bool:
    if path.suffix == ".py":
        return _python_reads_driver_source(source, driver)
    if path.suffix in {".js", ".mjs", ".ts"}:
        return _javascript_reads_driver_source(source, driver)
    position = source.find(driver)
    while position >= 0:
        context = source[max(0, position - 500) : position + len(driver) + 500]
        if path.suffix == ".rs" and re.search(
            r"(?:include_(?:bytes|str)!|read_to_string|fs::read)",
            context,
        ):
            return True
        position = source.find(driver, position + len(driver))
    return False


def _non_execution_references(
    repository_root: Path,
    drivers: set[str],
    python_owners: dict[str, set[str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    readers: dict[str, set[str]] = defaultdict(set)
    references: dict[str, set[str]] = defaultdict(set)
    for path in _reference_paths(repository_root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(repository_root)
        execution_label = f"python-subprocess:{relative}"
        for driver in set(DRIVER_PATTERN.findall(source)) & drivers:
            if execution_label in python_owners.get(driver, set()):
                continue
            if _reads_driver_source(path, source, driver):
                readers[driver].add(f"source-shape-only:{relative}")
            else:
                references[driver].add(f"non-executing-reference:{relative}")
    return readers, references


def _blocked_profiles(
    repository_root: Path,
    drivers: set[str],
    errors: list[str],
) -> dict[str, str]:
    document = _read_json(repository_root / "scripts" / REGISTRY_NAME)
    if not isinstance(document, dict) or document.get("version") != REGISTRY_VERSION:
        errors.append(f"{REGISTRY_NAME} is missing or has the wrong version")
        return {}
    profiles = document.get("blockedProfiles")
    if not isinstance(profiles, dict):
        errors.append(f"{REGISTRY_NAME} has no blockedProfiles object")
        return {}
    assignments: dict[str, str] = {}
    for profile_name, raw_profile in profiles.items():
        if not isinstance(profile_name, str) or not isinstance(raw_profile, dict):
            errors.append(f"{REGISTRY_NAME} has an invalid blocker profile")
            continue
        reason = raw_profile.get("reason")
        conditions = raw_profile.get("conditions")
        resources = raw_profile.get("resources")
        profile_drivers = raw_profile.get("drivers")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"blocker profile {profile_name} has no reason")
        if (
            not isinstance(conditions, list)
            or not conditions
            or any(not isinstance(item, str) or not item.strip() for item in conditions)
        ):
            errors.append(f"blocker profile {profile_name} has no explicit conditions")
        if (
            not isinstance(resources, list)
            or not resources
            or any(not isinstance(item, str) or not item.strip() for item in resources)
        ):
            errors.append(f"blocker profile {profile_name} has no explicit resources")
        if not isinstance(profile_drivers, list) or not profile_drivers:
            errors.append(f"blocker profile {profile_name} has no drivers")
            continue
        for driver in profile_drivers:
            if not isinstance(driver, str) or driver not in drivers:
                errors.append(
                    f"blocker profile {profile_name} names unknown driver {driver!r}"
                )
                continue
            if driver in assignments:
                errors.append(
                    f"{driver} is duplicated in blocker profiles "
                    f"{assignments[driver]} and {profile_name}"
                )
                continue
            assignments[driver] = profile_name
    return assignments


def conclusions(
    repository_root: Path, errors: list[str] | None = None
) -> list[DriverConclusion]:
    """Derive every execution owner, explicit blocker, and source-only reader."""
    target_errors = errors if errors is not None else []
    drivers = _driver_names(repository_root)
    package_owners = _package_owners(repository_root, drivers)
    python_owners = _python_owners(repository_root, drivers)
    blockers = _blocked_profiles(repository_root, drivers, target_errors)
    readers, references = _non_execution_references(
        repository_root, drivers, python_owners
    )
    result: list[DriverConclusion] = []
    for driver in sorted(drivers):
        owners = sorted(
            package_owners.get(driver, set()) | python_owners.get(driver, set())
        )
        blocker = blockers.get(driver)
        if owners and blocker is not None:
            target_errors.append(
                f"{driver} has both an execution owner and blocker profile {blocker}"
            )
        if not owners and blocker is None:
            target_errors.append(f"{driver} has no execution owner or explicit blocker")
        result.append(
            DriverConclusion(
                driver=driver,
                execution_owners=tuple(owners),
                blocker_profile=blocker,
                source_contract_readers=tuple(sorted(readers.get(driver, set()))),
                non_executing_references=tuple(sorted(references.get(driver, set()))),
            )
        )
    return result


def audit_repository(repository_root: Path) -> list[str]:
    """Return fixed ownership errors for one repository tree."""
    errors: list[str] = []
    inventory = conclusions(repository_root, errors)
    if not inventory:
        errors.append("no scripts/run_*_acceptance.py drivers were discovered")
    return errors


def _summary(inventory: list[DriverConclusion]) -> dict[str, int]:
    return {
        "drivers": len(inventory),
        "withExecutionOwner": sum(bool(item.execution_owners) for item in inventory),
        "explicitlyBlocked": sum(
            item.blocker_profile is not None for item in inventory
        ),
        "withSourceShapeReaders": sum(
            bool(item.source_contract_readers) for item in inventory
        ),
        "withOtherNonExecutingReferences": sum(
            bool(item.non_executing_references) for item in inventory
        ),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser.parse_args()


def main() -> int:
    parsed = _arguments()
    repository_root = Path(__file__).resolve().parents[1]
    errors: list[str] = []
    inventory = conclusions(repository_root, errors)
    report = {
        "version": REGISTRY_VERSION,
        "summary": _summary(inventory),
        "drivers": [
            {
                "driver": item.driver,
                "executionOwners": list(item.execution_owners),
                "blockerProfile": item.blocker_profile,
                "sourceContractReaders": list(item.source_contract_readers),
                "nonExecutingReferences": list(item.non_executing_references),
            }
            for item in inventory
        ],
        "errors": errors,
    }
    if parsed.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "| driver | execution owner | blocker | source readers | other references |"
        )
        print("| --- | --- | --- | ---: | ---: |")
        for item in inventory:
            owners = "<br>".join(item.execution_owners) or "—"
            blocker = item.blocker_profile or "—"
            print(
                f"| `{item.driver}` | {owners} | {blocker} | "
                f"{len(item.source_contract_readers)} | "
                f"{len(item.non_executing_references)} |"
            )
        print()
        print(json.dumps(_summary(inventory), ensure_ascii=False, sort_keys=True))
        if errors:
            print("\nErrors:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
