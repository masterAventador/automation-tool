#!/usr/bin/env python3
"""Scan user-facing source surfaces for forbidden brands and unexplained terms."""

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


def load_contract() -> dict[str, object]:
    try:
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read terminology contract: {error}")
    if not isinstance(value, dict) or value.get("version") != "user-facing-terminology.v1":
        fail("terminology contract version is invalid")
    return value


def collect_files(roots: list[str], excluded: list[str]) -> list[Path]:
    files: set[Path] = set()
    for root_value in roots:
        root = REPOSITORY_ROOT / root_value
        if not root.exists():
            fail(f"configured scan root does not exist: {root_value}")
        candidates = [root] if root.is_file() else root.rglob("*")
        for candidate in candidates:
            relative = candidate.relative_to(REPOSITORY_ROOT).as_posix()
            if matches_glob(relative, excluded):
                continue
            if candidate.is_symlink():
                fail(f"user-facing source surface must not be a symlink: {relative}")
            if candidate.is_file():
                files.add(candidate)
    return sorted(files)


def validate_contract(contract: dict[str, object]) -> tuple[list[str], dict[str, object]]:
    terms = contract.get("forbiddenUserFacingTerms")
    static_scan = contract.get("staticScan")
    if not isinstance(terms, list) or not terms or not all(
        isinstance(term, str) and term for term in terms
    ):
        fail("forbiddenUserFacingTerms must be a non-empty string list")
    if len({normalize(term) for term in terms}) != len(terms):
        fail("forbiddenUserFacingTerms must not contain normalized duplicates")
    if not isinstance(static_scan, dict):
        fail("staticScan policy is missing")
    return terms, static_scan


def run_self_test() -> None:
    positive_cases = {
        "MoneyPrinterTurbo": "MoneyPrinterTurbo",
        "Money Printer Turbo": "MoneyPrinterTurbo",
        "ＭｏｎｅｙＰｒｉｎｔｅｒＴｕｒｂｏ": "MoneyPrinterTurbo",
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
    print("user-facing branding scanner self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        run_self_test()
        return
    contract = load_contract()
    terms, scan = validate_contract(contract)
    roots = scan.get("roots")
    excluded = scan.get("excludedGlobs")
    extensions = scan.get("textExtensions")
    maximum_bytes = scan.get("maximumFileBytes")
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
    for path in collect_files(roots, excluded):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
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
        scanned += 1
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
    if violations:
        fail("\n" + "\n".join(sorted(set(violations))))
    print(f"user-facing branding and plain-language scan passed ({scanned} files)")


if __name__ == "__main__":
    main()
