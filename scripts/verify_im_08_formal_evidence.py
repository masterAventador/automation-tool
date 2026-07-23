#!/usr/bin/env python3
"""Fail-closed verifier for real IM-08 samples and signed-package evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Final

SAMPLE_KINDS: Final = {"knowledge_explainer", "news_summary", "ranked_list"}
PLATFORMS: Final = {"macos-arm64", "windows-x86_64"}
FAILURES: Final = {
    "offline", "script_api_failure", "material_api_failure", "voice_api_failure",
    "disk_full", "user_cancel", "app_restart", "worker_crash",
}
SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN: Final = re.compile(r"mock|fixture|synthetic|placeholder|fake", re.IGNORECASE)


class EvidenceRejected(ValueError):
    """Formal evidence is absent, incomplete, or not demonstrably real."""


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvidenceRejected(f"{label} schema mismatch")
    return value


def regular_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise EvidenceRejected(f"{label} path rejected")
    candidate = root.joinpath(relative)
    if candidate.is_symlink() or not candidate.is_file():
        raise EvidenceRejected(f"{label} file missing")
    resolved = candidate.resolve(strict=True)
    if root != resolved and root not in resolved.parents:
        raise EvidenceRejected(f"{label} escaped evidence root")
    return resolved


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def probe_video(path: Path, ffprobe: str) -> tuple[float, bool]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    value = json.loads(result.stdout)
    streams = value.get("streams")
    if not isinstance(streams, list) or not any(item.get("codec_type") == "video" for item in streams):
        raise EvidenceRejected("sample has no video stream")
    duration = float(value.get("format", {}).get("duration", 0))
    return duration, any(item.get("codec_type") == "audio" for item in streams)


def verify(root_value: Path, ffprobe: str) -> None:
    root = root_value.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise EvidenceRejected("evidence root rejected")
    manifest_path = regular_file(root, "manifest.json", "manifest")
    manifest = exact(json.loads(manifest_path.read_text(encoding="utf-8")), {
        "schemaVersion", "evidenceKind", "samples", "failureScenarios", "platformPackages",
    }, "manifest")
    if manifest["schemaVersion"] != 1 or manifest["evidenceKind"] != "real_external_services":
        raise EvidenceRejected("only real external-service evidence is accepted")
    if FORBIDDEN.search(json.dumps(manifest, ensure_ascii=False)):
        raise EvidenceRejected("mock or fixture marker rejected")

    samples = manifest["samples"]
    if not isinstance(samples, list) or {item.get("kind") for item in samples if isinstance(item, dict)} != SAMPLE_KINDS:
        raise EvidenceRejected("exactly three representative samples are required")
    for index, raw in enumerate(samples):
        sample = exact(raw, {
            "kind", "videoFile", "sha256", "generatedByNormalAppPath", "realServicesUsed",
            "burnedInSubtitlesReviewed", "narrationReviewed", "playbackReviewedBy", "rightsRecords",
        }, f"sample {index}")
        if sample["generatedByNormalAppPath"] is not True or sample["realServicesUsed"] is not True:
            raise EvidenceRejected("sample did not use the normal App path and real services")
        if sample["burnedInSubtitlesReviewed"] is not True or sample["narrationReviewed"] is not True:
            raise EvidenceRejected("subtitle or narration review missing")
        if not isinstance(sample["playbackReviewedBy"], str) or not sample["playbackReviewedBy"].strip():
            raise EvidenceRejected("human playback reviewer missing")
        video = regular_file(root, sample["videoFile"], "sample video")
        if not isinstance(sample["sha256"], str) or not SHA256.fullmatch(sample["sha256"]):
            raise EvidenceRejected("sample digest malformed")
        if digest(video) != sample["sha256"]:
            raise EvidenceRejected("sample digest mismatch")
        duration, audio = probe_video(video, ffprobe)
        if not 20 <= duration <= 90 or not audio:
            raise EvidenceRejected("sample duration or audio stream rejected")
        rights = sample["rightsRecords"]
        if not isinstance(rights, list) or not rights:
            raise EvidenceRejected("sample rights records missing")
        for record in rights:
            exact(record, {"asset", "source", "licenseOrPermission", "reviewedBy"}, "rights record")
            if not all(isinstance(record[key], str) and record[key].strip() for key in record):
                raise EvidenceRejected("rights record incomplete")

    failures = manifest["failureScenarios"]
    if not isinstance(failures, list) or {item.get("scenario") for item in failures if isinstance(item, dict)} != FAILURES:
        raise EvidenceRejected("failure matrix incomplete")
    for raw in failures:
        item = exact(raw, {"scenario", "normalAppPath", "expectedOutcomeObserved", "evidenceFile"}, "failure")
        if item["normalAppPath"] is not True or item["expectedOutcomeObserved"] is not True:
            raise EvidenceRejected("failure scenario did not pass through the normal App path")
        regular_file(root, item["evidenceFile"], "failure evidence")

    packages = manifest["platformPackages"]
    if not isinstance(packages, list) or {item.get("platform") for item in packages if isinstance(item, dict)} != PLATFORMS:
        raise EvidenceRejected("both formal platforms are required")
    for raw in packages:
        item = exact(raw, {
            "platform", "packageFile", "sha256", "signatureVerified", "cleanInstallVerified",
            "normalAppPathVerified", "cleanupVerified", "reportFile",
        }, "platform package")
        if not all(item[key] is True for key in (
            "signatureVerified", "cleanInstallVerified", "normalAppPathVerified", "cleanupVerified",
        )):
            raise EvidenceRejected("formal package verification incomplete")
        package = regular_file(root, item["packageFile"], "formal package")
        if not isinstance(item["sha256"], str) or digest(package) != item["sha256"]:
            raise EvidenceRejected("formal package digest mismatch")
        regular_file(root, item["reportFile"], "platform report")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    arguments = parser.parse_args()
    verify(arguments.evidence_root, arguments.ffprobe)
    print("IM-08 real sample and signed-package evidence passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
