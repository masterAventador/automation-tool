# FIX 能产出「连云端、带登录、动作授权可用」正式包的构建命令

> 状态：🔍 待验收（交付包已从干净树产出并逐项验证；仍缺 Gatekeeper 实测与真实服务器端到端）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/RESEARCH-cloud-deployment-readiness.md` §1.4 / §6 第 1 条——下周客户演示的唯一决定性阻塞项：**没有任何命令能构建出连云端的正式包**
>
> 域名：`at.xuanbai.tech`（用户已备好 Let's Encrypt 证书，443，DNS 指向 49.233.213.109）

## 缺陷

三处，RESEARCH 报告已定位两处，第三处在本次核实中被改写。

### 一、正式包里没有登录界面（根因与报告判断不同）

报告的判断是「正式构建用默认 vite mode，要把 `beforeBuildCommand` 换成 `pnpm build:customer-demo-assets`」。核实后**这个方向是错的**，它把一处构建期分叉当成了配置项：

```ts
// frontend/src/main.tsx:51-52（改动前）
const accountSessionGateway =
  import.meta.env.MODE === "customer-demo" ? new TauriAccountSessionGateway() : undefined;
```

`import.meta.env.MODE` 决定的不是「要不要登录」，而是**登录界面这段代码存不存在于产物里**——默认 mode 下 `TauriAccountSessionGateway` 整个被 tree-shake 掉。这正是「单一构建路径规范」要禁的第一类差异，不是它允许的第三类（指向隔离实例的配置值）。

按报告方案换 `beforeBuildCommand`，等于**保留分叉并再多一条构建路径**：正式包和验收包从此走两套资产构建，下一次漂移只是时间问题。

其余事实已核实，说明分叉只在这一行：

- `frontend/src/app/App.tsx:127-134` 本来就是运行期条件渲染（`accountSessionGateway === undefined ? … : <AccountSessionGate>`）；
- 七个账号 Command 早已注册在**正式构建**的 invoke handler 里（`frontend/src-tauri/src/lib.rs:4091-4100`），账号 Vault 也在正式构建里初始化（`lib.rs:4038-4045`）；Rust 侧没有分叉；
- `frontend/vite.config.ts` 的 `transformIndexHtml` 不处理 `customer-demo` 模式，所以 `build:customer-demo-assets` 用的是同一个 `/src/main.tsx` 入口，产物只比 `pnpm build` 多一个对象。

### 二、构建链剥掉部署 Profile

`scripts/run_p9_03_acceptance.py::release_environment()` 显式过滤掉所有 `AUTOMATION_TOOL_*`，且没有任何参数能把三件套传回去。`scripts/build_release_package.py` 全文不出现 `DEPLOYMENT_PROFILE`。

### 三、动作授权密钥现生成、用完即丢

```python
# scripts/run_p9_03_acceptance.py:142-143（改动前）
"AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY": executor_public_key,
"AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": executor_public_key,
```

同一把每次构建随机生成的公钥被写进两个位置，私钥只用于校验 Executor manifest 后即丢弃。服务端 `action-authorization-private-key` Secret 无论填什么都签不出这个 App 会接受的授权——包里「执行动作」是死的。

已核实两者之间**没有任何相等假设**（`executor_bootstrap.rs:35-36` 独立读取并自行校验 canonical 非零 32 字节公钥），同值纯属图省事。

## 方案与两处判断

### 判断一：服务地址保持编译期注入（不改）

`DeploymentProfile::load()` 的 local / demo 两个分支**在每个二进制里都被编译**，`option_env!` 只决定走哪支。同一份源码、同一个函数、同一条读取路径，只有值不同——这满足「单一构建路径规范」第三类，与 `MODE` 决定代码存不存在是两回事。

改成运行期配置文件的收益只有「换地址不用重建整包」，代价是：这个地址是账号密码与动作授权的信任锚，现在要把 App 指向别处需要 Profile 签名私钥 **+ 重新构建 + 重新签名**，改成运行期文件后只需私钥 + 一次文件写；且验签公钥仍必须编译期注入，否则签名等于没有，所以「完全不用重建」本就做不到，只是把重建换成一个可被替换的持久化文件，并新增缺失 / 损坏 / 回滚 / 降级四类失败矩阵。

本次不做。若以后要「一份产物对多个部署」，正确形态是「编译期只钉验签公钥，地址走签名配置文件」，应立独立任务。

### 判断二：「是否需要产品账号」是部署配置，不是构建模式

关键事实：**「需要账号」≠「Profile 是 demo」**。U9-04（`tauri.account-session-e2e.conf.json`）与 U9-06（`tauri.account-management-e2e.conf.json`）两条既有验收都跑在 loopback 上、Profile 是 local，但它们的隔离实例**确实启用了账号**。简单按 Profile 判会让这两条链路静默跳过登录，它们的被测对象整个失守。

所以配置值有两个来源，单一读取点：

```rust
// frontend/src-tauri/src/deployment_profile.rs
pub fn requires_product_account(&self) -> bool {
    self.kind == DeploymentProfileKind::Demo || ISOLATED_PRODUCT_ACCOUNT_INSTANCE.is_some()
}
```

`ISOLATED_PRODUCT_ACCOUNT_INSTANCE` = `option_env!("AUTOMATION_TOOL_ISOLATED_PRODUCT_ACCOUNT_INSTANCE")`。方向是单向的：**只能加要求，不能去掉**。忘记设置的构建 fail closed 停在登录界面，而不是敞开工作台。

## 交付

### 一、消除构建期分叉

| 文件 | 改动 |
| --- | --- |
| `frontend/src/main.tsx` | 无条件构造并传入 `TauriAccountSessionGateway` |
| `frontend/src-tauri/src/deployment_profile.rs` | 新增 `requires_product_account()` 与 `ISOLATED_PRODUCT_ACCOUNT_INSTANCE` |
| `frontend/src-tauri/src/account_session_vault.rs` | `AccountSessionSnapshot` 新增 `NotRequired` 变体 |
| `frontend/src-tauri/src/lib.rs` | 新增 `ProductAccountRequirement`；`restore_product_account_session` 在不需要账号时先于 Vault 与网络返回 |
| `frontend/src/features/account-session/account-session-gateway.ts` | zod 契约新增 `not_required` |
| `frontend/src/features/account-session/AccountSessionGate.tsx` | `not_required` 直接渲染 children |
| `frontend/package.json` | 删除 `build:customer-demo-assets`；两条账号验收构建加 knob |
| 两个账号验收 tauri 配置 | `beforeBuildCommand` 改回 `pnpm build` |

`lib.rs` 只改了与部署配置相关的三处：
- 新增 `ProductAccountRequirement` 结构体（`clear_account_session` 之前）；
- `restore_product_account_session` 增加一个 `State` 参数与开头的短路返回；
- `run()` 的 setup 中 `prepare_data_directory` 之后 `app.manage(ProductAccountRequirement(...))`。

正式构建仍是 `pnpm build`，`tauri.conf.json` 未改，包审计的「冻结产物必须属于被审计二进制」链路未改。

### 二、构建命令（在 `scripts/build_release_package.py` 之上扩展，未另起一套）

```sh
uv run --project backend --locked python scripts/build_release_package.py \
  --platform macos \
  --deployment-profile      /secure/customer-demo/deployment.json \
  --profile-signing-key     /secure/customer-demo/profile-signing-key \
  --action-authorization-key /secure/customer-demo/action-authorization-key \
  --work-dir /secure/customer-demo/build
```

`deployment.json` 只声明会变的三项，`version` 与 `profile` 两个常量由工具补齐，避免手抄出错：

```json
{
 "profileId": "demo-xuanbai",
 "baseUrl": "https://at.xuanbai.tech",
 "allowedHosts": ["at.xuanbai.tech"]
}
```

新增 `scripts/customer_demo_release.py`：

- 两把私钥**只以路径出现在 argv**，文件必须是 mode `0600` 的普通文件（非 0600、符号链接、过大、非 canonical base64url、非 32 字节一律拒绝）；
- `CustomerDemoMaterial` 是 `frozen=True, slots=True` 且只含公开半边，**结构上装不下私钥**；错误信息不插值文件内容；
- 动作授权私钥由运维一次性生成并保管，**同一份文件既喂构建（取公钥）又投递到服务端 `action-authorization-private-key` Secret**；「保留」是结构性的——密钥源头就是那个文件，不是构建产物；
- `deployment_profile.rs` 的每条规则在构建**开始前**再校验一遍（https、无端口、无路径、无尾斜杠、非 IP、profileId 形状、allowedHosts 升序去重且含 baseUrl 主机），避免二十分钟后才 panic。

### 三、两道新门禁（本次新增，防止同类缺陷复发）

1. **产物必须能到达登录能力**（`frontend/scripts/audit-production-package.mjs`）：审计过去只做否定陈述（「不含测试标记」），因此「少了一整个能力」它看不见。现在要求发布产物含 `restore_product_account_session` 与 `login_product_account` 两个 Tauri 命令名——用命令名而非 UI 文案，抗改写、抗压缩，且恰好在调用它的代码被编译掉时消失。
2. **二进制必须真的带着它所属的部署**（`customer_demo_release.require_compiled_deployment`）：三个环境变量只是给编译器的指令，陈旧的 cargo 缓存有权不理会。这一条从成品二进制里把 Profile payload / 签名 / 验签公钥 / 动作授权公钥 / 服务地址读回来，是**一次没发生的构建无法通过**的唯一断言。

顺带修掉一处会误导人的缺陷：`--work-dir` 等路径参数原本按原样传给 `cwd=frontend/` 的子进程，相对路径会被重新解释，报成「配置文件不存在」而路径其实存在。现在全部在 `parse_arguments` 里一次性绑定到调用目录。

## RED（逐条实跑）

### 前端契约与组件

```text
npx vitest run src/features/account-session
 FAIL  src/features/account-session/AccountSessionGate.test.tsx > 
   mounts the workbench directly on a deployment that issues no product accounts
   Unable to find an element with the text: 受保护工作台
 FAIL  src/features/account-session/account-session-gateway.test.ts >
   accepts the deployment that issues no product accounts, and never with an account
   AccountSessionGatewayError: Product account operation is unavailable
 Tests  2 failed | 9 passed (11)
```

### 构建边界（原测试正把缺陷钉死，本身是 RED 的一部分）

```text
node --test tests/account-session-tauri-boundary.test.mjs
✖ U9-04 keeps account bearer secrets in one Rust vault behind fixed Commands
  AssertionError: The input was expected to not match the regular expression /import\.meta\.env/u
✖ the deployment configuration, not the build mode, decides whether login is required
  AssertionError: The input did not match the regular expression /fn requires_product_account/u
ℹ pass 3  ℹ fail 2
```

### Rust

```text
cargo test --manifest-path src-tauri/Cargo.toml --test deployment_profiles --locked
error[E0599]: no method named `requires_product_account` found for struct `DeploymentProfile`
error: could not compile `automation-tool-desktop` (test "deployment_profiles") due to 3 previous errors
```

### 包审计缺少能力门禁

```text
node --test tests/production-package-audit.test.mjs
✖ E4-15 refuses a package whose assets cannot reach the product account login
ℹ tests 18  ℹ pass 17  ℹ fail 1
```

### 构建命令

```text
pytest scripts/test_customer_demo_release.py
E   ModuleNotFoundError: No module named 'customer_demo_release'
（补 parse_arguments 路径解析一轮）
E   TypeError: parse_arguments() takes 0 positional arguments but 1 was given
```

## GREEN（条数逐次核对）

| 门禁 | 改动前 | 改动后 |
| --- | --- | --- |
| `vitest`（account-session 子集） | 9 passed | **11 passed**（+2） |
| `vitest`（全量） | — | **480 passed \| 2 expected fail**（59 文件） |
| `node --test tests/account-session-tauri-boundary.test.mjs` | 4 passed | **5 passed**（+1） |
| `node --test tests/production-package-audit.test.mjs` | 17 passed | **18 passed**（+1） |
| `node --test tests/*.test.mjs`（全量） | — | **239 pass, 0 fail** |
| `cargo test --test deployment_profiles` | 5 passed | **6 passed**（+1） |
| `cargo test`（全量） | — | **376 passed, 0 failed** |
| `cargo test --test single_build_path` | 7 passed | **7 passed**（未新增豁免项） |
| `pytest scripts/test_customer_demo_release.py` | 文件不存在 | **12 passed** |
| `pytest scripts/test_embedded_browser_package.py` | 34 passed | **35 passed**（夹具改为从审计脚本读取必需能力） |
| `scripts/run_c10_07_acceptance.py` | 3 轮编译 | **4 轮编译**（新增 knob 置位一轮） |
| `check_release_package_wiring.py` + `--self-test` | 通过 | **通过** |
| `tsc -b` / `eslint --max-warnings 0` | — | exit 0，无输出 |
| `ruff check` / `ruff format --check` / `mypy`（本次文件） | — | 全通过 |

knob 的两向证明（`run_c10_07_acceptance.py` 现在两种方式各编译一次同一断言）：

```json
{"allowedHosts": ["api.automation-tool.test"], "baseUrl": "https://api.automation-tool.test",
 "compiledDemoProfile": true, "isolatedInstanceRequiresAccount": true, "localFallback": true,
 "localRequiresAccount": false, "profileId": "demo-acceptance", "tamperRejected": true}
```

若 knob 被读取却被忽略，`isolatedInstanceRequiresAccount` 那一轮的 `assert!(DeploymentProfile::local().requires_product_account())` 会失败；若它泄漏进普通构建，另一轮的取反断言会失败。

## 真实构建验证

用假域名 `https://api.demo-verify.example` 真构建了一个完整可分发包（**主工作树**，用途是证明机制，不是交付物）：

```text
[release] Building for the deployment at https://api.demo-verify.example
[release] Binary carries the deployment profile for https://api.demo-verify.example
[release] Video runtime installed: ['material-video-worker', 'media-toolchain', 'motion-video-worker']
[release] Package payload verified: 331 browser files (359441871 bytes) inside 2737 package files (1093171530 bytes)
[E4-15] Production desktop package audit passed
[P9-05] Release bundle audit passed: 2406 files, 733729659 bytes
[release] Built disk image: …/自动化运营工具_0.1.0.dmg (511642700 bytes)
[release] Release package built and every release gate passed
BUILD EXIT 0
```

随后**不依赖构建自述**、直接从成品二进制里读回（`.local/customer-demo-release/verify-package.py`）：

```text
1. compiled profile recovered from the shipped binary:
    {"version": "customer-demo-profile.v1", "profile": "demo", "profileId": "demo-verify",
     "baseUrl": "https://api.demo-verify.example", "allowedHosts": ["api.demo-verify.example"]}
2. profile verifying key compiled in, from the operator key file: e1SitmPIzXMy3LrgVtqgDxdXVWh9mT9AVFoDAy14A2k
3. action authorization public key compiled in: gfJEBEJ9s-IlbyNfXamYsWLgODkeInRoHQsoNNGPDyk
   executor manifest verifying key (distinct):  1PoVkpL1nEy3C5JLspzqiIbNZtGGI4YeHBT4fbq8OA0
4. login screen present in the audited distribution: heading, subtitle, both Commands
```

四项判据逐条落实：**地址**由二进制里解出的 canonical payload 直接证明（不是看构建日志）；**验签公钥**由运维密钥文件现场派生后在二进制中命中；**动作授权公钥**同样由外部密钥文件派生、命中，且与 Executor manifest 签名公钥**确为两把不同的钥匙**；**登录界面**在被审计的冻结产物里，标题、副标题、两个 Tauri 命令名齐全。

### 过程中被真门禁拦下的两次

1. 第二轮构建被 `require_compiled_deployment` 拦下，报「Control Plane address 缺失」。查明是**门禁自己写错了**：地址只存在于 base64url payload 内部（Rust 启动时解码并验签），正确的包里根本没有可读的地址字面量，那条检查会拒绝每一个好包。已改：payload 命中即是更强的证明——它就是被签名的那串字节，只会解码出这一个部署。**这条只有真跑一次构建才会暴露。**
2. 第一轮构建报「配置文件不存在」而路径其实存在——相对路径被 `cwd=frontend/` 的子进程重新解释。已在 `parse_arguments` 统一绑定到调用目录。

### 交付包（已产出并验证）

按「交付物必须来自干净树」的要求，建 `wt/release-build`（`git worktree`，`wt/` 已加入 `.git/info/exclude`），只覆盖本任务的 23 个文件。已核对 `8c3319a..7b776fd` 期间他人改动的文件与本任务**零重叠**，覆盖没有回退任何人的提交。

期间踩到一次真实风险：worktree 最初建在 `46d4770`，而 **main 自 `c0cc760`（11:10）起 TypeScript 就编译不过**——发布口类型换了却漏了两个消费方，修复一直躺在工作区未提交，遮住了真实状态。本机 `tsc -b` 看似正常纯属假象。暂存本任务改动后在纯净 `46d4770` 上复现了 6 个 TS2339，确认与本任务无关。改用修复提交 `7b776fd` 后干净树 `tsc -b` exit 0。

**这正是「交付物必须来自干净树」的价值所在：如果直接在主树出包，这个已经坏了一个多小时的 main 会被完整封进客户拿到的安装包里，而且不会有任何提示。**

最终交付包（`.local/customer-demo-release/build-xuanbai.sh`，干净树 `7b776fd` + 本任务改动）：

```text
[release] Building for the deployment at https://at.xuanbai.tech
[release] Binary carries the deployment profile for https://at.xuanbai.tech
[release] Video runtime installed: ['material-video-worker', 'media-toolchain', 'motion-video-worker']
[release] Package payload verified: 331 browser files (359441871 bytes) inside 2737 package files (1093171531 bytes)
[E4-15] Production desktop package audit passed
[P9-05] Release bundle audit passed: 2406 files, 733729660 bytes
[release] Release package built and every release gate passed

1. compiled profile recovered from the shipped binary:
    {"version": "customer-demo-profile.v1", "profile": "demo", "profileId": "demo-xuanbai",
     "baseUrl": "https://at.xuanbai.tech", "allowedHosts": ["at.xuanbai.tech"]}
2. profile verifying key compiled in, from the operator key file: e1SitmPIzXMy3LrgVtqgDxdXVWh9mT9AVFoDAy14A2k
3. action authorization public key compiled in: gfJEBEJ9s-IlbyNfXamYsWLgODkeInRoHQsoNNGPDyk
   executor manifest verifying key (distinct):  NjuQ_MTQly0vr9Dpo9hBUYly3wb48JqlOYE_FEguhKQ
4. login screen present in the audited distribution: heading, subtitle, both Commands
```

产物：`.local/customer-demo-release/xuanbai/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg`（511646475 字节）。

服务端必须拿到与包内公钥 `gfJEBEJ9s-IlbyNfXamYsWLgODkeInRoHQsoNNGPDyk` 配对的私钥，即 `.local/customer-demo-release/secrets/action-authorization-key`，投递为 `action-authorization-private-key` Secret。**这两把密钥文件不在 Git 里，丢了这批包就再也签不出它接受的动作授权。**

## 失败矩阵

| 情形 | 行为 |
| --- | --- |
| 三个部署参数只给了一或两个 | 拒绝并列出缺哪几个；不存在有用的部分形态 |
| 密钥文件非 `0600` / 符号链接 / 非普通文件 | 拒绝，信息只含路径 |
| 密钥文件非 canonical base64url / 非 32 字节 / 空 / 带首尾空白 | 拒绝 |
| `baseUrl` 带端口、带路径、带尾斜杠、http、IP、`localhost` | 构建开始前拒绝 |
| `allowedHosts` 为空、未升序、有重复、不含 baseUrl 主机 | 构建开始前拒绝 |
| `profileId` 不以 `demo-` 开头 / 含大写 / 过短 | 构建开始前拒绝 |
| 部署文件多字段或少字段 | 拒绝（`deny_unknown_fields` 同构） |
| cargo 缓存陈旧、Profile 未真正编译进去 | `require_compiled_deployment` 从成品二进制读回，缺任一项即失败 |
| 产物被 tree-shake 掉登录能力 | 包审计报「cannot reach a required capability」 |
| 相对路径参数 | 在 `parse_arguments` 绑定到调用目录，不再被子进程重新解释 |

## 清理

- 删除 `frontend/package.json` 的 `build:customer-demo-assets`，`customer-demo` vite 模式随之无引用；
- 两个账号验收 tauri 配置的 `beforeBuildCommand` 改回 `pnpm build`，不再存在第二套资产构建；
- 本次验证用的假域名产物与密钥都在 `.local/customer-demo-release/`，不进 Git。

## 遗留

1. **Gatekeeper 未闭环**。正式包仍是 ad-hoc 签名（`codesign --force --sign -`），能否在领导那台 M4 Air 上打开本次**没有实测**；另有一条线在 `docs/development/RESEARCH-demo-machine-readiness.md` 实测签名拦截，以那份为准。
2. **本次交付包未做安装后的用户路径验收**——没有装、没有打开、没有点。按项目规则这一条使本任务最多 `🔍 待验收`。
3. **交付包未在真实 `at.xuanbai.tech` 上跑通端到端**。已证明的是「包确实带着该地址、登录界面与可用的动作授权公钥」，不是「登录能连上那台服务器」——后者要等服务端按 RESEARCH §5.1 部署完成。
4. **U9-04 / U9-06 两条验收本次未实跑**。knob 的行为由 `run_c10_07_acceptance.py` 的两向编译证明，但那两个 App 的完整 WebdriverIO 链路没跑（`run_u9_06_acceptance.py` 会删除整个 App 私有目录，本机有真实抖音登录态，禁止运行）。**这两条必须在别的机器或清空环境上补跑**。
5. `build:tauri:account-session-test` / `build:tauri:account-management-test` 用 `VAR=1 cmd` 形式设 knob，在 Windows 的 cmd.exe 下不成立。这两条验收目前只有 macOS 调用方；若将来要在 Windows 跑，需要改成跨平台形式。
6. 文档中记录旧机制的历史快照未改写（`docs/development/completed-task-wiring-audit-20260726.md:206`、`pending-acceptance-audit-20260726.md:272`、`PLAN-control-plane-delivery.md:279`、`PLAN-production-device-registration.md:57`、`RESEARCH-cloud-deployment-readiness.md:80,364`）。它们是带日期的当时快照，本文件为其后继结论。
