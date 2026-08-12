# -*- mode: python ; coding: utf-8 -*-

import json
import sys
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    copy_metadata,
)

backend_root = Path(SPECPATH)
source_root = backend_root / "src"
repository_root = backend_root.parent
scripts_root = repository_root / "scripts"
sys.path.insert(0, str(source_root))
sys.path.insert(0, str(scripts_root))
from automation_tool.executor.pyinstaller_support import (
    materialize_internal_package_symlinks,
    remove_browser_installer_scripts,
)
from silero_vad_assets import (
    CONTRACT_PATH as silero_vad_contract_path,
    ensure_silero_vad_assets,
    load_silero_vad_contract,
)


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
onnxruntime_datas = collect_data_files("onnxruntime", includes=["LICENSE"])
onnxruntime_binaries = collect_dynamic_libs("onnxruntime")
onnxruntime_hiddenimports = ["onnxruntime"]
onnxruntime_metadata = copy_metadata("onnxruntime")
from PyInstaller.utils.hooks import collect_submodules

# 控制面的 domain 包用字符串惰性导入子模块，静态图追不到，整包收集。
control_plane_hiddenimports = collect_submodules("automation_tool.control_plane")

executor_hiddenimports = [
    # 合并服务：控制面 HTTP 与执行器同进程。uvicorn 按字符串装配事件环与
    # 协议实现，静态图看不见，必须显式列根；控制面装配链同理由入口的
    # 函数内延迟导入触达。
    *control_plane_hiddenimports,
    "automation_tool.local_service",
    "automation_tool.control_plane.bootstrap.cli",
    "automation_tool.control_plane.bootstrap.app",
    "uvicorn",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.lifespan.on",
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "automation_tool.executor.authentication",
    "automation_tool.executor.bootstrap",
    "automation_tool.executor.command_processor",
    "automation_tool.executor.runtime",
    "automation_tool.executor.silero_vad",
    "automation_tool.executor.material_speech_pipeline",
    "automation_tool.executor.material_speech_transcription",
    # `motion_authoring.__init__` deliberately imports the heavyweight one-shot
    # entry lazily. PyInstaller cannot discover that string import from the
    # normal CLI graph, so the packaged App needs this explicit root; Analysis
    # then follows the entry's complete authoring/component dependency graph.
    "automation_tool.executor.motion_authoring.entry",
]
protocol_hiddenimports = [
    "automation_tool.protocol.action_authorization",
    "automation_tool.protocol.action_message_template",
    "automation_tool.protocol.action_result",
    "automation_tool.protocol.douyin_search",
    "automation_tool.protocol.executor_envelope",
    "automation_tool.protocol.version",
]

# LE-14's local speech gate may never download a model at runtime. Fetch and
# verify the one pinned model before Analysis, then package those exact bytes
# and the contract that the runtime and candidate audits re-check.
silero_vad_contract = load_silero_vad_contract()
silero_vad_cache = ensure_silero_vad_assets()
if silero_vad_contract.license.cached_name != "SILERO-VAD-LICENSE.txt":
    raise SystemExit("the Silero VAD packaged license name drifted")
silero_vad_contract_source = (
    repository_root / "contracts/quality/silero-vad-runtime.v1.json"
)
if silero_vad_contract_source != silero_vad_contract_path:
    raise SystemExit("the Silero VAD contract path drifted")
silero_vad_datas = [
    (
        str(silero_vad_cache / silero_vad_contract.model.cached_name),
        "speech/silero-vad",
    ),
    (
        str(silero_vad_cache / silero_vad_contract.license.cached_name),
        "speech/silero-vad",
    ),
    (str(silero_vad_contract_source), "contracts/quality"),
]

# 一句话动效编排在这个包里以一次性子进程运行，它启动时就要读这些只读数据。
# 缺一份代理就起不来，所以这里 fail closed：宁可构建失败，也不出一个装好了却
# 用不了一句话制作的包。路径保持与仓库一致，冻结与源码两种运行用同一段解析代码。
motion_authoring_resources = [
    "contracts/quality/motion-catalog.v1.json",
    "contracts/video/motion-part-usability.v1.json",
    # The twelve published styles and their one-line summaries: the agent
    # validates DESIGN against this list and hands the summaries to the model,
    # so a package without it cannot author at all.
    "contracts/video/motion-style-presets.v1.json",
    "contracts/video/motion-render-canvas.v1.json",
    "contracts/video/motion-one-sentence-brief.v1.json",
    "contracts/video/motion-authoring-model-call.v1.json",
    "contracts/video/motion-authoring-refusal.v1.json",
    "contracts/video/motion-storyboard-duration.v1.json",
    "contracts/video/motion-authoring-workflow.v1.json",
    # PC-13/PC-03: the renderer answers each part's typeface requests from
    # these two at render time, so they ship with the code that reads them.
    "contracts/video/motion-part-typography.v1.json",
    "contracts/video/offline-motion-dependencies.v1.json",
    # PC-03/PC-12/PC-17: where this film's copy goes in a part, and how much
    # room it has there.
    "contracts/video/motion-part-slots.v1.json",
    "contracts/video/motion-part-slot-budget.v1.json",
    # PC-26: the narrator's voice model id and audio hosts come from the same
    # catalog declaration the App reads.
    "contracts/video/bailian-model-catalog.v1.json",
    # SA-01: the AutomationSkill vocabulary. automation_skill.py resolves it
    # through the shared resource root, so the frozen build must carry it —
    # a checkout being green proves nothing about the package
    # (REVIEW-2026-08-06 SA#5).
    "contracts/browser-use/automation-skill.v1.json",
    "vendor/hyperframes/skills/hyperframes-core/references/minimal-composition.md",
    "vendor/hyperframes/skills/hyperframes-core/references/determinism-rules.md",
]
# The agent lists effective component durations before it receives the App's
# staged catalog root. Package only the 25 locked source documents that publish
# those capture windows; demo pages and the rest of registry stay out.
motion_catalog_document = json.loads(
    (repository_root / "contracts/quality/motion-catalog.v1.json").read_text(
        encoding="utf-8"
    )
)
component_source_resources = [
    f"vendor/hyperframes/{item['path']}/{item['name']}.html"
    for item in motion_catalog_document["items"]
    if item["type"] == "component"
]
if len(component_source_resources) != 25:
    raise SystemExit("the Executor package needs exactly 25 locked component sources")
motion_authoring_resources.extend(component_source_resources)
motion_authoring_datas = []
for relative in motion_authoring_resources:
    source = repository_root / relative
    if not source.is_file():
        raise SystemExit(
            f"the Executor package cannot be built without {relative}: the "
            "one-sentence authoring agent reads it at startup"
        )
    motion_authoring_datas.append((str(source), str(Path(relative).parent)))

# 控制面启动时读取的契约随包分发（冻结根目录下同相对路径）。
control_plane_datas = [
    (
        str(repository_root / "contracts/publishing/bilibili-open-api.v1.json"),
        "contracts/publishing",
    ),
]

analysis = Analysis(
    [str(source_root / "automation_tool/executor/__main__.py")],
    pathex=[str(source_root)],
    binaries=[*playwright_binaries, *onnxruntime_binaries],
    datas=[
        *playwright_datas,
        *onnxruntime_datas,
        *onnxruntime_metadata,
        *silero_vad_datas,
        *motion_authoring_datas,
        *control_plane_datas,
    ],
    hiddenimports=[
        *playwright_hiddenimports,
        *onnxruntime_hiddenimports,
        *executor_hiddenimports,
        *protocol_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
analysis.datas = remove_direct_url_metadata(analysis.datas)
# 上游 driver 自带的系统浏览器安装脚本不进正式包：产品只用包内 Chromium，
# 打包它们等于在用户机器上留一条绕开该约束的现成路径。
analysis.datas = remove_browser_installer_scripts(analysis.datas)
analysis.binaries = remove_browser_installer_scripts(analysis.binaries)
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
