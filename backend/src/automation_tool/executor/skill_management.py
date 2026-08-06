"""SA-07: the management read model over the immutable skill registry.

An operator UI renders this projection: per skill, the full version tree (every
immutable version, disabled ones marked but never dropped), each version's
success rate and its parent, the variant that applies to a given page, and the
fixed control set (review / disable / rollback).

Routing here is the SA-06 rule exactly — ``applicableVersionFor`` returns the
version SA-06 would pick, or ``None`` when the page has drifted away from every
known variant, so the UI can surface "needs re-learning" instead of a stale
match. The adversarial matrix (prompt injection, version poisoning, drift) is
proven against the composed SA-01..06 defenses in the tests, not re-implemented.
"""

from __future__ import annotations

from collections.abc import Callable

from automation_tool.executor.skill_registry import SignedSkill, SkillRegistry
from automation_tool.executor.skill_router import (
    NoRouteAvailable,
    PageContext,
    VersionStats,
    route_skill,
)

_CONTROLS = ["review", "disable", "rollback"]

# Stats and disabled entries are keyed by (skillId, version). Version numbers
# restart at 1 for every skill, so a bare version key made skill A's record
# bleed into skill B's same-numbered version — wrong success rates on the
# operator UI, and one disable switching off every skill's v1 at once
# (REVIEW-2026-08-06 SA#2).
SkillVersionKey = tuple[str, int]


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


def _version_node(
    record: SignedSkill, stats: VersionStats | None, disabled: bool
) -> dict[str, object]:
    return {
        "version": record.version,
        "parentVersion": record.skill.parent_version,
        "riskLevel": record.skill.risk_level,
        "stepCount": len(record.skill.steps),
        "successRate": stats.success_rate if stats else None,
        "disabled": disabled,
    }


def build_management_view(
    registry: SkillRegistry,
    stats: dict[SkillVersionKey, VersionStats],
    *,
    disabled: set[SkillVersionKey],
) -> list[dict[str, object]]:
    by_skill: dict[str, list[SignedSkill]] = {}
    for record in registry.records():
        by_skill.setdefault(record.skill.skill_id, []).append(record)

    view: list[dict[str, object]] = []
    for skill_id, records in by_skill.items():
        ordered = sorted(records, key=lambda record: record.version)
        candidates = [_routing_candidate(record, disabled) for record in ordered]
        # SA-06's router ranks within one skill, so it keeps its bare version
        # keys — the scoping to this skill happens here, once.
        scoped_stats = {
            version: record
            for (owner, version), record in stats.items()
            if owner == skill_id
        }

        def applicable(
            context: PageContext,
            candidates: list[dict[str, object]] = candidates,
            scoped_stats: dict[int, VersionStats] = scoped_stats,
        ) -> int | None:
            try:
                return route_skill(candidates, context, scoped_stats)
            except NoRouteAvailable:
                return None

        view.append(
            {
                "skillId": skill_id,
                "platform": ordered[0].skill.platform,
                "domain": ordered[0].skill.domain,
                "fingerprint": ordered[0].skill.fingerprint_sha256,
                "versions": [
                    _version_node(
                        record,
                        stats.get((skill_id, record.version)),
                        (skill_id, record.version) in disabled,
                    )
                    for record in ordered
                ],
                "controls": list(_CONTROLS),
                "applicableVersionFor": applicable,
            }
        )
    return view


__all__ = ["SkillVersionKey", "build_management_view"]


# The management view is a Callable-bearing projection; typing helper so callers
# know the shape of `applicableVersionFor` without importing the dict soup.
ApplicableVersionFor = Callable[[PageContext], "int | None"]
