# FIX 两条视频线的包内 ffmpeg 真正接到 Worker 启动

- 日期：2026-07-26
- 范围：`frontend/src-tauri/src/`、`frontend/src-tauri/tests/`
- 提交：本次子任务不执行 `git add` / `git commit`，改动随主会话统一提交
- 关联：`docs/development/FIX-material-worker-package-size.md` 第 5 节第 3 条（缺陷发现过程）、
  `docs/development/FIX-video-runtime-release-assembly.md`（同期的构建期单路径收敛）

## 缺陷

`frontend/src-tauri/src/video_media_toolchain.rs` 早就把包内 ffmpeg/ffprobe 逐文件校验好了，
并给出两条线各自的环境变量映射：

- `intelligent_material_environment()` → `IMAGEIO_FFMPEG_EXE`（智能素材成片）
- `brand_motion_environment()` → `HYPERFRAMES_FFMPEG_PATH` / `HYPERFRAMES_FFPROBE_PATH`（品牌动效成片）

但这两个函数**全仓库只有定义本身与单元测试引用，零生产调用点**；`VideoWorkerLaunch` 也根本没有
向子进程注入环境变量的能力。于是两条线在真机上的实际解析顺序是：

```text
智能素材成片（Python，上游 app/utils/utils.py:get_ffmpeg_binary）
1. IMAGEIO_FFMPEG_EXE      → 未设置，跳过
2. shutil.which("ffmpeg")  → 命中用户机器上的系统 ffmpeg（Homebrew 用户就是它）
3. imageio_ffmpeg 自带     → 才轮到包内那份
4. 字面量 "ffmpeg"

品牌动效成片（Node，上游 packages/parsers/src/ffBinaries.ts:findFfBinary）
1. HYPERFRAMES_FFMPEG_PATH / _FFPROBE_PATH → 未设置，跳过
2. which / where
3. 手工扫 PATH（含 Windows PATHEXT）
4. 项目本地 .hyperframes/bin
5. /opt/homebrew/bin、/usr/local/bin、/usr/bin、/bin、/snap/bin  ← 即使 env_clear 也照样命中
```

后果：装了 Homebrew ffmpeg 的用户，产品用的是他系统那份——版本、编解码器、GPL 合规全部失控，
直接违反 CLAUDE.md 第 5 节「不发现、不选择、不下载或回退到系统浏览器/组件」。

补充说明（本次核实的现状，不改变缺陷成立）：品牌动效线的 MP4 编码这一步是 Rust
`lib.rs::encode_motion_video` 直接用包内绝对路径 spawn 的，那一步没有回退；缺的是 Worker 进程
本身的环境。Worker 一旦有任何一处走上游 ff 解析（现在的 `workers/motion_composition/worker.mjs`
只驱动 Chromium，不碰 ffmpeg），就会掉进上面第 5 档。所以这次按契约
`contracts/video/ffmpeg-toolchain.v1.json` 的 `worker_environment` 把两条线都接上，属于失败关闭。

## RED

先写测试，实际运行看到失败（`cargo test --test video_media_toolchain --test local_video_orchestrator`）：

```text
error[E0425]: cannot find function `material_worker_launch` in module `material_video_studio`
error[E0603]: function `motion_worker_launch` is private
error[E0599]: no method named `with_environment` found for struct `VideoWorkerLaunch` in the current scope
error[E0061]: this function takes 4 arguments but 5 arguments were supplied
error[E0599]: no method named `with_environment` found for struct `VideoWorkerLaunch` in the current scope
error: could not compile `automation-tool-desktop` (test "video_media_toolchain") due to 3 previous errors
error: could not compile `automation-tool-desktop` (test "local_video_orchestrator") due to 2 previous errors
```

用例条数（新增 4 条，不是「加了测试但条数没变」）：

| 测试文件 | 改前 | 改后 |
| --- | --- | --- |
| `tests/video_media_toolchain.rs` | 6 | 9 |
| `tests/local_video_orchestrator.rs` | 20 | 21 |
| `tests/single_build_path.rs` | 7 | 7（清单从 3 个 resolver 扩到 5 个） |

### 变异验证：证明测试断的是「注入真的生效」，不是「函数被调用了」

这个缺陷的形态就是「函数写好了没人调用」，所以两条正向用例都从**被启动的子进程内部**把
`os.environ` 落盘成 marker 再断言。为确认断言有效，做了两次变异（验证后已还原）：

变异 1：删掉 `spawn_worker` 里的注入循环（`command.env(name, value)`）——两条线同时失败，
且失败原因正是「子进程环境里没有这个变量」：

```text
---- the_smart_material_worker_process_receives_the_packaged_ffmpeg stdout ----
assertion `left == right` failed: the smart-material Worker resolves FFmpeg from this variable
first and falls back to the user's own installation when it is missing
  left: None
 right: Some(".../media-toolchain/bin/ffmpeg")

---- the_brand_motion_worker_process_receives_the_packaged_pair_and_nothing_else stdout ----
assertion `left == right` failed
  left: None
 right: Some(".../media-toolchain/bin/ffmpeg")
test result: FAILED. 7 passed; 2 failed
```

变异 2：只删掉 `material_worker_launch` 里的 `.with_environment(...)`——素材线用例与接线守卫
同时失败，动效线用例保持通过（说明两条线互相独立、断言没有串味）：

```text
test every_video_worker_launch_carries_the_packaged_environment_from_its_entry_point ... FAILED
test the_smart_material_worker_process_receives_the_packaged_ffmpeg ... FAILED
test the_brand_motion_worker_process_receives_the_packaged_pair_and_nothing_else ... ok
```

## GREEN

```text
cargo test
  39 个 "test result" 段全部 ok，合计 347 passed / 0 failed（1 ignored 为既有用例）

cargo test --test single_build_path
  running 7 tests ... test result: ok. 7 passed; 0 failed

cargo build --features video-studio-e2e
  Finished `dev` profile [unoptimized + debuginfo] target(s) in 40.63s
  （3 条 dead_code warning 全在 src/local_registration.rs，与本次改动无关，改前即存在）

cargo clippy --all-targets
  4 条 warning 全在 tests/local_video_orchestrator.rs:385/497/574/589，均为既有代码

python3 scripts/check_video_media_toolchain.py   → video media toolchain contract is valid
python3 scripts/check_user_facing_branding.py    → passed (51 frontend, 249 native files)
```

`cargo build --release` 未执行：会被既有 `build.rs` 发布密钥门禁拦住（需要合成发布密钥），
与本次改动无关。

## 交付

### 注入能力（`frontend/src-tauri/src/local_video_orchestrator.rs`）

- `VideoWorkerLaunch` 新增 `environment: BTreeMap<&'static str, PathBuf>` 字段；
- 新增 `with_environment(BTreeMap<&'static str, &Path>) -> Result<Self, VideoWorkerError>`，
  签名与 `video_media_toolchain` 两个映射函数的返回类型完全对齐，所以变量名只有一处定义；
- 每个值都过 `validate_executable_path`（绝对路径、祖先无软链/reparse point、正则文件、可执行），
  不合格直接 `ConfigurationInvalid` 失败关闭——宁可拒绝启动，也不给上游一个指向空处的变量；
- 变量名限制 `[A-Z][A-Z0-9_]*`、≤64 字节、最多 8 项，并拒绝 `FORBIDDEN_ENVIRONMENT_NAMES`
  （`PATH`、`PATHEXT`、`LD_PRELOAD`、`LD_LIBRARY_PATH`、`DYLD_*`、`NODE_OPTIONS`、`PYTHON*`）：
  这些名字决定进程去哪里找**代码**，属于重定位运行时，不是传递依赖路径；
- `spawn_worker` 在 `env_clear()` 之后注入，所以隔离启动的动效 Worker 只拿到包内依赖，
  不会把用户机器的 PATH 带回来。

### 智能素材成片接线（`frontend/src-tauri/src/material_video_studio.rs`）

- 新增 `media_toolchain(app)`：从 Tauri `resource_dir` 加载并校验包内 ffmpeg 对；
- 新增 `material_worker_launch(executable, asset_root, &toolchain)`：唯一的启动配置构造点，
  内部 `with_environment(toolchain.intelligent_material_environment())`；
- `open()` 改为走这条构造；两个包内资源（Worker、ffmpeg 对）在创建 workspace **之前**解析，
  顺带修掉旧代码「构造启动配置失败会留下孤儿 workspace」的清理缺口；
- 包缺失或被篡改时素材成片直接拒绝打开，而不是退回系统 ffmpeg。

### 品牌动效成片接线（`frontend/src-tauri/src/lib.rs`）

- `MotionRuntimePaths` 用 `toolchain: VideoMediaToolchain` 取代原来的 `ffmpeg: PathBuf`
  （编码步骤改用 `toolchain.ffmpeg_path()`，不再多存一份派生值）；
- `motion_worker_launch(..., media_environment)` 新增一个参数，内部
  `.and_then(|launch| launch.with_environment(media_environment))`；
- `submit_motion_video_draft` 传 `runtime.toolchain.brand_motion_environment()`。

### 门禁精确化（`frontend/src-tauri/tests/single_build_path.rs`）

`RUNTIME_DEPENDENCY_RESOLVERS` 从 3 项扩到 5 项，把新增的两个解析函数
（`material_video_studio.rs::media_toolchain`、`::material_worker_launch`）纳入同一条规则：
不得有 `cfg(feature)` 分支、不得读环境变量。**没有加任何豁免**。

需要区分的两件事，本次改动属于前者：

| 行为 | 判定 |
| --- | --- |
| 产品主动向自己启动的子进程**注入**依赖路径 | 正常依赖传递，是本次修复本身 |
| 产品**自己从环境变量读取**运行时依赖路径 | `single_build_path` 拦截的行为，仍然禁止 |

`no_source_file_relocates_a_runtime_dependency_through_the_environment` 检查的是被禁的
`AUTOMATION_TOOL_*` 读取名单，`runtime_dependencies_resolve_from_the_package_in_every_build`
检查的是 `env::var` 读取；注入用的是 `Command::env`，两条都不触发，门禁无误判、无需放宽。

### 测试（`frontend/src-tauri/tests/`）

- `video_media_toolchain.rs::the_smart_material_worker_process_receives_the_packaged_ffmpeg`：
  用生产构造函数 `material_worker_launch` 建启动配置 → 真正 `orchestrator.start()` 起进程 →
  断言**子进程环境里** `IMAGEIO_FFMPEG_EXE` 等于包内 ffmpeg 绝对路径；
- `video_media_toolchain.rs::the_brand_motion_worker_process_receives_the_packaged_pair_and_nothing_else`：
  同样走生产函数 `motion_worker_launch`，断言两个 `HYPERFRAMES_*` 都到位，并且子进程里
  **没有 PATH**（证明注入发生在 `env_clear` 之后，且没把宿主搜索路径带回来）；
- `local_video_orchestrator.rs::rejects_worker_environment_values_that_are_not_packaged_executables`：
  路径不存在 / 不可执行 / 相对路径、以及 `PATH`、`LD_PRELOAD`、`NODE_OPTIONS` 这类劫持名，
  全部 `ConfigurationInvalid`；合法的包内可执行文件被接受；
- `video_media_toolchain.rs::every_video_worker_launch_carries_the_packaged_environment_from_its_entry_point`：
  源码级接线守卫，逐个函数体断言 `open` → `material_worker_launch` → `intelligent_material_environment()`、
  `submit_motion_video_draft` → `motion_worker_launch` + `brand_motion_environment()`、
  `spawn_worker` → `command.env(`。专门拦「解析函数写好了但入口不调用」这个缺陷形态复发。

探针 Worker 用 `#!/usr/bin/python3` 绝对解释器（动效线 `env_clear` 后没有 PATH，
`/usr/bin/env` 找不到解释器），完成真实 bootstrap 握手与 `/health` 认证，不是空壳。

## 上游回退顺序的核实结论

变量设置之后，两条上游都**不再**回退到系统组件：

| 上游 | 位置 | 设置后的行为 |
| --- | --- | --- |
| 智能素材成片 | `app/utils/utils.py:get_ffmpeg_binary` | 第一顺位取 `IMAGEIO_FFMPEG_EXE`，直接返回，**不做存在性检查**，后面的 `shutil.which` 根本不执行 |
| 智能素材成片（MoviePy 侧） | `imageio_ffmpeg/_utils.py:get_ffmpeg_exe` | 同样第一顺位取该变量并原样返回（注释明写 "Dont test it: the user is explicit here!"） |
| 智能素材成片（pydub 侧） | `app/services/voice.py:_configure_pydub_ffmpeg` | 把 `get_ffmpeg_binary()` 结果赋给 `AudioSegment.converter`，不会走 pydub 自己的 `which` |
| 品牌动效成片 | `packages/parsers/src/ffBinaries.ts:findFfBinary` | 变量非空即 `resolve()` 返回；`configuredMustExist: true` 的调用点在文件不存在时返回 `undefined`（失败关闭），仍不查系统 |

还有两个次级传播路径已核实：

- WebUI 子进程：`workers/material_montage/webui_runtime.py` 用 `env={**os.environ, ...}` 起 Streamlit，
  变量会继承下去；
- 上游配置覆盖：`app/config/config.py` 里如果 `app.ffmpeg_path` 指向一个存在的文件，会
  **反过来覆盖** `os.environ["IMAGEIO_FFMPEG_EXE"]`。当前 `config.example.toml` 里 `ffmpeg_path`
  是注释状态（默认 `""`），WebUI 也没有任何暴露 ffmpeg 路径的设置项（`grep -rn ffmpeg webui/` 无命中），
  所以正常用户路径下不会被覆盖。**残留风险**：用户手工编辑 job workspace 私有目录里的 config.toml
  仍能顶掉。要彻底封死需要在启动 WebUI 前强制回写该字段，属于另一个任务。

## 失败矩阵

| 场景 | 行为 |
| --- | --- |
| `media-toolchain` 目录缺失 / 摘要不符 / 多文件少文件 / 目标漂移 | `VideoMediaToolchain::load` 拒绝 → 两条线都拒绝打开或提交，不回退系统 ffmpeg |
| 包内 ffmpeg 被换成软链或 reparse point | `with_environment` 的 `validate_executable_path` 逐层拒绝 |
| 包内 ffmpeg 权限位被去掉 | 加载阶段 `assert_executable` 已拒；`with_environment` 再拒一次 |
| 变量值指向不存在的路径 | 启动前 `ConfigurationInvalid`，不把坏路径丢给子进程 |
| 调用方试图注入 `PATH` / `LD_PRELOAD` / `NODE_OPTIONS` 等劫持名 | `ConfigurationInvalid` |
| 同一个变量名注入两次 / 超过 8 项 | `ConfigurationInvalid` |
| 动效 Worker 的 `env_clear` 与注入次序 | 注入在清空之后；用例断言子进程无 `PATH` |
| 素材线构造启动配置失败 | 清理刚创建的 workspace 再返回错误（本次顺带补的清理缺口） |
| Worker 崩溃后自动重启 | 重启走同一个 `spawn_worker`，环境随 `VideoWorkerLaunch` 克隆一并带上 |

## 真实边界

- 两条正向用例都是**真进程**：真 `Command::spawn`、真 bootstrap HMAC 握手、真 loopback `/health`，
  断言落在子进程自己写出的 `os.environ` 上，不是对配置对象的断言；
- 但 Worker 本体是探针脚本，不是冻结的正式 Worker，也没有真的调用 ffmpeg 编码；
  「变量到达子进程」是证明了，「上游用它编出了 MP4」要靠正式包的用户路径验收（见遗留项）；
- 未启动 App、浏览器、Playwright、WDIO，未打包。

## 清理

- 两次变异实验的源码均已还原（`git diff` 只剩本次修复内容）；
- 临时备份文件写在 scratchpad，未进入仓库；
- 测试用的临时目录、探针进程由用例自身 `Drop` / `orchestrator.stop()` 清理；
  全量 `cargo test` 后无残留 python 探针进程。

## 遗留项

1. **`imageio_ffmpeg` 自带的 47 MiB ffmpeg 现在可以裁了**，但**本次不做**：属于
   `FIX-material-worker-package-size.md` 第 5 节第 3 条那条尾巴，需要改
   `workers/material_montage/material-video-worker.spec` 与包体上限契约，另立任务。
   前置条件（环境变量真正接上）已由本次满足。
2. **正式包的正常用户路径验收未做**：需要在装好的 App 里打开「智能素材成片」跑一条真实成片，
   核对进程实际使用的 ffmpeg 是包内那份（例如在临时把系统 ffmpeg 改名的机器上仍能成片）。
   本次只到 Rust 层真实进程验收，按项目规则这一条最多 `🔍 待验收`。
3. **Windows 侧未验证**：两条正向用例是 `#[cfg(unix)]`（探针脚本走 shebang）。
   Windows 需要等价用例（`tests/local_video_orchestrator_windows.rs` 的模式）与真机验收。
4. **上游 config.toml 覆盖 `IMAGEIO_FFMPEG_EXE` 的残留路径**（见上一节）未封死。
