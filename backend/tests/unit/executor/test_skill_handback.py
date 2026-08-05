"""SA-05: failure handback and diff collection.

When a deterministic replay fails, what happens next depends entirely on
whether an external side effect was already dispatched:

* **not dispatched** — the run can be handed back to Browser Use from the last
  checkpoint to finish the remaining steps, and the difference between the
  replay path and the recovery is collected (feeds SA-06's candidate v2);
* **dispatched, outcome uncertain** — the platform may already have acted, so
  the only safe move is to reconcile: never continue, never resend.
"""

from __future__ import annotations

import pytest
from automation_tool.executor.automation_skill import parse_automation_skill
from automation_tool.executor.skill_handback import (
    HandbackDecision,
    decide_handback,
)
from automation_tool.executor.skill_replayer import ReplayFailed, replay_skill
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory
from tests.unit.executor.test_skill_replayer import FakePage
from tests.unit.executor.test_skill_trajectory_cleaner import raw


def skill():
    return parse_automation_skill(clean_trajectory(raw()))


def _failure(page: FakePage) -> ReplayFailed:
    try:
        replay_skill(skill(), page, parameters={"caption": "x"})
    except ReplayFailed as error:
        return error
    raise AssertionError("expected the replay to fail")


class TestHandback:
    def test_a_pre_dispatch_failure_resumes_browser_use_from_the_checkpoint(self) -> None:
        failure = _failure(FakePage(missing={"上传视频"}))
        assert failure.dispatched is False

        decision = decide_handback(skill(), failure)

        assert isinstance(decision, HandbackDecision)
        assert decision.action == "resume_browser_use"
        assert decision.resume_from_checkpoint == 1
        # The remaining steps (from the checkpoint onward) are what Browser Use
        # must complete; the already-passed prefix is not repeated.
        assert decision.remaining_step_indexes == [1, 2, 3]

    def test_a_mid_run_pre_dispatch_failure_only_resumes_the_tail(self) -> None:
        # The 作品标题 anchor is missing: step 1 (click, checkpoint) has passed
        # and dispatched nothing external; step 2 fails before any side effect.
        failure = _failure(FakePage(missing={"作品标题"}))
        assert failure.dispatched is False

        decision = decide_handback(skill(), failure)

        assert decision.action == "resume_browser_use"
        assert decision.resume_from_checkpoint == 1
        assert decision.remaining_step_indexes == [1, 2, 3]

    def test_a_post_dispatch_failure_reconciles_and_forbids_continue(self) -> None:
        # 发布 is the external step; its postcondition fails after the click, so
        # the outcome is uncertain — the platform may already have published.
        failure = _failure(FakePage(failing={"/creator-micro/content/manage"}))
        assert failure.dispatched is True

        decision = decide_handback(skill(), failure)

        assert decision.action == "reconcile_only"
        assert decision.resume_from_checkpoint is None
        assert decision.may_continue is False
        assert decision.may_resend is False


class TestDiffCollection:
    def test_a_resume_decision_records_the_diff_for_the_next_version(self) -> None:
        failure = _failure(FakePage(missing={"上传视频"}))
        decision = decide_handback(skill(), failure)

        assert decision.diff["failedStepIndex"] == 1
        assert decision.diff["reason"]
        assert decision.diff["dispatched"] is False

    def test_a_reconcile_decision_carries_no_resume_diff(self) -> None:
        failure = _failure(FakePage(failing={"/creator-micro/content/manage"}))
        decision = decide_handback(skill(), failure)

        assert decision.diff["dispatched"] is True
        assert decision.diff.get("resume") is None


class TestRejections:
    def test_a_failure_from_a_different_skill_shape_is_refused(self) -> None:
        broken = ReplayFailed("boom", checkpoint_index=99, failed_index=99)
        with pytest.raises(ValueError, match="checkpoint"):
            decide_handback(skill(), broken)
