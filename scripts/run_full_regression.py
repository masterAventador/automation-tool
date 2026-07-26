#!/usr/bin/env python3
"""Run every gate layer and report what actually happened.

There was already a way to do this: a shell loop, written fresh each time,
shaped like

    echo "### eslint"; (cd frontend && pnpm lint 2>&1 | tail -5); echo "rc=$?"

`rc=$?` after a pipeline is the exit status of `tail`, which is always 0. On
2026-07-26 that log read `rc=0` for eslint, for the backend suite and for the
script suite while all three were red — eslint had two errors, and the backend
and script suites were both dead at collection time on one ImportError. The run
was one step from being treated as "green, build the package"; the only thing
that stopped it was that the failures happened to print words a human noticed.

So the pipeline is gone. Each layer runs as its own process, its real exit code
is kept, and the summary is derived from those codes rather than written
alongside them. A layer that could not be started at all counts as failed, and
so does a run with no layers in it — a check that checked nothing must never
read as a pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_TAIL_LINES = 25


@dataclass(frozen=True)
class Layer:
    """One gate, and where it has to run."""

    name: str
    command: list[str]
    directory: str = "."


@dataclass(frozen=True)
class LayerResult:
    layer: Layer
    returncode: int
    output: str


# The whole gate, in the order that fails fastest first. `pnpm test:unit` and
# `pnpm test:contracts` are separate entries on purpose: they used to be one
# shell line, and the second one's exit code was discarded.
LAYERS: tuple[Layer, ...] = (
    Layer("台账计数", [sys.executable, "scripts/check_demo_roadmap_counts.py"]),
    Layer("tsc", ["pnpm", "exec", "tsc", "-b"], "frontend"),
    Layer("eslint", ["pnpm", "lint"], "frontend"),
    Layer("前端单元", ["pnpm", "test:unit"], "frontend"),
    Layer("契约", ["pnpm", "test:contracts"], "frontend"),
    Layer("playwright", ["pnpm", "exec", "playwright", "test"], "frontend"),
    Layer("scripts", [sys.executable, "scripts/run_script_tests.py"]),
    Layer("cargo", ["cargo", "test"], "frontend/src-tauri"),
    Layer("backend", [".venv/bin/python", "-m", "pytest", "-q"], "backend"),
)

NOTHING_RAN = Layer("(没有任何层被执行)", [])


def run_layer(layer: Layer, root: Path) -> LayerResult:
    """Run one layer and keep its own exit code, whatever happens."""
    try:
        completed = subprocess.run(
            layer.command,
            cwd=root / layer.directory,
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # Being unable to start a gate is not "no result", it is a failed gate.
        return LayerResult(layer, 127, f"{error}")
    return LayerResult(layer, completed.returncode, completed.stdout + completed.stderr)


def failures(results: list[LayerResult]) -> list[LayerResult]:
    """Every layer that did not pass, plus the empty case."""
    if not results:
        return [LayerResult(NOTHING_RAN, 1, "没有配置任何层；什么都没检查不等于通过")]
    return [result for result in results if result.returncode != 0]


def summarise(results: list[LayerResult]) -> str:
    """One line, derived from the exit codes rather than written beside them."""
    broken = failures(results)
    if not broken:
        return f"全部 {len(results)} 层通过"
    named = "、".join(f"{r.layer.name}(exit {r.returncode})" for r in broken)
    return f"{len(broken)}/{len(results)} 层失败：{named}"


def main() -> None:
    only = set(sys.argv[1:])
    layers = [layer for layer in LAYERS if not only or layer.name in only]
    results: list[LayerResult] = []
    for layer in layers:
        print(f"### {layer.name}", flush=True)
        result = run_layer(layer, REPOSITORY_ROOT)
        results.append(result)
        tail = result.output.strip().splitlines()[-OUTPUT_TAIL_LINES:]
        for line in tail:
            print(f"  {line}")
        print(f"  → exit {result.returncode}", flush=True)

    print()
    print(summarise(results))
    if failures(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
