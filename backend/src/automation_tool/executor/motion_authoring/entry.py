"""One request in on stdin, one answer out on stdout, then exit.

The App owns the render workspace and the model credential; this process owns
neither beyond the run it was started for. It is deliberately not a server:
there is no port to reach, nothing survives the call, and the credential exists
only in the bytes that arrived on stdin — never in `argv`, never in the
environment, never in the answer.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, Final, TextIO

from automation_tool.executor.motion_authoring.agent import (
    AUTHORING_VENDOR_ROOT,
    AUTHORING_WORKFLOW_CONTRACT,
    AuthoringWorkspace,
    MotionAuthoringAgent,
    MotionAuthoringPersistenceError,
    MotionAuthoringRejected,
    MotionAuthoringTools,
    MotionAuthoringUnavailable,
    MotionBrief,
    VideoCreationModelConfig,
    call_video_creation_model,
    load_locked_authoring_workflow,
)

SCHEMA_VERSION: Final = 1
MAX_REQUEST_BYTES: Final = 64 * 1024
_REQUEST_FIELDS: Final = frozenset(
    {
        "schemaVersion",
        "workspace",
        "brief",
        "aspectRatio",
        "durationSeconds",
        "language",
        "brandAssets",
        "model",
    }
)
# Optional rather than required: an installation with no parts catalog is a real
# state, not a malformed request, and the dangerous half of it — a storyboard
# that names a part while none is available — is refused by the agent with a
# reason that says so. Making it required would have meant every existing caller
# becoming shape-invalid to express a condition the agent already reports.
_OPTIONAL_REQUEST_FIELDS: Final = frozenset(
    {
        "catalogRoot",
    }
)
_MODEL_FIELDS: Final = frozenset({"baseUrl", "modelId", "apiKey"})
_REFUSAL_FIELDS: Final = frozenset({"schemaVersion", "status", "rejectionReason"})
_AGENT_REASON_PREFIX: Final = "motion authoring rejected: "
_BRIEF_REASON_PREFIX: Final = "brief is outside the declared bounds: "
_STATIC_GATE_MESSAGE_PREFIX: Final = "composition failed static gates: "
_REFUSAL_CONTRACT_PATH: Final = AUTHORING_WORKFLOW_CONTRACT.with_name(
    "motion-authoring-refusal.v1.json"
)
_WIRE_TOKEN: Final = re.compile(r"^[a-z0-9_]+$")


def _load_refusal_contract() -> tuple[
    frozenset[str], str, frozenset[str], dict[str, frozenset[str]]
]:
    try:
        document = json.loads(_REFUSAL_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("motion authoring refusal contract is unreadable") from error
    expected = {
        "schemaVersion",
        "id",
        "version",
        "policy",
        "fixedReasons",
        "staticGateReasonPrefix",
        "staticGateCodes",
        "nonRefusalOutcomes",
        "rationale",
    }
    fixed = document.get("fixedReasons")
    prefix = document.get("staticGateReasonPrefix")
    gate_codes = document.get("staticGateCodes")
    outcomes = document.get("nonRefusalOutcomes")
    if (
        not isinstance(document, dict)
        or set(document) != expected
        or document.get("schemaVersion") != 1
        or document.get("id") != "motion-authoring-refusal"
        or document.get("version") != "motion-authoring-refusal.v1"
        or document.get("policy") != "fail_closed"
        or not isinstance(fixed, list)
        or not fixed
        or not all(type(value) is str and _WIRE_TOKEN.fullmatch(value) for value in fixed)
        or fixed != sorted(set(fixed))
        or type(prefix) is not str
        or not prefix.endswith(":")
        or not _WIRE_TOKEN.fullmatch(prefix[:-1])
        or not isinstance(gate_codes, list)
        or not gate_codes
        or not all(type(value) is str and _WIRE_TOKEN.fullmatch(value) for value in gate_codes)
        or gate_codes != sorted(set(gate_codes))
        or not _outcomes_are_declared(outcomes, frozenset(fixed))
    ):
        raise RuntimeError("motion authoring refusal contract drifted")
    return (
        frozenset(fixed),
        prefix,
        frozenset(gate_codes),
        {name: frozenset(reasons) for name, reasons in outcomes.items()},
    )


def _outcomes_are_declared(outcomes: object, fixed: frozenset[str]) -> bool:
    """Is the non-refusal table a partition of known tokens into named classes?

    Overlapping classes are the one shape worth spelling out: a token in two
    classes would answer with whichever the iteration happened to reach first,
    and both sides of the wire would still look consistent while the card said
    two different things on different days.
    """
    if not isinstance(outcomes, dict) or not outcomes:
        return False
    names = list(outcomes)
    if names != sorted(set(names)) or not all(
        type(name) is str and _WIRE_TOKEN.fullmatch(name) and name != _REFUSED_STATUS
        for name in names
    ):
        return False
    seen: set[str] = set()
    for reasons in outcomes.values():
        if (
            not isinstance(reasons, list)
            or not reasons
            or reasons != sorted(set(reasons))
            or not frozenset(reasons) <= fixed
            or seen & set(reasons)
        ):
            return False
        seen |= set(reasons)
    return True


_REFUSED_STATUS: Final = "rejected"

(
    _FIXED_WIRE_REASONS,
    _STATIC_GATE_REASON_PREFIX,
    _STATIC_GATE_CODES,
    _NON_REFUSAL_OUTCOMES,
) = _load_refusal_contract()

# Fixed findings that reach this boundary as a bare message, with no agent
# prefix in front of them: the ones this file raises itself, and the ones
# `call_video_creation_model` raises about the model service.
_ENTRY_REASON_TOKENS: Final = {
    "model configuration is not the declared shape": "model_configuration_shape_invalid",
    "model configuration is not usable": "model_configuration_unusable",
    "video creation model timed out": "video_creation_model_timed_out",
    "video creation model transport failed": "video_creation_model_transport_failed",
    "workspace is missing": "workspace_missing",
    "workspace must be an absolute path the App already created": "workspace_not_absolute",
    "workspace is not a usable render workspace": "workspace_unusable",
    "catalog root is missing": "catalog_root_missing",
    "catalog root must be an absolute path the App resolved": "catalog_root_not_absolute",
    "brand assets are not the declared shape": "brand_assets_shape_invalid",
    "duration must be a whole number of seconds": "duration_not_whole_seconds",
    "request is not the declared shape": "request_shape_invalid",
    "unsupported request schema version": "request_schema_unsupported",
    "video creation model is unavailable": "video_creation_model_unavailable",
    "request is too large": "request_too_large",
}

_BRIEF_REASON_TOKENS: Final = {
    "brief text is out of range": "brief_text_out_of_range",
    "unsupported aspect ratio": "brief_aspect_ratio_unsupported",
    "duration is out of range": "brief_duration_out_of_range",
    "unsupported language": "brief_language_unsupported",
    "too many brand assets": "brief_too_many_brand_assets",
    "path must be a non-empty string": "brief_brand_asset_path_empty",
    "path must be a clean relative posix path": "brief_brand_asset_path_not_clean_relative",
    "path must not contain empty, current or parent segments": (
        "brief_brand_asset_path_invalid_segments"
    ),
    "path must not name an alternate data stream": "brief_brand_asset_path_alternate_data_stream",
    "path segment must not end with a dot or a space": (
        "brief_brand_asset_path_trailing_dot_or_space"
    ),
    "path must not name a reserved device": "brief_brand_asset_path_reserved_device",
}

# Every fixed message the current authoring agent can raise. This is not the
# wire vocabulary: it is the allowlist that permits one internal finding to be
# translated into a dedicated token. A new message is generic until this list,
# the shared token contract and the source-coverage test are all updated.
_AGENT_FIXED_REJECTION_BODIES: Final = frozenset(
    {
        "api key is malformed",
        "base url must be https",
        "beat layout is not published",
        "beat timing is out of range",
        "beat timing must be numeric",
        "beat_id is malformed",
        "beats count is out of range",
        "body is out of range",
        "brief must be a MotionBrief",
        "brief text is out of range",
        "catalog purposes missing",
        "the storyboard names catalog parts but this installation carries no parts catalog",
        "catalog_parts must be selectable catalog ids",
        "composition html must be a non-empty string",
        "composition not seekable",
        "motion part slot table is unreadable",
        "config must be a VideoCreationModelConfig",
        "config shape invalid",
        "declared asset must exist",
        "design has an unexpected key set",
        "design must be an object",
        "digest must be lowercase hex",
        "duplicate beat id",
        "duration exceeds the snapshot frame budget for this fps",
        "duration is out of range",
        "each beat must be a bounded string",
        "entry html must exist in workspace",
        "expected a regular file inside the workspace",
        "file entry invalid",
        "first response must carry the three closed fields",
        "fps out of range",
        "frame count out of range",
        "headline is out of range",
        "items are out of range",
        "locked motion catalog drifted",
        "locked motion catalog is unreadable",
        "motion part usability contract drifted",
        "motion part usability contract is unreadable",
        "no motion part is selectable",
        "model call contract drifted",
        "model call contract is unreadable",
        "model catalog or secret is unreadable",
        "model id required",
        "model output must be a JSON object",
        "workspace bytes must be a bytes payload",
        "model output was not JSON",
        "model reply must be a string",
        "model returned empty content",
        "model stream exceeded the size budget",
        "model timeout out of range",
        "not a MotionAuthoringTools instance",
        "one-sentence brief contract drifted",
        "one-sentence brief contract is unreadable",
        "one_message is out of range",
        "path collides with an existing entry that differs only by case",
        "path escapes the workspace",
        "path must be a clean relative posix path",
        "path must be a non-empty string",
        "path must not contain empty, current or parent segments",
        "path must not name a reserved device",
        "path must not name an alternate data stream",
        "path segment must not end with a dot or a space",
        "pinned workflow file digest drifted",
        "pinned workflow file is missing or a symlink",
        "primary_color must be a #rrggbb color",
        "purpose is out of range",
        "reference budget out of range",
        "refusing to write through a symlink",
        "render canvas contract drifted",
        "render canvas contract is unreadable",
        "script has an unexpected key set",
        "script must be an object",
        "secondary_color must be a #rrggbb color",
        "storyboard beat has an unexpected key set",
        "storyboard beat must be an object",
        "storyboard beats count is out of range",
        "storyboard beats must tile the film",
        "storyboard duration contract drifted",
        "storyboard duration contract is unreadable",
        "storyboard has an unexpected key set",
        "storyboard must be an object",
        "too many brand assets",
        "tool surface does not match the closed allowlist",
        "tools require an AuthoringWorkspace",
        "typography note is out of range",
        "unknown style preset",
        "unsupported aspect ratio",
        "unsupported language",
        "video model id missing",
        "video_creative purpose missing",
        "workflow contract is malformed",
        "workflow contract is unreadable",
        "workflow reference exceeded its budget",
        "workflow reference required",
        "workspace required",
        "workspace root must be a real, non-symlink directory",
        "workspace root must be an absolute path",
    }
)


def _agent_reason_token(body: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", body.lower()).strip("_")
    return f"agent_{normalized}"


def _closed_wire_reason(value: object) -> str | None:
    if type(value) is not str:
        return None
    if value in _FIXED_WIRE_REASONS:
        return value
    suffix = value.removeprefix(_STATIC_GATE_REASON_PREFIX)
    if suffix == value or not suffix:
        return None
    codes = suffix.split("+")
    if codes != sorted(set(codes)) or not all(code in _STATIC_GATE_CODES for code in codes):
        return None
    return value


def _closed_static_gate_reason(raw: str) -> str | None:
    if not raw.startswith(_STATIC_GATE_MESSAGE_PREFIX):
        return None
    encoded = raw.removeprefix(_STATIC_GATE_MESSAGE_PREFIX)
    if len(encoded) > 1024:
        return None
    try:
        codes = ast.literal_eval(encoded)
    except (SyntaxError, ValueError):
        return None
    if (
        not isinstance(codes, list)
        or not codes
        or not all(type(code) is str for code in codes)
        or codes != sorted(set(codes))
        or not all(code in _STATIC_GATE_CODES for code in codes)
        or repr(codes) != encoded
    ):
        return None
    return _closed_wire_reason(_STATIC_GATE_REASON_PREFIX + "+".join(codes))


def _closed_rejection_reason(raw: object) -> str | None:
    """Translate only the current fixed findings into the dedicated wire."""
    if type(raw) is not str:
        return None
    direct = _ENTRY_REASON_TOKENS.get(raw)
    if direct is not None:
        return _closed_wire_reason(direct)
    if raw.startswith(_BRIEF_REASON_PREFIX + _AGENT_REASON_PREFIX):
        body = raw.removeprefix(_BRIEF_REASON_PREFIX + _AGENT_REASON_PREFIX)
        return _closed_wire_reason(_BRIEF_REASON_TOKENS.get(body))
    if not raw.startswith(_AGENT_REASON_PREFIX):
        return None
    body = raw.removeprefix(_AGENT_REASON_PREFIX)
    static_gate = _closed_static_gate_reason(body)
    if static_gate is not None:
        return static_gate
    if body not in _AGENT_FIXED_REJECTION_BODIES:
        return None
    return _closed_wire_reason(_agent_reason_token(body))


def _answer_status(rejection_reason: str | None) -> str:
    """Name the outcome, so a refusal stays the one thing a refusal means.

    The refusal document is not a generic failure envelope: the App recognises
    it and reports the run as `authoring_refused`, which is worded — correctly —
    as the agent having read this brief and declined it, and tells the user to
    describe the film differently. Nothing read the brief when the model was
    never reached, when the pinned files no longer verify, or when the request
    was malformed before authoring began, so answering any of those as a refusal
    sends the user to rewrite a sentence that was never the problem. Measured on
    2026-07-26: an unreachable model produced exactly that sentence after two
    seconds, a model that stopped answering produced it word for word after 363,
    and a tree whose `vendor` was never checked out produced it after two.

    The status the App reads is the class name from the shared contract. A
    status the refusal parser does not accept keeps those runs out of that
    sentence even on an App too old to know the class, while the document still
    carries its own closed reason for one that does.
    """
    for name, reasons in _NON_REFUSAL_OUTCOMES.items():
        if rejection_reason in reasons:
            return name
    return _REFUSED_STATUS


class MotionAuthoringEntryRejected(ValueError):
    """Fixed failure boundary for a request this process will not act on."""

    def __init__(self, rejection_reason: str | None) -> None:
        self.rejection_reason = rejection_reason
        super().__init__("motion authoring entry rejected")


def _reject(reason: str) -> MotionAuthoringEntryRejected:
    return MotionAuthoringEntryRejected(_closed_rejection_reason(reason))


def parse_motion_authoring_refusal(document: object) -> str | None:
    """Return the closed refusal token, or None for any wider document."""
    if (
        not isinstance(document, dict)
        or set(document) != _REFUSAL_FIELDS
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["status"] != _REFUSED_STATUS
    ):
        return None
    return _closed_wire_reason(document["rejectionReason"])


def _model(payload: object) -> VideoCreationModelConfig:
    if not isinstance(payload, dict) or set(payload) != _MODEL_FIELDS:
        raise _reject("model configuration is not the declared shape")
    try:
        return VideoCreationModelConfig(
            base_url=payload["baseUrl"],
            model_id=payload["modelId"],
            api_key=payload["apiKey"],
        )
    except MotionAuthoringRejected as error:
        # The agent's own message never contains the key, but this boundary is
        # the one that answers the caller, so it restates rather than forwards.
        raise _reject("model configuration is not usable") from error


def _workspace(payload: object) -> AuthoringWorkspace:
    if not isinstance(payload, str) or not payload:
        raise _reject("workspace is missing")
    root = Path(payload)
    if not root.is_absolute():
        raise _reject("workspace must be an absolute path the App already created")
    try:
        return AuthoringWorkspace(root)
    except MotionAuthoringRejected as error:
        raise _reject("workspace is not a usable render workspace") from error


def _catalog_root(document: dict[str, Any]) -> Path | None:
    """Where the App says the packaged parts are, or nothing.

    Absent means an installation that carries no catalog, which the agent
    refuses to paper over: a beat that chose a part is reported rather than
    quietly drawn from the built-in template.

    Checked here rather than trusted, on the same terms as the workspace. This
    path is handed to the working-copy writer, which globs and reads under it,
    so a relative path would resolve against whatever the Executor's working
    directory happens to be.
    """
    payload = document.get("catalogRoot")
    if payload is None:
        return None
    if not isinstance(payload, str) or not payload:
        raise _reject("catalog root is missing")
    root = Path(payload)
    if not root.is_absolute():
        raise _reject("catalog root must be an absolute path the App resolved")
    return root


def _brief(document: dict[str, Any]) -> MotionBrief:
    assets = document["brandAssets"]
    if not isinstance(assets, list) or not all(type(a) is str for a in assets):
        raise _reject("brand assets are not the declared shape")
    duration = document["durationSeconds"]
    if type(duration) is not int:
        raise _reject("duration must be a whole number of seconds")
    try:
        return MotionBrief(
            text=document["brief"],
            aspect_ratio=document["aspectRatio"],
            duration_seconds=duration,
            language=document["language"],
            brand_assets=tuple(assets),
        )
    except MotionAuthoringRejected as error:
        raise _reject(f"brief is outside the declared bounds: {error}") from error


def run_motion_authoring_entry(
    document: object,
    *,
    model_call: Callable[..., str] = call_video_creation_model,
) -> dict[str, Any]:
    """Author one composition into an existing workspace and describe the RenderJob.

    The answer is what the App needs to start a render and nothing else: no
    workspace paths beyond the entry name it already knows, no model reply, no
    credential.
    """
    if not isinstance(document, dict) or not (
        _REQUEST_FIELDS <= set(document) <= _REQUEST_FIELDS | _OPTIONAL_REQUEST_FIELDS
    ):
        raise _reject("request is not the declared shape")
    if document["schemaVersion"] != SCHEMA_VERSION:
        raise _reject("unsupported request schema version")
    workspace = _workspace(document["workspace"])
    brief = _brief(document)
    model = _model(document["model"])
    try:
        agent = MotionAuthoringAgent(
            workspace=workspace,
            tools=MotionAuthoringTools(workspace),
            workflow=load_locked_authoring_workflow(
                vendor_root=AUTHORING_VENDOR_ROOT,
                contract_path=AUTHORING_WORKFLOW_CONTRACT,
            ),
            model_config=model,
            model_call=model_call,
            catalog_root=_catalog_root(document),
        )
        result = agent.author(brief)
    except MotionAuthoringUnavailable as error:
        raise _reject("video creation model is unavailable") from error
    except MotionAuthoringRejected as error:
        raise _reject(str(error)) from error
    except MotionAuthoringPersistenceError as error:
        raise _reject("workspace is not a usable render workspace") from error
    submission = result.submission
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "authored",
        "entryHtml": submission.entry_html,
        "allowedAssets": list(submission.allowed_assets),
        "frameCount": submission.frame_count,
        "framesPerSecond": submission.fps,
        "durationSeconds": submission.duration_seconds,
        "aspectRatio": submission.aspect_ratio,
        # One render per shot. The first four fields describe the template
        # segment and stay for the film that is only that; `segments` is what a
        # film assembled from catalog parts actually needs rendered.
        "segments": [segment.to_payload() for segment in submission.segments],
    }


def serve_one_motion_authoring_request(stream: BinaryIO, out: TextIO) -> int:
    """Read one request, answer one document, and report the outcome as an exit code.

    Every failure answers on stdout too. A caller that only ever sees a
    traceback on stderr has to guess whether the run was refused or crashed,
    and the App's user sees the same difference as "it didn't work".
    """
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise _reject("request is too large")
        result = run_motion_authoring_entry(json.loads(raw.decode("utf-8")))
    except MotionAuthoringEntryRejected as error:
        # The exception is exported for the Executor boundary tests and future
        # internal callers. Revalidate at the one serialization sink so a new
        # caller cannot bypass the closed translator by constructing it with
        # model output, a credential or a local path.
        rejection_reason = _closed_wire_reason(error.rejection_reason)
    except (json.JSONDecodeError, UnicodeError):
        rejection_reason = None
    else:
        json.dump(result, out, separators=(",", ":"), sort_keys=True)
        out.flush()
        return 0
    answer: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": _answer_status(rejection_reason),
    }
    if rejection_reason is not None:
        answer["rejectionReason"] = rejection_reason
    json.dump(answer, out, separators=(",", ":"), sort_keys=True)
    out.flush()
    return 70


__all__ = [
    "MAX_REQUEST_BYTES",
    "SCHEMA_VERSION",
    "MotionAuthoringEntryRejected",
    "parse_motion_authoring_refusal",
    "run_motion_authoring_entry",
    "serve_one_motion_authoring_request",
]
