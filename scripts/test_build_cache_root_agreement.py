#!/usr/bin/env python3
"""One build cache, resolved by three implementations that must agree.

`scripts/video_runtime_cache.py::cache_root` is the rule. Two more copies of it
exist inside the executor package —
`automation_tool/executor/captions/fonts.py::_build_cache_root` and
`automation_tool/executor/silero_vad.py::_build_cache_root` — and the
duplication is legitimate: the executor is frozen by PyInstaller and `scripts/`
is not part of the shipped package, so it cannot import the original.

What is not legitimate is that the three were kept in step **by hand**, with
nothing able to tell whether they still were. They currently agree; that is a
fact about today, not a property. The moment one of them learns about a new
platform, an override, or a `~` in an environment variable and the others do
not, the executor starts reading a different directory than the build wrote —
and the symptom is a missing font or model at runtime on a customer's machine,
nowhere near the edit that caused it.

This is a regression guard, not a bug report: it found nothing when it was
written. Its value is the next edit.
"""

from __future__ import annotations

import os
import sys
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# Spelled as one relative path, matching `MYPY_PATH_ROOTS`: the commit gate
# discovers these inserts textually and reads `ROOT / "backend" / "src"` as a
# root named `backend`, which is not one it knows about.
sys.path.insert(0, str(ROOT / "backend/src"))

from automation_tool.executor.captions.fonts import (  # noqa: E402
    _build_cache_root as fonts_cache_root,
)
from automation_tool.executor.silero_vad import (  # noqa: E402
    _build_cache_root as vad_cache_root,
)
from video_runtime_cache import cache_root  # noqa: E402

# The name each one is known by in a failure message. `cache_root` comes first
# because it is the rule the other two are copies of.
IMPLEMENTATIONS: tuple[tuple[str, Callable[[], Path]], ...] = (
    ("scripts/video_runtime_cache.py::cache_root", cache_root),
    ("executor/captions/fonts.py::_build_cache_root", fonts_cache_root),
    ("executor/silero_vad.py::_build_cache_root", vad_cache_root),
)

# Every branch the rule has, plus the ones an override reaches. `None` removes
# the variable rather than setting it empty: an empty override is falsy and
# takes the same path as an absent one, so setting it would skip the branch.
ENVIRONMENTS: tuple[tuple[str, dict[str, str | None]], ...] = (
    ("no override", {"AUTOMATION_TOOL_BUILD_CACHE": None}),
    (
        "explicit override",
        {"AUTOMATION_TOOL_BUILD_CACHE": os.fspath(Path("/tmp/automation-tool-probe"))},
    ),
    # `~` is the one an implementation can silently get wrong: forgetting
    # `expanduser()` yields a relative directory literally named "~".
    ("override with a home shorthand", {"AUTOMATION_TOOL_BUILD_CACHE": "~/at-probe"}),
    (
        "empty override is not an override",
        {"AUTOMATION_TOOL_BUILD_CACHE": ""},
    ),
    (
        "windows local app data",
        {"AUTOMATION_TOOL_BUILD_CACHE": None, "LOCALAPPDATA": r"D:\probe\AppData\Local"},
    ),
    (
        "windows without local app data",
        {"AUTOMATION_TOOL_BUILD_CACHE": None, "LOCALAPPDATA": None},
    ),
    (
        "xdg cache home",
        {"AUTOMATION_TOOL_BUILD_CACHE": None, "XDG_CACHE_HOME": "/var/tmp/at-xdg"},
    ),
    (
        "no xdg cache home",
        {"AUTOMATION_TOOL_BUILD_CACHE": None, "XDG_CACHE_HOME": None},
    ),
)


@contextmanager
def environment(changes: dict[str, str | None]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class BuildCacheRootAgreementTests(unittest.TestCase):
    def test_every_implementation_resolves_the_same_directory(self) -> None:
        for label, changes in ENVIRONMENTS:
            with self.subTest(environment=label), environment(changes):
                resolved = [(name, resolve()) for name, resolve in IMPLEMENTATIONS]
                expected_name, expected = resolved[0]
                for name, actual in resolved[1:]:
                    self.assertEqual(
                        expected,
                        actual,
                        f"under {label!r}: {name} resolves to {actual}, but "
                        f"{expected_name} resolves to {expected}. The executor "
                        "would read a directory the build never wrote.",
                    )

    def test_a_home_shorthand_is_expanded_rather_than_taken_literally(self) -> None:
        """The one drift that produces a plausible-looking wrong directory.

        Without `expanduser()` the override yields a *relative* path whose first
        component is literally `~`, so the cache lands inside whatever directory
        the process happened to start in and every later run misses it.
        """
        with environment({"AUTOMATION_TOOL_BUILD_CACHE": "~/at-probe"}):
            for name, resolve in IMPLEMENTATIONS:
                resolved = resolve()
                self.assertTrue(
                    resolved.is_absolute(),
                    f"{name} returned the relative path {resolved}",
                )
                self.assertNotIn(
                    "~",
                    resolved.parts,
                    f"{name} did not expand the home shorthand: {resolved}",
                )

    def test_the_directory_name_carries_the_project_scope(self) -> None:
        """A stray cache has to be attributable from its name alone.

        Required by the local-resource rule in CLAUDE.md §8: every local
        artifact this project writes must be traceable to `automation-tool`.
        """
        with environment({"AUTOMATION_TOOL_BUILD_CACHE": None}):
            for name, resolve in IMPLEMENTATIONS:
                self.assertEqual(
                    "automation-tool-build",
                    resolve().name,
                    f"{name} writes to a directory nobody can attribute",
                )


if __name__ == "__main__":
    unittest.main()
