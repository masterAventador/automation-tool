"""Strict, path-free LE-24 thinking measurement protocol."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

THINKING_ENVIRONMENT = "AUTOMATION_TOOL_LE24_MEASURE_THINKING"
MATERIAL_COUNT_ENVIRONMENT = "AUTOMATION_TOOL_LE24_MATERIAL_COUNT"
MEASUREMENT_MARKER = "LE24_MEASUREMENT"
_MEASUREMENT_PATTERN = re.compile(
    rf"^(?:\[\d+-\d+\]\s+)?{MEASUREMENT_MARKER} "
    r"(?P<document>\{[^\r\n]+\})\s*$",
    re.MULTILINE,
)
_MEASUREMENT_KEYS = {
    "schemaVersion",
    "enableThinking",
    "materialCount",
    "elapsedMs",
}


@dataclass(frozen=True, slots=True)
class Le24Measurement:
    enable_thinking: bool
    material_count: int
    elapsed_ms: int


def read_le24_measurement_request(
    source: Mapping[str, str],
) -> tuple[bool, int] | None:
    mode = source.get(THINKING_ENVIRONMENT)
    raw_material_count = source.get(MATERIAL_COUNT_ENVIRONMENT)
    if mode is None and raw_material_count is None:
        return None
    if mode not in {"enabled", "disabled"} or raw_material_count not in {
        "1",
        "2",
        "3",
    }:
        raise RuntimeError("LE-24 measurement request is invalid")
    return mode == "enabled", int(raw_material_count)


def parse_le24_measurement(
    output: str,
    *,
    expected_enable_thinking: bool,
    expected_material_count: int,
) -> Le24Measurement:
    matches = list(_MEASUREMENT_PATTERN.finditer(output))
    if len(matches) != 1:
        raise RuntimeError("LE-24 measurement output is invalid")
    try:
        document = json.loads(matches[0].group("document"))
    except json.JSONDecodeError:
        raise RuntimeError("LE-24 measurement output is invalid") from None
    if not isinstance(document, dict) or set(document) != _MEASUREMENT_KEYS:
        raise RuntimeError("LE-24 measurement output is invalid")
    schema_version = document["schemaVersion"]
    enable_thinking = document["enableThinking"]
    material_count = document["materialCount"]
    elapsed_ms = document["elapsedMs"]
    if (
        type(schema_version) is not int
        or schema_version != 1
        or type(enable_thinking) is not bool
        or enable_thinking is not expected_enable_thinking
        or type(material_count) is not int
        or material_count != expected_material_count
        or material_count not in {1, 2, 3}
        or type(elapsed_ms) is not int
        or not 1 <= elapsed_ms <= 900_000
    ):
        raise RuntimeError("LE-24 measurement output is invalid")
    return Le24Measurement(
        enable_thinking=enable_thinking,
        material_count=material_count,
        elapsed_ms=elapsed_ms,
    )
