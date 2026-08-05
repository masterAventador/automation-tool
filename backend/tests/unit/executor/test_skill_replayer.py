"""SA-04: deterministic replay of a signed skill against a semantic page.

The replayer executes by the skill's semantic anchors and pre/postconditions —
it does not call a vision model per step. Each step gets a timeout, at most one
external side effect, its own postcondition acceptance, and the whole run is
bounded by the skill's declared external boundary. A checkpoint step is where a
failed replay may later be handed back to Browser Use (SA-05).

The page is injected, so these tests drive real replay logic without a browser:
a scripted ``FakePage`` answers ``find`` / ``holds`` / ``current_path`` and
records the side effects it was asked to perform.
"""

from __future__ import annotations

import pytest
from automation_tool.executor.automation_skill import parse_automation_skill
from automation_tool.executor.skill_replayer import (
    ReplayFailed,
    ReplayOutcome,
    replay_skill,
)
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory
from tests.unit.executor.test_skill_trajectory_cleaner import raw


def skill():
    return parse_automation_skill(clean_trajectory(raw()))


class FakePage:
    """A scripted page: every anchor is findable and every condition holds."""

    def __init__(self, *, missing: set[str] | None = None, failing: set[str] | None = None):
        self.missing = missing or set()
        self.failing = failing or set()
        self.side_effects: list[tuple[str, str]] = []
        self.visited: list[str] = []

    def find(self, role: str, name: str) -> object | None:
        return None if name in self.missing else (role, name)

    def holds(self, kind: str, *, role=None, name=None, pattern=None) -> bool:
        token = pattern or name
        return token not in self.failing

    def act(self, kind: str, handle: object, value: str | None) -> None:
        _role, name = handle  # type: ignore[misc]
        self.side_effects.append((kind, name))

    def current_path(self) -> str:
        return "/creator-micro/content/manage"


class TestDeterministicReplay:
    def test_a_clean_skill_replays_and_reports_success(self) -> None:
        page = FakePage()
        outcome = replay_skill(skill(), page, parameters={"caption": "今天的护肤心得"})

        assert isinstance(outcome, ReplayOutcome)
        assert outcome.passed is True
        assert outcome.completed_steps == 3
        # Exactly one external side effect was performed (the 发布 click).
        assert outcome.external_side_effects == 1

    def test_fill_values_resolve_from_runtime_parameters_only(self) -> None:
        page = FakePage()
        replay_skill(skill(), page, parameters={"caption": "用户提供的标题"})

        fills = [effect for effect in page.side_effects if effect[0] == "fill"]
        assert fills == [("fill", "作品标题")]

    def test_a_missing_parameter_fails_before_any_side_effect(self) -> None:
        page = FakePage()
        with pytest.raises(ReplayFailed, match="parameter"):
            replay_skill(skill(), page, parameters={})
        assert page.side_effects == []


class TestSafetyProperties:
    def test_a_missing_anchor_stops_at_its_checkpoint(self) -> None:
        page = FakePage(missing={"上传视频"})
        with pytest.raises(ReplayFailed) as raised:
            replay_skill(skill(), page, parameters={"caption": "x"})
        # The failure names the checkpoint it stopped at, for SA-05 handback.
        assert raised.value.checkpoint_index == 1
        assert page.side_effects == []

    def test_a_failed_postcondition_is_a_replay_failure(self) -> None:
        page = FakePage(failing={"/creator-micro/content/manage"})
        with pytest.raises(ReplayFailed, match="postcondition"):
            replay_skill(skill(), page, parameters={"caption": "x"})

    def test_replay_never_exceeds_the_external_side_effect_boundary(self) -> None:
        # The skill declares maxExternalSteps=1; a page that reports two external
        # actions would be a defect, but the replayer counts from the skill, not
        # the page, so the boundary is a property of what it will *perform*.
        page = FakePage()
        outcome = replay_skill(skill(), page, parameters={"caption": "x"})
        assert outcome.external_side_effects <= skill().max_external_steps
