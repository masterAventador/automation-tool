# FIX：动效零件全中文命名、自动推荐重写、卡片冗余标签清理

> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 类型：试用缺陷修复（闭合 `docs/development/dogfood-findings-20260726.md` 第 2c、2d、2e 节）

## 缺陷

用户在 macOS 正式包上按真实路径试用「视频制作 → 品牌动效成片 → 动效零件」，提出三条：

| 来源 | 现象 | 代码事实 |
| --- | --- | --- |
| 2d | 134 个零件全是英文原名 | `build_motion_catalog_ui_projection.py` 对 `displayTitle` 只做商标 sanitize，不做翻译 |
| 2c | 自动推荐每次都是同样三项 | `recommendMotionPartsForBeat` 定完分类后 `candidates.slice(0, 3)`，与段落内容无关 |
| 2e | 卡片上三处冗余元素 | 标题硬编码「（134 项）」、重复的分类 Tag、App 内无法跳转的「有官方在线预览」 |

三条共同的病根是同一个：**投影层只做了"不违规"，没做"看得懂"**。商标 sanitize 把
`Apple Money Count` 变成 `星云科技 Money Count`——不违反品牌边界了，但对不看英文的运营
人员来说和没做一样。分类兜底同理：路由到一个分类就算"推荐"完成，没人问过"这三项和这段
文字有关系吗"。

## 交付一：134 个显式中文名

`DISPLAY_TITLES` 是逐项手写的 id → 中文名映射表，134 条，写在生成脚本里而不是契约里——
契约是生成物，手写内容必须在生成器中单点维护。名字表达"这个零件做什么"，不是英文直译：

| id | 原显示名 | 现显示名 |
| --- | --- | --- |
| `caption-clip-wipe` | Clip Wipe | 字幕擦除显现 |
| `caption-pill-karaoke` | Pill Karaoke | 胶囊逐字跟读字幕 |
| `apple-money-count` | 星云科技 Money Count | 金额数字滚动 |
| `code-snippet-apple-terminal-red-sands` | Code Snippet - 星云科技 Terminal Red Sands | 代码片段·终端红沙棕 |
| `lt-stack-bars` | Lower Third — Stack Bars | 身份条·堆叠条块 |

三条硬约束由生成器和门禁各自独立强制：**无任何 ASCII 字母**、**134 个互不重复**、
**不含锁定的商标指示词形态**。缺一项名、多一项名、名字与锁定目录 id 对不上，都在
构建期抛错。

BM-13 overlay 的 `trademarkReplacements` 在本投影中随之失效（没有英文可替换了），
`OVERLAY_PATH` 与 `_sanitize_title` 一并从生成器删除。overlay 本身仍由
`build_motion_catalog_release.py` / `check_motion_catalog_release.py` 消费，用于真实
HTML 与素材替换，未受影响。

## 交付二：自动推荐改为内容打分

旧规则两段式：正则路由到一个中文分类 → 取该分类前三项。第二段与段落内容完全无关，
所以同一分类下永远是同样三项，134 项中其余项除手动挑选外永不出现。

新规则对每个零件算一个分数，取前三：

| 信号 | 权重 | 说明 |
| --- | ---: | --- |
| 分类关键词命中数 | ×6 | 11 条主题规则，中文正则 + 英文/技术词整词匹配 |
| 段落 ASCII 词命中零件 id 分词 | ×4 | 例如段落写 `terminal` 命中终端配色系列 |
| 段落与零件中文名的共享二元组 | ×3 | 中文名本身成了可检索信号 |
| 段落与适用说明的共享二元组 | ×2 | 同分类内均匀，主要拉开跨分类差距 |

无任何信号时给「文字效果 / 转场 / 画面内复杂效果」很小的基线分，避免把毫无线索的
段落丢进终端配色这类专用零件。同分用 `FNV-1a(beatIndex + 段落文字 + 零件 id)` 打破，
所以**同输入永远同输出**（既有确定性测试保持通过），不同段落、不同段序则会轮换到不同零件。

关键词表补齐了英文与技术词（`api`/`sdk`/`ragflow`/`rag`/`llm`/`docker`/`terminal` 等），
旧表 9 条纯中文正则对这类文字一条都命中不了。

实测（本次真实运行输出）：

```text
增长看得见 本周销售数据增长          => 数据图表动画 / 世界地图 / 西班牙地图
本段讲解 RAGFlow 的 API 与 SDK 接入  => 代码改动对比 / 代码变形过渡 / 代码片段·现代深色
介绍这位嘉宾的身份与职位             => 身份条·简洁横条 / 身份条·侧边竖线 / 身份条·堆叠条块
用流程图说明三个步骤                 => 横向流程图 / 纵向流程图 / 反色叠加文字
台词逐字跟读字幕                     => 胶囊逐字跟读字幕 / 粒子爆开字幕 / 字重变化字幕
今天天气不错                         => 微光扫过文字 / 立体空间转场组 / 电影感推近转场
```

## 交付三：卡片清理与 `officialPreview` 下架

三处删除：标题的「（134 项）」、`<Tag color="blue">{part.category}</Tag>`、
`有官方在线预览` Tag。第 138 行「来源：文字已本地化」用户未明确要求，保留不动。

按代码删除规范排查 `officialPreview`：删掉那个 Tag 后，**前端再无任何消费方**，
因此把它从 `MotionPartOption` 接口、生成器 item 字段、UI 投影契约中一并删除，
门禁增加"投影不得再带该字段"的断言。

治理层保留：`contracts/quality/motion-catalog.v1.json` 的逐项 `officialPreview` 与
`check_motion_catalog.py` 的 `officialPreview: 100` 计数不动——那是 BM-11 权利审计的
事实源，与前端展示无关。投影 `counts` 中的 `officialPreview: 100` 也保留，因为
`counts` 的既有不变式是"逐字镜像锁定目录的 counts"，破坏它会引入第二套计数事实源。

「（134 项）」同时是 `user-facing-terminology.v1.json` 的 `conceptDistinctions`
必需文案，已同步改为「动效零件目录」，否则品牌门禁会因找不到该字符串而变红。

## RED

Python 侧（`scripts/test_motion_catalog_ui_projection.py`）：

```text
AssertionError: part name is not fully localized: 'App Showcase'
```

以及去掉 `officialPreview` 后的封闭键集：

```text
AssertionError: app-showcase: closed key set
```

TypeScript 侧（`npx vitest run src/features/video-studio`）：

```text
Test Files  3 failed | 2 passed (5)
     Tests  9 failed | 28 passed (37)

AssertionError: expected 'App Showcase' not to match /[A-Za-z]/
AssertionError: expected '文字效果' to be '代码演示' // Object.is equality
AssertionError: expected [ 'caption-clip-wipe', …(2) ] to include 'caption-pill-karaoke'
TestingLibraryElementError: Unable to find an element with the text: 数据图表动画
```

第三条正是用户报告的现象本身：旧实现对任何字幕段落都回答 `caption-clip-wipe /
caption-editorial-emphasis / caption-emoji-pop`。

**第一轮推荐测试写弱了，实测发现后重写。** 最初三条推荐测试里有两条（"按分类路由"、
"不同段落不同结果"）对旧实现**也是绿的**——旧实现路由本来就正确，坏的是路由之后。
补了三条对内容敏感、旧实现必红的断言（零件中文名与段落词匹配、同分类下两段不塌缩成同一答案）
才拿到有效 RED。

## GREEN

```text
backend/.venv/bin/python scripts/test_motion_catalog_ui_projection.py
  BM-15 motion catalog ui projection tests passed                                   EXIT=0
backend/.venv/bin/python scripts/check_motion_catalog_ui_projection.py
  check passed: 134 items, 11 categories, labels closed, names fully localized,
  no indicator or URL leakage                                                       EXIT=0
backend/.venv/bin/python scripts/check_user_facing_branding.py
  passed (50 frontend, 247 native files)                                            EXIT=0
backend/.venv/bin/python scripts/check_third_party_notice_ui_projection.py
  passed: 2 upstream projects, 9 borrowed packages, no leakage                      EXIT=0
backend/.venv/bin/python scripts/check_motion_catalog.py
  valid: 134 items (109 blocks / 25 components, 100 official previews)               EXIT=0
backend/.venv/bin/python scripts/test_motion_catalog.py
  tests passed: app-showcase tamper matrix rejected                                 EXIT=0

cd frontend && npx vitest run src/features/video-studio/motion-parts-catalog.test.ts \
                              src/features/video-studio/MotionPartsCatalog.test.tsx
  Test Files 2 passed (2)   Tests 18 passed (18)                                    EXIT=0
cd frontend && npx eslint .                                                          EXIT=0
cd frontend && npx tsc -p <排除并发任务文件的 tsconfig>                                EXIT=0
```

用例条数核对（防"测试没被收集"）：`motion-parts-catalog.test.ts` 4 → 10 条，
`MotionPartsCatalog.test.tsx` 6 → 8 条，全前端 415 条。Python 侧篡改矩阵加了
`EXECUTED_SCENARIOS` 计数断言——第一次跑就抓到我把期望值写成 10（实为 9）。

## 并发占用导致的非本任务红灯（如实登记）

本次工作期间同一工作树上有另一条任务线在改"段数与每段时长"（发现清单 2b）。
全量前端测试因此有 3 条红：

```text
FAIL src/features/video-studio/motion-duration.test.ts（模块 ./motion-duration 尚不存在）
FAIL VideoStudio.test.tsx > lets the user choose the beat count and the seconds each beat runs
FAIL VideoStudio.test.tsx > refuses to submit a beat count and length whose product exceeds the render budget
FAIL material-video-studio-gateway.test.ts > refuses a storyboard outside the declared duration budget
Test Files 3 failed | 55 passed (58)   Tests 3 failed | 412 passed (415)
```

`npx tsc -b --force` 同样只报这一条：
`motion-duration.test.ts(9,8): error TS2307: Cannot find module './motion-duration'`。
排除该文件后类型检查退出码 0，**本任务涉及的文件零错误**。

另有一条**先于本次改动就存在**的红灯：`scripts/test_user_facing_branding.py` 的
"clean synthetic tree" 场景失败于 `configured scan root does not exist:
frontend/src-tauri/src`——合成目录树没建 `nativeScan` 的这个根。已用
`git show HEAD:scripts/test_user_facing_branding.py` 取出改动前版本实跑复现，
失败信息完全一致，与本任务无关。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 某个 id 没有中文名 | 构建期 `ProjectionError` | 直测（临时删表项） |
| 中文名里混入 ASCII 字母 | 构建期拒绝 + 门禁独立拒绝 | 直测（生成器与门禁各测一次） |
| 两个零件重名 | 构建期拒绝 + 门禁独立拒绝 | 直测 |
| 名字表比锁定目录多一项 / 少一项 | 构建期拒绝 | 直测 |
| 中文名意外含锁定商标指示词形态 | 构建期拒绝 | 逐条扫 release lock 全部 forms |
| 源契约改了、投影没重新生成 | 门禁漂移检测变红 | 既有 |
| 投影重新长出 `officialPreview` | 门禁拒绝（漂移检测放行时的第二道） | 门禁断言 |
| 段落一个关键词都不命中 | 落到通用分类基线并按段落轮换，不再固定三项 | 单测 |
| 同段落同段序重复调用 | 结果完全一致 | 既有确定性单测 |
| 主题规则加入含正则元字符的词 | 模块加载即抛错 | `PLAIN_TERM` 守卫 |

## 真实边界

1. **没有做正式 Tauri App 的用户路径验收**。按项目规则，UI Harness（Vitest + Testing
   Library）只能证明 React 业务交互，不能替代正式包验收。本次修改的是用户在正式包里
   亲眼看到的文案与推荐结果，**必须**有一次真实 App 走查才算闭环，本会话没做。
2. **`e2e-tauri` 两个 spec 已同步改文案锚点但未运行**（`动效零件目录（134 项）` →
   `动效零件目录`，`Data Chart` → `数据图表动画`，删掉 `有官方在线预览` 断言）。
   桌面 E2E 需要真实 App 环境，不在本次范围。
3. **134 个中文名是人工判断，不是机器翻译**，其中若干项的原始语义只能从 id、分类和
   资产文件名推断（详见「遗留项」）。这些名字**没有经过用户确认**。
4. **推荐质量受锁定分类本身限制**。例如"续费驱动增长 客户持续选择新版"命中「产品与案例
   展示」，而该分类里混着 `north-korea-locked-down`（封锁地区地图叙事）这类演示场景，
   推荐结果会显得突兀。这是 BM-11 锁定分类的内容问题，不是打分逻辑问题，本次没动分类。
5. **Windows 侧未核对**。中文名的显示宽度、卡片换行在 Windows 上未验证。

## 清理

- 临时探针测试 `zz-sanity-probe.test.ts` 写入后立即删除，`git status` 已确认无残留；
- 临时 `frontend/.tsconfig.scope-check.json` 用完即删；
- 未启动 App、浏览器、Playwright、Tauri E2E，无进程残留；
- 未触碰 `.local/`（正在运行的试用会话与 ffmpeg 构建）；
- 未 `git add` / `git commit`，改动交主会话统一把关。

## 文档

- `scripts/build_motion_catalog_ui_projection.py`（134 项中文名表、本地化校验，删除 overlay 依赖与 sanitize）
- `scripts/check_motion_catalog_ui_projection.py`（`require_localized_titles` + 禁止 `officialPreview`）
- `scripts/test_motion_catalog_ui_projection.py`（本地化断言、篡改矩阵 +3、场景计数守卫、生成器与门禁直测）
- `scripts/test_user_facing_branding.py`（内联投影 fixture 跟随新形状）
- `contracts/video/motion-catalog-ui.v1.json`（重新生成）
- `contracts/quality/user-facing-terminology.v1.json`（`requiredCopy` 去掉项数）
- `frontend/src/features/video-studio/motion-parts-catalog.ts`（接口字段、加载期校验、推荐重写）
- `frontend/src/features/video-studio/MotionPartsCatalog.tsx`（三处删除）
- `frontend/src/features/video-studio/motion-parts-catalog.test.ts`（4 → 10 条）
- `frontend/src/features/video-studio/MotionPartsCatalog.test.tsx`（6 → 8 条）
- `frontend/src/features/video-studio/VideoStudio.test.tsx`（卡片锚点改中文名）
- `frontend/e2e-tauri/motion-parts-catalog.spec.ts`、`frontend/e2e-tauri/plain-language-comprehension.spec.ts`（文案锚点）
- `docs/development/dogfood-findings-20260726.md`（2c/2d/2e 登记修复）
- 本文件

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 正式 Tauri App 用户路径验收（看中文名、点自动推荐） | 未做 | 需真实包环境 |
| `e2e-tauri` 两个 spec 实跑 | 未做 | 桌面 E2E 队列 |
| 5 个语义把握不足的中文名待确认 | 未确认 | 见下表 |
| Windows 侧中文名显示核对 | 未做 | Windows 验收队列 |
| 「来源：文字已本地化」是否也删 | 待用户决策 | 发现清单 2e |

### 语义把握不足的 5 个名字

| id | 现中文名 | 不确定的原因 |
| --- | --- | --- |
| `north-korea-locked-down` | 封锁地区地图叙事 | 上游是特定国家的时事演示（附 `korea-map.png`），既要避开地缘政治表述又要说清用途，只能取"地图 + 叙事"的中性描述；实际画面未渲染确认 |
| `code-snippet-monokai` | 代码片段·暗底亮彩 | Monokai 是配色方案专名，无通用中文译名，只能按"深灰底 + 高饱和多色关键字"的观感描述，可能与实际渲染有出入 |
| `code-snippet-solarized-light` | 代码片段·米黄柔光 | 同上，Solarized 是专名；按浅色版的米黄底描述，未逐个渲染核对 |
| `vpn-youtube-spot` | 网络工具广告插播 | 上游是"视频平台上的 VPN 广告片"，VPN 直译不合适、原样保留又是 ASCII，退成"网络工具"后语义变宽 |
| `vfx-iphone-device` | 便携设备与工作站立体展示 | 原名是两类设备的 3D 展示，商标替换词是「便携设备」「便携工作站」，拼起来冗长；缩成"工作站"后与替换词表不完全一致 |

这 5 项都不违反硬约束（全中文、不重复、无商标词），但**用词准确性没有实物画面佐证**，
建议在正式 App 验收时逐项对照渲染结果确认。
