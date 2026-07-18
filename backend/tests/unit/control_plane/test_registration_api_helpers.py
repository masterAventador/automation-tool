import base64
from uuid import UUID

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.registrations import (
    InstallationRegistrationRequest,
    _bootstrap_token,
    _decode_base64url,
    _translate_registration_error,
)
from automation_tool.control_plane.application.registration import (
    InstallationAlreadyRegistered,
    InvalidRegistrationRequest,
)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.mark.parametrize(
    ("value", "exact_length"),
    (
        ("not+base64url", None),
        ("A", None),
        ("AB", None),
        (base64url(b"x" * 31), 32),
    ),
)
def test_base64url_decoder_rejects_pattern_decode_canonical_and_length_failures(
    value: str,
    exact_length: int | None,
) -> None:
    with pytest.raises(InvalidRegistrationRequest):
        _decode_base64url(value, exact_length=exact_length)


def test_non_bearer_credentials_are_rejected_even_when_called_directly() -> None:
    with pytest.raises(AppError) as captured:
        _bootstrap_token(HTTPAuthorizationCredentials(scheme="Basic", credentials="private"))

    assert captured.value.status_code == 401
    assert captured.value.code == "bootstrap_invalid"


def test_non_v4_challenge_id_is_rejected_by_request_schema() -> None:
    with pytest.raises(ValidationError):
        InstallationRegistrationRequest(
            challengeId=UUID("123e4567-e89b-12d3-a456-426614174000"),
            environmentId="demo-cn-1",
            signingPayload="A",
            signature="A" * 86,
        )


def test_error_translation_covers_validation_conflict_and_unknown_failures() -> None:
    validation = _translate_registration_error(InvalidRegistrationRequest())
    conflict = _translate_registration_error(InstallationAlreadyRegistered())
    unexpected = RuntimeError("private")

    assert (validation.status_code, validation.code) == (422, "validation")
    assert (conflict.status_code, conflict.code) == (409, "installation_exists")
    with pytest.raises(RuntimeError) as captured:
        _translate_registration_error(unexpected)
    assert captured.value is unexpected
