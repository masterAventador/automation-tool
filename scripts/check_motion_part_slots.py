#!/usr/bin/env python3
"""Freeze and verify where a part's copy may be replaced.

A slot says: *the 13th visible text node of `lt-bold-block` currently reads
`Maya Chen`*. Copy the model writes replaces that node and nothing else. The
pair (index, original) is a double anchor — when the original no longer matches,
this process has misunderstood the document and must refuse rather than write
into whatever now sits at that index.

Why the anchor comes from the release tree and not `vendor/hyperframes`
-----------------------------------------------------------------------
Every part exists twice. The submodule holds upstream's original; the release
tree holds it after BM-12's offline rewrite and BM-13's trademark overlay, and
**the release tree is what ships and what gets copied into a RenderJob
workspace**. Substitution happens on that copy, so that is what a slot must
anchor to. Measured on `spotify-card`, the two differ in both halves of the
anchor at once:

    submodule    idx=24 'HyperFrames'   idx=26 'HeyGen'      idx=36 'Spotify'
    release      idx=22 '动效画布'       idx=24 '人物创作平台'  idx=34 '音频平台'

The text differs because the overlay replaced it; the index differs because the
offline rewrite deleted the Google Fonts `<link>` elements ahead of it. Anchoring
against the submodule would fail for all 70 parts the overlay touches, and it
would fail by writing copy into the wrong node — which nothing downstream can
see.

This is why the check needs a built release tree, the same way
`check_motion_catalog_release.py` does. That tree is deterministic: its aggregate
digest is locked in `contracts/video/motion-catalog-release.v1.json`.

Which nodes become slots
------------------------
Not all of them. The 36 first-batch parts hold 419 visible nodes; 48 of them
become slots. Two rounds of curation got there, and the second round is the
interesting one — the first pass excluded five parts by name and kept everything
else, which is not curation at all. Reading all 116 survivors then found four
classes that must not be rewritten:

* **License attributions.** `nyc-paris-flight` and `north-korea-locked-down`
  carry `© OpenStreetMap contributors © CARTO`. Letting a model rewrite that
  breaks the map data licence.
* **Self-promotional demo pages.** `vfx-magnetic` ("Pixels bend toward your
  cursor", "No html2canvas") and `vfx-liquid-glass` ("Ship videos 10x faster")
  document their own technique — the same category as the 30 transition demos
  the usability grading already deferred, missed on the first pass.
* **Interface furniture.** `liquid-glass-context-menu` is a macOS menu mock;
  `Follow`/`Following` and `Subscribe`/`Subscribed` are the two states a button
  animates between, so replacing them breaks what the shot is showing.
* **Structure a slot cannot express yet.** `news-ticker` splits one headline
  across three nodes for emphasis and mirrors four ticker lines, both of which
  need slot grouping that does not exist.

The judgement is named per part and per text rather than derived from a
node-count threshold, because a threshold is a proxy for the judgement instead
of the judgement. Every surviving entry is re-derived from the shipped tree by
this gate, so the curation cannot drift away from the documents.

`--write` regenerates the contract from the selection below.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend/src"))

from automation_tool.executor.motion_authoring.part_document import (  # noqa: E402
    visible_text_nodes,
)

CONTRACT_PATH: Final = REPOSITORY_ROOT / "contracts/video/motion-part-slots.v1.json"
USABILITY_PATH: Final = REPOSITORY_ROOT / "contracts/video/motion-part-usability.v1.json"
RELEASE_LOCK_PATH: Final = (
    REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
)

# Parts whose visible text is scenery rather than copy: mock application
# interfaces and full-page effects. They stay usable as atmosphere shots with
# their own text; they simply have nothing worth rewriting. Named rather than
# derived from a node-count threshold, because the threshold would be a proxy
# for the judgement instead of the judgement.
SCENERY_PARTS: Final[dict[str, str]] = {
    "app-showcase": "整屏应用界面仿制，文字是布景",
    "ui-3d-reveal": "整屏应用界面仿制，136 个节点全是界面家具",
    "vfx-portal": "整页特效演示，文字讲的是这个特效本身",
    "vfx-shatter": "整页碎裂特效演示，同上",
    "vpn-youtube-spot": "整屏应用界面仿制",
    "vfx-magnetic": "上游给这个着色器写的技术演示页——「Pixels bend toward your cursor」"
    "「No html2canvas」，和 30 个转场演示页同一类",
    "vfx-liquid-glass": "上游给自己做的宣传页——「Ship videos 10x faster」"
    "「HTML is the source of truth for video」",
    "liquid-glass-context-menu": "macOS 右键菜单仿制，Share / New Tab / ⌘T 是界面家具",
    "news-ticker": "本轮不做：一句标题被强调切成三个节点（27/28/29），"
    "跑马灯又把四条文案各镜像一份（40..44 与 50..54），"
    "两者都需要槽位分组，而分组还没有设计",
}

# Nodes inside kept parts that must not become slots. Keyed by the exact text as
# it appears in the shipped document, because that is what the anchor compares.
FIXED_TEXT: Final[dict[str, str]] = {
    "© OpenStreetMap contributors © CARTO": "地图数据的署名要求，替换它就是违反许可",
    "Follow": "关注按钮的初始态；动效正是从它变成 Following，替换会弄坏语义",
    "Following": "关注按钮的完成态，同上",
    "Subscribe": "订阅按钮的初始态，同上",
    "Subscribed": "订阅按钮的完成态，同上",
    "now": "通知时间戳，不是文案",
    "HF": "头像里的首字母缩写，由品牌资料决定而不是由这段文案决定",
    "⌘T": "键盘快捷键",
    "⇧⌘N": "键盘快捷键",
}


class SlotError(ValueError):
    """Raised when the slot table cannot be derived or has drifted."""


def fail(message: str) -> None:
    raise SlotError(message)


def load_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        fail(f"required regular file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"{path.name} is not valid JSON: {error}")
    if not isinstance(value, dict):
        fail(f"{path.name} must contain an object")
    return value


def release_item_root() -> Path:
    """Where the built, read-only parts live — the ones a workspace copies."""
    lock = load_json(RELEASE_LOCK_PATH)
    root = (
        REPOSITORY_ROOT
        / lock["layout"]["releaseRoot"]
        / lock["catalogVersion"]
        / lock["layout"]["itemRoot"]
    )
    if not root.is_dir():
        fail(
            f"the release tree is not built at {root}; run the release build first "
            "— a slot must anchor to the document that actually ships, not to the "
            "submodule original"
        )
    return root


def first_batch_parts() -> list[str]:
    usability = load_json(USABILITY_PATH)
    return [item["name"] for item in usability["items"] if item["batch"] == "first"]


def part_document(item_root: Path, name: str) -> Path:
    documents = sorted((item_root / name).glob("*.html"))
    if len(documents) != 1:
        fail(f"{name} does not have exactly one HTML document in the release tree")
    return documents[0]


def build_contract() -> dict[str, object]:
    item_root = release_item_root()
    parts: list[dict[str, object]] = []
    for name in sorted(first_batch_parts()):
        if name in SCENERY_PARTS:
            continue
        document = part_document(item_root, name)
        nodes = [
            node
            for node in visible_text_nodes(document.read_text(encoding="utf-8"))
            if node.text.strip() not in FIXED_TEXT
        ]
        if not nodes:
            continue
        parts.append(
            {
                "name": name,
                "documentPath": document.name,
                "slots": [
                    {
                        "index": node.index,
                        "original": node.text.strip(),
                        "parentTag": node.parent_tag,
                    }
                    for node in nodes
                    if node.text.strip() not in FIXED_TEXT
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "id": "motion-part-slots.v1",
        "policy": "fail_closed",
        "counts": {
            "parts": len(parts),
            "slots": sum(len(part["slots"]) for part in parts),  # type: ignore[arg-type]
        },
        "parts": parts,
    }


def verify(contract: dict) -> None:
    """Re-derive every anchor from the release tree and refuse any difference."""
    item_root = release_item_root()
    allowed = set(first_batch_parts())
    for part in contract.get("parts", []):
        name = part.get("name")
        if name not in allowed:
            fail(
                f"{name} is not in the first batch of the usability grading, so it "
                "may not carry a slot table"
            )
        if name in SCENERY_PARTS:
            fail(f"{name} is scenery rather than copy and may not carry slots")
        document = part_document(item_root, str(name))
        if document.name != part.get("documentPath"):
            fail(f"{name} document path drifted: {part.get('documentPath')}")
        nodes = {
            node.index: node
            for node in visible_text_nodes(document.read_text(encoding="utf-8"))
        }
        for slot in part.get("slots", []):
            node = nodes.get(slot.get("index"))
            if node is None:
                fail(
                    f"{name} slot {slot.get('index')} is not a visible text node of "
                    "the shipped document"
                )
            if node.text.strip() != slot.get("original"):
                fail(
                    f"{name} slot {slot.get('index')} anchors to "
                    f"{slot.get('original')!r} but the shipped document says "
                    f"{node.text.strip()!r}"
                )
            if node.text.strip() in FIXED_TEXT:
                fail(
                    f"{name} slot {slot.get('index')} is fixed text that may not be "
                    f"rewritten: {FIXED_TEXT[node.text.strip()]}"
                )
            if node.parent_tag != slot.get("parentTag"):
                fail(
                    f"{name} slot {slot.get('index')} parent tag drifted: "
                    f"{slot.get('parentTag')!r} != {node.parent_tag!r}"
                )
    counts = contract.get("counts")
    expected = {
        "parts": len(contract.get("parts", [])),
        "slots": sum(len(part.get("slots", [])) for part in contract.get("parts", [])),
    }
    if counts != expected:
        fail(f"counts drifted: {counts} != {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()

    try:
        if arguments.write:
            built = build_contract()
            arguments.contract.write_text(
                json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                f"motion part slots written: {arguments.contract} "
                f"({built['counts']})"  # type: ignore[index]
            )
            return 0
        contract = load_json(arguments.contract)
        verify(contract)
    except SlotError as error:
        print(f"part slots check failed: {error}", file=sys.stderr)
        return 1

    counts = contract["counts"]
    print(
        f"motion part slots are valid: {counts['parts']} parts / "
        f"{counts['slots']} slots, every anchor re-derived from the release tree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
