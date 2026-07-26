# T36 一句话生成视频的 App 内闭环与本地预览

> 状态：🚧 部分完成 —— 「智能素材成片」的成片在 App 内可预览已实现并通过分层门禁；
> **一句话生成链路的真实 App 用户路径验收未取得，且查出一个产品级阻塞（素材源 Key 缺失）**。
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 前序记录：`docs/development/T36-oneshot-video-preview.md`（同一任务的上一条工作线，
> 交付了成片页「去发布」并留下未完成清单）。本文件是本条工作线的执行证据，不重复它的内容。
>
> 触发：客户 Demo 底线是「一句话生成视频能做完，并且可以本地预览」。

## 摸底结论（先查现状，不假设）

上一条工作线只查了**品牌动效线**（hyperframes），把结论写成了整体。实际有两条线，情况完全不同。

| 问题 | 事实 | 出处 |
| --- | --- | --- |
| 智能素材成片有没有「一句话生成」？ | **有，而且是上游现成能力。** `generate_script(video_subject)` → `generate_terms` → 搜素材 → 合成，全在上游 WebUI 里；我们通过 `open_material_video_studio` 启动上游自己的 Streamlit WebUI，用户在其中操作 | `vendor/moneyprinterturbo/app/services/llm.py`、`workers/material_montage/webui_runtime.py` |
| 文案模型是不是预置好的？ | **是。** 不是靠上游 config.toml，而是 Rust 侧 `material_video_script_model()` 读「设置与诊断」里配置过的模型，经 stdin 一次性喂给 Worker，再用 `install_script_model` 替换掉上游的 `llm._generate_response`。密钥不进 argv/env/日志 | `webui_runtime.serve_webui`、`model_service_adapter.py` |
| 素材源 Key 是不是预置好的？ | **不是，而且这是当前的硬阻塞。** `_preload_private_config` 逐字写入上游 `config.example.toml`（只钉住字幕字体），其中 `pexels_api_keys = []`。上游 `get_api_key` 在空列表时直接 `raise ValueError` | `workers/material_montage/webui_runtime.py:227`、`vendor/moneyprinterturbo/app/services/material.py:37` |
| ffmpeg 是不是从包内解析？ | **是。** `material_worker_launch` 把包内 `IMAGEIO_FFMPEG_EXE` 显式注入 Worker 环境，正是为了不让上游回落到本机 `PATH` 上的 ffmpeg | `material_video_studio.rs:545`、`video_media_toolchain.rs:186` |
| 成片能不能回到我们自己的成片页？ | **能。** 观察桥把上游任务终态复制成 `outputs/material-result.mp4`，Rust `reconcile_active_observation` 按 `rendered_video` 角色导入为 App 自有 Artifact | `job_observation_bridge.py`、`material_video_studio.rs:267` |
| 成片能不能在 App 内预览？ | **不能——这是本次修掉的洞。** 成片页只给品牌动效成片放了播放器；智能素材成片的卡片只有「去发布」和「删除成片」，而且**根本没有对应的取件命令**（全仓无 `read_material_video_artifact`） | 本次改动前的 `VideoStudio.tsx`、`lib.rs` 三处 `invoke_handler` |
| 品牌动效线的一句话入口 | 仍然不存在（`submit_motion_video_brief` / `one_sentence_v1` 全仓无），编排代理仍在 `tools/` 下未进执行器包。本次未动 | `tools/motion-authoring/` |

据此本次把力气放在**智能素材成片的本地预览**上：它是 Demo 底线里我们真正欠的那一半，
而且不依赖任何尚未打通的编排链路。

## RED

```text
cd frontend && npx vitest run src/features/video-studio/VideoStudio.test.tsx \
                             src/platform/tauri/material-video-studio-gateway.test.ts

  × plays a finished smart-material video inside the App
      Unable to find role="button" and name "播放知识讲解"
  × reads a finished smart-material artifact through its own narrow command
      TypeError: gateway.readMaterialArtifact is not a function
  × refuses a smart-material artifact id that is not a UUID v4
      TypeError: gateway.readMaterialArtifact is not a function

  Test Files  2 failed (2)
       Tests  3 failed | 27 passed (30)
```

```text
cd frontend/src-tauri && cargo test --test material_video_artifact
  error[E0432]: unresolved import `automation_tool_desktop_lib::material_video_studio::read_artifact`
```

三条前端 RED 是断言/运行期失败，不是编译失败——工作树在这段时间照常编译，
不会连带打断并行工作线。Rust 那条只影响新增的这一个测试二进制，`lib` 本身始终可编译。

## GREEN

```text
cd frontend/src-tauri && cargo test --test material_video_artifact       3 passed
cd frontend/src-tauri && cargo test --test motion_video_studio \
      --test material_video_gateway --test publish_artifact_handoff \
      --test video_job_workspace                                         30 passed
cd frontend/src-tauri && cargo test --tests -- --test-threads=4          41 个测试二进制 / 379 passed / 0 failed

cd frontend && npx vitest run src/features/video-studio \
      src/platform/tauri/material-video-studio-gateway.test.ts src/app
  Test Files  11 passed (11)     Tests 108 passed | 1 expected fail (109)

cd frontend && npx vitest run
  Test Files  59 passed (59)     Tests 486 passed | 1 expected fail (487)

cd frontend && npx tsc -b        exit 0

python3 scripts/check_user_facing_branding.py    passed
python3 scripts/cq_04_ledger_honesty.py          exit 0
```

`expected fail` 那条是既有的 `videoEditingGateway`，与本次无关。
`cargo` 全量 379 条全绿，其中 3 条是本次新增的 `material_video_artifact`。

## 交付

### 智能素材成片的成片可以在 App 内直接播放

- Rust 新增 `read_material_video_artifact` 命令 → `material_video_studio::read_artifact`，
  并在**三个** `invoke_handler`（`desktop-e2e` / 生产 / `control-plane-e2e`）里同时注册。
  只注册一处会让某些构建里按钮点了没反应，而单元测试看不出来。
- 取件逻辑没有复制一份：两种制作方式导入的是同一种 Artifact（同 `rendered_video` 角色、
  同 `video/mp4`），所以「按 id 找记录 → 拒绝过大的 → 校验并编码」下沉成
  `VideoJobWorkspaceStore::read_rendered_video_artifact`，动效线的 `read_artifact` 改为委托它。
  尺寸上限 `MAX_RENDERED_VIDEO_READ_BYTES` 和角色/媒体类型都用工作区已有的常量，不再各写一遍。
- 载荷类型也只留一份：`MotionVideoArtifactPayload` 更名为 `RenderedVideoArtifactPayload`
  （Rust 与 TS 同步），因为它现在同时服务两种制作方式，继续叫 Motion 会误导。
  全仓 grep 过消费方，`platform/tauri/`、`features/video-studio/`、`app/WorkbenchShell.tsx`
  三处全部同步，`tsc -b` exit 0。
- 网关侧 `readMotionArtifact` / `readMaterialArtifact` 共用一个私有
  `readRenderedVideo(command, id)`：两条命令的失败词汇不同所以命令分开，但 UUID 校验、
  载荷白名单和错误映射是同一件事，写一次。
- 成片页只有一个播放器、一条失败提示和一个 `play(subject, read)` 入口，两种制作方式共用。
  预览仍走既有 base64 `data:` URL，**没有新增 Tauri capability、没有改 CSP、没有开放文件系统**。

## 真实边界（生产同路径验收）

**结论：真实 App 用户路径验收未取得。** 本次预览功能拿到的是分层证据
（Rust 集成测试 + 组件/网关单测），按 CLAUDE.md 第 8 节，这只能证明状态机与 UI 投影，
不能证明桌面链路可用。

不过本次对**素材线的一句话生成**做了一次分层实跑（下一节），拿到了确定结论：
包里的完整制作界面能起来、一句话文案能生成、卡在素材源 Key。
即使把 App 验收环境搭好，生成这一步也会停在同一处。**不把分层证据说成验收通过。**

### 实测：正式包里的完整制作界面能起来，一句话文案能生成，卡在素材源 Key

这一段是**实跑结果，不是读代码的推断**。跑的是签名公证线今天 11:56 产出的正式包
（`.local/customer-demo-release/verify/.../自动化运营工具.app`）里的冻结 Worker，
按 `webui_runtime._child_command` 的原样命令启动，并注入包内 `IMAGEIO_FFMPEG_EXE`。

**分层说明（重要）**：本次是直接调用 Worker 的 `--serve-webui` 入口，
没有经过 `open_material_video_studio`，因此**不是生产同路径验收**，只是分层探针。
它回答的是「包里那套上游 WebUI 到底能不能用」，不能替代真实 App 用户路径验收。

按顺序拿到的结果：

| 步骤 | 结果 |
| --- | --- |
| 冻结 Worker 启动 + Streamlit 健康检查 | ✅ `health=200`、页面 `200`。用户几天前 dogfood 时的「视频制作服务无法启动」在这个包上不再复现 |
| 私有配置预置 | ✅ 写出 `config.toml`，字幕字体已钉成 `NotoSansCJKsc-Bold.ttf`（合规换字体的那次改动确实生效） |
| 文案模型注入 | ✅ 输入一句话「用三个要点介绍我们的新品上线」，点「使用AI生成视频文案和关键词」，**真实百炼模型返回了完整中文文案与英文关键词**。密钥经 stdin 一次性注入，未进 argv/env |
| 点「生成视频」 | ❌ **`请先填写 Pexels API Key`**，任务根本没有创建（Worker 日志无 start task、`storage/tasks` 为空） |

失败点在上游 `webui/Main.py:3030` 的出发前校验：`video_source == "pexels"` 且
`pexels_api_keys` 为空时直接 `st.error` + `st.stop()`。这不是我们的代码坏了，
是我们预置的上游配置里没有素材源 Key，而默认素材源就是 Pexels。

顺带看到一件必须记下来的事：这个窗口是用户从「智能素材成片 → 打开完整制作界面」
打开的产品窗口，页面顶部整幅显示 **`MoneyPrinterTurbo v1.3.2`**。按 CLAUDE.md 第 6 节，
上游项目名禁止进入用户可见界面。`scripts/check_user_facing_branding.py` 只扫我们自己的文件，
扫不到内嵌的上游 WebUI，所以这条一直是绿的。**客户 Demo 当场就会看到这个名字。**

### 顺带查清的一件事：验收环境比上一条工作线判断的要便宜

上一条记录说视频线要跑起来「还差编译期动作信任三元组、可达 Control Plane、签名执行器包」，
并把这套 harness 当成要新发明的东西。实际不必发明：

- `scripts/desktop_e2e_prerequisites.py` 已经提供 `startup_gate_environment()`（编译期三元组
  + Control Plane origin）和 `prepare_startup_gate()`（内置浏览器装配 + 签名执行器包缓存与安装），
  `run_h8_16f_acceptance.py` 就是按这套跑到 spec 的；
- `control-plane-e2e` 这个 feature 的 `invoke_handler` **已经同时包含全部视频命令与全部发布命令**
  （`open_material_video_studio`、`submit_motion_video_draft`、`read_motion_video_artifact`、
  本次新增的 `read_material_video_artifact`、`get_publish_workspace`、`begin_publish`…），
  所以上一条记录担心的「即便环境修好，去发布也只能验一半」在 `control-plane-e2e` 系构建上不成立。

也就是说，一句话生成 → 进度 → 预览 → 去发布 的完整用户路径验收，应当建在
`control-plane-e2e` 构建上，而不是去修 `video-studio-e2e` 那条。这条判断本次没有实施，
留给下一条工作线，见「未完成」。

## 失败矩阵

| 场景 | 行为 |
| --- | --- |
| 成片在点击播放前被删掉 | `NotFound` → `job_unavailable`，界面提示「暂时无法读取这条成片」，不报存储故障 |
| 成片大于 32 MB | `QuotaExceeded` → `job_unavailable`，不把整条视频读进内存，也不给出误导性的存储错误 |
| Artifact 存在但角色不是 `rendered_video` | 拒绝读取（有专门用例守住），播放器不会被喂到工作区里的其他文件 |
| 磁盘上的 Artifact 与清单不一致（被替换/截断/软链） | `open_artifact` 的逐字节校验失败 → `StorageUnavailable`，不播放未经校验的字节 |
| 正在忙（busy） | 播放按钮禁用，避免连点在切换途中改写播放源 |
| 同一页里多条成片 | 按钮无障碍名称带片名（`播放{片名}`），不会播错一条 |
| 外壳没有装配视频网关 | `WorkbenchShell` 的兜底网关明确抛错，不返回空数据假装成功 |

## 清理

未启动 App、未启动 Control Plane、未起 Docker 容器。实跑探针启动过两次冻结 Worker
（loopback 18901 / 18902，开跑前确认空闲）和一个无头 Chromium；跑完全部结束并核对：
`automation-tool-material-video-worker` 0 个、`agent-browser-chrome` 0 个、两个端口均已释放。
探针工作区、日志和三张截图用完即删，仓库内无残留文件。
全程未运行 `scripts/run_u9_06_acceptance.py`，未写入
`~/Library/Application Support/com.aventador.automationtool/`（只读列目录核对过一次现状：
用户手工扫码的抖音 Profile 与凭据完好，`video-workspaces-v1` 下 3 个空工作区、0 个 Artifact，
与「此前 dogfood 没做出过成片」一致）。

未触碰其他工作线占用的文件：`deploy/`、`scripts/release_assembly.py`、
`scripts/build_release_package.py`、`frontend/e2e-tauri/`。提交按文件逐个 `git add`，
未使用 `git add -A`，工作区内云端部署线与签名公证线的未提交改动原样留存。

## 未完成（下一条工作线的输入）

按阻塞程度排序：

1. **素材源 Key（产品级阻塞，不是代码问题，已实跑确认）**：默认素材源是 Pexels，
   而 `pexels_api_keys` 在我们预置的配置里是空的，点「生成视频」当场被上游拦下
   （`请先填写 Pexels API Key`），任务不创建。免费注册即可
   （<https://www.pexels.com/api/>），拿到后可以在完整制作界面的配置面板手填，
   或者由我们预置。**这件事解决之前，素材线的一句话生成在任何环境下都做不出视频。**
   如果决定预置，注意两点：素材源 Key 属于凭据，应走 `secure_store` 而不是明文写进
   `config.toml`；另外上游 `material.get_api_key` 在 Key 为空时会把整个 `config.app`
   序列化进异常文本，含其他已填 Key，这条泄漏面要一并处理。
2. **上游品牌名出现在用户可见窗口（合规问题，已实测看到）**：完整制作界面顶部整幅显示
   `MoneyPrinterTurbo v1.3.2`，违反 CLAUDE.md 第 6 节。现有
   `check_user_facing_branding.py` 只扫我们自己的文件，扫不到内嵌上游 WebUI。
   Demo 之前需要处理（上游 `webui/Main.py` 是只读 Submodule，只能按
   `_prepare_private_project` 已有的私有副本机制在装配期替换标题，或用注入的样式遮蔽）。
3. **真实 App 用户路径验收**：按上面「真实边界」的判断，在 `control-plane-e2e` 上新建一条
   驱动（tauri conf + wdio conf + spec + `scripts/run_*_acceptance.py`），走
   打开 App → 视频制作 → 智能素材成片 → 打开完整制作界面 → 一句话生成 → 成片页播放 → 去发布。
   新增 spec 会落在 `frontend/e2e-tauri/`，本次作业面之外，需要先与占用该目录的工作线对齐。
4. **品牌动效线的一句话入口**：执行器承载编排代理 → `submit_motion_video_brief` →
   前端 `one_sentence_v1`。素材线通了之后它是加分项，不是 Demo 阻塞项。
