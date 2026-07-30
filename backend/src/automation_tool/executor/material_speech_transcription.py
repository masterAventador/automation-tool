"""Locked, path-free Bailian speech transcription adapter."""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import re
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, NoReturn, cast

from automation_tool.executor.material_speech_pipeline import SpeechAudioBatch
from automation_tool.protocol.json_object import decode_bounded_json_object

BAILIAN_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_ASR_MODEL_ID: Final = "qwen3-asr-flash-2026-02-10"
MAX_RESPONSE_BYTES: Final = 1024 * 1024
MAX_TRANSCRIPT_CHARACTERS: Final = 100_000
_API_KEY_PATTERN: Final = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_TOP_LEVEL_KEYS: Final = {
    "choices",
    "created",
    "id",
    "model",
    "object",
    "usage",
}


class SpeechTranscriptionRejected(RuntimeError):
    """Configuration, transport or response data failed the ASR boundary."""

    def __init__(self) -> None:
        super().__init__("speech transcription request rejected")


def _reject() -> NoReturn:
    raise SpeechTranscriptionRejected from None


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, code, msg, headers, newurl
        with contextlib.suppress(OSError, ValueError):
            fp.close()
        _reject()


def _open_request(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> http.client.HTTPResponse:
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    return cast(http.client.HTTPResponse, opener.open(request, timeout=timeout))


def _declared_response_bytes(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    get_all = getattr(headers, "get_all", None)
    if not callable(get_all):
        _reject()
    values = get_all("Content-Length")
    if values is None:
        return None
    if not isinstance(values, list) or len(values) != 1:
        _reject()
    value = values[0]
    if type(value) is not str or "," in value:
        _reject()
    token = value.strip(" \t")
    if not token or not token.isascii() or not token.isdigit():
        _reject()
    return int(token)


def _validated_text(value: object, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(
            character not in {"\n", "\t"} and unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        _reject()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class BailianSpeechTranscriptionConfig:
    """Validated private settings used only by the Bailian adapter."""

    base_url: str
    model_id: str
    api_key: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            self.base_url != BAILIAN_BASE_URL
            or self.model_id != BAILIAN_ASR_MODEL_ID
            or type(self.api_key) is not str
            or _API_KEY_PATTERN.fullmatch(self.api_key) is None
            or type(self.timeout_seconds) not in {int, float}
            or not 0 < self.timeout_seconds <= 90
        ):
            _reject()

    def __repr__(self) -> str:
        return (
            "BailianSpeechTranscriptionConfig("
            f"base_url={self.base_url!r}, model_id={self.model_id!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, api_key=<redacted>)"
        )


def load_bailian_speech_transcription_config(
    *,
    catalog_path: Path,
    api_key: str,
    timeout_seconds: float,
) -> BailianSpeechTranscriptionConfig:
    """Select the one ASR snapshot declared by the shared video catalog."""

    try:
        if catalog_path.is_symlink() or not catalog_path.is_file():
            _reject()
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        document = None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("provider") != "bailian"
        or document.get("api_mode") != "openai_compatible"
        or document.get("base_url") != BAILIAN_BASE_URL
        or not isinstance(document.get("purposes"), list)
        or not isinstance(document.get("models"), list)
    ):
        _reject()
    purpose = next(
        (
            item
            for item in document["purposes"]
            if isinstance(item, dict) and item.get("id") == "speech_recognition"
        ),
        None,
    )
    model = next(
        (
            item
            for item in document["models"]
            if isinstance(item, dict) and item.get("id") == BAILIAN_ASR_MODEL_ID
        ),
        None,
    )
    if (
        not isinstance(purpose, dict)
        or purpose.get("api_mode") != "openai_compatible"
        or purpose.get("base_url") != BAILIAN_BASE_URL
        or purpose.get("default_model_id") != BAILIAN_ASR_MODEL_ID
        or purpose.get("allowed_model_ids") != [BAILIAN_ASR_MODEL_ID]
        or not isinstance(model, dict)
        or model.get("audio_input") is not True
        or model.get("image_input") is not False
    ):
        _reject()
    if type(timeout_seconds) not in {int, float}:
        _reject()
    return BailianSpeechTranscriptionConfig(
        base_url=BAILIAN_BASE_URL,
        model_id=BAILIAN_ASR_MODEL_ID,
        api_key=api_key,
        timeout_seconds=float(timeout_seconds),
    )


class BailianSpeechTranscriptionAdapter:
    """OpenAI-compatible Bailian implementation of the neutral ASR protocol."""

    def __init__(self, config: BailianSpeechTranscriptionConfig) -> None:
        if not isinstance(config, BailianSpeechTranscriptionConfig):
            _reject()
        self._config = config

    @property
    def config(self) -> BailianSpeechTranscriptionConfig:
        return self._config

    def __repr__(self) -> str:
        return f"BailianSpeechTranscriptionAdapter(config={self._config!r})"

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        if not isinstance(audio, SpeechAudioBatch):
            _reject()
        encoded = base64.b64encode(audio.wav_bytes).decode("ascii")
        body = json.dumps(
            {
                "model": self._config.model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:audio/wav;base64,{encoded}",
                                },
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": True},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._config.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        try:
            with _open_request(request, timeout=self._config.timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    _reject()
                declared = _declared_response_bytes(response)
                if declared is not None and declared > MAX_RESPONSE_BYTES:
                    _reject()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES or (declared is not None and len(raw) != declared):
                _reject()
            return _parse_response(raw)
        except SpeechTranscriptionRejected:
            raise
        except (
            http.client.HTTPException,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            pass
        _reject()


def _parse_response(raw: bytes) -> str:
    document = decode_bounded_json_object(raw, maximum_bytes=MAX_RESPONSE_BYTES)
    if (
        set(document) != _TOP_LEVEL_KEYS
        or document.get("model") != BAILIAN_ASR_MODEL_ID
        or document.get("object") != "chat.completion"
        or type(document.get("created")) is not int
        or not isinstance(document.get("usage"), dict)
    ):
        _reject()
    _validated_text(document.get("id"), maximum=512)
    choices = document.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        _reject()
    choice = choices[0]
    if (
        not isinstance(choice, dict)
        or set(choice) != {"finish_reason", "index", "message"}
        or choice.get("finish_reason") != "stop"
        or choice.get("index") != 0
        or type(choice.get("index")) is not int
    ):
        _reject()
    message = choice.get("message")
    if (
        not isinstance(message, dict)
        or set(message) != {"annotations", "content", "role"}
        or message.get("role") != "assistant"
        or not isinstance(message.get("annotations"), list)
    ):
        _reject()
    return _validated_text(
        message.get("content"),
        maximum=MAX_TRANSCRIPT_CHARACTERS,
    )


__all__ = [
    "BAILIAN_ASR_MODEL_ID",
    "BAILIAN_BASE_URL",
    "MAX_RESPONSE_BYTES",
    "BailianSpeechTranscriptionAdapter",
    "BailianSpeechTranscriptionConfig",
    "SpeechTranscriptionRejected",
    "load_bailian_speech_transcription_config",
]
