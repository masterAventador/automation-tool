import base64
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.bootstrap import account_operations_cli

CAPABILITY = "atoc1." + base64.urlsafe_b64encode(b"c" * 32).rstrip(b"=").decode("ascii")
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


def digest(value: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST", digest(CAPABILITY))
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID", str(ACTOR_ID))


def test_console_script_is_separate_from_public_http_and_accepts_secrets_only_on_stdin() -> None:
    pyproject = (Path(__file__).parents[3] / "pyproject.toml").read_text()
    assert (
        "automation-tool-account-operations = "
        '"automation_tool.control_plane.bootstrap.account_operations_cli:main"'
    ) in pyproject

    parser = account_operations_cli.build_parser()
    help_text = parser.format_help()
    assert "--password" not in help_text
    assert "--capability" not in help_text


def test_capability_is_canonical_digest_authenticated_without_reflection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    verifier = account_operations_cli.operations_identity_from_environment()
    assert verifier.authenticate(CAPABILITY).actor_id == ACTOR_ID

    for invalid in ("private", CAPABILITY + "x", "atoc1.private"):
        with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected) as error:
            verifier.authenticate(invalid)
        assert invalid not in str(error.value)


def test_create_reads_capability_and_password_from_bounded_json_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    captured: dict[str, object] = {}

    async def execute(
        command: str, arguments: object, payload: dict[str, object]
    ) -> dict[str, object]:
        captured.update(command=command, arguments=arguments, payload=payload)
        return {"status": "active", "revision": 1}

    monkeypatch.setattr(account_operations_cli, "_execute", execute)
    output = io.StringIO()
    account_operations_cli.main(
        ["create", "--login-name", "demo.operator", "--request-id", "create-demo"],
        input_stream=io.StringIO(
            json.dumps({"capability": CAPABILITY, "password": "correct horse battery staple"})
        ),
        output_stream=output,
    )

    assert captured["command"] == "create"
    assert captured["payload"] == {
        "capability": CAPABILITY,
        "password": "correct horse battery staple",
    }
    assert json.loads(output.getvalue()) == {"revision": 1, "status": "active"}


def test_malformed_oversized_or_extra_stdin_fails_with_one_fixed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch)
    cases = (
        "not-json",
        json.dumps({"capability": CAPABILITY, "password": "x", "extra": "private"}),
        "x" * 4097,
    )
    for payload in cases:
        with pytest.raises(SystemExit) as error:
            account_operations_cli.main(
                ["create", "--login-name", "demo.operator", "--request-id", "request"],
                input_stream=io.StringIO(payload),
                output_stream=io.StringIO(),
            )
        assert str(error.value) == "Account operations command failed"
        assert payload not in str(error.value)


def test_operations_identity_configuration_and_digest_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST", raising=False)
    monkeypatch.delenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID", raising=False)
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        account_operations_cli.operations_identity_from_environment()

    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST", "invalid")
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID", str(ACTOR_ID))
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        account_operations_cli.operations_identity_from_environment()

    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST", digest(CAPABILITY))
    monkeypatch.setenv(
        "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID",
        "123e4567-e89b-12d3-a456-426614174000",
    )
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        account_operations_cli.operations_identity_from_environment()


def test_capability_digest_mismatch_and_decoder_failures_are_uniform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = account_operations_cli.OperationsIdentity(
        capability_digest=b"x" * 32,
        actor_id=uuid4(),
    )
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        verifier.authenticate(CAPABILITY)

    def reject_decode(_value: str) -> bytes:
        raise ValueError

    monkeypatch.setattr(base64, "urlsafe_b64decode", reject_decode)
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        verifier.authenticate(CAPABILITY)
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        account_operations_cli._decode_digest(digest(CAPABILITY))


def test_noncanonical_decoded_digest_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    canonical_digest = digest(CAPABILITY)
    monkeypatch.setattr(
        base64,
        "urlsafe_b64encode",
        lambda _value: b"x" * 43,
    )
    with pytest.raises(account_operations_cli.AccountOperationsAuthenticationRejected):
        account_operations_cli._decode_digest(canonical_digest)


@pytest.mark.asyncio
async def test_reset_fails_closed_when_account_sessions_are_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIdentity:
        @staticmethod
        def authenticate(_capability: object) -> object:
            return object()

    class FakeDatabase:
        closed = False

        async def close(self) -> None:
            self.closed = True

    database = FakeDatabase()
    monkeypatch.setattr(
        account_operations_cli,
        "operations_identity_from_environment",
        lambda: FakeIdentity(),
    )
    monkeypatch.setattr(account_operations_cli, "database_from_environment", lambda: database)
    monkeypatch.setattr(account_operations_cli, "account_password_hasher_from_environment", object)
    monkeypatch.setattr(
        account_operations_cli,
        "SqlAlchemyCustomerAccountRepository",
        lambda _database: object(),
    )
    monkeypatch.setattr(
        account_operations_cli,
        "CustomerAccountService",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        account_operations_cli,
        "account_session_service_from_environment",
        lambda _database: None,
    )
    arguments = account_operations_cli.build_parser().parse_args(
        ["reset", "--login-name", "demo.operator", "--request-id", "reset-demo"]
    )

    with pytest.raises(RuntimeError):
        await account_operations_cli._execute("reset", arguments, {"capability": CAPABILITY})
    assert database.closed is True


def test_operations_clock_is_utc() -> None:
    assert account_operations_cli._Clock().now().tzinfo is not None
