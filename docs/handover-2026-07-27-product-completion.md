# 交接文档 · 2026-07-27 下午这一轮（产品补全线）

> 写给换台电脑接着干的人。**先读第五节**——那一节讲的是「只在公司这台 Mac 上、不在 Git 里」的东西，
> 不先处理它，第六节列的下一步全部跑不起来。

分支 `feature-audit`，已推 origin。上一份交接 `docs/handover-2026-07-27.md` 讲的是凌晨那轮出包，
与本轮无关，两份都留着。

---

## 一、这一轮到底在干什么

产品负责人 07-27 下午定的优先级：**一键生成视频最高**。于是开了这条线，目标是把
「AI 一句话出片」从「能出片」推到「出的是产品想要的片子」。

新建两份文档，它们是这条线的全部事实源：

- `docs/feature-completeness-audit-2026-07-27.md` —— 按负责人口述的八条业务链路逐条核对**真实代码**
  得出的盘点。结论是：今天能跑通的只有一条抖音单平台、人工触发的链路；
- `docs/product-completion-roadmap.md` —— P0～P6 补全路线图，**第 0 节是 PC 系列任务台账**，
  状态只活在那张表里，每个任务的证据写进 `docs/development/PC-nn.md`。

## 二、路线：A（分段渲染 + 拼接），负责人已定

零件不是能填参数的组件——这是打开 `vendor/hyperframes` 源码后推翻的第一个前提。
每个零件是一份**完整独立的 1920×1080 HTML 合成文档**，自带 GSAP 时间轴。所以：

```
模型编排（输出 JSON，不写 HTML）
  → 本机 TTS 合成，量出每段语音真实秒数        PC-07 ✅
  → 本机排时间轴：镜头时长 = max(语音, 动效)     PC-08
  → 本机按槽位表把文案注入零件副本               PC-03
  → 渲染前实测溢出，超了回抛让模型改短           PC-14
  → 每段按零件原生尺寸渲成 mp4 片段              PC-05
  → ffmpeg 拼接 + 配音轨                       PC-06
```

**A 顺带解掉 20 秒片长上限**（单段仍受 600 帧限制，总长不受限）。
同屏叠加（数据图表 + 下三分之一同框）解不了，那要实现上游的 `data-composition-src` 子合成机制，
留作 B，两者不冲突。

## 三、这一轮被实测推翻的三个设想——**这是最值钱的部分，别重犯**

1. **「零件可以填参数」** → 134 个里只有 6 个带 `params`，12 个参数全是颜色。125 个零件的文案
   写死在 HTML 里。所以必须本机做结构化文案槽替换；
2. **「先小后大 = 零件只当点缀不换文案」** → 30 个转场零件全是**演示页**，原样渲染出来是
   `SCENE A | SCENE B / Glitch / Prompt / use glitch shader transition`。而它们恰恰是原计划里
   「最安全、可原样用」的那批；
3. **「按可替换文案槽数量分批」** → 那份统计是正则扫全文件数出来的，把 15 个字幕零件排进了
   最容易的一批，**而它们 body 里一段可寻址文案都没有**，扫到的是 `<title>`。
   `caption-kinetic-slam` 的文案是 JS 里一个 28 词数组，每词自带 start/end。

**共同教训：判据一律是打开源码实测，不是读契约、读映射表、或按名字推断。**
第 3 条尤其——粗测（正则 54/47/23/10）与真解析器（52/42/28/12）的差距本身就是不该用粗测下结论的证据。

## 四、台账现状

```
PC-01  零件目录补齐可编排字段     ✅   补回上游 description/tags/duration/dimensions
PC-02  零件可用性分级             ✅   按文案存放形态分 first 36 / second 40 / deferred 58
PC-04  模型看得见目录             ✅   134 个裸 id → 76 个可用零件的真目录
PC-07  TTS 接入与时长测量         ✅   百炼 qwen3-tts-instruct-flash，真实 2.080/7.120/4.320 秒
PC-11  本台账门禁                 ✅   共用判定抽进 scripts/roadmap_ledger.py
PC-13  中文字体 + 拉丁补齐        🔍   见下方第四节之二
PC-03  第一批槽位表               ⬜   ← 下一步，依赖 PC-13
PC-05/06/08/09/10/12/14/15        ⬜   PC-03 之后；PC-15 是字体冗余整合
```

### 之二、字体线（PC-13，`f62a8ac`）已落地，但只能标 🔍

由子代理独立完成，我逐条复核过——**不是采信它的自述**：`check_third_party_sources.py` 退出 0
（submodule 未被碰）、八道相关门禁全绿、后端 3282 条单测通过、篡改验证真的能判红。

做成了什么：

```
净增 7,991,372 B（7.62 MB，占 dmg +1.63%）
  ├─ builtFonts   7,782,072 B   Noto Sans SC 可变 woff2，一个文件覆盖 7 个字重
  └─ artifacts      209,300 B   18 个新拉丁字体文件

三类处置（134 零件 / 40 家族 / 71 个 (family,weight)）
  1. OFL 且已在包内  18 家族 108 零件   102,884 B   ← 许可证零成本，但字重不是
  2. OFL 不在包内     4 家族   7 零件   106,416 B
  3. 不可再分发      15 家族  48 零件         0 B   ← 映射到包内已有字体
```

**必须知道的三件：**

1. **Chromium 先按字重分桶，再解析 `unicode-range`。** 所以 CJK face 必须按零件实际请求的
   每个 `(family, weight)` 逐条声明；只声明 400 或写 `font-weight: 100 900` 都不生效，
   **字会掉回系统字体**。漏一条的表现是「大部分零件中文正常、少数莫名其妙不对」；
2. **`fontTools` 的 `recalcTimestamp` 默认 True**，会把构建时钟写进 `head.modified`，
   同一输入两次产出不同字节（7,782,644 vs 7,782,216）。关掉两个 recalc 后三次输出同一个
   7,782,072 字节文件。**不发现这条，锁进去的摘要会在任何人重建时变红，而且长得和被篡改一模一样**；
3. **24 个 `code-snippet-*` 从此和上游官网预览长得不一样**——它们仿的是 VS Code / Apple Terminal，
   官网用 Menlo/SF Mono，而那些不可再分发，已映射到 JetBrains Mono。**这是许可证决定的，不可逆。**

**为什么不是 ✅**：渲染期注入还没接进执行器。一条 grep 就能复核——

```
$ grep -rn "part_font_css" --include=*.py --include=*.rs --include=*.ts .
scripts/motion_part_typography.py:285      def part_font_css(...)   ← 定义
scripts/test_motion_part_typography.py:155                          ← 唯一调用方
```

`backend/src/` 底下一次都没出现，**执行器根本不知道有这个函数**。所以用户点「一句话出片」，
渲染出来的中文仍旧掉系统字体，和今天早上一模一样。

`part_font_css()` 能生成、也用它渲出了真实产物
（`lt-bold-block` 22 个字形全部 `isCustomFont: true`，第一轮对照组是 11 个字形全部 PingFang SC），
但执行器还没有在写 RenderJob 工作区副本时调用它——**那个写副本的点正是 PC-03 要建的文案替换点**。
加上 Windows 未复测。所以字体已进包、机制已验，但从正式 App 走一遍还看不到效果。

台账自洽由 `python3 scripts/check_product_completion_roadmap.py` 守着，**改完台账跑一次**。

## 五、只在公司这台 Mac 上、不在 Git 里的东西（六类）

### 1. 密钥 `.local/secrets/`（必需，最先处理）

```
bailian-model.json          百炼 key —— PC-04 与 PC-07 全靠它
aliyun-video-editing.json   阿里云剪辑
```

百炼那份的**内容在 Git 里**：`docs/credentials-bailian-model.md`（负责人 07-23 明确要求提交的
跨设备共享例外，仓库必须保持 private）。抄成 `.local/secrets/bailian-model.json` 即可。

### 2. 构建缓存 `~/Library/Caches/automation-tool-build/`（539 MB，会自动重建但要时间）

```
material-video-worker  357M    motion-video-worker  108M
media-toolchain         42M    subtitle-fonts        32M
```

`media-toolchain/bin/ffprobe` 是 PC-07 量时长用的那把，**PC-05/PC-06 也要用**。
换机后第一次跑会卡在下载重建这一步。

### 3. `.local/` 产物（4.1 GB，全部可重建，但有一处引用会断）

```
release                        3.4G   出好的 dmg
desktop-e2e                    502M   桌面验收用的内置 Chromium 与执行器包
embedded-browser-video-studio  173M   ← 字体线的实测证据都在这里
offline-motion-deps             61M   离线依赖暂存
```

**`docs/development/PC-13.md` 里引用了 `pc-13-evidence/` 下的 mp4、PNG 和 JSON——
换机后那些引用指向不存在的文件。** 需要复核那些证据时得在原机器上看，或重跑探针。

### 4. Apple 签名与公证（只有出包才需要）

```
~/Documents/at-tools-credentials/        凭证包，内含 setup-on-new-mac.sh
钥匙串身份  Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
公证凭据    notarytool keychain-profile "at-tools-notary"
```

PC-03 到 PC-08 都不需要出包，所以这一类可以先不管。

### 5. `backend/.venv`（345 M）与 `frontend/node_modules`（450 M）

`uv sync` / `npm ci` 重建。**venv 必须建在 standalone Python 上，不能用 Homebrew 的**——
Homebrew 是 framework 布局，出包会死在执行器签名（`bundle format is ambiguous`），
而报错完全不指向解释器。见 `docs/macos-release-machine-setup.md`。

### 6. Playwright 的 Chromium `~/Library/Caches/ms-playwright/`

`chromium-1228` / `1217` / `headless_shell` 三份。缓存过期会把桌面验收全堵死，
现象与处置见上一份交接文档第二节。

## 六、下一步

**PC-03（第一批槽位表）**，前置是 PC-13 把字体定下来。要做的：

1. 从 first 批 36 个零件的 **419 个可见文本节点里筛**出真正该开的槽。
   长尾（`ui-3d-reveal` 136 个、`vpn-youtube-spot` 50 个、`vfx-portal` 45 个）是**假 UI 的布景文字，
   不是该替换的文案**——26 个零件 ≤5 个节点，那些才是理想形态；
2. 定位用「**文本节点序号 + 原文逐字**」双锚，不用 CSS 选择器。序号由已经落地的
   `automation_tool.executor.motion_authoring.part_document` 提供，**它随 Executor 一起打包**，
   因为冻结时数节点的和渲染时数节点的必须是同一份代码；
3. 每槽记的是**容器可用像素宽度 + 字号**，不是字数——同样 9 个字符，汉字约 9em、`iiiiiiiii` 约 2.3em、
   `WWWWWWWWW` 约 12em。字数只作为给模型的软预算；
4. 替换发生在**运行期写进 RenderJob 工作区的副本**，只读发布树不变。这条沿用 BM-13 的 overlay 思路
   （那边 121 条商标文本替换已经跑通，`apple` → `星云科技`）。

**PC-03 同时是字体线的收口点**：执行器写 RenderJob 工作区副本时要调 `part_font_css()`
把字体 CSS 注进去，那正是文案替换发生的地方。两件事在同一处落地，别拆开做。

再往后 PC-05（单零件渲染）→ PC-06（拼接）→ PC-08（时间轴主宰）。

新登记的 PC-15：安装包现在带**两份**中文字体（素材成片 Worker 里的思源黑体 OTF 33.4 MB
＋动效线新增的 7.42 MB），41 MB 冗余。PC-13 不复用前者的理由成立，但值得单独评估。

## 七、并行开发：一条今天付过代价的规则（已写进 CLAUDE.md §8.2）

**派会写文件的子代理之前，先 `python3 scripts/new_worktree.py <名称>` 建树，再把那棵树派下去。**
不要用 Agent 工具自带的 worktree 隔离——它做的是朴素 `git worktree add`，留下空的 `vendor/`，
而任何要读 `vendor/hyperframes` 的任务在那棵树里直接跑不起来。

今天没这么做，代价是：主线跑全量测试报出的红需要人工判归属（实际是子代理改 `pyproject.toml`
的 `default-groups` 撞上既有断言）；两边提交都不能 `git add -A`。发现时它已有 11 个文件在途，
中途搬迁风险高于继续并行，所以当天没搬。

另有一条 UI 线在 `codex/ui-redesign`（最后提交 `c4d0d14`，已停止）。
实测 `git merge-tree --write-tree feature-audit codex/ui-redesign` 退出 0，
**改动文件交集为空**，合并无冲突。

## 八、几个不显眼但会咬人的地方

1. **PyInstaller 规格里的 contracts 是手抄清单。** 新读一份契约忘了加进
   `backend/automation-tool-executor.spec`，结果是开发机全绿、用户装完一句话出片直接起不来——
   因为测试跑在仓库检出里那份文件本来就在。今天补了派生式门禁
   （`test_the_spec_packages_every_contract_the_authoring_agent_reads`），下次新增契约读取
   无需任何人记得加测试；
2. **新增拒绝消息必须同步登记 wire token**，否则
   `test_every_fixed_upstream_rejection_has_its_own_closed_reason_token` 会红。
   要改三处：`entry.py` 的 `_AGENT_FIXED_REJECTION_BODIES`、
   `contracts/video/motion-authoring-refusal.v1.json` 的 `fixedReasons`，以及消息本身；
3. **台账表格的单元格里不能有裸换行**——markdown 会不再把它当表格行渲染，而在 diff 里看着完全正常。
   PC-07 与 PC-13 两行都这么坏过，现在 `check_product_completion_roadmap.py` 会判红；
4. **`ruff` 对中文全角标点报 RUF001**，`agent.py` 已有 73 条同类，提交门禁不跑 ruff，这类不用追。

## 文档

- 本文件是本轮交接的唯一入口；
- 路线与决策依据在 `docs/product-completion-roadmap.md`，任务证据在 `docs/development/PC-*.md`；
- 项目规则本轮新增 §8.2（子代理隔离），此前还加过 §9.1（验收证据终态）、§9.2（打包边界）、
  §9.3（共享基础设施）。
