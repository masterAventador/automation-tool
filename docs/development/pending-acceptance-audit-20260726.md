# 「🔍 待验收」任务全量排查（2026-07-26）

> **〔2026-08-04 注〕本文件是 2026-07-26 的审计快照，不再更新。**其中凡以「阿里云剪辑」
> 为待办条件的行均已作废：那条外部剪辑服务链路已整条删除，剪辑改由随包 FFmpeg 在用户
> 本机执行，不需要任何外部凭据。CQ-04 的当前定义以 `docs/development/CQ-04.md` 为准。

> 触发原因：用户在 macOS 正式包上试用，发现视频制作/视频剪辑完全不可用，而全部自动化验收此前都是绿的。
> 根因已定位为「测试构建从环境变量拿资源、生产构建从 `Contents/Resources/` 拿资源，而没有任何任务负责把资源装进正式包」。
> 本文件是由此展开的**纯排查**产物：不改代码、不改任何任务状态、不做提交。
>
> 本次排查读取了 37 份任务台账（专项 27 + 主线 10），项目「不得批量加载历史任务文件」的规则在本任务不适用——逐项排查就是本任务的目的。

---

## 1. 准确计数与统计方法

### 1.1 统计方法（以及为什么不能用 grep）

用 Python 解析两份 Roadmap 的 Markdown 表格：逐行取 `|` 分隔的单元格，任务 ID 列用正则匹配，**取最后一列作为状态**，再计数。

**不能用 grep/ripgrep 统计。** 实测三种工具在这些状态 emoji 上的多字节交替匹配全部给出错误答案，且都不报错：

| 命令 | 输出 | 事实 |
| --- | --- | --- |
| `grep -o '🔍 待验收\|✅ 已完成\|🚧 实现中' 专项.md` | `86 🚧 实现中`、`2 🧪 RED` | 专项里 `实现中` 实际为 **0** |
| `grep -oE '…\|…' 专项.md` | 同上 | 同上 |
| `rg -o '\| (⬜ 未开始\|…\|⏸ 后置) \|' 专项.md` | `92 ⏸ 后置`、`1 🧪 RED` | 后置实际为 **9** |

三个错误答案互不相同，也都与文件内自带的汇总表（专项 Roadmap 第 554-558 行）矛盾。**这本身就是本次事故的同型问题：一个静默给出错误答案的检查，比没有检查更危险。** 下文所有计数以 Python 解析为准，并与文件内汇总表交叉核对通过。

### 1.2 专项 Roadmap（`docs/embedded-browser-video-studio-roadmap.md`）

解析到 **87** 行任务，与文档声明的 87 个专项任务一致。

| 状态 | 数量 |
| --- | --- |
| ✅ 已完成 | 51 |
| 🔍 待验收 | **27** |
| ⏸ 后置 | 9 |
| ⬜ 未开始 / 🧪 RED / 🚧 实现中 | 0 |
| **合计** | **87** |

与文件内汇总表（未开始 0、RED 0、实现中 0、待验收 27、已完成 51）一致。

27 项待验收：`EB-03`、`EB-11`～`EB-17`、`BU-06`、`BU-07`、`IM-05`、`IM-07`、`IM-08`、`BM-05`、`BM-07`、`BM-08`、`BM-15`、`BM-16`、`PB-05`～`PB-08`、`CQ-01`～`CQ-05`。

### 1.3 主 Roadmap（`docs/development-roadmap.md`）

解析到 **256** 行任务。

| 状态 | 数量 |
| --- | --- |
| ✅ 已完成 | 163 |
| ⬜ 未开始 | 81 |
| 🔍 各类待验收（措辞不统一，见下） | **10** |
| 历史/被替代（`R0-04`、`B5-02`） | 2 |
| **合计** | **256** |

10 项待验收，主 Roadmap 的状态措辞**没有统一成 `🔍 待验收`**，共出现 6 种写法：

| 任务 | 状态原文 |
| --- | --- |
| B5-15 / D6-16 / A7-16 / A7-17 | `🔍 待真实账号` |
| P9-03 / P9-09 | `🔍 待验收` |
| H8-22 | `🔍 待 Developer ID/公证与 Windows Authenticode` |
| P9-04 | `🔍 待 EB-16 正式包与 Authenticode` |
| P9-06 | `🔍 待设备验收` |
| P9-07 | `🔍 待 EB-16 正式签名包/账号/服务链` |

**两份合计 37 项处于「实现完成但未闭环」状态。**

### 1.4 排查中发现的台账缺口

- **`A7-16`、`A7-17` 没有独立台账文件**（`docs/development/A7-16.md`、`A7-17.md` 均不存在）。项目规则要求每个已激活任务都有独立证据文件，这两项只在主 Roadmap 第 571 行有一句汇总说明。
- 原因：**主 Roadmap 没有台账完整性门禁**。`scripts/check_embedded_browser_video_roadmap.py` 只对专项 87 行做「行完整、计数一致、单一 RED、每个已激活任务有且仅有一个证据文件、完成字段齐全」的检查；对主 Roadmap 只做一件事——第 206 行断言「专项任务行不得被复制进来」。因此主 Roadmap 的 163 项已完成 + 10 项待验收，其台账是否存在、是否齐全，**没有任何自动检查**。

---

## 2. 逐项判定表

分类说明。用户给出的四类不足以覆盖实际阻塞，实测有两类反复出现且不属于 A/B/C/D，如实增列：

| 类 | 含义 |
| --- | --- |
| **A** | 现在就能补：有脚本、有本机环境、无外部依赖 |
| **B** | 需要真实平台账号（抖音扫码、B站凭据） |
| **C** | 需要 Windows 交互式桌面会话（见 `docs/development/windows-manual-acceptance.md`） |
| **D** | 需要正式包（非 `video-studio-e2e` 测试构建）上的用户路径 |
| **E** | 需要本机不具备的硬件或签名凭据（macOS x86_64 机器、Apple Developer ID/公证、Windows Authenticode、真正的干净机） |
| **F** | 需要外部服务凭据或真实人（素材站/配音服务、真实用户读文档） |

「测试分支验过、生产没验」列的判据：该任务的验收是否跑在带 `#[cfg(feature = ...)]` 分叉的测试构建上，而对应的生产分支从未被任何测试或门禁走过。

### 2.1 专项 27 项

| 任务 | 缺的具体证据 | 为什么当时没做 | 类 | 测试分支验过·生产没验 |
| --- | --- | --- | --- | --- |
| **EB-03** macOS 浏览器暂存 | Intel Mac 上对 `chrome-mac-x64` 归档的真实离线启动探针（Windows 会话只验了 Mach-O x86_64 头，Windows 不能执行 Mach-O） | 无 macOS x86_64 硬件 | E | 否 |
| **EB-11** 登录与 Session | 受控抖音账号下的真实二维码渲染、手机确认、平台侧登录态最终核对 | 无真实抖音账号，且需有头浏览器人工扫码 | B | 否 |
| **EB-12** 搜索/浏览/候选迁移 | 真实抖音搜索结果页上的候选提取最终状态核对 | 同上 | B | 否 |
| **EB-13** 评论迁移 | 真实抖音评论发出后平台侧可见性核对 | 同上 | B | 否 |
| **EB-14** 私信与恢复迁移 | 真实抖音私信送达后平台侧核对 | 同上 | B | 否 |
| **EB-15** 诊断/接管/清理 | Windows 上 headed 人工接管窗口（进程树拆除与崩溃 fail-closed 已于 2026-07-25 在 Windows 真机闭合） | 需 Windows 已登录交互式桌面会话，且按静默规范必须显式开启 | C | 否 |
| **EB-16** 首发包与签名 | ① Apple Developer ID 签名+公证+装订+Gatekeeper 无警告；② 内层 Chromium 重签与 EB-05 逐文件摘要的冲突（须把签名提前到暂存阶段）；③ macOS x86_64 正式包；④ Windows Authenticode + SmartScreen；⑤ Windows 上真启动正式包；⑥ **从 App 窗口里真实看见并点开工作台各页面**（本机自动化进程无屏幕录制/辅助功能权限，`screencapture` 只拿到壁纸）；⑦ 发行物 Manifest 的树外可信锚点 | 无证书、无 Intel Mac、无 Windows 交互式会话、无屏幕录制授权 | E + C + D | **是**（浏览器那一份已修，见第 4 节） |
| **EB-17** 干净机纵向 | ① 真正无 Chrome/Edge 的干净机上启动（本机装着 Chrome，脚本对此显式拒绝而非静默通过）；② Windows 正式包上同一套判据；③ 扫码/搜索/受控动作/Browser Use 演示/动效渲染 | 无干净机、无 Windows 正式包、无真实账号 | E + B + D | 否 |
| **BU-06** 百炼与受限能力 | 带真实截图的完整视觉推理回合（真实发布页）；DeepSeek/GLM 的 DOM-only 真实页面验收 | 依赖 PB-05/PB-06 的真实发布页 | B | 否 |
| **BU-07** 攻击矩阵 | ① 从 macOS/Windows **正式包内部**跑同一矩阵；② macOS x86_64 主机；③ **矩阵连跑多次的稳定性**（随机端口与进程清理只跑过一遍） | 缺正式包、缺 Intel Mac；连跑稳定性单纯没做 | D + E + **A**（连跑） | 否 |
| **IM-05** 完整 WebUI 子 WebView | 真实生成、进度、取消、结果、成片——即 IM-08 的三类样片 | 缺素材站与配音服务条件 | F + D | **是** |
| **IM-07** RenderJob 与 Artifact 对账 | 从正式 App 生成至少一个真实 RenderJob，核对进度/取消/成功失败/播放/单次导入/保留/删除/重启恢复 | 缺凭据、缺正式签名包 | F + D | **是** |
| **IM-08** 代表性视频与正式包 | 三类真实样片（知识讲解/资讯摘要/榜单）+ 八项失败矩阵 + 双平台正式包，经 `run_im_08_acceptance.py --formal-evidence` | 缺百炼/素材站/配音凭据，缺双平台正式签名包 | F + D + E | **是** |
| **BM-05** AI 一句话到动效编排 | App 用户入口纵向（归 BM-08/BM-16）。真实模型编排本身已通过；Windows NTFS 路径语义已于 2026-07-25 补齐并修掉 3 个真实缺陷 | 依赖 BM-08/BM-16 | D | **是** |
| **BM-07** 风格推荐与冻结 | ① App 点击即完成冻结的纵向（归 BM-08）；② Windows 原生文件选择器上传 Logo；③ 成片里字幕/Logo/转场的关键帧视觉检查 | macOS WebKit WebDriver 无 `uploadFile`；依赖 BM-08 产出真实视频 | C + D | **是** |
| **BM-08** App 原生编辑与渲染 | ① Windows 同路径（含原生文件选择器、NTFS 语义）；② 双平台正式安装包链路 | 需 Windows 会话；正式包链路属 BM-16 | C + D | **是** |
| **BM-15** 134 项自动选用 | ① **App 内「一句话自动制作」接入编排代理**——当前 App 端覆盖结果只保存在草稿，注入真实 RenderJob 的端到端链路没有对应任务；② Windows 同路径；③ 双平台正式包 | 接线任务在专项 87 项里不存在 | D + C（**且缺立项**） | **是** |
| **BM-16** 确定性与正式包 | ① 双平台正式安装包（包内 Chromium/FFmpeg/Worker 链路、包内容负面检查、跨机确定性、低配机、休眠恢复）；② Windows 全链路；③ **134 项「内容级」逐项视觉验收**——86 项两帧摘要相同，world-map 等异步绘制条目截帧在内容绘制前，需为条目定义就绪信号后重跑 | 缺正式包、缺 Windows 会话；就绪信号单纯没做 | D + C + **A**（就绪信号 sweep） | **是** |
| **PB-05** 发布前流程 | ① 真实抖音发布页 + 平台最终状态（必须停在提交前）；② App 正常用户路径；③ Artifact 暂存以关闭 TOCTOU；④ 接管期浏览器占用无上界（I-β）；⑤ 共享页面租约未接线（I-γ）——`SURFACE_NOT_OWNED`/`SURFACE_LOST` 在生产不可达 | 无真实账号；③④⑤是发现但未修的真实缺陷 | B + **A**（③④⑤） | 否 |
| **PB-06** 单次发布与独立验收 | ① 真实抖音账号上完成一次真实投稿并核对平台最终状态；② 发布前作品列表基线（消除同名误判，**已知缺口不是已解决问题**）；③ App 侧确认界面投影 | 无真实账号；②归 PB-07 但未立项 | B + **A**（②） | 否 |
| **PB-07** 双平台发布界面与审计 | ① **素材来源接线**（视频产物 → 发布）——发布页需要 `selectedVideo`，真实 App 里目前没有东西会填它，**这个接线任务不存在**；② 真实 App 里点左侧导航进入发布页（debug 构建停在启动环境门，工作台不挂载）；③ 平台服务层三个新方法的管道从未验证过；④ 审计只在内存，重启清空 | ①未立项；②需完整启动环境；③需真实执行器链路 | D + B（**且缺立项**） | **是**（真实 App 走不到，只有 Harness 受控 Adapter 证据） |
| **PB-08** 分平台真实账号纵向 | ① 抖音真实账号可见 Browser Use 投稿 + 平台最终状态；② B站 API 投稿（`bilibili_archive_publishing.py` 完全未验）；③ Windows 正式包上同一条验收；④ 从 App 界面点进去的完整用户路径 | 抖音需扫码、B站无凭据、Windows 包在建 | B + D | 否 |
| **CQ-01** 普通用户可理解性 | ① Windows 上重跑 `plain-language-comprehension.spec.ts`（确认中文 locale/字体回退/截断不让卡片必答项消失）；② **五处改名没有真实 App 证据**（`控制服务`/`本机执行器`/`本机安装授权`/`剩余用量`/`客户演示版`），对应 9 个既有 wdio 用例的断言已同步但本会话未重跑 | ②需要真实 Control Plane/PostgreSQL 环境 | C + **A**（②，本机可起 PostgreSQL） | 部分 |
| **CQ-02** 上游名称泄漏扫描 | ① 真实 App（非 Harness）里的可访问性树扫描——debug 构建停在启动环境门；② 诊断导出包内容逐条脱敏断言；③ 原生扫描把**中文 docstring** 当用户文案（2026-07-26 实测撞上，未修）；④ 词表随新依赖同步的强制机制 | ①需预置启动环境；②③④归 CQ-05 但未做 | D + **A**（②③④） | **是**（运行时扫描只在 UI Harness 里跑） |
| **CQ-03** 资源与失败矩阵压力 | ① **两种视频任务 + 剪辑任务真实并发的资源压力**（CPU/内存/磁盘/网络）——本轮跑的是三个用不同 Profile 的真实 Chromium 进程，不是真的在渲染/剪辑/发布；② 休眠；③ 取消/紧停/崩溃/App 退出并入并发场景；④ Windows 正式包 | ①受阻于正式包缺视频资源；②需真实系统事件 | D + E + **A**（③） | 否（跑在真实正式包上） |
| **CQ-04** 双平台正式包纵向 | ① 抖音真实发布（核心一环）；② **「一句话 → 两类视频 → 阿里云剪辑 → 成片入库」没有串成一次从 App 界面发起的运行**；③ 不是从「全新安装」开始；④ Windows 正式包；⑤ B站实现完整性逐项核对 | ①需人工扫码；②两个密钥都在但没串起来（且正式包缺三份视频资源，串不起来） | B + D + **A**（⑤） | 否 |
| **CQ-05** 文档/SBOM/台账收口 | ① 真实用户照着四份文档走一遍；② 其余事实断言逐条复验（本轮只抽查三条）；③ **架构文档没有为本专项重写**——`frontend-architecture.md`/`backend-architecture.md` 里没有内置浏览器/Browser Use/视频制作三条线；④ Windows 卸载变体场景 | ①需真实用户；②③④单纯没做 | F + **A**（②③） | 否 |

### 2.2 主 Roadmap 10 项

| 任务 | 缺的具体证据 | 为什么当时没做 | 类 | 测试分支验过·生产没验 |
| --- | --- | --- | --- | --- |
| **B5-15** 登录复用验收 | 真实账号下 App/Executor/浏览器完整重启后不重扫的双重启证据。工程实现与隔离纵向已完成（四轮真实 App 生命周期），但生产 AppData 当前没有 current Profile marker，8 个隔离 Profile 经生产 detector 只读探测健康候选为 0 | 无真实账号 | B | 否 |
| **D6-16** 真实目标发现 | 真实搜索得到候选、经 App 目标预览确认、证明无外部副作用。当前 `candidate_count=0`——抖音首页展示 ByteDance verifycenter 验证码 iframe，**需用户按平台正常流程手动解除，不能自动绕过** | 需真实账号 + 用户手动过验证码 | B（含人工介入） | 否 |
| **A7-16** 评论真实验收 | 自有/授权目标上的评论，平台最终状态与服务端一致。**无独立台账文件** | 无真实账号；台账文件缺失无门禁拦截 | B | 否 |
| **A7-17** 私信真实验收 | 自有/授权目标上的私信，含重复/断网/确认丢失覆盖。**无独立台账文件** | 同上 | B | 否 |
| **H8-22** 更新 UI 与双平台 | Developer ID/公证 与 Windows Authenticode 下的真实签名包升级/跳过/覆盖/强更。macOS ad-hoc 与 Windows 普通 NSIS 实包矩阵已过（2026-07-24 Windows 11 实体机） | 2026-07-25 查 `Cert:\CurrentUser\My` 与 `Cert:\LocalMachine\My` 的 `-CodeSigningCert` 均为 0 | E | 否 |
| **P9-03** macOS Tauri 候选包 | Developer ID 签名 + 公证策略下的候选包 | 无证书与公证凭据 | E | 否 |
| **P9-04** Windows Tauri 候选包 | 正式 Authenticode 同 signer/证书链/时间戳/SmartScreen。普通候选已完成实机验收（安装根 369 文件、HKCU-only、卸载零残留），**但该普通候选只装配 Executor，对应平台 Chromium 由 EB-16 装配** | 无 Authenticode 证书；EB-16 Windows 包在建 | E + D | 否 |
| **P9-06** macOS 干净安装 | 正式 DMG + 授权账号 + 可交付本地服务/首次设备注册链下的 fresh 用户级安装、零 Python、扫码/browse/结果/双启动恢复。runner 已就绪 | 缺正式 DMG、账号与服务链 | E + B | 否 |
| **P9-07** Windows 干净安装 | 正式同 signer Authenticode 包 + 授权账号 + production 注册链下的最终设备轮次。runner 已于 2026-07-25 迁移到安装根 `embedded-browser` 唯一 Chromium | 无证书、无 EB-16 Chromium 资源、无授权账号与注册链 | E + D + B | 否 |
| **P9-09** 本地 MVP 最终验收 | 产品规划 14 条 MVP 验收 14/14。当前 **8 条自动化确认、4 条待授权真实平台、2 条待正式双平台设备/包** | 汇总型任务，阻塞等于其依赖项 | B + D + E | 否 |

---

## 3. 按类归并的可执行清单

### A 类：现在就能补（有脚本、有本机环境、无外部依赖）

这是本次排查里最有价值的一组——**它们不缺账号、不缺证书、不缺硬件，纯粹是没做**。

| 来源 | 要补什么 | 命令 / 做法 |
| --- | --- | --- |
| **BU-07** | 攻击矩阵连跑多次，确认随机端口与进程清理不 flake | `for i in $(seq 1 10); do python3 scripts/run_bu_07_acceptance.py \|\| break; done` |
| **BM-16** | 134 项「内容级」视觉验收：为异步绘制条目定义就绪信号或延迟截帧后重跑 sweep | 改 `workers/motion_composition/worker.mjs` 的就绪判定，再 `python3.12 scripts/run_bm_16_acceptance.py`（需先立 RED 测试，走 TDD） |
| **CQ-01** | 五处改名的真实 App 证据 | 起本机 PostgreSQL 后重跑既有 wdio：`pnpm --dir frontend test:control-plane-tauri`、`test:task-restart-tauri`、`test:app-crash-recovery-tauri`、`test:workbench-tauri`、`test:workbench-metrics-tauri`、`test:executor-crash-recovery-tauri`、`test:task-run-tauri`，以及 `run_control_plane_recovery` / `network-recovery` / `model-service` 对应入口 |
| **CQ-02** | ① 诊断导出包内容逐条脱敏断言；② 修「原生扫描把中文 docstring 当用户文案」；③ 词表随新依赖同步的强制机制 | ②③ 在 `scripts/check_user_facing_branding.py`；自检入口 `python3 scripts/check_user_facing_branding.py --self-test` |
| **CQ-03** | 取消 / 紧停 / 崩溃 / App 退出并入并发场景 | 扩 `scripts/test_cq_03_concurrent_isolation.py` + `scripts/run_cq_03_acceptance.py` |
| **CQ-04** | B站实现完整性逐项核对 | 对照 `backend/src/.../bilibili_archive_publishing.py` 与契约逐项核 |
| **CQ-05** | ① 四份文档其余事实断言逐条复验；② **架构文档补写内置浏览器 / Browser Use / 视频制作三条线** | 改 `docs/frontend-architecture.md`、`docs/backend-architecture.md` |
| **PB-05** | ① Artifact 暂存关闭 TOCTOU（上传前硬链接/复制进 App 私有 0o700 暂存目录）；② 接管期浏览器占用加持有上限/心跳（I-β）；③ 共享页面租约接线——否则 `SURFACE_NOT_OWNED`/`SURFACE_LOST` 在生产**不可达** | `build_platform_command_router` 需接收共享 `BrowserSurfaceLeaseManager` |
| **PB-06** | 发布前作品列表基线，消除同名作品误判 | 归 PB-07 发布任务编排层 |
| **台账** | 补 `docs/development/A7-16.md`、`A7-17.md`；给主 Roadmap 加台账完整性门禁 | 扩 `scripts/check_embedded_browser_video_roadmap.py` 的 legacy 分支，或新建主线门禁 |

**另有三项 A 类工作是「缺立项」而不是「缺执行」**，必须先在专项 Roadmap 新建任务行：

1. **素材来源接线**：视频产物 → 发布页 `selectedVideo`（PB-07 遗留项，专项 87 项里没有）；
2. **App 内「一句话自动制作」接入编排代理**：把每分镜零件覆盖结果注入真实 storyboard（BM-15 遗留项，同样没有）；
3. **剪辑工作台生产装配**：见第 6 节新隐患 ⑤，VE-08 台账写明「随相应装配任务与 CQ-04 端到端验收闭合」，但该装配任务在专项 87 项中不存在。

### B 类：需要真实平台账号

| 账号 | 要做的动作 | 覆盖任务 |
| --- | --- | --- |
| **受控抖音账号**（有头浏览器 + 人工扫码） | ① 扫码建立会话：`cd backend && uv run pytest tests/integration/test_douyin_login_embedded_browser.py`；② 同一 Profile 跑搜索/浏览/候选/评论/私信/恢复各集成套件并核对平台最终状态；③ 发布链路移除 fixture 路由直连 `creator.douyin.com`，**必须停在提交前**：`tests/integration/test_douyin_publish_embedded_browser.py`；④ PB-06 真正投稿一次并在作品列表核对；⑤ D6-16 需用户先手动解除首页 verifycenter 验证码挑战 | EB-11～EB-14、BU-06、PB-05、PB-06、PB-08、CQ-04、B5-15、D6-16、A7-16、A7-17、P9-06、P9-07、P9-09 |
| **B站 API 凭据** | 验证 `bilibili_archive_publishing.py` 的真实投稿（不走浏览器） | PB-08、CQ-04 |

### C 类：需要 Windows 交互式桌面会话

参考 `docs/development/windows-manual-acceptance.md` 与 `docs/development/windows-evidence-checklist.md`。

| 任务 | 要在 Windows 桌面上做什么 |
| --- | --- |
| EB-15 | headed 人工接管窗口（可见窗口、人工点击、关闭后进程树清空） |
| EB-16 | 真启动已安装的正式包 App（真实 HTTP 启动检查 + 真退出）——受 SSH 提权令牌限制，必须在非提权交互式会话 |
| BM-07 / BM-08 | 原生文件选择器上传 Logo（NTFS 路径语义） |
| BM-15 / BM-16 | 动效零件目录同路径 + 全链路 |
| CQ-01 | 重跑 `plain-language-comprehension.spec.ts`，确认 antd 中文 locale / 系统字体回退 / 截断不让卡片必答项消失 |

### D 类：需要正式包（非 `video-studio-e2e` 测试构建）

**前置阻断：正式包当前根本跑不起视频功能。** 在第 5 节的三份资源装进正式包之前，下列 D 类项全部无法执行。

| 任务 | 在正式包上点哪条用户路径 |
| --- | --- |
| IM-05 / IM-07 / IM-08 | 左侧「视频制作」→「智能素材成片」→ 打开完整制作界面 → 提交三类主题 → 「制作任务」看终态 → 「成片」播放并人工复核 → 依次触发取消/重试/关闭/重启 |
| BM-05 / BM-07 / BM-08 / BM-15 / BM-16 | 左侧「视频制作」→「品牌动效成片」→ 编辑标题与分镜 → 制作设置选风格/主色/辅助色/上传 Logo → 预览 → 提交渲染 → 取消 → 重试 → 成片播放 → 删除 |
| PB-07 / PB-08 | 左侧「作品发布」→ 选平台 → 发起 → 临界点确认 → 终态（当前缺素材来源接线，`selectedVideo` 无人填） |
| BU-07 | 从正式包内部跑攻击矩阵（当前跑在构建期暂存目录的 Chromium 上） |
| CQ-02 | 真实 App（非 UI Harness）里对 8 个页面取可访问性树快照 |
| CQ-03 | 两种视频任务 + 剪辑任务真实并发的资源压力 |
| CQ-04 | 「一句话 → 两类视频 → 阿里云剪辑 → 成片入库」串成一次从 App 界面发起的运行 |
| EB-16 | 从 App 窗口真实看见并点开工作台各页面（需先授予自动化进程 macOS 屏幕录制/辅助功能权限） |
| EB-17 / P9-04 / P9-07 | Windows 正式包上的同一套判据 |

### E 类：缺硬件或签名凭据（不属于用户给的四类）

| 阻塞物 | 覆盖任务 |
| --- | --- |
| **macOS x86_64 机器** | EB-03（真实离线启动）、EB-16（Intel 正式包）、BU-07（矩阵） |
| **Apple Developer ID + 公证凭据** | EB-16、H8-22、P9-03、P9-06。**注意 EB-16 台账记录了一个必须一并解决的冲突**：内层 Chromium 重签会改写 Mach-O，使 EB-05 的逐文件 SHA-256 全部失效；正确解法是把签名提前到暂存阶段（先按发布身份签名，再算摘要 Manifest） |
| **Windows Authenticode 证书** | EB-16、BU-07、H8-22、P9-04、P9-07 |
| **真正无 Chrome/Edge 的干净机** | EB-17（本机装着 Chrome，脚本对此显式拒绝而非静默通过） |
| **真实系统休眠事件** | CQ-03、BM-16 |
| **发行物 Manifest 的树外可信锚点** | EB-16（需三个 target 的锁定归档同时在手） |

### F 类：需要外部服务凭据或真实人

| 阻塞物 | 覆盖任务 |
| --- | --- |
| **素材站 + 配音服务凭据** | IM-05、IM-07、IM-08。注意：文案模型密钥用户已提供并验真（`qwen3.7-max-2026-06-08`/`deepseek-v4-pro`/`glm-5.2` 均 HTTP 200），阿里云剪辑 AK/Secret 也可用；**只剩素材站与配音两项**，台账未写明具体服务商 |
| **真实用户读文档** | CQ-05 |

---

## 4. 构建期分叉点全清单与未覆盖的生产分支

### 4.1 Cargo feature 定义（`frontend/src-tauri/Cargo.toml`）

```
desktop-test-driver = [dep:tauri-plugin-wdio, dep:tauri-plugin-wdio-webdriver]
desktop-e2e         = [desktop-test-driver]
control-plane-e2e   = [desktop-test-driver]
video-studio-e2e    = [desktop-e2e]
```

### 4.2 `#[cfg(feature = "video-studio-e2e")]` — 7 处，全部与本次事故同型

| 位置 | 测试分支 | 生产分支 | 生产分支被覆盖过吗 |
| --- | --- | --- | --- |
| `lib.rs:329/339/409` `motion_runtime_paths` / `MotionWorkerSource` | 从 `AUTOMATION_TOOL_BM08_WORKER` / `_BROWSER` / `_FFMPEG` / `_CHROMIUM_MAJOR` 读路径 | `resource_dir()/motion-video-worker/package` + `VideoMediaToolchain::load(resource_dir)` | **没有。** `motion_runtime_paths`、`MotionWorkerSource` 在整个 `frontend/src-tauri/tests/` 目录**零出现** |
| `material_video_studio.rs:511` `worker_executable` | 从 `AUTOMATION_TOOL_IM05_WORKER` 读路径 | `resource_dir()/material-video-worker/package/automation-tool-material-video-worker[.exe]` | **没有。** `worker_executable` 同样零出现在 `tests/` |
| **`lib.rs:749` `check_local_startup_environment`** | **无条件返回 `AppData::Ready` + `Executor::Ready` + `EmbeddedBrowser::Ready`** | 读真实 app_data / executor / 内置浏览器状态 | **没有。** 这是最关键的一处——见下 |
| **`lib.rs:1453/1462` `check_control_plane_health`** | **无条件返回 `status: "available"`、`service_version: "video-studio-acceptance"`** | 走真实 `ControlPlaneClient` + 设备凭据 | 生产分支由其他线的 wdio 覆盖，但**视频线的验收从未走过** |

**`lib.rs:749` 是这次事故能瞒过所有验收的结构性原因。** 全部 BM/IM 视频验收都构建在 `video-studio-e2e` 上，而这个构建**无条件宣布启动环境三项全部就绪**。生产里，启动门禁是唯一会因为「浏览器组件损坏 / 执行器缺失 / 资源不全」而拦住用户、不挂载工作台的地方。测试构建把它短路掉了，于是：

- 资源从环境变量来 → 视频功能在测试构建里能跑；
- 启动门禁被短路 → 测试构建不会因为资源缺失而拒绝启动；
- 两条叠加 → **测试构建对「资源到底在不在包里」这件事完全免疫**。

### 4.3 `#[cfg(not(debug_assertions))]` — release-only 分支

| 位置 | debug 分支 | release 分支 | 有门禁吗 |
| --- | --- | --- | --- |
| `lib.rs:3783/3786` executor 包根 | `ExecutorPlatformService::initialize`（无包根） | `resource_dir()/local-executor/package` | 只有验收脚本注入，见 4.5 |
| `executor_platform.rs:865/870` `executor_verifying_key` | 硬编码开发 fixture 公钥 | `option_env!("AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY")` | ✅ **`build.rs:63` 在 `PROFILE=release` 时强制存在、canonical Base64URL、32 字节、有效且非弱 Ed25519，否则 panic**。这是全仓做得最对的一处 |
| `app_update_coordinator.rs:575/580` 更新配置 | `std::env::var` 运行时读 | `option_env!("AUTOMATION_TOOL_UPDATE_ENDPOINT"/"_PUBLIC_KEY")` | ✅ **`build.rs:86` 在 release 强制 HTTPS、无 userinfo/fragment、三个占位符各一次、规范 Base64、真实 Minisign 公钥解析** |
| `app_update_coordinator.rs` 多处 `all(debug_assertions, desktop-e2e, not(control-plane-e2e))` | 允许 `AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS=1` 忽略证书、`_INSTALL_PROBE=1` 用假安装器 | 两者恒为 `false`，且 `UpdateInstallProbe` 类型不编译进 release | ✅ 正确：不安全开关只编译进 debug + desktop-e2e |
| `model_service_settings.rs:473` `is_valid_base_url` | 允许 `http://127.0.0.1` 或生产 URL | **只允许生产 URL** | ⚠️ 生产分支更严，但只有 debug 下的测试跑过。风险低（真实用户本就用生产 URL），但「release 比 debug 更严」这类分支在测试里天然测不到 |
| `video_editing_service_settings.rs:448` `is_valid_override` | 允许 `http://127.0.0.1` 覆盖 | **恒返回 `false`** | ⚠️ 同上 |
| `main.rs:1` `windows_subsystem = "windows"` | — | release 无控制台窗口 | 无风险 |

**`build.rs` 的 fail-closed 是本仓库处理构建期分叉的正确范式**：编译期强制校验、在 `tauri_build::build()` 之前 panic、错误不回显输入。第 5 节的资源装配缺的正是同一层门禁。

### 4.4 `#[cfg(feature = "control-plane-e2e")]` / `"desktop-e2e"` / `"desktop-test-driver"`

`lib.rs` 里约 60 处 `control-plane-e2e`，绝大多数是**追加**的验收专用 Tauri Command（从 `AUTOMATION_TOOL_<任务ID>_BOOTSTRAP_TOKEN` / `_ENVIRONMENT_ID` 环境变量取引导凭据），不替换生产路径，风险相对可控。少数**替换型**的：

- `lib.rs:1445` `check_control_plane_health` 在 `desktop-e2e` 下改签名（去掉 `ProductionDeviceCredentialVault` 参数）——即 desktop-e2e 构建绕过设备凭据；
- `lib.rs:857` `desktop-e2e` 下从 `AUTOMATION_TOOL_H813_EXPORT_DIRECTORY` 取诊断导出目录；
- `executor_platform.rs:436/677/684`、`executor_manager.rs:633/645/655`、`control_plane.rs:480`。

`lib.rs:3826` `desktop-test-driver` 挂载 WDIO 插件——由 `frontend/scripts/audit-production-package.mjs` 的禁止串检查覆盖。

### 4.5 前端构建期分叉

- `frontend/vite.config.ts:12-46`：按 `mode` 把 HTML 入口从 `/src/main.tsx` 换成 `test-browser-settings-main.tsx` / `test-production-main.ts` / `test-control-plane-main.ts` / `test-tauri-main.tsx`。**整个应用入口被替换**——凡是用这些 mode 构建的 E2E，验证的都不是生产 `main.tsx` 的装配。
- `frontend/src/main.tsx:52`：`import.meta.env.MODE === "customer-demo"` 决定是否装配 `TauriAccountSessionGateway`。
- `frontend/src/main.tsx:50`：`createLocalVideoEditingGateway(window.sessionStorage)` —— **不是构建期分叉，是生产里就写死的桩**。见第 6 节。

### 4.6 生产包审计门禁的结构性缺陷

`frontend/scripts/audit-production-package.mjs:112`：

```js
const bundledPaths = [
  ...stringsIn(configuration?.bundle?.resources),
  ...stringsIn(configuration?.bundle?.externalBin),
];
```

它**只做否定检查**——遍历已声明的资源找违禁标记（`webdriver`、`wdio`、运行时数据等）。它从不检查「必需资源是否已声明」。因此：

> **`bundle.resources` 为空时，这个审计永远通过。**

主 Roadmap 的 `P9-05 正式包内容审计` 标 ✅ 已完成，正是因为这个门禁对本次事故完全无感——它验的是「包里没有不该有的东西」，从没验过「包里有该有的东西」。

---

## 5. `Contents/Resources/` 生产资源完整清单

穷举方式：`rg 'resource_dir\(\)' frontend/src-tauri/src/` 取所有后续 `.join(...)`，再交叉核对各模块的 `*_DIRECTORY` 常量；另核对 `include_str!`（编译期内联，不占 Resources）与其余 `app.path()` 用途（app_data / 导出目录，非 Resources）。**确认共 5 项，没有第六个。**

| # | 生产代码期望的路径 | 解析位置 | 期望内容 | 装配路径 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | `Resources/embedded-browser/` | `embedded_browser_distribution.rs:19` `DISTRIBUTION_DIRECTORY` | `distribution-manifest.v1.json` + 完整 Chromium 149.0.7827.55 树（331 文件 / 359 MB） | `release_assembly.install_and_seal`，由 `run_eb_16_acceptance.py:232` 调用（打包**后**装入再重新封章，因为 Tauri bundler 会跟随符号链接破坏 Chrome for Testing 的 framework 链接） | ✅ 有 |
| 2 | `Resources/local-executor/package/` | `lib.rs:3789-3792`（`#[cfg(not(debug_assertions))]`）；`executor_platform.rs:38` `EXECUTOR_DIRECTORY` | 签名的 PyInstaller onedir Executor + `executor-manifest.v1.sig` | `run_eb_16_acceptance.py:177` `write_release_configuration()` —— **在验收时合成一份临时 Tauri 配置**，往 `bundle.resources` 里塞一条映射 | ⚠️ 有，但**只存在于验收脚本生成的临时配置里**，仓库里任何一份 `tauri*.conf.json` 都没有这条声明 |
| 3 | `Resources/media-toolchain/` | `video_media_toolchain.rs:11` `TOOLCHAIN_DIRECTORY` | `bin/ffmpeg`、`bin/ffprobe`、`manifest.json`（ffmpeg 8.1.2 / GPL-3.0-or-later） | **无** | ❌ **缺失** → `render_unavailable` |
| 4 | `Resources/motion-video-worker/package/` | `lib.rs:417-419` | `runtime/node`（Windows `runtime/node.exe`）、`app/worker.mjs` | **无** | ❌ **缺失** → 品牌动效无法渲染 |
| 5 | `Resources/material-video-worker/package/` | `material_video_studio.rs:522` | `automation-tool-material-video-worker`（Windows `.exe`） | **无** | ❌ **缺失** → `process_unavailable` |

**实测核对**（只读 `ls`，未改动任何文件）——`.local/eb-16/run/cargo-target/release/bundle/macos/自动化运营工具.app/Contents/Resources/`：

```
embedded-browser
local-executor
```

与静态分析一致：5 项里装了 2 项。

### 5.1 修复的当前状态（工作树，未提交）

> 本节在排查过程中被主线的并行修复推翻过一次，如实记录两次观察。

**排查开始时（本次会话早段）**：`scripts/release_assembly.py` 已新增 `VIDEO_RUNTIME_RESOURCES`（三份资源的 staging 名 → 安装路径 → 必需文件 → Windows 可执行名映射）、`require_packaged_video_runtime()`、`install_video_runtime()`、`resource_directory()`，但 `run_eb_16_acceptance.py` **尚未调用**它们——装配能力写好了，没接进产包路径。

**排查结束时复核**：已接入，macOS 与 Windows 两条产包路径都接上了。

| 文件 | 接入点 |
| --- | --- |
| `scripts/run_eb_16_acceptance.py` | `:911` `prepare_video_runtime(platform="macos")` → `:242` `install_video_runtime(...)` → `:267` `require_packaged_video_runtime(application, platform="macos")` |
| `scripts/run_eb_16_windows_acceptance.py` | `:559` `prepare_video_runtime(platform="windows")` → `:561` `install_video_runtime(...)` → `:577` `require_packaged_video_runtime(application=payload, platform="windows")` |

配套新增：`scripts/prepare_video_runtime.py`、`scripts/video_runtime_cache.py`、`scripts/test_video_runtime_cache.py`；`scripts/build_video_media_toolchain.sh` 的 `FFMPEG_STATIC_LINK_FLAGS[*]` 修复（dogfood 1b）。

映射本身写得到位：两个 Worker 装在 `<name>/package/...`、media-toolchain 直接装在 `<name>/...`，这个形状差异被集中声明了一次而不是在各调用点重建。

**仍未闭合的三点**（截至本文写作时）：

1. 全部改动**仍在工作树，未提交**；
2. **尚未重新构建正式包验证** —— `.local/eb-16/run/` 下那个包仍是修复前产物，第 5 节表格的「实测核对」反映的是修复前状态；
3. **`embedded-browser` 与 `local-executor` 仍未被同一套「单一资源声明 + 打包时强制核验」覆盖** —— 前者靠 `install_and_seal` 单独处理，后者仍靠 `write_release_configuration()` 现场合成临时配置注入。也就是说第 6 节 ① 描述的结构性问题只被修掉了三分之一：视频资源有了单一声明，浏览器和执行器还没有。

### 5.2 Windows 侧同一病根

`release_assembly.resource_directory()` 对 Windows 返回**安装根**而非 `Contents/Resources`。而 `scripts/run_eb_16_windows_acceptance.py:190-198` 同样是**在脚本里现场往 `configuration["bundle"]["resources"]` 塞两条资源树**（executor + browser），再交给 NSIS bundler。因此 Windows 侧：

- 同样没有任何仓库内配置声明资源；
- 三份视频资源在 Windows 上同样没有装配路径。

---

## 6. 我的判断：还有哪些没被发现的同类问题

已知三处（EB-16 内置浏览器、本轮三份视频资源、VF-04 跨平台脚本只验新平台）之外，本次排查新发现 **6 处**，按严重程度排序：

### ① 仓库里不存在「生产构建路径」——唯一能产出可用包的东西就是验收脚本

这是比「漏了三份资源」更根本的表述。三份 checked-in 的正式 Tauri 配置：

| 文件 | `bundle` 段 |
| --- | --- |
| `tauri.conf.json` | `{"active": true}` |
| `tauri.macos-candidate.conf.json` | `{"active": true, "targets": ["app","dmg"]}` |
| `tauri.windows-candidate.conf.json` | `{"active": true, "targets": ["nsis"], "windows": {...}}` |

**三份都没有 `resources`。** `frontend/package.json` 里 44 个 `build:tauri:*` 脚本全部是 `--debug --no-bundle` 的测试构建，**没有任何一条产出可分发正式包的脚本**。

于是实际情况是：executor 靠 `run_eb_16_acceptance.py` 现场合成临时配置注入，浏览器靠打包后 `install_and_seal` 装入，视频三份靠没有人。「生产装配」这件事从来不是一个可复现的、有单一事实源的构建步骤，而是散在验收脚本里的副产品。

**推论：只要下一个需要打包资源的功能出现，同样的事会第三次发生**，除非把资源清单收敛成单一声明 + 打包时强制核验（`release_assembly.py` 的 `VIDEO_RUNTIME_RESOURCES` 已经是正确方向，但还需要覆盖 browser 与 executor，并接进产包路径）。

### ② `video-studio-e2e` 构建短路了启动环境门禁与 Control Plane 健康检查

`lib.rs:749` 与 `lib.rs:1453/1462`。详见 4.2。这是「为什么资源缺失能瞒过全部视频验收」的直接答案，且**此前没有任何台账记录过它**——27 份台账里没有一份提到自己跑的构建把启动门禁短路了。

### ③ `motion_runtime_paths` / `worker_executable` / `MotionWorkerSource` 在 `tests/` 目录零出现

不是「生产分支没测」，是**两个分支都没有任何 Rust 测试**。`cargo test --test motion_video_studio` 覆盖的是 `motion_video_studio.rs` 的领域逻辑，不碰路径解析。这三个符号是 App 与磁盘资源之间唯一的接缝，也是最脆的地方。

### ④ 生产包审计只做否定检查，`bundle.resources` 为空恒过

见 4.6。这解释了为什么 `P9-05 正式包内容审计` 标了 ✅ 已完成却对本次事故毫无反应。

### ⑤ 剪辑线（VE-01～VE-08）**全部标 ✅ 已完成，但生产装配是 sessionStorage 桩**

`frontend/src/main.tsx:50`：

```ts
const videoEditingGateway = createLocalVideoEditingGateway(window.sessionStorage);
```

`frontend/src/platform/tauri/` 下**没有**真实的剪辑工作台网关。VE-04～VE-08 交付的领域层与阿里云 IMS/ICE Provider 实现（已用真实凭据验证过）**从未接入 Control Plane API**。正式 App 里的剪辑数据存在浏览器会话存储，进程退出即失。

**这比 27 项待验收更危险**，因为它已经被标成完成了。VE-08 台账写明「随相应装配任务与 CQ-04 端到端验收闭合」，而该装配任务在专项 87 项里**不存在**。

**由此产生的怀疑（我未能查证）：87 项里那 51 项 ✅ 已完成，有多少是同样情况——分层实现完成、生产装配从未接上？** 我只在 `main.tsx` 这一个装配点上抽到了 VE 一处，没有做全量核对。`PB-07` 台账里「正式装配漏了发布网关」已于 2026-07-26 修复，说明这个模式**至少已经命中过发布线一次**。

### ⑥ 主 Roadmap 没有台账完整性门禁

`A7-16`、`A7-17` 无独立台账文件，且没有任何检查会发现。`check_embedded_browser_video_roadmap.py` 对主 Roadmap 只断言「不要把专项行复制过来」。主 Roadmap 的 163 项已完成 + 10 项待验收，台账是否存在、字段是否齐全，全靠人自觉。

### 补充：状态词表在主 Roadmap 上已经失控

10 项待验收用了 6 种不同措辞（`🔍 待真实账号` / `🔍 待验收` / `🔍 待设备验收` / `🔍 待 Developer ID/公证与 Windows Authenticode` / `🔍 待 EB-16 正式包与 Authenticode` / `🔍 待 EB-16 正式签名包/账号/服务链`）。信息量是有的，但**任何自动统计都会漏**——我第一次统计时 `🔍 待验收` 只数出 2 项，差点漏掉另外 8 项。

---

## 7. 我没能查证的部分

如实登记，不猜：

1. **全部结论来自静态阅读 + 对已构建产物的只读目录清点，没有运行验证。** 按任务约束，本次排查未启动 App、未启动浏览器、未跑构建、未跑任何测试。「生产分支未被覆盖」是通过「符号在 `tests/` 目录零出现」+「门禁脚本不检查该项」推出的，不是运行时证明。

2. **`.local/eb-16/run/.../自动化运营工具.app` 是本轮修复开始前构建的产物。** 我据它认定「5 项装了 2 项」。修复接进产包路径后重新构建的包会不会正确，我无法判断——`run_eb_16_acceptance.py` 尚未调用新增的 `install_video_runtime`。

3. **没有全量核对 51 项 ✅ 已完成任务的生产装配。** 第 6 节 ⑤ 的怀疑（有多少已完成任务同样卡在装配层）是基于 VE 一处 + PB-07 一处的归纳，**没有逐项验证**。这件事我认为优先级高于补 27 项待验收——已标完成的缺口不会有人再去看。

4. **没有逐节读 `docs/development/windows-evidence-checklist.md` 与 `windows-manual-acceptance.md`。** C 类各项「Windows 上到底还差什么」以任务台账的自述为准，未与 Windows 侧文档交叉核对。

5. **素材站与配音服务具体是哪家、凭据从哪里获取，台账没有写明**，我没查到。F 类的这两项无法给出可执行动作。

6. **`control-plane-e2e` 的约 60 处分叉我只做了抽样。** 我确认了它们**绝大多数是追加型**（新增验收专用 Command）而非替换型，但没有逐处核对。其中是否还藏着第二个 `check_local_startup_environment` 式的「无条件返回就绪」，**我没有查证**。

7. **`P9-09` 的「14 条 MVP 验收，8 条自动化确认 / 4 条待授权真实平台 / 2 条待正式双平台设备包」这个拆分，我只读到主 Roadmap 第 564 行的汇总，没有去核对那 14 条各自是什么、8 条自动化确认里有没有跑在被短路的测试构建上。**

8. **未核对 `check_embedded_browser_package.py` 之外的包内容门禁是否还有其他遗漏。** 我确认了它只认识 `embedded-browser` 一个资源名，但没有穷举 `frontend/tests/production-package-audit.test.mjs` 与 `scripts/audit-release-bundle.mjs` 的全部断言。
