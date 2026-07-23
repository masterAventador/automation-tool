#!/usr/bin/env python3
"""BU-06: locked Bailian model catalog and restricted ChatOpenAI gateway.

Browser Use reaches Bailian through its own `ChatOpenAI(base_url=...)`; no
upstream source is modified. The catalog is a closed capability snapshot
(text / vision / function_calling / structured_output / context / region /
compatible api) locked to a verification date. Capability rules are fail
closed:

- a vision run (`requires_vision=True`) admits only a model whose snapshot
  declares vision — a text model is *blocked*, never silently degraded to a
  screenshot-blind run;
- text-only models are DOM-only and admit only when the caller has passed the
  DOM-only real-page acceptance (`dom_only_accepted=True`);
- there is no general menu: only a model id present in the locked catalog can
  be selected, and a `ChatOpenAI` is built only from a catalog snapshot, only
  against the locked base URL.

The api key is validated but never enters model repr or logs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from browser_use.llm import ChatOpenAI

_MODEL_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,63}$")
_API_KEY_PATTERN: Final = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_LOCKED_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class BailianModelRejected(RuntimeError):
    """A model selection or gateway build violated the locked catalog policy."""

    def __init__(self) -> None:
        super().__init__("bailian model selection rejected")


def _reject() -> None:
    raise BailianModelRejected


@dataclass(frozen=True)
class BailianModelSnapshot:
    """One locked model with its verified capability flags."""

    model_id: str
    display_name: str
    text: bool
    vision: bool
    function_calling: bool
    structured_output: bool
    context_window: int
    dom_only_acceptance_required: bool


@dataclass(frozen=True)
class BailianModelCatalog:
    """Closed capability snapshot locked to a verification date."""

    verified_at: str
    base_url: str
    region: str
    vision_default_model_id: str
    models: tuple[BailianModelSnapshot, ...]

    def by_id(self, model_id: str) -> BailianModelSnapshot | None:
        for model in self.models:
            if model.model_id == model_id:
                return model
        return None


def _require(condition: bool) -> None:
    if not condition:
        _reject()


def load_bailian_model_catalog(path: Path) -> BailianModelCatalog:
    """Load and strictly validate the locked Bailian catalog contract."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _reject()
    _require(
        isinstance(document, dict)
        and document.get("schema_version") == 1
        and document.get("policy") == "fail_closed"
        and document.get("compatible_api") == "openai_chat_completions"
        and document.get("base_url") == _LOCKED_BASE_URL
        and isinstance(document.get("verified_at"), str)
        and isinstance(document.get("region"), str)
        and isinstance(document.get("models"), list)
        and bool(document["models"])
    )
    models: list[BailianModelSnapshot] = []
    seen: set[str] = set()
    for entry in document["models"]:
        _require(isinstance(entry, dict))
        expected = {
            "model_id",
            "display_name",
            "text",
            "vision",
            "function_calling",
            "structured_output",
            "context_window",
            "dom_only_acceptance_required",
            "source",
        }
        _require(set(entry) == expected)
        model_id = entry["model_id"]
        _require(
            isinstance(model_id, str)
            and _MODEL_ID_PATTERN.fullmatch(model_id) is not None
            and model_id not in seen
            and isinstance(entry["display_name"], str)
            and isinstance(entry["text"], bool)
            and isinstance(entry["vision"], bool)
            and isinstance(entry["function_calling"], bool)
            and isinstance(entry["structured_output"], bool)
            and isinstance(entry["context_window"], int)
            and entry["context_window"] > 0
            and isinstance(entry["dom_only_acceptance_required"], bool)
        )
        # A text-only model must be flagged DOM-only; a vision model must not.
        _require(entry["dom_only_acceptance_required"] == (not entry["vision"]))
        seen.add(model_id)
        models.append(
            BailianModelSnapshot(
                model_id=model_id,
                display_name=entry["display_name"],
                text=entry["text"],
                vision=entry["vision"],
                function_calling=entry["function_calling"],
                structured_output=entry["structured_output"],
                context_window=entry["context_window"],
                dom_only_acceptance_required=entry["dom_only_acceptance_required"],
            )
        )
    vision_default = document["vision_default_model_id"]
    _require(any(m.model_id == vision_default and m.vision for m in models))
    return BailianModelCatalog(
        verified_at=document["verified_at"],
        base_url=document["base_url"],
        region=document["region"],
        vision_default_model_id=vision_default,
        models=tuple(models),
    )


def select_bailian_model(
    catalog: BailianModelCatalog,
    *,
    model_id: str,
    requires_vision: bool,
    dom_only_accepted: bool = False,
) -> BailianModelSnapshot:
    """Select a catalog model, blocking capability-incompatible choices."""
    _require(isinstance(catalog, BailianModelCatalog) and type(requires_vision) is bool)
    snapshot = catalog.by_id(model_id) if type(model_id) is str else None
    if snapshot is None:
        _reject()
        raise AssertionError  # pragma: no cover - _reject always raises
    if requires_vision and not snapshot.vision:
        _reject()
    if snapshot.dom_only_acceptance_required and not requires_vision and not dom_only_accepted:
        _reject()
    return snapshot


def build_bailian_chat_model(
    *,
    catalog: BailianModelCatalog,
    snapshot: BailianModelSnapshot,
    api_key: str,
) -> ChatOpenAI:
    """Build a ChatOpenAI locked to the catalog base URL and snapshot model."""
    from browser_use.llm import ChatOpenAI

    _require(
        isinstance(catalog, BailianModelCatalog)
        and isinstance(snapshot, BailianModelSnapshot)
        and catalog.by_id(snapshot.model_id) is snapshot
        and type(api_key) is str
        and _API_KEY_PATTERN.fullmatch(api_key) is not None
    )
    return ChatOpenAI(
        model=snapshot.model_id,
        api_key=api_key,
        base_url=catalog.base_url,
    )


def redacted_model_descriptor(
    catalog: BailianModelCatalog, snapshot: BailianModelSnapshot
) -> dict[str, object]:
    """Return a key-free descriptor for logs/diagnostics.

    The upstream ``ChatOpenAI.__repr__`` prints the api key in clear text
    (a known upstream behaviour we cannot patch), so production code must
    never repr or log the raw model object — this descriptor is the only
    representation allowed in logs, and it never carries the key.
    """
    _require(
        isinstance(catalog, BailianModelCatalog)
        and isinstance(snapshot, BailianModelSnapshot)
        and catalog.by_id(snapshot.model_id) is snapshot
    )
    return {
        "model_id": snapshot.model_id,
        "base_url": catalog.base_url,
        "vision": snapshot.vision,
        "verified_at": catalog.verified_at,
    }


__all__ = [
    "BailianModelCatalog",
    "BailianModelRejected",
    "BailianModelSnapshot",
    "build_bailian_chat_model",
    "load_bailian_model_catalog",
    "redacted_model_descriptor",
    "select_bailian_model",
]
