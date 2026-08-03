"""LE-14 T2: locked Silero ONNX assets and a CPU-only inference boundary."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
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


def test_the_build_cache_root_follows_each_platforms_own_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only one of these is reachable on any given host, so the platform is supplied.

    The function reads `sys.platform` and the environment and then does pure path
    work, so the path work is what gets asserted -- the same platform-value
    injection used for the Windows helpers elsewhere. Where each OS actually puts
    its caches is not this test's claim; that the four branches build the four
    documented paths is.
    """
    for name in ("AUTOMATION_TOOL_BUILD_CACHE", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    monkeypatch.setattr(sys, "platform", "darwin")
    assert silero_vad_module._build_cache_root() == home / "Library/Caches/automation-tool-build"

    monkeypatch.setattr(sys, "platform", "win32")
    assert silero_vad_module._build_cache_root() == home / "AppData/Local/automation-tool-build"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert silero_vad_module._build_cache_root() == tmp_path / "local/automation-tool-build"
    monkeypatch.delenv("LOCALAPPDATA")

    monkeypatch.setattr(sys, "platform", "linux")
    assert silero_vad_module._build_cache_root() == home / ".cache/automation-tool-build"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert silero_vad_module._build_cache_root() == tmp_path / "xdg/automation-tool-build"

    # The explicit override wins on every platform.
    monkeypatch.setenv("AUTOMATION_TOOL_BUILD_CACHE", str(tmp_path / "override"))
    assert silero_vad_module._build_cache_root() == tmp_path / "override"


def test_a_locked_file_record_that_is_not_the_declared_shape_is_refused() -> None:
    """These three numbers are what the digest check is run against."""
    from pathlib import PurePosixPath

    expected = PurePosixPath(MODEL_RELATIVE)
    complete: dict[str, object] = {
        "packagedPath": MODEL_RELATIVE,
        "bytes": 4,
        "sha256": "a" * 64,
    }

    cases: list[tuple[str, object]] = [
        ("not an object", [complete]),
        ("no packaged path", {**complete, "packagedPath": None}),
        ("a packaged path that is not the declared one", {**complete, "packagedPath": "other"}),
        ("a size that is not an int", {**complete, "bytes": "4"}),
        ("a size of zero", {**complete, "bytes": 0}),
        ("a negative size", {**complete, "bytes": -1}),
        ("a digest that is not text", {**complete, "sha256": None}),
        ("a digest of the wrong length", {**complete, "sha256": "a" * 63}),
        ("a digest that is not lowercase hex", {**complete, "sha256": "A" * 64}),
    ]
    for label, record in cases:
        with pytest.raises(SileroVadUnavailable):
            silero_vad_module._locked_file(record, expected_path=expected)
        assert label

    assert silero_vad_module._locked_file(complete, expected_path=expected).bytes == 4


def _contract_at(tmp_path: Path, document: object, name: str = "contract.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_runtime_contract_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    """It is what pins the model, the licences and the CPU-only policy."""
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    not_utf8 = tmp_path / "not-utf8.json"
    not_utf8.write_bytes(b"\xff\xfe{}")
    not_an_object = _contract_at(tmp_path, [1, 2], "list.json")

    for label, path in [
        ("no file at all", tmp_path / "absent.json"),
        ("a document that will not parse", malformed),
        ("bytes that are not utf-8", not_utf8),
        ("a document that is not an object", not_an_object),
    ]:
        with pytest.raises(SileroVadUnavailable):
            silero_vad_module._load_contract(path)
        assert label


def test_a_runtime_contract_that_drifted_from_what_it_declares_is_refused(
    tmp_path: Path,
) -> None:
    """Every field here is something a later step trusts without asking again."""

    def drifted(**changes: object) -> dict[str, object]:
        document = _contract()
        for dotted, value in changes.items():
            section, _, key = dotted.partition("__")
            if key:
                nested = document[section]
                assert isinstance(nested, dict)
                nested[key] = value
            else:
                document[section] = value
        return document

    cases: list[tuple[str, dict[str, object]]] = [
        ("another schema version", drifted(schemaVersion=2)),
        ("another contract id", drifted(id="something-else")),
        ("a policy that is not an object", drifted(policy="fail_closed")),
        ("downloads no longer forbidden", drifted(policy__runtimeDownloadAllowed=True)),
        ("a missing-asset behaviour that drifted", drifted(policy__missingAssetBehavior="warn")),
        ("upstream that is not an object", drifted(upstream="silero")),
        ("a runtime that is not an object", drifted(runtime="onnxruntime")),
        ("another runtime distribution", drifted(runtime__distribution="tflite")),
        ("another runtime version", drifted(runtime__version="1.0.0")),
        ("a provider that is not CPU", drifted(runtime__provider="CUDAExecutionProvider")),
        ("another runtime licence", drifted(runtime__licenseSpdx="Apache-2.0")),
        ("another packaged licence path", drifted(runtime__packagedLicensePath="LICENSE")),
        ("licence artifacts that are not an object", drifted(runtime__licenseArtifacts=[])),
        ("a licence artifact set that drifted", drifted(runtime__licenseArtifacts={"lf": {}})),
        ("a licence record that is not an object", drifted(license="MIT")),
        ("another licence spdx", drifted(license__spdx="Apache-2.0")),
        ("another licence source", drifted(license__sourceUrl="https://example.invalid/LICENSE")),
        ("another copyright holder", drifted(license__copyright="Copyright (c) someone else")),
    ]
    for label, document in cases:
        with pytest.raises(SileroVadUnavailable):
            silero_vad_module._load_contract(_contract_at(tmp_path, document))
        assert label

    assert silero_vad_module._load_contract(_contract_at(tmp_path, _contract())).model.bytes == len(
        FIXTURE_MODEL
    )


def test_a_licence_artifact_record_that_drifted_is_refused(tmp_path: Path) -> None:
    """Both spellings of the onnxruntime licence are pinned by size and digest."""
    for label, record in [
        ("not an object", "1073 bytes"),
        (
            "an extra key",
            {
                "bytes": 1_073,
                "sha256": "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
                "note": "x",
            },
        ),
        (
            "a size that drifted",
            {
                "bytes": 1_074,
                "sha256": "2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
            },
        ),
        ("a digest that drifted", {"bytes": 1_073, "sha256": "0" * 64}),
    ]:
        document = _contract()
        runtime = document["runtime"]
        assert isinstance(runtime, dict)
        artifacts = runtime["licenseArtifacts"]
        assert isinstance(artifacts, dict)
        artifacts["lf"] = record
        with pytest.raises(SileroVadUnavailable):
            silero_vad_module._load_contract(_contract_at(tmp_path, document))
        assert label


def test_a_locked_asset_that_moves_while_it_is_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opened, sized and hashed are three moments; a file may change between them.

    The window only exists inside the read loop, so the hook is on `os.read`
    itself -- there is no way into it from outside.
    """
    asset = tmp_path / "locked.bin"
    payload = b"x" * 64
    asset.write_bytes(payload)
    real_read = os.read

    def truncating(descriptor: int, size: int) -> bytes:
        return b""

    def growing(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        return chunk if chunk else b"extra"

    def replacing_with_a_link(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        asset.unlink()
        asset.symlink_to(tmp_path / "elsewhere.bin")
        return chunk

    for label, hook in [
        ("the file ran out early", truncating),
        ("the file grew past its declared size", growing),
        ("the name became a symlink mid-read", replacing_with_a_link),
    ]:
        asset.unlink(missing_ok=True)
        asset.write_bytes(payload)
        monkeypatch.setattr(os, "read", hook)
        assert silero_vad_module._read_stable_regular_file(asset, maximum_bytes=1_000) is None, (
            label
        )
        monkeypatch.undo()


def test_a_locked_asset_replaced_between_open_and_close_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same name, different file: the descriptor and the path must still agree."""
    asset = tmp_path / "locked.bin"
    asset.write_bytes(b"x" * 64)
    real_fstat = os.fstat
    calls = 0

    def drifting(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        metadata = real_fstat(descriptor)
        if calls == 1:
            return metadata
        fields = list(metadata)
        fields[stat.ST_INO] = metadata.st_ino + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", drifting)

    assert silero_vad_module._read_stable_regular_file(asset, maximum_bytes=1_000) is None


def test_the_runtime_is_resolved_from_whichever_layout_this_build_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three layouts, one of which is reachable at a time: onedir, frozen, checkout.

    A frozen build unpacks into `sys._MEIPASS`; a source checkout reads the
    contract from the repository and the model out of the build cache. Neither is
    reachable while running the suite from a checkout with no cache, so both are
    driven by supplying what the build would have set.
    """
    package = tmp_path / "package"
    package.mkdir()
    _write_package(package)

    # onedir: the caller names the root.
    payload, contract = silero_vad_module._resolved_assets(package)
    assert payload == FIXTURE_MODEL
    assert contract.model.bytes == len(FIXTURE_MODEL)

    # frozen: PyInstaller named it instead.
    monkeypatch.setattr(sys, "_MEIPASS", str(package), raising=False)
    frozen_payload, _contract = silero_vad_module._resolved_assets(None)
    assert frozen_payload == FIXTURE_MODEL
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    # checkout: the contract comes from the repository, the model from the cache.
    cache = tmp_path / "cache"
    (cache / "silero-vad").mkdir(parents=True)
    monkeypatch.setattr(silero_vad_module, "_build_cache_root", lambda: cache)
    monkeypatch.setattr(silero_vad_module, "_repository_root", lambda: package)
    with pytest.raises(SileroVadUnavailable):
        # The cache is empty, so this fails on the model rather than the contract.
        silero_vad_module._resolved_assets(None)


def test_the_packaged_runtime_audit_refuses_a_capi_that_is_not_a_directory(
    tmp_path: Path,
) -> None:
    """The CPU binding has to be a real directory of real files, not a link to one."""
    bundle = _write_candidate(tmp_path)
    capi = bundle / "_internal" / "onnxruntime" / "capi"
    for path in sorted(capi.iterdir()):
        path.unlink()
    capi.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    capi.symlink_to(elsewhere)

    with pytest.raises(SileroVadUnavailable):
        audit_packaged_silero_vad_runtime(bundle)


def test_the_packaged_runtime_audit_refuses_a_bundle_with_no_capi_at_all(
    tmp_path: Path,
) -> None:
    """An absent directory reads as no binding, which is a refusal, not a pass."""
    bundle = _write_candidate(tmp_path)
    capi = bundle / "_internal" / "onnxruntime" / "capi"
    for path in sorted(capi.iterdir()):
        path.unlink()
    capi.rmdir()

    with pytest.raises(SileroVadUnavailable):
        audit_packaged_silero_vad_runtime(bundle)


def test_the_vad_refuses_a_session_it_did_not_build(tmp_path: Path) -> None:
    """Only the locked CPU session may drive inference."""
    for value in (None, object(), "session"):
        with pytest.raises(SileroVadUnavailable):
            SileroVad(value)


def test_resetting_clears_both_the_state_and_the_context(tmp_path: Path) -> None:
    """A new material starts from silence, not from the tail of the previous one."""

    class _Session:
        def run(self, _names: object, _inputs: dict[str, object]) -> list[object]:
            return [np.asarray([[0.9]], dtype=np.float32), np.ones((2, 1, 128), np.float32)]

    session = _Session()
    monkey = SileroVad.__new__(SileroVad)
    object.__setattr__(monkey, "_session", session)
    monkey.reset()

    assert not monkey._state.any()
    assert not monkey._context.any()


def test_a_session_that_raises_is_refused_rather_than_read_around() -> None:
    """Whatever onnxruntime throws stays inside; the caller sees one closed reason."""

    class _Exploding:
        def run(self, _names: object, _inputs: dict[str, object]) -> list[object]:
            raise RuntimeError("onnxruntime defect")

    vad = SileroVad.__new__(SileroVad)
    object.__setattr__(vad, "_session", _Exploding())
    object.__setattr__(vad, "_state", np.zeros((2, 1, 128), dtype=np.float32))
    object.__setattr__(vad, "_context", np.zeros(64, dtype=np.float32))

    # A real chunk: a tuple of 512 finite floats in [-1, 1]. Passing anything
    # else would be refused by the argument check above and never reach the
    # session at all -- which is exactly what an earlier draft of this test did.
    chunk = tuple(0.0 for _ in range(512))

    with pytest.raises(SileroVadUnavailable):
        vad.probability(chunk, sample_rate_hz=16_000)


def test_the_runtime_import_is_the_one_module_the_contract_names() -> None:
    """Imported by name rather than at module load, so a broken install fails here."""
    assert silero_vad_module._import_onnxruntime() is onnxruntime
