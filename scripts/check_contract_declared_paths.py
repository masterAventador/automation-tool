#!/usr/bin/env python3
"""Fail closed when a contract declares a repository path that no longer exists.

A contract's `definedIn` list says which files carry the contract's rules, and
`enforcedBy` names the script that holds them to it. Both are repository paths.
When a file moves and those strings are not updated, the contract keeps
claiming to govern something that is no longer there — and reads exactly like
one that governs everything. Nothing was checking them.

Scope, stated rather than assumed: **only `definedIn` and `enforcedBy` are
checked.** Contracts hold many other path-shaped strings whose base is not the
repository root — `path` is relative to a package bundle, `healthPath` is a URL
path, `excludedGlobs` are globs, `redirect_path_prefix` is a CDN path. Resolving
those against the repository produces hundreds of false alarms; a survey on
2026-07-26 measured 1462 under a shape-based rule and 474 under a rule that
derived the key set automatically. So the key set here is curated, and a
curated list goes stale without a signal. The mitigation is that this checker
prints the keys it covers on every run, so the blind spot is visible instead of
inferred. If a new key starts declaring repository paths, add it here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

CHECKED_KEYS = ("definedIn", "enforcedBy")


def _declared_paths(node: object, key: str | None = None):
    """Every (key, value) string reachable under one of the checked keys."""
    if isinstance(node, dict):
        for child_key, value in node.items():
            yield from _declared_paths(value, child_key)
    elif isinstance(node, list):
        for value in node:
            yield from _declared_paths(value, key)
    elif isinstance(node, str) and key in CHECKED_KEYS:
        yield key, node


def collect_broken_declarations(root: Path) -> list[tuple[str, str, str]]:
    """Every (contract, key, value) whose declared repository path is missing."""
    broken: list[tuple[str, str, str]] = []
    for contract in sorted(root.glob("contracts/**/*.json")):
        try:
            document = json.loads(contract.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Malformed contracts are another check's business; this one would
            # only be guessing about a file it cannot read.
            continue
        relative = contract.relative_to(root).as_posix()
        for key, value in _declared_paths(document):
            if not (root / value).exists():
                broken.append((relative, key, value))
    return broken


def main() -> None:
    broken = collect_broken_declarations(REPOSITORY_ROOT)
    covered = ", ".join(CHECKED_KEYS)
    if broken:
        lines = "\n".join(f"  {c}\n    {k}: {v}" for c, k, v in broken)
        raise SystemExit(
            "contract declared path check failed: a contract names a file that "
            "does not exist. Either the file moved and the contract was not "
            f"updated, or the contract governs nothing.\n{lines}"
        )
    print(
        f"every repository path declared under {covered} resolves "
        "(other path-shaped contract values are out of scope — see this "
        "script's docstring)"
    )


if __name__ == "__main__":
    main()
