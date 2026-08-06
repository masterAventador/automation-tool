"""The one way a line of the Executor's stdout protocol is written.

The manager reads these frames with a parser that rejects any line ending in
`\\r`, so the framing is a byte contract, not a text one. Windows makes that
distinction load-bearing: `sys.stdout` there is a text stream that rewrites
`"\\n"` as `"\\r\\n"` on the way out, and a frame written through it is refused
before it is ever parsed.

This lived in two places once — the handshake wrote exact bytes, the platform
command result wrote text — and on 2026-08-05 that difference cost a Windows
release every platform command it had: the handshake arrived, the first command
closed the channel, and the App reported `process_unavailable` about an Executor
that was healthy, had done the work, and had written its ledger row. So there is
one writer, and both callers use it.
"""

from __future__ import annotations

from typing import TextIO


def write_protocol_line(output: TextIO, source: str) -> None:
    """Write `source` followed by exactly one `\\n`, then flush.

    Written through the underlying binary buffer when the stream has one, which
    every real stdout does; only in-memory text sinks — which no operating
    system rewrites — take the plain path.
    """
    binary_output = getattr(output, "buffer", None)
    if binary_output is not None:
        binary_output.write((source + "\n").encode("utf-8"))
        binary_output.flush()
        return
    output.write(source + "\n")
    output.flush()


__all__ = ["write_protocol_line"]
