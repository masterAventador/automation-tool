# LE-04 剪辑项目与任务状态机实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给剪辑线补上「一个项目输出成什么规格」与「一次渲染跑到哪一步了」两件事——LE-03 的 `Timeline` 只描述剪辑结构，渲染器还缺输出画幅、帧率、字幕样式，以及一个能表达进行中/取消中/失败原因的任务对象。

**Architecture:** 两个新模块，与 `material.py`、`timeline.py` 平级：
- `domain/editing_project.py`：`EditingProjectId`、`OutputSpec`、`CaptionStyle`、`EditingProject`
- `domain/editing_job.py`：`EditingJobId`、`EditingJobStatus`、`EditingJobFailureCode`、`EditingJobStateMachine`、`EditingJob`

`Timeline` 补 `project_id`（LE-03 有意留的空）。

**Tech Stack:** Python 3.12、frozen dataclass + `slots=True`、`StrEnum`、`MappingProxyType` 冻结转换表、pytest。

## Global Constraints

- **TDD 铁律**：每一步先写会失败的测试，实跑看到红，再写实现。
- **验证命令**（每个 task 提交前全部跑通，**全部前台跑**，在 `backend/` 目录下）：
  - `.venv/bin/python -m pytest tests/unit/control_plane/domain -q`
  - `.venv/bin/python -m ruff format --check <本 task 改的文件>`
  - `.venv/bin/python -m ruff check <本 task 改的文件>`
  - `.venv/bin/python -m mypy <本 task 改的文件>`
  - 后三条针对本 task 改的文件必须 exit 0。全库 `ruff check .` 有 98 个、`mypy` 有 17 个既有错误，不是本线引入，不用管，但**不许新增**。
- **覆盖率硬指标**：`fail_under = 100`（`backend/pyproject.toml:89`）。本 task 新建的每个模块都必须 **100% 语句 + 100% 分支、零 partial branch**：
  ```
  .venv/bin/python -m pytest tests/unit/control_plane/domain/test_editing_project.py --cov=automation_tool.control_plane.domain.editing_project --cov-report=term-missing -q
  ```
  （`material.py` 只有 91%，是 LE-02 遗留、已记账，**别去动它**。）
- **风格必须与 `timeline.py`/`material.py` 一致**：`_reject()` 抛 `Invalid*Model`、`type(x) is not int` 而非 `isinstance`（因为 `isinstance(True, int)` 为真）、`type(x) is not float` 同理拒绝整数、frozen dataclass + `slots=True`、`from __future__ import annotations`、`__all__` 字母序。
- **每个枚举值都必须有归属规则**，不许有落不到任何分支的成员。
- 中文提交信息，`feat(le-04):` / `fix(le-04):` 前缀。**逐文件 `git add`，禁止 `git add -A`**。

## LE-03 三轮审查留下的四条方法论，本计划直接沿用

实现者与审查者都要照做，这是前一个任务用真金白银换来的：

1. **覆盖率 100% ≠ 分支被验过。** coverage.py 把整个 `if` 当**一个**分支计，`or` 链里的子条件**不单独计量**。一个永远为假的析取项能带着满分混过去。**只能靠逐项推导**「在下层已有约束下这一项还可能为真吗」。
2. **门禁绿 ≠ 门禁在守东西。** `any(...)` 的生成器在**接受路径**上就被执行了，所以一条承重护栏可以零拒绝用例而覆盖率无感。
3. **测试变红 ≠ 这条断言有捕捉力。** 红可能是**别的**断言开的火。验证某条断言时，要让它前面的断言先通过，把它单独暴露出来。
4. **测试「为错误的理由通过」。** 一条 `pytest.raises` 用例，如果它的构造参数在 `pytest.raises` 块内就抛了，测试照样绿但根本没测到目标。**每写一条拒绝用例，都要给 `_reject()` 打桩抓调用栈上一帧，确认第一个开火的分支与用例名一致。脚本用完删掉不要提交。**

## 本计划的设计决定与理由

### 为什么输出规格用具体像素而不是画幅枚举

渲染管线（设计 §5）第二步是 `scale/crop 竖屏`、第三步是 `fps 归一`，两步都要具体数值；LE-10 的完成定义要求「ffprobe 断言编码/**分辨率**/**帧数**/时长」。用 `VideoAspectRatio` 那样的枚举，等于把数值挪到另一张查找表里，两处都要维护。创作线走枚举是因为它把画幅交给云端渲染器，本地渲染没有这层。

### 为什么宽高必须是偶数

随包 ffmpeg 用 libx264 写 h264、像素格式 yuv420p，色度平面在两个轴上都是半分辨率，**奇数尺寸 ffmpeg 直接拒绝**。这不是保守取值，是编码器的硬约束——放进模型才能在提交时就拒掉，而不是等渲染到一半失败。

### 为什么 `font_key` 是模式校验的键而不是自由字符串

它从用户设置流进模型，再由渲染器变成字体文件名。项目 `CLAUDE.md §7` 明令「模型输出、网页内容、文件名和外部输入一律视为不可信数据，不能直接拼接为 Shell、路径、SQL 或浏览器执行指令」。自由字符串能走出字体目录，模式校验的键不能。

### 为什么 `OutputSpec`/`CaptionStyle` 做成值对象，而 LE-03 否决了 `FrameSize`

看起来不一致，理由不同：`FrameSize` 只包两个字段、除了范围没有自己的不变式（范围已在原地校验），而 `Material` 的 ORM 映射近在眼前。这两个值对象各自**携带真正的跨字段不变式**（宽高必须是偶数、描边不得吞掉字形），各包 3–4 个字段，且作为整体传给渲染器。列数一样，可读性差别明显。

### 首期状态只有六个，没有 PAUSED、没有 OUTCOME_UNCERTAIN

- **不要 PAUSED**：本地渲染 5～55 秒（设计 §1.2 实测），暂停没有用户故事。
- **不要 OUTCOME_UNCERTAIN**：那个状态是给「平台动作是否已生效无法确认」用的。本地渲染的产物是我们自己的文件，能直接检查——半截的 mp4 就是失败，删掉即可，不存在不确定。
- **RUNNING 丢了 Worker 走 FAILED 而不是回 QUEUED**：ffmpeg 没有断点续渲，「恢复」只能是重跑，那是一个新任务。假装能续会让状态图说谎。

### 本计划有意不做的事（供审查者对照，不是遗漏）

| 不做 | 理由 |
| --- | --- |
| `EditingJob` 不带 `kind` 字段 | 首期只有渲染一种作业。AI 起草作业（LE-16 产出 Timeline 草稿）落地时才知道它的真实形状，现在加一个只有一个取值的字段就是预测性抽象 |
| 字幕不含颜色字段 | 台账登记的基线是「字号/描边/行距/字体」四项。白字黑描边是短视频字幕的通行约定，首期没有让用户改颜色的用户故事 |
| 不做变速、不做画中画 | 沿用 LE-03 的首期锁定 |
| 模型调用类失败码不在本 task | LE-13/15/16 落地时按实际失败形态扩枚举，枚举是可扩展的 |
| `EditingProject` 不含素材清单 | 素材与项目的关联属仓储层（LE-05），领域对象不持有集合引用 |

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `backend/src/automation_tool/control_plane/domain/editing_project.py` | 新建：输出规格、字幕样式、剪辑项目（T1/T2） |
| `backend/src/automation_tool/control_plane/domain/editing_job.py` | 新建：作业状态、失败码、状态机、作业对象（T4/T5） |
| `backend/src/automation_tool/control_plane/domain/timeline.py` | 改：`Timeline` 补 `project_id`（T3） |
| `backend/src/automation_tool/control_plane/domain/__init__.py` | 改：转出口（T6） |
| `backend/tests/unit/control_plane/domain/test_editing_project.py` | 新建（T1/T2） |
| `backend/tests/unit/control_plane/domain/test_editing_job.py` | 新建（T4/T5） |
| `backend/tests/unit/control_plane/domain/test_timeline.py` | 改（T3） |

---

### Task 1: `OutputSpec` 与 `CaptionStyle` 两个值对象

**Files:**
- Create: `backend/src/automation_tool/control_plane/domain/editing_project.py`
- Test: `backend/tests/unit/control_plane/domain/test_editing_project.py`

**Interfaces:**
- Consumes: `ResourceId`（`domain/resource_ids.py`）
- Produces：`InvalidEditingProjectModel(ValueError)`、`OutputSpec(width, height, fps)`、`CaptionStyle(font_key, font_px, stroke_px, line_spacing)`、模块私有 `_reject()`/`_validate_text()`/`_validate_timestamp()`/`_FONT_KEY_PATTERN`，以及常量 `MAX_PROJECT_TITLE_CHARACTERS=200`、`MIN_OUTPUT_DIMENSION=128`、`MAX_OUTPUT_DIMENSION=4096`、`MIN_OUTPUT_FPS=12`、`MAX_OUTPUT_FPS=60`、`MIN_CAPTION_FONT_PX=12`、`MAX_CAPTION_FONT_PX=200`、`MAX_CAPTION_STROKE_PX=20`、`MIN_CAPTION_LINE_SPACING=1.0`、`MAX_CAPTION_LINE_SPACING=3.0`

> **注意 `_validate_timestamp`**：本 task **不要**写它——T2 才有第一个调用者（`EditingProject.created_at`）。LE-03 的 T2 就因为提前写入无调用者的同名函数吃了一个 Critical（死代码 + 违反 TDD 铁律）。同理 `_validate_text` 也留到 T2。

- [ ] **Step 1: 写会失败的测试**

新建 `backend/tests/unit/control_plane/domain/test_editing_project.py`：

```python
"""Editing project invariants: what a render targets, and how captions look."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.editing_project import (
    MAX_CAPTION_FONT_PX,
    MAX_CAPTION_LINE_SPACING,
    MAX_CAPTION_STROKE_PX,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_FPS,
    MIN_CAPTION_FONT_PX,
    MIN_CAPTION_LINE_SPACING,
    MIN_OUTPUT_DIMENSION,
    MIN_OUTPUT_FPS,
    CaptionStyle,
    InvalidEditingProjectModel,
    OutputSpec,
)


def test_invalid_editing_project_model_is_a_value_error() -> None:
    assert issubclass(InvalidEditingProjectModel, ValueError)


def _output(**overrides: object) -> OutputSpec:
    defaults: dict[str, object] = {"width": 1080, "height": 1920, "fps": 30}
    defaults.update(overrides)
    return OutputSpec(**defaults)  # type: ignore[arg-type]


def test_a_portrait_output_is_accepted() -> None:
    spec = _output()
    assert (spec.width, spec.height, spec.fps) == (1080, 1920, 30)


@pytest.mark.parametrize("field", ["width", "height"])
def test_an_odd_frame_side_is_rejected_because_the_encoder_refuses_it(field: str) -> None:
    """h264/yuv420p halves chroma on both axes; ffmpeg rejects an odd size."""
    with pytest.raises(InvalidEditingProjectModel):
        _output(**{field: 1081})


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize(
    "value",
    [
        MIN_OUTPUT_DIMENSION - 2,
        MAX_OUTPUT_DIMENSION + 2,
        1080.0,
        True,
        "1080",
        None,
    ],
)
def test_frame_sides_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _output(**{field: value})


def test_frame_side_bounds_are_inclusive() -> None:
    assert _output(width=MIN_OUTPUT_DIMENSION, height=MIN_OUTPUT_DIMENSION).width == (
        MIN_OUTPUT_DIMENSION
    )
    assert _output(width=MAX_OUTPUT_DIMENSION, height=MAX_OUTPUT_DIMENSION).height == (
        MAX_OUTPUT_DIMENSION
    )


@pytest.mark.parametrize(
    "fps", [MIN_OUTPUT_FPS - 1, MAX_OUTPUT_FPS + 1, 29.97, True, "30", None]
)
def test_frame_rate_fails_closed(fps: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _output(fps=fps)


def test_frame_rate_bounds_are_inclusive() -> None:
    assert _output(fps=MIN_OUTPUT_FPS).fps == MIN_OUTPUT_FPS
    assert _output(fps=MAX_OUTPUT_FPS).fps == MAX_OUTPUT_FPS


def _caption(**overrides: object) -> CaptionStyle:
    defaults: dict[str, object] = {
        "font_key": "noto-sans-sc",
        "font_px": 48,
        "stroke_px": 3,
        "line_spacing": 1.4,
    }
    defaults.update(overrides)
    return CaptionStyle(**defaults)  # type: ignore[arg-type]


def test_a_caption_style_is_accepted() -> None:
    assert _caption().font_key == "noto-sans-sc"


@pytest.mark.parametrize(
    "font_key",
    [
        "../../../etc/passwd",
        "/absolute/path.ttf",
        "Noto-Sans-SC",
        "noto sans sc",
        "9-leading-digit",
        "",
        "x" * 65,
        None,
        b"noto",
    ],
)
def test_a_font_key_names_a_registry_entry_never_a_path(font_key: object) -> None:
    """It reaches the renderer as a filename; a free string could walk out."""
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_key=font_key)


@pytest.mark.parametrize(
    "font_px", [MIN_CAPTION_FONT_PX - 1, MAX_CAPTION_FONT_PX + 1, 48.0, True, None]
)
def test_caption_size_fails_closed(font_px: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_px=font_px)


@pytest.mark.parametrize(
    "stroke_px", [-1, MAX_CAPTION_STROKE_PX + 1, 3.0, True, None]
)
def test_caption_stroke_fails_closed(stroke_px: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(stroke_px=stroke_px)


def test_a_caption_may_have_no_stroke_at_all() -> None:
    assert _caption(stroke_px=0).stroke_px == 0


def test_a_stroke_that_would_swallow_the_glyph_is_rejected() -> None:
    """The stroke is drawn on both sides of the outline, so it costs 2x."""
    assert _caption(font_px=20, stroke_px=9).stroke_px == 9
    with pytest.raises(InvalidEditingProjectModel):
        _caption(font_px=20, stroke_px=10)


@pytest.mark.parametrize(
    "line_spacing",
    [
        MIN_CAPTION_LINE_SPACING - 0.1,
        MAX_CAPTION_LINE_SPACING + 0.1,
        1,
        True,
        "1.4",
        None,
    ],
)
def test_caption_line_spacing_fails_closed(line_spacing: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _caption(line_spacing=line_spacing)


def test_caption_line_spacing_bounds_are_inclusive() -> None:
    assert _caption(line_spacing=MIN_CAPTION_LINE_SPACING).line_spacing == (
        MIN_CAPTION_LINE_SPACING
    )
    assert _caption(line_spacing=MAX_CAPTION_LINE_SPACING).line_spacing == (
        MAX_CAPTION_LINE_SPACING
    )
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_editing_project.py -q
```

预期 `ModuleNotFoundError: No module named 'automation_tool.control_plane.domain.editing_project'`。

- [ ] **Step 3: 写最小实现**

新建 `backend/src/automation_tool/control_plane/domain/editing_project.py`：

```python
"""One editing project: what it renders to, and how its captions look."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Never

MAX_PROJECT_TITLE_CHARACTERS: Final = 200
MIN_OUTPUT_DIMENSION: Final = 128
MAX_OUTPUT_DIMENSION: Final = 4096
MIN_OUTPUT_FPS: Final = 12
MAX_OUTPUT_FPS: Final = 60
MIN_CAPTION_FONT_PX: Final = 12
MAX_CAPTION_FONT_PX: Final = 200
MAX_CAPTION_STROKE_PX: Final = 20
MIN_CAPTION_LINE_SPACING: Final = 1.0
MAX_CAPTION_LINE_SPACING: Final = 3.0

_FONT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class InvalidEditingProjectModel(ValueError):
    """An editing project domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing project model is invalid")


def _reject() -> Never:
    raise InvalidEditingProjectModel


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """The frame every clip is scaled into, and how often frames are written.

    Both sides must be even. The shipped encoder writes h264 in yuv420p,
    whose chroma planes are half resolution on each axis, and ffmpeg
    refuses an odd size outright — so an odd value is rejected here, at
    submit time, rather than halfway through a render.
    """

    width: int
    height: int
    fps: int

    def __post_init__(self) -> None:
        for side in (self.width, self.height):
            if (
                type(side) is not int
                or not MIN_OUTPUT_DIMENSION <= side <= MAX_OUTPUT_DIMENSION
                or side % 2 != 0
            ):
                _reject()
        if type(self.fps) is not int or not MIN_OUTPUT_FPS <= self.fps <= MAX_OUTPUT_FPS:
            _reject()


@dataclass(frozen=True, slots=True)
class CaptionStyle:
    """How captions are drawn. PIL renders them; ffmpeg only overlays.

    `font_key` names an entry in the bundled font registry — never a path.
    It arrives from user settings and the renderer turns it into a
    filename, so a free string could walk out of the font directory. A
    pattern-checked key cannot.
    """

    font_key: str
    font_px: int
    stroke_px: int
    line_spacing: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.font_key, str)
            or _FONT_KEY_PATTERN.fullmatch(self.font_key) is None
            or type(self.font_px) is not int
            or not MIN_CAPTION_FONT_PX <= self.font_px <= MAX_CAPTION_FONT_PX
            or type(self.stroke_px) is not int
            or not 0 <= self.stroke_px <= MAX_CAPTION_STROKE_PX
            or type(self.line_spacing) is not float
            or not MIN_CAPTION_LINE_SPACING <= self.line_spacing <= MAX_CAPTION_LINE_SPACING
        ):
            _reject()
        # The stroke sits on both sides of the glyph outline, so it eats
        # twice its width out of the letterform.
        if self.stroke_px * 2 >= self.font_px:
            _reject()


__all__ = [
    "MAX_CAPTION_FONT_PX",
    "MAX_CAPTION_LINE_SPACING",
    "MAX_CAPTION_STROKE_PX",
    "MAX_OUTPUT_DIMENSION",
    "MAX_OUTPUT_FPS",
    "MAX_PROJECT_TITLE_CHARACTERS",
    "MIN_CAPTION_FONT_PX",
    "MIN_CAPTION_LINE_SPACING",
    "MIN_OUTPUT_DIMENSION",
    "MIN_OUTPUT_FPS",
    "CaptionStyle",
    "InvalidEditingProjectModel",
    "OutputSpec",
]
```

- [ ] **Step 4: 跑测试确认通过 + 覆盖率 100%**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_editing_project.py --cov=automation_tool.control_plane.domain.editing_project --cov-report=term-missing -q
cd backend && .venv/bin/python -m ruff format --check src/automation_tool/control_plane/domain/editing_project.py tests/unit/control_plane/domain/test_editing_project.py
cd backend && .venv/bin/python -m ruff check src/automation_tool/control_plane/domain/editing_project.py tests/unit/control_plane/domain/test_editing_project.py
cd backend && .venv/bin/python -m mypy src/automation_tool/control_plane/domain/editing_project.py tests/unit/control_plane/domain/test_editing_project.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/editing_project.py \
        backend/tests/unit/control_plane/domain/test_editing_project.py
git commit -m "feat(le-04): 输出规格与字幕样式，宽高锁偶数、字体只收注册键"
```

---

### Task 2: `EditingProject` 聚合根

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/editing_project.py`
- Test: `backend/tests/unit/control_plane/domain/test_editing_project.py`

**Interfaces:**
- Consumes: T1 的 `OutputSpec`、`CaptionStyle`、`_reject`、`MAX_PROJECT_TITLE_CHARACTERS`；`ResourceId`
- Produces: `EditingProjectId(ResourceId)`（`_resource = "editing project"`）、`EditingProject(project_id, title, output, caption_style, created_at)`；本 task 加入模块私有 `_validate_text`、`_validate_timestamp`（它们的第一个调用者在此）

- [ ] **Step 1: 写会失败的测试**

追加到 `test_editing_project.py`（并把新名字加进顶部 import，另需 `from datetime import UTC, datetime, timedelta, timezone`）：

```python
def test_editing_project_id_is_a_uuid4_resource_id() -> None:
    identifier = EditingProjectId.new()
    assert EditingProjectId.parse(str(identifier)) == identifier


def test_editing_project_id_rejects_a_foreign_identifier_type() -> None:
    from automation_tool.control_plane.domain.resource_ids import InvalidResourceId
    from automation_tool.control_plane.domain.timeline import TimelineId

    with pytest.raises(InvalidResourceId):
        EditingProjectId.parse(TimelineId.new())


def _project(**overrides: object) -> EditingProject:
    defaults: dict[str, object] = {
        "project_id": EditingProjectId.new(),
        "title": "国庆探店合集",
        "output": _output(),
        "caption_style": _caption(),
        "created_at": datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return EditingProject(**defaults)  # type: ignore[arg-type]


def test_a_project_carries_everything_a_render_needs_but_the_timeline() -> None:
    project = _project()
    assert project.output.fps == 30
    assert project.caption_style.font_px == 48


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "not-an-id"),
        ("title", ""),
        ("title", "   "),
        ("title", "  前后有空白  "),
        ("title", "带\x00空字符"),
        ("title", "x" * (MAX_PROJECT_TITLE_CHARACTERS + 1)),
        ("title", None),
        ("output", {"width": 1080, "height": 1920, "fps": 30}),
        ("output", None),
        ("caption_style", "noto-sans-sc"),
        ("caption_style", None),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
        ("created_at", datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_project_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingProjectModel):
        _project(**{field: value})


def test_a_project_title_may_wrap_but_carries_no_control_characters() -> None:
    assert _project(title="国庆探店\n第二季").title == "国庆探店\n第二季"


def test_project_title_length_bound_is_inclusive() -> None:
    assert len(_project(title="国" * MAX_PROJECT_TITLE_CHARACTERS).title) == (
        MAX_PROJECT_TITLE_CHARACTERS
    )
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_editing_project.py -q
```

预期 `ImportError: cannot import name 'EditingProject'`。

- [ ] **Step 3: 写最小实现**

在 `editing_project.py` 顶部补 import：

```python
import unicodedata
from datetime import UTC, datetime
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ResourceId
```

在 `_reject` 之后加入两个校验器（函数体照抄 `timeline.py` 里的同名实现，逐字一致）：

```python
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
```

在 `InvalidEditingProjectModel` 之后加 ID：

```python
@final
class EditingProjectId(ResourceId):
    """Stable identifier for one editing project."""

    __slots__ = ()
    _resource = "editing project"
```

在 `CaptionStyle` 之后加聚合根：

```python
@dataclass(frozen=True, slots=True)
class EditingProject:
    """One editing project: the render settings every job under it inherits.

    It holds no material list and no timeline — those are separate
    aggregates joined at the repository layer, not object references.
    """

    project_id: EditingProjectId
    title: str
    output: OutputSpec
    caption_style: CaptionStyle
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project_id, EditingProjectId)
            or not isinstance(self.output, OutputSpec)
            or not isinstance(self.caption_style, CaptionStyle)
        ):
            _reject()
        _validate_text(self.title, maximum=MAX_PROJECT_TITLE_CHARACTERS)
        _validate_timestamp(self.created_at)
```

`__all__` 补 `"EditingProject"`、`"EditingProjectId"`（保持字母序）。

- [ ] **Step 4: 跑测试 + 覆盖率 100%**（命令同 T1 Step 4）

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/editing_project.py \
        backend/tests/unit/control_plane/domain/test_editing_project.py
git commit -m "feat(le-04): 剪辑项目聚合根，承载每次渲染继承的输出设置"
```

---

### Task 3: `Timeline` 接上 `project_id`

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/timeline.py`
- Test: `backend/tests/unit/control_plane/domain/test_timeline.py`

**Interfaces:**
- Consumes: T2 的 `EditingProjectId`
- Produces: `Timeline` 第二个字段变为 `project_id: EditingProjectId`

> LE-03 的 `Timeline` docstring 写着「It has no owning project yet — `EditingProject` arrives in LE-04, and a field pointing at a type that does not exist would be worse than none.」**本 task 就是那个 LE-04，记得把这段 docstring 一起改掉**，别留一句已经不成立的话。

- [ ] **Step 1: 写会失败的测试**

在 `test_timeline.py` 里：

1. 顶部 import 补 `from automation_tool.control_plane.domain.editing_project import EditingProjectId`
2. `_timeline` 工厂的 defaults 补一行 `"project_id": EditingProjectId.new(),`（放在 `timeline_id` 之后）
3. 追加两条用例：

```python
def test_a_timeline_belongs_to_one_project() -> None:
    project_id = EditingProjectId.new()
    assert _timeline(project_id=project_id).project_id is project_id


@pytest.mark.parametrize(
    "project_id",
    [None, "not-an-id", TimelineId.new()],
)
def test_a_timeline_refuses_a_project_reference_of_the_wrong_type(
    project_id: object,
) -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(project_id=project_id)
```

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py -q
```

预期：`TypeError: Timeline.__init__() got an unexpected keyword argument 'project_id'`（工厂已经在传了）。

- [ ] **Step 3: 写最小实现**

`timeline.py`：

1. 顶部补 `from automation_tool.control_plane.domain.editing_project import EditingProjectId`
2. `Timeline` 的 docstring 改成：

```python
    """One editable cut of one film, owned by one editing project.

    Render settings — frame size, frame rate, caption style — live on the
    project rather than here: they are the same for every cut of it.
    """
```

3. 字段补 `project_id: EditingProjectId`（放在 `timeline_id` 之后）
4. `__post_init__` 的结构大 `if` 补一项 `or not isinstance(self.project_id, EditingProjectId)`

- [ ] **Step 4: 跑测试 + 覆盖率**

```
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain -q
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/domain/test_timeline.py --cov=automation_tool.control_plane.domain.timeline --cov-report=term-missing -q
```

`timeline.py` 必须仍是 100%、零 partial。三条 lint/type 命令针对两个文件 exit 0。

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/timeline.py \
        backend/tests/unit/control_plane/domain/test_timeline.py
git commit -m "feat(le-04): 时间轴接上所属项目，LE-03 留的 owner 空补齐"
```

---

### Task 4: 作业状态与状态机

**Files:**
- Create: `backend/src/automation_tool/control_plane/domain/editing_job.py`
- Test: `backend/tests/unit/control_plane/domain/test_editing_job.py`

**Interfaces:**
- Produces: `InvalidEditingJobModel(ValueError)`、`InvalidEditingJobTransition(ValueError)`、`EditingJobStatus`（六个成员）、`EditingJobStateMachine`（静态方法 `terminal_statuses()` / `is_terminal()` / `allowed_targets()` / `can_transition()` / `transition()`），仿 `domain/task_state_machine.py` 的写法

**状态图（首期锁定）：**

```
QUEUED     → RUNNING | CANCELLING | FAILED
RUNNING    → CANCELLING | SUCCEEDED | FAILED
CANCELLING → SUCCEEDED | FAILED | CANCELLED
SUCCEEDED / FAILED / CANCELLED → 终态，无出边
```

`CANCELLING` 能落到 `SUCCEEDED`/`FAILED`：取消是**协作式**的（项目 `CLAUDE.md §4.4`：只有执行器确认终态后才能宣称副作用已经停止），取消请求可能正好撞上渲染完成或失败。

- [ ] **Step 1: 写会失败的测试**

新建 `backend/tests/unit/control_plane/domain/test_editing_job.py`：

```python
"""Editing job lifecycle: the closed transition graph and what each state carries."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.editing_job import (
    EditingJobStateMachine,
    EditingJobStatus,
    InvalidEditingJobTransition,
)


def test_invalid_transition_is_a_value_error() -> None:
    assert issubclass(InvalidEditingJobTransition, ValueError)


def test_the_first_release_has_exactly_six_states() -> None:
    """No PAUSED (a 5-55s render has no pause story) and no OUTCOME_UNCERTAIN
    (the output file is ours to inspect — a half-written mp4 is a failure)."""
    assert {status.value for status in EditingJobStatus} == {
        "queued",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_terminal_states_have_no_way_out() -> None:
    terminal = EditingJobStateMachine.terminal_statuses()
    assert terminal == frozenset(
        {EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED, EditingJobStatus.CANCELLED}
    )
    for status in terminal:
        assert EditingJobStateMachine.allowed_targets(status) == frozenset()
        assert EditingJobStateMachine.is_terminal(status)


def test_a_live_state_is_not_terminal() -> None:
    for status in (
        EditingJobStatus.QUEUED,
        EditingJobStatus.RUNNING,
        EditingJobStatus.CANCELLING,
    ):
        assert not EditingJobStateMachine.is_terminal(status)


def test_is_terminal_rejects_a_foreign_value_without_raising() -> None:
    assert not EditingJobStateMachine.is_terminal("succeeded")
    assert not EditingJobStateMachine.is_terminal(None)


@pytest.mark.parametrize(
    ("current", "targets"),
    [
        (
            EditingJobStatus.QUEUED,
            {EditingJobStatus.RUNNING, EditingJobStatus.CANCELLING, EditingJobStatus.FAILED},
        ),
        (
            EditingJobStatus.RUNNING,
            {
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
            },
        ),
        (
            EditingJobStatus.CANCELLING,
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
            },
        ),
    ],
)
def test_the_transition_graph_is_exactly_this(
    current: EditingJobStatus, targets: set[EditingJobStatus]
) -> None:
    assert EditingJobStateMachine.allowed_targets(current) == frozenset(targets)


def test_cancelling_may_still_land_on_success() -> None:
    """Cancellation is cooperative: the request can race a finished render."""
    assert EditingJobStateMachine.transition(
        EditingJobStatus.CANCELLING, EditingJobStatus.SUCCEEDED
    ) is EditingJobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EditingJobStatus.QUEUED, EditingJobStatus.SUCCEEDED),
        (EditingJobStatus.QUEUED, EditingJobStatus.CANCELLED),
        (EditingJobStatus.RUNNING, EditingJobStatus.QUEUED),
        (EditingJobStatus.RUNNING, EditingJobStatus.CANCELLED),
        (EditingJobStatus.CANCELLING, EditingJobStatus.RUNNING),
        (EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED),
        (EditingJobStatus.FAILED, EditingJobStatus.QUEUED),
        (EditingJobStatus.CANCELLED, EditingJobStatus.RUNNING),
        (EditingJobStatus.QUEUED, EditingJobStatus.QUEUED),
    ],
)
def test_an_illegal_transition_is_refused(
    current: EditingJobStatus, target: EditingJobStatus
) -> None:
    assert not EditingJobStateMachine.can_transition(current, target)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(current, target)


def test_a_lost_worker_does_not_send_a_running_job_back_to_the_queue() -> None:
    """ffmpeg has no checkpoint; resuming would be a lie. Re-running is a new job."""
    assert EditingJobStatus.QUEUED not in EditingJobStateMachine.allowed_targets(
        EditingJobStatus.RUNNING
    )


@pytest.mark.parametrize("value", ["running", None, 1])
def test_a_foreign_value_is_not_a_state(value: object) -> None:
    assert not EditingJobStateMachine.can_transition(value, EditingJobStatus.RUNNING)
    assert not EditingJobStateMachine.can_transition(EditingJobStatus.QUEUED, value)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.allowed_targets(value)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(value, EditingJobStatus.RUNNING)
    with pytest.raises(InvalidEditingJobTransition):
        EditingJobStateMachine.transition(EditingJobStatus.QUEUED, value)
```

- [ ] **Step 2: 跑测试确认失败**（预期 `ModuleNotFoundError`）

- [ ] **Step 3: 写最小实现**

新建 `backend/src/automation_tool/control_plane/domain/editing_job.py`：

```python
"""One editing job: where a render is in its life, and why it stopped."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class InvalidEditingJobTransition(ValueError):
    """An editing job transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Editing job state transition is invalid")


class EditingJobStatus(StrEnum):
    """Where one render is in its life.

    Six states, deliberately. There is no PAUSED — a 5-55 second local
    render has no pause story. There is no OUTCOME_UNCERTAIN either: that
    state exists for platform side effects nobody can re-read, whereas the
    output file here is ours to inspect, and a half-written mp4 is simply
    a failure to delete.
    """

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES: Final[frozenset[EditingJobStatus]] = frozenset(
    {EditingJobStatus.SUCCEEDED, EditingJobStatus.FAILED, EditingJobStatus.CANCELLED}
)

_TRANSITIONS: Final[Mapping[EditingJobStatus, frozenset[EditingJobStatus]]] = MappingProxyType(
    {
        EditingJobStatus.QUEUED: frozenset(
            {
                EditingJobStatus.RUNNING,
                EditingJobStatus.CANCELLING,
                EditingJobStatus.FAILED,
            }
        ),
        # No way back to QUEUED: ffmpeg has no checkpoint, so a render that
        # lost its worker cannot resume. Re-running it is a new job.
        EditingJobStatus.RUNNING: frozenset(
            {
                EditingJobStatus.CANCELLING,
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
            }
        ),
        # Cancellation is cooperative: the request can race a render that
        # already finished or already failed, so both remain reachable.
        EditingJobStatus.CANCELLING: frozenset(
            {
                EditingJobStatus.SUCCEEDED,
                EditingJobStatus.FAILED,
                EditingJobStatus.CANCELLED,
            }
        ),
        EditingJobStatus.SUCCEEDED: frozenset(),
        EditingJobStatus.FAILED: frozenset(),
        EditingJobStatus.CANCELLED: frozenset(),
    }
)


class EditingJobStateMachine:
    """Stateless transition policy for editing jobs."""

    @staticmethod
    def terminal_statuses() -> frozenset[EditingJobStatus]:
        return _TERMINAL_STATUSES

    @staticmethod
    def is_terminal(status: object) -> bool:
        return isinstance(status, EditingJobStatus) and status in _TERMINAL_STATUSES

    @staticmethod
    def allowed_targets(status: object) -> frozenset[EditingJobStatus]:
        if not isinstance(status, EditingJobStatus):
            raise InvalidEditingJobTransition
        return _TRANSITIONS[status]

    @staticmethod
    def can_transition(current: object, target: object) -> bool:
        return (
            isinstance(current, EditingJobStatus)
            and isinstance(target, EditingJobStatus)
            and target in _TRANSITIONS[current]
        )

    @staticmethod
    def transition(current: object, target: object) -> EditingJobStatus:
        if (
            not isinstance(current, EditingJobStatus)
            or not isinstance(target, EditingJobStatus)
            or target not in _TRANSITIONS[current]
        ):
            raise InvalidEditingJobTransition
        return target


__all__ = [
    "EditingJobStateMachine",
    "EditingJobStatus",
    "InvalidEditingJobTransition",
]
```

- [ ] **Step 4: 跑测试 + 覆盖率 100%**（`--cov=automation_tool.control_plane.domain.editing_job`）

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/editing_job.py \
        backend/tests/unit/control_plane/domain/test_editing_job.py
git commit -m "feat(le-04): 剪辑作业状态机，六态闭图与非法转换拒绝"
```

---

### Task 5: 失败码与 `EditingJob` 聚合根

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/editing_job.py`
- Test: `backend/tests/unit/control_plane/domain/test_editing_job.py`

**Interfaces:**
- Consumes: T4 的状态机；`EditingProjectId`（T2）、`TimelineId`（LE-03）、`ArtifactId`（`resource_ids.py`）
- Produces: `InvalidEditingJobModel`、`EditingJobId`、`EditingJobFailureCode`（八个成员）、`EditingJob(job_id, project_id, timeline_id, timeline_revision, status, failure_code, output_artifact_id, created_at, updated_at)`，以及五个转换方法 `start` / `request_cancel` / `succeed` / `fail` / `confirm_cancelled`

**状态与事实的耦合（每种状态都要有归属规则）：**

| 状态 | 产物 | 失败码 |
| --- | --- | --- |
| QUEUED / RUNNING / CANCELLING | 必须无 | 必须无 |
| SUCCEEDED | **必须有** | 必须无 |
| FAILED | 必须无 | **必须有** |
| CANCELLED | 必须无 | 必须无 |

**为什么用五个窄方法而不是一个 `with_status(...)`**：每个目标状态要求的事实不同，窄方法把这件事编码进签名——`succeed` 必须收产物 ID，`fail` 必须收失败码，其余两个都不收。一个宽签名只能靠运行时校验表达同样的事，调用方看签名读不出来。这也是 LE-02 的教训：转换不变式没法由单快照的 `__post_init__` 表达，得有显式方法承载。

- [ ] **Step 1: 写会失败的测试**

追加到 `test_editing_job.py`（import 相应补齐，另需 `from datetime import UTC, datetime, timedelta, timezone`）：

```python
def test_failure_codes_cover_the_render_failure_matrix() -> None:
    assert {code.value for code in EditingJobFailureCode} == {
        "invalid_timeline",
        "material_unavailable",
        "material_unsupported",
        "font_unavailable",
        "render_failed",
        "resource_exhausted",
        "permission_denied",
        "worker_lost",
    }


_CREATED = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 7, 29, 10, 1, tzinfo=UTC)


def _job(**overrides: object) -> EditingJob:
    defaults: dict[str, object] = {
        "job_id": EditingJobId.new(),
        "project_id": EditingProjectId.new(),
        "timeline_id": TimelineId.new(),
        "timeline_revision": 1,
        "status": EditingJobStatus.QUEUED,
        "failure_code": None,
        "output_artifact_id": None,
        "created_at": _CREATED,
        "updated_at": _CREATED,
    }
    defaults.update(overrides)
    return EditingJob(**defaults)  # type: ignore[arg-type]


def test_a_queued_job_records_which_revision_it_will_render() -> None:
    """The timeline can be edited while the job runs; the job pins a revision."""
    assert _job(timeline_revision=7).timeline_revision == 7


@pytest.mark.parametrize(
    "status",
    [EditingJobStatus.QUEUED, EditingJobStatus.RUNNING, EditingJobStatus.CANCELLING],
)
def test_a_job_still_in_flight_has_neither_an_output_nor_a_reason(
    status: EditingJobStatus,
) -> None:
    _job(status=status)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=status, output_artifact_id=ArtifactId.new())
    with pytest.raises(InvalidEditingJobModel):
        _job(status=status, failure_code=EditingJobFailureCode.RENDER_FAILED)


def test_a_succeeded_job_must_point_at_what_it_produced() -> None:
    artifact_id = ArtifactId.new()
    job = _job(status=EditingJobStatus.SUCCEEDED, output_artifact_id=artifact_id)
    assert job.output_artifact_id is artifact_id
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.SUCCEEDED)
    with pytest.raises(InvalidEditingJobModel):
        _job(
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_id=artifact_id,
            failure_code=EditingJobFailureCode.RENDER_FAILED,
        )


def test_a_failed_job_must_say_why() -> None:
    job = _job(
        status=EditingJobStatus.FAILED,
        failure_code=EditingJobFailureCode.MATERIAL_UNAVAILABLE,
    )
    assert job.failure_code is EditingJobFailureCode.MATERIAL_UNAVAILABLE
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.FAILED)
    with pytest.raises(InvalidEditingJobModel):
        _job(
            status=EditingJobStatus.FAILED,
            failure_code=EditingJobFailureCode.RENDER_FAILED,
            output_artifact_id=ArtifactId.new(),
        )


def test_a_cancelled_job_carries_neither() -> None:
    _job(status=EditingJobStatus.CANCELLED)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.CANCELLED, failure_code=EditingJobFailureCode.WORKER_LOST)
    with pytest.raises(InvalidEditingJobModel):
        _job(status=EditingJobStatus.CANCELLED, output_artifact_id=ArtifactId.new())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", "not-an-id"),
        ("project_id", "not-an-id"),
        ("timeline_id", "not-an-id"),
        ("timeline_revision", 0),
        ("timeline_revision", 1.0),
        ("timeline_revision", True),
        ("status", "queued"),
        ("status", None),
        ("failure_code", "render_failed"),
        ("output_artifact_id", "not-an-id"),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
        ("created_at", datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
        ("updated_at", datetime(2026, 7, 29, 10, 0)),
    ],
)
def test_job_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job(**{field: value})


def test_a_job_cannot_be_updated_before_it_was_created() -> None:
    _job(updated_at=_UPDATED)
    with pytest.raises(InvalidEditingJobModel):
        _job(created_at=_UPDATED, updated_at=_CREATED)


def test_a_render_walks_from_queued_to_a_finished_file() -> None:
    artifact_id = ArtifactId.new()
    job = _job()
    running = job.start(_UPDATED)
    assert running.status is EditingJobStatus.RUNNING
    done = running.succeed(artifact_id, _UPDATED)
    assert done.status is EditingJobStatus.SUCCEEDED
    assert done.output_artifact_id is artifact_id


def test_a_cancel_request_waits_for_the_worker_to_confirm() -> None:
    cancelling = _job().start(_UPDATED).request_cancel(_UPDATED)
    assert cancelling.status is EditingJobStatus.CANCELLING
    assert cancelling.confirm_cancelled(_UPDATED).status is EditingJobStatus.CANCELLED


def test_a_cancel_request_that_lost_the_race_still_records_the_file() -> None:
    artifact_id = ArtifactId.new()
    cancelling = _job().start(_UPDATED).request_cancel(_UPDATED)
    assert cancelling.succeed(artifact_id, _UPDATED).output_artifact_id is artifact_id


def test_a_failure_carries_its_reason_through_the_transition() -> None:
    failed = _job().start(_UPDATED).fail(EditingJobFailureCode.WORKER_LOST, _UPDATED)
    assert failed.status is EditingJobStatus.FAILED
    assert failed.failure_code is EditingJobFailureCode.WORKER_LOST


def test_every_transition_method_refuses_an_illegal_move() -> None:
    done = _job(status=EditingJobStatus.SUCCEEDED, output_artifact_id=ArtifactId.new())
    with pytest.raises(InvalidEditingJobTransition):
        done.start(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.request_cancel(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.fail(EditingJobFailureCode.RENDER_FAILED, _UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        done.confirm_cancelled(_UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        _job().succeed(ArtifactId.new(), _UPDATED)
    with pytest.raises(InvalidEditingJobTransition):
        _job().confirm_cancelled(_UPDATED)


def test_a_transition_refuses_a_timestamp_that_moves_backwards() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job(updated_at=_UPDATED).start(_CREATED)


def test_succeeding_demands_a_real_artifact_id() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job().start(_UPDATED).succeed("not-an-id", _UPDATED)  # type: ignore[arg-type]


def test_failing_demands_a_real_failure_code() -> None:
    with pytest.raises(InvalidEditingJobModel):
        _job().start(_UPDATED).fail("render_failed", _UPDATED)  # type: ignore[arg-type]
```

- [ ] **Step 2: 跑测试确认失败**（预期 `ImportError: cannot import name 'EditingJob'`）

- [ ] **Step 3: 写最小实现**

在 `editing_job.py` 顶部补 import：

```python
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Never, final

from automation_tool.control_plane.domain.editing_project import EditingProjectId
from automation_tool.control_plane.domain.resource_ids import ArtifactId, ResourceId
from automation_tool.control_plane.domain.timeline import TimelineId
```

加异常、ID、失败码、`_reject`、`_validate_timestamp`（照抄 `timeline.py` 的实现）：

```python
class InvalidEditingJobModel(ValueError):
    """An editing job domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing job model is invalid")


@final
class EditingJobId(ResourceId):
    """Stable identifier for one editing job."""

    __slots__ = ()
    _resource = "editing job"


class EditingJobFailureCode(StrEnum):
    """Why a render stopped, grouped by what the user can do about it."""

    INVALID_TIMELINE = "invalid_timeline"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    MATERIAL_UNSUPPORTED = "material_unsupported"
    FONT_UNAVAILABLE = "font_unavailable"
    RENDER_FAILED = "render_failed"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    PERMISSION_DENIED = "permission_denied"
    WORKER_LOST = "worker_lost"
```

聚合根：

```python
@dataclass(frozen=True, slots=True)
class EditingJob:
    """One render of one timeline revision, and where it got to."""

    job_id: EditingJobId
    project_id: EditingProjectId
    timeline_id: TimelineId
    timeline_revision: int
    status: EditingJobStatus
    failure_code: EditingJobFailureCode | None
    output_artifact_id: ArtifactId | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.job_id, EditingJobId)
            or not isinstance(self.project_id, EditingProjectId)
            or not isinstance(self.timeline_id, TimelineId)
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or not isinstance(self.status, EditingJobStatus)
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, EditingJobFailureCode)
            )
            or (
                self.output_artifact_id is not None
                and not isinstance(self.output_artifact_id, ArtifactId)
            )
        ):
            _reject()
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()
        self._validate_facts_match_status()

    def _validate_facts_match_status(self) -> None:
        """Every state says exactly which facts it must and must not carry."""
        if self.status is EditingJobStatus.SUCCEEDED:
            allowed = self.output_artifact_id is not None and self.failure_code is None
        elif self.status is EditingJobStatus.FAILED:
            allowed = self.output_artifact_id is None and self.failure_code is not None
        else:
            allowed = self.output_artifact_id is None and self.failure_code is None
        if not allowed:
            _reject()

    def _moved_to(self, status: EditingJobStatus, updated_at: datetime, **facts: object) -> EditingJob:
        EditingJobStateMachine.transition(self.status, status)
        _validate_timestamp(updated_at)
        if updated_at < self.updated_at:
            _reject()
        return replace(self, status=status, updated_at=updated_at, **facts)  # type: ignore[arg-type]

    def start(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.RUNNING, updated_at)

    def request_cancel(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.CANCELLING, updated_at)

    def succeed(self, output_artifact_id: ArtifactId, updated_at: datetime) -> EditingJob:
        return self._moved_to(
            EditingJobStatus.SUCCEEDED, updated_at, output_artifact_id=output_artifact_id
        )

    def fail(self, failure_code: EditingJobFailureCode, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.FAILED, updated_at, failure_code=failure_code)

    def confirm_cancelled(self, updated_at: datetime) -> EditingJob:
        return self._moved_to(EditingJobStatus.CANCELLED, updated_at)
```

> `replace()` 会重跑 `__post_init__`，所以 `succeed("not-an-id", ...)` 与 `fail("render_failed", ...)` 都会被结构校验拒下——不需要在方法里再写一遍类型检查。**但你要实测确认这一点**，别想当然：给 `_reject()` 打桩，确认这两条用例开火的是 `__post_init__` 的结构大 `if`。

`__all__` 按字母序补齐新名字。

- [ ] **Step 4: 跑测试 + 覆盖率 100%**

- [ ] **Step 5: 提交**

```bash
git add backend/src/automation_tool/control_plane/domain/editing_job.py \
        backend/tests/unit/control_plane/domain/test_editing_job.py
git commit -m "feat(le-04): 剪辑作业聚合根，状态与事实强耦合、转换走窄方法"
```

---

### Task 6: 转出口与收口

**Files:**
- Modify: `backend/src/automation_tool/control_plane/domain/__init__.py`
- Create: `docs/development/LE-04.md`
- Modify: `docs/local-video-editing-roadmap.md`

- [ ] **Step 1: 写会失败的测试**

在 `test_editing_job.py` 末尾加一条转出口守护：

```python
def test_the_domain_package_exports_the_editing_project_and_job() -> None:
    from automation_tool.control_plane import domain
    from automation_tool.control_plane.domain import editing_job, editing_project

    assert domain.EditingProject is editing_project.EditingProject
    assert domain.OutputSpec is editing_project.OutputSpec
    assert domain.CaptionStyle is editing_project.CaptionStyle
    assert domain.EditingJob is editing_job.EditingJob
    assert domain.EditingJobStateMachine is editing_job.EditingJobStateMachine
    assert set(domain.EditingJobFailureCode) == set(editing_job.EditingJobFailureCode)
```

- [ ] **Step 2: 跑测试确认失败**（预期 `AttributeError`）

- [ ] **Step 3: 接线**

`domain/__init__.py` 补两个模块的公开名（`EditingProject`、`EditingProjectId`、`OutputSpec`、`CaptionStyle`、`InvalidEditingProjectModel`、`EditingJob`、`EditingJobId`、`EditingJobStatus`、`EditingJobFailureCode`、`EditingJobStateMachine`、`InvalidEditingJobModel`、`InvalidEditingJobTransition`），`__all__` 保持字母序。

- [ ] **Step 4: 全量跑通**

```
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/automation-tool-export-openapi --output ../contracts/openapi/control-plane.v1.json --check
cd backend && .venv/bin/automation-tool-export-executor-schema --output ../contracts/protocol/executor-v1.schema.json --check
```

两个契约导出应无 diff（本 task 没有 API 路由）。全库 ruff 98、mypy 17 不许涨。

- [ ] **Step 5: 写 `docs/development/LE-04.md`**

开头必须有：

```markdown
# LE-04 剪辑项目与任务状态机

用户可操作：否
证据类型：分层实现
```

正文记录日期、提交列表、每个 task 的 RED/GREEN、四条门禁退出状态、每个新模块的覆盖率数字、**本计划「本计划有意不做的事」整表照抄**，以及：

- **LE-03 终审登记的两处缺口，本 task 认领了哪一处**：输出画幅/帧率/字幕样式已由 `EditingProject` 承载（`OutputSpec` + `CaptionStyle`）；「原声处理方式」三态开关**不在本 task**，仍归 LE-11。
- **字幕颜色有意不做**：台账登记的基线是四项（字号/描边/行距/字体），白字黑描边是通行约定，首期没有让用户改颜色的用户故事。
- **`EditingJob` 有意不带 `kind` 字段**：首期只有渲染一种作业，AI 起草作业落地时才知道真实形状。

范围主张必须限定清楚（「后端」而不是「全项目」）——LE-03 在这上面栽过一次。

- [ ] **Step 6: 台账收口**

`docs/local-video-editing-roadmap.md`：
- LE-04 行状态改 `✅ 已完成`
- **LE-09 行**补一句：字幕样式基线（字号/描边/行距/字体键）已由 `EditingProject.caption_style` 承载，LE-09 消费它而不是自己定义
- **LE-10 行**补一句：输出画幅与帧率已由 `EditingProject.output` 承载，ffprobe 断言的目标值取自它
- §5「当前下一步」改为 LE-05
- 更新计数并跑门禁：

```
python3 scripts/check_local_editing_roadmap_counts.py
backend/.venv/bin/python -m pytest scripts/test_check_local_editing_roadmap_counts.py -q
```

- [ ] **Step 7: 提交**

代码与台账必须在同一个提交（项目规则）。

```bash
git add backend/src/automation_tool/control_plane/domain/__init__.py \
        backend/tests/unit/control_plane/domain/test_editing_job.py \
        docs/development/LE-04.md \
        docs/local-video-editing-roadmap.md
git commit -m "feat(le-04): 剪辑项目与作业转出口，台账收口"
```
