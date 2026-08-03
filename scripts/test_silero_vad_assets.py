from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import silero_vad_assets  # noqa: E402

MODEL = b"locked-model"
LICENSE = (
    b"MIT License\n\nCopyright (c) 2020-present Silero Team\n\n"
    b"Permission is hereby granted, free of charge, to any person obtaining a copy\n"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _contract(path: Path) -> tuple[dict[str, object], Path]:
    document: dict[str, object] = {
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
            "bytes": len(MODEL),
            "sha256": _sha256(MODEL),
            "packagedPath": "speech/silero-vad/silero_vad_16k_op15.onnx",
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
            "bytes": len(LICENSE),
            "sha256": _sha256(LICENSE),
            "packagedPath": "speech/silero-vad/SILERO-VAD-LICENSE.txt",
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
    path.write_text(json.dumps(document), encoding="utf-8")
    return document, path


def test_assets_are_fetched_once_verified_and_cached_by_the_locked_contract(
    tmp_path: Path,
) -> None:
    document, contract_path = _contract(tmp_path / "silero-vad-runtime.v1.json")
    fetched: list[str] = []
    model = document["model"]
    license_record = document["license"]
    assert isinstance(model, dict)
    assert isinstance(license_record, dict)
    payloads = {
        model["sourceUrl"]: MODEL,
        license_record["sourceUrl"]: LICENSE,
    }

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return payloads[url]

    first = silero_vad_assets.ensure_silero_vad_assets(
        root=tmp_path / "cache",
        contract_path=contract_path,
        fetch=fetch,
    )
    second = silero_vad_assets.ensure_silero_vad_assets(
        root=tmp_path / "cache",
        contract_path=contract_path,
        fetch=lambda url: pytest.fail(f"cache unexpectedly fetched {url}"),
    )

    assert first == second
    assert (first / "silero_vad_16k_op15.onnx").read_bytes() == MODEL
    assert (first / "SILERO-VAD-LICENSE.txt").read_bytes() == LICENSE
    assert fetched == [model["sourceUrl"], license_record["sourceUrl"]]


@pytest.mark.parametrize(
    ("target", "payload"),
    [
        ("model", MODEL + b"tampered"),
        ("license", LICENSE + b"tampered"),
        ("license", b"not a license"),
    ],
)
def test_rejected_download_never_leaves_a_reusable_partial_cache(
    tmp_path: Path,
    target: str,
    payload: bytes,
) -> None:
    document, contract_path = _contract(tmp_path / "silero-vad-runtime.v1.json")
    model = document["model"]
    license_record = document["license"]
    assert isinstance(model, dict)
    assert isinstance(license_record, dict)
    payloads = {
        model["sourceUrl"]: payload if target == "model" else MODEL,
        license_record["sourceUrl"]: payload if target == "license" else LICENSE,
    }
    cache = tmp_path / "cache"

    with pytest.raises(silero_vad_assets.SileroVadAssetUnavailable):
        silero_vad_assets.ensure_silero_vad_assets(
            root=cache,
            contract_path=contract_path,
            fetch=lambda url: payloads[url],
        )

    assert not (cache / "silero-vad").exists()
    assert not (cache / "silero-vad.stamp.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model.packagedPath", "../outside/model.onnx"),
        ("license.packagedPath", "/absolute/LICENSE"),
        ("model.sourceUrl", "https://attacker.invalid/model.onnx"),
        ("upstream.tag", "master"),
        ("runtime.provider", "CoreMLExecutionProvider"),
    ],
)
def test_contract_cannot_redirect_download_packaging_or_provider(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    document, contract_path = _contract(tmp_path / "silero-vad-runtime.v1.json")
    section, name = field.split(".")
    record = document[section]
    assert isinstance(record, dict)
    record[name] = value
    contract_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(silero_vad_assets.SileroVadAssetContractRejected):
        silero_vad_assets.load_silero_vad_contract(contract_path)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
