"""The migration subprocess must be pinned to the interpreter running the tests.

On 2026-07-26 a full-suite sweep reported 216 integration failures. None of them
were real. The fixture shelled out to a bare `alembic`, which resolves through
`PATH` — and `backend/.venv/bin/python -m pytest`, a completely ordinary way to
run this suite, does not put `backend/.venv/bin` on `PATH`. Every test that
needed a migrated database died at the same place, and the shape of the damage
(216 failures, uniformly distributed) read like a product collapse.

`scripts/run_script_tests.py` spends a paragraph of its docstring on exactly
this: the interpreter must be pinned, never inherited. This fixture pinned the
interpreter it ran under and then forked to a console script that did not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = BACKEND_ROOT / "tests" / "integration" / "conftest.py"


def _load_integration_conftest() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "automation_tool_integration_conftest", CONFTEST
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_migration_command_runs_under_the_current_interpreter() -> None:
    command = _load_integration_conftest().alembic_command("upgrade", "head")

    assert command[:3] == [sys.executable, "-m", "alembic"], (
        "the migration subprocess must not be resolved through PATH; running "
        "the suite as `.venv/bin/python -m pytest` leaves the venv's bin "
        f"directory off PATH. Got: {command}"
    )
    assert command[3:] == ["upgrade", "head"]


def test_no_bare_console_script_survives_in_the_command() -> None:
    command = _load_integration_conftest().alembic_command("current")

    assert "alembic" not in command[:1], (
        "a bare console script name in argv[0] is the PATH dependency this test exists to prevent"
    )
