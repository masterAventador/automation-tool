#!/usr/bin/env python3
"""PC-13: which typeface every part asks for, and which packaged file serves it.

A part in ``vendor/hyperframes`` names its typefaces in its own CSS and the
submodule is read-only, so the renderer cannot edit the request -- it can only
answer it. Answering means, for each ``(family, weight)`` the part writes down,
declaring an ``@font-face`` under that exact name backed by bytes we are allowed
to ship.

Three facts from the PC-13 measurements shape everything here:

* **Weight is part of the key.** Chromium buckets a family's faces by weight
  before it consults ``unicode-range``; a Chinese face declared at 400 is never
  consulted for an element asking for 700, which drops that text to the host
  font. So the unit of work is the pair, not the family.
* **A missing weight is silent.** Declaring a face against a weight the package
  cannot serve fails exactly like declaring nothing at all, so the resolver
  reports unmet pairs instead of emitting a rule that cannot load.
* **Not every named typeface can be shipped.** ``SF Pro``, ``Menlo``,
  ``Segoe UI`` and ``Consolas`` are proprietary. Those names are served by a
  redistributable stand-in, declared under the name the part wrote, so the part
  is never touched and the substitution stays visible in one contract.

The scan is derived from the frozen catalog and the submodule on every run.
Nothing here is a transcribed list: a part that starts naming a new typeface
makes the gate red rather than silently rendering in the host font.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Final, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))

from automation_tool.executor.motion_authoring import (  # noqa: E402
    part_typography as _rules,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]

# The whole render-time chain — the scanner, the policies, the resolver and the
# rule generator — lives in the Executor package, because it has to run inside
# the frozen artifact and `scripts/` is not in it. What stays here is the work
# that only ever happens on a developer machine: reading the submodule, the
# contract and the lock. The names below are re-exported so this module's
# callers and gates keep one name each, with one definition behind it.
CHINESE_UNICODE_RANGE: Final = _rules.CHINESE_UNICODE_RANGE
LATIN_UNICODE_RANGE: Final = _rules.LATIN_UNICODE_RANGE
POLICY_PACKAGED: Final = _rules.POLICY_PACKAGED
POLICY_SUBSTITUTED: Final = _rules.POLICY_SUBSTITUTED
POLICY_HOST: Final = _rules.POLICY_HOST
POLICIES: Final = _rules.POLICIES
FamilyPolicy = _rules.FamilyPolicy
ResolvedFace = _rules.ResolvedFace
part_font_css = _rules.part_font_css
part_typography = _rules.requested_faces
family_policies = _rules.family_policies
packaged_weights = _rules.packaged_weights
resolve_faces = _rules.resolve_faces
face_artifact = _rules.face_artifact
CATALOG_CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
TYPOGRAPHY_CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/video/motion-part-typography.v1.json"
OFFLINE_LOCK_PATH: Final = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
SUBMODULE_ROOT: Final = REPOSITORY_ROOT / "vendor/hyperframes"

_SCANNED_SUFFIXES: Final = frozenset({".html", ".css", ".js"})


class TypographyError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"motion part typography failed: {message}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TypographyError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise TypographyError(f"{path} must contain an object")
    return value


def scan_catalog_parts(
    *, catalog_contract: dict | None = None, submodule_root: Path = SUBMODULE_ROOT
) -> dict[str, frozenset[tuple[str, int]]]:
    """Re-derive the request table from the submodule, keyed by part name.

    Read through the frozen catalog's file list rather than globbing the
    submodule, so the scan covers exactly the files that get packaged.
    """
    catalog = catalog_contract or load_json(CATALOG_CONTRACT_PATH)
    scanned: dict[str, frozenset[tuple[str, int]]] = {}
    for item in catalog["items"]:
        text = ""
        for record in item["files"]:
            path = Path(record["path"])
            if path.suffix.lower() not in _SCANNED_SUFFIXES:
                continue
            source = submodule_root / item["path"] / path
            if not source.is_file():
                raise TypographyError(f"{item['name']} source file is missing: {record['path']}")
            text += source.read_text(encoding="utf-8", errors="strict")
        scanned[item["name"]] = part_typography(text)
    return scanned


def load_typography_contract() -> dict:
    return load_json(TYPOGRAPHY_CONTRACT_PATH)


def families_without_policy(
    scanned: Mapping[str, frozenset[tuple[str, int]]], contract: dict
) -> frozenset[str]:
    """Typefaces some part names that nobody decided what to do about."""
    declared = set(family_policies(contract))
    named = {family for pairs in scanned.values() for family, _ in pairs}
    return frozenset(named - declared)


def scan_digest(scanned: Mapping[str, frozenset[tuple[str, int]]]) -> str:
    """A digest of the whole request table, so drift is one comparison."""
    lines = "".join(
        f"{name} {family} {weight}\n"
        for name in sorted(scanned)
        for family, weight in sorted(scanned[name])
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()
