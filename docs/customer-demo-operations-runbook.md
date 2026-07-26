# Customer Demo 部署、恢复与回滚手册

本手册只适用于单实例 Customer Demo。所有生产操作必须先填写云厂商、project、environment、域名、操作者、变更单、当前/目标镜像 digest 和时间窗口；没有明确目标时只允许运行隔离 rehearsal。Secret 只能来自只读 Secret 文件或标准输入，不进入 Git、镜像、环境文件、命令参数或日志。

## 1. 发布前检查

1. 两人复核命名目标、变更单、维护窗口、当前与目标不可变 digest、OCI version/revision、当前 Alembic revision 和回滚负责人。
2. 确认 application/database 网络和 runtime/migration/TLS Secret volumes 已由目标环境预创建；环境文件权限为 `0600`，且只含 `deploy_customer_demo.py` 白名单中的非 Secret 配置。
3. 确认 Control Plane 与 Ingress 都只能为 1 副本，自动扩缩容和自动故障副本关闭；PostgreSQL 无公网入口。
4. 运行 `backend/.venv/bin/python scripts/run_c10_13_acceptance.py --print-checklist`，逐项写入变更记录。正式操作前必须有最近一次通过的 C10-03、C10-10、C10-11 演练记录。
5. 若当前 revision、镜像身份、备份状态、证书、Secret 权限或目标身份任一未知，停止发布。

## 2. 备份

1. 使用 `automation_tool_backup` 最小只读身份在云私网生成一致性备份；Secret 从只读文件注入，不把数据库 URI 或口令放入 argv。
2. 备份产物写入不可变、加密的目标环境存储，计算 SHA-256，并生成 `customer-demo-backup-receipt.v1` receipt：环境 ID、数据库 ID、Alembic revision、对象位置、摘要、创建时间和保留策略必须齐全。
3. 在独立新数据库中恢复该备份，核对 Alembic revision、账号/Installation 数量和只读抽样；不得覆盖生产数据库。恢复验证失败则发布终止。
4. 将 receipt 与恢复验证结果附到变更单；部署程序只读取 receipt 和可选本地备份产物做摘要校验，不负责创建云备份。

## 3. 迁移与部署

从仓库根目录串行执行，所有路径必须是本次变更专用路径：

```sh
backend/.venv/bin/python scripts/deploy_customer_demo.py \
  --environment-file /secure/change/non-secret.env \
  --backup-receipt /secure/change/backup-receipt.json \
  --expected-app-version 1.2.3 \
  --expected-vcs-ref 0123456789abcdef0123456789abcdef01234567 \
  --project named-customer-demo \
  --state-directory /secure/change/state \
  --ca-file /secure/change/public-ca.pem
```

执行顺序固定为 preflight → verified backup → `alembic upgrade head` one-shot job → 单 Control Plane → 单 Ingress → HTTPS health/version。不得并行发布，不得跳过备份，不得手工先启动 Ingress，不得对数据库执行 downgrade。任一步失败时执行器只停止本轮新 Ingress/Control Plane，并保留数据库供诊断。

## 4. 健康验证

1. 通过正式域名与公开 CA 请求 `/api/v1/health`，必须是 200 且 service/version 精确匹配；请求 `/api/v1/version`，必须是 200，`apiVersion=v1` 且 version 为目标版本。
2. 检查 Compose project 中恰好一个 Control Plane、一个 Ingress，Control Plane 为非 root、只读 rootfs、无宿主端口；唯一公开端口属于 Ingress。
3. 用 Demo Profile App 运行 C10-09 同协议回归；确认 Executor 重连、outbox 重放和 `Last-Event-ID` 续传。
4. 任一检查失败立即保持入口关闭，进入第 7 节；不得以扩容、第二实例或跳过版本检查恢复服务。

## 5. 账号与设备吊销

账号级事件使用认证运维 one-shot job 的 `emergency-revoke`，只传 user ID、expected revision、唯一 request ID；运维 capability 以形如 `{"capability":"..."}` 的 0600 文件经 stdin 提供，job 必须挂载正式只读 runtime Secret。成功后核对账号 disabled、revision 递增、吊销设备数和 append-only audit；目标账号全部 access/refresh Session、Installation、设备凭据和设备 Session 应立即失效，其他账号不受影响。

单设备吊销只能由已登录账号在 App“设备管理”中选择目标 Installation，并携带服务端返回的 expected revision；核对目标设备立即失效、同账号其他设备与其他账号仍有效。禁止直接改库、匿名调用或临时增加运维 HTTP 写入口。

若怀疑凭据外泄但目标范围未知，先执行第 8 节环境紧急停服，再逐账号应急吊销；任何响应、截图和工单不得记录 capability、Token、Cookie 或密码。

## 6. 隔离恢复

1. 创建新的隔离 PostgreSQL 实例和专用私网，不复用生产数据库主机、volume 或应用连接。
2. 用备份身份把已验证备份恢复到新实例；运行 Alembic revision、约束、账号/Installation 数量和审计连续性检查。恢复期间 Ingress 不得连接该实例。
3. 以目标应用 digest 在隔离 application 网络启动一个无公网 rehearsal，执行 health/version、C10-09 协议和只读业务核对。
4. 只有恢复证据经双人批准后，才在停服窗口把 Secret 中的数据库目标切换到新实例并串行启动单 Control Plane/Ingress。原数据库保持只读封存，直到保留期结束。
5. 不得覆盖生产数据库，不得在现存生产库上运行 `pg_restore --clean`，不得把隔离恢复当成数据库 downgrade。

## 7. 应用回滚

先停止 Ingress 和 Control Plane，保存失败版本的容器元数据与脱敏日志，再根据迁移兼容性选择：

- 若先前应用 digest 明确兼容当前 schema：更新非 Secret 环境文件为该显式不可变 digest，重新生成当前数据库备份 receipt，再用第 3 节同一部署器串行发布并执行第 4 节检查。
- 若不兼容或兼容性未知：不得回滚应用到当前数据库。选择 forward fix，或按第 6 节从发布前备份恢复到新的隔离数据库，再把先前应用 digest 与该恢复库一起显式切换。

禁止浮动 tag、自动 rollback、第二个 Control Plane、数据库原地恢复和数据库 downgrade。回滚后保留失败版本 digest、当前 schema、备份 receipt、健康结果和决策人证据。

## 8. 紧急停服

环境级紧急停服按顺序停止公开入口和应用，保留数据库、网络、Secret volumes 与证据：

```sh
docker compose -f deploy/customer-demo/compose.v1.json \
  --env-file /secure/change/non-secret.env \
  -p named-customer-demo stop ingress control-plane
```

确认 HTTPS 已不可达、Control Plane/Ingress 均停止、PostgreSQL 未被重启或删除。不得使用 `down --volumes`，不得删除数据库、网络、Secret volume 或部署证据。若仅单任务失控，优先从已认证 App 发起任务“紧急停止”；若账号泄露，随后按第 5 节应急吊销。

重新开放前必须完成根因处置、备份、schema/镜像兼容性判断，并用第 3 节部署器启动；禁止直接 `start ingress` 绕过 migration、身份和健康门禁。

## 9. 恢复与收尾

1. 重跑 health/version、C10-09 协议、C10-10 恢复和受影响账号/设备隔离检查；确认仍只有 1+1 副本且无自动扩容。
2. 汇总目标、时间线、操作者、请求 ID、镜像 digest、schema revision、备份/恢复 receipt、吊销 revision、健康结果和脱敏日志；所有 Secret 值必须删除。
3. 删除本次临时 operator payload 与状态目录，按平台保留策略封存备份和旧数据库；不得删除仍在保留期的恢复证据。
4. 关闭变更单或事故单，并记录下一次恢复演练日期。任一验证未知时保持服务关闭并升级处理。

若 Customer Demo 已正式结束而不是等待恢复，不在本节直接删库或清空 volume；转到
[`Customer Demo 演示后退场与清理手册`](customer-demo-post-demo-cleanup.md)，按“冻结业务 → 吊销身份与凭据 → 停服 → 本机清理 → 持久数据和云资源退场 → 双人复核”的顺序执行。
