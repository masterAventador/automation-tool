# RESEARCH：云端 Control Plane 交付可行性（用户已有服务器 / 域名 / 证书）

> 日期：2026-07-26
> 性质：**验证报告**，不是任务台账。本轮未改任何产品代码、未提交、未连接用户服务器，全部在本机 Docker 完成。
> 前置：`docs/development/PLAN-control-plane-delivery.md`（提交 `03d8560`）在「用户没有服务器」的前提下否决了云端方案。本轮前提变了：
> 用户有 **4C4G 服务器 + 真实域名 + 可信 CA 证书**，目标机器 M4 Air / Apple Silicon / macOS 26 / 24GB / 无 MDM。

---

## 0. 结论先行

**云端这条路技术上通，但下周能不能演示，不取决于服务器，取决于「有没有一份连得上云端的正式包」——而现在没有，也没有任何构建命令能产出它。**

四条结论，按阻塞程度排序：

| # | 结论 | 阻塞级别 |
| --- | --- | --- |
| 1 | **域名和证书不用再买**。App 只接受 `https://<真实域名>`（443、无端口无路径），用户已有的域名 + 公开 CA 证书正好满足。但**服务地址只有编译期一条注入路径**，换地址必须重新构建整包 | ✅ 通过 |
| 2 | **服务端账号 + 设备门禁链路完整，两套注册路径不会打架**（机制上互斥且 fail-closed）。账号登录本身就会创建并绑定 Installation，云端形态**不需要** bootstrap grant | ✅ 通过 |
| 3 | **部署脚本本机跑通了**（真实 Docker，migration → health 200 → version 200）。但它不建库、不建网络/卷、不投递 Secret、不管证书，这些全是运维前置手工步骤 | ⚠️ 可做，工作量在前置 |
| 4 | **正式构建链根本不支持产出 customer-demo 包**。`release_environment()` 显式剥掉所有 `AUTOMATION_TOOL_*`，前端用默认 mode（**没有登录界面**）。需要三处构建侧改动 + 一把 Profile 签名密钥 | 🚫 **真正的阻塞项** |

**4C4G 够用**：实测三容器空载共 157 MiB，Compose 上限合计约 1.15 GiB + PostgreSQL。见 §4.4。

---

## 1. 验证一：App 接受什么形式的服务地址

### 1.1 校验规则（已逐行核对，非推断）

两处独立校验，**必须同时通过**：

**编译期 + 启动期**：`frontend/src-tauri/src/deployment_profile.rs:154-193`（`validate_manifest`）

- `scheme` 必须是 `https`；
- `port()` 必须为 `None` → **只能走 443**；
- `path()` 必须是 `/`，不允许 query / fragment / username / password；
- `base_url` 必须字面等于 `format!("https://{host}")` —— 多一个斜杠都拒；
- `host` 必须出现在 `allowedHosts` 里；
- `allowedHosts` 每一项过 `valid_hostname()`（第 205-227 行）：必须含 `.`，每个 label 只允许小写字母/数字/连字符，**TLD 必须 ≥2 位且全部是小写字母**。

**运行期**：`frontend/src-tauri/src/control_plane.rs:513-533`（`validated_demo_origin`）重复同一套判断，并派生 `wss://{host}/api/v1/executors/connect`。

### 1.2 明确回答

| 问题 | 结论 | 依据 |
| --- | --- | --- |
| 能不能用 `https://<IP>` | **不能**。`valid_hostname` 要求 TLD 全部为小写字母，`192.168.1.10` 的 `10` 是数字，直接拒 | `deployment_profile.rs:226` |
| 能不能改端口（如 8443） | **不能**。`parsed.port().is_some()` 即拒。公网必须监听 443 | `deployment_profile.rs:178` |
| 能不能带路径前缀 | **不能**。`path() != "/"` 即拒 | `deployment_profile.rs:179` |
| loopback 有没有例外 | 有，但**只对 `local` Profile**。`local` 固定 `http://127.0.0.1:8765`（`LOCAL_BASE_URL`），且**编译期没有三件套时才生效**。它和 demo Profile 是互斥的两条分支，不能混 | `deployment_profile.rs:14, 69-76` |
| 自签证书会不会被拒 | 会。App 的 HTTP 客户端用 `reqwest 0.13.4` + `rustls-platform-verifier`（Cargo.lock 已确认），既没有 `danger_accept_invalid_certs` 也没有 `add_root_certificate`。它走**系统信任库**——理论上把自签 CA 装进 macOS 系统钥匙串可以通，但那是要在演示机上改系统信任策略。**用户已有可信 CA 证书，这条不需要走** | `control_plane.rs:554-563` |

**所以：域名和证书都不用买。要求是「真实域名 + 公开 CA 证书 + 443 端口」，用户手上的正好满足。**

服务端侧口径一致：`scripts/deploy_customer_demo.py:30` 的 `SAFE_HOST` 同样要求带点的合法域名；`deploy/ingress/render_config.py` 会对 `DEMO_HOST` 做 canonical 小写 DNS 校验，IP / 通配 / 带端口一律构建失败。三处口径没有分歧。

### 1.3 配置从哪注入 —— **只有编译期一条路**

```
构建机环境变量 AUTOMATION_TOOL_DEPLOYMENT_PROFILE_{PAYLOAD,SIGNATURE,VERIFYING_KEY}
        ↓  build.rs: validate_optional_deployment_profile() 先 Ed25519 verify_strict 校验
        ↓  cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_*
        ↓  deployment_profile.rs: option_env!()  ← 编译期常量，二进制里写死
```

- 三个变量**全有**才是 demo Profile，**全无**才是 local，缺一即 `panic!("deployment profile is invalid")`；
- payload 是 canonical JSON（`serde_json::to_vec(&manifest) != payload` 即拒），字段顺序固定为 `version, profile, profileId, baseUrl, allowedHosts`；
- `profileId` 必须 `demo-` 开头、6～48 位小写字母数字连字符；
- **没有任何运行时环境变量、配置文件或界面入口能改它**。`frontend/tests/demo-deployment-profile.test.mjs` 专门断言 `deployment_profile.rs` 里不出现 `std::env::var`，这是被测试锁死的设计。

**用户必须知道的后果：换一次部署地址 = 重新构建、重新签名、重新分发整个 545 MiB 安装包。** 演示前把域名定死，别中途改。

### 1.4 🚫 真正的阻塞：目前没有任何命令能构建出 demo Profile 的正式包

| 事实 | 证据 |
| --- | --- |
| 正式包构建入口 `scripts/build_release_package.py` 全文**不出现** `DEPLOYMENT_PROFILE`，没有对应参数 | grep 无命中 |
| 它调用 `run_p9_03_acceptance.release_environment()`，该函数**显式过滤掉所有 `AUTOMATION_TOOL_*`**，只注入 Executor 校验公钥、动作限额、更新端点 | `run_p9_03_acceptance.py:130-155` |
| 正式包用 `tauri.conf.json` 的 `beforeBuildCommand: "pnpm build"` → **默认 vite mode**。而 `main.tsx:51` 只在 `MODE === "customer-demo"` 时注入 `accountSessionGateway` → **正式包里根本没有登录界面** | `tauri.conf.json:7`、`main.tsx:51-52` |
| 唯一签过 demo Profile 的地方是 `scripts/run_c10_07_acceptance.py`，它用硬编码测试私钥 `bytes([7])*32` 只跑 `cargo test`，**从没构建过 App bundle** | `run_c10_07_acceptance.py:74-101` |

**结论：C10-07「App Demo Profile」台账标 ✅，指的是 Rust 侧校验逻辑通过 `cargo test`，不代表存在过一份能连云端的包。** 要产出它，需要三处构建侧改动（§5.2）。

---

## 2. 验证二：账号与设备门禁在云端形态下怎么走通

### 2.1 完整首次使用流程

**运维侧（演示前，领导不在场）**

1. 部署栈起来（§3）；
2. 用一次性私网 job 创建演示账号，**不开 HTTP 管理面**：

```sh
docker run --rm --interactive \
  --network <database_network> \
  --mount type=volume,source=<RUNTIME_SECRETS_VOLUME>,target=/run/secrets,readonly \
  --user 65532:65532 --read-only --cap-drop ALL \
  --entrypoint automation-tool-account-operations <CONTROL_PLANE_IMAGE> \
  create --login-name demo.leader --request-id <本次唯一ID>
# stdin 传 {"capability":"atoc1.<...>","password":"<≥12位初始密码>"}
```

- 密码和 capability **只走 stdin JSON**，禁止进 argv / 环境变量 / 日志（`deploy/operations/account-operations-job.v1.json`）；
- capability 明文由运维离线保管，服务端只存它的 sha256（`account-operations-capability-digest`）；
- 命令集固定为 `create / disable / restore / reset / emergency-revoke`，`disable/restore/emergency-revoke` 还要 `--expected-revision`。

**本轮已实测**：`scripts/run_c10_06_acceptance.py` 通过，输出
`{"accountOperations":["create","disable","restore","reset","emergency-revoke"],"auditTypes":["account.disabled","device.revoked","session.all_revoked"],"databaseRole":"automation_tool_operations","ddlDenied":true,"hostPorts":0,"revokedDeviceCount":2,"taskDataDenied":true}`
—— 建号、停用、恢复、重置、应急吊销全部走通，DDL 与任务数据访问被拒绝。

**领导侧（打开 App）**

| 步 | 他看到什么 | 说明 |
| --- | --- | --- |
| 1 | 双击图标 → 「正在恢复产品账号」+ 转圈 | `AccountSessionGate` 是**最外层**门禁，包住 `StartupGate`（`App.tsx:127-134`） |
| 2 | 登录卡片：标题「登录自动化运营工具」，副标题「客户演示版需要产品账号；平台扫码登录将在进入工作台后单独处理」 | `AccountSessionGate.tsx:296-300` |
| 3 | 两个输入框：**登录名**（`^[A-Za-z][A-Za-z0-9._-]{2,63}$`）、**密码**（12～128 位）；一个「登录」按钮 + 一个「使用恢复票据」按钮 | 同上，325-350 行 |
| 4 | 点登录 → 成功后自动进入「正在检查桌面运行环境」（StartupGate）→ 工作台 | — |
| 5 | 顶部常驻账号条：登录名 + 「修改密码」「设备管理」「退出产品账号」 | 同上，178-197 行 |
| 失败 | 服务器连不上 → 「暂时无法确认账号状态 / 业务工作台保持关闭，恢复连接后可重新检查」+「重新检查」按钮 | 同上，96-110 行 |

**设备绑定在第 4 步内部自动完成，领导看不到任何多余步骤。**

### 2.2 设备怎么绑定（U9-05）

`login_product_account` 在保存账号 Session 之前，用生产 Ed25519 设备身份完成 challenge 签名：
`POST /api/v1/account-installations/binding-challenges` → 签名 → `POST /api/v1/account-installations/bindings`。

关键实现在 `backend/src/automation_tool/control_plane/infrastructure/database/account_installation_binding_repository.py:78-160`：

```python
if installation is None:
    installation_id = InstallationId.new().uuid
    installation = insert(installations).values(
        id=installation_id,
        device_public_key=challenge["device_public_key"],
        owner_user_id=user_id.uuid,   # ← 一次插入即带 owner
        ...)
```

**即：设备第一次登录时，绑定接口会直接创建带 owner 的 Installation，并签发 `atdc1` 设备凭据。** 云端形态**完全不需要** bootstrap grant，也不需要提前给设备做任何登记。这一条是本轮最重要的正面结论。

其余分支同一事务内覆盖：已有 owner 且是同一账号 → 轮换凭据并吊销旧设备 Session；owner 是别人 → `CrossAccountBindingRejected`；Installation 已吊销 → `RevokedInstallationBindingRejected`；账号非 ACTIVE → `AccountSessionRejected`。

### 2.3 今晚新增的本机设备注册（`93a6acf`）在云端走不走 —— **不走，且不会打架**

**服务端**：`bootstrap/registration.py:65-88` 明确写了 fail-closed：

```python
if provisioned is not None:
    if any(value is not None for value in configured):
        raise RegistrationConfigurationError   # 两把 bootstrap 公钥并存 → 直接拒绝启动
```

- 云端容器走 `container_cli`，**不签发**本机 grant，`provisioned=None`，只用 `DEMO_ENVIRONMENT_ID + DEMO_BOOTSTRAP_PUBLIC_KEY` 这一对；
- 本机走 `cli:local_app`，签发 grant 并传 `provisioned=...`，此时**必须没有**那对环境变量，否则启动即失败。

**App 端**：同一份代码在所有构建里都编译，但 `register_installation_from_local_handoff` 读的是 App 私有目录里的 `local-registration-bootstrap-v1` 交接文件。云端形态下这台机器没有本机 Control Plane，文件不存在 → 返回 `NotAttempted` → `Ok(())`，**不阻断启动，不产生任何副作用**（`lib.rs:1673-1707`）。

**两套路径是设计上互斥的，不打架。** 附带一条：云端仍必须提供 `DEMO_BOOTSTRAP_PUBLIC_KEY`（compose 必填），但它对演示流程没有实际用途——**生成一把 Ed25519，公钥填进去，私钥直接丢弃即可**。即使有人拿到私钥注册出一个 Installation，也因为无 owner 而被 §2.4 的门禁挡在业务接口之外。

### 2.4 `7086ae4` 新增的归属门禁在云端是打开的

`bootstrap/app.py:298-306`：

```python
resolved_device_session_service = build_device_session_service(
    resolved_database,
    require_installation_owner=resolved_account_session_service is not None,
)
```

云端配齐了账号三件套 → 账号服务存在 → `require_installation_owner=True` → **无 owner 的 Installation 拿不到业务接口**。这与 §2.2 的「登录即创建带 owner 的 Installation」正好配套，新设备天然满足。

---

## 3. 验证三：部署脚本本机跑通

### 3.1 实际执行结果（真实 Docker，非 Mock）

跑的是 `scripts/run_c10_08_acceptance.py`（它内部调用正式的 `scripts/deploy_customer_demo.py --rehearsal`），两次全绿：

```json
{"alembicRevision":"20260723_0034","healthStatus":200,"hostPorts":{"http":32788,"https":32789},
 "replicas":{"controlPlane":1,"ingress":1},"versionStatus":200}
```

走通的链路：镜像 OCI version/revision 校验 → compose config 渲染校验 → `alembic upgrade head` 一次性 job → 单 Control Plane 起来并 healthy → 校验只读 rootfs / 无宿主端口 / cap_drop ALL / Secret 只读挂载 → 单 Ingress → **经 TLS 请求 `/api/v1/health` 与 `/api/v1/version` 均 200** → 校验日志不含任何生成的 Secret。

隔离与清理符合项目规则：Compose project name `c10-08-<uuid12>`，容器/网络/卷全部带 `automation-tool-c10-08-<uuid12>` 前缀，端口用 `0`（随机 loopback 端口，实测 32788/32790 段），`finally` 块回收全部资源。**跑完已复查：容器、网络、卷、镜像零残留，未触碰本机其他项目的 `agent-platform-dev-*` 容器。**

### 3.2 部署脚本做什么、不做什么

| 做 | 不做（= 运维前置手工步骤） |
| --- | --- |
| 校验镜像 digest / OCI 标签 | **不建数据库、不建角色、不设密码** |
| 校验 24 小时内的备份 receipt | **不生成备份**（只校验 receipt 和可选产物摘要） |
| 跑 `alembic upgrade head` | **不建 application / database 网络** |
| 串行拉起单 Control Plane + 单 Ingress | **不建 runtime / migration / tls 三个卷** |
| HTTPS health/version 断言 | **不投递任何 Secret 文件** |
| 独占锁，失败只停本轮新服务 | **不申请、不安装、不续期证书** |
| | **不构建镜像** |

也就是说：`compose.v1.json` 里没有 PostgreSQL 服务，数据库是外部的；三个卷和两个网络都是 `external: true`。**在一台空的 4C4G 服务器上，脚本之前的手工前置比脚本本身多。**

### 3.3 环境变量与凭据清单（逐个，标明谁提供）

**A. 19 个非 Secret 环境变量**（写进 `0600` 的 `.env`，只允许这 19 个，多一个少一个都报 `environment is incomplete`）

| 变量 | 谁提供 | 备注 |
| --- | --- | --- |
| `CONTROL_PLANE_IMAGE` | 我们 | 正式部署必须 `repo@sha256:<64hex>` |
| `INGRESS_IMAGE` | 我们 | 同上 |
| `APPLICATION_NETWORK` / `DATABASE_NETWORK` | 运维 | 需预先 `docker network create` |
| `RUNTIME_SECRETS_VOLUME` / `MIGRATION_SECRETS_VOLUME` / `TLS_SECRETS_VOLUME` | 运维 | 需预先 `docker volume create` 并写入内容 |
| `DEMO_BIND_ADDRESS` | 运维 | 正式只允许 `0.0.0.0` 或 `127.0.0.1` |
| `DEMO_HTTP_PORT` / `DEMO_HTTPS_PORT` | 运维 | **必须 80 / 443**（App 不接受非 443，§1.2） |
| `DEMO_HOST` | **用户** | 真实域名，形如 `api.<用户域名>` |
| `ACCOUNT_PASSWORD_PEPPER_VERSION` | 我们 | 首次填 `1` |
| `ACCOUNT_OPERATIONS_ACTOR_ID` | 我们 | 必须是 UUIDv4 |
| `DEMO_ENVIRONMENT_ID` | 我们 | 小写标识，如 `demo-cn-1` |
| `DEMO_BOOTSTRAP_PUBLIC_KEY` | 我们 | 43 位 base64url，私钥可直接丢弃（§2.3） |
| `ACTION_MINIMUM_INTERVAL_SECONDS` | 我们 | **演示脚本要重设**，别沿用本机的 60 |
| `ACTION_TASK_LIMIT` | 我们 | **演示脚本要重设**，别沿用本机的 1 |
| `ACTION_DAILY_LIMIT` / `ACTION_CONSECUTIVE_FAILURE_THRESHOLD` | 我们 | — |

**B. 5 个 Secret 文件**（`deploy/secrets/inventory.v1.json`，投影到 `/run/secrets`，owner `65532`、`0400`，拒绝 symlink / 目录 / >8192 字节 / 首尾空白 / 非 UTF-8）

| 文件名 | 消费者 | 生成方式 | 谁提供 |
| --- | --- | --- | --- |
| `database-url` | control_plane / migration / account_operations | `postgresql+asyncpg://...` —— **migration 卷放 migrator URL，runtime 卷放 app URL，两者不同** | 运维 |
| `account-password-pepper` | control_plane / account_operations | 32 随机字节 base64url | 我们 |
| `account-fingerprint-key` | control_plane | 32 随机字节 base64url | 我们 |
| `account-operations-capability-digest` | account_operations | `sha256(capability)` 的 base64url，capability 形如 `atoc1.<43位base64url>` | 我们（capability 明文离线保管） |
| `action-authorization-private-key` | control_plane | Ed25519 私钥 base64url，**必须与 App 编译进去的 `AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY` 配对** | 我们（见 §5.2 遗留） |

**C. TLS 卷**：`tls.crt` + `tls.key`，owner `101`（nginx-unprivileged），`0400`。由**用户提供**证书与私钥。

### 3.4 硬编码的本机假设 —— 逐项检查结论

| 检查项 | 结论 |
| --- | --- |
| 部署脚本里的路径 | 只有 `REPOSITORY_ROOT` 相对定位 compose/plan 文件，其余路径全是命令行参数。**无本机假设** |
| `--rehearsal` 开关的差异 | rehearsal 才允许本地 image ID、端口 `0`、只允许 `127.0.0.1` 绑定。正式模式强制不可变 digest + 端口 ≥1 + 允许 `0.0.0.0`。**开关不会误带进正式路径** |
| `DEMO_HOST` | Ingress 镜像**构建期**用 `--build-arg DEMO_HOST` 烤死在 nginx.conf 里。换域名 = 重建 ingress 镜像。**不是本机假设，但要写进流程** |
| 平台架构 | 服务器多为 x86_64，本机是 arm64。**两个镜像必须在目标架构上构建或用 buildx 交叉构建**，否则 digest 校验通过但容器起不来 |
| `nginx` 上游 | `proxy_pass http://control-plane:8000`，靠 compose 服务名解析，跨主机不可用。单机部署没问题 |
| WebSocket | nginx 模板有 `map $http_upgrade $connection_upgrade` + `proxy_buffering off` + HTTP/1.1，Executor 的 `wss://.../api/v1/executors/connect` 可以走通 |
| `postgres` 网络别名 | 只是 rehearsal 脚本自己起的容器用了 `--network-alias postgres`；真实部署里 `database-url` 里写什么主机名就连什么，**不强制叫 postgres** |

**唯一真正会在真实服务器上翻车的是架构不匹配**，其余没有发现硬编码本机假设。

### 3.5 ⚠️ 一条运维禁令

**绝对不要在这台开发机（或任何有抖音登录态的机器）上跑 `scripts/run_u9_06_acceptance.py`。** 它第 265-267 行要求 `~/Library/Application Support/com.aventador.automationtool` 不存在才启动，第 364 行结束时 `shutil.rmtree(private_app_data)` —— 会**删掉整个 App 私有目录**。目前它会因为目录存在而拒绝启动（这是它自带的保护），但不要去绕。

---

## 4. 4C4G 够不够 —— 有数据的判断

### 4.1 实测占用（本轮 `docker stats --no-stream`，栈处于健康空载状态）

| 容器 | 内存实测 | Compose 上限 | CPU 实测 |
| --- | --- | --- | --- |
| control-plane（1 uvicorn worker） | **68.23 MiB** | 1024 MiB | 0.22% |
| ingress（nginx） | **2.24 MiB** | 128 MiB | 0.00% |
| postgres 18.4 | **86.74 MiB** | 未设限 | 3.92%（启动期） |
| **合计** | **≈157 MiB** | ≈1.15 GiB + PG | — |
| migration（一次性） | — | 512 MiB / 0.5 CPU | 跑完即退出 |

### 4.2 判断

**4 GiB 内存够用，余量很大。** 空载 157 MiB，即使按 Compose 上限满打满算（control-plane 1 GiB + ingress 128 MiB + PostgreSQL 给 1 GiB）也只有约 2.2 GiB，留给系统 1.8 GiB。演示是**单用户、单设备、单任务执行器**，并发压力可以忽略。

**4 vCPU 也够。** Compose 上限合计 1.25 CPU（control-plane 1.0 + ingress 0.25），migration 是一次性 0.5。真正吃 CPU 的浏览器自动化和视频渲染**全部在领导那台 M4 Air 上跑**，服务器只做 API 和数据库。

**唯一需要注意的是构建**：`docker build` 后端镜像会短时吃满多核并占用构建缓存。建议**在别处构建、推镜像仓库、服务器只 pull**，别在 4C4G 上现场构建。

### 4.3 磁盘（仅供参考，按用户要求不作为阻塞项）

| 项 | 大小 |
| --- | --- |
| control-plane 镜像 | 547 MB |
| ingress 镜像 | 76.1 MB |
| postgres:18.4-bookworm 镜像 | 649 MB |
| **镜像小计** | **≈1.27 GB** |
| 三个 Secret 卷 | < 2 KB |
| 演示数据 + WAL | 演示体量下 < 1 GB |
| **建议预留** | **10 GB**（含构建缓存与备份落地） |

---

## 5. 运维部署清单（从空服务器到领导能用）

> 顺序即依赖顺序，不可跳步。标 🚫 的是当前**还做不到**的步骤。

### 5.1 服务器侧（一次性）

**阶段 A — 基础设施**

1. 服务器装 Docker Engine + Compose v2，确认架构（`uname -m`），后续镜像必须匹配；
2. DNS：把 `api.<用户域名>` A 记录指向服务器公网 IP；
3. 防火墙：只放行 80 / 443；**5432 与 8000 绝不对公网开放**；
4. 创建两个网络：
   `docker network create automation-tool-demo-app`
   `docker network create automation-tool-demo-db`
5. 创建三个卷：`docker volume create` × `automation-tool-demo-runtime` / `-migration` / `-tls`。

**阶段 B — PostgreSQL**

6. 在 `database` 网络里起 PostgreSQL 18（私网，无公网 IP，禁 trust 认证）；
7. 以超级用户执行 `deploy/postgresql/roles.sql`，创建 `automation_tool_migrator` / `automation_tool_app` / `automation_tool_backup` 三个 LOGIN role；
8. 给三个 role 分别设**独立随机密码**（不写进 SQL / Git / argv / 日志）；
9. `CREATE DATABASE automation_tool_demo OWNER automation_tool_migrator;` + `REVOKE ALL ... FROM PUBLIC;` + 给三个 role `GRANT CONNECT`。

**阶段 C — Secret 投递**（全部 owner `65532`、`0400`；TLS 那两个 owner `101`）

10. `migration` 卷写入 `database-url` = **migrator** 的 `postgresql+asyncpg://` URL；
11. `runtime` 卷写入 5 个文件：`database-url`（**app** 身份）、`account-password-pepper`、`account-fingerprint-key`、`account-operations-capability-digest`、`action-authorization-private-key`；
12. `tls` 卷写入用户的 `tls.crt` + `tls.key`（owner `101`，`0400`）；
13. 记录 capability 明文（`atoc1.<...>`）到离线运维凭据库——**它是唯一能做账号运维的东西**。

**阶段 D — 镜像**

14. 在**目标架构**上构建（或 buildx 交叉构建）并推仓库：
    - Control Plane：`docker build -f backend/Dockerfile --build-arg APP_VERSION=<version> --build-arg VCS_REF=<40位commit> backend/`
    - Ingress：`docker build -f deploy/ingress/Dockerfile --build-arg DEMO_HOST=api.<用户域名> deploy/ingress/`
15. 记下两个镜像的 `repo@sha256:<digest>`，正式部署只接受不可变 digest。

**阶段 E — 部署**

16. 生成一次数据库备份并写 `customer-demo-backup-receipt.v1`（**必须 24 小时内**，否则脚本报 `backup receipt is stale`）；
17. 写 `0600` 的非 Secret 环境文件，19 个变量齐全，`DEMO_HTTPS_PORT=443`、`DEMO_HTTP_PORT=80`、`DEMO_BIND_ADDRESS=0.0.0.0`；
18. 执行：

```sh
python scripts/deploy_customer_demo.py \
  --environment-file /secure/change/non-secret.env \
  --backup-receipt /secure/change/backup-receipt.json \
  --expected-app-version <backend/pyproject.toml 的 version> \
  --expected-vcs-ref <40位 commit> \
  --project automation-tool-customer-demo \
  --state-directory /secure/change/state \
  --ca-file /secure/change/public-ca.pem
```

19. 验证输出 `healthStatus:200 / versionStatus:200 / replicas {1,1}`；再从**外网**用真实域名请求 `https://api.<域名>/api/v1/health`。

**阶段 F — 演示账号**

20. 跑 §2.1 的 `create` 一次性 job，拿到 User ID，记录初始密码；
21. （建议）让领导第一次登录后自行「修改密码」，或直接把初始密码给他。

### 5.2 🚫 App 侧（当前做不到，这是下周能否演示的决定因素）

22. **生成一把 Profile 签名 Ed25519 密钥**（长期运维凭据，丢了就再也签不出能被这批包接受的 Profile）；
23. **签出 Profile payload**：canonical JSON，字段顺序 `version, profile, profileId, baseUrl, allowedHosts`，值为
    `{"version":"customer-demo-profile.v1","profile":"demo","profileId":"demo-<xxx>","baseUrl":"https://api.<用户域名>","allowedHosts":["api.<用户域名>"]}`；
24. 🚫 **改构建链**（三处，目前都不存在）：
    - `run_p9_03_acceptance.release_environment()` 透传三个 `AUTOMATION_TOOL_DEPLOYMENT_PROFILE_*`；
    - 正式构建的 Tauri 配置把 `beforeBuildCommand` 换成 `pnpm build:customer-demo-assets`（机制已存在，`tauri.account-management-e2e.conf.json` 就是这么写的），否则包里没有登录界面；
    - 动作授权密钥改为**外部传入并保留**，让服务端的 `action-authorization-private-key` 与 App 编译进去的公钥配对（当前 `executor_signing_material()` 每次构建现场随机生成、用完即丢，这份包的「执行动作」是死的）；
25. 🚫 **签名与公证**：正式包目前是 ad-hoc 签名（`codesign --force --sign -`），拷到领导的 Mac 上会被 Gatekeeper 拦。要么申请 Developer ID + `notarytool` 公证 + `stapler` 装订，要么我们在他机器上手工放行并验证重启后仍可打开；
26. 装包，打开，**登录演示账号** → 设备自动绑定完成。

### 5.3 演示机预置（模型密钥，不让演示者填）

**demo Profile 的 App 数据目录和 local Profile 不是同一个**（`prepare_data_directory` 会多插一层）：

```
~/Library/Application Support/com.aventador.automationtool/profiles/<profileId>/
```

27. 建目录（`0700`）：`.../profiles/<profileId>/model-services/`；
28. 写两个文件，**`0600`**，内容是紧凑 JSON（字段名是 snake_case，`deny_unknown_fields`，多一个字段就会被判为 `StorageUnavailable`）：

`model-service-script-v1`：
```json
{"version":1,"purpose":"script","model_id":"qwen3.7-max-2026-06-08","api_key":"sk-ws-…"}
```

`model-service-video-creative-v1`：
```json
{"version":1,"purpose":"video_creative","model_id":"qwen3.7-max-2026-06-08","api_key":"sk-ws-…"}
```

- `api_key` 取 `docs/credentials-bailian-model.md` 里那把工作空间级 Key；
- `purpose` 只能是 `script` / `video_creative`；`model_id` 只能是 `deepseek-v4-pro` / `glm-5.2` / `qwen3.7-max-2026-06-08`，且 **video_creative 只允许 `qwen3.7-max-2026-06-08`**；
- 服务地址不用配，`PRODUCTION_BASE_URL` 硬编码为 `https://dashscope.aliyuncs.com/compatible-mode/v1`；
- 也可以让 App 自己写：打开「模型服务设置」页填一次即可，效果等价。手工写文件的好处是**领导不用碰这一页**。

30. **抖音扫码要重来一次**。浏览器 Profile 也在 `.../profiles/<profileId>/embedded-browser-profiles/` 之下，demo Profile 的目录是全新的，开发机上现有的登录态不会被复用。必须在预置阶段用真人手机扫，**不能留到客户面前**；
31. 跑通一遍完整链路，让库里有真实历史记录，避免演示第一分钟面对空列表。

### 5.4 ⛔ 演示后回收（必做，别漏）

**那是用户本人的百炼工作空间 API Key，明文躺在演示机的 App 数据目录里，`0600` 只防同机其他用户，不防拿到这台机器的人。** 演示结束后**二选一，当天完成**：

- **A（推荐）**：在百炼控制台删除并轮换这把 Key，同时在 `docs/credentials-bailian-model.md` 更新新值；
- **B（最低限度）**：删除演示机上的 `.../profiles/<profileId>/model-services/` 整个目录（视频剪辑凭据同理删 `editing-services/`），并确认 App 再次打开时模型设置页显示「未配置」。

同批需要回收的还有：演示账号（`disable` 或改密）、运维 capability（若曾在演示机出现过则轮换）、以及演示机上的 App 私有目录（若机器要归还）。

---

## 6. 仍未解决的问题

1. **🚫 没有任何命令能构建出连云端的正式包。** §5.2 第 24 步的三处构建侧改动一处都不存在，需要单独立任务 + TDD。这是下周能否演示的**唯一决定性阻塞项**，服务器和域名都解决不了它。
2. **🚫 Gatekeeper 仍未闭环。** 正式包是 ad-hoc 签名，能不能在领导那台 M4 Air 上打开，本轮**没有实测**（PLAN 文档 §8 第 1 条同样挂着）。内置 Chromium framework 与 Executor 这些嵌套可执行文件会不会各弹一次拦截，也没验过。**这是第二个应该立刻动手实测的点。**
3. **动作授权密钥的运维方案没有设计。** 只知道当前被丢弃，没有设计「密钥从哪来、存在哪、怎么同时喂给构建和服务端 Secret」。不解决就只能演「发现 + 预览 + 人工接管」，演不了「执行动作」。
4. **`docs/development-roadmap.md` 台账自相矛盾。** 第 5 行写「下一工程任务为 C10-08 云端部署」，第 409-414 行 C10-08～C10-13 全是 ✅。而 `docs/development/C10-08.md` 自己写明只做了本机隔离演练。按 CLAUDE.md §2.1，真实进度与台账不一致时任务视为未闭环，**这几行需要在后续任务中修正**（本轮是调研，不改台账）。
5. **镜像架构未验证。** 本机是 arm64，用户服务器大概率 x86_64。跨架构构建的 digest 校验、`postgres:18.4-bookworm` 的可用性、asyncpg/uvicorn 的 wheel 都没在 amd64 上验过。
6. **真实公网证书链路未验证。** 本轮用的是脚本自签的一小时 CA + `--ca-file`。真实域名 + 公开 CA + 443 + 真实 DNS 的组合没跑过，`rustls-platform-verifier` 在 macOS 26 上的行为也没实测。
7. **备份没有实现。** 部署脚本要 24 小时内的 receipt，但生成备份、算摘要、写 receipt、异地存储、恢复验证——一件都没有工具，全靠手工。
8. **演示现场断网仍然是全有全无。** 这一条 PLAN 文档 §4 的判断没有变：云端方案的网络依赖是本机方案的严格超集。服务器解决了「有没有地方部署」，没有解决「客户会议室 WiFi 不通怎么办」。**如果现场网络不可控，建议同时保留本机预置方案作为 B 计划。**
9. **未验证过一次真实的 demo Profile App 端到端。** `run_u9_06_acceptance.py`（账号登录 + 设备自动绑定）跑的是 `control-plane-e2e` feature + loopback origin，不是签名 demo Profile + HTTPS。§2 的结论来自代码与数据库实现逐行核对，**不是端到端实测**。
