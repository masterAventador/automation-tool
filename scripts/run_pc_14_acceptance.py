#!/usr/bin/env python3
"""PC-14 acceptance: 超长文案在真实包内 Chromium 里量出来、被一轮修复救回、修不动就拒。

三个场景，全部走执行器的真实授权书入口（`run_motion_authoring_entry`）+ 真实
release 目录 + 真实包内 Chromium 探针。S1/S2 的模型是脚本化双响应——要让「第一轮
必然超长」可复现，只有握住模型的笔；被验收的真实边界（浏览器测量、修复消息、
守卫、拒绝）全部真实。S3 换真实百炼模型把完整入口跑一遍。

S1 超长→修复→真渲染：约 40 个汉字进 liquid-glass-media-controls 的
   324px/22px 定宽标题槽，真探针在末帧量出横向溢出，修复消息带宽度与字号；
   短文案第二轮过；随后用生产 Worker 真渲染修复后的零件段（全帧数），
   断言帧存在且非静帧。
S2 修复仍超：第二轮仍超长 → 关死为
   agent_copy_overflows_its_slot_after_the_repair_round，不出片。
S3 真模型：真实凭据走完整入口，必须 authored；是否触发修复轮如实记录
   （模型自己写多长控制不了，这一项记录事实，不假装确定性）。

Usage:
    backend/.venv/bin/python scripts/run_pc_14_acceptance.py
    backend/.venv/bin/python scripts/run_pc_14_acceptance.py --skip-real-model
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

from run_bm_16_acceptance import (  # noqa: E402
    _render_once,
    _stage_chromium,
    stage_release_directory,
)

from automation_tool.executor.motion_authoring.entry import (  # noqa: E402
    MotionAuthoringEntryRejected,
    run_motion_authoring_entry,
)

EVIDENCE = ROOT / ".local/embedded-browser-video-studio/pc-14-evidence"
DEFAULT_SECRET = ROOT / ".local/secrets/bailian-model.json"

# 零件与槽是实测选出来的，不是照样式表猜的。2026-07-30 用超长文案扫过全部 37 个
# 有冻结槽位表的零件：大多数容器跟着内容长开、量不出盒级溢出（lt-clean-bar 的横幅
# 40 个汉字长到 1091px 仍装得下 1920 舞台——那不是溢出，判定放行是对的）；真会在
# 盒级夹住长文案的是定宽盒。liquid-glass-media-controls 槽 46（标题位，324px @
# 22px 定宽）实测：原文 (324,324,26,26)，超长 (880,324,…) 横向超出 556px，短中文
# (324,324,32,32) 贴合——正是「超长被拒、改短能救」的靶子。
PART = "liquid-glass-media-controls"
OVERFLOW_SLOT = 46
LONG_HEADLINE = "本季度华东华南华北三大区域销售额环比增长百分之十八点五创历史同期最高纪录再创新高"
SHORT_HEADLINE = "环比上升"


def _beat(headline: str) -> dict[str, object]:
    return {
        "beat_id": "hook",
        "purpose": "标题引入",
        "start_seconds": 0.0,
        "duration_seconds": 6.0,
        "catalog_parts": [PART],
        "layout": "title",
        "headline": headline,
        # 留空：不填的槽不判（维持零件原文案），场景只动一个变量。
        "body": "",
        "items": [],
    }


def _first_reply(headline: str) -> str:
    return json.dumps(
        {
            "design": {
                "style_preset_id": "blue-professional",
                "primary_color": "#0b1f3a",
                "secondary_color": "#2f6fd6",
                "typography": "克制无衬线，标题加粗",
            },
            "script": {
                "one_message": "本周销售额环比增长",
                "language": "zh",
                "beats": ["标题引入"],
            },
            "storyboard": {"beats": [_beat(headline)]},
        }
    )


def _repair_reply(headline: str) -> str:
    return json.dumps({"storyboard": {"beats": [_beat(headline)]}})


class ScriptedModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(
        self,
        _config: object,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: int,
        thinking: bool = True,
    ) -> str:
        # 深拷贝：agent 原地扩展同一个 messages 列表，浅存引用会让第一轮的
        # 记录被第二轮改写。
        self.calls.append([dict(message) for message in messages])
        if not self._responses:
            raise AssertionError("scripted model ran out of responses")
        return self._responses.pop(0)


def _workspace(run_root: Path, name: str) -> Path:
    workspace = run_root / name
    runtime = workspace / "runtime"
    runtime.mkdir(parents=True)
    # 模板 composition 引用的动画运行时。本验收只渲零件段（零件用目录自带的
    # offline-deps 真 GSAP），模板段不渲，这里存在即可过静态门禁。
    (runtime / "gsap.min.js").write_text("/* runtime stub */\n", encoding="utf-8")
    return workspace


def _request(workspace: Path, release: Path, browser: Path) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "workspace": str(workspace),
        "catalogRoot": str(release),
        "browserExecutable": str(browser),
        "brief": "用蓝色商务风做一段本周销售增长说明",
        "aspectRatio": "16:9",
        "durationSeconds": 6,
        "language": "zh",
        "brandAssets": [],
        "model": {
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "modelId": "qwen3.7-max-2026-06-08",
            "apiKey": "sk-" + "a" * 40,
        },
    }


def scenario_overflow_repaired_and_rendered(
    run_root: Path, release: Path, browser: Path, chromium_major: int
) -> dict[str, object]:
    workspace = _workspace(run_root, "s1-repair")
    model = ScriptedModel([_first_reply(LONG_HEADLINE), _repair_reply(SHORT_HEADLINE)])

    answer = run_motion_authoring_entry(
        _request(workspace, release, browser), model_call=model
    )

    if len(model.calls) != 2:
        raise RuntimeError(f"S1: expected exactly one repair round, saw {len(model.calls)} calls")
    repair_request = model.calls[1][-1]
    if repair_request["role"] != "user":
        raise RuntimeError("S1: the repair round must be a user message")
    for needle in (f"copy overflows slot {OVERFLOW_SLOT} horizontally", "324px wide at 22px type"):
        if needle not in repair_request["content"]:
            raise RuntimeError(f"S1: repair message lacks the measured finding: {needle!r}")

    baselines = list(workspace.glob("catalog-baseline/items/*/*.html"))
    if not baselines:
        raise RuntimeError("S1: no baseline working copy was measured")
    profile = workspace / "slot-probe-profile"
    if not profile.is_dir() or not any(profile.iterdir()):
        raise RuntimeError("S1: the packaged Chromium never touched its probe profile")
    stored = json.loads((workspace / "STORYBOARD.json").read_text(encoding="utf-8"))
    if stored["beats"][0]["headline"] != SHORT_HEADLINE:
        raise RuntimeError("S1: the repaired copy did not reach the storyboard on disk")

    segments = answer["segments"]
    if len(segments) != 1 or PART not in segments[0]["entryHtml"]:
        raise RuntimeError(f"S1: expected the one repaired part segment, got {segments}")
    segment = segments[0]

    # 成片的镜头半程：生产 Worker + 包内 Chromium 真渲染修复后的零件段。
    render = _render_once(
        browser,
        chromium_major,
        workspace,
        segment["entryHtml"],
        segment["allowedAssets"],
        segment["frameCount"],
        # 8 秒零件 240 帧，成本估算 30 + 240*0.4 = 126s，默认 120s 的停摆护栏不够。
        budget_seconds=240,
        spec_overrides={
            "canvas": segment["canvas"],
            "sourceStartMillis": segment["sourceStartMillis"],
            "sourceEndMillis": segment["sourceEndMillis"],
        },
    )
    digests = render["frames"]
    if len(set(digests)) < 3:
        raise RuntimeError(
            f"S1: the repaired shot rendered as a still ({len(set(digests))} distinct frames)"
        )
    return {
        "answer": {key: answer[key] for key in ("status", "frameCount", "durationSeconds")},
        "repairMessageExcerpt": repair_request["content"][:200],
        "renderedFrames": len(digests),
        "distinctFrameDigests": len(set(digests)),
        "firstFrameDigest": digests[0],
    }


def scenario_stage_escape_is_repaired(
    run_root: Path, release: Path, browser: Path
) -> dict[str, object]:
    """自动生长的盒子把整个零件推出舞台——盒级读数毫无动静的那一类。

    实测：lt-clean-bar 的标题盒随内容长开（scrollWidth 恒等 clientWidth），
    55 字标题把文档从 1920px 撑到 3019px。判定靠整篇文档尺寸的相对比较。
    """
    stage_part_beat = {
        "beat_id": "hook",
        "purpose": "标题引入",
        "start_seconds": 0.0,
        "duration_seconds": 6.0,
        "catalog_parts": ["lt-clean-bar"],
        "layout": "title",
        "headline": (
            "本季度华东华南华北三大区域销售额环比增长百分之十八点五"
            "创历史同期最高纪录再创新高连续六个季度保持两位数增长态势"
        ),
        "body": "",
        "items": [],
    }
    repaired_beat = dict(stage_part_beat, headline="销售增长")
    workspace = _workspace(run_root, "s4-stage-escape")
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "design": json.loads(_first_reply(""))["design"],
                    "script": json.loads(_first_reply(""))["script"],
                    "storyboard": {"beats": [stage_part_beat]},
                }
            ),
            json.dumps({"storyboard": {"beats": [repaired_beat]}}),
        ]
    )

    answer = run_motion_authoring_entry(
        _request(workspace, release, browser), model_call=model
    )
    if len(model.calls) != 2:
        raise RuntimeError(f"S4: expected exactly one repair round, saw {len(model.calls)}")
    repair_request = model.calls[1][-1]["content"]
    for needle in ("beyond its own stage", "1920px"):
        if needle not in repair_request:
            raise RuntimeError(f"S4: repair message lacks the stage finding: {needle!r}")
    if answer["status"] != "authored":
        raise RuntimeError(f"S4: expected authored after repair, got {answer['status']}")
    return {
        "status": answer["status"],
        "repairMessageExcerpt": repair_request[:200],
    }


def scenario_still_overflowing_is_refused(
    run_root: Path, release: Path, browser: Path
) -> dict[str, object]:
    workspace = _workspace(run_root, "s2-refused")
    model = ScriptedModel([_first_reply(LONG_HEADLINE), _repair_reply(LONG_HEADLINE)])

    try:
        run_motion_authoring_entry(_request(workspace, release, browser), model_call=model)
    except MotionAuthoringEntryRejected as rejected:
        reason = rejected.rejection_reason
        if reason != "agent_copy_overflows_its_slot_after_the_repair_round":
            raise RuntimeError(f"S2: wrong closed reason: {reason}") from rejected
        return {"rejectionReason": reason, "modelCalls": len(model.calls)}
    raise RuntimeError("S2: a film whose copy never fits was authored anyway")


def scenario_real_model(
    run_root: Path, release: Path, browser: Path, secret: Path
) -> dict[str, object]:
    from automation_tool.executor.motion_authoring.agent import (
        call_video_creation_model,
        load_video_creation_model_config,
    )

    config = load_video_creation_model_config(
        catalog_path=ROOT / "contracts/video/bailian-model-catalog.v1.json",
        secret_path=secret,
    )
    if config is None:
        raise RuntimeError("S3: the bailian credential did not load")

    workspace = _workspace(run_root, "s3-real-model")
    rounds: list[int] = []

    def recording_call(
        config_: object, messages: list[dict[str, str]], **keywords: object
    ) -> str:
        rounds.append(len(messages))
        return call_video_creation_model(config_, messages, **keywords)

    document = _request(workspace, release, browser)
    document["model"] = {
        "baseUrl": config.base_url,
        "modelId": config.model_id,
        "apiKey": config.api_key,
    }
    document["brief"] = "用蓝色商务风做一段本周销售增长说明，选一个横排字幕零件放标题"
    answer = run_motion_authoring_entry(document, model_call=recording_call)

    probe_ran = bool(list(workspace.glob("catalog-baseline/items/*/*.html")))
    return {
        "status": answer["status"],
        "segments": len(answer["segments"]),
        "modelRounds": len(rounds),
        "repairTriggered": len(rounds) > 1,
        "probeMeasured": probe_ran,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    parser.add_argument(
        "--skip-real-model",
        action="store_true",
        help="只跑 S1/S2（脚本化模型 + 真探针 + 真渲染），不调真实模型",
    )
    arguments = parser.parse_args()

    run_root = EVIDENCE / "run"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)

    print("[PC-14] staging the locked release catalog…")
    release = stage_release_directory(run_root)
    print("[PC-14] staging the packaged Chromium…")
    browser, chromium_major = _stage_chromium(run_root)

    results: dict[str, object] = {}
    print("[PC-14] S1: overflow measured, repaired, rendered…")
    results["s1_overflow_repaired_and_rendered"] = scenario_overflow_repaired_and_rendered(
        run_root, release, browser, chromium_major
    )
    print("[PC-14] S1 ok:", json.dumps(results["s1_overflow_repaired_and_rendered"], ensure_ascii=False))

    print("[PC-14] S4: auto-growing box pushed past the stage, repaired…")
    results["s4_stage_escape_repaired"] = scenario_stage_escape_is_repaired(
        run_root, release, browser
    )
    print("[PC-14] S4 ok:", json.dumps(results["s4_stage_escape_repaired"], ensure_ascii=False))

    print("[PC-14] S2: still overflowing after the repair round…")
    results["s2_still_overflowing_refused"] = scenario_still_overflowing_is_refused(
        run_root, release, browser
    )
    print("[PC-14] S2 ok:", json.dumps(results["s2_still_overflowing_refused"], ensure_ascii=False))

    if arguments.skip_real_model:
        results["s3_real_model"] = "skipped by flag"
        print("[PC-14] S3 skipped by flag")
    else:
        print("[PC-14] S3: the real model through the real entry…")
        results["s3_real_model"] = scenario_real_model(
            run_root, release, browser, arguments.secret
        )
        print("[PC-14] S3 ok:", json.dumps(results["s3_real_model"], ensure_ascii=False))

    report = EVIDENCE / "pc-14-acceptance.json"
    report.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[PC-14] acceptance passed; evidence at {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
