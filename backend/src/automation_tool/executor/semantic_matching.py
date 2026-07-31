"""Supplier-neutral semantic matching with a locked Bailian adapter."""

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

from automation_tool.control_plane.domain.material import (
    MAX_DESCRIPTION_CHARACTERS,
    MAX_TAG_CHARACTERS,
    MAX_TAGS,
    MAX_TRANSCRIPT_CHARACTERS,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.script_segmentation import (
    MAX_SCRIPT_SENTENCE_CHARACTERS,
    MAX_SCRIPT_SENTENCES,
    BailianScriptSegmentationConfig,
    ScriptSegmentationRejected,
    ScriptSegmentationResult,
    load_bailian_script_segmentation_config,
)
from automation_tool.protocol.json_object import decode_bounded_json_object

_BAILIAN_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL_ID: Final = "qwen3.7-max-2026-06-08"
_MODEL_IDS: Final = frozenset(
    {
        "deepseek-v4-pro",
        "glm-5.2",
        _DEFAULT_MODEL_ID,
    }
)
_API_KEY_PATTERN: Final = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_CANDIDATE_KEY_PATTERN: Final = re.compile(r"^m[0-9]{4}$")
_MAX_REQUEST_ID_CHARACTERS: Final = 512
_MAX_STRUCTURED_RESULT_BYTES: Final = 262_144
_MAX_RESPONSE_BYTES: Final = 262_144
_MAX_REQUEST_BYTES: Final = 2 * 1024 * 1024
_MATCHING_PROMPT: Final = (
    "Score the semantic relevance of every supplied sentence and candidate. "
    "Return one JSON object with exactly one field named scores. Its value must "
    "contain exactly one object for every sentenceSequence and candidateKey pair. "
    "Each object must contain exactly sentenceSequence, candidateKey, and an integer "
    "score from 0 to 100. Return no winner and no surrounding prose."
)

MAX_SEMANTIC_MATERIALS: Final = 32
SEMANTIC_SENTENCES_PER_REQUEST: Final = 16
SEMANTIC_MATCH_SCORE_THRESHOLD: Final = 60


class SemanticMatchingRejected(RuntimeError):
    """The semantic-matching boundary rejected its input or upstream reply."""

    def __init__(self) -> None:
        super().__init__("semantic matching request rejected")


def _reject() -> NoReturn:
    raise SemanticMatchingRejected from None


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
class SemanticMatchingOptions:
    """Request-level choices shared by every future matching provider."""

    enable_thinking: bool = False
    max_output_tokens: int = 16_384

    def __post_init__(self) -> None:
        if (
            type(self.enable_thinking) is not bool
            or type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 16_384
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SemanticMatchingSentence:
    """One script sentence projected into a matching request."""

    sequence: int
    text: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_SCRIPT_SENTENCES:
            _reject()
        _validate_text(self.text, maximum=MAX_SCRIPT_SENTENCE_CHARACTERS)


@dataclass(frozen=True, slots=True)
class SemanticMatchingCandidate:
    """Only the model-visible evidence for one visual material."""

    candidate_key: str
    description: str
    tags: tuple[str, ...]
    transcript: str | None

    def __post_init__(self) -> None:
        if (
            type(self.candidate_key) is not str
            or _CANDIDATE_KEY_PATTERN.fullmatch(self.candidate_key) is None
        ):
            _reject()
        _validate_text(self.description, maximum=MAX_DESCRIPTION_CHARACTERS)
        if (
            not isinstance(self.tags, tuple)
            or len(self.tags) > MAX_TAGS
            or len(set(self.tags)) != len(self.tags)
        ):
            _reject()
        for tag in self.tags:
            _validate_text(tag, maximum=MAX_TAG_CHARACTERS)
        if self.transcript is not None:
            _validate_text(self.transcript, maximum=MAX_TRANSCRIPT_CHARACTERS)


@dataclass(frozen=True, slots=True)
class SemanticMatchingRequest:
    """One bounded sentence batch with the complete visual candidate set."""

    sentences: tuple[SemanticMatchingSentence, ...]
    candidates: tuple[SemanticMatchingCandidate, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sentences, tuple)
            or not 1 <= len(self.sentences) <= SEMANTIC_SENTENCES_PER_REQUEST
            or not all(
                isinstance(sentence, SemanticMatchingSentence) for sentence in self.sentences
            )
            or len({sentence.sequence for sentence in self.sentences}) != len(self.sentences)
            or not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= MAX_SEMANTIC_MATERIALS
            or not all(
                isinstance(candidate, SemanticMatchingCandidate) for candidate in self.candidates
            )
            or len({candidate.candidate_key for candidate in self.candidates})
            != len(self.candidates)
        ):
            _reject()

    @classmethod
    def from_parts(
        cls,
        *,
        sentences: tuple[tuple[int, str], ...],
        candidates: tuple[tuple[str, str, tuple[str, ...], str | None], ...],
    ) -> SemanticMatchingRequest:
        """Build the neutral request without exposing provider wire classes."""

        try:
            built_sentences = tuple(
                SemanticMatchingSentence(sequence=sequence, text=text)
                for sequence, text in sentences
            )
            built_candidates = tuple(
                SemanticMatchingCandidate(
                    candidate_key=candidate_key,
                    description=description,
                    tags=tags,
                    transcript=transcript,
                )
                for candidate_key, description, tags, transcript in candidates
            )
            return cls(sentences=built_sentences, candidates=built_candidates)
        except (TypeError, ValueError):
            pass
        _reject()

    def to_wire(self) -> dict[str, object]:
        """Return the provider-neutral JSON value exposed to the model."""

        candidates: list[dict[str, object]] = []
        for candidate in self.candidates:
            encoded: dict[str, object] = {
                "candidateKey": candidate.candidate_key,
                "description": candidate.description,
                "tags": list(candidate.tags),
            }
            if candidate.transcript is not None:
                encoded["transcript"] = candidate.transcript
            candidates.append(encoded)
        return {
            "sentences": [
                {"sequence": sentence.sequence, "text": sentence.text}
                for sentence in self.sentences
            ],
            "candidates": candidates,
        }


@dataclass(frozen=True, slots=True)
class SemanticMatchingReply:
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
        if len(self.content.encode("utf-8")) > _MAX_STRUCTURED_RESULT_BYTES:
            _reject()
        _validate_text(self.finish_reason, maximum=64)


@dataclass(frozen=True, slots=True)
class SemanticCandidateScore:
    """One locally resolved material score."""

    material_id: MaterialId
    score: int
    qualified: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or type(self.score) is not int
            or not 0 <= self.score <= 100
            or type(self.qualified) is not bool
            or self.qualified is not (self.score >= SEMANTIC_MATCH_SCORE_THRESHOLD)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SemanticSentenceMatches:
    """All candidates for one sentence, already in deterministic local order."""

    sequence: int
    candidates: tuple[SemanticCandidateScore, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_SCRIPT_SENTENCES
            or not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= MAX_SEMANTIC_MATERIALS
            or not all(
                isinstance(candidate, SemanticCandidateScore) for candidate in self.candidates
            )
            or len({candidate.material_id for candidate in self.candidates}) != len(self.candidates)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class SemanticMatchingResult:
    """The complete supplier-neutral sentence x material score matrix."""

    request_ids: tuple[str, ...]
    sentences: tuple[SemanticSentenceMatches, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.request_ids, tuple)
            or not self.request_ids
            or len(set(self.request_ids)) != len(self.request_ids)
            or not isinstance(self.sentences, tuple)
            or not self.sentences
            or not all(isinstance(sentence, SemanticSentenceMatches) for sentence in self.sentences)
            or tuple(sentence.sequence for sentence in self.sentences)
            != tuple(range(1, len(self.sentences) + 1))
        ):
            _reject()
        for request_id in self.request_ids:
            _validate_text(request_id, maximum=_MAX_REQUEST_ID_CHARACTERS)


@runtime_checkable
class SemanticMatchingAdapter(Protocol):
    """The only model surface consumed by matching orchestration."""

    def match(
        self,
        request: SemanticMatchingRequest,
        *,
        options: SemanticMatchingOptions,
    ) -> SemanticMatchingReply: ...


def _request_bytes(request: SemanticMatchingRequest) -> bytes:
    return json.dumps(
        request.to_wire(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _project_requests(
    script: ScriptSegmentationResult,
    materials: tuple[Material, ...],
) -> tuple[SemanticMatchingRequest, ...]:
    if (
        not isinstance(script, ScriptSegmentationResult)
        or not isinstance(materials, tuple)
        or not 1 <= len(materials) <= MAX_SEMANTIC_MATERIALS
        or not all(isinstance(material, Material) for material in materials)
        or len({material.material_id for material in materials}) != len(materials)
    ):
        _reject()

    candidates: list[SemanticMatchingCandidate] = []
    for index, material in enumerate(materials, start=1):
        if material.kind not in {MaterialKind.VIDEO, MaterialKind.IMAGE}:
            _reject()
        if material.ai_description is None:
            _reject()
        candidates.append(
            SemanticMatchingCandidate(
                candidate_key=f"m{index:04d}",
                description=material.ai_description,
                tags=material.ai_tags,
                transcript=material.speech_transcript if material.has_speech else None,
            )
        )

    requests = tuple(
        SemanticMatchingRequest(
            sentences=tuple(
                SemanticMatchingSentence(sequence=sentence.sequence, text=sentence.text)
                for sentence in script.sentences[start : start + SEMANTIC_SENTENCES_PER_REQUEST]
            ),
            candidates=tuple(candidates),
        )
        for start in range(0, len(script.sentences), SEMANTIC_SENTENCES_PER_REQUEST)
    )
    if any(len(_request_bytes(request)) > _MAX_REQUEST_BYTES for request in requests):
        _reject()
    return requests


def _parse_scores(
    reply: SemanticMatchingReply,
    request: SemanticMatchingRequest,
) -> dict[tuple[int, str], int]:
    parsed: dict[tuple[int, str], int] | None = None
    try:
        document = decode_bounded_json_object(
            reply.content,
            maximum_bytes=_MAX_STRUCTURED_RESULT_BYTES,
        )
        if set(document) != {"scores"}:
            _reject()
        raw_scores = document["scores"]
        if not isinstance(raw_scores, list):
            _reject()
        expected = {
            (sentence.sequence, candidate.candidate_key)
            for sentence in request.sentences
            for candidate in request.candidates
        }
        collected: dict[tuple[int, str], int] = {}
        for raw_score in raw_scores:
            if not isinstance(raw_score, dict) or set(raw_score) != {
                "sentenceSequence",
                "candidateKey",
                "score",
            }:
                _reject()
            sequence = raw_score["sentenceSequence"]
            candidate_key = raw_score["candidateKey"]
            score = raw_score["score"]
            if (
                type(sequence) is not int
                or type(candidate_key) is not str
                or type(score) is not int
                or not 0 <= score <= 100
            ):
                _reject()
            key = (sequence, candidate_key)
            if key not in expected or key in collected:
                _reject()
            collected[key] = score
        if set(collected) != expected:
            _reject()
        parsed = collected
    except (KeyError, RecursionError, TypeError, UnicodeError, ValueError):
        pass
    if parsed is None:
        _reject()
    return parsed


def match_script_materials(
    adapter: SemanticMatchingAdapter,
    script: ScriptSegmentationResult,
    materials: tuple[Material, ...],
    *,
    options: SemanticMatchingOptions,
) -> SemanticMatchingResult:
    """Return a complete, locally ordered sentence x material score matrix."""

    if not isinstance(adapter, SemanticMatchingAdapter) or not isinstance(
        options, SemanticMatchingOptions
    ):
        _reject()
    requests = _project_requests(script, materials)

    request_ids: list[str] = []
    matrix: dict[tuple[int, str], int] = {}
    for request in requests:
        reply: SemanticMatchingReply | None = None
        try:
            candidate_reply = adapter.match(request, options=options)
            if (
                isinstance(candidate_reply, SemanticMatchingReply)
                and candidate_reply.finish_reason == "stop"
            ):
                reply = candidate_reply
        except Exception:
            pass
        if reply is None:
            _reject()
        request_ids.append(reply.request_id)
        matrix.update(_parse_scores(reply, request))

    if len(set(request_ids)) != len(request_ids):
        _reject()

    candidate_keys = tuple(f"m{index:04d}" for index in range(1, len(materials) + 1))
    sentence_matches = tuple(
        SemanticSentenceMatches(
            sequence=sentence.sequence,
            candidates=tuple(
                SemanticCandidateScore(
                    material_id=materials[candidate_index].material_id,
                    score=matrix[(sentence.sequence, candidate_key)],
                    qualified=(
                        matrix[(sentence.sequence, candidate_key)] >= SEMANTIC_MATCH_SCORE_THRESHOLD
                    ),
                )
                for candidate_index, candidate_key in sorted(
                    enumerate(candidate_keys),
                    key=lambda item: (-matrix[(sentence.sequence, item[1])], item[0]),
                )
            ),
        )
        for sentence in script.sentences
    )
    return SemanticMatchingResult(
        request_ids=tuple(request_ids),
        sentences=sentence_matches,
    )


@dataclass(frozen=True, slots=True, repr=False)
class BailianSemanticMatchingConfig:
    """Validated private settings used only inside the Bailian adapter."""

    base_url: str
    model_id: str
    api_key: str = field(repr=False)
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            self.base_url != _BAILIAN_BASE_URL
            or type(self.model_id) is not str
            or self.model_id not in _MODEL_IDS
            or type(self.api_key) is not str
            or _API_KEY_PATTERN.fullmatch(self.api_key) is None
            or type(self.timeout_seconds) not in {int, float}
            or not 0 < self.timeout_seconds <= 300
        ):
            _reject()

    def __repr__(self) -> str:
        return (
            "BailianSemanticMatchingConfig("
            f"base_url={self.base_url!r}, model_id={self.model_id!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, api_key=<redacted>)"
        )


def load_bailian_semantic_matching_config(
    *,
    catalog_path: Path,
    api_key: str,
    model_id: str | None,
    timeout_seconds: float,
) -> BailianSemanticMatchingConfig:
    """Reuse the packaged script-purpose model allowlist for text matching."""

    if not isinstance(catalog_path, Path):
        _reject()
    source_config: BailianScriptSegmentationConfig | None = None
    with contextlib.suppress(ScriptSegmentationRejected):
        source_config = load_bailian_script_segmentation_config(
            catalog_path=catalog_path,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
        )
    if source_config is None:
        _reject()
    return BailianSemanticMatchingConfig(
        base_url=source_config.base_url,
        model_id=source_config.model_id,
        api_key=source_config.api_key,
        timeout_seconds=source_config.timeout_seconds,
    )


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


class BailianSemanticMatchingAdapter:
    """OpenAI-compatible Bailian implementation behind the neutral protocol."""

    def __init__(self, config: BailianSemanticMatchingConfig) -> None:
        if not isinstance(config, BailianSemanticMatchingConfig):
            _reject()
        self._config = config

    @property
    def config(self) -> BailianSemanticMatchingConfig:
        return self._config

    def __repr__(self) -> str:
        return f"BailianSemanticMatchingAdapter(config={self._config!r})"

    def match(
        self,
        request: SemanticMatchingRequest,
        *,
        options: SemanticMatchingOptions,
    ) -> SemanticMatchingReply:
        if not isinstance(request, SemanticMatchingRequest) or not isinstance(
            options, SemanticMatchingOptions
        ):
            _reject()
        user_content_bytes = _request_bytes(request)
        if len(user_content_bytes) > _MAX_REQUEST_BYTES:
            _reject()
        user_content = user_content_bytes.decode("utf-8")
        body = json.dumps(
            {
                "model": self._config.model_id,
                "messages": [
                    {"role": "system", "content": _MATCHING_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
                "max_tokens": options.max_output_tokens,
                "enable_thinking": options.enable_thinking,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        transport_request = urllib.request.Request(
            f"{self._config.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        reply: SemanticMatchingReply | None = None
        try:
            with _open_request(
                transport_request,
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
            reply = SemanticMatchingReply(
                request_id=cast(str, document["id"]),
                content=cast(str, message["content"]),
                finish_reason=cast(str, choice["finish_reason"]),
            )
        except (
            http.client.HTTPException,
            OSError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            pass
        if reply is None:
            _reject()
        return reply


__all__ = [
    "MAX_SEMANTIC_MATERIALS",
    "SEMANTIC_MATCH_SCORE_THRESHOLD",
    "SEMANTIC_SENTENCES_PER_REQUEST",
    "BailianSemanticMatchingAdapter",
    "BailianSemanticMatchingConfig",
    "SemanticCandidateScore",
    "SemanticMatchingAdapter",
    "SemanticMatchingCandidate",
    "SemanticMatchingOptions",
    "SemanticMatchingRejected",
    "SemanticMatchingReply",
    "SemanticMatchingRequest",
    "SemanticMatchingResult",
    "SemanticMatchingSentence",
    "SemanticSentenceMatches",
    "load_bailian_semantic_matching_config",
    "match_script_materials",
]
