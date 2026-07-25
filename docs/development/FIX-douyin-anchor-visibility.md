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

---

# 第二次：search_page 的真 fail-open 与剩余两处等待写法

> 类型：既有缺陷修复，不占用专项 Roadmap 任务号。承接上文「真实边界」第 4 条登记的
> 独立任务：`search_page.py` 的真 fail-open，以及 `search_page.py` / `publish_page.py`
> 中同款 `wait_for` 写法。
>
> 日期：2026-07-25
>
> 提交：本文件所在提交（父提交 `8265e16`）

## 缺陷

### 一、`search_page.py::_visible_locator` —— 真 fail-open（安全相关，本次主因）

```python
def _visible_locator(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.is_visible():
            return locator
    return None
```

没有 `count > 1` 兜底，纯取每个 selector 的**第一个**匹配看可见性。单页应用预渲染隐藏
占位，"隐藏占位在前、真实元素在后"时 `.first` 解析到占位、`is_visible()` 为 False，遍历
完所有 selector 后返回 `None`。

`observe()` 用它先后探测 `_LOGIN_DIALOG_SELECTORS` 与 `_BLOCKING_DIALOG_SELECTORS`
（后者展开含 `DOUYIN_RISK_CHALLENGE_SELECTORS`，即验证码/滑块/风控）。漏判后不返回
`LOGIN_REQUIRED` / `DIALOG_BLOCKED`，继续判 `_SEARCH_INPUT_SELECTORS`，判成
`HOME_READY` 并**继续执行搜索链路**——直接违反项目规则 §5「验证码、滑块、风险提示、
异常登录和平台安全校验只能暂停并转人工」。

与上一次三个页面对象的 fail-**closed** 误报不同，这一处是真的往前走。

### 二、两处 `wait_for` 仍钉在第一个匹配

`search_page.py::_wait_for_state` 与 `publish_page.py::_wait_for` 都是
`page.locator(", ".join(selectors)).first.wait_for(state="visible")`。成因与上文第二层
完全一致：进入等待时第一个匹配必然是隐藏占位，真实元素后来插入也解析不到，只能烧满超时。

### 三、候选读取同款写法（fail-closed，但会让正常页面颗粒无收）

`_result_items()` / `result_item_count()` 不过滤可见性直接 `count()`，`_required_nested_locator()`
取 `.first` 看可见性。后果：

- 结果流带一行骨架占位时 `count()` 把它算进去，`nth(0)` 取到骨架，`candidate_items` 的
  可见性校验把**整批候选**判成 `PRIVACY_REJECTED`——只要该页面总渲染骨架，一条候选都拿不到；
- 卡片内部有隐藏作者占位时，`.first` 解析到占位，整张卡片被判成"缺作者"；
- 一张卡片里若有两个可见作者节点，`.first` 静默取其中一个——身份事实（`platform_target_id`、
  展示名）可能取自另一个人，后续动作会打到陌生人身上。

## RED（真实失败输出）

单元层（`tests/unit/executor/test_douyin_search_page.py`，隐藏占位在前 + 真实节点在后）：

```text
>       assert observation.state is state
E       AssertionError: assert <DouyinSearchPageState.HOME_READY: 'home_ready'>
E                       is <DouyinSearchPageState.LOGIN_REQUIRED: 'login_required'>
E        +  where ... = DouyinSearchPageObservation(page_version='douyin.web.v1',
E              entry='home', state='home_ready', evidence='search_entry_visible',
E              selector_version='douyin.search-page.v1', circuit_open=False).state
```

`[role="dialog"]` 与 `iframe[src^="…/verifycenter/captcha/"]` 两个参数化同样红，均判成
`HOME_READY`。

真实内置 Chromium（`tests/integration/test_douyin_search_execution_browser.py`，
生产入口 `DouyinSearchExecution.run()`）：

```text
>           assert observation.state is DouyinSearchExecutionState.DIALOG_BLOCKED
E           AssertionError: assert <DouyinSearchExecutionState.TIMED_OUT: 'timed_out'>
E                           is <DouyinSearchExecutionState.DIALOG_BLOCKED: 'dialog_blocked'>
E            +  where ... = DouyinSearchExecutionObservation(state='timed_out',
E                  evidence='result_url_timed_out', …, circuit_open=True).state
```

`result_url_timed_out` 说明它没有停在风控前，而是**填了关键词、点了搜索**，一路走到等待
结果 URL 才超时——风控页当前正显示在屏幕上。这是本次缺陷在真实浏览器里的完整复现。

等待类缺陷（单测抓不到"烧满超时"，用 fake 记录每次超时并断言 `wait_timeouts == []`）：

```text
>       assert ready.state is DouyinSearchPageState.HOME_READY
E       AssertionError: assert <DouyinSearchPageState.UNKNOWN: 'unknown'> is HOME_READY
```

`publish_page` 同款：

```text
>       assert observation.state is DouyinPublishPageState.FORM_READY
E       AssertionError: assert <DouyinPublishPageState.UNKNOWN: 'unknown'> is FORM_READY
```

候选读取三条：`_a_hidden_skeleton_row_…`（整批被 `PRIVACY_REJECTED`）、
`_a_hidden_author_placeholder_…`（卡片被判缺作者）、`_two_visible_author_nodes_…`
（静默取到 `creator-001`，应当 fail closed）。

## GREEN — 每组选择器的归属依据

删除 `_visible_locator`，全部改为直接调用 `page_anchors` 的三个原语：

| 分组 | 归属 | 依据 |
| --- | --- | --- |
| `_LOGIN_DIALOG_SELECTORS` | `any_visible` | 语义是"有没有"的闸门。登录面板与其外壳可以同时可见，判成歧义会把停机原因换成"页面不可用"；组内含 `:has-text()`，本就不能逗号拼接 |
| `_BLOCKING_DIALOG_SELECTORS` | `any_visible` | 同上。验证码 iframe 与包着它的 `[role="dialog"]` 同时可见是常态，必须仍然报 `BLOCKING_DIALOG` |
| `_SEARCH_INPUT_SELECTORS` | `unique_visible` | 要拿回定位器并**填入关键词**。两个可见搜索框意味着填到哪个不确定，必须歧义。纯 CSS |
| `_SEARCH_SUBMIT_SELECTORS` | `unique_visible` | 要拿回定位器并**点击**。同上。纯 CSS |
| `_RESULT_LIST_SELECTORS` | `unique_visible` | 结果就绪的唯一根锚点，两个可见列表说明页面已改版。纯 CSS |
| `_RESULT_ITEM_SELECTORS` | `visible_matches`（逐 selector 计数） | 天然匹配多个元素，唯一性无意义；需要的是"真实渲染了几条"，骨架不能计入，也不能顶掉 `nth()` 的下标 |
| `_CANDIDATE_AUTHOR_SELECTORS` | `unique_visible` | 从该节点读身份事实。一张卡片两个可见作者 = 无法确定要对谁动作，必须 fail closed。纯 CSS |
| `_CANDIDATE_NAME_SELECTORS` | `unique_visible` | 同上。纯 CSS |
| `login_dialog()` / `blocking_dialog()` 取回 | `any_visible` + `visible_matches(...).first` | 与 `observe()` 的闸门语义一致：取"第一个可见的转人工锚点"，不要求唯一 |

其余：

- `_wait_for_state`（search）与 `_wait_for`（publish）改为在 `visible_matches(...)` 上等待，
  写法与 `8265e16` 里 comment/direct_message/profile 三处一致；
- `observe()` 新增 `except AnchorConflict → CONFLICTING_ANCHORS`（该组合本就在
  `_ALLOWED_OBSERVATIONS` 中），不再被 `except Exception` 吞成 `PAGE_UNAVAILABLE`；
- 新接入 `unique_visible` 的 5 个分组补进 `test_douyin_page_anchors.py` 的
  `UNIQUE_VISIBLE_GROUPS` 护栏（15 组 → 20 组）。

## 失败矩阵（本次新增覆盖）

- **风控 / 验证码 / 登录失效被占位遮住**：单测三种转人工弹窗 + 真实 Chromium 一条生产路径，
  断言不仅状态正确，且**关键词从未被填入**（`window.__searchedKeyword is None`）、URL 未跳转；
- **等待与超时竞争**：`search` 与 `publish` 两条等待路径断言 `wait_timeouts == []`，即"等待必须
  被任一可见匹配满足"，不接受"超时后 `observe()` 兜底又变绿"；
- **页面改版歧义**：同组内两个可见锚点 → `CONFLICTING_ANCHORS`，不再静默取第一个；
- **惰性渲染骨架**：骨架行不计入 `result_item_count`、不顶掉 `nth()` 下标、不再让整批候选被拒；
- **身份事实歧义**：一张卡片两个可见作者 → `PAGE_UNAVAILABLE` 而非静默取一个。

## 行为变更（需要复核者确认的一处）

结果流中**只有隐藏行**时，旧行为是 `PRIVACY_REJECTED`（判成必须转人工的页面问题），新行为是
"0 条候选的正常空快照"。依据：可见过滤后我们只读用户真正看得见的行，读到 0 条是诚实答案；
把惰性渲染的正常页面报成隐私拒绝会造成无谓转人工。对应用例
`test_only_hidden_rows_report_an_empty_snapshot_rather_than_a_privacy_rejection`。

## 真实边界（如实登记）

1. 全部证据来自内置 Chromium + 本地隔离 fixture 页面，**无真实抖音账号的平台最终状态验收**；
   风控页是自建 fixture，不是真实抖音风控；
2. `_result_items()` 返回的仍是**惰性重求值**的定位器，`nth(index)` 在两次求值之间可能漂移
   （与 `8265e16` 中 `first_video_entry` 修掉的是同一类问题）。本次未处理，钉死快照需要改
   接口形态。**这条的优先级不低，此前的"只有读、无外部副作用"定性是错的**：

   - `_candidate_from_item` 产出的 `platform_target_id` 正是后续评论/私信动作的**打击目标**，
     `display_name` 是运营人员审批目标时看到的名字。读到错配的一对，外部副作用就打到错的人
     身上——正是"外部副作用必须打到正确目标"要防的；
   - `author` 与 `name` 是对同一个 `item` 的**两次独立惰性求值**，之间存在漂移窗口；
   - **本次改动扩大了触发条件**：改用可见过滤前，惰性渲染在结果流**末尾**追加行，已读下标是
     稳定的；改用可见过滤后，**顶部隐藏骨架显形会让整个可见下标空间整体位移一位**——而
     "骨架先渲染再显形"恰恰是本次修复的立论前提，属高概率事件；
   - 现有的 `href_target_id != target_id → PrivacyRejected` 交叉校验只覆盖 ID 与 href，
     **不覆盖 `display_name` 与 author 节点的同源性**。

   独立审查用真实 Chromium 复现（顶部隐藏骨架 + 一张真实卡片，在两次字段读取之间让骨架显形）：

   ```text
   visible count at snapshot time = 1
   platform_target_id='creator-A'   display_name='骨架占位'   -> mixed identity = True
   ```

   根治方向：把行快照钉死（`element_handle`，同 `8265e16` 修 `first_video_entry` 的做法），
   或在 `_candidate_from_item` 内对 author 与 name 做一次同源交叉校验；
3. `login_dialog()` / `blocking_dialog()` 两个访问器**当前只有测试在调用**，生产代码无调用点。
   本次保留（属页面对象对外形态，删除是产品决定），一并登记；
4. `publish_page` 的 22 条真实浏览器用例（`test_douyin_publish_embedded_browser.py`）已随
   集成子集全量通过，等待写法变更未破坏 PB-05。

## 清理

- 删除 `search_page.py::_visible_locator`（唯一的第 5 份私有锚点判定），`grep -rn "_visible_locator" backend/`
  返回空（EXIT=1）；
- `_Locator` 协议中随之失效的 `wait_for` 删除，等待改用新增的最小 `_WaitLocator` 协议
  （与 comment/direct_message/profile 一致）；协议内成员重排为"先 `AnchorLocator` 的三个成员、
  再候选读取专有成员"，与新增 docstring 对应；
- 对外返回类型由私有 `_Locator` 改为 `page_anchors` 导出的 `AnchorLocator`；
- 五个测试 fake（search_page / publish_page / candidate_extraction / bounded_scroll /
  search_execution）统一支持 `.locator("visible=true")` 链式调用与"隐藏占位在前"建模；
  三处重复的分组字面量提为 `INPUT_GROUP` / `SUBMIT_GROUP` / `RESULT_GROUP` 常量；
- 顺带修正 `test_douyin_search_execution_browser.py` 第 26 行**既有**的 `ruff format` 偏差
  （HEAD 上即存在，与本次逻辑无关，仅为让 `ruff format --check <改动文件>` 通过）；
- 测试结束无浏览器进程残留（按 `Chrome for Testing` / 项目隔离 Profile 过滤，计数为 0）。

## 验证命令与真实退出码

```text
uv run --frozen pytest tests/unit tests/contract -q          3307 passed, 1 skipped   EXIT=0
uv run --frozen pytest tests/integration -k "douyin or browser or drift" -q
                                                             63 passed, 5 skipped     EXIT=0
uv run --frozen mypy                                         7 errors in 3 files      EXIT=1
uvx ruff check … executor/ tests/unit/executor/              49 errors                EXIT=1
uvx ruff format --check … <10 个改动文件>                     10 files already formatted EXIT=0
pnpm --dir frontend test:contracts                           212 pass / 0 fail        EXIT=0
git diff --check                                             无输出                   EXIT=0
```

两条基线在动手前于干净的 `8265e16` 上实测：`ruff` 49 errors；`mypy` 7 errors in 3 files
（`tests/integration/conftest.py` 4 / `test_browser_runtime_lifecycle.py` 2 /
`test_douyin_qr_login.py` 1）。改后逐条比对完全一致，非口头断言。
