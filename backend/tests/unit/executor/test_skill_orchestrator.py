"""The orchestrator that puts SA-01..07 in front of business flows.

Route (SA-06) → replay (SA-04) → on failure, obey the handback decision
(SA-05): a pre-dispatch failure lands as ``recovery_pending`` — the honest
"this skill awaits repair or re-recording" state, because a real Browser Use
resume needs vision-model credentials and a logged-in session; a post-dispatch
failure lands as ``reconcile_required`` and the caller must walk the existing
side-effect reconciliation path. Seed skills load from signed records verified
against a pinned publisher key — the same trust anchor discipline as SA-03.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.unit.executor.test_skill_replayer import FakePage
from tests.unit.executor.test_skill_trajectory_cleaner import raw

from automation_tool.executor.skill_orchestrator import (
    SeedSkillLoadRejected,
    SkillExecutionKind,
    SkillOrchestrator,
    load_seed_registry,
)
from automation_tool.executor.skill_registry import (
    SkillPublicationRejected,
    SkillRegistry,
    sign_candidate,
)
from automation_tool.executor.skill_replayer import replay_skill
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory

SIGNING_SEED = bytes(range(32))
PUBLISHER_PUBLIC_KEY = (
    Ed25519PrivateKey.from_private_bytes(SIGNING_SEED).public_key().public_bytes_raw()
)
APPROVAL = {
    "reviewer": "op@studio",
    "decision": "approved",
    "reviewedAt": "2026-08-18T10:00:00Z",
}
PARAMETERS = {"caption": "今天的护肤心得"}


def candidate_v1() -> dict[str, object]:
    return clean_trajectory(raw())


def candidate_v2() -> dict[str, object]:
    document = copy.deepcopy(candidate_v1())
    document["version"] = 2
    document["parentVersion"] = 1
    return document


def signed(candidate: dict[str, object]) -> dict[str, object]:
    from automation_tool.executor.automation_skill import parse_automation_skill

    sandbox_outcome = replay_skill(
        parse_automation_skill(candidate), FakePage(), parameters=PARAMETERS
    )
    return sign_candidate(
        candidate, approval=APPROVAL, seed=SIGNING_SEED, replay=sandbox_outcome
    )


def registry_with(*candidates: dict[str, object]) -> SkillRegistry:
    registry = SkillRegistry(trusted_public_key=PUBLISHER_PUBLIC_KEY)
    for candidate in candidates:
        registry.publish(signed(candidate))
    return registry


def skill_id_of(candidate: dict[str, object]) -> str:
    return str(candidate["skillId"])


class TestExecutionMatrix:
    def test_a_routable_skill_replays_and_reports_success(self) -> None:
        candidate = candidate_v1()
        orchestrator = SkillOrchestrator(registry_with(candidate))

        report = orchestrator.execute(
            skill_id_of(candidate), FakePage(), parameters=PARAMETERS
        )

        assert report.kind is SkillExecutionKind.REPLAYED
        assert report.version == 1
        assert report.outcome is not None and report.outcome.passed is True
        assert report.decision is None

    def test_an_unknown_skill_reports_no_route(self) -> None:
        orchestrator = SkillOrchestrator(registry_with(candidate_v1()))

        report = orchestrator.execute(
            "00000000-0000-4000-8000-000000000000", FakePage(), parameters=PARAMETERS
        )

        assert report.kind is SkillExecutionKind.NO_ROUTE
        assert report.version is None
        assert report.outcome is None
        assert report.decision is None

    def test_a_disabled_version_reports_no_route(self) -> None:
        candidate = candidate_v1()
        identifier = skill_id_of(candidate)
        orchestrator = SkillOrchestrator(
            registry_with(candidate), disabled={(identifier, 1)}
        )

        report = orchestrator.execute(identifier, FakePage(), parameters=PARAMETERS)

        assert report.kind is SkillExecutionKind.NO_ROUTE

    def test_a_pre_dispatch_failure_lands_recovery_pending(self) -> None:
        candidate = candidate_v1()
        orchestrator = SkillOrchestrator(registry_with(candidate))
        page = FakePage(missing={"作品标题"})

        report = orchestrator.execute(
            skill_id_of(candidate), page, parameters=PARAMETERS
        )

        assert report.kind is SkillExecutionKind.RECOVERY_PENDING
        assert report.version == 1
        assert report.outcome is None
        decision = report.decision
        assert decision is not None
        assert decision.action == "resume_browser_use"
        assert decision.may_continue is True
        assert decision.may_resend is False
        assert decision.diff["dispatched"] is False
        # Nothing external ever ran: the 发布 click never happened.
        assert ("click", "发布", None) not in page.side_effects

    def test_a_post_dispatch_failure_lands_reconcile_required(self) -> None:
        candidate = candidate_v1()
        orchestrator = SkillOrchestrator(registry_with(candidate))
        # The external 发布 click succeeds, then the success URL never appears.
        page = FakePage(path="/somewhere-else")

        report = orchestrator.execute(
            skill_id_of(candidate), page, parameters=PARAMETERS
        )

        assert report.kind is SkillExecutionKind.RECONCILE_REQUIRED
        assert report.version == 1
        decision = report.decision
        assert decision is not None
        assert decision.action == "reconcile_only"
        assert decision.may_continue is False
        assert decision.may_resend is False
        assert decision.diff["dispatched"] is True
        assert ("click", "发布", None) in page.side_effects

    def test_proven_failures_steer_routing_to_the_other_version(self) -> None:
        """SA-06 wiring end-to-end: the orchestrator's own failure records must
        feed the next route. Three pure failures make the routed version
        proven-failing, so the fourth attempt reaches the unproven sibling."""
        first, second = candidate_v1(), candidate_v2()
        orchestrator = SkillOrchestrator(registry_with(first, second))
        identifier = skill_id_of(first)

        routed: list[int | None] = []
        for _ in range(4):
            report = orchestrator.execute(
                identifier, FakePage(missing={"上传视频"}), parameters=PARAMETERS
            )
            assert report.kind is SkillExecutionKind.RECOVERY_PENDING
            routed.append(report.version)

        # Both start unproven → the higher version wins ties; after three pure
        # failures v2 is proven-failing and v1 gets its first chance.
        assert routed == [2, 2, 2, 1]

    def test_the_dispatch_hook_reaches_the_replayer(self) -> None:
        candidate = candidate_v1()
        orchestrator = SkillOrchestrator(registry_with(candidate))
        seen: list[bool] = []

        report = orchestrator.execute(
            skill_id_of(candidate),
            FakePage(),
            parameters=PARAMETERS,
            on_external_dispatch=lambda: seen.append(True),
        )

        assert report.kind is SkillExecutionKind.REPLAYED
        assert seen == [True]

    def test_a_hook_exception_is_not_recorded_as_a_skill_failure(self) -> None:
        class DispatchRefused(Exception):
            pass

        candidate = candidate_v1()
        orchestrator = SkillOrchestrator(registry_with(candidate))

        def refuse() -> None:
            raise DispatchRefused

        with pytest.raises(DispatchRefused):
            orchestrator.execute(
                skill_id_of(candidate),
                FakePage(),
                parameters=PARAMETERS,
                on_external_dispatch=refuse,
            )

        # 台账拒绝不是技能的错：不能污染路由统计。
        assert orchestrator.stats.snapshot() == {}

    def test_a_success_keeps_the_proven_version_on_top(self) -> None:
        first, second = candidate_v1(), candidate_v2()
        orchestrator = SkillOrchestrator(registry_with(first, second))
        identifier = skill_id_of(first)

        initial = orchestrator.execute(identifier, FakePage(), parameters=PARAMETERS)
        repeat = orchestrator.execute(identifier, FakePage(), parameters=PARAMETERS)

        assert initial.kind is SkillExecutionKind.REPLAYED
        assert repeat.kind is SkillExecutionKind.REPLAYED
        # The success record beats the unproven sibling on the next route.
        assert (initial.version, repeat.version) == (2, 2)


def write_anchor(path: Path, *, key_hex: str | None = None) -> Path:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "publisherPublicKey": key_hex or PUBLISHER_PUBLIC_KEY.hex(),
            }
        ),
        encoding="utf-8",
    )
    return path


class TestSeedLoading:
    def test_signed_seed_records_load_into_a_verifying_registry(
        self, tmp_path: Path
    ) -> None:
        anchor = write_anchor(tmp_path / "skill-publisher.v1.json")
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()
        candidate = candidate_v1()
        (seeds / "comment.v1.json").write_text(
            json.dumps(signed(candidate), ensure_ascii=False), encoding="utf-8"
        )

        registry = load_seed_registry(anchor_path=anchor, seeds_root=seeds)

        record = registry.at(skill_id_of(candidate), 1)
        assert record.version == 1

    def test_seed_records_publish_parents_before_children(self, tmp_path: Path) -> None:
        """File order must not decide publish order: a v2 file that sorts first
        still needs its v1 parent published before it."""
        anchor = write_anchor(tmp_path / "skill-publisher.v1.json")
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()
        (seeds / "0-newest.json").write_text(
            json.dumps(signed(candidate_v2()), ensure_ascii=False), encoding="utf-8"
        )
        (seeds / "1-oldest.json").write_text(
            json.dumps(signed(candidate_v1()), ensure_ascii=False), encoding="utf-8"
        )

        registry = load_seed_registry(anchor_path=anchor, seeds_root=seeds)

        assert registry.at(skill_id_of(candidate_v1()), 2).version == 2

    def test_a_tampered_seed_record_is_rejected(self, tmp_path: Path) -> None:
        anchor = write_anchor(tmp_path / "skill-publisher.v1.json")
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()
        record = signed(candidate_v1())
        tampered = copy.deepcopy(record)
        steps = tampered["skill"]["steps"]  # type: ignore[index]
        steps[0]["goal"]["name"] = "改过的锚点"  # type: ignore[index]
        (seeds / "comment.v1.json").write_text(
            json.dumps(tampered, ensure_ascii=False), encoding="utf-8"
        )

        with pytest.raises(SkillPublicationRejected):
            load_seed_registry(anchor_path=anchor, seeds_root=seeds)

    def test_a_missing_anchor_fails_loud(self, tmp_path: Path) -> None:
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()

        with pytest.raises(SeedSkillLoadRejected):
            load_seed_registry(
                anchor_path=tmp_path / "absent.json", seeds_root=seeds
            )

    def test_a_malformed_anchor_fails_loud(self, tmp_path: Path) -> None:
        anchor = tmp_path / "skill-publisher.v1.json"
        anchor.write_text(
            json.dumps({"schemaVersion": 1, "publisherPublicKey": "not-hex"}),
            encoding="utf-8",
        )
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()

        with pytest.raises(SeedSkillLoadRejected):
            load_seed_registry(anchor_path=anchor, seeds_root=seeds)

    def test_a_missing_seeds_directory_fails_loud(self, tmp_path: Path) -> None:
        anchor = write_anchor(tmp_path / "skill-publisher.v1.json")

        with pytest.raises(SeedSkillLoadRejected):
            load_seed_registry(
                anchor_path=anchor, seeds_root=tmp_path / "absent-seeds"
            )

    def test_an_empty_seeds_directory_yields_an_empty_registry(
        self, tmp_path: Path
    ) -> None:
        anchor = write_anchor(tmp_path / "skill-publisher.v1.json")
        seeds = tmp_path / "seed-skills"
        seeds.mkdir()

        registry = load_seed_registry(anchor_path=anchor, seeds_root=seeds)

        assert registry.records() == []
