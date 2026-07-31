"""Authenticated stdin bootstrap for the packaged local-editing Worker."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Never

from automation_tool.executor.material_probe import (
    MAX_PATH_CHARACTERS,
    MaterialProbeRejected,
    PackagedMediaTools,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES = 16 * 1024
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")
_ROOT_KEYS = frozenset(
    {
        "assetRoot",
        "bootstrapVersion",
        "enableWebUi",
        "localSessionToken",
        "mediaTools",
        "protocolVersion",
        "renderBrowser",
        "scriptModel",
        "workerKind",
    }
)
_MEDIA_TOOL_KEYS = frozenset({"ffmpegPath", "ffprobePath"})


class LocalEditingWorkerBootstrapRejected(ValueError):
    """The Worker bootstrap is malformed or names unverified local tools."""

    def __init__(self) -> None:
        super().__init__("local editing worker bootstrap rejected")


def _reject() -> Never:
    raise LocalEditingWorkerBootstrapRejected from None


def _load_object(payload: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                _reject()
            document[key] = value
        return document

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject()
    if not isinstance(value, dict):
        _reject()
    return value


def _asset_root(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_PATH_CHARACTERS
        or contains_control_or_bidi(value)
    ):
        _reject()
    path = Path(value)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            _reject()
        if any(ancestor.is_symlink() for ancestor in path.parents):
            _reject()
        return path.resolve(strict=True)
    except OSError:
        _reject()


def _media_tools(value: object) -> PackagedMediaTools:
    if not isinstance(value, dict) or set(value) != _MEDIA_TOOL_KEYS:
        _reject()
    ffmpeg = value.get("ffmpegPath")
    ffprobe = value.get("ffprobePath")
    if not isinstance(ffmpeg, str) or not isinstance(ffprobe, str) or ffmpeg == ffprobe:
        _reject()
    try:
        return PackagedMediaTools(
            ffprobe_path=Path(ffprobe),
            ffmpeg_path=Path(ffmpeg),
        )
    except MaterialProbeRejected:
        _reject()


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingWorkerBootstrap:
    asset_root: Path = field(repr=False)
    media_tools: PackagedMediaTools = field(repr=False)
    _session_token: bytes = field(repr=False)

    def session_token_bytes(self) -> bytes:
        return self._session_token

    def __repr__(self) -> str:
        return "LocalEditingWorkerBootstrap(<redacted>)"


def parse_local_editing_worker_bootstrap(
    payload: bytes,
) -> LocalEditingWorkerBootstrap:
    """Parse the sole Rust-issued bootstrap without consulting process state."""

    if (
        not isinstance(payload, bytes)
        or not 1 < len(payload) <= MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
    ):
        _reject()
    document = _load_object(payload[:-1])
    token = document.get("localSessionToken")
    if (
        set(document) != _ROOT_KEYS
        or document.get("bootstrapVersion") != "1"
        or document.get("protocolVersion") != "1.0"
        or document.get("workerKind") != "python"
        or document.get("enableWebUi") is not False
        or document.get("renderBrowser") is not None
        or document.get("scriptModel") is not None
        or not isinstance(token, str)
        or _TOKEN_PATTERN.fullmatch(token) is None
    ):
        _reject()
    return LocalEditingWorkerBootstrap(
        asset_root=_asset_root(document.get("assetRoot")),
        media_tools=_media_tools(document.get("mediaTools")),
        _session_token=bytes.fromhex(token),
    )


__all__ = [
    "MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES",
    "LocalEditingWorkerBootstrap",
    "LocalEditingWorkerBootstrapRejected",
    "parse_local_editing_worker_bootstrap",
]
