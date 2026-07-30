"""LE-13 T1: supplier-neutral material-understanding adapter boundary."""

from __future__ import annotations

import inspect
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.material_understanding import (
    BailianMaterialUnderstandingAdapter,
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

    def read(self) -> bytes:
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

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
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
        raise OSError(f"private upstream error {API_KEY} after {timeout}")

    monkeypatch.setattr(urllib.request, "urlopen", fail_transport)

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
