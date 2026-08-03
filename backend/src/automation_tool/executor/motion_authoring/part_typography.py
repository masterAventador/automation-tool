"""Which typefaces a part asks for, answered with bytes we are allowed to ship.

A part in the release tree names its typefaces in its own CSS and is read-only,
so the renderer cannot edit the request — it can only answer it. Answering means
declaring, for each `(family, weight)` the part writes down, an `@font-face`
under that exact name.

This module is the *emission* half of PC-13. It lives in the Executor package
rather than beside the gate that discovered these facts, because emission runs
on the user's machine: `automation-tool-executor.spec` builds the frozen package
from the import graph rooted at `executor/__main__.py` with
`pathex=[backend/src]`, so nothing under `scripts/` reaches the artifact at all.
An emitter imported from there would work in every repository test and be absent
from the package a customer installs. `scripts/motion_part_typography.py` keeps
the scanning, the contract and the policies — all developer-machine work — and
imports these rules from here, the same arrangement `part_document.py` uses for
the text-node enumerator.

Three measured facts from PC-13 shape the rules themselves:

* **Weight is part of the key.** Chromium buckets a family's faces by weight
  before it consults `unicode-range`, so a Chinese face declared at 400 is never
  consulted for an element asking for 700 and that text drops to the host font.
* **A missing weight is silent.** Nothing reports it; the page renders.
* **The requested name is what gets declared.** `SF Pro` and `Menlo` cannot be
  redistributed, so a stand-in carries their bytes — but under the name the part
  wrote, because the part cannot be edited.

(The docstrings here stay in English for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation_tool.executor.motion_authoring.resources import CONTRACTS_ROOT

TYPOGRAPHY_CONTRACT_PATH: Final = CONTRACTS_ROOT / "video/motion-part-typography.v1.json"
OFFLINE_LOCK_PATH: Final = CONTRACTS_ROOT / "video/offline-motion-dependencies.v1.json"

# The code points a Chinese face must own. Latin, Latin-1 punctuation and the
# middle dot are deliberately absent: those stay with the part's own typeface,
# which is the whole point of the split.
CJK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x2E80, 0x2EFF),  # CJK radicals supplement
    (0x2F00, 0x2FDF),  # Kangxi radicals
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x30FF),  # Hiragana and Katakana (present in the SC fonts)
    (0x3100, 0x312F),  # Bopomofo
    (0x31C0, 0x31EF),  # CJK strokes
    (0x3200, 0x33FF),  # Enclosed CJK letters, CJK compatibility
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFE10, 0xFE1F),  # Vertical forms
    (0xFE30, 0xFE4F),  # CJK compatibility forms
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
)

CHINESE_UNICODE_RANGE: Final = ", ".join(f"U+{start:04X}-{end:04X}" for start, end in CJK_RANGES)

# Everything the part's own typeface keeps. Copied from the range Google Fonts
# uses for its `latin` + `latin-ext` subsets, which is what the packaged woff2
# files actually contain.
LATIN_UNICODE_RANGE: Final = (
    "U+0000-00FF, U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, "
    "U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2000-206F, "
    "U+20A0-20C0, U+2113, U+2122, U+2191, U+2193, U+2212, U+2215, U+2C60-2C7F, "
    "U+A720-A7FF, U+FEFF, U+FFFD"
)


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


@dataclass(frozen=True, slots=True)
class PackagedFaceArtifact:
    """One locked font file and only the code points it actually contains."""

    path: str
    unicode_range: str


class FontRequestUnmet(RuntimeError):
    """A part names a typeface the package cannot declare a face for.

    Not recoverable by retrying and not something the user typed wrong: the
    gate proves every part's requests are met, so meeting an unmet one here
    means this installation is not the one that was checked. Rendering anyway
    would put the text in whatever the host machine has, on one machine and not
    another, with nothing anywhere reporting it.
    """


def _object_entries(
    document: Mapping[str, object],
    field: str,
) -> tuple[Mapping[str, object], ...]:
    raw = document.get(field)
    if not isinstance(raw, list) or any(not isinstance(entry, dict) for entry in raw):
        raise FontRequestUnmet(f"a packaged typography contract has invalid {field}")
    return tuple(entry for entry in raw if isinstance(entry, dict))


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


def requested_faces(text: str) -> frozenset[tuple[str, int]]:
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


def family_policies(contract: Mapping[str, object]) -> dict[str, FamilyPolicy]:
    policies: dict[str, FamilyPolicy] = {}
    for entry in _object_entries(contract, "families"):
        family = entry.get("family")
        policy = entry.get("policy")
        replacement = entry.get("replacement")
        reason = entry.get("reason", "")
        visual_difference = entry.get("visualDifference", "")
        if (
            not isinstance(family, str)
            or policy not in POLICIES
            or (replacement is not None and not isinstance(replacement, str))
            or not isinstance(reason, str)
            or not isinstance(visual_difference, str)
        ):
            raise FontRequestUnmet("a packaged typography family policy is invalid")
        assert isinstance(policy, str)
        policies[family] = FamilyPolicy(
            policy,
            replacement,
            reason,
            visual_difference,
        )
    return policies


def packaged_weights(lock: Mapping[str, object]) -> dict[str, frozenset[int]]:
    """Which weights the offline package can actually serve, per source family.

    Derived from the locked stylesheet faces, so a font file that was never
    downloaded cannot be declared against by accident.
    """
    weights: dict[str, set[int]] = {}
    for sheet in _object_entries(lock, "stylesheets"):
        for face in _object_entries(sheet, "faces"):
            family = face.get("family")
            raw_weight = face.get("weight")
            if not isinstance(family, str) or not isinstance(raw_weight, (int, str)):
                raise FontRequestUnmet("a packaged typography face is invalid")
            try:
                weight = int(raw_weight)
            except ValueError as error:
                raise FontRequestUnmet("a packaged typography face weight is invalid") from error
            weights.setdefault(family, set()).add(weight)
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


def face_artifact(
    lock: Mapping[str, object], source_family: str, weight: int
) -> tuple[PackagedFaceArtifact, ...]:
    """The locked woff2 paths and exact ranges for one family and weight."""
    matches: list[tuple[bool, PackagedFaceArtifact]] = []
    for sheet in _object_entries(lock, "stylesheets"):
        for face in _object_entries(sheet, "faces"):
            family = face.get("family")
            raw_weight = face.get("weight")
            if not isinstance(raw_weight, (int, str)):
                raise FontRequestUnmet("a packaged typography face weight is invalid")
            try:
                face_weight = int(raw_weight)
            except ValueError as error:
                raise FontRequestUnmet("a packaged typography face weight is invalid") from error
            if family == source_family and face_weight == weight:
                path = face.get("artifactPath")
                unicode_range = face.get("unicodeRange")
                if not isinstance(path, str) or not isinstance(unicode_range, str):
                    raise FontRequestUnmet("a packaged font face has no path or unicode range")
                artifact = PackagedFaceArtifact(path=path, unicode_range=unicode_range)
                if any(existing == artifact for _, existing in matches):
                    continue
                # Put the basic subset first for stable output, but retain every
                # subset because each file may only claim its locked range.
                basic_latin = face.get("subset") == "latin" or "U+0000" in str(unicode_range)
                matches.append((basic_latin, artifact))
    return tuple(artifact for _, artifact in sorted(matches, key=lambda item: not item[0]))


def _load(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FontRequestUnmet("a packaged typography contract is unreadable") from error
    if not isinstance(value, dict):
        raise FontRequestUnmet("a packaged typography contract is not an object")
    return value


def packaged_typography_contract() -> Mapping[str, object]:
    return _load(TYPOGRAPHY_CONTRACT_PATH)


def packaged_offline_lock() -> Mapping[str, object]:
    return _load(OFFLINE_LOCK_PATH)


def document_font_css(
    text: str,
    *,
    typography_contract: Mapping[str, object] | None = None,
    offline_lock: Mapping[str, object] | None = None,
    artifact_prefix: str = "",
) -> str:
    """Every rule this one document needs, or a refusal naming what is missing.

    This is the render-time entry point: the document is the same one being
    copied into the workspace, so what it asks for is read from it rather than
    from a table that could describe a different build of the same part.

    Both contracts default to the ones that ship beside the binary. Requiring a
    caller to supply them would mean the only code able to render is code that
    knows where the package put its own files — the build-time switch on where
    to look that this project has already paid for once.

    `artifact_prefix` is what the artifact paths are relative *to*. The contract
    records them from the catalog root, while the rules are injected into a
    document that sits two levels down at `items/<name>/`; emitting them
    unprefixed produced URLs that resolve to nothing, and a font that fails to
    load is the silent host-font fallback PC-13 exists to remove.
    """
    if typography_contract is None:
        typography_contract = packaged_typography_contract()
    if offline_lock is None:
        offline_lock = packaged_offline_lock()
    policies = family_policies(typography_contract)
    weights = packaged_weights(offline_lock)
    faces, unmet = resolve_faces(requested_faces(text), policies=policies, packaged_weights=weights)
    if unmet:
        named = ", ".join(f"{family} {weight}" for family, weight in unmet)
        raise FontRequestUnmet(f"the package cannot declare a face for: {named}")
    chinese = artifact_prefix + str(
        typography_contract["chineseFace"]["artifactPath"]  # type: ignore[index]
    )

    blocks: list[str] = []
    for face in faces:
        artifacts = face_artifact(offline_lock, face.source_family, face.weight)
        if not artifacts:
            raise FontRequestUnmet(f"no packaged file serves {face.source_family} {face.weight}")
        for artifact in artifacts:
            blocks.append(
                _font_face_rule(
                    face,
                    artifact=artifact_prefix + artifact.path,
                    unicode_range=artifact.unicode_range,
                )
            )
        blocks.append(
            _font_face_rule(
                face,
                artifact=chinese,
                unicode_range=CHINESE_UNICODE_RANGE,
            )
        )
    return "\n".join(blocks)


def cjk_codepoints() -> frozenset[int]:
    """Every code point the Chinese face claims, as one set."""
    return frozenset(code for start, end in CJK_RANGES for code in range(start, end + 1))


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
            _font_face_rule(
                face,
                artifact=latin_artifact(face),
                unicode_range=LATIN_UNICODE_RANGE,
            )
        )
        blocks.append(
            _font_face_rule(
                face,
                artifact=chinese_artifact,
                unicode_range=CHINESE_UNICODE_RANGE,
            )
        )
    return "\n".join(blocks)


def _font_face_rule(
    face: ResolvedFace,
    *,
    artifact: str,
    unicode_range: str,
) -> str:
    return (
        f"@font-face{{font-family:'{face.css_family}';font-style:normal;"
        f"font-weight:{face.weight};font-display:block;"
        f"src:url({artifact}) format('woff2');"
        f"unicode-range:{unicode_range};}}"
    )


__all__ = [
    "CHINESE_UNICODE_RANGE",
    "CJK_RANGES",
    "LATIN_UNICODE_RANGE",
    "POLICIES",
    "POLICY_HOST",
    "POLICY_PACKAGED",
    "POLICY_SUBSTITUTED",
    "FamilyPolicy",
    "FontRequestUnmet",
    "ResolvedFace",
    "cjk_codepoints",
    "document_font_css",
    "face_artifact",
    "family_policies",
    "packaged_weights",
    "part_font_css",
    "requested_faces",
    "resolve_faces",
]
