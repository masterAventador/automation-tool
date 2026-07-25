# FIX 正式构建的首次设备注册链

> 状态：🔍 待验收（后端签发、Rust 消费、409 恢复诊断与四层单元/集成门禁已落地并全绿；缺一次 macOS 正式包上的用户路径验收，也缺真实 Tauri E2E）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：用户在 macOS 正式包点「平台状态 → 抖音 → 打开登录处理」，返回 `credential_missing`，整条 RPA 链路在本机不可用
>
> 方案：`docs/development/PLAN-production-device-registration.md` 方案 A（本机服务签发一次性 bootstrap 交接文件）

## 缺陷

`app_data_dir` 下没有 `device-credential-v1`，设备从未注册。链路死在第一步：

```
open_douyin_login              lib.rs
  → execute_douyin_login_command
    → ensure_executor_running
      → issue_executor_connection
        → exchange_device_session
          → required_credential      → CredentialMissing → "credential_missing"
```

根因不是"某个命令写错了"，而是**正式构建里根本没有产生凭据的路径**：唯一调用
`ControlPlaneClient::register_installation` 的 Tauri Command 全部在 `#[cfg(feature = "control-plane-e2e")]`
下，且 bootstrap token 一律来自 `AUTOMATION_TOOL_<任务ID>_BOOTSTRAP_TOKEN` 环境变量——用户机器上不会有这个
变量。验收脚本现场签好 token 喂进测试构建，于是验收长期全绿，用户拿到的包功能为零。这与「单一构建路径规范」
第 94 行描述的事故形态完全一致。

关键前提（本次没有新写注册逻辑的原因）：`register_installation`（control_plane.rs:848）与
`DemoBootstrap`（control_plane.rs:3413）**都没有 cfg 门控**，默认构建就编译进去了。缺的只是
**生产调用方**和 **token 来源**。

## RED（逐条实跑，不是脑补）

### 后端签发侧

```text
uv run --locked pytest tests/unit/control_plane/test_local_provisioning.py -q
  ModuleNotFoundError: No module named
  'automation_tool.control_plane.bootstrap.local_provisioning'
  1 error in 0.30s
```

### 后端装配侧

```text
uv run --locked pytest tests/unit/control_plane/test_registration_configuration.py -q
  TypeError: registration_service_from_environment() got an unexpected keyword argument 'provisioned'
  TypeError: create_app() got an unexpected keyword argument 'local_registration_bootstrap'
  3 failed, 12 passed in 0.45s

uv run --locked pytest tests/unit/control_plane/test_cli.py -q
  ImportError: cannot import name 'local_app' from
  'automation_tool.control_plane.bootstrap.cli'
```

### Rust：409 砖机态的缺陷证据（对改动前的行为，真实断言失败而非编译失败）

先只加 `ControlPlaneErrorCode::InstallationConflict` 变体与两处映射、**不加**
`validate_response_metadata` 分支，跑：

```text
cargo test --lib response_metadata_requires_matching_correlation_json_and_no_store

thread '...' panicked at src/control_plane.rs:4525:9:
assertion `left == right` failed: a service that already registered this device must be
distinguishable from an ordinary rejection, or a failed credential write becomes a
permanently unexplained failure
  left: RequestRejected
 right: InstallationConflict
test result: FAILED. 0 passed; 1 failed
```

即：改动前，`installation_exists`（409）与任何普通拒绝一样被压成 `operation_unavailable`，
用户永远得不到可行动的解释。

### Rust：交接文件读取器与注册编排

```text
cargo test --lib local_registration
  error[E0432]: unresolved imports `super::parse_local_registration_handoff`,
  `super::LocalRegistrationHandoffStore`, `super::LOCAL_REGISTRATION_HANDOFF_FILE_NAME`, ...

（编排一轮）
  error[E0432]: unresolved imports `super::ensure_installation_registered`,
  `super::InstallationRegistrar`, `super::InstallationRegistrationOutcome`
```

### 前端

```text
npx vitest run src/api/control-plane/transport.test.ts src/app/startup.test.ts
  - "code": "installation_conflict"     - "retryable": false
  + "code": "transport_unavailable"     + "retryable": true
  Tests  2 failed | 14 passed (16)

npx vitest run src/app/App.test.tsx
  Unable to find role="heading" and name "本机设备注册需要重置"
  Tests  1 failed | 10 passed (11)
```

## GREEN（条数逐次核对）

| 门禁 | 改动前 | 改动后 |
| --- | --- | --- |
| `cargo test`（全量） | 328 passed | **343 passed, 0 failed**（+15，全部在 `local_registration`；409 断言并入既有用例） |
| `cargo test --test single_build_path` | 7 passed | **7 passed**（无新增豁免项） |
| `uv run pytest tests/unit` | 3151 passed, 1 skipped | **3164 passed, 1 skipped**（+13） |
| `uv run pytest tests/integration/test_local_provisioning_registration.py` | 文件不存在 | **5 passed**（真实 PostgreSQL） |
| `npx vitest run` | — | **449 passed \| 1 expected fail**（58 文件） |
| `node --test tests/*.test.mjs` | — | **219 pass, 0 fail** |
| `npx tsc -b` | — | 无输出，exit 0 |
| `npx eslint . --max-warnings 0` | — | 无输出 |
| `uv run ruff check` / `ruff format --check` / `mypy`（本次改动文件） | — | 全通过；仓库既有的 31 个 ruff 与 9 个 mypy 报错均在未触及文件，未新增 |

## 交付

### 一、契约先行 `contracts/protocol/local-registration-handoff-v1.json`

冻结文件名、App identifier、environmentId、字段集合、canonical 形式、大小上限、有效期上限、
权限要求、签发/消费策略与 13 条失败策略。**两端都有门禁读它**：

- Python `test_frozen_contract_governs_every_local_provisioning_constant`
- Rust `the_frozen_contract_governs_every_handoff_constant`（`../../contracts/...` 相对 `CARGO_MANIFEST_DIR`）

### 二、后端签发 `backend/.../bootstrap/local_provisioning.py`（新增）

启动时内存生成 Ed25519 keypair → 用**既有 `DemoBootstrapGrant` 领域对象**构造 claims（因此 7 天上限等
不变量复用而非重写）→ 签 `atb1` → **`del signer`，私钥永不落盘** → canonical JSON 原子写入
`0600` 文件、目录 `0700`。返回值只有 `environment_id` + `public_key`（`slots=True` 的 frozen dataclass，
结构上装不下私钥）。

- environmentId 固定 `local`，purpose 只有 `installation.register`，有效期**恰好 600 秒**；
- `local_app_data_directory()` 按平台推导 `com.aventador.automationtool`（darwin/win32/XDG）。

### 三、后端装配（改）

- `bootstrap/registration.py`：`registration_service_from_environment(database, *, provisioned=None)`。
  **一个服务只允许一个信任来源**——部署配环境变量对，loopback 启动传入刚签发的公钥；两者同时出现
  fail closed（否则两把公钥都能对同一服务注册）。既有"要么都有要么都无"的语义原样保留；
- `bootstrap/app.py`：`create_app(..., local_registration_bootstrap=None)`，只在既有那一处
  `registration_service_from_environment` 调用点透传，未新增第二条装配分支；
- `bootstrap/cli.py`：新增 `local_app()` 工厂——先签发再 `create_app(...)`；uvicorn 指向
  `cli:local_app`。**`container_cli.py` 一行未改**，云端入口不签发（并有源码断言守住）。

### 四、Rust 消费 `frontend/src-tauri/src/local_registration.rs`（新增）

把交接文件当**外部不可信输入**：

- 复用 `AppDataSecretStore`（symlink / 非普通文件 / 权限过宽 / 超长 / 缺失语义全部现成），不新写文件读取；
- **canonical 判据是字节相等**：反序列化后再序列化，与原始字节逐字节比对。一次性挡掉重复 key、
  键序调整、缩进美化、尾随空白——比逐条检查更难绕过；
- token 格式**不另写校验器**，直接交给既有 `DemoBootstrap::new`；
- **跨语言字节对齐有专门用例**：`a_document_the_local_service_really_wrote_is_accepted_unchanged`
  钉住一份由 Python 签发器真实产出、逐字节记录的文档（grant 早已过期、签名私钥当场丢弃）。两端各自
  对着契约写，只有这条用例能证明签发方的实际输出扛得住消费方的 canonical 判据；
- `LocalRegistrationHandoff` 手写 `Debug`，只打印类型名——派生实现会把 token 打进 panic/断言输出；
- `ensure_installation_registered` 是编排：vault 非空则**根本不读文件**；成功才 `consume()`；
  失败与冲突都**保留**交接文件。

### 五、Rust 409 独立诊断（改 `control_plane.rs` / `lib.rs`）

- 新增 `ControlPlaneErrorCode::InstallationConflict`；
- `validate_response_metadata`：`CompleteInstallationRegistration` + 409 → `InstallationConflict`。
  只对这一个 operation 生效（`IssueInstallationRegistrationChallenge`、`CreateTask` 的 409 仍是
  `RequestRejected`，有断言守住）。App 每次都用刚签发的 challenge，因此它能撞上的 409 只有
  `installation_exists`；
- `map_control_plane_error` / `map_executor_connection_error` → `"installation_conflict"`；
- `check_control_plane_health`（`not(desktop-e2e)` 变体）在 `check_installation_access_if_registered`
  **之前**插入注册尝试。该函数早已在 `single_build_path.rs` 的
  `REVIEWED_FEATURE_FORKED_FUNCTIONS` 白名单中，本次**未新增任何豁免项**；
- `run()` 在既有生产块内 `app.manage(initialize_local_registration_handoff_store(&app_data_directory)?)`。

### 六、前端诊断

- `transport.ts`：新增 `installation_conflict` 码与固定英文技术消息；
- `control-plane-transport.ts`：`installationAccessError` 泛化为 `preservedNativeError` +
  `PRESERVED_NATIVE_CODES` 白名单（两个码），其余一律仍是不透明 `transport_unavailable`；
- `startup.ts`：新增 `installation_conflict` 诊断码 + `controlPlaneDiagnostic()` 显式 switch；
- `StartupGate.tsx`：标题「本机设备注册需要重置」、副标题「重试不会恢复…」、卡片给出重置步骤。
  **不进 `LOCAL_DIAGNOSTICS`**——它不是本机组件问题，不该打开本地修复工具。

## 409 砖机态的恢复路径（方案文档风险 2）

`register_installation` 先拿到服务端签发的凭据，再 `vault.replace`。写盘失败（磁盘满 / 权限）时服务端已有
一个 active Installation 与 active 凭据，本机什么都没有；因为设备公钥没变，重试必撞 409
`installation_exists`。设计如下：

| 环节 | 行为 | 测试 |
| --- | --- | --- |
| 写 vault 失败当次 | `register_installation` 已把 `SecureStoreUnavailable` 映射成 `OutcomeUncertain`；编排返回 `Failed`，**不删交接文件** | `a_refused_or_unreachable_service_keeps_the_grant_for_the_next_start`（含 `OutcomeUncertain`） |
| 下次启动重试 | 服务端 409 → `InstallationConflict`（不再混进 `operation_unavailable`） | `response_metadata_requires_matching_correlation_json_and_no_store` |
| 编排 | 返回 `Conflict`，**保留**交接文件（恢复要换的是设备身份，不是 grant；grant 仍在有效期内就还能用） | `a_service_that_already_owns_this_device_key_is_reported_as_a_conflict` |
| 用户可见 | 独立诊断，明说"重试无法恢复"并给出重置步骤，不显示路径 / token / 底层异常 | `App.test.tsx` 新增用例 + `startup.test.ts` 新增用例 |
| 服务端语义 | 409 `installation_exists` 且 Installation 数不变 | `test_reregistering_the_same_device_key_is_refused_as_a_conflict`（真实 PostgreSQL） |
| 恢复动作有效性 | 换一把设备公钥即可再次注册成功，无需新 bootstrap | `test_a_fresh_device_key_recovers_after_the_conflict`（真实 PostgreSQL） |

**恢复步骤（写进诊断文案的具体含义）**：退出 App → 删除 App 私有目录下的设备身份文件
`device-identity-ed25519-v1` → 重启本地 Control Plane（会签发新交接文件）→ 重启 App。
App 用新公钥注册，与服务端上那条孤儿 Installation 不再冲突。

> 注意：这一步目前**由用户手工执行**，App 内没有"重置设备身份"按钮。见「真实边界」第 3 条。

## 失败矩阵

### 交接文件侧（Rust，`local_registration.rs`）

不存在（不是失败，保持未注册）/ 空 / 超过 4096 字节 / 非 UTF-8 / JSON 损坏 / `null` / 数组 /
未知字段 / **重复 key** / 缺字段 / 键序调整 / 美化缩进 / 尾随空白 / `version != 1` /
environmentId 非 `local` / token 前缀非 `atb1` / token 段数不足 / token 段为空 /
`expiresAt` 类型错 / 已过期（边界为闭区间：`expiresAt == now` 即拒绝）/ symlink / 非普通文件 /
group 或 other 可读 —— 全部固定错误码，`Display` 恒为 `local registration handoff is unusable`，
不回显任何内容。

### 编排侧（Rust）

vault 已有凭据 → 不读文件、不发请求；vault 读不出 → `Failed` 且不碰文件；
服务不可达 / 拒绝 / `OutcomeUncertain` → `Failed` 且**保留**文件；成功 → `Registered` 且删除文件
（删除失败不回退已存好的凭据）；409 → `Conflict` 且保留文件。

### 服务端侧（Python，真实 PostgreSQL）

grant 有效 → 201 + `atdc1`；同一设备公钥重复注册 → 409 `installation_exists` 且 Installation 数不变；
新设备公钥 → 201；grant 过期 → 403 `bootstrap_denied` 且 Installation 数为 0；
上一次启动的 grant（公钥已轮换）→ 401 `bootstrap_invalid` 且 Installation 数为 0。

### 签发侧（Python）

App 私有目录被普通文件占用 → `LocalProvisioningUnavailable` 且异常文本不含路径；
二次启动 → 公钥与 token 都轮换、旧文件被原子替换、无残留临时文件；私钥不落盘（目录里只有交接文件一个条目）。

### 未覆盖

**Windows**：`AppDataSecretStore` 的 Windows 分支只保证原子替换，`ensure_private_file_permissions`
在非 unix 直接返回 `Ok`——即"同机其他普通用户不可读"这一条在 Windows 上**没有产品级保证，也没有测试**。
这是方案文档 §6.1 已经提出、本次仍未查证的问题。Python 侧同样跳过了 `chmod`。

## 正常用户路径验收

**未完成。** 已完成与仍缺的分开记：

### 已完成

- **真实 PostgreSQL 上走真实注册 HTTP 端点**（不是 mock、不是进程内直调 service）：
  `create_app()` 从环境构建 → `TestClient` → `POST /api/v1/installations/registration-challenges`
  → `POST /api/v1/installations` → 拿到 `atdc1` 并断言库里 Installation 计数。5 个用例全绿。
- **release profile 真实编译**：`cargo build --release` 在本仓库默认环境下会被 `build.rs` 的
  发布密钥门禁拦住（`release Executor verification key is required`，与本次改动无关的既有设计）。
  用一次性合成的 Ed25519 执行器公钥 + minisign 更新公钥（取自 `run_p9_04_acceptance.py` 的同款
  fixture）、隔离 `CARGO_TARGET_DIR` 重跑：`Finished 'release' profile [optimized] in 53.85s`，
  产出 21 MB 二进制。即注册代码确实进得了 release 编译。
- `cargo build --features control-plane-e2e`：`Finished 'dev' profile in 10.06s`。

### 仍缺

1. **macOS 正式包上的用户路径**：打开 App → 自动注册 → 点「打开登录处理」→ 内置 Chromium 真实拉起。
   未执行（本次任务禁止启动 App / 浏览器 / 打包）。
2. **真实 Tauri E2E**：没有新增 WDIO 用例覆盖"隔离 PostgreSQL + 真实 uvicorn + 真实交接文件 →
   隐藏 App 从正式启动入口完成注册 → 断言 AppData 出现 `device-credential-v1` 且交接文件已删除"。
   方案 §4.2 第 7 步未做。
3. **Playwright UI Harness**：未新增用例，诊断只经 Vitest + Testing Library 验证。

## 真实边界

1. **不覆盖本地 Control Plane 的交付形态。** 这是本任务完成定义里写死的排除项。方案 A 只保证
   "服务在的时候能自动注册"；用户桌面上的正式包**依然没有 Control Plane 可连**，也没有 PostgreSQL。
   因此本任务**不得**把 P9-06 / P9-09 改绿，本文件也没有改它们。
2. **"未注册"依然不阻断启动。** 方案 §4.1 第 8 步提到的 `installation_registration_required` 诊断
   **没有实现**。理由：唯一会命中它的场景就是"本机没有 Control Plane"——那正是今天所有用户机器的状态，
   把它变成阻断会让 App 从"能打开、部分功能失败"退化成"打不开"，而修复它属于第 1 条的范围。
   本次只把**不可自愈**的 409 做成阻断诊断。取舍已记录，不是遗漏。
3. **没有 App 内的设备身份重置入口。** 409 的恢复要求用户手工删除 `device-identity-ed25519-v1`。
   诊断文案说明了"重置本机设备身份"，但没有一个按钮能做这件事。
4. **Windows 私有性未验证**（见失败矩阵「未覆盖」）。
5. **同一 grant 在 10 分钟内可为不同设备公钥注册多个 Installation。** 服务端 challenge 是一次性的，
   但 token 本身不是。压制手段只有：≤10 分钟有效期 + App 用后即删 + 只绑 `local` 环境 + 只在本机文件系统。
   攻击者需要已经是本机同一用户——那时他本来就能直接操作 App。未新增服务端一次性消费状态（那是新业务状态）。
6. **集成测试的 RED 顺序不纯。** `test_local_provisioning_registration.py` 是在后端单元 GREEN **之后**
   写的，所以它从第一次运行就是绿的。它的定位是**生产同路径回归护栏**（证明 `create_app` 这条正式装配
   链路和真实 HTTP 端点能走通），缺陷证据是上面单元层那两条真实 RED。这里如实标注，不冒充 RED。
7. **`registration_service_from_environment` 的签名变化对既有 15 个验收脚本的影响未逐一验证。**
   新参数有默认值 `None`，且所有验收脚本都直接 `uvicorn ... automation_tool.control_plane:create_app`
   并自行注入两个 DEMO 环境变量（未走 `cli:main`），静态判断为无影响；未实际重跑这些脚本。

## 遗留项（不在本任务范围，需另立任务）

| 遗留 | 内容 | 为什么不在本任务做 |
| --- | --- | --- |
| 本地 Control Plane 交付形态（ADR） | 普通用户怎么获得并运行本机 Control Plane 与 PostgreSQL | 架构决策，涉及打包审计与 P9-06 完成定义，且现行基线禁止随包分发 |
| 云端关闭 bootstrap 注册端点 | 把 `deploy/customer-demo/compose.v1.json` 与 `scripts/deploy_customer_demo.py` 的 `DEMO_ENVIRONMENT_ID` / `DEMO_BOOTSTRAP_PUBLIC_KEY` 由**必填**改为**禁止**，云端注册入口收敛到 U9-05 账号绑定 | 方案 §4.1 阶段五，本任务明确排除；需回归 C10 部署门禁 |
| App 内设备身份重置 | 409 诊断旁给出可点击的重置动作 | 涉及"停任务 → 删身份 → 重新注册"的完整状态机，需独立失败矩阵 |
| Windows 私有 ACL 验证 | 证明 `0600` 等价语义在 Windows 上成立 | 需 Windows 主机验收 |
| 真实 Tauri E2E + macOS 正式包验收 | 方案 §4.2 第 7、8 步 | 本任务禁止启动 App / 打包 |

对第 2 项的现状说明：本次改动**没有触碰**云端任何配置，`7086ae4` 引入的"无账号归属的 Installation
不得访问业务接口"守卫也**未被绕过**。本地场景根本不装配账号服务，因此
`build_device_session_service(require_installation_owner=False)`——该门禁在本地自动不生效，这是
`app.py` 注释里写明的设计，不是被本次改动放宽的。

## 清理

- 未新增常驻服务。集成测试用的 PostgreSQL 由既有 `postgresql_url` fixture 管理，compose project name
  为 `automation-tool-pytest-<pid>`、端口取自 `unused_loopback_port()`，随 session 结束销毁；
- release profile 编译产物写在会话 scratchpad 的隔离 `CARGO_TARGET_DIR`，未进仓库、未污染
  `frontend/src-tauri/target` 与 `~/Library/Caches/automation-tool-build/`；
- 未启动 App、浏览器、Playwright、WDIO，未打包；
- `~/Library/Application Support/com.aventador.automationtool/` 下**没有任何读写**（所有测试都在
  `tmp_path` / `std::env::temp_dir()` 的隔离目录里），用户的抖音登录态与 Profile 未被触碰。

## 文档

- `contracts/protocol/local-registration-handoff-v1.json`（新增）
- `backend/src/automation_tool/control_plane/bootstrap/local_provisioning.py`（新增）
- `backend/src/automation_tool/control_plane/bootstrap/registration.py`（改）
- `backend/src/automation_tool/control_plane/bootstrap/app.py`（改）
- `backend/src/automation_tool/control_plane/bootstrap/cli.py`（改）
- `backend/tests/unit/control_plane/test_local_provisioning.py`（新增，8 项）
- `backend/tests/unit/control_plane/test_cli.py`（2 → 4）
- `backend/tests/unit/control_plane/test_registration_configuration.py`（12 → 15）
- `backend/tests/integration/test_local_provisioning_registration.py`（新增，5 项，真实 PostgreSQL）
- `frontend/src-tauri/src/local_registration.rs`（新增，15 项）
- `frontend/src-tauri/src/control_plane.rs`（改：`InstallationConflict` + 409 映射 + 断言）
- `frontend/src-tauri/src/lib.rs`（改：模块声明、生产注册编排、状态装配、两处错误映射）
- `frontend/src/api/control-plane/transport.ts`、`frontend/src/platform/tauri/control-plane-transport.ts`、
  `frontend/src/app/startup.ts`、`frontend/src/app/StartupGate.tsx`（改）
- `frontend/src/api/control-plane/transport.test.ts`、`frontend/src/app/startup.test.ts`、
  `frontend/src/app/App.test.tsx`（各 +1 项）
- 本文件
