#!/usr/bin/env python3
"""Fetch and cache the one reviewed Silero VAD model used by the Executor."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/silero-vad-runtime.v1.json"
CACHE_NAME = "silero-vad"
# 1.3 MB of immutable, platform-independent ONNX weights, committed alongside
# the fonts. Nothing about them needs a per-machine download.
COMMITTED_ASSET_DIRECTORY = REPOSITORY_ROOT / "assets/silero-vad"
MODEL_SOURCE_PREFIX = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/"
)
LICENSE_SOURCE_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/LICENSE"
UPSTREAM_REPOSITORY = "https://github.com/snakers4/silero-vad"
UPSTREAM_TAG = "v6.2.1"
UPSTREAM_COMMIT = "7e30209a3e901f9842f81b225f3e93d8199902b1"
CONTRACT_ID = "automation-tool.silero-vad-runtime.v1"
MODEL_PACKAGED_PATH = PurePosixPath("speech/silero-vad/silero_vad_16k_op15.onnx")
LICENSE_PACKAGED_PATH = PurePosixPath("speech/silero-vad/SILERO-VAD-LICENSE.txt")
FETCH_TIMEOUT_SECONDS = 120
MAXIMUM_MODEL_BYTES = 4 * 1024 * 1024
MAXIMUM_LICENSE_BYTES = 16 * 1024


class SileroVadAssetContractRejected(RuntimeError):
    """The model pinning contract is incomplete or unsafe."""


class SileroVadAssetUnavailable(RuntimeError):
    """The reviewed model bytes could not be obtained."""


@dataclass(frozen=True, slots=True)
class LockedAsset:
    source_url: str
    bytes: int
    sha256: str
    packaged_path: PurePosixPath

    @property
    def cached_name(self) -> str:
        return self.packaged_path.name


@dataclass(frozen=True, slots=True)
class SileroVadAssetContract:
    model: LockedAsset
    license: LockedAsset


def _reject(message: str) -> NoReturn:
    raise SileroVadAssetContractRejected(message)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _reject(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        _reject(f"{field} must be a non-empty string")
    return value


def _positive_integer(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        _reject(f"{field} must be a positive integer")
    return value


def _digest(value: object, field: str) -> str:
    digest = _text(value, field)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        _reject(f"{field} must be a lowercase SHA-256")
    return digest


def _packaged_path(value: object, field: str, expected: PurePosixPath) -> PurePosixPath:
    rendered = _text(value, field)
    candidate = PurePosixPath(rendered)
    if (
        candidate != expected
        or candidate.is_absolute()
        or ".." in candidate.parts
        or rendered != candidate.as_posix()
    ):
        _reject(f"{field} is not the locked package path")
    return candidate


def _locked_asset(
    value: object,
    *,
    field: str,
    expected_path: PurePosixPath,
    source_prefix: str,
) -> LockedAsset:
    record = _object(value, field)
    source_url = _text(record.get("sourceUrl"), f"{field}.sourceUrl")
    if not source_url.startswith(source_prefix):
        _reject(f"{field}.sourceUrl is not the locked upstream release")
    return LockedAsset(
        source_url=source_url,
        bytes=_positive_integer(record.get("bytes"), f"{field}.bytes"),
        sha256=_digest(record.get("sha256"), f"{field}.sha256"),
        packaged_path=_packaged_path(
            record.get("packagedPath"),
            f"{field}.packagedPath",
            expected_path,
        ),
    )


def load_silero_vad_contract(
    path: Path = CONTRACT_PATH,
) -> SileroVadAssetContract:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SileroVadAssetContractRejected("cannot read the Silero VAD contract") from error
    root = _object(document, "contract")
    if root.get("schemaVersion") != 1 or root.get("id") != CONTRACT_ID:
        _reject("the Silero VAD contract identity is not locked")
    policy = _object(root.get("policy"), "policy")
    if (
        policy.get("runtimeDownloadAllowed") is not False
        or policy.get("missingAssetBehavior") != "fail_closed"
    ):
        _reject("the Silero VAD runtime download policy is not fail closed")
    upstream = _object(root.get("upstream"), "upstream")
    if (
        upstream.get("repository") != UPSTREAM_REPOSITORY
        or upstream.get("tag") != UPSTREAM_TAG
        or upstream.get("commit") != UPSTREAM_COMMIT
    ):
        _reject("the Silero VAD upstream release is not locked")
    model_record = _object(root.get("model"), "model")
    if (
        model_record.get("sourcePath") != "src/silero_vad/data/silero_vad_16k_op15.onnx"
        or model_record.get("sampleRateHz") != 16_000
        or model_record.get("chunkSamples") != 512
        or model_record.get("contextSamples") != 64
        or model_record.get("stateShape") != [2, 1, 128]
        or model_record.get("inputNames") != ["input", "state", "sr"]
        or model_record.get("outputNames") != ["output", "stateN"]
        or model_record.get("onnxOpset") != 15
        or model_record.get("sourceUrl")
        != (
            "https://raw.githubusercontent.com/snakers4/silero-vad/"
            "v6.2.1/src/silero_vad/data/silero_vad_16k_op15.onnx"
        )
    ):
        _reject("the Silero VAD model interface is not locked")
    license_record = _object(root.get("license"), "license")
    if (
        license_record.get("spdx") != "MIT"
        or license_record.get("copyright") != "Copyright (c) 2020-present Silero Team"
    ):
        _reject("the Silero VAD license is not MIT")
    runtime = _object(root.get("runtime"), "runtime")
    expected_runtime_licenses = {
        "lf": {
            "bytes": 1_073,
            "sha256": "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
        },
        "crlf": {
            "bytes": 1_094,
            "sha256": "c250d6278f0b47a6439fb7592b08b58a55eb9f535aa49a1db63211c3f982b674",
        },
    }
    if (
        runtime.get("distribution") != "onnxruntime"
        or runtime.get("version") != "1.23.2"
        or runtime.get("provider") != "CPUExecutionProvider"
        or runtime.get("licenseSpdx") != "MIT"
        or runtime.get("packagedLicensePath") != "onnxruntime/LICENSE"
        or runtime.get("licenseArtifacts") != expected_runtime_licenses
    ):
        _reject("the Silero VAD runtime is not locked to CPU ONNX Runtime")
    model = _locked_asset(
        model_record,
        field="model",
        expected_path=MODEL_PACKAGED_PATH,
        source_prefix=MODEL_SOURCE_PREFIX,
    )
    license_asset = _locked_asset(
        license_record,
        field="license",
        expected_path=LICENSE_PACKAGED_PATH,
        source_prefix=LICENSE_SOURCE_URL,
    )
    if license_asset.source_url != LICENSE_SOURCE_URL:
        _reject("license.sourceUrl is not the locked upstream license")
    return SileroVadAssetContract(model=model, license=license_asset)


def _verify(asset: LockedAsset, payload: bytes, *, is_license: bool) -> None:
    if len(payload) != asset.bytes or hashlib.sha256(payload).hexdigest() != asset.sha256:
        raise SileroVadAssetUnavailable(f"{asset.cached_name} does not match its locked bytes")
    if is_license and (
        not payload.startswith(b"MIT License\n")
        or b"Copyright (c)" not in payload
        or b"Permission is hereby granted" not in payload
    ):
        raise SileroVadAssetUnavailable("the Silero VAD license text is not MIT")


def _fetch(url: str, *, limit: int) -> bytes:
    if not (url.startswith(MODEL_SOURCE_PREFIX) or url == LICENSE_SOURCE_URL):
        raise SileroVadAssetUnavailable("the Silero VAD URL is not locked")
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
            payload = bytes(response.read(limit + 1))
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise SileroVadAssetUnavailable("cannot fetch the locked Silero VAD asset") from error
    if len(payload) > limit:
        raise SileroVadAssetUnavailable("the Silero VAD asset exceeds its fetch limit")
    return payload


def _committed_payload(asset: LockedAsset) -> bytes | None:
    """Return the committed bytes only when they are the locked ones.

    Matching on the file name alone is not enough: a caller may pass a contract
    whose `cachedName` collides with a committed file while pinning entirely
    different bytes, and returning the local file then answers a question that
    was never asked. Digesting first makes this a cache — the locked digest
    stays the only thing that decides what counts as the asset.
    """
    committed = COMMITTED_ASSET_DIRECTORY / asset.cached_name
    if not committed.is_file():
        return None
    payload = committed.read_bytes()
    if hashlib.sha256(payload).hexdigest() != asset.sha256:
        return None
    return payload


def ensure_silero_vad_assets(
    *,
    root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """Return the verified model cache, fetching only when its lock changed."""

    from video_runtime_cache import ensure_cached

    contract = load_silero_vad_contract(contract_path)

    def obtain(asset: LockedAsset, *, limit: int) -> bytes:
        committed = _committed_payload(asset)
        if committed is not None:
            return committed
        if fetch is None:
            return _fetch(asset.source_url, limit=limit)
        try:
            return fetch(asset.source_url)
        except SileroVadAssetUnavailable:
            raise
        except Exception as error:
            raise SileroVadAssetUnavailable("cannot fetch the locked Silero VAD asset") from error

    def build(destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        model = obtain(contract.model, limit=MAXIMUM_MODEL_BYTES)
        _verify(contract.model, model, is_license=False)
        (destination / contract.model.cached_name).write_bytes(model)
        license_payload = obtain(contract.license, limit=MAXIMUM_LICENSE_BYTES)
        _verify(contract.license, license_payload, is_license=True)
        (destination / contract.license.cached_name).write_bytes(license_payload)

    return ensure_cached(
        name=CACHE_NAME,
        contracts=(contract_path, Path(__file__)),
        build=build,
        root=root,
    )


__all__ = [
    "CACHE_NAME",
    "CONTRACT_PATH",
    "LICENSE_PACKAGED_PATH",
    "MODEL_PACKAGED_PATH",
    "LockedAsset",
    "SileroVadAssetContract",
    "SileroVadAssetContractRejected",
    "SileroVadAssetUnavailable",
    "ensure_silero_vad_assets",
    "load_silero_vad_contract",
]
