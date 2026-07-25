# 抖音页面锚点可见性判定收敛（既有缺陷修复）

> 类型：既有缺陷修复，不占用专项 Roadmap 任务号。三个页面对象的锚点判定收敛到
> PB-05 建立的单点定义，并修掉过程中暴露的两层更深的缺陷。
>
> 日期：2026-07-25
>
> 提交：本文件所在提交

## 背景

`comment_page.py`、`profile_page.py`、`direct_message_page.py` 各自留着一份**私有**
`_unique_visible_locator`，实现完全相同，且与 PB-05 已收敛的 `page_anchors.py` 重复：

```python
locator = page.locator(", ".join(selectors))   # 不过滤可见性
count = locator.count()                         # 含隐藏节点
if count > 1: raise _AnchorConflict
if count == 0: return None
return locator.first if locator.first.is_visible() else None
```

## 缺陷分三层，只有第一层是动手前识别到的

### 第一层：隐藏占位使正常页面误报"锚点歧义"

单页应用会预渲染隐藏占位节点。由于判序是先 `count > 1`，"隐藏占位 + 可见真元素"必然
先抛冲突，三个页面的 `observe()` 都映射成 `CONFLICTING_ANCHORS → UNKNOWN → circuit_open`。

**危害定性更正**：动手前把它判成 fail-open（漏判后继续执行）是**不准确的**——它是
fail-**closed** 误报。真实危害是：登录失效弹窗、风控/阻断遮罩、私信不可用提示全被报成
"页面歧义"（转人工原因错误、排障误导），且正常页面被一个隐藏占位判成不可用。

真正的 fail-open 在第四个文件 `search_page.py::_visible_locator`（无 `count > 1` 兜底），
本次未处理，已登记为独立任务。

### 第二层：等待逻辑没跟着改，让已成功的动作被判成"结果不确定"

第一轮只改了 `observe()`，三处 `wait_for` 仍是
`page.locator(", ".join(selectors)).first.wait_for(state="visible")`。

`_can_wait` 只在"当前一个可见锚点都没有"时才允许等待，所以进入等待时 `.first` 必然解析到
隐藏占位；真实元素随后 append 成兄弟节点，`.first` 每次重解析仍是占位，**永远等不到
visible**，只能烧满超时再靠 `observe()` 兜底。

真实 Chromium 探针（隐藏占位在前，1 秒后 append 可见兄弟节点）：

```text
first.wait_for TIMED OUT after 4.00s
but visible matches now = 1
visible-filtered wait SUCCEEDED after 1.30s
```

后果不是漏判（`observe()` 是 fail-closed 的），而是：`comment_action` /
`direct_message_action` / `side_effect_recovery` 的 `wait_for_final(10_000)` 对着只停留
2–3 秒的成功提示，等满 10 秒回来时提示已消失 → `REQUIRED_ANCHOR_MISSING` →
`_uncertain(...)`。**一条实际发送成功的评论/私信被判成"结果不确定"**，按项目规则必须转人工
且禁止重放——误报本身即真实损失。

### 第三层：修第二层时引入了外部副作用打错目标的风险

把 `first_video_entry` 改用 `visible_matches(...).first` 后，该定位器是**惰性且每次重新
求值**的：`get_attribute("href")` 校验的元素与调用方 `action_operation.py` 点击的元素可能
不是同一个。

真实 Chromium 复现（隐藏骨架 `href=…499` 在前，已加载卡片 `href=…456` 在后，取到 entry 后
让骨架变可见再点击）：

```text
AssertionError: assert '/video/7351234567890123499' == '/video/7351234567890123456'
```

**评论会发到另一个视频上**。老写法 `page.locator(selector).first`（DOM 序、不含可见性）在两次
解析之间是稳定的，隐藏骨架在前时它选择"拒绝"；新写法把"拒绝"换成了"可能打到别的元素"——
从"不干活"退化成"干错事"，而这是不可撤销的外部副作用。

## GREEN

- 三份私有实现、三个私有 `_AnchorConflict`、三个私有 `_Locator` 协议全部删除，收敛到
  `page_anchors.py`：语义为"有没有"的闸门用 `any_visible`（逐 selector 探测），需要拿回
  定位器且多个可见确应判歧义的用 `unique_visible`（先经 `visible=true` 过滤再 count）；
- 三处 `wait_for` 改为在 `visible_matches(...)` 上等待；
- `first_video_entry` 用 `element_handle(timeout=...)` 把已校验的节点固化，后续读属性与点击
  都作用于同一快照；节点若被隐藏或卸载则点击超时失败，绝不改打其它元素；
- 新增参数化护栏，对当前全部 15 个 `unique_visible` 分组断言不含 Playwright 引擎前缀。

## 失败矩阵（本次新增覆盖）

- **页面改版 / 惰性渲染**：隐藏预渲染占位在前 + 真实可见节点在后 → 锚点判定、闸门判定、
  **有界等待**三条路径均以"任一可见匹配"为准；
- **等待与超时竞争**：等待预算不再被隐藏占位吞掉；`wait_for_final` 不再把已完成的评论/私信
  判成结果不确定；
- **外部副作用目标漂移（TOCTOU）**：校验与点击之间页面继续渲染 → 点击目标钉死在已校验节点；
- **选择器契约退化**：任何 `unique_visible` 分组混入引擎前缀 → 单测直接失败，不再静默退化成
  `page_unavailable`。该护栏已做反向验证（临时注入 `text="私信"` 确认变红后还原）。

## 真实边界（如实登记）

1. ElementHandle 消除的是**元素漂移**。同一节点上的 `href` 被 SPA 就地改写（校验后、点击前）
   **未覆盖**——要覆盖必须让页面对象自己拥有 click，属接口形态变更，本次未做；
2. `require_entry` 校验失败时已取得的 ElementHandle 未显式 `dispose()`，靠页面导航/关闭回收。
   当前每个 action 只取一次，影响可忽略，属已知未处理项；
3. 全部证据来自内置 Chromium + 本地隔离 fixture 页面，**无真实抖音账号的平台最终状态验收**；
4. `search_page.py::_visible_locator` 的真 fail-open，以及 `publish_page.py` 与 `search_page.py`
   中同款 `wait_for` 写法，本次**未改**，留给独立任务。

## 测试有效性的一处订正

`backend/tests/fixtures/douyin_browse_pages/profile-drift.html` 原本是两个**空的** `<main>`，
在真实 Chromium 里高度为 0、根本不可见。老实现不过滤可见性才把它们数成两个锚点判定"歧义"——
也就是说那条集成用例**声称在测"页面改版歧义"，实际测的是"数到两个隐藏节点"**。已给两个
`<main>` 各加一个 `<h1>` 使其真实渲染，该用例才真正覆盖声称的场景。

独立审查复核方式：把该 fixture 单独还原到 HEAD、保留新实现跑真实 Chromium，得到
`assert TIMED_OUT is UNKNOWN`，耗时从 3.07s 涨到 13.46s；并用探针确认空 `<main>` 的
`raw count = 1 / visible count = 0`。

## 清理

- 删除 3 份 `_unique_visible_locator`、3 个私有 `_AnchorConflict`、3 个私有 `_Locator` 协议，
  `grep` 确认无残留引用；对外返回类型改用 `page_anchors` 导出的 `AnchorLocator`；
- 三个页面测试 fake 中已成死代码的 `is_visible()` 一并删除；
- 常量 `_ATTRIBUTE_TIMEOUT_MILLISECONDS` 已无属性读取含义，改名 `_VIDEO_ENTRY_TIMEOUT_MILLISECONDS`；
- 测试结束无浏览器进程残留（按 `Chrome for Testing` / 项目隔离 Profile 过滤，计数为 0）。

## 验证命令与真实退出码

```text
uv run --frozen pytest tests/unit tests/contract -q        3287 passed, 1 skipped   EXIT=0
uv run --frozen pytest tests/integration -k "douyin or browser or drift" -q
                                                            62 passed, 5 skipped     EXIT=0
uv run --frozen pytest tests/integration/test_douyin_browse_browser.py -q
                                                            3 passed                 EXIT=0
uvx ruff check … executor/ tests/unit/executor/             49 errors（与 main 基线逐条一致）
uv run --frozen mypy                                        7 errors in 3 files（与基线一致）
pnpm --dir frontend test:contracts                          212 pass / 0 fail        EXIT=0
git diff --check                                            无输出                   EXIT=0
```

两个基线均以 `git stash push -u` 挪走全部改动后在 main 上实测对照得出，非口头断言。
