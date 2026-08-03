"""LE-14 T2: locked Silero ONNX assets and a CPU-only inference boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import onnxruntime  # type: ignore[import-untyped]
import pytest

from automation_tool.executor import silero_vad as silero_vad_module
from automation_tool.executor.silero_vad import (
    SileroVad,
    SileroVadUnavailable,
    audit_packaged_silero_vad_runtime,
    create_silero_vad,
)

FIXTURE_MODEL = b"synthetic-silero-model"
FIXTURE_LICENSE = (
    b"MIT License\nCopyright (c) fixture\nPermission is hereby granted, free of charge\n"
)
MODEL_RELATIVE = "speech/silero-vad/silero_vad_16k_op15.onnx"
LICENSE_RELATIVE = "speech/silero-vad/SILERO-VAD-LICENSE.txt"
CONTRACT_RELATIVE = "contracts/quality/silero-vad-runtime.v1.json"
assert isinstance(onnxruntime.__file__, str)
ONNXRUNTIME_LICENSE = Path(onnxruntime.__file__).with_name("LICENSE").read_bytes()
LF_ONNXRUNTIME_LICENSE = ONNXRUNTIME_LICENSE.replace(b"\r\n", b"\n")
WINDOWS_ONNXRUNTIME_LICENSE = LF_ONNXRUNTIME_LICENSE.replace(b"\n", b"\r\n")
assert len(LF_ONNXRUNTIME_LICENSE) == 1_073
assert (
    hashlib.sha256(LF_ONNXRUNTIME_LICENSE).hexdigest()
    == "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c"
)
assert len(WINDOWS_ONNXRUNTIME_LICENSE) == 1_094
assert (
    hashlib.sha256(WINDOWS_ONNXRUNTIME_LICENSE).hexdigest()
    == "c250d6278f0b47a6439fb7592b08b58a55eb9f535aa49a1db63211c3f982b674"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_source_runtime_resolves_the_repository_contract() -> None:
    repository = silero_vad_module._repository_root()

    assert (repository / CONTRACT_RELATIVE).is_file()


def test_locked_asset_reader_requests_binary_mode_when_the_platform_defines_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"header\r\n\x1abinary\r\ntail"
    asset = tmp_path / "locked.onnx"
    asset.write_bytes(payload)
    binary_flag = 1 << 28
    real_binary_flag = getattr(os, "O_BINARY", 0)
    real_open = os.open
    observed_flags: list[int] = []

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        observed_flags.append(flags)
        return real_open(path, (flags & ~binary_flag) | real_binary_flag, mode)

    monkeypatch.setattr(os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(os, "open", recording_open)

    assert (
        silero_vad_module._read_stable_regular_file(
            asset,
            maximum_bytes=len(payload),
            exact_bytes=len(payload),
        )
        == payload
    )
    assert observed_flags and observed_flags[0] & binary_flag


def _contract() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": "automation-tool.silero-vad-runtime.v1",
        "policy": {
            "runtimeDownloadAllowed": False,
            "missingAssetBehavior": "fail_closed",
        },
        "upstream": {
            "repository": "https://github.com/snakers4/silero-vad",
            "tag": "v6.2.1",
            "commit": "7e30209a3e901f9842f81b225f3e93d8199902b1",
        },
        "model": {
            "sourcePath": "src/silero_vad/data/silero_vad_16k_op15.onnx",
            "sourceUrl": (
                "https://raw.githubusercontent.com/snakers4/silero-vad/"
                "v6.2.1/src/silero_vad/data/silero_vad_16k_op15.onnx"
            ),
            "bytes": len(FIXTURE_MODEL),
            "sha256": _sha256(FIXTURE_MODEL),
            "packagedPath": MODEL_RELATIVE,
            "sampleRateHz": 16_000,
            "chunkSamples": 512,
            "contextSamples": 64,
            "stateShape": [2, 1, 128],
            "inputNames": ["input", "state", "sr"],
            "outputNames": ["output", "stateN"],
            "onnxOpset": 15,
        },
        "license": {
            "spdx": "MIT",
            "sourceUrl": "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/LICENSE",
            "bytes": len(FIXTURE_LICENSE),
            "sha256": _sha256(FIXTURE_LICENSE),
            "packagedPath": LICENSE_RELATIVE,
            "copyright": "Copyright (c) 2020-present Silero Team",
        },
        "runtime": {
            "distribution": "onnxruntime",
            "version": "1.23.2",
            "provider": "CPUExecutionProvider",
            "licenseSpdx": "MIT",
            "packagedLicensePath": "onnxruntime/LICENSE",
            "licenseArtifacts": {
                "lf": {
                    "bytes": 1_073,
                    "sha256": "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
                },
                "crlf": {
                    "bytes": 1_094,
                    "sha256": "c250d6278f0b47a6439fb7592b08b58a55eb9f535aa49a1db63211c3f982b674",
                },
            },
        },
    }


def _write_package(root: Path) -> None:
    contract = root / CONTRACT_RELATIVE
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps(_contract()), encoding="utf-8")
    model = root / MODEL_RELATIVE
    model.parent.mkdir(parents=True)
    model.write_bytes(FIXTURE_MODEL)
    license_path = root / LICENSE_RELATIVE
    license_path.write_bytes(FIXTURE_LICENSE)


def _write_candidate(root: Path) -> Path:
    bundle = root / "automation-tool-executor"
    package = bundle / "_internal"
    _write_package(package)
    runtime_license = package / "onnxruntime/LICENSE"
    runtime_license.parent.mkdir(parents=True)
    runtime_license.write_bytes(ONNXRUNTIME_LICENSE)
    capi = package / "onnxruntime/capi"
    capi.mkdir(parents=True)
    (capi / "onnxruntime_pybind11_state.so").write_bytes(b"binding")
    (capi / "libonnxruntime.1.23.2.dylib").write_bytes(b"runtime")
    return bundle


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[
            tuple[object, dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]]]
        ] = []

    def run(
        self,
        output_names: object,
        inputs: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]],
    ) -> list[np.ndarray[tuple[int, ...], np.dtype[np.float32]]]:
        self.calls.append((output_names, inputs))
        next_state = np.full((2, 1, 128), len(self.calls), dtype=np.float32)
        return [np.array([[0.75]], dtype=np.float32), next_state]


class RecordingRuntime:
    __version__ = "1.23.2"

    def __init__(
        self,
        *,
        providers: tuple[str, ...] = ("CPUExecutionProvider",),
        session: object | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.providers = providers
        self.session = RecordingSession() if session is None else session
        self.failure = failure
        self.session_options: list[object] = []
        self.session_calls: list[tuple[bytes, tuple[str, ...], object]] = []

    def get_available_providers(self) -> list[str]:
        return list(self.providers)

    def SessionOptions(self) -> object:
        options = SimpleNamespace(inter_op_num_threads=0, intra_op_num_threads=0)
        self.session_options.append(options)
        return options

    def InferenceSession(
        self,
        model: bytes,
        *,
        providers: list[str],
        sess_options: object,
    ) -> object:
        self.session_calls.append((model, tuple(providers), sess_options))
        if self.failure is not None:
            raise self.failure
        return self.session


@pytest.mark.parametrize("failure", ("missing", "corrupt", "symlink"))
def test_model_is_rejected_before_onnxruntime_is_loaded(
    tmp_path: Path,
    failure: str,
) -> None:
    package = tmp_path / "package"
    _write_package(package)
    model = package / MODEL_RELATIVE
    if failure == "missing":
        model.unlink()
    elif failure == "corrupt":
        model.write_bytes(FIXTURE_MODEL[:-1] + b"x")
    else:
        target = package / "replacement.onnx"
        target.write_bytes(FIXTURE_MODEL)
        model.unlink()
        try:
            model.symlink_to(os.path.relpath(target, model.parent))
        except OSError:
            pytest.skip("symlinks are unavailable")
    loads = 0

    def load_runtime() -> object:
        nonlocal loads
        loads += 1
        return RecordingRuntime()

    with pytest.raises(
        SileroVadUnavailable,
        match=r"^Silero VAD is unavailable; reinstall the application$",
    ) as captured:
        create_silero_vad(package_root=package, runtime_loader=load_runtime)

    assert loads == 0
    assert captured.value.__context__ is None


@pytest.mark.parametrize("failure", ("missing-license", "corrupt-license", "contract-link"))
def test_license_and_contract_are_part_of_the_same_runtime_gate(
    tmp_path: Path,
    failure: str,
) -> None:
    package = tmp_path / "package"
    _write_package(package)
    if failure == "missing-license":
        (package / LICENSE_RELATIVE).unlink()
    elif failure == "corrupt-license":
        (package / LICENSE_RELATIVE).write_bytes(b"not the reviewed license")
    else:
        contract = package / CONTRACT_RELATIVE
        target = package / "replacement-contract.json"
        target.write_bytes(contract.read_bytes())
        contract.unlink()
        try:
            contract.symlink_to(os.path.relpath(target, contract.parent))
        except OSError:
            pytest.skip("symlinks are unavailable")

    with pytest.raises(SileroVadUnavailable):
        create_silero_vad(
            package_root=package,
            runtime_loader=lambda: RecordingRuntime(),
        )


@pytest.mark.parametrize("failure", ("import", "provider", "session"))
def test_runtime_failures_are_normalized_without_retaining_private_details(
    tmp_path: Path,
    failure: str,
) -> None:
    package = tmp_path / "package"
    _write_package(package)

    def load_runtime() -> object:
        if failure == "import":
            raise ImportError("/Users/operator/private/onnxruntime")
        if failure == "provider":
            return RecordingRuntime(providers=("CoreMLExecutionProvider",))
        return RecordingRuntime(failure=RuntimeError("/Users/operator/private/model.onnx"))

    with pytest.raises(
        SileroVadUnavailable,
        match=r"^Silero VAD is unavailable; reinstall the application$",
    ) as captured:
        create_silero_vad(package_root=package, runtime_loader=load_runtime)

    assert captured.value.__context__ is None


def test_factory_creates_one_cpu_session_and_inference_keeps_state_and_context(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    _write_package(package)
    runtime = RecordingRuntime()

    vad = create_silero_vad(
        package_root=package,
        runtime_loader=lambda: runtime,
    )

    first = tuple(float(index) / 512 for index in range(512))
    second = tuple(-float(index) / 512 for index in range(512))
    assert vad.probability(first, sample_rate_hz=16_000) == pytest.approx(0.75)
    assert vad.probability(second, sample_rate_hz=16_000) == pytest.approx(0.75)

    assert isinstance(vad, SileroVad)
    assert len(runtime.session_calls) == 1
    model, providers, options = runtime.session_calls[0]
    assert model == FIXTURE_MODEL
    assert providers == ("CPUExecutionProvider",)
    assert isinstance(options, SimpleNamespace)
    assert options.inter_op_num_threads == 1
    assert options.intra_op_num_threads == 1
    session = runtime.session
    assert isinstance(session, RecordingSession)
    assert len(session.calls) == 2
    first_names, first_inputs = session.calls[0]
    second_names, second_inputs = session.calls[1]
    assert first_names == ["output", "stateN"]
    assert first_inputs["input"].shape == (1, 576)
    assert first_inputs["input"].dtype == np.float32
    assert first_inputs["sr"].shape == ()
    assert first_inputs["sr"].dtype == np.int64
    assert int(first_inputs["sr"]) == 16_000
    assert np.array_equal(first_inputs["input"][0, :64], np.zeros(64, dtype=np.float32))
    assert first_inputs["state"].shape == (2, 1, 128)
    assert second_names == ["output", "stateN"]
    assert np.array_equal(
        second_inputs["input"][0, :64],
        np.asarray(first[-64:], dtype=np.float32),
    )
    assert np.array_equal(
        second_inputs["state"],
        np.ones((2, 1, 128), dtype=np.float32),
    )


def test_factory_never_reopens_the_model_after_digest_verification(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    _write_package(package)
    model_path = package / MODEL_RELATIVE

    class ReplacingRuntime(RecordingRuntime):
        def InferenceSession(
            self,
            model: bytes,
            *,
            providers: list[str],
            sess_options: object,
        ) -> object:
            model_path.write_bytes(b"unverified replacement")
            assert model == FIXTURE_MODEL
            return super().InferenceSession(
                model,
                providers=providers,
                sess_options=sess_options,
            )

    runtime = ReplacingRuntime()

    vad = create_silero_vad(
        package_root=package,
        runtime_loader=lambda: runtime,
    )

    assert isinstance(vad, SileroVad)
    assert runtime.session_calls[0][0] == FIXTURE_MODEL


@pytest.mark.parametrize(
    "missing",
    [
        MODEL_RELATIVE,
        LICENSE_RELATIVE,
        "onnxruntime/LICENSE",
        "onnxruntime/capi/onnxruntime_pybind11_state.so",
        "onnxruntime/capi/libonnxruntime.1.23.2.dylib",
    ],
)
def test_candidate_gate_rejects_each_missing_shipped_vad_runtime_part(
    tmp_path: Path,
    missing: str,
) -> None:
    bundle = _write_candidate(tmp_path)
    (bundle / "_internal" / missing).unlink()

    with pytest.raises(SileroVadUnavailable):
        audit_packaged_silero_vad_runtime(bundle)


def test_candidate_gate_accepts_the_complete_digest_locked_runtime(
    tmp_path: Path,
) -> None:
    bundle = _write_candidate(tmp_path)

    audit_packaged_silero_vad_runtime(bundle)


def test_candidate_gate_accepts_the_locked_windows_wheel_license(
    tmp_path: Path,
) -> None:
    bundle = _write_candidate(tmp_path)
    (bundle / "_internal/onnxruntime/LICENSE").write_bytes(WINDOWS_ONNXRUNTIME_LICENSE)

    audit_packaged_silero_vad_runtime(bundle)


@pytest.mark.parametrize(
    ("samples", "sample_rate_hz"),
    [
        (None, 16_000),
        ([0.0] * 512, 16_000),
        ((0.0,) * 511, 16_000),
        ((0.0,) * 513, 16_000),
        ((0,) * 512, 16_000),
        (((0.0,) * 511) + (float("nan"),), 16_000),
        (((0.0,) * 511) + (float("inf"),), 16_000),
        (((0.0,) * 511) + (1.01,), 16_000),
        ((0.0,) * 512, 8_000),
        ((0.0,) * 512, True),
    ],
)
def test_invalid_pcm_is_rejected_before_session_inference(
    tmp_path: Path,
    samples: object,
    sample_rate_hz: object,
) -> None:
    package = tmp_path / "package"
    _write_package(package)
    runtime = RecordingRuntime()
    vad = create_silero_vad(package_root=package, runtime_loader=lambda: runtime)

    with pytest.raises(SileroVadUnavailable):
        vad.probability(samples, sample_rate_hz=sample_rate_hz)

    session = runtime.session
    assert isinstance(session, RecordingSession)
    assert session.calls == []


@pytest.mark.parametrize(
    "outputs",
    [
        [],
        [np.array([[0.5]], dtype=np.float32)],
        [
            np.array([[float("nan")]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ],
        [
            np.array([[1.1]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ],
        [
            np.array([[0.5]], dtype=np.float32),
            np.zeros((1, 1, 128), dtype=np.float32),
        ],
    ],
)
def test_invalid_session_output_is_never_returned(
    tmp_path: Path,
    outputs: list[np.ndarray[tuple[int, ...], np.dtype[np.float32]]],
) -> None:
    package = tmp_path / "package"
    _write_package(package)

    class InvalidSession:
        def run(self, output_names: object, inputs: object) -> object:
            del output_names, inputs
            return outputs

    vad = create_silero_vad(
        package_root=package,
        runtime_loader=lambda: RecordingRuntime(session=InvalidSession()),
    )

    with pytest.raises(SileroVadUnavailable):
        vad.probability((0.0,) * 512, sample_rate_hz=16_000)
