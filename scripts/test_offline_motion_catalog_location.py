#!/usr/bin/env python3
"""The locked offline motion catalog is a build *input*, so it is cached per machine.

It is 46 MiB of digest-pinned files fetched from jsdelivr, gstatic and cdnjs —
the same category as the Chromium archive and the media toolchain, all of which
already live in the machine-wide artifact cache. This one stayed in the
checkout's `.local/`, which meant every worktree either downloaded it again or
found nothing: `.local` is not copied into a new worktree, and CLAUDE.md §8.1
records the day that cost sixteen acceptance runs.

The release tree built *from* it deliberately stays in `.local`. That one is an
output — `commit_gate`'s slow tier rebuilds it from the commit's own code, and
a shared location would let one run's output become another run's input.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_offline_motion_catalog import catalog_root  # noqa: E402
from video_runtime_cache import cache_root  # noqa: E402


class CatalogLocationTests(unittest.TestCase):
    def test_the_catalog_lives_in_the_machine_artifact_cache(self) -> None:
        resolved = catalog_root()

        self.assertTrue(
            resolved.is_relative_to(cache_root()),
            f"{resolved} must sit under the shared artifact cache {cache_root()}",
        )

    def test_the_catalog_never_resolves_inside_a_checkout(self) -> None:
        resolved = catalog_root()

        self.assertFalse(
            resolved.is_relative_to(ROOT),
            f"{resolved} still depends on which checkout the caller runs from",
        )


class OnlyOneCatalogLookupTests(unittest.TestCase):
    """No script may re-derive the location by joining it onto a checkout.

    Five scripts each spelled `REPOSITORY_ROOT / lock["layout"]["catalogRoot"]`,
    and one skipped the lock entirely and hardcoded the path. Moving the
    directory once meant finding all six; missing one leaves a script reading a
    directory nothing writes, which is how `build_embedded_chromium_staging`
    ended up pointing at an archive that had already moved.
    """

    SCRIPTS = ROOT / "scripts"
    # The one module allowed to derive it: `catalog_root()` is the definition
    # every other caller imports. Exempting it by name rather than by pattern
    # keeps the exemption countable — a second name here would be visible.
    DEFINITION = "build_offline_motion_catalog.py"

    @staticmethod
    def _docstring_nodes(tree: ast.AST) -> set[int]:
        """Ids of the string constants that are docstrings, not code.

        Prose is allowed to name the old location — the comments explaining the
        move have to be able to say what moved.
        """
        carriers = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        documented: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, carriers):
                continue
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                documented.add(id(body[0].value))
        return documented

    def test_no_script_joins_the_catalog_root_onto_the_repository(self) -> None:
        offenders: list[str] = []
        for path in sorted(self.SCRIPTS.glob("*.py")):
            if path.name.startswith("test_") or path.name == self.DEFINITION:
                continue
            source = path.read_text(encoding="utf-8")
            if "catalogRoot" not in source and "offline-motion-deps/catalog" not in source:
                continue
            tree = ast.parse(source)
            documented = self._docstring_nodes(tree)
            for node in ast.walk(tree):
                # `<anything> / lock["layout"]["catalogRoot"]`
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    right = node.right
                    if (
                        isinstance(right, ast.Subscript)
                        and isinstance(right.slice, ast.Constant)
                        and right.slice.value == "catalogRoot"
                    ):
                        offenders.append(
                            f"{path.name}:{node.lineno} joins catalogRoot by hand"
                        )
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "offline-motion-deps/catalog" in node.value
                    and id(node) not in documented
                ):
                    offenders.append(f"{path.name}:{node.lineno} hardcodes the old path")

        self.assertEqual(
            [],
            offenders,
            "the catalog location is derived in more than one place: " + str(offenders),
        )


if __name__ == "__main__":
    unittest.main()
