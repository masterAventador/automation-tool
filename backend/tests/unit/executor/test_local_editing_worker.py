"""LE-12 T1: authenticated local-editing Worker bootstrap."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from automation_tool.executor.local_editing_worker import (
    LocalEditingWorkerBootstrapRejected,
    parse_local_editing_worker_bootstrap,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def _document(tmp_path: Path) -> dict[str, object]:
    return {
        "assetRoot": str(tmp_path),
        "bootstrapVersion": "1",
        "enableWebUi": False,
        "localSessionToken": "ab" * 32,
        "mediaTools": {
            "ffmpegPath": str(_executable(tmp_path, "ffmpeg")),
            "ffprobePath": str(_executable(tmp_path, "ffprobe")),
        },
        "pexelsApiKey": None,
        "protocolVersion": "1.0",
        "renderBrowser": None,
        "scriptModel": None,
        "workerKind": "python",
    }


def _payload(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode() + b"\n"


def test_bootstrap_constructs_only_the_exact_packaged_media_pair(tmp_path: Path) -> None:
    document = _document(tmp_path)

    parsed = parse_local_editing_worker_bootstrap(_payload(document))

    assert parsed.asset_root == tmp_path
    assert parsed.media_tools.ffmpeg_path == tmp_path / "ffmpeg"
    assert parsed.media_tools.ffprobe_path == tmp_path / "ffprobe"
    assert parsed.session_token_bytes() == bytes.fromhex("ab" * 32)
    assert repr(parsed) == "LocalEditingWorkerBootstrap(<redacted>)"
    assert str(tmp_path) not in repr(parsed)
    assert "abab" not in repr(parsed)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("mediaTools"),
        lambda value: value.update(extra=True),
        lambda value: value.update(localSessionToken="short"),
        lambda value: value.update(workerKind="node"),
        lambda value: value.update(enableWebUi=True),
        lambda value: value["mediaTools"].update(extra=True),
        lambda value: value["mediaTools"].update(ffmpegPath="relative/ffmpeg"),
        lambda value: value.update(mediaTools=[]),
    ],
)
def test_bootstrap_shape_fails_closed(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], object],
) -> None:
    document = _document(tmp_path)
    mutate(document)

    with pytest.raises(LocalEditingWorkerBootstrapRejected) as error:
        parse_local_editing_worker_bootstrap(_payload(document))

    assert str(error.value) == "local editing worker bootstrap rejected"
    assert str(tmp_path) not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{}",
        b"[]\n",
        b'{"assetRoot":"a","assetRoot":"b"}\n',
        b"{" + b"x" * (16 * 1024) + b"}\n",
        b"\xff\n",
    ],
)
def test_bootstrap_wire_fails_closed(tmp_path: Path, payload: bytes) -> None:
    del tmp_path
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        parse_local_editing_worker_bootstrap(payload)


@pytest.mark.parametrize(
    "asset_root",
    [None, "", "relative", "/does/not/exist", "bad\u202epath", "x" * 4097],
)
def test_bootstrap_rejects_untrusted_asset_roots(
    tmp_path: Path,
    asset_root: object,
) -> None:
    document = _document(tmp_path)
    document["assetRoot"] = asset_root

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        parse_local_editing_worker_bootstrap(_payload(document))


def test_bootstrap_rejects_asset_root_symlinks(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)
    nested = real_root / "nested"
    nested.mkdir()
    ancestor_link = tmp_path / "ancestor-link"
    ancestor_link.symlink_to(real_root, target_is_directory=True)

    for candidate in (root_link, ancestor_link / "nested"):
        document = _document(tmp_path)
        document["assetRoot"] = os.fspath(candidate)
        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            parse_local_editing_worker_bootstrap(_payload(document))


def test_bootstrap_collapses_asset_root_resolution_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(tmp_path)

    def fail_resolve(self: Path, *, strict: bool = False) -> Path:
        del self, strict
        raise OSError("private-path-detail")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(LocalEditingWorkerBootstrapRejected) as error:
        parse_local_editing_worker_bootstrap(_payload(document))

    assert "private-path-detail" not in str(error.value)


@pytest.mark.parametrize("ffmpeg_path", [None, 7])
def test_bootstrap_rejects_non_text_media_paths(
    tmp_path: Path,
    ffmpeg_path: object,
) -> None:
    document = _document(tmp_path)
    media_tools = document["mediaTools"]
    assert isinstance(media_tools, dict)
    media_tools["ffmpegPath"] = ffmpeg_path

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        parse_local_editing_worker_bootstrap(_payload(document))


def test_bootstrap_rejects_one_binary_for_both_media_tools(tmp_path: Path) -> None:
    document = _document(tmp_path)
    media_tools = document["mediaTools"]
    assert isinstance(media_tools, dict)
    media_tools["ffmpegPath"] = media_tools["ffprobePath"]

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        parse_local_editing_worker_bootstrap(_payload(document))
