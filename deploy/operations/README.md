# Customer Demo account operations job

账号运维只通过 `automation-tool-account-operations` 一次性私网 job 执行，不开放 HTTP 管理面。`account-operations-job.v1.json` 固定允许命令、非 root/只读容器、单并发、stdin Secret 和审计要求；`role.sql` 创建独立 `automation_tool_operations` 登录身份，只授予十张账号、Session 和设备表的 `SELECT/INSERT/UPDATE`，不授予 DELETE、DDL、任务/RPA 数据、全 schema 或内建全库角色权限。

部署顺序是 Alembic migration → 创建/校正 operations role → 从 Secret Store 设置随机数据库密码与 `CONNECT` → 应用 `role.sql` 表权限 → 运行 job。密码、账号初始密码和 `atoc1` capability 不进入 SQL、Git、环境变量、argv 或日志；数据库 URL、Pepper 与 capability digest 使用 C10-05 固定只读文件，原始 capability 和账号密码只经有界 JSON stdin。

`reset` 签发的一次性 recovery token 是唯一允许的 Secret stdout，调用方必须只在内存中立即消费且禁止落日志；其他输出都只是 User ID、状态、revision、过期时间或吊销数量等安全投影。`emergency-revoke` 需要 User ID、expected revision 和 request ID，在单一 PostgreSQL 事务内停用账号、前移 credential version、吊销全部产品 Session 与所属设备/credential/device Session，并写 operations actor 审计。恢复账号不会复活已吊销设备。自动重试关闭；状态不确定时先查询审计与 revision，不能盲目重复命令。
