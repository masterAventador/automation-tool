"""The one-shot authoring entry the App calls to turn a sentence into a RenderJob.

The authoring agent has to run somewhere on the user's machine: it holds a
video-creation model key, writes into a private render workspace and produces
untrusted HTML. The Executor is already the process with that posture, so the
entry lives here — as a short-lived child process that reads one JSON request
on stdin and answers one JSON document on stdout, never as a long-lived server
and never with the key on a command line or in the environment.

The authored happy path is covered next to the composition fixture it needs, in
`scripts/test_motion_authoring_agent.py`; what belongs here is the boundary this
process owns — parsing the request, refusing one the shared contract rejects,
and answering with a document instead of a traceback.
"""

from __future__ import annotations

import ast
import io
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

import automation_tool.executor.motion_authoring.entry as motion_authoring_entry
from automation_tool.executor.motion_authoring import (
    MotionAuthoringEntryRejected,
    run_motion_authoring_entry,
)
from automation_tool.executor.motion_authoring.agent import (
    MotionAuthoringRejected,
    VideoCreationModelConfig,
    call_video_creation_model,
)

BRIEF = "用蓝色商务风做一段本周销售增长说明"
MODEL = {
    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "modelId": "qwen3.7-max-2026-06-08",
    "apiKey": "sk-" + "a" * 40,
}


class _NeverCalledModel:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *arguments: object, **keywords: object) -> str:
        self.calls += 1
        raise AssertionError("the model must not be reached")


def _request(root: Path, **overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schemaVersion": 1,
        "workspace": str(root),
        "brief": BRIEF,
        "aspectRatio": "16:9",
        "durationSeconds": 6,
        "language": "zh",
        "brandAssets": [],
        "model": MODEL,
    }
    document.update(overrides)
    return document


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "job"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "gsap.min.js").write_text("/* runtime */\n", encoding="utf-8")
    return root


def test_a_film_longer_than_the_renderer_can_capture_never_reaches_the_model(
    workspace: Path,
) -> None:
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected):
        run_motion_authoring_entry(
            _request(workspace, durationSeconds=10_000), model_call=model
        )
    assert model.calls == 0


def test_a_request_that_is_not_the_declared_shape_is_refused(workspace: Path) -> None:
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected):
        run_motion_authoring_entry(
            {"workspace": str(workspace), "brief": BRIEF}, model_call=model
        )
    assert model.calls == 0


def test_a_workspace_outside_the_request_is_refused(tmp_path: Path) -> None:
    """A relative or non-existent workspace is a caller error, not a directory to create.

    The App owns render workspaces and hands over one it already created; a
    path this process invents would be outside the store that bounds, quotas
    and eventually deletes them.
    """
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected):
        run_motion_authoring_entry(
            _request(tmp_path, workspace="relative/path"), model_call=model
        )
    assert model.calls == 0


def test_the_child_process_answers_one_json_document_and_never_echoes_the_key(
    workspace: Path,
) -> None:
    request = _request(workspace, durationSeconds=10_000)

    completed = subprocess.run(
        [sys.executable, "-m", "automation_tool.executor", "--author-motion"],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=180,
    )

    assert completed.returncode != 0
    answer = json.loads(completed.stdout.decode())
    assert answer == {
        "schemaVersion": 1,
        "status": "rejected",
        "rejectionReason": "brief_duration_out_of_range",
    }
    combined = completed.stdout.decode() + completed.stderr.decode()
    assert MODEL["apiKey"] not in combined


def test_the_final_refusal_serializer_revalidates_the_closed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = "/Users/private/input.txt"

    def raise_unchecked_reason(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise MotionAuthoringEntryRejected(f"caller path: {private_path}")

    monkeypatch.setattr(
        motion_authoring_entry,
        "run_motion_authoring_entry",
        raise_unchecked_reason,
    )
    output = io.StringIO()

    assert (
        motion_authoring_entry.serve_one_motion_authoring_request(
            io.BytesIO(b"{}"),
            output,
        )
        == 70
    )
    assert json.loads(output.getvalue()) == {
        "schemaVersion": 1,
        "status": "rejected",
    }
    assert private_path not in output.getvalue()


def test_refusal_parser_accepts_only_the_dedicated_closed_reason_field() -> None:
    parser = getattr(motion_authoring_entry, "parse_motion_authoring_refusal", None)

    assert callable(parser)
    assert (
        parser(
            {
                "schemaVersion": 1,
                "status": "rejected",
                "rejectionReason": "brief_duration_out_of_range",
            }
        )
        == "brief_duration_out_of_range"
    )
    assert (
        parser(
            {
                "schemaVersion": 1,
                "status": "rejected",
                "rejectionReason": "the caller supplied /Users/private/input.txt",
            }
        )
        is None
    )
    assert (
        parser(
            {
                "schemaVersion": 1,
                "status": "rejected",
                "rejectionReason": "brief_duration_out_of_range",
                "detail": "arbitrary strings must not widen this wire",
            }
        )
        is None
    )


def _fixed_agent_rejections() -> set[str]:
    source = Path(motion_authoring_entry.__file__).with_name("agent.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    messages: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"_reject", "_require"}
            and node.args
            and isinstance(node.args[-1], ast.Constant)
            and isinstance(node.args[-1].value, str)
        ):
            messages.add(f"motion authoring rejected: {node.args[-1].value}")
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "MotionAuthoringRejected"
            and node.exc.args
            and isinstance(node.exc.args[0], ast.Constant)
            and isinstance(node.exc.args[0].value, str)
        ):
            messages.add(node.exc.args[0].value)
    return messages


def _fixed_entry_rejections() -> set[str]:
    source = Path(motion_authoring_entry.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_reject"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def test_every_fixed_upstream_rejection_has_its_own_closed_reason_token() -> None:
    classifier: Any = getattr(motion_authoring_entry, "_closed_rejection_reason", None)
    assert callable(classifier)

    fixed_messages = _fixed_agent_rejections() | _fixed_entry_rejections()
    classified = {message: classifier(message) for message in fixed_messages}
    assert all(reason is not None for reason in classified.values()), classified
    assert len(set(classified.values())) == len(classified), classified
    assert classifier("motion authoring rejected: caller path /Users/private/input.txt") is None

    structure_tokens = {
        classifier(f"motion authoring rejected: {label} {ending}")
        for label in ("design", "script", "storyboard beat", "storyboard")
        for ending in ("must be an object", "has an unexpected key set")
    }
    assert None not in structure_tokens
    assert len(structure_tokens) == 8

    color_tokens = {
        classifier(f"motion authoring rejected: {key} must be a #rrggbb color")
        for key in ("primary_color", "secondary_color")
    }
    assert None not in color_tokens
    assert len(color_tokens) == 2

    gate_tokens = {
        classifier(
            "motion authoring rejected: "
            f"composition failed static gates after local fixes: ['{code}']"
        )
        for code in (
            "canvas_mismatch",
            "clip_coverage",
            "clip_interval_invalid",
            "clip_overlap",
            "clip_visibility_uncontrolled",
            "composition_invalid",
            "composition_too_large",
            "determinism_violation",
            "duration_mismatch",
            "missing_canvas",
            "missing_clip",
            "missing_composition_root",
            "missing_duration",
            "missing_timeline",
            "network_reference",
            "remote_reference",
            "timeline_not_paused",
            "undeclared_asset",
        )
    }
    assert None not in gate_tokens
    assert len(gate_tokens) == 18
    assert (
        classifier(
            "motion authoring rejected: "
            "composition failed static gates after local fixes: "
            "['remote_reference', 'undeclared_asset']"
        )
        is not None
    )
    assert (
        classifier(
            "motion authoring rejected: "
            "composition failed static gates after local fixes: ['user_supplied']"
        )
        is None
    )


def _unused_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _model_at(base_url: str) -> VideoCreationModelConfig:
    return VideoCreationModelConfig(
        base_url=base_url, model_id=MODEL["modelId"], api_key=MODEL["apiKey"]
    )


def test_a_model_that_never_answers_is_not_the_same_failure_as_one_that_is_not_there() -> None:
    """A model that is not there and a model that goes quiet are two failures.

    Failure injection measured both on 2026-07-26, and the user was told the
    same thing by both: an unreachable model produced "judged this description
    impossible, try a more specific one" after two seconds, and a model that
    took the connection and then sent nothing produced it word for word after
    363. That number is MODEL_TIMEOUT_SECONDS plus the connect, and the two
    paths met at one `except OSError` in `call_video_creation_model`, which gave
    them one reason and made them indistinguishable from there on.

    Real sockets over the real transport: one port nobody listens on, and one
    that accepts the connection and never writes a byte. The timeouts are
    seconds rather than minutes; the shape is the one that was measured.
    """
    dead_port = _unused_loopback_port()
    with pytest.raises(MotionAuthoringRejected) as unreachable:
        call_video_creation_model(
            _model_at(f"https://127.0.0.1:{dead_port}/v1"),
            [{"role": "user", "content": "hi"}],
            timeout_seconds=5,
        )

    stop = threading.Event()
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        stalled_port = int(listener.getsockname()[1])

        def accept_and_stall() -> None:
            connection, _ = listener.accept()
            stop.wait(30)
            connection.close()

        stalling = threading.Thread(target=accept_and_stall, daemon=True)
        stalling.start()
        try:
            with pytest.raises(MotionAuthoringRejected) as stalling_model:
                call_video_creation_model(
                    _model_at(f"https://127.0.0.1:{stalled_port}/v1"),
                    [{"role": "user", "content": "hi"}],
                    timeout_seconds=2,
                )
        finally:
            stop.set()
            stalling.join(timeout=5)
    finally:
        listener.close()

    assert str(unreachable.value) != str(stalling_model.value)
    classifier: Any = motion_authoring_entry._closed_rejection_reason
    assert classifier(str(unreachable.value)) != classifier(str(stalling_model.value))
    assert classifier(str(unreachable.value)) is not None
    assert classifier(str(stalling_model.value)) is not None


@pytest.mark.parametrize(
    "reason",
    ["video_creation_model_transport_failed", "video_creation_model_unavailable"],
)
def test_a_model_service_failure_is_never_answered_as_a_refusal_of_the_brief(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """An unusable model service is not the agent declining this brief.

    The refusal document means something: `answer_is_refusal` recognises it and
    reports `authoring_refused`, which the card words as the agent having read
    this description and judged it impossible, and which asks the user for a
    more specific sentence. Nothing read the sentence when the model was never
    reached, so answering that way sends the user to rewrite something that was
    never the problem.

    Model-service failures therefore leave the refusal channel. The document
    still carries its own closed reason, so nothing is lost for the day the App
    reads it.
    """
    def raise_model_service_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise MotionAuthoringEntryRejected(reason)

    monkeypatch.setattr(
        motion_authoring_entry, "run_motion_authoring_entry", raise_model_service_failure
    )
    output = io.StringIO()

    assert (
        motion_authoring_entry.serve_one_motion_authoring_request(io.BytesIO(b"{}"), output) != 0
    )
    answer = json.loads(output.getvalue())
    assert motion_authoring_entry.parse_motion_authoring_refusal(answer) is None
    assert answer["status"] != "rejected"
    assert answer["rejectionReason"] == reason
