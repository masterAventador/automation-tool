# LE-07 素材探测 实施计划

> 日期：2026-07-29
> 工作树：`/Users/aventador/sourceCode/automation-tool/wt/le-07-probe`（分支 `le-07-probe`）
> 台账行：`docs/local-video-editing-roadmap.md` LE-07
> 设计依据：`docs/superpowers/specs/2026-07-28-local-smart-edit-design.md` §4.2、§4.3、§8

## 0. 前置：变基已完成（2026-07-29）

本分支原基点 `5875191` 早于 LE-01～LE-04，缺 `material.py`、专项台账与设计文档。上层已把 `wt/le-07-probe` reset 到 **`e53ef70`**（`fix(le-04): domain 层 7 处正则结尾锚点改 \Z`）——`smart-edit` 上最后一个改到 `material.py` 的提交，含 LE-03 的 `MaterialKind` 分叉与正则修正。

已核：三份文件均在位，`check_third_party_sources.py` exit 0，本计划文件（untracked）保留。

不采用「先做、集成时再补契约测试」——那正是台账 §1 点名要防的「装配缺口掉进任务之间的缝里」。

## 1. 调研结论（已实测，不是推断）

### 1.1 随包 ffmpeg/ffprobe 已就绪，零准备成本

`~/Library/Caches/automation-tool-build/media-toolchain/` 已存在且可用，**不需要重建**：

- `ffprobe 8.1.2` / `ffmpeg 8.1.2`，共 42 MB（各约 15.6 MB）
- 构建参数含 `--enable-gpl --enable-libx264 --disable-network --disable-autodetect`
- 本任务需要的 `silencedetect`、`ebur128`、`volumedetect` 全部存在
- 下游要用的 `select`、`scdet`、`xfade` 也在（LE-08/LE-10 不必重建）

缓存路径由 `scripts/video_runtime_cache.py:cache_root()` 给出，测试必须调这个函数取路径，**禁止硬编码 `~/Library/Caches/...`**。

### 1.2 生产如何定位这个二进制——已有先例，直接照抄

`frontend/src-tauri/src/video_media_toolchain.rs` 是唯一的解析器：校验 `manifest.json`（schema/target/version/license）、逐文件核对 size 与 sha256、拒绝软链与 reparse point、断言可执行位，然后交出绝对路径。

交给 Python 的方式有现成的**同款先例**——内置 Chromium：

```
backend/src/automation_tool/executor/browser_runtime.py:42
    """Paths already authorized and held stable by the Rust BrowserRuntime caller."""
    executable_path: Path
```

`executable_path` 从**命令载荷**（`platform_commands.py:110` 的 Pydantic 命令字段）进来，由 Rust 校验后下发；Python 侧只 `_require_path()` 复核形状（绝对路径、无软链段、正规文件、可执行位、长度与控制字符上限），**从不自己去找**。

**结论：LE-07 不需要新的定位方式，也不需要环境变量。** `PackagedMediaTools` 照 `BrowserLaunchRequest` 写：路径由调用方传入，模块内不做发现、不查 PATH、不读环境变量、不设默认值。生产由 Rust 传已验证路径（装配归 LE-12），测试传缓存路径——**同一条代码路径，只是配置值不同**，属 `CLAUDE.md` 单一构建路径规范明确允许的第 3 类差异。

> 注：契约 `contracts/video/ffmpeg-toolchain.v1.json` 的 `worker_environment` 只覆盖既有两个 Worker（`IMAGEIO_FFMPEG_EXE` / `HYPERFRAMES_*`）。**本任务不加第三条环境变量**——命令载荷传参比环境变量更贴合既有 Chromium 先例，也避免 imageio 那种「环境变量缺失就回落 PATH」的结构。

### 1.3 ffprobe / ffmpeg 实测行为（决定了实现形状）

用随包二进制对 5 个真实素材实测：

**取值命令**（只用 `-show_entries`，不加 `-show_format -show_streams`，否则会把 `tags` 一起吐出来——`tags` 是攻击者可控的元数据，§7 要求视为不可信）：

```
ffprobe -v error -print_format json \
  -show_entries "format=duration,format_name:stream=codec_type,codec_name,width,height" -- <path>
```

| 素材 | `format.duration` | 流 | 判定 |
| --- | --- | --- | --- |
| `sound.mp4` 640×360 | `"3.000000"` | h264 + aac | VIDEO |
| `mute.mp4` 480×854 | `"1.000000"` | 仅 h264 | VIDEO，无音轨 |
| `audio.m4a` | `"2.000000"` | 仅 aac | AUDIO |
| `image.png` 800×600 | **键不存在** | png（`codec_type=video`） | IMAGE |

~~**图片的判别信号是「`format` 里根本没有 `duration` 键」**，不是 duration=0。据此定 kind：~~

- ~~有 video 流 + 有 `format.duration` → `VIDEO`~~
- ~~有 video 流 + 无 `format.duration` → `IMAGE`~~
- 无 video 流 + 有 audio 流 → `AUDIO`
- 都没有 → 拒绝

> **⚠️ 上面划掉的判据在 T2 被实测推翻，勿照此实现。** 只用这 5 个素材调研，样本恰好让「有没有
> 时长」和「是不是图片」重合了。扩大样本后两个方向都出错：管道输出的 Matroska（`MediaRecorder`
> 的 WebM 就是这形状）**真实 2 秒视频不带 duration 键**，会被判成图片；而 JPEG 容器是 `image2`
> 且**自报 `duration: 0.040000`**，会被判成 40 毫秒视频。
>
> **时长是容器写不写的偶然事实，容器类型才是本质。** 正确判据见 T2 实现：仅当**无音轨**且
> `format_name` 是图片容器（`*_pipe` 或 `image2`）才判 IMAGE，否则一律 VIDEO 并要求可用时长。

**失败行为（关键，直接决定会不会写出静默失败）：**

```
[notmedia.txt] exit=1  stdout={}  stderr=notmedia.txt: Invalid data found when processing input
[corrupt.mp4]  exit=1  stdout={}  stderr=... moov atom not found
[missing.mp4]  exit=1  stdout={}  stderr=missing.mp4: No such file or directory
[sound.mp4]    exit=0  stdout={...}
```

两条硬约束：

1. **失败时 stdout 是合法 JSON `{}`**。只解析 stdout 不看 returncode，会拿到一个空 dict 然后一路编出默认值——教科书级静默失败。**必须先判 returncode。**
2. **stderr 里带文件路径**。§7 禁止本机私有路径进日志与错误响应，所以 **ffprobe/ffmpeg 的 stderr 一律不得进入异常消息、日志或返回值**，只能就地丢弃并抛出固定文案的拒绝。

**音频测量命令**：

```
ffmpeg -nostdin -v info -i <path> \
  -af "silencedetect=noise=-50dB:d=0.3,ebur128=peak=none:framelog=quiet" -f null -
```

实测：

| 素材 | silencedetect | ebur128 整合响度 |
| --- | --- | --- |
| `sound.mp4` | 无输出（全程有声） | `I: -21.8 LUFS` |
| `silent.mp4`（有音轨但数字静音） | `silence_start: 0` / `silence_end: 2.020136 \| silence_duration: 2.020136` | `I: -70.0 LUFS` |
| `audio.m4a` | 无输出 | `I: -21.8 LUFS` |

两个要点：

- **`framelog=quiet` 必须加**。不加时 ebur128 每 100 ms 打一行 `t: ... I: ...`，一条 4 小时素材约 14 万行 stderr。加了以后总 stderr 稳定在 54 行左右，与时长无关——这直接对应失败矩阵的「超大文件」。
- **`-70.0 LUFS` 正好是 ebur128 的地板，也正好是 `Material` 允许的下界**（`-70.0 <= audio_loudness_lufs <= 0.0`）。数字静音落在边界上，语义清晰。

### 1.4 与 `Material` 对齐时发现的一处不可表示状态

`material.py:135`：

```python
if self.kind is MaterialKind.AUDIO and not self.has_audio:
    _reject()
```

**一条全程数字静音的纯音频文件，构造不出 `Material`**：kind 只能是 `AUDIO`，而 `has_audio` 按「有效音频」判定为假，两者互斥。

这不是缺陷，是模型有意的不变式。**本任务的处理：探测阶段直接拒绝这类素材**（`MaterialProbeRejected`），不把一个填不进 `Material` 的事实对象交出去。T3 与 T7 各有一条用例钉住它。

### 1.5 边界现状

- **`executor/` 零处 import `control_plane`——实测确认，非推断。** 判据：

  ```
  grep -rnE "^\s*(from|import)\s+.*control_plane" backend/src/automation_tool/executor/   →  0
  ```

  宽口径 `grep -rn "control_plane" backend/src/automation_tool/executor/` 有 2 条命中，但两条都是 `runtime.py:201/215` 的 `_is_control_plane_restart` **函数名**，不是 import。反向 `control_plane/` import `executor/` 同样 0 处。`protocol/` 才是两边共用的层（36 / 34 个文件）。

  **方向由现状确认**：探测模块不 import `control_plane`，`ProbedMaterialKind` 在执行器侧另立，取值一致性由 T7 的跨层测试钉住（测试可同时 import 两边，生产代码不行）。
- `Material` 没有任何路径字段，所以「路径不上报」在模型层已经成立；本任务要保证的是**执行器侧的产出对象也不带路径**，且这一点由结构而非注释保证（见 T5/T6）。
- 顺带发现：本树 `CLAUDE.md` §9.2 引用的 `backend/tests/unit/executor/test_shipped_package_boundary.py` 只存在于 `feature-audit`（`ddc6632`），**main 与本线都没有**。即该条规则在本线上目前没有门禁在守。不属 LE-07 范围，仅报告。

## 2. 交付物

```
backend/src/automation_tool/executor/material_probe.py        # 新建，唯一生产模块
backend/tests/unit/executor/test_material_probe.py            # 分层测试
backend/tests/unit/executor/test_material_probe_media.py      # 真实素材验收
docs/development/LE-07.md                                     # 证据文件
docs/local-video-editing-roadmap.md                           # 台账行（依赖变基）
```

## 3. 模块设计

### 3.1 结构化拒绝理由

失败矩阵有 8 类场景。若它们共用一句固定文案，用户在 LE-18 的素材库界面只会看到「素材探测失败」，唯一能做的就是挨个试。**拒绝理由必须是结构化的类型**，判据是「用户看到它之后知道下一步该干什么」。

仿 `domain/video_creation.py:90` 的 `RenderFailureCode` 写法（`StrEnum` + snake_case 取值）。执行器不 import `control_plane`，故照形状自定义：

```python
class MaterialProbeRejection(StrEnum):
    UNREADABLE = "unreadable"                  # 移动/删除/改名/无读权限 → 让用户重新定位文件
    UNSAFE_PATH = "unsafe_path"                # 非绝对/软链段/控制字符/BiDi → 换个正常路径
    UNSUPPORTED_FORMAT = "unsupported_format"  # ffprobe 认不出容器 → 换格式或转码
    CORRUPT = "corrupt"                        # 容器认得但解码报错 → 文件坏了，重新导出
    NO_USABLE_STREAM = "no_usable_stream"      # 既无视频轨也无音轨 → 这不是能用的素材
    EMPTY_DURATION = "empty_duration"          # 时长折算为 0 ms → 太短，用不了
    SILENT_AUDIO = "silent_audio"              # 纯音频但全程静音 → 这条音频没有声音
    TOO_LONG = "too_long"                      # 超 4 小时上限 → 先裁剪
    TOO_LARGE = "too_large"                    # 超体积/画幅上限 → 先压缩
    PROBE_FAILED = "probe_failed"              # ffprobe/ffmpeg 超时或异常退出 → 重试


class MaterialProbeRejected(RuntimeError):
    """带结构化理由；消息固定，永不含路径、永不含 ffmpeg stderr。"""
    def __init__(self, rejection: MaterialProbeRejection) -> None:
        super().__init__("material probe rejected")   # 固定文案，§7 不放松
        self.rejection = rejection
```

枚举是封闭类型不是自由文本，**不构成泄漏通道**——§7 的约束落在异常消息与日志上，两者仍然固定且无路径。

**每个成员都要有专属拒绝用例，并用打桩核实它是该用例第一个开火的分支**（方法论第 3 条）。不允许出现某个成员永远拿不到——这是本线的高频故障模式。收口前额外跑一条元测试：遍历 `MaterialProbeRejection` 全部成员，断言每个都至少被一条用例实际抛出过。

### 3.2 其余类型

```python
@dataclass(frozen=True, slots=True, repr=False)
class PackagedMediaTools:
    """Paths already authorized and held stable by the Rust caller."""
    ffprobe_path: Path
    ffmpeg_path: Path
    def __post_init__(self) -> None: self.revalidate()
    def revalidate(self) -> None: ...        # 每次用前复核，对应「文件被移动/删除/改名」
    def __repr__(self) -> str: return "PackagedMediaTools(<redacted>)"

class ProbedMaterialKind(StrEnum):           # 值与 MaterialKind 逐字相同
    VIDEO = "video"; IMAGE = "image"; AUDIO = "audio"

@dataclass(frozen=True, slots=True)
class MaterialFacts:
    """探测学到的全部事实。刻意不含任何路径字段。"""
    kind: ProbedMaterialKind
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool
    audio_loudness_lufs: float | None
    content_digest: str                      # 64 位小写十六进制

def probe_material(tools: PackagedMediaTools, path: Path) -> MaterialFacts: ...
```

`ProbedMaterialKind` 在执行器侧另立，是为了不让 `executor` 依赖 `control_plane`。两者取值一致由 **T7 的跨层测试**钉住（测试可以同时 import 两边，生产代码不行）。

**路径映射（`MaterialPathRegistry`）**：`material_id -> Path` 的本机映射，JSON 落在调用方给的本地目录。它与 `MaterialFacts` 是两个对象，`MaterialFacts` 结构上无处安放路径——这就是「边界在代码结构上成立」的含义。

## 4. Task 拆分

每个 Task 独立提交，前缀 `feat(le-07):`。每个 Task 都走 **RED（实跑看到红）→ GREEN → 门禁**。

---

### T1 `PackagedMediaTools`：失败关闭的工具句柄

**RED**：`test_material_probe.py` 写以下用例，逐条单独一个拒绝理由（不写「一个大拒绝测试」——`or` 链里的子条件不单独计量，合并写会让某个析取项永远为假还满分）：

- 接受：指向缓存里真实 ffprobe/ffmpeg 的一对路径
- 拒绝：相对路径 / 空路径 / 超 4096 字符 / 含控制字符 / 含 BiDi 覆盖字符 / 路径段是软链 / 指向目录 / 指向不可执行的正规文件 / 传入 `str` 而非 `Path`
- `repr()` 结果为 `PackagedMediaTools(<redacted>)`，且不含任何路径片段
- `revalidate()` 在文件被 unlink 后抛 `MaterialProbeRejected`

**GREEN**：照 `browser_runtime._require_path` 的判据实现（该函数为 private，不跨模块复用，按同一判据重写并在 docstring 注明来源）。

**门禁**：本文件 pytest + ruff format/check + mypy。

---

### T2 ffprobe 读时长/分辨率/编码

**RED**：

- 四种 kind 判定各一条（VIDEO / IMAGE / AUDIO / 都没有→拒绝）
- **returncode != 0 → 拒绝**（打桩返回 `returncode=1, stdout="{}"`，证明不看 stdout 就下结论）
- stdout 是 `{}` 但 returncode=0 → 拒绝（防御 ffprobe 行为变化）
- stdout 非法 JSON → 拒绝
- stdout 超出上限 → 拒绝
- 超时 → 拒绝（`subprocess` 超时打桩）
- `duration` 不是数字字符串 / 为负 / 为 `"N/A"` → 拒绝
- **`duration` 折算成 0 ms → 拒绝**（`Material` 要求 ≥1，对应失败矩阵「时长为 0」）
- 时长超 `MAX_MATERIAL_DURATION_MS`（4 小时）→ 拒绝
- 宽高缺失 / 非正 / 超 8192 → 拒绝
- AUDIO 的 `width`/`height` 必须是 `None`
- **抛出的异常消息里不含路径任一片段、不含 ffprobe stderr 原文**

**GREEN**：`subprocess.run` 带 `--` 分隔符（防以 `-` 开头的路径被当选项）、`capture_output`、`timeout`、`stdin=DEVNULL`、`check=False`；先判 returncode，再解析 JSON，再逐字段校验。

**门禁**：同上。

---

### T3 `silencedetect` + `ebur128` 判有无有效音频与响度

**RED**：

- 无音频流 → `has_audio=False`, `loudness=None`，且**不启动 ffmpeg**（断言未调用，省一次整文件解码）
- 有音频流且有声 → `has_audio=True`，响度落在 [-70.0, 0.0]
- 有音频流但全程静音（`silence_duration` ≈ 时长）→ `has_audio=False`, `loudness=None`
- `I: -70.0 LUFS` 地板 → 判为静音
- 部分静音（前半静音后半有声）→ `has_audio=True`
- ffmpeg returncode != 0 → 拒绝
- ffmpeg 超时 → 拒绝
- stderr 无 `I:` 行 → 拒绝（不默认成 0.0）
- 解析到的响度 > 0.0 或 < -70.0 → 拒绝
- **kind=AUDIO 且判定无有效音频 → 拒绝**（§1.4 的不可表示状态）
- 异常消息不含路径、不含 ffmpeg stderr

**GREEN**：命令固定为 §1.3 那条（含 `framelog=quiet`）；stderr 按行读且总量设上限。

**门禁**：同上。

---

### T4 内容摘要去重

**RED**：

- 两个字节相同、文件名不同的文件 → digest 相同
- 改动一个字节 → digest 不同
- digest 是 64 位小写十六进制（用 `protocol.safe_text.is_sha256_hex` 断言，**复用既有校验而非另写**）
- 与 `hashlib.sha256(path.read_bytes()).hexdigest()` 逐字相等
- 大于 1 MiB 的文件（跨多个读块）结果仍正确 → 证明分块边界没错
- 文件超尺寸上限 → 拒绝
- 读取中途文件消失 → 拒绝
- 无读权限 → 拒绝

**GREEN**：流式 sha256，1 MiB 分块，读前先查 `stat` 尺寸上限。

**门禁**：同上。

---

### T5 `probe_material` 编排 + 结构性无路径边界

**RED**：

- 编排顺序：先 `revalidate()`，再 ffprobe，再（按需）ffmpeg，再摘要
- 任一步拒绝则整体拒绝，且不产出半成品
- **结构测试**：`dataclasses.fields(MaterialFacts)` 中不存在名字含 `path` / `dir` / `file` / `location` 的字段
- **结构测试**：`repr(facts)` 与 `str(facts)` 对一个位于特殊字符目录下的素材，输出不含该路径的任一片段
- 路径含空格 / `&` / `$` / `'` / 中文 → 探测成功（实测该目录名可用）
- 以 `-` 开头的路径 → 不被当作 ffprobe 选项

**GREEN**：编排函数。

**门禁**：同上。

---

### T6 `MaterialPathRegistry`：映射只存本机

**RED**：

- 登记 `material_id -> Path` 后可回查
- 落盘 JSON 后重建实例仍可回查
- 同一路径重复登记 → 幂等
- 登记非绝对路径 / 非正规文件 → 拒绝
- `repr()` 不含路径
- **AST 边界测试**：读 `material_probe.py` 的 AST，断言 `MaterialFacts` 的类定义体内没有任何路径类字段（改名躲不掉，判据是 AST 不是 grep 产物）

**GREEN**：最小注册表。

**门禁**：同上。

---

### T7 真实素材验收 + 填满 `Material`（依赖变基）

**本 Task 额外承接的两项**（从前序 Task 显式推迟而来，不靠人记得）：

1. **枚举元测试**——遍历 `MaterialProbeRejection` 全部成员，断言每个都至少被一条用例实际抛出过。
   推迟到此处是因为成员到 T4 才齐全（T2 加了 `UNDECODABLE` / `PROBE_CRASHED` 等，T3 加
   `SILENT_AUDIO`，T4 加体积上限相关成员），在 T2 写只能覆盖一半。
2. **产物 → `Material` 真实构造用例**——审查指出：目前音频无画幅、图片无时长**只由「手抄的常量
   加手写的断言」保证**，仓库里没有任何用例真的拿探测产物去建一个 `Material`。
   注意跨层常量一致性测试**拦不住形状组合问题**：两份限值一模一样，也挡不住「视频被判成图片」
   这类 kind / 时长 / 画幅的组合错误（T2 的 Critical 正是此类）。故本 Task 必须逐条素材实际构造
   `Material`，而不只是比对上限数值。
3. **每一条 ffmpeg / ffprobe 命令行，都必须至少有一个用例用随包真实二进制跑通。**
   存根用例负责覆盖分支与失败矩阵，真实二进制用例负责证明**这条命令行本身合法**，两者不可
   互相替代。收口前逐条清点模块里拼出的每一条命令行，确认都有真实二进制用例覆盖。

   依据是 T3 的实测：`read_audio_facts` 写完时 **110 条单元测试全绿、覆盖率 100%，而六个真实
   文件全部失败**——拼出的 ffmpeg 命令行根本不合法（输入必须在输出选项之前；且 **ffmpeg 不支持
   `--`**，实测它把 `--` 当文件名打开，而同族的 ffprobe 支持）。**同族工具之间的差异只有真跑
   才知道**，而存根对任何参数都照样回答，**这一类问题它本质上测不出**——「100% 覆盖 + 全绿」
   在这里提供的信息量是零。这是可复现的失败模式，不是偶发。

**RED / 验收**：`test_material_probe_media.py`，session 级 fixture 用**随包 ffmpeg** 现场生成 8 个素材（前 6 个实测总耗时约 1 秒）：

| 素材 | 规格 |
| --- | --- |
| `sound.mp4` | 640×360 / 25fps / 3s / h264+aac / 有声 |
| `silent.mp4` | 1280×720 / 30fps / 2s / 有音轨但数字静音 |
| `mute.mp4` | 480×854 / 24fps / 1s / 无音轨 |
| `audio.m4a` | 纯音频 2s |
| `image.png` | 800×600 |
| `corrupt.mp4` | 取 `sound.mp4` 前 5000 字节 |
| `pipe_av.mkv` | 管道输出的 Matroska，容器不报时长（T2 Critical 的形状）|
| `shot.jpg` | JPEG，容器 `image2` 且自报 0.04 秒时长 |

断言：

1. 每条素材的 `MaterialFacts` 与**另起一次 ffprobe、用不同查询形状**读出的值逐字相等（独立读数交叉核对，不是自己跟自己比）
2. `sha256` 与 `hashlib` 独立算出的值相等
3. **每条素材的事实能构造出合法 `Material`**——把 `MaterialFacts` 的字段喂进 `Material(...)`，补齐 LE-07 不负责的字段（`has_speech=False` 等），构造成功即证明「产出填得满」
4. `ProbedMaterialKind` 的成员名与取值 == `MaterialKind` 的成员名与取值（跨层一致性）
5. `corrupt.mp4` 被拒绝
6. 素材被 unlink 后再探测 → 拒绝

工具路径经 `scripts/video_runtime_cache.cache_root()` 取得；缺失时**硬失败并提示跑 `scripts/prepare_video_runtime.py`**，不 skip（skip 会让验收静默消失）。

**门禁**：本任务两个测试文件 + 全部改动文件的 ruff/mypy。

---

## 5. 覆盖率与自证

新模块要求 100% 语句 + 100% 分支、零 partial branch。收口前跑：

```
cd backend && .venv/bin/python -m pytest tests/unit/executor/test_material_probe.py \
  tests/unit/executor/test_material_probe_media.py \
  --cov=automation_tool.executor.material_probe --cov-report=term-missing --cov-branch -q
```

四条方法论逐条执行：

1. **逐项推导析取式**：每个 `or` 链的每个子条件，各有一条只触发它的用例（T1/T2 已按此拆）
2. **护栏零拒绝检查**：对每条拒绝分支，确认存在至少一条**被拒绝**的用例，而不是只在接受路径上被执行过
3. **断言捕捉力核实**：给失败路径打桩抓调用栈上一帧，逐条核对「第一个开火的分支」与用例名一致
4. **变异自证改运算符不改常量**：`>=`↔`>`、`and`↔`or`、`not` 增删；全程 `PYTHONDONTWRITEBYTECODE=1` + `-B` 并清 `__pycache__`（同秒等长变异体会复用 `.pyc`）；带一条必须被杀的 canary 自检

## 6. 失败矩阵覆盖对照（设计 §8「素材」行）

| 场景 | 覆盖位置 |
| --- | --- |
| 文件被移动/删除/改名 | T1 `revalidate` 用例、T4 读取中途消失、T7 unlink 后探测 |
| 格式不支持 | T2 非媒体文件 |
| 时长为 0 | T2 折算 0 ms 拒绝 |
| 无视频轨 | T2 AUDIO 判定、T3 无音频流分支、T7 `audio.m4a` / `mute.mp4` |
| 编码损坏 | T2 returncode 用例、T7 `corrupt.mp4` |
| 超大文件 | T2 时长上限、T4 尺寸上限、T3 `framelog=quiet` 界定 stderr |
| 路径含特殊字符 | T5 空格/`&`/`$`/`'`/中文、以 `-` 开头 |
| 路径泄漏（§7） | T1/T5/T6 的 repr 断言、T2/T3 的异常消息断言 |

## 7. 明确不做

- **VAD 与 ASR**（三级漏斗第 2、3 级）属 LE-14
- **抽帧**属 LE-08
- **Rust 侧把已验证路径下发给剪辑 Worker** 属 LE-12；本任务只交付 Python 侧接收端。该装配缺口写进 `docs/development/LE-07.md` 的遗留项，并在报告里点名，不让它掉进缝里
- **落库**属 LE-05；本任务的注册表只是本机 JSON

## 8. 已拍板事项（2026-07-29）

1. **变基**：已 reset 到 `e53ef70`，见 §0
2. **`MaterialPathRegistry` 属 LE-07**。理由：台账行「路径映射只存本机不上报 Control Plane」的主语就是本任务；往后推的话 LE-05 是落库（错误的家）、LE-12 是 Worker 生命周期（勉强），结果多半是没人认领。
   **范围钉死**：类型 + 本机 JSON 持久化（目录由调用方给）+ 结构性边界。**不做**跨进程同步、**不做**落库。
3. **T7 缺工具链硬失败，不 skip**。一个被 skip 的验收测试和没有验收测试是一回事，但它看起来是绿的——比没有更糟。
4. **拒绝理由结构化**（§3.1），异常消息本身仍固定且不含路径与 ffmpeg stderr。

## 9. 交付节奏

**每个 Task 做完立即返回给上层**，由上层派两轮独立审查（spec 符合性 + `pr-review-toolkit` 代码质量）。不攒到最后一次性交——审查的价值在早发现。
