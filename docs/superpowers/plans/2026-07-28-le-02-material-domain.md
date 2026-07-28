# LE-02 Material 素材库领域对象 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立本地剪辑的素材库领域对象 `Material`，承载探测结果（时长、分辨率、内容摘要、有无声音、响度、有无人声、人声区间、转写、镜头边界）与 AI 理解结果（描述、标签），并把「用户改过的描述不被 AI 覆盖」这条不变式写进类型而不是写进注释。

**Architecture:** 纯领域层，无 I/O、无外部依赖。不可变 `@dataclass(frozen=True, slots=True)`，构造即校验，非法值一律拒绝。跟随 `video_creation.py` 既有风格：模块内定义本领域专属 ID，继承 `resource_ids.ResourceId`。

**Tech Stack:** Python 3.12 / dataclasses / StrEnum / pytest

## Global Constraints

- 工作树 `/Users/aventador/sourceCode/automation-tool/wt/smart-edit`，分支 `smart-edit`
- **中文 commit message**，conventional 前缀保留英文，冒号后用中文，无任何 AI 署名
- 禁止 `git add -A`；逐个文件显式 `git add`
- 后端测试：`cd backend && .venv/bin/python -m pytest <path> -v`
- **每个 Task 提交前必须跑 `ruff check`**（CI 在 `.github/workflows/quality.yml:57` 跑它）。LE-01 全程的门禁集里漏了后端 ruff——只跑了前端 lint——所以行长、盲断言这类问题一路不会暴露。`line-length = 100`，`B017` 禁止 `pytest.raises(Exception)`，断言具体异常类型
- **TDD 强制**：每个 Task 先写测试、运行看到失败、再写实现。禁止先写实现后补测试
- 长命令在前台跑完再继续，不得放后台后结束回合
- 领域层不得 import 任何 infrastructure、不得做文件或网络 I/O

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `backend/src/automation_tool/control_plane/domain/material.py` | 新建。`MaterialId`、`MaterialKind`、`DescriptionSource`、`InvalidMaterialModel`、`Material` 及其不变式 |
| `backend/tests/unit/control_plane/domain/test_material.py` | 新建。全部领域校验与不变式测试 |

单文件承载整个素材领域是有意的：这些类型只对彼此有意义，拆开会制造跨文件的循环引用。文件预计 250–300 行，仍在可一次读完的范围内。

**风格基准**：`backend/src/automation_tool/control_plane/domain/video_creation.py`。照抄它的 `_reject()` / `_validate_text()` 写法、`@final class XxxId(ResourceId)` 带 `__slots__ = ()` 与 `_resource`、`Final` 上限常量、模块级正则常量。

---

## Task 1: 基础类型（ID、枚举、异常）

**Files:**
- Create: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: `automation_tool.control_plane.domain.resource_ids.ResourceId`
- Produces: `MaterialId`、`MaterialKind`（video/image/audio）、`DescriptionSource`（ai/user）、`InvalidMaterialModel`。Task 2–5 全部建立在这些之上

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/control_plane/domain/test_material.py`：

```python
"""Material domain invariants for the local editing library."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    InvalidMaterialModel,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId


def test_material_id_is_a_uuid4_resource_id() -> None:
    identifier = MaterialId.new()
    assert MaterialId.parse(str(identifier)) == identifier


def test_material_id_rejects_a_foreign_identifier_type() -> None:
    from automation_tool.control_plane.domain.resource_ids import ArtifactId

    with pytest.raises(InvalidResourceId):
        MaterialId.parse(ArtifactId.new())


def test_material_kinds_are_exactly_the_three_supported() -> None:
    assert {kind.value for kind in MaterialKind} == {"video", "image", "audio"}


def test_description_sources_distinguish_ai_from_user() -> None:
    assert {source.value for source in DescriptionSource} == {"ai", "user"}


def test_invalid_material_model_is_a_value_error() -> None:
    assert issubclass(InvalidMaterialModel, ValueError)
```

- [ ] **Step 2: 运行，确认失败**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -v
```

Expected: 收集阶段就失败，`ModuleNotFoundError: No module named 'automation_tool.control_plane.domain.material'`。

- [ ] **Step 3: 写最小实现**

创建 `backend/src/automation_tool/control_plane/domain/material.py`：

```python
"""Local editing material library: what one source file is and what we know about it."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ResourceId

MAX_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1000
MAX_MATERIAL_DIMENSION: Final = 8192
MAX_DESCRIPTION_CHARACTERS: Final = 2_000
MAX_TRANSCRIPT_CHARACTERS: Final = 100_000
MAX_TAGS: Final = 32
MAX_TAG_CHARACTERS: Final = 32
MAX_SHOT_BOUNDARIES: Final = 4_096
MAX_SPEECH_SEGMENTS: Final = 4_096

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvalidMaterialModel(ValueError):
    """A material domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Material model is invalid")


@final
class MaterialId(ResourceId):
    """Stable identifier for one imported source file."""

    __slots__ = ()
    _resource = "material"


class MaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class DescriptionSource(StrEnum):
    """Who wrote the description currently held by a material.

    The distinction exists to keep a later AI pass from overwriting what a
    user typed: `USER` is a terminal state for the description field.
    """

    AI = "ai"
    USER = "user"


def _reject() -> Never:
    raise InvalidMaterialModel
```

- [ ] **Step 4: 运行，确认通过**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -v
```

Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-02): 素材领域的基础类型

MaterialId、MaterialKind（视频/图片/音频）、DescriptionSource（ai/user）与
InvalidMaterialModel。DescriptionSource 的存在是为了让「用户改过的描述不被
AI 覆盖」成为类型层面的事实而不是注释里的约定。"
```

---

## Task 2: Material 核心字段与基础校验

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: Task 1 的全部类型
- Produces: `Material` 数据类，字段签名为
  `material_id: MaterialId`、`kind: MaterialKind`、`duration_ms: int | None`、`width: int`、`height: int`、`content_digest: str`。Task 3–5 在同一个类上追加字段

- [ ] **Step 1: 写失败测试（追加到同一测试文件）**

```python
def _video(**overrides: object) -> Material:
    """A valid video material, with named fields overridable per test."""
    defaults: dict[str, object] = {
        "material_id": MaterialId.new(),
        "kind": MaterialKind.VIDEO,
        "duration_ms": 15_000,
        "width": 1920,
        "height": 1080,
        "content_digest": "a" * 64,
    }
    defaults.update(overrides)
    return Material(**defaults)  # type: ignore[arg-type]


def test_a_valid_video_material_is_accepted() -> None:
    material = _video()
    assert material.kind is MaterialKind.VIDEO
    assert material.duration_ms == 15_000


def test_video_without_duration_is_rejected() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=None)


def test_image_must_not_carry_a_duration() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(kind=MaterialKind.IMAGE, duration_ms=15_000)


def test_image_without_duration_is_accepted() -> None:
    material = _video(kind=MaterialKind.IMAGE, duration_ms=None)
    assert material.duration_ms is None


@pytest.mark.parametrize("duration", [0, -1, MAX_MATERIAL_DURATION_MS + 1])
def test_duration_outside_the_supported_range_is_rejected(duration: int) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=duration)


@pytest.mark.parametrize("dimension", [0, -1, MAX_MATERIAL_DIMENSION + 1])
def test_dimensions_outside_the_supported_range_are_rejected(dimension: int) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(width=dimension)
    with pytest.raises(InvalidMaterialModel):
        _video(height=dimension)


@pytest.mark.parametrize(
    "digest",
    ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64],
    ids=["empty", "uppercase", "too-short", "too-long", "non-hex"],
)
def test_content_digest_must_be_lowercase_sha256(digest: str) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(content_digest=digest)


def test_material_is_immutable() -> None:
    material = _video()
    with pytest.raises(Exception):
        material.width = 640  # type: ignore[misc]
```

同时把 `Material` 与 `MAX_MATERIAL_DURATION_MS`、`MAX_MATERIAL_DIMENSION` 加进该文件顶部的 import。

- [ ] **Step 2: 运行，确认失败**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -v
```

Expected: `ImportError: cannot import name 'Material'`。

- [ ] **Step 3: 写最小实现（追加到 material.py）**

```python
@dataclass(frozen=True, slots=True)
class Material:
    """One imported source file and everything probing has learned about it."""

    material_id: MaterialId
    kind: MaterialKind
    duration_ms: int | None
    width: int
    height: int
    content_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.material_id, MaterialId)
            or not isinstance(self.kind, MaterialKind)
            or type(self.width) is not int
            or not 1 <= self.width <= MAX_MATERIAL_DIMENSION
            or type(self.height) is not int
            or not 1 <= self.height <= MAX_MATERIAL_DIMENSION
            or not isinstance(self.content_digest, str)
            or _SHA256_PATTERN.fullmatch(self.content_digest) is None
        ):
            _reject()
        self._validate_duration()

    def _validate_duration(self) -> None:
        if self.kind is MaterialKind.IMAGE:
            if self.duration_ms is not None:
                _reject()
            return
        if type(self.duration_ms) is not int or not 1 <= self.duration_ms <= MAX_MATERIAL_DURATION_MS:
            _reject()
```

在文件顶部补 `from dataclasses import dataclass`。

- [ ] **Step 4: 运行，确认通过**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -v
```

Expected: 全部通过（Task 1 的 5 项 + 本 Task 的项）

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-02): Material 核心字段与基础校验

尺寸、时长、内容摘要的取值范围，以及图片不得带时长、视频与音频必须带时长
这条按类型分叉的规则。摘要固定为小写 sha256，大写与非十六进制一律拒绝——
它要用来做去重，格式不统一等于去重失效。"
```

---

## Task 3: 音频与人声字段的交叉一致性

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: Task 2 的 `Material`
- Produces: `Material` 追加 `has_audio: bool`、`audio_loudness_lufs: float | None`、`has_speech: bool`、`speech_segments_ms: tuple[tuple[int, int], ...]`、`speech_transcript: str | None`。LE-07 探测与 LE-14 人声检测写入这些字段

**这些字段之间有真实的依赖关系，不是各自独立的可选值。** 三级漏斗（`silencedetect` → VAD → ASR）决定了：无声音就不可能有人声，无人声就不该有人声区间或转写。把这些写成构造期校验，后面的编排代码就不必到处做防御性判断。

- [ ] **Step 1: 写失败测试**

```python
def test_material_without_audio_must_not_carry_loudness() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=False, audio_loudness_lufs=-18.0)


def test_material_without_audio_cannot_have_speech() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=False, has_speech=True)


def test_material_without_speech_must_not_carry_segments() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=False, speech_segments_ms=((0, 1_000),))


def test_material_without_speech_must_not_carry_a_transcript() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=False, speech_transcript="讲了点什么")


def test_speech_material_carries_segments_and_transcript() -> None:
    material = _video(
        has_audio=True,
        has_speech=True,
        speech_segments_ms=((500, 3_000), (4_000, 9_000)),
        speech_transcript="第一句。第二句。",
    )
    assert material.speech_segments_ms == ((500, 3_000), (4_000, 9_000))


def test_speech_segments_must_be_ordered_and_disjoint() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((4_000, 9_000), (500, 3_000)))
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((0, 5_000), (3_000, 8_000)))


def test_speech_segment_must_not_be_empty_or_reversed() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((1_000, 1_000),))
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((3_000, 1_000),))


def test_speech_segment_must_not_exceed_the_material_duration() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(
            duration_ms=5_000,
            has_audio=True,
            has_speech=True,
            speech_segments_ms=((0, 6_000),),
        )
```

并把 `_video` 的 `defaults` 补上这五个字段的合法默认值：`has_audio=False`、`audio_loudness_lufs=None`、`has_speech=False`、`speech_segments_ms=()`、`speech_transcript=None`。

- [ ] **Step 2: 运行，确认失败**

Expected: `TypeError: Material.__init__() got an unexpected keyword argument 'has_audio'`

- [ ] **Step 3: 写最小实现**

在 `Material` 上追加字段，并在 `__post_init__` 末尾调用 `self._validate_audio()`：

```python
    has_audio: bool
    audio_loudness_lufs: float | None
    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...]
    speech_transcript: str | None
```

```python
    def _validate_audio(self) -> None:
        if type(self.has_audio) is not bool or type(self.has_speech) is not bool:
            _reject()
        if not self.has_audio:
            if (
                self.audio_loudness_lufs is not None
                or self.has_speech
            ):
                _reject()
        elif self.audio_loudness_lufs is not None and (
            type(self.audio_loudness_lufs) is not float or not -70.0 <= self.audio_loudness_lufs <= 0.0
        ):
            _reject()
        if not isinstance(self.speech_segments_ms, tuple):
            _reject()
        if not self.has_speech:
            if self.speech_segments_ms or self.speech_transcript is not None:
                _reject()
            return
        if not 1 <= len(self.speech_segments_ms) <= MAX_SPEECH_SEGMENTS:
            _reject()
        _validate_text(self.speech_transcript, maximum=MAX_TRANSCRIPT_CHARACTERS, optional=True)
        previous_end = 0
        for segment in self.speech_segments_ms:
            if (
                not isinstance(segment, tuple)
                or len(segment) != 2
                or any(type(value) is not int for value in segment)
            ):
                _reject()
            start, end = segment
            if start < previous_end or end <= start:
                _reject()
            if self.duration_ms is not None and end > self.duration_ms:
                _reject()
            previous_end = end
```

同时把 `video_creation.py` 的 `_validate_text` 照抄进本模块（含 `unicodedata` import 与控制字符检查），它是本文件唯一需要的文本校验。

- [ ] **Step 4: 运行，确认通过**

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-02): 音频与人声字段的交叉一致性

三级漏斗（silencedetect 到 VAD 到 ASR）决定了这些字段互相依赖：没声音就
不可能有人声，没人声就不该有区间和转写。写成构造期校验之后，后面的编排
代码不必到处做防御性判断。

人声区间要求有序、互不重叠、且不超过素材本身时长——它后面要当作可切点用，
乱序或越界的区间会直接产出一个剪不出来的片段。"
```

---

## Task 4: 镜头边界

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: Task 3 的 `Material`
- Produces: `Material` 追加 `shot_boundaries_ms: tuple[int, ...]`。LE-08 自适应抽帧写入，LE-16 选片段时把它当候选切点读

- [ ] **Step 1: 写失败测试**

```python
def test_shot_boundaries_are_strictly_increasing() -> None:
    material = _video(duration_ms=20_000, shot_boundaries_ms=(0, 4_000, 12_000))
    assert material.shot_boundaries_ms == (0, 4_000, 12_000)
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=20_000, shot_boundaries_ms=(4_000, 4_000))
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=20_000, shot_boundaries_ms=(12_000, 4_000))


def test_shot_boundary_must_fall_inside_the_material() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=5_000, shot_boundaries_ms=(0, 6_000))
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=5_000, shot_boundaries_ms=(-1,))


def test_image_carries_no_shot_boundaries() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(kind=MaterialKind.IMAGE, duration_ms=None, shot_boundaries_ms=(0,))
```

`_video` 的 defaults 补 `shot_boundaries_ms=()`。

- [ ] **Step 2: 运行，确认失败**

- [ ] **Step 3: 写最小实现**

```python
    def _validate_shot_boundaries(self) -> None:
        if not isinstance(self.shot_boundaries_ms, tuple):
            _reject()
        if not self.shot_boundaries_ms:
            return
        if self.duration_ms is None or len(self.shot_boundaries_ms) > MAX_SHOT_BOUNDARIES:
            _reject()
        previous = -1
        for boundary in self.shot_boundaries_ms:
            if type(boundary) is not int or boundary <= previous or not 0 <= boundary < self.duration_ms:
                _reject()
            previous = boundary
```

- [ ] **Step 4: 运行，确认通过**

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-02): 镜头边界校验

严格递增且落在素材时长之内。这些边界后面有两个用途：让多模态模型看懂
整条素材的结构，以及当作挑片段时的候选切点，所以越界或乱序的边界会直接
产出一个剪不出来的片段。图片没有镜头可言，带边界一律拒绝。"
```

---

## Task 5: 描述来源与 AI 覆盖保护

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/material.py`
- Test: `backend/tests/unit/control_plane/domain/test_material.py`

**Interfaces:**
- Consumes: Task 4 的 `Material`、Task 1 的 `DescriptionSource`
- Produces: `Material` 追加 `ai_description: str | None`、`ai_tags: tuple[str, ...]`、`description_source: DescriptionSource`、`described_at: datetime | None`，以及两个演进方法：
  - `with_ai_description(description: str, tags: tuple[str, ...], described_at: datetime) -> Material`
  - `with_user_description(description: str) -> Material`
  LE-13 素材理解调用前者，LE-18 素材库界面调用后者

**这是本任务真正的目的。** 台账那句「用户改过的描述不被 AI 覆盖」如果只写在文档里，实现时全靠调用方自觉；写成方法后，`with_ai_description` 在 `USER` 状态下直接返回原对象，覆盖不了就是覆盖不了。

- [ ] **Step 1: 写失败测试**

```python
from datetime import UTC, datetime


def test_a_fresh_material_has_no_description() -> None:
    material = _video()
    assert material.ai_description is None
    assert material.described_at is None
    assert material.description_source is DescriptionSource.AI


def test_ai_description_is_written_onto_an_undescribed_material() -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    material = _video().with_ai_description("室内，一个人在喝水", ("室内", "人物"), stamped)
    assert material.ai_description == "室内，一个人在喝水"
    assert material.ai_tags == ("室内", "人物")
    assert material.described_at == stamped
    assert material.description_source is DescriptionSource.AI


def test_ai_may_redescribe_material_it_described_itself() -> None:
    first = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    second = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    material = _video().with_ai_description("第一版", ("旧",), first)
    updated = material.with_ai_description("第二版", ("新",), second)
    assert updated.ai_description == "第二版"
    assert updated.described_at == second


def test_user_description_switches_the_source() -> None:
    material = _video().with_user_description("我自己写的说明")
    assert material.ai_description == "我自己写的说明"
    assert material.description_source is DescriptionSource.USER


def test_ai_cannot_overwrite_a_user_written_description() -> None:
    stamped = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    edited = _video().with_user_description("我自己写的说明")
    unchanged = edited.with_ai_description("AI 想改成这样", ("模型",), stamped)
    assert unchanged is edited
    assert unchanged.ai_description == "我自己写的说明"
    assert unchanged.description_source is DescriptionSource.USER


def test_user_may_rewrite_their_own_description() -> None:
    edited = _video().with_user_description("第一次写的")
    rewritten = edited.with_user_description("改了一版")
    assert rewritten.ai_description == "改了一版"
    assert rewritten.description_source is DescriptionSource.USER


def test_described_at_must_be_timezone_aware() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_description("说明", (), datetime(2026, 7, 28, 10, 0))


@pytest.mark.parametrize("tags", [("",), (" 前后有空格 ",), ("x" * (MAX_TAG_CHARACTERS + 1),)])
def test_tags_are_validated(tags: tuple[str, ...]) -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_description("说明", tags, stamped)


def test_tags_must_be_unique() -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_description("说明", ("室内", "室内"), stamped)
```

`_video` 的 defaults 补 `ai_description=None`、`ai_tags=()`、`description_source=DescriptionSource.AI`、`described_at=None`。

- [ ] **Step 2: 运行，确认失败**

Expected: `TypeError: Material.__init__() got an unexpected keyword argument 'ai_description'`

- [ ] **Step 3: 写最小实现**

字段追加，`__post_init__` 末尾调用 `self._validate_description()`：

```python
    def _validate_description(self) -> None:
        if not isinstance(self.description_source, DescriptionSource):
            _reject()
        _validate_text(self.ai_description, maximum=MAX_DESCRIPTION_CHARACTERS, optional=True)
        if not isinstance(self.ai_tags, tuple) or len(self.ai_tags) > MAX_TAGS:
            _reject()
        for tag in self.ai_tags:
            _validate_text(tag, maximum=MAX_TAG_CHARACTERS)
        if len(set(self.ai_tags)) != len(self.ai_tags):
            _reject()
        if self.described_at is not None and (
            not isinstance(self.described_at, datetime) or self.described_at.tzinfo is None
        ):
            _reject()
        if self.ai_description is None and (self.ai_tags or self.described_at is not None):
            _reject()

    def with_ai_description(
        self,
        description: str,
        tags: tuple[str, ...],
        described_at: datetime,
    ) -> Material:
        """Record what the model saw — unless a person has already written it.

        Returns self unchanged when the description came from the user. The
        check lives here rather than in the caller because every future
        describe pass would otherwise have to remember it, and one that forgets
        silently destroys the user's edit.
        """
        if self.description_source is DescriptionSource.USER:
            return self
        return replace(
            self,
            ai_description=description,
            ai_tags=tags,
            described_at=described_at,
            description_source=DescriptionSource.AI,
        )

    def with_user_description(self, description: str) -> Material:
        """Record what a person typed, and mark the field theirs from now on."""
        return replace(
            self,
            ai_description=description,
            ai_tags=(),
            described_at=None,
            description_source=DescriptionSource.USER,
        )
```

补 import：`from dataclasses import dataclass, replace`、`from datetime import datetime`。

- [ ] **Step 4: 运行，确认通过**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_material.py -v
```

- [ ] **Step 5: 跑后端全量单测确认没影响别处**

```bash
cd backend && .venv/bin/python -m pytest tests/unit -q
```

Expected: 全部通过（此前基线 2752 passed / 2 skipped / 1 xfailed，本任务只新增用例，不应有任何既有用例转红）

- [ ] **Step 6: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/material.py \
        backend/tests/unit/control_plane/domain/test_material.py
git commit -m "feat(le-02): 描述来源与 AI 覆盖保护

「用户改过的描述不被 AI 覆盖」这条以前只写在台账里，实现时全靠调用方自觉。
现在写成 with_ai_description：来源是 USER 时直接返回原对象，覆盖不了就是
覆盖不了。检查放在方法里而不是调用方，是因为将来每一次重新理解素材都得
记着这件事，而漏掉一次就会不声不响地毁掉用户的编辑。"
```

---

## Task 6: 台账收口

**Files:**
- Modify: `docs/local-video-editing-roadmap.md`
- Create: `docs/development/LE-02.md`

**Interfaces:**
- Consumes: Task 1–5 的提交
- Produces: LE-02 标记完成，当前下一步指向 LE-03

- [ ] **Step 0: 把 material 模块接进 `domain/__init__.py`**

`domain/__init__.py` 逐个 import 并重新导出全部 17 个 submodule，`material.py` 是唯一没接的。后果具体：`from automation_tool.control_plane.domain import MaterialKind`——本代码库每个消费方都用的写法——会失败，测试只能改用子模块全路径。

**推迟到这一步而不是在 Task 1 就接，是有意的**：那时 `Material` 还不存在，接进去只能导出基础类型，而后面每个 task 都要再改一次 `__init__.py`，五次无谓改动。现在类型齐了，一次接完。

按该文件既有写法，import 并在 `__all__` 中登记：`MaterialId`、`MaterialKind`、`DescriptionSource`、`InvalidMaterialModel`、`Material`，以及 `MAX_*` 上限常量。**逐条核对新增的 `__all__` 条目都能在你新增的 import 里找到来源**——LE-01 Task 2 花了一整轮修复就是因为这份文件的导出与实际模块不同步。

接完跑一次确认包级导入可用：

```bash
cd backend && .venv/bin/python -c "from automation_tool.control_plane.domain import Material, MaterialKind, DescriptionSource; print('ok')"
```

- [ ] **Step 1: 更新台账**

`docs/local-video-editing-roadmap.md`：
- LE-02 行状态 `⬜ 未开始` → `✅ 已完成`
- 进度区 `✅ 已完成：1` → `2`，`⬜ 未开始：23` → `22`
- §5 当前下一步改为 LE-03（Timeline 重写）

- [ ] **Step 2: 跑计数守卫**

```bash
python3 scripts/check_local_editing_roadmap_counts.py
```

Expected: 退出 0

- [ ] **Step 3: 写证据文件**

创建 `docs/development/LE-02.md`，开头必须是：

```markdown
# LE-02 Material 素材库领域对象

> 用户可操作：否
> 证据类型：分层实现
> 日期：2026-07-28
```

正文记录 RED/GREEN（每个 Task 的失败与通过输出）、不变式清单、真实边界（领域层无 I/O、字段间的交叉依赖来自三级漏斗）、清理、文档变化。每个数字都必须来自实际跑过的命令。

- [ ] **Step 4: 提交**

```bash
git add docs/local-video-editing-roadmap.md docs/development/LE-02.md
git commit -m "docs(le-02): 素材领域对象收口，下一步转 LE-03

Material 承载探测与理解两类结果，字段间的交叉依赖直接来自三级漏斗的结构。
用户描述保护写进了方法而不是文档。"
```

---

## Self-Review

**1. 规格覆盖**

| 台账 LE-02 交付项 | 覆盖任务 |
| --- | --- |
| `Material`（kind/时长/分辨率/内容摘要） | Task 2 |
| `has_audio` / 响度 | Task 3 |
| `has_speech` / 人声区间 / 转写（LE-14 追加） | Task 3 |
| 镜头边界 | Task 4 |
| AI 描述与标签 | Task 5 |
| `MaterialId` | Task 1 |
| 校验规则 | Task 2–5 各自覆盖 |
| 去重规则 | Task 2 的 `content_digest` 格式约束 |
| 用户改过的描述不被 AI 覆盖 | Task 5 |

**2. 占位符扫描**

无 TBD/TODO。所有测试与实现均为可直接运行的代码。

**3. 类型一致性**

`_video` 工厂的 defaults 在 Task 2/3/4/5 各追加一次，每次追加的字段名与该 Task 实现里新增的字段逐一对应。`with_ai_description` 的三个参数（description/tags/described_at）在测试与实现中签名一致。`DescriptionSource` 在 Task 1 定义、Task 5 使用。

**4. 一处有意的取舍**

去重只在领域层约束摘要**格式**，不做「同摘要素材是否已存在」的判断——那需要查询已有素材集合，属于仓储职责，归 LE-05。领域对象不持有集合，也不该为了去重去认识仓储。
