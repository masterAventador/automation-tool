# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线、Control Plane 应用工厂、lifespan、统一错误处理、Health/Version API、SQLAlchemy asyncpg/Alembic 数据库基线、六类不可混用的稳定资源 ID、Installation 持久化表、无账号 Installation 注册 API、版本化设备凭据生命周期、短期设备 Session 交换、Executor v1 Envelope、受认证 Executor WebSocket、单活连接 Registry、工作台运行状态、持久命令投递/重连/ACK、Task 事件原子收敛与 SSE 续拉、无副作用 FakeExecutor、正式 Local Executor 最小进程、纯领域任务状态机、Task/Attempt/Action/Event/Command 持久化模型，以及 Task 幂等创建、隔离查询、暂停/恢复和取消/紧停 API。React 工作台已消费 Control Plane 权威事实；正式 Executor 当前只完成 bootstrap、Hello/Heartbeat 和信号退出，不执行任务或平台动作。

`automation_tool.protocol.executor_envelope` 是 Control Plane 与 Local Executor 唯一共享的 v1 wire envelope。正式输入必须使用 `parse_executor_message` 解析：只接受最大 32 KiB 的 UTF-8 JSON object，拒绝重复 key、未知 envelope 字段、非 `1.0` 版本、未知 message type、非 canonical UUIDv4、非 UTC 时间、倒序 deadline、非法幂等键和超出 JavaScript 安全整数范围的序号。生命周期消息没有伪造的 task ID；任务命令、回执和事件必须同时绑定 task/attempt。Payload 最大 16 KiB、深度 8、单集合 64 项、单字符串 4096 字符，并拒绝 Cookie/Token/密钥字段、私有路径、inline data URI、非有限数字和双向控制字符；所有解析失败只返回不挂底层异常链的固定错误。

权威 Schema 固定为 `contracts/protocol/executor-v1.schema.json`，只能由 Pydantic 源通过 `automation-tool-export-executor-schema` 生成。`contracts/fixtures/executor-v1/` 包含 6 个 valid 和 25 个 invalid wire 样例；标准 Draft 2020-12 validator 负责 15 个结构层失败，Python、Rust、TypeScript 正式解析器另外实现 Schema `x-semantic-validation-required` 声明的 10 个 deadline、重复 key、敏感信息、私有路径、inline data、非有限数字和递归资源失败。三端必须回放同一目录，不能复制另一套 fixture。

资源 ID 统一使用规范小写 UUIDv4，并通过 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 值对象隔离；一次在线连接另使用短生命周期的 `ExecutorConnectionId`。外部字符串必须先调用对应类型的 `parse`，新资源调用 `new`；不能把普通字符串、另一类资源 ID 或非 UUIDv4 值直接带入领域层。

`control_plane.domain.task_state_machine` 定义 16 个 `TaskStatus`、5 个无出边终态和唯一显式转换矩阵。取消必须先进入 `cancelling`；取消/完成竞态从 `cancelling` 按真实事实收敛；`outcome_uncertain` 只可从已执行、人工接管或取消中的状态进入。所有 256 个状态对均由单元测试分类，字符串输入、自循环、终态复活和未列出的跳转固定拒绝，后续应用服务不得另建状态分支。

`tasks` 表已通过迁移 `20260718_0006` 建立 Task UUIDv4、Installation 外键、状态、正 revision 和有序时间，并保留 `(id, installation_id)` 复合绑定及 Installation 更新时间索引。迁移 `20260718_0010` 增加受协议字符集/128 字节上限约束的 `creation_idempotency_key`，并以 `(installation_id, creation_idempotency_key)` 唯一；旧 Task 确定性回填 `legacy:<task-id>`。迁移 `20260718_0013` 增加与 `(task_id, installation_id)` 强绑定的 `douyin_search_exposure_definitions`，以明确列保存版本、关键词、动作、消息模板、目标上限、间隔和强制确认开关，不保存任意 JSON。`SqlAlchemyTaskRepository` 先锁 Installation，再原子写 Task 与定义；同 scope/key 只有定义完全一致才重放，改参数会拒绝。读取/转换始终携带 Installation scope，状态转换复用领域状态机并以 expected revision + 行锁做 CAS。

`POST /api/v1/tasks` 只接受 `douyin.search_exposure.v1` 的封闭 DTO 和必填 `Idempotency-Key`，并强制复用 `require_current_installation_access` 的精确 `app.control-plane` Session scope。DTO 明确校验安全关键词、browse/comment/direct_message、动作与消息模板关系、`1..100` 目标上限、`1..3600` 有序间隔，以及固定开启的预览和最终确认。第一次创建返回 201；同 Installation/key/定义重放返回相同公开快照和 200；响应不回显定义、幂等键、Session 或凭据。未知字段、敏感文本、改意图重放和持久化冲突均进入稳定 `no-store` 错误边界。

`GET /api/v1/tasks` 与 `GET /api/v1/tasks/{task_id}` 使用同一服务端 Installation scope。列表按 `(updated_at DESC, task_id DESC)` 做 PostgreSQL keyset 分页，`limit` 为 `1..100`，下一页游标是长度受限、canonical JSON 编码的 opaque Base64URL；重复 key、未知字段、非法 UUID/UTC 时间、非规范编码和畸形 Base64 均统一返回 422。详情对非法、未知和其他 Installation 的 Task 统一返回相同 `task_not_found` 404；列表和详情只返回公开快照并固定 `no-store`，不泄露幂等键、凭据或其他 scope 是否存在。

迁移 `20260718_0007` 新增 `execution_attempts`、`task_actions` 和 `tasks.current_attempt_id`。Attempt 以 `(task_id, attempt_number)` 去重，部分唯一索引保证每个 Task 最多一个非终态 Attempt；终态必须有完成时间，重试只能创建新序号。Action 以 `(execution_attempt_id, ordinal)` 去重，明确区分 planned/authorized/prepared/dispatched/verified 等副作用阶段与 pending/succeeded/failed/cancelled/outcome_uncertain 结果；数据库拒绝阶段、结果和完成时间矛盾。Task→Attempt→Action 全链路使用包含 Task 与 Installation 的复合外键，不能把其他任务或安装实例的子资源挂入当前链路。

迁移 `20260718_0008` 新增 `task_events` 与 `tasks.last_event_sequence`。19 种事件使用独立 `1.0` 版本，序号统一限制为 Python/Rust/TypeScript 可无损表达的 `1..2^53-1`；`(task_id, sequence)` 主键拒绝重复，事件可选引用 Attempt/Action，但复合外键始终锁死同一 Task 和 Installation。`SafeTaskEventMessage` 与 Executor payload 复用一套敏感赋值、私有路径、inline data、控制/双向字符拒绝规则，数据库再拒绝空值、超长、控制字符和明显凭据；表内没有任意 JSON 或页面原文。

迁移 `20260718_0011` 为既有事件确定性回填并强制保存 `source_idempotency_key` 与 32 字节 `source_fingerprint`，message ID 和 idempotency key 都按 Installation 唯一。`TaskEventConvergenceService` 将 14 种 Executor TaskEvent 映射到封闭领域事件和 Task/Attempt 目标；step payload 只允许可选规范 `action_id`，progress 另要求 `0..100` strict integer，Action 只有显式绑定时才推进，不能猜测当前动作。仓储先锁 Task，精确重复返回当前快照；冲突、缺口、非精确迟到、跨 scope、非法状态和服务端时间回退固定拒绝。合法下一事件在一个事务内插入事实并以 revision/watermark CAS 更新 Task、Attempt 和显式 Action，任一失败全部回滚。

迁移 `20260718_0012` 增加可选、受 `0..100` 和 `step.progress` 类型约束的 `progress_percent` 明确列；收敛服务把已验证的结构化进度写入该列，不引入任意 JSON。`GET /api/v1/tasks/{task_id}/events` 复用 `app.control-plane` Installation scope，非法/未知/跨 Installation Task 统一不可见。仓储用一个 PostgreSQL MVCC 查询同时读取 Task watermark/状态和最多 100 条已提交事件，严格按 sequence 返回；未提交事件及其投影不可见，水位前存在空洞或错 Task 结果会 fail closed。

SSE 使用标准十进制 `Last-Event-ID`，拒绝非规范、越界或超前水位；公开 data 只有 Task/Attempt/Action ID、事件版本/类型/序号、Task revision/status、结构化进度、UTC 时间和安全消息，不暴露来源 message/idempotency/fingerprint。响应固定 `no-store, no-transform` 与禁代理缓冲，空闲时发送 comment keepalive；终态且追平 watermark 后关闭，非终态连接最多 55 秒主动轮换，使 App 用新短期 Session 续接。响应开始后的数据库失败只安全断流并由相同 Last-Event-ID 重连，不把底层异常写进帧或日志。

迁移 `20260718_0009` 新增 `task_commands` 持久 Outbox。wire message/correlation/response ID 必须是 UUIDv4；命令类型精确匹配 Executor v1 的 offer/pause/resume/cancel/emergency-stop，Attempt 内 sequence 唯一且不超过 `2^53-1`，Installation 内 idempotency key 和 response message 分别唯一。pending、in_flight、delivered、acknowledged、rejected、expired 六态与 next delivery、lease、delivery attempts、投递/确认时间、deadline、响应类型保持数据库一致：socket 投递不能冒充 Executor ACK，offer 只接受 accept/reject，控制命令只接受 control_ack。

`TaskCommandDeliveryService` 与 `SqlAlchemyTaskCommandRepository` 已把 Outbox 接入当前 Executor 连接。enqueue 先锁 active Installation 并对同 scope 幂等重放；每轮 WebSocket 重认证后先批量过期，再以 `FOR UPDATE SKIP LOCKED` 抢占 pending、过期 lease 或 ACK 超时的 delivered 命令。发送失败释放为延迟 pending；发送成功只进入 delivered；新连接会立即重投此前 delivered，Control Plane 崩溃留下的 in-flight 则在 lease 到期后恢复。回执同时匹配 Installation、Task、Attempt、correlation 和 sequence，且 accept/reject/control_ack payload 使用封闭布尔形状；首个合法回执持久化，后续同结论重复回执幂等，错配、迟到和响应 ID 冲突 fail closed。

`POST /api/v1/tasks/{task_id}/pause` 与 `/resume` 复用 `app.control-plane` Installation scope、空 JSON 和必填 `Idempotency-Key`。仓储先锁 Installation 与目标 Task/current Attempt，只有 running/running 可暂停、paused/paused 可恢复；随后在同一事务按 Attempt 分配下一安全 sequence 并写入 pending Outbox，不修改 Task/Attempt revision 或状态。首次写入返回公开命令的 202，同一 scope/key/意图重放返回 200，改意图、状态冲突、跨 scope 和序号耗尽 fail closed。

暂停/恢复还有独立确认门禁：socket delivered 与 `task.control_ack` 都只改变 Command。`task.paused`/`task.resumed` 收敛事务必须锁定该 Attempt 最新 pause/resume 命令，并核对类型、acknowledged、`task.control_ack`、correlation 和确认时间；未确认、旧控制命令或伪造 correlation 的事件不能提前改 Task/Attempt。唯一 `visible=false` Tauri/WKWebView 已经经正式 Rust API、真实 Uvicorn/PostgreSQL 与 HOLD FakeExecutor 跑通 offer→pause→resume，最终 Task 回到 running。

`POST /api/v1/tasks/{task_id}/cancel` 与 `/emergency-stop` 复用同一受认证控制边界和 Outbox。首次合法请求在一个事务内写入 pending Command，并把 Task/Attempt 各以 revision CAS 前进一次到 `cancelling`；同键同意图重放只返回原 Command，改意图、再次终止、终态、错 scope 和状态不一致均拒绝。`task.cancelled` 以及 cancelling 下的 `task.outcome_uncertain` 必须匹配最新 cancel/emergency-stop 的已确认 ACK/correlation；完成、部分完成或失败事实若与取消并发，则仍可从 cancelling 收敛为真实终态。HOLD FakeExecutor 对正常取消回报 cancelled，对硬紧停保守回报 outcome uncertain。

当前 offer payload 仍保持空的安全骨架；T3-17 已建立受约束 Task 定义事实，但尚未发布 Executor 业务 payload 版本，后续只能从这些明确列构造，不能退回任意 JSON。FakeExecutor 继续按相同正式 envelope 做无副作用回放；T3-09 不因 ACK 提前修改 Task/Attempt，正式状态只通过上述 T3-11 持久事件事实收敛。

`automation_tool.executor.fake` 是不依赖 Control Plane 内部实现的确定性协议引擎：严格复用正式 parser、身份、deadline、Attempt command sequence 和 task/attempt 绑定，按 message ID 与 idempotency key 双账本去重。它覆盖 accept/reject、成功、部分成功、失败、登录、人工接管、结果不确定和 hold 场景，并为 pause/resume/cancel/emergency-stop 生成正式 control ACK 与单调事件；生成中失败会原子回滚状态和事件水位。`fake_client` 只通过 `ws(s)://.../api/v1/executors/connect`、唯一正式子协议和 Bearer Session 出站连接，不执行 RPA、文件、子进程或数据库副作用。

E4-02 增加正式控制台入口 `automation-tool-executor`。`executor/bootstrap.py` 只从 stdin 读取一条换行结尾、最多 16 KiB 的严格 JSON object，字段固定为 bootstrap 版本、Executor WebSocket URL、短期 `executor.connect` Session、Installation/Executor UUIDv4 和 `1..60` 秒心跳间隔；重复 key、未知字段、非法类型与超限统一拒绝。明文 `ws` 只允许带有效端口的 `127.0.0.1` 固定路径，远端只允许标准端口 `wss`，userinfo/query/fragment 全部拒绝。

`executor/runtime.py` 从安装产物自身检测 macOS/Windows 与 arm64/x86_64，不相信 stdin 自报运行平台；它使用共享 `executor/transport.py` 的唯一子协议、Bearer Header、禁代理/压缩和 32 KiB 上限连接 Control Plane，发送正式 Hello，再以严格递增 sequence 发送 Heartbeat。第一条心跳存活后 stdout 只输出固定 `executor.healthy`，正常 SIGINT/SIGTERM 后输出固定 `executor.stopped`；Session、ID、URL、原始异常和服务端帧都不进入该投影或 stderr。E4-02 不处理任何 Task Command，收到应用帧或连接异常立即以固定错误退出；有界重启、命令账本和真实消息回放分别由 E4-08、E4-11、E4-12 实现。

`installations` 表保存 UUIDv4 主键、唯一 32 字节 Ed25519 公钥、`active`/`revoked` 状态、正数 revision、创建/更新时间和吊销时间。数据库约束拒绝状态与吊销时间矛盾、倒序时间、非法 UUID 版本、重复公钥和非 32 字节公钥；revision 更新必须在语句中携带旧值作为 CAS 条件。

`DemoBootstrapGrant` 的 claims 由离线 Ed25519 私钥签名为 `atb1` 凭据；Control Plane 只配置 32 字节公钥并验证签名，不持有签发私钥。claims 有效期最多 7 天，绑定一个规范 `DemoEnvironmentId`，唯一 `BootstrapPurpose` 为 `installation.register`。服务端签发最长 5 分钟且不超过 bootstrap 到期时间的一次性 challenge，绑定环境、bootstrap SHA-256 指纹和设备公钥；App 用设备 Ed25519 私钥签名后，PostgreSQL 在同一事务中锁定 challenge、验证证明、创建 Installation、签发初始设备凭据并标记消费。批次次数、撤销和审计仍由 C10-06 实现，不在本 API 中伪造。

长期设备凭据格式为 `atdc1.<credential-id>.<256-bit-secret>`，只在初始签发或轮换成功时返回一次。`device_credentials` 表只保存 SHA-256 摘要、正数版本、精确 `device.session.exchange` scope 和 `active`/`rotated`/`revoked` 历史；每个 Installation 由部分唯一索引限制为一个 active 版本。轮换和吊销先锁 Installation、再锁凭据并常量时间核对摘要；旧版本、错误秘密、未知凭据和已吊销 Installation 统一返回不回显输入的认证失败。

当前长期凭据可调用 `POST /api/v1/device-sessions` 换取 `atds1.<session-id>.<256-bit-secret>`，响应禁止缓存。Session 固定 5 分钟寿命并允许客户端时钟最多落后 30 秒，只能精确选择 `app.control-plane` 或 `executor.connect` 一项能力。`device_sessions` 表只保存摘要及 Installation、父凭据 ID、父凭据版本的复合绑定；认证使用 `[not_before, expires_at)` 半开边界，父凭据轮换/吊销或 Installation 撤销后既有 Session 立即失效。

服务器运维侧使用 `automation-tool-revoke-installation --installation-id ... --expected-revision ...` 原子吊销一个 Installation；命令在单事务中更新 Installation revision、active 长期凭据和全部 Session，未知/重复/stale revision/并发失败不会回显目标。App 业务路由统一依赖 `require_current_installation_access` 校验 `app.control-plane` Session 并取得强类型 Installation scope；`GET /api/v1/installations/current` 与 Task 创建、列表、详情都使用该依赖，后续任务路由不得相信客户端自报 scope 或复制一套认证。

`WS /api/v1/executors/connect` 只接受唯一 `automation-tool.executor.v1` 子协议和 `executor.connect` Session；认证后立即从长连接 scope 擦除原始 Authorization Header。升级后第一帧必须是正式 Python parser 验证的 `executor.hello`，并把连接绑定到 Installation、Executor、协议版本、Executor 版本、平台、架构、Hello sequence 和独立 `ExecutorConnectionId`；随后只接受同一身份的 heartbeat、任务命令回执或 TaskEvent。heartbeat sequence 严格递增，回执交给持久 Outbox 核对，事件交给原子收敛服务；协议/状态冲突固定 4406，持久化不可用固定 1011。连接每秒重新读取 PostgreSQL 认证状态并轮询 due 命令，Session、父凭据或 Installation 失效会以固定 4401 关闭。Uvicorn 固定使用 `websockets-sansio`，并在传输层把消息限制为 32 KiB。

`ExecutorConnectionRegistry` 是当前单 Control Plane 进程内唯一在线事实源，以 Installation 为单活键并公开不含 channel/凭据的只读投影：连接/Executor ID、版本/平台/架构、服务端连接与最后心跳时间、最后 sequence。新 Hello 先原子成为 current，再以固定 4409 关闭旧连接；旧连接迟到 heartbeat 和 unregister 都不能影响新连接。`send_current` 必须同时命中 Installation 与预期 Connection ID，限制 UTF-8 wire 为 1..32 KiB，并在 socket 写入后再次检查 current；写入竞态、连接替换和传输失败由持久投递服务做重投判断。应用 lifespan 以 1012 关闭并清空全部连接。Registry 不持久化认证或任务事实，MVP/Demo 因而必须保持单 Control Plane 实例，多副本前先建设跨副本连接路由。

`GET /api/v1/workbench/status` 复用 `require_current_installation_access`，从 Registry 只投影 `ready`、Executor `online/offline` 与服务端最后心跳时间，统一 `no-store`，不返回 Installation、Executor、Connection ID 或底层异常。该状态是工作台只读在线事实，任务状态仍只来自 PostgreSQL 快照与事件。

真实网络基础认证验收在 `backend/` 执行 `uv run python ../scripts/run_i2_13_acceptance.py`；持久命令验收执行 `uv run python ../scripts/run_t3_09_acceptance.py`；FakeExecutor 正式路径验收执行 `uv run python ../scripts/run_t3_10_acceptance.py`；事件闭环执行 `uv run python ../scripts/run_t3_11_acceptance.py`；SSE App 入口执行 `uv run python ../scripts/run_t3_12_acceptance.py`；暂停/恢复入口执行 `uv run python ../scripts/run_t3_13_acceptance.py`；取消/紧停入口执行 `uv run python ../scripts/run_t3_14_acceptance.py`；Query/Reducer/Tauri Channel 入口执行 `uv run python ../scripts/run_t3_15_acceptance.py`；工作台真实页面入口执行 `uv run python ../scripts/run_t3_16_acceptance.py`；新建任务表单、运行详情、完整生命周期与 Control Plane 重启入口分别在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_17_acceptance.py`、`backend/.venv/bin/python scripts/run_t3_18_acceptance.py`、`backend/.venv/bin/python scripts/run_t3_19_acceptance.py` 和 `backend/.venv/bin/python scripts/run_t3_20_acceptance.py`。E4-02 的正式进程入口验收执行 `uv run pytest tests/integration/test_local_executor_process.py`：测试经安装后的 `automation-tool-executor`、stdin、真实 Uvicorn、正式 Session 认证和 Registry 验证 Hello/Heartbeat、固定健康输出与 SIGTERM 清理。T3-20 使用隔离 PostgreSQL、完整 Alembic 和唯一隐藏 App 验证同库重启；所有验收结束后回收对应 App、进程、端口、容器、网络和卷。

真实测试版 Tauri App 已通过正式 Rust 网络桥消费 Health、Installation 注册/访问、设备凭据轮换/吊销、Session 换票和 Task 创建/查询/事件端点。Rust 从 App 私有目录加载设备私钥和长期凭据，执行签名、凭据注入、任务定义复验与 SSE 严格解析，React 不接触任何秘密；T3-17 已由唯一 `visible=false` App 真实点击新建表单，经固定 Tauri Command、Uvicorn 和 PostgreSQL 原子创建匹配定义。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run automation-tool-export-openapi --output ../contracts/openapi/control-plane.v1.json --check
uv run automation-tool-export-executor-schema --output ../contracts/protocol/executor-v1.schema.json --check
uv run pyinstaller --noconfirm --clean automation-tool-executor.spec
```

PyInstaller 产物固定为 `dist/automation-tool-executor/` 的 `onedir` 目录；macOS 入口为同名可执行文件，Windows 入口为 `automation-tool-executor.exe`。`pyinstaller` 只锁在开发依赖组，当前包不引入 Playwright；双平台 CI 已配置从冻结产物启动入口并验证 bootstrap/连接失败的固定退出契约。当前 macOS 实包已通过，Windows Hosted Runner 因 GitHub 账户 Billing/Actions spending limit 在分配 runner 前被拒绝，仍需在可用 Windows runner 或实体机补验收。

`automation-tool-executor` 只由 Tauri/PyInstaller 入口通过 stdin 启动；不要把 bootstrap JSON、Session 或其他秘密放入命令行、环境变量、shell 历史或普通配置。构建目录和产物均被 Git 忽略，后续由 E4-04/E4-05 增加 Manifest、签名和 Rust 侧完整目录验证。

后端 Pydantic/FastAPI 是跨端 DTO 的唯一来源。路由契约变化后先去掉 `--check` 重新导出快照，再到 `frontend/` 执行 `pnpm generate:api`；提交前两侧都必须通过各自漂移检查。

先在仓库根目录启动开发数据库：

```bash
cp .env.example .env
openssl rand -hex 32
# 把随机值分别填入两个数据库密码，并把开发库密码同步到 DATABASE_URL
docker compose up --detach --wait postgres-dev
```

再在 `backend/` 目录升级数据库并启动本地 Control Plane：

```bash
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env automation-tool-control-plane
```

注册 API 默认关闭；Demo/本地注册联调必须同时提供下列公开部署配置，缺一或格式非法时启动 fail closed。公钥使用无 padding 的 canonical base64url，离线签发私钥不得进入服务器环境、仓库或日志：

```bash
AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID=demo-cn-1
AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY=<32-byte-ed25519-public-key-base64url>
```

服务只绑定 `127.0.0.1:8765`。启动时配置缺失会 fail closed；数据库断开时 Health 返回可重试的结构化 `503 dependency_unavailable`。当前端点为：

- `GET http://127.0.0.1:8765/api/v1/health`
- `GET http://127.0.0.1:8765/api/v1/version`
- `POST http://127.0.0.1:8765/api/v1/installations/registration-challenges`
- `POST http://127.0.0.1:8765/api/v1/installations`
- `GET http://127.0.0.1:8765/api/v1/installations/current`
- `POST http://127.0.0.1:8765/api/v1/device-credentials/rotations`
- `POST http://127.0.0.1:8765/api/v1/device-credentials/revocations`
- `POST http://127.0.0.1:8765/api/v1/device-sessions`
- `POST http://127.0.0.1:8765/api/v1/tasks`
- `GET http://127.0.0.1:8765/api/v1/workbench/status`
- `GET http://127.0.0.1:8765/api/v1/tasks`
- `GET http://127.0.0.1:8765/api/v1/tasks/{task_id}`
- `WS ws://127.0.0.1:8765/api/v1/executors/connect`

迁移回滚验证（只对明确的测试数据库执行）：

```bash
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic downgrade base
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic upgrade head
```

两套数据库必须使用两次独立生成的随机密码。开发库持久化到命名卷；测试库使用 tmpfs，不与开发库共享用户、密码、数据库或数据目录。停止服务使用 `docker compose down`，明确需要删除本地开发数据时才额外使用 `--volumes`。自动化测试会启动随机端口上的隔离测试库并在结束后删除其容器、网络和卷。

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
