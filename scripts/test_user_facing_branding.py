#!/usr/bin/env python3
"""CQ-01 plain-language gate tests for ``check_user_facing_branding.py``.

The real CQ-01 delivery evidence is the production Tauri App user path
(``scripts/run_cq_01_acceptance.py``). This file only proves the static
regression gate itself: the committed repository passes, and every declared
comprehension rule fails closed when the contract or the UI copy is tampered
with.

Scenarios span missing or malformed declarations, enforced terms without Chinese
wording in ``plainLanguageMappings``, bare industry terms in the three copy
carriers, plural forms, copy containing parentheses, literals following a JSX
self-closing tag, accessibility names, lost concept distinctions and card
labels, an English-only motion part explanation, a vendored upstream whose name
nobody added to the forbidden list, and captured real App pages that carry a
bare term or nothing at all.

The scenario count is printed by the run rather than written here. It used to be
a hand-maintained ``34``, which stayed ``34`` whether 34 scenarios ran or 3 did
— a number that cannot be wrong cannot report a gap either.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_user_facing_branding.py"
CONTRACT = ROOT / "contracts/quality/user-facing-terminology.v1.json"

TAURI_CONFIGURATION = json.dumps({"app": {"windows": [{"title": "自动化运营工具"}]}})
PARTS_PROJECTION = {
    "counts": {"total": 1},
    "categories": ["文字效果"],
    "items": [
        {
            "id": "caption-highlight",
            "displayTitle": "高亮标记字幕",
            "typeLabel": "局部组件",
            "category": "文字效果",
            "performanceLabel": "轻量",
            "deviceRequirementLabel": "任意设备",
            "applicabilityLabel": "标题与文字动画",
            "provenanceLabel": "无需调整",
        }
    ],
}


def run_check(root: Path | None = None, contract: Path | None = None):
    arguments = [sys.executable, str(CHECK)]
    if root is not None:
        arguments.extend(["--root", str(root)])
    if contract is not None:
        arguments.extend(["--contract", str(contract)])
    return subprocess.run(
        arguments, capture_output=True, text=True, timeout=300, cwd=ROOT
    )


# The embedded WebUI is upstream code, so it names the upstream project in its
# own sources on purpose and cannot be scanned like ours. What the gate pins is
# the size and shape of that exposure surface, so an upstream upgrade that adds
# a new occurrence has to be re-checked against the window guard by a human.
EMBEDDED_WEBUI_ROOT = "vendor/webui"
EMBEDDED_WEBUI_OK = '<span class="mpt-brand__name">MoneyPrinterTurbo</span>\n'
EMBEDDED_WEBUI_DIGEST = (
    "f1e555f0127df185360cbff5732dedb76a4a32039fabf87722d37a96e733136d"
)


def base_contract() -> dict:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["staticScan"]["roots"] = ["frontend/index.html", "frontend/src"]
    contract["embeddedWebUiScan"] = {
        "roots": [EMBEDDED_WEBUI_ROOT],
        "excludedGlobs": [],
        "textExtensions": [".py"],
        "maximumFileBytes": 1048576,
        "exposureSha256": EMBEDDED_WEBUI_DIGEST,
        "exposureCount": 1,
    }
    # The synthetic tree only contains the frontend files each case needs. The
    # native scan was added later and points at real source roots that this
    # tree does not have, so it has to be narrowed here too — otherwise every
    # case fails on a missing directory before it can exercise anything.
    contract["nativeScan"]["roots"] = []
    # 合成树里没有 docs/，这一项由各自需要它的场景显式声明（见 documentationScan
    # 三条场景）。留着真实契约的四份文档路径，每个场景都会挂在「文件不存在」上。
    contract.pop("documentationScan", None)
    contract["staticScan"]["excludedGlobs"] = ["**/*.test.*", "**/*.spec.*"]
    contract["conceptDistinctions"] = [
        {
            "id": "video_creation_methods",
            "displayName": "两种视频制作方式",
            "sourceFile": "frontend/src/Studio.tsx",
            "requiredCopy": ["智能素材成片", "品牌动效成片"],
            "requiredCopyFrom": "videoCreationMethodCardLabels",
        }
    ]
    contract["partsCatalogProjection"] = "contracts/video/motion-catalog-ui.v1.json"
    contract["videoCreationMethodCardLabels"] = ["最适合", "不适合"]
    return contract


THIRD_PARTY_SOURCES_OK = {
    "schemaVersion": 1,
    "sources": [
        {"id": "moneyprinterturbo", "url": "https://example.invalid/MoneyPrinterTurbo.git"},
        {"id": "hyperframes", "url": "https://example.invalid/hyperframes.git"},
    ],
}


def materialize(
    directory: Path,
    contract: dict,
    sources: dict[str, str],
    embedded: str = EMBEDDED_WEBUI_OK,
    third_party_sources: dict | None = None,
    native_sources: dict[str, str] | None = None,
    documentation: dict[str, str] | None = None,
) -> Path:
    third_party_sources = (
        THIRD_PARTY_SOURCES_OK if third_party_sources is None else third_party_sources
    )
    (directory / EMBEDDED_WEBUI_ROOT).mkdir(parents=True, exist_ok=True)
    (directory / EMBEDDED_WEBUI_ROOT / "Main.py").write_text(embedded, encoding="utf-8")
    (directory / "frontend/src").mkdir(parents=True, exist_ok=True)
    (directory / "frontend/src-tauri").mkdir(parents=True, exist_ok=True)
    (directory / "contracts/video").mkdir(parents=True, exist_ok=True)
    (directory / "frontend/index.html").write_text(
        "<html><body>自动化运营工具</body></html>", encoding="utf-8"
    )
    (directory / "frontend/src-tauri/tauri.conf.json").write_text(
        TAURI_CONFIGURATION, encoding="utf-8"
    )
    (directory / "contracts/video/motion-catalog-ui.v1.json").write_text(
        json.dumps(PARTS_PROJECTION, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "contracts/quality").mkdir(parents=True, exist_ok=True)
    (directory / "contracts/quality/third-party-sources.v1.json").write_text(
        json.dumps(third_party_sources, ensure_ascii=False), encoding="utf-8"
    )
    for name, text in sources.items():
        path = directory / "frontend/src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name, text in (native_sources or {}).items():
        path = directory / "backend/src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name, text in (documentation or {}).items():
        path = directory / "docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    contract_path = directory / "terminology.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    return contract_path


STUDIO_OK = (
    'export const methods = ["智能素材成片", "品牌动效成片"];\n'
    'export const labels = ["最适合", "不适合"];\n'
    "export function Studio() {\n"
    "  return <p>选择制作方式后开始智能素材成片或品牌动效成片。</p>;\n"
    "}\n"
)


# 场景计数由 `expect` 自己累加。原先末尾印的是写死的 34：新增三个场景后它照样印 34，
# 也就是这个数字既不能证明跑了多少，也不会因为漏跑而变——一个不会错的计数等于没有计数。
EXECUTED: list[str] = []


def expect(
    name: str,
    contract: dict,
    sources: dict[str, str],
    *,
    passes: bool,
    embedded: str = EMBEDDED_WEBUI_OK,
    third_party_sources: dict | None = None,
    native_sources: dict[str, str] | None = None,
    documentation: dict[str, str] | None = None,
) -> None:
    EXECUTED.append(name)
    with tempfile.TemporaryDirectory(prefix="automation-tool-cq01-test-") as temporary:
        directory = Path(temporary)
        contract_path = materialize(
            directory,
            contract,
            sources,
            embedded,
            third_party_sources,
            native_sources,
            documentation,
        )
        result = run_check(root=directory, contract=contract_path)
        if passes:
            assert result.returncode == 0, f"{name}: expected pass, got {result.stderr}"
            return
        assert result.returncode != 0, f"{name}: tampered input must fail"
        assert "user-facing branding check failed" in result.stderr, (
            f"{name}: {result.stderr}"
        )


def main() -> int:
    assert CHECK.is_file(), "scripts/check_user_facing_branding.py is missing"

    # The committed repository must pass its own gate.
    committed = run_check()
    assert committed.returncode == 0, f"committed tree must pass: {committed.stderr}"

    # The scanner self-test stays available.
    self_test = subprocess.run(
        [sys.executable, str(CHECK), "--self-test"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert self_test.returncode == 0, f"self-test must pass: {self_test.stderr}"

    expect("clean synthetic tree", base_contract(), {"Studio.tsx": STUDIO_OK}, passes=True)

    # A new vendored upstream whose name nobody added to the forbidden list.
    # Without this rule the run is green — not because the name is absent from
    # the UI, but because the scanner never looked for it. That is the whole
    # point: an unasked question and a negative answer look identical.
    added = copy.deepcopy(THIRD_PARTY_SOURCES_OK)
    added["sources"].append(
        {"id": "swiftcaption", "url": "https://example.invalid/SwiftCaption.git"}
    )
    expect(
        "new upstream missing from the term list",
        base_contract(),
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        third_party_sources=added,
    )

    # The repository name is checked as well as the id: a source declared as
    # `id: "sc"` pointing at `SwiftCaption.git` still puts that word on screen.
    renamed = copy.deepcopy(THIRD_PARTY_SOURCES_OK)
    renamed["sources"][0] = {
        "id": "moneyprinterturbo",
        "url": "https://example.invalid/SomeOtherName.git",
    }
    expect(
        "upstream repository name missing from the term list",
        base_contract(),
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        third_party_sources=renamed,
    )

    expect(
        "third-party source authority absent",
        base_contract(),
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        third_party_sources={"schemaVersion": 1, "sources": []},
    )

    # 用户文档里写「点某某按钮」，那个按钮必须真的存在。2026-08-04 实测：四份文档里
    # 52 条引号文案有 6 条在产品里根本没有——卸载说明第 1 步的菜单名和按钮名都不存在，
    # 用户照着做第一步就卡住。这类错误不是数字抄错，抽查常量永远碰不到；而 docs/ 当时
    # 不在任何扫描范围内，所以没有任何东西守着它。
    documented = base_contract()
    documented["documentationScan"] = {
        "roots": ["docs/user-help.md"],
        "searchRoots": ["frontend/src"],
        "excludedGlobs": ["**/*.test.*"],
        "systemUiLabels": ["应用程序"],
    }
    expect(
        "documented control that exists in the product",
        documented,
        {"Studio.tsx": STUDIO_OK},
        passes=True,
        documentation={"user-help.md": "选好之后点「智能素材成片」就行。\n"},
    )
    expect(
        "documented control the product does not have",
        documented,
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        documentation={"user-help.md": "选好之后点「打开完整制作界面」就行。\n"},
    )
    # 系统界面词（访达的「应用程序」、活动监视器等）不是本产品的控件，按白名单放行；
    # 白名单是逐条声明的，不是「找不到就放过」。
    expect(
        "system UI label on the declared allowlist",
        documented,
        {"Studio.tsx": STUDIO_OK},
        passes=True,
        documentation={"user-help.md": "把它拖进「应用程序」文件夹。\n"},
    )

    # 中文 docstring 是写给维护者的内部技术说明，不是用户文案。把它当文案扫，会逼人
    # 不敢用中文写技术注释——一个门禁不该产生这种激励。`#` 注释本来就不受影响，只有
    # docstring 会，因为它形式上也是字符串字面量。
    native_contract = base_contract()
    native_contract["nativeScan"]["roots"] = ["backend/src"]
    native_contract["nativeScan"]["excludedGlobs"] = []
    expect(
        "Chinese docstring is internal documentation, not copy",
        native_contract,
        {"Studio.tsx": STUDIO_OK},
        passes=True,
        native_sources={
            "packaging.py": (
                "def remove_browser_installer_scripts() -> None:\n"
                '    """删除随包的 Chromium 安装脚本，避免运行期触发第二次下载。"""\n'
                "    return None\n"
            )
        },
    )

    # 但跳过 docstring 不能把扫描弄瞎：同一个词出现在真正会显示给用户的字面量里，
    # 仍然必须红。没有这一条，上面那条「通过」既可能是修对了，也可能是整个原生扫描
    # 被关掉了——两者输出一模一样。
    expect(
        "a real user-facing literal in the same file still fails",
        native_contract,
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        native_sources={
            "packaging.py": (
                "def notice() -> str:\n"
                '    """删除随包的 Chromium 安装脚本，避免运行期触发第二次下载。"""\n'
                '    return "Chromium 组件损坏，请重新安装"\n'
            )
        },
    )

    # 模块与类的 docstring 走的是同一条规则，一并钉住：只跳过「体内第一条语句是
    # 字符串」这一种位置，不是「文件里所有字符串」。
    expect(
        "module and class docstrings are skipped the same way",
        native_contract,
        {"Studio.tsx": STUDIO_OK},
        passes=True,
        native_sources={
            "packaging.py": (
                '"""本模块负责 Chromium 随包装配。"""\n'
                "\n"
                "\n"
                "class Packager:\n"
                '    """按 Profile 决定 Chromium 的落地位置。"""\n'
                "\n"
                "    value = 1\n"
            )
        },
    )

    # Contract declarations are mandatory and closed.
    contract = base_contract()
    del contract["unexplainedIndustryTerms"]
    expect("missing industry term list", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    contract["unexplainedIndustryTerms"][0]["explanationScope"] = "whatever"
    expect("unknown explanation scope", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    contract["unexplainedIndustryTerms"].append(
        {"term": "control plane", "explanationScope": "segment"}
    )
    contract["plainLanguageMappings"]["control plane"] = "控制服务"
    expect("duplicated term", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    del contract["videoCreationMethodCardLabels"]
    expect("missing card labels", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    del contract["partsCatalogProjection"]
    expect("missing parts projection path", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    contract["plainLanguageMappings"]["Executor"] = "Local Executor"
    expect("mapping wording is not Chinese", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    del contract["plainLanguageMappings"]["Executor"]
    expect("enforced term without wording", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    for entry in contract["unexplainedIndustryTerms"]:
        if entry["term"] == "API Key":
            del entry["reason"]
    expect("file scope without reason", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    # A card label that disappears from the shipped source must fail closed;
    # before requiredCopyFrom was honoured the label list was never compared.
    expect(
        "card label removed from the source",
        base_contract(),
        {"Studio.tsx": STUDIO_OK.replace('"不适合"', '"不太适合"')},
        passes=False,
    )

    contract = base_contract()
    contract["conceptDistinctions"] = []
    expect("empty concept distinctions", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    # The embedded upstream WebUI is a user-visible surface the frontend and
    # native scans cannot reach. Its brand exposure surface is pinned, so an
    # upstream upgrade that adds an occurrence has to be re-checked against the
    # window guard instead of reaching a customer unnoticed.
    expect(
        "embedded WebUI brand surface grew",
        base_contract(),
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        embedded=EMBEDDED_WEBUI_OK + 'st.caption("Powered by MoneyPrinterTurbo")\n',
    )

    expect(
        "embedded WebUI brand surface reworded",
        base_contract(),
        {"Studio.tsx": STUDIO_OK},
        passes=False,
        embedded='<span class="mpt-brand__name">Money Printer Turbo</span>\n',
    )

    contract = base_contract()
    del contract["embeddedWebUiScan"]
    expect("missing embedded WebUI scan", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    contract = base_contract()
    contract["embeddedWebUiScan"]["roots"] = []
    expect("embedded WebUI scan disabled", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    # A bare industry term inside rendered Chinese copy fails closed.
    expect(
        "bare term in a Chinese string literal",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Bad.tsx": 'const notice = "Executor 已经确认这次操作。";\n'},
        passes=False,
    )
    expect(
        "bare term in JSX text",
        base_contract(),
        {
            "Studio.tsx": STUDIO_OK,
            "Bad.tsx": "export const A = <span>Control Plane 不可用</span>;\n",
        },
        passes=False,
    )
    expect(
        "bare term in a template literal",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Bad.tsx": "const t = `等待 Executor 确认 ${id} 的结果`;\n"},
        passes=False,
    )
    expect(
        "English-only JSX text still counts as rendered copy",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Bad.tsx": "export const A = <span>No data Executor</span>;\n"},
        passes=False,
    )

    # The same term is accepted once the plain wording explains it in place.
    expect(
        "segment scope satisfied inside the same sentence",
        base_contract(),
        {
            "Studio.tsx": STUDIO_OK,
            "Good.tsx": 'const notice = "本机执行器 Executor 已经确认这次操作。";\n',
        },
        passes=True,
    )

    # File scope: the explanation may live anywhere in the same source file.
    file_scope_source = (
        'const hint = "请输入新的阿里百炼 API Key。";\n'
        'const help = "接口密钥只保存在本机受保护存储中。";\n'
    )
    expect(
        "file scope satisfied elsewhere in the file",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Settings.tsx": file_scope_source},
        passes=True,
    )
    expect(
        "file scope with no explanation anywhere",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Settings.tsx": 'const hint = "请输入新的阿里百炼 API Key。";\n'},
        passes=False,
    )

    # Regressions found in review: each one used to be silently skipped.
    expect(
        "plural form of a term",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Bad.tsx": 'const t = "剩余 Tokens 1234";\n'},
        passes=False,
    )
    expect(
        "copy carrying parentheses is still scanned",
        base_contract(),
        {
            "Studio.tsx": STUDIO_OK,
            "Bad.tsx": "export const A = <span>由 Executor 处理 (自动重试)</span>;\n",
        },
        passes=False,
    )
    expect(
        "literals after a JSX self-closing tag are still scanned",
        base_contract(),
        {
            "Studio.tsx": STUDIO_OK,
            # One line, exactly as JSX renders it: a stray regular expression
            # heuristic used to swallow every literal after the `/>`.
            "Bad.tsx": (
                'export const A = <Foo bar={1} /> {x ? "等待 Executor 确认" : "已完成"};\n'
            ),
        },
        passes=False,
    )
    expect(
        "accessibility names are scanned without Chinese characters",
        base_contract(),
        {
            "Studio.tsx": STUDIO_OK,
            "Bad.tsx": "export const A = <input aria-label={`${t} Executor`} />;\n",
        },
        passes=False,
    )

    # Identifiers are code, not rendered copy, and must not raise false alarms.
    identifier_source = (
        "const renderJobId = 1;\n"
        'const label = { providerLabel: "阿里云视频剪辑服务", templateName: "标准模板" };\n'
        'export const A = <div className="timeline-preset-asset">时间轴预览</div>;\n'
    )
    expect(
        "identifiers and class names are not user copy",
        base_contract(),
        {"Studio.tsx": STUDIO_OK, "Code.tsx": identifier_source},
        passes=True,
    )

    # Concept distinctions must stay in the shipped source.
    expect(
        "concept distinction copy removed",
        base_contract(),
        {"Studio.tsx": 'export const methods = ["智能素材成片"];\n'},
        passes=False,
    )
    contract = base_contract()
    contract["conceptDistinctions"][0]["sourceFile"] = "frontend/src/Missing.tsx"
    expect("concept distinction file missing", contract, {"Studio.tsx": STUDIO_OK}, passes=False)

    # Every motion part name must ship with a Chinese explanation beside it.
    with tempfile.TemporaryDirectory(prefix="automation-tool-cq01-test-") as temporary:
        directory = Path(temporary)
        contract_path = materialize(directory, base_contract(), {"Studio.tsx": STUDIO_OK})
        projection = copy.deepcopy(PARTS_PROJECTION)
        projection["items"][0]["applicabilityLabel"] = "caption animation"
        (directory / "contracts/video/motion-catalog-ui.v1.json").write_text(
            json.dumps(projection, ensure_ascii=False), encoding="utf-8"
        )
        result = run_check(root=directory, contract=contract_path)
        assert result.returncode != 0, "English-only part explanation must fail"
        assert "user-facing branding check failed" in result.stderr, result.stderr

    # The real App acceptance judges captured page text with the same matcher;
    # prove that judgement is not vacuous.
    from run_cq_01_acceptance import require_plain_captured_pages

    with tempfile.TemporaryDirectory(prefix="automation-tool-cq01-test-") as temporary:
        capture = Path(temporary) / "captured-pages.json"
        capture.write_text(
            json.dumps(
                [{"page": "设置与诊断", "text": "剩余 Tokens 1234", "accessibleNames": []}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            require_plain_captured_pages(capture)
        except RuntimeError as error:
            assert "Token" in str(error), f"unexpected capture failure: {error}"
        else:
            raise AssertionError("captured real App text with a bare term must fail")

        capture.write_text(
            json.dumps(
                [
                    {
                        "page": "设置与诊断",
                        "text": "阿里百炼 API Key",
                        "accessibleNames": ["接口密钥"],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        require_plain_captured_pages(capture)

        capture.write_text("[]", encoding="utf-8")
        try:
            require_plain_captured_pages(capture)
        except RuntimeError:
            pass
        else:
            raise AssertionError("an empty capture must not silently pass")

    print("CQ-01 user-facing plain-language gate tests passed")
    assert len(EXECUTED) == len(set(EXECUTED)), "场景名重复会让计数看起来对而实际漏跑"
    # 四项非 `expect` 的检查：仓库自身过闸、扫描器自检、内联零件投影、真实 App 抓取判定。
    print(f"executed checks: {len(EXECUTED) + 4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
