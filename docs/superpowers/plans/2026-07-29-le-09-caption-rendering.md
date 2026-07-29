# LE-09 字幕渲染与 fallback 机制 实施计划

> 日期：2026-07-29
> 工作树：`/Users/aventador/sourceCode/automation-tool/wt/le-09-captions`，基点 `cf5ae10`
> 设计依据：`docs/superpowers/specs/2026-07-28-local-smart-edit-design.md` §6.0/§6、§5.2、§8「字体」行
> 台账：`docs/local-video-editing-roadmap.md` 第 60 行 LE-09
> 上游接口：`backend/src/automation_tool/control_plane/domain/editing_project.py`（LE-04）
> 状态：**已批准，按 T1～T5 逐个实施，每个 task 单独提交并送审**

---

## 0. 调研结论（`e53ef70` 实测，结论已被 `cf5ae10` 采纳进台账与设计文档）

### 0.1 PIL 能吃两种格式；但真正的答案是「生产里没有 woff2 中文字体」

**工具能力**（Pillow 12.3.0 / FreeType 2.14.3 实测）：

| 文件 | 魔数 | 真实格式 | `ImageFont.truetype` | `TTFont` |
| --- | --- | --- | --- | --- |
| `NotoSansCJKsc-Bold.ttf` | `OTTO` | OpenType/CFF | ✅ `('Noto Sans CJK SC','Bold')` | ✅ cmap 44810 条 |
| `big-shoulders-display-latin.woff2` | `wOF2` | WOFF2 | ✅ | ⚠️ **需 `brotli`** |

两条与直觉相反的事实：**PIL 直接吃 woff2**（FreeType 自带解压）；
**反而 fontTools 读 woff2 的 cmap 需要 `brotli`**。两边都要用，故 `brotli` 是硬依赖。

**生产现实**（设计 §6.0 已按此更正）：真正的中文字体是 **Noto Sans CJK SC，静态 OTF**，
锁在 `notofonts/noto-cjk` 的 `Sans2.004`，登记在 `contracts/quality/asset-rights-policy.v1.json`
（`defaultDecision: "deny"`），由 `scripts/subtitle_font_assets.py::ensure_subtitle_fonts()`
**构建期**获取到 `~/Library/Caches/automation-tool-build/subtitle-fonts/`（本机已存在，17.0/16.4 MB）。
仓库里没有任何字体二进制（`scripts/test_material_video_worker.py:338` 守着）。

`offline-motion-dependencies.v1.json` 是**另一条链路**：18 个家族、52 个 woff2、合计 1.0 MB、
`keptFontSubsets` 只有 latin/latin-ext、**零中文**。1.0 MB 里装不下任何中文字体。

**所以「PIL 不吃 woff2 该怎么办」这个分支不会发生**：中文字体是 OTF，PIL 原生支持。

### 0.2 fallback 链的第二个字体：只有一个合法选择

`defaultDecision: deny` ⇒ 没登记的不许进包。四条 font 登记里只有
`big-shoulders-display`（`big-shoulders-display-latin.woff2`, OFL-1.1）是在册拉丁字体；
`font-utm-kabel-kt` 是 NOASSERTION，vendor 里的 BeVietnamPro/Charm 未登记。
这就是 `brotli` 成为硬依赖的直接原因。

已知副作用：该 woff2 是可变字体，`wght` 默认 100，PIL 不设轴渲染 **Thin**。
实现里用 `set_variation_by_axes` 设到可读字重并写测试。

### 0.3 「断言 PNG 非空」零捕捉力（台账已按此更正）

```
Noto Sans CJK SC 渲染 U+1F600（不在 cmap）: nonzero_px=1226，与 .notdef 逐字节相同 ← 实心方框
BeVietnamPro     渲染 U+4E2D（不在 cmap）: nonzero_px=0                          ← 空白
```

豆腐块有墨、缺字无墨，非空断言两头都抓不住。
**正确判据是与 `.notdef` 差分**（`font.getmask(chr(0x10FFFF))`）。

### 0.4 端到端 probe 已跑通

链 `[big-shoulders, noto-sans-cjk-sc-bold]`、文本 `AB中文龘`：逐字解析正确，
PNG 10629 B / size=(700,140) / ink bbox=(8,29,300,116)；肉眼核对 `龘` 笔画完整，无豆腐块。

### 0.5 依赖

`backend/.venv` 当前没有 PIL / fontTools。需新增 `pillow`(14MB) / `fonttools`(18MB) / `brotli`(<1MB)。

**进 `[dependency-groups].executor`**（与 `playwright` 同组）：它们是 Local Executor 运行时依赖，
Control Plane 用不上。`excludes=[]` ⇒ import 图就是打包边界（CLAUDE.md §9.2），装错组的后果是实打实的。

**用 `uv` 不用 pip**：CI 跑 `uv sync --locked --dev` 与 `uv lock --check`（`.github/workflows/quality.yml:53-54`），
手工 pip 装会让 `uv lock --check` 红。流程：改 `pyproject.toml` → `uv lock` → `uv sync`。

**体积风险已登记到 LE-20**（`check_embedded_browser_package.py:106` 硬编码 `local-executor: 177 MiB`），
不属本任务范围，此处记一笔防止掉进缝里。

---

## 1. 交付物

```
backend/src/automation_tool/executor/captions/__init__.py     新增
backend/src/automation_tool/executor/captions/fonts.py        新增  注册表 + cmap fallback 机制
backend/src/automation_tool/executor/captions/render.py       新增  PIL 渲染：换行/描边/行距 → PNG
backend/tests/unit/executor/test_caption_fonts.py             新增
backend/tests/unit/executor/test_caption_render.py            新增
backend/tests/unit/executor/test_caption_acceptance.py        新增  真实字体真实产物
backend/pyproject.toml + backend/uv.lock                      改动  executor 组 +pillow +fonttools +brotli
docs/development/LE-09.md                                     新增  证据文件
docs/local-video-editing-roadmap.md                           改动  LE-09 行 → 🔍 待验收
```

**不动**：`vendor/`、`scripts/subtitle_font_assets.py`、`contracts/`、
`backend/automation-tool-executor.spec`（装配归 LE-20）、`executor/__init__.py`（§1.3）、`control_plane/`。

### 1.1 与上游 `CaptionStyle` 对齐（按 `editing_project.py` 原文）

原文比派工单凭记忆给的严格得多，**五处实质差异**必须跟上：

| # | 原文 | 影响 |
| --- | --- | --- |
| 1 | `font_px ∈ [12,200]`、`stroke_px ∈ [0,20]`、`line_spacing ∈ [1.0,3.0]` | 只写「正数」会接受上游拒绝的值 |
| 2 | `type(x) is not int` 而非 `isinstance` | **拒绝 `bool`**（`isinstance(True,int)` 为真） |
| 3 | `type(self.line_spacing) is not float` | **`line_spacing=1`（int）被拒**，必须 `1.0`。极易踩 |
| 4 | `^...\Z` + `fullmatch` | `$` 会放过 `"noto\n"`；`e53ef70` 就是专门改这个的 |
| 5 | 统一 `_reject()`，消息固定 | 上游有意不做逐字段消息 |

**镜像 `CaptionRenderStyle` 的三个决定**：同样的边界值；同样的严格类型判定（含拒 `bool`、
拒 int 型 `line_spacing`）；**不同的异常类型且保留逐字段消息**——上游那条面向 API 边界，
不泄漏字段合理；执行器这条面向内部调用方（LE-10），精确消息才能定位。

**为什么执行器要再校验**：CLAUDE.md §4.3 执行器是独立部署单元，数据经协议到达必须自校验；
且 LE-10 可能在本地组装样式而不经 Control Plane。

**防漂移（coordinator 明确要求的跨层测试）**：生产代码不 import Control Plane（§4.3 禁止），
但**测试可以**——测试期两个包同在一仓库，不构成生产依赖：

```python
def test_executor_bounds_match_the_control_plane_contract() -> None:
    from automation_tool.control_plane.domain import editing_project as upstream
    from automation_tool.executor.captions import render

    assert render.MIN_CAPTION_FONT_PX == upstream.MIN_CAPTION_FONT_PX
    assert render.MAX_CAPTION_FONT_PX == upstream.MAX_CAPTION_FONT_PX
    assert render.MAX_CAPTION_STROKE_PX == upstream.MAX_CAPTION_STROKE_PX
    assert render.MIN_CAPTION_LINE_SPACING == upstream.MIN_CAPTION_LINE_SPACING
    assert render.MAX_CAPTION_LINE_SPACING == upstream.MAX_CAPTION_LINE_SPACING
    assert render.FONT_KEY_PATTERN.pattern == upstream._FONT_KEY_PATTERN.pattern
```

再加一条**跨层等价**用例：对每个边界端点与每种越界，断言两侧**接受/拒绝结论一致**
（只比结论不比异常类型，后者有意不同）。以及**字段名逐一对应**的断言。

### 1.2 `font_key` 绝不参与路径拼接

`editing_project.py:64` 的 docstring 把这写成了 `CaptionStyle` 的存在理由
（"the renderer turns it into a filename … a free string could walk out of the font directory"）——
**那个 renderer 就是本任务**。做法：正则 → 查闭集注册表 → 查不到抛错，
**任何情况下不用 `font_key` 拼文件名**；`packagedName` 取自 asset-rights 登记且断言不含路径分隔符。
用 monkeypatch 哨兵用例钉住「全程零文件系统访问」，而不是只断言抛了错。

### 1.3 不碰 `executor/__init__.py`

CLAUDE.md §9.2：`__init__.py` 重新导出什么，等于允许什么被顺带打包。
本任务模块按模块路径导入。（附带发现，非本任务引入也不修：§9.2 提到的
`test_shipped_package_boundary.py` 在本树不存在，而 `executor/__init__.py` 当前确实导出了
`FakeExecutorEngine` 等测试替身。如实登记。）

### 1.4 字体根目录：一段代码解析两种运行形态

复用 `executor/motion_authoring/agent.py:116 _resource_root()` 已被注释背书的形状：
冻结走 `sys._MEIPASS/fonts`，源码走构建缓存 `subtitle-fonts/`（因为仓库禁止字体二进制）。
**同一个函数、同一条规则**，不是「用环境变量决定产品去哪找东西」。
不 import `motion_authoring.agent`（该模块 import 时即读契约并可能抛错，耦合不划算）；
两处各 6 行、注释互指。若要消除重复需改别的任务的文件，**不主动动**。

### 1.5 缺字且整条链都没有 → fail closed（已获批准）

抛 `CaptionGlyphUnavailable`，带**码位**不带原文（§7 禁止原文进日志），不留半成品文件。
依据：FIX 文档已就同类问题决策过（「不回退系统字体、不跳过字幕」）；
§0.3 实测证明画替代符号后下游断言全部照常通过，正是 T108 事故形状。
代价（用户打 emoji 会整次失败）如实登记，缓解归 LE-20。

---

## 2. 范围：装配归 LE-20，LE-09 完成后最多 `🔍 待验收`

台账已写明。装配策略本身（进包 vs 设计 §6.3 的「按需下载」）是 LE-20 的决策，
LE-09 抢做会替它砍掉一半选项。本任务交付机制，不交付装配，并把缺口做成机器可见：
一条结构性用例断言冻结形态下 `font_root()` 落在 `<_MEIPASS>/fonts`，作为 LE-20 的落点契约。
`docs/development/LE-09.md` 声明 `用户可操作：否 / 证据类型：分层实现`，补验收依赖挂 LE-20。

---

## 3. 任务拆分

每个 task 独立提交，**做完立即返回**等两轮审查（spec 符合性 + `pr-review-toolkit` 代码质量）。
严格 RED → 实跑看红 → 最小实现 → 实跑看绿。

- **T1 依赖与骨架**：`pillow`/`fonttools`/`brotli` 进 executor 组（`uv lock` + `uv sync`），
  captions 包可导入。RED 是 `ModuleNotFoundError`。
- **T2 字体注册表与解析**：`FONT_KEY_PATTERN`、`REGISTERED_CAPTION_FONTS` 闭集、
  `font_root()`、`resolve_font_file()`；12 条用例含六个平台分支与哨兵用例。
- **T3 cmap 覆盖与 fallback 链**（核心）：`glyph_coverage()`（排除 `.notdef` 映射）、
  `FontChain.face_for()`、`segment_runs()`；12 条用例；对 `and`/`in`/`!=` 三处做算子变异自证。
- **T4 PIL 渲染**：`CaptionRenderStyle`（含跨层钉子）、`_load_face`（可变字体字重修正）、
  换行/行距/描边、RGBA 透明 PNG；19 条用例。
- **T5 真实验收**：`.notdef` 差分三层断言 + fail-closed 反向用例 + 冻结落点结构用例；
  写 `docs/development/LE-09.md`；台账 LE-09 → `🔍 待验收` 并跑
  `scripts/check_local_editing_roadmap_counts.py`。

（各 task 的完整代码与逐条用例表见本文件历史版本与实施时的提交；此处保留骨架以免与实现漂移。）

---

## 4. 失败矩阵覆盖（设计 §8「字体」行）

| 场景 | 行为 | 覆盖 |
| --- | --- | --- |
| 字体文件缺失 | `CaptionFontUnavailable` 点名 font_key | T2 |
| 字体加载失败（字节损坏） | `CaptionFontUnavailable` | T3 + T4 |
| 字形缺失但链里有 fallback | 降级到下一个字体 | T5 主用例 |
| **字形缺失且整条链都没有** | **fail closed，带码位不带原文，不留半成品** | T5 反向用例 |
| cmap 有码位但映到 `.notdef` | 视为未覆盖，继续降级 | T3 |
| `font_key` 试图逃出字体目录 | 正则拒绝，全程零文件系统访问 | T2 哨兵用例 |
| 空链 / 重复字体 | `CaptionFontRejected` | T3 |
| 样式越界（含 bool、int 型 line_spacing、描边吃字面） | `CaptionFontRejected` | T4 |
| woff2 缺 brotli | `CaptionFontUnavailable` | T3 反证依赖存在 |

---

## 5. 门禁（每个 task 提交前**前台**跑）

```bash
cd backend && .venv/bin/python -m pytest tests/unit/executor/test_caption_*.py -q
cd backend && .venv/bin/python -m pytest tests/unit/executor/test_caption_*.py \
    --cov=automation_tool.executor.captions --cov-report=term-missing --cov-fail-under=100
cd backend && .venv/bin/python -m ruff format --check <改动文件>
cd backend && .venv/bin/python -m ruff check <改动文件>
cd backend && .venv/bin/python -m mypy <改动文件>
cd backend && uv lock --check          # T1 起，防止锁文件漂移
```

覆盖率必须 `--cov=automation_tool.executor.captions` 限定到本包
（`source = ["automation_tool"]` 会把全库算进来）。目标：100% 语句 + 100% 分支、零 partial。

全库既有红（`ruff check .` 约 98 / `mypy` 17 / 全库覆盖率）不归本任务，
但每个 task 后核对**没有增加**。

**变异自证跑法**（`.pyc` 缓存坑）：

```bash
cd backend && find . -name __pycache__ -prune -exec rm -rf {} + && \
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest tests/unit/executor/test_caption_fonts.py -q
```

改运算符不改常量值；带一个必被杀的 canary 自检，canary 活下来则所有变异结论作废重跑。
