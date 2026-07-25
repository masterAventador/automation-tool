# PLAN：正式构建的首次设备注册链（方案稿，未实现）

- 状态：📝 方案待审定（本轮不写生产代码、不改现有文件）
- 日期：2026-07-25
- 触发：macOS 正式包点击「平台状态 → 抖音 → 打开登录处理」返回 `credential_missing`，整条 RPA 链路在本机不可用
- 结论预告：正式构建缺的不只是"一个注册命令"，而是**两个都没人负责的装配环节**——① 设备首次注册的 bootstrap 来源，② 本机 Control Plane 的交付形态。只补 ① 能让链路跑通，但 P9-06「打开即用」仍不成立

---

## 1. 现状事实（逐条带证据）

### 1.1 失败链路已确认

```
open_douyin_login                     frontend/src-tauri/src/lib.rs:1111
  → execute_douyin_login_command      frontend/src-tauri/src/lib.rs:1076
    → ensure_executor_running         frontend/src-tauri/src/lib.rs:1019
      → issue_executor_connection     frontend/src-tauri/src/control_plane.rs:818
        → exchange_device_session     frontend/src-tauri/src/control_plane.rs:950
          → required_credential       frontend/src-tauri/src/control_plane.rs:958 / 1760
```

`required_credential` 在 vault 为空时返回 `ControlPlaneErrorCode::CredentialMissing`（control_plane.rs:1769），经 `map_executor_connection_error`（lib.rs:845）变成 `credential_missing`。设备凭据文件名 `device-credential-v1`（device_credentials.rs:13），当前 App 私有目录中不存在。

### 1.2 服务端注册链是完整的，而且不弱

- 端点：`POST /api/v1/installations/registration-challenges` 与 `POST /api/v1/installations`，位于 `backend/src/automation_tool/control_plane/api/registrations.py:166,195`；
- 编排：`InstallationRegistrationService`（application/registration.py:203）验证 bootstrap → 32 字节 CSPRNG nonce → canonical signing payload → 一次性 challenge → Ed25519 设备证明 → 同事务创建 Installation 并签发 `atdc1` 设备凭据；
- bootstrap 形态：`atb1.<canonical-payload-base64url>.<ed25519-signature>`，由 `Ed25519BootstrapTokenVerifier`（infrastructure/security/bootstrap_tokens.py:92）**只验签不签发**；claims 固定 5 个字段 `environmentId/expiresAt/notBefore/purpose/version`，purpose 只有 `installation.register`（domain/demo_bootstrap.py:31），有效期硬上限 7 天（domain/demo_bootstrap.py:8）；
- 服务端只保存 token 的 SHA-256 指纹，不保存原 token（application/registration.py:261）。

**关键：Control Plane 不持有签发私钥。** 它只从部署配置读一把 32 字节验证公钥。

### 1.3 注册 API 默认关闭，只靠两个环境变量开启

`registration_service_from_environment`（bootstrap/registration.py:62-90）：

- 两个变量都缺 → 返回 `None`，注册服务不装配；
- 只给一个 → `RegistrationConfigurationError`，启动 fail closed；
- 变量名：`AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID`、`AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY`（bootstrap/registration.py:40-41）；
- 装配点：bootstrap/app.py:286-289；未装配时 `_service` 依赖返回 `503 registration_unavailable`（api/registrations.py:108-117）。

**本机当前实际状态：注册 API 是关的。** 现在监听 `127.0.0.1:8765` 的进程（PID 4320）由本会话的 `run_app_session.py` 启动，其环境来自 `scripts/run_eb_16_acceptance.py:623` 的 `isolated_database_environment()`——该函数**先剥掉所有 `AUTOMATION_TOOL_*`**，再只注入数据库四件套和 `DATABASE_URL`，没有注入两个 DEMO 变量。所以即使 App 侧有注册入口，今天这台服务也会回 503。

### 1.4 Rust 侧注册能力**存在**，但没有生产调用方

- `ControlPlaneClient::register_installation`（control_plane.rs:848）**没有任何 cfg 门控**，默认构建里就编译进去了；它自己做了"vault 非空则拒绝"（control_plane.rs:857）、设备签名、凭据写回 vault；
- `DemoBootstrap`（control_plane.rs:3413）同样无 cfg 门控，格式校验完整；
- 但生产 `invoke_handler`（lib.rs:3902-3963）里**没有任何注册命令**。唯一调用 `register_installation` 的 Tauri Command 全部在 `#[cfg(feature = "control-plane-e2e")]` 下（lib.rs:3571 `register_installation_for_revocation_acceptance`、lib.rs:3607 `run_control_plane_acceptance`、lib.rs:2043 等 15 处），且 token 来源一律是 `std::env::var("AUTOMATION_TOOL_<任务ID>_BOOTSTRAP_TOKEN")`。

**这正是全局「单一构建路径规范」第 94 行描述的事故形态**：验收构建从环境变量拿依赖（验收脚本现场签好再喂进去），生产构建没有对应装配路径，于是验收长期全绿而用户拿到的包功能为零。

### 1.5 U9-05 账号绑定链路是生产可用的，但要账号

- `login_product_account`（lib.rs:1601）**在生产 handler 中已注册**（lib.rs:3906）；
- 它登录成功后立刻调用 `bind_account_installation`（control_plane.rs:639），复用同一套 challenge/设备证明，把 Installation 原子绑定当前账号并把签发的 `atdc1` 写进同一个 vault（control_plane.rs:684）；绑定失败会回滚登录（lib.rs:1618）；
- 但 React 侧只有在 Vite `MODE === "customer-demo"` 时才构造 `TauriAccountSessionGateway`（frontend/src/main.tsx:51-52），local 构建根本不渲染登录界面（app/App.tsx:128-133）。

所以：**Rust 里已经有一条能签发设备凭据的生产路径，只是 P9 本地阶段按规则不能用它**（项目规则第 2 节：P9 以前不做产品注册/登录/账号）。

### 1.6 顺带查出的云端缺口（本方案必须一并处理）

- `deploy/customer-demo/compose.v1.json:44-45` 与 `scripts/deploy_customer_demo.py:51-52` 把 `DEMO_ENVIRONMENT_ID` / `DEMO_BOOTSTRAP_PUBLIC_KEY` 列为**必填**——也就是说客户 Demo 上线后 bootstrap 注册端点是**开着的**；
- `installations.owner_user_id` 可为空（account_installation_binding_repository.py:170 用 `owner_user_id.is_(None)` 判首次绑定）；
- 业务守卫 `require_current_installation_access`（api/installation_access.py:57-70）**只校验 device session，不校验 Installation 是否有 owner**。

推论：**在今天的 Demo 配置下，一份泄漏的 bootstrap token 就能注册出一个 `owner_user_id = NULL` 的 Installation，并用它调用全部业务 API，全程不需要产品账号。** 这与项目规则第 4.2 节「云端客户 Demo 必须使用产品账号认证，并把 Installation/设备凭据绑定到已登录账号」相冲突。它不是本次故障的原因，但任何设备注册方案都必须把它收口，否则等于默许两条云端入口。

### 1.7 第二个更大的缺口：本机 Control Plane 没有交付形态

- 架构基线明确：「Control Plane 从第一天就是独立进程和独立部署单元，不随 Tauri 安装包分发」（项目规则 4.2）；
- `frontend/src-tauri/tauri.conf.json` 里没有任何 `resources` / `externalBin` 指向 control plane；打包审计规则也只认 `local-executor/package`（docs/frontend-architecture.md P9-05 段）；
- 路线图第 100 行：「本地/云端服务 | ⬜ 开发验收会临时启动并清理本地服务；尚无常驻环境，未部署云端」；
- P9-06 / P9-07 的待办文字里，「可交付本地服务」与「首次设备注册链」是**并列的两项**（roadmap:561-562）。

也就是说：今天用户能点到那个按钮，是因为本会话临时起了一个开发用 Control Plane + 隔离 PostgreSQL 容器。**产品本身没有任何机制让普通用户获得这个服务。** 本方案只解决"凭据从哪来"；"服务从哪来"必须单独立项，否则 P9-06 的"零 Python 前置、打开即用"仍然不成立。

---

## 2. 候选方案

### 方案 A：本机服务签发一次性 bootstrap 交接文件（推荐）

#### 怎么工作

1. **签发侧（本机 Control Plane 的启动出口）**：本机启动时在内存生成 Ed25519 keypair → 把公钥交给**现有的同一条**注册装配路径（`registration_service_from_environment` 需从"只读环境变量"扩展为"读同一个配置对象，环境变量是其来源之一"，不是新增第二套注册实现）→ 用私钥签一张 `environmentId = local`、`purpose = installation.register`、有效期 ≤10 分钟的 `atb1` token → **私钥立刻丢弃，永不落盘** → 把 token 写进固定的 App 私有交接文件。
2. **交接文件**：位置为 App `app_data_dir` 根（与 `device-credential-v1` 同级），文件名版本化如 `local-registration-bootstrap-v1`；内容是 exact-field canonical JSON：`{"version":1,"environmentId":"local","token":"atb1...","expiresAt":<unix>}`；POSIX `0600` / 目录 `0700`，Windows 复用既有私有 ACL 适配器（secure_store.rs:222 已有 Windows 分支）。
3. **消费侧（App）**：在既有生产启动探针 `check_control_plane_health`（lib.rs:1506）内部，`check_installation_access_if_registered` 之前增加一步——**vault 为空时**尝试读交接文件 → 严格校验（symlink / 权限 / 大小 / canonical JSON / 未知字段 / 未过期 / token 与 environment 格式）→ 调用**已存在的** `register_installation`（control_plane.rs:848）→ 成功后立即删除交接文件。
4. **所有构建编译同一段代码**：默认、`desktop-e2e`、`control-plane-e2e`、local、demo 全部走这一条；没有新 cfg、没有新环境变量、没有 Vite mode 判断。e2e 只是把"谁写这个文件"换成验收脚本，产品代码逐字相同。

#### 六条约束逐条核对

| # | 约束 | 结论 | 说明 |
|---|---|---|---|
| 1 | 禁止两套业务实现 | ✅ | 复用同一 challenge/proof/凭据签发；本地与 Demo 差别只在"谁离线签发、公钥从哪配、token 怎么送达"，属于凭据与基础设施差异 |
| 2 | 不得降低云端门禁 | ✅（需配套改动） | 客户端这条路在 Demo 也会执行，但 Demo 服务端**不配置** bootstrap 公钥 → 503，且 Demo 机器上根本不会出现交接文件。**必须同时**把 `deploy/customer-demo/compose.v1.json:44-45` 与 `scripts/deploy_customer_demo.py:51-52` 的两个变量从「必填」改为「禁止出现」，让云端唯一注册入口收敛到 U9-05 账号绑定。这一改动同时修掉 §1.6 的既有缺口 |
| 3 | 不得引入构建期分叉 | ✅ | 无 `cfg`、无 `option_env!`、无 `import.meta.env.MODE`、无运行时环境变量。交接文件属于允许差异第 3 类"指向隔离实例的配置值"，读取代码路径唯一 |
| 4 | 凭据存储纪律 | ✅ | bootstrap token 只在 Rust 内存（`Zeroizing`）与 App 私有文件之间流转，不进 React、不进 Tauri Command 返回值、不进日志；设备凭据仍走既有 `vault.replace` |
| 5 | P9 不做产品账号 | ✅ | 无需登录，App 打开后仍直接进工作台 |
| 6 | fail-closed 且不泄漏 | ✅ | 文件缺失/过期/权限不对/服务端 503 → 保持未注册，UI 显示固定诊断文案，不显示路径、不显示 token、不回显底层错误 |

#### 威胁模型

- **远程攻击者**：本机 Control Plane 只绑 `127.0.0.1:8765`（backend/README.md「服务只绑定 127.0.0.1:8765」；deployment_profile.rs:14 `LOCAL_BASE_URL`）；交接文件只在本机文件系统。远程无可达路径。Demo 侧该端点关闭。
- **同机其他用户**：文件 `0600` + 目录 `0700`，非特权用户读不到。能读到的只有 root/管理员，而 root 本来就能读 `device-credential-v1` 和设备私钥 → **不引入新的提权面**。
- **令牌泄漏**：能力被三重收窄——purpose 只能注册（不能建任务、不能调业务 API）、environment 绑死 `local`、有效期 ≤10 分钟。最坏结果是在**本机**服务上多注册一个 Installation；而攻击者若已是同机同用户，本来就能直接操作 App。爆炸半径 = 本机这一个实例。
- **重放**：challenge 一次性消费（docs/backend-architecture.md:454），同一 token 在有效期内可为**不同设备公钥**注册多个 Installation。压制手段是短有效期 + App 用后即删 + 本机边界；不新增服务端状态。若审定认为不够，可加"签发后 N 分钟自动失效并轮换"，但不建议为此新增服务端一次性消费表（那是新业务状态）。
- **伪造文件**：攻击者可以往 AppData 塞一个自制 JSON，但 token 必须通过目标 Control Plane 配置的公钥验签；本机公钥是本次启动随机生成的，Demo 上根本没有配置。伪造只能得到固定拒绝。

#### 对云端 Demo 的影响

- 正向：把 Demo 的注册入口从"bootstrap OR 账号"收敛为"只有账号"，堵掉 §1.6 的无 owner Installation 缺口；
- 需要改 `deploy/customer-demo` 配置、部署脚本校验和 `deploy/secrets/inventory.v1.json` 清单，并回归 C10 部署门禁；
- U9-05 链路完全不动。

#### 实现工作量

中。后端一个 provisioning 出口 + 一份契约；Rust 一个读取器 + 一处启动挂钩；前端一个诊断态；部署配置收敛；测试 6~8 组。没有数据库迁移、没有新 HTTP 端点、没有新协议消息。

---

### 方案 B：本机 loopback 一次性握手端点（控制服务自签发凭据）

#### 怎么工作

本机 Control Plane 暴露 `POST /api/v1/local-registrations`，用启动时写到 App 私有文件的一次性 32 字节 secret 做 bearer 认证，直接创建 Installation 并返回设备凭据，跳过 `atb1` 与设备公钥证明。

#### 约束核对

| # | 约束 | 结论 |
|---|---|---|
| 1 | 禁止两套业务实现 | ❌ **违反**。Installation 创建与凭据签发出现第二条入口，绕过 `InstallationRegistrationService` 的 challenge/设备证明 |
| 2 | 不得降低云端门禁 | ⚠️ 依赖"云端务必不要启用"这一条配置纪律。新增了一个只要配错就是匿名写接口的攻击面，与"禁止裸露匿名写接口"的精神相悖 |
| 3 | 无构建期分叉 | ✅ |
| 4 | 凭据存储纪律 | ✅ |
| 5 | P9 不做账号 | ✅ |
| 6 | fail-closed | 可做到 |

#### 威胁模型差异

本机文件信任度与 A 相同，但**丢掉了设备公钥持有证明**——凭据只绑到"谁读到了那个 secret"，不再绑到 App 私有身份私钥。若把设备证明加回来，就等于重写了一遍 A 已有的流程。另外多出一个可被误配置暴露的服务端端点。

#### 结论

不推荐。工作量不比 A 小（新端点 + DTO + 契约 + 威胁模型 + 部署校验），安全性更弱，且直接踩约束 1。

---

### 方案 C：本地也走产品账号（把 U9-05 变成唯一注册路径）

#### 怎么工作

取消 P9「本地不做账号」的前提，本机 Control Plane 预置一个由运维入口创建的本地账号，App 首次启动要求登录，之后一切复用 `login_product_account` → `bind_account_installation`。同时删掉 main.tsx:51-52 的 `MODE === "customer-demo"` 分叉。

#### 约束核对

| # | 约束 | 结论 |
|---|---|---|
| 1 | 禁止两套业务实现 | ✅ **最强**。全产品只剩一条注册路径 |
| 2 | 云端门禁 | ✅ 不变 |
| 3 | 无构建期分叉 | ✅ 顺带消灭 main.tsx 里现存的 Vite mode 分叉（那处目前正是"构建模式决定产品路径"的实例） |
| 4 | 凭据纪律 | ✅ |
| 5 | P9 不做产品账号 | ❌ **直接违反**。项目规则第 2 节写死「App 打开后直接进入 RPA 运营工作台」「暂不做产品注册、登录、用户」 |
| 6 | fail-closed | ✅ |

#### 结论

**本轮不采纳，但应记录为 U9 完成后的终局方向。** 它是唯一能彻底消除 local/demo 双入口的答案；代价是必须先修改产品阶段边界，并解决"本地账号从哪来、离线能不能用、密码谁管"这些新问题。若主会话认为 P9 边界可以调整，这是比 A 更干净的长期解。

---

### 已考虑并否决的其他做法

| 做法 | 否决理由 |
|---|---|
| 生产构建加 `#[cfg]` 分支去注册 | 全局「单一构建路径规范」明令禁止，且正是本次事故的成因 |
| 沿用 `AUTOMATION_TOOL_*_BOOTSTRAP_TOKEN` 环境变量 | 同上；且用户环境里根本不会有这个变量 |
| 配对码 / 用户手抄短码 | 项目规则 4.2、docs/backend-architecture.md:476 明确排除配对码与逐设备审批；且 secret 会经过 React |
| App 内粘贴 token 输入框 | secret 经 WebView，且要求用户理解 bootstrap 概念；只能作为 A 的降级备份 |
| 把 bootstrap 私钥编进安装包 | 私钥进产物 = 全体安装共享一把签发密钥，泄漏即全线失守；违反 §1.2 的"服务端不持有签发私钥"设计前提 |
| App 自己起 Control Plane 作为 Sidecar | 违反架构基线「Control Plane 不随 Tauri 安装包分发」。**但它可能才是 P9-06「打开即用」的真答案**，需要独立 ADR，见 §5 |

---

## 3. 推荐与最大风险

### 推荐：方案 A + 一条必须同时立项的伴生任务

采纳 A 的理由：

1. 它是**唯一不新增业务实现、不新增端点、不新增构建分叉**的做法；
2. 它把差异压在"配置值与送达方式"这一层，正好落在全局规则允许的第 3 类差异里；
3. 它顺手把云端那条无 owner 的 bootstrap 入口关掉，净效果是**安全性提升**而非下降；
4. 服务端零改动风险——`InstallationRegistrationService` 一行不动。

### 最大风险（按严重度排序）

1. **【最高】只做 A 会造出"看起来修好了"的假象。** 用户桌面上的正式包依然没有 Control Plane 可连；A 只保证"服务在的时候能自动注册"。若不同时立项本地服务交付，P9-06 仍然过不去，而台账可能被误改绿——这和今天暴露的问题是同一种病。**建议：A 的任务完成定义里必须写明"不覆盖本地服务交付"，并同时新建一个本地服务交付任务。**
2. **【高】注册成功但本机写 vault 失败 → 不可恢复。** `register_installation` 先拿到服务端签发的凭据，再 `vault.replace`；若写盘失败（磁盘满 / 权限），服务端已存在一个 active Installation 与 active 凭据，而本机什么都没有。重试会撞 `installation_exists`（409，设备公钥唯一），因为设备私钥没变。**必须**为这条设计明确恢复路径：把 409 映射为独立诊断（"本机安装记录与服务不一致，需要重置本机数据"），并在文档写清"删除设备身份文件 → 新设备公钥 → 需要一张新 bootstrap"。这条如果不处理，会变成偶发但永久的砖机态。
3. **【中】交接文件由后端进程写入 App 私有目录，是一次轻微的层次倒置。** 后端因此需要知道 App identifier 与各平台 AppData 约定（`scripts/run_i2_09_acceptance.py:55` 已经这么干了，但那是测试脚本）。缓解：把路径规则、文件格式、权限、有效期全部冻结进 `contracts/`，两端各自实现、共用契约门禁；App 侧一律以"外部不可信输入"对待并做完整校验。
4. **【中】本机服务若以容器运行则写不进用户 AppData。** 当前本机服务是用户态 `uv run` 进程，可以写；一旦本地服务改为 Docker 交付，A 的送达方式要重新设计。这条与风险 1 耦合——本地服务交付形态定下来之前，A 的送达方式存在返工可能。
5. **【低】同一 token 在有效期内可注册多个 Installation。** 已由 ≤10 分钟有效期 + 用后即删 + 本机边界压制，且攻击者需已在本机同用户上下文。若审定要求更严，代价是新增服务端一次性消费状态（新业务状态，不建议）。

---

## 4. 实现拆解（若采纳 A）

### 4.1 涉及文件与顺序

**阶段一：契约先行**

1. `contracts/protocol/local-registration-handoff-v1.json`（新建）——冻结文件名、路径推导规则、exact 字段集、最大字节数、最长有效期、权限要求、失效语义。两端门禁都读它。

**阶段二：后端签发侧**

2. `backend/src/automation_tool/control_plane/bootstrap/registration.py`（改）——把公钥来源从"只读环境变量"扩展为"读同一个配置对象"，环境变量仍是来源之一；保持"两项要么都有要么都无、否则 fail closed"的既有语义。
3. `backend/src/automation_tool/control_plane/bootstrap/local_provisioning.py`（新建）——内存生成 keypair、签 `atb1`、写交接文件、丢弃私钥。**只做签发与落盘，不碰注册业务。**
4. `backend/src/automation_tool/control_plane/bootstrap/cli.py`（改）——本机 CLI 启动时调用上一步；容器入口不调用。

**阶段三：Rust 消费侧**

5. `frontend/src-tauri/src/secure_store.rs`（改，优先复用而非新建）——补一个"只读外部私有文件"的入口，复用既有 symlink/权限/大小校验。
6. `frontend/src-tauri/src/local_registration.rs`（新建）——解析 + 校验 + 过期判断 + 用后删除；**不含网络调用**。
7. `frontend/src-tauri/src/lib.rs`（改）——在 `check_control_plane_health`（1506）里，`check_installation_access_if_registered` 之前插入"vault 为空则尝试交接注册"，复用 control_plane.rs:848；新增错误码映射（含 `installation_exists` → 独立诊断）。

**阶段四：前端投影**

8. `frontend/src/app/startup.ts`（改）——`StartupDiagnosticCode` 增加 `installation_registration_required` 与 `installation_conflict`。
9. `frontend/src/app/StartupGate.tsx`（改）——两条固定中文诊断文案，不含路径、不含 token、不含底层错误。

**阶段五：云端收口**

10. `deploy/customer-demo/compose.v1.json`（改）+ `scripts/deploy_customer_demo.py`（改）——两个 DEMO 变量由必填改为禁止；`deploy/secrets/inventory.v1.json` 同步。
11. `backend/src/automation_tool/control_plane/api/installation_access.py`（评估）——是否让云端业务守卫额外要求 `owner_user_id` 非空。**这一条建议拆成独立任务**，因为它会影响所有业务路由的鉴权语义，不该夹在注册任务里。

**阶段六：文档与台账**

12. `docs/backend-architecture.md` §9.2、`docs/frontend-architecture.md` §14、`backend/README.md`、`docs/development-roadmap.md`（新任务行 + 当前下一步）、`docs/development/<新任务ID>.md`。

### 4.2 TDD 顺序（每步先 RED，看到失败再写实现）

| 步 | 层 | 先写的失败测试 | 命令 |
|---|---|---|---|
| 1 | 后端单元 | provisioning 产出的 claims 字段精确、purpose/environment 正确、有效期 ≤10 分钟、私钥不落盘、文件权限 0600、原子替换、二次启动轮换旧文件 | `uv run pytest tests/unit/control_plane/test_local_provisioning.py` |
| 2 | 后端集成（真实 PostgreSQL） | 用 provisioning 出的 token 走**真实** `/registration-challenges` + `/installations`，拿到 `atdc1`；过期 token、跨环境、challenge 重放分别拒绝 | `uv run pytest tests/integration/test_local_provisioning_registration.py` |
| 3 | Rust 单元 | 交接文件解析器：缺失 / 空 / 超长 / 非 canonical JSON / 未知字段 / 重复 key / 已过期 / symlink / group 或 other 可读 / 非普通文件 / token 格式非法 / environment 非法 —— 全部固定错误且不回显内容 | `cargo test local_registration` |
| 4 | Rust 单元 | 注册编排：vault 非空时不读文件；成功后删除文件；服务端 503 时保持未注册且**保留**文件；`vault.replace` 失败时不删文件并返回 outcome-uncertain；409 映射为冲突诊断 | `cargo test`（默认 / `desktop-e2e` / `control-plane-e2e` 三套配置各跑一次） |
| 5 | 前端单元 | 两个新诊断码的投影与文案；文案不含路径/token | `pnpm --dir frontend test` |
| 6 | UI Harness | 未注册 → 显示"需要完成设备注册" → 「重新检查」后进入工作台（受控 Adapter） | `pnpm --dir frontend test:e2e` |
| 7 | 真实 Tauri E2E | 隔离 PostgreSQL + 真实 Uvicorn + provisioning 生成**真实**交接文件 → 隐藏 App 从**正式启动入口**完成注册 → 断言：DB 内 Installation/凭据存在、AppData 出现 `device-credential-v1`、交接文件已删除 | 新 runner `scripts/run_<任务ID>_acceptance.py` |
| 8 | 生产同路径验收 | macOS 正式包 + 本机服务，用户点「打开登录处理」，浏览器真实拉起 | 人工 + runner 留证 |

TDD 铁律提醒：第 7 步的 runner 只负责"扮演本机服务写文件"，**不得**给产品代码加任何测试专用入口；产品侧读取代码必须与用户路径逐字相同。

### 4.3 失败矩阵（必须覆盖）

**交接文件侧**：不存在 / 空 / 超过大小上限 / 非 UTF-8 / JSON 损坏 / 未知字段 / 重复 JSON key / 缺字段 / `version` 不为 1 / 已过期 / `notBefore` 未到 / environmentId 非法 slug / token 段数或前缀错 / 文件是 symlink / 是目录 / 权限过宽 / 属主不是当前用户 / 读到一半被替换。

**服务端侧**：注册服务未装配（503）/ token 验签失败（401）/ 环境不匹配（403）/ challenge 未知或过期（410）/ challenge 已消费（409）/ 设备公钥已注册（409 `installation_exists`）/ 数据库不可用（503）。

**并发与恢复**：两个 App 实例同时启动同时注册（只能一个赢，另一个必须得到确定性拒绝而不是脏状态）/ 注册请求发出后断网（结果不确定，不得自动重放到产生第二个 Installation）/ 注册成功但写 vault 失败（见风险 2）/ App 在两步之间被杀 / 电脑休眠导致 challenge 过期 / App 重启后交接文件仍在但已过期。

**边界与泄漏**：任何错误路径都不得把 token、文件路径、AppData 绝对路径、底层异常写进日志、错误响应或 React；诊断文案固定。

**平台**：macOS 与 Windows 分别验收（Windows 走 ACL 而非 mode bits，必须单独证明"其他用户不可读"）。

### 4.4 完成定义

- 上述 8 步全绿，三套 Rust 配置、后端全量、前端四层门禁通过；
- macOS 正式包上完成一次真实用户路径验收（打开 App → 自动注册 → 点「打开登录处理」→ 内置 Chromium 真实拉起）；
- 云端部署配置收口并跑通 C10 部署门禁；
- 同一提交内更新架构文档、README、路线图与 `docs/development/<任务ID>.md`；
- **不得**因为本任务把 P9-06 / P9-09 改绿——本地服务交付仍缺。

---

## 5. 必须同时立项的伴生任务（建议）

| 建议任务 | 内容 | 为什么不能并进本任务 |
|---|---|---|
| 本地 Control Plane 交付形态定义（ADR） | 普通用户怎么获得并运行本机 Control Plane 与 PostgreSQL？是随包分发的 Sidecar、独立安装器、还是嵌入式存储？现行架构基线禁止随包分发，若要改必须先改基线 | 这是架构决策，涉及打包审计、安全边界与 P9-06 完成定义，范围远超一次注册修复 |
| 云端 Installation 归属强校验 | 业务守卫要求 `owner_user_id` 非空 | 影响全部业务路由鉴权语义，需要独立失败矩阵与迁移评估 |

---

## 6. 我没能查证的部分

1. **Windows 私有 ACL 的实际覆盖面**。`secure_store.rs:222` 有 Windows 分支且用了 `windows_sys` 文件 API，但我只逐行读了 unix 的 `0600/0700` 实现，没有核对 Windows 分支是否真能保证"同机其他普通用户不可读"。方案 A 的同机威胁结论在 Windows 上**待验证**。
2. **本地 Control Plane 的目标交付形态完全没有设计文档**。我在 `docs/`、`deploy/`、路线图里都没找到任何描述"用户怎么得到这个服务"的任务或 ADR，只找到"尚无常驻环境"这一句状态。§5 的建议基于这个空白，不是基于已有决策。
3. **PostgreSQL 在本地 MVP 的交付方式同样没查到**。项目规则写死"数据库从第一天使用 PostgreSQL，开发使用本机 Docker"，但没有任何任务说明终端用户机器上的 PostgreSQL 从哪来。这会直接影响方案 A 的送达方式（容器写不进用户 AppData）。
4. **`registration_service_from_environment` 的配置来源改造对既有 15 个验收脚本的影响面**，我只做了 grep 计数（`scripts/run_*_acceptance.py` 中 12 处注入两个 DEMO 变量），没有逐个确认改造后它们是否仍按原样工作。
5. **`bind_account_installation` 在 Installation 已由 bootstrap 注册（owner 为空）时的行为**，我读到 `account_installation_binding_repository.py:160-173` 会认领空 owner，但没有验证"先 bootstrap 注册、后账号登录"这条混合顺序是否有测试覆盖。这条与云端收口方案相关。
6. **未运行任何测试、未构建、未启动 App**（本轮硬性约束）。以上全部结论来自代码与文档静态阅读，实现前仍需按 §4.2 从 RED 开始重新验证每一条。
