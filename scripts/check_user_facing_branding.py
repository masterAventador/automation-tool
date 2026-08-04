#!/usr/bin/env python3
"""Scan user-facing source surfaces for forbidden brands and unexplained terms.

Six rules are enforced against ``contracts/quality/user-facing-terminology.v1.json``:

0. the brand surface of the embedded upstream WebUI stays exactly as pinned;
1. upstream project names never reach a user-visible surface, and every
   dependency declared in ``contracts/quality/third-party-sources.v1.json`` has
   its name on that forbidden list — otherwise the scan passes by never looking;
2. no declared industry term reaches rendered copy without its plain Chinese
   wording (in the same sentence, or anywhere on the same page for the two
   vendor console field names and the product category name);
3. the concept distinctions a normal user needs (two creation methods, the 12
   overall styles, the 134 motion parts, and the separate video editing module)
   stay in the shipped source, and every English motion part name ships with a
   Chinese explanation beside it;
4. every control the user documentation tells the reader to click exists in what
   ships — the docs were the one deliverable no scan looked at, and on
   2026-08-04 that cost six wrong control names, one of them in the uninstall
   guide's first instruction.

This is a regression gate only. The delivery evidence for user comprehension is
``scripts/run_cq_01_acceptance.py``, which drives the real production App.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/user-facing-terminology.v1.json"
UPSTREAM_COMPACT_TERMS = {"moneyprinterturbo", "hyperframes"}
CHINESE = re.compile(r"[一-鿿]")
JSX_TEXT = re.compile(r">([^<>{}]*)<", re.S)
# A rendered JSX text node never carries statement punctuation; requiring its
# absence keeps generics, comparisons and arrow bodies out of the copy scan.
# Parentheses and quotes stay allowed: real copy such as
# "由本机执行器处理 (自动重试)" must not be dropped from the scan.
NOT_TEXT = re.compile(r"[;=\[\]`&|]")
# Accessibility names and other attribute copy are user-visible surfaces even
# when they hold no Chinese character, so they are always scanned.
ATTRIBUTE_COPY = re.compile(
    r"\b(?:aria-label|aria-description|alt|title|placeholder)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|\{`([^`]*)`\}|\{\"([^\"]*)\"\})"
)
INTERPOLATION = re.compile(r"\$\{[^{}]*\}")
# Rust `format!` and Python f-string placeholders. JSON braces are deliberately
# left alone: `{"subject":"新品介绍"}` is copy, not a placeholder.
NATIVE_INTERPOLATION = re.compile(r"\{[0-9A-Za-z_.\[\]]*(?::[^{}\"]*)?\}")
RAW_STRING_OPENER = re.compile(r"b?r(#*)\"")
QUOTED = re.compile(r"\"((?:[^\"\\\n]|\\.)*)\"|'((?:[^'\\\n]|\\.)*)'")
EXPLANATION_SCOPES = {"segment", "file"}
REGEX_PRECEDING = set("(,=:[!&|?{};+-*%~^\n\t ")


def fail(message: str) -> None:
    raise SystemExit(f"user-facing branding check failed: {message}")


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def compact(value: str) -> str:
    return "".join(character for character in normalize(value) if character.isalnum())


def matches_glob(relative_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def term_occurs(text: str, term: str) -> bool:
    normalized_text = normalize(text)
    normalized_term = normalize(term)
    if normalized_term in UPSTREAM_COMPACT_TERMS:
        return normalized_term in compact(text)
    if len(normalized_term) <= 4 and normalized_term.isalnum():
        return (
            re.search(
                rf"(?<![\w]){re.escape(normalized_term)}(?![\w])",
                normalized_text,
                flags=re.UNICODE,
            )
            is not None
        )
    return normalized_term in normalized_text


def industry_term_occurs(text: str, term: str) -> bool:
    """Match a declared industry term as a whole word, ignoring letter case.

    A trailing plural ``s`` is part of the match so that ``Tokens``,
    ``Profiles`` and ``Sessions`` cannot slip past the gate. This is the only
    implementation of the rule: the real App acceptance feeds its captured page
    text back through this same function instead of re-deriving the pattern.
    """
    return (
        re.search(
            rf"(?<![0-9A-Za-z]){re.escape(term)}s?(?![0-9A-Za-z])",
            text,
            flags=re.IGNORECASE,
        )
        is not None
    )


def script_literals(text: str) -> list[str]:
    """Return quoted and template literal contents of a TypeScript source.

    A small state machine is used instead of a single regular expression so
    that comments and regular expression literals cannot swallow quotes and
    silently hide user copy from the scan.
    """
    literals: list[str] = []
    index = 0
    length = len(text)
    previous_significant = "\n"
    while index < length:
        character = text[index]
        if character == "/" and index + 1 < length and text[index + 1] == "/":
            index = text.find("\n", index)
            if index == -1:
                break
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if (
            character == "/"
            and previous_significant in REGEX_PRECEDING
            # `<Foo bar={1} />` ends with `}` then `/>`: that slash closes a JSX
            # tag, and treating it as a regular expression would swallow every
            # literal after it on the same line.
            and not (previous_significant == "}" and text[index + 1 : index + 2] == ">")
        ):
            index += 1
            in_class = False
            while index < length:
                current = text[index]
                if current == "\\":
                    index += 2
                    continue
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    index += 1
                    break
                elif current == "\n":
                    break
                index += 1
            previous_significant = "/"
            continue
        if character in "\"'`":
            quote = character
            index += 1
            start = index
            while index < length:
                current = text[index]
                if current == "\\":
                    index += 2
                    continue
                if current == quote:
                    break
                if current == "\n" and quote != "`":
                    break
                index += 1
            literals.append(text[start:index])
            index += 1
            previous_significant = quote
            continue
        if not character.isspace():
            previous_significant = character
        index += 1
    return literals


def python_literals(text: str) -> list[str] | None:
    """Return a Python source's string literals, minus its docstrings.

    A docstring is a string literal, so the "literal carrying Chinese is user
    copy" rule used to demand a plain-Chinese rewrite of internal technical
    documentation. Writing
    ``pyinstaller_support.remove_browser_installer_scripts`` on 2026-07-26 hit
    exactly that: its docstring said ``Chromium`` and the gate asked for
    「浏览器组件」. The docstring was rewritten in English to get past it, which
    is the wrong outcome twice over — the copy rule learned nothing, and the
    gate had quietly created an incentive not to document in Chinese. ``#``
    comments were never affected; only docstrings, and only because of their
    form.

    Positions rather than text decide what is skipped: only the string that *is*
    a module, class or function body's first statement. A string with identical
    wording used as real copy elsewhere in the same file is still scanned, and
    ``test_user_facing_branding.py`` pins that case — without it, "docstrings
    are skipped" and "native scanning is switched off" would produce the same
    green.

    Returns ``None`` when the source does not parse, so the caller falls back to
    the shared scanner rather than skipping the file.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, documented):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def native_literals(suffix: str, text: str) -> list[str]:
    """Return the string literal contents of a Rust or Python source.

    Comments are skipped on purpose. An upstream project name is allowed in
    code, internal notes and diagnostics — it is forbidden only where a user
    can read it — so scanning a whole native file would fail on legitimate
    constants such as the worker's environment variable names.

    Python goes through the parser instead of the scanner below, so docstrings
    can be told apart from copy by position; see ``python_literals``. Rust has
    no equivalent construct — its documentation is ``///`` comments, which this
    scanner already skips — so it stays here.
    """
    if suffix == ".py":
        parsed = python_literals(text)
        if parsed is not None:
            return parsed
    literals: list[str] = []
    index = 0
    length = len(text)
    rust = suffix == ".rs"
    line_comment = "//" if rust else "#"
    while index < length:
        character = text[index]
        if text.startswith(line_comment, index):
            index = text.find("\n", index)
            if index == -1:
                break
            continue
        if rust and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if rust and character in "br":
            opener = RAW_STRING_OPENER.match(text, index)
            if opener is not None:
                terminator = '"' + opener.group(1)
                start = opener.end()
                end = text.find(terminator, start)
                end = length if end == -1 else end
                literals.append(text[start:end])
                index = end + len(terminator)
                continue
        # Rust reserves `'` for lifetimes and char literals, neither of which
        # can carry copy; Python uses it for ordinary strings.
        if character == '"' or (not rust and character == "'"):
            if not rust and text.startswith(character * 3, index):
                start = index + 3
                end = text.find(character * 3, start)
                end = length if end == -1 else end
                literals.append(text[start:end])
                index = end + 3
                continue
            index += 1
            start = index
            while index < length:
                current = text[index]
                if current == "\\":
                    index += 2
                    continue
                if current == character:
                    break
                # A Rust string may span lines; a Python single-quoted one may
                # not, so a stray quote cannot swallow the rest of the file.
                if current == "\n" and not rust:
                    break
                index += 1
            literals.append(text[start:index])
            index += 1
            continue
        index += 1
    return literals


def native_user_copy(suffix: str, text: str) -> list[str]:
    """Return the fragments of a Rust or Python source a user can read.

    A literal carrying Chinese is either operator copy or the platform text a
    page object matches; identifiers, paths and protocol values in these two
    languages are ASCII. That is what makes the distinction mechanical rather
    than a judgement call about which module is "user-facing".
    """
    segments: list[str] = []
    for literal in native_literals(suffix, text):
        cleaned = NATIVE_INTERPOLATION.sub(" ", literal)
        if CHINESE.search(cleaned):
            segments.append(cleaned)
    return segments


def artifact_name_literals(text: str, extensions: list[str]) -> list[str]:
    """Return the file names this source can put on a user's disk.

    An artifact or export name carries no Chinese, so the copy rule above
    cannot see it. A file name also cannot carry a Chinese explanation, so the
    only rule it can break is the upstream-name one, and that is all that is
    checked here.
    """
    names: list[str] = []
    for extension in extensions:
        pattern = re.compile(
            rf"[0-9A-Za-z_\-{{}}.]+{re.escape(extension)}(?![0-9A-Za-z])",
            flags=re.IGNORECASE,
        )
        names.extend(match.group(0) for match in pattern.finditer(text))
    return names


def attribute_copy(text: str) -> list[str]:
    """Return accessibility names and other attribute copy shown to users."""
    values: list[str] = []
    for match in ATTRIBUTE_COPY.finditer(text):
        raw = next(group for group in match.groups() if group is not None)
        value = INTERPOLATION.sub(" ", raw).strip()
        if value:
            values.append(value)
    return values


def jsx_text_nodes(text: str) -> list[str]:
    nodes: list[str] = []
    for match in JSX_TEXT.finditer(text):
        node = " ".join(match.group(1).split())
        if not node or NOT_TEXT.search(node):
            continue
        if CHINESE.search(node) or re.search(r"[A-Za-z]", node):
            nodes.append(node)
    return nodes


def user_copy_segments(suffix: str, text: str) -> list[str]:
    """Return the text fragments a user can actually read on screen.

    Quoted literals only count when they carry Chinese characters, because a
    pure ASCII literal in TypeScript is almost always an identifier, class name
    or protocol value. JSX text nodes always count: they are rendered verbatim,
    so an untranslated English node must not slip through.
    """
    segments: list[str] = attribute_copy(text)
    if suffix in {".ts", ".tsx"}:
        for literal in script_literals(text):
            cleaned = INTERPOLATION.sub(" ", literal)
            if CHINESE.search(cleaned):
                segments.append(cleaned)
        if suffix == ".tsx":
            segments.extend(jsx_text_nodes(text))
        return segments
    if suffix in {".html", ".htm"}:
        for match in JSX_TEXT.finditer(text):
            node = match.group(1).strip()
            if node:
                segments.append(node)
    for match in QUOTED.finditer(text):
        value = match.group(1) or match.group(2) or ""
        if CHINESE.search(value):
            segments.append(value)
    return segments


def load_contract(contract_path: Path) -> dict[str, object]:
    try:
        value = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read terminology contract: {error}")
    if not isinstance(value, dict) or value.get("version") != "user-facing-terminology.v1":
        fail("terminology contract version is invalid")
    return value


def collect_files(root: Path, roots: list[str], excluded: list[str]) -> list[Path]:
    files: set[Path] = set()
    for root_value in roots:
        scan_root = root / root_value
        if not scan_root.exists():
            fail(f"configured scan root does not exist: {root_value}")
        candidates = [scan_root] if scan_root.is_file() else scan_root.rglob("*")
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            if matches_glob(relative, excluded):
                continue
            if candidate.is_symlink():
                fail(f"user-facing source surface must not be a symlink: {relative}")
            if candidate.is_file():
                files.add(candidate)
    return sorted(files)


def string_list(contract: dict[str, object], key: str) -> list[str]:
    value = contract.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        fail(f"{key} must be a non-empty string list")
    return value  # type: ignore[return-value]


def validate_contract(contract: dict[str, object]) -> None:
    terms = string_list(contract, "forbiddenUserFacingTerms")
    if len({normalize(term) for term in terms}) != len(terms):
        fail("forbiddenUserFacingTerms must not contain normalized duplicates")
    if not isinstance(contract.get("staticScan"), dict):
        fail("staticScan policy is missing")

    string_list(contract, "videoCreationMethodCardLabels")

    mappings = contract.get("plainLanguageMappings")
    if not isinstance(mappings, dict) or not mappings:
        fail("plainLanguageMappings must be a non-empty object")
    for term, plain in mappings.items():
        if not isinstance(plain, str) or not CHINESE.search(plain):
            fail(f"{term}: plainLanguageMappings value must be Chinese wording")

    industry = contract.get("unexplainedIndustryTerms")
    if not isinstance(industry, list) or not industry:
        fail("unexplainedIndustryTerms must be a non-empty list")
    seen: set[str] = set()
    for entry in industry:
        if not isinstance(entry, dict):
            fail("unexplainedIndustryTerms entries must be objects")
        term = entry.get("term")
        scope = entry.get("explanationScope")
        if not isinstance(term, str) or not term:
            fail("unexplainedIndustryTerms entry is missing term")
        # plainLanguageMappings is the single source of the Chinese wording;
        # an enforced term without a mapping entry fails closed.
        if term not in mappings:
            fail(f"{term}: plainLanguageMappings has no wording for this term")
        if scope not in EXPLANATION_SCOPES:
            fail(f"{term}: explanationScope must be one of {sorted(EXPLANATION_SCOPES)}")
        if scope == "file" and not isinstance(entry.get("reason"), str):
            fail(f"{term}: file-scoped terms must record why the term is kept")
        if normalize(term) in seen:
            fail(f"{term}: duplicated industry term declaration")
        seen.add(normalize(term))

    distinctions = contract.get("conceptDistinctions")
    if not isinstance(distinctions, list) or not distinctions:
        fail("conceptDistinctions must be a non-empty list")
    for entry in distinctions:
        if not isinstance(entry, dict):
            fail("conceptDistinctions entries must be objects")
        for key in ("id", "displayName", "sourceFile"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                fail(f"conceptDistinctions entry is missing {key}")
        required = entry.get("requiredCopy")
        if not isinstance(required, list) or not required or not all(
            isinstance(item, str) and item for item in required
        ):
            fail(f"{entry.get('id')}: requiredCopy must be a non-empty string list")
        reference = entry.get("requiredCopyFrom")
        if reference is not None:
            if not isinstance(reference, str):
                fail(f"{entry.get('id')}: requiredCopyFrom must be a string")
            string_list(contract, reference)

    projection = contract.get("partsCatalogProjection")
    if not isinstance(projection, str) or not projection:
        fail("partsCatalogProjection must be a path string")


def scan_forbidden_terms(relative: str, text: str, terms: list[str]) -> list[str]:
    violations: list[str] = []
    for term in terms:
        if term_occurs(relative, term):
            violations.append(f"{relative}: forbidden term in path: {term}")
        if term_occurs(text, term):
            line_number = next(
                (
                    index
                    for index, line in enumerate(text.splitlines(), start=1)
                    if term_occurs(line, term)
                ),
                1,
            )
            violations.append(f"{relative}:{line_number}: forbidden term: {term}")
    return violations


def scan_forbidden_segments(
    relative: str, segments: list[str], terms: list[str], surface: str
) -> list[str]:
    """Report forbidden terms inside already-extracted user-visible fragments."""
    violations: list[str] = []
    for segment in segments:
        for term in terms:
            if term_occurs(segment, term):
                violations.append(
                    f"{relative}: forbidden term {term!r} in {surface}: "
                    f"{segment.strip()[:60]!r}"
                )
    return violations


def scan_industry_terms(
    relative: str,
    segments: list[str],
    industry: list[dict[str, str]],
    mappings: dict[str, str],
) -> list[str]:
    """Report industry terms that reach user copy without their plain wording.

    ``segment`` scope demands the plain wording in the same sentence.
    ``file`` scope accepts it anywhere in the same source file; that file is
    only an approximation of one screen, which is why the scope is named after
    the file rather than the page.
    """
    violations: list[str] = []
    file_copy = "\n".join(segments)
    for entry in industry:
        term = entry["term"]
        plain = mappings[term]
        scope = entry["explanationScope"]
        explained_in_file = plain in file_copy
        for segment in segments:
            if not industry_term_occurs(segment, term):
                continue
            if scope == "segment" and plain not in segment:
                violations.append(
                    f"{relative}: unexplained industry term {term!r} in user copy: "
                    f"{segment.strip()[:60]!r} (use {plain!r})"
                )
            elif scope == "file" and not explained_in_file:
                violations.append(
                    f"{relative}: industry term {term!r} is never explained in this "
                    f"file (add {plain!r})"
                )
    return violations


def scan_concept_distinctions(
    root: Path, distinctions: list[dict[str, object]], contract: dict[str, object]
) -> list[str]:
    violations: list[str] = []
    for entry in distinctions:
        source = root / str(entry["sourceFile"])
        if not source.is_file():
            violations.append(
                f"{entry['sourceFile']}: concept {entry['id']} has no source file"
            )
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            violations.append(f"{entry['sourceFile']}: cannot read concept source: {error}")
            continue
        expected = list(entry["requiredCopy"])  # type: ignore[arg-type]
        reference = entry.get("requiredCopyFrom")
        if isinstance(reference, str):
            expected.extend(contract[reference])  # type: ignore[arg-type]
        for required in expected:
            if str(required) not in text:
                violations.append(
                    f"{entry['sourceFile']}: concept {entry['id']} "
                    f"({entry['displayName']}) lost the copy {required!r}"
                )
    return violations


def scan_parts_projection(root: Path, projection_path: str) -> list[str]:
    path = root / projection_path
    if not path.is_file():
        return [f"{projection_path}: motion parts projection is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"{projection_path}: motion parts projection is invalid: {error}"]
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return [f"{projection_path}: motion parts projection has no items"]
    violations: list[str] = []
    for item in items:
        identifier = item.get("id", "<unknown>") if isinstance(item, dict) else "<unknown>"
        if not isinstance(item, dict):
            violations.append(f"{projection_path}: part {identifier} is not an object")
            continue
        for key in ("typeLabel", "category", "applicabilityLabel"):
            value = item.get(key)
            if not isinstance(value, str) or not CHINESE.search(value):
                violations.append(
                    f"{projection_path}: part {identifier} shows {key} without a "
                    "Chinese explanation next to its name"
                )
    return violations


def scan_policy(contract: dict[str, object], key: str) -> dict[str, object]:
    """Return a validated scan policy block."""
    policy = contract.get(key)
    if not isinstance(policy, dict):
        fail(f"{key} policy is missing")
    for field in ("roots", "excludedGlobs", "textExtensions"):
        value = policy.get(field)  # type: ignore[union-attr]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            fail(f"{key}.{field} must be a string list")
    maximum = policy.get("maximumFileBytes")  # type: ignore[union-attr]
    if not isinstance(maximum, int) or maximum < 1:
        fail(f"{key}.maximumFileBytes must be a positive integer")
    return policy  # type: ignore[return-value]


def read_scan_sources(
    root: Path, policy: dict[str, object], legal_paths: list[str]
) -> tuple[list[tuple[Path, str, str]], list[str]]:
    """Return the readable sources a policy covers, plus any read failures."""
    extensions = list(policy["textExtensions"])  # type: ignore[arg-type]
    maximum_bytes = int(policy["maximumFileBytes"])  # type: ignore[arg-type]
    sources: list[tuple[Path, str, str]] = []
    violations: list[str] = []
    for path in collect_files(
        root,
        list(policy["roots"]),  # type: ignore[arg-type]
        list(policy["excludedGlobs"]),  # type: ignore[arg-type]
    ):
        relative = path.relative_to(root).as_posix()
        if matches_glob(relative, legal_paths) or path.suffix.casefold() not in extensions:
            continue
        if path.stat().st_size > maximum_bytes:
            violations.append(f"{relative}: file exceeds {maximum_bytes} bytes")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"{relative}: user-facing source is not UTF-8")
            continue
        except OSError as error:
            violations.append(f"{relative}: cannot read user-facing source: {error}")
            continue
        sources.append((path, relative, text))
    return sources, violations


def embedded_web_ui_exposure(
    root: Path, policy: dict[str, object]
) -> tuple[list[str], str]:
    """Return the embedded WebUI's brand occurrences and their digest."""
    sources, violations = read_scan_sources(root, policy, [])
    if violations:
        fail("\n" + "\n".join(sorted(set(violations))))
    inventory = sorted(
        f"{relative}:{line.strip()}"
        for _path, relative, text in sources
        for line in text.splitlines()
        if any(term in compact(line) for term in UPSTREAM_COMPACT_TERMS)
    )
    return inventory, hashlib.sha256("\n".join(inventory).encode()).hexdigest()


def scan_embedded_web_ui(root: Path, policy: dict[str, object]) -> list[str]:
    """Pin the brand surface of the WebUI the product embeds.

    That WebUI is upstream code, so it names the upstream project in its own
    sources on purpose and cannot be scanned like ours: rule 1 would fail on
    every release. What the product guarantees instead is that the studio
    window's initialization script rewrites every occurrence before the page is
    shown, and fails closed if any survives — but only the desktop end-to-end
    run can prove that, and it needs a full App build.

    So this rule pins the exposure surface the guard was written against. An
    upstream upgrade that adds, moves or rewords a brand occurrence turns red
    here, at normal gate speed, and forces a human to re-check the guard
    instead of letting the change reach a customer's screen unnoticed.
    """
    if not policy["roots"]:
        fail("embeddedWebUiScan.roots must not be empty")
    declared_digest = policy.get("exposureSha256")
    declared_count = policy.get("exposureCount")
    if not isinstance(declared_digest, str) or len(declared_digest) != 64:
        fail("embeddedWebUiScan.exposureSha256 must be a sha256 digest")
    if not isinstance(declared_count, int) or declared_count < 0:
        fail("embeddedWebUiScan.exposureCount must be a non-negative integer")
    inventory, digest = embedded_web_ui_exposure(root, policy)
    if len(inventory) == declared_count and digest == declared_digest:
        return []
    return [
        "embedded WebUI brand surface changed: "
        f"found {len(inventory)} occurrences, sha256 {digest}; "
        f"contract declares {declared_count}, sha256 {declared_digest}. "
        "Re-check that the studio window guard still rewrites every one of "
        "them, then update embeddedWebUiScan in the terminology contract."
    ]


THIRD_PARTY_SOURCES_RELATIVE = "contracts/quality/third-party-sources.v1.json"
DOCUMENTED_LABEL = re.compile(r"[「『]([^」』\n]{2,40})[」』]")


def label_ships(label: str, haystack: str) -> bool:
    """True when the label appears in shipped text as its own run of characters.

    Plain substring containment is not enough. `平台状态` is contained in the
    product's `打开平台状态` button, so a document that calls it a *menu* — which
    the uninstall guide did — would pass while sending the reader looking for
    something that is not there. Requiring the neighbours not to be Chinese
    characters separates "the product has this label" from "some longer label
    happens to contain these characters", and it still accepts a label quoted
    inside a sentence, where the neighbours are 「」 or punctuation.
    """
    for match in re.finditer(re.escape(label), haystack):
        before = haystack[match.start() - 1] if match.start() else ""
        after = haystack[match.end()] if match.end() < len(haystack) else ""
        if CHINESE.fullmatch(before) or CHINESE.fullmatch(after):
            continue
        return True
    return False


def scan_documented_controls(root: Path, policy: Mapping[str, object]) -> list[str]:
    """Every control a user doc tells the reader to click must exist.

    The user documentation is a deliverable, and it is the one deliverable no
    gate looked at: `staticScan` and `nativeScan` cover `frontend/` and
    `backend/src`, so `docs/` had nothing watching it at all. On 2026-08-04 that
    cost six wrong control names in four documents — the uninstall guide's very
    first instruction named a menu (`平台状态`) and a button
    (`退出该平台登录并清理运营档案`) that do not exist, so a reader following it
    stops on step one. Two style names (`雏菊晴日`, `商务蓝`) were not in the
    twelve either.

    None of those are the kind of mistake the earlier checks could find: they are
    neither forbidden brands nor unexplained jargon nor wrong numbers. What makes
    them mechanically checkable is that a control named in a document must be
    named somewhere in what ships.

    Test files and fixtures are excluded from the search on purpose: `商务蓝`
    existed only as arbitrary data in a gateway test, and searching those would
    have called it real.
    """
    roots = policy.get("roots")
    search_roots = policy.get("searchRoots")
    allowed = policy.get("systemUiLabels")
    if not isinstance(roots, list) or not roots:
        fail("documentationScan.roots must not be empty")
    if not isinstance(search_roots, list) or not search_roots:
        fail("documentationScan.searchRoots must not be empty")
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        fail("documentationScan.systemUiLabels must be a string list")
    excluded = policy.get("excludedGlobs")
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        fail("documentationScan.excludedGlobs must be a string list")

    shipped: list[str] = []
    for relative in search_roots:
        base = root / str(relative)
        if not base.exists():
            return [f"documentationScan.searchRoots names a missing path: {relative}"]
        candidates = sorted(base.rglob("*")) if base.is_dir() else [base]
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            display = str(path.relative_to(root))
            if matches_glob(display, list(excluded)):
                continue
            try:
                shipped.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    haystack = "\n".join(shipped)

    permitted = set(allowed)
    violations: list[str] = []
    for relative in roots:
        document = root / str(relative)
        if not document.is_file():
            return [f"documentationScan.roots names a missing document: {relative}"]
        text = document.read_text(encoding="utf-8")
        for match in DOCUMENTED_LABEL.finditer(text):
            label = match.group(1)
            if label in permitted or label_ships(label, haystack):
                continue
            violations.append(
                f"{relative}: the document names 「{label}」 but nothing that ships "
                "contains it; a reader following this cannot find it"
            )
    return violations


def scan_upstream_name_coverage(root: Path, terms: list[str]) -> list[str]:
    """Every vendored upstream must have its name on the forbidden list.

    Rules 1 and 2 scan for the names in ``forbiddenUserFacingTerms``, which
    means this gate can only ever catch a name somebody remembered to declare.
    Nothing tied that list to the actual dependency set, so adding a third
    submodule would leave the scanner **silently blind to it** — passing not
    because the new name is absent from the UI, but because it was never looked
    for. That failure mode is indistinguishable from success in the output,
    which is exactly the kind of check this repository treats as no check.

    ``third-party-sources.v1.json`` is the authority on what is vendored
    (`scripts/check_third_party_sources.py` holds it to pinned tags), so the
    coverage question is answerable: for every source there, its id and its
    repository name must both be covered by a declared term.
    """
    path = root / THIRD_PARTY_SOURCES_RELATIVE
    if not path.is_file():
        return [f"{THIRD_PARTY_SOURCES_RELATIVE} is missing; upstream names cannot be checked"]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"{THIRD_PARTY_SOURCES_RELATIVE} is unreadable: {error}"]
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        return [f"{THIRD_PARTY_SOURCES_RELATIVE} declares no sources"]

    declared = {compact(term) for term in terms}
    violations: list[str] = []
    for entry in sources:
        if not isinstance(entry, dict):
            violations.append(f"{THIRD_PARTY_SOURCES_RELATIVE} has a malformed source entry")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            violations.append(f"{THIRD_PARTY_SOURCES_RELATIVE} has a source without an id")
            continue
        names = {identifier}
        url = entry.get("url")
        if isinstance(url, str) and url:
            names.add(url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
        for name in sorted(names):
            if compact(name) not in declared:
                violations.append(
                    f"upstream dependency {identifier!r} is not covered by "
                    f"forbiddenUserFacingTerms (missing {name!r}); the brand scan "
                    "would pass without ever looking for it"
                )
    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    arguments = parser.parse_args()
    if arguments.self_test:
        run_self_test()
        return
    root: Path = arguments.root.resolve()
    contract = load_contract(arguments.contract)
    validate_contract(contract)

    terms = list(contract["forbiddenUserFacingTerms"])  # type: ignore[arg-type]
    industry = list(contract["unexplainedIndustryTerms"])  # type: ignore[arg-type]
    distinctions = list(contract["conceptDistinctions"])  # type: ignore[arg-type]
    mappings = dict(contract["plainLanguageMappings"])  # type: ignore[arg-type]
    static = scan_policy(contract, "staticScan")
    native = scan_policy(contract, "nativeScan")
    embedded = scan_policy(contract, "embeddedWebUiScan")
    artifact_extensions = native.get("artifactFileExtensions")
    if not isinstance(artifact_extensions, list) or not all(
        isinstance(item, str) and item.startswith(".") for item in artifact_extensions
    ):
        fail("nativeScan.artifactFileExtensions must be a list of file extensions")
    legal_paths = contract.get("allowedLegalDisclosurePaths")
    if not isinstance(legal_paths, list) or not all(
        isinstance(item, str) for item in legal_paths
    ):
        fail("allowedLegalDisclosurePaths must be a string list")

    # The shipped frontend must not name an upstream project anywhere, so the
    # whole file is scanned there.
    static_sources, violations = read_scan_sources(root, static, legal_paths)
    for path, relative, text in static_sources:
        violations.extend(scan_forbidden_terms(relative, text, terms))
        segments = user_copy_segments(path.suffix.casefold(), text)
        violations.extend(scan_industry_terms(relative, segments, industry, mappings))

    # Rust and Python legitimately name the upstream projects in constants and
    # paths, so only what a user can read is scanned: the copy, and the file
    # names this code puts on their disk.
    native_sources, native_violations = read_scan_sources(root, native, legal_paths)
    violations.extend(native_violations)
    for path, relative, text in native_sources:
        segments = native_user_copy(path.suffix.casefold(), text)
        violations.extend(scan_forbidden_segments(relative, segments, terms, "user copy"))
        violations.extend(scan_industry_terms(relative, segments, industry, mappings))
        names = artifact_name_literals(text, artifact_extensions)
        violations.extend(
            scan_forbidden_segments(relative, names, terms, "artifact or export name")
        )

    violations.extend(scan_upstream_name_coverage(root, terms))
    documentation = contract.get("documentationScan")
    if documentation is not None:
        if not isinstance(documentation, dict):
            fail("documentationScan policy is invalid")
        violations.extend(scan_documented_controls(root, documentation))
    violations.extend(scan_embedded_web_ui(root, embedded))
    violations.extend(scan_concept_distinctions(root, distinctions, contract))
    violations.extend(scan_parts_projection(root, str(contract["partsCatalogProjection"])))
    if violations:
        fail("\n" + "\n".join(sorted(set(violations))))
    print(
        "user-facing branding and plain-language scan passed "
        f"({len(static_sources)} frontend, {len(native_sources)} native files)"
    )


def run_self_test() -> None:
    positive_cases = {
        "MoneyPrinterTurbo": "MoneyPrinterTurbo",
        "Money Printer Turbo": "MoneyPrinterTurbo",
        "ＭｏｎｅｙＰｒｉｎｔｅｒＴｕｒｂｏ": "MoneyPrinterTurbo",  # noqa: RUF001
        "hyper-frames": "Hyperframes",
        "这是 B-roll 画面": "B-roll",
        "先做 PoC 再继续": "PoC",
    }
    for value, term in positive_cases.items():
        if not term_occurs(value, term):
            fail(f"self-test missed forbidden term {term!r} in {value!r}")
    negative_cases = {
        "补充画面": "B-roll",
        "前期验证": "PoC",
        "epoch": "PoC",
        "智能素材成片": "MoneyPrinterTurbo",
        "品牌动效成片": "Hyperframes",
    }
    for value, term in negative_cases.items():
        if term_occurs(value, term):
            fail(f"self-test produced false positive for {term!r} in {value!r}")

    industry_positive = [
        ("等待 Executor 确认", "Executor"),
        ("Control Plane 不可用", "Control Plane"),
        ("请输入新的阿里百炼 API Key。", "API Key"),
    ]
    for value, term in industry_positive:
        if not industry_term_occurs(value, term):
            fail(f"self-test missed industry term {term!r} in {value!r}")
    industry_negative = [
        ("等待本机执行器确认", "Executor"),
        ("renderJobId", "Render"),
        ("剪辑时间轴", "Timeline"),
    ]
    for value, term in industry_negative:
        if industry_term_occurs(value, term):
            fail(f"self-test produced false positive for industry term {term!r}")
    # Plural forms used to slip past the word boundary.
    for value, term in [("剩余 Tokens 1234", "Token"), ("清理 Profiles", "Profile")]:
        if not industry_term_occurs(value, term):
            fail(f"self-test missed the plural form of {term!r} in {value!r}")

    source = (
        "// Executor comment stays out of the scan\n"
        'const pattern = /"[a-z]+"/u;\n'
        'const notice = "等待本机执行器确认";\n'
        "const node = <span>控制服务不可用</span>;\n"
        "const identifier = renderJobId;\n"
    )
    segments = user_copy_segments(".tsx", source)
    if "等待本机执行器确认" not in segments or "控制服务不可用" not in segments:
        fail(f"self-test failed to extract user copy: {segments}")
    if any("comment" in segment for segment in segments):
        fail("self-test extracted a comment as user copy")

    # Copy carrying parentheses or quotes is still rendered copy.
    bracketed = user_copy_segments(".tsx", "const a = <span>由 Executor 处理 (自动重试)</span>;\n")
    if not any(industry_term_occurs(segment, "Executor") for segment in bracketed):
        fail(f"self-test dropped bracketed rendered copy: {bracketed}")

    # A JSX self-closing tag must not be parsed as a regular expression.
    self_closing = user_copy_segments(
        ".tsx",
        'const a = <Foo bar={1} /> {x ? "等待 Executor 确认" : "已完成"};\n',
    )
    if not any(industry_term_occurs(segment, "Executor") for segment in self_closing):
        fail(f"self-test lost literals after a JSX self-closing tag: {self_closing}")

    # Accessibility names are scanned even without Chinese characters.
    attributes = user_copy_segments(
        ".tsx", 'const a = <input aria-label={`${title} API Key`} />;\n'
    )
    if not any(industry_term_occurs(segment, "API Key") for segment in attributes):
        fail(f"self-test skipped accessibility name copy: {attributes}")

    # Rust produces user copy of its own: window titles, the style names and
    # error wording. Internal constants and comments in the same file may name
    # the upstream projects, so only the copy is scanned, never the whole file.
    rust_source = (
        "//! 上游项目 MoneyPrinterTurbo 只出现在内部说明里\n"
        'const FFMPEG: &str = "HYPERFRAMES_FFMPEG_PATH";\n'
        'let window = builder.title("智能素材成片");\n'
        'let notice = "等待 Executor 确认";\n'
        'let raw = r#"{"subject":"新品介绍"}"#;\n'
    )
    rust_copy = native_user_copy(".rs", rust_source)
    if "智能素材成片" not in rust_copy:
        fail(f"self-test lost the Rust window title: {rust_copy}")
    if not any("新品介绍" in segment for segment in rust_copy):
        fail(f"self-test lost a Rust raw string literal: {rust_copy}")
    if not any(industry_term_occurs(segment, "Executor") for segment in rust_copy):
        fail(f"self-test lost Rust user copy: {rust_copy}")
    if any("HYPERFRAMES" in segment or "MoneyPrinter" in segment for segment in rust_copy):
        fail(f"self-test treated internal Rust code as user copy: {rust_copy}")

    # Python produces the approval prompt the operator reads before a real
    # action, and its selectors quote the platform's own Chinese text.
    python_source = (
        '# 内部注释可以写 MoneyPrinterTurbo\n'
        'VENDOR = "vendor/moneyprinterturbo"\n'
        'prompt = f"即将执行 {action}，目标账号 {target}"\n'  # noqa: RUF001
        'notice = "等待 Executor 确认"\n'
    )
    python_copy = native_user_copy(".py", python_source)
    if not any("即将执行" in segment for segment in python_copy):
        fail(f"self-test lost the Python approval prompt: {python_copy}")
    if not any(industry_term_occurs(segment, "Executor") for segment in python_copy):
        fail(f"self-test lost Python user copy: {python_copy}")
    if any("moneyprinterturbo" in segment for segment in python_copy):
        fail(f"self-test treated an internal Python path as user copy: {python_copy}")

    # An artifact or export name reaches the user as a file on their disk, and
    # it carries no Chinese, so it is scanned as a name rather than as copy.
    names = artifact_name_literals(
        'const OUT: &str = "brand-motion-result.mp4";\n'
        'let export = format!("automation-tool-diagnostics-{id}.zip");\n'
        'let leak = "broll-01.mp4";\n',
        [".mp4", ".zip"],
    )
    if "brand-motion-result.mp4" not in names or "broll-01.mp4" not in names:
        fail(f"self-test lost an artifact file name: {names}")
    leaked = [name for name in names if term_occurs(name, "broll")]
    if leaked != ["broll-01.mp4"]:
        fail(f"self-test did not catch an upstream artifact name: {names}")
    print("user-facing branding scanner self-test passed")


if __name__ == "__main__":
    main()
