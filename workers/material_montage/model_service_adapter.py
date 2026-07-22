"""In-memory Bailian adapter for the upstream script-generation service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

PRODUCTION_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ALLOWED_MODELS: Final = frozenset(
    {"deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"}
)
API_KEY_PATTERN: Final = re.compile(r"^sk-[A-Za-z0-9_-]{17,253}$")
_installed: tuple[ScriptModelConfiguration, object] | None = None


class ScriptModelRejected(ValueError):
    """Fixed boundary for invalid native-to-Worker model settings."""


@dataclass(frozen=True, repr=False)
class ScriptModelConfiguration:
    source_provider: str
    upstream_provider: str
    base_url: str
    model_id: str
    api_key: str

    def __repr__(self) -> str:
        return (
            "ScriptModelConfiguration(source_provider='bailian', "
            f"upstream_provider='openai', model_id={self.model_id!r}, api_key=<redacted>)"
        )


def parse_script_model(value: object) -> ScriptModelConfiguration | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "apiKey",
        "baseUrl",
        "modelId",
        "sourceProvider",
        "upstreamProvider",
    }:
        raise ScriptModelRejected("invalid script model")
    api_key = value.get("apiKey")
    model_id = value.get("modelId")
    if (
        value.get("sourceProvider") != "bailian"
        or value.get("upstreamProvider") != "openai"
        or value.get("baseUrl") != PRODUCTION_BASE_URL
        or not isinstance(model_id, str)
        or model_id not in ALLOWED_MODELS
        or not isinstance(api_key, str)
        or API_KEY_PATTERN.fullmatch(api_key) is None
    ):
        raise ScriptModelRejected("invalid script model")
    return ScriptModelConfiguration(
        source_provider="bailian",
        upstream_provider="openai",
        base_url=PRODUCTION_BASE_URL,
        model_id=model_id,
        api_key=api_key,
    )


def install_script_model(configuration: ScriptModelConfiguration) -> str:
    """Prepare the compatible client without importing or mutating upstream config."""
    from openai import OpenAI

    global _installed
    client = OpenAI(api_key=configuration.api_key, base_url=configuration.base_url)
    _installed = (configuration, client)
    return configuration.model_id


def generate_script(prompt: str) -> str:
    """OpenAI-compatible request function consumed by the WebUI bridge in IM-05."""
    if _installed is None:
        return "Error: model service configuration required"
    configuration, client = _installed
    try:
        response = client.chat.completions.create(
            model=configuration.model_id,
            messages=[{"role": "user", "content": prompt}],
        )
        choices = getattr(response, "choices", None)
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("invalid model response")
        return content.replace("\n", "")
    except Exception:
        return "Error: model service operation unavailable"
