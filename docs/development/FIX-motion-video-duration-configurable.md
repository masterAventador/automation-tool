# FIX：品牌动效成片的段数与每段时长改为用户可配

> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 类型：真实产品缺陷修复（闭合 `dogfood-findings-20260726.md` 第 2b 节）

## 缺陷

用户在 macOS 正式包上试用「视频制作 → 品牌动效成片 → 脚本与分镜」，看到提示「每段 1 秒」，
且固定三段，产出成片总长 3 秒。用户原话：

> 每段1秒是啥意思，写死的吗？你看哪个视频是一共就3秒的。。固定3段也是写死的吗？
> 这玩意不应该是让用户自己去设置一共几段，每段几秒吗

3 秒成片没有任何实际使用价值。段数与每段时长在代码里共有 **8 处**互不相干的写死值，
其中 3 处是清单里没写的、更严重的：

| 位置 | 写死的值 | 清单是否列出 |
| --- | --- | --- |
| `motion_video_studio.rs:26` | `MOTION_DURATION_SECONDS = 3` | 是 |
| `motion_video_studio.rs:27` | `MOTION_FRAME_COUNT = 30 * 3` | 是 |
| `motion_video_studio.rs:160` | `self.beats.len() != 3` 直接拒绝提交 | 是 |
| `motion_video_studio.rs:355` | `"durationSeconds": 1`（每段时长字面量） | 是 |
| `motion_video_studio.rs:810` | HTML `data-duration="3"`，与常量脱钩 | 是 |
| `motion_video_studio.rs:813` | 合成脚本里 `Math.min(2.999, …)` / `Math.min(2, …)` | **否** |
| `lib.rs:640` | ffmpeg `-frames:v 90`、`-framerate 30` | **否** |
| `lib.rs:523-524` | 渲染沙箱 `max_duration_seconds=60`、`max_cpu_seconds=60` | **否** |
| `VideoStudio.tsx:239/338/343/440/462` | 「固定三段模板」「三段脚本与分镜」「每段 1 秒」「/ 3」 | 部分 |
| `material-video-studio-gateway.ts:148` | `request.beats.length !== 3` | 否 |

`lib.rs:640` 那条是最隐蔽的：即使前面全部改对，ffmpeg 仍然只取前 90 帧，
成片还是 3 秒，而且不报错——静默截断。

## 渲染沙箱预算核对结论（任务要求专项核对，结论：预算与时长确实脱钩，已修）

`contracts/video/motion-render-sandbox-budget.v1.json` 声明的是**上限**，不是调用者的申报值：

- `wallClockSecondsMaximum: 300`
- `cpuParallelismMaximum: 8`
- `cpuSecondsCeilingFormula: "maxDurationSeconds * cpuParallelismMaximum"`

调用点 `lib.rs` 申报的是固定的 `60 / 60`，与帧数完全无关。两个独立问题：

1. **墙钟预算与时长脱钩（会真实炸）。** 90 帧固定给 60 秒。用户把成片调到上限 20 秒
   = 600 帧，帧数是原来的 6.7 倍，墙钟预算仍是 60 秒 → Worker 必然 `timeout`
   → 任务显示「制作失败」，用户完全无法理解原因。这与 BM-08 那次「沙箱预算硬编码 60、
   契约公式算出来是 480」是同一个病：调用者自己发明数字，不跟随实际输入。
2. **CPU 预算被钉死在墙钟值上。** 申报 60 CPU 秒 / 60 墙钟秒，等于把平均核占用限制在
   1.0 核。而该契约自己的 `rationale` 写明合法的合成渲染实测约 2–3 核。
   90 帧跑得快才没暴露；600 帧时合法渲染会在 ~90–135 墙钟秒被 CPU 守卫误杀。

修法：两者都由帧数推导，公式写进新契约的 `rationale`：

```
wall = renderWallSecondsBase + ceil(frameCount * renderWallMillisPerFrame / 1000)
     = 30 + ceil(frameCount * 0.4)
cpu  = wall * renderCpuParallelism = wall * 3
```

| 成片长度 | 帧数 | 墙钟 | CPU | 沙箱上限 |
| --- | ---: | ---: | ---: | --- |
| 1 秒（下限） | 30 | 42 | 126 | 300 / 336 |
| 12 秒（默认） | 360 | 174 | 522 | 300 / 1392 |
| 20 秒（上限） | 600 | 270 | 810 | 300 / 2160 |

CPU 申报值取上限 8 的 3/8，既给实测 2–3 核留出余量，又保留守卫有效性。
`the_render_sandbox_budget_follows_the_frame_count_instead_of_a_fixed_number` 用**真实的**
`VideoWorkerRenderSandboxRequest::new` 构造器验收上下两端，证明推导出的预算真的被沙箱接受——
不是只对着公式自证。

ffmpeg 编码超时同样是固定的 120 秒，一并改为 `max(推导墙钟, 120)`：取 `max` 保证
最短成片的截止时间与今天完全一致，不因本次改动变紧。

## 上下限与依据

新增 `contracts/video/motion-storyboard-duration.v1.json` 作为唯一声明源，
Rust 用 `include_str!` 读、TypeScript 直接 `import` 同一个文件。

| 项 | 值 | 依据 |
| --- | ---: | --- |
| 成片总长上限 | **20 秒** | **硬约束，不是拍脑袋**：`SANDBOX_FRAMES_MAXIMUM = 600`（Rust 与 `worker.mjs` 双侧声明，受既有契约测试保护），30 fps → 600 / 30 = 20 秒。`duration_limits()` 在解析时把 `totalSecondsMaximum * framesPerSecond` 与该常量比对，超了直接 fail closed——防止以后有人改大契约却让用户撞上无法理解的 `configuration_invalid` |
| 段数 | 1 – 10 | 1 段是仍能成片的最小分镜。10 段之后总预算已无法给每段留够 2 秒，再多只能产出读不完的镜头 |
| 每段时长 | 1 – 10 秒 | 1 秒是标题加字幕能读完的下限。10 秒是整个预算的一半，静态模板卡片停留超过这个长度是卡顿不是镜头 |
| 默认 | 3 段 × 4 秒 = 12 秒 | 退役模板产出 3 秒，短于任何可用的社交短片；12 秒落在各平台都接受的区间，且离总预算上限还有余量 |

10 × 10 = 100 > 20，所以两个因子各自合法、乘积超预算是**真实可达**的状态，
必须单独校验并单独给提示——这正是 `the_declared_duration_limits_reject_out_of_range_beats_seconds_and_their_combination`
和界面层那条用例覆盖的场景。

## 单一事实源

| 消费方 | 怎么拿到 |
| --- | --- |
| `motion_video_studio.rs` | `include_str!` 契约 → `duration_limits()`，fail closed |
| 帧数 / 总时长 / 每段起止 | `MotionStoryboardPlan`，全部由段数 × 每段时长推导，无第二份 |
| `composition.html` 的 `data-duration` 与 seek 脚本 | 同一个 plan 注入，不再有 `"3"` / `2.999` 字面量 |
| `renderjob.json` / `STORYBOARD.json` / `SCRIPT.json` / `frame.md` | 同一个 plan |
| ffmpeg `-framerate` / `-frames:v` / 超时 | 由 `prepared.frames_per_second()` / `frame_count()` 传入 |
| 沙箱墙钟 / CPU 预算 | `render_sandbox_budget(frame_count)` |
| `motion-duration.ts`（界面 + Tauri 网关校验） | `import` 同一个契约 JSON |

`local_video_orchestrator.rs` 的 `SANDBOX_FRAMES_MAXIMUM` 改为 `pub` 并加注释，
让「成片最长 20 秒」这个结论有可追溯的来源，而不是又一个抄下来的 600。
该常量不在 `motion-render-sandbox-budget.test.mjs` 的正则里，加 `pub` 不影响既有门禁。

## RED

Rust（先写测试，实跑，编译期红）：

```text
error[E0432]: unresolved import
  --> tests/motion_video_studio.rs:2:5
   |   duration_limits, render_sandbox_budget, MotionVideoStudioErrorCode

error[E0061]: this function takes 6 arguments but 7 arguments were supplied
  --> tests/motion_video_studio.rs:90:5
 95 |         seconds_per_beat,
    |         ---------------- unexpected argument #5 of type `u32`

error[E0599]: no method named `total_seconds` found for struct `PreparedMotionRenderJob`
  --> tests/motion_video_studio.rs:108:25

error: could not compile `automation-tool-desktop` (test "motion_video_studio") due to 3 previous errors
```

TypeScript（三个文件同时红）：

```text
 × refuses a storyboard outside the declared duration budget before touching the native command 2ms
 × lets the user choose the beat count and the seconds each beat runs 95ms
 × refuses to submit a beat count and length whose product exceeds the render budget 105ms
 FAIL  src/features/video-studio/motion-duration.test.ts [ src/features/video-studio/motion-duration.test.ts ]

 Test Files  3 failed (3)
      Tests  3 failed | 18 passed (21)
```

`motion-duration.test.ts` 整个文件加载失败（模块尚不存在），所以它的 4 条用例
**当时并未计入 21**——这正是本项目踩过三次的「新测试没被收集却全绿」陷阱，
GREEN 后必须回来对条数。

## GREEN

```text
cd frontend/src-tauri && cargo test --test motion_video_studio
  running 7 tests
  test the_render_sandbox_budget_follows_the_frame_count_instead_of_a_fixed_number ... ok
  test manual_template_rejects_active_content_and_incomplete_storyboards_before_workspace_creation ... ok
  test the_declared_duration_limits_reject_out_of_range_beats_seconds_and_their_combination ... ok
  test manual_template_freezes_editable_copy_and_seekable_composition_in_private_render_job ... ok
  test user_configured_beat_count_and_seconds_per_beat_drive_the_whole_storyboard ... ok
  test artifact_import_removes_the_working_copy_and_user_delete_removes_the_only_video ... ok
  test bm16_all_twelve_locked_styles_freeze_seekable_compositions ... ok
  test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

cd frontend/src-tauri && cargo test            passed: 321  failed: 0
cd frontend && npx vitest run                  Test Files 58 passed (58)   Tests 419 passed (419)
cd frontend && npx tsc -b --force              退出码 0
cd frontend && npx eslint .                    退出码 0
python3 scripts/check_user_facing_branding.py  passed (51 frontend, 247 native files)
```

**条数核对（RED 那条陷阱的收尾）：**

| 套件 | 改动前 | 改动后 | 我新增 |
| --- | ---: | ---: | ---: |
| `cargo test --test motion_video_studio` | 4 | 7 | +3 |
| `cargo test`（全量） | 318 | 321 | +3 |
| `motion-duration.test.ts` | 不存在 | 4 | +4 |
| `VideoStudio.test.tsx` | 12 | 14 | +2 |
| `material-video-studio-gateway.test.ts` | 6 | 7 | +1 |

`--reporter=verbose` 逐条核对过 7 个新用例的名字都真的出现在输出里，不是靠总数推断。

**已知的、不属于本次改动的失败：**

```text
cd frontend && node --test tests/*.test.mjs
  ℹ tests 219   ℹ pass 218   ℹ fail 1
  ✖ VF-04 keeps the Windows native build and reparse-point checks closed
```

该用例断言 `scripts/build_video_media_toolchain.sh` 含 `${FFMPEG_STATIC_LINK_FLAGS[*]}`，
而主会话正在做的 1b 修复把它改成了 `${FFMPEG_STATIC_LINK_FLAGS[*]:-}`，测试尚未同步。
`git diff` 确认本次改动未触碰这两个文件，属主会话在途工作。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 段数 0 / 超上限 | `draft_invalid`，不创建工作区 | Rust + 网关用例 |
| 每段时长 0 / 超上限 | 同上 | Rust + 网关用例 |
| 每段时长为小数（2.5） | 网关 `protocol_mismatch`，不发原生命令 | 网关用例 |
| 两因子各自合法、乘积超总预算（5×6、6×6） | 拒绝 + 中文提示指出具体秒数 | Rust + 网关 + 界面三层 |
| 界面越界时提交 | 「提交本机渲染」置灰，`submitMotionDraft` 一次都没被调用 | 界面用例断言 `not.toHaveBeenCalled()` |
| 最长成片（600 帧）撞沙箱帧数上限 | 契约解析期 fail closed，不可能进到运行期 | `duration_limits()` 与 `SANDBOX_FRAMES_MAXIMUM` 比对 |
| 最长成片的推导预算被沙箱拒绝 | 用真实构造器验收上下两端 | Rust 用例 |
| 契约被改成默认值超预算 / fps 与代码不符 / 缺字段 | fail closed | `duration_limits()` 全量校验 |
| 段数增加后动效零件选择错位 | 选择数组随段数同步增减 | 实现（见下方真实边界 3） |
| 段数减少后预览停在已删除的段 | 索引夹取到最后一段，不越界 | 实现 |

## 真实边界

1. **没有做正式 Tauri App 的用户路径验收。** 本任务被明确限定不启动 App、不跑 Tauri 构建、
   不跑 Playwright。Vitest + `cargo test` 只能证明领域逻辑与 React 交互，
   按项目规则**不能**替代正式包验收。因此本项最多算 `🔍 待验收`。
2. **没有真实渲染过一条比 3 秒更长的成片。** 推导出的预算被真实沙箱构造器接受这一点已验证，
   但「600 帧在真机上确实能在 270 秒内跑完」需要真实渲染才能确认。
   为不盲改在途绿灯，`frontend/e2e-tauri/motion-video-native.spec.ts` 被显式钉在
   每段 1 秒（= 今天的 90 帧），保持该验收的渲染开销与今天完全一致，同时顺带覆盖新控件；
   更长成片的真实渲染验收留作遗留项。
3. **动效零件选择数组随段数同步是实现保证，没有独立测试。** 该断言需要触碰
   `MotionPartsCatalog`，而本任务明确不得修改该文件（另一子代理在改）。
   已确认 `MotionPartsCatalog` 用 `selections.map()` 写回，长度不同步会静默丢弃新段的选择，
   所以 `changeMotionBeatCount` 同时 resize 两份状态。
4. **预览播放节奏仍是每段 500 毫秒，不是真实秒数。** 它是分镜翻页，不是实时播放器；
   把它改成真实时长会让 10 秒/段的预览像卡死。界面没有任何文案声称它是实时播放。
5. **`draft.beats[…]!` 的非空断言保留。** 空分镜在界面上不可达（`InputNumber min=1`
   经实测不会以 0 触发 `onChange`，已用一次性探针验证并删除）。这是既有代码的假设，
   本次只把索引改得更安全（`Math.min` 夹取），没有新增未覆盖的防御分支。
6. **AI 一句话成片链路不受影响。** `tools/motion-authoring/` 有自己的 `duration_seconds`
   与预算推导，不经过 `MotionVideoDraftRequest::manual_template`，未被本次改动波及。

## 清理

一次性探针文件 `frontend/src/__probe__/probe.test.tsx` 与 `/tmp` 下的副本已删除，
`git status` 已确认无残留。未启动 App、浏览器、Playwright 或任何本地服务，
无进程 / 端口 / 容器需要回收。未触碰 `.local/`、构建缓存、`scripts/release_assembly.py`、
`scripts/test_release_assembly.py`、`MotionPartsCatalog.tsx`、`motion-parts-catalog.ts`。

## 文档

| 文件 | 改动 |
| --- | --- |
| `contracts/video/motion-storyboard-duration.v1.json` | 新增，唯一声明源 |
| `frontend/src/features/video-studio/motion-duration.ts` | 新增，界面与网关共用的校验与文案 |
| `frontend/src/features/video-studio/motion-duration.test.ts` | 新增，4 条 |
| `frontend/src-tauri/src/motion_video_studio.rs` | plan / limits / budget，全部写死值收敛 |
| `frontend/src-tauri/src/local_video_orchestrator.rs` | `SANDBOX_FRAMES_MAXIMUM` 改 `pub` 并加依据注释 |
| `frontend/src-tauri/src/lib.rs` | 沙箱预算与 ffmpeg 参数按帧数推导 |
| `frontend/src-tauri/tests/motion_video_studio.rs` | +3 条，既有断言改为按配置推导 |
| `frontend/src/features/video-studio/VideoStudio.tsx` | 段数 / 每段时长控件、文案随配置变化、零件选择同步 |
| `frontend/src/features/video-studio/VideoStudio.test.tsx` | +2 条 |
| `frontend/src/features/video-studio/material-video-studio-gateway.ts` | 请求类型加 `secondsPerBeat` |
| `frontend/src/platform/tauri/material-video-studio-gateway.ts` | 校验改为按契约范围 |
| `frontend/src/platform/tauri/material-video-studio-gateway.test.ts` | +1 条 |
| `frontend/e2e-tauri/motion-video-native.spec.ts` | 显式驱动新控件并钉住帧数 |
| `docs/development/dogfood-findings-20260726.md` | 第 2b 节登记本修复 |
| 本文件 | 新增 |

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 正式 macOS 包用户路径验收（设 5 段 × 4 秒并真实产出 20 秒成片） | 未做 | 下一次真机 dogfood |
| 20 秒 / 600 帧成片的真实渲染耗时是否落在推导的 270 秒内 | 未验证 | 同上 |
| Windows 侧同一链路验收 | 未做 | Windows 验收队列 |
| 每段单独设置时长（当前是全局统一每段几秒） | 未做 | 用户本次只要求「一共几段、每段几秒」，超出即为过度设计 |
| `VF-04` 那条 node 用例与 1b 修复同步 | 未做 | 主会话（本次不得触碰该脚本） |
