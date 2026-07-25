#!/usr/bin/env python3
"""BM-15 deterministic UI projection builder.

Composes ``contracts/video/motion-catalog-ui.v1.json`` from the locked BM-11
catalog and rights ledgers: 134 items with closed Chinese category/type/
performance/device/applicability/provenance labels and a fully localized
Chinese name per part. The projection is the only catalog payload the frontend
may import; upstream names, trademark indicator forms, domains and URLs must
never appear in it.

Part names are not derived from the upstream English titles. Sanitizing those
titles still left the operator reading English ("Clip Wipe", "Editorial
Emphasis") or a half-Chinese hybrid ("星云科技 Money Count"), so ``DISPLAY_TITLES``
below carries one explicit Chinese name per locked id. The name says what the
part does, so an operator who reads no English can pick one.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog.v1.json"
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
RELEASE_LOCK_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
PROJECTION_PATH = REPOSITORY_ROOT / "contracts/video/motion-catalog-ui.v1.json"

# A localized name may carry Chinese characters and Chinese punctuation only:
# a single ASCII letter anywhere means an upstream word survived.
ASCII_LETTER_PATTERN = re.compile(r"[A-Za-z]")

SCHEMA_VERSION = "1.0"
PROJECTION_ID = "automation-tool.motion-catalog-ui.v1"

TYPE_LABELS = {"block": "完整画面块", "component": "局部组件"}
PERFORMANCE_LABELS = {
    "dom_css": "轻量",
    "canvas_2d": "中等",
    "webgl": "较高",
    "webgpu": "高",
}
DEVICE_LABELS = {
    "dom_css": "任意设备",
    "canvas_2d": "普通显卡即可",
    "webgl": "需要支持 3D 加速的显卡",
    "webgpu": "需要较新显卡与系统",
}
PROVENANCE_LABELS = {
    "cleared": "无需调整",
    "needs_localization": "文字已本地化",
    "needs_asset_replacement": "示例素材已替换",
    "needs_localization_and_asset_replacement": "文字已本地化，示例素材已替换",
}
APPLICABILITY_LABELS = {
    "转场": "分镜之间的切换与节奏",
    "字幕": "台词与要点的同步展示",
    "代码演示": "代码讲解与技术教程",
    "画面内复杂效果": "画面强调与氛围效果",
    "人名与身份条": "人物介绍与身份说明",
    "数据与地图": "数据指标与地理信息",
    "社交平台展示": "社交内容与账号展示",
    "产品与案例展示": "产品功能与案例呈现",
    "文字效果": "标题与文字动画",
    "流程图": "流程与结构说明",
    "其他": "通用画面补充",
}

# One Chinese name per locked catalog id. Every entry is written by hand: the
# name describes the visible effect, not the upstream title it replaces.
DISPLAY_TITLES = {
    "app-showcase": "应用界面展示",
    "apple-money-count": "金额数字滚动",
    "blue-sweater-intro-video": "人物头像开场介绍",
    "caption-blend-difference": "反色叠加文字",
    "caption-clip-wipe": "字幕擦除显现",
    "caption-editorial-emphasis": "重点词强调字幕",
    "caption-emoji-pop": "表情弹出字幕",
    "caption-glitch-rgb": "彩色错位故障字幕",
    "caption-gradient-fill": "渐变填色字幕",
    "caption-highlight": "高亮标记字幕",
    "caption-kinetic-slam": "重砸落字字幕",
    "caption-matrix-decode": "乱码解码字幕",
    "caption-neon-accent": "霓虹点缀字幕",
    "caption-neon-glow": "霓虹发光字幕",
    "caption-parallax-layers": "分层视差字幕",
    "caption-particle-burst": "粒子爆开字幕",
    "caption-pill-karaoke": "胶囊逐字跟读字幕",
    "caption-texture": "纹理质感字幕",
    "caption-weight-shift": "字重变化字幕",
    "chromatic-radial-split": "彩边放射分裂转场",
    "cinematic-zoom": "电影感推近转场",
    "code-3d-extrude": "代码立体挤出",
    "code-diff": "代码改动对比",
    "code-highlight": "代码高亮扫过",
    "code-morph": "代码变形过渡",
    "code-particle-assemble": "代码粒子聚合",
    "code-scroll": "代码滚动定位",
    "code-shader-dissolve": "代码溶解消散",
    "code-snippet-apple-terminal-basic": "代码片段·终端基础配色",
    "code-snippet-apple-terminal-clear-dark": "代码片段·终端通透深色",
    "code-snippet-apple-terminal-clear-light": "代码片段·终端通透浅色",
    "code-snippet-apple-terminal-grass": "代码片段·终端草地绿",
    "code-snippet-apple-terminal-homebrew": "代码片段·终端墨绿荧屏",
    "code-snippet-apple-terminal-man-page": "代码片段·终端手册米白",
    "code-snippet-apple-terminal-novel": "代码片段·终端书页米黄",
    "code-snippet-apple-terminal-ocean": "代码片段·终端海洋蓝",
    "code-snippet-apple-terminal-pro": "代码片段·终端专业黑",
    "code-snippet-apple-terminal-red-sands": "代码片段·终端红沙棕",
    "code-snippet-apple-terminal-silver-aerogel": "代码片段·终端银灰",
    "code-snippet-apple-terminal-solid-colors": "代码片段·终端纯色",
    "code-snippet-dark-2026": "代码片段·年度深色",
    "code-snippet-dark-modern": "代码片段·现代深色",
    "code-snippet-dark-plus": "代码片段·增强深色",
    "code-snippet-flight": "代码片段飞行掠过",
    "code-snippet-high-contrast": "代码片段·高对比深色",
    "code-snippet-high-contrast-light": "代码片段·高对比浅色",
    "code-snippet-light-2026": "代码片段·年度浅色",
    "code-snippet-light-modern": "代码片段·现代浅色",
    "code-snippet-light-plus": "代码片段·增强浅色",
    "code-snippet-monokai": "代码片段·暗底亮彩",
    "code-snippet-solarized-light": "代码片段·米黄柔光",
    "code-snippet-visual-studio-dark": "代码片段·代码工作台深色",
    "code-snippet-visual-studio-light": "代码片段·代码工作台浅色",
    "code-typing": "代码逐字键入",
    "cross-warp-morph": "交叉扭曲变形转场",
    "data-chart": "数据图表动画",
    "domain-warp-dissolve": "流体扭曲溶解转场",
    "flash-through-white": "白闪切换转场",
    "flowchart": "横向流程图",
    "flowchart-vertical": "纵向流程图",
    "glitch": "故障闪切转场",
    "grain-overlay": "胶片颗粒叠加",
    "gravitational-lens": "引力透镜扭曲转场",
    "grid-pixelate-wipe": "格子马赛克擦除转场",
    "instagram-follow": "图片社区关注提示",
    "ios26-liquid-glass": "移动系统玻璃桌面",
    "light-leak": "漏光过曝转场",
    "liquid-glass-context-menu": "玻璃质感右键菜单",
    "liquid-glass-media-controls": "玻璃质感播放控件",
    "liquid-glass-notification": "玻璃质感通知条",
    "liquid-glass-widgets": "玻璃质感桌面小组件",
    "logo-outro": "品牌标识收尾",
    "lower-third-bild": "身份条·新闻简报风",
    "lt-accent-underline": "身份条·彩色下划线",
    "lt-bold-block": "身份条·粗体色块",
    "lt-clean-bar": "身份条·简洁横条",
    "lt-color-block": "身份条·撞色方块",
    "lt-dark-card": "身份条·深色卡片",
    "lt-kicker-name": "身份条·前缀引题",
    "lt-mask-reveal": "身份条·遮罩揭出",
    "lt-side-rule": "身份条·侧边竖线",
    "lt-soft-pill": "身份条·柔和胶囊",
    "lt-stack-bars": "身份条·堆叠条块",
    "macos-notification": "桌面系统通知弹窗",
    "macos-tahoe-liquid-glass": "桌面系统玻璃界面",
    "morph-text": "文字变形过渡",
    "motion-blur": "运动模糊拖影",
    "news-ticker": "滚动新闻条",
    "north-korea-locked-down": "封锁地区地图叙事",
    "nyc-paris-flight": "跨洋航线地图动画",
    "parallax-unzoom": "视差拉远转场",
    "parallax-zoom": "视差推近转场",
    "reddit-post": "兴趣社区帖子卡片",
    "ridged-burn": "焰边烧穿转场",
    "ripple-waves": "水波涟漪转场",
    "sdf-iris": "圆形光圈开合转场",
    "shimmer-sweep": "微光扫过文字",
    "spain-map": "西班牙地图",
    "spotify-card": "音频平台播放卡片",
    "swirl-vortex": "漩涡旋转转场",
    "texture-mask-text": "纹理蒙版文字",
    "thermal-distortion": "热浪扭曲转场",
    "tiktok-follow": "短视频平台关注提示",
    "transitions-3d": "立体空间转场组",
    "transitions-blur": "模糊虚化转场组",
    "transitions-cover": "覆盖遮挡转场组",
    "transitions-destruction": "碎裂破坏转场组",
    "transitions-dissolve": "渐隐溶解转场组",
    "transitions-distortion": "画面扭曲转场组",
    "transitions-grid": "网格分块转场组",
    "transitions-light": "光效闪耀转场组",
    "transitions-mechanical": "机械推拉转场组",
    "transitions-other": "综合杂项转场组",
    "transitions-push": "推移滑动转场组",
    "transitions-radial": "放射旋转转场组",
    "transitions-scale": "缩放伸缩转场组",
    "ui-3d-reveal": "界面立体揭示",
    "us-map": "美国地图",
    "us-map-bubble": "美国地图气泡图",
    "us-map-flow": "美国地图流向图",
    "us-map-hex": "美国地图蜂窝格",
    "vfx-iphone-device": "便携设备与工作站立体展示",
    "vfx-liquid-background": "流体渐变背景",
    "vfx-liquid-glass": "液态玻璃质感",
    "vfx-magnetic": "磁吸吸附动效",
    "vfx-portal": "传送门穿越",
    "vfx-shatter": "画面碎裂飞散",
    "vfx-text-cursor": "光标打字文字",
    "vignette": "暗角压边",
    "vpn-youtube-spot": "网络工具广告插播",
    "whip-pan": "快速甩镜转场",
    "world-map": "世界地图",
    "x-post": "社交动态帖子卡片",
    "yt-lower-third": "视频平台底部信息条",
}


class ProjectionError(RuntimeError):
    """Raised when the projection cannot be composed safely."""


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectionError(f"{path.name} must contain an object")
    return value


def _boundary_pattern(literal: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(literal) + r"(?![0-9A-Za-z])", re.IGNORECASE
    )


def _localized_title(name: str, forms: dict[str, list[str]], taken: dict[str, str]) -> str:
    """Return the hand-written Chinese name for a locked catalog id."""
    title = DISPLAY_TITLES.get(name)
    if title is None:
        raise ProjectionError(f"{name}: no localized Chinese name is declared")
    if ASCII_LETTER_PATTERN.search(title) is not None:
        raise ProjectionError(
            f"{name}: localized name still contains ASCII letters: {title!r}"
        )
    for literals in forms.values():
        for literal in literals:
            if _boundary_pattern(literal).search(title) is not None:
                raise ProjectionError(
                    f"{name}: localized name contains an indicator form: {title!r}"
                )
    owner = taken.get(title)
    if owner is not None:
        raise ProjectionError(
            f"{name}: localized name duplicates the one used by {owner}: {title!r}"
        )
    taken[title] = name
    return title


def compose_projection() -> dict:
    catalog = _load(CATALOG_PATH)
    rights = _load(RIGHTS_PATH)
    release_lock = _load(RELEASE_LOCK_PATH)

    forms = release_lock["trademarkScan"]["forms"]
    rights_by_name = {entry["name"]: entry for entry in rights["items"]}
    declared = set(DISPLAY_TITLES)
    locked = {entry["name"] for entry in catalog["items"]}
    if declared != locked:
        raise ProjectionError(
            "localized name table does not match the locked catalog ids: "
            f"missing {sorted(locked - declared)}, extra {sorted(declared - locked)}"
        )

    items = []
    taken: dict[str, str] = {}
    for entry in sorted(catalog["items"], key=lambda value: value["name"]):
        name = entry["name"]
        audit = rights_by_name.get(name)
        if audit is None:
            raise ProjectionError(f"rights ledger is missing item: {name}")
        category = entry["category"]
        if category not in APPLICABILITY_LABELS:
            raise ProjectionError(f"{name}: category outside the closed set")
        items.append(
            {
                "id": name,
                "displayTitle": _localized_title(name, forms, taken),
                "typeLabel": TYPE_LABELS[entry["type"]],
                "category": category,
                "performanceLabel": PERFORMANCE_LABELS[audit["gpuRequirement"]],
                "deviceRequirementLabel": DEVICE_LABELS[audit["gpuRequirement"]],
                "applicabilityLabel": APPLICABILITY_LABELS[category],
                "provenanceLabel": PROVENANCE_LABELS[audit["conclusion"]],
            }
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": PROJECTION_ID,
        "counts": catalog["counts"],
        "categories": catalog["categories"],
        "categoryCounts": catalog["categoryCounts"],
        "items": items,
    }


def serialize_projection(projection: dict) -> str:
    return json.dumps(projection, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECTION_PATH)
    arguments = parser.parse_args()
    projection = compose_projection()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with open(arguments.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(serialize_projection(projection))
    print(
        "motion catalog ui projection written:",
        f"{len(projection['items'])} items,",
        f"{len(projection['categories'])} categories",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
