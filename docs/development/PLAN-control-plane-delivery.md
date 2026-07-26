# PLAN：Control Plane 的交付形态（方案稿，未实现）

> 日期：2026-07-26
> 状态：调研 + 方案，**未写任何代码、未改任何现有文件、未启动任何服务**
> 触发：正式包已能出厂（DMG 545.7 MiB），但装到别人的电脑上打开是一张诊断页——Control Plane 从未有过交付形态
> 场景：**只做场景 B**。安装包装到领导的电脑上，由领导带去客户现场演示。领导不敲命令、不装 Docker、不看日志；现场网络不可控。

---

## 0. 结论先行

**推荐：预置演示机（Provisioned Demo Machine）。** 由我们在交付前把领导那台 Mac 一次性配好：App + 本机 Control Plane 服务 + 本机 PostgreSQL + 完成一次设备注册 + 完成一次抖音扫码，然后交给他。**领导只做一件事：双击 Dock 里的图标。**

三条理由：

1. **它避开了本方案唯一真正的死结**——不需要让领导装 Docker/PostgreSQL，也不需要我们在一周内把 PostgreSQL 打进安装包并过签名/公证；
2. **它不改架构基线**。CLAUDE.md 4.2 禁止的是「随 Tauri 安装包分发」；一个由我们单独安装、随用户登录启动的本机服务，本身就是「独立进程和独立部署单元」，字面和精神都不冲突；
3. **它对断网免疫**。演示现场只有客户访客 WiFi 甚至没网时，全链路除抖音本身外都在本机。

**必须同时接受的三个已知妥协**（详见 §7）：

- **正式包目前是 ad-hoc 签名，没有 Developer ID、没有公证**。装到第二台 Mac 上 Gatekeeper 会拦。交付前必须解决（申请证书公证，或在预置时由我们手工过闸并验证重启后仍可打开）；
- **当前这份 DMG 无法执行"动作"步骤**。动作授权私钥在构建时用完即丢，服务端签不出这份包能验的授权（§2.4）。要么重新构建并保留密钥，要么演示脚本停在"发现 + 预览 + 人工接管"；
- **抖音登录态不可搬运**。开发机上那份登录态在开发机的 App 私有目录里，不会跟着安装包走。领导那台机器必须现场扫一次码（预置时做，不要留到客户面前）。

**明确否决：** 一周内做「随包分发的内置 Control Plane + 内置 PostgreSQL」（体积余量不够 + 要改基线 + 签名面暴涨），以及「云端部署」（现场断网即全废，且从未真实部署过）。两者都是**正式交付**的候选，不是**下周演示**的候选。

---

## 1. 现状事实（逐条带证据）

### 1.1 没有 Control Plane，App 打开就是诊断页

- `frontend/src/app/startup.ts` 的 `createDesktopStartupCheck` 把 `transport.checkHealth()` 的失败映射成 `control_plane_unavailable` 诊断，并返回 `{status: "blocked"}`；
- `frontend/src/app/StartupGate.tsx` 在 `blocked` 时**不渲染 children**，整屏是 `Result status="error"`，标题「暂时无法连接业务服务」，只有一个「重新检查」按钮；
- 结论：这不是"少一个功能"，是**App 完全不可用**。

### 1.2 App 去哪连是编译期钉死的，用户改不了

`frontend/src-tauri/src/deployment_profile.rs`：

- `DeploymentProfile::load()` 读三个 `option_env!` 编译期常量；三个都缺 → 返回 `local`，`base_url` 固定 `http://127.0.0.1:8765`（`LOCAL_BASE_URL`，第 14 行）；
- 三个都有 → 必须通过 Ed25519 `verify_strict` + canonical JSON 校验，且 `base_url` 必须是**无端口、无路径的精确 HTTPS origin**，`profile_id` 必须 `demo-` 开头；
- 没有任何运行时环境变量、配置文件或界面入口能改这个值（C10-07 的设计目标就是这个）。

**推论**：现有这份 DMG 是 `local` Profile，它只会去连**本机 8765 端口**。要让它连云端，必须**重新构建**一份带签名 demo Profile 的包，不是改配置。

### 1.3 Control Plane 启动需要什么

| 依赖 | 事实 | 证据 |
| --- | --- | --- |
| PostgreSQL | **硬依赖**。URL 必须以 `postgresql+asyncpg://` 开头，否则 fail closed | `backend/src/automation_tool/control_plane/bootstrap/database.py` |
| 迁移 | 35 个 Alembic 版本，正式入口**永不自动迁移**，必须显式 `alembic upgrade head` | `backend/migrations/versions/`；`docs/backend-architecture.md` §20.2 |
| Secret 投递 | 默认 `environment` 模式（读环境变量）；容器镜像固定 `files` 模式读 `/run/secrets` | `bootstrap/runtime_secrets.py:107` |
| 本机入口 | `automation-tool-control-plane` → `bootstrap/cli.py:main` → uvicorn 固定 `127.0.0.1:8765`，工厂是 `local_app` | `backend/pyproject.toml:23`、`bootstrap/cli.py` |
| 账号三件套 | **本机形态不需要**。三个变量缺一即不装配账号服务 | `bootstrap/app.py:263` |
| 归属门禁 | `require_installation_owner = (账号服务 is not None)`，本机自动为 `false` | `bootstrap/app.py:306`（`7086ae4` 引入） |
| 健康检查 | `/api/v1/health` **会真的查数据库**，DB 不可用即 503 | `api/system.py:60-72` |

最后一条很重要：**PostgreSQL 没起来 = App 打不开**，不是"部分功能降级"。

### 1.4 云端交付所需的东西一件都没有

- `deploy/customer-demo/compose.v1.json` 要求 11 个必填非秘密变量 + 3 个外部预建 volume + 2 个外部预建网络 + 2 个不可变镜像 digest；
- `deploy/secrets/inventory.v1.json` 要求 5 个 Secret 文件（database-url、account-password-pepper、account-fingerprint-key、account-operations-capability-digest、action-authorization-private-key），必须由云 Secret Store 以 `0400`/`0440` 普通文件投影到 `/run/secrets`；
- `scripts/deploy_customer_demo.py` 还要求：24 小时内的已验证备份 receipt、OCI version/revision 校验、公开 CA、真实域名（`SAFE_HOST` 要求带点的合法域名）；
- **`docs/development/C10-08.md` 白纸黑字**：「未连接任何未指定的云账号，也不把本地隔离演练描述成真实公网部署」。C10-08～C10-13 的 `✅` 全部是**本机隔离 Docker 网络里的演练**，不是真实云。

也就是说：没有服务器、没有域名、没有 DNS、没有公网证书、没有备份、没有 Demo 账号。**云端方案是从零开始，不是"把现成的东西部署一下"。**

### 1.5 路线图里这件事就没有过任务

- `docs/development-roadmap.md` 第 100 行：`| 本地/云端服务 | ⬜ 开发验收会临时启动并清理本地服务；尚无常驻环境，未部署云端 |`；
- `docs/` 与 `docs/adr/`（只有 0001 内置 Chromium、0002 视频剪辑模块）里没有任何描述"用户怎么得到这个服务"的文档；
- `docs/development/PLAN-production-device-registration.md` §1.7 已经把这条标为「第二个更大的缺口」，并在 §5 建议单独立项——**本文件就是那个立项**。

---

## 2. 交付前必须先解决的四个既有障碍

这四条与选哪个方案无关，**任何一条不解决，领导那台机器都演不了**。

### 2.1 Gatekeeper：正式包目前无法在第二台 Mac 上打开

- `scripts/build_release_package.py:221` 与 `scripts/release_assembly.py:50` 都是 `codesign --force --sign -`，即 **ad-hoc 签名**；
- `docs/development/RELEASE-package-clean-rebuild.md` 结尾自己写着「未闭环：……正式签名与公证」；
- 后果：DMG 拷到别的 Mac 上，macOS 会以「无法验证开发者」拒绝打开。近几个 macOS 版本已经取消了"右键打开"的简易绕过，需要进「系统设置 → 隐私与安全性 → 仍要打开」，而且**内置 Chromium 与 Executor 这些嵌套可执行文件各自也可能各弹一次**。

**处理方式（二选一）：**

- **A（正规）**：申请/启用 Developer ID Application 证书，改用真实身份签名 + hardened runtime + `notarytool` 公证 + `stapler` 装订。这是 P9-03/EB-16 一直挂着的 `🔍`；
- **B（应急）**：预置时由我们在领导机器上手工完成 Gatekeeper 放行（`xattr -d com.apple.quarantine` 或系统设置放行），并**验证重启后仍能打开**。风险：领导以后自己重装/移动 App 会再次被拦；DMG 若经 AirDrop/邮件/浏览器分发会重新带上 quarantine 属性。

> 一周内 A 更稳妥的前提是证书已经有；没有的话 B 是唯一现实选择，但必须在**我们的手上**完成放行，不能让领导现场处理。

### 2.2 动作授权私钥被丢弃，这份 DMG 签不出可用的动作授权

链条如下：

1. `scripts/run_p9_03_acceptance.py:106` 的 `executor_signing_material()` 用 `secrets.token_bytes(32)` **每次构建现场生成**一把 Ed25519 密钥；
2. `scripts/build_release_package.py:157` 用它签 Executor manifest；
3. `scripts/run_p9_03_acceptance.py:142-143` 把**同一把公钥**同时写进 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY` 和 `AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY`，编译进 App；
4. `scripts/run_eb_16_acceptance.py:749-753` **只把公钥写进 `build/executor-verifying-key`**，私钥留在内存里，进程退出即消失。

而 Control Plane 要下发动作命令，必须持有**匹配的私钥**：`application/task_command_delivery.py:484-500`，`ACTION_EXECUTE` 命令在 `action_authority_issuer is None` 时直接 `raise ValueError`，根本发不出去。

**结论：当前这份 DMG 的"执行动作"这一步是死的，任何服务端配置都救不回来。**

处理方式：

- **重新构建**，构建时用一把**我们保留的** Ed25519 密钥（把种子存成运维凭据），构建后把私钥配进本机 Control Plane 的 `AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY` + 四个限额变量；
- 或者**演示脚本避开动作**：只演 登录 → 发现目标 → 预览 → 人工接管 → 结果，不演"执行动作"。

顺带记一条：现有构建的 `LOCAL_ACTION_TASK_LIMIT=1`、`LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS=60`（`run_p9_03_acceptance.py:144-145`）。就算密钥问题解决，**一个任务也只允许 1 个动作、间隔 60 秒**。演示前要按演示脚本重设这两个值。

### 2.3 抖音登录态不会跟着安装包走

- 登录态在 `~/Library/Application Support/com.aventador.automationtool/embedded-browser-profiles/douyin`（EB-16 首启清单里有这一项）；
- 那是**开发机上这个用户的**目录，安装包里没有、也不该有；
- 领导那台机器必须**自己扫一次码**。扫码要真人拿手机，**必须在预置阶段完成，不能留到客户面前**——验证码/风控/异常登录一律只能转人工（CLAUDE.md §5），现场翻车没有 B 计划。

### 2.4 演示数据是空的

新机器的 PostgreSQL 是空库，工作台上没有任何历史任务、事件或结果。预置时应当跑通一遍完整链路，让库里留下真实的成功记录，避免演示第一分钟面对空列表。

---

## 3. PostgreSQL 硬依赖：四条出路逐个评估

这是场景 B 的核心障碍。逐条评。

### 出路一：预置时在演示机上装一个本机 PostgreSQL（**推荐**）

**做法**：交付前由我们在领导机器上安装 PostgreSQL（Postgres.app 拖拽安装，或我们打包好的 postgres 二进制 + 一次 `initdb`），建库建角色，跑一次 `alembic upgrade head`，然后配成随登录启动。

| 维度 | 评价 |
| --- | --- |
| 领导要做什么 | **什么都不做**。安装是我们干的 |
| 是否需要 Docker | 否 |
| 是否改架构基线 | 否。仍是 PostgreSQL，仍是独立进程 |
| 是否改代码 | **零代码改动**。`AUTOMATION_TOOL_DATABASE_URL` 指过去即可 |
| 体积影响 | 不进安装包，不占那 118 MiB 余量 |
| 断网影响 | 无 |
| 工作量 | 半天～一天（含验证） |
| 风险 | 领导机器上多了一个他不知道的后台服务；卸载/迁移需要我们介入 |

**这是唯一一条能在一周内做到、且零代码风险的路。**

### 出路二：把 PostgreSQL 打进安装包（内置数据库）

**做法**：把 postgres 服务端二进制作为第六份资源装进 `.app`，首启做 `initdb` + 迁移，由 Tauri 管生命周期。

**硬障碍：**

1. **体积余量不够或极度勉强**。`scripts/check_embedded_browser_package.py:126` 把整包上限钉在 **1270 MiB**，当前实测 1151.9 MiB，**余量约 118 MiB**。而这条路要塞进去的是：Control Plane 的 PyInstaller 包（估 50～80 MiB，见 §4）+ postgres 二进制与 share 数据（估 40～60 MiB，若不裁 ICU 会再多 30 MiB 以上）。**两者相加大概率突破上限**，等于同一周内还要压体积；
2. **PostgreSQL 15+ 默认链 ICU**，Homebrew 的 bottle 不可重定位，必须自己 `--without-icu` 从源码编译或用可重定位发行版——这不是"下载解压"能收工的事；
3. **签名/公证面暴涨**。postgres 带一堆独立可执行文件和动态库，每一个都要签、都要过 hardened runtime，而 §2.1 说明我们连主程序的正式签名都还没打通；
4. **首启 `initdb` 是个新的失败面**：磁盘满、权限、locale、端口占用、上次没干净关闭的数据目录——全都要进失败矩阵（CLAUDE.md §9 强制）；
5. **与架构基线冲突**：CLAUDE.md 4.2「Control Plane 从第一天就是独立进程和独立部署单元，不随 Tauri 安装包分发」。走这条路必须先写 ADR 改基线。

**结论**：这是**正式产品**（要给任意客户"双击就能用"）的正确长期答案，但**不是下周的答案**。工作量以周计，不以天计。

### 出路三：改用 SQLite

**直接否决。** 理由：

- `bootstrap/database.py` 硬性要求 `postgresql+asyncpg://`；
- `infrastructure/database/schema.py` 直接 `from sqlalchemy.dialects.postgresql import ARRAY, UUID`，并有 **6 处 `postgresql_where` 部分唯一索引**（第 671、759、1288、2413、2518 行等）——这些索引正是"非终态 Attempt 单活""同一设备公钥唯一 active 凭据"这类**核心业务不变量**的执行者，SQLite 需要另写一套等价约束；
- 35 个 Alembic 迁移要另做一条 SQLite 链；
- 321 个集成测试全部跑真实 PostgreSQL，要么另写一套，要么失去覆盖；
- CLAUDE.md 4.2 明文「数据库从第一天使用 PostgreSQL」，且 §4.2 末尾「local/demo 只允许配置、凭据和基础设施不同，**禁止维护两套业务实现**」——两个方言的 schema + 两条迁移链就是两套实现。

**这条路的代价远大于它省下的麻烦，而且换来的是长期的双实现债务。**

### 出路四：云端部署，本机不要数据库

见 §4 方案三。**它确实绕开了数据库**，但把风险换成了"演示现场必须有可用的外网"，并且需要一整套从未存在过的东西。

### 推荐

**出路一（预置本机 PostgreSQL）用于下周；出路二（内置数据库）立项做正式交付；出路三永久否决；出路四作为多客户交付的并行长期方向。**

---

## 4. 候选交付形态对比

### 方案一：预置演示机（Provisioned Demo Machine）—— **推荐**

**形态**：我们拿到（或远程接管）领导的 Mac，一次性完成：

1. 装 App（DMG，含 Gatekeeper 放行）；
2. 装本机 PostgreSQL，建库、跑迁移；
3. 装本机 Control Plane（PyInstaller 单文件，**领导机器上不需要 Python**），配成 **LaunchAgent** 随登录启动，`KeepAlive` 拉起；
4. 启动 App 完成一次设备注册（§6）；
5. 抖音扫码登录一次；
6. 跑一遍完整链路留下真实数据；
7. 交付前断网复测一遍。

| 维度 | 结论 |
| --- | --- |
| 客户/领导要做什么 | **双击图标**。没有第二步 |
| 我们要做什么 | 一次性预置（半天）+ 把 Control Plane 打成 PyInstaller 包（约一天，复用 Executor 现成的打包链路） |
| 首次使用体验 | 见 §5 |
| 失败时看到什么 | 「暂时无法连接业务服务」+「重新检查」按钮。**领导自己解决不了**，只能打电话给我们 → 必须靠预置阶段的断网复测把概率压到最低 |
| 与基线是否冲突 | **不冲突**。独立进程、独立部署单元、不随安装包分发，三条全中 |
| 断网影响 | 无（抖音本身除外） |
| 工作量 | **1.5～2 人日**，不含 §2.1 签名 |
| 主要风险 | 只解决"这一台机器"。第二台机器要重来一遍 |

**为什么不直接手工起服务而要做 PyInstaller 包 + LaunchAgent**：手工 `uv run` 起的服务，终端一关就没了，重启电脑就没了。领导不会去重启它。必须是**开机自动在、他看不见、也关不掉**的形态。

> 注：这与全局规则「本地开发服务不得常驻/开机自启」不矛盾——那条规则约束的是**开发机**。领导那台是**交付出去的演示机**，服务常驻正是它的交付形态。

### 方案二：内置 Control Plane + 内置 PostgreSQL（随包分发）

**形态**：第六、第七份资源进 `.app`，Tauri 像管 Executor 一样管这两个进程；首启 `initdb` + `alembic upgrade head`。

| 维度 | 结论 |
| --- | --- |
| 客户要做什么 | 双击安装，打开。**这是真正的产品形态** |
| 我们要做什么 | ADR 改基线 + 打包 postgres + 首启编排 + 体积压缩 + 全套失败矩阵 + 签名公证每个二进制 |
| 首次使用体验 | 首启多等 `initdb` + 迁移（估 5～15 秒），需要一个"正在初始化"页面——**现在没有这个状态**，`StartupGate` 只有 checking/blocked 两态 |
| 与基线是否冲突 | **直接冲突**（CLAUDE.md 4.2）。必须先立 ADR 改基线才能动手 |
| 断网影响 | 无 |
| 工作量 | **数周**。体积余量 118 MiB 大概率不够（§3 出路二） |
| 结论 | **正式交付的正确答案，下周做不完** |

### 方案三：云端部署，所有客户共用

**形态**：真实服务器 + 真实域名 + 公网证书，App 换成签名 demo Profile 构建，登录产品账号后设备自动绑定。

| 维度 | 结论 |
| --- | --- |
| 客户要做什么 | 装 App、**登录产品账号**（demo Profile 强制，见 CLAUDE.md 4.1）、扫抖音码 |
| 我们要做什么 | 采购服务器 + 域名 + 证书 + 部署 PostgreSQL + 5 个 Secret + 备份 + 建 Demo 账号 + **重新构建一份签名 demo Profile 的 App** + 真跑一遍 C10-08～C10-13 |
| 首次使用体验 | 多一步账号登录；每次打开都依赖外网 |
| 失败时看到什么 | 断网/服务器故障 → 同样是「暂时无法连接业务服务」阻断页 |
| 与基线是否冲突 | 不冲突，**这正是基线规划的形态** |
| 断网影响 | **致命**。客户会议室 WiFi 不通 = 演示直接结束，且现场无任何补救手段 |
| 工作量 | 从零开始，乐观也要 1～2 周，且部署必须用户明确授权（CLAUDE.md §10） |
| 结论 | **多客户长期交付的正确方向；下周不可行，且与"网络不可控"直接对撞** |

### 网络权衡（专门回答）

演示失败代价高，就意味着**要按最坏网络假设选方案**：

- 云端方案的失败模式是**全有全无**且**不可现场补救**——服务器连不上，App 连工作台都进不去，没有降级路径，没有离线模式，`StartupGate` 不会放行；
- 本机方案的失败模式是**局部且可预演**——本机链路完全离线，只有抖音那一段需要网。而抖音这一段**任何方案都需要网**（云端方案还额外需要一段网）。

换句话说：**云端方案的网络依赖是本机方案的严格超集**。在"现场网络不可控 + 失败代价高"的前提下，本机方案在网络维度上单向优于云端方案，没有取舍可言。

唯一支持云端的论点是"一次部署服务多个客户"——那是**下一阶段**的问题，不是下周的问题。

---

## 5. 首次启动体验（推荐方案下逐步展开）

### 交付时（我们做，领导不在场）

| 步 | 内容 | 耗时 |
| --- | --- | --- |
| 1 | 装 DMG，过 Gatekeeper（§2.1） | 2 分钟 |
| 2 | 装 PostgreSQL，`initdb`，建库建角色 | 5 分钟 |
| 3 | 跑 `alembic upgrade head`（35 个版本） | < 1 分钟 |
| 4 | 装 Control Plane LaunchAgent，起服务 | 5 分钟 |
| 5 | 打开 App → 自动完成设备注册（§6） | 10 秒 |
| 6 | 抖音扫码（真人手机） | 2 分钟 |
| 7 | 跑通一遍完整链路留数据 | 10 分钟 |
| 8 | **拔网线复测**：关掉 WiFi 重启电脑，确认 App 仍能开、工作台仍能看历史 | 10 分钟 |

### 领导使用时

| 步 | 他看到什么 | 耗时 |
| --- | --- | --- |
| 1 | 双击 Dock 图标 | — |
| 2 | 「正在启动运营工作台 / 正在检查桌面运行环境」+ 转圈 | 1～3 秒 |
| 3 | 直接进工作台，有历史任务和结果 | — |

**没有输入、没有登录、没有等待初始化**（本机是 `local` Profile，不装配账号服务；`accountSessionGateway` 只在 `MODE === "customer-demo"` 时注入，见 `frontend/src/main.tsx:51`）。

### 失败时他看到什么

只有一种：整屏「暂时无法连接业务服务 / 桌面应用已启动，但控制服务当前不可用。请检查本地服务或网络后重试。」+「重新检查」按钮。

**他能自己解决吗：**

- 如果是**开机竞态**（PostgreSQL 还没就绪，Control Plane 已经被问健康 → 503）：点「重新检查」等几秒就好。**这是唯一他能自救的情况，必须在交付时明确告诉他这一条**；
- 其他任何情况（服务挂了、数据目录损坏、端口被占）：**他解决不了**，界面上没有任何可操作项（`control_plane_unavailable` 不在 `LOCAL_DIAGNOSTICS` 里，连"打开本地修复工具"按钮都不会出现）。

**因此预置阶段必须做的两件事：**

1. LaunchAgent 加 `KeepAlive` 与启动顺序保证，让 PostgreSQL 先就绪；
2. 给领导一张纸（或一条微信）：「打开如果是红色页面，点一下『重新检查』；再不行打电话给我」。

---

## 6. 今晚新增的设备注册在这个形态下怎么工作

参见 `docs/development/FIX-production-device-registration.md`（提交 `93a6acf`）。

**机制**：本机 Control Plane 启动时（`bootstrap/cli.py:local_app`）在内存里生成一把 Ed25519 密钥，签一张 10 分钟有效的 bootstrap grant，把公钥配进注册服务，把签好的 grant 写成 `0600` 文件放进 App 私有目录（`local-registration-bootstrap-v1`），然后**丢弃私钥**。App 启动时若凭据保险箱为空，就读这个文件完成一次正常的挑战/设备证明注册，成功后删文件。

**在领导的电脑上成立吗：成立，但有三个必须遵守的条件。**

| 条件 | 说明 | 违反的后果 |
| --- | --- | --- |
| **1. 服务必须以领导本人的用户身份运行** | `local_app_data_directory()` 推导的是 `~/Library/Application Support/com.aventador.automationtool`。必须是 **LaunchAgent**（用户域），**不能是 LaunchDaemon**（系统域，HOME 是 root 的） | 交接文件写到 root 的目录，App 永远读不到，RPA 全链路死在 `credential_missing` |
| **2. 服务必须用 `automation-tool-control-plane` 入口启动** | 只有 `cli:local_app` 会签发；`container_cli` 不签发，直接 `uvicorn ... create_app` 也不签发 | 同上。注意 `scripts/run_eb_16_acceptance.py:505` 用的正是 `create_app()`，**不要照抄那段代码去起服务** |
| **3. 注册必须在服务启动后 10 分钟内完成** | grant 生命周期恰好 600 秒（`local_provisioning.py:LOCAL_BOOTSTRAP_LIFETIME`） | 过期后 App 保持未注册状态。App 仍能打开（未注册**不阻断**启动，这是 FIX 文档「真实边界」第 2 条的明确取舍），但点任何 RPA 功能都失败 |

**条件 3 在实践中是一次性问题**：预置时我们启动服务后立刻打开 App，注册在几秒内完成，凭据落进保险箱**永久有效**。此后 App 启动根本不再读那个文件（`vault 非空则不读文件`）。领导以后开机、重启、断网都不受影响。

**两个要提前知道的坑：**

- **服务每次启动都会重新签发一张新 grant 并原子替换旧文件**，注册服务只信最新那把公钥。所以"服务重启过 N 次"不会累积问题；
- **409 砖机态**：如果注册时凭据写盘失败（磁盘满/权限），服务端已有 Installation 而本机没有凭据，重试必定 409。App 会显示专门的「本机设备注册需要重置」诊断，恢复要**手工删除** `device-identity-ed25519-v1` 再重启服务和 App——**App 里没有这个按钮**。这一步领导绝对做不了，只能我们远程。预置阶段确认注册成功即可基本排除。

**在云端方案（方案三）里，这套机制不参与**：那条路走 U9-05 的账号登录 + 设备绑定，交接文件不存在。

---

## 7. 最短可行路径（从今天算起）

### 7.1 推荐路径：预置演示机

| 序 | 事项 | 估时 | 阻塞谁 |
| --- | --- | --- | --- |
| T1 | **决定动作步骤演不演** | 立刻 | 决定 T2 要不要重新构建 |
| T2 | 若演动作：用**保留的**密钥重新构建 DMG，并把 `LOCAL_ACTION_TASK_LIMIT` / `MINIMUM_INTERVAL` 调成演示可用值（§2.2） | 0.5 天 | T4 |
| T3 | 把 Control Plane 打成 PyInstaller 包（复用 `backend/automation-tool-executor.spec` 的现成路径，改入口为 `control_plane.bootstrap.cli:main`） | 1 天 | T4 |
| T4 | 解决 Gatekeeper（§2.1，A 或 B） | 0.5～2 天 | T5 |
| T5 | 预置领导机器（§5 交付时 8 步，含扫码与断网复测） | 0.5 天 | 演示 |
| T6 | 写一页给领导的操作说明 + 我们的应急联系流程 | 0.5 小时 | — |

**关键路径合计约 2.5～4 人日**，最大不确定性在 T4（有没有 Developer ID 证书）。

**必须在同一批次内做的台账工作**（CLAUDE.md §2.1 强制）：本文件转成正式任务 ID，在 `docs/development-roadmap.md` 建行并同步状态，第 100 行「本地/云端服务」那条要跟着改。

### 7.2 降级方案：如果 T3 或 T4 来不及

**降级形态**：不做 PyInstaller 包，不解决正式签名。改为——

1. 在领导机器上装 Python + uv + 项目源码（我们操作，他不碰）；
2. Control Plane 以 `uv run automation-tool-control-plane` 起，包进 LaunchAgent；
3. Gatekeeper 走手工放行（§2.1 方式 B）。

**妥协点，逐条写明：**

| 妥协 | 后果 | 演示时怎么避开 |
| --- | --- | --- |
| 领导机器上留了完整项目源码和 Python 环境 | 源码在客户现场的电脑上；也不符合"零 Python 前置"的 P9-06 完成定义 | 装在他的用户目录深处，别放桌面。**这台机器演示后应当回收清理** |
| App 是 ad-hoc 签名 + 手工放行 | 他若把 App 挪位置/重装会再次被拦 | **交付后不要动 App 的位置**，写进给他的说明 |
| 若跳过 T2，动作步骤演不了 | 演示只能到"发现 + 预览 + 人工接管" | 演示脚本**不要提"执行动作"**，把叙事落在"发现能力 + 人工接管的安全边界"上——后者本来就是我们相对竞品的卖点 |
| 只有一台机器可演 | 换机器要重来一遍 | 提前确认演示就用这一台，且**带上充电器**（浏览器 + 视频链路很吃电） |

**降级方案的关键路径约 1 人日**，可以作为 T3/T4 的兜底并行准备。

### 7.3 明确不做的事

- 不在下周尝试内置 PostgreSQL（§3 出路二）；
- 不在下周尝试云端部署（§4 方案三）；
- 不动 SQLite（§3 出路三，永久否决）；
- 不为了演示放宽任何门禁、不把演练当真实部署、不把 Mock 当验收。

---

## 8. 我没能查证的部分

本轮**未运行任何测试、未构建、未启动任何服务、未连接任何机器**，以下结论来自代码与文档静态阅读：

1. **Gatekeeper 的实际行为未实测。** 我确认了签名是 ad-hoc（`codesign --sign -`），但没有在第二台 Mac 上试过打开这份 DMG，也没有验证内置 Chromium framework 与 Executor 这些嵌套可执行文件在别的机器上会不会各弹一次拦截、hardened runtime 下有没有 library validation 问题。**这是 §2.1 的全部风险来源，也是整个方案里最该先动手实测的一条。**
2. **macOS 上 PostgreSQL 的可交付形态与体积全部是估计值。** Postgres.app 的实际体积、是否支持 arm64 免配置初始化、能否随登录静默启动；从源码 `--without-icu` 编译后的最小可用集有多大；PyPI 上打包 postgres 二进制的方案（如 `pgserver`）在 macOS arm64 上是否可用、版本是否兼容 35 个迁移——**一条都没验证过**。§3 出路二的"40～60 MiB"和出路一的"5 分钟"都是估计。
3. **Control Plane 的 PyInstaller 包体积未验证。** 我确认了 Executor 的 spec 入口是 `executor/__main__.py`，且 executor 侧不 import fastapi/sqlalchemy/alembic/asyncpg，因此 Control Plane 需要**新的一份**打包，不能白嫖现有的 177 MiB。但"50～80 MiB"是估计，也没验证 uvicorn/asyncpg/alembic 在 PyInstaller 下是否需要额外 hook。
4. **LaunchAgent 形态下的 `HOME` 与 App 私有目录推导未实测。** §6 条件 1 是从 `local_provisioning.local_app_data_directory()` 的代码推导的，没有在真实 LaunchAgent 环境里验证 `Path.home()` 一定解析成登录用户的目录。
5. **开机竞态的真实表现未实测。** "PostgreSQL 未就绪 → 健康 503 → 阻断页 → 点重新检查即恢复"是从 `api/system.py` + `StartupGate.tsx` 推的，没有真的模拟过一次开机顺序。
6. **重新构建并保留动作授权密钥的具体改法未设计。** 我只确认了密钥当前被丢弃（§2.2），没有设计"密钥从哪来、存在哪、怎么同时喂给构建和服务"的方案——那需要单独一轮，且涉及运维凭据管理。
7. **未查证领导那台 Mac 的具体情况**：芯片架构（arm64/x86_64，当前包只有一种架构）、macOS 版本、是否有 MDM 管控或磁盘加密策略、是否允许安装未公证软件。**这四条任何一条不满足都会推翻上面的时间估算，应当在动手前先问清楚。**
8. **未查证是否已有 Apple Developer 账号与 Developer ID 证书。** §7.1 的 T4 估时（0.5～2 天）完全取决于这一条。
