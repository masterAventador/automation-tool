#!/usr/bin/env python3
"""Verify AV-03 threat, terminology, and user-facing brand contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
THREAT_MODEL = (
    REPOSITORY_ROOT / "contracts/security/embedded-browser-video-threat-model.v1.json"
)
TERMINOLOGY = REPOSITORY_ROOT / "contracts/quality/user-facing-terminology.v1.json"


def load_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise AssertionError(f"缺少 AV-03 契约: {path.relative_to(REPOSITORY_ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"AV-03 契约必须是对象: {path.relative_to(REPOSITORY_ROOT)}")
    return value


def require_threat_model() -> None:
    model = load_object(THREAT_MODEL)
    if model.get("version") != "embedded-browser-video.threat-model.v1":
        raise AssertionError("威胁模型版本不正确")
    surfaces = model.get("surfaces")
    expected = {
        "loopback_worker",
        "generated_html",
        "asset_download",
        "model_and_provider_secrets",
        "local_paths",
        "upstream_name_leakage",
    }
    if not isinstance(surfaces, dict) or set(surfaces) != expected:
        raise AssertionError("威胁模型没有完整覆盖 AV-03 六类攻击面")
    for name, surface in surfaces.items():
        if not isinstance(surface, dict):
            raise AssertionError(f"{name} 攻击面必须是对象")
        threats = surface.get("threats")
        controls = surface.get("controls")
        verification = surface.get("verification")
        required_lists = (threats, controls, verification)
        if not all(isinstance(value, list) and value for value in required_lists):
            raise AssertionError(f"{name} 缺少威胁、控制或验证")
    if model.get("userFeatureAcceptance") != "formal_app_normal_user_path_required":
        raise AssertionError("威胁模型没有固定正常用户路径验收")


def require_terminology() -> None:
    contract = load_object(TERMINOLOGY)
    if contract.get("version") != "user-facing-terminology.v1":
        raise AssertionError("用户术语契约版本不正确")
    providers = contract.get("videoCreationMethods")
    if not isinstance(providers, list) or {
        item.get("displayName") for item in providers if isinstance(item, dict)
    } != {"智能素材成片", "品牌动效成片"}:
        raise AssertionError("两种视频制作方式中文名称不完整")
    forbidden = contract.get("forbiddenUserFacingTerms")
    if not isinstance(forbidden, list):
        raise AssertionError("缺少用户界面禁止词")
    normalized = {str(term).casefold() for term in forbidden}
    for required in ("moneyprinterturbo", "hyperframes", "b-roll", "poc"):
        if required not in normalized:
            raise AssertionError(f"用户界面禁止词缺少 {required}")
    mappings = contract.get("plainLanguageMappings")
    if not isinstance(mappings, dict):
        raise AssertionError("缺少通俗语言映射")
    expected_mappings = {
        "B-roll": "补充画面",
        "PoC": "前期验证",
        "Provider": "制作方式",
        "Worker": "本机视频服务",
        "Artifact": "素材或成片",
    }
    for source, expected in expected_mappings.items():
        if mappings.get(source) != expected:
            raise AssertionError(f"{source} 没有映射为 {expected}")


def require_documents() -> None:
    document = REPOSITORY_ROOT / "docs/embedded-browser-video-security-and-branding.md"
    evidence = REPOSITORY_ROOT / "docs/development/AV-03.md"
    scanner = REPOSITORY_ROOT / "scripts/check_user_facing_branding.py"
    for path in (document, evidence, scanner):
        if not path.is_file():
            raise AssertionError(f"缺少 AV-03 文件: {path.relative_to(REPOSITORY_ROOT)}")
    text = document.read_text(encoding="utf-8")
    for phrase in (
        "随机 loopback 端口",
        "生成 HTML",
        "素材下载",
        "密钥与模型数据边界",
        "路径不泄漏",
        "上游名称不进入用户界面",
        "正常用户路径验收",
    ):
        if phrase not in text:
            raise AssertionError(f"AV-03 文档缺少 {phrase}")
    evidence_text = evidence.read_text(encoding="utf-8")
    for phrase in (
        "# AV-03 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if phrase not in evidence_text:
            raise AssertionError(f"AV-03 独立证据缺少 {phrase}")


def require_roadmap() -> None:
    roadmap = (
        REPOSITORY_ROOT / "docs/embedded-browser-video-studio-roadmap.md"
    ).read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| AV-03 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 AV-03 必须唯一且为已完成")


def main() -> None:
    require_threat_model()
    require_terminology()
    require_documents()
    require_roadmap()
    subprocess.run(
        ["python3", "scripts/check_user_facing_branding.py", "--self-test"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    subprocess.run(
        ["python3", "scripts/check_user_facing_branding.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    print("AV-03 threat, terminology and branding acceptance passed")


if __name__ == "__main__":
    main()
