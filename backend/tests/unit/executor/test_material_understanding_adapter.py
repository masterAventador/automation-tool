"""LE-13 T1: supplier-neutral material-understanding adapter boundary."""

from __future__ import annotations

import http.client
import inspect
import io
import json
import os
import traceback
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor import material_understanding as material_understanding_module
from automation_tool.executor.material_understanding import (
    BailianMaterialUnderstandingAdapter,
    BailianMaterialUnderstandingConfig,
    MaterialUnderstandingAdapter,
    MaterialUnderstandingFrame,
    MaterialUnderstandingOptions,
    MaterialUnderstandingRejected,
    load_bailian_material_understanding_config,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
API_KEY = "sk-ws-private-material-understanding-key-123456"


class _Response:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self._body


def _reply() -> _Response:
    return _Response(
        {
            "id": "req-material-001",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            '{"description":"室内产品展示","tags":["室内","产品"],'
                            '"shots":[{"start_ms":0,"end_ms":1000,"description":"正面"}]}'
                        )
                    },
                }
            ],
        }
    )


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[urllib.request.Request, float]]:
    calls: list[tuple[urllib.request.Request, float]] = []

    def open_request(request: urllib.request.Request, *, timeout: float) -> _Response:
        calls.append((request, timeout))
        return _reply()

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)
    return calls


def _adapter() -> BailianMaterialUnderstandingAdapter:
    return BailianMaterialUnderstandingAdapter(
        load_bailian_material_understanding_config(
            catalog_path=CATALOG_PATH,
            api_key=API_KEY,
            timeout_seconds=12.5,
        )
    )


def test_bailian_implementation_satisfies_the_supplier_neutral_protocol() -> None:
    adapter = _adapter()

    assert isinstance(adapter, MaterialUnderstandingAdapter)
    assert "Bailian" not in MaterialUnderstandingAdapter.__name__
    assert "OpenAI" not in MaterialUnderstandingAdapter.__name__
    assert "DashScope" not in MaterialUnderstandingAdapter.__name__
    assert API_KEY not in repr(adapter)
    assert API_KEY not in repr(adapter.config)


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_request_body_follows_the_thinking_configuration(
    enable_thinking: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_transport(monkeypatch)
    private_path = "/Users/operator/Private Videos/source clip.mp4"
    jpeg = b"\xff\xd8\xff\xe0private-frame\xff\xd9"

    reply = _adapter().understand(
        (
            MaterialUnderstandingFrame(
                timestamp_ms=0,
                is_scene_cut=True,
                jpeg_bytes=jpeg,
            ),
        ),
        options=MaterialUnderstandingOptions(enable_thinking=enable_thinking),
    )

    assert reply.request_id == "req-material-001"
    assert reply.finish_reason == "stop"
    assert "室内产品展示" in reply.content
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
    assert body["model"] == "qwen3.7-max-2026-06-08"
    assert body["enable_thinking"] is enable_thinking
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    encoded = request_data.decode("utf-8")
    assert private_path not in encoded
    assert "timestamp_ms=0" in encoded
    assert "is_scene_cut=true" in encoded
    image_part = body["messages"][1]["content"][2]
    assert image_part == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,/9j/4HByaXZhdGUtZnJhbWX/2Q==",
        },
    }


def test_catalog_drift_and_a_text_only_model_are_rejected(
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    purpose = next(item for item in catalog["purposes"] if item["id"] == "video_creative")
    purpose["default_model_id"] = "glm-5.2"
    purpose["allowed_model_ids"] = ["glm-5.2"]
    changed = tmp_path / "catalog.json"
    changed.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(MaterialUnderstandingRejected):
        load_bailian_material_understanding_config(
            catalog_path=changed,
            api_key=API_KEY,
            timeout_seconds=12.5,
        )

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["base_url"] = "https://model-proxy.example/v1"
    changed.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(MaterialUnderstandingRejected):
        load_bailian_material_understanding_config(
            catalog_path=changed,
            api_key=API_KEY,
            timeout_seconds=12.5,
        )


def test_transport_failure_is_fixed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_transport(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _Response:
        raise OSError(
            f"private upstream error {API_KEY} after {timeout} "
            "/Users/operator/Private Videos/source clip.mp4"
        )

    monkeypatch.setattr(material_understanding_module, "_open_request", fail_transport)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ) as captured:
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )

    assert API_KEY not in str(captured.value)
    assert API_KEY not in repr(captured.value)
    assert os.fspath(REPOSITORY_ROOT) not in str(captured.value)
    formatted = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert API_KEY not in formatted
    assert "/Users/operator/Private Videos/source clip.mp4" not in formatted
    assert captured.value.__context__ is None


def test_direct_config_cannot_bypass_the_locked_endpoint() -> None:
    with pytest.raises(MaterialUnderstandingRejected):
        BailianMaterialUnderstandingConfig(
            base_url="http://attacker.invalid/v1",
            model_id="attacker-model",
            api_key=API_KEY,
            timeout_seconds=-1,
        )


def test_transport_installs_a_redirect_rejecting_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_handlers: list[urllib.request.BaseHandler] = []

    class _Opener:
        def open(
            self,
            _request: urllib.request.Request,
            *,
            timeout: float,
        ) -> _Response:
            assert timeout == 12.5
            return _reply()

    def build_opener(
        *handlers: urllib.request.BaseHandler,
    ) -> _Opener:
        installed_handlers.extend(handlers)
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    response = material_understanding_module._open_request(
        urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        ),
        timeout=12.5,
    )

    assert isinstance(response, _Response)
    assert len(installed_handlers) == 1
    redirect_handler = installed_handlers[0]
    assert isinstance(redirect_handler, urllib.request.HTTPRedirectHandler)
    with pytest.raises(MaterialUnderstandingRejected):
        redirect_handler.redirect_request(
            urllib.request.Request(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                data=b'{"private":"material"}',
                headers={"Authorization": f"Bearer {API_KEY}"},
            ),
            io.BytesIO(),
            307,
            "Temporary Redirect",
            http.client.HTTPMessage(),
            "https://attacker.invalid/collect",
        )


def test_model_response_is_bounded_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_sizes: list[int] = []

    class _OversizedResponse:
        def __enter__(self) -> _OversizedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"x" * (300_000 if size < 0 else size)

    def open_request(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _OversizedResponse:
        assert timeout == 12.5
        return _OversizedResponse()

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)

    with pytest.raises(MaterialUnderstandingRejected):
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )

    assert read_sizes == [262_145]


def test_truncated_http_response_is_fixed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {
            "id": "req-truncated",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"description":"不完整但可解析"}'},
                }
            ],
        }
    ).encode()
    wire = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body) + 128).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )

    class _TruncatedSocket:
        def makefile(self, _mode: str) -> io.BytesIO:
            return io.BytesIO(wire)

    response = http.client.HTTPResponse(cast(Any, _TruncatedSocket()))
    response.begin()

    def open_request(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> http.client.HTTPResponse:
        assert timeout == 12.5
        return response

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ) as captured:
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )

    assert captured.value.__context__ is None
    formatted = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert API_KEY not in formatted


@pytest.mark.parametrize(
    "content_length_headers",
    [
        ("invalid",),
        ("{actual}", "{larger}"),
        ("{actual}, {larger}",),
    ],
)
def test_invalid_or_conflicting_content_lengths_are_rejected(
    content_length_headers: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {
            "id": "req-malformed-length",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"description":"不应被接受"}'},
                }
            ],
        }
    ).encode()
    header_lines = b"".join(
        b"Content-Length: "
        + value.format(actual=len(body), larger=len(body) + 128).encode()
        + b"\r\n"
        for value in content_length_headers
    )
    wire = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + header_lines
        + b"Connection: close\r\n\r\n"
        + body
    )

    class _MalformedLengthSocket:
        def makefile(self, _mode: str) -> io.BytesIO:
            return io.BytesIO(wire)

    response = http.client.HTTPResponse(cast(Any, _MalformedLengthSocket()))
    response.begin()

    def open_request(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> http.client.HTTPResponse:
        assert timeout == 12.5
        return response

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )


@pytest.mark.parametrize("header_shape", ["repeated", "combined"])
def test_repeated_content_lengths_are_bounded_before_parsing(
    header_shape: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {
            "id": "req-repeated-length",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"description":"不应被接受"}'},
                }
            ],
        }
    ).encode()
    length_header = b"Content-Length: " + str(len(body)).encode() + b"\r\n"
    header_lines = (
        length_header * 5
        if header_shape == "repeated"
        else b"Content-Length: " + b", ".join([str(len(body)).encode()] * 5) + b"\r\n"
    )
    wire = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + header_lines
        + b"Connection: close\r\n\r\n"
        + body
    )

    class _RepeatedLengthSocket:
        def makefile(self, _mode: str) -> io.BytesIO:
            return io.BytesIO(wire)

    response = http.client.HTTPResponse(cast(Any, _RepeatedLengthSocket()))
    response.begin()

    def open_request(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> http.client.HTTPResponse:
        assert timeout == 12.5
        return response

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )


@pytest.mark.parametrize("non_ows", [b"\x0b", b"\x0c", b"\xa0"])
def test_non_http_whitespace_in_content_length_is_rejected(
    non_ows: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(
        {
            "id": "req-non-ows-length",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"description":"不应被接受"}'},
                }
            ],
        }
    ).encode()
    wire = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + non_ows
        + str(len(body)).encode()
        + non_ows
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )

    class _NonOwsLengthSocket:
        def makefile(self, _mode: str) -> io.BytesIO:
            return io.BytesIO(wire)

    response = http.client.HTTPResponse(cast(Any, _NonOwsLengthSocket()))
    response.begin()

    def open_request(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> http.client.HTTPResponse:
        assert timeout == 12.5
        return response

    monkeypatch.setattr(material_understanding_module, "_open_request", open_request)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        _adapter().understand(
            (
                MaterialUnderstandingFrame(
                    timestamp_ms=0,
                    is_scene_cut=True,
                    jpeg_bytes=b"\xff\xd8\xff\xd9",
                ),
            ),
            options=MaterialUnderstandingOptions(),
        )


def test_public_types_do_not_leak_provider_names() -> None:
    public_names = {
        MaterialUnderstandingAdapter.__name__,
        MaterialUnderstandingFrame.__name__,
        MaterialUnderstandingOptions.__name__,
    }
    provider_terms = ("Bailian", "OpenAI", "DashScope")

    assert not any(term in name for term in provider_terms for name in public_names)
    assert inspect.signature(MaterialUnderstandingAdapter.understand).return_annotation != Any
    assert isinstance(MaterialUnderstandingFrame.__dataclass_fields__, dict)
    assert isinstance(MaterialUnderstandingOptions.__dataclass_fields__, dict)
