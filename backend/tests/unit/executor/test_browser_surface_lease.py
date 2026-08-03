"""BU-04: exclusive action-ownership lease over the one operations browser."""

from __future__ import annotations

import pytest

from automation_tool.executor.browser_surface_lease import (
    BrowserSurfaceLeaseManager,
    LeaseState,
    SurfaceLeaseRejected,
)

CDP = "http://127.0.0.1:53211"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def manager(clock: FakeClock) -> BrowserSurfaceLeaseManager:
    return BrowserSurfaceLeaseManager(clock=clock)


class TestTakeoverGrant:
    def test_playwright_owns_the_surface_by_default(self) -> None:
        lease = manager(FakeClock())
        assert lease.state() is LeaseState.OWNER_ACTIVE
        lease.authorize_playwright_action()  # 不抛即持有动作权

    def test_takeover_requires_confirmed_pause(self) -> None:
        lease = manager(FakeClock())
        with pytest.raises(SurfaceLeaseRejected):
            lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=False)
        assert lease.state() is LeaseState.OWNER_ACTIVE

    def test_grant_gives_exclusive_borrower_authority(self) -> None:
        clock = FakeClock()
        lease = manager(clock)
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        assert lease.state() is LeaseState.LEASED
        assert grant.cdp_url == CDP
        assert len(grant.token) >= 32
        lease.authorize_borrower(grant.token)
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_playwright_action()

    def test_second_takeover_while_leased_is_rejected(self) -> None:
        lease = manager(FakeClock())
        lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        with pytest.raises(SurfaceLeaseRejected):
            lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)

    def test_cdp_url_must_be_exact_loopback(self) -> None:
        lease = manager(FakeClock())
        for invalid in (
            "http://localhost:53211",
            "http://0.0.0.0:53211",
            "https://127.0.0.1:53211",
            "http://127.0.0.1:53211/path",
            "not-a-url",
        ):
            with pytest.raises(SurfaceLeaseRejected):
                lease.begin_takeover(cdp_url=invalid, timeout_seconds=60, pause_confirmed=True)

    def test_timeout_bounds_are_enforced(self) -> None:
        lease = manager(FakeClock())
        for invalid in (0, -1, 3601):
            with pytest.raises(SurfaceLeaseRejected):
                lease.begin_takeover(cdp_url=CDP, timeout_seconds=invalid, pause_confirmed=True)


class TestReleaseAndExpiry:
    def test_release_with_confirmed_disconnect_returns_ownership(self) -> None:
        lease = manager(FakeClock())
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        lease.release(grant.token, disconnect_confirmed=True)
        assert lease.state() is LeaseState.OWNER_ACTIVE
        lease.authorize_playwright_action()
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower(grant.token)

    def test_release_without_disconnect_confirmation_is_rejected(self) -> None:
        lease = manager(FakeClock())
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        with pytest.raises(SurfaceLeaseRejected):
            lease.release(grant.token, disconnect_confirmed=False)
        assert lease.state() is LeaseState.LEASED

    def test_wrong_token_cannot_release_or_act(self) -> None:
        lease = manager(FakeClock())
        lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        with pytest.raises(SurfaceLeaseRejected):
            lease.release("f" * 64, disconnect_confirmed=True)
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower("f" * 64)
        assert lease.state() is LeaseState.LEASED

    def test_expiry_denies_both_controllers_until_reclaimed(self) -> None:
        clock = FakeClock()
        lease = manager(clock)
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        clock.now += 61
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower(grant.token)
        assert lease.state() is LeaseState.RECLAIM_REQUIRED
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_playwright_action()
        with pytest.raises(SurfaceLeaseRejected):
            lease.release(grant.token, disconnect_confirmed=True)

    def test_borrower_crash_forces_reclaim_path(self) -> None:
        lease = manager(FakeClock())
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        lease.report_borrower_failure(grant.token)
        assert lease.state() is LeaseState.RECLAIM_REQUIRED
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_playwright_action()

    def test_confirmed_reclaim_restores_playwright_ownership(self) -> None:
        clock = FakeClock()
        lease = manager(clock)
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        clock.now += 120
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower(grant.token)
        lease.confirm_surface_reclaimed()
        assert lease.state() is LeaseState.OWNER_ACTIVE
        lease.authorize_playwright_action()

    def test_reclaim_confirmation_outside_reclaim_state_is_rejected(self) -> None:
        lease = manager(FakeClock())
        with pytest.raises(SurfaceLeaseRejected):
            lease.confirm_surface_reclaimed()

    def test_new_takeover_after_reclaim_issues_fresh_token(self) -> None:
        clock = FakeClock()
        lease = manager(clock)
        first = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        clock.now += 61
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower(first.token)
        lease.confirm_surface_reclaimed()
        second = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        assert second.token != first.token
        with pytest.raises(SurfaceLeaseRejected):
            lease.authorize_borrower(first.token)
        lease.authorize_borrower(second.token)

    def test_grant_repr_never_reveals_token(self) -> None:
        lease = manager(FakeClock())
        grant = lease.begin_takeover(cdp_url=CDP, timeout_seconds=60, pause_confirmed=True)
        assert grant.token not in repr(grant)
        assert grant.token not in repr(lease)
