# 本地智能剪辑设计（废弃云剪辑，改用随包 FFmpeg）

> 日期：2026-07-28
> 状态：设计已确认，待实施
> 台账：`docs/local-video-editing-roadmap.md`

## 1. 为什么推翻云剪辑

### 1.1 现状核对结果

专项台账把 VE-01～VE-08 全部标为 ✅ 已完成，但用户在正式 App 里走不通。三处证据：

| 位置 | 实际情况 |
| --- | --- |
| `frontend/src/main.tsx:50` | 生产组合根注入 `createLocalVideoEditingGateway(window.sessionStorage)`，剪辑项目与时间轴存在浏览器 sessionStorage，关闭 App 即丢失 |
| `frontend/src/features/video-editing/local-video-editing-gateway.ts:153` | `submitEditingJob` 固定 `throw VideoEditingGatewayError("editing_service_unavailable")` |
| `backend/src/automation_tool/control_plane/api/` | 19 个路由文件中没有任何 editing 路由，剪辑没有 REST API |

后端阿里云 IMS 能力（Timeline 编译、提交、回调对账、成片导入）确实实现了，`backend/tests/real_cloud/` 下也有真调阿里云的测试，但**只有 pytest 够得着，产品路径够不着**——中间缺 API 层与前端接线。

`docs/development/VE-08.md` 的遗留项第一条已经写明这个缺口：

> Control Plane 剪辑 API 正式装配（把 `SqlAlchemyAliyunEditingIntentStore`、`SqlAlchemyEditingOutputLedger`、Provider Registry 与工作台 Gateway 接通）仍是 VE 线后续装配缺口

证据文件是诚实的，问题出在台账：VE-08 登记了缺口，但专项 Roadmap 把 8 项全标 ✅，那个装配缺口没有落成任何一行待办任务，掉进了任务之间的缝里。

`frontend/e2e-tauri/video-editing.spec.ts` 的验收脚本同样诚实但被误读——它真起 Tauri App、真点按钮，但断言的期望值是"功能不可用被正确展示"：

```ts
it("... shows honest submission state", ...)
await expect(workbench).toHaveText(expect.stringContaining("云端剪辑功能尚未开通"));
```

**一个绿色的验收脚本，实际断言的是用户会看到"功能未开通"。** 它绿恰恰证明功能不可用。

### 1.2 本地渲染性能实测

实测负载：60 秒 1080×1920 竖屏成片，6 段按 in/out 裁剪 + 拼接 + 6 段字幕 overlay + 人声与 BGM 混音。使用**随包 ffmpeg 8.1.2**（用户实际会运行的二进制），非 Homebrew 版本。

- 本机（Apple M2 Max / 12 核）：**4.7 秒**
- 产物 ffprobe 读数：h264 / 1080×1920 / 1800 帧 / 60.000000 秒 / 16 MB
- **CPU 工作量 38.2 CPU·秒**，**峰值内存 983 MB**
- PIL 渲染 6 张中文字幕 PNG：0.12 秒

按 CPU 工作量折算到 Windows（单核性能比取自 Geekbench 量级估算，非实测）：

| 机型 | 核心 | 单核相对 M2 Max | 估算耗时 |
| --- | --- | --- | --- |
| 双核老机（i3-7020U 一类） | 2C4T | 35% | 55 秒 |
| 老办公本 i5-8250U | 4C8T | 40% | 24 秒 |
| 主流轻薄本 i5-1235U | 2P+8E | 65% | 10 秒 |
| 主流台式 R5 5600 | 6C12T | 70% | 9 秒 |
| 游戏本 i7-12700H | 6P+8E | 80% | 5 秒 |

结论：最差的老双核机约 1 分钟出一条 60 秒片，主流机 10 秒上下，内存 1 GB。估算偏保守——测试素材 `testsrc2` 是高熵噪声画面，比真实素材更难压缩。

**硬件编码不需要**：VideoToolbox 实测 9.3 秒（比软编慢一倍），文件 44 MB（大 2.75 倍）。此负载瓶颈在滤镜不在编码，故不为随包 ffmpeg 增加硬件编码器。

### 1.3 云端的真实成本

阿里云单价取自项目自己冻结的 `contracts/video/aliyun-ims-editing-staging.v1.json`：1080p **0.06 元/分钟**，按起始分钟取整，失败不计费。这笔钱不是重点。

真正的成本在同一契约的三行约束：

```json
"input_output_oss_must_match_service_region": true,
"cross_region_materials_supported": false,
"external_or_cdn_material_urls_supported": false
```

**本地素材必须全量上传到指定地域 OSS，不允许外链。** 而本设计的输入正是用户本机的零散素材：

- 500 MB 素材 / 家用上行 30 Mbps → 约 2.5 分钟
- 2 GB 素材 → 约 9 分钟
- 本地渲染同样负载：5～55 秒

**上传耗时是渲染耗时的 10～100 倍。**

补充一条结构性理由：现有两种成片方式本来就是本地渲染（智能素材成片走 MoviePy，品牌动效成片走 Canvas + ffmpeg）。同一 App 内成片在本地、剪辑上云本身就不一致。

### 1.4 决策

废弃云剪辑，删除相关代码与契约，改为随包 FFmpeg 本地剪辑。不保留 Provider 抽象层——只有一条实现时，Provider 注册表与一致性套件属于为假设性需求的过早抽象。

## 2. 架构

云剪辑架构是 Control Plane 直接调阿里云 HTTP API，全程不碰用户硬盘。本地剪辑做不到这样，因为项目基线卡着两条：

- `CLAUDE.md §4.2`：Control Plane 不得直接依赖用户本机路径，它是独立部署单元，未来部署到云服务器
- `CLAUDE.md §4.3`：Local Executor 才是运行在用户电脑、负责本地文件和真实副作用的那一层

因此本地剪辑引擎只能落在 Local Executor 侧：

```
React 剪辑工作台
   → Tauri 窄接口
   → Control Plane：EditingProject / Material / Timeline / EditingJob / Artifact
        （只存元数据与状态，不碰文件，不存本机路径原文）
   → Tauri 调度 Local Video Editing Worker
        └─ 随包 ffmpeg 8.1.2 + PIL 字幕渲染
        → 受控任务目录、素材原地读取、成片入库
```

**路径归属**：Control Plane 存"素材稳定 ID + 内容摘要 + AI 描述"，不存路径原文（`CLAUDE.md §7` 明令本机私有路径不得进入 Control Plane）。路径映射由 Local Executor 侧自行维护。

**素材不上传**：本地渲染不需要把素材搬到任何地方。唯一离开本机的是 AI 理解用的抽帧 JPEG（见 §4.1）。

## 3. 领域模型

供应商无关层全部删除重写。重写要点如下。

### 3.1 Material（新增素材库）

AI 描述的落点，也是入库探测结果的载体。

```python
@dataclass(frozen=True, slots=True)
class Material:
    material_id: MaterialId
    kind: MaterialKind              # video / image / audio
    duration_ms: int | None         # 图片为 None
    width: int
    height: int
    content_digest: str             # 去重
    has_audio: bool                 # silencedetect 探测结果
    audio_loudness_lufs: float | None
    has_speech: bool                # VAD 判定：是人声还是纯环境音
    speech_segments_ms: tuple[tuple[int, int], ...]   # 人声区间
    speech_transcript: str | None   # ASR 转写，仅对有人声素材
    shot_boundaries_ms: tuple[int, ...]   # 场景检测结果，兼作可切点
    ai_description: str | None      # AI 生成，用户可改
    ai_tags: tuple[str, ...]
    described_at: datetime | None
```

用户改过的 `ai_description` 不被后续 AI 运行覆盖。

### 3.2 TimelineClip（补入出点）

现有 `TimelineClip` 只能表达"放在成片第几秒、放多久"，表达不了"取素材的哪一截"——这是现有模型做不了智能剪辑的根因。

```python
@dataclass(frozen=True, slots=True)
class TimelineClip:
    clip_id: str
    start_ms: int                   # 在成片时间轴上的位置
    duration_ms: int                # 在成片里占多长
    source_material_id: MaterialId | None
    source_in_ms: int | None        # 新增：取素材的第几毫秒开始
    source_out_ms: int | None       # 新增：取到第几毫秒
    text: str | None
    gain_db: float                  # 音量，音轨用
    transition_in: TimelineTransition | None
```

**首期约束**：`source_out_ms - source_in_ms` 必须等于 `duration_ms`，即不做变速。差值表达变速倍率的能力留给后续，首期按 YAGNI 锁死。

### 3.3 三轨音频

现有 `TimelineTrackKind` 只有 `VISUAL / AUDIO / CAPTION`，一种 AUDIO 表达不了旁白、原声、音乐三路。

```python
class TimelineTrackKind(StrEnum):
    VISUAL = "visual"
    NARRATION = "narration"    # 旁白：TTS 或用户录音
    AMBIENT = "ambient"        # 素材原声，跟随 clip 的 in/out
    MUSIC = "music"            # BGM
    CAPTION = "caption"
```

## 4. AI 编排链路（首期：文案驱动）

### 4.1 自适应抽帧

固定帧数对长素材等于没看——模型无法知道整条视频讲什么。改为三层策略：

1. **主策略：场景检测抽帧**。`select='eq(n,0)+gt(scene,0.1)'` 找镜头切换，**镜头数决定帧数**。一条 10 秒 3 镜头的抽 3 帧，一条 2 分钟 20 镜头的抽 20 帧
2. **兜底：长镜头补抽**。单镜头超过 8 秒的按时间补帧，避免固定机位长素材只抽到 1 帧
3. **封顶：按时长分档**，防止 token 消耗失控

| 素材时长 | 帧数上限 | 上传量（768px JPEG 估算） |
| --- | --- | --- |
| ≤15 秒 | 6 | ~80 KB |
| ≤1 分钟 | 12 | ~160 KB |
| ≤5 分钟 | 24 | ~320 KB |
| ≤20 分钟 | 40 | ~530 KB |
| >20 分钟 | 60 | ~800 KB |

超上限时按时间均匀降采样，**优先保留场景切点**。

实测验证（随包 ffmpeg 8.1.2）：12 秒 3 镜头素材，`select` + `scene` 变量准确抽出 3 帧，每张 8–22 KB。同一素材下 `scdet` 滤镜在阈值 3/5/10 时均只报出 1 个切点，故**采用 `select` 而非 `scdet`**。

**镜头边界被用两次**：一次让模型看懂素材结构，一次作为 §4.2 选片段的候选切点。

### 4.2 完整链路

```
素材入库（本地，不上传）
  → ffprobe 读时长/分辨率、内容摘要去重
  → silencedetect 探测有无有效音频、响度
  → 有声音的走 VAD：是人声还是纯环境音
  → 有人声的走 ASR 转写 → speech_transcript
  → 自适应抽帧 → 768px JPEG
  → 百炼多模态：描述 + 标签 + 镜头时间区间
  → 存 Material

一句话文案
  → 百炼文本模型：脚本分句
  → TTS 合成每句 → 每句真实音频时长          ← 时长驱动画面时长
  → 语义匹配：句子 ↔ 素材描述（有转写的把转写文本一并纳入匹配依据）
  → 在选中素材的镜头区间内挑最贴的一段 → source_in_ms / source_out_ms
  → 产出 Timeline 草稿
```

旁白支持两种来源：TTS 合成（默认）与用户上传录音（转写后对齐到文案句子，复用同一 ASR 能力）。

### 4.3 人声素材的三级漏斗

对所有素材无差别跑语音识别成本过高，故分三级筛：

| 级别 | 手段 | 成本 | 判定 |
| --- | --- | --- | --- |
| 1 | `silencedetect`（ffmpeg 内置） | 极低，一次扫描 | 有没有有效声音 |
| 2 | VAD 人声检测（Silero VAD，本地） | 低，几十倍实时 | 是人声还是纯环境音 |
| 3 | ASR 转写 | 高，只对通过前两级的素材跑 | 说了什么 |

**运行时选型与装配**（受 `CLAUDE.md` 单一构建路径规范约束，每一项都必须有生产装配路径）：

- **VAD 走本地**：Silero VAD 的 ONNX 模型约 2 MB，用 onnxruntime 推理，不需要完整 PyTorch。体积可接受，随包分发
- **ASR 走云端**：本地 Whisper 模型 base 约 140 MB、small 约 460 MB，为一个筛选步骤增加这个体积不划算；改用百炼语音识别，**只上传音轨不上传视频**（一条 2 分钟素材音轨约 2 MB）

因此 LE-14 引入的新运行时依赖只有 onnxruntime 与 Silero VAD 模型，两者都必须进安装包并有出厂门禁核对，不允许出现"验收时模型在临时目录、正式包里没有"的结构。

转写结果有三个用途，不止于判断：作同期声字幕、纳入语义匹配依据、决定这段不配 TTS。

### 4.4 有人声素材的编排规则

有人声的素材被当作**自带旁白的片段**：

- 它的时长由**原声内容**决定，不由文案句子决定
- 它在成片里独立占一个段落
- 该段落用原声 + 转写字幕，**不配 TTS 旁白**（不排 narration 轨）
- 文案的句子只分配给无人声素材

极端情况：全部素材都有人声时，成片为纯原声拼接，不含任何 TTS——这是合理结果，不是错误。反之全部素材无人声时退化为纯 TTS 旁白成片。

首期不做说话人分离（diarization），同一条素材内多人对话按整体转写处理。

### 4.5 产出形态

默认产出 Timeline 草稿落进剪辑工作台，用户可改后再提交渲染；同时提供"一键直出片"跳过审阅。两条路径共用同一个 Timeline 生成器。

## 5. 渲染管线

全部在 Local Executor 的 Worker 内，随包 ffmpeg 一条 filter_complex 完成：

```
每段: trim=in:out → scale/crop 竖屏 → fps 归一
     ↓
concat 拼接 → xfade 转场
     ↓
PIL 渲染字幕 PNG（含 fallback 链）→ overlay
     ↓
旁白 concat → [narration]
素材原声 atrim → [ambient]
BGM → [music]
     ↓
sidechaincompress：以 narration 为 sidechain 压 ambient 与 music
     ↓
amix → libx264 veryfast crf23 → mp4
```

### 5.1 已核实的滤镜可用性

随包 ffmpeg 8.1.2 实测结果：

| 类别 | 有 | 无 |
| --- | --- | --- |
| 转场/合成 | `xfade` `fade` `overlay` `blend` `alphamerge` `colorkey` | — |
| 抽帧/检测 | `select` `thumbnail` `scdet` `scale` `fps` `showinfo` | — |
| 音频 | `amix` `amerge` `sidechaincompress` `volume` `adelay` `apad` `atrim` `acompressor` `dynaudnorm` `loudnorm` `silencedetect` `astats` | — |
| 字幕 | `drawbox` | `drawtext` `subtitles` `ass` |

**`xfade` 已在包内**，是 libavfilter 内置滤镜，不依赖外部库。契约 `contracts/video/ffmpeg-toolchain.v1.json` 的 `required_capabilities.filters` 只声明了 `amix/aresample/concat/overlay/scale`，需补充声明，**但无需重新构建 ffmpeg**。

### 5.2 字幕不走 drawtext

`drawtext` 需要 freetype，`subtitles`/`ass` 需要 libass，随包 ffmpeg 均未编译。**不为此重新构建**，理由：

- 重构建代价大：freetype + fontconfig + libass + harfbuzz + fribidi 五个库，各自锁版本、锁 SHA256、登记许可证进 SBOM，macOS 与 Windows 两个 runner 都要重建，还要过 `check_third_party_sources.py`
- PIL 路线已在生产中运行：现有智能素材成片即用此方式，实测 6 张中文字幕 0.12 秒
- 对"多字体可选"需求 PIL 更合适：直接读任意 ttf/otf，不需要经 fontconfig 注册；跨平台字体发现是已知难点
- 中文排版可控性更好：换行、描边、阴影、行距、竖排均可精确控制

### 5.3 音频闪避

素材自带声音时默认**自动闪避**：`sidechaincompress` 以旁白轨为 sidechain，旁白发声时压低原声与 BGM，旁白间隙抬回。素材入库时的 `has_audio` 决定是否排 ambient 轨——无有效音频的空镜不排。

闪避不是"原声变大"，而是两步：先由 `gain_db` 把原声定在背景基准（默认 40%，需用真实素材复核），再由 `sidechaincompress` 在旁白发声时从基准往下压、停顿时松回基准。**原声始终不超过基准音量。**

`release`（松开速度）是实际调参的关键：小于 100 ms 会让换气与字间隙都触发起伏，听感像信号不稳；大于 800 ms 则句间短停顿里原声回不来。默认取 200–500 ms 区间，需以真实素材定标。

用户可覆盖默认：每条 clip 有 `gain_db`，另有"原声处理方式"开关（自动闪避 / 固定音量 / 静音），默认自动闪避。

**有人声的素材段落不排 narration 轨**（见 §4.4），该段落的原声即主音轨，不参与闪避压制。

## 6. 字体方案

### 6.1 现状更正

`contracts/video/offline-motion-dependencies.v1.json` 锁定 23 个 Google 字体家族（全部 OFL-1.1，钉在 commit `00e726a`），本机落地 143 个 woff2 分片。其中**仅 Noto Sans SC 一个支持中文**，其余为拉丁字体。

**Noto Sans SC 不解决生僻字**：它与 Source Han 同源，覆盖约 3 万字（CJK 基本区 + 扩展 A），扩展 B 以上仍显示豆腐块。"取全字符集而非 GBK 子集"的决定方向正确但未走到头。

补充事实更正：「璟」（U+749F）在 GBK 中存在（GBK 覆盖 U+4E00–U+9FA5 全部），仅 GB2312 无此字。

### 6.2 候选字体（均为 OFL-1.1）

| 字体 | 风格 | 汉字覆盖 | 用途 |
| --- | --- | --- | --- |
| Noto Sans SC（已有） | 黑体 | 约 3 万字：基本区 + 扩展 A | 默认字幕 |
| Plangothic 遍黑 | 黑体 | 扩展 B 区至扩展 J 区全部 | 生僻字 fallback |
| 文津宋体 WenJinMincho | 宋体 | Unicode 全部汉字 + IVD 异体字 | 风格可选 |
| 霞鹜文楷 GB | 楷体 | 通用规范 8105 字 + GB18030-2022 级别 2 | 风格可选 |

**Plangothic 正好是 Noto Sans SC 的补集**：前者专做扩展 B～J，后者管基本区 + 扩展 A，两者同为黑体，拼合后风格一致、覆盖完整。

### 6.3 实现约束

- **PIL 不支持自动 fallback**，`ImageFont` 是单字体的。fallback 链需自行实现：用 `fontTools` 读 cmap 判断字形存在性，缺字字符换字体单独渲染后拼合
- **体积待核实**。覆盖 10 万字的字体通常数十 MB。项目已有离线依赖下载机制（`.local/offline-motion-deps/downloads/`），生僻字字体可走按需下载而非进安装包，与既有"覆盖倒退换不来 12 MB"的决定一致
- **机制与字体分两个任务**，避免互相阻塞：fallback 链路本身用现有 Noto Sans SC 加一个拉丁字体即可验证（台账 LE-09），生僻字字体的引入、锁版本与生产装配是独立任务（台账 LE-20）
- **字体必须有生产装配路径**。按 `CLAUDE.md` 单一构建路径规范，不允许出现"验收时字体在临时目录、正式包里没有"的结构；出厂门禁须拒绝缺字体的包

## 7. 删除清单

### 7.1 后端生产代码

```
domain/aliyun_ims_editing_provider.py          967 行
domain/aliyun_ims_editing_staging.py           686 行
domain/aliyun_ims_editing_output.py            557 行
domain/aliyun_ims_editing_reconciliation.py    491 行
domain/aliyun_ims_editing_callback.py          180 行
infrastructure/database/aliyun_editing_intent_repository.py   158 行
domain/video_editing.py                        337 行   （重写）
domain/video_editing_provider.py               302 行   （删除，不重写）
domain/video_editing_outputs.py                256 行   （重写）
domain/video_editing_provider_conformance.py   247 行   （删除，不重写）
domain/fake_second_editing_provider.py                  （删除）
```

### 7.2 测试

`backend/tests/unit/control_plane/domain/` 下 8 个 editing 测试文件（约 3700 行）、`backend/tests/integration/` 下 2 个仓储测试、`backend/tests/real_cloud/` 下 4 个 VE 真实云测试。

### 7.3 其他

- `schema.py` 中 `aliyun_editing_intents` 与 `editing_output_lineages` 两张表的声明
- **迁移文件不删**：alembic 链为 `0031 → 0032(aliyun_editing_intents) → 0033(bilibili) → 0034(editing_output_lineages)`，`0032` 位于链中间且 `0033` 的 `down_revision` 指向它，删文件会断链；已执行过迁移的数据库也仍会残留表。改为**新增 `20260728_0035` drop 迁移**把两张表删掉，迁移历史保留
- 契约 `contracts/video/aliyun-ims-editing-staging.v1.json`
- 前端 `features/settings/VideoEditingServiceSettings.tsx` 及其网关、`platform/tauri/video-editing-service-gateway.ts`
- Tauri 4 个 command：`get_video_editing_service_settings`、`configure_video_editing_service`、`clear_video_editing_service`、`test_video_editing_service_connection`
- 专项台账 `docs/embedded-browser-video-studio-roadmap.md` 中 VE-01～VE-08 八行及相关计数

### 7.4 保留

`frontend/e2e-tauri/video-editing.spec.ts` 需重写而非删除——用户路径验收仍需要，但断言目标从"诚实展示不可用"改为"真实出片"。

## 8. 失败矩阵

| 类别 | 场景 |
| --- | --- |
| 素材 | 文件被移动/删除/改名、格式不支持、时长为 0、无视频轨、编码损坏、超大文件、路径含特殊字符 |
| 抽帧与理解 | 场景检测无切点、抽帧失败、模型超时、模型拒答、描述为空、token 超限 |
| 人声检测与转写 | 纯音乐被 VAD 误判为人声、人声被判为环境音、转写结果为空、方言或嘈杂环境转写不可用、ASR 超时、只有背景人声（路人说话）被当作主体 |
| 编排 | 素材数量不足以覆盖所有句子、单条素材过短无法满足旁白时长、语义匹配全部低于阈值 |
| 渲染 | ffmpeg 非零退出、磁盘写满、输出目录无权限、编码器不可用、渲染超时、进程被杀 |
| 音频 | 素材无音轨但排了 ambient 轨、采样率不一致、旁白与画面总时长不等、BGM 短于成片 |
| 字体 | 字体文件缺失、字形缺失且无 fallback、字体加载失败 |
| 任务控制 | 暂停/取消/紧停与渲染进程竞争、App 退出后恢复、重复提交、多任务并发抢 CPU |
| 跨端 | Worker 未启动、握手超时、崩溃、版本不匹配 |

## 9. 测试策略

按项目四层门禁：

| 层级 | 覆盖 |
| --- | --- |
| pytest 单元 | 领域对象校验、状态机、抽帧策略分档、编排算法、fallback 链选字、三级漏斗的分支判定 |
| pytest 集成 | 真实 PostgreSQL 仓储、真实 ffmpeg 渲染小样本并 ffprobe 断言产物、真实 VAD 模型对已知人声/纯音乐样本的判定 |
| Vitest / Testing Library | 工作台组件、素材库 UI、DTO 校验 |
| Playwright UI Harness | React 业务交互 |
| cargo test | Tauri command、Worker 生命周期、错误转换 |
| WebdriverIO Tauri E2E | 正式 App 从素材导入到成片播放的完整用户路径 |

**验收深度要求**（`CLAUDE.md §9.1`）：用户可操作的任务，证据必须落在可外部核对的终态——ffprobe 读数、产物文件尺寸与摘要、数据库行。"元素可见 / 按钮可点击 / 测试 passed"不算。

## 10. 后续（不在首期）

以下三种编排方式记入台账，首期只做文案驱动：

- **素材驱动**：AI 先理解全部素材，自行组织叙事顺序
- **节奏驱动（卡点）**：分析 BGM 节拍，画面严格卡在鼓点切换
- **模板驱动**：用户选成片模板，素材自动填入槽位（可复用品牌动效成片的 12 套风格与槽位机制）

其他后续项：变速适配、长视频自动剪成多条短视频、说话人分离（同一素材内多人对话区分说话人）。

注：「素材里有人说话就不配旁白」原列为后续项，现已提前进首期，见 §4.3、§4.4 与台账 LE-14、LE-16。
