"""SA-05: decide what a failed replay does next, and collect the diff.

The decision hinges on one fact the replayer records — whether an external side
effect was already dispatched when the failure occurred:

* **not dispatched** → ``resume_browser_use``: hand control back to Browser Use
  from the last checkpoint, complete the remaining steps, and record the diff so
  SA-06 can compile a candidate v2. The already-passed prefix is never repeated;
* **dispatched** → ``reconcile_only``: the platform may already have acted, so
  the outcome is uncertain. Continue and resend are both forbidden; the only
  safe move is to reconcile against the platform's real state (BU-05 / ActionGate
  territory).

This module makes the decision; it does not itself talk to Browser Use or the
platform. It is the branch that the recovery orchestrator obeys.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from automation_tool.executor.automation_skill import AutomationSkill
from automation_tool.executor.skill_replayer import ReplayFailed


@dataclass(frozen=True)
class HandbackDecision:
    action: str  # "resume_browser_use" | "reconcile_only"
    resume_from_checkpoint: int | None
    remaining_step_indexes: list[int]
    may_continue: bool
    may_resend: bool
    diff: dict[str, object] = field(default_factory=dict)


def decide_handback(skill: AutomationSkill, failure: ReplayFailed) -> HandbackDecision:
    step_indexes = [step.index for step in skill.steps]
    if failure.checkpoint_index not in {0, *step_indexes}:
        raise ValueError("the failure names a checkpoint that is not in this skill")

    if failure.dispatched:
        # Outcome uncertain: the external action may have taken effect. No
        # continue, no resend — reconcile against the platform only.
        return HandbackDecision(
            action="reconcile_only",
            resume_from_checkpoint=None,
            remaining_step_indexes=[],
            may_continue=False,
            may_resend=False,
            diff={
                "failedStepIndex": failure.failed_index,
                "reason": str(failure),
                "dispatched": True,
            },
        )

    # Nothing external has happened yet: resume Browser Use from the checkpoint.
    resume_from = failure.checkpoint_index or (step_indexes[0] if step_indexes else 0)
    remaining = [index for index in step_indexes if index >= resume_from]
    return HandbackDecision(
        action="resume_browser_use",
        resume_from_checkpoint=resume_from,
        remaining_step_indexes=remaining,
        may_continue=True,
        may_resend=False,
        diff={
            "failedStepIndex": failure.failed_index,
            "reason": str(failure),
            "dispatched": False,
            "resume": {"fromCheckpoint": resume_from, "remaining": remaining},
        },
    )


__all__ = ["HandbackDecision", "decide_handback"]
