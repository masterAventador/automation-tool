#!/usr/bin/env python3
"""C4: local script imports must name symbols their provider really defines.

The gate under test parses source only. Its fixture provider deliberately writes
a sentinel if imported, so this test also proves that validation does not
execute a script just to inspect its namespace.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_script_import_symbols.py"


def _load_checker() -> ModuleType:
    assert CHECKER.is_file(), "the cross-script import-symbol gate does not exist"
    specification = importlib.util.spec_from_file_location(
        "check_script_import_symbols",
        CHECKER,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def check_a_deliberately_missing_name_is_reported_without_import_side_effects() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(prefix="automation-tool-c4-imports-") as directory:
        repository = Path(directory)
        sentinel = repository / "provider-was-imported"
        _write(
            repository / "scripts" / "provider.py",
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n"
            "EXISTING = object()\n",
        )
        _write(
            repository / "scripts" / "consumer.py",
            "from provider import EXISTING, DELETED_NAME\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert not sentinel.exists(), (
            "the static gate imported the provider and ran its side effect"
        )
        assert len(violations) == 1, (
            "the deliberate missing import must produce exactly one violation, "
            f"got {[str(violation) for violation in violations]}"
        )
        violation = violations[0]
        assert violation.importer == Path("scripts/consumer.py")
        assert violation.line == 1
        assert violation.module == "provider"
        assert violation.name == "DELETED_NAME"


def check_only_real_direct_module_bindings_satisfy_an_import() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-c4-bindings-"
    ) as directory:
        repository = Path(directory)
        _write(
            repository / "scripts" / "provider.py",
            "import json as imported_module\n"
            "from pathlib import Path as imported_name\n"
            "CONSTANT = 1\n"
            "ANNOTATED: int\n"
            "ANNOTATED_WITH_VALUE: int = 1\n"
            "LEFT, RIGHT = (1, 2)\n"
            "def function():\n"
            "    local_only = 1\n"
            "async def async_function():\n"
            "    return None\n"
            "class Namespace:\n"
            "    class_only = 1\n"
            "if True:\n"
            "    conditional_only = 1\n",
        )
        _write(
            repository / "backend" / "tests" / "test_consumer.py",
            "from scripts.provider import (\n"
            "    ANNOTATED,\n"
            "    ANNOTATED_WITH_VALUE,\n"
            "    CONSTANT,\n"
            "    LEFT,\n"
            "    RIGHT,\n"
            "    Namespace,\n"
            "    async_function,\n"
            "    function,\n"
            "    imported_module,\n"
            "    imported_name,\n"
            "    local_only,\n"
            "    class_only,\n"
            "    conditional_only,\n"
            ")\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert {(violation.module, violation.name) for violation in violations} == {
            ("scripts.provider", "ANNOTATED"),
            ("scripts.provider", "class_only"),
            ("scripts.provider", "local_only"),
        }, (
            f"module binding scope drifted: {[str(violation) for violation in violations]}"
        )


def check_module_control_flow_only_accepts_names_bound_on_every_runtime_path() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-c4-control-flow-"
    ) as directory:
        repository = Path(directory)
        _write(
            repository / "scripts" / "provider.py",
            "from typing import TYPE_CHECKING\n"
            "if True:\n"
            "    ALWAYS_TRUE = 1\n"
            "if 1:\n"
            "    ALWAYS_TRUTHY = 1\n"
            "if False:\n"
            "    NEVER_RUNTIME = 1\n"
            "if TYPE_CHECKING:\n"
            "    TYPE_ONLY = 1\n"
            "if object():\n"
            "    BOTH_BRANCHES = 1\n"
            "else:\n"
            "    BOTH_BRANCHES = 2\n"
            "if object():\n"
            "    ONE_BRANCH = 1\n",
        )
        _write(
            repository / "scripts" / "consumer.py",
            "from provider import (\n"
            "    ALWAYS_TRUE,\n"
            "    ALWAYS_TRUTHY,\n"
            "    BOTH_BRANCHES,\n"
            "    NEVER_RUNTIME,\n"
            "    ONE_BRANCH,\n"
            "    TYPE_ONLY,\n"
            ")\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert {(violation.module, violation.name) for violation in violations} == {
            ("provider", "NEVER_RUNTIME"),
            ("provider", "ONE_BRANCH"),
            ("provider", "TYPE_ONLY"),
        }, (
            "module control-flow certainty drifted: "
            f"{[str(violation) for violation in violations]}"
        )


def check_compound_statements_cannot_hide_a_deleted_runtime_name() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-c4-compound-statements-"
    ) as directory:
        repository = Path(directory)
        _write(
            repository / "scripts" / "provider.py",
            "from contextlib import suppress\n"
            "FROM_FOR = 1\n"
            "for item in (0,):\n"
            "    del FROM_FOR\n"
            "    break\n"
            "    FROM_FOR = 2\n"
            "FROM_CONTINUE = 1\n"
            "for item in (0,):\n"
            "    del FROM_CONTINUE\n"
            "    continue\n"
            "    FROM_CONTINUE = 2\n"
            "FROM_WHILE = 1\n"
            "while True:\n"
            "    del FROM_WHILE\n"
            "    break\n"
            "FROM_MATCH = 1\n"
            "match 0:\n"
            "    case 0:\n"
            "        del FROM_MATCH\n"
            "FROM_TRY = 1\n"
            "try:\n"
            "    del FROM_TRY\n"
            "    raise RuntimeError\n"
            "    FROM_TRY = 2\n"
            "except RuntimeError:\n"
            "    pass\n"
            "FROM_EXCEPT_ALIAS = 1\n"
            "try:\n"
            "    raise RuntimeError\n"
            "except RuntimeError as FROM_EXCEPT_ALIAS:\n"
            "    pass\n"
            "FROM_WITH = 1\n"
            "with suppress(RuntimeError):\n"
            "    del FROM_WITH\n"
            "    raise RuntimeError\n"
            "    FROM_WITH = 2\n"
            "FROM_NESTED_WITH = 1\n"
            "with suppress(RuntimeError):\n"
            "    if True:\n"
            "        del FROM_NESTED_WITH\n"
            "        raise RuntimeError\n"
            "        FROM_NESTED_WITH = 2\n"
            "FROM_NESTED_TRY = 1\n"
            "try:\n"
            "    if True:\n"
            "        del FROM_NESTED_TRY\n"
            "        raise RuntimeError\n"
            "        FROM_NESTED_TRY = 2\n"
            "except RuntimeError:\n"
            "    pass\n",
        )
        _write(
            repository / "scripts" / "consumer.py",
            "from provider import (\n"
            "    FROM_CONTINUE,\n"
            "    FROM_EXCEPT_ALIAS,\n"
            "    FROM_FOR,\n"
            "    FROM_MATCH,\n"
            "    FROM_NESTED_TRY,\n"
            "    FROM_NESTED_WITH,\n"
            "    FROM_TRY,\n"
            "    FROM_WHILE,\n"
            "    FROM_WITH,\n"
            ")\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert {violation.name for violation in violations} == {
            "FROM_CONTINUE",
            "FROM_EXCEPT_ALIAS",
            "FROM_FOR",
            "FROM_MATCH",
            "FROM_NESTED_TRY",
            "FROM_NESTED_WITH",
            "FROM_TRY",
            "FROM_WHILE",
            "FROM_WITH",
        }, (
            "a compound statement hid a runtime deletion: "
            f"{[str(violation) for violation in violations]}"
        )


def check_imports_in_statically_dead_branches_are_not_reported() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-c4-dead-imports-"
    ) as directory:
        repository = Path(directory)
        _write(repository / "scripts" / "provider.py", "EXISTING = 1\n")
        _write(
            repository / "scripts" / "consumer.py",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from provider import TYPE_ONLY_MISSING\n"
            "if False:\n"
            "    from provider import FALSE_MISSING\n"
            "if 0:\n"
            "    from provider import FALSY_MISSING\n"
            "if True:\n"
            "    from provider import LIVE_MISSING\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert [(violation.module, violation.name) for violation in violations] == [
            ("provider", "LIVE_MISSING")
        ], (
            "statically dead imports must not fail the runtime gate: "
            f"{[str(violation) for violation in violations]}"
        )


def check_package_form_import_requires_the_local_submodule_to_exist() -> None:
    checker = _load_checker()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-c4-package-import-"
    ) as directory:
        repository = Path(directory)
        _write(repository / "scripts" / "existing.py", "VALUE = 1\n")
        _write(
            repository / "backend" / "tests" / "test_consumer.py",
            "from scripts import existing, deleted_provider\n",
        )

        violations = checker.find_missing_imported_symbols(repository)

        assert len(violations) == 1, [str(violation) for violation in violations]
        violation = violations[0]
        assert violation.importer == Path("backend/tests/test_consumer.py")
        assert violation.module == "scripts"
        assert violation.name == "deleted_provider"


def check_the_real_repository_has_no_broken_local_import_names() -> None:
    checker = _load_checker()
    violations = checker.find_missing_imported_symbols(ROOT)
    assert not violations, "\n".join(str(violation) for violation in violations)


def check_this_test_is_auto_discovered_by_the_aggregate_runner() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_script_tests

    discovered = {path.name for path in run_script_tests.discover(ROOT)}
    assert Path(__file__).name in discovered


CHECKS = (
    check_a_deliberately_missing_name_is_reported_without_import_side_effects,
    check_only_real_direct_module_bindings_satisfy_an_import,
    check_module_control_flow_only_accepts_names_bound_on_every_runtime_path,
    check_compound_statements_cannot_hide_a_deleted_runtime_name,
    check_imports_in_statically_dead_branches_are_not_reported,
    check_package_form_import_requires_the_local_submodule_to_exist,
    check_the_real_repository_has_no_broken_local_import_names,
    check_this_test_is_auto_discovered_by_the_aggregate_runner,
)


def main() -> int:
    failures = 0
    for check in CHECKS:
        try:
            check()
        except (AssertionError, AttributeError, OSError, SyntaxError) as error:
            failures += 1
            print(f"FAIL {check.__name__}: {error}")
        else:
            print(f"ok   {check.__name__}")
    print(f"executed checks: {len(CHECKS)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
