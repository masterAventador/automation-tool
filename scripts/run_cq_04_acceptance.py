#!/usr/bin/env python3
"""CQ-04 验收：专项终验，如实说清哪些真跑了、哪些跑不了。

CQ-04 要的是"从全新安装的真实 App 一句话创建两类视频、送入独立阿里云剪辑、预览、
成片入库，再用抖音 Browser Use 完成真实发布"。这条链路上有三处外部条件：

| 条件 | 本机 | 影响 |
| --- | --- | --- |
| 百炼模型密钥 | 有 | 一句话生成脚本可跑 |
| 阿里云剪辑密钥 | 有 | 只证明供应商凭据可达 |
| 独立剪辑生产装配 | 缺失 | 正式 App 仍使用 sessionStorage Gateway |
| 抖音创作者账号 | **没有**（要扫码） | 真实发布跑不了 |

所以本脚本不假装跑完了整条链路。它做三件事：

1. **探测外部条件与生产装配**——不解析密钥内容，且不把文件存在当成 wiring 完成；
2. **跑不依赖缺失条件的那些段**，用的是真实正式包；
3. **核对台账没有虚标**——每个 `🔍 待验收` 的任务必须说得出自己缺什么。

第 3 条是 CQ-04 独有的：作为终验，它必须能回答"这 87 项里哪些是真完成的"。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from cq_04_ledger_honesty import (  # noqa: E402
    LedgerHonestyRejected,
    require_status_matches_evidence,
)
from cq_04_vertical_readiness import (  # noqa: E402
    VerticalReadinessRejected,
    video_editing_production_wiring_gaps,
)

ROADMAP = REPOSITORY_ROOT / "docs/embedded-browser-video-studio-roadmap.md"
EVIDENCE_DIRECTORY = REPOSITORY_ROOT / "docs/development"
SECRETS = REPOSITORY_ROOT / ".local/secrets"
PRODUCTION_MAIN = REPOSITORY_ROOT / "frontend/src/main.tsx"
PRODUCTION_WIRING_TEST = (
    REPOSITORY_ROOT / "frontend/src/app/production-wiring.test.ts"
)

_TASK_ROW = re.compile(
    r"^\|\s*([A-Z]{2}-\d{2})\s*\|.*\|\s*([⬜🧪🚧🔍✅⏸][^|]*)\|\s*$", re.M
)
_NOT_ACTIVATED = ("⬜", "⏸")

# 外部条件：只看文件在不在，不读内容——密钥不该进入任何日志或报告。
EXTERNAL_CONDITIONS = {
    "百炼模型密钥": SECRETS / "bailian-model.json",
    "阿里云剪辑密钥": SECRETS / "aliyun-video-editing.json",
}

# 不依赖缺失外部条件、且跑在真实正式包上的段。
# 第三个字段是解释器：要用 Playwright 驱动浏览器的段必须走 backend 环境，
# 系统 python3 里没有它——第一次跑就是在这里 ModuleNotFoundError 的。
BACKEND_PYTHON = REPOSITORY_ROOT / "backend/.venv/bin/python"
PACKAGE_SEGMENTS = (
    ("正式包只用包内浏览器、不多出第二套", "scripts/run_eb_17_acceptance.py", True),
    ("三条业务线并发共用一个包内浏览器", "scripts/run_cq_03_acceptance.py", False),
    ("发布链路跑在包内浏览器上", "scripts/run_pb_08_acceptance.py", False),
)

# 用户界面不泄漏上游名，与第三方声明页的投影一致性。
CONTENT_GATES = (
    ("用户界面上游名称扫描", "scripts/check_user_facing_branding.py"),
    ("第三方软件声明投影", "scripts/check_third_party_notice_ui_projection.py"),
    ("第三方源码锁与 SBOM", "scripts/check_third_party_sources.py"),
    ("专项台账状态门禁", "scripts/check_embedded_browser_video_roadmap.py"),
)


def announce(message: str) -> None:
    print(f"[CQ-04] {message}", flush=True)


def fail(message: str) -> None:
    print(f"CQ-04 acceptance failed: {message}")
    raise SystemExit(1)


def probe_external_conditions() -> dict[str, bool]:
    available = {}
    for name, path in EXTERNAL_CONDITIONS.items():
        present = path.is_file() and path.stat().st_size > 0
        available[name] = present
        announce(f"{name}: {'可用' if present else '缺失'}")
    # 抖音要人工扫码，没有"文件在不在"这种判据；它一律算缺失，
    # 由本脚本如实报告，不用测试页冒充。
    available["抖音创作者账号"] = False
    announce("抖音创作者账号: 缺失（需人工扫码，不可自动化获取）")
    return available


def probe_production_readiness() -> dict[str, bool]:
    """凭据之外，正式 App 自己也必须真的把能力装进去。"""
    try:
        gaps = video_editing_production_wiring_gaps(
            PRODUCTION_MAIN,
            PRODUCTION_WIRING_TEST,
            ROOT / "frontend/src-tauri/src/video_editing_workspace.rs",
        )
    except VerticalReadinessRejected as error:
        fail(str(error))
    ready = not gaps
    if ready:
        announce("独立视频剪辑生产装配: 已闭合")
    else:
        announce("独立视频剪辑生产装配: 缺失（" + "; ".join(gaps) + "）")
    return {"独立视频剪辑生产装配": ready}


def sweep_the_ledger() -> int:
    """核对每个已激活任务的状态与它自己的证据内容自洽。"""
    roadmap = ROADMAP.read_text(encoding="utf-8")
    rows = _TASK_ROW.findall(roadmap)
    if len(rows) != 87:
        fail(f"expected 87 task rows in the specialized roadmap, found {len(rows)}")
    problems: list[str] = []
    checked = 0
    for task_id, status in rows:
        status = status.strip()
        if status.startswith(_NOT_ACTIVATED):
            continue
        checked += 1
        try:
            require_status_matches_evidence(
                task_id, status, EVIDENCE_DIRECTORY / f"{task_id}.md"
            )
        except LedgerHonestyRejected as error:
            problems.append(str(error))
    if problems:
        fail("the ledger disagrees with itself:\n  " + "\n  ".join(problems))
    if checked == 0:
        fail("no activated task was checked — the ledger sweep proved nothing")
    return checked


def run_segment(label: str, script: str, *, needs_backend: bool = False) -> None:
    announce(f"Running: {label}")
    interpreter = BACKEND_PYTHON if needs_backend else Path(sys.executable)
    if needs_backend and not interpreter.is_file():
        fail(f"{label} needs the backend environment at {interpreter}")
    result = subprocess.run(
        [str(interpreter), script], cwd=REPOSITORY_ROOT, check=False
    )
    if result.returncode != 0:
        fail(f"{label} failed ({script})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-package-segments",
        action="store_true",
        help="只做条件探测、内容门禁与台账核对，不跑需要正式包的段",
    )
    arguments = parser.parse_args()

    announce("Probing the external conditions this vertical needs")
    available = probe_external_conditions()
    available.update(probe_production_readiness())

    for label, script in CONTENT_GATES:
        run_segment(label, script)

    if arguments.skip_package_segments:
        announce("Skipping the package segments by request")
    else:
        for label, script, needs_backend in PACKAGE_SEGMENTS:
            run_segment(label, script, needs_backend=needs_backend)

    announce("Sweeping the ledger for status that disagrees with its own evidence")
    checked = sweep_the_ledger()
    announce(f"{checked} activated tasks all state what they are still missing")

    missing = [name for name, present in available.items() if not present]
    print(
        "CQ-04 acceptance passed for everything the host can reach: content gates, "
        f"the release-package segments, and a {checked}-task ledger sweep. "
        f"Still unreachable here: {', '.join(missing)} — the vertical is NOT complete, "
        "and no fixture may stand in for it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
