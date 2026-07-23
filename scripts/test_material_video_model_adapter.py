#!/usr/bin/env python3
"""Secret-free config and compatible-call tests for the material-video model adapter."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))

import model_service_adapter as adapter  # noqa: E402
from model_service_adapter import (  # noqa: E402
    PRODUCTION_BASE_URL,
    ScriptModelRejected,
    generate_script,
    install_script_model,
    parse_script_model,
)

KEY = "sk-im04-private-model-key-1234567890"


def document(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "apiKey": KEY,
        "baseUrl": PRODUCTION_BASE_URL,
        "modelId": "deepseek-v4-pro",
        "sourceProvider": "bailian",
        "upstreamProvider": "openai",
    }
    value.update(changes)
    return value


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.failure = False

    def create(self, **values: object) -> object:
        self.calls.append(values)
        if self.failure:
            raise RuntimeError(f"private failure {KEY}")
        message = types.SimpleNamespace(content="第一行\n第二行")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeOpenAI:
    instances: ClassVar[list[FakeOpenAI]] = []

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.chat = types.SimpleNamespace(completions=FakeCompletions())
        self.instances.append(self)


class MaterialVideoModelAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_openai = sys.modules.get("openai")
        module = types.ModuleType("openai")
        module.OpenAI = FakeOpenAI
        sys.modules["openai"] = module
        FakeOpenAI.instances.clear()
        adapter._installed = None

    def tearDown(self) -> None:
        adapter._installed = None
        if self.previous_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = self.previous_openai

    def test_exact_bailian_to_openai_compatible_mapping_is_required(self) -> None:
        configuration = parse_script_model(document())
        self.assertIsNotNone(configuration)
        assert configuration is not None
        self.assertEqual(configuration.upstream_provider, "openai")
        self.assertEqual(configuration.model_id, "deepseek-v4-pro")
        self.assertNotIn(KEY, repr(configuration))
        for changes in (
            {"sourceProvider": "openai"},
            {"upstreamProvider": "qwen"},
            {"baseUrl": "https://evil.example/v1"},
            {"modelId": "old-model"},
            {"apiKey": "secret"},
            {"extra": True},
        ):
            with self.assertRaises(ScriptModelRejected):
                parse_script_model(document(**changes))

    def test_install_never_imports_upstream_config_and_call_is_fixed(self) -> None:
        configuration = parse_script_model(document(modelId="glm-5.2"))
        assert configuration is not None
        before = set(sys.modules)
        self.assertEqual(install_script_model(configuration), "glm-5.2")
        client = FakeOpenAI.instances[-1]
        self.assertEqual(client.api_key, KEY)
        self.assertEqual(client.base_url, PRODUCTION_BASE_URL)
        self.assertNotIn("app.config.config", set(sys.modules) - before)
        self.assertEqual(generate_script("用户主题"), "第一行第二行")
        self.assertEqual(
            client.chat.completions.calls,
            [
                {
                    "model": "glm-5.2",
                    "messages": [{"role": "user", "content": "用户主题"}],
                }
            ],
        )

    def test_unconfigured_and_transport_failures_are_redacted(self) -> None:
        self.assertEqual(generate_script("正文"), "Error: model service configuration required")
        configuration = parse_script_model(document())
        assert configuration is not None
        install_script_model(configuration)
        FakeOpenAI.instances[-1].chat.completions.failure = True
        failure = generate_script("正文")
        self.assertEqual(failure, "Error: model service operation unavailable")
        self.assertNotIn(KEY, failure)


WORKSPACE_KEY = "sk-ws-T.SYNTHETIC0.9Zz9.MEUCIQTestOnlyTestOnlyTestOnlyTestOnly-42"


class WorkspaceKeyFormatTests(unittest.TestCase):
    def test_accepts_real_workspace_key_shape_with_dots(self) -> None:
        configuration = parse_script_model(document(apiKey=WORKSPACE_KEY))
        assert configuration is not None
        self.assertEqual(configuration.api_key, WORKSPACE_KEY)
        self.assertNotIn(WORKSPACE_KEY, repr(configuration))

    def test_still_rejects_other_punctuation_and_wrong_prefix(self) -> None:
        for invalid in (
            "sk-bad key with spaces padded to length",
            "sk-bad/key/with/slashes/padded/to/len",
            "ws-missing-sk-prefix-padded-to-length",
            "sk-..",
        ):
            with self.assertRaises(ScriptModelRejected):
                parse_script_model(document(apiKey=invalid))


if __name__ == "__main__":
    unittest.main()
