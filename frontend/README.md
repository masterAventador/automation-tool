# Desktop Frontend

本目录只构建 Tauri 桌面客户端的 React UI 资产。Vite 开发服务器用于本机联调和后续 Playwright UI Harness，不是 Web 产品，也没有静态站点部署入口。

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm check:api
pnpm test:ui
pnpm test:rust
pnpm test:tauri
pnpm test:layers
pnpm check:production-boundaries
pnpm dev
pnpm tauri:dev
```

`pnpm dev` 只绑定 `127.0.0.1:1420`。`pnpm tauri:dev` 会显式合并仅开发用的 `tauri.dev.conf.json`，启动这个本机服务和真实桌面窗口。正式 `tauri.conf.json` 不含开发 URL/devCSP，交付必须由 Tauri 打包，不能把 `dist/` 发布为用户入口。

当前 `src-tauri/capabilities/main.json` 不暴露任何 IPC 权限；后续每项原生能力必须随对应任务单独增加最小权限。`src-tauri/app-icon.svg` 是工程占位图标，不代表最终品牌设计。

H8-18 已锁定 Rust `tauri-plugin-updater 2.10.1` 作为 macOS/Windows 更新检查与安装原语；H8-20/H8-21 已在 Rust 注册插件并只开放 `get_app_update_state`、`check_app_update_now`、`decide_app_update` 三个脱敏产品 Command。`src-tauri/src/app_updates.rs` 验证官方 dynamic feed 的 `raw_json` 与通用 `update_contract` v1，并向 `features/app-updates/contracts.ts` 对应的状态闭集投影安全版本、策略和 Artifact 元数据；React 不安装 updater JavaScript binding，`main` Capability 也不授予 updater 权限，所以不能取得下载 URL/签名、安装路径或调用插件原生命令。真实签名包跨版本验收仍由 H8-22 完成。

H8-19 的 `UpdatePolicyService` 已由正式 Tauri setup 使用当前包版本和固定 stable channel 初始化。它只在 App 私有 `app-updates/update-policy-v1` 保存 schema、版本下限、最高已见发布的不可变 identity、可选更新决策和单调 revision，不保存 URL、签名、发布说明或路径；Unix 目录/文件为 `0700/0600`，写入失败时磁盘与内存均不推进。可选更新暂缓后由下一次启动/轮询重新提示，跳过只压制同版本，立即安装意图跨重启保留；强制更新不接受用户选择。同版本换策略/摘要/平台、回退、重复旧按钮和损坏状态全部 fail closed。

H8-20 的 `AppUpdateCoordinator` 让启动、每 6 小时周期检查和用户手动检查共用同一个并发门与状态转换；重叠触发只执行一次官方 updater 检查。`AppUpdateCache` 从 Rust 私有的官方响应取得 URL/Minisign 签名，以 HTTP Range/强 ETag 断点续传，在固定 `app-updates/cache-v1` 中流式复算 SHA-256 与 Minisign，双重通过后才原子替换唯一 `candidate.package`。失败断点可续传，摘要/签名错误不会覆盖旧包，清单不保存 URL、签名或路径。release 构建必须提供经 `build.rs` 验证的 HTTPS endpoint 模板和公开 Minisign 公钥；签名私钥不进入 App。`pnpm test:h8-20-app` 使用隐藏 App、真实 FastAPI feed 和临时 HTTPS 完成中断→手动恢复→精确缓存命中验收。

H8-21 的 `AppUpdateInstallationCoordinator` 只接受协调层持有的 official `Update` 和与 release identity 精确匹配的缓存。读取完整包后再次验证长度、SHA-256 与当前 feed 的 Minisign 签名，成功才隐藏所有可见窗口并调用 `ExecutorPlatformService.shutdown_for_app_exit()`；缓存、停止运行环境或官方安装器任一步失败都返回固定脱敏错误，已隐藏窗口会恢复。Windows 的官方安装原语接管退出和安装，macOS 原位替换成功后调用 Tauri restart；WebView 不能传 package、路径、URL 或签名。`pnpm test:h8-21-app` 以 `visible:false` 的独立 App、专属 `dist-h821` 构建资产和三次启动，验证暂缓再提示、同版本跳过、下一版本恢复提示、立即安装交接，以及强更首次下载后在下次启动无重复下载地安装；finally 删除资产、AppData、服务和端口，不与 production `dist` 或其他验收构建互相覆盖。

设备身份和长期设备凭据只由 Rust 管理。正式 App 首启使用系统 CSPRNG 生成 Ed25519 私钥，并保存到 Tauri `app_data_dir` 下的固定 App 私有文件；长期 `atdc1` 凭据使用同一存储边界。目录在 Unix 为 `0700`、文件为 `0600`，写入使用同目录临时文件、落盘同步和原子替换；Windows 使用当前用户 AppData 继承的私有 ACL。React、Tauri Command、普通配置文件和 `localStorage` 均没有密钥或长期凭据读写面，也不调用 macOS Keychain 或 Windows Credential Manager，因此不会产生系统钥匙串授权提示。已存在的 32 字节私钥只复用不轮换，凭据可替换和删除；符号链接、非法权限、内容损坏、存储拒绝或随机源失败均 fail closed。`desktop-e2e` 构建只使用不落盘的临时身份，App 私有存储由 Rust 行为测试和正式 Tauri 启动验收覆盖。

正式 `TauriControlPlaneTransport` 只调用注册过的 `check_control_plane_health` Command；Task 服务端状态由 `TauriTaskProjectionSource` 通过固定 `get_task_snapshot`、`list_task_snapshots` 和 `stream_task_projection_events` 消费。工作台以固定 `get_workbench_status` 与 `emergency_stop_workbench_task` 读取运行状态并提交紧停；新建页只通过 `TauriTaskCreationGateway` 调用 `create_douyin_search_exposure_task`，发送经 Zod 和 Rust 双重校验的封闭任务定义。运行详情通过 `TauriTaskRunControlGateway` 的四个固定 Command 提交暂停、恢复、取消与紧停，并以权威快照和持久事件展示状态、进度、时间线及已有 Action 结果。请求由 Rust `reqwest` 客户端从固定 local origin 发出，禁止 React 传入任意 URL、Header 或 bearer。Task 快照严格校验 `status/revision/lastEventSequence`、UTC 时间、降序稳定性和 opaque cursor；SSE 通过 Rust Tauri Channel 推送。TanStack Query 维护权威快照和创建/控制后失效，不建立 WebView EventSource 或第二事实源。请求禁止系统代理和重定向；设备签名及凭据注入只在 Rust 内完成。

D6-03 将关键词和目标上限固定为桌面端唯一公开常量与 `douyinSearchKeywordSchema`：关键词按 Unicode code point 计数而不是 UTF-16 code unit，接受 80 个非 BMP 字符并拒绝第 81 个；空值、首尾空白、C0/C1/DEL、Bidi 和安全文本违规在表单调用 Gateway 前拒绝，Gateway 与 Rust 仍再次 fail closed。目标数固定 `1..100`；这些值由跨语言契约与 OpenAPI/Python 公共策略逐项核对，React 不能自行放宽。

API DTO 只能由 `../contracts/openapi/control-plane.v1.json` 生成：

```bash
pnpm generate:api
pnpm check:api
```

`src/api/generated/control-plane.ts` 禁止手改。后端契约先由 FastAPI 导出快照，前端再生成并检查逐字漂移。

Executor v1 使用 `src/api/protocol/executor-envelope.ts` 的 Zod 判别联合和 `src-tauri/src/executor_protocol.rs` 的 Rust 解析器。两者与 Python `parse_executor_message` 回放 `../contracts/fixtures/executor-v1/` 的同一组 10 个 valid、27 个 invalid 原始 wire 文件；未知字段/类型、重复 key、非规范 UUIDv4、非 UTC 或微秒倒序 deadline、超出 JavaScript 安全整数的 sequence、任务作用域混淆、资源滥用和敏感 payload 都 fail closed。TypeScript/Rust 解析错误只返回固定公开信息，不反射原始输入；I2-13 已在 Control Plane 侧让真实 WebSocket 帧经过 Python 正式入口，E4-02/E4-12 再接入 Local Executor 进程，React 只消费公开投影。

E4-05 的 `src-tauri/src/executor_package.rs` 是签名 `onedir` 的唯一运行时信任边界。验证器只接受 Rust 原生调用方提供的可信 Ed25519 公钥、显式 `semver` 允许范围和可选已安装版本，没有 Tauri Command、React 参数、服务端 key、URL 或在线下载入口。它先严格验证 `atems1` 对 Manifest 原始字节的签名，再拒绝非 canonical/重复/未知字段，绑定当前 macOS/Windows 与 aarch64/x86_64，最后两次枚举完整目录并以稳定文件 identity 逐项复算大小、SHA-256 和目录摘要；弱公钥、版本越界/回退、symlink、非普通文件、目录增删和 payload 篡改均只返回固定错误。E4-07 装配进程监管时必须从 App 自己的受信资源/编译配置提供公钥和路径，不能新增 IPC 信任参数。

E4-06 的 `src-tauri/src/executor_bootstrap.rs` 是本机进程启动认证边界。它用系统 CSPRNG 每次生成不可克隆、Drop 时清零的 32 字节 `LocalSessionToken`，只把其 64 位小写十六进制编码写入单行 stdin bootstrap；Control Plane `executor.connect` Session 是另一个字段和用途。Python 健康/停止事件只返回 `atlep1` HMAC-SHA-256 证明，Rust 以受维护的 `hmac` crate 做常量时间校验并绑定事件名和 Executor 协议版本。模块没有 Tauri Command、argv、环境变量、日志、任意进程或 React 接口；E4-07 只能在包验证通过后由 Rust Manager 使用它启动正式 Executor。

E4-07 的 `src-tauri/src/executor_manager.rs` 是固定 start/status/stop 生命周期边界。它在每次 start 前复验 signed onedir，从 Manifest 精确入口无参数启动单一子进程，经 stdin 写入并关闭 E4-06 bootstrap，只接受有界且认证的 healthy/stopped stdout 事件；并发 start 由 Mutex 线性化，超时、坏包、坏证明和 Drop 均 fail closed 并回收直接子进程。模块不提供旧 stdio `invoke`、任意 payload 或 capability 命令；E4-13 已从固定 PlatformAdapter 接入，E4-14 已由隐藏真实 App 完成 macOS/Windows 纵向验收。`../scripts/run_e4_07_acceptance.py` 已从公开 Rust Manager 原入口完成真实签名 PyInstaller→Uvicorn 的 macOS arm64 与 Windows x86_64 全链路；Hosted Windows CI Billing 限制只保留为持续集成覆盖缺口。

E4-08 在同一 Manager 内增加唯一命名 supervisor thread 和显式 `ExecutorRestartPolicy`。状态机公开 running/restarting/stopped 与已消耗 `restartCount`；当前 MVP 预算为 2。只有 OS 异常崩溃进入延迟恢复，每次恢复重新验包、生成新 stdin 本机会话并验证 healthy；显式 stop、正常退出、固定失败退出、坏包/坏认证不会重启。Manager Drop 先关闭并 join supervisor，再回收子进程。macOS 已用真实签名进程、SIGKILL 与公开 Manager 入口验证两次恢复和预算耗尽；完整 PyInstaller 路径也已回归。Windows 原生仍待 runner。

E4-09 继续在同一 `executor_manager.rs` 内为每次启动建立独立 OS 进程容器。Unix 在 exec 前调用 `process_group(0)`，所有强制边界向负 PGID 发 `SIGKILL`；显式 stop 先让主进程提交认证 stopped proof，主进程退出后仍清理剩余后代。Windows 以 `CREATE_SUSPENDED` 消除挂 Job 前的派生竞态，配置并挂入 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object 后才恢复主线程，失败即关闭/终止。真实签名进程生成的忽略 `SIGTERM` 孙进程已证明正常停止、挂起停止超时、启动超时、异常恢复和 Manager Drop 均无残留；macOS 通过，Windows 类型与行为待原生 runner。

E4-10 的 `src-tauri/src/executor_diagnostics.rs` 是 stderr 的第二道信任边界。Manager 不使用 `lines()` 或 `read_to_end`，而是以 `fill_buf/consume` 流式排空；单行超过 4096 bytes 或非法 UTF-8 时只留固定占位，避免无界分配和截断秘密。有效行移除 Authorization/Bearer、设备/本机会话、Cookie、密码/私钥、URL userinfo/全部 query、data/file URL、macOS/Linux/Windows 私有路径及控制/Bidi 字符，然后以 200 行/64 KiB 滚动内存队列保留。`diagnostics()` 只返回安全副本，不含原始 stderr、PID 或 Session，也不写 App data/钥匙串。Python 与 Rust 共同回放根目录 14 组 fixtures；macOS/Windows 真实 signed Executor stderr 均已从 Manager 原入口证明脱敏和三重限界。

E4-11 扩展同一个 `executor_bootstrap.rs`/`executor_manager.rs`，由 Rust 持有并验证绝对 `state_directory: PathBuf`，只经一次性 stdin 交给正式 Executor；路径不会来自 React 或通用 Tauri Command。Python CLI 在联网前于该目录创建固定 `executor-ledger.sqlite3`，完成 v1 迁移和 Installation/Executor 绑定。账本保存 command/idempotency 指纹、Attempt checkpoint 和待发送协议 outbox，不保存 Control Plane/本机会话、Cookie、密钥或任意配置，也不使用钥匙串。E4-12 已让正式 Executor 从真实 Control Plane 消费 `task.offer` 并精确重放持久 ACK/Event；E4-13 已从 App 私有 `app_data_dir/local-executor/state` 固定装配，仍没有 React/Tauri 账本 API。

E4-13 的 `src-tauri/src/executor_platform.rs` 从 Tauri `app_data_dir` 固定派生 `local-executor/package`、`state` 和 `executor-id-v1`。Executor UUIDv4 在 App 私有 `0700/0600` 边界原子持久且重启复用；React 不能提交路径、URL、身份或 Session。正式 `TauriPlatformAdapter` 只有状态、重启、脱敏诊断和本机进程树紧停四个无参数 invoke；“设置与诊断”页面严格校验公开 DTO，并明确本机硬停止不等于业务 Task 已停止。Rust 重启链自行换取当前 Installation 与独立短期 `executor.connect` Session，秘密仍只在清零内存/stdin 中。

E4-14 已用唯一 `visible=false` 真实 App 从诊断页完成启动、状态刷新、本机紧停和再次启动。链路经过正式 TypeScript Adapter、Tauri IPC、Rust Control Plane client、真实 PostgreSQL/Uvicorn 与 signed PyInstaller Executor；实际 `SIGKILL` 验证 supervisor 恢复，实际挂起验证超时后的完整进程树回收，App 正常退出则由生产 `RunEvent::ExitRequested/Exit` 路径显式停止 Executor。测试专用动态 loopback origin 与故障注入只在 `control-plane-e2e` 构建存在；App 私有 UUID/SQLite 和 `0700/0600` 权限均已核对，凭据不入 SQLite，也不调用系统钥匙串。

```bash
backend/.venv/bin/python scripts/run_e4_14_acceptance.py
```

该编排先检查并选取动态端口，使用 `automation-tool-e414-<pid>` 专属 Compose project、独立 AppData 和临时目录，结束时只清理本次资源。嵌入式 WebDriver 在 App 正常退出后关闭会话时会固定收到 `ECONNREFUSED`；runner 只接受没有测试断言错误的这一精确退出签名，并额外检查 Executor 进程已消失。

E4-15 把正式制品审计提升到实际 release 二进制。`build.rs` 只在 release Profile 强制读取编译期 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY`，并在 Tauri 打包前拒绝缺失、非 canonical Base64URL、非 32 字节、无效或弱 Ed25519 公钥；debug 才能使用公开开发 fixture signer。正式 `tauri.conf.json` 不再含 `devUrl`/devCSP，开发时必须走 `pnpm tauri:dev` 显式合并 `tauri.dev.conf.json`。

```bash
backend/.venv/bin/python scripts/run_e4_15_acceptance.py
```

该脚本先实证缺失/畸形公钥的 release 构建失败，再用验收专用公开公钥在唯一临时 Cargo target 构建真实 production-mode Tauri binary；`audit-production-package.mjs` 扫描二进制、正式 Vite 资产、Tauri 配置和 `cargo tree --no-default-features`，拒绝 WebDriver/WDIO、所有验收 Command、测试 origin/资源/Sidecar、开发验证公钥和 1420 调试端口，并确认制品确实绑定本次预期发布公钥。App 不会启动，临时制品结束即删除。

B5-02 的 `src-tauri/src/browser_discovery.rs` 只在 Rust 原生层发现 macOS 标准 `/Applications` Chrome/Edge。它通过 Security.framework 绑定正式 Bundle signing identifier、Developer Team、所有架构、嵌套代码和固定主可执行文件，并保存 App/入口 dev+inode 供启动前重新验签；React、Tauri IPC、Control Plane 和普通设置均没有任意路径入口。本机真实 Chrome 已经公开生产 API发现/复验通过，Microsoft Edge 因未安装保持真实验收待办；当前模块只发现不启动浏览器，也不读取用户默认 Profile。

B5-04 已把浏览器枚举接入“设置与诊断”。WebView 只接收 `google_chrome` / `microsoft_edge` 和当前选择，不接收应用路径、可执行文件路径或 identity；保存 Command 只接受枚举，并在 Rust 内重新发现受信安装后才原子写入 `app_data_dir/settings/browser-selection-v1`。`pnpm test:browser-settings-tauri` 使用动态检查过的 loopback 端口、独立 `com.aventador.automationtool.b504acceptance` AppData 和唯一 `visible=false` Tauri App，从真实页面保存、刷新并重读选择，结束精确清理。

B5-05 新增原生 `BrowserProfileStore`：Tauri setup 从自身 AppData 管理唯一实例，只生成 `browser-profiles/douyin/<canonical UUIDv4>`，不接受 WebView、服务端、昵称、账号或任意路径输入。Unix 以父目录句柄相对 `openat/mkdirat` 创建并固定 `0700`，Windows 以父 HANDLE 相对 `NtCreateFile` 创建并设置当前用户 protected DACL；两端都持有并复验稳定目录 identity，symlink/reparse、普通文件和路径替换全部 fail closed。当前没有 Profile Tauri Command，也不会启动浏览器；B5-06/B5-07 将直接消费这一 Rust 对象。

B5-06 已为该 Profile 对象加入跨进程单实例锁和崩溃恢复标记；Playwright driver 进入 Executor onedir，但不下载或捆绑浏览器。B5-08 的正式 BrowserRuntime 只存在于 Python Executor，提供单 context、线程约束、页面/窗口、有界超时和确定关闭；macOS 冻结验收从同一 Rust 受信浏览器/Profile/锁链路完成系统 Chrome 双窗口正常关闭及 process-group 整树强杀。探针、路径、原始 Page 和 Profile identity 均不进入正式 React/IPC 面，B5-09 才消费页面证据。

B5-15 的 `pnpm test:platform-session-reuse-tauri` 只启动 `visible=false` 的独立 App 标识。四个 App 生命周期复用同一 AppData/Profile，前两轮从页面操作验证重启后直接健康，后两轮验证过期扫码和风控人工接管；runner 在每轮 App 退出后核对 Profile marker、device/inode、Executor/Chrome 清理及最终服务端投影。测试页面只在单独签名的验收 Executor 中路由到官方 origin，不进入正式 Executor、Vite 资产或发布配置；真实账号最终证据不由该夹具替代。

B5-16 的 `pnpm test:default-profile-isolation-tauri` 使用另一个 `visible=false` App 标识，并从同一正式平台状态页面打开无头系统 Chrome。WDIO 只用两个临时信号文件建立“浏览器已活跃/审计已结束”握手，不接收 Profile ID、目录或 Cookie；外层 runner 在 Chrome 活跃窗口检查完整进程树的 `--user-data-dir` 和 `lsof` 打开文件，只允许 Rust `BrowserProfileStore.current_douyin_profile()` 派生的 App 私有 Profile。生产源码契约还递归拒绝 Chrome/Edge 默认 User Data 与 Cookie/storage-state API，测试结束核对浏览器和项目资源零残留。

`harness.html` 和 `src/test-harness/` 只供 Playwright 本机 UI 测试。任务生命周期场景使用窄测试 Adapter 与 `sessionStorage` 模拟创建、暂停、恢复、取消、成功和整页刷新恢复。正式 Vite 构建只以 `index.html` 为入口；`pnpm check:production-boundaries` 会重新构建并扫描产物，若发现 Harness 页面、运行标记或测试 Adapter 标记立即失败。UI Harness 通过只代表 React 交互，不代表 Tauri IPC、Rust、Sidecar 或 RPA 可用。例行 Playwright 在全局配置显式固定 `headless: true`，跨目录契约也要求所有非真实账号、非冻结 headed 探针的 Python 浏览器用例显式 `headless=True`；新增漏项会在启动浏览器前直接失败。

四层桌面测试命令分别是：`pnpm test:unit`（Vitest）、`pnpm test:ui`（Playwright UI Harness）、`pnpm test:rust`（Rust）和 `pnpm test:tauri`（真实 Tauri + WebdriverIO）；`pnpm test:layers` 顺序执行全部层级。`test:tauri` 只构建带 `desktop-e2e` Cargo 特性、测试专用前端入口和内联测试 Capability 的 debug App；正常构建仍保持 `withGlobalTauri=false`，正式 Cargo 依赖树不启用 WDIO 插件，生产资产扫描也拒绝 WDIO 标记。所有自动化 Tauri 构建都通过测试专用配置把主窗口设为 `visible=false`，在后台运行且不抢占用户前台；正式 `tauri.conf.json` 不包含这个覆盖，产品窗口正常可见。

H8-16E 的生产启动入口使用 `createDesktopStartupCheck` 并行聚合正式 Control Plane Health 与唯一 `check_local_startup_environment` Command。Rust 每次只读复验 App 私有目录、浏览器 Profile 存储、受信 Chrome/Edge 选择、编译期动作信任配置和 signed Executor package；WebView 只能得到三项封闭状态，不能得到路径、版本、PID、凭据或底层错误。业务工作台在全部 ready 前不挂载；本机问题可展开既有浏览器设置/Executor 诊断并重新检查，纯网络故障和 Installation 吊销不会显示无关修复入口。`pnpm test:h8-16e-tauri` 使用唯一 `visible=false` App、动态端口、真实签名 Executor 和真实本地 Control Plane，从页面保存浏览器选择后进入工作台，并验证 Executor 始终 stopped；测试结束统一清理驱动、App、Executor、AppData、端口并恢复生产 Vite 资产。

H8-16B 保留同一个 `TaskDiscoveryGateway` 和固定 `start_task_discovery` Command；Control Plane 返回的 `423 installation_task_active` 经 Rust `InstallationBusy` 与 TypeScript `installation_busy` 原样分类为不可重试安全错误，任务详情显示“当前设备已有任务正在运行”，不会展示活动 Task ID、底层数据库错误或凭据。D6-10 隐藏 App 验收从两个真实草稿 Task 的页面按钮依次发起请求，第二个请求看到忙碌提示后才启动 Executor 收敛第一个 Task；测试专用同步 Command 不发业务请求且不进入正式构建。

H8-11 复用既有“设置与诊断”页和正式 `get_executor_diagnostics` Command，不增加通用日志查看器。Rust Manager 对真实 Executor stderr 按公共 fixture v2 二次脱敏并执行 200 行/单行 4096 bytes/总计 64 KiB 内存上限；React 只能收到已经限界的安全行，不能提交查询、路径、URL 或任意日志输入。隐藏 `visible=false` E4-14 App 已从同一正式读取入口验证 hostile 凭据、页面内容、URL、Cookie、异常和私有路径均不会到达 WebView；测试准备 Command 仅在 `control-plane-e2e` 特性存在，不进入生产制品。

I2-09 的生产同路径纵向验收必须从仓库根的 Python 3.12/uv 后端环境执行：

```bash
cd backend
uv run python ../scripts/run_i2_09_acceptance.py
```

脚本会使用随机密码和端口启动隔离 PostgreSQL、执行正式 Alembic 链、启动真实 FastAPI，再由隐藏的真实测试版 Tauri App 经正式 Rust 桥完成 Health → 注册 → App Session → 轮换 → Executor Session → 吊销。最后同时核对 App 私有目录和 PostgreSQL 最终状态，并回收服务、容器、卷及精确隔离 App 数据目录；直接运行 HTTP 客户端或 UI Harness 不能替代该验收。

I2-14 的 Installation 吊销纵向验收同样必须从 uv 后端环境执行 `uv run python ../scripts/run_i2_14_acceptance.py`。它会验证测试配置的主窗口为 `visible=false`，再由隐藏真实 App 完成注册和受保护访问，由服务器运维 CLI 原子吊销 Installation，最后让同一 App 从正式启动入口进入“当前安装实例已失效”；长期凭据只保留在隔离 `app_data_dir` 私有文件中且不经 React/IPC 返回，结束后精确清理。

T3-06 的创建任务纵向验收执行 `uv run python ../scripts/run_t3_06_acceptance.py`。它先校验专用 Tauri 配置只有一个 `visible=false` 主窗口，再由隐藏真实 App 经正式 Rust 客户端完成 Installation 注册、两次 App Session 换票和同键创建/重放；最终核对 App 私有身份/凭据权限、PostgreSQL 只有一条 draft Task，结束后清理隔离 App 数据、Uvicorn、容器、卷和端口。直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该验收。

T3-07 的任务查询纵向验收执行 `uv run python ../scripts/run_t3_07_acceptance.py`。脚本先预置另一个 Installation 的 Task，再由唯一 `visible=false` 真实 App 经正式 Rust 客户端完成注册、创建三个自有 Task、2+1 游标分页、详情读取和跨 Installation 不可见检查；最终核对两个 Installation、四条 Task、七张正式 App Session、App 私有身份/凭据权限及预置数据未被改动，结束后精确清理。直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该验收。

T3-12 的事件流纵向验收执行 `uv run python ../scripts/run_t3_12_acceptance.py`。唯一 `visible=false` 真实 App 从私有 vault 换取 App Session，在 Executor 事件到达前经正式 Rust 客户端建立 SSE，读取序号 1、2 后主动断开，再以新 Session 和 `Last-Event-ID: 2` 续拉 3、4、5，核对 progress 50 与终态关闭；FakeExecutor 同时经正式 Session/WebSocket 产生持久事件。验收回收 App 数据、Uvicorn、PostgreSQL 容器/卷和端口，不弹窗、不抢焦点；直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该产品入口证据。

T3-13 的暂停/恢复纵向验收执行 `uv run python ../scripts/run_t3_13_acceptance.py`。唯一 `visible=false` 真实 App 经正式 Rust `pause_task`/`resume_task` 写入命令，并通过同一 Rust SSE 客户端等待 `task.paused`/`task.resumed`；HOLD FakeExecutor 从正式 Session/WebSocket 处理 offer、pause、resume。验收核对命令 sequence 1/2/3 全部 acknowledged、事件 sequence 1..4、Task 最终 running/revision 5，并回收 App 私有测试目录、Uvicorn、PostgreSQL 容器/卷和端口，全程不弹窗、不抢焦点。

T3-14 的取消/紧停纵向验收执行 `uv run python ../scripts/run_t3_14_acceptance.py`。唯一 `visible=false` 真实 App 依次创建两个 Task，并从正式 Rust `cancel_task`/`emergency_stop_task` 发出请求；同一 HOLD FakeExecutor 从正式 Session/WebSocket 分别回报 `task.cancelled` 与 `task.outcome_uncertain`。验收核对每个 Task 的 offer/control 命令均 acknowledged、事件 sequence 1..3、最终 revision 5，并清理 App 私有目录、服务、容器、卷和端口，全程不弹窗、不抢焦点。

T3-15 的 Query/事件投影纵向验收执行 `uv run python ../scripts/run_t3_15_acceptance.py`。唯一 `visible=false` 真实 App 在 WebView 中调用正式 TypeScript source：先用 TanStack Query 经 Rust 拉取带水位的 Task 快照，再从同一 Rust SSE 通过 Tauri Channel 实时接收事件，由正式 Reducer 收敛 sequence 1..5 到 succeeded。FakeExecutor 经正式 Session/WebSocket 产生事件；验收核对最终 revision/watermark、App 私有身份/凭据权限，并清理 App、服务、容器、卷和端口，全程不弹窗、不抢焦点。

T3-16 的工作台纵向验收执行 `uv run python ../scripts/run_t3_16_acceptance.py`。唯一 `visible=false` 真实 App 从页面加载当前/最近 Task 和 Control Plane/Executor 状态，再真实点击“全局紧急停止/确认紧停”；正式 Rust client 写入紧停命令，HOLD FakeExecutor 从生产 Session/WebSocket 收到并 ACK，事件把 Task 收敛为 `outcome_uncertain`。运行状态轮询在隐藏窗口仍保持，设备私钥与长期凭据仍只在测试 App 的 `app_data_dir` 私有文件中。

T3-17/A7-05 的新建任务纵向验收在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_17_acceptance.py`。唯一 `visible=false` 真实 App 从页面进入“新建任务”，先证明未知变量在表单层被拒绝且不发起请求，再提交唯一合法 `{{target_display_name}}` 个性化文案；请求经正式 `TauriTaskCreationGateway`、固定 Rust Command、真实 Uvicorn/PostgreSQL 写入精确 `douyin.search_exposure.v1` 定义。App 不弹窗、不读取系统钥匙串，验收结束清理隔离 App 数据和后端资源。

T3-18/A7-15 的运行详情纵向验收在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_18_acceptance.py`。唯一 `visible=false` 真实 App 打开两个有持久事件和目标结果的 Task，从页面发出固定目标结果查询并核对成功、跳过、失败、不确定及受限证据，再依次真实点击暂停、恢复、取消和紧急停止；正式 TypeScript source/gateway、Tauri IPC、Rust Session 网络桥、后端 Outbox/HOLD FakeExecutor/PostgreSQL 链路将命令全部 ACK，并把两个任务分别收敛为 `cancelled` 与 `outcome_uncertain`。验收不操作社交平台、不读取 Executor SQLite，结束后清理隔离 App 数据、服务、端口和 PostgreSQL/Compose 资源。

T3-19 的完整生命周期纵向验收在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_19_acceptance.py`。唯一 `visible=false` 真实 App 从页面创建两个 Task：首个依次真实点击暂停、恢复、取消，第二个由受控 Executor 收敛为成功；随后整页刷新并从工作台重新进入成功详情。正式 TypeScript gateway、固定 Rust Command、真实 Uvicorn/PostgreSQL、Outbox 和 Executor 链路全部参与，最终核对命令 ACK、事件、revision、水位与两个页面创建的定义；设备秘密只在隔离 `app_data_dir`，结束后精确清理。

T3-20 的重启恢复纵向验收在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_20_acceptance.py`。唯一 `visible=false` App 从页面创建并运行 Task，在 Executor 离线时真实提交取消；runner 停止首个 Uvicorn 后整页刷新验证“Control Plane 不可用”，再以同一 PostgreSQL 启动第二个 Uvicorn。同一 FakeExecutor/Session 自动重连并消费原 pending cancel，App 点击“重新检查”后从工作台和详情读取 `cancelled`；最终核对原 Task/Command/Event ID、定义、revision 与水位未丢失，秘密仍只在隔离 `app_data_dir`。

H8-04 的 App 崩溃恢复纵向验收在仓库根目录执行 `backend/.venv/bin/python scripts/run_h8_04_acceptance.py`。runner 只构建一次独立 `visible=false` H8-04 Tauri 二进制；第一个 App 从真实表单创建 comment Task，经正式 IPC 启动签名 Executor 并在工作台/详情读取 running 后，只对经系统复核的 App PID 发硬杀。签名 Executor 保持唯一且继续在线；第二个 App 复用同一 `app_data_dir`，不调用任何准备、创建、控制或 restart Command，只从正式工作台和详情恢复原 Task/事件。验收逐字段比较崩溃前后的 PostgreSQL 业务事实和 SQLite 副作用账本，保证任务、命令、事件与平台许可均未重复。

H8-05 的 Executor 崩溃恢复纵向验收执行 `backend/.venv/bin/python scripts/run_h8_05_acceptance.py`。唯一 `visible=false` App 从真实表单创建 comment Task，经正式诊断页启动签名 Executor，再调用 feature-gated 故障注入 IPC；页面必须看到“自动恢复次数 1”，随后从正式工作台与详情读到“结果待确认”。runner 同时锁定进程始终只有一个、restart budget 只消耗一次、Control Plane 仅有一条 recovery Event，以及 SQLite 一条 uncertain/一条 prepared 和唯一 delivered outbox；不启动运营浏览器或访问真实账号。

H8-06 的 Control Plane 重启恢复纵向验收执行 `backend/.venv/bin/python scripts/run_h8_06_acceptance.py`。唯一 `visible=false` App 从真实表单创建并运行 comment Task，经正式诊断入口启动签名 Executor；runner 只暂停该精确 PID，App 从原详情页点击取消并确认服务端已持久化 delivered Command，随后真实停止并以同一 PostgreSQL 重启 Uvicorn。App 在停服时展示不可用，恢复后从正式工作台/详情读到取消终态；同一 Executor PID、Session、`restartCount=0` 和本机/云端唯一命令事件事实证明过程没有由 supervisor 重启或测试夹具代打。

H8-07 的异常网络恢复纵向验收执行 `backend/.venv/bin/python scripts/run_h8_07_acceptance.py`。专用 `tauri.network-recovery-e2e.conf.json` 保持唯一窗口 `visible=false`；App 仍从正式表单、详情取消、Rust 网络桥和 Executor 生命周期命令发起调用。runner 强杀真实 Uvicorn 制造无 WebSocket close frame 的断网，核对同一 Executor PID 离线落盘且拒绝 prepared 动作 dispatch，再用同一 PostgreSQL 恢复服务并连续抖动两次；App 最终从工作台/详情读取 cancelled，`restartCount=0`，测试与生产配置隔离。

D6-11 在既有 `ControlPlaneClient` 和 `TauriPlatformAdapter` 上增加三个固定操作：读取目标预览、精确替换排除集合、确认当前 revision。Rust 自行换取短期 App Session，只构造固定 task-scoped 路径并严格解析有界、脱敏 DTO；React 不能提交 base URL、Session、平台目标 ID、dedupe key 或浏览器事实。`task-target-preview-source.ts` 用同一 Zod 边界拒绝未知字段、乱序、非法状态和不一致计数。仓库根执行 `backend/.venv/bin/python scripts/run_d6_11_acceptance.py` 会从唯一 `visible=false` App 经正式 TypeScript source/Tauri Command/Rust 网络桥连接真实 Uvicorn/PostgreSQL，完成列表、排除、确认及幂等重放。

A7-06 在同一目标预览 DTO 增加封闭 action、原始 message template 和 `confirmationRevision`。页面的最终确认区与 Popconfirm 同时展示动作、文案、数量和 revision；弹窗打开时用受控状态与同步 ref 冻结完整审阅快照，后台 Query/事件刷新不能把待提交 revision 偷换成新值。旧提交由正式 Rust Command 发往真实 Control Plane 后返回 `request_rejected`，专用 Tauri 错误适配保留该冲突语义，页面显示安全提示并回拉最新预览；其他原生错误仍不反射底层文本。`scripts/run_d6_12_acceptance.py` 已用隐藏真实 App 验证旧 revision 拒绝与重新审阅后成功确认。

D6-12 将正式 source 注入任务详情中的 `TaskTargetPreviewPanel`。页面展示有界最小摘要、固定来源、计划执行/本次排除/策略拦截计数和去重/黑名单标记，支持单项选择、全部取消、恢复全部及最终二次确认；空选择不能确认，过期 revision 会回拉，同意图网络不确定重试复用幂等键，错误不显示底层文本或私密目标 ID。执行 `backend/.venv/bin/python scripts/run_d6_12_acceptance.py` 会在独立隐藏 Tauri App 中真实点击取消和确认，经正式 React/source/IPC/Rust/HTTP/PostgreSQL 核对任务进入 `queued`；测试窗口不会显示，也不启动运营浏览器。

`@wdio/tauri-service 1.2.0` 的发布清单仍将 `@wdio/native-utils` 固定在缺少其已调用导出的 2.4.0，因此 `pnpm-workspace.yaml` 通过官方依赖 override 固定到已提供该导出的 2.5.0；未修改任何第三方源码。当前 embedded provider 的成功测试仍会输出两条上游诊断噪声：误检查外部 `tauri-driver`，以及会话销毁后清理空 mock；两者不影响真实 WKWebView 会话和测试结果，项目不会因此安装未使用的外部驱动。

CI 在 `quality.yml` 的 Ubuntu Frontend/Rust job 重放契约、单测、Lint、类型、UI Harness、生产边界，以及默认、`desktop-e2e`、`control-plane-e2e` 三种 Cargo 配置检查；`desktop.yml` 再用 GitHub Hosted macOS/Windows 以验收专用公开公钥构建并审计临时 release binary，然后运行后台隐藏窗口的真实 Tauri 冒烟。两条工作流都只有 `contents: read`，不读取 secret、不上传安装包，也不执行发布或部署。
