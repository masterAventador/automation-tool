"""LE-15 T1: supplier-neutral script segmentation and Bailian adapter."""

from __future__ import annotations

import http.client
import json
import os
import traceback
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import IO, cast
from unittest.mock import MagicMock

import pytest

from automation_tool.executor import script_segmentation as script_segmentation_module
from automation_tool.executor.motion_authoring.voiceover import MAX_NARRATION_CHARS
from automation_tool.executor.script_segmentation import (
    BailianScriptSegmentationAdapter,
    BailianScriptSegmentationConfig,
    ScriptSegmentationAdapter,
    ScriptSegmentationOptions,
    ScriptSegmentationRejected,
    ScriptSegmentationReply,
    load_bailian_script_segmentation_config,
    segment_script,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
API_KEY = "sk-private-script-segmentation-key-123456"


class _Response:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.headers = Message()
        self.headers.add_header("Content-Length", str(len(self._body)))

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


def _response(
    *,
    content: str = '{"sentences":["先介绍产品。","再说明优势。"]}',
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> _Response:
    message: dict[str, object] = {"content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return _Response(
        {
            "id": "req-script-001",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": message,
                }
            ],
        }
    )


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: _Response | None = None,
) -> list[tuple[urllib.request.Request, float]]:
    calls: list[tuple[urllib.request.Request, float]] = []

    def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
        calls.append((request, timeout))
        return response or _response()

    monkeypatch.setattr(script_segmentation_module, "_open_request", open_request)
    return calls


def _adapter(
    *,
    model_id: str | None = None,
) -> BailianScriptSegmentationAdapter:
    return BailianScriptSegmentationAdapter(
        load_bailian_script_segmentation_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=model_id,
            timeout_seconds=12.5,
        )
    )


def test_bailian_adapter_satisfies_the_supplier_neutral_protocol() -> None:
    adapter = _adapter()

    assert isinstance(adapter, ScriptSegmentationAdapter)
    assert "Bailian" not in ScriptSegmentationAdapter.__name__
    assert "OpenAI" not in ScriptSegmentationAdapter.__name__
    assert API_KEY not in repr(adapter)
    assert API_KEY not in repr(adapter.config)
    assert script_segmentation_module.MAX_SCRIPT_SENTENCE_CHARACTERS == MAX_NARRATION_CHARS


def test_public_config_constructor_rejects_malformed_types_with_the_fixed_error() -> None:
    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        BailianScriptSegmentationConfig(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id=cast(str, []),
            api_key=API_KEY,
            timeout_seconds=10,
        )

    assert raised.value.__context__ is None


def test_default_token_budget_covers_the_maximum_legal_script() -> None:
    options = ScriptSegmentationOptions()

    assert options.max_output_tokens == 16_384


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_request_body_follows_thinking_and_contains_only_the_script_text(
    enable_thinking: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_transport(monkeypatch)
    private_path = "/Users/operator/Private Videos/source clip.mp4"
    text = "请把这段产品介绍整理成两句旁白。"

    reply = _adapter().segment(
        text,
        options=ScriptSegmentationOptions(enable_thinking=enable_thinking),
    )

    assert reply.request_id == "req-script-001"
    assert reply.finish_reason == "stop"
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert timeout == 12.5
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.data is not None
    request_data = cast(bytes, request.data)
    body = json.loads(request_data)
    assert set(body) == {
        "enable_thinking",
        "max_tokens",
        "messages",
        "model",
        "response_format",
        "stream",
        "temperature",
    }
    assert body["model"] == "qwen3.7-max-2026-06-08"
    assert body["enable_thinking"] is enable_thinking
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    assert body["messages"][1] == {"role": "user", "content": text}
    encoded = request_data.decode("utf-8")
    assert private_path not in encoded
    assert "video" not in encoded.casefold()
    assert "browser" not in encoded.casefold()


def test_catalog_default_and_explicit_allowed_text_models_are_selected(
    tmp_path: Path,
) -> None:
    default_config = load_bailian_script_segmentation_config(
        catalog_path=CATALOG_PATH,
        api_key=API_KEY,
        model_id=None,
        timeout_seconds=10,
    )
    selected_config = load_bailian_script_segmentation_config(
        catalog_path=CATALOG_PATH,
        api_key=API_KEY,
        model_id="glm-5.2",
        timeout_seconds=10,
    )
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["base_url"] = "http://private.invalid"
    drifted = tmp_path / "catalog.json"
    drifted.write_text(json.dumps(catalog), encoding="utf-8")

    assert default_config.model_id == "qwen3.7-max-2026-06-08"
    assert selected_config.model_id == "glm-5.2"
    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ):
        load_bailian_script_segmentation_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id="unlisted-model",
            timeout_seconds=10,
        )
    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ):
        load_bailian_script_segmentation_config(
            catalog_path=drifted,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )


@pytest.mark.parametrize(
    "drift",
    [
        "duplicate-script-purpose",
        "duplicate-model-record",
        "non-string-allowed-model",
        "text-capability-removed",
    ],
)
def test_ambiguous_or_malformed_catalog_records_are_fixed_failures(
    drift: str,
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    purposes = catalog["purposes"]
    models = catalog["models"]
    script_purpose = next(item for item in purposes if item["id"] == "script")
    if drift == "duplicate-script-purpose":
        purposes.append(dict(script_purpose))
    elif drift == "duplicate-model-record":
        models.append(dict(models[0]))
    elif drift == "non-string-allowed-model":
        script_purpose["allowed_model_ids"].append({"id": "unexpected"})
    else:
        next(item for item in models if item["id"] == "glm-5.2")["text"] = False
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        load_bailian_script_segmentation_config(
            catalog_path=path,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )

    assert raised.value.__context__ is None


class _StaticAdapter:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.calls = 0

    def segment(
        self,
        text: str,
        *,
        options: ScriptSegmentationOptions,
    ) -> ScriptSegmentationReply:
        del text, options
        self.calls += 1
        return ScriptSegmentationReply(
            request_id="req-static-001",
            content=self.content,
            finish_reason=self.finish_reason,
        )


def test_strict_result_parsing_assigns_contiguous_local_sequences() -> None:
    result = segment_script(
        _StaticAdapter('{"sentences":["第一句。","第二句。"]}'),
        "请生成两句旁白。",
        options=ScriptSegmentationOptions(),
    )

    assert result.request_id == "req-static-001"
    assert tuple((item.sequence, item.text) for item in result.sentences) == (
        (1, "第一句。"),
        (2, "第二句。"),
    )


@pytest.mark.parametrize(
    "content",
    [
        '{"sentences":[]}',
        '{"sentences":[""]}',
        '{"sentences":[" 首尾空白"]}',
        '{"sentences":["合法"],"extra":true}',
        '{"sentences":["第一句"],"sentences":["第二句"]}',
        '{"sentences":[1]}',
    ],
)
def test_malformed_or_open_ended_results_are_rejected(content: str) -> None:
    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ):
        segment_script(
            _StaticAdapter(content),
            "请生成旁白。",
            options=ScriptSegmentationOptions(),
        )


def test_result_quantity_and_text_budgets_are_fail_closed() -> None:
    too_many = json.dumps(
        {"sentences": ["句"] * (script_segmentation_module.MAX_SCRIPT_SENTENCES + 1)},
        ensure_ascii=False,
    )
    too_long = json.dumps(
        {"sentences": ["句" * (script_segmentation_module.MAX_SCRIPT_SENTENCE_CHARACTERS + 1)]},
        ensure_ascii=False,
    )
    too_large_total = json.dumps(
        {"sentences": ["句" * script_segmentation_module.MAX_SCRIPT_SENTENCE_CHARACTERS] * 5},
        ensure_ascii=False,
    )

    for content in (too_many, too_long, too_large_total):
        with pytest.raises(
            ScriptSegmentationRejected,
            match=r"^script segmentation request rejected$",
        ):
            segment_script(
                _StaticAdapter(content),
                "请生成旁白。",
                options=ScriptSegmentationOptions(),
            )


def test_invalid_input_is_rejected_before_the_adapter_is_called() -> None:
    adapter = _StaticAdapter('{"sentences":["不会调用"]}')

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ):
        segment_script(
            adapter,
            "x" * (script_segmentation_module.MAX_SCRIPT_PROMPT_CHARACTERS + 1),
            options=ScriptSegmentationOptions(),
        )

    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_response(refusal="cannot comply"), "refusal"),
        (_response(finish_reason="length"), "length"),
    ],
)
def test_refusal_and_incomplete_reply_are_fixed_failures(
    response: _Response,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(monkeypatch, response=response)

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        reply = _adapter().segment(
            "请生成旁白。",
            options=ScriptSegmentationOptions(),
        )
        if expected == "length":
            segment_script(
                _StaticAdapter(reply.content, finish_reason=reply.finish_reason),
                "请生成旁白。",
                options=ScriptSegmentationOptions(),
            )

    assert raised.value.__context__ is None


def test_transport_failure_has_no_secret_path_or_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = "/Users/operator/Private Videos/source clip.mp4"

    def fail_transport(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _Response:
        del timeout
        raise OSError(f"{API_KEY}: {private_path}")

    monkeypatch.setattr(script_segmentation_module, "_open_request", fail_transport)

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        _adapter().segment(
            "请生成旁白。",
            options=ScriptSegmentationOptions(),
        )

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert raised.value.__context__ is None
    assert API_KEY not in rendered
    assert private_path not in rendered
    assert API_KEY not in repr(raised.value)
    assert private_path not in repr(raised.value)


def test_oversized_catalog_is_rejected_before_any_unbounded_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "oversized-catalog.json"
    oversized.write_bytes(b"{" + b"x" * script_segmentation_module._MAX_CATALOG_BYTES)

    def fail_unbounded_read(_path: Path) -> bytes:
        raise AssertionError("catalog loader attempted an unbounded read")

    monkeypatch.setattr(Path, "read_bytes", fail_unbounded_read)

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        load_bailian_script_segmentation_config(
            catalog_path=oversized,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_catalog_schema_version_requires_an_exact_integer(
    schema_version: object,
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["schema_version"] = schema_version
    drifted = tmp_path / "catalog.json"
    drifted.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        load_bailian_script_segmentation_config(
            catalog_path=drifted,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_mode", "dashscope_native"),
        ("base_url", "https://attacker.invalid/v1"),
    ],
)
def test_script_purpose_cannot_override_the_locked_endpoint(
    field: str,
    value: str,
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    purpose = next(item for item in catalog["purposes"] if item["id"] == "script")
    purpose[field] = value
    drifted = tmp_path / "catalog.json"
    drifted.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        load_bailian_script_segmentation_config(
            catalog_path=drifted,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "timeout_seconds",
    [
        True,
        "/Users/operator/Private Videos/source clip.mp4",
        10**400,
    ],
)
def test_dynamic_timeout_is_validated_before_float_conversion(
    timeout_seconds: object,
) -> None:
    private_path = "/Users/operator/Private Videos/source clip.mp4"

    with pytest.raises(
        ScriptSegmentationRejected,
        match=r"^script segmentation request rejected$",
    ) as raised:
        load_bailian_script_segmentation_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=cast(float, timeout_seconds),
        )

    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert raised.value.__context__ is None
    assert private_path not in rendered


def test_equivalent_escaped_json_can_carry_the_maximum_legal_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = json.dumps(
        {"sentences": ["😀" * MAX_NARRATION_CHARS] * 4},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    _install_transport(monkeypatch, response=_response(content=content))

    reply = _adapter().segment(
        "请生成旁白。",
        options=ScriptSegmentationOptions(),
    )
    result = segment_script(
        _StaticAdapter(reply.content),
        "请生成旁白。",
        options=ScriptSegmentationOptions(),
    )

    assert len(result.sentences) == 4
    assert sum(len(sentence.text) for sentence in result.sentences) == 4_000


def test_catalog_path_is_explicit_and_not_discovered_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_catalog = tmp_path / "private-catalog.json"
    monkeypatch.setenv("AUTOMATION_TOOL_BAILIAN_CATALOG", os.fspath(private_catalog))

    config = load_bailian_script_segmentation_config(
        catalog_path=CATALOG_PATH,
        api_key=API_KEY,
        model_id=None,
        timeout_seconds=10,
    )

    assert config.model_id == "qwen3.7-max-2026-06-08"


def test_redirects_are_closed_and_rejected() -> None:
    response = MagicMock()

    with pytest.raises(ScriptSegmentationRejected):
        script_segmentation_module._RejectRedirectHandler().redirect_request(
            urllib.request.Request("https://dashscope.aliyuncs.com/original"),
            cast(IO[bytes], response),
            302,
            "redirect",
            cast(http.client.HTTPMessage, Message()),
            "https://dashscope.aliyuncs.com/redirected",
        )

    response.close.assert_called_once_with()


def test_default_transport_builds_the_redirect_rejecting_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock()
    expected = _response()
    opener.open.return_value = expected
    build_opener = MagicMock(return_value=opener)
    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    request = urllib.request.Request("https://dashscope.aliyuncs.com/request")

    actual = script_segmentation_module._open_request(request, timeout=3.5)

    assert cast(object, actual) is expected
    handler = build_opener.call_args.args[0]
    assert isinstance(handler, script_segmentation_module._RejectRedirectHandler)
    opener.open.assert_called_once_with(request, timeout=3.5)


class _DeclaredLengthResponse:
    def __init__(self, headers: object) -> None:
        self.headers = headers


class _DeclaredLengthHeaders:
    def __init__(self, values: object) -> None:
        self.values = values

    def get_all(self, _name: str) -> object:
        return self.values


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (object(), None),
        (_DeclaredLengthResponse(_DeclaredLengthHeaders(None)), None),
        (_DeclaredLengthResponse(_DeclaredLengthHeaders(["42"])), 42),
    ],
)
def test_declared_response_length_accepts_only_an_optional_single_decimal(
    response: object,
    expected: int | None,
) -> None:
    assert script_segmentation_module._declared_response_bytes(response) == expected


@pytest.mark.parametrize(
    "headers",
    [
        object(),
        _DeclaredLengthHeaders(("42",)),
        _DeclaredLengthHeaders(["41", "42"]),
        _DeclaredLengthHeaders([42]),
        _DeclaredLengthHeaders(["4,2"]),
        _DeclaredLengthHeaders([""]),
        _DeclaredLengthHeaders(["\uff11\uff12"]),
        _DeclaredLengthHeaders(["4.2"]),
    ],
)
def test_invalid_declared_response_lengths_fail_closed(headers: object) -> None:
    with pytest.raises(ScriptSegmentationRejected):
        script_segmentation_module._declared_response_bytes(_DeclaredLengthResponse(headers))


@pytest.mark.parametrize(
    "construct",
    [
        lambda: ScriptSegmentationOptions(enable_thinking=cast(bool, 1)),
        lambda: ScriptSegmentationOptions(max_output_tokens=0),
        lambda: script_segmentation_module.ScriptSentence(sequence=0, text="句子"),
    ],
)
def test_public_value_objects_reject_invalid_scalar_boundaries(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(ScriptSegmentationRejected):
        construct()


def test_orchestrator_and_adapter_reject_wrong_protocol_objects() -> None:
    with pytest.raises(ScriptSegmentationRejected):
        segment_script(
            cast(ScriptSegmentationAdapter, object()),
            "旁白",
            options=ScriptSegmentationOptions(),
        )
    with pytest.raises(ScriptSegmentationRejected):
        BailianScriptSegmentationAdapter(cast(BailianScriptSegmentationConfig, object()))
    with pytest.raises(ScriptSegmentationRejected):
        _adapter().segment(
            "旁白",
            options=cast(ScriptSegmentationOptions, object()),
        )


def test_missing_or_unreadable_catalogs_are_fixed_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ScriptSegmentationRejected):
        load_bailian_script_segmentation_config(
            catalog_path=missing,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )

    original_stat = Path.stat

    def fail_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if path == CATALOG_PATH:
            raise OSError("private catalog path")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_stat)
    with pytest.raises(ScriptSegmentationRejected):
        load_bailian_script_segmentation_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )


def test_catalog_growth_between_stat_and_read_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "growing.json"
    catalog.write_bytes(b"x" * (script_segmentation_module._MAX_CATALOG_BYTES + 1))
    original_stat = Path.stat

    def stale_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        metadata = original_stat(path, follow_symlinks=follow_symlinks)
        if path != catalog:
            return metadata
        values = list(metadata)
        values[6] = 1
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", stale_stat)

    with pytest.raises(ScriptSegmentationRejected):
        load_bailian_script_segmentation_config(
            catalog_path=catalog,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )


def test_catalog_model_records_require_string_ids(
    tmp_path: Path,
) -> None:
    document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    document["models"][0]["id"] = 1
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ScriptSegmentationRejected):
        load_bailian_script_segmentation_config(
            catalog_path=catalog,
            api_key=API_KEY,
            model_id=None,
            timeout_seconds=10,
        )


@pytest.mark.parametrize(
    "document",
    [
        {
            "id": "request",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"sentences":["旁白"]}'},
                }
            ],
        },
        {"id": "request", "choices": []},
        {"id": "request", "choices": ["not-an-object"]},
        {
            "id": "request",
            "choices": [{"finish_reason": "stop", "message": "not-an-object"}],
        },
    ],
)
def test_adapter_rejects_response_length_and_nested_shape_drift(
    document: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(document)
    if document["choices"]:
        first = cast(list[object], document["choices"])[0]
        if isinstance(first, dict) and isinstance(first.get("message"), dict):
            response.headers.replace_header("Content-Length", "1")
    _install_transport(monkeypatch, response=response)

    with pytest.raises(ScriptSegmentationRejected):
        _adapter().segment("旁白", options=ScriptSegmentationOptions())
