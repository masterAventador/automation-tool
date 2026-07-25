from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.health import (
    DouyinSessionHealthReporter,
    DouyinSessionHealthReportRejected,
    SystemSessionHealthClock,
)
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.protocol import PlatformSessionHealthEnvelope, PlatformSessionState

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"


class FixedClock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"823e4567-e89b-42d3-a456-{self.value:012d}")


class Locator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> Locator:
        return self

    def is_visible(self) -> bool:
        return self._visible

    def locator(self, selector: str) -> Locator:
        assert selector == VISIBLE_MATCH_ENGINE
        return self

    def count(self) -> int:
        return 1 if self._visible else 0


class Page:
    url = "https://www.douyin.com/user/self"

    def __init__(self, selector: str) -> None:
        self.selector = selector

    def locator(self, selector: str) -> Locator:
        return Locator(self.selector in selector.split(", "))


def window(selector: str) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, Page(selector)))


def reporter(state_directory: Path, clock: FixedClock) -> DouyinSessionHealthReporter:
    return DouyinSessionHealthReporter(
        ledger=ExecutorLedger(
            state_directory=state_directory,
            installation_id=INSTALLATION_ID,
            executor_id=EXECUTOR_ID,
        ),
        clock=clock,
        id_source=Ids(),
    )


def test_real_detector_fact_becomes_typed_durable_non_sensitive_wire_report(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    health = reporter(tmp_path / "state", clock)

    report = health.observe(
        window('[data-e2e="login-button"]'),
        sequence=2,
    )

    assert isinstance(report, PlatformSessionHealthEnvelope)
    assert report.payload.platform == "douyin"
    assert report.payload.state is PlatformSessionState.MISSING
    assert report.payload.session_revision == 1
    assert report.payload.observed_at == NOW
    assert report.sent_at == NOW
    assert report.deadline_at == NOW + timedelta(seconds=30)
    serialized = report.model_dump_json().lower()
    for forbidden in ("cookie", "profile", "captcha", "qr_code", "page_text"):
        assert forbidden not in serialized


def test_page_recovery_requires_explicit_new_epoch_and_survives_reopen(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    clock = FixedClock()
    first = reporter(state_directory, clock)
    first.observe(window('[data-e2e="login-expired"]'), sequence=2)
    clock.value += timedelta(seconds=1)

    with pytest.raises(DouyinSessionHealthReportRejected):
        first.observe(window('[data-e2e="user-avatar"]'), sequence=3)

    recovered = reporter(state_directory, clock).observe(
        window('[data-e2e="user-avatar"]'),
        sequence=3,
        recovered=True,
    )

    assert recovered.payload.state is PlatformSessionState.HEALTHY
    assert recovered.payload.session_revision == 2
    persisted = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    ).get_platform_session("douyin")
    assert persisted is not None
    assert persisted.session_revision == 2
    assert persisted.circuit_open is False


def test_first_observation_can_reuse_an_already_healthy_persistent_profile(
    tmp_path: Path,
) -> None:
    health = reporter(tmp_path / "state", FixedClock())

    reused = health.observe(
        window('[data-e2e="user-avatar"]'),
        sequence=2,
        recovered=True,
    )

    assert reused.payload.state is PlatformSessionState.HEALTHY
    assert reused.payload.session_revision == 1
    assert reused.payload.observed_at == NOW


def test_explicit_logout_fact_advances_to_missing_without_page_inference(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    clock = FixedClock()
    health = reporter(state_directory, clock)
    health.observe(window('[data-e2e="user-avatar"]'), sequence=2)
    clock.value += timedelta(seconds=1)

    logged_out = health.record_logout(sequence=3)

    assert logged_out.payload.state is PlatformSessionState.MISSING
    assert logged_out.payload.session_revision == 2
    assert logged_out.payload.observed_at == clock.value
    persisted = ExecutorLedger(
        state_directory=state_directory,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    ).get_platform_session("douyin")
    assert persisted is not None
    assert persisted.state is PlatformSessionState.MISSING
    assert persisted.session_revision == 2


def test_reporter_rejects_invalid_dependencies_sequence_clock_and_ids(tmp_path: Path) -> None:
    ledger = ExecutorLedger(
        state_directory=tmp_path / "state",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )
    for arguments in (
        {"ledger": object(), "clock": FixedClock(), "id_source": Ids()},
        {"ledger": ledger, "clock": object(), "id_source": Ids()},
        {"ledger": ledger, "clock": FixedClock(), "id_source": object()},
    ):
        with pytest.raises(DouyinSessionHealthReportRejected):
            DouyinSessionHealthReporter(**arguments)  # type: ignore[arg-type]

    health = DouyinSessionHealthReporter(
        ledger=ledger,
        clock=FixedClock(),
        id_source=lambda: UUID(int=0),
    )
    for sequence in (0, True, 2**53):
        with pytest.raises(DouyinSessionHealthReportRejected):
            health.observe(window('[data-e2e="login-button"]'), sequence=sequence)
    with pytest.raises(DouyinSessionHealthReportRejected):
        health.observe(window('[data-e2e="login-button"]'), sequence=2)


def test_logout_rejects_invalid_sequence_and_invalid_clock_without_writing(tmp_path: Path) -> None:
    clock = FixedClock()
    health = reporter(tmp_path / "state", clock)
    for sequence in (0, True, 2**53):
        with pytest.raises(DouyinSessionHealthReportRejected):
            health.record_logout(sequence=sequence)

    clock.value = datetime(2026, 7, 19, 12, 0)
    with pytest.raises(DouyinSessionHealthReportRejected):
        health.record_logout(sequence=2)
    assert health._ledger.get_platform_session("douyin") is None
    assert SystemSessionHealthClock().now().utcoffset() == UTC.utcoffset(NOW)
