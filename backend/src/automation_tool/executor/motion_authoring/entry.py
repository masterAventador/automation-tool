"""One request in on stdin, one answer out on stdout, then exit.

The App owns the render workspace and the model credential; this process owns
neither beyond the run it was started for. It is deliberately not a server:
there is no port to reach, nothing survives the call, and the credential exists
only in the bytes that arrived on stdin — never in `argv`, never in the
environment, never in the answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, Final, TextIO

from automation_tool.executor.motion_authoring.agent import (
    AUTHORING_VENDOR_ROOT,
    AUTHORING_WORKFLOW_CONTRACT,
    AuthoringWorkspace,
    MotionAuthoringAgent,
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
_MODEL_FIELDS: Final = frozenset({"baseUrl", "modelId", "apiKey"})


class MotionAuthoringEntryRejected(ValueError):
    """Fixed failure boundary for a request this process will not act on."""


def _reject(reason: str) -> MotionAuthoringEntryRejected:
    return MotionAuthoringEntryRejected(f"motion authoring entry rejected: {reason}")


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
    if not isinstance(document, dict) or set(document) != _REQUEST_FIELDS:
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
        )
        result = agent.author(brief)
    except MotionAuthoringUnavailable as error:
        raise _reject("video creation model is unavailable") from error
    except MotionAuthoringRejected as error:
        raise _reject(str(error)) from error
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
    except (MotionAuthoringEntryRejected, json.JSONDecodeError, UnicodeError):
        # The reason is deliberately not forwarded: it is built from caller
        # input and the caller already knows what it sent, while anything that
        # travelled through this process may quote it back.
        json.dump(
            {"schemaVersion": SCHEMA_VERSION, "status": "rejected"},
            out,
            separators=(",", ":"),
        )
        out.flush()
        return 70
    json.dump(result, out, separators=(",", ":"), sort_keys=True)
    out.flush()
    return 0


__all__ = [
    "MAX_REQUEST_BYTES",
    "MotionAuthoringEntryRejected",
    "SCHEMA_VERSION",
    "run_motion_authoring_entry",
    "serve_one_motion_authoring_request",
]
