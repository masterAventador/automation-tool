"""Turn one beat's narration into an audio file, and say where it landed.

Why this exists at all
----------------------
A shot lasts `max(narration, animation)` (roadmap P0-1d), so the film's timeline
cannot be laid out until the narration's real length is known. The gateway does
not return one, so the audio is written into the RenderJob workspace and
measured there — see `measure_audio_seconds`, which asks the same `ffprobe` the
render path uses rather than deriving a second opinion from the WAV header.

Measured 2026-07-27 against the real service, three lengths back to back:

    10 characters -> 2.160 s     35 -> 6.080 s     72 -> 14.000 s

Close enough to linear (~0.19 s per character) to be worth handing the model as
a budget so it writes narration that roughly fits; nowhere near exact enough to
lay a timeline on.

Why the answer's URL is not simply fetched
------------------------------------------
TTS is not on the OpenAI-compatible endpoint — measured, `/audio/speech` there
answers 404 — so this speaks the DashScope-native endpoint, which replies with a
signed, expiring object-store link rather than bytes. It hands that link over as
plain `http://`, and the same host serves the same object over TLS (both
measured), so the scheme is upgraded before the fetch instead of the audio being
pulled in the clear.

This is the only place in the authoring path that fetches a URL a remote service
chose, so the host is checked against a list declared in the model catalog
beside the endpoint itself. Everything else — other schemes, a look-alike host
like `aliyuncs.com.evil.example`, a scheme-relative `//host/path` — is refused
rather than normalised into something fetchable.

(The docstrings here stay in English on purpose: `check_user_facing_branding.py`
reads any Chinese-bearing literal in a `.py` source as operator copy, and would
report this file's own notes as unexplained jargon in the product.)
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from automation_tool.executor.motion_authoring.authoring_workspace import (
    AuthoringWorkspace,
)

# A minute of 24 kHz mono PCM is about 2.9 MB; the observed 14 s clip was
# 672 KB. This bounds a single beat, not the film, and exists so a wrong answer
# cannot fill the user's disk before anything else notices.
MAX_VOICEOVER_BYTES: Final = 32 * 1024 * 1024
MAX_NARRATION_CHARS: Final = 1_000
VOICEOVER_TIMEOUT_SECONDS: Final = 120

_API_KEY: Final = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_DASHSCOPE_TTS_PATH: Final = "/services/aigc/multimodal-generation/generation"


class VoiceoverRejected(RuntimeError):
    """A boundary of the voiceover path was violated."""


def _reject(message: str) -> None:
    raise VoiceoverRejected(f"voiceover rejected: {message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        _reject(message)


@dataclass(frozen=True)
class VoiceoverConfig:
    base_url: str
    model_id: str
    api_key: str = field(repr=False)
    voice: str
    audio_host_suffixes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(
            type(self.base_url) is str and self.base_url.startswith("https://"),
            "base url must be https",
        )
        _require(type(self.model_id) is str and bool(self.model_id), "model id required")
        _require(
            type(self.api_key) is str and _API_KEY.fullmatch(self.api_key) is not None,
            "api key is malformed",
        )
        _require(type(self.voice) is str and bool(self.voice), "voice required")
        _require(
            isinstance(self.audio_host_suffixes, tuple)
            and bool(self.audio_host_suffixes)
            and all(
                type(suffix) is str and suffix.startswith(".")
                for suffix in self.audio_host_suffixes
            ),
            "audio host suffixes must be a non-empty tuple of dotted suffixes",
        )


@dataclass(frozen=True)
class SynthesizedVoiceover:
    relative_path: str
    bytes_written: int


def resolve_audio_url(url: object, *, allowed_suffixes: Sequence[str]) -> str:
    """Return the URL this process is willing to fetch, or fail closed.

    The service answers with `http://`; the same object is served over TLS, so
    the scheme is upgraded rather than the audio being fetched in the clear. The
    host must *end with* one of the declared suffixes and the suffix must be a
    label boundary, which is what separates `x.aliyuncs.com` from the
    look-alike `aliyuncs.com.evil.example`.
    """
    if type(url) is not str or not url:
        _reject("audio url must be a non-empty string")
        raise AssertionError  # pragma: no cover
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        _reject(f"audio url scheme is not fetchable: {parsed.scheme or 'none'}")
    if not parsed.hostname:
        _reject("audio url has no host")
    host = (parsed.hostname or "").lower()
    if not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in allowed_suffixes):
        _reject("audio url host is not declared in the model catalog")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _post_json(url: str, body: bytes, headers: dict[str, str], timeout: int) -> bytes:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except OSError as error:
        # Never surface the key or the upstream body; keep the reason bounded,
        # and keep "did not answer" distinct from "answered something wrong".
        if isinstance(error, TimeoutError) or isinstance(
            getattr(error, "reason", None), TimeoutError
        ):
            raise VoiceoverRejected("voiceover rejected: voice model timed out") from error
        raise VoiceoverRejected("voiceover rejected: voice model transport failed") from error


def _get_bytes(url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(MAX_VOICEOVER_BYTES + 1)
    except OSError as error:
        raise VoiceoverRejected("voiceover rejected: audio download failed") from error


def synthesize_voiceover(
    config: VoiceoverConfig,
    narration: str,
    *,
    workspace: AuthoringWorkspace,
    relative_path: str,
    post: Callable[[str, bytes, dict[str, str], int], bytes] = _post_json,
    fetch: Callable[[str, int], bytes] = _get_bytes,
    timeout_seconds: int = VOICEOVER_TIMEOUT_SECONDS,
) -> SynthesizedVoiceover:
    """Synthesise one beat's narration into the RenderJob workspace."""
    if not isinstance(config, VoiceoverConfig):
        _reject("config must be a VoiceoverConfig")
    if not isinstance(workspace, AuthoringWorkspace):
        _reject("a workspace is required")
    _require(
        type(narration) is str and bool(narration.strip()),
        "narration must be a non-empty string",
    )
    _require(len(narration) <= MAX_NARRATION_CHARS, "narration is out of range")

    body = json.dumps(
        {
            "model": config.model_id,
            "input": {"text": narration, "voice": config.voice},
            "parameters": {"language_type": "Chinese"},
        }
    ).encode("utf-8")
    raw = post(
        f"{config.base_url}{_DASHSCOPE_TTS_PATH}",
        body,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        timeout_seconds,
    )
    try:
        answer: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise VoiceoverRejected("voiceover rejected: voice model answer was not JSON") from error
    if not isinstance(answer, dict):
        _reject("voice model answer must be a JSON object")
    audio = answer.get("output", {})
    audio = audio.get("audio", {}) if isinstance(audio, dict) else {}
    url = resolve_audio_url(
        audio.get("url") if isinstance(audio, dict) else None,
        allowed_suffixes=config.audio_host_suffixes,
    )

    payload = fetch(url, timeout_seconds)
    _require(isinstance(payload, bytes) and bool(payload), "audio download was empty")
    _require(len(payload) <= MAX_VOICEOVER_BYTES, "audio exceeded its byte budget")

    workspace.write_bytes(relative_path, payload)
    return SynthesizedVoiceover(relative_path=relative_path, bytes_written=len(payload))


def measure_audio_seconds(path: Path, *, ffprobe: Path) -> float:
    """Ask the render toolchain how long this is — not the WAV header.

    The number decides shot lengths, and the thing that will eventually mux the
    audio is ffmpeg. Deriving it here from the header would be a second opinion
    that can disagree with the muxer, and the disagreement would show up as
    narration running past its shot with nothing raising an error.
    """
    if not path.is_file() or path.is_symlink():
        _reject("audio to measure must be a regular file")
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        _reject("ffprobe could not read the synthesized audio")
    try:
        seconds = float(json.loads(completed.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise VoiceoverRejected(
            "voiceover rejected: ffprobe reported no usable duration"
        ) from error
    _require(seconds > 0.0, "measured duration must be positive")
    return seconds


def load_voiceover_config(*, catalog_path: Path, secret_path: Path) -> VoiceoverConfig | None:
    """Build the config from the catalog and the secret, or None when unset.

    The purpose carries its own `base_url` and `api_mode` because TTS is not on
    the OpenAI-compatible endpoint the rest of the catalog names — measured,
    `/audio/speech` there answers 404 — and the hosts its audio may be fetched
    from are declared beside them rather than being a literal in this file.
    """
    if secret_path.is_symlink() or not secret_path.is_file():
        return None
    try:
        secret = json.loads(secret_path.read_text(encoding="utf-8"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VoiceoverRejected(
            "voiceover rejected: model catalog or secret is unreadable"
        ) from error
    if not isinstance(secret, dict) or not isinstance(catalog, dict):
        _reject("config shape invalid")
    api_key = secret.get("apiKey")
    if not isinstance(api_key, str):
        return None
    return voiceover_config_from_catalog(catalog_path=catalog_path, api_key=api_key)


def voiceover_config_from_catalog(*, catalog_path: Path, api_key: str) -> VoiceoverConfig:
    """The config from the packaged catalog contract and a key already in hand.

    The Executor's authoring child has no secret file — the key arrives on
    stdin with the authoring request (PC-26) — so the catalog parsing stands
    on its own here and `load_voiceover_config` delegates to it. One parser,
    two entry points, never a second copy of what the contract means.
    """
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VoiceoverRejected(
            "voiceover rejected: model catalog or secret is unreadable"
        ) from error
    if not isinstance(catalog, dict):
        _reject("config shape invalid")
    purposes = catalog.get("purposes")
    _require(isinstance(purposes, list), "catalog purposes missing")
    purpose = next(
        (item for item in purposes if isinstance(item, dict) and item.get("id") == "voiceover"),
        None,
    )
    _require(isinstance(purpose, dict), "voiceover purpose missing")
    assert isinstance(purpose, dict)  # narrowed by the check above
    _require(purpose.get("api_mode") == "dashscope_native", "voiceover api mode drifted")
    suffixes = purpose.get("audio_host_suffixes")
    _require(
        isinstance(suffixes, list) and bool(suffixes),
        "voiceover audio host suffixes missing",
    )
    return VoiceoverConfig(
        base_url=purpose.get("base_url", ""),
        model_id=purpose.get("default_model_id", ""),
        api_key=api_key,
        voice=purpose.get("default_voice", "Cherry"),
        audio_host_suffixes=tuple(suffixes),
    )


__all__ = [
    "MAX_NARRATION_CHARS",
    "MAX_VOICEOVER_BYTES",
    "VOICEOVER_TIMEOUT_SECONDS",
    "SynthesizedVoiceover",
    "VoiceoverConfig",
    "VoiceoverRejected",
    "load_voiceover_config",
    "measure_audio_seconds",
    "resolve_audio_url",
    "synthesize_voiceover",
    "voiceover_config_from_catalog",
]
