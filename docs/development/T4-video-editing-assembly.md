# T4 独立剪辑生产装配

> 状态：进行中（第一片：项目与时间线设备持久化）
>
> 日期：2026-07-31
>
> 提交：本文件所在提交

用户可操作：否

证据类型：分层实现

## 本片交付

正式 App 不再构造 `createLocalVideoEditingGateway(window.sessionStorage)`。新增
`TauriVideoEditingGateway`，六个工作台动作只允许通过固定 IPC 命令进入 Rust：

- 项目列表与创建；
- 时间线读取与保存；
- 剪辑任务列表与提交。

Rust 新增 `VideoEditingWorkspace`，在 Tauri `app_data_dir/video-editing-workspace-v1`
下原子保存 provider 中性的项目、时间线和任务快照。状态文件有 16 MiB 上限、严格字段、
UUIDv4、标题、素材去重、轨道/片段、时间范围与结果形状复验；私有目录和文件权限不符合
要求、JSON 被破坏或写入失败时均 fail closed。保存时间线沿用同一 Timeline ID、修订号单调
递增，关闭并重新打开 Store 后仍能读回。

云端提交尚未接线，本片刻意保留 `editing_service_unavailable`：不会为了让页面看起来可用而
凭空写一条 `queued` 作业。CQ-04 的静态事实门禁也同步收窄为这一条真实缺口，不会因为
`main.tsx` 已经出现 Tauri Gateway 就误报整条云剪辑完成。

## RED

```text
pnpm exec vitest run src/platform/tauri/video-editing-gateway.test.ts
  Failed to resolve import "./video-editing-gateway"

cargo test --locked --test video_editing_workspace
  could not find video_editing_workspace in automation_tool_desktop_lib
```

两条失败分别证明前端 IPC Gateway 和 Rust 持久化边界此前不存在。

## GREEN

```text
Tauri video editing gateway                 3 passed
production wiring                          14 passed（合计 17）
frontend typecheck + lint                   0 error
video_editing_workspace Rust integration    3 passed
CQ-04 vertical readiness                    3 tests OK
```

Rust 持久化测试覆盖 App Store 重开后项目/时间线仍在、修订递增、非法输入不落盘，以及云端
Adapter 接线前提交不伪造作业。前端测试覆盖六条固定命令、strict DTO 与封闭错误脱敏；
`production-wiring.test.ts` 已把 `videoEditingGateway` 纳入真实 Tauri 装配清单。

## 正常用户路径验收

未完成。本片是 T4 的设备持久化与 IPC 分层实现；尚未构建新的正式包，也没有把用户操作送到
真实阿里云 Provider，因此不声称“从正式 App 提交剪辑”可用。最终用户判据仍是：从工作台
提交后真实云端成片回流为新 Artifact，并能进入成片列表和发布链路。

## 遗留项

1. 设计并实现 Control Plane 剪辑应用服务、项目/时间线/任务仓储与固定 API。
2. 由受控本机边界读取长期凭据和输入 Artifact，完成同地域 OSS 暂存，不向 React、日志或
   Control Plane 数据库暴露密钥与本机路径。
3. 装配已有 `AliyunImsEditingProvider`、持久意图、轮询对账、成片导入与清理实现。
4. 构建正式包，从正常入口完成真实云任务、App 重启恢复、成片回流与发布交接验收。
