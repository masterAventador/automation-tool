"""LE-16 T1: supplier-neutral semantic matching and Bailian adapter."""

from __future__ import annotations

import http.client
import io
import json
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import IO, cast
from unittest.mock import MagicMock

import pytest

from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor import semantic_matching as semantic_matching_module
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationResult,
    ScriptSentence,
)
from automation_tool.executor.semantic_matching import (
    MAX_SEMANTIC_MATERIALS,
    SEMANTIC_MATCH_SCORE_THRESHOLD,
    SEMANTIC_SENTENCES_PER_REQUEST,
    BailianSemanticMatchingAdapter,
    BailianSemanticMatchingConfig,
    SemanticCandidateScore,
    SemanticMatchingAdapter,
    SemanticMatchingCandidate,
    SemanticMatchingOptions,
    SemanticMatchingRejected,
    SemanticMatchingReply,
    SemanticMatchingRequest,
    SemanticMatchingResult,
    SemanticMatchingSentence,
    SemanticSentenceMatches,
    load_bailian_semantic_matching_config,
    match_script_materials,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
API_KEY = "sk-private-semantic-matching-key-123456"


def _script(count: int = 2) -> ScriptSegmentationResult:
    return ScriptSegmentationResult(
        request_id="req-script-source",
        sentences=tuple(
            ScriptSentence(sequence=index, text=f"第 {index} 句文案。")
            for index in range(1, count + 1)
        ),
    )


def _material(
    *,
    kind: MaterialKind = MaterialKind.VIDEO,
    description: str | None = "海边日落与慢慢驶过的帆船",
    tags: tuple[str, ...] = ("海边", "日落"),
    has_speech: bool = False,
    transcript: str | None = None,
    digest_character: str = "a",
    material_id: MaterialId | None = None,
) -> Material:
    is_image = kind is MaterialKind.IMAGE
    is_audio = kind is MaterialKind.AUDIO
    if has_speech and transcript is None:
        transcript = "欢迎来到海边。"
    return Material.register(
        material_id=material_id or MaterialId.new(),
        kind=kind,
        duration_ms=None if is_image else 15_000,
        width=None if is_audio else 1920,
        height=None if is_audio else 1080,
        content_digest=digest_character * 64,
        has_audio=is_audio or has_speech,
        audio_loudness_lufs=-18.0 if is_audio or has_speech else None,
        has_speech=has_speech,
        speech_segments_ms=((1_000, 3_000),) if has_speech else (),
        speech_transcript=transcript if has_speech else None,
        shot_boundaries_ms=() if is_audio or is_image else (0, 5_000),
        ai_description=description,
        ai_tags=tags if description is not None else (),
        description_source=DescriptionSource.AI,
        described_at=datetime.now(UTC) if description is not None else None,
    )


def _score_content(
    request: SemanticMatchingRequest,
    *,
    score_for: dict[tuple[int, str], int] | None = None,
) -> str:
    overrides = score_for or {}
    return json.dumps(
        {
            "scores": [
                {
                    "sentenceSequence": sentence.sequence,
                    "candidateKey": candidate.candidate_key,
                    "score": overrides.get(
                        (sentence.sequence, candidate.candidate_key),
                        75,
                    ),
                }
                for sentence in request.sentences
                for candidate in request.candidates
            ]
        },
        ensure_ascii=False,
    )


class _ScoringAdapter:
    def __init__(
        self,
        scores: dict[tuple[int, str], int] | None = None,
    ) -> None:
        self.requests: list[tuple[SemanticMatchingRequest, SemanticMatchingOptions]] = []
        self._scores = scores

    def match(
        self,
        request: SemanticMatchingRequest,
        *,
        options: SemanticMatchingOptions,
    ) -> SemanticMatchingReply:
        self.requests.append((request, options))
        return SemanticMatchingReply(
            request_id=f"req-match-{len(self.requests)}",
            content=_score_content(request, score_for=self._scores),
            finish_reason="stop",
        )


def test_neutral_protocol_defaults_and_private_config_repr() -> None:
    config = load_bailian_semantic_matching_config(
        catalog_path=CATALOG_PATH,
        api_key=API_KEY,
        model_id=None,
        timeout_seconds=12.5,
    )
    adapter = BailianSemanticMatchingAdapter(config)

    assert isinstance(adapter, SemanticMatchingAdapter)
    assert "Bailian" not in SemanticMatchingAdapter.__name__
    assert "OpenAI" not in SemanticMatchingAdapter.__name__
    assert API_KEY not in repr(config)
    assert API_KEY not in repr(adapter)
    assert adapter.config is config
    assert config.model_id == "qwen3.7-max-2026-06-08"
    assert SemanticMatchingOptions().enable_thinking is False
    assert SEMANTIC_MATCH_SCORE_THRESHOLD == 60
    assert SEMANTIC_SENTENCES_PER_REQUEST == 16
    assert MAX_SEMANTIC_MATERIALS == 32


def test_projection_includes_transcript_but_excludes_private_material_facts() -> None:
    silent = _material(digest_character="a")
    speaking = _material(
        has_speech=True,
        transcript="镜头里的人说:欢迎来到海边。",
        digest_character="b",
    )
    adapter = _ScoringAdapter()

    match_script_materials(
        cast(SemanticMatchingAdapter, adapter),
        _script(1),
        (silent, speaking),
        options=SemanticMatchingOptions(),
    )

    assert len(adapter.requests) == 1
    request, _options = adapter.requests[0]
    assert request.sentences[0].sequence == 1
    assert request.candidates[0].candidate_key == "m0001"
    assert request.candidates[0].transcript is None
    assert request.candidates[1].candidate_key == "m0002"
    assert request.candidates[1].transcript == "镜头里的人说:欢迎来到海边。"
    encoded = json.dumps(request.to_wire(), ensure_ascii=False)
    for forbidden in (
        str(silent.material_id),
        str(speaking.material_id),
        silent.content_digest,
        speaking.content_digest,
        "1920",
        "1080",
        "-18.0",
        "/Users/operator/private.mp4",
    ):
        assert forbidden not in encoded


def test_projection_preserves_material_multiline_evidence() -> None:
    material = _material(
        description="第一行画面描述\n第二行画面描述\t补充",
        has_speech=True,
        transcript="第一批转写\n第二批转写\t停顿",
    )
    adapter = _ScoringAdapter()

    match_script_materials(
        cast(SemanticMatchingAdapter, adapter),
        _script(1),
        (material,),
        options=SemanticMatchingOptions(),
    )

    request, _options = adapter.requests[0]
    assert request.candidates[0].description == "第一行画面描述\n第二行画面描述\t补充"
    assert request.candidates[0].transcript == "第一批转写\n第二批转写\t停顿"


def test_local_sorting_is_stable_and_threshold_is_inclusive() -> None:
    first = _material(digest_character="a")
    second = _material(digest_character="b")
    third = _material(digest_character="c")
    adapter = _ScoringAdapter(
        {
            (1, "m0001"): 60,
            (1, "m0002"): 88,
            (1, "m0003"): 88,
            (2, "m0001"): 59,
            (2, "m0002"): 20,
            (2, "m0003"): 10,
        }
    )

    result = match_script_materials(
        cast(SemanticMatchingAdapter, adapter),
        _script(),
        (first, second, third),
        options=SemanticMatchingOptions(),
    )

    assert result.request_ids == ("req-match-1",)
    assert [
        (candidate.material_id, candidate.score, candidate.qualified)
        for candidate in result.sentences[0].candidates
    ] == [
        (second.material_id, 88, True),
        (third.material_id, 88, True),
        (first.material_id, 60, True),
    ]
    assert [candidate.qualified for candidate in result.sentences[1].candidates] == [
        False,
        False,
        False,
    ]


def test_all_legal_script_sentences_are_matched_in_fixed_batches() -> None:
    adapter = _ScoringAdapter()

    result = match_script_materials(
        cast(SemanticMatchingAdapter, adapter),
        _script(17),
        (_material(),),
        options=SemanticMatchingOptions(enable_thinking=True),
    )

    assert [len(request.sentences) for request, _options in adapter.requests] == [16, 1]
    assert all(options.enable_thinking is True for _request, options in adapter.requests)
    assert result.request_ids == ("req-match-1", "req-match-2")
    assert tuple(sentence.sequence for sentence in result.sentences) == tuple(range(1, 18))


@pytest.mark.parametrize(
    "materials",
    [
        (),
        (_material(kind=MaterialKind.AUDIO, digest_character="b"),),
        (_material(description=None, digest_character="c"),),
    ],
    ids=["empty", "audio", "missing-description"],
)
def test_invalid_candidates_are_rejected_before_the_paid_call(
    materials: tuple[Material, ...],
) -> None:
    adapter = _ScoringAdapter()

    with pytest.raises(
        SemanticMatchingRejected,
        match=r"^semantic matching request rejected$",
    ) as raised:
        match_script_materials(
            cast(SemanticMatchingAdapter, adapter),
            _script(1),
            materials,
            options=SemanticMatchingOptions(),
        )

    assert adapter.requests == []
    assert raised.value.__context__ is None


def test_duplicate_and_too_many_candidates_are_rejected_before_the_paid_call() -> None:
    duplicate_id = MaterialId.new()
    adapter = _ScoringAdapter()
    duplicate = (
        _material(material_id=duplicate_id, digest_character="a"),
        _material(material_id=duplicate_id, digest_character="b"),
    )

    for materials in (
        duplicate,
        tuple(_material(digest_character=f"{index:x}"[-1]) for index in range(33)),
    ):
        with pytest.raises(SemanticMatchingRejected):
            match_script_materials(
                cast(SemanticMatchingAdapter, adapter),
                _script(1),
                materials,
                options=SemanticMatchingOptions(),
            )

    assert adapter.requests == []


def test_oversized_complete_evidence_is_rejected_before_the_first_paid_call() -> None:
    adapter = _ScoringAdapter()
    materials = tuple(
        _material(
            has_speech=True,
            transcript="转" * 100_000,
            digest_character=f"{index:x}"[-1],
        )
        for index in range(22)
    )

    with pytest.raises(SemanticMatchingRejected):
        match_script_materials(
            cast(SemanticMatchingAdapter, adapter),
            _script(1),
            materials,
            options=SemanticMatchingOptions(),
        )

    assert adapter.requests == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda scores: scores.pop(),
        lambda scores: scores.append(dict(scores[0])),
        lambda scores: scores.__setitem__(0, {**scores[0], "candidateKey": "unknown"}),
        lambda scores: scores.__setitem__(0, {**scores[0], "sentenceSequence": 999}),
        lambda scores: scores.__setitem__(0, {**scores[0], "score": -1}),
        lambda scores: scores.__setitem__(0, {**scores[0], "score": True}),
        lambda scores: scores.__setitem__(0, {**scores[0], "extra": "field"}),
    ],
    ids=[
        "missing",
        "duplicate",
        "unknown-candidate",
        "unknown-sentence",
        "negative-score",
        "boolean-score",
        "extra-field",
    ],
)
def test_score_matrix_is_closed_and_complete(
    mutate: Callable[[list[dict[str, object]]], object],
) -> None:
    class BrokenAdapter:
        def match(
            self,
            request: SemanticMatchingRequest,
            *,
            options: SemanticMatchingOptions,
        ) -> SemanticMatchingReply:
            del options
            document = json.loads(_score_content(request))
            scores = cast(list[dict[str, object]], document["scores"])
            mutate(scores)
            return SemanticMatchingReply(
                request_id="req-broken",
                content=json.dumps(document),
                finish_reason="stop",
            )

    with pytest.raises(
        SemanticMatchingRejected,
        match=r"^semantic matching request rejected$",
    ) as raised:
        match_script_materials(
            cast(SemanticMatchingAdapter, BrokenAdapter()),
            _script(1),
            (_material(),),
            options=SemanticMatchingOptions(),
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "{}",
        '{"scores":{}}',
        '{"scores":[],"scores":[]}',
        '{"scores":[null]}',
        "{",
    ],
    ids=[
        "not-object",
        "missing-field",
        "not-list",
        "duplicate-key",
        "non-object-item",
        "malformed-json",
    ],
)
def test_score_document_shape_is_closed(content: str) -> None:
    class RawAdapter:
        def match(
            self,
            request: SemanticMatchingRequest,
            *,
            options: SemanticMatchingOptions,
        ) -> SemanticMatchingReply:
            del request, options
            return SemanticMatchingReply(
                request_id="req-raw",
                content=content,
                finish_reason="stop",
            )

    with pytest.raises(SemanticMatchingRejected) as raised:
        match_script_materials(
            cast(SemanticMatchingAdapter, RawAdapter()),
            _script(1),
            (_material(),),
            options=SemanticMatchingOptions(),
        )

    assert raised.value.__context__ is None


def test_adapter_failure_and_duplicate_batch_ids_are_fixed() -> None:
    private_path = "/Users/operator/private/source.mp4"

    class FailedAdapter:
        def match(
            self,
            request: SemanticMatchingRequest,
            *,
            options: SemanticMatchingOptions,
        ) -> SemanticMatchingReply:
            del request, options
            raise OSError(private_path)

    with pytest.raises(SemanticMatchingRejected) as raised:
        match_script_materials(
            cast(SemanticMatchingAdapter, FailedAdapter()),
            _script(1),
            (_material(),),
            options=SemanticMatchingOptions(),
        )
    assert raised.value.__context__ is None
    assert private_path not in str(raised.value)

    class DuplicateIdAdapter(_ScoringAdapter):
        def match(
            self,
            request: SemanticMatchingRequest,
            *,
            options: SemanticMatchingOptions,
        ) -> SemanticMatchingReply:
            super().match(request, options=options)
            return SemanticMatchingReply(
                request_id="req-duplicate",
                content=_score_content(request),
                finish_reason="stop",
            )

    with pytest.raises(SemanticMatchingRejected):
        match_script_materials(
            cast(SemanticMatchingAdapter, DuplicateIdAdapter()),
            _script(17),
            (_material(),),
            options=SemanticMatchingOptions(),
        )


@pytest.mark.parametrize(
    "construct",
    [
        lambda: SemanticMatchingOptions(max_output_tokens=0),
        lambda: SemanticMatchingSentence(sequence=0, text="句子。"),
        lambda: SemanticMatchingSentence(sequence=1, text=""),
        lambda: SemanticMatchingCandidate(
            candidate_key="material-1",
            description="描述",
            tags=(),
            transcript=None,
        ),
        lambda: SemanticMatchingCandidate(
            candidate_key="m0001",
            description="描述",
            tags=("重复", "重复"),
            transcript=None,
        ),
        lambda: SemanticMatchingRequest.from_parts(
            sentences=cast(tuple[tuple[int, str], ...], ((1,),)),
            candidates=(("m0001", "描述", (), None),),
        ),
        lambda: SemanticMatchingRequest.from_parts(
            sentences=((1, "句子。"), (1, "重复序号。")),
            candidates=(("m0001", "描述", (), None),),
        ),
        lambda: SemanticMatchingReply(
            request_id="req-large",
            content="中" * 100_000,
            finish_reason="stop",
        ),
        lambda: SemanticCandidateScore(
            material_id=MaterialId.new(),
            score=60,
            qualified=False,
        ),
        lambda: SemanticSentenceMatches(sequence=0, candidates=()),
        lambda: SemanticMatchingResult(request_ids=(), sentences=()),
    ],
    ids=[
        "token-budget",
        "sentence-sequence",
        "sentence-text",
        "candidate-key",
        "candidate-tags",
        "malformed-parts",
        "duplicate-sentence",
        "reply-byte-budget",
        "qualification-drift",
        "sentence-result",
        "empty-result",
    ],
)
def test_public_value_boundaries_fail_closed(construct: Callable[[], object]) -> None:
    with pytest.raises(SemanticMatchingRejected):
        construct()


class _Response:
    def __init__(
        self,
        body: dict[str, object] | bytes,
        *,
        content_length: str | None = None,
    ) -> None:
        self._body = (
            body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        )
        self.headers = Message()
        if content_length is not None:
            self.headers.add_header("Content-Length", content_length)
        else:
            self.headers.add_header("Content-Length", str(len(self._body)))

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_bailian_request_is_closed_and_follows_thinking(
    enable_thinking: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[urllib.request.Request, float]] = []

    def open_request(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _Response:
        calls.append((request, timeout))
        return _Response(
            {
                "id": "req-bailian-match",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"scores":[{"sentenceSequence":1,'
                                '"candidateKey":"m0001","score":91}]}'
                            )
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(semantic_matching_module, "_open_request", open_request)
    config = load_bailian_semantic_matching_config(
        catalog_path=CATALOG_PATH,
        api_key=API_KEY,
        model_id=None,
        timeout_seconds=12.5,
    )
    request = SemanticMatchingRequest.from_parts(
        sentences=((1, "海边日落。"),),
        candidates=(("m0001", "海边日落", ("海边",), None),),
    )

    reply = BailianSemanticMatchingAdapter(config).match(
        request,
        options=SemanticMatchingOptions(enable_thinking=enable_thinking),
    )

    assert reply.request_id == "req-bailian-match"
    assert len(calls) == 1
    transport_request, timeout = calls[0]
    assert transport_request.full_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert timeout == 12.5
    assert transport_request.get_header("Authorization") == f"Bearer {API_KEY}"
    body = json.loads(cast(bytes, transport_request.data))
    assert set(body) == {
        "enable_thinking",
        "max_tokens",
        "messages",
        "model",
        "response_format",
        "stream",
        "temperature",
    }
    assert body["enable_thinking"] is enable_thinking
    assert body["messages"][1] == {
        "role": "user",
        "content": json.dumps(request.to_wire(), ensure_ascii=False, separators=(",", ":")),
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"scores":[]}'},
                }
            ],
        },
        {"id": "req", "choices": [{"finish_reason": "stop"}]},
        {
            "id": "req",
            "choices": [{"finish_reason": "stop", "message": {}}],
        },
        {
            "id": "req",
            "choices": [{"message": {"content": '{"scores":[]}'}}],
        },
        {"id": "req", "choices": []},
        {"id": "req", "choices": [7]},
        {"id": "req", "choices": [{"finish_reason": "stop", "message": 7}]},
        {
            "id": "req",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"scores":[]}', "refusal": "blocked"},
                }
            ],
        },
        b"\xff",
    ],
    ids=[
        "missing-choices",
        "missing-id",
        "missing-message",
        "missing-content",
        "missing-finish-reason",
        "no-choice",
        "bad-choice",
        "bad-message",
        "refusal",
        "invalid-utf8",
    ],
)
def test_bailian_response_shape_and_refusal_fail_closed(
    body: dict[str, object] | bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        semantic_matching_module,
        "_open_request",
        lambda *_args, **_kwargs: _Response(body),
    )
    adapter = BailianSemanticMatchingAdapter(
        load_bailian_semantic_matching_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=12.5,
        )
    )
    request = SemanticMatchingRequest.from_parts(
        sentences=((1, "句子。"),),
        candidates=(("m0001", "描述", (), None),),
    )

    with pytest.raises(SemanticMatchingRejected):
        adapter.match(request, options=SemanticMatchingOptions())


def test_bailian_transport_and_declared_length_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SemanticMatchingRequest.from_parts(
        sentences=((1, "句子。"),),
        candidates=(("m0001", "描述", (), None),),
    )
    adapter = BailianSemanticMatchingAdapter(
        load_bailian_semantic_matching_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=12.5,
        )
    )
    for transport in (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private path")),
        lambda *_args, **_kwargs: _Response(b"{}", content_length="999"),
    ):
        monkeypatch.setattr(semantic_matching_module, "_open_request", transport)
        with pytest.raises(SemanticMatchingRejected) as raised:
            adapter.match(request, options=SemanticMatchingOptions())
        assert raised.value.__context__ is None


def test_config_loader_and_adapter_public_inputs_are_fixed(tmp_path: Path) -> None:
    with pytest.raises(SemanticMatchingRejected):
        load_bailian_semantic_matching_config(
            catalog_path=cast(Path, object()),
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=12.5,
        )
    with pytest.raises(SemanticMatchingRejected) as raised:
        load_bailian_semantic_matching_config(
            catalog_path=tmp_path / "missing.json",
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=12.5,
        )
    assert raised.value.__context__ is None

    with pytest.raises(SemanticMatchingRejected):
        BailianSemanticMatchingAdapter(cast(BailianSemanticMatchingConfig, object()))

    adapter = BailianSemanticMatchingAdapter(
        load_bailian_semantic_matching_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=12.5,
        )
    )
    with pytest.raises(SemanticMatchingRejected):
        adapter.match(
            cast(SemanticMatchingRequest, object()),
            options=SemanticMatchingOptions(),
        )
    oversized_request = SemanticMatchingRequest.from_parts(
        sentences=((1, "句子。"),),
        candidates=tuple((f"m{index:04d}", "描述", (), "转" * 100_000) for index in range(1, 23)),
    )
    with pytest.raises(SemanticMatchingRejected):
        adapter.match(oversized_request, options=SemanticMatchingOptions())
    with pytest.raises(SemanticMatchingRejected):
        match_script_materials(
            cast(SemanticMatchingAdapter, object()),
            _script(1),
            (_material(),),
            options=SemanticMatchingOptions(),
        )


def test_http_helpers_reject_redirects_and_ambiguous_lengths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock()
    marker = cast(http.client.HTTPResponse, object())
    opener.open.return_value = marker
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: opener)
    transport_request = urllib.request.Request("https://example.invalid")

    assert semantic_matching_module._open_request(transport_request, timeout=1.5) is marker
    opener.open.assert_called_once_with(transport_request, timeout=1.5)

    stream: IO[bytes] = io.BytesIO()
    with pytest.raises(SemanticMatchingRejected):
        semantic_matching_module._RejectRedirectHandler().redirect_request(
            transport_request,
            stream,
            302,
            "redirect",
            cast(http.client.HTTPMessage, Message()),
            "https://other.invalid",
        )
    assert stream.closed

    assert semantic_matching_module._declared_response_bytes(object()) is None
    missing_getter = MagicMock()
    missing_getter.headers = object()
    with pytest.raises(SemanticMatchingRejected):
        semantic_matching_module._declared_response_bytes(missing_getter)
    without_length = MagicMock()
    without_length.headers.get_all.return_value = None
    assert semantic_matching_module._declared_response_bytes(without_length) is None
    for raw_values in (
        ["1", "2"],
        ["1,2"],
        ["not-a-number"],
    ):
        response = MagicMock()
        response.headers.get_all.return_value = raw_values
        with pytest.raises(SemanticMatchingRejected):
            semantic_matching_module._declared_response_bytes(response)


def test_fixed_error_covers_invalid_public_values_and_non_stop_reply() -> None:
    with pytest.raises(SemanticMatchingRejected):
        SemanticMatchingOptions(enable_thinking=cast(bool, 1))
    with pytest.raises(SemanticMatchingRejected):
        BailianSemanticMatchingConfig(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id=cast(str, []),
            api_key=API_KEY,
            timeout_seconds=12.5,
        )

    class TruncatedAdapter:
        def match(
            self,
            request: SemanticMatchingRequest,
            *,
            options: SemanticMatchingOptions,
        ) -> SemanticMatchingReply:
            del request, options
            return SemanticMatchingReply(
                request_id="req-truncated",
                content='{"scores":[]}',
                finish_reason="length",
            )

    with pytest.raises(SemanticMatchingRejected) as raised:
        match_script_materials(
            cast(SemanticMatchingAdapter, TruncatedAdapter()),
            _script(1),
            (_material(),),
            options=SemanticMatchingOptions(),
        )
    assert raised.value.__context__ is None
