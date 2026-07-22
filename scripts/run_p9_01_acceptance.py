#!/usr/bin/env python3
"""Build and inspect one disposable signing-ready macOS Executor candidate."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.macos_candidate import build_macos_executor_candidate
from automation_tool.executor.package_manifest import write_signed_executor_manifest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
ACCEPTANCE_SIGNING_SEED = bytes(range(32))


def main() -> int:
    if platform.system() != "Darwin":
        raise RuntimeError("P9-01 acceptance requires macOS")
    architecture = (
        "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
    )
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-p901-acceptance-"
    ) as temporary:
        bundle = Path(temporary) / "automation-tool-executor"
        print("[P9-01] Building the isolated macOS PyInstaller onedir")
        audit = build_macos_executor_candidate(
            backend_root=BACKEND_ROOT,
            output_directory=bundle,
        )
        entrypoint = bundle / "automation-tool-executor"
        startup = subprocess.run(
            [os.fspath(entrypoint)],
            input=b"",
            capture_output=True,
            check=False,
            env={"PATH": os.defpath},
            timeout=30,
        )
        if (
            startup.returncode != 2
            or startup.stdout != b""
            or startup.stderr != b"Local Executor bootstrap is rejected\n"
        ):
            raise RuntimeError("P9-01 frozen startup boundary failed")

        print(
            "[P9-01] Proving offline Manifest signing readiness with a disposable test seed"
        )
        signed = write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="0.1.0",
            build_id="p9-01-acceptance",
            target_platform="macos",
            target_architecture=architecture,
            signing_private_key=ACCEPTANCE_SIGNING_SEED,
        )
        public_key = Ed25519PrivateKey.from_private_bytes(
            ACCEPTANCE_SIGNING_SEED
        ).public_key()
        public_key.verify(signed.signature_bytes, signed.manifest_bytes)
        document = json.loads(signed.manifest_bytes)
        if (
            document["platform"] != "macos"
            or document["architecture"] != architecture
            or document["entrypoint"] != "automation-tool-executor"
            or document["package_size"] != audit.package_size
        ):
            raise RuntimeError("P9-01 signing metadata is inconsistent")

    print(
        "[P9-01] macOS Executor candidate passed dependency, path, architecture, "
        "code-signature and offline-signing audits: "
        f"{audit.file_count} files, {audit.mach_o_file_count} Mach-O files, "
        f"{audit.package_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
