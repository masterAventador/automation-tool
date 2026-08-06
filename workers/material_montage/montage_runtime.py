"""Headless material montage: the upstream pipeline without the Streamlit UI.

The product's own React form collects the parameters and they arrive on the
authenticated stdin bootstrap — one worker process per job, exactly the
WebUI's lifecycle. Everything below the surface is unchanged and shared:
`_preload_private_config` pins the subtitle font and the packaged Pexels key,
`install_script_model` routes the LLM call, and the job observation bridge
projects progress into the App-owned render-job ledger, so the job list,
cancel handling and artifact reads did not move.

Why a rewrite at all: the upstream UI is 3,219 lines of Streamlit that can
only exist inside a native WebView overlay floating above the page — the
source of the "whole page unclickable" defect (T108) and of the floating
panel the operator reported on 2026-08-05. The upstream *services* are a
plain Python library; this module calls them directly.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from job_observation_bridge import (
    MAX_SUBJECT_CHARACTERS as OBSERVATION_SUBJECT_LIMIT,
    OBSERVATION_FILE,
    SCHEMA_VERSION,
    JobCancelled,
    _atomic_json,
)
from model_service_adapter import ScriptModelConfiguration, install_script_model

MAX_SUBJECT_CHARACTERS: Final = 240
MAX_SCRIPT_CHARACTERS: Final = 5000
ASPECTS: Final = frozenset({"9:16", "16:9", "1:1"})
COLOR_PATTERN: Final = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Upstream voice identifiers are dash-joined ASCII names (edge-tts). A closed
# pattern rather than a closed list: the curated choices live in the product
# UI, and a value that could not be a voice name must not reach a subprocess.
VOICE_PATTERN: Final = re.compile(r"^[A-Za-z]{2}-[A-Za-z]{2,8}-[A-Za-z0-9-]{2,64}$")
REQUEST_KEYS: Final = frozenset(
    {
        "aspect",
        "clipDurationSeconds",
        "fontSizePx",
        "script",
        "strokeColor",
        "strokeWidthPx",
        "subject",
        "subtitleEnabled",
        "textColor",
        "voiceName",
    }
)


class MontageRejected(ValueError):
    """The montage request is not one the product ever produces."""


@dataclass(frozen=True)
class MontageRequest:
    """One validated montage job, exactly as the product form submitted it."""

    subject: str
    script: str | None
    aspect: str
    clip_duration_seconds: int
    voice_name: str
    subtitle_enabled: bool
    font_size_px: int
    text_color: str
    stroke_color: str
    stroke_width_px: float

    def __repr__(self) -> str:  # The subject is operator content; keep it out.
        return "MontageRequest(<redacted>)"


def parse_montage_request(value: object) -> MontageRequest:
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise MontageRejected("invalid montage request")
    subject = value.get("subject")
    script = value.get("script")
    aspect = value.get("aspect")
    clip_duration = value.get("clipDurationSeconds")
    voice_name = value.get("voiceName")
    subtitle_enabled = value.get("subtitleEnabled")
    font_size = value.get("fontSizePx")
    text_color = value.get("textColor")
    stroke_color = value.get("strokeColor")
    stroke_width = value.get("strokeWidthPx")
    if (
        not isinstance(subject, str)
        or not 0 < len(subject.strip()) <= MAX_SUBJECT_CHARACTERS
        or not (
            script is None
            or (isinstance(script, str) and len(script) <= MAX_SCRIPT_CHARACTERS)
        )
        or aspect not in ASPECTS
        or not isinstance(clip_duration, int)
        or isinstance(clip_duration, bool)
        or not 2 <= clip_duration <= 10
        or not isinstance(voice_name, str)
        or VOICE_PATTERN.fullmatch(voice_name) is None
        or not isinstance(subtitle_enabled, bool)
        or not isinstance(font_size, int)
        or isinstance(font_size, bool)
        or not 24 <= font_size <= 120
        or not isinstance(text_color, str)
        or COLOR_PATTERN.fullmatch(text_color) is None
        or not isinstance(stroke_color, str)
        or COLOR_PATTERN.fullmatch(stroke_color) is None
        or not isinstance(stroke_width, (int, float))
        or isinstance(stroke_width, bool)
        or not 0 <= float(stroke_width) <= 5
    ):
        raise MontageRejected("invalid montage request")
    return MontageRequest(
        subject=subject.strip(),
        script=script.strip() if isinstance(script, str) and script.strip() else None,
        aspect=aspect,
        clip_duration_seconds=clip_duration,
        voice_name=voice_name,
        subtitle_enabled=subtitle_enabled,
        font_size_px=font_size,
        text_color=text_color,
        stroke_color=stroke_color,
        stroke_width_px=float(stroke_width),
    )


class MontageThread(threading.Thread):
    """The montage job thread, carrying its outcome for the exit watchdog.

    `failed` is what worker_main's watchdog reads to pick the process exit
    code — before it existed, every dead pipeline exited 0 and was
    indistinguishable from a finished one (REVIEW-2026-08-06 C2).
    """

    failed: bool = False


_TERMINAL_STATUSES: Final = frozenset({"succeeded", "failed", "cancelled"})


def _write_failure_observation(runtime_root: Path, subject: str) -> None:
    """Leave a terminal observation when the pipeline died outside the bridge.

    The bridge writes the observation only through upstream `update_task`; a
    crash before or between those calls leaves the file absent or "running",
    and the App ledger would wait forever. A terminal state the bridge already
    wrote (success, failure, cancellation) is authoritative and stays.
    """
    path = runtime_root / OBSERVATION_FILE
    revision = 1
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = {}
        if existing.get("status") in _TERMINAL_STATUSES:
            return
        recorded = existing.get("revision")
        if isinstance(recorded, int) and not isinstance(recorded, bool):
            revision = max(revision, recorded + 1)
    import uuid

    _atomic_json(
        path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "renderJobId": runtime_root.parents[2].name,
            "workerTaskId": str(uuid.uuid4()),
            "revision": revision,
            "status": "failed",
            "progressPercent": 0,
            "subject": subject[:OBSERVATION_SUBJECT_LIMIT],
            "outputFile": None,
            "failureCode": "generation_failed",
        },
    )


def start_montage(
    asset_root: Path,
    configuration: ScriptModelConfiguration | None,
    pexels_api_key: str | None,
    request: MontageRequest,
    *,
    pipeline: Callable[[str, object, str], object] | None = None,
) -> MontageThread:
    """Run one montage job on a daemon thread and return that thread.

    The private runtime layout is the WebUI's exactly — the observation bridge
    derives the render-job id from `runtime_root.parents[2]`, so keeping the
    depth identical is what keeps the App-owned ledger working unchanged.
    `pipeline` exists for tests; production always drives the packaged
    upstream `app.services.task.start`.
    """
    from webui_runtime import (
        WebUiRejected,
        _preload_private_config,
        _prepare_shared_runtime,
        default_subtitle_font_name,
    )

    runtime_parent = asset_root / ".automation-tool-montage"
    runtime_parent.mkdir(mode=0o700, exist_ok=True)
    if runtime_parent.is_symlink() or not runtime_parent.is_dir():
        raise WebUiRejected("invalid runtime root")
    import secrets

    runtime_root = runtime_parent / secrets.token_urlsafe(32)
    runtime_root.mkdir(mode=0o700)
    output_root = asset_root.parent / "outputs"
    if output_root.is_symlink() or not output_root.is_dir():
        raise WebUiRejected("invalid output root")
    _preload_private_config(runtime_root, pexels_api_key)
    # Before the thread, not inside it: a runtime that cannot be laid out must
    # fail the submission synchronously, where worker_main can still refuse the
    # job, instead of dying on a daemon thread after "submitted" was reported.
    _prepare_shared_runtime(runtime_root)
    if configuration is not None:
        install_script_model(configuration)

    def upstream_engine() -> tuple[Callable[..., object], Callable[..., object]]:
        """Wire the packaged upstream exactly the way the WebUI wired it.

        Imported lazily: these modules exist only in the frozen worker
        environment (upstream's own dependencies travel with the package),
        which is also why the unit tests exercise the mapping through the
        `pipeline` seam and the full pipeline is packaged-worker evidence.
        """
        from app.models.schema import VideoParams
        from app.services import llm
        from app.services import state as state_module
        from app.services import task as task_module
        from app.utils import utils
        from job_observation_bridge import install_job_observation_bridge
        from model_service_adapter import generate_script

        llm._generate_response = generate_script
        utils.root_dir = lambda: str(runtime_root)
        install_job_observation_bridge(state_module, runtime_root, output_root)
        return task_module.start, VideoParams

    def run() -> None:
        import uuid

        try:
            if pipeline is None:
                engine, params_model = upstream_engine()
            else:
                engine, params_model = pipeline, _CapturedParams
            params = params_model(
                video_subject=request.subject,
                video_script=request.script or "",
                # ASPECTS already validated membership; upstream VideoAspect
                # uses the same literal strings.
                video_aspect=request.aspect,
                video_clip_duration=request.clip_duration_seconds,
                video_count=1,
                video_source="pexels",
                voice_name=request.voice_name,
                # 本次发行不随包提供任何背景音乐素材（与 WebUI 侧的移除一致）。
                bgm_type="",
                subtitle_enabled=request.subtitle_enabled,
                # Pinned explicitly: upstream's default is STHeitiMedium.ttc,
                # which the shipped package deliberately excludes (rights not
                # cleared) — subtitle burn-in died on the missing file the
                # first time a keyed montage reached it (2026-08-06).
                font_name=default_subtitle_font_name(),
                font_size=request.font_size_px,
                text_color=request.text_color,
                stroke_color=request.stroke_color,
                stroke_width=request.stroke_width_px,
            )
            engine(str(uuid.uuid4()), params, "video")
        except JobCancelled:
            # The bridge already wrote "cancelled" before raising; a user
            # action is a clean process exit, not a worker failure.
            raise
        except BaseException:
            thread.failed = True
            _write_failure_observation(runtime_root, request.subject)
            raise  # threading's excepthook prints the traceback for diagnosis

    thread = MontageThread(target=run, name="material-montage", daemon=True)
    thread.start()
    return thread


class _CapturedParams:
    """Test-seam stand-in for upstream `VideoParams`: records the mapping."""

    def __init__(self, **kwargs: object) -> None:
        for name, value in kwargs.items():
            setattr(self, name, value)


__all__ = [
    "MAX_SCRIPT_CHARACTERS",
    "MAX_SUBJECT_CHARACTERS",
    "MontageRejected",
    "MontageRequest",
    "MontageThread",
    "parse_montage_request",
    "start_montage",
]
