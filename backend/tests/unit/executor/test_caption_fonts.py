"""Caption font registry: resolution, coverage and the fallback chain."""

from __future__ import annotations


def test_caption_fonts_module_is_importable() -> None:
    """The captions package ships with the Executor, so it must import."""
    from automation_tool.executor.captions import fonts

    assert fonts.REGISTERED_CAPTION_FONTS
