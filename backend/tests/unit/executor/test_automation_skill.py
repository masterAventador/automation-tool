"""SA-01: the declarative AutomationSkill document and its fail-closed gate.

Self-healing is not "the model edits Playwright code". A successful Browser Use
trajectory compiles into this restricted, auditable JSON — semantic goals,
closed action kinds, pre/postconditions, risk, checkpoints and evidence — and
nothing else. Everything the roadmap forbids (arbitrary JS, CSS-selector
injection, shell, secrets, user prose, raw screenshots) must be structurally
impossible, not merely discouraged: raw coordinates may exist only as evidence,
fill values may only reference runtime parameters, and every free-text field is
scanned before the document is accepted.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from automation_tool.executor.automation_skill import (
    AutomationSkillRejected,
    parse_automation_skill,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPOSITORY_ROOT / "contracts/browser-use/automation-skill.v1.json"


def golden() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "skillId": "0a53d3ab-49c4-4c1a-9c26-5f5c86f8b001",
        "version": 1,
        "parentVersion": None,
        "platform": "douyin",
        "domain": "creator.douyin.com",
        "pathPattern": "/creator-micro/content/upload",
        "entryFingerprint": {
            "kind": "dom_outline_v1",
            "sha256": "a" * 64,
        },
        "language": "zh-CN",
        "viewport": {"width": 1280, "height": 800},
        "riskLevel": "high",
        "sideEffectBoundary": {"maxExternalSteps": 1},
        "steps": [
            {
                "index": 1,
                "goal": {
                    "role": "button",
                    "name": "上传视频",
                    "nearText": None,
                    "relativePosition": None,
                },
                "action": {"kind": "click"},
                "preconditions": [
                    {"kind": "element_visible", "role": "button", "name": "上传视频"}
                ],
                "postconditions": [
                    {"kind": "element_visible", "role": "textbox", "name": "作品标题"}
                ],
                "timeoutSeconds": 20,
                "external": False,
                "checkpoint": True,
            },
            {
                "index": 2,
                "goal": {
                    "role": "textbox",
                    "name": "作品标题",
                    "nearText": "填写标题",
                    "relativePosition": None,
                },
                "action": {"kind": "fill", "value": {"parameter": "caption"}},
                "preconditions": [],
                "postconditions": [],
                "timeoutSeconds": 20,
                "external": False,
                "checkpoint": False,
            },
            {
                "index": 3,
                "goal": {
                    "role": "button",
                    "name": "发布",
                    "nearText": None,
                    "relativePosition": "below",
                },
                "action": {"kind": "click"},
                "preconditions": [
                    {"kind": "element_visible", "role": "button", "name": "发布"}
                ],
                "postconditions": [
                    {"kind": "url_matches", "pattern": "/creator-micro/content/manage"}
                ],
                "timeoutSeconds": 60,
                "external": True,
                "checkpoint": False,
            },
        ],
        "successEvidence": [
            {"kind": "url_matches", "pattern": "/creator-micro/content/manage"},
            {
                "kind": "click_point_v1",
                "stepIndex": 3,
                "x": 640,
                "y": 720,
            },
        ],
    }


class TestAcceptance:
    def test_a_golden_skill_parses_with_its_boundaries_intact(self) -> None:
        skill = parse_automation_skill(golden())

        assert skill.skill_id == "0a53d3ab-49c4-4c1a-9c26-5f5c86f8b001"
        assert skill.version == 1
        assert skill.parent_version is None
        assert len(skill.steps) == 3
        assert skill.steps[2].external is True
        assert skill.external_step_count == 1

    def test_a_status_toast_is_a_speakable_outcome(self) -> None:
        """评论这类不产生导航的外部动作，其结果证据是 toast——
        role=status 必须在封闭词汇表内，否则该结果永远无法被技能表达。"""
        document = copy.deepcopy(golden())
        document["steps"][2]["postconditions"] = [
            {"kind": "element_visible", "role": "status", "name": "评论成功"}
        ]
        document["successEvidence"] = [
            {"kind": "element_visible", "role": "status", "name": "评论成功"}
        ]

        skill = parse_automation_skill(document)

        assert skill.steps[2].postconditions[0].role == "status"
        assert skill.success_evidence[0].role == "status"

    def test_a_url_prefix_is_a_speakable_outcome(self) -> None:
        """搜索这类导航型流程落在 /search/<关键词>——路径随输入变化，
        等值 url_matches 表达不了，前缀匹配必须在封闭词汇表内。"""
        document = copy.deepcopy(golden())
        document["steps"][2]["postconditions"] = [
            {"kind": "url_prefix_matches", "pattern": "/creator-micro"}
        ]
        document["successEvidence"] = [
            {"kind": "url_prefix_matches", "pattern": "/creator-micro"}
        ]

        skill = parse_automation_skill(document)

        assert skill.steps[2].postconditions[0].kind == "url_prefix_matches"
        assert skill.success_evidence[0].pattern == "/creator-micro"

    def test_the_contract_file_pins_the_same_closed_vocabularies(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))

        assert document["schemaVersion"] == 1
        assert set(document["actionKinds"]) == {
            "click",
            "fill",
            "press_key",
            "scroll",
            "wait",
        }
        assert set(document["conditionKinds"]) == {
            "element_visible",
            "element_absent",
            "url_matches",
            "url_prefix_matches",
        }
        assert document["limits"]["maxSteps"] >= 3
        assert document["limits"]["maxExternalSteps"] == 1


class TestRejectionMatrix:
    def _reject(self, mutate, message: str) -> None:
        document = copy.deepcopy(golden())
        mutate(document)
        with pytest.raises(AutomationSkillRejected, match=message):
            parse_automation_skill(document)

    def test_unknown_keys_anywhere_are_refused(self) -> None:
        self._reject(lambda d: d.update({"extra": 1}), "unknown")
        self._reject(
            lambda d: d["steps"][0].update({"selector": "#publish"}), "unknown"
        )
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"css": ".btn"}), "unknown"
        )

    def test_fill_values_may_only_reference_runtime_parameters(self) -> None:
        # 用户正文不入库：字面量填充值即为走私正文。
        self._reject(
            lambda d: d["steps"][1].update(
                {"action": {"kind": "fill", "value": "我的护肤心得正文"}}
            ),
            "parameter",
        )

    def test_arbitrary_js_and_selector_injection_are_refused(self) -> None:
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"name": "<script>alert(1)"}),
            "forbidden",
        )
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"name": "javascript:void(0)"}),
            "forbidden",
        )
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"name": "button#publish > a"}),
            "forbidden",
        )

    def test_selector_shapes_the_old_blocklist_let_through_are_refused(self) -> None:
        """REVIEW-2026-08-06 SA#10：旧黑名单只挡得住带 #、> 的那一种写法。

        审查实跑放行了这些全部：属性选择器、伪类、类链、事件属性。真正的
        结构性保护是 ReplayPage 协议没有选择器入口，但既然声称「文本扫描
        拒绝选择器」，扫描就得真的拒得住常见形态。
        """
        # 纯词形态（如 .btn.primary）字符黑名单挡不住也不该挡——英文句点在
        # 正常文案里太常见；那一类由「协议没有选择器入口」这层结构保护兜底。
        for shape in (
            "div[data-e2e=publish]",
            "input[name='caption']",
            "a:nth-child(2)",
            "onclick=alert(1)",
            "li.item*3",
        ):
            self._reject(
                lambda d, shape=shape: d["steps"][0]["goal"].update({"name": shape}),
                "forbidden",
            )

    def test_shell_and_secret_material_are_refused(self) -> None:
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"name": "$(rm -rf /)"}),
            "forbidden",
        )
        self._reject(
            lambda d: d["steps"][1]["goal"].update({"nearText": "cookie: sess=abc"}),
            "forbidden",
        )

    def test_raw_coordinates_cannot_be_a_goal(self) -> None:
        self._reject(
            lambda d: d["steps"][0]["goal"].update({"x": 100}),
            "unknown",
        )

    def test_screenshots_have_no_field_to_live_in(self) -> None:
        self._reject(
            lambda d: d["successEvidence"].append(
                {"kind": "screenshot", "base64": "AAAA"}
            ),
            "evidence",
        )

    def test_step_indexes_must_be_consecutive_from_one(self) -> None:
        self._reject(lambda d: d["steps"][1].update({"index": 5}), "consecutive")

    def test_external_steps_are_bounded_by_the_declared_boundary(self) -> None:
        def two_external(document: dict) -> None:
            document["steps"][0]["external"] = True

        self._reject(two_external, "external")

    def test_version_lineage_must_be_coherent(self) -> None:
        self._reject(lambda d: d.update({"version": 0}), "version")
        self._reject(
            lambda d: d.update({"version": 2, "parentVersion": 2}), "version"
        )

    def test_identity_and_fingerprint_shapes_are_pinned(self) -> None:
        self._reject(lambda d: d.update({"skillId": "not-a-uuid"}), "skill")
        self._reject(
            lambda d: d["entryFingerprint"].update({"sha256": "zz"}), "fingerprint"
        )
        self._reject(lambda d: d.update({"domain": "not a hostname"}), "domain")
        self._reject(lambda d: d.update({"pathPattern": "javascript:x"}), "path")

    def test_timeouts_and_viewport_are_bounded(self) -> None:
        self._reject(
            lambda d: d["steps"][0].update({"timeoutSeconds": 0}), "timeout"
        )
        self._reject(
            lambda d: d["steps"][0].update({"timeoutSeconds": 3600}), "timeout"
        )
        self._reject(
            lambda d: d["viewport"].update({"width": 20}), "viewport"
        )
