#!/usr/bin/env python3
"""IM-01 recursive initialization and offline source-build acceptance."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vendor/moneyprinterturbo"
CONTRACT = ROOT / "contracts/quality/moneyprinter-source-bundle.v1.json"
OFFICIAL_ORIGIN = "https://github.com/harry0703/MoneyPrinterTurbo.git"
EXPECTED_COMMIT = "b1588e1fdc6c5e54358f66ca2ff323e1dddf1364"
EXPECTED_ARCHIVE_SHA256 = "2cc9f0b65af922788a28a6c9810f12ebc9350987288d1db008c06591bd97528b"


def run(*command: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def require_static_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        value.get("tag") != "v1.3.2"
        or value.get("commit") != EXPECTED_COMMIT
        or value.get("origin") != OFFICIAL_ORIGIN
    ):
        raise AssertionError("IM-01 source version lock drifted")
    archive = value.get("archive")
    policy = value.get("policy")
    if not isinstance(archive, dict) or archive.get("sha256") != EXPECTED_ARCHIVE_SHA256:
        raise AssertionError("IM-01 archive digest lock drifted")
    if not isinstance(policy, dict) or policy != {
        "recursiveInitializationRequired": True,
        "networkRequiredToBuild": False,
        "sourceCheckoutReadOnly": True,
        "allowPatches": False,
        "allowDirtyTree": False,
        "allowRuntimeSourceUpdate": False,
    }:
        raise AssertionError("IM-01 source build policy drifted")


def require_recursive_offline_checkout_and_build() -> None:
    actual_before = run("git", "status", "--porcelain=v1", "--untracked-files=all", cwd=SOURCE)
    head_before = run("git", "rev-parse", "HEAD", cwd=SOURCE)
    with tempfile.TemporaryDirectory(prefix="im01-recursive-") as directory:
        temporary = Path(directory)
        superproject = temporary / "superproject"
        run(
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-recurse-submodules",
            str(ROOT),
            str(superproject),
            cwd=temporary,
        )
        initialized_source = superproject / "vendor/moneyprinterturbo"
        run(
            "git",
            "config",
            "submodule.vendor/moneyprinterturbo.url",
            str(SOURCE),
            cwd=superproject,
        )
        run(
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--checkout",
            "--",
            "vendor/moneyprinterturbo",
            cwd=superproject,
        )
        if run("git", "rev-parse", "HEAD", cwd=initialized_source) != EXPECTED_COMMIT:
            raise AssertionError("recursive initialization did not resolve the locked commit")
        recursive_status = run(
            "git",
            "submodule",
            "status",
            "--recursive",
            "--",
            "vendor/moneyprinterturbo",
            cwd=superproject,
        )
        if not recursive_status.startswith(f"{EXPECTED_COMMIT} "):
            raise AssertionError("recursive submodule is not initialized cleanly")
        run(
            "git",
            "remote",
            "set-url",
            "origin",
            OFFICIAL_ORIGIN,
            cwd=initialized_source,
        )

        digests: list[str] = []
        for name in ("first.tar", "second.tar"):
            output = temporary / name
            result = run(
                "python3",
                str(ROOT / "scripts/build_moneyprinter_source_bundle.py"),
                "--source",
                str(initialized_source),
                "--output",
                str(output),
                cwd=ROOT,
            )
            digest = result.rsplit("=", maxsplit=1)[-1]
            digests.append(digest)
        if digests != [EXPECTED_ARCHIVE_SHA256, EXPECTED_ARCHIVE_SHA256]:
            raise AssertionError("offline builds are not reproducible")

    if run("git", "rev-parse", "HEAD", cwd=SOURCE) != head_before:
        raise AssertionError("acceptance changed the upstream checkout")
    if run("git", "status", "--porcelain=v1", "--untracked-files=all", cwd=SOURCE) != actual_before:
        raise AssertionError("acceptance wrote into the upstream checkout")


def require_documentation_and_status() -> None:
    evidence = ROOT / "docs/development/IM-01.md"
    if not evidence.is_file():
        raise AssertionError("IM-01 evidence is missing")
    text = evidence.read_text(encoding="utf-8")
    for heading in (
        "# IM-01 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 文档变化",
    ):
        if heading not in text:
            raise AssertionError(f"IM-01 evidence is missing {heading}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| IM-01 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("IM-01 roadmap row is missing, duplicated or incomplete")


def main() -> int:
    require_static_contract()
    require_recursive_offline_checkout_and_build()
    subprocess.run(
        ["python3", "scripts/test_moneyprinter_source_bundle.py"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["python3", "scripts/check_third_party_sources.py"],
        cwd=ROOT,
        check=True,
    )
    require_documentation_and_status()
    print("IM-01 locked recursive source acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
