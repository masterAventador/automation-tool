# 客户 Demo 部署设计

本设计适用于 `customer-demo-v1`，权威机器契约是 [`customer-demo-deployment.v1.json`](../contracts/deployment/customer-demo-deployment.v1.json)。现有根目录 `compose.yaml` 只服务本机开发/测试，包含 loopback 端口和本地卷，不能复用为客户环境；因此 C10-01 新建独立部署契约，但不在本任务创建云资源、真实域名、生产 Compose 或第二套业务实现。

## 拓扑与信任边界

```text
Tauri App / Local Executor
          |
          | HTTPS + WSS :443
          v
  专用 TLS 反向代理  -- 唯一公网入口
          |
          | 私网 HTTP :8000
          v
  Control Plane x1
          |
          | 私网 PostgreSQL TLS :5432
          v
  PostgreSQL Primary x1
          |
          +--> 加密备份存储（只允许备份/恢复作业访问）
```

- 公网只暴露 HTTPS `443`；`80` 仅做同域 HTTPS 跳转。Control Plane 和 PostgreSQL 均无公网直连地址。
- 域名使用 `api.<customer-domain>` 占位，C10-04 才绑定真实域名、证书、反代与安全头。桌面 App 只信任签名 Demo Profile 中的精确 HTTPS origin，不接受用户输入 base URL。
- Control Plane 保持一个进程实例、一个 worker，不自动横向扩容；PostgreSQL 保持一个 primary，不声明 HA 或自动故障转移。该限制与当前进程内连接注册、单任务 Executor 事实一致。
- App、Executor 与 Control Plane 继续使用同一 OpenAPI、Session、Installation 和任务协议。不存在 Web 产品、公共注册入口、匿名业务写入口或直连数据库的桌面能力。
- 数据库仅允许 Control Plane、一次性迁移作业和受控备份/恢复作业访问。应用账号不是 superuser，迁移权限与运行权限在 C10-03 分离。

## 容量与资源预算

| 组件 | 硬上限 | 设计容量 |
| --- | --- | --- |
| HTTPS 入口 | `0.25 CPU / 128 MiB` | 单域名、单上游；请求体最多 `1 MiB` |
| Control Plane | `1 CPU / 1024 MiB`，1 worker，30 秒优雅停止 | 最多 5 个同时在线桌面 App |
| PostgreSQL 18 | `1 CPU / 2048 MiB / 20 GiB`，50 连接 | 单 primary；连接池总量必须低于 50 |
| RPA 执行 | 不在云端运行 | 整个 Demo 环境同时最多 1 个 running Task |

这是验收上限，不是性能承诺。达到连接、内存、磁盘或任务上限时，应拒绝新工作并保留既有任务、命令、事件和副作用账本，不允许通过临时多开 Control Plane 绕过约束。磁盘超过 80%、健康连续三次失败、备份失败或证书不足 14 天时必须告警。

## Secret 与配置边界

- DB URL、账号密码 Pepper、账号 fingerprint key、运维 capability digest、动作授权私钥和 TLS 私钥只能来自部署 Secret Store 投影的 runtime UID `0400` 或 root/runtime-group `0440` 固定运行时文件。
- Secret 不进入 Git、镜像层、Compose/部署清单、进程参数、日志或桌面 WebView；运行时错误继续固定脱敏。
- 非秘密配置（环境 ID、公钥、资源上限、域名 allowlist、版本标签）也必须由部署清单单点定义，不能在镜像和运行命令维护两份漂移值。
- Secret 轮换采用受控重启；C10-05 已冻结文件装载与基础轮换依赖，账号/设备吊销操作由 C10-06、最终顺序和应急手册由 C10-13 固化。

## 备份、恢复与故障处理

- 每 24 小时至少一次加密 PostgreSQL 备份，保留 7 天；目标 `RPO <= 24h`、`RTO <= 4h`。
- 首次客户 Demo 前必须把备份恢复到新的隔离 PostgreSQL 实例，执行迁移一致性、`/api/v1/health`、账号登录和最小只读业务核对；不能在原 primary 上覆盖恢复来证明可恢复。
- Control Plane 镜像、部署清单和迁移代码由 Git commit + 不可变镜像标签恢复，不把容器文件系统当备份。Secret 由其所属 Secret Store 单独备份和轮换。
- Control Plane 故障时停止接收新请求；本地 Executor 保留 SQLite/Outbox 并按既有有界重连恢复。PostgreSQL 故障时 `/api/v1/health` 返回不可用，禁止降级为内存数据库或继续宣称写入成功。
- 本拓扑没有自动 failover。数据库损坏或丢失时先隔离故障 primary，再恢复到新实例、核对迁移与版本，最后切换私网连接；全过程允许计划停机。

## 发布与回滚顺序

发布固定串行执行：

1. 生成并验证当前数据库备份；
2. 使用一次性迁移身份执行 Alembic upgrade，失败立即停止；
3. 以不可变版本标签启动一个 Control Plane，旧实例先优雅停止，不并行运行两个业务实例；
4. 校验 `/api/v1/health` 与 `/api/v1/version`，再执行 App 协议兼容检查；
5. 全部通过后才开放 Demo 使用。

健康失败时恢复上一版应用镜像；数据库迁移不自动 downgrade。只有迁移明确向后兼容才能直接回滚应用，否则按备份恢复到新数据库实例并重新核对。任何失败都保留原日志的脱敏 request ID、镜像 digest、迁移 revision 和备份标识，不记录 Secret 或业务正文。

## 后续任务归属

| 任务 | 落地责任 |
| --- | --- |
| C10-02 | 构建锁定、非 root、可探测、可优雅停止的 Control Plane 镜像 |
| C10-03 | 最小权限 PostgreSQL、迁移身份、网络隔离、备份与隔离恢复演练 |
| C10-04 | 真实 HTTPS/域名、反代、请求上限、超时、限流与安全头 |
| C10-05 | Secret 注入、权限、轮换与泄漏扫描 |
| C10-06 | Demo 账号初始化、停用、恢复和全 Session/设备应急吊销 |
| C10-07 | 签名 App Demo Profile、origin allowlist 和 local/demo 数据隔离 |
| C10-08～C10-12 | 经用户明确授权后执行部署、云端回归、恢复、吊销和客户视角验收 |
| C10-13 | 把本设计和实际环境参数固化为可执行部署/回滚手册 |

C10-01 的验收只冻结设计与可执行契约，不证明云资源、证书、备份或恢复已经存在。实际环境事实必须由后续任务逐项补证据，不能用本文档代替。
