# FIX 动效零件在固定模板路径下的诚实状态

> 状态：🔍 待验收（前端止损已落地并经组件与页面测试证明；缺一次正式 App 用户路径验收）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/FIX-motion-parts-selection-wiring.md` 第 6 节方案 D。
> 该调查确认「视频制作 → 品牌动效成片 → 动效零件」页勾选的零件对成片零影响，
> 且这不是漏传字段能修的——把零件 id 变成分镜像素的合成能力在仓库里不存在。
> 本次只做止损，不实现合成能力。

## 缺陷

用户在动效零件页勾选的零件，不会以任何形式进入成片。而页面的每一处交互都在暗示相反的事：

| 界面元素 | 用户读到的意思 | 实际 |
| --- | --- | --- |
| 「动效零件」标签页可点开 | 这是本次制作的一个步骤 | 与提交请求无关 |
| 134 张卡片各有「加入第 N 段」按钮 | 点了就会加进这一段 | 只改前端 state |
| 「已选 N 项」实时更新 | 系统记住了我的选择 | 提交时不读这个 state |
| 「自动推荐」可点并写入选择 | 系统在替我挑零件 | 同上 |
| 分镜切换（第 1/2/3 段） | 可以逐段安排 | 同上 |

唯一的免责说明是 `MotionPartsCatalog.tsx` 说明段落中间的一句
「提交固定模板手工制作时不使用零件选用」——一句埋在长段落里的小字，
对抗上面五处持续的正向反馈。主对话当天刚修好「自动推荐」的算法（`74fb282`），
反而让这个暗示更强。

判据：一个不读文档的运营人员，在勾选之前就必须知道这些勾选不会影响成片。
加一行小字不满足这个判据。

## 选择的形态与理由

**禁用交互 + 显著状态标注，保留浏览。** 三个信号同时成立，且互相独立：

1. **标签页最上方一条警告横幅**，位于零件目录之前，用户在看到任何一张卡片前先读到它：
   > 本次制作方式不会用到零件选择
   >
   > 你现在用的是"固定模板手工制作"：成片画面只由整体风格、品牌颜色和你填写的文字决定，
   > 不会放入下面的任何零件。零件目前只服务于尚未开放的"一句话自动制作"，
   > 所以这里可以浏览了解有哪些零件，但暂时不能选用。
2. **134 张卡片的按钮全部禁用**，文案由「加入第 N 段」改为「本次制作不使用」。
   用户滚到哪里都在重复这条事实，而不是只在页面顶部说一次。
3. **每段的汇总行不再显示「已选 N 项」**，改为「第 N 段：本次制作不使用零件」；
   「自动推荐」按钮与分镜切换一并禁用。

三条硬约束的对应：

| 约束 | 做法 |
| --- | --- |
| 不能让页面变成谎言的另一面（整个藏掉会让用户以为产品没有这个能力） | 目录、分类筛选、134 张卡片的性能/设备/适用/来源全部保留可读；横幅明说零件是给哪条链路用的、为什么现在不能用 |
| 保留扩展性 | 见下节：能力由 `MotionPartsUsage` 决定，不写死在组件里；接通消费链路只需在一处登记新制作方式 |
| 不做合成能力实现 | 未改任何渲染、提交、契约或原生代码 |

被否掉的两种形态：**隐藏标签页**违反第一条约束；**只改那句小字的措辞**仍然打不过
五处正向交互反馈，不满足判据。

## RED

先写测试并实跑，确认失败原因正是"页面在暗示勾选有效"：

```text
cd frontend && npx vitest run src/features/video-studio        EXIT=1
  Test Files  3 failed | 3 passed (6)
  Tests  5 failed | 46 passed (51)

× MotionPartsCatalog ... > states it above the catalog, before anything can be ticked
    TestingLibraryElementError: Unable to find an accessible element with the role "alert"
× MotionPartsCatalog ... > disables every part action and labels it instead of offering a beat
    AssertionError: expected [ <button …(2)>…(1)</button>, …(133) ] to have a length of +0 but got 134
× MotionPartsCatalog ... > never shows a per-beat count the film would honour
    AssertionError: expected [ <span …(1)></span>, …(2) ] to have a length of +0 but got 3
× VideoStudio ... > says in the 动效零件 tab that the submitted job ignores part selections
    TestingLibraryElementError: Unable to find an element with the text: /本次制作方式不会用到零件选择/
× motion parts catalog projection > reports that the fixed template does not turn selections into pixels
    TypeError: motionPartsUsage is not a function
```

五条失败逐条对应缺陷表：没有任何提示（第 1、4 条）、134 个「加入第 N 段」按钮真实存在
（第 2 条）、三行「已选 N 项」真实存在（第 3 条）、判定能力缺失（第 5 条）。

新增 7 条测试中另外 2 条**在旧代码上就是绿的**，这是有意为之，它们是守门用例而不是 RED：

- `keeps all 134 parts browsable so the capability stays visible`——守"不要整个藏掉"，
  只有当有人把目录删掉或隐藏时才变红；
- `drops the notice and restores per-beat selection`（`applies_to_output`）——守扩展性。
  旧组件忽略未知 prop 所以恰好通过；真正的价值在于**将来**有人把开关翻过来时它必须仍然绿。

## GREEN

```text
cd frontend && npx vitest run src/features/video-studio        EXIT=0
  Test Files  6 passed (6)
  Tests  51 passed (51)

cd frontend && npx vitest run                                  EXIT=0
  Test Files  58 passed (58)
  Tests  446 passed | 1 expected fail (447)

cd frontend && npx tsc -b --force                              EXIT=0
cd frontend && npx eslint .                                    EXIT=0

python3 scripts/check_user_facing_branding.py                  EXIT=0
  user-facing branding and plain-language scan passed (51 frontend, 249 native files)
python3 scripts/check_motion_catalog_ui_projection.py          EXIT=0
  check passed: 134 items, 11 categories, labels closed, names fully localized,
  no indicator or URL leakage
```

条数逐次核对：`src/features/video-studio` **44 → 51**（+7）。分解：
`MotionPartsCatalog.test.tsx` 8 → 13（+5）、`motion-parts-catalog.test.ts` 10 → 11（+1）、
`VideoStudio.test.tsx` 14 → 15（原「浏览目录并保留逐段覆盖」拆成「浏览目录」与
「说明提交时不使用零件」两条，+1）。RED 一轮 51 条里 5 失败 46 通过，GREEN 一轮 51 条全通过，
两轮总数一致，排除了"新测试没被收集"。

`npx tsc -b` 必须带 `--force`，不带会因增量缓存直接空转返回 0。
首轮改完 e2e spec 后 `tsc -b --force` 真实拦下过一次
`TS2802: Type 'ChainablePromiseArray' can only be iterated through...`，已改为索引遍历。

## 交付

### 能力判定（`motion-parts-catalog.ts`）

```ts
export type MotionPartsUsage = "applies_to_output" | "browse_only";

const USAGE_BY_CREATION_MODE: Readonly<
  Record<MotionCreationMode, MotionPartsUsage>
> = { manual_template_v1: "browse_only" };

export function motionPartsUsage(mode: MotionCreationMode): MotionPartsUsage
```

- 用 `Record<MotionCreationMode, …>` 而不是数组或布尔量，是为了让**类型系统成为扩展点的门禁**：
  一旦有人给 `MotionVideoDraftRequest["creationMode"]` 加上新的制作方式，这个 record 会立刻
  编译失败，直到有人明确回答"这条链路读不读零件选择"。接通「一句话自动制作」后，把它登记为
  `applies_to_output` 即可让全部交互自动恢复，组件不需要改；
- `browse_only` 记录的是产品事实（提交请求到渲染 Worker 之间没有任何一段携带零件 id），
  不是可随手翻的功能开关。`motion-parts-catalog.test.ts` 用一条测试把这个断言钉住，
  防止有人在没接通渲染前先把开关翻过来、让「已选 N 项」重新出现。

### 界面（`MotionPartsCatalog.tsx`）

新增 `usage` prop（必填，不给默认值——默认值会让新调用方无声地落进某一侧）。
`selectable === false` 时：渲染警告横幅、禁用分镜切换、禁用「自动推荐」、
禁用并改写全部卡片按钮文案、汇总行改为「本次制作不使用零件」，
并从说明段落里删掉「这里可以逐段查看并手工覆盖」这句只在消费链路成立时才为真的话。
`selectable === true` 时行为与改动前逐字相同。

四条用户可见文案抽成模块常量单点定义，避免组件文案与测试/e2e 锚点分头维护。

### 接线（`VideoStudio.tsx`）

`"manual_template_v1"` 原来只作为字面量出现在提交请求里。抽成 `MOTION_CREATION_MODE`
单点定义，提交请求与 `motionPartsUsage(MOTION_CREATION_MODE)` 共用同一个值——
提交的制作方式与零件页的判定不可能各说各话。

`motionPartSelections` 状态、随段数 resize 的逻辑与 `onSelectionsChange` 全部保留，
消费链路接通后无需重建。

### 桌面 E2E 锚点（`frontend/e2e-tauri/motion-parts-catalog.spec.ts`）

原用例断言的是「点击加入第 1 段 → 已选 1 项 → 切页后仍是 1 项」，这条路径在正式 App 里
已不存在。改为断言：横幅文案出现、`已选` 不再出现、`加入第 N 段` 按钮数为 0、
卡片按钮与「自动推荐」全部 disabled，并保留原有的分类筛选、卡片属性、
零件 id 不泄漏与目录可浏览断言。**按任务约束只改文案锚点，未运行 WDIO。**

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 固定模板路径打开零件页 | 卡片前先出现警告横幅（DOM 顺序断言，不只是"存在"） | 组件测试 |
| 用户尝试勾选任一零件 | 134 个按钮全部 disabled，无「加入第 N 段」文案 | 组件 + 页面测试 |
| 用户点「自动推荐」 | 按钮 disabled，`onSelectionsChange` 未被调用 | 组件测试 |
| 页面携带历史选择（如 `[["data-chart"],[],[]]`） | 不显示「已选 N 项」，三行全部显示「本次制作不使用零件」 | 组件测试 |
| 有人担心能力被藏掉 | 134 项仍全部可浏览、分类筛选仍生效、卡片四项属性仍可读 | 组件测试 |
| 消费链路接通后 | 横幅消失，勾选、计数、自动推荐逐字恢复原行为 | 组件测试（`applies_to_output`） |
| 有人在未接通渲染时翻开关 | 判定测试变红 | `motion-parts-catalog.test.ts` |
| 新增制作方式但没表态是否消费零件 | 编译失败（`Record` 缺键） | 类型系统 |

## 正常用户路径验收

**未完成。** 按任务约束未启动 App、浏览器、Playwright、WDIO 或任何构建。

已完成的分层证据：组件测试（`MotionPartsCatalog.test.tsx`）与页面测试
（`VideoStudio.test.tsx`，从「选择品牌动效成片 → 点开动效零件标签页」这一真实交互序列进入）
都断言了用户可感知的事实——横幅可见、按钮 disabled、计数消失。
按项目规则第 8 节，这只能证明 React 业务交互，不能替代正式 App 的用户路径验收。

仍缺：在正式 macOS/Windows 包里点开「视频制作 → 品牌动效成片 → 动效零件」，
肉眼确认横幅位置、禁用态与文案换行。已把对应断言写进 `motion-parts-catalog.spec.ts`，
下一次跑桌面 E2E 门禁时会一并覆盖。

## 真实边界（我没做到的部分）

1. **没有做任何正式 App 用户路径验收**，见上节。横幅在真实窗口宽度下的换行、
   禁用按钮在 Ant Design 主题下的对比度都未肉眼确认。
2. **没有实现任何合成能力**，也没有让零件真的进入成片——那属于
   `FIX-motion-parts-selection-wiring.md` 第 6 节 A/B/C 的产品决策，本次只做 D。
   用户仍然得不到"选了零件就出现在画面里"的能力。
3. **未改动 Rust、后端、契约与 `scripts/`**（并行会话正在改这些）。因此
   `deny_unknown_fields`、Worker 子文档拒绝等既有边界完全未触碰，也未新增跨进程失败路径。
4. **`docs/development-roadmap.md` 与专项 roadmap 的状态没有动。** BM-12/13/14 仍是 ✅、
   BM-15/16 仍是 🔍。上一份调查已经指出"✅ 但零件从未生效"应当修正，
   本次是止损而非实现，不足以单方面下调这些任务状态，留给主对话决策。
5. **Windows 侧未执行。** 全部结论来自 macOS 树上的测试与静态检查。
6. **`check_user_facing_branding.py` 的 native 文件计数在本次会话内从 247 变到 249**，
   这是并行会话在改 Rust/后端所致，不是本次改动的影响；本次只新增中文文案，
   未引入任何英文术语或上游项目名。

## 清理

未启动 App、浏览器、Worker、Playwright、WDIO、Tauri 构建或任何本地服务；
无进程、端口、容器、临时帧或临时 Profile 需要回收。
未触碰 `.local/`、`~/Library/Caches/automation-tool-build/`、
`~/Library/Application Support/com.aventador.automationtool/`、`vendor/`、
`backend/`、`contracts/`、`scripts/`、`frontend/src-tauri/`。
临时测试日志写在会话 scratchpad，未进仓库。未 `git add` / `git commit`。

## 文档

| 文件 | 改动 |
| --- | --- |
| `frontend/src/features/video-studio/motion-parts-catalog.ts` | 新增 `MotionPartsUsage` 与 `motionPartsUsage()` |
| `frontend/src/features/video-studio/motion-parts-catalog.test.ts` | 10 → 11 |
| `frontend/src/features/video-studio/MotionPartsCatalog.tsx` | 新增 `usage` prop、警告横幅、禁用态与文案 |
| `frontend/src/features/video-studio/MotionPartsCatalog.test.tsx` | 8 → 13 |
| `frontend/src/features/video-studio/VideoStudio.tsx` | 抽 `MOTION_CREATION_MODE`，传入 `usage` |
| `frontend/src/features/video-studio/VideoStudio.test.tsx` | 14 → 15 |
| `frontend/e2e-tauri/motion-parts-catalog.spec.ts` | 断言改为诚实状态（未运行） |
| 本文件 | 新增 |

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 在 A（装配 + 内联合成器）/ B（接 AI 一句话链路）/ C（自研零件效果）之间选定方向 | 待决策 | 主对话 |
| 正式 App 里点开动效零件页的用户路径验收 | 未做 | 下一次桌面 E2E 门禁 |
| Windows 正式包同一页面的验收 | 未做 | 需 Windows 主机 |
| BM-12/13/14 的 ✅ 状态是否应下调 | 待决策 | 与实现任务同一提交 |
| 消费链路接通后把 `manual_template_v1` 之外的制作方式登记进 `USAGE_BY_CREATION_MODE` | 未做 | 实现任务 |
