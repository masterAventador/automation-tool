"""The shipped Executor package may not re-export its test doubles.

Why this exists
---------------
`automation-tool-executor.spec` declares `excludes=[]`: nothing is kept out of
the frozen package by name. What does or does not ship is therefore decided
entirely by the import graph, and `automation_tool/executor/__init__.py` sits
at the root of it. Re-exporting `FakeExecutorEngine` there means any entry point
that imports the *package* — rather than the specific module it needs — drags a
protocol-replaying stand-in into a customer's installation.

Measured 2026-07-27 on the real macOS candidate: the double is not in the
package today. That is luck rather than design, because the entry point happens
to import submodules directly; a single future `import automation_tool.executor`
would change the answer with nothing objecting. `CLAUDE.md` §8 requires a
release package to carry no test command, and a fake Executor is one.

The fix is structural rather than a scan: the doubles stay importable by their
own module path — the four test files that use them already do exactly that —
but they are not part of what importing the package gives you.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE_INIT = (
    Path(__file__).resolve().parents[3]
    / "src/automation_tool/executor/__init__.py"
)
# Names whose presence in a customer's installation would be a defect. They are
# matched as substrings so a renamed variant is caught too.
_TEST_DOUBLE_MARKERS = ("Fake", "Mock", "Stub", "Dummy")


def _exported_names() -> tuple[str, ...]:
    """Everything importing the package hands out, read from the source."""
    tree = ast.parse(_PACKAGE_INIT.read_text(encoding="utf-8"))
    exported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            exported.extend(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            exported.extend(
                element.value
                for element in getattr(node.value, "elts", [])
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return tuple(exported)


@pytest.mark.parametrize("marker", _TEST_DOUBLE_MARKERS)
def test_the_package_does_not_re_export_a_test_double(marker: str) -> None:
    offenders = sorted({name for name in _exported_names() if marker in name})
    assert offenders == [], (
        f"automation_tool.executor re-exports {offenders}; with excludes=[] in the "
        "PyInstaller spec, importing the package would ship them to a customer. "
        "Import the module directly where the double is needed."
    )


def test_the_module_paths_still_work_for_the_tests_that_need_them() -> None:
    """Removing the re-export must not remove the capability."""
    from automation_tool.executor.fake import FakeExecutorEngine
    from automation_tool.executor.fake_client import FakeExecutorClient

    assert FakeExecutorEngine is not None
    assert FakeExecutorClient is not None


def test_nothing_asks_the_package_root_for_a_test_double() -> None:
    """The other half of the boundary: consumers must not ask for what is gone.

    Removing the re-exports made every `from automation_tool.executor import
    FakeExecutorClient` an `ImportError`. That is the right failure, but it is a
    *runtime* one — and the seven acceptance runners carrying it are only ever
    exercised when somebody runs them by hand, so they sat broken until
    2026-07-29, when PC-21's catch-up run reached `run_t3_16_acceptance.py` and
    it died before its first assertion.

    A static check catches the whole class at once and costs nothing, which the
    runtime one cannot: importing all 123 runners to find out would execute
    whatever they do at import time.
    """
    root = Path(__file__).resolve().parents[3].parent
    offenders: list[str] = []
    for path in sorted(root.glob("scripts/*.py")) + sorted(root.glob("backend/tests/**/*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not this gate's job
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "automation_tool.executor":
                continue
            for alias in node.names:
                if any(marker in alias.name for marker in _TEST_DOUBLE_MARKERS):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} {alias.name}")
    assert offenders == [], (
        "these ask the package root for a test double, which it deliberately no "
        "longer re-exports; import them by module path "
        "(`automation_tool.executor.fake` / `.fake_client`) instead: "
        f"{offenders}"
    )
