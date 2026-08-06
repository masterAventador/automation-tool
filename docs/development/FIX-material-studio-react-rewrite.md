# FIX：素材成片改为页面内 React 表单，替代上游 Streamlit WebView 浮层

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：worker 层 e2ac71de / Rust 层 a4f4f38c / React 层 51703793（各层独立提交，
> 每层先红后绿）
>
> 类型：用户实测问题的重写修复（不改任何 roadmap 任务状态）

## 缺陷（用户 2026-08-05 实测报告）

素材成片的制作界面是上游 moneyprinterturbo 的 3,219 行 Streamlit UI，只能存在于一块
原生 WebView 覆盖层里：浮在页面之上、靠 JS 逐帧追坐标、无法融入页面布局——「整页
点不动」（T108）与本次「悬浮的丑面板」都来自它。用户明确决定：用 React 重写界面，
直接调上游的服务层，Streamlit 不再进产品。

## 设计（三层，全部复用既有通道）

- **worker**：`montage_runtime.py` 把表单参数（主题/可选讲稿/比例/片段时长/配音/
  字幕样式）严格校验后构造上游 `VideoParams`，在与 WebUI **完全相同**的私有运行时
  布局下直驱 `app.services.task.start` 整条流水线——同一 `_preload_private_config`
  （字幕字体 + Pexels key）、同一模型路由、同一观察桥，`runtime_root` 深度一致所以
  台账投影、取消与产物读取一行未改。请求随已鉴权 stdin 引导文档到达
  （`montageRequest`，与 `enableWebUi` 互斥），一个任务一个 worker 进程，流水线结束
  进程自动退出释放编排器槽位；
- **Rust**：`VideoWorkerMontageRequest` 边界校验与 worker 逐条镜像（变异验证：放宽
  clip 边界测试即红）；`submit_montage` 复用交互式工作室全部生命周期，只是没有
  WebView；`runtime_directory` 扫描 webui/montage 两个运行时父目录；
- **React**：素材 tab 渲染产品自己的表单，提交走 `submitMaterialMontage`，
  `EmbeddedMaterialStudio`、坐标同步、view 错误表与孤儿样式整体删除。

## RED / GREEN（分层）

```text
worker  4 条新用例先红（解析矩阵/引导互斥/参数映射/preload 收到 key）
        → scripts/test_material_video_worker.py 80 passed
Rust    montage 校验 2 条 + 变异验证真红真绿 → cargo test --lib 171/171
React   表单提交用例先红（选中素材方式后应有表单而非 WebView）
        → 前端全量 736/736、整仓 eslint --max-warnings 0、tsc -b、vite 生产构建
门禁    check_user_facing_branding 60 frontend / 317 native 通过
```

## 真实边界（重要，如实记）

- **端到端真实出片尚未验证**：完整流水线（真模型、真 Pexels 下载、真 ffmpeg 合成）
  只存在于打包后的冻结环境，单元层通过 `pipeline` 测试缝覆盖参数映射。**必须在下一次
  出包后从正式 App 表单提交一条真任务并核对产物文件**，在此之前本重写最多算分层完成；
- **WebView 机器仍在代码里**：Rust `open/updateView/close`、worker `start_webui`、
  网关 HTTP 面与多个验收脚本仍引用旧路径。产品 UI 已不再调用它们；按删除规范应当
  清除，但涉及 EB/BM 多条验收链的改动量属于独立任务，**登记为遗留而不是悄悄留着**；
- 配音音色为四个 edge-tts 中文音色的固定清单；颜色/描边当前用固定默认值，表单后续
  按需要再开放。

## 清理

- `EmbeddedMaterialStudio`、`materialStudioView`、`MATERIAL_STUDIO_ERRORS`、
  `.material-video-studio-embedded` 样式块删除；`useRef` 等孤儿导入清除；
- 顺带修正一个流程记录：此前几轮曾用 `npx stylelint` 验样式，本项目根本没有配置
  stylelint，那个绿是空转——样式判据以 eslint/tsc/vitest/使用点 grep 为准。

## 遗留项

| 项 | 状态 |
| --- | --- |
| 正式包上从表单提交一条真任务并核对出片 | 待下次出包（与 V4 的 key 验证合并做） |
| WebView 机器（Rust open/close、start_webui、网关 HTTP 面、相关验收脚本）删除 | 独立任务，未开始 |
