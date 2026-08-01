# -*- mode: python ; coding: utf-8 -*-

import json
import sys
import tomllib
from pathlib import Path, PurePosixPath

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


worker_root = Path(SPECPATH)
repository_root = worker_root.parents[1]
upstream_root = repository_root / "vendor/moneyprinterturbo"
backend_source_root = repository_root / "backend" / "src"
sys.path.insert(0, str(backend_source_root))
sys.path.insert(0, str(upstream_root))
sys.path.insert(0, str(repository_root / "scripts"))

from subtitle_font_assets import (  # noqa: E402
    PACKAGED_FONT_DIRECTORY,
    bundled_subtitle_fonts,
    ensure_subtitle_fonts,
    packaged_license_notice,
)
from silero_vad_assets import (  # noqa: E402
    ensure_silero_vad_assets,
    load_silero_vad_contract,
)

contract_path = repository_root / "contracts/quality/material-video-worker-package.v1.json"
contract = json.loads(contract_path.read_text(encoding="utf-8"))
backend_project = tomllib.loads(
    (repository_root / "backend/pyproject.toml").read_text(encoding="utf-8")
)
backend_version = backend_project["project"]["version"]
backend_metadata = Path(workpath) / f"automation_tool-{backend_version}.dist-info"
backend_metadata.mkdir(parents=True, exist_ok=False)
(backend_metadata / "METADATA").write_text(
    "Metadata-Version: 2.3\n"
    "Name: automation-tool\n"
    f"Version: {backend_version}\n",
    encoding="utf-8",
)
excluded_modules = list(contract["build"]["excludedModules"])
excluded_upstream_resources = set(contract["build"]["excludedUpstreamResources"])
# `excludedUpstreamResourceFiles` removes individual proprietary assets from a
# directory the release otherwise still ships, which `excludedUpstreamResources`
# cannot express: `resource/fonts` has to travel because the cleared faces live
# there, while the four Windows/macOS system faces and the rights-undetermined
# UTM Kabel KT face in the same directory carry no sufficient redistribution grant.
excluded_upstream_resource_files = {
    PurePosixPath(name) for name in contract["build"]["excludedUpstreamResourceFiles"]
}

moviepy_datas, moviepy_binaries, moviepy_hiddenimports = collect_all("moviepy")
imageio_datas, imageio_binaries, imageio_hiddenimports = collect_all("imageio")
ffmpeg_datas, ffmpeg_binaries, ffmpeg_hiddenimports = collect_all("imageio_ffmpeg")
streamlit_datas, streamlit_binaries, streamlit_hiddenimports = collect_all("streamlit")
tour_datas, tour_binaries, tour_hiddenimports = collect_all("streamlit_tour")
onnxruntime_datas = collect_data_files("onnxruntime", includes=["LICENSE"])
onnxruntime_binaries = collect_dynamic_libs("onnxruntime")

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
    "brotli",
    "fonttools",
    "numpy",
    "onnxruntime",
]
hiddenimports = [
    "automation_tool.executor.local_editing_worker",
    "automation_tool.executor.local_editing_worker_process",
    "automation_tool.executor.local_material_preview",
    "automation_tool.executor.smart_edit_worker_process",
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
    "onnxruntime",
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
    *onnxruntime_datas,
]
for distribution in runtime_distributions:
    datas += copy_metadata(distribution)
datas.append((str(backend_metadata), backend_metadata.name))
upstream_resource_root = upstream_root / "resource"
for entry in sorted(upstream_resource_root.rglob("*")):
    if not entry.is_file():
        continue
    relative = PurePosixPath(entry.relative_to(upstream_resource_root).as_posix())
    if relative.parts[0] in excluded_upstream_resources:
        continue
    if relative in excluded_upstream_resource_files:
        continue
    destination = PurePosixPath("upstream/resource")
    if str(relative.parent) != ".":
        destination = destination / relative.parent
    datas.append((str(entry), str(destination)))
# The open replacements for the removed system faces, plus the licence text the
# SIL Open Font License requires to travel with them. Neither is checked into
# the repository: both are fetched once against the digests locked in the asset
# rights register and cached outside the checkout, exactly like the embedded
# browser and the media toolchain. Every byte is verified before it lands here.
font_cache = ensure_subtitle_fonts()
for font in bundled_subtitle_fonts():
    datas.append((str(font_cache / font.packaged_name), PACKAGED_FONT_DIRECTORY))
font_license = packaged_license_notice()
datas.append((str(font_cache / font_license.packaged_name), PACKAGED_FONT_DIRECTORY))
silero_contract_path = repository_root / "contracts/quality/silero-vad-runtime.v1.json"
silero_contract = load_silero_vad_contract(silero_contract_path)
silero_cache = ensure_silero_vad_assets(contract_path=silero_contract_path)
bailian_catalog_path = (
    repository_root / "contracts/video/bailian-model-catalog.v1.json"
)
datas += [
    (str(upstream_root / "webui"), "upstream/webui"),
    (str(upstream_root / "config.example.toml"), "upstream"),
    (str(upstream_root / "LICENSE"), "upstream"),
    # The runtime reads `build.defaultSubtitleFontName` from here rather than
    # carrying a second copy of the font name.
    (str(contract_path), "contracts"),
    (str(bailian_catalog_path), "contracts/video"),
    (str(silero_contract_path), "contracts/quality"),
    (
        str(silero_cache / silero_contract.model.cached_name),
        "speech/silero-vad",
    ),
    (
        str(silero_cache / silero_contract.license.cached_name),
        "speech/silero-vad",
    ),
]
for source in (upstream_root / "app").rglob("*"):
    if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
        destination = Path("upstream/app") / source.relative_to(upstream_root / "app").parent
        datas.append((str(source), str(destination)))

analysis = Analysis(
    [str(worker_root / "worker_main.py")],
    pathex=[str(worker_root), str(upstream_root), str(backend_source_root)],
    binaries=[
        *moviepy_binaries,
        *imageio_binaries,
        *ffmpeg_binaries,
        *streamlit_binaries,
        *tour_binaries,
        *onnxruntime_binaries,
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
