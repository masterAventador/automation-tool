#!/usr/bin/env python3
"""What a failed builder gets to say for itself.

Written after the media toolchain build failed on the Windows acceptance
machine on 2026-07-27 and reported eight lines of curl progress bar. The
reason was `No working C compiler found.`, in the same stream a little further
up, and recovering it meant running the builder by hand — possible on a laptop
with someone watching, impossible on a build machine.

The mechanism is worth stating plainly, because it is not "the tail was too
short": `str.splitlines()` treats a carriage return as a line break, so one
progress line redrawn in place becomes one line per redraw. Measured — twenty
redraws split into twenty-two lines. A tail of the last N then holds nothing
but the bar, however large N is.
"""

from __future__ import annotations

import subprocess

__all__ = ["builder_diagnostic"]


def _decoded(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _visible_lines(output: str | bytes | None) -> list[str]:
    """The lines a terminal would be showing, one entry per real line.

    Split on newlines only — never on carriage returns — and keep the last
    segment of each, which is what the redraws left on screen.
    """
    lines: list[str] = []
    for line in _decoded(output).split("\n"):
        settled = line.split("\r")[-1].rstrip()
        if settled.strip():
            lines.append(settled)
    return lines


def builder_diagnostic(
    completed: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
    *,
    lines_per_stream: int = 20,
) -> str:
    """A bounded tail of each stream, with redrawn lines collapsed.

    Both streams, because builders split their output across them and which of
    the two carries the reason is not knowable from here.
    """
    parts: list[str] = []
    for name, output in (("stderr", completed.stderr), ("stdout", completed.stdout)):
        lines = _visible_lines(output)
        if lines:
            parts.append(f"{name}:\n" + "\n".join(lines[-lines_per_stream:]))
    return "\n".join(parts) if parts else "(builder produced no output)"
