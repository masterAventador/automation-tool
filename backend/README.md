# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线、Control Plane 应用工厂、lifespan、统一错误处理、Health/Version API、SQLAlchemy asyncpg/Alembic 数据库基线、六类不可混用的稳定资源 ID、Installation 持久化表、无账号 Installation 注册 API、版本化设备凭据生命周期、短期设备 Session 交换、Executor v1 Envelope、受认证 Executor WebSocket、单活连接 Registry、工作台运行状态、持久命令投递/重连/ACK、Task 事件原子收敛与 SSE 续拉、无副作用 FakeExecutor、正式 Local Executor 最小进程、纯领域任务状态机、Task/Attempt/Action/Event/Command 持久化模型，以及 Task 幂等创建、隔离查询、暂停/恢复和取消/紧停 API。React 工作台已消费 Control Plane 权威事实；正式 Executor 当前完成 bootstrap、Hello/Heartbeat、信号退出和抖音 Session 最小健康上报，尚不执行业务任务或平台副作用。

`automation_tool.protocol.executor_envelope` 是 Control Plane 与 Local Executor 唯一共享的 v1 wire envelope。正式输入必须使用 `parse_executor_message` 解析：只接受最大 32 KiB 的 UTF-8 JSON object，拒绝重复 key、未知 envelope 字段、非 `1.0` 版本、未知 message type、非 canonical UUIDv4、非 UTC 时间、倒序 deadline、非法幂等键和超出 JavaScript 安全整数范围的序号。生命周期消息没有伪造的 task ID；任务命令、回执和事件必须同时绑定 task/attempt。Payload 最大 16 KiB、深度 8、单集合 64 项、单字符串 4096 字符，并拒绝 Cookie/Token/密钥字段、私有路径、inline data URI、非有限数字和双向控制字符；所有解析失败只返回不挂底层异常链的固定错误。

权威 Schema 固定为 `contracts/protocol/executor-v1.schema.json`，只能由 Pydantic 源通过 `automation-tool-export-executor-schema` 生成。`contracts/fixtures/executor-v1/` 包含 10 个 valid 和 27 个 invalid wire 样例；标准 Draft 2020-12 validator 负责 17 个结构层失败，Python、Rust、TypeScript 正式解析器另外实现 Schema `x-semantic-validation-required` 声明的 10 个 deadline、重复 key、敏感信息、私有路径、inline data、非有限数字和递归资源失败。三端必须回放同一目录，不能复制另一套 fixture。

资源 ID 统一使用规范小写 UUIDv4，并通过 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 值对象隔离；一次在线连接另使用短生命周期的 `ExecutorConnectionId`。外部字符串必须先调用对应类型的 `parse`，新资源调用 `new`；不能把普通字符串、另一类资源 ID 或非 UUIDv4 值直接带入领域层。

`control_plane.domain.task_state_machine` 定义 16 个 `TaskStatus`、5 个无出边终态和唯一显式转换矩阵。取消必须先进入 `cancelling`；取消/完成竞态从 `cancelling` 按真实事实收敛；`outcome_uncertain` 只可从已执行、人工接管或取消中的状态进入。所有 256 个状态对均由单元测试分类，字符串输入、自循环、终态复活和未列出的跳转固定拒绝，后续应用服务不得另建状态分支。

`tasks` 表已通过迁移 `20260718_0006` 建立 Task UUIDv4、Installation 外键、状态、正 revision 和有序时间，并保留 `(id, installation_id)` 复合绑定及 Installation 更新时间索引。迁移 `20260718_0010` 增加受协议字符集/128 字节上限约束的 `creation_idempotency_key`，并以 `(installation_id, creation_idempotency_key)` 唯一；旧 Task 确定性回填 `legacy:<task-id>`。迁移 `20260718_0013` 增加与 `(task_id, installation_id)` 强绑定的 `douyin_search_exposure_definitions`，以明确列保存版本、关键词、动作、消息模板、目标上限、间隔和强制确认开关，不保存任意 JSON。`SqlAlchemyTaskRepository` 先锁 Installation，再原子写 Task 与定义；同 scope/key 只有定义完全一致才重放，改参数会拒绝。读取/转换始终携带 Installation scope，状态转换复用领域状态机并以 expected revision + 行锁做 CAS。

`POST /api/v1/tasks` 只接受 `douyin.search_exposure.v1` 的封闭 DTO 和必填 `Idempotency-Key`，并强制复用 `require_current_installation_access` 的精确 `app.control-plane` Session scope。DTO 明确校验安全关键词、browse/comment/direct_message、A7-05 固定/单变量文案与动作关系、`1..100` 目标上限、`1..3600` 有序间隔，以及固定开启的预览和最终确认。第一次创建返回 201；同 Installation/key/定义重放返回相同公开快照和 200；响应不回显定义、幂等键、Session 或凭据。未知字段、敏感文本、改意图重放和持久化冲突均进入稳定 `no-store` 错误边界。

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

H8-01 在正式 `ExecutorCommandProcessor` 和 SQLite v5 账本上补齐本机安全检查点，不新增 schema。pause/resume 只能附着到已有 Attempt，pause 仅接受 running、resume 仅接受 paused；控制命令与 ACK 先持久化。pause 成为最新命令后，`begin_side_effect_dispatch()` 在同一个 `BEGIN IMMEDIATE` 边界拒绝任何新的 prepared→dispatched；已有 dispatched 事实不会被伪装撤销，只有全部结算后 `pending_task_controls()` 才可见该 pause。`complete_task_control()` 再原子核对 ACK、身份、correlation、command/event sequence、checkpoint revision/state，把 checkpoint 与 `task.paused/task.resumed` Outbox 一次提交；进程运行循环会在恢复 Outbox 后和每轮空闲时继续推进，因此无需服务端重复下发命令。

H8-02 复用上述 command/checkpoint/outbox/side-effect 表实现普通协作式取消，不新增 schema 或第二状态机。`task.cancel` 只接受 running/paused Attempt；命令一经持久化，新的 prepared→dispatched 就在同一 SQLite 锁边界被拒绝。无在途 dispatched 时，checkpoint 与 `task.cancelled` 原子提交为 terminal；已有动作必须先由原执行/恢复边界结算，verified 后才 cancelled，uncertain 后只能提交 `task.outcome_uncertain` 与 outcome_uncertain checkpoint。ACK 只表示已接收，不能提前伪造终态；终态事件类型在提交事务内按最新副作用事实重新核对，重放返回首次持久事实。H8-02 阶段对 `task.emergency_stop` 明确拒绝；H8-03 现已通过独立本机持久 latch、进程树硬停和报告型恢复链实现该命令，不改变普通 cancel 语义。

H8-03 的 `task.emergency_stop` 在 App 网络调用前先持久化最小本机意图并硬停完整 Executor/浏览器进程树；Executor SQLite 以单个 `BEGIN IMMEDIATE` 封锁新 dispatch、把已派发未确认动作收敛为 uncertain，并把 ACK、`task.outcome_uncertain` 与 checkpoint/outbox 原子落账。App 网络恢复后从原工作台/详情轮询认领同一 marker，向 Control Plane 幂等补发并启动只报告既有账本事实的签名 Executor，服务端与本机最终精确收敛。

H8-04 不在 Backend 增加 App 恢复 API 或第二份任务状态。第一个隐藏 App 被硬杀后，原签名 Executor 继续通过既有认证 WebSocket/heartbeat 在线，PostgreSQL 保持 Task/Attempt 权威 running；第二个 App 只凭原 AppData 身份调用既有工作台、Task Query 和事件路径恢复 UI。严格验收逐字段锁定 Task/Attempt/Command/Event 与本机副作用账本未变化，证明 App 重启不会重建任务、控制命令或平台动作。

`POST /api/v1/tasks/{task_id}/cancel` 与 `/emergency-stop` 复用同一受认证控制边界和 Outbox。首次合法请求在一个事务内写入 pending Command，并把 Task/Attempt 各以 revision CAS 前进一次到 `cancelling`；同键同意图重放只返回原 Command，改意图、再次终止、终态、错 scope 和状态不一致均拒绝。`task.cancelled` 以及 cancelling 下的 `task.outcome_uncertain` 必须匹配最新 cancel/emergency-stop 的已确认 ACK/correlation；完成、部分完成或失败事实若与取消并发，则仍可从 cancelling 收敛为真实终态。HOLD FakeExecutor 对正常取消回报 cancelled，对硬紧停保守回报 outcome uncertain。

当前 offer payload 仍保持空的安全骨架；T3-17 已建立受约束 Task 定义事实，但尚未发布 Executor 业务 payload 版本，后续只能从这些明确列构造，不能退回任意 JSON。FakeExecutor 继续按相同正式 envelope 做无副作用回放；T3-09 不因 ACK 提前修改 Task/Attempt，正式状态只通过上述 T3-11 持久事件事实收敛。

`automation_tool.executor.fake` 是不依赖 Control Plane 内部实现的确定性协议引擎：严格复用正式 parser、身份、deadline、Attempt command sequence 和 task/attempt 绑定，按 message ID 与 idempotency key 双账本去重。它覆盖 accept/reject、成功、部分成功、失败、登录、人工接管、结果不确定和 hold 场景，并为 pause/resume/cancel/emergency-stop 生成正式 control ACK 与单调事件；生成中失败会原子回滚状态和事件水位。`fake_client` 只通过 `ws(s)://.../api/v1/executors/connect`、唯一正式子协议和 Bearer Session 出站连接，不执行 RPA、文件、子进程或数据库副作用。

E4-02 增加正式控制台入口 `automation-tool-executor`。`executor/bootstrap.py` 只从 stdin 读取一条换行结尾、最多 16 KiB 的严格 JSON object，字段固定为 bootstrap 版本、Executor WebSocket URL、短期 `executor.connect` Session、Installation/Executor UUIDv4、`1..60` 秒心跳间隔和由 Rust 提供的 App 私有 Executor 状态目录；重复 key、未知字段、非法类型、相对/根/含 `..` 或控制字符的路径与超限统一拒绝。明文 `ws` 只允许带有效端口的 `127.0.0.1` 固定路径，远端只允许标准端口 `wss`，userinfo/query/fragment 全部拒绝。

`executor/runtime.py` 从安装产物自身检测 macOS/Windows 与 arm64/x86_64，不相信 stdin 自报运行平台；它使用共享 `executor/transport.py` 的唯一子协议、Bearer Header、禁代理/压缩和 32 KiB 上限连接 Control Plane，发送正式 Hello，再以严格递增 sequence 发送 Heartbeat。第一条心跳存活后 stdout 只输出固定 `executor.healthy`，正常 SIGINT/SIGTERM 后输出固定 `executor.stopped`；Session、ID、URL、原始异常和服务端帧都不进入该投影或 stderr。E4-12 的 `command_processor.py` 已接入正式 `task.offer`：先写 E4-11 SQLite，再原子提交固定无副作用 ACK/Event outbox；重启只重放原消息，其他命令与非法帧固定拒绝。

`installations` 表保存 UUIDv4 主键、唯一 32 字节 Ed25519 公钥、`active`/`revoked` 状态、正数 revision、创建/更新时间和吊销时间。数据库约束拒绝状态与吊销时间矛盾、倒序时间、非法 UUID 版本、重复公钥和非 32 字节公钥；revision 更新必须在语句中携带旧值作为 CAS 条件。

`DemoBootstrapGrant` 的 claims 由离线 Ed25519 私钥签名为 `atb1` 凭据；Control Plane 只配置 32 字节公钥并验证签名，不持有签发私钥。claims 有效期最多 7 天，绑定一个规范 `DemoEnvironmentId`，唯一 `BootstrapPurpose` 为 `installation.register`。服务端签发最长 5 分钟且不超过 bootstrap 到期时间的一次性 challenge，绑定环境、bootstrap SHA-256 指纹和设备公钥；App 用设备 Ed25519 私钥签名后，PostgreSQL 在同一事务中锁定 challenge、验证证明、创建 Installation、签发初始设备凭据并标记消费。批次次数、撤销和审计仍由 C10-06 实现，不在本 API 中伪造。

长期设备凭据格式为 `atdc1.<credential-id>.<256-bit-secret>`，只在初始签发或轮换成功时返回一次。`device_credentials` 表只保存 SHA-256 摘要、正数版本、精确 `device.session.exchange` scope 和 `active`/`rotated`/`revoked` 历史；每个 Installation 由部分唯一索引限制为一个 active 版本。轮换和吊销先锁 Installation、再锁凭据并常量时间核对摘要；旧版本、错误秘密、未知凭据和已吊销 Installation 统一返回不回显输入的认证失败。

当前长期凭据可调用 `POST /api/v1/device-sessions` 换取 `atds1.<session-id>.<256-bit-secret>`，响应禁止缓存。Session 固定 5 分钟寿命并允许客户端时钟最多落后 30 秒，只能精确选择 `app.control-plane` 或 `executor.connect` 一项能力。`device_sessions` 表只保存摘要及 Installation、父凭据 ID、父凭据版本的复合绑定；认证使用 `[not_before, expires_at)` 半开边界，父凭据轮换/吊销或 Installation 撤销后既有 Session 立即失效。

服务器运维侧使用 `automation-tool-revoke-installation --installation-id ... --expected-revision ...` 原子吊销一个 Installation；命令在单事务中更新 Installation revision、active 长期凭据和全部 Session，未知/重复/stale revision/并发失败不会回显目标。App 业务路由统一依赖 `require_current_installation_access` 校验 `app.control-plane` Session 并取得强类型 Installation scope；`GET /api/v1/installations/current` 与 Task 创建、列表、详情都使用该依赖，后续任务路由不得相信客户端自报 scope 或复制一套认证。

`WS /api/v1/executors/connect` 只接受唯一 `automation-tool.executor.v1` 子协议和 `executor.connect` Session；认证后立即从长连接 scope 擦除原始 Authorization Header。升级后第一帧必须是正式 Python parser 验证的 `executor.hello`，并把连接绑定到 Installation、Executor、协议版本、Executor 版本、平台、架构、Hello sequence 和独立 `ExecutorConnectionId`；随后只接受同一身份的 heartbeat、任务命令回执或 TaskEvent。heartbeat sequence 严格递增，回执交给持久 Outbox 核对，事件交给原子收敛服务；协议/状态冲突固定 4406，持久化不可用固定 1011。连接每秒重新读取 PostgreSQL 认证状态并轮询 due 命令，Session、父凭据或 Installation 失效会以固定 4401 关闭。Uvicorn 固定使用 `websockets-sansio`，并在传输层把消息限制为 32 KiB。

`ExecutorConnectionRegistry` 是当前单 Control Plane 进程内唯一在线事实源，以 Installation 为单活键并公开不含 channel/凭据的只读投影：连接/Executor ID、版本/平台/架构、服务端连接与最后心跳时间、最后 sequence。新 Hello 先原子成为 current，再以固定 4409 关闭旧连接；旧连接迟到 heartbeat 和 unregister 都不能影响新连接。`send_current` 必须同时命中 Installation 与预期 Connection ID，限制 UTF-8 wire 为 1..32 KiB，并在 socket 写入后再次检查 current；写入竞态、连接替换和传输失败由持久投递服务做重投判断。应用 lifespan 以 1012 关闭并清空全部连接。Registry 不持久化认证或任务事实，MVP/Demo 因而必须保持单 Control Plane 实例，多副本前先建设跨副本连接路由。

`GET /api/v1/workbench/status` 复用 `require_current_installation_access`，从 Registry 只投影 `ready`、Executor `online/offline` 与服务端最后心跳时间，统一 `no-store`，不返回 Installation、Executor、Connection ID 或底层异常。该状态是工作台只读在线事实，任务状态仍只来自 PostgreSQL 快照与事件。

真实网络基础认证验收在 `backend/` 执行 `uv run python ../scripts/run_i2_13_acceptance.py`；持久命令验收执行 `uv run python ../scripts/run_t3_09_acceptance.py`；FakeExecutor 正式路径验收执行 `uv run python ../scripts/run_t3_10_acceptance.py`；事件闭环执行 `uv run python ../scripts/run_t3_11_acceptance.py`；SSE App 入口执行 `uv run python ../scripts/run_t3_12_acceptance.py`；暂停/恢复入口执行 `uv run python ../scripts/run_t3_13_acceptance.py`；取消/紧停入口执行 `uv run python ../scripts/run_t3_14_acceptance.py`；Query/Reducer/Tauri Channel 入口执行 `uv run python ../scripts/run_t3_15_acceptance.py`；工作台真实页面入口执行 `uv run python ../scripts/run_t3_16_acceptance.py`；新建任务表单、运行详情、完整生命周期与 Control Plane 重启入口分别在仓库根目录执行 `backend/.venv/bin/python scripts/run_t3_17_acceptance.py`、`backend/.venv/bin/python scripts/run_t3_18_acceptance.py`、`backend/.venv/bin/python scripts/run_t3_19_acceptance.py` 和 `backend/.venv/bin/python scripts/run_t3_20_acceptance.py`；D6-10 目标发现闭环执行 `backend/.venv/bin/python scripts/run_d6_10_acceptance.py`，D6-11 目标预览 API 闭环执行 `backend/.venv/bin/python scripts/run_d6_11_acceptance.py`，D6-12 用户页面闭环执行 `backend/.venv/bin/python scripts/run_d6_12_acceptance.py`，三者都由各自唯一隐藏 Tauri App 经正式 Rust 命令和真实 Uvicorn/PostgreSQL 验收。E4-02/E4-11 的正式进程入口验收执行 `uv run pytest tests/integration/test_local_executor_process.py`：测试经安装后的 `automation-tool-executor`、stdin、真实 Uvicorn、正式 Session 认证和 Registry 验证 Hello/Heartbeat、固定健康输出、SQLite identity/秘密不落库与 SIGTERM 清理；仓库根的 `uv run --project backend python scripts/run_e4_07_acceptance.py` 另验证 signed PyInstaller→公开 Rust Manager→真实 Uvicorn→同一 SQLite。B5-12 的最小 Session 投影验收执行 `uv run --project backend python scripts/run_b5_12_acceptance.py`，经后台系统 Chrome、生产 detector/reporter、正式认证 WebSocket、真实 Uvicorn/Alembic/PostgreSQL 核对六列 projection。所有验收都使用项目专属隔离 PostgreSQL、唯一端口/网络/卷；结束后回收对应 App、浏览器、进程、端口、容器、网络和卷。

H8-01 安全暂停原入口在仓库根执行 `backend/.venv/bin/python scripts/run_h8_01_acceptance.py`：唯一隐藏 App 经正式网络控制 API 驱动真实 Executor，验证已有 dispatched 先结算、暂停命令落账后零新增 dispatch、runtime 自动 PAUSED 与 App 恢复 RUNNING；FakeExecutor 只负责建立初始服务端 running 事实，不冒充本机安全检查点。

H8-02 协作式取消原入口执行 `backend/.venv/bin/python scripts/run_h8_02_acceptance.py`：唯一 `visible=false` task-termination App 经正式 Rust 控制调用、Uvicorn/PostgreSQL 和认证 WebSocket 发出普通取消；真实 Executor 先 ACK 并阻止新 dispatch，既有 dispatched 被标记无法确认后自动上报 `task.outcome_uncertain`。同一 App 的紧停半程仍由既有 HOLD FakeExecutor 夹具完成，只用于跑完原页面流程，不冒充 H8-03 离线硬停止。

H8-03 离线紧停原入口执行 `backend/.venv/bin/python scripts/run_h8_03_acceptance.py`；H8-04 App 崩溃恢复原入口执行 `backend/.venv/bin/python scripts/run_h8_04_acceptance.py`。两者均使用唯一隐藏 Tauri App 配置、签名 PyInstaller Executor、真实 Uvicorn/PostgreSQL/认证 WebSocket、项目专属 Compose/AppData/SQLite，并在成功或失败后回收各自拥有的进程、端口、容器、网络、卷和私有目录；H8-04 会连续启动两个 App 进程，但第二个 App 不调用任何准备、创建、控制或 Executor restart Command。

D6-13 未确认副作用守卫在仓库根目录执行 `backend/.venv/bin/python scripts/run_d6_13_acceptance.py`。该 runner 通过正式 Uvicorn/Executor WebSocket 验证无绑定与旧确认的业务 offer 零投递、当前确认 offer 正常投递；它不调用 App API、不启动 Tauri 或运营浏览器。D6-11/D6-12 已独立证明确认事实来自真实 App 原入口，本任务只验收后端到 Executor 的原始生产边界。

D6-14 页面漂移同路径验收执行 `uv run pytest tests/integration/test_page_drift_artifact_browser.py`。用例从正式 `ExecutorCommandProcessor.handle(task.discover)` 进入生产发现编排，以隔离临时 Profile 和无头系统 Chrome 命中确定性锚点冲突页，核对 `handoff_required`、受限本机诊断内容与 BrowserRuntime 关闭；它没有 App API，因此不启动 Tauri，也不触碰用户默认 Profile 或真实账号。

D6-15 Fake 页面回归执行 `uv run pytest tests/integration/test_douyin_discovery_fake_pages.py`。七个小型静态 HTML 文件覆盖正常、可见空结果、阻塞弹窗、登录跳转、未知版本和无限滚动，全部经正式 command processor 与生产发现编排在隔离 Profile 的无头系统 Chrome 中回放；语料没有外部 URL、fetch、Cookie 或 storage 依赖，不进入 PyInstaller/正式包。D6-04 搜索、D6-05 滚动与 D6-07 候选隐私浏览器测试也读取同一语料目录。

D6-16 真实只读探针在仓库根执行 `AUTOMATION_TOOL_D616_PROFILE_DIRECTORY=<App 私有 Profile> backend/.venv/bin/python scripts/run_d6_16_browser_acceptance.py`。路径只从当前进程环境读取，脚本不输出路径、账号、候选名、Cookie、storage 或页面正文；先用生产 Session detector 有界确认健康，再从正式 `task.discover` command 进入生产发现编排，浏览器固定 `headless=true`。当前真实证据为受保护页 `healthy`、首页验证码风控并收敛 `handoff_required/blocking_dialog`，退出码 3 表示尚未完成候选验收；不得把该结果标成 D6-16 完成。

A7-01 的 `control_plane/domain/action_risk_policy.py` 是服务端风险硬限制的唯一纯领域入口。`ActionRiskScope` 只接受强类型 Installation、封闭 `douyin` 平台和既有 `browse/comment/direct_message` 动作；`ActionRiskPolicy` 必须显式传入整秒正最小间隔、任务动作上限、UTC 日上限和连续失败阈值，不提供运营默认值。`MAX_ACTION_RISK_LIMIT = 2^53-1` 只是 Python/Rust/TypeScript 无损序列化的结构上界，不代表安全日额度；真实额度需经受控账号校准后由后续装配提供。

A7-02 的 `SqlAlchemyActionRiskAuthorizationRepository` 是当前唯一服务端计数/授权持久边界。迁移 `20260720_0020` 增加 `action_risk_authorizations`，并用复合外键把每条事实同时绑定既有 `task_actions`、Target、Attempt、Task 和 Installation；授权事务以 Installation 行锁串行化，同事务复验运行态、任务动作、Session 健康/门闩、最新目标确认、可执行且未排除 Target，再计算任务、UTC 日和最小间隔硬限制。成功只写一条 `authorized` Action 和一条不可变策略/计数快照；拒绝、限流或唯一冲突整笔回滚。同一 ActionId 仅在目标、Attempt、Task、Installation 和动作完全相同时返回原事实；当前模块没有 HTTP、Executor wire 或 App 入口。

A7-03 的 `protocol/action_authorization.py` 是 Control Plane 与 Local Executor 共用的唯一短期动作授权契约。`Ed25519ActionAuthorizationIssuer` 只接受 A7-02 不可变事实、服务端 UTC 时钟、显式 `1 秒..5 分钟` 生命周期和独立 32 字节私钥；`Ed25519ActionAuthorizationVerifier` 只接受固定公钥，并把签名后的 Action/Target/Attempt/Task/Installation/Executor、`douyin` 动作、`action:<action_id>` 幂等键与授权/截止时间逐字段绑定到本次执行意图。token 使用域隔离、排序紧凑 JSON、六位 UTC 时间、无 padding Base64URL 和固定 `ataa1` 前缀；重复/额外字段、非 canonical 编码、错签名、跨 scope、超过 30 秒未来偏差或到期全部返回脱敏拒绝。服务端私钥属于部署秘密，不进入 App 沙盒、Executor、SQLite、协议或系统钥匙串。

A7-04 的 `executor/action_gate.py` 是后续真实动作进入 prepared 前的唯一 Executor 本机准入边界。构造时必须显式注入本机最小间隔、单任务动作上限、固定 Ed25519 公钥 Verifier、UTC 时钟和真实私有 SQLite；`admit()` 只接受签名 token 与 A7-03 完整 expectation，不接收服务端频控值。SQLite v4 的策略单例首次绑定后只能取更长间隔和更小任务上限，缺行、半损坏或越界都 fail closed；持久紧停 latch 只有本机 expected revision 与单调 UTC 时间同时匹配才可清除。准入事务先检查 latch，再做 exact replay、跨任务的 Installation/平台/动作最小间隔和 Task/平台/动作计数；五路并发只能有一个赢家。账本只保存最小 claims、准入时间、ordinal 和 token 的 SHA-256 指纹，不保存 token、Cookie、Profile、页面内容、账号或密钥；该内部边界没有新增 HTTP、OpenAPI、Tauri Command 或 React 接口。

A7-05 的 `protocol/action_message_template.py` 是 Control Plane 与后续 Local Executor 动作共用的唯一 Python 文案策略。`action-message-template.v1` 允许纯固定文案，并只开放 `{{target_display_name}}`；全字面为空、纯占位符、未知/畸形占位符、首尾空白、超过 500 Unicode code point、控制/Bidi、敏感赋值、inline data 或私有路径全部以固定错误拒绝。Alembic `20260720_0021` 用 PostgreSQL check constraint 拒绝纯变量及任何剩余花括号，直接写库也不能绕过封闭变量集。模块只校验并返回变量枚举，不渲染目标数据、不调用 LLM、不产生平台副作用。

A7-06 的 `task-target-confirmation-intent.v1` 把 Installation、Task、page revision、selection revision、动作、原始文案模板和按预览顺序排列的全部已选 Target ID 编成 canonical JSON 后计算 SHA-256。Alembic `20260720_0022` 为既有 confirmation 回填 action/message、版本和指纹，再强制非空与封闭约束；读取/重放会从当前定义和目标集合重算，篡改即拒绝。确认请求使用显式 `confirmationRevision`，必须等于当前 Task revision；A7-02 授权重算完整意图，D6-13 offer 入队/claim 复验动作与文案，旧 revision、定义变化或选择变化不能获得副作用执行资格。

A7-07 的 `executor/side_effect_ledger.py` 定义 `prepared → dispatched → verified|uncertain` 封闭值对象，`executor/ledger.py` 以 SQLite v5 提供正式持久入口。评论/私信先将精确效果 SHA-256 写为 prepared；`begin_side_effect_dispatch()` 在 `BEGIN IMMEDIATE` 内只给一个竞争者返回 `replayed=false`，调用者只有拿到该许可才可执行不可重复点击。平台最终核对将状态一次性结算为 verified 和验证 SHA-256，无法确认则结算为 uncertain；终态、摘要或资源绑定变化均拒绝，重启只列出未解决事实而不重新授权点击。该账本不保存正文、Token、Cookie、Profile、页面原文、账号或密钥，也没有 HTTP、OpenAPI、Tauri Command 或 React 接口。

A7-08 的 `executor/rpa/douyin/comment_page.py` 是唯一评论 DOM selector 所有者，不负责填写或点击。共享 `DouyinPageVersionModel` 新增 canonical `/video/<数字 ID>` 详情入口；Page Object 只在该入口暴露唯一可见的 comment input、submit 和 final confirmation locator，并提供最多 60 秒的有界 ready/final wait。登录/风控优先熔断，输入或提交缺一、同组出现多个元素、未知路由、驱动异常和中途漂移统一返回封闭状态或固定错误。生产 `BrowserRuntime` 已在无头系统 Chrome 中回放隔离官方-origin Fake 页面，证明 ready→confirmed、阻塞与漂移分支；没有访问真实账号或执行真实平台评论。

A7-09 的 `executor/rpa/douyin/direct_message_page.py` 是唯一主动私信 DOM selector 和权限差异所有者，也不负责输入或点击。共享 `DouyinPageVersionModel` 新增 canonical `/user/<目标 ID>` 用户主页入口；Page Object 只在该入口依次暴露唯一可见的进入会话、message input/send、final confirmation 或 permission notice locator，并提供最多 60 秒的 profile/conversation/final 有界等待。“暂时无法私信”和“关注后才能私信”保持不同 evidence，登录/风控优先熔断；半套/重复/冲突锚点、未知路由、驱动异常和中途漂移全部返回封闭状态或固定错误。生产 `BrowserRuntime` 已在无头系统 Chrome 中回放隔离官方-origin Fake 页面，证明 profile→conversation→confirmed、权限拒绝与漂移分支；没有访问真实账号或执行真实平台私信。

A7-10 的 `executor/rpa/douyin/profile_page.py` 只拥有通用用户主页、登录和阻塞锚点，不包含评论/私信控件；`browse.py` 只接受 D6-10 的最小 `DouyinCandidate`，经共享 `douyin_user_profile_url()` 构造 canonical 目标页并执行一次有界 `domcontentloaded` 导航。开始、导航后和最终成功前固定检查取消，ready 后还会通过 Page Object 二次取得主页根节点，避免最后一刻 DOM 漂移被误报成功。登录、风控、导航/主页超时、未知版本、重复锚点、取消探针异常和驱动失败都映射为封闭脱敏结果。生产 `BrowserRuntime` 已在无头系统 Chrome 中回放 ready/login/blocked/drift 四个官方-origin Fake 目标，页面内评论/私信陷阱按钮触发数为 0，且运行后关闭完整浏览器；本任务不新增 App、HTTP、Executor wire 或真实平台动作。

A7-11 的 `executor/rpa/douyin/comment_action.py` 是评论副作用的唯一单次执行编排。它先用 A7-04 完整验签与本机硬下限准入，再把 A7-05 固定/单目标显示名模板渲染成最终受限正文，并在任何页面动作前把只含摘要的精确意图写为 A7-07 `prepared`；Page Object ready 后填写正文，随后只有原子取得 `replayed=false` dispatch 许可的调用者能单击一次。最终确认锚点必须二次复验并写入域隔离验证摘要后才返回 `verified/comment_confirmed`；许可前失败保持 `not_dispatched/prepared`，许可后任何点击、确认、时钟或账本失败都返回 `outcome_uncertain`，并尽力持久化 `uncertain`。已存在的 dispatched/uncertain/verified 重放不访问 DOM、不填写、不点击。receipt 只投影 Action/Target、封闭状态/evidence、账本状态/revision 与重放位，不保存或表示正文、Token、Cookie、Profile、URL、页面内容或密钥；当前生产 `BrowserRuntime` 已在无头官方-origin 隔离页验证首次真实 locator 单击一次、重放零单击，真实抖音最终状态仍由 A7-16 补验。

A7-12 的 `executor/rpa/douyin/direct_message_action.py` 是主动私信副作用的唯一单次执行编排。完整私信授权先通过 A7-04 并写入 A7-07 `prepared`，随后可从 A7-09 的 `profile_ready` 单击一次会话入口，也可从 `conversation_ready` 恢复；入口点击只改变本地页面阶段，不取得发送资格。最终文案填入后重新取得 send locator，只有 `begin_side_effect_dispatch()` 的唯一赢家可单击一次发送。两类平台权限分别返回 `messaging_not_allowed/follow_required` 的阶段化 evidence；入口、权限、登录、风控或页面失败保持 prepared，send 许可后的任何未知结果只返回 uncertain，dispatched/uncertain/verified 重放不访问 DOM。receipt 和 SQLite 不保存私信正文、Token、Cookie、Profile、URL、页面内容或密钥；无头生产 BrowserRuntime 隔离验收证明首次进入/发送各一次、精确重放均为零新增，真实抖音最终状态仍由 A7-17 补验。

A7-13 的 `executor/rpa/douyin/side_effect_recovery.py` 只恢复 A7-07 中仍为 `dispatched` 的崩溃窗口，不放宽 uncertain 终态。它先按 Action ID 读取真实 SQLite；prepared、verified、uncertain 不访问 DOM，dispatched 才按持久 action 选择 A7-08/A7-09 Page Object 并只读等待、二次取得 final confirmation。确认后使用 A7-11/A7-12 导出的同一域隔离验证摘要结算 verified；未确认、权限变化或页面故障结算 uncertain，结算失败保留 dispatched。并发恢复只投影首次账本终态，源码没有 click/fill/press/navigation/selector/URL/HTTP。当前生产 BrowserRuntime 已在无头官方-origin 隔离页证明两类恢复 verified 且所有动作计数为 0；H8-05 再负责崩溃重启时装配调用，A7-15 再消费 receipt。

A7-14 的连续失败状态只存在于 Control Plane PostgreSQL。迁移 `20260721_0023` 增加不可变 `action_risk_results` 与当前 `action_failure_circuits`；每个结果必须通过复合外键匹配同一 ActionAuthorization 的 Installation/平台/动作，circuit 的最后结果和打开结果也必须属于同一 scope。正式 `step.completed/step.failed` 收敛事务在锁定 Installation 后串行更新动作终态、结果审计、连续失败和 Task event；成功只在 circuit 尚未打开时清零，失败达到该授权快照阈值时写 `task.awaiting_human` 并同步暂停当前 Attempt。授权仓储在精确 Action ID 重放之后、任何新授权之前检查 circuit，因此重放可读而新动作停止。

已打开 circuit 不接受晚到成功自动恢复，也不接受另一个 Task 的 resume 代为清除；只有打开它的 Task 经现有已 ACK `task.resumed` 控制链、单调服务端时间和同一数据库事务才能清零。真实 PostgreSQL 覆盖跨 Task 并发失败单一触发、成功清零、晚到成功、重复结果、时钟回退、计数溢出、错误恢复者、迁移升降级与约束故障；认证 `executor.connect` Session 和正式 `/api/v1/executors/connect` 路由已作为原调用方发送 `step.failed`，最终核对 circuit、审计结果、`task.awaiting_human` 和后续授权拒绝。该能力没有新增 App HTTP/Tauri/React 接口、浏览器或平台动作。

A7-15 新增共享 `action-result-evidence.v1` 封闭证据词汇和 `executor/rpa/douyin/action_result.py` 适配器，把评论、私信与崩溃恢复 receipt 映射到正式 `step.completed`、`step.failed` 或 `task.outcome_uncertain` payload。Task event 收敛层同时校验 evidence 版本、事件类型和成功/失败/不确定集合；迁移 `20260721_0024` 为 `task_actions` 增加最小 `evidence_code`、回填旧终态并以数据库约束拒绝跨 outcome 证据。持久事实不含评论/私信正文、页面原文、Cookie、Profile、URL、绝对路径或密钥。

`GET /api/v1/tasks/{task_id}/target-results` 是 App 唯一目标结果读取面。`TaskTargetResultService` 与 `SqlAlchemyTaskTargetResultRepository` 按当前 Installation、Task 和 current Attempt 把目标排除/策略处置以及 Action 状态投影成 pending/running/succeeded/skipped/failed/outcome_uncertain；非法或跨 scope Task 与不存在 Task 同样不可见，数据库或证据异常 fail closed。OpenAPI、Rust 固定 operation/Tauri Command 和现有 React 运行详情只消费公开摘要，不读取 Executor SQLite。`scripts/run_t3_18_acceptance.py` 通过唯一隐藏真实 Tauri App、正式 Session、Uvicorn、完整 Alembic/PostgreSQL 和 WebdriverIO 从原页面验证目标级结果与既有控制链；测试结束回收 AppData、端口、进程和 Compose 资源。

B5-13/B5-14 的隐藏 App 原入口执行 `uv run --project backend python scripts/run_b5_13_acceptance.py`：唯一 `visible=false` Tauri App 实际点击平台状态、打开处理、重新检查和确认安全注销，经正式 TypeScript/Rust、signed Executor、后台系统 Chrome、认证 WebSocket 和真实 Uvicorn/Alembic/PostgreSQL 核对 `missing` projection 与持久 logout gate，并从 App 原入口确认新任务被门闩拒绝。runner 使用专属随机端口、Compose project、AppData、SQLite 和 Profile；注销后只允许 current Profile 消失，Executor SQLite 必须保留，并在 App 正常退出后审计浏览器、Executor、容器和端口零残留。

B5-15 工程纵向验收执行 `backend/.venv/bin/python scripts/run_b5_15_acceptance.py`。它用唯一 AppData/Profile 连续运行 `first/restart/expired/risk` 四个隐藏 Tauri App 生命周期，每轮都从正式 TypeScript Gateway、Tauri IPC、Rust Manager、本机认证命令、signed Executor、无头系统 Chrome、WebSocket 到 PostgreSQL；前两轮必须保持同一 marker 和目录 identity 且直接健康，后两轮分别进入扫码和人工接管，最终 SQLite/PostgreSQL 固定收敛到 revision 2/risk。官方 origin 的确定性页面只打入 `backend/tests/fixtures/automation-tool-executor-b515.spec` 生成的独立验收包，不改变正式 Executor spec，也不冒充真实账号证据；真实账号纵向补验仍保持待办。

B5-16 默认 Profile 隔离验收执行 `backend/.venv/bin/python scripts/run_b5_16_acceptance.py`。唯一 `visible=false` App 从正式 React/IPC/Rust/Manager/Executor 入口启动无头系统 Chrome，扫码页面让 persistent context 保持活跃；runner 随后从 OS 进程表确认唯一根进程的 `--user-data-dir` 精确指向 AppData current Profile，递归检查完整后代树，再用 `lsof` 核对实际打开文件没有落入用户默认 Chrome/Edge User Data。源码契约同时递归拒绝默认 Profile 常量与 Cookie/storage-state API；runner 不打印 Profile 路径或 UUID，完成后只清理专属 AppData、进程、随机端口和 Compose 资源。

E4-10 的 `executor/diagnostics.py` 与 Rust Manager 共同回放根目录 `contracts/fixtures/executor-diagnostics-v1.json`，为 Python 后续结构化诊断提供同一 fail-closed 脱敏规则。它不改变正式 CLI 的固定 stderr，也不替代 Rust 对真实子进程 stderr 的独立流式限界和再次脱敏。

E4-11 的 `executor/ledger.py` 使用 Python 内置 `sqlite3`，不引入第二 ORM 或服务端数据库依赖。v1 建立 `executor_identity`、`executor_commands`、`executor_attempt_checkpoints` 和 `executor_outbox`；B5-12 迁移到 v2 增加 `executor_platform_sessions`，D6-10 的 v3 扩展只读发现重放，A7-04 的 v4 增加动作策略单例、紧停 latch 和最小准入事实，A7-07 的当前 v5 再增加最小副作用状态机。命令按 message/idempotency 双键与 SHA-256 意图指纹重放，Attempt 命令序号连续，checkpoint 用 revision/CAS 和单调事件序号更新，outbox 保存已通过正式协议模型的精确回执/事件并可持久标记 delivered；平台健康只保存平台、封闭状态、单调 revision 和观察时间。目录祖先 symlink/reparse point、宽权限、身份错绑、更新竞争、损坏/未来 schema 和文件替换均 fail closed。CLI 在联网前完成迁移；数据库只在 App 私有目录保存协议任务事实、非敏感平台健康、动作准入和副作用摘要最小事实，不保存 Control Plane Session、完整 ActionAuthorization token、Cookie、浏览器登录数据、密钥、正文、页面原文或任意配置，也不使用系统钥匙串。

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
uv run automation-tool-build-executor-manifest \
  --bundle-dir dist/automation-tool-executor \
  --executor-version 0.1.0 \
  --build-id local-build-1 \
  --platform macos \
  --architecture aarch64 \
  < /path/to/offline-32-byte-ed25519-seed
```

PyInstaller 产物固定为 `dist/automation-tool-executor/` 的 `onedir` 目录；macOS 入口为同名可执行文件，Windows 入口为 `automation-tool-executor.exe`。`pyinstaller` 只锁在开发依赖组；B5-07 起 Playwright 是 Executor 正式运行依赖，spec 用 `collect_all("playwright")` 收集 Python driver，但仓库和构建流程均不执行 `playwright install`，正式目录不包含 Playwright 浏览器缓存。双平台 CI 从冻结产物验证 bootstrap/连接失败契约及 driver/浏览器分离，另由测试专用冻结探针从生产 `browser_runtime.py` 启动系统 Chrome/Edge headed persistent context；探针不进入正式包、不上传或发布产物。macOS arm64 与 Windows x86_64 实包/系统浏览器原生验收均已通过；Hosted Windows Runner 仍受 GitHub 账户 Billing/Actions spending limit 限制，但不再是本机产品验收阻塞。

B5-07 证明 Playwright Python runtime 可冻结且能使用显式受信系统浏览器与 App 私有 Profile。B5-08 在同一生产模块上新增正式 `BrowserRuntime`：生产请求默认 `headless=False`，只有 Rust 内部受信验收配置可设无头；启动仍要求显式内部 `executable_path`、`accept_downloads=False`、30 秒启动上限、15 秒动作默认值和 30 秒导航默认值。Runtime 线程约束且同时只允许一个 context，提供主窗口、开窗、捕获弹窗、1～60000 ms 显式等待、定向关窗和幂等关闭。启动前再次复验浏览器/Profile 路径；关闭无论 context 是否失败都停止 Playwright driver，错误、路径和对象表示统一脱敏。macOS 冻结实包已用受信 Chrome/私有 Profile/真实 B5-06 锁验证双窗口正常关闭，并在生产 Manager 相同 process-group 语义下强杀完整后代树；Windows Job Object 原生组合仍待 runner。B5-09 的 `executor/rpa/douyin/session.py` 只接受 Runtime-owned `BrowserWindow`，固定访问官方 `https://www.douyin.com/user/self` 后从有界选择器封闭返回 `healthy/expired/missing/risk/unknown` 和非敏感 evidence；来源冲突、页面不可用、未知 DOM 或非官方 origin 全部 `unknown`，只有 `healthy` 关闭熔断。

B5-10 的 `executor/rpa/douyin/login.py` 复用该 detector，并从同一个 Runtime 打开一个专用 headed 窗口。`begin()` 固定导航到 `/user/self`，只在初始 `login_required/unknown-insufficient` 时最多等待 10 秒让异步二维码或用户资料壳出现；`recheck()` 不接收扫码/认证布尔值，先观察当前页面，只有非冲突 `unknown` 才回到受保护入口复验。二维码使用真实页面的 `aria-label="二维码"` 语义，不读取 `src`。真实抖音页已验证空白 Profile 显示官方二维码、实际扫码后收敛到 `healthy`，以及用户授权持久 Profile 重开后直接复用登录；真实系统 Chrome 绑定官方 origin 的隔离路由夹具另覆盖手机确认、二维码失效和冲突分支。

D6-01 的 `executor/rpa/douyin/page_version.py` 是 Wave 6 唯一页面 route/version 模型。它只认 `douyin.web.v1` 的 canonical 首页、`/user/self` 和 `/search/<term>?type=general`，并严格校验 HTTPS 官方 host、端口、userinfo、fragment、路径、查询、percent/UTF-8 编码、控制/Bidi 与资源上限；任何未知组合返回 `unknown/circuit_open`，`require_entry()` 不允许调用方带着错误入口继续。B5-09 Session probe 常量已改从该模型导入；模型不依赖 Playwright、Control Plane 或 Task 领域，不读取或输出页面正文、Cookie、账号、Profile 和源 URL。

D6-02 的 `executor/rpa/douyin/search_page.py` 是搜索页唯一 Page Object。它复用 D6-01 route/version 事实，按可访问语义优先、版本化 `data-e2e` 兜底集中封装搜索输入、提交、结果列表、登录和阻塞弹窗，并只从 Runtime-owned `BrowserWindow` 产生 `home_ready/results_ready/login_required/dialog_blocked/unknown`。入口/锚点冲突、缺失、页面异常和识别后的 DOM 变化全部 fail closed；模块本身不导航、不点击、不输入、不滚动、不执行页面脚本，也不读取或输出 Cookie、storage state、页面正文、URL、账号或 Profile。

D6-03 的 `protocol/douyin_search.py` 是 Control Plane 与 Local Executor 共同使用的 `douyin.search-input.v1` 输入策略：关键词按 Unicode code point 限 `1..80`，必须保持原始首尾边界且拒绝 C0/C1、DEL、Bidi、敏感赋值、私有路径和 inline data；单任务目标数固定 `1..100` 并拒绝 bool/浮点冒充整数。T3-17 领域对象已改为构造该共享不可变对象，不再维护第二组 Python 常量或校验分支；错误和对象表示不回显关键词。FastAPI/OpenAPI、PostgreSQL、React Zod/表单和 Rust 原生桥继续在各自信任边界复验相同上下限。

D6-04 的 `executor/rpa/douyin/search.py` 是搜索动作的单次执行边界。它只接收 Runtime-owned `BrowserWindow` 与 D6-03 `DouyinSearchInput`，经 D6-01 构造并复验 canonical 结果 URL，经 D6-02 Page Object 等待并重新取得控件；固定执行一次首页导航、一次原样 `fill`、一次 `click(no_wait_after=True)`、精确结果 URL 等待和结果列表复验。每阶段有界，任何超时、登录、阻塞弹窗、入口/锚点冲突、DOM 漂移或页面异常都返回不含关键词/URL 的封闭结果，并且同一执行对象不可再次运行。模块不滚动、不评论、不私信、不执行页面脚本、不访问 Cookie/storage，也不连接 Control Plane；D6-05 才在成功结果页上增加有界滚动。

D6-05 的 `executor/rpa/douyin/bounded_scroll.py` 只接受 D6-04 成功观察、同一 Runtime-owned 窗口、公共搜索输入和无参数取消探针。它最多执行 20 轮固定 `mouse.wheel(0, 800)`，每轮最多 3 秒、每 100ms 只经 Page Object 复验页面并读取裁剪到 `target_limit` 的结果项数量；达到目标、一轮无新增或轮次上限即封闭完成。取消在开始、滚动前、增长等待期间和增长后检查，异常/非 bool 探针、计数倒退、登录/弹窗、页面漂移或浏览器失败均开路停止。结果项 selector 仍只在 `search_page.py`，滚动模块不读取候选内容、不执行脚本、不点击、不评论/私信、不调用 Control Plane；D6-06 再定义可持久化 Candidate。

D6-06 的 `protocol/douyin_candidate.py` 是 Control Plane 与 Local Executor 共同消费的纯 `douyin.candidate.v1` 模型。平台目标 ID 只接受 `1..128` 位规范 ASCII identifier，最小摘要只允许 `1..80` 位安全展示名和可选 `1..64` 位规范公开号；来源当前唯一允许 `general_search_author`，page revision 为 `1..2^53-1` 真整数。去重键以固定域和原始平台目标 ID 做 SHA-256，再编码为 `atdck1_` + 43 位 Base64URL，因此同一目标跨名称/revision 稳定且不同目标分离。模型不可变、异常和对象表示脱敏，不含头像、简介、联系方式、页面正文或 URL；D6-07 已收紧页面提取裁剪，D6-09 已接入 PostgreSQL，D6-10 的正式 Executor wire 只传输其最小公开字段且不传去重键。

D6-07 的 `executor/rpa/douyin/candidate_extraction.py` 是单次、有限的 Candidate 隐私裁剪器，selector 和原始 DOM 读取仍只存在于 `search_page.py`。Page Object 只读取受控作者节点的 `data-user-id`/官方作者 href、显示名与可选 `data-user-handle`；相对或官方 HTTPS href 在本机缩减为目标 ID，query 不离开页面层，跨域、userinfo、fragment、层级路径及 ID 冲突拒绝。返回值只能是 D6-06 Candidate tuple；任一非法项、浏览器读取异常或提取中页面漂移都会丢弃整个快照，不返回部分集合。该层不读取页面正文、HTML、头像、简介、联系方式、Cookie/storage，不调用 Control Plane，也尚不进入 Executor wire 或数据库。生产集成验收从 D6-04 搜索公开入口进入同一无头系统 Chrome，再调用公开提取器并确认私有测试页字段未出现在结果中；真实抖音 DOM 仍由 D6-16 验收。

D6-08 的 `control_plane/domain/douyin_candidate_policy.py` 是无 I/O 的 `douyin.candidate-policy.v1` 领域策略。MVP 历史窗口固定 30 天，采用包含 cutoff 与评估时刻的 UTC 闭区间；输入只接受单一 page revision、最多 100 条 Candidate、每个 key 唯一的最新历史事实和黑名单 key。判断只读取 D6-06 `dedupe_key`，原因优先级固定为 `blacklisted > duplicate_in_task > duplicate_in_history > eligible`，并保持输入顺序和全部 Decision，供预览明确展示排除原因。未来时间、非 UTC、重复 lookup key、混合 revision、非 tuple/伪类型或自相矛盾的直接构造全部拒绝且不回显候选。模块不读取平台 ID/昵称、公开号或页面数据，不依赖 SQLAlchemy/仓储、Executor、浏览器、HTTP 或 Tauri；D6-09 已由 PostgreSQL 提供 Installation-scoped 历史事实并持久化 Decision，黑名单 key 仍须由当前已认证用例提供且不会从其他 Installation 查询。

D6-09 的迁移 `20260718_0016` 与 `SqlAlchemyTaskTargetRepository` 建立真实 `task_targets` 边界。表只保存 Target UUIDv4、Task/Installation 复合归属、`1..100` ordinal、D6-06 最小 Candidate 字段、page revision、D6-08 disposition/policy version 和 UTC 时间；没有页面正文、源 URL、头像、简介、联系方式或自由 JSON。Target ID 全局唯一，`(id,task_id,installation_id)` 为后续复合引用保留绑定，`(task_id,installation_id,ordinal)` 保证预览顺序唯一；dedupe key 刻意不唯一，以保留 `duplicate_in_task` 行。仓储在 active Installation 与精确 Task 行锁内，仅按当前 Candidate key 查询同 Installation、其他 Task 的最新历史，再调用 D6-08 并在同一事务替换快照；同/旧 page revision、快照早于父 Task、跨 scope、吊销实例和非法输入都会回滚。列表按 `(ordinal ASC,id ASC)` keyset 并强制 task/installation/page revision 三重过滤；D6-10 已从正式 Executor 收敛入口调用该仓储，D6-11 再提供公开 cursor/API。

D6-10 的 `POST /api/v1/tasks/{task_id}/discoveries` 只接受 `app.control-plane` Session 和 `Idempotency-Key`。`SqlAlchemyTaskDiscoveryRepository.start` 在 active Installation、健康且未被 logout gate 阻断的抖音 Session 下，原子建立唯一 Attempt、`task.discover` command、`task.discovery_started` event 和 `discovering_targets` Task 投影；首次返回 202，同键同任务重放返回 200。命令 payload 由服务端 Task 定义生成固定 `douyin.discovery.v1` keyword/target limit/page revision，客户端不能提交浏览器路径、Cookie、Profile 或页面内容。

D6-11 的 `TaskTargetPreviewService` 与 `SqlAlchemyTaskTargetPreviewRepository` 暴露 Installation-scoped 最新预览。`GET /api/v1/tasks/{task_id}/target-preview` 使用绑定 page/task revision、ordinal 和 Target ID 的有界 keyset cursor；`PUT .../exclusions` 精确替换用户排除集合并追加 `task.target_selection_updated`；`POST .../confirmations` 至少保留一个可用目标，持久化选择 revision 后追加 `task.targets_confirmed`，将 Task 从 `awaiting_confirmation` 收敛到 `queued`。两个写入口都要求 `Idempotency-Key` 和期望 revision，精确重放返回原事实，改体、过期、跨 scope、策略排除项、并发竞争或数据库事实缺失统一 fail closed。迁移 `20260720_0018` 只增加最小排除/确认关系和事件词汇；Target 重新发现会先删除旧确认。公开 DTO 不含平台目标 ID、dedupe key、页面正文、URL、Cookie 或 Profile 路径。

D6-13 的迁移 `20260720_0019` 为既有 `task_commands` 增加可空 `target_confirmation_message_id`，不复制 Target、文案或任意 payload。只有带 `douyin.search_exposure.v1` 定义、未来可能承载浏览/评论/私信执行的 `task.offer` 必须在 enqueue 时命中 `queued` Task 的当前 confirmation 并绑定其 UUIDv4 source message；同键重放也必须仍匹配同一确认。claim 使用关联 `EXISTS` 再验绑定、Task scope/status/revision，缺失、旧绑定、确认被清除和预确认命令都保持 `pending/delivery_attempts=0`；发现与控制命令继续可用，紧停不依赖确认。迁移前无绑定的业务 offer 会 fail closed 而不是补造授权；无业务定义的 T3 协议骨架保持兼容。该任务不签发 ActionAuthorization、不生成动作 payload，也不执行平台副作用。

D6-14 的 `executor/page_drift_artifact.py` 是页面漂移专用、尚未上传的本机诊断 spool，不是 H8-09 通用 Artifact API。只在 D6-04 明确返回 `page_version_unknown/conflicting_anchors` 时写入；Schema 全部是固定枚举与数值，单文件上限 2 KiB、总数上限 20，文件名为 UUIDv4，引用带 SHA-256、固定媒体类型和 `page-drift-artifacts/<id>.json` 相对路径，目录/文件在 POSIX 分别收紧为 `0700/0600`。任意未知目录项、路径替换、坏 ID/时钟、磁盘/权限或部分写入都 fail closed，部分文件会回收；诊断失败不能解除页面熔断，发现结果仍经正式 Executor wire 收敛到 `handoff_required`，Control Plane 投影为 `awaiting_human`。关键词、URL、HTML、页面正文、Cookie、凭据和 Profile 路径没有输入字段，H8-09/H8-12 后续再统一展示、保留与清理接口。

D6-15 的权威 Fake 页面只位于 `tests/fixtures/douyin_discovery_pages/`。`home.html` 提供唯一搜索入口；结果样例分别提供两条最小作者、可见但零 article 的空列表，以及每次 wheel 只增加一条作者的无界源；另三个入口样例封闭普通 dialog、302 Session probe 与未知官方路径。回归从服务端已生成的 `task.discover` wire 开始，不绕过 Page Object；正常结果收敛 2 个 Candidate，空结果为 `failed/no_candidates`，弹窗/未知版本进入 handoff，登录跳转进入 login required，无限源在本机 20 轮硬上限后只保留 21 个 Candidate。每个场景结束都关闭 BrowserRuntime，测试不点击登录、不执行平台副作用。

D6-16 首轮真实 Profile 验收暴露首页风控 iframe 未被搜索 Page Object 识别、只能等到 `home_ready_timed_out`。`session.py` 现公开唯一 `DOUYIN_RISK_CHALLENGE_SELECTORS`，`search_page.py` 复用它并保持登录弹窗优先级；官方验证码 iframe 或 captcha container 与普通阻塞 dialog 一样立即返回 `DIALOG_BLOCKED/BLOCKING_DIALOG`，D6-04/D6-10 最终形成 `handoff_required`。该修复没有增加绕过、自动解题或验证码交互，也没有把 iframe URL、页面内容或 Session 数据写入 Artifact/日志。真实 Profile 已证明受保护页健康，但首页挑战尚未解除，真实候选/预览继续待补。

Local Executor 的 `ExecutorCommandProcessor` 在 D6-10 的 SQLite v3 中先保存 `task.discover`，只通过 `ProductionDouyinDiscoveryOperation` 组合现有搜索、有界滚动和最小提取器，并将结果以最多 10 条 Candidate 的 `task.discovery_batch` 和唯一 `task.discovery_completed` 写入持久 outbox 后发送；A7-04/A7-07 已在不丢失这些事实的前提下依次原地迁移到 SQLite v4/v5。Control Plane 的 bounded accumulator 只接受当前已确认命令的连续批次；完成时在同一 PostgreSQL 事务复验 Installation/Task/Attempt/correlation/page revision、调用 D6-09 替换 Target、追加终态事件并把 Task 收敛到 `awaiting_confirmation`。登录失效、人工接管和失败分别进入封闭状态，不保存 Target；断线完整重放必须与已持久化快照精确一致。三端 Schema/解析器现共同覆盖 28 种消息、10 个 valid 和 27 个 invalid fixture。

B5-11 把 flow 契约升级为 `douyin.qr-login.v2`，当前状态固定为 `login_required/awaiting_scan/awaiting_confirmation/qr_expired/healthy/handoff_required/unknown`。B5-09 发现 ByteDance 验证中心外层挑战后只投影 `handoff_required/risk_challenge`，不读取跨源挑战内容，也没有点击、填写、拖拽、验证码识别或绕过路径；同一个可见窗口留给用户处理。无参数 `recheck()` 只有重新观察到 `healthy` 才关闭熔断，挑战仍在、页面未知或登录失效都继续阻止副作用。代码不调用 Cookie/storage-state API，页面、二维码、验证码、Profile 路径和账号信息不进入协议、Tauri IPC 或日志。B5-13 已提供平台状态与重新检查，B5-14 已启用带二次确认的安全注销。

B5-12 的 `executor/rpa/douyin/health.py` 把生产 detector 事实写入 SQLite v2 平台 Session 表，再构造 Executor-scoped `platform.session_health`。首次观察建立 revision 1；倒序观察、较低 revision 和同 epoch 非健康→健康全部拒绝，只有显式恢复/重新登录推进 epoch。Control Plane 在认证 WebSocket 上复验 Installation/Executor scope，PostgreSQL 表只保留 `installation_id/platform/state/session_revision/observed_at/updated_at`；Cookie、Profile、二维码、验证码、页面原文、message/executor ID 均不持久化。

B5-13 新增 Installation-scoped `GET /api/v1/platform-sessions/douyin`，复用 active Installation 认证并只返回 exact 平台、封闭状态和观察时间；无投影为 `unknown/null`。`executor/platform_commands.py` 与正式 CLI 组合一个认证 stdin worker，使用独立 `atlcp1` HMAC 域接受固定 open/recheck 命令，在线程内复用 `DouyinQrLoginFlow`，并把 `DouyinSessionHealthReporter` 结果排入同一个 WebSocket runtime。命令结果、错误和对象表示不返回路径、页面或凭据；健康、错误、EOF、App 退出与 Manager 强停都会关闭 flow、context、driver 和完整进程树。

B5-14 新增 Installation-scoped `POST /api/v1/platform-sessions/douyin/logout/prepare`，返回非敏感 blocked revision 并在 PostgreSQL `platform_session_gates` 持久化门闩。存在门闩时，新 Task 和新 offer 一律拒绝；claim 路径与 prepare 共用 Installation 行锁，已排队/待重投的工作命令也不会被取出，仅取消与紧停命令仍允许投递。同一 prepare 幂等，只有门闩 revision 之后的真实 `healthy` 上报才能删除门闩。Tauri 在 prepare 成功后才停止 Executor、释放浏览器/Profile 锁并定向删除 current Profile，再重启 Executor 发送 path-free `douyin.logout.complete`；该命令推进本机 SQLite epoch、上报 `missing`，App 只在重新查询到权威 `missing` 后返回成功。

Manifest 工具在 `onedir` 根内写入 `executor-manifest.v1.json` 和 `executor-manifest.v1.sig`。Manifest 使用 compact、键排序的 ASCII JSON 加单个 LF，签名覆盖这些原始字节；签名文件固定为无 padding Base64URL 的 `atems1` envelope。Manifest 绑定 SemVer、构建 ID、平台、架构、平台精确入口、总大小、目录摘要和每个 payload 普通文件的相对路径/大小/SHA-256；metadata 自身不进入清单。离线 Ed25519 私钥必须是 stdin 中精确 32 字节，不能放入 argv、环境、日志、仓库、构建产物或 App；仓库固定 fixture 使用的 `00..1f` 只是假测试种子，不能用于发布。

`automation-tool-executor` 只由 Tauri/PyInstaller 入口通过 stdin 启动；不要把 bootstrap JSON、Session 或其他秘密放入命令行、环境变量、shell 历史或普通配置。E4-06 在 Control Plane `session_token` 之外增加用途隔离的 `local_session_token`：Rust 每次用系统 CSPRNG 生成精确 32 字节并编码为 64 位小写十六进制，Python 只在 `SecretStr`/可清零认证器中持有。stdout 的 `executor.healthy`/`executor.stopped` 只携带按事件和协议版本域隔离的 `atlep1` HMAC-SHA-256 证明，不回传令牌；E4-07 的 Rust Manager 必须以现有常量时间 verifier 校验后才接受进程健康。构建目录和产物均被 Git 忽略；E4-04/E4-05 分别负责完整目录签名与 Rust 可信复验。

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
