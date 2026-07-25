from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest
from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_surface_lease import LeaseState
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.platform_commands import (
    DOUYIN_PUBLISH_DISPATCH_COMMAND,
    DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    DOUYIN_PUBLISH_RELEASE_COMMAND,
    DOUYIN_QR_LOGIN_FLOW_VERSION,
    PUBLISH_RELEASED_STATE,
    DouyinPublishPreflightCommandOperation,
    PlatformCommand,
    PlatformCommandRejected,
    PlatformCommandRouter,
    PlatformCommandWorker,
    read_platform_command,
    write_platform_command_result,
)
from automation_tool.executor.rpa.douyin.publish_preflight import (
    DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
)
from automation_tool.executor.rpa.douyin.publish_release import (
    DOUYIN_PUBLISH_RELEASE_FLOW_VERSION,
    DouyinPublishReleaseEvidence,
    DouyinPublishReleaseState,
)
from pydantic import SecretStr, ValidationError

TOKEN = "".join(f"{value:02x}" for value in range(32))
COMMAND_ID = "123e4567-e89b-42d3-a456-426614174005"
PUBLISH_JOB_ID = "123e4567-e89b-42d3-a456-426614174006"
EXECUTABLE = "/opt/automation-tool/chromium"
PROFILE = "/opt/automation-tool/profile"
ARTIFACT = "/opt/automation-tool/artifacts/clip.mp4"
TITLE = "自动化运营工具测试标题"
DESCRIPTION = "自动化运营工具测试简介"
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174011"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174012"


def publish_ledger(tmp_path: Any) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=Path(tmp_path) / "publish-ledger",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def authenticator() -> LocalSessionAuthenticator:
    return LocalSessionAuthenticator(SecretStr(TOKEN))


def publish_payload(**overrides: object) -> dict[str, object]:
    source = authenticator()
    payload: dict[str, object] = {
        "artifactPath": ARTIFACT,
        "commandId": COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        "description": DESCRIPTION,
        "executablePath": EXECUTABLE,
        "headless": True,
        "profileDirectory": PROFILE,
        "protocolVersion": "1.0",
        "publishJobId": PUBLISH_JOB_ID,
        "title": TITLE,
    }
    payload["authenticationProof"] = source.proof_for_publish_command(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        executable_path=EXECUTABLE,
        profile_directory=PROFILE,
        headless=True,
        publish_job_id=PUBLISH_JOB_ID,
        artifact_path=ARTIFACT,
        title=TITLE,
        description=DESCRIPTION,
    )
    payload.update(overrides)
    return payload


def test_publish_proof_binds_every_authenticated_field() -> None:
    source = authenticator()
    baseline: dict[str, Any] = dict(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        executable_path=EXECUTABLE,
        profile_directory=PROFILE,
        headless=True,
        publish_job_id=PUBLISH_JOB_ID,
        artifact_path=ARTIFACT,
        title=TITLE,
        description=DESCRIPTION,
    )
    proof = source.proof_for_publish_command(**baseline)
    source.verify_publish_command(**baseline, presented_proof=proof)
    for field, tampered in (
        ("artifact_path", "/opt/automation-tool/artifacts/other.mp4"),
        ("title", f"{TITLE}!"),
        ("description", f"{DESCRIPTION}!"),
        ("profile_directory", "/opt/automation-tool/other-profile"),
        ("publish_job_id", "123e4567-e89b-42d3-a456-426614174007"),
        ("headless", False),
    ):
        with pytest.raises(LocalSessionAuthenticationRejected):
            source.verify_publish_command(
                **{**baseline, field: tampered},
                presented_proof=proof,
            )


def test_login_proof_cannot_authorize_a_publish_command() -> None:
    source = authenticator()
    login_proof = source.proof_for_command(
        command_id=COMMAND_ID,
        command_type="douyin.login.open",
        executable_path=EXECUTABLE,
        profile_directory=PROFILE,
        headless=True,
    )
    with pytest.raises(LocalSessionAuthenticationRejected):
        source.verify_publish_command(
            command_id=COMMAND_ID,
            command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
            executable_path=EXECUTABLE,
            profile_directory=PROFILE,
            headless=True,
            publish_job_id=PUBLISH_JOB_ID,
            artifact_path=ARTIFACT,
            title=TITLE,
            description=DESCRIPTION,
            presented_proof=login_proof,
        )


def test_publish_proof_rejects_unknown_command_types() -> None:
    with pytest.raises(LocalSessionAuthenticationRejected):
        authenticator().proof_for_publish_command(
            command_id=COMMAND_ID,
            command_type="douyin.login.open",
            executable_path=EXECUTABLE,
            profile_directory=PROFILE,
            headless=True,
            publish_job_id=PUBLISH_JOB_ID,
            artifact_path=ARTIFACT,
            title=TITLE,
            description=DESCRIPTION,
        )


def command_line(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def test_authenticated_publish_frame_is_accepted() -> None:
    command = read_platform_command(io.BytesIO(command_line(publish_payload())), authenticator())
    assert command.command_type == DOUYIN_PUBLISH_PREFLIGHT_COMMAND
    assert command.artifact_path == ARTIFACT
    assert command.title == TITLE
    assert repr(command) == "PlatformCommand(<redacted>)"


@pytest.mark.parametrize(
    "overrides",
    [
        {"artifactPath": "clip.mp4"},
        {"artifactPath": "/opt/automation-tool/../clip.mp4"},
        {"artifactPath": "/opt/automation-tool/clip‮mp4"},
        {"title": ""},
        {"title": "标题\n"},
        {"description": "a" * 4097},
        {"publishJobId": "not-a-uuid"},
        {"publishJobId": "123e4567-e89b-11d3-a456-426614174006"},
    ],
)
def test_malformed_publish_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        PlatformCommand.model_validate(publish_payload(**overrides))


def test_publish_fields_are_required_for_publish_commands() -> None:
    payload = publish_payload()
    for field in ("artifactPath", "title", "description", "publishJobId"):
        incomplete = dict(payload)
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            PlatformCommand.model_validate(incomplete)


def test_login_and_logout_commands_reject_publish_fields() -> None:
    source = authenticator()
    login = {
        "authenticationProof": source.proof_for_command(
            command_id=COMMAND_ID,
            command_type="douyin.login.open",
            executable_path=EXECUTABLE,
            profile_directory=PROFILE,
            headless=True,
        ),
        "commandId": COMMAND_ID,
        "commandType": "douyin.login.open",
        "executablePath": EXECUTABLE,
        "headless": True,
        "profileDirectory": PROFILE,
        "protocolVersion": "1.0",
        "title": TITLE,
    }
    with pytest.raises(ValidationError):
        PlatformCommand.model_validate(login)
    logout = {
        "authenticationProof": source.proof_for_session_command(
            command_id=COMMAND_ID,
            command_type="douyin.logout.complete",
        ),
        "commandId": COMMAND_ID,
        "commandType": "douyin.logout.complete",
        "protocolVersion": "1.0",
        "artifactPath": ARTIFACT,
    }
    with pytest.raises(ValidationError):
        PlatformCommand.model_validate(logout)


def test_publish_result_frames_carry_the_publish_flow_version() -> None:
    output = io.StringIO()
    write_platform_command_result(
        output,
        authenticator(),
        command_id=COMMAND_ID,
        state="publish_pre_submit_ready",
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    )
    document = json.loads(output.getvalue())
    assert document["flowVersion"] == DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
    assert document["state"] == "publish_pre_submit_ready"
    assert document["platform"] == "douyin"


def publish_command() -> PlatformCommand:
    return PlatformCommand.model_validate(publish_payload())


def login_command() -> PlatformCommand:
    source = authenticator()
    return PlatformCommand.model_validate(
        {
            "authenticationProof": source.proof_for_command(
                command_id=COMMAND_ID,
                command_type="douyin.login.open",
                executable_path=EXECUTABLE,
                profile_directory=PROFILE,
                headless=True,
            ),
            "commandId": COMMAND_ID,
            "commandType": "douyin.login.open",
            "executablePath": EXECUTABLE,
            "headless": True,
            "profileDirectory": PROFILE,
            "protocolVersion": "1.0",
        }
    )


class LoginLikeOperation:
    """A double of an operation that never owns the browser between commands."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.handled: list[str] = []
        self.closed = False

    def handle(self, command: PlatformCommand) -> str:
        self.handled.append(command.command_type)
        return self.state

    def close(self) -> None:
        self.closed = True


class RecordingOperation(LoginLikeOperation):
    """A double of a publish operation, which must satisfy its whole contract.

    Releasing the surface and reporting pending approval terms are both on the
    protocol, so a double missing either one is not a publish operation and the
    router is right to refuse it.
    """

    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.released = 0
        self.approval: tuple[str, str] | None = None

    def release_surface(self) -> None:
        self.released += 1

    def pending_approval(self) -> tuple[str, str] | None:
        return self.approval


def test_router_dispatches_each_command_family_and_closes_both() -> None:
    login = RecordingOperation("awaiting_scan")
    publish = RecordingOperation("publish_pre_submit_ready")
    router = PlatformCommandRouter(login=login, publish=publish)
    assert router.handle(login_command()) == "awaiting_scan"
    assert router.handle(publish_command()) == "publish_pre_submit_ready"
    assert login.handled == ["douyin.login.open"]
    assert publish.handled == [DOUYIN_PUBLISH_PREFLIGHT_COMMAND]
    router.close()
    assert login.closed and publish.closed


def test_router_requires_real_operations() -> None:
    with pytest.raises(PlatformCommandRejected):
        PlatformCommandRouter(login=cast(Any, object()), publish=RecordingOperation("x"))
    with pytest.raises(PlatformCommandRejected):
        PlatformCommandRouter(login=RecordingOperation("x"), publish=cast(Any, object()))
    router = PlatformCommandRouter(login=RecordingOperation("x"), publish=RecordingOperation("y"))
    with pytest.raises(PlatformCommandRejected):
        router.handle(cast(Any, object()))


def publish_operation(tmp_path: Any) -> DouyinPublishPreflightCommandOperation:
    return DouyinPublishPreflightCommandOperation(
        ledger=publish_ledger(tmp_path),
        browser_authority=BrowserLaunchAuthority(),
    )


def test_publish_operation_rejects_other_command_families(tmp_path: Any) -> None:
    operation = publish_operation(tmp_path)
    try:
        with pytest.raises(PlatformCommandRejected):
            operation.handle(login_command())
        with pytest.raises(PlatformCommandRejected):
            operation.handle(cast(Any, object()))
    finally:
        operation.close()


def test_publish_operation_blocks_an_unusable_artifact_without_a_browser(tmp_path: Any) -> None:
    from automation_tool.executor.rpa.douyin.publish_preflight import (
        DouyinPublishPreflightEvidence,
    )

    operation = publish_operation(tmp_path)
    try:
        assert operation.handle(publish_command()) == "publish_blocked"
        receipt = operation.latest_receipt()
        assert receipt is not None, "a blocked command must still explain itself"
        assert receipt.evidence is DouyinPublishPreflightEvidence.ARTIFACT_REJECTED
    finally:
        operation.close()


def test_content_rejected_by_the_preflight_policy_is_reported_as_a_receipt(
    tmp_path: Any,
) -> None:
    """A title the command model accepts but the preflight caps must be explained."""
    from automation_tool.executor.rpa.douyin.publish_preflight import (
        DouyinPublishPreflightEvidence,
    )

    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(b"\x00\x00\x00\x18ftypmp42automation-tool")
    artifact.chmod(0o600)
    operation = publish_operation(tmp_path)
    try:
        command = publish_command_for(str(artifact), EXECUTABLE, PROFILE, title="标" * 400)
        assert operation.handle(command) == "publish_blocked"
        receipt = operation.latest_receipt()
        assert receipt is not None
        assert receipt.evidence is DouyinPublishPreflightEvidence.CONTENT_REJECTED
    finally:
        operation.close()


class FailingCloseOperation(LoginLikeOperation):
    def close(self) -> None:
        super().close()
        raise RuntimeError("private close failure")


def test_router_closes_both_operations_even_when_one_fails() -> None:
    login = FailingCloseOperation("awaiting_scan")
    publish = RecordingOperation("publish_blocked")
    router = PlatformCommandRouter(login=login, publish=publish)
    with pytest.raises(PlatformCommandRejected):
        router.close()
    assert login.closed and publish.closed
    assert repr(router) == "PlatformCommandRouter(<redacted>)"


def test_publish_operation_exposes_its_surface_lease_and_redacted_repr(tmp_path: Any) -> None:
    operation = publish_operation(tmp_path)
    try:
        assert operation.surface_lease().state() is LeaseState.OWNER_ACTIVE
        assert repr(operation) == "DouyinPublishPreflightCommandOperation(<redacted>)"
    finally:
        operation.close()


def test_publish_operation_rejects_unusable_construction(tmp_path: Any) -> None:
    opened = publish_ledger(tmp_path)
    with pytest.raises(PlatformCommandRejected):
        DouyinPublishPreflightCommandOperation(
            ledger=opened, runtime_factory=cast(Any, object())
        )
    with pytest.raises(PlatformCommandRejected):
        DouyinPublishPreflightCommandOperation(
            ledger=opened, browser_authority=cast(Any, object())
        )
    with pytest.raises(PlatformCommandRejected):
        DouyinPublishPreflightCommandOperation(
            ledger=opened, surface_lease=cast(Any, object())
        )
    with pytest.raises(PlatformCommandRejected):
        # A publish operation without a durable ledger could not enforce
        # at-most-once, so it must not be constructible at all.
        DouyinPublishPreflightCommandOperation(ledger=cast(Any, object()))
    with pytest.raises(PlatformCommandRejected):
        DouyinPublishPreflightCommandOperation(
            ledger=opened, publish_policy=cast(Any, object())
        )


def publish_proof(**overrides: Any) -> str:
    baseline: dict[str, Any] = dict(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        executable_path=EXECUTABLE,
        profile_directory=PROFILE,
        headless=True,
        publish_job_id=PUBLISH_JOB_ID,
        artifact_path=ARTIFACT,
        title=TITLE,
        description=DESCRIPTION,
    )
    baseline.update(overrides)
    return authenticator().proof_for_publish_command(**baseline)


def test_control_characters_can_never_shift_a_proof_field_boundary() -> None:
    """A NUL inside one field must not be able to impersonate the field separator."""
    for overrides in (
        {"artifact_path": f"{ARTIFACT}\x00{TITLE}", "title": "t"},
        {"title": f"{TITLE}\x00{DESCRIPTION}", "description": "d"},
        {"description": f"{DESCRIPTION}\x00"},
        {"executable_path": f"{EXECUTABLE}\x00"},
        {"profile_directory": f"{PROFILE}‮"},
    ):
        with pytest.raises(LocalSessionAuthenticationRejected):
            publish_proof(**overrides)


def test_login_proof_also_rejects_control_characters_in_local_paths() -> None:
    source = authenticator()
    for overrides in (
        {"executable_path": f"{EXECUTABLE}\x00"},
        {"profile_directory": f"{PROFILE}\x00"},
    ):
        baseline: dict[str, Any] = dict(
            command_id=COMMAND_ID,
            command_type="douyin.login.open",
            executable_path=EXECUTABLE,
            profile_directory=PROFILE,
            headless=True,
        )
        baseline.update(overrides)
        with pytest.raises(LocalSessionAuthenticationRejected):
            source.proof_for_command(**baseline)


def release_command() -> PlatformCommand:
    source = authenticator()
    return PlatformCommand.model_validate(
        {
            "authenticationProof": source.proof_for_session_command(
                command_id=COMMAND_ID,
                command_type=DOUYIN_PUBLISH_RELEASE_COMMAND,
            ),
            "commandId": COMMAND_ID,
            "commandType": DOUYIN_PUBLISH_RELEASE_COMMAND,
            "protocolVersion": "1.0",
        }
    )


def test_release_command_is_authenticated_and_path_free() -> None:
    command = read_platform_command(
        io.BytesIO(
            (
                json.dumps(
                    release_command().model_dump(by_alias=True, exclude_none=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        ),
        authenticator(),
    )
    assert command.command_type == DOUYIN_PUBLISH_RELEASE_COMMAND
    assert command.artifact_path is None
    assert command.executable_path is None


def test_release_command_gives_the_operations_browser_back(tmp_path: Any) -> None:
    operation = publish_operation(tmp_path)
    try:
        assert operation.handle(release_command()) == PUBLISH_RELEASED_STATE
        assert operation.latest_receipt() is None
    finally:
        operation.close()


def test_a_busy_browser_is_reported_apart_from_a_borrowed_page_surface(
    tmp_path: Any,
) -> None:
    """`browser_busy` and `surface_not_owned` need different user handling."""
    from automation_tool.executor.browser_runtime import BrowserLaunchRequest
    from automation_tool.executor.rpa.douyin.publish_preflight import (
        DouyinPublishPreflightEvidence,
    )

    authority = BrowserLaunchAuthority()
    operation = DouyinPublishPreflightCommandOperation(
        ledger=publish_ledger(tmp_path),
        browser_authority=authority,
    )
    executable = tmp_path / "chromium"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(b"\x00\x00\x00\x18ftypmp42automation-tool")
    artifact.chmod(0o600)
    command = publish_command_for(str(artifact), str(executable), str(profile))

    authority.authorize(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
            headless=True,
        )
    )
    held = authority.acquire()
    try:
        assert operation.handle(command) == "publish_blocked"
    finally:
        held.close()
        operation.close()
    receipt = operation.latest_receipt()
    assert receipt is not None
    assert receipt.evidence is DouyinPublishPreflightEvidence.BROWSER_BUSY


def publish_command_for(
    artifact_path: str,
    executable: str,
    profile: str,
    *,
    title: str = TITLE,
) -> PlatformCommand:
    source = authenticator()
    payload: dict[str, object] = {
        "artifactPath": artifact_path,
        "commandId": COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        "description": DESCRIPTION,
        "executablePath": executable,
        "headless": True,
        "profileDirectory": profile,
        "protocolVersion": "1.0",
        "publishJobId": PUBLISH_JOB_ID,
        "title": title,
    }
    payload["authenticationProof"] = source.proof_for_publish_command(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        executable_path=executable,
        profile_directory=profile,
        headless=True,
        publish_job_id=PUBLISH_JOB_ID,
        artifact_path=artifact_path,
        title=title,
        description=DESCRIPTION,
    )
    return PlatformCommand.model_validate(payload)


def test_a_busy_browser_keeps_the_existing_login_failure_semantics(tmp_path: Any) -> None:
    """A locked browser authority keeps the pre-PB-05 login behaviour.

    Reporting a dedicated `browser_busy` state was reverted: it survives Python
    and Rust but dies in the App (`lib.rs` rejects any logout state other than
    `logged_out`, and the platform-session Zod enum is strict), which turns a
    retryable `process_unavailable` into a non-retryable `protocol_mismatch`
    with no UI copy. Surfacing this condition properly needs the frontend state
    and copy added together in PB-07; until then the executor keeps the old,
    user-recoverable path. This test locks that decision in.
    """
    from automation_tool.executor.browser_runtime import BrowserLaunchRequest
    from automation_tool.executor.ledger import ExecutorLedger
    from automation_tool.executor.platform_commands import DouyinLoginCommandOperation
    from automation_tool.executor.rpa.douyin.health import DouyinSessionHealthReporter

    authority = BrowserLaunchAuthority()
    executable = tmp_path / "chromium"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    operation = DouyinLoginCommandOperation(
        health_reporter=DouyinSessionHealthReporter(
            ledger=ExecutorLedger(
                state_directory=tmp_path / "ledger",
                installation_id="123e4567-e89b-42d3-a456-426614174003",
                executor_id="123e4567-e89b-42d3-a456-426614174004",
            )
        ),
        outbound=Queue(),
        browser_authority=authority,
    )
    source = authenticator()
    command = PlatformCommand.model_validate(
        {
            "authenticationProof": source.proof_for_command(
                command_id=COMMAND_ID,
                command_type="douyin.login.open",
                executable_path=str(executable),
                profile_directory=str(profile),
                headless=True,
            ),
            "commandId": COMMAND_ID,
            "commandType": "douyin.login.open",
            "executablePath": str(executable),
            "headless": True,
            "profileDirectory": str(profile),
            "protocolVersion": "1.0",
        }
    )
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
            headless=True,
        )
    )
    held = authority.acquire()
    try:
        with pytest.raises(PlatformCommandRejected):
            operation.handle(command)
    finally:
        held.close()
        operation.close()


def test_router_reclaims_a_held_publish_surface_before_login_commands() -> None:
    publish = RecordingOperation("publish_pre_submit_ready")
    login = LoginLikeOperation("logged_out")
    router = PlatformCommandRouter(login=login, publish=publish)
    router.handle(publish_command())
    assert publish.released == 0
    router.handle(login_command())
    assert publish.released == 1


def test_router_rejects_a_publish_operation_that_cannot_release_the_surface() -> None:
    """M2 depends on this release; a publish operation without it must not construct."""
    with pytest.raises(PlatformCommandRejected):
        PlatformCommandRouter(
            login=LoginLikeOperation("awaiting_scan"),
            publish=cast(Any, LoginLikeOperation("publish_blocked")),
        )


def result_document(*, state: str, command_type: str) -> dict[str, Any]:
    output = io.StringIO()
    write_platform_command_result(
        output,
        authenticator(),
        command_id=COMMAND_ID,
        state=state,
        command_type=command_type,
    )
    return cast(dict[str, Any], json.loads(output.getvalue()))


def test_the_flow_contract_follows_the_command_family_not_the_state() -> None:
    """Each command family owns its flow contract; nothing is reverse-derived."""
    assert (
        result_document(state="awaiting_scan", command_type="douyin.login.open")["flowVersion"]
        == DOUYIN_QR_LOGIN_FLOW_VERSION
    )
    assert (
        result_document(state="healthy", command_type="douyin.login.recheck")["flowVersion"]
        == DOUYIN_QR_LOGIN_FLOW_VERSION
    )
    assert (
        result_document(state="logged_out", command_type="douyin.logout.complete")["flowVersion"]
        == "douyin.session-control.v1"
    )
    assert (
        result_document(state=PUBLISH_RELEASED_STATE, command_type=DOUYIN_PUBLISH_RELEASE_COMMAND)[
            "flowVersion"
        ]
        == DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
    )


@pytest.mark.parametrize(
    "command_type",
    ["douyin.NEW.family.added.in.PB-07", "", cast(Any, None), cast(Any, 12345)],
)
def test_an_unregistered_command_family_has_no_flow_contract(command_type: Any) -> None:
    """Failing open here would recreate the exact defect the family lookup fixed."""
    with pytest.raises(PlatformCommandRejected):
        result_document(state="awaiting_scan", command_type=command_type)


# --- PB-06: the confirmed single dispatch ----------------------------------


CONFIRMATION_ID = "123e4567-e89b-42d3-a456-426614174007"


def dispatch_payload(**overrides: object) -> dict[str, object]:
    source = authenticator()
    payload: dict[str, object] = {
        "commandId": COMMAND_ID,
        "commandType": DOUYIN_PUBLISH_DISPATCH_COMMAND,
        "confirmationId": CONFIRMATION_ID,
        "protocolVersion": "1.0",
        "publishJobId": PUBLISH_JOB_ID,
    }
    payload["authenticationProof"] = source.proof_for_publish_dispatch_command(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_DISPATCH_COMMAND,
        publish_job_id=PUBLISH_JOB_ID,
        confirmation_id=CONFIRMATION_ID,
    )
    payload.update(overrides)
    return payload


def dispatch_command(**overrides: object) -> PlatformCommand:
    return PlatformCommand.model_validate(dispatch_payload(**overrides))


def test_dispatch_proof_binds_the_job_and_the_confirmation() -> None:
    source = authenticator()
    baseline: dict[str, Any] = dict(
        command_id=COMMAND_ID,
        command_type=DOUYIN_PUBLISH_DISPATCH_COMMAND,
        publish_job_id=PUBLISH_JOB_ID,
        confirmation_id=CONFIRMATION_ID,
    )
    proof = source.proof_for_publish_dispatch_command(**baseline)
    source.verify_publish_dispatch_command(**baseline, presented_proof=proof)

    for field, tampered in (
        ("command_id", "123e4567-e89b-42d3-a456-426614174099"),
        ("publish_job_id", "123e4567-e89b-42d3-a456-426614174098"),
        ("confirmation_id", "123e4567-e89b-42d3-a456-426614174097"),
    ):
        with pytest.raises(LocalSessionAuthenticationRejected):
            source.verify_publish_dispatch_command(
                **{**baseline, field: tampered},
                presented_proof=proof,
            )


def test_a_dispatch_frame_is_authenticated_as_its_own_command_family() -> None:
    frame = json.dumps(dispatch_payload()).encode("utf-8") + b"\n"

    command = read_platform_command(
        cast(Any, io.BytesIO(frame)),
        authenticator(),
    )

    assert command.command_type == DOUYIN_PUBLISH_DISPATCH_COMMAND
    assert command.confirmation_id is not None


def test_a_dispatch_frame_signed_as_a_preflight_is_refused() -> None:
    source = authenticator()
    forged = dispatch_payload(
        authenticationProof=source.proof_for_session_command(
            command_id=COMMAND_ID,
            command_type=DOUYIN_PUBLISH_RELEASE_COMMAND,
        )
    )
    frame = json.dumps(forged).encode("utf-8") + b"\n"

    with pytest.raises(PlatformCommandRejected):
        read_platform_command(cast(Any, io.BytesIO(frame)), authenticator())


def test_a_dispatch_without_a_confirmation_is_not_a_valid_command() -> None:
    with pytest.raises(ValidationError):
        PlatformCommand.model_validate(
            {**dispatch_payload(), "confirmationId": None},
        )


def test_the_dispatch_command_family_declares_its_own_flow_contract() -> None:
    output = io.StringIO()

    write_platform_command_result(
        output,
        authenticator(),
        command_id=COMMAND_ID,
        state="publish_outcome_uncertain",
        command_type=DOUYIN_PUBLISH_DISPATCH_COMMAND,
    )

    assert json.loads(output.getvalue())["flowVersion"] == DOUYIN_PUBLISH_RELEASE_FLOW_VERSION


def test_a_dispatch_without_a_pre_submit_receipt_is_not_dispatched(tmp_path: Any) -> None:
    """Late is an outcome, not a protocol violation: the executor stays alive."""
    operation = publish_operation(tmp_path)
    try:
        assert operation.latest_approval() is None

        assert operation.handle(dispatch_command()) == "publish_not_dispatched"

        release = operation.latest_release()
        assert release is not None
        assert release.state is DouyinPublishReleaseState.NOT_DISPATCHED
        assert release.evidence is DouyinPublishReleaseEvidence.STALE_CONFIRMATION
        assert release.dispatch_state is None
    finally:
        operation.close()


def test_releasing_the_surface_voids_the_pending_approval_too(tmp_path: Any) -> None:
    """An approval describes a filled page; the page is about to be closed."""
    operation = publish_operation(tmp_path)
    try:
        operation.release_surface()
        assert operation.latest_approval() is None

        assert operation.handle(dispatch_command()) == "publish_not_dispatched"
    finally:
        operation.close()


@pytest.mark.parametrize("missing", ["publish_job_id", "confirmation_id"])
def test_a_dispatch_missing_its_own_identity_is_a_protocol_violation(
    tmp_path: Any,
    missing: str,
) -> None:
    """The model cannot produce this frame, so reaching it means something broke."""
    fields = dispatch_command().__dict__
    malformed = PlatformCommand.model_construct(**{**fields, missing: None})
    operation = publish_operation(tmp_path)
    try:
        with pytest.raises(PlatformCommandRejected):
            operation.handle(malformed)
        assert operation.latest_release() is None
    finally:
        operation.close()


def test_a_preflight_result_carries_the_approval_it_wants_confirmed() -> None:
    """The App cannot ask for a confirmation it was never told the terms of."""
    output = io.StringIO()

    write_platform_command_result(
        output,
        authenticator(),
        command_id=COMMAND_ID,
        state="publish_pre_submit_ready",
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        confirmation_id=CONFIRMATION_ID,
        target_account="自动化运营测试账号",
    )

    document = json.loads(output.getvalue())
    assert document["confirmationId"] == CONFIRMATION_ID
    assert document["targetAccount"] == "自动化运营测试账号"
    assert document["authenticationProof"] == authenticator().proof_for_command_result(
        command_id=COMMAND_ID,
        state="publish_pre_submit_ready",
        confirmation_id=CONFIRMATION_ID,
        target_account="自动化运营测试账号",
    )


def test_the_approval_terms_are_bound_into_the_result_proof() -> None:
    """An unbound account name could be swapped for another on the way to the App."""
    source = authenticator()
    baseline = dict(
        command_id=COMMAND_ID,
        state="publish_pre_submit_ready",
        confirmation_id=CONFIRMATION_ID,
        target_account="自动化运营测试账号",
    )
    proof = source.proof_for_command_result(**baseline)

    for field, tampered in (
        ("confirmation_id", "123e4567-e89b-42d3-a456-426614174099"),
        ("target_account", "别人的账号"),
        ("state", "publish_blocked"),
    ):
        assert (
            source.proof_for_command_result(**{**baseline, field: tampered}) != proof
        ), field


def test_a_result_without_approval_terms_keeps_its_existing_binding() -> None:
    """Login and logout frames must not change shape under a publish change."""
    output = io.StringIO()

    write_platform_command_result(
        output,
        authenticator(),
        command_id=COMMAND_ID,
        state="publish_blocked",
        command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
    )

    document = json.loads(output.getvalue())
    assert "confirmationId" not in document
    assert "targetAccount" not in document


def test_approval_terms_outside_the_frozen_shape_are_refused() -> None:
    for confirmation_id, target_account in [
        ("not-a-uuid", "账号"),
        (CONFIRMATION_ID, ""),
        (CONFIRMATION_ID, "账号‮"),
        (CONFIRMATION_ID, "x" * 200),
    ]:
        with pytest.raises(PlatformCommandRejected):
            write_platform_command_result(
                io.StringIO(),
                authenticator(),
                command_id=COMMAND_ID,
                state="publish_pre_submit_ready",
                command_type=DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
                confirmation_id=confirmation_id,
                target_account=target_account,
            )


def test_the_worker_carries_the_pending_approval_into_the_result_frame() -> None:
    """The App learns the terms from the same authenticated frame, or not at all."""
    publish = RecordingOperation("publish_pre_submit_ready")
    publish.approval = (CONFIRMATION_ID, "自动化运营测试账号")
    output = io.StringIO()
    worker = PlatformCommandWorker(
        input_stream=cast(Any, io.BytesIO(publish_frame())),
        authenticator=authenticator(),
        operation=PlatformCommandRouter(login=RecordingOperation("healthy"), publish=publish),
        result_output=output,
    )

    worker.run(threading.Event())

    document = json.loads(output.getvalue())
    assert document["state"] == "publish_pre_submit_ready"
    assert document["confirmationId"] == CONFIRMATION_ID
    assert document["targetAccount"] == "自动化运营测试账号"
    assert document["authenticationProof"] == authenticator().proof_for_command_result(
        command_id=COMMAND_ID,
        state="publish_pre_submit_ready",
        confirmation_id=CONFIRMATION_ID,
        target_account="自动化运营测试账号",
    )


def test_a_result_frame_without_pending_terms_carries_none() -> None:
    """A blocked preflight has nothing to approve, so it says nothing."""
    publish = RecordingOperation("publish_blocked")
    output = io.StringIO()
    worker = PlatformCommandWorker(
        input_stream=cast(Any, io.BytesIO(publish_frame())),
        authenticator=authenticator(),
        operation=PlatformCommandRouter(login=RecordingOperation("healthy"), publish=publish),
        result_output=output,
    )

    worker.run(threading.Event())

    document = json.loads(output.getvalue())
    assert "confirmationId" not in document
    assert "targetAccount" not in document


def publish_frame() -> bytes:
    return json.dumps(publish_payload()).encode("utf-8") + b"\n"

