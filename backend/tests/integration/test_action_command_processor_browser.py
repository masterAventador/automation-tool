from __future__ import annotations

import json
import os
import queue
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

from automation_tool.executor.action_authorization import (
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.executor.action_gate import ExecutorActionGate, LocalActionHardPolicy
from automation_tool.executor.action_operation import ProductionDouyinActionOperation
from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.bootstrap import ExecutorBootstrap
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.command_processor import ExecutorCommandProcessor
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.runtime import (
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ExecutorLifecycleEnvelope,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
    parse_executor_message,
)

MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 7, 21, 11, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174004")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174006")
TARGET_ID = ProtocolTargetId("223e4567-e89b-42d3-a456-426614174002")
LOCAL_SESSION_TOKEN = "05" * 32


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class DeterministicIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> UUID:
        self._value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self._value:012d}")


class RoutingRuntime:
    def __init__(self, action: DouyinSearchExposureAction) -> None:
        self._action = action
        self._runtime = BrowserRuntime()
        self.observed_counts: tuple[int, ...] = ()

    def start(self, request: BrowserLaunchRequest) -> None:
        self._runtime.start(request)
        page = cast(Any, self._runtime.primary_window().playwright_page)
        browse = (FIXTURES / "douyin_browse_pages/profile-ready.html").read_text(encoding="utf-8")
        comment_profile = (
            FIXTURES / "douyin_action_command_pages/comment-target-profile.html"
        ).read_text(encoding="utf-8")
        comment = (FIXTURES / "douyin_comment_pages/comment-action.html").read_text(
            encoding="utf-8"
        )
        direct = (FIXTURES / "douyin_direct_message_pages/message-action.html").read_text(
            encoding="utf-8"
        )
        profile_document = {
            DouyinSearchExposureAction.BROWSE: browse,
            DouyinSearchExposureAction.COMMENT: comment_profile,
            DouyinSearchExposureAction.DIRECT_MESSAGE: direct,
        }[self._action]
        page.route(
            "https://www.douyin.com/user/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=profile_document,
            ),
        )
        page.route(
            "https://www.douyin.com/video/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body=comment),
        )

    def primary_window(self) -> BrowserWindow:
        return self._runtime.primary_window()

    def close(self) -> None:
        page = cast(Any, self._runtime.primary_window().playwright_page)
        if self._action is DouyinSearchExposureAction.BROWSE:
            self.observed_counts = (page.evaluate("window.__browseSideEffects"),)
        elif self._action is DouyinSearchExposureAction.COMMENT:
            self.observed_counts = (page.evaluate("window.__commentDispatches"),)
        else:
            self.observed_counts = (
                page.evaluate("window.__conversationEntries"),
                page.evaluate("window.__messageDispatches"),
            )
        self._runtime.close()


def offer() -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "323e4567-e89b-42d3-a456-426614174001",
            "message_type": "task.offer",
            "sent_at": NOW.isoformat(),
            "deadline_at": (NOW + timedelta(minutes=5)).isoformat(),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"task:offer:{ATTEMPT_ID}",
            "sequence": 1,
            "payload": {},
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        },
        separators=(",", ":"),
    )


def action_command(action: DouyinSearchExposureAction, action_id: ProtocolActionId) -> str:
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=TARGET_ID,
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW - timedelta(seconds=1),
        deadline_at=NOW + timedelta(minutes=4),
    )
    authority = encode_action_authorization_token(
        claims,
        Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).sign(
            action_authorization_signing_input(claims)
        ),
    )
    template = (
        None if action is DouyinSearchExposureAction.BROWSE else "您好 {{target_display_name}}"
    )
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": "423e4567-e89b-42d3-a456-426614174001",
            "message_type": "action.execute",
            "sent_at": NOW.isoformat(),
            "deadline_at": (NOW + timedelta(minutes=4)).isoformat(),
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "correlation_id": "423e4567-e89b-42d3-a456-426614174002",
            "idempotency_key": f"action:{action_id}",
            "sequence": 2,
            "payload": {
                "action_version": "douyin.action-command.v1",
                "action_id": str(action_id),
                "target_id": str(TARGET_ID),
                "action": action.value,
                "signed_authority": authority,
                "platform_target_id": "creator-001",
                "display_name": "隔离目标",
                "public_handle": "target-one",
                "source": "general_search_author",
                "page_revision": 1,
                "message_template_version": (
                    None if template is None else "action-message-template.v1"
                ),
                "message_template": template,
            },
            "task_id": str(TASK_ID),
            "execution_attempt_id": str(ATTEMPT_ID),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def run_server(
    handler: Any,
) -> tuple[Server, threading.Thread, int]:
    server = serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=[Subprotocol(EXECUTOR_WEBSOCKET_SUBPROTOCOL)],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.socket.getsockname()[1])


@pytest.mark.parametrize(
    ("action", "action_id", "expected_evidence", "expected_counts"),
    (
        (
            DouyinSearchExposureAction.BROWSE,
            ProtocolActionId("223e4567-e89b-42d3-a456-426614174011"),
            "profile_visible",
            (0,),
        ),
        (
            DouyinSearchExposureAction.COMMENT,
            ProtocolActionId("223e4567-e89b-42d3-a456-426614174012"),
            "comment_confirmed",
            (1,),
        ),
        (
            DouyinSearchExposureAction.DIRECT_MESSAGE,
            ProtocolActionId("223e4567-e89b-42d3-a456-426614174013"),
            "message_confirmed",
            (1, 1),
        ),
    ),
)
def test_formal_processor_executes_each_production_action_through_a_headless_browser(
    tmp_path: Path,
    action: DouyinSearchExposureAction,
    action_id: ProtocolActionId,
    expected_evidence: str,
    expected_counts: tuple[int, ...],
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("H8-16D system Chrome fake-page acceptance currently requires macOS Chrome")
    profile = tmp_path / action.value / "profile"
    profile.mkdir(mode=0o700, parents=True)
    state = tmp_path / action.value / "state"
    ledger = ExecutorLedger(
        state_directory=state,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    clock = Clock()
    gate = ExecutorActionGate(
        ledger=ledger,
        verifier=Ed25519ActionAuthorizationVerifier(
            public_key=(
                Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes_raw()
            ),
            clock=clock,
        ),
        policy=LocalActionHardPolicy(
            minimum_interval=timedelta(seconds=1),
            task_action_limit=100,
        ),
        clock=clock,
    )
    authority = BrowserLaunchAuthority()
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
            headless=True,
        )
    )
    runtime = RoutingRuntime(action)
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=clock,
        id_source=DeterministicIds(),
        action_operation=ProductionDouyinActionOperation(
            ledger=ledger,
            action_gate=gate,
            browser_authority=authority,
            clock=clock,
            runtime_factory=lambda: runtime,
        ),
    )

    assert [message.message_type for message in processor.handle(offer())] == [
        "task.accept",
        "task.started",
    ]
    batch = processor.handle(action_command(action, action_id))

    assert [message.message_type for message in batch] == [
        "action.accept",
        "step.started",
        "step.completed",
    ]
    result_payload = cast(dict[str, Any], batch[-1].payload)
    assert result_payload["evidence"] == expected_evidence
    assert runtime.observed_counts == expected_counts
    effect = ledger.get_side_effect(str(action_id))
    if action is DouyinSearchExposureAction.BROWSE:
        assert effect is None
    else:
        assert effect is not None and effect.state is SideEffectState.VERIFIED
    assert os.stat(profile).st_mode & 0o777 == 0o700


def test_local_executor_websocket_drives_production_comment_through_a_headless_browser(
    tmp_path: Path,
) -> None:
    if sys.platform != "darwin" or not MACOS_CHROME.is_file():
        pytest.skip("H8-16D system Chrome fake-page acceptance currently requires macOS Chrome")
    action = DouyinSearchExposureAction.COMMENT
    action_id = ProtocolActionId("223e4567-e89b-42d3-a456-426614174014")
    profile = tmp_path / "websocket" / "profile"
    profile.mkdir(mode=0o700, parents=True)
    state = tmp_path / "websocket" / "state"
    ledger = ExecutorLedger(
        state_directory=state,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    clock = Clock()
    gate = ExecutorActionGate(
        ledger=ledger,
        verifier=Ed25519ActionAuthorizationVerifier(
            public_key=(
                Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes_raw()
            ),
            clock=clock,
        ),
        policy=LocalActionHardPolicy(
            minimum_interval=timedelta(seconds=1),
            task_action_limit=100,
        ),
        clock=clock,
    )
    authority = BrowserLaunchAuthority()
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=MACOS_CHROME,
            profile_directory=profile,
            headless=True,
        )
    )
    runtime = RoutingRuntime(action)
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
        clock=clock,
        id_source=DeterministicIds(),
        action_operation=ProductionDouyinActionOperation(
            ledger=ledger,
            action_gate=gate,
            browser_authority=authority,
            clock=clock,
            runtime_factory=lambda: runtime,
        ),
    )
    stop = threading.Event()
    captured: queue.Queue[object] = queue.Queue()

    def handler(connection: ServerConnection) -> None:
        try:
            assert connection.subprotocol == EXECUTOR_WEBSOCKET_SUBPROTOCOL
            assert connection.request is not None
            assert connection.request.headers["authorization"] == "Bearer private-session"
            hello = parse_executor_message(connection.recv(timeout=5))
            assert isinstance(hello, ExecutorLifecycleEnvelope)
            assert hello.message_type == "executor.hello"
            connection.send(offer())
            offer_batch = tuple(
                parse_executor_message(connection.recv(timeout=5)) for _ in range(2)
            )
            connection.send(action_command(action, action_id))
            action_batch = tuple(
                parse_executor_message(connection.recv(timeout=5)) for _ in range(3)
            )
            captured.put((offer_batch, action_batch))
        except Exception as error:
            captured.put(error)
        finally:
            stop.set()

    server, server_thread, port = run_server(handler)
    output = StringIO()
    bootstrap = ExecutorBootstrap.model_validate(
        {
            "bootstrap_version": "1",
            "websocket_url": f"ws://127.0.0.1:{port}/api/v1/executors/connect",
            "local_session_token": LOCAL_SESSION_TOKEN,
            "session_token": "private-session",
            "installation_id": str(INSTALLATION_ID),
            "executor_id": str(EXECUTOR_ID),
            "heartbeat_interval_seconds": 60,
            "state_directory": str(state),
        }
    )
    try:
        LocalExecutorProcess(
            bootstrap=bootstrap,
            metadata=RuntimeMetadata(
                executor_version="0.1.0",
                platform="macos",
                architecture="arm64",
            ),
            reporter=ExecutorProcessReporter(
                output,
                LocalSessionAuthenticator(SecretStr(LOCAL_SESSION_TOKEN)),
            ),
            command_processor=processor,
            clock=clock,
            id_source=DeterministicIds(),
            open_timeout=timedelta(seconds=2),
            close_timeout=timedelta(seconds=1),
        ).run(stop)
        received = captured.get(timeout=5)
        if isinstance(received, Exception):
            raise received
        offer_batch, action_batch = cast(tuple[tuple[Any, ...], tuple[Any, ...]], received)
        assert [message.message_type for message in offer_batch] == [
            "task.accept",
            "task.started",
        ]
        assert [message.message_type for message in action_batch] == [
            "action.accept",
            "step.started",
            "step.completed",
        ]
        result_payload = cast(dict[str, Any], action_batch[-1].payload)
        assert result_payload["evidence"] == "comment_confirmed"
        assert runtime.observed_counts == (1,)
        effect = ledger.get_side_effect(str(action_id))
        assert effect is not None and effect.state is SideEffectState.VERIFIED
        assert "private-session" not in output.getvalue()
    finally:
        stop.set()
        server.shutdown()
        server_thread.join(timeout=5)
    assert not server_thread.is_alive()
