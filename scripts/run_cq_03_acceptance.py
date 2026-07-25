#!/usr/bin/env python3
"""CQ-03 验收：三条业务线并发共用一个浏览器二进制，互不干扰。

产品把**一套** Chromium 同时给三条线用：

| 线 | Profile | 输入可信度 |
| --- | --- | --- |
| RPA 运营 | App 私有持久 Profile，装着用户的平台登录态 | 平台网页，不可信 |
| Browser Use | 每次新建的临时 Profile | 网页内容 + 模型输出，不可信 |
| 动效渲染 | 每次新建的临时 Profile | 生成的 HTML，不可信 |

共用二进制是有意的（EB-02 验过一套就够），但三者的**进程与 Profile 必须互不相交**。
后两条线的输入都不可信，一旦它们能写到运营 Profile，一次渲染崩溃或一段注入网页就能
带走用户的平台登录态。

本脚本真的把三条线同时跑起来（都用正式包内的那一个 Chromium），并断言：

1. 三者用的是**同一个**二进制——不是各自下载了一套；
2. 三者的 Profile 路径互不相交、互不嵌套；
3. 并发期间运营 Profile **逐文件指纹未变**；
4. 杀掉其中一条线，另外两条**照常存活**（进程树互相独立）；
5. 全部退出后，本次从包内启动的进程一个不剩。

不涉及真实平台账号，也不产生任何外部副作用。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from cq_03_concurrent_isolation import (  # noqa: E402
    ConcurrentIsolationRejected,
    directory_fingerprint,
    require_disjoint_profiles,
    require_untouched,
)

DEFAULT_PACKAGE = (
    REPOSITORY_ROOT
    / ".local/eb-16/run/cargo-target/release/bundle/macos/自动化运营工具.app"
)
LINE_NAMES = ("operations", "browser_use", "render")
SETTLE_SECONDS = 2.0


def announce(message: str) -> None:
    print(f"[CQ-03] {message}", flush=True)


def fail(message: str) -> None:
    print(f"CQ-03 acceptance failed: {message}")
    raise SystemExit(1)


def packaged_browser(application: Path) -> Path:
    manifest = (
        application / "Contents/Resources/embedded-browser/distribution-manifest.v1.json"
    )
    if not manifest.is_file():
        fail(f"package carries no distribution manifest: {manifest}")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    executable = (
        application / "Contents/Resources/embedded-browser" / document["executable"]
    )
    if not executable.is_file():
        fail(f"packaged browser executable is missing: {executable}")
    return executable.resolve()


class BrowserLine:
    """一条业务线：一个独立 Profile 上的浏览器进程。"""

    def __init__(self, name: str, executable: Path, profile: Path) -> None:
        self.name = name
        self.executable = executable
        self.profile = profile
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.profile.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [
                os.fspath(self.executable),
                "--headless=new",
                f"--user-data-dir={os.fspath(self.profile)}",
                "--no-first-run",
                "--no-default-browser-check",
                "--use-mock-keychain",
                "--remote-debugging-port=0",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        """拆掉整个进程组，不只是主进程。

        Chromium 会 fork 出一串 helper 进程。只 terminate 主进程会留下它们——
        第一次跑就是这么失败的（两个 helper 活了下来）。`start_new_session=True`
        让每条线自成进程组，所以这里按组杀，这也正是产品侧要保证的语义。
        """
        if self.process is None or self.process.poll() is not None:
            return
        group = os.getpgid(self.process.pid)
        for signal_number in (signal.SIGTERM, signal.SIGKILL):
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(group, signal_number)
            try:
                self.process.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue


def seed_operations_profile(profile: Path) -> None:
    """造一个"已登录"的运营 Profile，内容静止。

    真实 Profile 里这个位置放的是平台会话。这里放的是等长的占位内容——本脚本不需要
    真实登录态，需要的是"有东西可被碰"。
    """
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    (profile / "Default/Cookies").write_bytes(b"placeholder platform session\n")
    (profile / "Default/Preferences").write_bytes(b'{"placeholder": true}\n')
    (profile / "Local State").write_bytes(b'{"placeholder": true}\n')


def processes_from(marker: str) -> list[int]:
    listing = subprocess.run(
        ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, check=False
    ).stdout
    pids: list[int] = []
    for line in listing.splitlines():
        if marker not in line:
            continue
        head = line.strip().split(None, 1)[0]
        if head.isdigit():
            pids.append(int(head))
    return pids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    application = parser.parse_args().package.resolve()
    if platform.system() != "Darwin":
        fail("this acceptance currently covers macOS only")
    if not application.is_dir():
        fail(f"no release package at {application} — build it with run_eb_16_acceptance.py")

    executable = packaged_browser(application)
    announce(f"All three lines will share one binary: {executable.name}")

    # 只对**本次**启动的进程判定残留。上一轮失败留下的进程会一直复现同样的 pid，
    # 把它们算进来会让后续每一次运行都失败，而那不是本次运行的问题。
    preexisting = set(processes_from(os.fspath(executable)))
    if preexisting:
        announce(f"Ignoring {len(preexisting)} pre-existing process(es) from an earlier run")

    deterministic = subprocess.run(
        [sys.executable, "scripts/test_cq_03_concurrent_isolation.py"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic isolation gate failed")

    with tempfile.TemporaryDirectory(prefix="automation-tool-cq03-", dir="/private/tmp") as raw:
        base = Path(raw)
        profiles = {name: base / name for name in LINE_NAMES}
        try:
            require_disjoint_profiles(profiles)
        except ConcurrentIsolationRejected as error:
            fail(str(error))
        announce("Profiles are disjoint and non-nesting")

        lines = [BrowserLine(name, executable, profiles[name]) for name in LINE_NAMES]
        operations, browser_use, render = lines

        # --- 阶段一：运营 Profile 静止，另外两条线并发跑 -----------------------
        #
        # 这是真正要防的事：用户没在做 RPA，但平台登录态就在磁盘上；此时跑渲染或
        # Browser Use，两者的输入（生成的 HTML、网页内容 + 模型输出）都不可信，
        # 绝不能碰到那个 Profile。
        #
        # 第一版判据是"三条线一起跑，运营 Profile 不变"——那是错的：运营浏览器
        # 自己在跑，它当然一直在写自己的缓存、会话和数据库，一跑就是几十个文件变化。
        # 把运营线的**浏览器**停下、只留 Profile，才问得出"别人有没有碰它"。
        seed_operations_profile(operations.profile)
        before = directory_fingerprint(operations.profile)
        # 落空防护：指纹为空时"未被碰过"是空对空，什么都没证明。
        if not before:
            fail("the operations profile fingerprint is empty — the comparison proves nothing")

        announce("Operations profile is at rest; starting the other two lines concurrently")
        threads = [threading.Thread(target=line.start) for line in (browser_use, render)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        time.sleep(SETTLE_SECONDS)
        for line in (browser_use, render):
            if not line.alive():
                fail(f"the {line.name} line died during the concurrent run")

        try:
            require_untouched(
                operations.profile, before, directory_fingerprint(operations.profile)
            )
        except ConcurrentIsolationRejected as error:
            fail(str(error))
        announce("Neither line touched the operations profile — byte for byte unchanged")

        # --- 阶段二：三条线同时在跑，验证进程树互相独立 -----------------------
        operations.start()
        time.sleep(SETTLE_SECONDS)
        for line in lines:
            if not line.alive():
                fail(f"the {line.name} line is not running in the three-way phase")
        announce("All three lines are running at the same time")

        announce("Killing the Browser Use line; the other two must survive")
        browser_use.stop()
        time.sleep(SETTLE_SECONDS)
        if browser_use.alive():
            fail("the Browser Use line refused to stop")
        for survivor in (operations, render):
            if not survivor.alive():
                fail(f"killing one line took down {survivor.name} — the trees are not independent")
        announce("Killing one line left the other two running")

        for line in lines:
            line.stop()

    deadline = time.monotonic() + 30
    leftover = [pid for pid in processes_from(os.fspath(executable)) if pid not in preexisting]
    while leftover and time.monotonic() < deadline:
        time.sleep(0.5)
        leftover = [
            pid for pid in processes_from(os.fspath(executable)) if pid not in preexisting
        ]
    if leftover:
        fail(f"packaged browser processes survived the run: {leftover}")

    print(
        "CQ-03 acceptance passed: three lines shared one packaged Chromium binary with "
        "disjoint profiles and independent process trees; killing one left the others "
        "running, the operations profile was never touched, and nothing was left behind"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
