"""LE-14 T3: the locked Bailian adapter receives audio bytes and nothing else."""

from __future__ import annotations

import base64
import io
import json
import traceback
import urllib.request
import wave
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor import material_speech_transcription as transcription
from automation_tool.executor.material_speech_pipeline import SpeechAudioBatch
from automation_tool.executor.material_speech_transcription import (
    BailianSpeechTranscriptionAdapter,
    BailianSpeechTranscriptionConfig,
    SpeechTranscriptionRejected,
    load_bailian_speech_transcription_config,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
API_KEY = "sk-private-speech-transcription-key-123456789"
PRIVATE_PATH = "/Users/operator/Private Videos/family clip.mp4"


class Headers:
    def __init__(self, length: int | None) -> None:
        self._length = length

    def get_all(self, name: str) -> list[str] | None:
        assert name == "Content-Length"
        return None if self._length is None else [str(self._length)]


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        declared_length: int | None = None,
        status: int = 200,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = Headers(len(body) if declared_length is None else declared_length)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x01\x00" * 1_600)
    return output.getvalue()


def _success_body(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "logprobs": None,
                "message": {
                    "content": "欢迎使用本地音轨转写。",
                    "role": "assistant",
                },
            }
        ],
        "created": 1_785_000_000,
        "id": "chatcmpl-speech-001",
        "model": "qwen3-asr-flash-2026-02-10",
        "object": "chat.completion",
        "system_fingerprint": None,
        "usage": {
            "completion_tokens": 12,
            "completion_tokens_details": {"text_tokens": 12},
            "prompt_tokens": 25,
            "prompt_tokens_details": {"audio_tokens": 25, "text_tokens": 0},
            "seconds": 1,
            "total_tokens": 37,
        },
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


def _adapter() -> BailianSpeechTranscriptionAdapter:
    return BailianSpeechTranscriptionAdapter(
        load_bailian_speech_transcription_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            timeout_seconds=90,
        )
    )


def test_current_bailian_success_shape_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(_success_body()),
    )

    transcript = _adapter().transcribe(SpeechAudioBatch(_wav(), 100))

    assert transcript == "欢迎使用本地音轨转写。"


def test_request_is_one_base64_wav_and_contains_no_video_or_url_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[urllib.request.Request, float]] = []

    def open_request(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> Response:
        calls.append((request, timeout))
        return Response(_success_body())

    monkeypatch.setattr(transcription, "_open_request", open_request)
    wav = _wav()

    transcript = _adapter().transcribe(SpeechAudioBatch(wav_bytes=wav, duration_ms=100))

    assert transcript == "欢迎使用本地音轨转写。"
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert timeout == 90
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    body = json.loads(cast(bytes, request.data))
    assert body == {
        "model": "qwen3-asr-flash-2026-02-10",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": (
                                "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")
                            )
                        },
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": {"enable_itn": True},
    }
    rendered = cast(bytes, request.data)
    assert PRIVATE_PATH.encode() not in rendered
    assert b"video_url" not in rendered
    assert b"file_url" not in rendered
    assert b"http://" not in rendered
    assert b"https://" not in rendered


def test_catalog_locks_the_snapshot_and_private_repr_redacts_the_key() -> None:
    adapter = _adapter()

    assert adapter.config.model_id == "qwen3-asr-flash-2026-02-10"
    assert adapter.config.timeout_seconds == 90
    assert API_KEY not in repr(adapter)
    assert API_KEY not in repr(adapter.config)


def test_non_numeric_timeout_is_a_fixed_redacted_configuration_rejection() -> None:
    with pytest.raises(
        SpeechTranscriptionRejected,
        match=r"^speech transcription request rejected$",
    ) as captured:
        load_bailian_speech_transcription_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            timeout_seconds=cast(float, PRIVATE_PATH),
        )

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert PRIVATE_PATH not in rendered
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "body",
    [
        _success_body(choices=[]),
        _success_body(
            choices=[
                {
                    "finish_reason": "length",
                    "index": 0,
                    "logprobs": None,
                    "message": {
                        "content": "部分结果",
                        "role": "assistant",
                    },
                }
            ]
        ),
        _success_body(
            choices=[
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "logprobs": None,
                    "message": {
                        "content": "",
                        "role": "assistant",
                    },
                }
            ]
        ),
        _success_body(
            choices=[
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "logprobs": {"private": "unexpected"},
                    "message": {
                        "content": "完整结果",
                        "role": "assistant",
                    },
                }
            ]
        ),
        _success_body(system_fingerprint="\0private"),
        _success_body(future=True),
    ],
    ids=[
        "no-choice",
        "incomplete",
        "empty",
        "non-empty-logprobs",
        "invalid-system-fingerprint",
        "open-top-level-shape",
    ],
)
def test_open_or_partial_responses_are_fixed_rejections(
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(body),
    )

    with pytest.raises(
        SpeechTranscriptionRejected,
        match=r"^speech transcription request rejected$",
    ):
        _adapter().transcribe(SpeechAudioBatch(_wav(), 100))


def test_transport_failure_is_redacted_and_keeps_no_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> Response:
        raise OSError(f"{PRIVATE_PATH} {API_KEY} after {timeout}")

    monkeypatch.setattr(transcription, "_open_request", fail)

    with pytest.raises(
        SpeechTranscriptionRejected,
        match=r"^speech transcription request rejected$",
    ) as captured:
        _adapter().transcribe(SpeechAudioBatch(_wav(), 100))

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert PRIVATE_PATH not in rendered
    assert API_KEY not in rendered
    assert captured.value.__context__ is None


def test_declared_or_actual_oversized_response_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(
            _success_body(),
            declared_length=transcription.MAX_RESPONSE_BYTES + 1,
        ),
    )
    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(SpeechAudioBatch(_wav(), 100))

    oversized = b"{" + b" " * transcription.MAX_RESPONSE_BYTES + b"}"
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(oversized),
    )
    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(SpeechAudioBatch(_wav(), 100))


def test_only_http_200_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(_success_body(), status=201),
    )

    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(SpeechAudioBatch(_wav(), 100))


def test_adapter_source_never_reads_a_path_or_builds_a_public_file_url() -> None:
    source = Path(transcription.__file__).read_text(encoding="utf-8")

    assert ".read_bytes(" not in source
    assert "file_urls" not in source
    assert "video_url" not in source
    assert "oss://" not in source
    assert "http://" not in source
    assert "SpeechAudioBatch" in source


def _batch() -> SpeechAudioBatch:
    return SpeechAudioBatch(wav_bytes=_wav(), duration_ms=100)


def test_a_catalog_that_cannot_be_read_or_drifted_is_refused(tmp_path: Path) -> None:
    """One snapshot is declared; anything else is not the model this was reviewed for."""
    document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    real = tmp_path / "catalog.json"
    real.write_text(json.dumps(document), encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(real)
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")

    def written(name: str, changes: dict[str, object]) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps({**document, **changes}), encoding="utf-8")
        return path

    cases: list[tuple[str, Path]] = [
        ("no catalog at all", tmp_path / "absent.json"),
        ("a symlink standing in for it", link),
        ("a catalog that will not parse", malformed),
        ("a document that is not an object", written("list.json", {})),
        ("another schema version", written("schema.json", {"schema_version": 2})),
        ("another provider", written("provider.json", {"provider": "openai"})),
        ("another api mode", written("mode.json", {"api_mode": "responses"})),
        ("another gateway", written("gateway.json", {"base_url": "https://example.invalid"})),
        ("purposes that are not a list", written("purposes.json", {"purposes": {}})),
        ("models that are not a list", written("models.json", {"models": {}})),
        ("no speech purpose at all", written("nopurpose.json", {"purposes": []})),
    ]
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    for label, path in cases:
        with pytest.raises(SpeechTranscriptionRejected):
            load_bailian_speech_transcription_config(
                catalog_path=path, api_key=API_KEY, timeout_seconds=90
            )
        assert label


def test_the_adapter_refuses_a_configuration_or_batch_of_the_wrong_type() -> None:
    with pytest.raises(SpeechTranscriptionRejected):
        BailianSpeechTranscriptionAdapter(cast(BailianSpeechTranscriptionConfig, object()))

    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(cast(SpeechAudioBatch, object()))


def test_a_configuration_outside_the_locked_snapshot_is_refused() -> None:
    """The gateway, the model and the key shape are all pinned by the same object."""
    complete: dict[str, object] = {
        "base_url": transcription.BAILIAN_BASE_URL,
        "model_id": transcription.BAILIAN_ASR_MODEL_ID,
        "api_key": API_KEY,
        "timeout_seconds": 90.0,
    }

    cases: list[tuple[str, dict[str, object]]] = [
        ("another gateway", {"base_url": "https://example.invalid/v1"}),
        ("another model", {"model_id": "whisper-1"}),
        ("a key that is not text", {"api_key": None}),
        ("a key of the wrong shape", {"api_key": "not-a-key"}),
        ("a timeout that is not a number", {"timeout_seconds": "90"}),
        ("a timeout of zero", {"timeout_seconds": 0}),
        ("a timeout past the ceiling", {"timeout_seconds": 91}),
    ]
    for label, overrides in cases:
        with pytest.raises(SpeechTranscriptionRejected):
            BailianSpeechTranscriptionConfig(**{**complete, **overrides})  # type: ignore[arg-type]
        assert label


def test_a_response_declaring_no_length_is_still_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP/1.1 chunked responses carry no Content-Length; that is not an error."""
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(_success_body(), declared_length=None),
    )

    assert _adapter().transcribe(_batch()) == "欢迎使用本地音轨转写。"


def test_a_response_whose_headers_cannot_be_asked_about_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declared length is how truncation is caught, so headers must be readable."""

    class _NoGetAll(Response):
        def __init__(self) -> None:
            super().__init__(_success_body())
            self.headers = cast(Headers, object())

    monkeypatch.setattr(transcription, "_open_request", lambda _request, *, timeout: _NoGetAll())

    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(_batch())


def test_a_content_length_that_is_not_one_plain_number_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two values, a comma-joined pair or non-digits all mean the framing is unclear."""

    class _Headers:
        def __init__(self, values: object) -> None:
            self._values = values

        def get_all(self, _name: str) -> object:
            return self._values

    for label, values in [
        ("two separate headers", ["10", "20"]),
        ("a comma-joined pair", ["10,20"]),
        ("something that is not a list", "10"),
        ("a value that is not text", [10]),
        ("an empty value", [""]),
        ("a value that is not digits", ["ten"]),
        ("a non-ascii digit", ["１０"]),
    ]:
        response = Response(_success_body())
        response.headers = cast(Headers, _Headers(values))
        monkeypatch.setattr(
            transcription, "_open_request", lambda _request, *, timeout, _r=response: _r
        )
        with pytest.raises(SpeechTranscriptionRejected):
            _adapter().transcribe(_batch())
        assert label


def test_a_redirect_is_refused_and_its_body_closed() -> None:
    """A redirect could take the audio somewhere the catalog never declared."""

    class _Body:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    body = _Body()
    handler = transcription._RejectRedirectHandler()

    with pytest.raises(SpeechTranscriptionRejected):
        handler.redirect_request(
            urllib.request.Request("https://example.invalid/v1"),
            cast(Any, body),
            302,
            "Found",
            cast(Any, Headers(0)),
            "https://elsewhere.invalid/v1",
        )

    assert body.closed, "the redirect's body is released rather than left open"


def test_the_transport_installs_the_redirect_refusing_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built per call so no global opener can quietly re-enable redirects."""
    seen: list[object] = []

    class _Opener:
        def open(self, _request: object, timeout: float) -> Response:
            seen.append(timeout)
            return Response(_success_body())

    def building(*handlers: object) -> _Opener:
        seen.extend(handlers)
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", building)

    transcription._open_request(urllib.request.Request("https://example.invalid/v1"), timeout=5.0)

    assert any(isinstance(handler, transcription._RejectRedirectHandler) for handler in seen)
    assert 5.0 in seen


def test_a_response_that_does_not_match_its_declared_length_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncation reads as a valid short document; the declared length is what catches it."""
    body = _success_body()
    monkeypatch.setattr(
        transcription,
        "_open_request",
        lambda _request, *, timeout: Response(body, declared_length=len(body) + 1),
    )

    with pytest.raises(SpeechTranscriptionRejected):
        _adapter().transcribe(_batch())


def test_an_assistant_message_of_the_wrong_shape_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transcript is read out of this one object; an unexpected shape is not read around."""
    for label, message in [
        ("not an object", "欢迎使用本地音轨转写。"),
        ("an extra key", {"content": "话", "role": "assistant", "refusal": None}),
        ("a missing key", {"content": "话"}),
        ("another role", {"content": "话", "role": "system"}),
    ]:
        choice = {
            "finish_reason": "stop",
            "index": 0,
            "logprobs": None,
            "message": message,
        }
        monkeypatch.setattr(
            transcription,
            "_open_request",
            lambda _request, *, timeout, _c=choice: Response(_success_body(choices=[_c])),
        )
        with pytest.raises(SpeechTranscriptionRejected):
            _adapter().transcribe(_batch())
        assert label


def test_a_response_with_no_content_length_header_is_read_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_all` answering None is the absent-header case, not a malformed one."""

    class _NoHeader:
        def get_all(self, _name: str) -> None:
            return None

    response = Response(_success_body())
    response.headers = cast(Headers, _NoHeader())
    monkeypatch.setattr(transcription, "_open_request", lambda _request, *, timeout: response)

    assert _adapter().transcribe(_batch()) == "欢迎使用本地音轨转写。"
