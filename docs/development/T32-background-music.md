# T32 智能素材成片：移除做不到的背景音乐选项

> 状态：✅ 已完成
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：用户试用「智能素材成片」时反馈背景音乐三个选项全都没有声音。
> 前置事实见 `docs/development/FIX-material-worker-audio-assets.md` 的「仍缺的验收」，
> 那里已把「三个背景音乐选项现在实际都等价于无背景音乐」登记为未处理的产品可见后果，
> 并建议按缺陷单独立项。本任务就是那一项。

## 1. 现状核实

### 1.1 三个选项是谁渲染的

`vendor/moneyprinterturbo/webui/Main.py:2301 _render_background_music_settings`，
由 `:2744` 在音频设置面板内无条件调用。选项是写死的三项，默认值是 `random`：

| 界面文案 | 取值 | 默认 |
| --- | --- | --- |
| 无背景音乐 | `""` | |
| 随机背景音乐 | `"random"` | ✅ 默认 |
| 自定义背景音乐 | `"custom"` | |

下面还有一个「背景音乐音量」选择器（`bgm_volume_select`），选「自定义背景音乐」时
额外出现一个文件名输入框（`custom_bgm_file_input`）。

**不是我们自己的界面层渲染的**，`frontend/src/features/video-studio/` 里没有任何
背景音乐控件；用户看到的是内置 WebUI，即 App 从「打开完整制作界面」打开的那个窗口。

### 1.2 选中之后实际发生什么

三条路径全部走到「没有音乐」，**没有报错，也没有任何提示**：

| 选项 | 代码位置 | 实际行为 |
| --- | --- | --- |
| 无背景音乐 | `app/services/video.py:507` | `if not bgm_type: return ""`，本来就是无音乐 |
| 随机背景音乐 | `app/services/video.py:529-536` | `glob("*.mp3")` 取空 → 记一条 WARNING → `return ""` |
| 自定义背景音乐 | `app/services/video.py:510-527` | 解析限制在 `resource/songs` 内，目录是空的 → `ValueError` → 被捕获 → 记 WARNING → `return ""` |
| 消费端 | `app/services/video.py:1163-1166` | `if bgm_file:` 才建 `AudioFileClip`，空字符串直接跳过混音 |

即：**静默跳过**，不是报错被吞掉。日志里那条 `no bgm files found in song directory`
就是唯一痕迹，用户看不到。

### 1.3 包里还有没有音乐资源

**没有，一个都没有。** `contracts/quality/material-video-worker-package.v1.json` 的
`build.excludedUpstreamResources` 列了 `songs`，理由写明 29 个上游背景音乐没有再分发授权。
`workers/material_montage/material-video-worker.spec:95` 在打包时按这个清单跳过整个目录，
`scripts/test_material_video_worker.py` 里有专门的门禁拒绝带 `songs` 的候选包。

上游 checkout（`vendor/moneyprinterturbo/resource/songs/`）里那 29 个 mp3 仍然在，
但那是只读 Submodule 的内容，**不进包**，用户机器上没有。

运行时 `resource/songs` 目录还是会被上游 `utils.song_dir()` 在任务私有目录里创建成空目录，
而该目录随任务创建、随任务销毁，用户也没有正常途径往里放文件——所以「自定义背景音乐」
即使填对文件名也读不到东西。

## 2. 修法与理由

### 2.1 为什么不能用上游配置关掉

查过了，**上游没有这个配置项**：

- `config.example.toml` 的 `[ui]` 只有 `hide_log`；`[app]` 只有 `hide_config`，
  而且 `Main.py:1670` 把 `hide_config` 强制写回 `False`，它历史上也只用于隐藏旧的基础设置面板；
- `_render_background_music_settings` 是无条件调用，没有任何 feature flag、环境变量或钩子；
- 选项列表写死在函数体里，`stable_selectbox` 也不从配置读取候选项。

所以「通过上游正式配置关掉」这条路走不通。

### 2.2 选定的修法：私有运行时样式覆盖

在**我们自己的适配层**移除这组控件，并在原位说明当前版本的边界。

落点是 `workers/material_montage/webui_runtime.py` 的 `_prepare_private_project`：
它本来就把上游 `webui/` 复制成一份私有运行时项目，上游 `Main.py:63-65` 会读取该目录下的
`styles.css` 并注入页面。于是把上游样式原文保留、在末尾追加一段覆盖规则：

- `bgm_volume_select`、`custom_bgm_file_input` 整个 `display: none`；
- `bgm_type_select` 容器的子元素隐藏，容器 `::after` 渲染一句
  **「当前版本不提供背景音乐素材，成片不会添加背景音乐。」**

这跟同一个文件里既有的 `_private_config_document` 是同一类做法：**取上游文档原文、
追加我们的值、写进私有运行时根目录**，上游 Submodule 一个字节都没改。
挂钩用的 `st-key-<key>` 类名也不是我们发明的，上游自己的 `styles.css` 已经用它做了
6 处布局和隐藏（例如 `st-key-task_table_header_`）。

### 2.3 为什么不是别的方案

- **改上游 `Main.py`**：项目规则明令禁止改第三方源码 / Monkey Patch / 私有 Fork，
  `vendor/moneyprinterturbo` 是只读 Submodule；
- **改我们的界面层**：控件不在我们的界面层，改不到；
- **补一份有授权的音乐**：见下面「需要用户拍板」，属合规决定，本任务不做；
- **只把默认值改成「无背景音乐」**：另外两个选项仍然留在界面上、仍然点了没反应，
  没有解决「界面提供做不到的事」这个核心问题。

## 3. TDD 证据

新增 `scripts/test_material_video_worker.py::MaterialVideoWorkerBackgroundMusicTest`，
5 条用例：包里确实没有音乐、上游仍在用这三个 widget key、三个控件都被移除、
说明文案在位、上游样式逐字保留。

### RED

先写测试、后写实现。RED 是**断言失败**，不是导入或编译失败——
用例只 import 已存在的 `_prepare_private_project`，跑的是它真实产出的私有样式表：

```text
$ python3 -m pytest scripts/test_material_video_worker.py -k BackgroundMusic -q
.FF..
FAILED ...::test_private_project_removes_every_background_music_control
  AssertionError: 'st-key-bgm_type_select' not found in '...'
FAILED ...::test_private_project_states_that_this_release_has_no_background_music
  AssertionError: '当前版本不提供背景音乐素材，成片不会添加背景音乐。' not found in '...'
2 failed, 3 passed, 45 deselected
```

### GREEN

```text
$ python3 -m pytest scripts/test_material_video_worker.py -k BackgroundMusic -q
5 passed, 45 deselected in 0.06s

$ python3 -m pytest scripts/test_material_video_worker.py -q
49 passed, 1 skipped in 0.10s
```

## 4. 真实验收（真实 WebUI，正常渲染路径）

按锁定版本 `uv sync --project vendor/moneyprinterturbo --locked --no-dev --no-install-project`
建独立运行环境（Python 3.11.15 / Streamlit 1.59.1），走**产品同一条适配代码路径**
（`serve_webui` → `_preload_private_config` → `_prepare_private_project` → Streamlit），
用无头浏览器打开真实页面核对。

**修改前**（同一条路径，未改代码）：

```text
combobox "背景音乐来源" [expanded=false]: 随机背景音乐
combobox "背景音乐音量" [expanded=false]: 20%
```

**修改后**：

```json
{"bodyHasMusicLabel": false,
 "rows": [
  {"key": "st-key-bgm_type_select_zh", "display": "block", "rendered": "",
   "afterContent": "\"当前版本不提供背景音乐素材，成片不会添加背景音乐。\"", "visibleHeight": 42},
  {"key": "st-key-bgm_volume_select_zh", "display": "none", "rendered": "背景音乐音量",
   "visibleHeight": 0}]}
```

- 可访问性树里「背景音乐」条目数 **0**（改前 2 个 combobox），音频面板从「试听配音」
  直接接到字幕设置；
- 页面正文不再出现「背景音乐来源」「背景音乐音量」；
- 说明文案实际渲染出来，高度 42px，肉眼可见（已截图核对后按规范删除截图）。

**与产品窗口叠加验证**：把产品真实的 `frontend/src-tauri/src/material_video_studio_init.js`
原样注入同一页面（产品窗口就是用它做初始化脚本），结果：

```json
{"state": "ready", "failure": null, "title": "智能素材成片",
 "notice": "\"当前版本不提供背景音乐素材，成片不会添加背景音乐。\"",
 "noticeHeight": 42, "bodyHasMusicLabel": false}
```

主题守卫没有 fail closed，说明文案照常显示，两层没有互相打架。

**没验到的一步**：没有在重新冻结的 PyInstaller 包和正式 `.app` 里跑一遍。
本次验收用的是同一份适配源码 + 同一版上游 + 同一版 Streamlit，但不是用户最终拿到的那个产物。
冻结包里 `upstream/webui/styles.css` 与 `_prepare_private_project` 的行为跟源码路径一致
（`material-video-worker.spec:114` 整目录打包），重新构建即可覆盖；
本次未重建是因为另一条工作线正在同一份正式包上跑素材成片链路验收，不能中途换包。

## 5. 失败矩阵

| 情况 | 期望行为 | 本次证据 |
| --- | --- | --- |
| 上游 `styles.css` 缺失 | `_prepare_private_project` 抛 `WebUiRejected("upstream WebUI stylesheet unavailable")`，不静默跳过 | 实现内显式判定 |
| 上游改掉 widget key | 覆盖层失效、选项回到界面 → 用例 `test_upstream_still_uses_the_widget_keys_the_overlay_targets` 变红 | 用例已建 |
| 上游样式被覆盖层吞掉 | 上游规则逐字保留，覆盖层只追加在末尾 | `test_private_project_keeps_every_upstream_rule` |
| 以后补了音乐但忘了恢复选项 | `test_release_ships_no_background_music_at_all` 会红，强制一起处理 | 用例已建 |
| 产品主题脚本与覆盖层冲突 | 主题 fail-closed 守卫不触发，文案照常渲染 | 上面叠加验证 |

## 6. 清理

- 本次验收启动的 Streamlit 子进程、WebUI 父进程、无头浏览器全部已停止并复核无残留；
- 临时截图看完即删；
- 依赖环境、运行目录全部在会话 scratchpad 内，未写入仓库，未改 `.local/`；
- 未新增任何废弃属性或常量；覆盖层用到的三个 widget key 与文案都是单点定义。

## 7. 需要用户拍板（合规，未处理）

**技术上「把音乐补回来」这条路是通的，但这是授权问题，不是技术问题，本任务按要求没做。**

现状：

- `contracts/quality/asset-rights-policy.v1.json` 的 `entries` 里**没有任何 `music_sfx` 条目**，
  只有两个 Noto 字体，默认判定是 `deny`，所以现在包里一段音乐都不能带；
- 但 `contracts/quality/motion-asset-overlay.v1.json` 里已经有 **4 个 `music_sfx` 类别的音频**
  （`assets/motion-catalog-overlay/audio/*.wav`），来源写的是 `project-self-authored`，
  由 `scripts/build_motion_asset_overlay.py` 自己合成，
  `redistributionAllowed` / `commercialUseAllowed` / `syncUseAllowed` 全是 `true`，
  `contentIdRisk` 是 `none-self-authored`。

也就是说：**我们已经有一条自己合成、权利干净的音频生产路径**，用它给智能素材成片补一套
背景音乐在合规上没有障碍。但那 4 个文件是 1-2 秒的动效音效（39-79 KB），不是能铺满
30-60 秒成片的背景音乐，直接拿来用会很难听，需要单独生成一批更长的环境音乐并登记
`asset-rights-policy` 的 `music_sfx`。

**要不要做、做几首、什么风格，请用户决定。** 一旦补上，本任务的覆盖层要一起撤掉，
上面的失败矩阵里已经埋了会变红的用例强制这件事被一起处理。
