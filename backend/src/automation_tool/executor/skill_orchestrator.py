"""Put SA-01..07 in front of business flows: route → replay → handback.

Business actions used to drive hardcoded page objects; they now hand this
orchestrator a live ``ReplayPage`` and a skill id, and obey the report:

* ``replayed`` — the routed version completed with its success evidence held;
* ``no_route`` — nothing published matches this page (never recorded, or every
  variant disabled). The honest business state is "awaiting recording";
* ``recovery_pending`` — replay failed before any external side effect. SA-05
  says this is resumable by Browser Use, but a real resume needs vision-model
  credentials and a logged-in session, so v1 records the handback diff and
  reports the pending state instead of pretending to heal;
* ``reconcile_required`` — replay failed after an external dispatch. Continue
  and resend are both forbidden; the caller must walk its existing side-effect
  reconciliation path.

Every attempt feeds the stats store, so SA-06's routing preference (proven
success first, proven failure last) emerges from real outcomes.

Seed skills load from signed records checked into ``contracts/browser-use``:
the registry verifies each against the pinned publisher key from the anchor
file — the private half never ships; records are signed at development time.

Routing context honesty: without a live DOM fingerprinter, the default context
is the newest published version's own (fingerprint, language, viewport) — "the
page family we believe we are on". Real drift is detected by the replay itself
failing, which is the stronger signal; callers that do fingerprint live pages
can pass an explicit context.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from automation_tool.executor.automation_skill import AutomationSkill
from automation_tool.executor.motion_authoring.resources import CONTRACTS_ROOT
from automation_tool.executor.skill_handback import HandbackDecision, decide_handback
from automation_tool.executor.skill_management import SkillVersionKey
from automation_tool.executor.skill_registry import SignedSkill, SkillRegistry
from automation_tool.executor.skill_replayer import (
    ReplayFailed,
    ReplayOutcome,
    ReplayPage,
    replay_skill,
)
from automation_tool.executor.skill_router import (
    NoRouteAvailable,
    PageContext,
    VersionStats,
    route_skill,
)

SEED_ANCHOR_PATH: Final = CONTRACTS_ROOT / "browser-use/skill-publisher.v1.json"
SEED_SKILLS_ROOT: Final = CONTRACTS_ROOT / "browser-use/seed-skills"

_HEX_KEY: Final = re.compile(r"^[0-9a-f]{64}$")
_ANCHOR_KEYS: Final = frozenset({"schemaVersion", "publisherPublicKey"})


class SeedSkillLoadRejected(ValueError):
    """The seed anchor or a seed record cannot be loaded as provisioned."""


class SkillExecutionKind(StrEnum):
    REPLAYED = "replayed"
    NO_ROUTE = "no_route"
    RECOVERY_PENDING = "recovery_pending"
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True)
class SkillExecutionReport:
    kind: SkillExecutionKind
    skill_id: str
    version: int | None
    outcome: ReplayOutcome | None
    decision: HandbackDecision | None
    detail: str


class SkillStatsStore:
    """Per (skillId, version) outcome history with a monotone attempt clock."""

    def __init__(self) -> None:
        self._records: dict[SkillVersionKey, VersionStats] = {}
        self._clock = 0

    def record(self, skill_id: str, version: int, *, succeeded: bool) -> None:
        self._clock += 1
        existing = self._records.get((skill_id, version), VersionStats(0, 0, 0))
        self._records[(skill_id, version)] = VersionStats(
            successes=existing.successes + (1 if succeeded else 0),
            failures=existing.failures + (0 if succeeded else 1),
            last_hit=self._clock,
        )

    def scoped(self, skill_id: str) -> dict[int, VersionStats]:
        return {
            version: record
            for (owner, version), record in self._records.items()
            if owner == skill_id
        }

    def snapshot(self) -> dict[SkillVersionKey, VersionStats]:
        """For the SA-07 management projection, which keys stats the same way."""
        return dict(self._records)


def _context_of(skill: AutomationSkill) -> PageContext:
    return PageContext(
        fingerprint=skill.fingerprint_sha256,
        language=skill.language,
        viewport_width=skill.viewport_width,
    )


def _routing_candidate(
    record: SignedSkill, disabled: set[SkillVersionKey]
) -> dict[str, object]:
    skill = record.skill
    return {
        "version": skill.version,
        "fingerprint": skill.fingerprint_sha256,
        "language": skill.language,
        "viewportWidth": skill.viewport_width,
        "disabled": (skill.skill_id, skill.version) in disabled,
    }


class SkillOrchestrator:
    def __init__(
        self,
        registry: SkillRegistry,
        *,
        stats: SkillStatsStore | None = None,
        disabled: set[SkillVersionKey] | None = None,
    ) -> None:
        self._registry = registry
        self._stats = stats if stats is not None else SkillStatsStore()
        self._disabled = set(disabled or ())

    @property
    def stats(self) -> SkillStatsStore:
        return self._stats

    def execute(
        self,
        skill_id: str,
        page: ReplayPage,
        *,
        parameters: dict[str, str],
        context: PageContext | None = None,
        on_external_dispatch: Callable[[], None] | None = None,
    ) -> SkillExecutionReport:
        records = sorted(
            (
                record
                for record in self._registry.records()
                if record.skill.skill_id == skill_id
            ),
            key=lambda record: record.version,
        )
        if not records:
            return self._no_route(skill_id, "no version is published for this skill")

        chosen_context = context or _context_of(records[-1].skill)
        candidates = [_routing_candidate(record, self._disabled) for record in records]
        try:
            version = route_skill(
                candidates, chosen_context, self._stats.scoped(skill_id)
            )
        except NoRouteAvailable as error:
            return self._no_route(skill_id, str(error))

        skill = self._registry.at(skill_id, version).skill
        try:
            # A hook exception propagates raw (see replay_skill): the ledger's
            # refusal is not a skill failure and must not enter the stats.
            outcome = replay_skill(
                skill,
                page,
                parameters=parameters,
                on_external_dispatch=on_external_dispatch,
            )
        except ReplayFailed as failure:
            self._stats.record(skill_id, version, succeeded=False)
            decision = decide_handback(skill, failure)
            if decision.action == "reconcile_only":
                return SkillExecutionReport(
                    kind=SkillExecutionKind.RECONCILE_REQUIRED,
                    skill_id=skill_id,
                    version=version,
                    outcome=None,
                    decision=decision,
                    detail=(
                        "an external side effect was dispatched before the failure; "
                        "reconcile against the platform's real state"
                    ),
                )
            return SkillExecutionReport(
                kind=SkillExecutionKind.RECOVERY_PENDING,
                skill_id=skill_id,
                version=version,
                outcome=None,
                decision=decision,
                detail=(
                    "replay failed before any side effect; "
                    "the skill awaits repair or re-recording"
                ),
            )
        self._stats.record(skill_id, version, succeeded=True)
        return SkillExecutionReport(
            kind=SkillExecutionKind.REPLAYED,
            skill_id=skill_id,
            version=version,
            outcome=outcome,
            decision=None,
            detail="replay completed and its success evidence held",
        )

    def _no_route(self, skill_id: str, reason: str) -> SkillExecutionReport:
        return SkillExecutionReport(
            kind=SkillExecutionKind.NO_ROUTE,
            skill_id=skill_id,
            version=None,
            outcome=None,
            decision=None,
            detail=reason,
        )


def load_publisher_anchor(path: Path = SEED_ANCHOR_PATH) -> bytes:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SeedSkillLoadRejected("the publisher anchor file is missing") from None
    except (OSError, json.JSONDecodeError) as error:
        raise SeedSkillLoadRejected("the publisher anchor file is unreadable") from error
    if (
        not isinstance(document, dict)
        or set(document) != _ANCHOR_KEYS
        or document["schemaVersion"] != 1
    ):
        raise SeedSkillLoadRejected("the publisher anchor is malformed")
    key = document["publisherPublicKey"]
    if not isinstance(key, str) or _HEX_KEY.fullmatch(key) is None:
        raise SeedSkillLoadRejected("the publisher key must be 32 lowercase hex bytes")
    return bytes.fromhex(key)


def _seed_order_key(record: object) -> tuple[str, int]:
    """Publish parents before children; malformed records sort first and are
    then rejected loudly by the registry itself."""
    if isinstance(record, dict):
        skill = record.get("skill")
        if isinstance(skill, dict):
            identifier = skill.get("skillId")
            version = skill.get("version")
            if (
                isinstance(identifier, str)
                and isinstance(version, int)
                and not isinstance(version, bool)
            ):
                return (identifier, version)
    return ("", 0)


def load_seed_registry(
    *,
    anchor_path: Path = SEED_ANCHOR_PATH,
    seeds_root: Path = SEED_SKILLS_ROOT,
) -> SkillRegistry:
    registry = SkillRegistry(trusted_public_key=load_publisher_anchor(anchor_path))
    if not seeds_root.is_dir():
        raise SeedSkillLoadRejected("the seed skills directory is missing")
    records: list[object] = []
    for file in sorted(seeds_root.rglob("*.json")):
        try:
            records.append(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise SeedSkillLoadRejected(
                f"seed record {file.name} is unreadable"
            ) from error
    for record in sorted(records, key=_seed_order_key):
        registry.publish(record)
    return registry


__all__ = [
    "SEED_ANCHOR_PATH",
    "SEED_SKILLS_ROOT",
    "SeedSkillLoadRejected",
    "SkillExecutionKind",
    "SkillExecutionReport",
    "SkillOrchestrator",
    "SkillStatsStore",
    "load_publisher_anchor",
    "load_seed_registry",
]
