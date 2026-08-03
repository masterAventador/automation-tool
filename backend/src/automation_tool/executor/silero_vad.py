"""CPU-only Silero VAD runtime over one digest-locked packaged ONNX model."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import stat
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn, Protocol, cast, runtime_checkable

import numpy as np
import numpy.typing as npt

CONTRACT_RELATIVE_PATH: Final = PurePosixPath("contracts/quality/silero-vad-runtime.v1.json")
MODEL_RELATIVE_PATH: Final = PurePosixPath("speech/silero-vad/silero_vad_16k_op15.onnx")
LICENSE_RELATIVE_PATH: Final = PurePosixPath("speech/silero-vad/SILERO-VAD-LICENSE.txt")
MODEL_CACHE_NAME: Final = "silero-vad"
MODEL_FILE_NAME: Final = MODEL_RELATIVE_PATH.name
LICENSE_FILE_NAME: Final = LICENSE_RELATIVE_PATH.name
SAMPLE_RATE_HZ: Final = 16_000
CHUNK_SAMPLES: Final = 512
CONTEXT_SAMPLES: Final = 64
STATE_SHAPE: Final = (2, 1, 128)
INPUT_NAMES: Final = ("input", "state", "sr")
OUTPUT_NAMES: Final = ("output", "stateN")
CPU_PROVIDER: Final = "CPUExecutionProvider"
CONTRACT_ID: Final = "automation-tool.silero-vad-runtime.v1"
UPSTREAM_COMMIT: Final = "7e30209a3e901f9842f81b225f3e93d8199902b1"
ONNXRUNTIME_VERSION: Final = "1.23.2"
MAXIMUM_CONTRACT_BYTES: Final = 64 * 1024
_READ_CHUNK_BYTES: Final = 1024 * 1024
ONNXRUNTIME_LICENSE_RELATIVE_PATH: Final = PurePosixPath("onnxruntime/LICENSE")
ONNXRUNTIME_LICENSE_ARTIFACTS: Final = (
    (
        "lf",
        1_073,
        "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
    ),
    (
        "crlf",
        1_094,
        "c250d6278f0b47a6439fb7592b08b58a55eb9f535aa49a1db63211c3f982b674",
    ),
)


class SileroVadUnavailable(RuntimeError):
    """A corrupt installation or invalid inference prevented speech detection."""

    def __init__(self) -> None:
        super().__init__("Silero VAD is unavailable; reinstall the application")


def _reject() -> NoReturn:
    raise SileroVadUnavailable from None


@dataclass(frozen=True, slots=True)
class _LockedFile:
    relative_path: PurePosixPath
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _RuntimeContract:
    model: _LockedFile
    license: _LockedFile
    onnxruntime_licenses: tuple[_LockedFile, ...]


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _build_cache_root() -> Path:
    override = os.environ.get("AUTOMATION_TOOL_BUILD_CACHE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/automation-tool-build"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "automation-tool-build"
        return Path.home() / "AppData/Local/automation-tool-build"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "automation-tool-build"
    return Path.home() / ".cache/automation-tool-build"


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _reject()
    return value


def _positive_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        _reject()
    return value


def _locked_file(
    record: object,
    *,
    expected_path: PurePosixPath,
) -> _LockedFile:
    if not isinstance(record, dict):
        _reject()
    rendered = record.get("packagedPath")
    if not isinstance(rendered, str) or PurePosixPath(rendered) != expected_path:
        _reject()
    return _LockedFile(
        relative_path=expected_path,
        bytes=_positive_integer(record.get("bytes")),
        sha256=_digest(record.get("sha256")),
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _read_stable_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
    exact_bytes: int | None = None,
) -> bytes | None:
    """Read only the ordinary non-link file held by the checked descriptor."""
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > maximum_bytes
            or (exact_bytes is not None and before.st_size != exact_bytes)
        ):
            return None
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, _READ_CHUNK_BYTES))
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return None
        after = os.fstat(descriptor)
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode):
            return None
        if not _same_file(before, after) or not _same_file(before, current):
            return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _load_contract(path: Path) -> _RuntimeContract:
    payload = _read_stable_regular_file(path, maximum_bytes=MAXIMUM_CONTRACT_BYTES)
    if payload is None:
        _reject()
    malformed = False
    document: object = None
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        malformed = True
    if malformed:
        _reject()
    if not isinstance(document, dict):
        _reject()
    policy = document.get("policy")
    upstream = document.get("upstream")
    model = document.get("model")
    runtime = document.get("runtime")
    if (
        document.get("schemaVersion") != 1
        or document.get("id") != CONTRACT_ID
        or not isinstance(policy, dict)
        or policy.get("runtimeDownloadAllowed") is not False
        or policy.get("missingAssetBehavior") != "fail_closed"
        or not isinstance(upstream, dict)
        or upstream.get("repository") != "https://github.com/snakers4/silero-vad"
        or upstream.get("tag") != "v6.2.1"
        or upstream.get("commit") != UPSTREAM_COMMIT
        or not isinstance(model, dict)
        or model.get("sourcePath") != "src/silero_vad/data/silero_vad_16k_op15.onnx"
        or model.get("sampleRateHz") != SAMPLE_RATE_HZ
        or model.get("chunkSamples") != CHUNK_SAMPLES
        or model.get("contextSamples") != CONTEXT_SAMPLES
        or model.get("stateShape") != list(STATE_SHAPE)
        or model.get("inputNames") != list(INPUT_NAMES)
        or model.get("outputNames") != list(OUTPUT_NAMES)
        or model.get("onnxOpset") != 15
        or model.get("sourceUrl")
        != (
            "https://raw.githubusercontent.com/snakers4/silero-vad/"
            "v6.2.1/src/silero_vad/data/silero_vad_16k_op15.onnx"
        )
        or not isinstance(runtime, dict)
        or runtime.get("distribution") != "onnxruntime"
        or runtime.get("version") != ONNXRUNTIME_VERSION
        or runtime.get("provider") != CPU_PROVIDER
        or runtime.get("licenseSpdx") != "MIT"
        or runtime.get("packagedLicensePath") != ONNXRUNTIME_LICENSE_RELATIVE_PATH.as_posix()
    ):
        _reject()
    license_artifacts = runtime.get("licenseArtifacts")
    if not isinstance(license_artifacts, dict) or set(license_artifacts) != {
        name for name, _bytes, _sha256 in ONNXRUNTIME_LICENSE_ARTIFACTS
    }:
        _reject()
    onnxruntime_licenses: list[_LockedFile] = []
    for name, expected_bytes, expected_sha256 in ONNXRUNTIME_LICENSE_ARTIFACTS:
        record = license_artifacts.get(name)
        if (
            not isinstance(record, dict)
            or set(record) != {"bytes", "sha256"}
            or record.get("bytes") != expected_bytes
            or record.get("sha256") != expected_sha256
        ):
            _reject()
        onnxruntime_licenses.append(
            _LockedFile(
                relative_path=ONNXRUNTIME_LICENSE_RELATIVE_PATH,
                bytes=expected_bytes,
                sha256=expected_sha256,
            )
        )
    license_record = document.get("license")
    if (
        not isinstance(license_record, dict)
        or license_record.get("spdx") != "MIT"
        or license_record.get("sourceUrl")
        != "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/LICENSE"
        or license_record.get("copyright") != "Copyright (c) 2020-present Silero Team"
    ):
        _reject()
    return _RuntimeContract(
        model=_locked_file(model, expected_path=MODEL_RELATIVE_PATH),
        license=_locked_file(license_record, expected_path=LICENSE_RELATIVE_PATH),
        onnxruntime_licenses=tuple(onnxruntime_licenses),
    )


def _verified_regular_file_bytes(path: Path, locked: _LockedFile) -> bytes:
    payload = _read_stable_regular_file(
        path,
        maximum_bytes=locked.bytes,
        exact_bytes=locked.bytes,
    )
    if payload is None or hashlib.sha256(payload).hexdigest() != locked.sha256:
        _reject()
    return payload


def _verified_one_of_regular_file_bytes(path: Path, locked_files: tuple[_LockedFile, ...]) -> bytes:
    for locked in locked_files:
        payload = _read_stable_regular_file(
            path,
            maximum_bytes=locked.bytes,
            exact_bytes=locked.bytes,
        )
        if payload is not None and hashlib.sha256(payload).hexdigest() == locked.sha256:
            return payload
    _reject()


def _resolved_assets(package_root: Path | None) -> tuple[bytes, _RuntimeContract]:
    frozen = getattr(sys, "_MEIPASS", None)
    if package_root is not None:
        root = Path(package_root)
        contract_path = root.joinpath(*CONTRACT_RELATIVE_PATH.parts)
        model_path = root.joinpath(*MODEL_RELATIVE_PATH.parts)
        license_path = root.joinpath(*LICENSE_RELATIVE_PATH.parts)
    elif isinstance(frozen, str):
        root = Path(frozen)
        contract_path = root.joinpath(*CONTRACT_RELATIVE_PATH.parts)
        model_path = root.joinpath(*MODEL_RELATIVE_PATH.parts)
        license_path = root.joinpath(*LICENSE_RELATIVE_PATH.parts)
    else:
        root = _build_cache_root() / MODEL_CACHE_NAME
        contract_path = _repository_root().joinpath(*CONTRACT_RELATIVE_PATH.parts)
        model_path = root / MODEL_FILE_NAME
        license_path = root / LICENSE_FILE_NAME
    contract = _load_contract(contract_path)
    model_payload = _verified_regular_file_bytes(model_path, contract.model)
    _verified_regular_file_bytes(license_path, contract.license)
    return model_payload, contract


def audit_packaged_silero_vad_runtime(bundle_directory: Path) -> None:
    """Fail unless a real onedir candidate carries the model and CPU runtime."""

    package_root = Path(bundle_directory) / "_internal"
    _model_payload, contract = _resolved_assets(package_root)
    runtime_license = package_root.joinpath(*contract.onnxruntime_licenses[0].relative_path.parts)
    _verified_one_of_regular_file_bytes(runtime_license, contract.onnxruntime_licenses)
    capi = package_root / "onnxruntime/capi"
    try:
        metadata = capi.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _reject()
        files = tuple(path for path in capi.iterdir() if path.is_file() and not path.is_symlink())
    except SileroVadUnavailable:
        raise
    except OSError:
        files = ()
    binding = any(
        path.name.startswith("onnxruntime_pybind11_state")
        and path.suffix.lower() in {".so", ".pyd"}
        and path.stat().st_size > 0
        for path in files
    )
    native_runtime = any(
        (
            path.name.startswith(f"libonnxruntime.{ONNXRUNTIME_VERSION}")
            or path.name.lower() == "onnxruntime.dll"
        )
        and path.stat().st_size > 0
        for path in files
    )
    if not binding or not native_runtime:
        _reject()


@runtime_checkable
class _InferenceSession(Protocol):
    def run(self, output_names: object, input_feed: object) -> object: ...


class _SessionOptions(Protocol):
    inter_op_num_threads: int
    intra_op_num_threads: int


class _OnnxRuntime(Protocol):
    __version__: str

    def get_available_providers(self) -> object: ...

    def SessionOptions(self) -> _SessionOptions: ...

    def InferenceSession(
        self,
        model: bytes,
        *,
        providers: list[str],
        sess_options: _SessionOptions,
    ) -> object: ...


class SileroVad:
    """Stateful probability inference for consecutive 16 kHz PCM chunks."""

    def __init__(self, session: object) -> None:
        if not isinstance(session, _InferenceSession):
            _reject()
        self._session = session
        self._state: npt.NDArray[np.float32] = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context: npt.NDArray[np.float32] = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def reset(self) -> None:
        self._state = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def probability(
        self,
        samples: object,
        *,
        sample_rate_hz: object,
    ) -> float:
        """Return one speech probability without accepting malformed PCM."""

        if (
            type(samples) is not tuple
            or len(samples) != CHUNK_SAMPLES
            or sample_rate_hz != SAMPLE_RATE_HZ
            or type(sample_rate_hz) is not int
            or any(
                type(value) is not float or not math.isfinite(value) or not -1.0 <= value <= 1.0
                for value in samples
            )
        ):
            _reject()
        chunk = np.asarray(samples, dtype=np.float32)
        model_input = np.concatenate((self._context, chunk)).reshape(1, -1)
        failed = False
        outputs: object = None
        try:
            outputs = self._session.run(
                list(OUTPUT_NAMES),
                {
                    INPUT_NAMES[0]: model_input,
                    INPUT_NAMES[1]: self._state,
                    INPUT_NAMES[2]: np.asarray(SAMPLE_RATE_HZ, dtype=np.int64),
                },
            )
        except Exception:
            failed = True
        if failed or not isinstance(outputs, (list, tuple)) or len(outputs) != 2:
            _reject()
        probability = np.asarray(outputs[0])
        state = np.asarray(outputs[1])
        if (
            probability.shape != (1, 1)
            or probability.dtype != np.float32
            or state.shape != STATE_SHAPE
            or state.dtype != np.float32
            or not np.isfinite(probability).all()
            or not np.isfinite(state).all()
        ):
            _reject()
        value = float(probability[0, 0])
        if not 0.0 <= value <= 1.0:
            _reject()
        self._context = chunk[-CONTEXT_SAMPLES:].copy()
        self._state = state.copy()
        return value


def _import_onnxruntime() -> object:
    return importlib.import_module("onnxruntime")


def create_silero_vad(
    *,
    package_root: Path | None = None,
    runtime_loader: Callable[[], object] = _import_onnxruntime,
) -> SileroVad:
    """Verify packaged bytes, then create exactly one CPU ONNX session."""

    model_payload, _contract = _resolved_assets(package_root)
    failed = False
    session: object = None
    try:
        runtime = cast(_OnnxRuntime, runtime_loader())
        providers = runtime.get_available_providers()
        if (
            getattr(runtime, "__version__", None) != ONNXRUNTIME_VERSION
            or not isinstance(providers, list)
            or CPU_PROVIDER not in providers
            or not callable(runtime.SessionOptions)
            or not callable(runtime.InferenceSession)
        ):
            failed = True
        else:
            options = runtime.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            session = runtime.InferenceSession(
                model_payload,
                providers=[CPU_PROVIDER],
                sess_options=options,
            )
    except Exception:
        failed = True
    if failed:
        _reject()
    return SileroVad(session)


__all__ = [
    "CHUNK_SAMPLES",
    "CONTEXT_SAMPLES",
    "CPU_PROVIDER",
    "LICENSE_RELATIVE_PATH",
    "MODEL_RELATIVE_PATH",
    "SAMPLE_RATE_HZ",
    "STATE_SHAPE",
    "SileroVad",
    "SileroVadUnavailable",
    "audit_packaged_silero_vad_runtime",
    "create_silero_vad",
]
