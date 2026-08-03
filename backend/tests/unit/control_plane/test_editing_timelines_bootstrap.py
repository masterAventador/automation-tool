"""The timeline service the API is wired to reads a real UTC clock.

Timeline revisions are ordered by the moment they were saved, so a clock that
answered without a zone would make two saves incomparable across machines.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from automation_tool.control_plane.bootstrap.editing_timelines import timeline_service
from automation_tool.control_plane.infrastructure.database import Database


class _UnusedDatabase(Database):
    """Satisfies the wiring; no session is opened while only the clock is read."""

    def __init__(self) -> None:
        pass

    def session(self) -> Any:
        raise AssertionError("the clock does not need a session")


def test_the_wired_timeline_service_reads_an_aware_utc_clock() -> None:
    before = datetime.now(UTC)

    moment = timeline_service(_UnusedDatabase())._clock()

    assert moment.tzinfo is UTC
    assert before <= moment <= datetime.now(UTC)
