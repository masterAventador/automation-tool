#!/usr/bin/env python3
"""Collect paired formal-App observations and derive the LE-24 time copy."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from le24_measurement import (
    MATERIAL_COUNT_ENVIRONMENT,
    THINKING_ENVIRONMENT,
    Le24Measurement,
    Le24MeasurementStep,
    Le24PairedSample,
    build_le24_measurement_plan,
    format_le24_estimate,
    parse_le24_measurement,
    summarize_le24_measurements,
)

ROOT = Path(__file__).resolve().parents[1]
SINGLE_OBSERVATION_RUNNER = ROOT / "scripts/run_le_19_acceptance.py"
DEFAULT_OUTPUT_ROOT = ROOT / ".local/local-video-editing/le24-measurements"
REVISION = re.compile(r"[0-9a-f]{40}")


def collect_le24_paired_measurements(
    observe: Callable[[Le24MeasurementStep], Le24Measurement],
) -> tuple[Le24PairedSample, ...]:
    observations: dict[tuple[int, int], dict[bool, int]] = {}
    for step in build_le24_measurement_plan():
        observation = observe(step)
        if (
            type(observation) is not Le24Measurement
            or observation.enable_thinking is not step.enable_thinking
            or observation.material_count != step.material_count
            or type(observation.elapsed_ms) is not int
            or not 1 <= observation.elapsed_ms <= 900_000
        ):
            raise RuntimeError("LE-24 observation does not match its plan")
        pair = observations.setdefault((step.material_count, step.repetition), {})
        if step.enable_thinking in pair:
            raise RuntimeError("LE-24 observation does not match its plan")
        pair[step.enable_thinking] = observation.elapsed_ms

    paired = tuple(
        Le24PairedSample(
            material_count=material_count,
            repetition=repetition,
            disabled_elapsed_ms=observations[(material_count, repetition)][False],
            enabled_elapsed_ms=observations[(material_count, repetition)][True],
        )
        for material_count in (1, 2, 3)
        for repetition in (1, 2, 3)
    )
    return paired


def build_le24_report(
    samples: Sequence[Le24PairedSample],
    *,
    platform: str,
    revision: str,
) -> dict[str, object]:
    if platform not in {"macos", "windows"} or REVISION.fullmatch(revision) is None:
        raise RuntimeError("LE-24 report identity is invalid")
    summary = summarize_le24_measurements(tuple(samples))
    return {
        "schemaVersion": 1,
        "platform": platform,
        "revision": revision,
        "repetitionsPerMode": 3,
        "pairs": [
            {
                "materialCount": sample.material_count,
                "repetition": sample.repetition,
                "disabledElapsedMs": sample.disabled_elapsed_ms,
                "enabledElapsedMs": sample.enabled_elapsed_ms,
                "deltaMs": sample.delta_ms,
            }
            for sample in samples
        ],
        "analysis": {
            "kind": summary.kind,
            "medianDeltaByMaterialCountMs": {
                str(material_count): delta_ms
                for material_count, delta_ms in zip(
                    (1, 2, 3),
                    summary.median_delta_by_material_count_ms,
                    strict=True,
                )
            },
            "baseMs": summary.base_ms,
            "perMaterialMs": summary.per_material_ms,
            "rangeMs": summary.range_ms,
            "userCopy": format_le24_estimate(summary),
        },
    }


def _platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError("LE-24 formal App measurements require macOS or Windows")


def _revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    revision = completed.stdout.strip()
    if REVISION.fullmatch(revision) is None:
        raise RuntimeError("LE-24 Git revision is invalid")
    return revision


def _run_formal_observation(step: Le24MeasurementStep) -> Le24Measurement:
    print(
        "LE-24 正式 App 计时："
        f"第 {step.repetition}/3 轮，{step.material_count} 条素材，"
        f"深度思考{'开' if step.enable_thinking else '关'}"
    )
    environment = os.environ.copy()
    environment[THINKING_ENVIRONMENT] = (
        "enabled" if step.enable_thinking else "disabled"
    )
    environment[MATERIAL_COUNT_ENVIRONMENT] = str(step.material_count)
    completed = subprocess.run(
        [sys.executable, str(SINGLE_OBSERVATION_RUNNER)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=1_800,
    )
    if completed.returncode != 0:
        raise RuntimeError("LE-24 formal App observation failed")
    return parse_le24_measurement(
        completed.stdout + completed.stderr,
        expected_enable_thinking=step.enable_thinking,
        expected_material_count=step.material_count,
    )


def _write_report(destination: Path, report: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    platform = _platform_name()
    revision = _revision()
    destination = arguments.output or (
        DEFAULT_OUTPUT_ROOT / f"{platform}-{revision[:12]}.json"
    )
    samples = collect_le24_paired_measurements(_run_formal_observation)
    report = build_le24_report(samples, platform=platform, revision=revision)
    _write_report(destination, report)
    print(f"LE-24 成对计时报告已生成：{destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
