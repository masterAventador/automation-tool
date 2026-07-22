# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

backend_root = Path(SPECPATH)
source_root = backend_root / "src"
sys.path.insert(0, str(source_root))
from automation_tool.executor.pyinstaller_support import materialize_internal_package_symlinks


def remove_direct_url_metadata(entries):
    """Drop editable-install provenance that embeds the developer checkout path."""

    return [
        entry
        for entry in entries
        if not (
            Path(entry[0]).name == "direct_url.json"
            and any(part.endswith(".dist-info") for part in Path(entry[0]).parts)
        )
    ]


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
playwright_hiddenimports.append("automation_tool.executor.browser_runtime")

analysis = Analysis(
    [str(source_root / "automation_tool/executor/__main__.py")],
    pathex=[str(source_root)],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
analysis.datas = remove_direct_url_metadata(analysis.datas)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="automation-tool-executor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name="automation-tool-executor",
)
materialize_internal_package_symlinks(Path(bundle.name))
