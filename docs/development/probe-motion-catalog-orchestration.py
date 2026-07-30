"""一次性探针：目录说明够不够模型做编排决策。

不渲染、不落盘、不进仓库。只回答一个问题：把 12 套 preset 与 134 个零件
交给模型，它能不能为一句话简报选出合法且合理的组合。
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path("/Users/aventador/sourceCode/automation-tool")

BRIEF = "介绍我们新出的智能咖啡机，主打一键萃取和自动清洗，卖点是省时间"


def load_materials() -> tuple[str, str, set[str], set[str]]:
    presets = json.loads((ROOT / "contracts/video/motion-style-presets.v1.json").read_text("utf-8"))
    catalog = json.loads((ROOT / "contracts/quality/motion-catalog.v1.json").read_text("utf-8"))

    preset_lines = [
        f"- {p['id']}｜{p['displayName']}：{p['summary']}" for p in presets["presets"]
    ]
    # 零件目录只有 name / title / category，没有用途说明 —— 这正是本次要测的
    by_category: dict[str, list[str]] = {}
    for item in catalog["items"]:
        by_category.setdefault(item["category"], []).append(f"{item['name']}（{item['title']}）")
    part_lines = [
        f"【{category}】" + "、".join(names) for category, names in sorted(by_category.items())
    ]
    return (
        "\n".join(preset_lines),
        "\n".join(part_lines),
        {p["id"] for p in presets["presets"]},
        {i["name"] for i in catalog["items"]},
    )


PROMPT = """你在为一条短视频做编排决策。只输出 JSON，不要任何解释文字。

可选风格模板（必须恰好选一个，用 id）：
{presets}

可选动效零件（按分类列出，格式为 零件id（英文标题）。必须只从中选择，不得编造）：
{parts}

用户简报：{brief}

输出这个结构：
{{
  "style_preset_id": "选中的模板 id",
  "reason": "为什么选它，一句话",
  "scenes": [
    {{"beat": 1, "purpose": "这一段要表达什么", "part": "零件id", "why": "为什么这个零件适合这一段"}}
  ]
}}

要求：分 4 到 6 段；每段的 part 必须是上面列出的零件 id 之一；不同段尽量不用重复零件。"""


def ask(model: str, prompt: str, api_key: str, base_url: str) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read())
    return payload["choices"][0]["message"]["content"]


def judge(raw: str, preset_ids: set[str], part_ids: set[str]) -> None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as error:
        print(f"  ❌ 输出不是合法 JSON：{error}")
        print(f"  原文前 300 字：{raw[:300]}")
        return

    preset = answer.get("style_preset_id")
    ok_preset = preset in preset_ids
    print(f"  风格模板：{preset}  {'✅ 合法' if ok_preset else '❌ 不在目录里（编造）'}")
    print(f"  理由：{answer.get('reason', '')}")

    scenes = answer.get("scenes", [])
    legal = sum(1 for s in scenes if s.get("part") in part_ids)
    invented = [s.get("part") for s in scenes if s.get("part") not in part_ids]
    unique = len({s.get("part") for s in scenes})
    print(f"  分镜数：{len(scenes)}　合法零件：{legal}/{len(scenes)}　去重后：{unique}")
    if invented:
        print(f"  ❌ 编造的零件 id：{invented}")
    for scene in scenes:
        mark = "✅" if scene.get("part") in part_ids else "❌"
        print(f"    {mark} 第{scene.get('beat')}段 [{scene.get('purpose')}] → {scene.get('part')}")
        print(f"        理由：{scene.get('why')}")


def main() -> None:
    credential = json.loads((ROOT / ".local/secrets/bailian-model.json").read_text("utf-8"))
    presets, parts, preset_ids, part_ids = load_materials()
    prompt = PROMPT.format(presets=presets, parts=parts, brief=BRIEF)
    print(f"简报：{BRIEF}")
    print(f"喂给模型：{len(preset_ids)} 套模板、{len(part_ids)} 个零件，prompt 共 {len(prompt)} 字\n")

    for model in sys.argv[1:] or ["qwen3.7-max-2026-06-08"]:
        print(f"===== {model} =====")
        try:
            raw = ask(model, prompt, credential["apiKey"], credential["openAiCompatibleBaseUrl"])
        except Exception as error:  # noqa: BLE001 - 探针，任何失败都要看见
            print(f"  调用失败：{type(error).__name__}: {error}")
            continue
        judge(raw, preset_ids, part_ids)
        print()


if __name__ == "__main__":
    main()
