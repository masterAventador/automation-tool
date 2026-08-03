#!/usr/bin/env python3
"""Compatibility entry. The assertions live in backend/tests, not here.

COV-01 moved the 118 deterministic motion-authoring tests into
`backend/tests/unit/executor/test_motion_authoring_agent.py` so the backend
coverage run actually collects them -- `backend/pyproject.toml` sets
`testpaths = ["tests"]`, so while they sat under `scripts/` the 359 points they
really cover were counted as debt.

This path has to keep working regardless:

* `run_script_tests.py` discovers `scripts/test_*.py` by glob, not by list;
* `contracts/video/motion-render-canvas.v1.json` and
  `motion-one-sentence-brief.v1.json` both name this file in `enforcedBy`;
* BM-15/BM-16 acceptance and the slow commit gate invoke it directly.

So it forwards. It does not re-implement: two copies of the same assertions
drifting apart is a worse outcome than the collection gap this task set out to
close.

The canonical module is deliberately loaded under a fixed name so that the
suite's own `unittest -v` output identifies where the tests came from. That name
appearing in this entry's output is what
`scripts/test_motion_authoring_collection_boundary.py` checks -- proof of
execution rather than a grep for a path string, which would pass just as well
against an entry that forwards nothing.

`load_tests` would be the idiomatic unittest hook, but pytest does not implement
that protocol and this file is also invoked as `pytest scripts/...`. Binding the
classes into this module's namespace works for both runners.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "backend/tests/unit/executor/test_motion_authoring_agent.py"
MODULE_NAME = "canonical_motion_authoring_agent_tests"


def _load_canonical() -> object:
    specification = importlib.util.spec_from_file_location(MODULE_NAME, CANONICAL)
    if specification is None or specification.loader is None:
        raise SystemExit(f"cannot load the canonical motion authoring tests from {CANONICAL}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[MODULE_NAME] = module
    specification.loader.exec_module(module)
    return module


def _bind_canonical_cases() -> int:
    """Expose the canonical cases here so unittest and pytest both find them."""
    module = _load_canonical()
    bound = 0
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value.__module__ == MODULE_NAME
        ):
            globals()[name] = value
            bound += 1
    if bound == 0:
        raise SystemExit(f"the canonical module exposed no test cases: {CANONICAL}")
    return bound


_bind_canonical_cases()


if __name__ == "__main__":
    unittest.main(verbosity=2)
