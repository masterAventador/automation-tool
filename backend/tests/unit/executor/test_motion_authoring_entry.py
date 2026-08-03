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
from typing import Any, cast

import pytest

import automation_tool.executor.motion_authoring.entry as motion_authoring_entry
from automation_tool.executor.motion_authoring import (
    MotionAuthoringEntryRejected,
    run_motion_authoring_entry,
)
from automation_tool.executor.motion_authoring.agent import (
    AuthoringWorkspace,
    MotionAuthoringAgent,
    MotionAuthoringRejected,
    MotionAuthoringTools,
    MotionAuthoringUnavailable,
    VideoCreationModelConfig,
    call_video_creation_model,
    verify_closed_tool_surface,
)
from automation_tool.executor.motion_authoring.entry import (
    serve_one_motion_authoring_request,
)

BRIEF = "用蓝色商务风做一段本周销售增长说明"
MODEL = {
    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "modelId": "qwen3.7-max-2026-06-08",
    "apiKey": "sk-" + "a" * 40,
}

FIRST_AUTHORING_REPLY = json.dumps(
    {
        "design": {
            "style_preset_id": "blue-professional",
            "primary_color": "#0b1f3a",
            "secondary_color": "#2f6fd6",
            "typography": "restrained bold sans serif",
        },
        # The injected DESIGN write failure happens before these fields are
        # parsed. Keeping them present still exercises the production response
        # shape without duplicating the large happy-path composition fixture.
        #
        # Exactly these three keys, no more: T92 moved the composition out of
        # the model's reply and into this machine's template, and the first
        # response is checked for the exact key set before anything is written.
        # A stale fourth key here fails that check instead of the disk write,
        # so the test would pass on the wrong rejection — which is what the
        # T92/T106 merge produced until this line was removed.
        "script": None,
        "storyboard": None,
    }
)


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
        run_motion_authoring_entry(_request(workspace, durationSeconds=10_000), model_call=model)
    assert model.calls == 0


def test_a_request_that_is_not_the_declared_shape_is_refused(workspace: Path) -> None:
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected):
        run_motion_authoring_entry({"workspace": str(workspace), "brief": BRIEF}, model_call=model)
    assert model.calls == 0


def test_a_relative_browser_executable_is_refused_before_the_model(
    workspace: Path,
) -> None:
    """The probe launches whatever this path names, so a path the App did not
    resolve absolutely is a caller error — never something to search for."""
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(
            _request(workspace, browserExecutable="relative/chromium"),
            model_call=model,
        )
    assert caught.value.rejection_reason == "browser_executable_not_absolute"
    assert model.calls == 0


def test_an_empty_browser_executable_is_refused_before_the_model(
    workspace: Path,
) -> None:
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(_request(workspace, browserExecutable=""), model_call=model)
    assert caught.value.rejection_reason == "browser_executable_missing"
    assert model.calls == 0


def test_the_authorized_browser_reaches_the_agent_as_a_probe(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the field the agent gets a real probe; without it, none.

    `None` must keep meaning "no measurement" — an older App that never sends
    the field keeps today's behaviour instead of a crash or a fake pass.
    """
    seen: list[object] = []

    class _RecordingAgent:
        def __init__(self, **keywords: object) -> None:
            seen.append(keywords.get("slot_probe"))

        def author(self, _brief: object) -> None:
            raise AssertionError("the recording agent never authors")

    monkeypatch.setattr(motion_authoring_entry, "MotionAuthoringAgent", _RecordingAgent)
    for request in (
        _request(workspace, browserExecutable=str(workspace / "chromium")),
        _request(workspace),
    ):
        with pytest.raises(AssertionError, match="recording agent never authors"):
            run_motion_authoring_entry(request, model_call=_NeverCalledModel())
    with_field, without_field = seen
    assert callable(with_field)
    assert without_field is None


def test_a_relative_ffprobe_executable_is_refused_before_the_model(
    workspace: Path,
) -> None:
    """Narration length is measured by whatever this path names (PC-26), so a
    path the App did not resolve absolutely is a caller error."""
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(
            _request(workspace, ffprobeExecutable="relative/ffprobe"),
            model_call=model,
        )
    assert caught.value.rejection_reason == "ffprobe_executable_not_absolute"
    assert model.calls == 0


def test_an_empty_ffprobe_executable_is_refused_before_the_model(
    workspace: Path,
) -> None:
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(_request(workspace, ffprobeExecutable=""), model_call=model)
    assert caught.value.rejection_reason == "ffprobe_executable_missing"
    assert model.calls == 0


def test_the_ffprobe_path_reaches_the_agent_as_a_narrator(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the field the agent gets a narrator; without it, a silent film —
    an older App keeps today's behaviour instead of a crash or a fake voice."""
    seen: list[object] = []

    class _RecordingAgent:
        def __init__(self, **keywords: object) -> None:
            seen.append(keywords.get("narrator"))

        def author(self, _brief: object) -> None:
            raise AssertionError("the recording agent never authors")

    monkeypatch.setattr(motion_authoring_entry, "MotionAuthoringAgent", _RecordingAgent)
    for request in (
        _request(workspace, ffprobeExecutable=str(workspace / "ffprobe")),
        _request(workspace),
    ):
        with pytest.raises(AssertionError, match="recording agent never authors"):
            run_motion_authoring_entry(request, model_call=_NeverCalledModel())
    with_field, without_field = seen
    assert callable(with_field)
    assert without_field is None


def test_a_workspace_outside_the_request_is_refused(tmp_path: Path) -> None:
    """A relative or non-existent workspace is a caller error, not a directory to create.

    The App owns render workspaces and hands over one it already created; a
    path this process invents would be outside the store that bounds, quotas
    and eventually deletes them.
    """
    model = _NeverCalledModel()
    with pytest.raises(MotionAuthoringEntryRejected):
        run_motion_authoring_entry(_request(tmp_path, workspace="relative/path"), model_call=model)
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


def test_a_partial_workspace_write_is_closed_and_rolled_back(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    """A disk failure is an execution failure, not a traceback or model refusal."""
    private_path = workspace / "operator-private" / "DESIGN.json"
    original_write_text = Path.write_text

    def short_write_then_fail(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == "DESIGN.json":
            original_write_text(path, data[:8], encoding=encoding)
            raise OSError(f"disk full while writing {private_path}")
        return original_write_text(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", short_write_then_fail)
    original_entry = motion_authoring_entry.run_motion_authoring_entry

    def run_with_scripted_model(document: object) -> dict[str, object]:
        return original_entry(
            document,
            model_call=lambda *_args, **_kwargs: FIRST_AUTHORING_REPLY,
        )

    monkeypatch.setattr(
        motion_authoring_entry,
        "run_motion_authoring_entry",
        run_with_scripted_model,
    )
    output = io.StringIO()

    try:
        return_code = motion_authoring_entry.serve_one_motion_authoring_request(
            io.BytesIO(json.dumps(_request(workspace)).encode()),
            output,
        )
    except Exception as error:  # pragma: no cover - the assertion below is RED today
        pytest.fail(
            "workspace persistence failure escaped the closed process boundary: "
            f"{type(error).__name__}"
        )

    serialized = output.getvalue()
    assert return_code == 70
    assert json.loads(serialized) == {
        "schemaVersion": 1,
        "status": "app_request_invalid",
        "rejectionReason": "workspace_unusable",
    }
    assert str(private_path) not in serialized
    assert MODEL["apiKey"] not in serialized
    assert not (workspace / "DESIGN.json").exists()
    assert (workspace / "runtime/gsap.min.js").is_file()


def test_a_non_workspace_oserror_is_not_misclassified_as_workspace_unusable(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    """Only the workspace writer may opt an OSError into this fixed mapping."""
    private_path = workspace.parent / "installed-workflow" / "SKILL.md"

    def fail_outside_workspace(*_args: object, **_kwargs: object) -> object:
        raise OSError(f"locked workflow read failed at {private_path}")

    monkeypatch.setattr(
        motion_authoring_entry,
        "load_locked_authoring_workflow",
        fail_outside_workspace,
    )

    with pytest.raises(OSError) as unexpected_io:
        motion_authoring_entry.run_motion_authoring_entry(
            _request(workspace),
            model_call=lambda *_args, **_kwargs: FIRST_AUTHORING_REPLY,
        )

    assert str(private_path) in str(unexpected_io.value)


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

    # Every static-gate code is now a report about the *local* template rather
    # than about model output, so each one still has to arrive as its own
    # reason: a template defect answered as a generic refusal would tell the
    # user to rewrite a sentence that was never involved.
    gate_tokens = {
        classifier(f"motion authoring rejected: composition failed static gates: ['{code}']")
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
            "missing_animation_runtime",
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
    assert len(gate_tokens) == 19
    assert (
        classifier(
            "motion authoring rejected: composition failed static gates: "
            "['remote_reference', 'undeclared_asset']"
        )
        is not None
    )
    assert (
        classifier("motion authoring rejected: composition failed static gates: ['user_supplied']")
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
    absent_reason = classifier(str(unreachable.value))
    silent_reason = classifier(str(stalling_model.value))
    assert absent_reason != silent_reason
    assert absent_reason is not None
    assert silent_reason is not None

    # Two reasons are only worth having if they end up saying two different
    # things. Both used to reach the App as one code, and one code is one
    # sentence on the card whatever the tokens underneath it were.
    outcomes = _non_refusal_outcomes()
    assert absent_reason in outcomes["model_transport_failed"]
    assert silent_reason in outcomes["model_timed_out"]


def _non_refusal_outcomes() -> dict[str, frozenset[str]]:
    outcomes: Any = getattr(motion_authoring_entry, "_NON_REFUSAL_OUTCOMES", None)
    assert isinstance(outcomes, dict) and outcomes, "the shared contract must declare the classes"
    return outcomes


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

    assert motion_authoring_entry.serve_one_motion_authoring_request(io.BytesIO(b"{}"), output) != 0
    answer = json.loads(output.getvalue())
    assert motion_authoring_entry.parse_motion_authoring_refusal(answer) is None
    assert answer["status"] != "rejected"
    assert answer["rejectionReason"] == reason


def test_the_shared_contract_declares_which_findings_are_not_refusals() -> None:
    """The classes live in the contract both sides read, not in either side's head.

    The App has to tell an unreachable model from a silent one from a damaged
    install, and it can only do that from the reason token the child writes. If
    the App kept its own copy of which token means what, a token added here
    would keep its old meaning over there with nothing failing — the reason
    would simply stop being understood and the run would fall back to the
    catch-all sentence. So the mapping is one table in the shared contract and
    this test is what stops it drifting away from the vocabulary it classifies.
    """
    outcomes = _non_refusal_outcomes()
    fixed: frozenset[str] = motion_authoring_entry._FIXED_WIRE_REASONS

    assert set(outcomes) == {
        "app_request_invalid",
        "executor_defect",
        "installation_damaged",
        "model_configuration_required",
        "model_timed_out",
        "model_transport_failed",
    }
    seen: set[str] = set()
    for name, reasons in outcomes.items():
        assert motion_authoring_entry._WIRE_TOKEN.fullmatch(name), name
        assert name != "rejected", "a non-refusal class must not reuse the refusal status"
        assert reasons, name
        assert reasons <= fixed, (name, reasons - fixed)
        assert not (reasons & seen), (name, reasons & seen)
        seen |= reasons

    # Vacuous membership is the failure mode this guards: a class that exists
    # but classifies nothing would let every one of these findings keep telling
    # the user to rewrite a sentence nothing read.
    # PC-26 widened this class: a TTS round that fails is transport to a model
    # service failing, and answering it as a refusal would send the user to
    # rewrite a sentence the narrator never read.
    assert outcomes["model_transport_failed"] == {
        "agent_voiceover_synthesis_failed",
        "video_creation_model_transport_failed",
    }
    assert outcomes["model_timed_out"] == {"video_creation_model_timed_out"}
    assert "video_creation_model_unavailable" in outcomes["model_configuration_required"]
    assert "agent_pinned_workflow_file_is_missing_or_a_symlink" in outcomes["installation_damaged"]
    assert "request_shape_invalid" in outcomes["app_request_invalid"]


@pytest.mark.parametrize(
    ("name", "reason"),
    sorted(
        (name, reason) for name, reasons in _non_refusal_outcomes().items() for reason in reasons
    )
    if getattr(motion_authoring_entry, "_NON_REFUSAL_OUTCOMES", None)
    else [("missing", "missing")],
)
def test_every_non_refusal_finding_is_answered_with_its_own_status(
    monkeypatch: pytest.MonkeyPatch, name: str, reason: str
) -> None:
    """The answer's status names the class, so an older App still cannot misread it.

    An App that only knows `rejected` sees an unfamiliar status and falls back
    to "we could not finish", which is wrong but harmless. The failure this
    prevents is the opposite one: a status of `rejected` on a finding nothing
    read would be understood perfectly, and understood as the user's sentence
    being at fault.
    """

    def raise_finding(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise MotionAuthoringEntryRejected(reason)

    monkeypatch.setattr(motion_authoring_entry, "run_motion_authoring_entry", raise_finding)
    output = io.StringIO()

    assert motion_authoring_entry.serve_one_motion_authoring_request(io.BytesIO(b"{}"), output) != 0
    answer = json.loads(output.getvalue())
    assert answer["status"] == name
    assert answer["rejectionReason"] == reason
    assert motion_authoring_entry.parse_motion_authoring_refusal(answer) is None


def test_a_request_the_app_built_wrong_is_not_answered_as_a_refusal_of_the_brief() -> None:
    """No stand-in anywhere: a real malformed request through the real entry.

    `request_shape_invalid` is this process judging what the App sent it. The
    user typed a sentence that was never looked at, so asking them for a more
    specific one is both useless and untrue.
    """
    output = io.StringIO()

    assert (
        motion_authoring_entry.serve_one_motion_authoring_request(
            io.BytesIO(b'{"schemaVersion":1}'), output
        )
        != 0
    )
    answer = json.loads(output.getvalue())
    assert answer["rejectionReason"] == "request_shape_invalid"
    assert answer["status"] == "app_request_invalid"
    assert motion_authoring_entry.parse_motion_authoring_refusal(answer) is None


def test_a_damaged_install_is_not_answered_as_a_refusal_of_the_brief(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    """A real missing pinned file, produced by pointing at a real empty directory.

    This is the widest mouth of the funnel, and it was found by accident: a tree
    whose `vendor` was never checked out failed in `load_locked_authoring_workflow`
    after two seconds, and the card said "请换一句更具体的描述". Nothing about the
    installation is the user's description, and no rewrite of it will ever put
    the missing file back.

    The vendor root is redirected to an empty directory rather than stubbed, so
    the failure is the real `is_file()` check on a real path, reached through the
    real entry with a real workspace and a real request.
    """
    empty_vendor = workspace.parent / "vendor-with-nothing-in-it"
    empty_vendor.mkdir()
    monkeypatch.setattr(motion_authoring_entry, "AUTHORING_VENDOR_ROOT", empty_vendor)
    model = _NeverCalledModel()

    with pytest.raises(MotionAuthoringEntryRejected) as damaged:
        run_motion_authoring_entry(_request(workspace), model_call=model)

    assert model.calls == 0, "a damaged install must not reach the model service"
    assert damaged.value.rejection_reason == "agent_pinned_workflow_file_is_missing_or_a_symlink"
    outcomes = _non_refusal_outcomes()
    assert damaged.value.rejection_reason in outcomes["installation_damaged"]


class _ToolsWithAnExtraCapability(MotionAuthoringTools):
    """A tool surface wider than the closed allowlist — the real drift, not a stub."""

    def read_anything(self, relative_path: str) -> str:  # pragma: no cover - never called
        raise AssertionError("this capability exists only to widen the surface")


def _wiring_defects(workspace: Path) -> dict[str, Any]:
    """Every guard that can only fire because we wired this process wrong.

    Each entry calls the real constructor or the real verifier with the real
    wrong argument, so the token asserted below is the token the shipped code
    actually produces rather than one copied out of the contract by hand. A
    typo in the contract would classify nothing and leave every side looking
    consistent, which is the failure this shape rules out.
    """
    root = AuthoringWorkspace(workspace)
    tools = MotionAuthoringTools(root)
    not_a_workspace = cast(Any, object())
    not_a_workflow = cast(Any, object())
    return {
        "agent_workspace_required": lambda: MotionAuthoringAgent(
            workspace=not_a_workspace,
            tools=tools,
            workflow=not_a_workflow,
            model_config=None,
        ),
        "agent_workflow_reference_required": lambda: MotionAuthoringAgent(
            workspace=root,
            tools=tools,
            workflow=not_a_workflow,
            model_config=None,
        ),
        "agent_not_a_motionauthoringtools_instance": lambda: verify_closed_tool_surface(
            cast(Any, object())
        ),
        "agent_tool_surface_does_not_match_the_closed_allowlist": (
            lambda: verify_closed_tool_surface(_ToolsWithAnExtraCapability(root))
        ),
        # The fifth of the same kind. `entry.py` is the only production caller
        # and it always passes the AuthoringWorkspace it already validated, so
        # nothing the user types can reach this guard either.
        "agent_tools_require_an_authoringworkspace": lambda: MotionAuthoringTools(
            cast(Any, object())
        ),
    }


def test_our_own_wiring_defect_is_never_answered_as_a_refusal_of_the_brief(
    workspace: Path,
) -> None:
    """Internal guards were telling the user to rewrite their sentence.

    None of them can be reached by anything the user types. The workspace was
    not handed over, the pinned workflow reference was not handed over, the
    tools argument was the wrong type, or the tool surface no longer matches
    the closed allowlist — every one of them is this process being constructed
    wrong, and every one of them arrived at the card as
    "请换一句更具体的描述后重试".

    That is the same mistake T90 removed for an unreachable model service, in a
    different disguise: our defect worded as the user's fault. The user cannot
    act on it at all, and the sentence they are sent to rewrite was never read.

    All of them are reported together rather than one assertion per guard, so a
    contract that classifies some of them names the ones it left behind instead
    of stopping at the first.
    """
    outcomes = _non_refusal_outcomes()
    classify: Any = motion_authoring_entry._closed_rejection_reason
    status: Any = motion_authoring_entry._answer_status
    defects = _wiring_defects(workspace)

    produced: dict[str, object] = {}
    for expected_token, produce in defects.items():
        with pytest.raises(MotionAuthoringRejected) as defect:
            produce()
        produced[expected_token] = classify(str(defect.value))

    assert produced == {token: token for token in defects}, (
        "these guards no longer produce the tokens the contract classifies"
    )
    ours = outcomes.get("executor_defect", frozenset())
    unclassified = sorted(token for token in defects if token not in ours)
    assert not unclassified, f"our own defects are still refusals of the brief: {unclassified}"
    blamed = sorted(token for token in defects if status(token) == "rejected")
    assert not blamed, f"these reach the App as a refusal of a sentence nothing read: {blamed}"


def test_the_request_shape_is_checked_field_by_field(workspace: Path) -> None:
    """Each of these reaches something that would otherwise be built from it."""
    model = _NeverCalledModel()

    cases: list[tuple[str, dict[str, object]]] = [
        ("another schema version", {"schemaVersion": 2}),
        ("a workspace that is not text", {"workspace": 1}),
        ("an empty workspace", {"workspace": ""}),
        ("a workspace that is not a usable one", {"workspace": str(workspace / "absent")}),
        ("a model that is not an object", {"model": "qwen"}),
        ("a model with the wrong field set", {"model": {"modelId": "x"}}),
        ("brand assets that are not a list", {"brandAssets": "logo.png"}),
        ("a brand asset that is not text", {"brandAssets": [1]}),
        ("a duration that is not a whole number", {"durationSeconds": 6.0}),
        ("a thinking choice that is not a bool", {"modelThinking": "yes"}),
        ("a catalog root that is not text", {"catalogRoot": 1}),
        ("an empty catalog root", {"catalogRoot": ""}),
        ("a relative catalog root", {"catalogRoot": "relative/catalog"}),
        ("catalog part overrides that are not a list", {"catalogParts": {}}),
        ("a catalog part override that is not text", {"catalogParts": [1]}),
    ]
    for label, overrides in cases:
        with pytest.raises(MotionAuthoringEntryRejected):
            run_motion_authoring_entry(_request(workspace, **overrides), model_call=model)
        assert label

    assert model.calls == 0, "nothing may reach the model before the request is trusted"


def test_a_model_configuration_the_agent_refuses_is_restated_not_forwarded(
    workspace: Path,
) -> None:
    """The agent's message never carries the key, but this boundary restates anyway."""
    model = _NeverCalledModel()

    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(
            _request(
                workspace,
                model={"baseUrl": "http://insecure.example", "modelId": "m", "apiKey": "sk-x"},
            ),
            model_call=model,
        )

    assert "sk-x" not in str(caught.value)
    assert model.calls == 0


def test_a_model_that_is_unavailable_is_told_apart_from_one_that_refused(
    workspace: Path,
) -> None:
    """Retryable and not-retryable lead to different next steps for the caller."""

    def unavailable(*_args: object, **_kw: object) -> object:
        raise MotionAuthoringUnavailable("motion authoring unavailable: gateway down")

    with pytest.raises(MotionAuthoringEntryRejected) as caught:
        run_motion_authoring_entry(_request(workspace), model_call=unavailable)

    # The message stays fixed; the closed reason is what tells the two apart.
    assert caught.value.rejection_reason == "video_creation_model_unavailable"


def test_a_wire_reason_outside_the_closed_vocabulary_is_dropped() -> None:
    """Anything not in the contract becomes no reason at all, never a passthrough."""
    closed = motion_authoring_entry._closed_wire_reason

    assert closed(None) is None
    assert closed(1) is None
    assert closed("something_nobody_declared") is None


def test_a_static_gate_reason_is_only_read_when_it_is_the_declared_shape() -> None:
    reader = motion_authoring_entry._closed_static_gate_reason

    assert reader("not a gate message") is None
    prefix = motion_authoring_entry._STATIC_GATE_MESSAGE_PREFIX
    assert reader(prefix + "x" * 1025) is None, "an oversized payload is not parsed"
    assert reader(prefix + "[unclosed") is None, "a payload that will not parse is dropped"


def test_a_rejection_reason_that_is_not_text_or_not_the_agents_is_dropped() -> None:
    reader = motion_authoring_entry._closed_rejection_reason

    assert reader(None) is None
    assert reader(1) is None
    assert reader("something the agent never says") is None


def test_an_outcome_table_that_could_answer_two_ways_is_refused() -> None:
    """A token in two classes would answer with whichever iteration reached first."""
    declared = motion_authoring_entry._outcomes_are_declared
    fixed = frozenset({"a", "b"})

    assert declared({"partial": ["a"], "refused_by_model": ["b"]}, fixed) is True
    assert declared("not a table", fixed) is False
    assert declared({}, fixed) is False
    assert declared({"b_class": ["a"], "a_class": ["b"]}, fixed) is False, "unsorted names"
    assert declared({"Partial": ["a"]}, fixed) is False, "a name outside the wire token shape"
    assert declared({"partial": "a"}, fixed) is False, "reasons that are not a list"
    assert declared({"partial": []}, fixed) is False, "a class with no reasons"
    assert declared({"partial": ["b", "a"]}, fixed) is False, "unsorted reasons"
    assert declared({"partial": ["c"]}, fixed) is False, "a reason outside the fixed set"
    assert declared({"partial": ["a"], "second": ["a"]}, fixed) is False, (
        "the same reason in two classes"
    )
    reserved = motion_authoring_entry._REFUSED_STATUS
    assert declared({reserved: ["a"]}, fixed) is False, "the reserved status name"


def test_a_request_larger_than_the_reader_accepts_is_refused_without_parsing() -> None:
    oversized = b"{" + b" " * (motion_authoring_entry.MAX_REQUEST_BYTES + 1) + b"}"
    out = io.StringIO()

    code = serve_one_motion_authoring_request(io.BytesIO(oversized), out)

    assert code != 0
    assert json.loads(out.getvalue())["rejectionReason"] == "request_too_large"


def test_a_request_that_is_not_readable_json_answers_without_a_reason() -> None:
    """A body that will not decode names no closed reason, so none is invented."""
    out = io.StringIO()

    code = serve_one_motion_authoring_request(io.BytesIO(b"\xff\xfe not json"), out)

    assert code != 0
