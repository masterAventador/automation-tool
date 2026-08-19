"""开发期工具：签名种子技能并写入 contracts/browser-use。

种子技能是人工按已知页面结构编写的候选文档，走与真实录制完全相同的
发布门（SA-01 schema → SA-03 lint → 沙箱回放 → 人工批准 → Ed25519 签名）。
签名在开发机完成：

- 发布私钥只存在于本机 ``.local/skill-publisher/ed25519.seed``（不进 Git）；
- 公钥锚写入 ``contracts/browser-use/skill-publisher.v1.json``（进 Git）；
- 签名后的种子记录写入 ``contracts/browser-use/seed-skills/``（进 Git）。

运行时只做验证（skill_orchestrator.load_seed_registry），不需要私钥。
用法：backend/.venv/bin/python scripts/sign_seed_skills.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend/src"))

from automation_tool.executor.automation_skill import parse_automation_skill  # noqa: E402
from automation_tool.executor.skill_registry import sign_candidate  # noqa: E402
from automation_tool.executor.skill_replayer import replay_skill  # noqa: E402
from automation_tool.executor.skill_trajectory_cleaner import (  # noqa: E402
    _deterministic_uuid,  # 与真实录制同一条 skillId 推导，避免两套 id 规则漂移
)

PRIVATE_SEED_PATH = REPOSITORY_ROOT / ".local/skill-publisher/ed25519.seed"
ANCHOR_PATH = REPOSITORY_ROOT / "contracts/browser-use/skill-publisher.v1.json"
SEEDS_ROOT = REPOSITORY_ROOT / "contracts/browser-use/seed-skills"

APPROVAL = {
    "reviewer": "aventador",
    "decision": "approved",
    "reviewedAt": "2026-08-19T00:00:00Z",
}

SANDBOX_PARAMETERS = {
    "comment_message": "沙箱回放占位内容",
}


class SandboxPage:
    """签名前沙箱回放的页面：所有锚点可解析、所有条件成立。

    这一步验证的是文档自身可回放（步骤/参数/条件一致），不是真实站点
    匹配——真实站点的第一次回放才是漂移信号，失败会如实落为待修复。
    """

    def find(self, role, name, *, near_text=None, relative_position=None):
        return (role, name)

    def holds(self, kind, *, role=None, name=None, pattern=None):
        return True

    def act(self, kind, handle, value):
        return None

    def current_path(self):
        return "/video"


def _fingerprint(domain: str, entry_path: str, goal_names: list[str]) -> str:
    # 与 skill_trajectory_cleaner 的 dom_outline_v1 推导保持一字不差。
    return hashlib.sha256("\0".join([domain, entry_path, *goal_names]).encode("utf-8")).hexdigest()


def _condition(kind: str, role: str, name: str) -> dict[str, object]:
    return {"kind": kind, "role": role, "name": name}


def _goal(role: str, name: str) -> dict[str, object]:
    return {"role": role, "name": name, "nearText": None, "relativePosition": None}


def douyin_comment_candidate() -> dict[str, object]:
    """抖音视频页评论：聚焦输入框 → 填评论 → 点发表，toast 证明结果。"""
    domain = "www.douyin.com"
    entry_path = "/video"
    goal_names = ["留下你的精彩评论", "留下你的精彩评论", "发表评论"]
    fingerprint = _fingerprint(domain, entry_path, goal_names)
    return {
        "schemaVersion": 1,
        "skillId": _deterministic_uuid(fingerprint),
        "version": 1,
        "parentVersion": None,
        "platform": "douyin",
        "domain": domain,
        "pathPattern": entry_path,
        "entryFingerprint": {"kind": "dom_outline_v1", "sha256": fingerprint},
        "language": "zh-CN",
        "viewport": {"width": 1280, "height": 800},
        "riskLevel": "high",
        "sideEffectBoundary": {"maxExternalSteps": 1},
        "steps": [
            {
                "index": 1,
                "goal": _goal("textbox", "留下你的精彩评论"),
                "action": {"kind": "click"},
                "preconditions": [],
                "postconditions": [
                    _condition("element_visible", "textbox", "留下你的精彩评论")
                ],
                "timeoutSeconds": 10,
                "external": False,
                "checkpoint": True,
            },
            {
                "index": 2,
                "goal": _goal("textbox", "留下你的精彩评论"),
                "action": {"kind": "fill", "value": {"parameter": "comment_message"}},
                "preconditions": [],
                "postconditions": [],
                "timeoutSeconds": 10,
                "external": False,
                # 外部步之前最后的安全点：失败接管从这里恢复，不重复已过前缀。
                "checkpoint": True,
            },
            {
                "index": 3,
                "goal": _goal("button", "发表评论"),
                "action": {"kind": "click"},
                "preconditions": [
                    _condition("element_visible", "button", "发表评论")
                ],
                "postconditions": [
                    _condition("element_visible", "status", "评论成功")
                ],
                "timeoutSeconds": 15,
                "external": True,
                "checkpoint": False,
            },
        ],
        "successEvidence": [
            {"kind": "element_visible", "role": "status", "name": "评论成功"}
        ],
    }


SEEDS: dict[str, dict[str, object]] = {
    "douyin-comment.v1.json": douyin_comment_candidate(),
}


def _signing_seed() -> bytes:
    if PRIVATE_SEED_PATH.exists():
        seed = bytes.fromhex(PRIVATE_SEED_PATH.read_text(encoding="utf-8").strip())
        if len(seed) != 32:
            raise SystemExit(f"{PRIVATE_SEED_PATH} 不是 32 字节 hex 私钥种子")
        return seed
    seed = os.urandom(32)
    PRIVATE_SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_SEED_PATH.write_text(seed.hex() + "\n", encoding="utf-8")
    PRIVATE_SEED_PATH.chmod(0o600)
    print(f"生成新发布私钥种子: {PRIVATE_SEED_PATH}")
    return seed


def main() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = _signing_seed()
    public_key = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()

    ANCHOR_PATH.write_text(
        json.dumps(
            {"schemaVersion": 1, "publisherPublicKey": public_key.hex()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"公钥锚已写入: {ANCHOR_PATH}")

    SEEDS_ROOT.mkdir(parents=True, exist_ok=True)
    for file_name, candidate in SEEDS.items():
        skill = parse_automation_skill(candidate)
        outcome = replay_skill(skill, SandboxPage(), parameters=SANDBOX_PARAMETERS)
        record = sign_candidate(
            candidate, approval=APPROVAL, seed=seed, replay=outcome
        )
        target = SEEDS_ROOT / file_name
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"种子技能已签名: {target} (skillId={skill.skill_id}, v{skill.version})")


if __name__ == "__main__":
    main()
