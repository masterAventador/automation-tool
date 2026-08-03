"""修复轮只许改文案：结构、时序、零件、布局一个都不许动。

T92 当年砍掉修复轮是因为重写 13KB HTML 太贵；PC-14 的廉价版只重写几十字节
文案。守卫存在的理由：模型拿到「改短」的指令后完全可能顺手重排节拍——那
就不再是廉价修复，而是一部新片子绕过了前面所有已通过的门禁。
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.agent import (
    MotionAuthoringRejected,
    StoryboardArtifact,
    require_repair_changed_only_copy,
)


def _payload(headline: str = "本周销售增长", duration: float = 3.0) -> dict[str, object]:
    return {
        "beats": [
            {
                "beat_id": "b1",
                "purpose": "开场",
                "start_seconds": 0.0,
                "duration_seconds": duration,
                "catalog_parts": [],
                "layout": "points",
                "headline": headline,
                "body": "环比上升百分之十二",
                "items": ["华东", "华南"],
            }
        ]
    }


def test_a_repair_that_only_shortens_copy_is_accepted() -> None:
    before = StoryboardArtifact.from_payload(_payload())
    after = StoryboardArtifact.from_payload(_payload(headline="销售增长"))
    require_repair_changed_only_copy(before, after)


def test_a_repair_that_moves_a_beat_is_refused() -> None:
    before = StoryboardArtifact.from_payload(_payload())
    after = StoryboardArtifact.from_payload(_payload(duration=4.0))
    with pytest.raises(MotionAuthoringRejected):
        require_repair_changed_only_copy(before, after)


def test_a_repair_that_changes_the_beat_count_is_refused() -> None:
    before = StoryboardArtifact.from_payload(_payload())
    extended = _payload()
    beats = extended["beats"]
    assert isinstance(beats, list)
    beats.append(
        {
            "beat_id": "b2",
            "purpose": "收尾",
            "start_seconds": 3.0,
            "duration_seconds": 2.0,
            "catalog_parts": [],
            "layout": "points",
            "headline": "谢谢观看",
            "body": "",
            "items": [],
        }
    )
    after = StoryboardArtifact.from_payload(extended)
    with pytest.raises(MotionAuthoringRejected):
        require_repair_changed_only_copy(before, after)
