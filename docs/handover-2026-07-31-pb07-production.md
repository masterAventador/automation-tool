# 交接文档（2026-07-31）——PB-07 B站生产发布链路收束

> 接手对象：用户家中 Codex
>
> 交接范围：本轮只收束 PB-07 的 B站生产装配、最终门禁与异常文件排查；本文和代码一起
> 提交到 `main`。没有继续激活 PB-08、CQ 或其他专项任务。
>
> 当前结论：PB-07 的生产代码与确定性验证已经完成；按项目“真实用户路径”规则仍保持
> `🔍 待验收`，因为本机没有真实 B站开放平台凭据，不能用 Mock 结果冒充真实投稿。

---

## 1. 为什么本轮需要重做 PB-07 的生产装配

旧证据文件曾说明“双平台发布界面已完成”，但从正式 `main.tsx`、Tauri composition root
和 Control Plane App factory 重新沿真实入口审计后，实际存在一个跨层缺口：

- PB-02～PB-04 已有 B站开放平台契约、领域服务、HTTP gateway、SQL attempt 和对账模型；
- React 发布页也已经列出 B站；
- 但 Rust 把 B站路由固定为 `NotIntegrated`，正式 App 不可能调用已有服务；
- Control Plane 的生产 App factory 没装配 B站服务，也没有 App 可调用的路由；
- 用户没有受保护的 B站凭据设置入口；
- 后端容器没有携带运行时读取的
  `contracts/publishing/bilibili-open-api.v1.json`。

因此，旧状态属于“各层能力存在，但生产 composition 不存在”。本轮补的不是另一套
测试替身，而是把正式 App、正式 Control Plane 和正式容器接成一条可运行的链。

---

## 2. 当前生产链路

正常用户路径现在是：

```text
设置页保存 B站开放平台凭据
  ↓
Rust 受保护存储持有密钥；React 只看“已配置”和公开投稿默认项
  ↓
用户从已登记成片进入“作品发布”，选择 B站并填写标题/简介
  ↓
Rust 从 Artifact ID 解析并创建 App 私有暂存副本
  ↓
随正式包逐文件验签的 ffprobe 读取真实容器时长，向上取整到整秒
  ↓
Control Plane prepare：校验凭据、素材摘要、字段和平台上限，建立 installation/job 绑定会话
  ↓
App 流式上传视频；服务端私有临时文件再次核对大小和 SHA-256
  ↓
B站开放平台上传完成，App 停在统一的“待你确认”临界点
  ↓
用户确认后才调用 submission（这是稿件创建的不可逆副作用）
  ↓
成功：UI 显示“已提交平台审核”；SQL 记录 BV 资源标识并进入对账
不明确：UI 显示 outcome_uncertain，不自动重试
取消：销毁服务端会话、删除本地暂存副本，不创建稿件
```

B站路径不会启动 Executor、不会读取抖音 Profile，也不会借用抖音的 Browser Use 发布
流程。抖音原路径未改变。

---

## 3. 本轮实现地图

### 3.1 Control Plane

新增或接通的核心文件：

- `backend/src/automation_tool/control_plane/api/bilibili_publishing.py`
  - `POST /api/v1/publishing/bilibili/jobs/{publish_job_id}`：prepare；
  - `PUT /api/v1/publishing/bilibili/jobs/{publish_job_id}/video`：流式上传；
  - `POST /api/v1/publishing/bilibili/jobs/{publish_job_id}/submission`：用户批准后创建稿件；
  - `DELETE /api/v1/publishing/bilibili/jobs/{publish_job_id}/session`：取消并清理会话；
  - 四条路由都要求当前 installation 的 App Session；
  - 响应统一带 `Cache-Control: no-store`，错误不反射凭据。
- `backend/src/automation_tool/control_plane/application/bilibili_publishing_runtime.py`
  - 把已有 PB-03 领域服务、官方 gateway、token provider 和 SQL store 装成生产运行时；
  - 会话绑定 `installation_id + publish_job_id + 随机 session token`；
  - 最多 32 个会话，硬过期 2 小时；
  - 同一 installation 开始新任务时先替换旧会话；
  - 正常提交和取消后立即关闭 HTTP client 并销毁内存凭据；
  - `repr` 不输出密钥。
- `backend/src/automation_tool/control_plane/infrastructure/bilibili/token_provider.py`
  - 持有短生命周期 access/refresh token；
  - 距过期不足 5 分钟时，在平台调用前主动续期；
  - token rotation 通过响应返回 App，长期所有权仍在桌面受保护存储；
  - token 不写 Control Plane 数据库和日志。
- `backend/src/automation_tool/control_plane/bootstrap/bilibili_publishing.py`
  - 从锁定契约文件构建运行时；
  - 正式镜像读 `/app/contracts/publishing/bilibili-open-api.v1.json`；
  - 本地开发从仓库契约读取；
  - 缺失、symlink 或解析失败时 fail closed。
- `backend/src/automation_tool/control_plane/bootstrap/app.py`
  - 正式 App factory 装配 runtime 和四条路由；
  - lifespan 退出时先关闭 B站会话，再关闭数据库。

持久状态复用现有
`SqlAlchemyBilibiliArchivePublishStore` 和
`SqlAlchemyBilibiliReconciliationStore`，没有另造第二份发布账本，也不需要新增迁移。

### 3.2 Tauri / Rust

- `frontend/src-tauri/src/bilibili_service_settings.rs`
  - Client ID、App Secret、Access Token、Refresh Token 只存 Rust 受保护存储；
  - snapshot 只返回目标账号、分区、标签、转载设置和配置状态；
  - 临时 secret 使用 zeroize；
  - 单字段最多 4096 bytes，聚合序列化后同样不得超过 secure store 的 4096-byte 上限；
  - token rotation 原子替换受保护存储中的旧 token。
- `frontend/src-tauri/src/control_plane.rs`
  - 新增四个固定 Control Plane operation；
  - OpenAPI operation allowlist、路径、HTTP 方法、状态码和响应形状逐项闭合；
  - prepare JSON 使用 zeroizing buffer；
  - 视频用 `tokio_util::io::ReaderStream` 流式上传，不把整片读入内存；
  - 只接受规范 UUIDv4、SHA-256、session token 和 BV 资源标识。
- `frontend/src-tauri/src/video_media_toolchain.rs`
  - 复用正式包内已验证的 `ffprobe`；
  - 读取容器 `format=duration`，拒绝空、NaN、无穷、非正数和超界值；
  - B站要求整秒，使用 `ceil` 避免低报最后不足一秒的时长。
- `frontend/src-tauri/src/lib.rs`
  - `get_publish_workspace` 从受保护存储判断 B站是否 ready；
  - `begin_publish` 按平台 route 穷举分派；
  - B站 prepare + upload 成功后才进入统一审批 UI；
  - `approve_publish` 只在 confirmation ID 与当前审批一致时调用 submission；
  - `cancel_publish` 对 B站调用服务端 session cancel，并清理 App 暂存副本；
  - 运输失败发生在 submission 后时，一律落 `outcome_uncertain`，不自动重发。
- `frontend/src-tauri/src/publish_workspace.rs`
  - B站 route 从 `NotIntegrated` 改为 `OfficialApi`；
  - 新增用户终态 `submitted`，中文为“已提交平台审核”；
  - 抖音仍使用原有 `published` 终态。

### 3.3 React

- `frontend/src/features/settings/BilibiliServiceSettings.tsx`
  - 设置页可填写已签发的开放平台凭据、授权有效期、目标账号、tid、标签和转载设置；
  - 保存成功立即清空所有密钥输入；
  - 清除配置后公开字段也立即恢复空值；
  - 页面不回显任何密钥。
- `frontend/src/features/settings/bilibili-service-gateway.ts`
  - 定义严格公开 snapshot 和关闭的错误码集合。
- `frontend/src/platform/tauri/bilibili-service-gateway.ts`
  - 固定调用三个 native command；
  - 拒绝多字段、泄漏 secret、状态不一致和畸形 snapshot。
- `frontend/src/main.tsx`、`frontend/src/app/App.tsx`、
  `frontend/src/app/WorkbenchShell.tsx`
  - 正式入口构造并注入真实 B站设置 gateway；
  - production wiring 测试防止再次出现“文件写好了但没人装配”。
- `frontend/src/features/publishing/PublishWorkspace.tsx`
  - 标题和简介上限分别收敛到 B站正式契约的 80/250 字符；
  - 支持 `submitted` 终态。

### 3.4 契约、容器和部署

- `contracts/openapi/control-plane.v1.json`
  - 已包含四个 B站 operation 及其严格 DTO。
- `frontend/src/api/generated/control-plane.ts`
  - 已从最新快照重新生成。
- `contracts/publishing/publish-workspace.v1.json`
  - 新增 `submitted`；
  - “待配置”提示改为引导用户进入设置。
- 根 `.dockerignore`
  - 默认拒绝全部内容；
  - 只放行后端运行时、迁移和一个锁定的 B站契约；
  - tests、虚拟环境、缓存、工作区产物都不进入 context。
- `backend/Dockerfile`
  - 构建上下文统一为仓库根；
  - builder/runtime 都显式复制 B站契约；
  - runtime 仍以 UID/GID 65532 非 root 运行。
- `deploy/cloud/deploy.sh`、`deploy/cloud/deploy_cloud_demo.py`、
  `scripts/run_c10_02_acceptance.py`～`run_c10_08_acceptance.py`
  - 全部同步为同一个仓库根 Docker build context；
  - 云端 source bundle 加入 `.dockerignore` 和锁定契约。

### 3.5 用户文档

已更正：

- `docs/user-help.md`；
- `docs/privacy-notice.md`；
- `docs/development/PB-07.md`；
- `docs/development/FIX-publish-video-source-wiring.md`；
- `docs/development/PLAN-publish-video-source.md`；
- `docs/development/completed-task-wiring-audit-20260726.md`；
- `docs/embedded-browser-video-studio-roadmap.md`。

隐私表述现在明确：凭据长期保存在桌面受保护存储；发布时会通过配置的 Control Plane
传输并短暂存在服务端内存；不会进入 Control Plane 数据库或普通日志。

---

## 4. 本轮额外修掉的缺陷

### 4.1 secure store 聚合大小

最初只验证每个 secret 不超过 4096 bytes，但四个字段分别合法时，聚合 JSON 仍可能超过
底层 secure store 的 4096-byte 上限，导致“设置页先接受、保存时才报存储故障”。现在保存
前先验证完整序列化长度，返回 `configuration_invalid`，并有 Rust 回归测试。

### 4.2 OpenAPI 假门禁

新增路由后，旧 OpenAPI 快照和 Rust 的“完整 operation allowlist”测试仍可能保持绿色，
因为测试只核对旧集合。现已：

- 重新生成 Control Plane OpenAPI 快照；
- 重新生成前端 TypeScript DTO；
- 把四个 operation 加入 Rust 的路径、方法、状态码和 operationId 闭合集合；
- 把 Customer Demo 的 AppSession 写操作契约同步为完整集合。

### 4.3 服务端会话泄漏

最初取消只清 App 内存，不通知 Control Plane，凭据会留到两小时过期，并可能耗尽 32 个
session。现在有正式 DELETE session 路由，正常取消立即销毁；同 installation 的新 prepare
也会替换任何旧 session，作为网络清理失败后的第二道边界。

### 4.4 审批等待期间 token 过期

视频可能已上传，但用户在审批页停留，token 到 submission 时才过期。现在
`current_access_token()` 在距离过期 5 分钟以内主动 refresh，避免不可逆调用开始后才发现
凭据已经失效。

### 4.5 设置清除后的公开状态

清除 secret 后，旧目标账号、tid 和 tag 曾继续留在表单中，容易让操作员误认仍有配置。
现已补红绿组件测试，clear snapshot 会同步清空所有公开默认项。

---

## 5. 凭据和副作用安全边界

后续修改这条链时，不得破坏以下约束：

1. React 不得读取已保存 secret；snapshot 只能含公开设置。
2. App Secret、Access Token、Refresh Token 不得进入普通日志、Debug/Display、数据库、
   OpenAPI 响应或发布审计。
3. B站视频上传本身发生在审批前，但“创建稿件”必须严格发生在用户确认之后。
4. confirmation ID、publish job ID 和 session token 是三个不同身份，不得复用。
5. submission 网络或协议结果不明时只能是 `outcome_uncertain`，不得自动重试。
6. 取消只适用于 submission 之前；派发后不能显示“已取消”。
7. B站不得启动抖音 Executor、读取抖音运营 Profile 或接管抖音浏览器。
8. 服务端临时视频目录为 `0700`，视频文件为 `0600`，离开请求作用域即删除。
9. 大小、时长、扩展名和 SHA-256 在已有领域服务中再次核对；不能只信 HTTP header。
10. 真实投稿是外部副作用。没有用户明确授权、真实账号和验收窗口时，不要自行触发。

---

## 6. 最终门禁证据

本轮最终状态下执行并通过：

```text
# 后端全量（从 backend/ 执行）
uv run pytest tests/unit tests/contract -q
3703 passed, 2 skipped in 51.50s

# 前端完整 Vitest
pnpm test:unit
67 files passed；638 tests passed

# 前端静态/契约
pnpm test:contracts
274 passed
pnpm check:api
passed
pnpm typecheck
passed
pnpm lint
passed

# Rust
cargo test --lib --locked
136 passed
cargo clippy --lib --locked -- -D warnings
passed
cargo check --locked --features control-plane-e2e
passed
cargo check --locked --features desktop-e2e
passed（仅 3 条既有 local_registration dead_code warning）

# Python 静态检查
uv run ruff check <本轮 Python 源与测试>
passed
uv run mypy <4 个新增生产源文件>
passed

# 治理门禁
scripts/check_product_completion_roadmap.py
26 tasks / 26 evidence / 26 completed
scripts/check_acceptance_evidence_depth.py
38 checks passed
scripts/check_user_facing_branding.py
58 frontend + 276 native files passed
scripts/check_embedded_browser_video_roadmap.py
passed

# 最终生产镜像
docker build --progress=plain --output=type=cacheonly -f backend/Dockerfile .
passed；context 134.70 kB
日志确认契约进入 builder step #14 和 runtime step #22
```

补充：

- `git diff --check` 通过；
- `cargo fmt --check` 只报告本轮未修改的
  `frontend/src-tauri/src/local_video_orchestrator.rs` 和
  `frontend/src-tauri/tests/local_video_orchestrator.rs` 既有格式差异；
- 为避免覆盖其他工作线，没有格式化这两个文件；
- 本轮涉及的 Rust 文件不在 rustfmt diff 中。

---

## 7. 空文件 `16` 的根因与修复

### 7.1 现象

仓库历史中已经有一次相同事故：

```text
d27f75d chore: 删除误入仓库的空文件 16（shell 重定向笔误产物）
```

本轮曾观察到工作目录出现 0-byte、未跟踪文件 `16`：

- 从仓库根执行时，文件出现在根目录；
- 从 `backend/` 执行时，文件出现在 `backend/16`；
- `backend/16` 的创建时间 19:00:27 落在后端全量测试执行期间。

两次都只删除空的未跟踪文件，没有碰用户产物。

### 7.2 精确定位

先后做了这些隔离：

```text
Frontend Vitest 前 34 个文件：359 passed，前后无 16
Frontend Vitest 后 33 个文件：279 passed，前后无 16
Frontend 完整 67 文件并发：638 passed，前后无 16
```

随后给 pytest 临时挂了一个 `pytest_runtest_protocol` hook，每个用例前后检查当前工作目录，
文件一出现就以专用退出码停止。精确报告：

```text
FILE_16_CREATED_BY=
tests/unit/executor/test_motion_segment_concat.py::
test_the_join_is_measured_afterwards_not_trusted
```

根因是该测试的假 ffmpeg：

```sh
touch "$#"; exit 0
```

Shell 的 `$#` 不是“最后一个参数”，而是“参数数量”。`join_segments()` 当前给 ffmpeg
传入 16 个参数，因此替身在 pytest 的当前工作目录创建了一个名为 `16` 的空文件。它并没有
像测试注释声称的那样创建目标 `film.mp4`，而假 ffprobe 又不读取输入文件，所以测试仍然
保持绿色，副作用长期未被发现。

### 7.3 修复与回归

测试替身已改为遍历参数并触碰最后一个参数，也就是 ffmpeg 的真实输出路径：

```sh
for value do output="$value"; done
touch "$output"
```

测试新增 `assert output.is_file()`；旧替身会在这里失败，因此不是只删除副作用而没有锁住
行为。临时 pytest hook 已删除，当前 `main` 的根目录和 `backend/` 均无 `16`。

仓库内的旧工作树 `wt/pc21-b` 基于删除提交之前的旧分支，该分支本身仍跟踪一个 0-byte
`16`；它不是当前 `main` 的未跟踪产物。为避免污染另一条分支，本轮保持它原样，也没有触碰
该工作树原有的两个 `.local-streamB*.summary`。该旧工作树后续若 rebase/merge 当前
`main`，历史删除提交会一并处理。不要把 `16` 加入 `.gitignore`，修复点应继续由目标文件
断言守住。

---

## 8. 当前仍未完成的真实边界

这些不是本轮生产代码缺失，而是需要外部条件的验收：

1. **没有真实 B站开放平台凭据。**
   当前设置入口接收已经签发的 Client ID、App Secret、Access Token、Refresh Token 和
   expiresAt；App 不代替 B站授权页完成 OAuth 用户授权。
2. **没有真实 B站投稿。**
   领域、HTTP、SQL、App 与 UI 的确定性证据齐全，但没有平台返回的真实 BV ID，所以 PB-07
   和 PB-08 都不能改为 `✅`。
3. **没有 Windows 正式 App 的 B站用户路径。**
   Rust 和容器边界可跨平台编译，真实 Windows 安装树仍需在 Windows 机器验证。
4. **发布页审计仍是进程内状态。**
   Durable platform attempt 在 PostgreSQL；发布页展示用的 UI audit 重启后清空。PB-07
   原任务没有要求把 UI audit 另存一份，后续若扩范围必须先补独立设计。
5. **网络清理失败有最多两小时内存窗口。**
   正常提交/取消会立即清理；若 App 在 cancel 前崩溃且后续清理请求也失败，Control Plane
   只在内存持有该会话，最多两小时，同 installation 下次 prepare 也会替换它。

---

## 9. 家中 Codex 的建议接手顺序

### 9.1 第一优先：先同步并确认本提交

```bash
git switch main
git pull --ff-only
git status --short --branch
git log -3 --oneline
```

期望工作区干净，根目录没有 `16`。若本机尚未把办公室提交推到共享远端，先由用户明确决定
是否推送；不要在不确认远端状态时强推或重写 `main`。

### 9.2 有真实 B站凭据时：完成 PB-08 的真实纵向验收

真实投稿前先让用户确认测试账号、视频、标题、分区和“本次会产生真实平台稿件”。

建议至少做两条独立用户路径：

#### 路径 A：取消

1. 从正式 App 设置页保存测试账号凭据；
2. 选择一条可丢弃的已登记 mp4 Artifact；
3. 发起 B站发布，等视频上传完成并出现统一审批卡；
4. 核对审批卡目标账号、视频摘要、标题和简介；
5. 点击取消；
6. 证明平台没有新稿件，App 暂存副本已删除；
7. 再发起一个任务，确认没有旧 session 占用或旧文案复用。

#### 路径 B：单次提交

1. 使用新的 Artifact 和新的标题，避免与路径 A 混淆；
2. 发起后先确认 UI 停在审批点，平台尚无稿件；
3. 只点击一次确认；
4. 记录真实平台返回的 BV ID、平台稿件状态和 Control Plane SQL phase；
5. UI 应显示“已提交平台审核”；
6. 重复点击、重放 confirmation 或重启 App 都不得创建第二份稿件；
7. 退出 App 后核对没有本次暂存文件、浏览器或进程残留。

如果可控制测试 token，再补一次“距过期不足 5 分钟”的 rotation 路径：验证投稿仍成功，
下一次任务可继续使用新 token，同时 React、日志和数据库中均找不到 token 明文。

真实验收结果写进：

- `docs/development/PB-08.md`；
- `docs/embedded-browser-video-studio-roadmap.md` 的 PB-07/PB-08 行；
- 对应正式包、平台回执和清理证据。

不要只写“接口 200”或“测试 passed”；至少保留真实 BV ID、平台状态、SQL phase、Artifact
摘要和清理事实。任何真实 secret 都不得写入证据。

### 9.3 没有 B站凭据时

不要停在等待状态，也不要把 Mock 改写成真实通过。可继续专项 Roadmap 中不依赖凭据的
剩余验收，但 PB-07/PB-08 保持 `🔍`。

当前总体状态：

- 产品完成台账：26/26 `✅`；
- 专项 Roadmap：53 `✅`、25 `🔍`、0 `⬜`、9 `⏸`，后置
  SA/BM-09/BM-10 不在首发完成口径；
- 与发布最直接的下一项：PB-08 真实双平台/Windows 纵向；
- 随后是 CQ-03/CQ-04 的真实任务级压力与双平台正式包纵向；
- CQ-05 最后做文档、SBOM、卸载变体和整套台账收口。

Windows、抖音真实账号、B站真实凭据、有效阿里云剪辑凭据分别是不同外部条件；不要因为
其中一个缺失就把其他可做项一起阻塞，也不要把一个平台的证据冒充另一个平台。

---

## 10. 提交与停止点

本轮停止点是：

- PB-07 生产代码完成；
- 最终确定性门禁通过；
- 生产镜像真实构建通过；
- `16` 的具体测试根因已修复，当前 `main` 根目录和 `backend/` 均无残留；
- 本交接文档已提交；
- 不启动 PB-08 或下一项任务；
- 不创建、停止或删除用户原有的 Docker 容器；
- 不推送远端，除非用户另行明确要求。
