#!/usr/bin/env python3
"""Scan user-facing source surfaces for forbidden brands and unexplained terms.

Three rules are enforced against ``contracts/quality/user-facing-terminology.v1.json``:

1. upstream project names never reach a user-visible surface;
2. no declared industry term reaches rendered copy without its plain Chinese
   wording (in the same sentence, or anywhere on the same page for the two
   vendor console field names and the product category name);
3. the concept distinctions a normal user needs (two creation methods, the 12
   overall styles, the 134 motion parts, and the separate video editing module)
   stay in the shipped source, and every English motion part name ships with a
   Chinese explanation beside it.

This is a regression gate only. The delivery evidence for user comprehension is
``scripts/run_cq_01_acceptance.py``, which drives the real production App.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import unicodedata
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
    scan = contract["staticScan"]
    roots = scan.get("roots")  # type: ignore[union-attr]
    excluded = scan.get("excludedGlobs")  # type: ignore[union-attr]
    extensions = scan.get("textExtensions")  # type: ignore[union-attr]
    maximum_bytes = scan.get("maximumFileBytes")  # type: ignore[union-attr]
    legal_paths = contract.get("allowedLegalDisclosurePaths")
    if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
        fail("staticScan.roots must be a string list")
    if not isinstance(excluded, list) or not all(
        isinstance(item, str) for item in excluded
    ):
        fail("staticScan.excludedGlobs must be a string list")
    if not isinstance(extensions, list) or not all(
        isinstance(item, str) for item in extensions
    ):
        fail("staticScan.textExtensions must be a string list")
    if not isinstance(maximum_bytes, int) or maximum_bytes < 1:
        fail("staticScan.maximumFileBytes must be a positive integer")
    if not isinstance(legal_paths, list) or not all(
        isinstance(item, str) for item in legal_paths
    ):
        fail("allowedLegalDisclosurePaths must be a string list")

    violations: list[str] = []
    scanned = 0
    for path in collect_files(root, roots, excluded):
        relative = path.relative_to(root).as_posix()
        if matches_glob(relative, legal_paths) or path.suffix.casefold() not in extensions:
            continue
        size = path.stat().st_size
        if size > maximum_bytes:
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
        scanned += 1
        violations.extend(scan_forbidden_terms(relative, text, terms))
        segments = user_copy_segments(path.suffix.casefold(), text)
        violations.extend(scan_industry_terms(relative, segments, industry, mappings))

    violations.extend(scan_concept_distinctions(root, distinctions, contract))
    violations.extend(scan_parts_projection(root, str(contract["partsCatalogProjection"])))
    if violations:
        fail("\n" + "\n".join(sorted(set(violations))))
    print(f"user-facing branding and plain-language scan passed ({scanned} files)")


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
    print("user-facing branding scanner self-test passed")


if __name__ == "__main__":
    main()
