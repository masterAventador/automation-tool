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
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion_cjk_font_probe import cjk_unicode_range

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]

# The Han code points the Chinese face claims, owned by the PC-13 probe so the
# measurement and the production rule can never drift apart.
CHINESE_UNICODE_RANGE: Final = cjk_unicode_range()
# Everything the part's own typeface keeps. Copied from the range Google Fonts
# uses for its `latin` + `latin-ext` subsets, which is what the packaged woff2
# files actually contain.
LATIN_UNICODE_RANGE: Final = (
    "U+0000-00FF, U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, "
    "U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2000-206F, "
    "U+20A0-20C0, U+2113, U+2122, U+2191, U+2193, U+2212, U+2215, U+2C60-2C7F, "
    "U+A720-A7FF, U+FEFF, U+FFFD"
)
CATALOG_CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
TYPOGRAPHY_CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/video/motion-part-typography.v1.json"
OFFLINE_LOCK_PATH: Final = REPOSITORY_ROOT / "contracts/video/offline-motion-dependencies.v1.json"
SUBMODULE_ROOT: Final = REPOSITORY_ROOT / "vendor/hyperframes"

_SCANNED_SUFFIXES: Final = frozenset({".html", ".css", ".js"})

# Family names that name a class of font rather than a font. A part naming one
# of these is asking the host for whatever it has, which is the fallback this
# whole mechanism exists to replace -- there is no face to declare.
_GENERIC_FAMILIES: Final = frozenset(
    {
        "-apple-system",
        "blinkmacsystemfont",
        "cursive",
        "emoji",
        "fangsong",
        "fantasy",
        "inherit",
        "initial",
        "math",
        "monospace",
        "revert",
        "sans-serif",
        "serif",
        "system-ui",
        "ui-monospace",
        "ui-rounded",
        "ui-sans-serif",
        "ui-serif",
        "unset",
    }
)

# CSS's initial `font-weight`. A rule that names a typeface without a weight is
# a request for 400 and needs a face declared at 400.
_DEFAULT_WEIGHT: Final = 400

_RULE_BODY: Final = re.compile(r"\{([^{}]*)\}")
_FONT_FAMILY: Final = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE)
_FONT_WEIGHT: Final = re.compile(r"font-weight\s*:\s*(\d{3})")
# `morph-text` keeps its typeface in an attribute and assigns it to
# `style.fontFamily` from script; the CSS scan alone never sees it.
_DATA_FONT: Final = re.compile(r"data-font\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


class TypographyError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"motion part typography failed: {message}")


# What the package can do about a typeface a part names. `host` is a decision
# and not an escape hatch: it has to be written down per family with a reason,
# and it is only defensible where the host font renders the text correctly
# rather than as tofu -- colour emoji, and nothing else so far.
POLICY_PACKAGED: Final = "packaged"
POLICY_SUBSTITUTED: Final = "substituted"
POLICY_HOST: Final = "host"
POLICIES: Final = frozenset({POLICY_PACKAGED, POLICY_SUBSTITUTED, POLICY_HOST})


@dataclass(frozen=True, slots=True)
class FamilyPolicy:
    """What the package does about one typeface a part names."""

    policy: str
    replacement: str | None
    reason: str
    visual_difference: str


@dataclass(frozen=True, slots=True)
class ResolvedFace:
    """One `@font-face` rule: the requested name, served by shippable bytes."""

    css_family: str
    source_family: str
    weight: int


def _family_names(stack: str) -> set[str]:
    names: set[str] = set()
    for candidate in stack.split(","):
        name = candidate.strip().strip("\"'").strip()
        if not name or name.startswith("var(") or name.startswith("$"):
            continue
        if name.lower() in _GENERIC_FAMILIES:
            continue
        names.add(name)
    return names


def part_typography(text: str) -> frozenset[tuple[str, int]]:
    """Every ``(family, weight)`` one part document requests.

    Entities are unescaped first: one part writes its font stack as
    ``&quot;Inter&quot;`` inside a style block, and a scan over the raw bytes
    reads the entity as part of the name.
    """
    source = html.unescape(text)
    pairs: set[tuple[str, int]] = set()
    for body in _RULE_BODY.findall(source):
        declaration = _FONT_FAMILY.search(body)
        if declaration is None:
            continue
        weight_match = _FONT_WEIGHT.search(body)
        weight = int(weight_match.group(1)) if weight_match else _DEFAULT_WEIGHT
        for name in _family_names(declaration.group(1)):
            pairs.add((name, weight))
    for stack in _DATA_FONT.findall(source):
        for name in _family_names(stack):
            pairs.add((name, _DEFAULT_WEIGHT))
    return frozenset(pairs)


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


def family_policies(contract: dict) -> dict[str, FamilyPolicy]:
    policies: dict[str, FamilyPolicy] = {}
    for entry in contract["families"]:
        policies[entry["family"]] = FamilyPolicy(
            entry["policy"],
            entry.get("replacement"),
            entry.get("reason", ""),
            entry.get("visualDifference", ""),
        )
    return policies


def families_without_policy(
    scanned: Mapping[str, frozenset[tuple[str, int]]], contract: dict
) -> frozenset[str]:
    """Typefaces some part names that nobody decided what to do about."""
    declared = set(family_policies(contract))
    named = {family for pairs in scanned.values() for family, _ in pairs}
    return frozenset(named - declared)


def packaged_weights(lock: dict) -> dict[str, frozenset[int]]:
    """Which weights the offline package can actually serve, per source family.

    Derived from the locked stylesheet faces, so a font file that was never
    downloaded cannot be declared against by accident.
    """
    weights: dict[str, set[int]] = {}
    for sheet in lock["stylesheets"]:
        for face in sheet["faces"]:
            weights.setdefault(face["family"], set()).add(int(face["weight"]))
    return {family: frozenset(values) for family, values in weights.items()}


def resolve_faces(
    pairs: Iterable[tuple[str, int]],
    *,
    policies: Mapping[str, FamilyPolicy],
    packaged_weights: Mapping[str, frozenset[int]],
) -> tuple[tuple[ResolvedFace, ...], tuple[tuple[str, int], ...]]:
    """Turn requests into declarable faces, and name the ones that cannot be.

    The second element is the whole reason this returns a pair: an unmet
    request has to reach a gate, because at render time it is indistinguishable
    from a part that simply looks wrong.
    """
    faces: list[ResolvedFace] = []
    unmet: list[tuple[str, int]] = []
    for family, weight in sorted(set(pairs)):
        policy = policies.get(family)
        if policy is None:
            unmet.append((family, weight))
            continue
        if policy.policy == POLICY_HOST:
            continue
        source = policy.replacement if policy.policy == POLICY_SUBSTITUTED else family
        if source is None or weight not in packaged_weights.get(source, frozenset()):
            unmet.append((family, weight))
            continue
        faces.append(ResolvedFace(css_family=family, source_family=source, weight=weight))
    return tuple(faces), tuple(unmet)


def face_artifact(lock: dict, source_family: str, weight: int) -> tuple[str, ...]:
    """The locked woff2 paths that serve one source family at one weight."""
    paths: list[str] = []
    for sheet in lock["stylesheets"]:
        for face in sheet["faces"]:
            if face["family"] == source_family and int(face["weight"]) == weight:
                if face["artifactPath"] not in paths:
                    paths.append(face["artifactPath"])
    return tuple(paths)


def part_font_css(
    faces: Sequence[ResolvedFace],
    *,
    chinese_artifact: str,
    latin_artifact: Callable[[ResolvedFace], str],
) -> str:
    """The `<style>` block injected into a part's render-time working copy.

    Two rules per requested pair, split by `unicode-range`: the part's own
    Latin typeface keeps the Latin code points, and the Chinese face takes the
    Han ones. Both rules carry the *requested* weight rather than a range,
    because PC-13 measured that Chromium picks the weight bucket first and
    never falls back to a lighter face inside the same family.
    """
    blocks: list[str] = []
    for face in faces:
        blocks.append(
            f"@font-face{{font-family:'{face.css_family}';font-style:normal;"
            f"font-weight:{face.weight};font-display:block;"
            f"src:url({latin_artifact(face)}) format('woff2');"
            f"unicode-range:{LATIN_UNICODE_RANGE};}}"
        )
        blocks.append(
            f"@font-face{{font-family:'{face.css_family}';font-style:normal;"
            f"font-weight:{face.weight};font-display:block;"
            f"src:url({chinese_artifact}) format('woff2');"
            f"unicode-range:{CHINESE_UNICODE_RANGE};}}"
        )
    return "\n".join(blocks)


def scan_digest(scanned: Mapping[str, frozenset[tuple[str, int]]]) -> str:
    """A digest of the whole request table, so drift is one comparison."""
    lines = "".join(
        f"{name} {family} {weight}\n"
        for name in sorted(scanned)
        for family, weight in sorted(scanned[name])
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()
