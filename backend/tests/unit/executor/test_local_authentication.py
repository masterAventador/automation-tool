from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import SecretStr

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.bootstrap import (
    ExecutorBootstrapRejected,
    read_executor_bootstrap,
)

LOCAL_SESSION_TOKEN = "".join(f"{value:02x}" for value in range(32))
EXPECTED_HEALTHY_PROOF = "atlep1.NOuvIGSTV1bPoAZcqjJCd4V0TtBvVdvc4nPHufoUpRY"
STATE_DIRECTORY = str((Path.cwd() / ".automation-tool-executor-auth-test").resolve())


def bootstrap_source(local_session_token: object = LOCAL_SESSION_TOKEN) -> bytes:
    return (
        json.dumps(
            {
                "bootstrap_version": "1",
                "websocket_url": "ws://127.0.0.1:8765/api/v1/executors/connect",
                "local_session_token": local_session_token,
                "session_token": "atds1.private-control-plane-session",
                "installation_id": "123e4567-e89b-42d3-a456-426614174003",
                "executor_id": "123e4567-e89b-42d3-a456-426614174004",
                "heartbeat_interval_seconds": 1,
                "state_directory": STATE_DIRECTORY,
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_bootstrap_keeps_local_and_control_plane_sessions_separate_and_secret() -> None:
    bootstrap = read_executor_bootstrap(BytesIO(bootstrap_source()))

    assert isinstance(bootstrap.local_session_token, SecretStr)
    assert bootstrap.local_session_token.get_secret_value() == LOCAL_SESSION_TOKEN
    assert bootstrap.session_token.get_secret_value().startswith("atds1.")
    assert LOCAL_SESSION_TOKEN not in repr(bootstrap)
    assert LOCAL_SESSION_TOKEN not in bootstrap.model_dump_json()


def test_local_authenticator_emits_domain_bound_non_reflective_proofs() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(LOCAL_SESSION_TOKEN))

    healthy = authenticator.proof_for("executor.healthy")
    stopped = authenticator.proof_for("executor.stopped")

    assert healthy == EXPECTED_HEALTHY_PROOF
    assert stopped != healthy
    assert healthy.startswith("atlep1.")
    assert LOCAL_SESSION_TOKEN not in healthy
    assert LOCAL_SESSION_TOKEN not in stopped
    assert LOCAL_SESSION_TOKEN not in repr(authenticator)


@pytest.mark.parametrize(
    "local_session_token",
    (
        "",
        "0" * 63,
        "0" * 65,
        "A" * 64,
        "g" * 64,
        "private-local-session",
        0,
        None,
    ),
)
def test_bootstrap_rejects_noncanonical_local_session_tokens_without_reflection(
    local_session_token: object,
) -> None:
    with pytest.raises(
        ExecutorBootstrapRejected,
        match=r"^Local Executor bootstrap is rejected$",
    ) as captured:
        read_executor_bootstrap(BytesIO(bootstrap_source(local_session_token)))

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private" not in str(captured.value).lower()


def test_authenticator_rejects_unknown_events_with_one_fixed_error() -> None:
    authenticator = LocalSessionAuthenticator(SecretStr(LOCAL_SESSION_TOKEN))

    for event in ("private.event", 0):
        with pytest.raises(
            LocalSessionAuthenticationRejected,
            match=r"^Local Executor authentication is rejected$",
        ) as captured:
            authenticator.proof_for(event)  # type: ignore[arg-type]

        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert "private" not in str(captured.value).lower()

    authenticator.close()
    with pytest.raises(LocalSessionAuthenticationRejected):
        authenticator.proof_for("executor.healthy")

    with pytest.raises(LocalSessionAuthenticationRejected):
        LocalSessionAuthenticator(SecretStr("A" * 64))
    with pytest.raises(LocalSessionAuthenticationRejected):
        LocalSessionAuthenticator("0" * 64)  # type: ignore[arg-type]
