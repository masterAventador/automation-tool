# Customer Demo PostgreSQL

本目录只定义 PostgreSQL 权限模型，不保存实例地址、账号密码、备份或客户数据。真实云实例必须位于私网，安全组只允许 Control Plane、一次性迁移作业和备份/恢复作业访问 `5432`；禁止公网 IP、`0.0.0.0/0`、本机端口映射和 trust 认证。

固定身份：

- `automation_tool_migrator`：数据库/`public` schema owner，仅由串行迁移与隔离恢复作业使用；
- `automation_tool_app`：Control Plane 运行身份，只获得既有及未来业务表的 DML、序列和必要函数执行权限；
- `automation_tool_backup`：只读备份身份，只获得业务表/序列读取权限。

`roles.sql` 只创建无内嵌密码的受限 LOGIN role。密码由云 Secret Store 生成、保存和轮换，并通过供应商的受控管理通道设置；不得写入 SQL 文件、Git、镜像、部署清单、命令参数或日志。连接客户端使用 root-only Secret 文件（例如 libpq `PGPASSFILE`）或平台原生短期身份。

固定顺序：

1. 由云管理员在私网实例执行 `roles.sql`，安全设置三个独立随机密码，并创建 owner 为 `automation_tool_migrator` 的 Demo 数据库；
2. 一次性迁移作业从 Secret Store 取得 migrator URL，执行正式 `alembic upgrade head`；Control Plane 容器启动时不迁移；
3. migrator 在目标数据库执行 `privileges.sql`，随后以 app 身份运行 `/api/v1/health`；
4. 备份作业使用 `automation_tool_backup` 执行 PostgreSQL 18 的 custom-format `pg_dump --no-owner --no-acl`，交给云端加密存储并按 C10-01 保留 7 天；
5. Demo 前把备份恢复到新的隔离实例，先建立相同固定角色，再以 migrator 执行 `pg_restore --no-owner --no-acl` 和 `privileges.sql`，核对 Alembic revision、业务计数、健康与账号只读事实；禁止覆盖原 primary 做恢复演练。

应用身份不能建表、建 schema、改角色或读取其他数据库；备份身份不能插入、更新、删除或执行迁移。任何权限检查、备份、恢复、revision 或数据核对失败都停止发布，不自动 downgrade 或切换原数据库。
