#!/usr/bin/env python3
"""Build an isolated Node 22 motion-video Worker candidate outside the repository."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_offline_motion_catalog import catalog_root as locked_catalog_root  # noqa: E402

CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
WORKER = ROOT / "workers/motion_composition/worker.mjs"
DOWNLOAD_ROOT = "https://nodejs.org/dist/v22.23.1"


@dataclass(frozen=True)
class CandidateAudit:
    node_version: str
    runtime_bytes: int
    worker_sha256: str


def _target() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "windows-x64"
    raise RuntimeError("BM-02 supports only the declared desktop targets")


def _download(archive: Path, filename: str, expected_sha256: str) -> None:
    last_error: OSError | None = None
    for attempt in range(3):
        digest = hashlib.sha256()
        archive.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                f"{DOWNLOAD_ROOT}/{filename}",
                headers={"User-Agent": "automation-tool-build/1"},
            )
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                archive.open("wb") as destination,
            ):
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    destination.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError("Node runtime archive digest mismatch")
            return
        except OSError as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 + attempt)
    archive.unlink(missing_ok=True)
    raise RuntimeError("Node runtime download failed") from last_error


OFFLINE_MOTION_LOCK = ROOT / "contracts/video/offline-motion-dependencies.v1.json"
OFFLINE_MOTION_CATALOG = locked_catalog_root()
AUTHORING_RUNTIME_PACKAGE = "gsap"


def _locked_authoring_runtime() -> tuple[str, str, str]:
    """The declared download URL, local catalog path and digest for the animation runtime."""
    lock = json.loads(OFFLINE_MOTION_LOCK.read_text(encoding="utf-8"))
    if lock.get("schemaVersion") != 1:
        raise RuntimeError("offline motion dependency lock drifted")
    for artifact in lock["artifacts"]:
        if artifact.get("package") != AUTHORING_RUNTIME_PACKAGE:
            continue
        digest = artifact["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise RuntimeError("locked animation runtime digest is malformed")
        return artifact["downloadUrl"], artifact["localPath"], digest
    raise RuntimeError("the dependency lock declares no animation runtime")


def _install_authoring_runtime(destination: Path) -> str:
    """Place the animation runtime the authored composition loads, digest first.

    The bytes come from the locked catalog when this machine has already built
    it and from the locked URL when it has not, so a clean machine can still
    produce the package — a build that only works where a git-ignored cache
    happens to exist is a build only one machine can make. Either way the
    digest decides: it is checked before the file is written, and a mismatch
    fails the build rather than shipping a runtime the authoring prompt was
    not written against.
    """
    url, local_path, expected = _locked_authoring_runtime()
    cached = OFFLINE_MOTION_CATALOG / local_path
    payload: bytes | None = None
    if cached.is_file():
        candidate = cached.read_bytes()
        if hashlib.sha256(candidate).hexdigest() == expected:
            payload = candidate
    if payload is None:
        last_error: OSError | None = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "automation-tool-build/1"}
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = response.read(8 * 1024 * 1024)
                break
            except OSError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(1 + attempt)
        if payload is None:
            raise RuntimeError("animation runtime download failed") from last_error
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise RuntimeError("animation runtime digest mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return actual


def _extract_member(archive: Path, suffix: str, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            matches = [
                name for name in bundle.namelist()
                if name.endswith(suffix) and (suffix != "/LICENSE" or name.count("/") == 1)
            ]
            if len(matches) != 1:
                raise RuntimeError("Node runtime archive layout drifted")
            with bundle.open(matches[0]) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive, mode="r:gz") as bundle:
        matches = [
            member for member in bundle.getmembers()
            if member.name.endswith(suffix)
            and (suffix != "/LICENSE" or member.name.count("/") == 1)
        ]
        if len(matches) != 1 or not matches[0].isfile():
            raise RuntimeError("Node runtime archive layout drifted")
        source = bundle.extractfile(matches[0])
        if source is None:
            raise RuntimeError("Node runtime archive member is unavailable")
        with source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def isolated_environment() -> dict[str, str]:
    environment = {
        "PATH": "",
        "HOME": "" if os.name != "nt" else os.environ.get("USERPROFILE", ""),
        "LANG": "C",
        "LC_ALL": "C",
        "NODE_OPTIONS": "",
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if key in os.environ:
                environment[key] = os.environ[key]
    return environment


def build_candidate(output: Path) -> CandidateAudit:
    output = output.resolve()
    try:
        output.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("BM-02 candidate must be built outside the repository")
    if output.exists():
        raise RuntimeError("BM-02 candidate output already exists")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    target = _target()
    archive_spec = contract["archives"][target]
    with tempfile.TemporaryDirectory(prefix="bm02-download-") as directory:
        archive = Path(directory) / archive_spec["file"]
        _download(archive, archive_spec["file"], archive_spec["sha256"])
        runtime_dir = output / "runtime"
        app_dir = output / "app"
        runtime_dir.mkdir(parents=True)
        app_dir.mkdir()
        executable = runtime_dir / ("node.exe" if os.name == "nt" else "node")
        runtime_suffix = "/node.exe" if os.name == "nt" else "/bin/node"
        _extract_member(archive, runtime_suffix, executable)
        _extract_member(archive, "/LICENSE", output / "NODE-LICENSE")
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    shutil.copyfile(WORKER, app_dir / "worker.mjs")
    authoring_runtime = contract["packageLayout"]["authoringRuntimeAsset"]
    _install_authoring_runtime(output / authoring_runtime)
    version = subprocess.check_output(
        [str(executable), "--version"],
        env=isolated_environment(),
        text=True,
        timeout=10,
    ).strip()
    expected = f"v{contract['runtime']['version']}"
    if version != expected:
        raise RuntimeError("Node runtime version drifted")
    worker_digest = hashlib.sha256((app_dir / "worker.mjs").read_bytes()).hexdigest()
    return CandidateAudit(version.removeprefix("v"), executable.stat().st_size, worker_digest)


if __name__ == "__main__":
    raise SystemExit("Use scripts/run_bm_02_acceptance.py so the candidate is always cleaned")
