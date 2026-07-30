"""LE-14 T3: the locked Bailian adapter receives audio bytes and nothing else."""

from __future__ import annotations

import base64
import io
import json
import traceback
import urllib.request
import wave
from pathlib import Path
from typing import cast

import pytest

from automation_tool.executor import material_speech_transcription as transcription
from automation_tool.executor.material_speech_pipeline import SpeechAudioBatch
from automation_tool.executor.material_speech_transcription import (
    BailianSpeechTranscriptionAdapter,
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
                "message": {
                    "annotations": [
                        {
                            "emotion": "neutral",
                            "language": "zh",
                            "type": "audio_info",
                        }
                    ],
                    "content": "欢迎使用本地音轨转写。",
                    "role": "assistant",
                },
            }
        ],
        "created": 1_785_000_000,
        "id": "chatcmpl-speech-001",
        "model": "qwen3-asr-flash-2026-02-10",
        "object": "chat.completion",
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
                    "message": {
                        "annotations": [],
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
                    "message": {
                        "annotations": [],
                        "content": "",
                        "role": "assistant",
                    },
                }
            ]
        ),
        _success_body(future=True),
    ],
    ids=["no-choice", "incomplete", "empty", "open-top-level-shape"],
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
