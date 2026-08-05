"""SA-06: route a page to a published skill version — never just the newest.

Published versions are immutable and never deleted (SA-03 keeps every one). The
router's job is to pick, for the page actually in front of it, the version most
likely to succeed:

1. **eligibility** — only versions whose page fingerprint, language and viewport
   match the context, and that are not rolled back, are candidates. A/B variants
   that match the same page all stay eligible;
2. **ranking** — among eligible versions, higher historical success rate wins;
   ties break by most recent hit, then by higher version. A version with no
   history yet is reachable (so it can earn a record) but ranks below any proven
   one.

Rollback is exclusion, not deletion: a disabled version is skipped and routing
falls back to another, and re-enabling it restores it — because the record was
never removed.
"""

from __future__ import annotations

from dataclasses import dataclass


class NoRouteAvailable(RuntimeError):
    """No published version matches this page and is enabled."""


@dataclass(frozen=True)
class PageContext:
    fingerprint: str
    language: str
    viewport_width: int


@dataclass(frozen=True)
class VersionStats:
    successes: int
    failures: int
    last_hit: int

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total else 0.0

    @property
    def observed(self) -> bool:
        return (self.successes + self.failures) > 0


def _version_of(candidate: dict[str, object]) -> int:
    value = candidate["version"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise NoRouteAvailable("a routing candidate has no integer version")
    return value


def _eligible(candidate: dict[str, object], context: PageContext) -> bool:
    return (
        candidate.get("disabled") is not True
        and candidate.get("fingerprint") == context.fingerprint
        and candidate.get("language") == context.language
        and candidate.get("viewportWidth") == context.viewport_width
    )


def route_skill(
    candidates: list[dict[str, object]],
    context: PageContext,
    stats: dict[int, VersionStats],
) -> int:
    eligible = [candidate for candidate in candidates if _eligible(candidate, context)]
    if not eligible:
        raise NoRouteAvailable("no enabled version matches this page")

    def rank(candidate: dict[str, object]) -> tuple[int, float, int, int]:
        version = _version_of(candidate)
        record = stats.get(version)
        if record is None or not record.observed:
            # Unproven: reachable, but below anything with a record.
            return (0, 0.0, 0, version)
        return (1, record.success_rate, record.last_hit, version)

    best = max(eligible, key=rank)
    return _version_of(best)


__all__ = ["NoRouteAvailable", "PageContext", "VersionStats", "route_skill"]
