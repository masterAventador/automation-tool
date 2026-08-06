"""SA-04: deterministic replay of a signed skill against a semantic page.

The replayer walks a skill's steps by their semantic anchors (role + name,
near-text, relative position) and checks each step's pre/postconditions — it
never calls a vision model per step, which is what makes replay cheap and
reproducible. The vision model only re-enters the picture when a step fails and
SA-05 hands control back to Browser Use from the last checkpoint.

Safety is per step, not per run:

* **one side effect maximum** — a step performs its single action, nothing else;
* **the external boundary is the skill's, not the page's** — the replayer counts
  the external actions it will perform and stops if a skill declares more than
  its ``sideEffectBoundary`` allows;
* **independent acceptance** — each step's postconditions are checked before the
  next step runs, so a step that "clicked" but did not achieve its outcome is a
  failure, not a silent pass;
* **checkpoints locate handback** — a failure reports the index of the last
  checkpoint at or before it, which is where SA-05 may resume with Browser Use.

The page is an injected protocol so the engine is testable and browser-free.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn, Protocol

from automation_tool.executor.automation_skill import (
    AutomationSkill,
    SkillCondition,
    SkillStep,
)


class ReplayPage(Protocol):
    """What the replayer needs from a page — semantic, never coordinate-based."""

    def find(self, role: str, name: str) -> object | None: ...

    def holds(
        self,
        kind: str,
        *,
        role: str | None = None,
        name: str | None = None,
        pattern: str | None = None,
    ) -> bool: ...

    def act(self, kind: str, handle: object, value: str | None) -> None:
        """Perform one action. `value` is the action's payload by kind:
        fill → the resolved runtime parameter, press_key → the key name,
        scroll → the direction, click/wait → None. Dropping the last two was
        how a replay pressed "some key" and scrolled "somewhere"
        (REVIEW-2026-08-06 SA#4)."""
        ...

    def current_path(self) -> str: ...


class ReplayFailed(RuntimeError):
    """Deterministic replay could not complete this skill on this page.

    ``dispatched`` records whether an external side effect had already been
    performed when the failure occurred. SA-05 reads it: a failure before any
    dispatch can be handed back to Browser Use from ``checkpoint_index``, while
    a failure after an external dispatch is outcome-uncertain and must only be
    reconciled — never continued or resent.
    """

    def __init__(
        self,
        message: str,
        *,
        checkpoint_index: int,
        failed_index: int = 0,
        dispatched: bool = False,
    ) -> None:
        super().__init__(message)
        self.checkpoint_index = checkpoint_index
        self.failed_index = failed_index
        self.dispatched = dispatched


@dataclass(frozen=True)
class ReplayOutcome:
    passed: bool
    completed_steps: int
    external_side_effects: int


def _condition_holds(page: ReplayPage, condition: SkillCondition) -> bool:
    return page.holds(
        condition.kind,
        role=condition.role,
        name=condition.name,
        pattern=condition.pattern,
    )


def replay_skill(
    skill: AutomationSkill,
    page: ReplayPage,
    *,
    parameters: dict[str, str],
) -> ReplayOutcome:
    if skill.external_step_count > skill.max_external_steps:
        # A published skill can never reach here (SA-03 lint), but a replay must
        # not trust that; the boundary is enforced where the side effect happens.
        raise ReplayFailed(
            "skill declares more external steps than its boundary allows",
            checkpoint_index=0,
        )

    last_checkpoint = 0
    external_performed = 0
    current_index = 0
    dispatched = False

    def fail(message: str) -> NoReturn:
        raise ReplayFailed(
            message,
            checkpoint_index=last_checkpoint,
            failed_index=current_index,
            dispatched=dispatched,
        )

    # Pre-flight every runtime parameter before a single side effect runs: a
    # skill that needs a value nobody supplied must fail with the page untouched,
    # not halfway through after an irreversible click.
    for step in skill.steps:
        if step.action.kind == "fill":
            name = step.action.parameter
            if name is None or name not in parameters:
                fail(f"step {step.index} needs runtime parameter {name!r}")

    # `dispatched` is monotone for the whole run: once any external action has
    # been performed, every later failure is outcome-uncertain, no matter how
    # many internal steps sit in between. Resetting it per step was how a
    # failure right after 发布 got classified as safely resumable — the shape
    # of a duplicate post (REVIEW-2026-08-06 SA#1).
    for step in skill.steps:
        current_index = step.index
        if step.checkpoint:
            last_checkpoint = step.index

        value = _action_payload(step, parameters, fail)

        for condition in step.preconditions:
            if not _condition_holds(page, condition):
                fail(f"precondition not met before step {step.index}")

        handle = page.find(step.goal.role, step.goal.name)
        if handle is None:
            fail(f"anchor not found for step {step.index}: {step.goal.role}")

        # The single side effect. Nothing else happens in this step.
        if step.external:
            external_performed += 1
            if external_performed > skill.max_external_steps:
                fail("replay would exceed the external side-effect boundary")
            # The *attempt* is what makes the outcome uncertain: a driver that
            # dies mid-click cannot say whether the platform saw it, so the
            # flag must flip before the action, not after it returns.
            dispatched = True
        try:
            page.act(step.action.kind, handle, value)
        except Exception as error:
            # A real page adapter raises native driver errors (click timeout,
            # page torn down). Escaping raw would strip the checkpoint and
            # dispatched semantics SA-05 decides by — an external click timeout
            # classified "resumable" is the duplicate-post shape again.
            fail(f"step {step.index} action failed: {error}")

        for condition in step.postconditions:
            if not _condition_holds(page, condition):
                fail(f"postcondition not met after step {step.index}")

    if not _success_evidence_holds(skill, page):
        fail("success evidence did not hold after the final step")

    return ReplayOutcome(
        passed=True,
        completed_steps=len(skill.steps),
        external_side_effects=external_performed,
    )


def _action_payload(
    step: SkillStep, parameters: dict[str, str], fail: Callable[[str], NoReturn]
) -> str | None:
    """What `act` must carry for this step — see `ReplayPage.act`."""
    action = step.action
    if action.kind == "fill":
        name = action.parameter
        if name is None or name not in parameters:
            fail(f"step {step.index} needs runtime parameter {name!r}")
            raise AssertionError("unreachable")  # pragma: no cover
        return parameters[name]
    if action.kind == "press_key":
        return action.key
    if action.kind == "scroll":
        return action.direction
    return None


def _success_evidence_holds(skill: AutomationSkill, page: ReplayPage) -> bool:
    # Multiple url_matches entries are alternative exits and ANY one proves
    # the outcome: current_path() has a single value, so all-of semantics made
    # two different patterns mutually exclusive — such a skill could never
    # replay, and because publishing requires a passing replay, never publish
    # (REVIEW-2026-08-06 SA#11). Element evidence stays all-of.
    url_patterns = [
        evidence.pattern
        for evidence in skill.success_evidence
        if evidence.kind == "url_matches"
    ]
    if url_patterns and page.current_path() not in url_patterns:
        return False
    for evidence in skill.success_evidence:
        if evidence.kind == "element_visible" and not page.holds(
            "element_visible", role=evidence.role, name=evidence.name
        ):
            return False
        # click_point_v1 is evidence-of-record only, not a replay assertion.
    return True


__all__ = ["ReplayFailed", "ReplayOutcome", "ReplayPage", "replay_skill"]
