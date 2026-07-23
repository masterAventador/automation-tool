#!/usr/bin/env python3
"""Fail closed when the pinned Browser Use API or history wire format drifts."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import inspect
import json
import os
import sys
import tempfile
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/browser-use/api-contract.v1.json"


class ContractError(RuntimeError):
    """Raised when the Browser Use compatibility boundary changed."""


def fail(message: str) -> None:
    raise ContractError(message)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        fail(f"required JSON file is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        fail(f"required TOML file is missing: {path}")
    with path.open("rb") as stream:
        return tomllib.load(stream)


def resolve_public_import(path: str) -> type[Any]:
    module_name, attribute = path.rsplit(".", 1)
    value = getattr(importlib.import_module(module_name), attribute, None)
    if not isinstance(value, type):
        fail(f"public import is not a type: {path}")
    return value


def normalized_default(parameter: inspect.Parameter) -> object:
    if parameter.default is inspect.Parameter.empty:
        return "__required__"
    return parameter.default


def check_parameters(target: object, expected: dict[str, object], label: str) -> None:
    parameters = inspect.signature(target).parameters
    for name, expected_default in expected.items():
        parameter = parameters.get(name)
        if parameter is None:
            fail(f"{label} lost required compatibility parameter: {name}")
        if normalized_default(parameter) != expected_default:
            fail(
                f"{label}.{name} default drifted: "
                f"{normalized_default(parameter)!r} != {expected_default!r}"
            )


def check_dependency_lock(contract: dict[str, Any]) -> None:
    package = contract["package"]
    pyproject_path = REPOSITORY_ROOT / package["pyproject"]
    lock_path = REPOSITORY_ROOT / package["lock"]
    pyproject = read_toml(pyproject_path)
    dependencies = pyproject["project"]["dependencies"]
    if package["requirement"] not in dependencies:
        fail(f"production dependencies do not contain exact {package['requirement']}")

    lock = read_toml(lock_path)
    matches = [entry for entry in lock["package"] if entry.get("name") == package["name"]]
    if len(matches) != 1 or matches[0].get("version") != package["version"]:
        fail("Browser Use lock entry is absent, duplicated, or at the wrong version")
    hashes = {
        artifact.get("hash")
        for artifact in [matches[0].get("sdist", {}), *matches[0].get("wheels", [])]
    }
    if package["wheel_sha256"] not in hashes:
        fail("official Browser Use wheel digest is absent from the production lock")


def check_history_fixture(contract: dict[str, Any], imports: dict[str, type[Any]]) -> None:
    history_contract = contract["history"]
    history_list = imports["AgentHistoryList"]
    history = imports["AgentHistory"]
    action_result = imports["ActionResult"]
    if set(history_list.model_fields) != set(history_contract["list_fields"]):
        fail("AgentHistoryList fields drifted")
    if set(history.model_fields) != set(history_contract["item_fields"]):
        fail("AgentHistory fields drifted")
    if not set(history_contract["action_result_fields"]).issubset(action_result.model_fields):
        fail("ActionResult fields drifted")
    for method in history_contract["methods"]:
        if not callable(getattr(history_list, method, None)):
            fail(f"AgentHistoryList method disappeared: {method}")

    fixture_path = REPOSITORY_ROOT / history_contract["fixture"]
    fixture = read_json(fixture_path)
    loaded = history_list.load_from_file(fixture_path, imports["AgentOutput"])
    expectations = history_contract["expectations"]
    observed = {
        "is_done": loaded.is_done(),
        "is_successful": loaded.is_successful(),
        "final_result": loaded.final_result(),
        "urls": loaded.urls(),
        "action_names": loaded.action_names(),
        "screenshots": loaded.screenshots(),
    }
    if observed != expectations:
        fail(f"history helper semantics drifted: {observed!r}")
    if loaded.model_dump(mode="json") != fixture:
        fail("history fixture no longer round-trips through model_dump")
    with tempfile.TemporaryDirectory(prefix="automation-tool-bu01-") as temporary:
        output = Path(temporary) / "history.json"
        loaded.save_to_file(output)
        if read_json(output) != fixture:
            fail("history fixture no longer round-trips through save_to_file")


def check_tools_registration(imports: dict[str, type[Any]]) -> None:
    tools = imports["Tools"](exclude_actions=[])
    action_result = imports["ActionResult"]

    @tools.action("BU-01 deterministic contract probe")
    async def bu_01_contract_probe(value: str) -> Any:
        return action_result(extracted_content=value)

    registered = tools.registry.registry.actions
    if "bu_01_contract_probe" not in registered:
        fail("Tools.action no longer registers a typed async action")


def check_contract(contract_path: Path = CONTRACT_PATH) -> None:
    contract = read_json(contract_path)
    if contract.get("schema_version") != 1:
        fail("unsupported Browser Use contract schema")
    package = contract["package"]
    if sys.version_info[:2] != tuple(package["python_minor"]):
        fail(f"Browser Use must run on Python {package['python_minor']}")
    if importlib.metadata.version(package["name"]) != package["version"]:
        fail("installed Browser Use version drifted")
    check_dependency_lock(contract)

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    imports = {
        name: resolve_public_import(path) for name, path in contract["public_imports"].items()
    }
    for name, expected_module in contract["runtime_modules"].items():
        if imports[name].__module__ != expected_module:
            fail(f"{name} runtime module drifted: {imports[name].__module__}")
    for label, expected in contract["parameters"].items():
        owner_name, _, method_name = label.partition(".")
        target = imports[owner_name]
        if method_name:
            target = getattr(target, method_name)
        check_parameters(target, expected, label)
    check_history_fixture(contract, imports)
    check_tools_registration(imports)


def self_test() -> None:
    contract = read_json(CONTRACT_PATH)
    cases: list[tuple[str, dict[str, Any]]] = []

    wrong_version = deepcopy(contract)
    wrong_version["package"]["version"] = "0.13.5"
    cases.append(("wrong-version", wrong_version))

    wrong_default = deepcopy(contract)
    wrong_default["parameters"]["Agent.run"]["max_steps"] = 499
    cases.append(("wrong-default", wrong_default))

    missing_fixture = deepcopy(contract)
    missing_fixture["history"]["fixture"] = "contracts/browser-use/fixtures/missing.json"
    cases.append(("missing-fixture", missing_fixture))

    with tempfile.TemporaryDirectory(prefix="automation-tool-bu01-self-test-") as temporary:
        root = Path(temporary)
        for name, invalid in cases:
            path = root / f"{name}.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            try:
                check_contract(path)
            except ContractError:
                continue
            fail(f"self-test accepted invalid case: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    check_contract()
    if args.self_test:
        self_test()
    print("Browser Use 0.13.6 API and history contract passed")


if __name__ == "__main__":
    try:
        main()
    except (ContractError, KeyError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(f"Browser Use API contract failed: {error}") from error
