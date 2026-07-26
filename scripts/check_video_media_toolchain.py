#!/usr/bin/env python3
"""Validate the closed FFmpeg/ffprobe supply-chain contract and staged payload."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts/video/ffmpeg-toolchain.v1.json"
SHA256_LENGTH = 64


class ContractError(ValueError):
    pass


def is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def create_test_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 1314:
            raise
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not link.is_dir():
        raise ContractError("could not create the Windows junction rejection fixture")


def remove_test_directory_link(link: Path) -> None:
    """Tear down whichever of the two fixtures `create_test_directory_link` made.

    The pair is asymmetric and has to stay that way: `os.rmdir` is the only call
    that removes a Windows junction without following it, and it raises
    `NotADirectoryError` on a POSIX symlink, where `os.unlink` is correct.
    Removing only the junction -- which is what this used to do -- made the
    self-test raise on macOS and Linux from inside a `finally`, long after the
    assertion it guards had already passed.

    `Path.is_symlink()` answers this on both sides: CPython reports a junction
    as a directory rather than a link, so Windows still takes the `rmdir` branch.
    Neither call touches the directory the fixture points at.
    """
    if link.is_symlink():
        link.unlink()
    else:
        link.rmdir()


def plain_files_under(root: Path) -> set[Path]:
    files: set[Path] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ContractError("candidate directory cannot be enumerated") from error
        for entry in entries:
            path = Path(entry.path)
            if is_link_or_reparse(path):
                raise ContractError("candidate contains a link or reparse point")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                files.add(path)
            else:
                raise ContractError("candidate contains a special file")
    return files


def exact_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    if set(value) != expected:
        raise ContractError(f"{where} fields must be exactly {sorted(expected)}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_contract(document: dict[str, Any]) -> None:
    exact_keys(
        document,
        {
            "schema_version",
            "policy",
            "verified_at",
            "ffmpeg",
            "x264",
            "required_capabilities",
            "worker_environment",
            "targets",
            "package_layout",
            "release_requirements",
        },
        "contract",
    )
    if document["schema_version"] != 1 or document["policy"] != "fail_closed":
        raise ContractError("unsupported schema or policy")
    ffmpeg = document["ffmpeg"]
    exact_keys(
        ffmpeg,
        {
            "version",
            "source_url",
            "source_sha256",
            "license",
            "why_gpl",
            "forbidden_configure_flags",
        },
        "ffmpeg",
    )
    if ffmpeg["version"] != "8.1.2":
        raise ContractError("FFmpeg version must be exactly 8.1.2")
    if ffmpeg["license"] != "GPL-3.0-or-later" or "libx264" not in ffmpeg["why_gpl"]:
        raise ContractError("libx264 toolchain must be declared GPL")
    if len(ffmpeg["source_sha256"]) != SHA256_LENGTH:
        raise ContractError("source SHA-256 must be complete")
    if "--enable-nonfree" not in ffmpeg["forbidden_configure_flags"]:
        raise ContractError("nonfree builds must be forbidden")
    x264 = document["x264"]
    exact_keys(x264, {"revision", "source_url", "source_sha256", "license"}, "x264")
    if x264 != {
        "revision": "b35605ace3ddf7c1a5d67a2eb553f034aef41d55",
        "source_url": "https://code.videolan.org/videolan/x264/-/archive/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz",
        "source_sha256": "cd71a7515b0e9a012e1ac9b1f8415bebcaf6fc97d4db32286642ac4c0fbe24f9",
        "license": "GPL-2.0-or-later",
    }:
        raise ContractError("x264 source or license drifted")

    capabilities = document["required_capabilities"]
    exact_keys(
        capabilities,
        {
            "programs",
            "encoders",
            "decoders",
            "demuxers",
            "muxers",
            "filters",
            "protocols",
        },
        "required_capabilities",
    )
    required = {
        "programs": {"ffmpeg", "ffprobe"},
        "encoders": {"libx264", "aac", "png"},
        "decoders": {"h264", "aac", "png"},
        "demuxers": {"concat", "image2", "image2pipe", "mov"},
        "muxers": {"mp4", "mov", "image2"},
        "filters": {"amix", "aresample", "concat", "overlay", "scale"},
        "protocols": {"file", "pipe"},
    }
    for group, expected in required.items():
        if set(capabilities[group]) != expected or len(capabilities[group]) != len(
            expected
        ):
            raise ContractError(f"capability group {group} drifted")

    environments = document["worker_environment"]
    if environments != {
        "智能素材成片": {"IMAGEIO_FFMPEG_EXE": "ffmpeg"},
        "品牌动效成片": {
            "HYPERFRAMES_FFMPEG_PATH": "ffmpeg",
            "HYPERFRAMES_FFPROBE_PATH": "ffprobe",
        },
    }:
        raise ContractError("worker environment mapping drifted")

    targets = document["targets"]
    if not isinstance(targets, list) or len(targets) != 2:
        raise ContractError("exactly two release targets are required")
    expected_targets = {
        "macos-arm64": ("macos", "arm64"),
        "windows-x86_64": ("windows", "x86_64"),
    }
    seen: set[str] = set()
    for target in targets:
        exact_keys(target, {"id", "os", "arch", "build_runner"}, "target")
        target_id = target["id"]
        if target_id in seen or expected_targets.get(target_id) != (
            target["os"],
            target["arch"],
        ):
            raise ContractError("target tuple is unsupported or duplicated")
        seen.add(target_id)
        expected_runner = "macos-15" if target_id == "macos-arm64" else "windows-2025"
        if target["build_runner"] != expected_runner:
            raise ContractError("release runner drifted")
    if seen != set(expected_targets):
        raise ContractError("both release targets are required")

    layout = document["package_layout"]
    exact_keys(
        layout,
        {
            "root",
            "manifest",
            "notice",
            "license",
            "build_info",
            "source_archive",
            "x264_source_archive",
            "binaries",
        },
        "package_layout",
    )
    if layout != {
        "root": "media-toolchain",
        "manifest": "manifest.json",
        "notice": "NOTICE.txt",
        "license": "COPYING.GPLv3",
        "build_info": "BUILD-INFO.txt",
        "source_archive": "source/ffmpeg-8.1.2.tar.xz",
        "x264_source_archive": "source/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz",
        "binaries": {
            "macos": ["bin/ffmpeg", "bin/ffprobe"],
            "windows": ["bin/ffmpeg.exe", "bin/ffprobe.exe"],
        },
    }:
        raise ContractError("package layout drifted")

    release = document["release_requirements"]
    exact_keys(
        release,
        {
            "bundle_source_archive",
            "bundle_license_and_build_info",
            "allow_system_path_fallback",
            "allow_runtime_download",
            "one_verified_pair_for_both_video_providers",
        },
        "release_requirements",
    )
    if release != {
        "bundle_source_archive": True,
        "bundle_license_and_build_info": True,
        "allow_system_path_fallback": False,
        "allow_runtime_download": False,
        "one_verified_pair_for_both_video_providers": True,
    }:
        raise ContractError("release requirements may not be weakened")


def command_output(binary: Path, *arguments: str) -> str:
    result = subprocess.run(
        [str(binary), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise ContractError(f"media tool failed capability probe: {binary.name}")
    return result.stdout + result.stderr


def run_checked(binary: Path, *arguments: str) -> None:
    result = subprocess.run(
        [str(binary), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise ContractError(
            f"media compatibility command failed: {binary.name}: {detail}"
        )


def compatibility_smoke(ffmpeg: Path, ffprobe: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="automation-tool-vf04-smoke-") as directory:
        root = Path(directory)
        material_clips: list[Path] = []
        for index, color in enumerate(("0x17324d", "0xd97706"), start=1):
            clip = root / f"material-{index}.mp4"
            run_checked(
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x180:r=10:d=0.5",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={400 + index * 100}:sample_rate=44100:duration=0.5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            )
            material_clips.append(clip)
        concat_file = root / "clips.txt"
        concat_file.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in material_clips),
            encoding="utf-8",
        )
        material_output = root / "material-output.mp4"
        run_checked(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(material_output),
        )

        run_checked(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=10:d=1",
            str(root / "frame-%03d.png"),
        )
        motion_output = root / "motion-output.mp4"
        run_checked(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            "10",
            "-i",
            str(root / "frame-%03d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(motion_output),
        )
        for output in (material_output, motion_output):
            probe = command_output(
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_type",
                "-of",
                "json",
                str(output),
            )
            metadata = json.loads(probe)
            if not any(
                stream.get("codec_name") == "h264" for stream in metadata["streams"]
            ):
                raise ContractError("compatibility smoke did not produce H.264 video")


def validate_candidate(root: Path, target_id: str, document: dict[str, Any]) -> None:
    if is_link_or_reparse(root) or not root.is_dir():
        raise ContractError("candidate root must be a real directory")
    target = next(
        (item for item in document["targets"] if item["id"] == target_id), None
    )
    if target is None:
        raise ContractError("unknown release target")
    suffix = ".exe" if target["os"] == "windows" else ""
    ffmpeg = root / "bin" / f"ffmpeg{suffix}"
    ffprobe = root / "bin" / f"ffprobe{suffix}"
    manifest_path = root / "manifest.json"
    for path in (
        ffmpeg,
        ffprobe,
        root / "NOTICE.txt",
        root / "BUILD-INFO.txt",
        root / "source" / "ffmpeg-8.1.2.tar.xz",
        root / "source" / "x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz",
        manifest_path,
    ):
        if is_link_or_reparse(path) or not path.is_file():
            raise ContractError(
                f"candidate file missing or linked: {path.relative_to(root)}"
            )
    if (
        sha256_file(root / "source" / "ffmpeg-8.1.2.tar.xz")
        != document["ffmpeg"]["source_sha256"]
    ):
        raise ContractError("bundled FFmpeg source digest mismatch")
    if (
        sha256_file(
            root / "source" / "x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz"
        )
        != document["x264"]["source_sha256"]
    ):
        raise ContractError("bundled x264 source digest mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exact_keys(
        manifest,
        {"schema_version", "target_id", "version", "license", "files"},
        "manifest",
    )
    if (
        manifest["schema_version"],
        manifest["target_id"],
        manifest["version"],
        manifest["license"],
    ) != (
        1,
        target_id,
        "8.1.2",
        "GPL-3.0-or-later",
    ):
        raise ContractError("runtime manifest identity mismatch")
    candidate_files = plain_files_under(root)
    expected_paths = {
        path.relative_to(root).as_posix()
        for path in candidate_files
        if path != manifest_path
    }
    manifest_paths: set[str] = set()
    for entry in manifest["files"]:
        exact_keys(entry, {"path", "size", "sha256"}, "manifest file")
        relative = Path(entry["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or entry["path"] in manifest_paths
        ):
            raise ContractError("runtime manifest path is unsafe or duplicated")
        path = root / relative
        if is_link_or_reparse(path) or not path.is_file():
            raise ContractError("runtime manifest file is missing or linked")
        if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
            raise ContractError("runtime manifest file identity mismatch")
        manifest_paths.add(entry["path"])
    if manifest_paths != expected_paths:
        raise ContractError(
            "runtime manifest must cover every packaged file exactly once"
        )

    version_output = command_output(ffmpeg, "-version")
    probe_version_output = command_output(ffprobe, "-version")
    if (
        "ffmpeg version 8.1.2" not in version_output
        or "ffprobe version 8.1.2" not in probe_version_output
    ):
        raise ContractError("ffmpeg/ffprobe exact version mismatch")
    if "--enable-nonfree" in version_output or "--enable-gpl" not in version_output:
        raise ContractError("candidate must be GPL and must not be nonfree")

    probes = {
        "encoders": command_output(ffmpeg, "-hide_banner", "-encoders"),
        "decoders": command_output(ffmpeg, "-hide_banner", "-decoders"),
        "demuxers": command_output(ffmpeg, "-hide_banner", "-demuxers"),
        "muxers": command_output(ffmpeg, "-hide_banner", "-muxers"),
        "filters": command_output(ffmpeg, "-hide_banner", "-filters"),
        "protocols": command_output(ffmpeg, "-hide_banner", "-protocols"),
    }
    for group, names in document["required_capabilities"].items():
        if group == "programs":
            continue
        for name in names:
            if name not in probes[group]:
                raise ContractError(f"candidate missing {group} capability: {name}")
    compatibility_smoke(ffmpeg, ffprobe)


def self_test(document: dict[str, Any]) -> None:
    validate_contract(document)
    mutations: list[tuple[str, Any]] = [
        ("version drift", lambda data: data["ffmpeg"].update(version="8.1.1")),
        (
            "license downgrade",
            lambda data: data["ffmpeg"].update(license="LGPL-2.1-or-later"),
        ),
        (
            "runtime download",
            lambda data: data["release_requirements"].update(
                allow_runtime_download=True
            ),
        ),
        (
            "system fallback",
            lambda data: data["release_requirements"].update(
                allow_system_path_fallback=True
            ),
        ),
        (
            "missing codec",
            lambda data: data["required_capabilities"]["encoders"].remove("libx264"),
        ),
        ("unknown target", lambda data: data["targets"][0].update(arch="x86_64")),
        ("partial digest", lambda data: data["x264"].update(source_sha256="abc")),
        ("unknown field", lambda data: data.update(extra=True)),
    ]
    for label, mutate in mutations:
        candidate = copy.deepcopy(document)
        mutate(candidate)
        try:
            validate_contract(candidate)
        except ContractError:
            continue
        raise ContractError(f"self-test accepted invalid case: {label}")

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-vf04-selftest-"
    ) as directory:
        linked = Path(directory) / "linked"
        create_test_directory_link(linked, Path(directory))
        try:
            validate_candidate(linked, "macos-arm64", document)
        except ContractError as error:
            if str(error) != "candidate root must be a real directory":
                raise
        else:
            raise ContractError("self-test accepted a linked candidate root")
        finally:
            remove_test_directory_link(linked)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--target")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    validate_contract(document)
    if args.self_test:
        self_test(document)
    if bool(args.candidate) != bool(args.target):
        raise SystemExit("--candidate and --target must be provided together")
    if args.candidate:
        validate_candidate(args.candidate.absolute(), args.target, document)
    print("video media toolchain contract is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
