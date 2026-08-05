"""SA-02: clean a successful Browser Use trajectory into candidate skill steps.

A raw trajectory is a list of observed actions plus the page facts around each
one — noisy, coordinate-laden, and carrying whatever the model happened to see
(cookies in a toast, a session id in a URL query, absolute pixel positions).
The cleaner distils that into candidate steps SA-03 can compile against the
SA-01 schema: semantic anchors only, no coordinates as anchors, no secrets, no
incidental state. What must survive is exactly what makes a skill replayable
and auditable — the external side-effect boundary, the account/domain the
trajectory ran under, and the success evidence.
"""

from __future__ import annotations

import copy

import pytest
from automation_tool.executor.automation_skill import parse_automation_skill
from automation_tool.executor.skill_trajectory_cleaner import (
    TrajectoryRejected,
    clean_trajectory,
)


def raw() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "platform": "douyin",
        "account": {"handle": "@studio-demo", "loggedIn": True},
        "domain": "creator.douyin.com",
        "entryUrl": "https://creator.douyin.com/creator-micro/content/upload?sess=SECRET123",
        "viewport": {"width": 1280, "height": 800},
        "language": "zh-CN",
        "actions": [
            {
                "kind": "click",
                "target": {
                    "role": "button",
                    "name": "上传视频",
                    "nearText": "从这里开始",
                    "x": 220,
                    "y": 140,
                },
                "external": False,
                "resultingVisible": {"role": "textbox", "name": "作品标题"},
            },
            {
                "kind": "fill",
                "target": {"role": "textbox", "name": "作品标题", "x": 300, "y": 200},
                "value": {"parameter": "caption"},
                "external": False,
                "resultingVisible": None,
            },
            {
                "kind": "click",
                "target": {"role": "button", "name": "发布", "x": 640, "y": 720},
                "external": True,
                "resultingUrl": "https://creator.douyin.com/creator-micro/content/manage",
            },
        ],
        "successEvidence": [
            {"kind": "url_matches", "url": "https://creator.douyin.com/creator-micro/content/manage"},
        ],
    }


class TestCleaning:
    def test_a_cleaned_trajectory_compiles_against_the_sa01_schema(self) -> None:
        cleaned = clean_trajectory(raw())

        # The whole point: what SA-02 emits is a valid SA-01 document.
        skill = parse_automation_skill(cleaned)
        assert skill.platform == "douyin"
        assert skill.domain == "creator.douyin.com"
        assert len(skill.steps) == 3
        assert skill.version == 1
        assert skill.parent_version is None
        assert skill.external_step_count == 1

    def test_coordinates_never_become_anchors_but_survive_as_evidence(self) -> None:
        cleaned = clean_trajectory(raw())

        for step in cleaned["steps"]:
            assert "x" not in step["goal"]
            assert "y" not in step["goal"]
        # The click on the external step is preserved as click_point_v1 evidence.
        click_points = [
            item for item in cleaned["successEvidence"] if item["kind"] == "click_point_v1"
        ]
        assert click_points, "the external click's coordinates must survive as evidence"
        assert click_points[0]["x"] == 640
        assert click_points[0]["y"] == 720

    def test_the_entry_url_secret_is_stripped_to_a_path_pattern(self) -> None:
        cleaned = clean_trajectory(raw())

        # `?sess=SECRET123` must not reach the stored document in any form.
        assert "SECRET123" not in str(cleaned)
        assert cleaned["pathPattern"] == "/creator-micro/content/upload"

    def test_the_account_and_side_effect_boundary_are_preserved(self) -> None:
        cleaned = clean_trajectory(raw())

        assert cleaned["sideEffectBoundary"]["maxExternalSteps"] == 1
        # The account handle drives version routing later, but is not itself a
        # secret and must not be dropped — it rides alongside, not inside the skill.
        assert clean_trajectory(raw(), with_metadata=True)["metadata"]["account"] == (
            "@studio-demo"
        )


class TestRejectionMatrix:
    def _reject(self, mutate, message: str) -> None:
        document = copy.deepcopy(raw())
        mutate(document)
        with pytest.raises(TrajectoryRejected, match=message):
            clean_trajectory(document)

    def test_a_trajectory_from_a_logged_out_session_is_refused(self) -> None:
        self._reject(lambda d: d["account"].update({"loggedIn": False}), "logged in")

    def test_a_trajectory_with_no_success_evidence_is_refused(self) -> None:
        self._reject(lambda d: d.update({"successEvidence": []}), "evidence")

    def test_more_external_actions_than_the_boundary_are_refused(self) -> None:
        self._reject(
            lambda d: d["actions"][0].update({"external": True}), "external"
        )

    def test_a_secret_bearing_target_name_is_refused_not_silently_kept(self) -> None:
        self._reject(
            lambda d: d["actions"][0]["target"].update({"name": "token: abc123"}),
            "forbidden",
        )

    def test_a_cross_domain_action_url_is_refused(self) -> None:
        self._reject(
            lambda d: d["actions"][2].update(
                {"resultingUrl": "https://evil.example.com/steal"}
            ),
            "domain",
        )
