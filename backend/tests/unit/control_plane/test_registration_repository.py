"""Deterministic coverage for registration challenge availability decisions."""

from datetime import UTC, datetime, timedelta

import pytest

from automation_tool.control_plane.application.registration import (
    RegistrationChallengeExpired,
    RegistrationChallengeUsed,
)
from automation_tool.control_plane.infrastructure.database.registration import (
    _validate_challenge_availability,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def test_available_challenge_is_accepted_before_expiry() -> None:
    _validate_challenge_availability(
        consumed_at=None,
        expires_at=NOW + timedelta(seconds=1),
        completed_at=NOW,
    )


def test_consumed_challenge_is_rejected_before_expiry_check() -> None:
    with pytest.raises(RegistrationChallengeUsed):
        _validate_challenge_availability(
            consumed_at=NOW - timedelta(seconds=1),
            expires_at=NOW - timedelta(hours=1),
            completed_at=NOW,
        )


@pytest.mark.parametrize("completed_at", [NOW, NOW + timedelta(microseconds=1)])
def test_expired_challenge_uses_a_strict_half_open_boundary(completed_at: datetime) -> None:
    with pytest.raises(RegistrationChallengeExpired):
        _validate_challenge_availability(
            consumed_at=None,
            expires_at=NOW,
            completed_at=completed_at,
        )
