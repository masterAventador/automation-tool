#!/usr/bin/env python3
"""BM-02 fixed-boundary rejection tests for the Node Worker source."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers/motion_composition/worker.mjs"


def run_worker(payload: str) -> subprocess.CompletedProcess[str]:
    node = os.environ.get("BM02_NODE", shutil_which_node())
    environment = {"PATH": ""}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if key in os.environ:
                environment[key] = os.environ[key]
    return subprocess.run(
        [node, str(WORKER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )


def shutil_which_node() -> str:
    import shutil

    value = shutil.which("node")
    if value is None:
        raise AssertionError("development Node is unavailable")
    return value


def main() -> int:
    missing = run_worker("")
    assert missing.returncode == 64
    assert missing.stdout == ""
    assert missing.stderr == "Motion composition worker command is required\n"
    malformed = run_worker('{"localSessionToken":"private-value"}\n')
    assert malformed.returncode == 65
    assert malformed.stdout == ""
    assert malformed.stderr == "Motion composition worker bootstrap is rejected\n"
    with tempfile.TemporaryDirectory(prefix="bm02-assets-") as directory:
        bootstrap = {
            "assetRoot": directory,
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": "a" * 64,
            "protocolVersion": "1.0",
            "scriptModel": None,
            "workerKind": "node",
            "unexpectedSecret": "must-not-leak",
        }
        rejected = run_worker(json.dumps(bootstrap) + "\n")
        assert rejected.returncode == 65
        assert "must-not-leak" not in rejected.stderr
        assert directory not in rejected.stderr
    # App 的共享引导文档自 2026-08-05 起始终携带 pexelsApiKey（无密钥构建为
    # null，带密钥构建为字符串）。三个读者里 material 侧两个当晚就统一了，
    # 这个 Node worker 漏了：9 键真实文档被 hasExactKeys 拒绝、worker 报拒后
    # App 还在等 ready——BM-08 真实 App 提交卡死（2026-08-06 采样实锤双向
    # 空等）。测试此前构造的 8 键文档与真实 App 是两种协议。
    with tempfile.TemporaryDirectory(prefix="bm02-assets-") as directory:
        real_app_document = {
            "assetRoot": directory,
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": "a" * 64,
            "pexelsApiKey": None,
            "protocolVersion": "1.0",
            "renderBrowser": None,
            "scriptModel": None,
            "workerKind": "node",
        }
        accepted = run_worker(json.dumps(real_app_document) + "\n")
        assert "worker.ready" in accepted.stdout, (
            "the exact document the App sends (pexelsApiKey always present, "
            f"null without a packaged key) must reach ready: {accepted.stderr!r}"
        )
        keyed = dict(real_app_document, pexelsApiKey="P" * 56)
        assert "worker.ready" in run_worker(json.dumps(keyed) + "\n").stdout, (
            "a packaged-key build sends the key as a string; this worker does "
            "not use it but must still recognise the shared protocol"
        )
        hostile = dict(real_app_document, pexelsApiKey="not a key !!")
        assert run_worker(json.dumps(hostile) + "\n").returncode == 65
    # A headless Chromium with the GPU disabled has no WebGL at all unless the
    # software rasterizer is explicitly allowed: Chromium 149 refuses the
    # SwiftShader fallback without `--enable-unsafe-swiftshader`, every shader
    # catalog item then takes its no-GL branch and renders one static page, and
    # the worker's own static-frame gate refuses the job (BM-16, first seen on
    # `chromatic-radial-split`). So the two flags may only travel together.
    source = WORKER.read_text(encoding="utf-8")
    disables = source.count('"--disable-gpu"')
    allows = source.count('"--enable-unsafe-swiftshader"')
    assert disables >= 1
    assert disables == allows, (
        "every --disable-gpu launch needs --enable-unsafe-swiftshader beside "
        f"it: {disables} vs {allows}"
    )
    assert "--allow-file-access-from-files" not in source, (
        "runtime data must be inlined into the composed release; the render "
        "sandbox may not widen file-origin access"
    )
    # `load` is not the composition-ready boundary. A document may parse an
    # inlined GLB first and only then register its seekable GSAP timeline. The
    # Worker must keep probing through warm-up and derive the capture mode from
    # the final probe; otherwise it takes several identical opening frames and
    # falsely rejects a healthy composition as static (BM-16, first seen on
    # `vfx-iphone-device`).
    assert "const readTimelineMetadata = async () =>" in source
    assert source.count("await readTimelineMetadata()") >= 2
    assert "timelineExpected" in source
    assert "timelineMetadata.timelineCount === 0" in source
    assert "data-required-font-family" in source
    assert "face.status === \"loaded\"" in source
    assert "finish({ status: \"font\" })" in source
    print("BM-02 Node Worker rejection tests passed")
    print("executed checks: 8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
