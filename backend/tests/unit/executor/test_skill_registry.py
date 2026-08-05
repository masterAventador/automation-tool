"""SA-03: compile, audit and sign a candidate skill into an immutable record.

The first successful trajectory becomes an immutable ``version: 1``. Publishing
is gated: the candidate must pass the SA-01 schema, a lint pass, and a replay
sandbox, and it must carry an explicit human approval before it is signed. The
model cannot self-elevate (no code path signs without the human review record)
and cannot overwrite a live version (the store is append-only per
``(skillId, version)``).
"""

from __future__ import annotations

import copy

import pytest
from automation_tool.executor.skill_registry import (
    SkillPublicationRejected,
    SkillRegistry,
    sign_candidate,
    verify_signed_skill,
)
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory
from tests.unit.executor.test_skill_trajectory_cleaner import raw

_SEED = bytes(range(32))


def _candidate() -> dict[str, object]:
    return clean_trajectory(raw())


def _approval() -> dict[str, object]:
    return {"reviewer": "operator", "decision": "approved", "reviewedAt": "2026-08-05T00:00:00Z"}


class TestSignAndVerify:
    def test_a_reviewed_candidate_signs_and_verifies(self) -> None:
        signed = sign_candidate(_candidate(), approval=_approval(), seed=_SEED)

        record = verify_signed_skill(signed)
        assert record.version == 1
        assert record.skill.platform == "douyin"
        assert record.approval["decision"] == "approved"

    def test_a_tampered_signed_skill_fails_verification(self) -> None:
        signed = sign_candidate(_candidate(), approval=_approval(), seed=_SEED)
        tampered = copy.deepcopy(signed)
        tampered["skill"]["domain"] = "evil.example.com"

        with pytest.raises(SkillPublicationRejected, match="signature"):
            verify_signed_skill(tampered)

    def test_signing_without_an_approval_is_refused(self) -> None:
        with pytest.raises(SkillPublicationRejected, match="review"):
            sign_candidate(_candidate(), approval=None, seed=_SEED)

    def test_signing_a_schema_invalid_candidate_is_refused(self) -> None:
        broken = _candidate()
        broken["steps"] = []
        with pytest.raises(SkillPublicationRejected, match="schema"):
            sign_candidate(broken, approval=_approval(), seed=_SEED)

    def test_lint_refuses_a_skill_with_no_checkpoint(self) -> None:
        # A skill the replayer can never re-enter safely is not publishable.
        candidate = _candidate()
        for step in candidate["steps"]:
            step["checkpoint"] = False
        with pytest.raises(SkillPublicationRejected, match="checkpoint"):
            sign_candidate(candidate, approval=_approval(), seed=_SEED)


class TestImmutableRegistry:
    def test_first_publish_creates_version_one(self) -> None:
        registry = SkillRegistry()
        signed = sign_candidate(_candidate(), approval=_approval(), seed=_SEED)

        stored = registry.publish(signed)

        assert stored.version == 1
        assert registry.live(stored.skill.skill_id).version == 1

    def test_publishing_the_same_version_twice_is_refused(self) -> None:
        registry = SkillRegistry()
        signed = sign_candidate(_candidate(), approval=_approval(), seed=_SEED)
        registry.publish(signed)

        with pytest.raises(SkillPublicationRejected, match="immutable"):
            registry.publish(signed)

    def test_a_second_version_needs_the_first_as_parent(self) -> None:
        registry = SkillRegistry()
        registry.publish(sign_candidate(_candidate(), approval=_approval(), seed=_SEED))

        orphan = _candidate()
        orphan["version"] = 2
        orphan["parentVersion"] = None
        with pytest.raises(SkillPublicationRejected, match="parent"):
            registry.publish(sign_candidate(orphan, approval=_approval(), seed=_SEED))

        child = _candidate()
        child["version"] = 2
        child["parentVersion"] = 1
        stored = registry.publish(sign_candidate(child, approval=_approval(), seed=_SEED))
        assert stored.version == 2
        # v1 is never deleted; both remain retrievable.
        assert registry.at(stored.skill.skill_id, 1).version == 1
        assert registry.at(stored.skill.skill_id, 2).version == 2

    def test_the_model_cannot_forge_an_approval_field_into_the_skill(self) -> None:
        # The approval travels signed but outside the skill document, so a skill
        # that tries to carry its own "approved" flag is just an unknown key.
        candidate = _candidate()
        candidate["approved"] = True
        with pytest.raises(SkillPublicationRejected, match="schema"):
            sign_candidate(candidate, approval=_approval(), seed=_SEED)
