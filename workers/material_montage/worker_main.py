#!/usr/bin/env python3
"""Minimal process boundary for the isolated material-video runtime."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import re
import sys
from typing import Final

PROTOCOL_VERSION: Final = 1
SAFE_MODULE_NAME: Final = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
RUNTIME_MODULES: Final[dict[str, str | None]] = {
    "moviepy": "moviepy",
    "streamlit": "streamlit",
    "streamlit-tour": None,
    "edge-tts": "edge_tts",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "openai": None,
    "faster-whisper": None,
    "dashscope": None,
    "azure-cognitiveservices-speech": None,
    "python-multipart": None,
    "pydub": "pydub",
    "litellm": None,
    "google-genai": None,
}


def runtime_probe() -> dict[str, object]:
    importlib.import_module("app")
    versions: dict[str, str] = {}
    for distribution, module in RUNTIME_MODULES.items():
        if module is not None:
            importlib.import_module(module)
        versions[distribution] = importlib.metadata.version(distribution)
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "status": "ready",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dependencies": versions,
        "capabilities": [
            "video_composition",
            "speech_synthesis",
            "subtitle_transcription",
            "web_ui",
        ],
    }


def dependency_probe(name: str) -> dict[str, object]:
    if name == "upstream-app":
        importlib.import_module("app")
        return {"dependency": name, "status": "ready"}
    module = RUNTIME_MODULES.get(name)
    if module is None:
        raise ValueError("dependency is not part of the startup set")
    importlib.import_module(module)
    return {"dependency": name, "status": "ready"}


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) == 2 and values[0] == "--probe-dependency":
        try:
            payload = dependency_probe(values[1])
        except ModuleNotFoundError as error:
            missing = error.name or "unknown"
            if SAFE_MODULE_NAME.fullmatch(missing) is None:
                missing = "unknown"
            print(
                json.dumps(
                    {"dependency": values[1], "missingModule": missing, "status": "unavailable"},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 70
        except Exception:
            print("Material video worker dependency is unavailable", file=sys.stderr)
            return 70
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    if values != ["--probe"]:
        print("Material video worker command is required", file=sys.stderr)
        return 64
    try:
        payload = runtime_probe()
    except Exception:
        print("Material video worker startup is unavailable", file=sys.stderr)
        return 70
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
