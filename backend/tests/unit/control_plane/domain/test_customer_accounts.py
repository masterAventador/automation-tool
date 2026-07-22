from dataclasses import FrozenInstanceError

import pytest

from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountAuditEventType,
    AccountStatus,
    InvalidAccountModel,
    LoginName,
)


def test_login_names_are_ascii_case_insensitive_and_canonical() -> None:
    login_name = LoginName.parse("Alice.OPS-01")

    assert login_name.value == "alice.ops-01"
    assert str(login_name) == "alice.ops-01"
    assert LoginName.parse(login_name) is login_name
    assert "alice" not in repr(login_name).lower()

    with pytest.raises(FrozenInstanceError):
        login_name.value = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    (
        None,
        b"alice",
        "",
        "ab",
        "a" * 65,
        " alice",
        "alice ",
        "1alice",
        "alice+ops",
        "álîce",
        "\uff21lice",
        "alice\nops",
    ),
)
def test_login_names_reject_non_ascii_or_noncanonical_shapes_without_echoing(
    value: object,
) -> None:
    with pytest.raises(InvalidAccountModel) as captured:
        LoginName.parse(value)

    assert str(captured.value) == "Account model is invalid"
    assert repr(value) not in repr(captured.value)


def test_account_lifecycle_and_audit_vocabularies_are_closed() -> None:
    assert [status.value for status in AccountStatus] == ["active", "locked", "disabled"]
    assert [kind.value for kind in AccountAuditActorKind] == ["operations", "user", "system"]
    assert [event.value for event in AccountAuditEventType] == [
        "account.created",
        "account.locked",
        "account.unlocked",
        "account.disabled",
        "account.enabled",
        "login.succeeded",
        "login.failed",
        "credential.changed",
        "recovery.issued",
        "recovery.consumed",
        "session.refreshed",
        "session.logged_out",
        "session.reuse_detected",
        "session.all_revoked",
        "device.bound",
        "device.revoked",
    ]
