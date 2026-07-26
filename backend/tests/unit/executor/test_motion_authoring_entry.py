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

import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring import (
    MotionAuthoringEntryRejected,
    run_motion_authoring_entry,
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
    assert answer["status"] == "rejected"
    combined = completed.stdout.decode() + completed.stderr.decode()
    assert MODEL["apiKey"] not in combined
