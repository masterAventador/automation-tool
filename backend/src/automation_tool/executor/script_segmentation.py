"""Supplier-neutral script segmentation with a locked Bailian adapter."""

from __future__ import annotations

import contextlib
import http.client
import json
import re
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Final, NoReturn, Protocol, cast, runtime_checkable

from automation_tool.executor.motion_authoring.voiceover import MAX_NARRATION_CHARS
from automation_tool.protocol.json_object import decode_bounded_json_object

_BAILIAN_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_SCRIPT_MODEL_ID: Final = "qwen3.7-max-2026-06-08"
_SCRIPT_MODEL_IDS: Final = frozenset(
    {
        "deepseek-v4-pro",
        "glm-5.2",
        _DEFAULT_SCRIPT_MODEL_ID,
    }
)
_API_KEY_PATTERN: Final = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_MAX_CATALOG_BYTES: Final = 262_144
_MAX_RESPONSE_BYTES: Final = 65_536
_MAX_STRUCTURED_RESULT_BYTES: Final = 32_768
_MAX_REQUEST_ID_CHARACTERS: Final = 512
_SCRIPT_PROMPT: Final = (
    "Return one JSON object with exactly one field named sentences. "
    "Its value must be a non-empty ordered array of narration sentence strings. "
    "Return no surrounding prose."
)

MAX_SCRIPT_PROMPT_CHARACTERS: Final = 4_000
MAX_SCRIPT_SENTENCES: Final = 128
MAX_SCRIPT_SENTENCE_CHARACTERS: Final = MAX_NARRATION_CHARS
MAX_SCRIPT_TOTAL_CHARACTERS: Final = 4_000


class ScriptSegmentationRejected(RuntimeError):
    """The script-segmentation boundary rejected its input or upstream reply."""

    def __init__(self) -> None:
        super().__init__("script segmentation request rejected")


def _reject() -> NoReturn:
    raise ScriptSegmentationRejected from None


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
    if headers is None:
        return None
    get_all = getattr(headers, "get_all", None)
    if not callable(get_all):
        _reject()
    raw_values = get_all("Content-Length")
    if raw_values is None:
        return None
    if not isinstance(raw_values, list) or len(raw_values) != 1:
        _reject()
    raw_value = raw_values[0]
    if type(raw_value) is not str or "," in raw_value:
        _reject()
    token = raw_value.strip(" \t")
    if not token or not token.isascii() or not token.isdigit():
        _reject()
    return int(token)


def _validate_text(
    value: object,
    *,
    maximum: int,
    allow_json_whitespace: bool = False,
) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(
            (not allow_json_whitespace or character not in {"\n", "\r", "\t"})
            and unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        _reject()
    return value


@dataclass(frozen=True, slots=True)
class ScriptSegmentationOptions:
    """Request-level choices shared by every future model provider."""

    enable_thinking: bool = False
    max_output_tokens: int = 2_048

    def __post_init__(self) -> None:
        if (
            type(self.enable_thinking) is not bool
            or type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 16_384
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class ScriptSegmentationReply:
    """Provider-independent completion facts needed by the parsing layer."""

    request_id: str
    content: str
    finish_reason: str

    def __post_init__(self) -> None:
        _validate_text(self.request_id, maximum=_MAX_REQUEST_ID_CHARACTERS)
        _validate_text(
            self.content,
            maximum=_MAX_STRUCTURED_RESULT_BYTES,
            allow_json_whitespace=True,
        )
        _validate_text(self.finish_reason, maximum=64)


@dataclass(frozen=True, slots=True)
class ScriptSentence:
    """One locally sequenced narration sentence."""

    sequence: int
    text: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_SCRIPT_SENTENCES:
            _reject()
        _validate_text(self.text, maximum=MAX_SCRIPT_SENTENCE_CHARACTERS)


@dataclass(frozen=True, slots=True)
class ScriptSegmentationResult:
    """The complete supplier-neutral ordered script."""

    request_id: str
    sentences: tuple[ScriptSentence, ...]

    def __post_init__(self) -> None:
        _validate_text(self.request_id, maximum=_MAX_REQUEST_ID_CHARACTERS)
        if (
            not isinstance(self.sentences, tuple)
            or not 1 <= len(self.sentences) <= MAX_SCRIPT_SENTENCES
            or not all(isinstance(sentence, ScriptSentence) for sentence in self.sentences)
            or tuple(sentence.sequence for sentence in self.sentences)
            != tuple(range(1, len(self.sentences) + 1))
            or sum(len(sentence.text) for sentence in self.sentences) > MAX_SCRIPT_TOTAL_CHARACTERS
        ):
            _reject()


@runtime_checkable
class ScriptSegmentationAdapter(Protocol):
    """The only model surface consumed by script-segmentation orchestration."""

    def segment(
        self,
        text: str,
        *,
        options: ScriptSegmentationOptions,
    ) -> ScriptSegmentationReply: ...


def segment_script(
    adapter: ScriptSegmentationAdapter,
    text: str,
    *,
    options: ScriptSegmentationOptions,
) -> ScriptSegmentationResult:
    """Ask one neutral adapter for a strict ordered narration script."""

    if not isinstance(adapter, ScriptSegmentationAdapter) or not isinstance(
        options, ScriptSegmentationOptions
    ):
        _reject()
    validated_text = _validate_text(
        text,
        maximum=MAX_SCRIPT_PROMPT_CHARACTERS,
        allow_json_whitespace=True,
    )
    reply = adapter.segment(validated_text, options=options)
    if not isinstance(reply, ScriptSegmentationReply) or reply.finish_reason != "stop":
        _reject()
    try:
        document = decode_bounded_json_object(
            reply.content,
            maximum_bytes=_MAX_STRUCTURED_RESULT_BYTES,
        )
        if set(document) != {"sentences"}:
            _reject()
        raw_sentences = document["sentences"]
        if (
            not isinstance(raw_sentences, list)
            or not 1 <= len(raw_sentences) <= MAX_SCRIPT_SENTENCES
        ):
            _reject()
        sentences = tuple(
            ScriptSentence(
                sequence=sequence,
                text=cast(str, raw_sentence),
            )
            for sequence, raw_sentence in enumerate(raw_sentences, start=1)
        )
        return ScriptSegmentationResult(
            request_id=reply.request_id,
            sentences=sentences,
        )
    except (KeyError, RecursionError, TypeError, UnicodeError, ValueError):
        pass
    _reject()


@dataclass(frozen=True, slots=True, repr=False)
class BailianScriptSegmentationConfig:
    """Validated private settings used only inside the Bailian adapter."""

    base_url: str
    model_id: str
    api_key: str = field(repr=False)
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            self.base_url != _BAILIAN_BASE_URL
            or type(self.model_id) is not str
            or self.model_id not in _SCRIPT_MODEL_IDS
            or type(self.api_key) is not str
            or _API_KEY_PATTERN.fullmatch(self.api_key) is None
            or type(self.timeout_seconds) not in {int, float}
            or not 0 < self.timeout_seconds <= 300
        ):
            _reject()

    def __repr__(self) -> str:
        return (
            "BailianScriptSegmentationConfig("
            f"base_url={self.base_url!r}, model_id={self.model_id!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, api_key=<redacted>)"
        )


def load_bailian_script_segmentation_config(
    *,
    catalog_path: Path,
    api_key: str,
    model_id: str | None,
    timeout_seconds: float,
) -> BailianScriptSegmentationConfig:
    """Select one allowed text model from the packaged video-model catalog."""

    try:
        if catalog_path.is_symlink() or not catalog_path.is_file():
            _reject()
        document = decode_bounded_json_object(
            catalog_path.read_bytes(),
            maximum_bytes=_MAX_CATALOG_BYTES,
        )
    except (OSError, RecursionError, TypeError, UnicodeError, ValueError):
        document = {}
    purposes = document.get("purposes")
    models = document.get("models")
    if (
        document.get("schema_version") != 1
        or document.get("provider") != "bailian"
        or document.get("api_mode") != "openai_compatible"
        or document.get("base_url") != _BAILIAN_BASE_URL
        or not isinstance(purposes, list)
        or not isinstance(models, list)
    ):
        _reject()
    script_purposes = [
        item for item in purposes if isinstance(item, dict) and item.get("id") == "script"
    ]
    if len(script_purposes) != 1:
        _reject()
    purpose = script_purposes[0]
    allowed_model_ids = purpose.get("allowed_model_ids")
    default_model_id = purpose.get("default_model_id")
    if (
        default_model_id != _DEFAULT_SCRIPT_MODEL_ID
        or not isinstance(allowed_model_ids, list)
        or not all(type(item) is str for item in allowed_model_ids)
        or set(allowed_model_ids) != _SCRIPT_MODEL_IDS
        or len(allowed_model_ids) != len(_SCRIPT_MODEL_IDS)
    ):
        _reject()
    selected_model_id = default_model_id if model_id is None else model_id
    if type(selected_model_id) is not str or selected_model_id not in _SCRIPT_MODEL_IDS:
        _reject()
    if not all(isinstance(item, dict) and type(item.get("id")) is str for item in models):
        _reject()
    for allowed_model_id in _SCRIPT_MODEL_IDS:
        matching_models = [item for item in models if item.get("id") == allowed_model_id]
        if len(matching_models) != 1 or matching_models[0].get("text") is not True:
            _reject()
    return BailianScriptSegmentationConfig(
        base_url=_BAILIAN_BASE_URL,
        model_id=selected_model_id,
        api_key=api_key,
        timeout_seconds=float(timeout_seconds),
    )


class BailianScriptSegmentationAdapter:
    """OpenAI-compatible Bailian implementation hidden behind the neutral protocol."""

    def __init__(self, config: BailianScriptSegmentationConfig) -> None:
        if not isinstance(config, BailianScriptSegmentationConfig):
            _reject()
        self._config = config

    @property
    def config(self) -> BailianScriptSegmentationConfig:
        return self._config

    def __repr__(self) -> str:
        return f"BailianScriptSegmentationAdapter(config={self._config!r})"

    def segment(
        self,
        text: str,
        *,
        options: ScriptSegmentationOptions,
    ) -> ScriptSegmentationReply:
        if not isinstance(options, ScriptSegmentationOptions):
            _reject()
        validated_text = _validate_text(
            text,
            maximum=MAX_SCRIPT_PROMPT_CHARACTERS,
            allow_json_whitespace=True,
        )
        body = json.dumps(
            {
                "model": self._config.model_id,
                "messages": [
                    {
                        "role": "system",
                        "content": _SCRIPT_PROMPT,
                    },
                    {"role": "user", "content": validated_text},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
                "max_tokens": options.max_output_tokens,
                "enable_thinking": options.enable_thinking,
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
            with _open_request(
                request,
                timeout=self._config.timeout_seconds,
            ) as response:
                declared_response_bytes = _declared_response_bytes(response)
                raw_response = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw_response) > _MAX_RESPONSE_BYTES or (
                type(declared_response_bytes) is int
                and len(raw_response) != declared_response_bytes
            ):
                _reject()
            document = decode_bounded_json_object(
                raw_response,
                maximum_bytes=_MAX_RESPONSE_BYTES,
            )
            choices = document["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                _reject()
            choice = choices[0]
            if not isinstance(choice, dict):
                _reject()
            message = choice["message"]
            if not isinstance(message, dict):
                _reject()
            refusal = message.get("refusal")
            if refusal is not None and refusal != "":
                _reject()
            return ScriptSegmentationReply(
                request_id=cast(str, document["id"]),
                content=cast(str, message["content"]),
                finish_reason=cast(str, choice["finish_reason"]),
            )
        except (
            http.client.HTTPException,
            OSError,
            json.JSONDecodeError,
            KeyError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            pass
        _reject()


__all__ = [
    "MAX_SCRIPT_PROMPT_CHARACTERS",
    "MAX_SCRIPT_SENTENCES",
    "MAX_SCRIPT_SENTENCE_CHARACTERS",
    "MAX_SCRIPT_TOTAL_CHARACTERS",
    "BailianScriptSegmentationAdapter",
    "BailianScriptSegmentationConfig",
    "ScriptSegmentationAdapter",
    "ScriptSegmentationOptions",
    "ScriptSegmentationRejected",
    "ScriptSegmentationReply",
    "ScriptSegmentationResult",
    "ScriptSentence",
    "load_bailian_script_segmentation_config",
    "segment_script",
]
