"""BU-04: exclusive action-ownership lease over the one operations browser.

The operations browser has exactly one action owner at any moment. The
Playwright executor owns the surface by default; a Browser Use takeover for
the douyin publish flow is granted only after the executor has confirmed its
actions are paused, receives a high-entropy token bound to a random-loopback
CDP endpoint, and carries a hard deadline. Expiry, borrower failure or a
release without a confirmed CDP disconnect never silently returns ownership:
the lease enters ``RECLAIM_REQUIRED`` where *both* controllers are denied
until the caller confirms the surface has been reclaimed (borrower session
verified closed, or the browser process restarted). Tokens never appear in
``repr`` or logs.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Final

_CDP_URL_PATTERN: Final = re.compile(r"^http://127\.0\.0\.1:\d{2,5}$")
_MAX_LEASE_SECONDS: Final = 3600.0
_TOKEN_BYTES: Final = 32


class SurfaceLeaseRejected(RuntimeError):
    """The lease transition or authorization violates the exclusivity policy."""

    def __init__(self) -> None:
        super().__init__("browser surface lease operation rejected")


@unique
class LeaseState(StrEnum):
    """Closed lease lifecycle for the single operations-browser surface."""

    OWNER_ACTIVE = "owner_active"
    LEASED = "leased"
    RECLAIM_REQUIRED = "reclaim_required"


@dataclass(frozen=True, repr=False)
class SurfaceLeaseGrant:
    """One takeover grant; the token authorizes the borrower exclusively."""

    token: str = field(repr=False)
    cdp_url: str
    deadline: float

    def __repr__(self) -> str:
        return f"SurfaceLeaseGrant(cdp_url={self.cdp_url!r}, token=<redacted>)"


class BrowserSurfaceLeaseManager:
    """Single-holder action-ownership ledger for the operations browser."""

    __slots__ = ("_clock", "_deadline", "_state", "_token")

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._state = LeaseState.OWNER_ACTIVE
        self._token: str | None = None
        self._deadline = 0.0

    def state(self) -> LeaseState:
        self._expire_if_due()
        return self._state

    def begin_takeover(
        self, *, cdp_url: str, timeout_seconds: float, pause_confirmed: bool
    ) -> SurfaceLeaseGrant:
        """Grant exclusive borrower authority after the owner confirmed pause."""
        self._expire_if_due()
        if (
            self._state is not LeaseState.OWNER_ACTIVE
            or pause_confirmed is not True
            or type(cdp_url) is not str
            or _CDP_URL_PATTERN.fullmatch(cdp_url) is None
            or type(timeout_seconds) not in {int, float}
            or not 0 < float(timeout_seconds) <= _MAX_LEASE_SECONDS
        ):
            raise SurfaceLeaseRejected
        token = secrets.token_hex(_TOKEN_BYTES)
        self._token = token
        self._deadline = self._clock() + float(timeout_seconds)
        self._state = LeaseState.LEASED
        return SurfaceLeaseGrant(token=token, cdp_url=cdp_url, deadline=self._deadline)

    def authorize_playwright_action(self) -> None:
        """Allow one deterministic-executor action only while owner-active."""
        self._expire_if_due()
        if self._state is not LeaseState.OWNER_ACTIVE:
            raise SurfaceLeaseRejected

    def authorize_borrower(self, token: str) -> None:
        """Allow one borrower action only for the live, unexpired grant."""
        self._expire_if_due()
        if self._state is not LeaseState.LEASED or not self._token_matches(token):
            raise SurfaceLeaseRejected

    def release(self, token: str, *, disconnect_confirmed: bool) -> None:
        """Return ownership only after the borrower confirmed CDP disconnect."""
        self._expire_if_due()
        if self._state is not LeaseState.LEASED or not self._token_matches(token):
            raise SurfaceLeaseRejected
        if disconnect_confirmed is not True:
            raise SurfaceLeaseRejected
        self._token = None
        self._deadline = 0.0
        self._state = LeaseState.OWNER_ACTIVE

    def report_borrower_failure(self, token: str) -> None:
        """A borrower crash/timeout forces the reclaim path; never auto-return."""
        if self._state is LeaseState.LEASED and self._token_matches(token):
            self._enter_reclaim()
        elif self._state is not LeaseState.RECLAIM_REQUIRED:
            raise SurfaceLeaseRejected

    def confirm_surface_reclaimed(self) -> None:
        """The caller verified no external session remains (or restarted the browser)."""
        if self._state is not LeaseState.RECLAIM_REQUIRED:
            raise SurfaceLeaseRejected
        self._token = None
        self._deadline = 0.0
        self._state = LeaseState.OWNER_ACTIVE

    def _token_matches(self, token: object) -> bool:
        return (
            type(token) is str
            and self._token is not None
            and secrets.compare_digest(token, self._token)
        )

    def _expire_if_due(self) -> None:
        if self._state is LeaseState.LEASED and self._clock() > self._deadline:
            self._enter_reclaim()

    def _enter_reclaim(self) -> None:
        self._token = None
        self._deadline = 0.0
        self._state = LeaseState.RECLAIM_REQUIRED

    def __repr__(self) -> str:
        return f"BrowserSurfaceLeaseManager(state={self._state.value!r})"


__all__ = [
    "BrowserSurfaceLeaseManager",
    "LeaseState",
    "SurfaceLeaseGrant",
    "SurfaceLeaseRejected",
]
