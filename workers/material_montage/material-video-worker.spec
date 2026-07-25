# -*- mode: python ; coding: utf-8 -*-

import json
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


worker_root = Path(SPECPATH)
repository_root = worker_root.parents[1]
upstream_root = repository_root / "vendor/moneyprinterturbo"
sys.path.insert(0, str(upstream_root))

contract = json.loads(
    (repository_root / "contracts/quality/material-video-worker-package.v1.json").read_text(
        encoding="utf-8"
    )
)
excluded_modules = list(contract["build"]["excludedModules"])

moviepy_datas, moviepy_binaries, moviepy_hiddenimports = collect_all("moviepy")
imageio_datas, imageio_binaries, imageio_hiddenimports = collect_all("imageio")
ffmpeg_datas, ffmpeg_binaries, ffmpeg_hiddenimports = collect_all("imageio_ffmpeg")
streamlit_datas, streamlit_binaries, streamlit_hiddenimports = collect_all("streamlit")
tour_datas, tour_binaries, tour_hiddenimports = collect_all("streamlit_tour")

runtime_distributions = [
    "moviepy",
    "streamlit",
    "streamlit-tour",
    "edge-tts",
    "fastapi",
    "uvicorn",
    "openai",
    "faster-whisper",
    "dashscope",
    "azure-cognitiveservices-speech",
    "python-multipart",
    "pydub",
    "litellm",
    "google-genai",
]
hiddenimports = [
    "app",
    "moviepy",
    "streamlit",
    "streamlit_tour",
    "edge_tts",
    "fastapi",
    "uvicorn",
    "openai",
    "faster_whisper",
    "dashscope",
    "azure.cognitiveservices.speech",
    "multipart",
    "pydub",
    "litellm",
    "google.genai",
    *moviepy_hiddenimports,
    *imageio_hiddenimports,
    *ffmpeg_hiddenimports,
    *streamlit_hiddenimports,
    *tour_hiddenimports,
    *collect_submodules("app"),
]
datas = [
    *moviepy_datas,
    *imageio_datas,
    *ffmpeg_datas,
    *streamlit_datas,
    *tour_datas,
]
for distribution in runtime_distributions:
    datas += copy_metadata(distribution)
datas += [
    (str(upstream_root / "resource"), "upstream/resource"),
    (str(upstream_root / "webui"), "upstream/webui"),
    (str(upstream_root / "config.example.toml"), "upstream"),
    (str(upstream_root / "LICENSE"), "upstream"),
]
for source in (upstream_root / "app").rglob("*"):
    if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
        destination = Path("upstream/app") / source.relative_to(upstream_root / "app").parent
        datas.append((str(source), str(destination)))

analysis = Analysis(
    [str(worker_root / "worker_main.py")],
    pathex=[str(worker_root), str(upstream_root)],
    binaries=[
        *moviepy_binaries,
        *imageio_binaries,
        *ffmpeg_binaries,
        *streamlit_binaries,
        *tour_binaries,
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="automation-tool-material-video-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="automation-tool-material-video-worker",
)
