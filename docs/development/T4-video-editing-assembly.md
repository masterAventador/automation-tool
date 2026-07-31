# T4 独立剪辑生产装配

> 状态：🔍 待验收（源码生产链已闭合；待有效凭据、正式包与双平台正常用户路径）
>
> 日期：2026-07-31
>
> 提交：本文件所在提交

用户可操作：是

证据层级：分层实现 + Mock 纵向 + 真实网关失败边界；当前已发布正式包尚未包含本次改动

## 本片交付

正式 App 的独立剪辑已经从 WebView 草稿替身接到完整 native 执行链：

1. `TauriVideoEditingGateway` 用固定 IPC 保存项目与 Timeline 修订；
2. `VideoEditingWorkspace` 在任何云副作用前原子保存 `queued` 作业，再受控进入
   `running / succeeded / failed / outcome_uncertain`；
3. `VideoJobWorkspaceStore` 从统一 Artifact 库重新验摘要，并复制成私有
   `<artifactId>.<ext>` 输入；
4. Rust 从 App 私有设置读取地域、OSS Bucket 与 AccessKey，验证已签名 Executor 后，
   通过 stdin 启动 `--execute-video-editing` 一次性子进程，并用平台统一的进程树托管保证
   超时或正常退出时不遗留后代进程；
5. Executor 用生产 OSS/IMS 签名 Transport，串起既有
   `AliyunImsEditingProvider`、轮询对账、输出导入、成本/血缘与临时对象清理；
6. Rust 重新核对输出路径、摘要和大小，导入统一 Artifact 库，再落作业终态；
7. 工作台用固定 `read_video_editing_artifact` Command 读取成功产物并在预览页播放。

长期密钥只在 Rust 私有 Store 与一次性 stdin 文档中短暂出现，不进 argv、环境变量、React、
日志、Control Plane 数据库或错误文本。本机路径同样不进 WebView、错误或持久化作业 DTO。
冻结 Executor 现在显式携带 `aliyun-ims-editing-staging.v1.json`，缺契约时构建即失败。

## 持久化与故障语义

- 项目、Timeline 与作业状态是 16 MiB 有界、严格字段、UUIDv4、私有权限、原子替换的 JSON；
- App 重启时遗留的 `queued/running/paused/cancelling` 不会永远假运行，而是收敛为
  `outcome_uncertain`；
- `outcome_uncertain` 保留本机执行 Workspace 和云端临时对象，不自动重提；
- 明确失败清理暂存对象；成功导入后清理暂存与输出对象；
- 输出只有重新进入统一 Artifact 库后才允许标 `succeeded`。

当前尚未实现 App 重启后凭 vendor JobId 自动继续对账；重启中断会诚实停在
`outcome_uncertain`，不会重复提交。这是后续恢复增强，不影响正常一次性路径，但仍是 T4
保持待验收而不是标完成的原因之一。

## RED

```text
pytest test_executor_video_editing.py
  ModuleNotFoundError: automation_tool.executor.video_editing

cargo test --test video_editing_executor_child
  could not find video_editing_executor

cargo test --test video_editing_executor_child timeout_terminates_the_whole_child_process_tree
  the editing child left a descendant process running

cargo test --test video_editing_workspace
  no prepare/mark-running/settle editing job API

cargo test --test video_job_workspace stages_verified_editing_inputs
  no stage_editing_artifacts method

CQ-04 vertical readiness
  production editing dispatch missing native execution facts
```

## GREEN

```text
Backend production Transport / Executor / frozen-contract tests   21 passed
Frontend Gateway / settings / workbench / preview                 22 passed
Rust settings / workspace / child / main-thread focused suite     33 passed
CQ-04 vertical readiness                                           5 passed
Backend ruff + mypy                                                0 issue
Frontend TypeScript                                                0 error
```

另有确定性失败矩阵覆盖：非法供应商字段、路径/摘要篡改、无效密钥形状、IMS 明确拒绝、
子进程拒绝/超时/畸形输出/后代进程清理、旧设置无 OSS Bucket 的无阻塞迁移、App 中断恢复、
Artifact 篡改与配额失败。

## 真实网关结果

使用本机现有凭据文件运行了新的 opt-in T4 真实云验收。请求在任何 IMS
`SubmitMediaProducingJob` 之前停止：

```text
OSS PutObject → HTTP 403 / InvalidAccessKeyId
IMS 只读连接测试 → AuthenticationRejected
```

这两条相互印证：凭据文件存在，但当前 AccessKey 已被真实网关拒绝。没有剪辑 Job dispatch，
因此本轮没有产生剪辑计费，也没有可声称的真实云成功。测试保留为
`backend/tests/real_cloud/test_t4_video_editing_executor.py`，换入有效凭据后可直接复跑完整的上传、
提交、轮询、导入与清理链。

## 正常用户路径验收

未完成。源码路径与 Mock 纵向已闭合，但尚未：

1. 用有效阿里云凭据跑出真实成片；
2. 重建签名公证 macOS 正式包并从设置页、工作台正常点击完成一次；
3. 在 Windows x86_64 正式安装树复跑同一路径；
4. 验证 App 中断后按 vendor JobId 自动续对账，而不只停在 `outcome_uncertain`。

## 遗留项

| 项 | 状态 |
| --- | --- |
| 有效阿里云 IMS/OSS 凭据 | 当前文件被真实网关判 `InvalidAccessKeyId / AuthenticationRejected` |
| 真实云最小成片 | 待有效凭据，复跑 opt-in T4 测试 |
| macOS 签名公证正式包正常用户路径 | 待重新构建 |
| Windows x86_64 正式包同路径 | 待 Windows 环境 |
| 中断后自动续对账 | 当前安全收敛为 `outcome_uncertain`，尚无自动恢复 |
