#!/usr/bin/env python3
"""PB-08 验收：发布链路跑在正式安装包内的那一个 Chromium 上。

PB-05/PB-06 已经把发布链路在**构建期暂存**出来的 Chromium 上验过了。PB-08 的增量
不是再写一遍那些用例，而是换掉它们脚下的浏览器：同一套集成测试，浏览器改成从正式
安装包的发行物 Manifest 解析出来的那一个。用户装到机器上的是包，不是暂存目录。

五个维度按 PB-08 定义逐条落实：

| 维度 | 本脚本怎么证明 |
| --- | --- |
| 单一 Chromium | 浏览器路径必须落在包内；跑完按包路径过滤进程表确认为空 |
| 单次副作用 | 集成测试里的 at-most-once 账本用例照跑，只是换了浏览器 |
| 结果对账 | 同上：发布后独立读作品列表核对的用例照跑 |
| 退出清理 | 进程与 Profile 残留检查 |
| 风控边界 | 失败矩阵用例照跑 |

**真实平台投稿不在本脚本内**：抖音需要扫码、B站需要凭据，两者都没有。那一项按项目
规则标 `🔍 待真实账号` 并继续，不用测试页冒充真实平台证据。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from build_embedded_chromium_staging import load_staging_contract  # noqa: E402
from eb_17_clean_machine import (  # noqa: E402
    CleanMachineRejected,
    require_no_browser_installer_scripts,
    scan_package_for_system_browser_references,
)
from release_assembly import (  # noqa: E402
    ReleaseAssemblyRejected,
    require_packaged_browser,
)

STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_PACKAGE = (
    REPOSITORY_ROOT
    / ".local/release/cargo-target/release/bundle/macos/自动化运营工具.app"
)

# 发布链路在真实内置浏览器上的全部用例。PB-08 换掉它们脚下的浏览器，不改断言。
PUBLISH_SUITES = (
    "tests/integration/test_douyin_publish_embedded_browser.py",
    "tests/integration/test_douyin_side_effect_recovery_browser.py",
)


def announce(message: str) -> None:
    print(f"[PB-08] {message}", flush=True)


def fail(message: str) -> None:
    print(f"PB-08 acceptance failed: {message}")
    raise SystemExit(1)


def current_target_id() -> str:
    system = platform.system()
    machine = platform.machine().casefold()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    fail(f"unsupported PB-08 host: {system}/{machine}")
    raise AssertionError("unreachable")


def package_platform(target_id: str) -> str:
    if target_id.startswith("macos-"):
        return "macos"
    if target_id.startswith("windows-"):
        return "windows"
    fail(f"unsupported package target: {target_id}")
    raise AssertionError("unreachable")


def declared_packaged_browser(
    *,
    application: Path,
    browser_root: Path,
    target_id: str,
    locked_version: str,
) -> Path:
    """从已验证的发行物 Manifest 解析浏览器，并约束在安装包根内。"""
    manifest = browser_root / "distribution-manifest.v1.json"
    if not manifest.is_file():
        fail(f"package carries no distribution manifest: {manifest}")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
        declared_target = document["target"]
        declared_version = document["runtime"]["chromium"]["browser_version"]
        relative_executable = Path(*str(document["executable"]).split("/"))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        fail(f"package distribution manifest is malformed: {error}")
    if declared_target != target_id:
        fail(f"package targets {declared_target!r}, not {target_id!r}")
    if declared_version != locked_version:
        fail(
            f"packaged browser version {declared_version!r} does not match "
            f"the locked version {locked_version!r}"
        )
    executable = (browser_root / relative_executable).resolve()
    if not executable.is_relative_to(application.resolve()):
        fail(f"resolved browser lives outside the package: {executable}")
    if not executable.is_file():
        fail(f"packaged browser executable is missing: {executable}")
    return executable


def packaged_browser_executable(application: Path, target_id: str) -> Path:
    """复用发布关口逐文件验证，再解析 macOS/Windows 各自资源根。"""
    try:
        browser_root = require_packaged_browser(
            application=application,
            target_id=target_id,
            platform=package_platform(target_id),
        )
    except ReleaseAssemblyRejected as error:
        fail(str(error))
    locked = load_staging_contract(STAGING_CONTRACT).browser_version
    return declared_packaged_browser(
        application=application,
        browser_root=browser_root,
        target_id=target_id,
        locked_version=locked,
    )


def leftover_processes(marker: str) -> list[str]:
    if os.name == "nt":
        listing = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    else:
        listing = subprocess.run(
            ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, check=False
        ).stdout
    return [line.strip() for line in listing.splitlines() if marker in line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    application = parser.parse_args().package.resolve()
    target_id = current_target_id()
    if not application.is_dir():
        fail(f"no release package at {application} — build it with run_eb_16_acceptance.py")

    announce("Checking the release package content before using it")
    try:
        scanned = scan_package_for_system_browser_references(application)
        require_no_browser_installer_scripts(application=application)
    except CleanMachineRejected as error:
        fail(str(error))
    announce(f"Package content is clean ({scanned} files scanned)")

    executable = packaged_browser_executable(application, target_id)
    announce(f"Publish chain will run on the packaged browser: {executable.name}")

    before = leftover_processes(os.fspath(application))
    environment = dict(os.environ)
    environment["AUTOMATION_TOOL_PACKAGED_BROWSER_EXECUTABLE"] = os.fspath(executable)

    announce("Running the real-browser publish suites against the packaged browser")
    result = subprocess.run(
        ["uv", "run", "pytest", *PUBLISH_SUITES, "-q"],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        fail("the publish suites failed on the packaged browser")

    leftover = [line for line in leftover_processes(os.fspath(application)) if line not in before]
    if leftover:
        fail(f"packaged browser processes survived the run: {leftover}")

    print(
        "PB-08 acceptance passed: the publish chain ran end to end on the browser inside "
        f"the release package ({target_id}); no process was left behind. Real platform "
        "posting stays 🔍 待真实账号 — douyin needs a scan-code sign-in and bilibili needs "
        "credentials, and neither may be faked with a fixture page."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
