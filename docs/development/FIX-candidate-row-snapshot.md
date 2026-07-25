# 候选行读取钉死快照（既有缺陷修复）

> 类型：既有缺陷修复，不占用专项 Roadmap 任务号。承接
> `docs/development/FIX-douyin-anchor-visibility.md` 第二节「真实边界」第 2 条登记的独立
> 任务：`_result_items()` 返回惰性定位器、`nth(index)` 在两次求值之间漂移。
>
> 日期：2026-07-25
>
> 提交：本文件所在提交（父提交 `519c1c9`）

## 缺陷机理

`search_page.py::candidate_items()` 拿到 `_result_items()` 返回的 Playwright Locator，
用 `locator.nth(index)` 逐行取候选。Locator 是**惰性**的：每次对它做操作都会重新解析一次
选择器。`_candidate_from_item()` 对同一个 `item` 做了多次独立求值：

| 顺序 | 调用 | 重新解析一次行下标 |
| --- | --- | --- |
| 1 | `candidate_item.is_visible()` | 是 |
| 2 | `_required_nested_locator(item, AUTHOR)` 内的 `count()` | 是 |
| 3 | `_read_required_text(item, NAME)` 内的 `count()` | 是 |
| 4 | 同上的 `inner_text()` | 是 |
| 5 | `author.get_attribute("data-user-id")` | 是 |
| 6 | 同上的 `href` | 是 |
| 7 | 同上的 `data-user-handle` | 是 |

结果流随时可能变化（追加新行；顶部骨架占位显形）。`8265e16` 之后行集合是**可见过滤**的，
顶部隐藏骨架一显形，整个可见下标空间就整体挪一位——而「骨架先渲染再显形」正是上一轮修复的
立论前提，属高概率事件。于是第 3~4 步与第 5~7 步可能落在**两张不同的卡片**上：

- `platform_target_id`（后续评论/私信真正打击的目标）取自 A 号作者；
- `display_name`（界面展示、并被渲染进消息模板的名字）取自 B 号作者。

**动作打给了 A，界面和文案显示的是 B。** 这是「动作打给谁」的身份错配。现有的
`href_target_id != target_id → PrivacyRejected` 交叉校验只覆盖 ID 与 href（两者都读自同一个
author 节点，会一起漂移），**不覆盖 `display_name` 与 author 节点的同源性**，所以这条路径
是 fail-**open**：错配的候选被正常返回，没有任何一层拒绝它。

同样地，第 1 步的可见性闸门与后续字段读取之间也存在同一个窗口：**被校验可见的那一行，不
一定是被读取的那一行**，返回的候选可能从未经过可见性闸门。

## RED（真实失败输出）

单元层（`tests/unit/executor/test_douyin_candidate_extraction.py`）。原有的
`FakeLocator` 在构造时就把节点列表**捕获**下来，根本不模拟 Playwright 的惰性重求值——这正是
既有用例从未抓到这个缺陷的原因。本次先把 fake 改成持有「查询闭包」而非节点列表，每次读取都
重新求值，并加入「读到一半在顶部显形一行」的钩子。

```text
    def test_a_row_revealed_between_two_field_reads_cannot_mix_two_authors() -> None:
        ...
>       assert (candidate.platform_target_id, candidate.summary.display_name) == (
            "creator-001",
            "创作者甲",
        )
E       AssertionError: assert ('creator-drift', '创作者甲') == ('creator-001', '创作者甲')
E
E         At index 0 diff: 'creator-drift' != 'creator-001'
```

```text
    def test_the_row_whose_visibility_was_checked_is_the_row_that_is_read() -> None:
        ...
>       assert (candidate.platform_target_id, candidate.summary.display_name) == (
            "creator-001",
            "创作者甲",
        )
E       AssertionError: assert ('creator-drift', '骨架占位') == ('creator-001', '创作者甲')
E
E         At index 0 diff: 'creator-drift' != 'creator-001'
```

```text
    def test_every_row_snapshot_is_released_on_success_and_on_failure() -> None:
        """A pinned row holds a browser-side reference until it is disposed."""
        read = FakePage(items=[item(), item(target_id="creator-002", href="/user/creator-002")])
        assert extract(read, maximum=2).candidate_count == 2
>       assert read.handles and all(handle.disposed for handle in read.handles)
E       assert ([])
E        +  where [] = <test_douyin_candidate_extraction.FakePage object at 0x10a812bd0>.handles
```

```text
3 failed, 45 passed in 0.15s
```

第一条就是缺陷本体：动作目标 `creator-drift`，展示名 `创作者甲`。

## GREEN

```text
48 passed in 0.16s
```

- `page_anchors.py` 新增 `AnchorSnapshot` 协议与 `unique_visible_in_snapshot()`，把「同组只允许
  一个可见匹配」这条规则从 Locator 扩展到**钉死的元素快照**，可见性判定仍然只有这一份定义；
- `search_page.py` 新增 `_candidate_from_row()`：先 `row.element_handle(timeout=...)` 把该行
  钉成一次快照，再在同一个快照上完成可见性闸门与全部字段读取；
- 快照所有权明确：`_candidate_from_row` 用 `finally` 释放行快照，`_read_required_text` 用
  `finally` 释放字段快照，`_candidate_from_item` 用 `finally` 释放 author 快照，
  `unique_visible_in_snapshot` 在返回或抛错前释放所有**未交给调用方**的匹配。成功、隐私拒绝、
  页面异常三条路径都由 `test_every_row_snapshot_is_released_on_success_and_on_failure` 断言；
- `data-user-handle` 的读取从 href 校验之后上移进同一个 try，让 author 快照的存活区间收敛为
  「三次属性读取」，不再跨越 URL 解析。

## ElementHandle 行为核实结论（任务要求自行核实的部分）

用内置 Chromium 实测，**任务描述中「ElementHandle 也有 `.locator()`」是错的**：

```text
ElementHandle has locator: False
```

`ElementHandle` 没有 `locator`，所以 `unique_visible()` 不能直接作用于它（会 AttributeError
被 `except Exception` 吞成 `page_unavailable`）。实测确定的替代路径与差异：

| 事实 | 实测结果 |
| --- | --- |
| `handle.query_selector_all('<css组> >> visible=true')` 能否用共享的可见引擎 | 能。`raw nested: ['creator-A', 'creator-A2']` / `visible nested: ['creator-A2']` |
| 逗号并集是否去重（同一元素命中组内两个选择器） | 去重。`union dedup count: 1`，与 `unique_visible` 的语义一致 |
| 顶部插入新行后，快照是否跟着漂移 | 不漂移。`rows.nth(0) id: row-inserted`，而 `handle id: row-a`，其嵌套查询仍解析到 `creator-A2` |
| 节点被移出 DOM 后 | `is_visible: False`、嵌套匹配数 `0`，fail closed，绝不改读别的行 |
| `ElementHandle.get_attribute` / `inner_text` 的签名 | **不接受 `timeout`**：`get_attribute(self, name)`、`inner_text(self)`；Locator 版本才有 `timeout` |

最后一条在开发中真实触发过一次回归：照搬 Locator 的 `timeout=` 会 `TypeError`，让原本正常的
`results-normal.html` 抽取变成 `page_unavailable`。据此把 `_CANDIDATE_FIELD_TIMEOUT_MILLISECONDS`
改名为 `_CANDIDATE_ROW_TIMEOUT_MILLISECONDS`——它现在只约束「把一行钉成快照」这一次等待，
字段读取不再有等待预算（快照已解析完毕，没有选择器可等）。原先字段读取那点等待预算，在
`unique_visible` 用 `count()`（本就不等待）计数之后才生效，实际覆盖不到「节点尚未渲染」的场景，
因此不认为这是能力损失；如判断有误，应作为独立问题重新评估。

## 真实边界（如实登记）

1. **没有任何真实浏览器用例能复现这个交错。** 尝试过在 fixture 页面里劫持
   `HTMLElement.prototype.innerText`、`Element.prototype.getAttribute`、`querySelectorAll`、
   `matches`、`getClientRects` 让页面在读取过程中自行插入一行，全部**不触发**：

   ```text
   PROBE hits after count: []
   PROBE hits after inner_text: []
   PROBE hits after get_attribute: []
   ```

   Playwright 的 injected script 运行在**隔离世界（isolated world）**，主世界里改写的原型对它
   不可见。因此交错本身只在单元层覆盖。这条链路的可信度靠两段证据拼起来：真实 Chromium 证明
   「Locator 的 `nth(0)` 会漂移、ElementHandle 不会」（见上表，并固化为
   `test_a_pinned_row_keeps_its_own_fields_when_the_feed_reveals_a_row_above_it`），单元 fake
   精确按这个行为建模，单元用例证明读取逻辑在该行为下正确。**不宣称这等价于真实浏览器复现。**

2. **本次新增的 3 个真实浏览器用例，在把 `search_page.py` 回滚到修复前后仍然全部通过**
   （实测：`git stash push -- backend/src/.../search_page.py` 后 `4 passed in 5.22s`）。它们守的
   是「移植到快照后可见性过滤与歧义检测没有丢」以及「Playwright 的快照语义没有变」，
   **不是缺陷本体的回归网**。缺陷本体的回归网只有单元层那两条。

3. **`count` 与 `nth(index)` 之间的重求值窗口本次未关闭。** `_result_items()` 先 `count()`
   得到行数，循环里再逐行 `nth(index).element_handle()`；两者之间可见下标仍可能整体位移。
   后果与本次修掉的不同：每个候选内部**是自洽的**（全部字段来自同一个节点），漂移只会让某一行
   被读两次或被跳过。重复候选在下游被
   `douyin_candidate_policy.py::evaluate_douyin_candidates` 判成 `DUPLICATE_IN_TASK`（同批里
   `dedupe_key` 重复的第二条不再 `ELIGIBLE`），不会导致对同一个人重复动作；跳过则只是少拿几个
   目标。二者都不构成身份错配。根治方式是用 `Locator.element_handles()` 一次原子地把整组可见行取成快照
   （实测一次调用返回 `['row-inserted', 'row-a', 'row-b']`），代价是改动 `_result_items()` 的
   返回形态并调整依赖 `count()` 校验的既有用例，超出本次范围。

4. 全部证据来自内置 Chromium + 本地隔离 fixture 页面，**无真实抖音账号的平台最终状态验收**。
   本修复只影响读取阶段，不产生外部副作用，但「读出来的目标是否就是抖音上那个人」仍未在真实
   平台验证过。

5. 同一节点上的属性被 SPA 就地改写（读取 `data-user-id` 之后、读取 `href` 之前）**未覆盖**，
   与 `8265e16` 登记的边界同类：快照消除的是**元素漂移**，不是**节点内容改写**。

6. `login_dialog()` / `blocking_dialog()` 仍然只有测试在调用，生产无调用点（沿用上一轮登记）。

## 失败矩阵（本次新增覆盖）

- **页面改版 / 惰性渲染 → 外部副作用目标漂移（TOCTOU）**：读取过程中顶部显形一行 → 候选的
  身份事实与展示名必须同源，否则整批读取失败，不允许返回错配的一对；
- **可见性闸门与读取竞争**：被校验可见的行必须就是被读取的行；
- **同组歧义**：一张卡片两个可见作者 → `AnchorConflict` → `PAGE_UNAVAILABLE`，在真实 Chromium
  上以生产入口验证（`results-two-visible-authors.html`）；
- **惰性渲染占位**：隐藏骨架行 + 卡片内隐藏 author/name 占位 → 真实 Chromium 上仍读到真实作者，
  证明可见引擎经 `query_selector_all` 链接后过滤语义未变（`results-lazy-rendering.html`）；
- **资源泄漏**：成功、隐私拒绝、页面异常三条路径都必须释放所有快照，不得把浏览器端引用留住；
- **节点卸载**：被钉死的行若被移出 DOM，`is_visible()` 为 False、嵌套匹配为 0，fail closed。

## 清理

- 删除 `_Locator` 协议中已成死代码的 `is_visible` / `get_attribute` / `inner_text`，改由新增的
  `_Snapshot` 协议承载（签名按 ElementHandle 的真实签名，去掉 `timeout`）；
- `_Locator` 改为显式继承 `AnchorLocator`（此前靠结构相容，删掉 `first` 后 mypy 立刻报
  12 处 `arg-type`，说明这层关系本就该写出来）；
- `_required_nested_locator` 改名 `_required_nested_snapshot`（不再返回定位器）；
- `_CANDIDATE_FIELD_TIMEOUT_MILLISECONDS` 改名 `_CANDIDATE_ROW_TIMEOUT_MILLISECONDS`；
- 单元 fake 中随之失效的 `FakeLocator.is_visible` / `get_attribute` / `inner_text` 一并删除
  （删除前先逐个换成 `raise AssertionError` 跑一遍确认确实无人调用，48 passed 后才删）；
- 新增两个 fixture 已登记进 `test_douyin_discovery_fake_pages.py` 的闭集断言；两个 fixture 都是
  纯 HTML，无脚本、无外链、无 cookie/localStorage/fetch，满足该用例的既有约束；
- 排查过程用的 3 个临时探针文件（`test_zz_probe*.py`）已删除；
- `ruff format` 顺带修正了 `test_douyin_candidate_extraction_browser.py` 中**既有**的一处格式
  偏差（第 28 行参数列表折行，HEAD 上即存在，与本次逻辑无关）；
- 测试结束无浏览器进程残留：`pgrep -f chrome_for_testing | wc -l` 每轮均为 `0`。

## 验证命令与真实输出

```text
uv run --frozen pytest tests/unit/executor/ -q       1017 passed, 1 skipped   （基线 1010 passed, 1 skipped）
uv run --frozen pytest tests/integration/ -q -k douyin
                                                      55 passed, 3 skipped, 252 deselected
                                                      （基线 52 passed, 3 skipped）
uv run --frozen ruff check .                          Found 10 errors          （与 519c1c9 基线逐条一致）
uv run --frozen mypy .                                Found 7 errors in 3 files（与基线一致）
uv run --frozen ruff format --check <改动文件>         25 files already formatted
```

两条基线在动手前于干净的 `519c1c9` 上实测：`ruff check .` 10 errors（2 处 RUF001 + 1 处 I001 +
其余，非上一轮台账里那份 `uvx ruff check <指定目录>` 的 49）；`mypy .` 7 errors in 3 files
（`tests/integration/conftest.py` / `test_browser_runtime_lifecycle.py` / `test_douyin_qr_login.py`）。
