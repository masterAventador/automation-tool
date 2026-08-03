"""LE-15 T2: synthesize every script sentence and measure the real audio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))

from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor import script_voiceover as script_voiceover_module  # noqa: E402
from automation_tool.executor.material_probe import (  # noqa: E402
    MAX_MATERIAL_DURATION_MS,
    MaterialProbeRejected,
    MaterialProbeRejection,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
)
from automation_tool.executor.motion_authoring import voiceover as voiceover_module  # noqa: E402
from automation_tool.executor.motion_authoring.agent import AuthoringWorkspace  # noqa: E402
from automation_tool.executor.motion_authoring.voiceover import (  # noqa: E402
    MAX_VOICEOVER_BYTES,
    SynthesizedVoiceover,
    VoiceoverConfig,
    VoiceoverRejected,
)
from automation_tool.executor.script_segmentation import (  # noqa: E402
    ScriptSegmentationResult,
    ScriptSentence,
)
from automation_tool.executor.script_voiceover import (  # noqa: E402
    ScriptVoiceoverCancelled,
    ScriptVoiceoverClip,
    ScriptVoiceoverRejected,
    ScriptVoiceoverResult,
    synthesize_script_voiceovers,
)

API_KEY = "sk-private-script-voiceover-key-123456"


def _config() -> VoiceoverConfig:
    return VoiceoverConfig(
        base_url="https://dashscope.aliyuncs.com/api/v1",
        model_id="qwen3-tts-instruct-flash-2026-01-26",
        api_key=API_KEY,
        voice="Cherry",
        audio_host_suffixes=(".aliyuncs.com",),
    )


def _script(*sentences: str) -> ScriptSegmentationResult:
    return ScriptSegmentationResult(
        request_id="req-script-voiceover-001",
        sentences=tuple(
            ScriptSentence(sequence=sequence, text=text)
            for sequence, text in enumerate(sentences, start=1)
        ),
    )


def _workspace(root: Path) -> AuthoringWorkspace:
    root.mkdir()
    return AuthoringWorkspace(root)


def _stub_tools(root: Path) -> PackagedMediaTools:
    suffix = ".cmd" if os.name == "nt" else ""
    body = b"@exit /b 0\r\n" if os.name == "nt" else b"#!/bin/sh\nexit 0\n"
    ffprobe = root / f"ffprobe{suffix}"
    ffmpeg = root / f"ffmpeg{suffix}"
    for tool in (ffprobe, ffmpeg):
        tool.write_bytes(body)
        tool.chmod(0o755)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _facts(
    duration_ms: int | None,
    *,
    kind: ProbedMaterialKind = ProbedMaterialKind.AUDIO,
) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=kind,
        duration_ms=duration_ms,
        width=None,
        height=None,
        video_codec=None,
        audio_codec="pcm_s16le",
    )


class _Synthesizer:
    def __init__(self, *, fail_on_call: int | None = None, payload: bytes = b"audio") -> None:
        self.fail_on_call = fail_on_call
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        config: VoiceoverConfig,
        narration: str,
        *,
        workspace: AuthoringWorkspace,
        relative_path: str,
    ) -> SynthesizedVoiceover:
        assert config == _config()
        self.calls.append((narration, relative_path))
        if len(self.calls) == self.fail_on_call:
            raise VoiceoverRejected("private upstream detail: /Users/operator/audio.wav")
        payload = self.payload + str(len(self.calls)).encode("ascii")
        workspace.write_bytes(relative_path, payload)
        return SynthesizedVoiceover(
            relative_path=relative_path,
            bytes_written=len(payload),
        )


def _install_probe(
    monkeypatch: pytest.MonkeyPatch,
    facts: list[MediaStreamFacts | BaseException],
) -> list[Path]:
    calls: list[Path] = []
    remaining = iter(facts)

    def read(
        tools: PackagedMediaTools,
        source: Path,
    ) -> MediaStreamFacts:
        assert isinstance(tools, PackagedMediaTools)
        calls.append(source)
        answer = next(remaining)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(script_voiceover_module, "read_stream_facts", read)
    return calls


def test_each_sentence_is_synthesized_once_and_keeps_its_real_probe_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    tools = _stub_tools(tmp_path)
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    probe_calls = _install_probe(monkeypatch, [_facts(1237), _facts(2341)])
    script = _script("第一句。", "第二句。")

    result = synthesize_script_voiceovers(
        script,
        config=_config(),
        workspace=workspace,
        tools=tools,
    )

    assert result.script_request_id == script.request_id
    assert tuple(clip.sentence for clip in result.clips) == script.sentences
    assert tuple(clip.relative_path for clip in result.clips) == (
        "voiceover/sentence-0001.wav",
        "voiceover/sentence-0002.wav",
    )
    assert tuple(clip.duration_ms for clip in result.clips) == (1237, 2341)
    assert synthesizer.calls == [
        ("第一句。", "voiceover/sentence-0001.wav"),
        ("第二句。", "voiceover/sentence-0002.wav"),
    ]
    assert probe_calls == [
        workspace.root / "voiceover/sentence-0001.wav",
        workspace.root / "voiceover/sentence-0002.wav",
    ]
    assert all(path.is_file() for path in probe_calls)


def test_a_second_sentence_tts_failure_is_not_retried_and_rolls_back_the_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    synthesizer = _Synthesizer(fail_on_call=2)
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    _install_probe(monkeypatch, [_facts(1237)])

    with pytest.raises(
        ScriptVoiceoverRejected,
        match=r"^script voiceover request rejected$",
    ) as raised:
        synthesize_script_voiceovers(
            _script("第一句。", "第二句。", "不会调用的第三句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert [narration for narration, _ in synthesizer.calls] == ["第一句。", "第二句。"]
    assert list(workspace.root.rglob("*.wav")) == []


def test_cancellation_between_sentences_rolls_back_without_calling_next_tts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    synthesizer = _Synthesizer()
    monkeypatch.setattr(script_voiceover_module, "synthesize_voiceover", synthesizer)
    _install_probe(monkeypatch, [_facts(1_237)])
    polls = 0

    def cancelled() -> bool:
        nonlocal polls
        polls += 1
        return polls >= 2

    with pytest.raises(
        ScriptVoiceoverCancelled,
        match=r"^script voiceover cancelled$",
    ) as raised:
        synthesize_script_voiceovers(
            _script("第一句。", "不得调用的第二句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
            cancellation_requested=cancelled,
        )

    assert raised.value.__cause__ is None
    assert [narration for narration, _ in synthesizer.calls] == ["第一句。"]
    assert list(workspace.root.rglob("*.wav")) == []


def test_a_tts_timeout_is_not_retried_and_leaves_a_fixed_empty_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    calls: list[str] = []

    def time_out(
        _config: VoiceoverConfig,
        narration: str,
        *,
        workspace: AuthoringWorkspace,
        relative_path: str,
    ) -> SynthesizedVoiceover:
        del workspace, relative_path
        calls.append(narration)
        raise TimeoutError("/Users/operator/private-tts-request")

    monkeypatch.setattr(script_voiceover_module, "synthesize_voiceover", time_out)

    with pytest.raises(
        ScriptVoiceoverRejected,
        match=r"^script voiceover request rejected$",
    ) as raised:
        synthesize_script_voiceovers(
            _script("超时的第一句。", "不能重试的第二句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert calls == ["超时的第一句。"]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert list(workspace.root.rglob("*.wav")) == []


def test_an_empty_tts_download_never_reaches_ffprobe_or_leaves_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    network_calls: list[str] = []

    def return_empty_audio(
        config: VoiceoverConfig,
        narration: str,
        *,
        workspace: AuthoringWorkspace,
        relative_path: str,
    ) -> SynthesizedVoiceover:
        def post(
            _url: str,
            _body: bytes,
            _headers: dict[str, str],
            _timeout: int,
        ) -> bytes:
            network_calls.append("post")
            return json.dumps(
                {"output": {"audio": {"url": "http://x.oss-cn-beijing.aliyuncs.com/a.wav"}}}
            ).encode("utf-8")

        def fetch(_url: str, _timeout: int) -> bytes:
            network_calls.append("fetch")
            return b""

        return voiceover_module.synthesize_voiceover(
            config,
            narration,
            workspace=workspace,
            relative_path=relative_path,
            post=post,
            fetch=fetch,
        )

    def probe_must_not_run(
        _tools: PackagedMediaTools,
        _source: Path,
    ) -> MediaStreamFacts:
        pytest.fail("ffprobe must not run for an empty TTS download")

    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        return_empty_audio,
    )
    monkeypatch.setattr(
        script_voiceover_module,
        "read_stream_facts",
        probe_must_not_run,
    )

    with pytest.raises(
        ScriptVoiceoverRejected,
        match=r"^script voiceover request rejected$",
    ) as raised:
        synthesize_script_voiceovers(
            _script("空音频必须失败。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert network_calls == ["post", "fetch"]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert list(workspace.root.rglob("*.wav")) == []


@pytest.mark.parametrize("duration_ms", [None, 0])
def test_a_missing_or_zero_real_duration_rolls_back_the_single_tts_call(
    duration_ms: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    _install_probe(monkeypatch, [_facts(duration_ms)])

    with pytest.raises(
        ScriptVoiceoverRejected,
        match=r"^script voiceover request rejected$",
    ) as raised:
        synthesize_script_voiceovers(
            _script("真实时长必须大于零。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert synthesizer.calls == [("真实时长必须大于零。", "voiceover/sentence-0001.wav")]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert list(workspace.root.rglob("*.wav")) == []


def test_a_probe_failure_rolls_back_every_audio_already_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    _install_probe(
        monkeypatch,
        [
            _facts(1237),
            MaterialProbeRejected(MaterialProbeRejection.PROBE_FAILED),
        ],
    )

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。", "第二句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert len(synthesizer.calls) == 2
    assert list(workspace.root.rglob("*.wav")) == []


@pytest.mark.parametrize(
    ("kind", "duration_ms"),
    [
        (ProbedMaterialKind.VIDEO, 1237),
        (ProbedMaterialKind.AUDIO, None),
        (ProbedMaterialKind.AUDIO, 0),
    ],
)
def test_a_non_audio_or_non_positive_probe_result_is_rejected(
    kind: ProbedMaterialKind,
    duration_ms: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        _Synthesizer(),
    )
    _install_probe(monkeypatch, [_facts(duration_ms, kind=kind)])

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert list(workspace.root.rglob("*.wav")) == []


def test_an_existing_fixed_output_is_preserved_before_any_tts_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    existing = root / "voiceover/sentence-0002.wav"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing operator data")
    workspace = AuthoringWorkspace(root)
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )

    def probe(_tools: PackagedMediaTools, _source: Path) -> MediaStreamFacts:
        pytest.fail("probe must not run when the output already exists")

    monkeypatch.setattr(script_voiceover_module, "read_stream_facts", probe)

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。", "已有输出的第二句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert synthesizer.calls == []
    assert existing.read_bytes() == b"existing operator data"


def test_a_rejected_retry_preserves_the_previously_successful_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    _install_probe(monkeypatch, [_facts(1237)])
    script = _script("第一句。")
    tools = _stub_tools(tmp_path)

    first = synthesize_script_voiceovers(
        script,
        config=_config(),
        workspace=workspace,
        tools=tools,
    )
    output = workspace.root / first.clips[0].relative_path
    original_bytes = output.read_bytes()

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            script,
            config=_config(),
            workspace=workspace,
            tools=tools,
        )

    assert synthesizer.calls == [("第一句。", "voiceover/sentence-0001.wav")]
    assert output.read_bytes() == original_bytes


def test_a_failed_batch_preserves_files_authored_before_the_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    previous = workspace.write_bytes("previous-result.bin", b"keep previous result")
    synthesizer = _Synthesizer(fail_on_call=2)
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    _install_probe(monkeypatch, [_facts(1237)])

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。", "第二句失败。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert previous.read_bytes() == b"keep previous result"
    assert list(workspace.root.rglob("*.wav")) == []


def test_a_local_write_failure_preserves_files_authored_before_the_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    previous = workspace.write_bytes("previous-result.bin", b"keep previous result")
    original_write_bytes = Path.write_bytes

    def short_write_then_fail(path: Path, payload: bytes) -> int:
        if path.name == "sentence-0001.wav":
            original_write_bytes(path, payload[:1])
            raise OSError(f"private path: {path}")
        return original_write_bytes(path, payload)

    monkeypatch.setattr(Path, "write_bytes", short_write_then_fail)
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        _Synthesizer(),
    )

    with pytest.raises(ScriptVoiceoverRejected) as raised:
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert previous.read_bytes() == b"keep previous result"
    assert list(workspace.root.rglob("*.wav")) == []


def test_an_unusable_output_parent_is_rejected_before_any_tts_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    blocked_parent = root / "voiceover"
    blocked_parent.write_bytes(b"not a directory")
    workspace = AuthoringWorkspace(root)
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert synthesizer.calls == []
    assert blocked_parent.read_bytes() == b"not a directory"


def test_a_missing_written_audio_is_rejected_without_a_private_path_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")

    def report_without_writing(
        _config: VoiceoverConfig,
        _narration: str,
        *,
        workspace: AuthoringWorkspace,
        relative_path: str,
    ) -> SynthesizedVoiceover:
        del workspace
        return SynthesizedVoiceover(relative_path=relative_path, bytes_written=1)

    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        report_without_writing,
    )

    with pytest.raises(ScriptVoiceoverRejected) as raised:
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_an_unreadable_output_parent_is_rejected_without_a_private_path_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    output_parent = workspace.root / "voiceover"
    original_lstat = Path.lstat

    def reject_output_parent(path: Path) -> os.stat_result:
        if path == output_parent:
            raise PermissionError(f"private path: {path}")
        return original_lstat(path)

    synthesizer = _Synthesizer()
    monkeypatch.setattr(Path, "lstat", reject_output_parent)
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )

    with pytest.raises(ScriptVoiceoverRejected) as raised:
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert synthesizer.calls == []


def test_an_invalid_synthesizer_reply_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )


def test_written_audio_must_match_the_synthesizer_byte_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")

    def report_a_different_size(
        _config: VoiceoverConfig,
        _narration: str,
        *,
        workspace: AuthoringWorkspace,
        relative_path: str,
    ) -> SynthesizedVoiceover:
        workspace.write_bytes(relative_path, b"x")
        return SynthesizedVoiceover(relative_path=relative_path, bytes_written=2)

    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        report_a_different_size,
    )

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=_stub_tools(tmp_path),
        )

    assert list(workspace.root.rglob("*.wav")) == []


def test_result_and_public_input_types_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(ScriptVoiceoverRejected):
        ScriptVoiceoverResult(script_request_id="", clips=())

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            object(),  # type: ignore[arg-type]
            config=_config(),
            workspace=_workspace(tmp_path / "workspace"),
            tools=_stub_tools(tmp_path),
        )


def test_packaged_tools_are_revalidated_before_any_tts_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    tools = _stub_tools(tmp_path)
    tools.ffprobe_path.unlink()
    synthesizer = _Synthesizer()
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )

    with pytest.raises(ScriptVoiceoverRejected):
        synthesize_script_voiceovers(
            _script("第一句。"),
            config=_config(),
            workspace=workspace,
            tools=tools,
        )

    assert synthesizer.calls == []
    assert list(workspace.root.rglob("*.wav")) == []


@pytest.mark.parametrize(
    ("duration_ms", "accepted"),
    [
        (0, False),
        (1, True),
        (MAX_MATERIAL_DURATION_MS, True),
        (MAX_MATERIAL_DURATION_MS + 1, False),
    ],
)
def test_clip_duration_uses_all_four_interval_edges(
    duration_ms: int,
    accepted: bool,
) -> None:
    def construct() -> ScriptVoiceoverClip:
        return ScriptVoiceoverClip(
            sentence=ScriptSentence(sequence=1, text="一句。"),
            relative_path="voiceover/sentence-0001.wav",
            duration_ms=duration_ms,
            bytes_written=1,
        )

    if accepted:
        assert construct().duration_ms == duration_ms
    else:
        with pytest.raises(ScriptVoiceoverRejected):
            construct()


@pytest.mark.parametrize(
    ("bytes_written", "accepted"),
    [
        (0, False),
        (1, True),
        (MAX_VOICEOVER_BYTES, True),
        (MAX_VOICEOVER_BYTES + 1, False),
    ],
)
def test_clip_bytes_use_all_four_interval_edges(
    bytes_written: int,
    accepted: bool,
) -> None:
    def construct() -> ScriptVoiceoverClip:
        return ScriptVoiceoverClip(
            sentence=ScriptSentence(sequence=1, text="一句。"),
            relative_path="voiceover/sentence-0001.wav",
            duration_ms=1,
            bytes_written=bytes_written,
        )

    if accepted:
        assert construct().bytes_written == bytes_written
    else:
        with pytest.raises(ScriptVoiceoverRejected):
            construct()


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = cache_root() / "media-toolchain/bin"
    ffprobe = root / f"ffprobe{suffix}"
    ffmpeg = root / f"ffmpeg{suffix}"
    if not (ffprobe.exists() and ffmpeg.exists()):
        raise AssertionError(
            "packaged media toolchain missing; run scripts/prepare_video_runtime.py"
        )
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def test_packaged_ffprobe_measures_an_off_grid_synthesized_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _packaged_tools()
    source = tmp_path / "off-grid-source.wav"
    subprocess.run(
        [
            os.fspath(tools.ffmpeg_path),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=997:sample_rate=48000",
            "-t",
            "1.237",
            "-c:a",
            "pcm_s16le",
            os.fspath(source),
        ],
        check=True,
        capture_output=True,
    )
    payload = source.read_bytes()
    synthesizer = _Synthesizer(payload=payload[:-1])
    monkeypatch.setattr(
        script_voiceover_module,
        "synthesize_voiceover",
        synthesizer,
    )
    workspace = _workspace(tmp_path / "workspace")

    result = synthesize_script_voiceovers(
        _script("真实时长只听随包探针。"),
        config=_config(),
        workspace=workspace,
        tools=tools,
    )

    assert result.clips[0].duration_ms == 1237
    assert result.clips[0].bytes_written == len(payload)


def test_a_cancellation_probe_that_cannot_be_trusted_is_not_read_as_carry_on() -> None:
    """An unusable answer stops the run rather than being taken as "keep going"."""
    from automation_tool.executor.script_voiceover import ScriptVoiceoverRejected

    def raising() -> bool:
        raise RuntimeError("probe defect")

    for label, probe in [
        ("the probe raises", raising),
        ("the probe answers with an int", lambda: 1),
        ("the probe answers with nothing", lambda: None),
    ]:
        with pytest.raises(ScriptVoiceoverRejected):
            script_voiceover_module._cancel_if_requested(probe)  # type: ignore[arg-type]
        assert label
