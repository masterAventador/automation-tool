"""Versioned public protocol shared by Control Plane and Local Executor.

Public names remain available from this package, but their owning modules are
loaded only when a caller asks for one.  Leaf protocol modules can therefore be
embedded in the Python 3.11 video Worker without parsing the unrelated 3.12-only
Executor envelope.
"""

from importlib import import_module
from typing import Any

from automation_tool.protocol.douyin_candidate import (
    DOUYIN_CANDIDATE_VERSION,
    MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS,
    MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS,
    MAX_DOUYIN_TARGET_ID_CHARACTERS,
    DouyinCandidate,
    DouyinCandidateKey,
    DouyinCandidateRejected,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

_PUBLIC_MODULES = (
    "action_authorization",
    "action_message_template",
    "action_result",
    "douyin_search",
    "executor_envelope",
    "version",
)


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    for module_name in _PUBLIC_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        if name in vars(module):
            value = vars(module)[name]
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ACTION_AUTHORIZATION_CLOCK_SKEW",
    "ACTION_AUTHORIZATION_MAX_LIFETIME",
    "ACTION_AUTHORIZATION_TOKEN_PREFIX",
    "ACTION_AUTHORIZATION_VERSION",
    "ACTION_MESSAGE_TEMPLATE_VERSION",
    "ACTION_RESULT_EVIDENCE_VERSION",
    "API_VERSION",
    "CURRENT_DESKTOP_APP_VERSION",
    "CURRENT_EXECUTOR_PROTOCOL",
    "CURRENT_EXECUTOR_RUNTIME_VERSION",
    "DOUYIN_ACTION_COMMAND_VERSION",
    "DOUYIN_CANDIDATE_VERSION",
    "DOUYIN_DISCOVERY_PROTOCOL_VERSION",
    "DOUYIN_SEARCH_INPUT_VERSION",
    "EXECUTOR_ACTION_RESULT_EVIDENCE",
    "EXECUTOR_PROTOCOL_VERSION",
    "EXECUTOR_WEBSOCKET_SUBPROTOCOL",
    "FAILED_ACTION_RESULT_EVIDENCE",
    "MAXIMUM_COMPATIBLE_DESKTOP_APP_VERSION",
    "MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
    "MAXIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION",
    "MAX_ACTION_AUTHORIZATION_TOKEN_BYTES",
    "MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS",
    "MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS",
    "MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS",
    "MAX_DISCOVERY_BATCH_CANDIDATES",
    "MAX_DISCOVERY_BATCH_COUNT",
    "MAX_DOUYIN_TARGET_ID_CHARACTERS",
    "MAX_EXECUTOR_MESSAGE_BYTES",
    "MAX_EXECUTOR_SEQUENCE",
    "MAX_SEARCH_KEYWORD_CHARACTERS",
    "MAX_TASK_TARGET_LIMIT",
    "MINIMUM_COMPATIBLE_DESKTOP_APP_VERSION",
    "MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
    "MINIMUM_COMPATIBLE_EXECUTOR_RUNTIME_VERSION",
    "PERSISTED_ACTION_RESULT_EVIDENCE",
    "SKIPPED_ACTION_RESULT_EVIDENCE",
    "SUCCESS_ACTION_RESULT_EVIDENCE",
    "UNCERTAIN_ACTION_RESULT_EVIDENCE",
    "ActionAuthorizationClaims",
    "ActionAuthorizationRejected",
    "ActionMessageTemplate",
    "ActionMessageTemplateRejected",
    "ActionMessageVariable",
    "ActionResultEvidence",
    "CorrelationId",
    "DouyinActionCommandPayload",
    "DouyinCandidate",
    "DouyinCandidateKey",
    "DouyinCandidateRejected",
    "DouyinCandidateSource",
    "DouyinCandidateSummary",
    "DouyinDiscoveryBatchPayload",
    "DouyinDiscoveryCandidatePayload",
    "DouyinDiscoveryCommandPayload",
    "DouyinDiscoveryCompletedPayload",
    "DouyinSearchExposureAction",
    "DouyinSearchInput",
    "DouyinSearchInputRejected",
    "ExecutorEnvelope",
    "ExecutorLifecycleEnvelope",
    "ExecutorMessage",
    "ExecutorProtocolError",
    "IdempotencyKey",
    "MessageId",
    "ParsedActionAuthorizationToken",
    "PlatformSessionHealthEnvelope",
    "PlatformSessionHealthPayload",
    "PlatformSessionState",
    "ProtocolActionId",
    "ProtocolExecutionAttemptId",
    "ProtocolExecutorId",
    "ProtocolInstallationId",
    "ProtocolTargetId",
    "ProtocolTaskId",
    "TaskActionCommandEnvelope",
    "TaskCommandEnvelope",
    "TaskCommandResultEnvelope",
    "TaskDiscoveryBatchEnvelope",
    "TaskDiscoveryCommandEnvelope",
    "TaskDiscoveryCompletedEnvelope",
    "TaskEventEnvelope",
    "action_authorization_idempotency_key",
    "action_authorization_signing_input",
    "encode_action_authorization_token",
    "parse_action_authorization_token",
    "parse_executor_message",
]
