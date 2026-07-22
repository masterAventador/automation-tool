#!/usr/bin/env python3
"""Build the locked intelligent-material source archive without touching upstream files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import NoReturn

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/moneyprinter-source-bundle.v1.json"


class SourceBundleError(RuntimeError):
    """The source checkout or generated archive violates the locked contract."""


def reject(message: str) -> NoReturn:
    raise SourceBundleError(f"智能素材成片源码包构建被拒绝：{message}")


def load_contract() -> dict[str, object]:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        reject(f"无法读取源码锁：{error}")
    if not isinstance(contract, dict) or contract.get("schemaVersion") != 1:
        reject("源码锁版本不受支持")
    policy = contract.get("policy")
    required_policy = {
        "recursiveInitializationRequired": True,
        "networkRequiredToBuild": False,
        "sourceCheckoutReadOnly": True,
        "allowPatches": False,
        "allowDirtyTree": False,
        "allowRuntimeSourceUpdate": False,
    }
    if not isinstance(policy, dict) or any(
        policy.get(key) is not expected for key, expected in required_policy.items()
    ):
        reject("只读、离线或补丁策略发生漂移")
    return contract


def git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        reject(result.stderr.strip() or "Git 检查失败")
    return result.stdout


def required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        reject(f"源码锁缺少 {field}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source(source: Path, contract: dict[str, object]) -> None:
    if not source.is_dir() or not (source / ".git").exists():
        reject("submodule 未递归初始化")
    if source.is_symlink():
        reject("源码目录不能是符号链接")

    expected_commit = required_string(contract.get("commit"), "commit")
    expected_tag = required_string(contract.get("tag"), "tag")
    expected_tree = required_string(contract.get("tree"), "tree")
    expected_origin = required_string(contract.get("origin"), "origin")
    facts = {
        "commit": str(git(source, "rev-parse", "HEAD")).strip(),
        "tag": str(git(source, "describe", "--tags", "--exact-match", "HEAD")).strip(),
        "tree": str(git(source, "rev-parse", "HEAD^{tree}")).strip(),
        "origin": str(git(source, "remote", "get-url", "origin")).strip(),
    }
    expected = {
        "commit": expected_commit,
        "tag": expected_tag,
        "tree": expected_tree,
        "origin": expected_origin,
    }
    for field, value in expected.items():
        if facts[field] != value:
            reject(f"{field} 与锁定版本不一致")

    status = str(
        git(
            source,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
    ).strip()
    if status:
        reject("上游工作树存在修改或未跟踪文件")
    nested = str(git(source, "submodule", "status", "--recursive")).splitlines()
    if any(not line.startswith(" ") for line in nested if line):
        reject("嵌套 submodule 未初始化或发生漂移")


def validate_archive(output: Path, contract: dict[str, object]) -> str:
    archive = contract.get("archive")
    if not isinstance(archive, dict):
        reject("源码锁缺少 archive")
    expected_sha = required_string(archive.get("sha256"), "archive.sha256")
    prefix = required_string(archive.get("prefix"), "archive.prefix")
    expected_count = archive.get("trackedFileCount")
    required_paths = archive.get("requiredPaths")
    if type(expected_count) is not int or expected_count < 1:
        reject("trackedFileCount 无效")
    if not isinstance(required_paths, list) or any(
        not isinstance(path, str) or not path for path in required_paths
    ):
        reject("requiredPaths 无效")

    actual_sha = sha256(output)
    if actual_sha != expected_sha:
        reject("离线源码包摘要与锁定值不一致")
    with tarfile.open(output, mode="r:") as bundle:
        members = bundle.getmembers()
        file_names: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            is_archive_root = member.isdir() and member.name == prefix.rstrip("/")
            if (
                path.is_absolute()
                or ".." in path.parts
                or (not is_archive_root and not member.name.startswith(prefix))
            ):
                reject("源码包包含越界路径")
            if member.issym() or member.islnk():
                reject("源码包包含链接文件")
            if member.isfile():
                file_names.add(member.name.removeprefix(prefix))
        if len(file_names) != expected_count:
            reject("源码包文件数量与锁定值不一致")
        if not set(required_paths).issubset(file_names):
            reject("源码包缺少启动或许可证文件")
    return actual_sha


def build_bundle(source: Path, output: Path) -> str:
    contract = load_contract()
    source = source.resolve(strict=True)
    output = output.resolve(strict=False)
    if output == source or source in output.parents:
        reject("输出不能写入上游源码目录")
    if output.exists():
        reject("输出文件已存在，拒绝覆盖")
    if not output.parent.is_dir():
        reject("输出目录不存在")
    validate_source(source, contract)
    commit = required_string(contract.get("commit"), "commit")
    archive = contract.get("archive")
    if not isinstance(archive, dict):
        reject("源码锁缺少 archive")
    prefix = required_string(archive.get("prefix"), "archive.prefix")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "archive",
            "--format=tar",
            f"--prefix={prefix}",
            commit,
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        reject(result.stderr.strip() or "Git archive 构建失败")
    try:
        return validate_archive(output, contract)
    except Exception:
        output.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=REPOSITORY_ROOT / "vendor/moneyprinterturbo",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    digest = build_bundle(arguments.source, arguments.output)
    print(f"智能素材成片只读源码包已构建：sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
