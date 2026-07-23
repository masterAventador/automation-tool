"""Vendor-agnostic conformance suite for `VideoEditingProvider` adapters (VE-08).

Every editing provider — the first-phase cloud adapter, the fake second
provider, and any future vendor — must pass this identical suite through the
neutral protocol alone. The suite imports no vendor modules and receives the
provider plus a few scenario inputs; a future adapter therefore plugs in by
constructing one scenario, with zero changes to the domain layer or pages.

A failed check raises `EditingProviderConformanceViolation` carrying only the
fixed check identifier; vendor payloads never enter the error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, final

from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderCapabilities,
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderId,
    EditingProviderJobSnapshot,
    EditingSubmission,
    InvalidEditingProviderModel,
    VideoEditingProvider,
    editing_submission_idempotency_key,
)

CONFORMANCE_CHECKS: Final[tuple[str, ...]] = (
    "capabilities_declared",
    "supported_timeline_validates",
    "unsupported_capability_rejected",
    "unknown_job_not_found",
    "submit_returns_queued_snapshot",
    "submit_replay_idempotent",
    "conflicting_resubmission_rejected",
    "artifacts_before_success_conflict",
    "success_reaches_terminal_with_outputs",
    "artifacts_match_confirmed_outputs",
    "terminal_cancel_is_stable",
    "terminal_replay_returns_settled",
)


@final
class EditingProviderConformanceViolation(Exception):
    """One provider broke the neutral contract; carries the check id only."""

    def __init__(self, check: str) -> None:
        super().__init__(f"Editing provider conformance check failed: {check}")
        self.check: Final[str] = check


@final
@dataclass(frozen=True, slots=True)
class EditingProviderConformanceScenario:
    """Provider under test plus the minimal scenario inputs the suite needs."""

    provider: VideoEditingProvider
    supported_submission: EditingSubmission
    unsupported_timeline: EditingTimeline | None
    conflicting_timeline: EditingTimeline
    drive_to_success: Callable[[EditingJobId], Awaitable[None]]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider, VideoEditingProvider)
            or not isinstance(self.supported_submission, EditingSubmission)
            or (
                self.unsupported_timeline is not None
                and not isinstance(self.unsupported_timeline, EditingTimeline)
            )
            or not isinstance(self.conflicting_timeline, EditingTimeline)
            or self.conflicting_timeline == self.supported_submission.timeline
            or self.conflicting_timeline.project_id != self.supported_submission.project_id
        ):
            raise InvalidEditingProviderModel


@final
@dataclass(frozen=True, slots=True)
class EditingProviderConformanceReport:
    """The provider identity and every check it passed, in suite order."""

    provider_id: EditingProviderId
    passed_checks: tuple[str, ...]


def _ensure(condition: bool, check: str) -> None:
    if not condition:
        raise EditingProviderConformanceViolation(check)


async def _expect_failure(
    check: str,
    code: EditingProviderErrorCode,
    operation: Callable[[], Awaitable[object]],
) -> None:
    try:
        await operation()
    except EditingProviderFailure as failure:
        _ensure(failure.code is code, check)
        return
    raise EditingProviderConformanceViolation(check)


def _snapshot_shape_ok(
    snapshot: EditingProviderJobSnapshot,
    capabilities: EditingProviderCapabilities,
    editing_job_id: EditingJobId,
) -> bool:
    return (
        isinstance(snapshot, EditingProviderJobSnapshot)
        and snapshot.provider_id == capabilities.provider_id
        and snapshot.editing_job_id == editing_job_id
    )


async def run_editing_provider_conformance(
    scenario: EditingProviderConformanceScenario,
) -> EditingProviderConformanceReport:
    """Run the full neutral-contract suite and report the passed checks."""
    if not isinstance(scenario, EditingProviderConformanceScenario):
        raise InvalidEditingProviderModel
    provider = scenario.provider
    submission = scenario.supported_submission
    job_id = submission.editing_job_id
    passed: list[str] = []

    capabilities = await provider.capabilities()
    _ensure(
        isinstance(capabilities, EditingProviderCapabilities), "capabilities_declared"
    )
    passed.append("capabilities_declared")

    await provider.validate(submission.timeline)
    _ensure(
        capabilities.supports(submission.timeline), "supported_timeline_validates"
    )
    passed.append("supported_timeline_validates")

    if scenario.unsupported_timeline is not None:
        unsupported = scenario.unsupported_timeline
        await _expect_failure(
            "unsupported_capability_rejected",
            EditingProviderErrorCode.UNSUPPORTED_CAPABILITY,
            lambda: provider.validate(unsupported),
        )
    passed.append("unsupported_capability_rejected")

    unknown_job = EditingJobId.new()
    await _expect_failure(
        "unknown_job_not_found",
        EditingProviderErrorCode.NOT_FOUND,
        lambda: provider.get(unknown_job),
    )
    await _expect_failure(
        "unknown_job_not_found",
        EditingProviderErrorCode.NOT_FOUND,
        lambda: provider.cancel(unknown_job),
    )
    await _expect_failure(
        "unknown_job_not_found",
        EditingProviderErrorCode.NOT_FOUND,
        lambda: provider.fetch_artifacts(unknown_job),
    )
    passed.append("unknown_job_not_found")

    first = await provider.submit(submission)
    _ensure(
        _snapshot_shape_ok(first, capabilities, job_id)
        and first.status is EditingJobStatus.QUEUED
        and not first.output_artifact_ids
        and first.failure_code is None,
        "submit_returns_queued_snapshot",
    )
    passed.append("submit_returns_queued_snapshot")

    replay = await provider.submit(submission)
    _ensure(replay == first, "submit_replay_idempotent")
    passed.append("submit_replay_idempotent")

    conflicting = EditingSubmission(
        editing_job_id=job_id,
        project_id=submission.project_id,
        timeline=scenario.conflicting_timeline,
        idempotency_key=editing_submission_idempotency_key(job_id),
    )
    await _expect_failure(
        "conflicting_resubmission_rejected",
        EditingProviderErrorCode.CONFLICT,
        lambda: provider.submit(conflicting),
    )
    passed.append("conflicting_resubmission_rejected")

    await _expect_failure(
        "artifacts_before_success_conflict",
        EditingProviderErrorCode.CONFLICT,
        lambda: provider.fetch_artifacts(job_id),
    )
    passed.append("artifacts_before_success_conflict")

    await scenario.drive_to_success(job_id)
    settled = await provider.get(job_id)
    _ensure(
        _snapshot_shape_ok(settled, capabilities, job_id)
        and settled.status is EditingJobStatus.SUCCEEDED
        and bool(settled.output_artifact_ids)
        and settled.failure_code is None,
        "success_reaches_terminal_with_outputs",
    )
    passed.append("success_reaches_terminal_with_outputs")

    artifacts = await provider.fetch_artifacts(job_id)
    _ensure(
        artifacts == settled.output_artifact_ids, "artifacts_match_confirmed_outputs"
    )
    passed.append("artifacts_match_confirmed_outputs")

    cancelled = await provider.cancel(job_id)
    _ensure(cancelled == settled, "terminal_cancel_is_stable")
    passed.append("terminal_cancel_is_stable")

    terminal_replay = await provider.submit(submission)
    _ensure(terminal_replay == settled, "terminal_replay_returns_settled")
    passed.append("terminal_replay_returns_settled")

    _ensure(tuple(passed) == CONFORMANCE_CHECKS, "suite_completed")
    return EditingProviderConformanceReport(
        provider_id=capabilities.provider_id, passed_checks=tuple(passed)
    )


__all__ = [
    "CONFORMANCE_CHECKS",
    "EditingProviderConformanceReport",
    "EditingProviderConformanceScenario",
    "EditingProviderConformanceViolation",
    "run_editing_provider_conformance",
]
