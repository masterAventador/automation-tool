# 交接文档（2026-07-31）——给 codex：剩余任务拆解与开工手册

> 上一手：Claude（feature-audit 分支，已全部合入 main `66904e8`）。
> 本文写给接手的 codex。**每一节都按「背景 → 具体步骤 → 涉及文件 → 测试与验收判据 → 已知的坑」拆开写，照着做即可。**
> 有不明白的先读第 0 节的必读清单，再读对应任务的 `docs/development/PC-nn.md`。

---

## 0. 开工前必读与纪律（不遵守会返工，全部有过真实事故）

### 0.1 必读文档（新会话第一件事）

按 `CLAUDE.md`（项目根）第 1 节的清单读。与本交接直接相关的最少集合：

1. `docs/product-completion-roadmap.md` —— **PC 系列唯一状态台账**（第 0 节的表）。
   当前：26 项 / ✅20 / ⬜3（PC-22、PC-23、PC-24）/ 🔍3（PC-13、PC-16、PC-21 的残留半项）；
2. 你要做的那个任务的 `docs/development/PC-nn.md` —— 唯一证据文件；
3. `docs/embedded-browser-video-studio-roadmap.md` —— 87 项专项台账（与 PC 系列分开记，互不双写）。

### 0.2 铁律（出过事故的那几条，原文在根 CLAUDE.md，这里只列最容易犯的）

- **TDD**：没有失败的测试不写生产代码。先写测试→亲眼看它红→最小实现→绿。
  修 bug 也一样：先写能暴露 bug 的红测试；
- **台账纪律**：开工先把台账行改 `🧪 RED` 或 `🚧 实现中`（同时最多一个）；
  验收过了**第一件事**改台账，然后才轮到总结汇报。改完跑
  `backend/.venv/bin/python scripts/check_product_completion_roadmap.py`——
  它检查证据文件头必须有 `> 日期：YYYY-MM-DD` 和 `> 状态：**已完成**`
  （粗体、不带 emoji、状态词要与表里 emoji 后面的词逐字一致）；
- **验收深度**：「元素可见/按钮可点/接口 200/测试 passed」都不算终态。证据必须是
  「没真跑过就写不出来」的具体物：ffprobe 读数、帧指纹、文件字节数、平台返回的 ID。
  门禁 `scripts/check_acceptance_evidence_depth.py` 会检查证据文件；
- **单一构建路径**：禁止测试构建与正式构建在「产品去哪找东西」上分叉。
  允许的差异只有三类：WebDriver 挂载、窗口可见性/日志级别、指向隔离实例的配置值；
- **批量改测试必须逐文件看 diff**：形状匹配会改变语义（断言方向插反过两次，
  tsc/lint/门禁全绿，只有肉眼看 diff 能发现）；
- **判定「跑通了」之前先确认那条命令真的会失败**：`cmd > log 2>&1; echo $?` 的
  退出码来自 echo；`tsc --noEmit -p frontend/tsconfig.json` 是空编译，正式入口是
  `tsc -b`（在 frontend 目录下 `pnpm exec tsc -b`）；
- **worktree 一律 `python3 scripts/new_worktree.py <名称> [提交]` 建**（默认基于
  origin/main，要当前代码显式传 HEAD），建完先跑一条最便宜的验收当冒烟；
- **本机测试全部无头/后台**，结束必须清理本次启动的浏览器/容器/服务，只清
  `automation-tool` 前缀的隔离实例。

### 0.3 常用命令速查

```bash
# 后端全量（约 47s，3399 条）
backend/.venv/bin/python -m pytest backend/tests/unit -q
# authoring agent 套件（111 条）
backend/.venv/bin/python -m pytest scripts/test_motion_authoring_agent.py -q
# Rust 全量（在 frontend/src-tauri 下）
cargo test
# 前端类型 + 单测（在 frontend 下）
pnpm exec tsc -b && pnpm exec vitest run
# 台账/证据/文案三门禁
backend/.venv/bin/python scripts/check_product_completion_roadmap.py
backend/.venv/bin/python scripts/check_acceptance_evidence_depth.py
backend/.venv/bin/python scripts/check_user_facing_branding.py
# 一句话链路的真实验收（真模型+真TTS+真渲染，约 10-15 分钟，会起隔离 Postgres）
backend/.venv/bin/python scripts/run_t36_acceptance.py
```

### 0.4 封闭词表机制（改 authoring 拒绝语必踩，先懂再动）

authoring 子进程的每一条固定拒绝语都要能翻译成专属 wire token：

1. agent.py / entry.py 里新增 `_reject("某句话")` →
   `backend/tests/unit/executor/test_motion_authoring_entry.py::test_every_fixed_upstream_rejection_has_its_own_closed_reason_token`
   会红（它用 AST 扫源码里的字面量）；
2. 补两处：entry.py 的 `_AGENT_FIXED_REJECTION_BODIES`（agent 语）或
   `_ENTRY_REASON_TOKENS`（entry 语），以及
   `contracts/video/motion-authoring-refusal.v1.json` 的 `fixedReasons`
   （**必须保持字典序**，Rust 侧校验 strictly sorted）；
3. 决定 token 归哪类：真是「用户句子的问题」→ 留在 refusal（不进
   `nonRefusalOutcomes`）；App 拼错请求 → `app_request_invalid`；我们自己的接线
   缺陷 → `executor_defect`；到模型/TTS 服务的传输失败 → `model_transport_failed`；
   安装损坏 → `installation_damaged`。判据：答成 refusal 会让用户去改一个
   没人读过的句子，就不许答成 refusal。

### 0.5 打包边界机制（改 executor 会 import 的东西必踩）

- 执行器冻结包 `excludes=[]`，import 图就是打包边界；agent/entry 读的每份契约
  必须列进 `backend/automation-tool-executor.spec` 的
  `motion_authoring_resources`，否则
  `test_pyinstaller_bundle_contract.py` 红（这门禁刚抓过一次真漏包）；
- `automation_tool/executor/__init__.py` 不许 re-export 测试替身
  （`Fake*/Mock*/Stub*/Dummy*`），`test_shipped_package_boundary.py` 守着。

---

## 1. 刚合入 main 的东西（你接手的起点）

merge commit `66904e8`，一句话成片链路现在的完整形态：

```
用户一句话 → Rust submit_motion_video_brief
  → 冻结执行器 --author-motion（stdin 带 workspace/catalogRoot/
     browserExecutable/ffprobeExecutable/model 凭据）
  → 真模型写 design/script/storyboard（script.beats 与 storyboard.beats 1:1，
     第 i 条是第 i 拍的旁白词）
  → 逐拍真 TTS 合成 narration/<beat_id>.wav + ffprobe 测真秒数
  → plan_film 按 max(语音, 动效) 排片（语音臂已收口，PC-08）
  → 装配零件工作副本（PC-03 槽替换 + PC-13 字体注入）
  → 包内 Chromium 同会话量溢出（PC-14：超出量之差 + 容差；舞台逃逸另测）
     超了回抛模型改短一轮，仍超即拒
  → 逐镜真渲染 → 拼接 → 旁白按镜头起点 adelay 混音（-c:v copy）
  → 有声成片（T36 门禁现在断言音轨存在）
```

最近一条真实验收成片：h264 1920×1080 / 36.07s / 1082 帧 + aac 旁白 33.83s
（brief 声明 600 帧被语音拉长到 1082——语音主宰时间轴）。

---

## 2. 剩余任务拆解（按建议顺序）

### [C1] PC-22 智能素材成片改走真实内嵌（⬜，建议先做）

**背景**：产品负责人 2026-07-29 定「坚持内嵌」。现状两头都不通：
`OperationsWorkspace` 是唯一渲染 `VideoStudio` 的地方且无条件传 `embedded`，
「打开完整制作界面」整支是不可达死代码；embedded 支自己显示「当前真实内嵌服务
尚未接入」。IM-05 验收为此保持红——**不许改那条 spec 迁就现状**。

**已经替你查明的关键事实**：`material_video_studio::open`（Rust）把两件事捆在
一起——①启动带 WebUI 的素材 worker（包内可执行文件+工具链解析+工作区创建）、
②新建并显示独立 Tauri 窗口。内嵌要①不要②，**不能整块删 `open()`**，要拆：
服务那半保留复用，窗口那半随非 embedded 分支删。

**步骤**：
1. 读 `frontend/src-tauri/src/material_video_studio.rs` 的 `open`，拆出
   `start_service()`（返回 WebUI 的 loopback 地址）与窗口逻辑；
2. 新增 Tauri command（如 `material_studio_service_address`）只做启动服务+
   返回地址；TDD：cargo test 先红；
3. `frontend/src/features/video-studio/VideoStudio.tsx` 的 embedded 支：
   调新 command 拿地址，iframe/webview 加载真实 WebUI（注意 CSP，
   `frontend/src-tauri/tauri.conf.json` 的 security 段可能要给 loopback 开
   frame-src——最小放行，只 loopback + 随机端口模式）；
4. 按代码删除规范清理死代码：非 embedded 分支、`OPEN_ERRORS`、
   `OPEN_STUDIO_HINT_ID`、`opening`/`openMessage` 状态与相关测试（先 Grep
   再删，删完跑 vitest + tsc -b）；
5. IM-05（`scripts/run_im_05_acceptance.py`）按内嵌路径重写并跑绿。

**判据**：正式 App 的内嵌页面里能真实驱动素材成片（真 worker、真 WebUI、
真出片）；IM-05 通过；死代码清干净。

**坑**：worker 服务生命周期——独立窗口时代窗口关闭即停服务；内嵌后要想清楚
何时停（页面卸载？App 退出？），别让 worker 常驻泄漏（本地服务管理规范）。

### [C2] PC-24 运行时取数零件的数据供给通道（⬜）

**背景**：6 个零件（spain-map、us-map ×3、world-map、vfx-iphone-device）运行时
`fetch()` 本地数据/3D 模型，file:// 下被 Chromium CORS 拒，永远渲不出。
它们目前躺在渲染豁免契约里
（`contracts/quality/motion-catalog-standalone-render-exclusions.v1.json`，
class 见 PC-21 §18.6 D 类）。

**方向（二选一，先做实验再定）**：
- A 构建期内联：把 fetch 的目标（GeoJSON/GLB）在 `build_offline_motion_catalog.py`
  阶段改写成内联 `<script type="application/json">` 或 base64——**只允许在构建
  产出的发布树上做变换，不许改 vendor submodule**（第三方边界规则）；GLB 几 MB
  的 base64 会膨胀 33%，先量大小再决定；
- B 随机 loopback 供给：渲染 worker 起随机端口静态服务只服务发布树目录，威胁
  模型对齐 `contracts/security/embedded-browser-video-threat-model.v1.json` 的
  loopback Worker 边界（默认拒绝 + 失败矩阵）。改动落在
  `workers/motion_composition/worker.mjs` 与沙箱白名单。
- **明确不采用** `--allow-file-access-from-files`（放宽路径泄漏边界，威胁模型禁）。

**判据**：6 项从豁免契约划掉（豁免清单卫生门禁
`scripts/test_motion_catalog_render_exclusions.py` 会因「豁免了不存在的理由」红，
正好当 RED）；`scripts/run_bm_16_acceptance.py` 的全量 sweep 里 6 项渲绿
（变成 115 rendered / 19 excluded）。

**坑**：sweep 很贵（134 项 × 真渲染），开发时先单渲这 6 项（bm_16 里的
`_render_once` 可复用）；豁免契约改动要同步 `run_bm_16_acceptance.py` 里
`item sweep` 的计数断言。

### [C3] PC-23 d6_10 收敛验收并入受控执行器编排（⬜）

**背景**：d6_10（任务收敛）的旧编排前提被生产 App 自带 sidecar 打破：runner 后
启的 formal Executor 不再是唯一执行器，命令 63ms 被 sidecar 秒拒，「忙观察」
「收敛」两阶段都不成立。方向已定（产品负责人 2026-07-29）：**并入 h8_16f 式
受控执行器包**——sidecar 即 formal，受控页面拖住发现让忙窗口真实可控。

**步骤**：
1. 读 `docs/development/PC-21.md` §20（完整定性）与
   `scripts/run_h8_16f_acceptance.py`（受控执行器包的做法）；
2. 改 `scripts/run_d6_10_acceptance.py`：不再自己起 formal Executor，改用
   h8_16f 的受控包机制（受控测试页面让发现动作可人为拖住）；
3. 忙语义三层确定性覆盖（仓储 pytest / Rust 423 映射 cargo test / 组件测试）
   不动，本任务只补「真实 App UI 亲眼看到忙」那一层。

**判据**：d6_10 在真实 App 上跑绿：忙窗口出现（UI 可见 423/忙态）→ 受控放行 →
收敛完成；台账 PC-23 → ✅。

**坑**：d6_10 的日志基建刚修过（wdio 输出落文件、每个失败路径尾 60 行），
别退化；受控页面属于测试驱动挂载（允许差异第 1 类），不许进正式包。

### [C4] PC-16 残余：Windows 正式包（🔍，需要 Windows 机）

macOS 半程已全闭环（bundle 用户路径出片用到零件，见 PC-16.md §8.6）。剩：
1. Windows 机上 `scripts/build_release_package.py` 对应的 Windows 流程出包
   （`tauri.windows-candidate.conf.json`；Chromium 归档 `chrome-win64.zip` 需
   先下载到 `.local/eb-04-windows/`）；
2. 验「零件在包里」（134 个 HTML + manifest 逐文件摘要）与「缺了被拒」
   （clone 一份逐个弄坏，参照 PC-16.md §8.3 的三形态）;
3. 参照 `run_pc_16_macos_package_acceptance.py` 写 Windows 版 bundle 用户路径
   验收（或先把「包内有+缺了拒」做完标注残余）。
4. PC-13 的 🔍 同机收口：中文字体在 Windows 包里的逐文件核对（PC-13.md 记着
   只能标待验收的原因）。PC-21 的 🔍 残留半项也在其行内写明（Windows 侧 spec）。

**坑**：出包机前置见 PC-16.md §6（fonts.gstatic.com 不通时从建好的检出拷
`.local/offline-motion-deps` 再用门禁校验）；执行器 venv 必须 standalone
Python（Homebrew/系统 Python 出的包死在签名，报错不指向解释器——这是 macOS
的教训，Windows 侧同理留意解释器来源）。

### [C5] T2.2 镜头结构成为产品数据（任务列表 #54）

**背景**：`scripts/motion_shot_structure.py`（从成片量出镜头边界）已落地并在
T36 验收里做过一次性比对（T2.1）。差的是把「镜头结构」当产品数据留存：授权
answer 里的 segments（每镜帧数/起止）在 App 侧只用于渲染，渲染完丢弃——
验收想比对「声明结构 vs 实测结构」只能靠临时脚本。

**步骤**：把 answer 的镜头表（每镜 frameCount / narrationSeconds / part）
持久化进 RenderJob 快照或 artifact 元数据（Rust `MotionRenderJobSnapshot`
或 artifact 落盘 JSON），T36 验收读它与 `motion_shot_structure.py` 的实测边界
比对（容差 ±1 帧）。

**判据**：T36 验收多一条断言「声明镜头表与实测镜头边界一致」，跑绿。

### [C6] BM-13 素材替换（**已预留给 codex**，见台账）

liquid-glass ×2、texture-mask-text 等目录内容重做。按
`docs/embedded-browser-video-studio-roadmap.md` 里 BM-13 行执行；改目录内容后
必跑：`build_offline_motion_catalog.py` → `check_offline_motion_catalog.py` →
`check_motion_catalog_release.py` → 槽位表若受影响重跑
`frontend/scripts/measure-motion-part-slots.mjs` + `check_motion_part_slot_budget.py`。

---

## 3. 本轮沉淀的领域知识（做上面任务大概率用到）

1. **中文行框比拉丁高约 11-12% 字号**。盒级溢出判定必须用「超出量之差 +
   容差」（X 1px、Y 15% 字号，容差加在增量上），否则 5/37 零件对任意中文报溢出。
   判定实现在 `slot_overflow_probe.py`，改判定先读它的模块 docstring；
2. **大多数零件容器随内容长开**（scrollWidth 恒等 clientWidth），盒级探针
   量不出长文案——「撑出舞台」靠整篇文档尺寸（documentElement）相对比较另测；
3. **`RenderSegmentAnswer` 是 `deny_unknown_fields`**：给 answer 段加字段必须
   serde default 可选，且 Python 侧只在有值时写 key（无声片形状逐字节不变，
   有专测钉住键集）；
4. **`--disable-gpu` 下 WebGL 全灭**，包内 Chromium 要 `--enable-unsafe-swiftshader`
   伴随（有 flag 配对门禁守着，`scripts/test_motion_video_worker.py`）；
5. **colima 共享 socket 别抢**（规则 §9.3）：起 Docker 前 `docker context ls`，
   卡住先看 CPU（零→查网络/镜像拉取，非零→查资源）；
6. **dashscope 偶发瞬时无响应**：一句话链路验收偶红时先用冻结执行器直跑复现
   （`--author-motion` + stdin 请求）再定性，别急着改代码——本轮 T36 第一跑
   就是纯瞬时故障，直跑复现全绿，重跑即过。

---

## 4. 悬置与守候项（不需要马上做，但别弄丢）

- `audit-release-bundle.mjs` 二十多个拒绝点同抛一句 `Release bundle is rejected`
  不说哪条规则（PC-16.md §8.1 记为未修诊断缺陷）；
- 时间判定类测试不能与他人共享机器并行（h8_08 等 5 例污染实录，PC-21）；
- `.local/embedded-browser-video-studio/` 下各任务证据目录按需清理，
  `pc-16-evidence` 里的成片与抽帧、`t36-evidence` 的有声片是台账引用的证据，
  **别删**；
- 台账 26 项计数由 `check_product_completion_roadmap.py` 守，改表必跑。
