"""Strict, path-free LE-24 thinking measurement protocol."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from statistics import median
from typing import Literal

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


@dataclass(frozen=True, slots=True)
class Le24MeasurementStep:
    repetition: int
    material_count: int
    enable_thinking: bool


@dataclass(frozen=True, slots=True)
class Le24PairedSample:
    material_count: int
    repetition: int
    disabled_elapsed_ms: int
    enabled_elapsed_ms: int

    @property
    def delta_ms(self) -> int:
        return self.enabled_elapsed_ms - self.disabled_elapsed_ms


@dataclass(frozen=True, slots=True)
class Le24MeasurementSummary:
    kind: Literal["count_based", "range"]
    median_delta_by_material_count_ms: tuple[int, int, int]
    base_ms: int | None
    per_material_ms: int | None
    range_ms: tuple[int, int] | None


def build_le24_measurement_plan() -> tuple[Le24MeasurementStep, ...]:
    """Return three paired runs per count while alternating pair order."""
    plan: list[Le24MeasurementStep] = []
    for repetition in (1, 2, 3):
        for material_count in (1, 2, 3):
            modes = (
                (False, True)
                if (repetition + material_count) % 2 == 0
                else (True, False)
            )
            plan.extend(
                Le24MeasurementStep(
                    repetition=repetition,
                    material_count=material_count,
                    enable_thinking=enable_thinking,
                )
                for enable_thinking in modes
            )
    return tuple(plan)


def _round_fraction(value: Fraction) -> int:
    return math.floor(value + Fraction(1, 2))


def _paired_measurements_are_valid(samples: list[Le24PairedSample]) -> bool:
    expected_pairs = {
        (material_count, repetition)
        for material_count in (1, 2, 3)
        for repetition in (1, 2, 3)
    }
    actual_pairs = {(sample.material_count, sample.repetition) for sample in samples}
    return (
        len(samples) == len(expected_pairs)
        and actual_pairs == expected_pairs
        and all(
            type(value) is int and 1 <= value <= 900_000
            for sample in samples
            for value in (
                sample.disabled_elapsed_ms,
                sample.enabled_elapsed_ms,
            )
        )
    )


def summarize_le24_measurements(
    source: list[Le24PairedSample] | tuple[Le24PairedSample, ...],
) -> Le24MeasurementSummary:
    samples = list(source)
    if not _paired_measurements_are_valid(samples):
        raise RuntimeError("LE-24 paired measurements are invalid")

    deltas_by_count = {
        material_count: [
            sample.delta_ms
            for sample in samples
            if sample.material_count == material_count
        ]
        for material_count in (1, 2, 3)
    }
    medians = tuple(
        int(median(deltas_by_count[material_count])) for material_count in (1, 2, 3)
    )
    if any(value <= 0 for value in medians):
        raise RuntimeError("LE-24 paired measurements are invalid")

    first_increment = medians[1] - medians[0]
    second_increment = medians[2] - medians[1]
    stable_samples = all(
        max(abs(delta - medians[material_count - 1]) for delta in deltas)
        <= max(1_000, medians[material_count - 1] // 4)
        for material_count, deltas in deltas_by_count.items()
    )
    increments_are_linear = (
        first_increment > 0
        and second_increment > 0
        and max(first_increment, second_increment)
        <= Fraction(3, 2) * min(first_increment, second_increment)
    )
    slope = Fraction(medians[2] - medians[0], 2)
    intercept = Fraction(medians[1]) - 2 * slope
    predicted = tuple(
        intercept + slope * material_count for material_count in (1, 2, 3)
    )
    residual_limit = max(1_000, max(medians) * 15 // 100)
    residuals_are_small = (
        max(
            abs(Fraction(observed) - expected)
            for observed, expected in zip(medians, predicted, strict=True)
        )
        <= residual_limit
    )

    if (
        stable_samples
        and increments_are_linear
        and slope > 0
        and intercept >= 0
        and residuals_are_small
    ):
        return Le24MeasurementSummary(
            kind="count_based",
            median_delta_by_material_count_ms=medians,
            base_ms=_round_fraction(intercept),
            per_material_ms=_round_fraction(slope),
            range_ms=None,
        )
    return Le24MeasurementSummary(
        kind="range",
        median_delta_by_material_count_ms=medians,
        base_ms=None,
        per_material_ms=None,
        range_ms=(min(medians), max(medians)),
    )


def format_le24_estimate(summary: Le24MeasurementSummary) -> str:
    if summary.kind == "count_based":
        if summary.base_ms is None or summary.per_material_ms is None:
            raise RuntimeError("LE-24 measurement summary is invalid")
        base_seconds = math.ceil(summary.base_ms / 1_000)
        per_material_seconds = math.ceil(summary.per_material_ms / 1_000)
        if base_seconds == 0:
            return f"开启后预计每条素材约多花 {per_material_seconds} 秒。"
        return (
            f"开启后预计约多花 {base_seconds} 秒 + 每条素材 {per_material_seconds} 秒。"
        )
    if summary.range_ms is None:
        raise RuntimeError("LE-24 measurement summary is invalid")
    low_ms, high_ms = summary.range_ms
    low_seconds = max(1, math.floor(low_ms / 1_000))
    high_seconds = max(low_seconds, math.ceil(high_ms / 1_000))
    if low_seconds == high_seconds:
        return f"开启后预计约多花 {high_seconds} 秒。"
    return f"开启后预计约多花 {low_seconds}～{high_seconds} 秒。"


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
