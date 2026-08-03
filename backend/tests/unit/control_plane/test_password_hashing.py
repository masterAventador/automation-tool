import pytest

from automation_tool.control_plane.domain import InvalidAccountModel, PasswordHash
from automation_tool.control_plane.infrastructure.security.passwords import (
    Argon2idPasswordHasher,
)

PASSWORD = "correct horse battery staple"
PEPPER = b"p" * 32


def test_password_hashing_uses_frozen_rfc_9106_argon2id_parameters_and_unique_salts() -> None:
    hasher = Argon2idPasswordHasher(pepper=PEPPER, pepper_version=7)

    first = hasher.hash(PASSWORD)
    second = hasher.hash(PASSWORD)

    assert first.encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert second.encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert first.encoded != second.encoded
    assert first.pepper_version == 7
    assert first.version == 1
    assert PASSWORD not in first.encoded
    assert PEPPER.decode() not in first.encoded
    assert first.encoded not in repr(first)
    assert PASSWORD not in repr(first)


def test_password_verification_requires_the_matching_password_and_deployment_pepper() -> None:
    stored = Argon2idPasswordHasher(pepper=PEPPER, pepper_version=7).hash(PASSWORD)

    assert Argon2idPasswordHasher(pepper=PEPPER, pepper_version=7).verify(PASSWORD, stored)
    assert not Argon2idPasswordHasher(pepper=PEPPER, pepper_version=7).verify(
        "wrong password value", stored
    )
    assert not Argon2idPasswordHasher(pepper=b"q" * 32, pepper_version=7).verify(PASSWORD, stored)
    assert not Argon2idPasswordHasher(pepper=PEPPER, pepper_version=8).verify(PASSWORD, stored)
    assert not Argon2idPasswordHasher(pepper=PEPPER, pepper_version=7).verify(PASSWORD, object())


@pytest.mark.parametrize(
    "password",
    (None, b"not-text", "short", "x" * 129, "valid-length\ud800"),
)
def test_password_hashing_rejects_invalid_secrets_without_echoing(password: object) -> None:
    hasher = Argon2idPasswordHasher(pepper=PEPPER, pepper_version=1)

    with pytest.raises(InvalidAccountModel) as captured:
        hasher.hash(password)

    assert str(captured.value) == "Account model is invalid"
    assert repr(password) not in repr(captured.value)


@pytest.mark.parametrize(
    ("pepper", "pepper_version"),
    ((b"short", 1), (b"p" * 33, 1), ("p" * 32, 1), (b"p" * 32, 0), (b"p" * 32, True)),
)
def test_password_hasher_rejects_invalid_deployment_secrets(
    pepper: object,
    pepper_version: object,
) -> None:
    with pytest.raises(RuntimeError) as captured:
        Argon2idPasswordHasher(pepper=pepper, pepper_version=pepper_version)

    assert str(captured.value) == "Password hasher configuration is invalid"
    assert repr(pepper) not in repr(captured.value)


@pytest.mark.parametrize(
    ("encoded", "pepper_version", "version"),
    (
        (1, 1, 1),
        ("x" * 256, 1, 1),
        ("not-an-argon-hash", 1, 1),
        ("$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA", 1, 1),
        ("$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA", True, 1),
        ("$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA", 0, 1),
        ("$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA", 1, True),
        ("$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA", 1, 0),
    ),
)
def test_stored_password_hash_rejects_untrusted_or_unversioned_values(
    encoded: object,
    pepper_version: object,
    version: object,
) -> None:
    with pytest.raises(InvalidAccountModel):
        PasswordHash(
            encoded=encoded,
            pepper_version=pepper_version,
            version=version,
        )
