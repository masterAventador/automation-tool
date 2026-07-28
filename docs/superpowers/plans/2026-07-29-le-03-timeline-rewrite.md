# LE-03 Timeline 重写实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把只能表达「放在第几秒、放多久」的旧 Timeline，换成能表达「取素材的哪一截、多大音量、走哪条音轨」的本地剪辑 Timeline。

**Architecture:** 新建 `backend/src/automation_tool/control_plane/domain/timeline.py`，与 `material.py` 平级，引用 `MaterialId`。旧的 `TimelineId`/`TimelineTrackKind`/`TransitionKind`/`TimelineTransition`/`TimelineClip`/`TimelineTrack`/`Timeline` 从 `video_creation.py` **删除**，不留两份。

**Tech Stack:** Python 3.12、frozen dataclass + `slots=True`、`StrEnum`、pytest。

## 已核实的前置事实（写计划时实跑得出，实现者不必重查）

| 事实 | 证据 |
| --- | --- |
| `video_creation.py` 的 Timeline 全族在 `backend/src/` 下**零生产消费者** | `grep -rn "Timeline" --include="*.py" src/` 只命中 `domain/video_creation.py` 与 `domain/__init__.py` 的转出口 |
| 唯一的后端测试消费者是 `backend/tests/unit/control_plane/domain/test_video_creation.py` | 同上 grep + 该文件 482 行 |
| 前端 `frontend/src/features/video-editing/video-editing-dto.ts` 是**手写 zod 副本**，与后端无生成耦合 | 该文件自定义 `timelineTrackKindSchema` 等，未从任何生成产物导入 |
| `executor/motion_authoring/` 里的 `RenderJob`/`Storyboard`/`StoryboardBeat` 是**同名不同物** | 它们定义在 `executor/motion_authoring/agent.py`，不 import `control_plane.domain.video_creation` |
| Timeline 未出现在 OpenAPI 与执行协议 schema 中 | LE-06 才建剪辑 REST API，当前无路由引用 |

**结论**：本任务改后端领域层**不会**打断前端构建，也不会影响品牌动效成片。前端 `video-editing` 那套壳由 LE-17 重写。

## Global Constraints

- **TDD 铁律**：每一步先写会失败的测试，实跑看到红，再写实现。禁止先实现后补测试。
- **验证命令**（每个 task 提交前全部跑通，全部在**前台**跑，不要后台）：
  - `backend/.venv/bin/python -m pytest tests/unit/control_plane/domain -q`（在 `backend/` 目录下）
  - `backend/.venv/bin/python -m ruff format --check .`（在 `backend/` 目录下）
  - `backend/.venv/bin/python -m ruff check .`（在 `backend/` 目录下）—— **注意本仓库 ruff 全库已有约 98 个既有错误，只需确认你的改动没有新增**，用 `git stash` 前后对比或只看命中你改动文件的条目
  - `backend/.venv/bin/python -m mypy`（在 `backend/` 目录下）
- **风格必须与 `material.py` 一致**：`_reject()` 抛 `Invalid*Model`、`type(x) is not int` 而非 `isinstance`（因为 `isinstance(True, int)` 为真）、`type(x) is not float` 同理拒绝整数、frozen dataclass + `slots=True`、`from __future__ import annotations`。
- **每个 kind / 每种轨道都必须有自己的规则**。LE-02 终审的教训：`MaterialKind.AUDIO` 一路没有 kind 专属规则，导致 174 个荒谬状态被接受。新写的每一个枚举值，都要问一遍「这一支的专属规则是什么」，答不出来就是漏了。
- **禁止两种写法表达同一个状态**。`TransitionKind.CUT` 与 `transition_in=None` 都表示「硬切」，故本次删掉 `CUT`。
- 中文提交信息，`feat(le-03):` / `fix(le-03):` 前缀。
- **不要 `git add -A`**，逐文件列出。

## 本计划有意不做的事（供审查者对照，不是遗漏）

| 不做 | 理由 |
| --- | --- |
| Timeline 不带 `project_id` / owner 字段 | `EditingProject` 由 LE-04 定义，现在加等于先造一个指向不存在类型的字段 |
| 不校验 `source_out_ms` 是否落在素材真实时长内 | Timeline 只持有 `MaterialId`，看不到 `Material.duration_ms`。跨聚合根校验属 LE-05 仓储层，届时两者同时在手 |
| 不校验「视觉 clip 有无 in/out」与素材 kind 是否相符 | 同上：视频要切片、图片没有时间轴，两者都合法，只有拿到 `Material.kind` 才能判。LE-05 补 |
| 不做变速 | 设计文档 §3.2 明令首期锁死 `source_out_ms - source_in_ms == duration_ms` |
| 音轨不支持转场 | 首期渲染管线（设计 §5）只有 `xfade` 视频转场，音频交叉淡化不在首期 |

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `backend/src/automation_tool/control_plane/domain/material.py` | 改：`width`/`height` 按 kind 分叉（T1） |
| `backend/src/automation_tool/control_plane/domain/timeline.py` | 新建：本地剪辑 Timeline 全族（T2/T3/T4） |
| `backend/src/automation_tool/control_plane/domain/video_creation.py` | 改：删掉旧 Timeline 全族（T5） |
| `backend/src/automation_tool/control_plane/domain/__init__.py` | 改：转出口换成新模块（T5） |
| `backend/tests/unit/control_plane/domain/test_material.py` | 改：补宽高分叉用例（T1） |
| `backend/tests/unit/control_plane/domain/test_timeline.py` | 新建：Timeline 全族不变式（T2/T3/T4） |
| `backend/tests/unit/control_plane/domain/test_video_creation.py` | 改：移除旧 Timeline 用例（T5） |

---

### Task 1: Material 宽高按 kind 分叉

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: 现有 `Material`、`MaterialKind`、`InvalidMaterialModel`、`_reject`
- Produces: `Material.width: int | None`、`Material.height: int | None`。后续任务与 LE-05/LE-07 按 kind 读取：video/image 必有值，audio 必为 `None`

**背景**：当前 `width: int`、`height: int` 都强制落在 `[1, 8192]`，音频素材被迫编造一对宽高。这是**强制荒谬**（无论怎么填都是错的），不同于可选荒谬（填了没人看）。改成 `int | None` 并按 kind 分叉，与 `duration_ms` 对 IMAGE 的处理对称。

- [ ] **Step 1: 写会失败的测试**

在 `backend/tests/unit/control_plane/domain/test_material.py` 里，先补一个音频素材工厂（放在已有的 `_video` 工厂后面）：

```python
def _audio(**overrides: object) -> Material:
    """A valid audio material, with named fields overridable per test."""
    defaults: dict[str, object] = {
        "material_id": MaterialId.new(),
        "kind": MaterialKind.AUDIO,
        "duration_ms": 30_000,
        "width": None,
        "height": None,
        "content_digest": "b" * 64,
        "has_audio": True,
        "audio_loudness_lufs": -18.0,
        "has_speech": False,
        "speech_segments_ms": (),
        "speech_transcript": None,
        "shot_boundaries_ms": (),
        "ai_description": None,
        "ai_tags": (),
        "description_source": DescriptionSource.AI,
        "described_at": None,
    }
    defaults.update(overrides)
    return Material(**defaults)  # type: ignore[arg-type]
```

再补四个用例：

```python
def test_audio_material_carries_no_frame_size() -> None:
    assert _audio().width is None
    assert _audio().height is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("width", 1920), ("height", 1080), ("width", 0), ("height", -1)],
)
def test_audio_material_rejects_any_frame_size(field: str, value: object) -> None:
    with pytest.raises(InvalidMaterialModel):
        _audio(**{field: value})


@pytest.mark.parametrize("kind", [MaterialKind.VIDEO, MaterialKind.IMAGE])
@pytest.mark.parametrize("field", ["width", "height"])
def test_visual_material_requires_a_frame_size(kind: MaterialKind, field: str) -> None:
    overrides: dict[str, object] = {"kind": kind, field: None}
    if kind is MaterialKind.IMAGE:
        overrides["duration_ms"] = None
    with pytest.raises(InvalidMaterialModel):
        _video(**overrides)


@pytest.mark.parametrize("value", [0, MAX_MATERIAL_DIMENSION + 1, 1080.0, True])
def test_visual_material_rejects_out_of_range_frame_size(value: object) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(width=value)
```

同时把文件里已有的、给音频素材填了宽高的构造改成 `None`（用 grep 找 `MaterialKind.AUDIO` 的出现处逐一核对）。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -q
```

预期：`test_audio_material_carries_no_frame_size` 与 `test_visual_material_requires_a_frame_size` 失败（当前 `None` 会被现有的 `type(self.width) is not int` 拒绝 / 缺失的分叉不拒绝）。**必须亲眼看到红**。

- [ ] **Step 3: 写最小实现**

在 `material.py` 中把字段类型改掉：

```python
    duration_ms: int | None
    width: int | None
    height: int | None
```

把 `__post_init__` 里的宽高检查从那个大 `if` 里摘出来，改为调用新方法：

```python
    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or not isinstance(self.kind, MaterialKind)
            or not isinstance(self.content_digest, str)
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
        ):
            _reject()
        self._validate_frame_size()
        self._validate_duration()
        self._validate_audio()
        self._validate_shot_boundaries()
        self._validate_description()

    def _validate_frame_size(self) -> None:
        """Only material with a picture has a frame size; audio has none to state."""
        if self.kind is MaterialKind.AUDIO:
            if self.width is not None or self.height is not None:
                _reject()
            return
        for value in (self.width, self.height):
            if type(value) is not int or not 1 <= value <= MAX_MATERIAL_DIMENSION:
                _reject()
```

- [ ] **Step 4: 跑测试确认通过**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain -q
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy
```

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-03): 素材宽高按 kind 分叉，音频不再被迫编造画幅"
```

---

### Task 2: 新建 timeline.py —— ID、轨道种类、转场、TimelineClip

**Files:**
- Create: `backend/src/automation_tool/control_plane/domain/timeline.py`
- Test: `backend/tests/unit/control_plane/domain/test_timeline.py`

**Interfaces:**
- Consumes: `MaterialId`、`MAX_MATERIAL_DURATION_MS`（来自 `domain/material.py`）；`ResourceId`（来自 `domain/resource_ids.py`）
- Produces: 供 T3/T4 使用 ——
  - `InvalidTimelineModel(ValueError)`
  - `TimelineId(ResourceId)`，`_resource = "timeline"`
  - `TimelineTrackKind`：`VISUAL/NARRATION/AMBIENT/MUSIC/CAPTION`
  - `TransitionKind`：`FADE/DISSOLVE/WIPE`（**无 CUT**）
  - `TimelineTransition(kind, duration_ms)`
  - `TimelineClip(clip_id, start_ms, duration_ms, source_material_id, source_in_ms, source_out_ms, text, gain_db, transition_in)`，只读属性 `end_ms`
  - 常量 `MAX_TIMELINE_DURATION_MS=600_000`、`MAX_CLIPS_PER_TRACK=512`、`MAX_TRANSITION_DURATION_MS=10_000`、`MAX_CLIP_TEXT_CHARACTERS=2_000`、`MIN_GAIN_DB=-60.0`、`MAX_GAIN_DB=12.0`
  - 模块私有 `_reject()`、`_validate_text()`、`_validate_timestamp()`、`_LOCAL_ID_PATTERN`

**本 task 只做到 clip 为止**，`TimelineTrack`/`Timeline` 是 T3/T4。

**为什么删掉 `TransitionKind.CUT`**：硬切就是「没有转场」，已由 `transition_in=None` 表达。保留 `CUT` 等于同一个状态有两种写法，改一处忘另一处就是定时炸弹（全局规则「代码复用原则」的典型反例）。

- [ ] **Step 1: 写会失败的测试**

新建 `backend/tests/unit/control_plane/domain/test_timeline.py`：

```python
"""Local editing timeline invariants: what plays when, from where, how loud."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_GAIN_DB,
    MAX_TIMELINE_DURATION_MS,
    MAX_TRANSITION_DURATION_MS,
    MIN_GAIN_DB,
    InvalidTimelineModel,
    TimelineClip,
    TimelineId,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)


def test_timeline_id_is_a_uuid4_resource_id() -> None:
    identifier = TimelineId.new()
    assert TimelineId.parse(str(identifier)) == identifier


def test_timeline_id_rejects_a_foreign_identifier_type() -> None:
    with pytest.raises(InvalidResourceId):
        TimelineId.parse(MaterialId.new())


def test_invalid_timeline_model_is_a_value_error() -> None:
    assert issubclass(InvalidTimelineModel, ValueError)


def test_track_kinds_split_one_audio_lane_into_three() -> None:
    assert {kind.value for kind in TimelineTrackKind} == {
        "visual",
        "narration",
        "ambient",
        "music",
        "caption",
    }


def test_a_hard_cut_is_the_absence_of_a_transition_not_a_kind_of_one() -> None:
    assert {kind.value for kind in TransitionKind} == {"fade", "dissolve", "wipe"}


@pytest.mark.parametrize("duration_ms", [0, -1, MAX_TRANSITION_DURATION_MS + 1, 1.0, True])
def test_transition_rejects_an_unusable_duration(duration_ms: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTransition(TransitionKind.FADE, duration_ms)  # type: ignore[arg-type]


def _media_clip(**overrides: object) -> TimelineClip:
    """A valid clip that plays a slice of one material."""
    defaults: dict[str, object] = {
        "clip_id": "clip-1",
        "start_ms": 0,
        "duration_ms": 3_000,
        "source_material_id": MaterialId.new(),
        "source_in_ms": 5_000,
        "source_out_ms": 8_000,
        "text": None,
        "gain_db": None,
        "transition_in": None,
    }
    defaults.update(overrides)
    return TimelineClip(**defaults)  # type: ignore[arg-type]


def _caption_clip(**overrides: object) -> TimelineClip:
    """A valid clip that draws text and plays nothing."""
    defaults: dict[str, object] = {
        "clip_id": "caption-1",
        "start_ms": 0,
        "duration_ms": 3_000,
        "source_material_id": None,
        "source_in_ms": None,
        "source_out_ms": None,
        "text": "第一句字幕",
        "gain_db": None,
        "transition_in": None,
    }
    defaults.update(overrides)
    return TimelineClip(**defaults)  # type: ignore[arg-type]


def test_a_media_clip_states_where_on_the_film_and_where_in_the_source() -> None:
    clip = _media_clip()
    assert clip.end_ms == 3_000
    assert clip.source_out_ms is not None and clip.source_in_ms is not None
    assert clip.source_out_ms - clip.source_in_ms == clip.duration_ms


def test_first_release_takes_the_slice_at_its_own_speed() -> None:
    """No speed change: the slice length must equal the length it occupies."""
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=0, source_out_ms=6_000)  # 2x fast-forward
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=0, source_out_ms=1_500)  # slow motion


@pytest.mark.parametrize(
    ("source_in_ms", "source_out_ms"),
    [(5_000, None), (None, 8_000)],
)
def test_a_source_window_is_stated_at_both_ends_or_neither(
    source_in_ms: object, source_out_ms: object
) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=source_in_ms, source_out_ms=source_out_ms)


def test_a_still_source_may_omit_the_window_it_has_no_time_axis() -> None:
    assert _media_clip(source_in_ms=None, source_out_ms=None).source_in_ms is None


def test_a_source_window_cannot_start_before_the_source_does() -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=-1, source_out_ms=2_999)


def test_text_has_nothing_to_slice() -> None:
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(source_in_ms=0, source_out_ms=3_000)


def test_a_clip_either_plays_a_source_or_draws_text_never_both_nor_neither() -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(text="同时带文字")
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text=None)


@pytest.mark.parametrize(
    "gain_db", [MIN_GAIN_DB - 0.1, MAX_GAIN_DB + 0.1, 0, True, "0.0"]
)
def test_gain_is_a_float_inside_the_usable_range(gain_db: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(gain_db=gain_db)


def test_gain_accepts_the_range_ends() -> None:
    assert _media_clip(gain_db=MIN_GAIN_DB).gain_db == MIN_GAIN_DB
    assert _media_clip(gain_db=MAX_GAIN_DB).gain_db == MAX_GAIN_DB


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clip_id", "Bad_ID"),
        ("clip_id", ""),
        ("start_ms", -1),
        ("start_ms", 1.0),
        ("duration_ms", 0),
        ("duration_ms", MAX_TIMELINE_DURATION_MS + 1),
        ("duration_ms", True),
        ("source_material_id", "not-an-id"),
        ("transition_in", "fade"),
    ],
)
def test_clip_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(**{field: value})


def test_caption_text_is_bounded_and_free_of_control_characters() -> None:
    assert _caption_clip(text="第一行\n第二行").text == "第一行\n第二行"
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="x" * (MAX_CLIP_TEXT_CHARACTERS + 1))
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="带\x00空字符")
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="  前后有空白  ")
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py -q
```

预期：`ModuleNotFoundError: No module named 'automation_tool.control_plane.domain.timeline'`。

- [ ] **Step 3: 写最小实现**

新建 `backend/src/automation_tool/control_plane/domain/timeline.py`：

```python
"""Local editing timeline: what plays when, taken from where, at what level."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, final

from automation_tool.control_plane.domain.material import (
    MAX_MATERIAL_DURATION_MS,
    MaterialId,
)
from automation_tool.control_plane.domain.resource_ids import ResourceId

MAX_TIMELINE_DURATION_MS: Final = 600_000
MIN_TIMELINE_DURATION_MS: Final = 100
MAX_CLIPS_PER_TRACK: Final = 512
MAX_TRANSITION_DURATION_MS: Final = 10_000
MAX_CLIP_TEXT_CHARACTERS: Final = 2_000
MIN_GAIN_DB: Final = -60.0
MAX_GAIN_DB: Final = 12.0

_LOCAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class InvalidTimelineModel(ValueError):
    """A timeline domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Timeline model is invalid")


@final
class TimelineId(ResourceId):
    """Stable identifier for one timeline lineage."""

    __slots__ = ()
    _resource = "timeline"


class TimelineTrackKind(StrEnum):
    """One lane of the film. The three audio lanes mix differently.

    `NARRATION` drives the ducking sidechain, `AMBIENT` and `MUSIC` get
    ducked by it — one `AUDIO` lane could not say which was which.
    """

    VISUAL = "visual"
    NARRATION = "narration"
    AMBIENT = "ambient"
    MUSIC = "music"
    CAPTION = "caption"


AUDIBLE_TRACK_KINDS: Final = frozenset(
    {TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC}
)


class TransitionKind(StrEnum):
    """How one visual clip gives way to the next.

    A hard cut is the absence of a transition — `transition_in=None` — so
    there is deliberately no `CUT` member: two spellings of one state is
    how they drift apart.
    """

    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE = "wipe"


def _reject() -> Never:
    raise InvalidTimelineModel


def _validate_text(value: object, *, maximum: int, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _reject()
    for character in value:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            _reject()


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


@dataclass(frozen=True, slots=True)
class TimelineTransition:
    kind: TransitionKind
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, TransitionKind)
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TRANSITION_DURATION_MS
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class TimelineClip:
    """One thing happening on one lane, for one stretch of the film."""

    clip_id: str
    start_ms: int
    duration_ms: int
    source_material_id: MaterialId | None
    source_in_ms: int | None
    source_out_ms: int | None
    text: str | None
    gain_db: float | None
    transition_in: TimelineTransition | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.clip_id, str)
            or _LOCAL_ID_PATTERN.fullmatch(self.clip_id) is None
            or type(self.start_ms) is not int
            or self.start_ms < 0
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
            or (
                self.source_material_id is not None
                and not isinstance(self.source_material_id, MaterialId)
            )
            or (
                self.transition_in is not None
                and not isinstance(self.transition_in, TimelineTransition)
            )
        ):
            _reject()
        _validate_text(self.text, maximum=MAX_CLIP_TEXT_CHARACTERS, optional=True)
        if (self.source_material_id is None) == (self.text is None):
            _reject()
        self._validate_source_window()
        self._validate_gain()

    def _validate_source_window(self) -> None:
        """Where in the source this slice comes from — at both ends or neither.

        Omitting it means the source has no time axis of its own: a still
        image occupies `duration_ms` without any stretch of it being taken.
        """
        if (self.source_in_ms is None) != (self.source_out_ms is None):
            _reject()
        if self.source_in_ms is None:
            return
        if self.source_material_id is None:
            _reject()
        if (
            type(self.source_in_ms) is not int
            or type(self.source_out_ms) is not int
            or self.source_in_ms < 0
            or self.source_out_ms > MAX_MATERIAL_DURATION_MS
        ):
            _reject()
        if self.source_out_ms - self.source_in_ms != self.duration_ms:
            _reject()

    def _validate_gain(self) -> None:
        if self.gain_db is None:
            return
        if type(self.gain_db) is not float or not MIN_GAIN_DB <= self.gain_db <= MAX_GAIN_DB:
            _reject()

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


__all__ = [
    "AUDIBLE_TRACK_KINDS",
    "MAX_CLIPS_PER_TRACK",
    "MAX_CLIP_TEXT_CHARACTERS",
    "MAX_GAIN_DB",
    "MAX_TIMELINE_DURATION_MS",
    "MAX_TRANSITION_DURATION_MS",
    "MIN_GAIN_DB",
    "MIN_TIMELINE_DURATION_MS",
    "InvalidTimelineModel",
    "TimelineClip",
    "TimelineId",
    "TimelineTrackKind",
    "TimelineTransition",
    "TransitionKind",
]
```

注意 `_validate_source_window` 里 mypy 会因为 `self.source_out_ms` 的 `int | None` 抱怨 —— 上面的写法先做了 `type(...) is not int` 的短路拒绝，若 mypy 仍不收敛，用局部变量固定窄化：

```python
        source_in = self.source_in_ms
        source_out = self.source_out_ms
        if type(source_in) is not int or type(source_out) is not int:
            _reject()
```

（`_reject()` 返回 `Never`，mypy 会据此窄化后续分支。）

- [ ] **Step 4: 跑测试确认通过**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py -q
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy
```

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/timeline.py \
        backend/tests/unit/control_plane/domain/test_timeline.py
git commit -m "feat(le-03): 剪辑片段补入出点与音量，锁死首期不变速"
```

---

### Task 3: TimelineTrack —— 按轨道种类分叉的形状与排布

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/timeline.py`
- Test: `backend/tests/unit/control_plane/domain/test_timeline.py`

**Interfaces:**
- Consumes: T2 产出的 `TimelineClip`、`TimelineTrackKind`、`AUDIBLE_TRACK_KINDS`、`MAX_CLIPS_PER_TRACK`、`_reject`、`_LOCAL_ID_PATTERN`
- Produces: `TimelineTrack(track_id, kind, clips)`，供 T4 组装

**要表达的规则（每种轨道都必须有自己的一条，不许留白）：**

| 轨道 | clip 形状 | 排布 |
| --- | --- | --- |
| `CAPTION` | 只画字：无素材、无音量、无转场 | 可留白，不可重叠 |
| `VISUAL` | 只放画面：有素材、无文字、无音量；可有转场 | **首尾相接**，第一条从 0 开始；有转场时按重叠计算起点 |
| `NARRATION`/`AMBIENT`/`MUSIC` | 有素材、无文字、**必须有音量**、不可有转场 | 可留白，不可重叠 |

> **T2 实施后的修订（2026-07-29）**：T2 的实现者穷举字段组合时发现原计划的参考实现有洞——字幕片段与「省略窗口的静态素材」都能带 `gain_db`，而两者都没有声音可调。他在 `TimelineClip._validate_gain` 补了「有 `gain_db` 必须有 `source_in_ms`」的拒绝分支。
>
> **这条修订倒灌进本 task，有两个后果，必须照办：**
>
> **T2 终审后的第二轮修订**：T2 的代码质量审查（实测 112 种字段组合 + 分支覆盖率）另外定下两件与本 task 相关的事：
>
> - **转场「不得吞掉本片段」这半条规则下沉到了 `TimelineClip`**（实测发现 10 秒淡入可以挂在 3 秒片段上，而片段自己就掌握判断所需的全部信息）。所以本 task 的轨道层只判「不得吞掉**上一个**片段」——那半条才真的需要邻居。下面的实现已按此改写，别再写 `min(previous_duration, clip.duration_ms)`。
> - **`_validate_timestamp` 已从 `timeline.py` 删除**（在 T2 里它是无调用者的死代码，且违反 TDD 铁律）。它由 T4 连同 `Timeline.created_at` 的测试一起加回来，本 task 用不到它。
>
> **T2 实施中的第一轮修订**，两个后果必须照办：
>
> 1. 音轨分支里**不要再写 `clip.source_in_ms is None`** —— 它已成死代码：有 `gain_db` 就必有窗口（clip 级保证），没 `gain_db` 则 `clip.gain_db is None` 这个析取项先开火，第二项永远到不了。音轨「必须有入出点」这条现在由 clip 级**传递保证**：轨道要求有音量 → 音量要求有窗口。
> 2. 原计划里两条测试会**为错误的理由通过**（在 `pytest.raises` 块里，clip 构造阶段就抛了，根本没轮到 `TimelineTrack`）。下面 Step 1 已改写，照改写后的版本写。

**为什么视觉轨要「首尾相接且转场按重叠算」**：`xfade` 的输出长度是 `a + b - transition`，两段真的会重叠播放。如果时间轴按「紧挨着不重叠」记，那 `Timeline.duration_ms` 就跟真实成片长度对不上——台账里就会出现一个自己骗自己的数字。所以带转场的一条，起点必须正好等于「上一条结束 - 转场时长」。

**为什么音量对视觉轨必须为 None**：素材原声走 `AMBIENT` 独立轨（设计 §5），视觉轨只出画面。视觉 clip 带音量就是两处都能调同一个东西。

- [ ] **Step 1: 写会失败的测试**

在 `test_timeline.py` 末尾追加：

```python
def _visual_track(**overrides: object) -> TimelineTrack:
    defaults: dict[str, object] = {
        "track_id": "visual",
        "kind": TimelineTrackKind.VISUAL,
        "clips": (
            _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
            _media_clip(
                clip_id="visual-2",
                start_ms=3_000,
                duration_ms=4_000,
                source_in_ms=0,
                source_out_ms=4_000,
            ),
        ),
    }
    defaults.update(overrides)
    return TimelineTrack(**defaults)  # type: ignore[arg-type]


def _audible_clip(**overrides: object) -> TimelineClip:
    """A clip fit for an audible lane: it states a level and a stretch."""
    defaults: dict[str, object] = {"gain_db": -12.0}
    defaults.update(overrides)
    return _media_clip(**defaults)


def test_a_visual_track_runs_end_to_end_from_zero() -> None:
    track = _visual_track()
    assert track.clips[0].start_ms == 0
    assert track.clips[1].start_ms == track.clips[0].end_ms


def test_a_visual_track_refuses_a_gap_that_would_render_as_black() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=3_500,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                ),
            )
        )


def test_a_visual_track_refuses_to_start_late() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(_media_clip(clip_id="visual-1", start_ms=500, duration_ms=3_000),)
        )


def test_a_transition_overlaps_its_two_clips_so_the_film_really_is_that_long() -> None:
    """xfade renders a + b - transition; the timeline must say the same."""
    track = _visual_track(
        clips=(
            _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
            _media_clip(
                clip_id="visual-2",
                start_ms=2_200,
                duration_ms=4_000,
                source_in_ms=0,
                source_out_ms=4_000,
                transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
            ),
        )
    )
    assert track.clips[-1].end_ms == 6_200


def test_a_transition_that_does_not_overlap_is_rejected() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=3_000,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
                ),
            )
        )


def test_a_transition_cannot_swallow_either_clip_whole() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="visual-1", start_ms=0, duration_ms=800),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=0,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
                ),
            )
        )


def test_the_first_clip_has_nothing_to_transition_from() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(
                    clip_id="visual-1",
                    start_ms=0,
                    duration_ms=3_000,
                    transition_in=TimelineTransition(TransitionKind.FADE, 500),
                ),
            )
        )


def test_a_visual_clip_carries_no_level_of_its_own() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(_media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000, gain_db=0.0),)
        )


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_an_audible_track_states_a_level_for_every_clip(kind: TimelineTrackKind) -> None:
    TimelineTrack("sound", kind, (_audible_clip(clip_id="sound-1"),))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("sound", kind, (_media_clip(clip_id="sound-1"),))


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_no_audible_lane_will_take_a_clip_with_no_stretch_to_play(
    kind: TimelineTrackKind,
) -> None:
    """A windowless clip cannot carry a level, and an audible lane demands one.

    The clip itself is legal — a still image occupies time without playing
    any stretch of a source. It is this lane that has no use for it.
    """
    windowless = _media_clip(clip_id="sound-1", source_in_ms=None, source_out_ms=None)
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("sound", kind, (windowless,))


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_audio_gets_no_transitions_in_the_first_release(kind: TimelineTrackKind) -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack(
            "sound",
            kind,
            (
                _audible_clip(clip_id="sound-1", start_ms=0, duration_ms=3_000),
                _audible_clip(
                    clip_id="sound-2",
                    start_ms=3_000,
                    duration_ms=3_000,
                    source_in_ms=0,
                    source_out_ms=3_000,
                    transition_in=TimelineTransition(TransitionKind.FADE, 500),
                ),
            ),
        )


def test_a_silent_stretch_between_two_narration_clips_is_fine() -> None:
    track = TimelineTrack(
        "narration",
        TimelineTrackKind.NARRATION,
        (
            _audible_clip(clip_id="line-1", start_ms=0, duration_ms=2_000,
                          source_in_ms=0, source_out_ms=2_000),
            _audible_clip(clip_id="line-2", start_ms=2_600, duration_ms=2_000,
                          source_in_ms=0, source_out_ms=2_000),
        ),
    )
    assert track.clips[1].start_ms > track.clips[0].end_ms


@pytest.mark.parametrize(
    "kind",
    [
        TimelineTrackKind.NARRATION,
        TimelineTrackKind.AMBIENT,
        TimelineTrackKind.MUSIC,
        TimelineTrackKind.CAPTION,
    ],
)
def test_no_track_lets_two_clips_play_over_each_other(kind: TimelineTrackKind) -> None:
    first = _caption_clip if kind is TimelineTrackKind.CAPTION else _audible_clip
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack(
            "lane",
            kind,
            (
                first(clip_id="lane-1", start_ms=0, duration_ms=2_000,
                      **({} if kind is TimelineTrackKind.CAPTION
                         else {"source_in_ms": 0, "source_out_ms": 2_000})),
                first(clip_id="lane-2", start_ms=1_500, duration_ms=2_000,
                      **({} if kind is TimelineTrackKind.CAPTION
                         else {"source_in_ms": 0, "source_out_ms": 2_000})),
            ),
        )


def test_a_caption_track_only_draws_text() -> None:
    TimelineTrack("caption", TimelineTrackKind.CAPTION, (_caption_clip(),))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("caption", TimelineTrackKind.CAPTION, (_media_clip(),))


def test_a_caption_lane_refuses_a_clip_that_wants_to_dissolve() -> None:
    """A caption appears and disappears; only the picture lane dissolves.

    The level half of this rule lives one layer down — a caption clip
    cannot carry `gain_db` at all, so it never reaches a lane. See
    `test_gain_requires_something_audible_to_adjust` in Task 2.
    """
    dissolving = _caption_clip(transition_in=TimelineTransition(TransitionKind.FADE, 300))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("caption", TimelineTrackKind.CAPTION, (dissolving,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("track_id", "Bad_ID"),
        ("kind", "visual"),
        ("clips", ()),
        ("clips", [_media_clip()]),
    ],
)
def test_track_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(**{field: value})


def test_a_track_refuses_two_clips_with_the_same_id() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="same", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="same",
                    start_ms=3_000,
                    duration_ms=3_000,
                    source_in_ms=0,
                    source_out_ms=3_000,
                ),
            )
        )
```

记得把 `TimelineTrack` 加进文件顶部的 import。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py -q
```

预期：`ImportError: cannot import name 'TimelineTrack'`。

- [ ] **Step 3: 写最小实现**

在 `timeline.py` 的 `TimelineClip` 之后加入：

```python
@dataclass(frozen=True, slots=True)
class TimelineTrack:
    """One lane of the film, and everything scheduled on it."""

    track_id: str
    kind: TimelineTrackKind
    clips: tuple[TimelineClip, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.track_id, str)
            or _LOCAL_ID_PATTERN.fullmatch(self.track_id) is None
            or not isinstance(self.kind, TimelineTrackKind)
            or not isinstance(self.clips, tuple)
            or not 1 <= len(self.clips) <= MAX_CLIPS_PER_TRACK
            or any(not isinstance(clip, TimelineClip) for clip in self.clips)
            or len({clip.clip_id for clip in self.clips}) != len(self.clips)
        ):
            _reject()
        for clip in self.clips:
            self._validate_clip_shape(clip)
        self._validate_layout()

    def _validate_clip_shape(self, clip: TimelineClip) -> None:
        """What a clip on THIS lane is allowed to carry.

        Every kind gets a rule. A kind with no rule of its own is how a
        model ends up accepting states nobody can render.
        """
        if self.kind is TimelineTrackKind.CAPTION:
            if (
                clip.source_material_id is not None
                or clip.gain_db is not None
                or clip.transition_in is not None
            ):
                _reject()
            return
        if clip.text is not None:
            _reject()
        if self.kind is TimelineTrackKind.VISUAL:
            # The picture lane carries no level of its own; a material's own
            # sound rides the separate AMBIENT lane so it can be ducked.
            if clip.gain_db is not None:
                _reject()
            return
        # NARRATION / AMBIENT / MUSIC: every clip states its level, and the
        # first release mixes them with no crossfades. Having a level already
        # implies having a stretch to play — `TimelineClip` enforces that —
        # so checking the window again here would be unreachable.
        if clip.gain_db is None or clip.transition_in is not None:
            _reject()

    def _validate_layout(self) -> None:
        """Where the clips sit relative to each other.

        The picture lane runs end to end from zero — a gap would render as
        black nobody asked for. A transition is a real overlap: `xfade`
        outputs `a + b - transition`, so the incoming clip must start
        exactly that much before the outgoing one ends, or the timeline's
        own duration stops matching the film it describes.
        """
        previous_end = 0
        previous_duration = 0
        for index, clip in enumerate(self.clips):
            if self.kind is not TimelineTrackKind.VISUAL:
                if clip.start_ms < previous_end:
                    _reject()
            else:
                transition = clip.transition_in
                overlap = 0 if transition is None else transition.duration_ms
                # `TimelineClip` already refuses a transition that would swallow
                # the incoming clip. Only the outgoing one needs a neighbour to
                # judge, so only that half lives here.
                if transition is not None and (index == 0 or overlap >= previous_duration):
                    _reject()
                if clip.start_ms != previous_end - overlap:
                    _reject()
            previous_end = clip.end_ms
            previous_duration = clip.duration_ms

    @property
    def end_ms(self) -> int:
        return self.clips[-1].end_ms
```

把 `"TimelineTrack"` 加进 `__all__`（保持字母序）。

- [ ] **Step 4: 跑测试确认通过**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain -q
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy
```

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/timeline.py \
        backend/tests/unit/control_plane/domain/test_timeline.py
git commit -m "feat(le-03): 轨道按种类分叉，画面轨首尾相接、转场按真实重叠计算"
```

---

### Task 4: Timeline —— 轨道种类唯一、画面轨必需且末端等于总时长

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/timeline.py`
- Test: `backend/tests/unit/control_plane/domain/test_timeline.py`

**Interfaces:**
- Consumes: T2/T3 产出的全部
- Produces: `Timeline(timeline_id, revision, duration_ms, tracks, created_at)`，供 LE-04 接到 `EditingProject`、LE-05 落库；以及本 task 加回的模块私有 `_validate_timestamp`

> **本 task 要把 `_validate_timestamp` 加回来。** T2 曾照原计划把它写进 `timeline.py`，但当时无任何调用者——T2 的终审判定它是死代码（覆盖率显示它永不可达，且违反「没有失败测试就没有生产代码」），已删除，连同 `from datetime import UTC, datetime` 一起。本 task 是它第一个真实调用者（`Timeline.created_at`），请**先写会失败的时间戳测试，再把函数与 import 一起加回**。函数体照抄 `material.py` / `video_creation.py` 里的同名实现。

**规则：**

1. 每种轨道最多一条（首期渲染管线是单条 concat 链，两条画面轨意味着画中画，`§5` 不做）
2. 必须有画面轨
3. 画面轨末端**严格等于** `duration_ms` —— 不允许尾部黑屏
4. 所有 clip 都不得越过 `duration_ms`
5. `revision >= 1`，`created_at` 必须是带 UTC 时区的 datetime

- [ ] **Step 1: 写会失败的测试**

追加到 `test_timeline.py`（并把 `Timeline`、`MIN_TIMELINE_DURATION_MS` 加进 import，另需 `from datetime import UTC, datetime`）：

```python
def _timeline(**overrides: object) -> Timeline:
    defaults: dict[str, object] = {
        "timeline_id": TimelineId.new(),
        "revision": 1,
        "duration_ms": 7_000,
        "tracks": (_visual_track(),),
        "created_at": datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Timeline(**defaults)  # type: ignore[arg-type]


def test_a_timeline_is_as_long_as_its_picture_lane() -> None:
    assert _timeline().duration_ms == _visual_track().end_ms


def test_a_timeline_refuses_to_claim_a_length_its_picture_does_not_fill() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(duration_ms=9_000)
    with pytest.raises(InvalidTimelineModel):
        _timeline(duration_ms=5_000)


def test_a_timeline_without_a_picture_lane_is_not_a_film() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                TimelineTrack(
                    "narration",
                    TimelineTrackKind.NARRATION,
                    (_audible_clip(clip_id="line-1", start_ms=0, duration_ms=7_000,
                                   source_in_ms=0, source_out_ms=7_000),),
                ),
            )
        )


def test_each_lane_appears_at_most_once() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(tracks=(_visual_track(track_id="visual-a"),
                          _visual_track(track_id="visual-b")))


def test_a_full_timeline_carries_picture_narration_ambient_music_and_captions() -> None:
    timeline = _timeline(
        tracks=(
            _visual_track(),
            TimelineTrack(
                "narration",
                TimelineTrackKind.NARRATION,
                (_audible_clip(clip_id="line-1", start_ms=0, duration_ms=6_000,
                               source_in_ms=0, source_out_ms=6_000),),
            ),
            TimelineTrack(
                "ambient",
                TimelineTrackKind.AMBIENT,
                (_audible_clip(clip_id="room-1", start_ms=0, duration_ms=7_000,
                               source_in_ms=0, source_out_ms=7_000),),
            ),
            TimelineTrack(
                "music",
                TimelineTrackKind.MUSIC,
                (_audible_clip(clip_id="bgm-1", start_ms=0, duration_ms=7_000,
                               source_in_ms=0, source_out_ms=7_000, gain_db=-24.0),),
            ),
            TimelineTrack(
                "caption",
                TimelineTrackKind.CAPTION,
                (_caption_clip(clip_id="cap-1", start_ms=0, duration_ms=3_000),),
            ),
        )
    )
    assert len(timeline.tracks) == len(TimelineTrackKind)


def test_nothing_may_run_past_the_end_of_the_film() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                _visual_track(),
                TimelineTrack(
                    "music",
                    TimelineTrackKind.MUSIC,
                    (_audible_clip(clip_id="bgm-1", start_ms=0, duration_ms=9_000,
                                   source_in_ms=0, source_out_ms=9_000),),
                ),
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeline_id", "not-an-id"),
        ("revision", 0),
        ("revision", 1.0),
        ("revision", True),
        ("duration_ms", MIN_TIMELINE_DURATION_MS - 1),
        ("duration_ms", MAX_TIMELINE_DURATION_MS + 1),
        ("tracks", ()),
        ("tracks", [_visual_track()]),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
    ],
)
def test_timeline_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(**{field: value})


def test_a_timeline_refuses_two_lanes_with_the_same_id() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                _visual_track(track_id="same"),
                TimelineTrack(
                    "same",
                    TimelineTrackKind.CAPTION,
                    (_caption_clip(clip_id="cap-1", start_ms=0, duration_ms=3_000),),
                ),
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py -q
```

预期：`ImportError: cannot import name 'Timeline'`。

- [ ] **Step 3: 写最小实现**

```python
@dataclass(frozen=True, slots=True)
class Timeline:
    """One editable cut of one film.

    It has no owning project yet — `EditingProject` arrives in LE-04, and a
    field pointing at a type that does not exist would be worse than none.
    """

    timeline_id: TimelineId
    revision: int
    duration_ms: int
    tracks: tuple[TimelineTrack, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timeline_id, TimelineId)
            or type(self.revision) is not int
            or self.revision < 1
            or type(self.duration_ms) is not int
            or not MIN_TIMELINE_DURATION_MS <= self.duration_ms <= MAX_TIMELINE_DURATION_MS
            or not isinstance(self.tracks, tuple)
            or not 1 <= len(self.tracks) <= len(TimelineTrackKind)
            or any(not isinstance(track, TimelineTrack) for track in self.tracks)
            or len({track.track_id for track in self.tracks}) != len(self.tracks)
            or len({track.kind for track in self.tracks}) != len(self.tracks)
        ):
            _reject()
        _validate_timestamp(self.created_at)
        picture = self.track_of(TimelineTrackKind.VISUAL)
        if picture is None or picture.end_ms != self.duration_ms:
            _reject()
        if any(clip.end_ms > self.duration_ms for track in self.tracks for clip in track.clips):
            _reject()

    def track_of(self, kind: TimelineTrackKind) -> TimelineTrack | None:
        """The one track on that lane, or None. Lanes are unique by construction."""
        for track in self.tracks:
            if track.kind is kind:
                return track
        return None
```

把 `"Timeline"` 加进 `__all__`。

- [ ] **Step 4: 跑测试确认通过**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain -q
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy
```

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/timeline.py \
        backend/tests/unit/control_plane/domain/test_timeline.py
git commit -m "feat(le-03): 时间轴每种轨道最多一条，画面轨末端严格等于成片长度"
```

---

### Task 5: 退役 video_creation.py 里的旧 Timeline 并收口台账

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/video_creation.py`
- Modify: `backend/src/automation_tool/control_plane/domain/__init__.py`
- Modify: `backend/tests/unit/control_plane/domain/test_video_creation.py`
- Modify: `docs/local-video-editing-roadmap.md`
- Create: `docs/development/LE-03.md`

**Interfaces:**
- Consumes: T2/T3/T4 产出的 `timeline.py` 全族
- Produces: `domain/__init__.py` 从 `timeline` 转出口 Timeline 全族；`video_creation.py` 不再定义任何 Timeline 类型

**要删的（全部来自 `video_creation.py`）：** `TimelineId`、`TimelineTrackKind`、`TransitionKind`、`TimelineTransition`、`TimelineClip`、`TimelineTrack`、`Timeline`，以及只服务它们的常量 `MAX_TRACKS`、`MAX_CLIPS_PER_TRACK`。

**`RenderJob.timeline_id` 怎么办**：它引用 `TimelineId`。改成 `from automation_tool.control_plane.domain.timeline import TimelineId`。两条线（创作线与剪辑线）都从时间轴渲染，共用同一个 ID 类型是事实而非耦合。

**注意 `_LOCAL_ID_PATTERN`**：删完 Timeline 后若 `video_creation.py` 里没有别的使用者，一并删掉（全局规则「重构后清理规范」）。删前用 grep 确认。

- [ ] **Step 1: 写会失败的测试**

在 `backend/tests/unit/control_plane/domain/test_video_creation.py` 顶部加一条守护用例，证明旧类型确实不在了：

```python
def test_the_creation_line_no_longer_defines_its_own_timeline() -> None:
    """One Timeline, in one module. Two would drift apart."""
    import automation_tool.control_plane.domain.video_creation as creation

    leftovers = [
        name
        for name in ("Timeline", "TimelineClip", "TimelineTrack", "TimelineTrackKind",
                     "TimelineTransition", "TransitionKind")
        if name in vars(creation)
    ]
    assert leftovers == []
```

再在 `test_timeline.py` 里加一条，证明转出口指向新模块：

```python
def test_the_domain_package_exports_the_local_editing_timeline() -> None:
    from automation_tool.control_plane import domain
    from automation_tool.control_plane.domain import timeline as module

    assert domain.Timeline is module.Timeline
    assert domain.TimelineTrackKind is module.TimelineTrackKind
    assert set(domain.TransitionKind) == set(module.TransitionKind)
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_video_creation.py::test_the_creation_line_no_longer_defines_its_own_timeline tests/unit/control_plane/domain/test_timeline.py::test_the_domain_package_exports_the_local_editing_timeline -q
```

预期：两条都失败（旧类型仍在 / 转出口仍指旧模块）。

- [ ] **Step 3: 写最小实现**

1. 从 `video_creation.py` 删掉上面列出的七个类型与两个常量；`RenderJob` 顶部改成从 `timeline` 导入 `TimelineId`；同步删掉 `__all__` 里的对应条目；如果 `_LOCAL_ID_PATTERN` 已无使用者则删掉它。
2. `domain/__init__.py`：把 Timeline 全族的导入从 `video_creation` 挪到 `timeline`，并补上 `TimelineTrack`、`TimelineTransition`、`TransitionKind`、`InvalidTimelineModel`、`AUDIBLE_TRACK_KINDS` 等新增名字；`__all__` 保持字母序。
3. `test_video_creation.py`：删掉所有构造旧 Timeline 的用例与 `_timeline` 工厂，删掉相关 import；`test_public_models_have_exact_provider_neutral_fields` 里的 Timeline 条目一并删掉。**注意保留并修好非 Timeline 的用例**——`test_storyboard_rejects_non_contiguous_scenes_and_timeline_rejects_overlap` 这类混了两件事的，拆成只测 storyboard 的那半。

- [ ] **Step 4: 跑全量确认通过**

```
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy
cd backend && .venv/bin/python -m automation_tool.control_plane.openapi_export --help >/dev/null 2>&1 || true
```

另外确认契约导出未受影响（Timeline 不在 OpenAPI 里，应当无 diff）：

```
cd backend && .venv/bin/automation-tool-export-openapi --output ../contracts/openapi/control-plane.v1.json --check
cd backend && .venv/bin/automation-tool-export-executor-schema --output ../contracts/protocol/executor-v1.schema.json --check
```

前端不受影响，但跑一次确认：

```
cd frontend && pnpm test -- --run src/app/no-cloud-editing.test.ts
```

- [ ] **Step 5: 写任务证据文件**

新建 `docs/development/LE-03.md`，开头必须有：

```markdown
# LE-03 Timeline 重写

用户可操作：否
证据类型：分层实现
```

正文记录：日期、提交列表、RED/GREEN、失败矩阵覆盖了哪些、**有意不做的边界**（本计划「本计划有意不做的事」整表照抄，含 LE-05 要补的跨聚合根校验）、清理证据（旧类型确已删除的 grep 结果）、文档证据。

**另外必须记进 LE-03.md 的五笔账**（都是 T1～T4 执行过程中实测得出的，不写下来就会丢）：

1. **考虑过并否决：把 `width`/`height` 收成 `FrameSize | None` 值对象。** T1 的代码质量审查提出（置信度 65），理由是能让「一半有一半没有」在类型层不可构造。**否决理由**：LE-05 建表时 ORM 要把它拍回两列，映射成本落在持久化边界；而运行时完整性已由 363 种 `kind × width × height` 组合的穷举证明（344 拒 / 19 接受 / 0 异常逃逸）。记为「考虑过并否决」而非默默忽略。

2. **`material.py` 有 11 行未覆盖，是 LE-02 的遗留，不是 LE-03 引入的。** 实测证据：在 LE-02 收口提交 `495e922` 上用 `PYTHONPATH` 指向该提交的源码跑 `test_material.py`，得 90%、同样 11 行未覆盖；T1 之后是 91%、仍是同样 11 行——**T1 自己的新代码全覆盖，既没恶化也没改善**。而门禁是 `fail_under = 100`（`backend/pyproject.toml:89`），也就是说 **LE-02 标 ✅ 已完成时，覆盖率门禁在 `material.py` 上就是红的**。这条要同步登记到台账 §7 的已知问题里，并明确归属。

3. **后端 CI 有三个全库红的门禁**，都不是本线引入：`ruff check .` 约 98 个错误、`mypy` 17 个错误（全在 `tests/unit/executor/` 下）、`pytest --cov` 的 `fail_under = 100`。三者都在 `.github/workflows/quality.yml` 的 backend job 里（第 57、58、61 行），意味着该 job 在 main 上就过不去。同步登记到台账 §7。

4. **`scripts/check_acceptance_evidence_depth.py` 在仓库里不存在**，但项目 `CLAUDE.md` §9.1 引用了它并称之为门禁。同步登记到台账 §7。

5. **一条方法论教训，写进 LE-03.md 供后续任务复用**：**coverage.py 把整个 `if` 当一个分支计，`or` 链里的子条件不单独计量**。所以一个永远为假的析取项能带着 100% 覆盖 / 零 partial branch 的成绩混过去——T3 就是这样漏掉字幕轨那处死判断的，只有逐项推导「在下层已有约束下这一项还可能为真吗」才查得出来。**门禁全绿只说明门禁看得见的那部分没问题。**

- [ ] **Step 6: 台账转 ✅**

`docs/local-video-editing-roadmap.md`：
- LE-03 行状态改 `✅ 已完成`
- LE-04 行的交付描述补一句：「并把 `Timeline` 接到 `EditingProject`（LE-03 有意未加 owner 字段）」
- 更新 §5「当前下一步」为 LE-04
- 更新各处计数，然后跑守护脚本：

```
python3 scripts/check_local_editing_roadmap_counts.py
backend/.venv/bin/python -m pytest scripts/test_check_local_editing_roadmap_counts.py -q
```

- [ ] **Step 7: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/video_creation.py \
        backend/src/automation_tool/control_plane/domain/__init__.py \
        backend/tests/unit/control_plane/domain/test_video_creation.py \
        backend/tests/unit/control_plane/domain/test_timeline.py \
        docs/development/LE-03.md \
        docs/local-video-editing-roadmap.md
git commit -m "refactor(le-03): 创作线交出 Timeline，全项目只剩一份定义"
```
