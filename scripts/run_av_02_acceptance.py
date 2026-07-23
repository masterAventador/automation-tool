#!/usr/bin/env python3
"""Verify AV-02 third-party source and rights governance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCKS = REPOSITORY_ROOT / "contracts/quality/third-party-sources.v1.json"
ASSET_RIGHTS = REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"
SOURCE_SBOM = REPOSITORY_ROOT / "third_party/source-submodules.cdx.json"
EXPECTED_SOURCES = {
    "moneyprinterturbo": {
        "path": "vendor/moneyprinterturbo",
        "url": "https://github.com/harry0703/MoneyPrinterTurbo.git",
        "tag": "v1.3.2",
        "commit": "b1588e1fdc6c5e54358f66ca2ff323e1dddf1364",
        "license": "MIT",
    },
    "hyperframes": {
        "path": "vendor/hyperframes",
        "url": "https://github.com/heygen-com/hyperframes.git",
        "tag": "v0.7.68",
        "commit": "71d84ff27f1c2b2828f4fdf9015c3da4157140ee",
        "license": "Apache-2.0",
    },
}


def load_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise AssertionError(f"缺少治理文件：{path.relative_to(REPOSITORY_ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str, cwd: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_submodules(source_locks: dict[str, object]) -> None:
    sources = source_locks.get("sources")
    if not isinstance(sources, list):
        raise AssertionError("third-party source locks 缺少 sources")
    indexed = {
        source.get("id"): source for source in sources if isinstance(source, dict)
    }
    if set(indexed) != set(EXPECTED_SOURCES):
        raise AssertionError("third-party source locks 必须且只能登记两个上游源码")

    for source_id, expected in EXPECTED_SOURCES.items():
        source = indexed[source_id]
        for field in ("path", "url", "tag", "commit"):
            if source.get(field) != expected[field]:
                raise AssertionError(f"{source_id} 的 {field} 未精确锁定")
        license_record = source.get("license")
        if not isinstance(license_record, dict):
            raise AssertionError(f"{source_id} 缺少许可证记录")
        if license_record.get("spdx") != expected["license"]:
            raise AssertionError(f"{source_id} SPDX 许可证不匹配")
        if not license_record.get("sha256"):
            raise AssertionError(f"{source_id} 缺少许可证摘要")

        path = REPOSITORY_ROOT / expected["path"]
        if not (path / ".git").exists():
            raise AssertionError(f"{source_id} submodule 未初始化")
        if git("rev-parse", "HEAD", cwd=path) != expected["commit"]:
            raise AssertionError(f"{source_id} submodule commit 漂移")
        if git("status", "--porcelain", cwd=path):
            raise AssertionError(f"{source_id} submodule 工作树不干净")
        if git("remote", "get-url", "origin", cwd=path) != expected["url"]:
            raise AssertionError(f"{source_id} submodule origin 漂移")


def require_asset_rights_policy() -> None:
    policy = load_json(ASSET_RIGHTS)
    if policy.get("defaultDecision") != "deny":
        raise AssertionError("未登记资产必须默认拒绝进入发行包")
    categories = policy.get("requiredCategories")
    required = {"font", "stock_media", "music_sfx", "codec_binary", "map_3d", "generated"}
    if not isinstance(categories, dict) or set(categories) != required:
        raise AssertionError("资产权利策略分类不完整")
    for category, fields in categories.items():
        if not isinstance(fields, list) or not {"source", "license", "sha256"}.issubset(fields):
            raise AssertionError(f"{category} 权利记录字段不完整")


def require_sbom() -> None:
    sbom = load_json(SOURCE_SBOM)
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        raise AssertionError("源码 SBOM 不是 CycloneDX 1.6")
    components = sbom.get("components")
    if not isinstance(components, list) or len(components) != 2:
        raise AssertionError("源码 SBOM 必须登记两个 submodule")
    versions = {
        component.get("name"): component.get("version")
        for component in components
        if isinstance(component, dict)
    }
    if versions != {"MoneyPrinterTurbo": "v1.3.2", "Hyperframes": "v0.7.68"}:
        raise AssertionError("源码 SBOM 版本与锁定版本不一致")


def require_documentation() -> None:
    governance = REPOSITORY_ROOT / "docs/third-party-source-governance.md"
    evidence = REPOSITORY_ROOT / "docs/development/AV-02.md"
    checker = REPOSITORY_ROOT / "scripts/check_third_party_sources.py"
    for path in (governance, evidence, checker):
        if not path.is_file():
            raise AssertionError(f"缺少 AV-02 文件：{path.relative_to(REPOSITORY_ROOT)}")
    governance_text = governance.read_text(encoding="utf-8")
    for phrase in (
        "上游源码只读",
        "独立升级任务",
        "安全公告",
        "字体、素材与编解码器",
        "SBOM",
        "正常用户路径验收",
    ):
        if phrase not in governance_text:
            raise AssertionError(f"第三方治理文档缺少：{phrase}")
    evidence_text = evidence.read_text(encoding="utf-8")
    for phrase in (
        "# AV-02 完成证据",
        "状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
    ):
        if phrase not in evidence_text:
            raise AssertionError(f"AV-02 独立证据缺少：{phrase}")


def require_roadmap() -> None:
    roadmap = (
        REPOSITORY_ROOT / "docs/embedded-browser-video-studio-roadmap.md"
    ).read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| AV-02 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise AssertionError("专项 Roadmap 中 AV-02 必须唯一且为已完成")


def main() -> None:
    if not (REPOSITORY_ROOT / ".gitmodules").is_file():
        raise AssertionError("AV-02 缺少 .gitmodules")
    require_submodules(load_json(SOURCE_LOCKS))
    require_asset_rights_policy()
    require_sbom()
    require_documentation()
    require_roadmap()
    subprocess.run(
        ["python3", "scripts/check_third_party_sources.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    print("AV-02 third-party source governance acceptance passed")


if __name__ == "__main__":
    main()
