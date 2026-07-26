#!/usr/bin/env python3
"""C10 guard against prose that silently copies a derived repository inventory."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COUNT_WORD = (
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
)


def _fail(message: str) -> None:
    raise AssertionError(message)


def _module_document(relative: str) -> str:
    source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
    return ast.get_docstring(ast.parse(source), clean=False) or ""


def _matches(patterns: tuple[str, ...], text: str) -> list[str]:
    return [
        match.group(0)
        for pattern in patterns
        for match in re.finditer(pattern, text, flags=re.IGNORECASE)
    ]


def check_current_script_prose_does_not_copy_derived_inventory_counts() -> None:
    """Script documentation must describe coverage, not snapshot tree sizes."""
    patterns_by_file = {
        "scripts/gate_prerequisites.py": (
            rf"\b{COUNT_WORD} gates?\b",
            r"\bdownloads?\s+\d+\s+digest-pinned artifacts?\b",
            r"\b~\d+(?:\.\d+)?\s*MB\b",
        ),
        "scripts/prepare_video_runtime.py": (
            rf"\b(?:the|all) {COUNT_WORD}(?: video runtime)? resources?\b",
        ),
        "scripts/run_eb_06_acceptance.py": (
            r"\ball \d+ files\b",
            r"\b~\d+(?:\.\d+)?\s*MB\b",
        ),
    }
    copied: dict[str, list[str]] = {}
    for relative, patterns in patterns_by_file.items():
        matches = _matches(patterns, _module_document(relative))
        if matches:
            copied[relative] = matches
    if copied:
        _fail(f"script prose copies inventories that are derived at runtime: {copied}")


def check_current_protocol_docs_do_not_copy_schema_or_fixture_counts() -> None:
    """Current reference docs must point at the protocol facts instead of recounting them."""
    current_references = (
        "backend/README.md",
        "frontend/README.md",
        "docs/backend-architecture.md",
        "docs/development-roadmap.md",
    )
    copied_count_patterns = (
        r"\b\d+\s*种(?:已声明\s*)?(?:v1\s*)?(?:消息|类型|message type)",
        r"\b\d+\s*个\s*(?:valid|invalid|公共\s*fixtures?)\b",
        r"\b\d+\s*种消息\b",
    )
    copied: dict[str, list[str]] = {}
    for relative in current_references:
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        matches = _matches(copied_count_patterns, text)
        if matches:
            copied[relative] = matches
    if copied:
        _fail(
            "current protocol documentation copies counts already owned by the "
            f"Pydantic union or fixture tree: {copied}"
        )


def check_release_resource_contract_does_not_name_the_next_ordinal() -> None:
    """Adding one resource must not make the contract's own guidance stale."""
    text = (
        REPOSITORY_ROOT / "contracts/quality/release-package-resources.v1.json"
    ).read_text(encoding="utf-8")
    copied = _matches(
        (
            r"\badding an? (?:first|second|third|fourth|fifth|sixth|seventh|"
            r"eighth|ninth|tenth|\d+(?:st|nd|rd|th)) resource\b",
        ),
        text,
    )
    if copied:
        _fail(f"release resource guidance copies the current inventory size: {copied}")


def check_secret_inventory_documentation_does_not_recount_file_names() -> None:
    """The secret manifest already owns which fixed files a deployment reads."""
    text = (REPOSITORY_ROOT / "deploy/secrets/README.md").read_text(encoding="utf-8")
    copied = _matches(
        (r"inventory 指定的[一二三四五六七八九十百\d]+个固定文件名",),
        text,
    )
    if copied:
        _fail(f"secret inventory documentation copies the manifest count: {copied}")


def check_backend_readme_does_not_copy_openapi_path_inventory() -> None:
    """The generated OpenAPI snapshot, not a Markdown list, owns HTTP routes."""
    text = (REPOSITORY_ROOT / "backend/README.md").read_text(encoding="utf-8")
    copied = re.findall(
        r"^- `(?:GET|POST|PUT|PATCH|DELETE) "
        r"(?:https?://127\.0\.0\.1:8765)?/api/v1/[^`]+`$",
        text,
        flags=re.MULTILINE,
    )
    if copied:
        _fail(
            "backend README copies HTTP paths already derived into the OpenAPI "
            f"snapshot: {copied}"
        )


def check_runtime_compatibility_docs_do_not_copy_contract_versions() -> None:
    """The machine-readable compatibility matrix owns all current version values."""
    backend_readme = (REPOSITORY_ROOT / "backend/README.md").read_text(encoding="utf-8")
    compatibility_paragraph = next(
        line for line in backend_readme.splitlines() if line.startswith("P9-08 的")
    )
    copied: dict[str, list[str]] = {}
    readme_versions = _matches((r"`\d+\.\d+\.\d+`",), compatibility_paragraph)
    if readme_versions:
        copied["backend/README.md"] = readme_versions

    architecture = (REPOSITORY_ROOT / "docs/backend-architecture.md").read_text(
        encoding="utf-8"
    )
    compatibility_section = architecture.split(
        "P9-08 把当前 pre-1.0 release", maxsplit=1
    )[1].split("### 安装实例", maxsplit=1)[0]
    architecture_versions = _matches(
        (r"`=?\d+\.\d+(?:\.\d+)?`",), compatibility_section
    )
    if architecture_versions:
        copied["docs/backend-architecture.md"] = architecture_versions
    if copied:
        _fail(
            "runtime compatibility documentation copies versions already owned by "
            f"contracts/protocol/runtime-compatibility-v1.json: {copied}"
        )


CHECKS = (
    check_current_script_prose_does_not_copy_derived_inventory_counts,
    check_current_protocol_docs_do_not_copy_schema_or_fixture_counts,
    check_release_resource_contract_does_not_name_the_next_ordinal,
    check_secret_inventory_documentation_does_not_recount_file_names,
    check_backend_readme_does_not_copy_openapi_path_inventory,
    check_runtime_compatibility_docs_do_not_copy_contract_versions,
)


def main() -> int:
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            failures += 1
            print(f"FAIL {check.__name__}: {error}")
        else:
            print(f"PASS {check.__name__}")
    print(f"executed checks: {len(CHECKS)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
