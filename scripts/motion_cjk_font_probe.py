#!/usr/bin/env python3
"""PC-13 probe: can a part keep its Latin typeface and still set Chinese?

The parts in ``vendor/hyperframes`` name their typefaces in CSS -- ``"Anton"``,
``"Archivo Black"``, ``"Space Mono"`` -- and the submodule is read-only, so the
only way to give them Chinese glyphs is from outside the part. The offline
catalog already splits one family name across several ``@font-face`` rules by
``unicode-range``; this probe asks whether the same split works when the two
faces come from *different* fonts: Latin from the part's own typeface, Han from
a Chinese font, under one family name.

The pure half -- which families a part asks for, and the face records that
express the split -- is unit-tested. The half that only the real browser can
answer is measured by ``--render``, which launches the packaged Chromium with
the production render flags and reads ``CSS.getPlatformFontsForNode``: the
browser reports, per element, which physical font drew how many glyphs. That is
the only answer that cannot be talked around.

Nothing here writes to the submodule, to a contract or to the offline catalog.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Final, Iterable, Sequence

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]

# Family names that select a class of font rather than a font, plus the two
# CSS-wide keywords. A part naming one of these is asking the host for whatever
# it has, which is the fallback this probe exists to replace.
_GENERIC_FAMILIES: Final = frozenset(
    {
        "-apple-system",
        "blinkmacsystemfont",
        "cursive",
        "emoji",
        "fangsong",
        "fantasy",
        "inherit",
        "initial",
        "math",
        "monospace",
        "revert",
        "sans-serif",
        "serif",
        "system-ui",
        "ui-monospace",
        "ui-rounded",
        "ui-sans-serif",
        "ui-serif",
        "unset",
    }
)

_FONT_FAMILY_DECLARATION: Final = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE)

# The code points a Chinese face must own. Latin, Latin-1 punctuation and the
# middle dot are deliberately absent: those stay with the part's own typeface,
# which is the whole point of the split.
_CJK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x2E80, 0x2EFF),  # CJK radicals supplement
    (0x2F00, 0x2FDF),  # Kangxi radicals
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x30FF),  # Hiragana and Katakana (present in the SC fonts)
    (0x3100, 0x312F),  # Bopomofo
    (0x31C0, 0x31EF),  # CJK strokes
    (0x3200, 0x33FF),  # Enclosed CJK letters, CJK compatibility
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFE10, 0xFE1F),  # Vertical forms
    (0xFE30, 0xFE4F),  # CJK compatibility forms
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
)

_CJK_UNICODE_RANGE: Final = ", ".join(
    f"U+{start:04X}-{end:04X}" for start, end in _CJK_RANGES
)


def cjk_codepoints() -> frozenset[int]:
    """Every code point the Chinese face claims, as one set."""
    return frozenset(
        code for start, end in _CJK_RANGES for code in range(start, end + 1)
    )


def cjk_unicode_range() -> str:
    """The same claim as a CSS `unicode-range` value.

    Public because the production rule generator must emit the exact range this
    probe measured; two hand-kept copies would drift and the drift would only
    show up as characters silently rendering in the host font.
    """
    return _CJK_UNICODE_RANGE


def named_font_families(text: str) -> frozenset[str]:
    """The typefaces a document names, ignoring generics and custom properties.

    A part is only reachable by this mechanism through a name it actually
    writes down: a rule keyed on ``"Archivo Black"`` is what lets an outside
    stylesheet add a Han face to that same name.
    """
    families: set[str] = set()
    for declaration in _FONT_FAMILY_DECLARATION.finditer(text):
        for candidate in declaration.group(1).split(","):
            name = candidate.strip().strip("\"'").strip()
            if not name or name.startswith("var(") or name.startswith("$"):
                continue
            if name.lower() in _GENERIC_FAMILIES:
                continue
            families.add(name)
    return frozenset(families)


def frame_seek_times(*, duration_seconds: float, fps: int) -> list[float]:
    """The timeline positions one capture pass seeks to, in order.

    Bounded by the part's own duration rather than reaching it: a GSAP timeline
    seeked past its end holds the final state, so an inclusive last frame turns
    into a still tail that reads as a stalled render.
    """
    if duration_seconds <= 0 or fps <= 0:
        raise ValueError("a capture needs a positive duration and frame rate")
    count = int(round(duration_seconds * fps))
    return [index / fps for index in range(max(count, 1))]


def cjk_face_records(
    *, families: Iterable[str], weights: Iterable[int], artifact_path: str
) -> list[dict[str, str]]:
    """Face records in the shape ``stylesheet_css`` already emits.

    One record per (family, weight). They all point at the same file: a face is
    selected by family name, weight and code point, so the same bytes can serve
    every family the catalog ships. The cost of the font is paid once.
    """
    return [
        {
            "family": family,
            "style": "normal",
            "weight": str(weight),
            "display": "block",
            "subset": "chinese-simplified",
            "unicodeRange": _CJK_UNICODE_RANGE,
            "artifactPath": artifact_path,
        }
        for family in families
        for weight in weights
    ]


# --------------------------------------------------------------------------
# The measured half: the real packaged Chromium.
# --------------------------------------------------------------------------

# Same flags the production render session uses, minus the frame-capture
# machinery: a font answer taken under different rasterisation settings would
# not be an answer about the product.
_RENDER_FLAGS: Final = (
    "--headless",
    "--remote-debugging-pipe",
    "--use-mock-keychain",
    "--password-store=basic",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-component-update",
    "--disable-background-networking",
    "--no-pings",
    "--block-new-web-contents",
    "--deny-permission-prompts",
    "--dns-prefetch-disable",
    "--disable-lcd-text",
    "--disable-font-subpixel-positioning",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--proxy-server=127.0.0.1:9",
    "--proxy-bypass-list=<-loopback>",
)


class CdpPipe:
    """Minimal CDP client over the browser's fd 3/4 pipe."""

    def __init__(self, *, reader, writer) -> None:
        self._reader_stream = reader
        self._writer_stream = writer
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._buffer = b""
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        stream = self._reader_stream
        while True:
            chunk = stream.read(4096)
            if not chunk:
                # The browser is gone. Waking the waiters here turns a dead
                # process into an immediate error instead of a full timeout
                # spent waiting for a reply that can never arrive.
                self._closed = True
                self._ready.set()
                return
            self._buffer += chunk
            while b"\0" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\0", 1)
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                identifier = message.get("id")
                if isinstance(identifier, int):
                    with self._lock:
                        self._pending[identifier] = message
                    self._ready.set()

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            identifier = self._next_id
        message: dict = {"id": identifier, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        self._writer_stream.write(json.dumps(message).encode("utf-8") + b"\0")
        self._writer_stream.flush()
        deadline = threading.Event()
        timer = threading.Timer(60.0, deadline.set)
        timer.start()
        try:
            while True:
                with self._lock:
                    if identifier in self._pending:
                        return self._pending.pop(identifier)
                if self._closed:
                    raise RuntimeError(f"the browser closed the pipe during {method}")
                if deadline.is_set():
                    raise TimeoutError(f"no CDP response for {method}")
                self._ready.wait(0.05)
                self._ready.clear()
        finally:
            timer.cancel()


def _evaluate(pipe: CdpPipe, session: str, expression: str, await_promise: bool = False) -> object:
    response = pipe.send(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
        session,
    )
    result = response.get("result", {})
    if "exceptionDetails" in result:
        raise RuntimeError(f"page evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def measure_document(
    *, browser: Path, document: Path, selectors: Sequence[str], width: int, height: int,
    screenshot: Path | None, seek_seconds: float = 0.0,
    frames_directory: Path | None = None, frame_times: Sequence[float] = (),
) -> dict:
    """Open one document in the packaged Chromium and report what drew the text.

    ``CSS.getPlatformFontsForNode`` is the measurement: for each selector it
    returns the physical fonts the layout used and how many glyphs each one
    drew. A claim about which typeface a part ends up with is checkable against
    that number and nothing else.
    """
    workdir = Path(tempfile.mkdtemp(prefix="automation-tool-pc13-probe-"))
    # `--remote-debugging-pipe` speaks on the child's fd 3 (browser reads) and
    # fd 4 (browser writes), which `Popen` cannot wire directly.
    read_child, write_parent = os.pipe()
    read_parent, write_child = os.pipe()

    def _wire_pipe_descriptors() -> None:
        os.dup2(read_child, 3)
        os.dup2(write_child, 4)

    process = subprocess.Popen(
        [
            str(browser),
            *_RENDER_FLAGS,
            f"--user-data-dir={workdir / 'profile'}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Descriptors 3 and 4 must be listed even though nothing in the parent
        # calls them that: CPython closes every unlisted descriptor *after*
        # `preexec_fn` runs, so the two the dup2 just created would be shut
        # again and the browser would report "pipe file descriptors are not
        # open".
        pass_fds=(read_child, write_child, 3, 4),
        preexec_fn=_wire_pipe_descriptors,
        start_new_session=True,
    )
    os.close(read_child)
    os.close(write_child)
    pipe = CdpPipe(
        reader=os.fdopen(read_parent, "rb", buffering=0),
        writer=os.fdopen(write_parent, "wb", buffering=0),
    )

    try:
        version = pipe.send("Browser.getVersion").get("result", {})
        created = pipe.send("Target.createTarget", {"url": "about:blank"})
        target = created["result"]["targetId"]
        attached = pipe.send("Target.attachToTarget", {"flatten": True, "targetId": target})
        session = attached["result"]["sessionId"]
        pipe.send("Page.enable", {}, session)
        pipe.send("DOM.enable", {}, session)
        pipe.send("CSS.enable", {}, session)
        pipe.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
            session,
        )
        pipe.send("Page.navigate", {"url": document.resolve().as_uri()}, session)
        _evaluate(
            pipe,
            session,
            "new Promise((resolve) => { if (document.readyState === 'complete') resolve(true);"
            " else addEventListener('load', () => resolve(true)); })",
            await_promise=True,
        )
        _evaluate(pipe, session, "document.fonts.ready.then(() => true)", await_promise=True)
        loaded = _evaluate(
            pipe,
            session,
            "Array.from(document.fonts).map((face) => "
            "[face.family, face.weight, face.status].join('/'))",
        )
        # A part opens with its text hidden and wipes in; measuring at time zero
        # would measure an invisible element. Seek the same way the production
        # render session does, then let the seeked state composite.
        _evaluate(
            pipe,
            session,
            "(async () => {"
            " for (const timeline of Object.values(window.__timelines ?? {}))"
            f"  if (timeline && typeof timeline.seek === 'function') timeline.seek({seek_seconds}, false);"
            " await new Promise((resolve) => requestAnimationFrame("
            "  () => requestAnimationFrame(() => resolve(true))));"
            " return true; })()",
            await_promise=True,
        )

        root = pipe.send("DOM.getDocument", {"depth": 1}, session)["result"]["root"]["nodeId"]
        measurements = []
        for selector in selectors:
            found = pipe.send(
                "DOM.querySelector", {"nodeId": root, "selector": selector}, session
            )
            node = found.get("result", {}).get("nodeId")
            if not node:
                measurements.append({"selector": selector, "error": "not-found"})
                continue
            fonts = pipe.send("CSS.getPlatformFontsForNode", {"nodeId": node}, session)
            box = _evaluate(
                pipe,
                session,
                "(() => { const element = document.querySelector("
                f"{json.dumps(selector)});"
                " const rect = element.getBoundingClientRect();"
                " return {text: element.textContent, width: Math.round(rect.width * 100) / 100,"
                " height: Math.round(rect.height * 100) / 100}; })()",
            )
            measurements.append(
                {
                    "selector": selector,
                    "text": box.get("text") if isinstance(box, dict) else None,
                    "width": box.get("width") if isinstance(box, dict) else None,
                    "height": box.get("height") if isinstance(box, dict) else None,
                    "fonts": fonts.get("result", {}).get("fonts", []),
                }
            )

        def capture(target: Path) -> dict:
            shot = pipe.send(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
                session,
            )
            data = base64.b64decode(shot["result"]["data"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return {
                "path": str(target),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        captured = capture(screenshot) if screenshot is not None else None

        frames_written: list[dict] = []
        if frames_directory is not None and frame_times:
            for index, moment in enumerate(frame_times):
                _evaluate(
                    pipe,
                    session,
                    "(async () => {"
                    " for (const timeline of Object.values(window.__timelines ?? {}))"
                    f"  if (timeline && typeof timeline.seek === 'function') timeline.seek({moment}, false);"
                    " await new Promise((resolve) => requestAnimationFrame("
                    "  () => requestAnimationFrame(() => resolve(true))));"
                    " return true; })()",
                    await_promise=True,
                )
                frames_written.append(capture(frames_directory / f"frame-{index:04d}.png"))

        return {
            "browser": version.get("product"),
            "document": str(document),
            "loadedFaces": loaded,
            "measurements": measurements,
            "screenshot": captured,
            "frames": {
                "count": len(frames_written),
                "bytes": sum(frame["bytes"] for frame in frames_written),
                "distinctDigests": len({frame["sha256"] for frame in frames_written}),
            } if frames_written else None,
        }
    finally:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=30)
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--screenshot", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--seek", type=float, default=0.0)
    parser.add_argument("--frames-directory", type=Path, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--fps", type=int, default=30)
    arguments = parser.parse_args()

    frame_times: list[float] = []
    if arguments.frames_directory is not None:
        if arguments.duration is None:
            parser.error("--frames-directory needs --duration")
        frame_times = frame_seek_times(
            duration_seconds=arguments.duration, fps=arguments.fps
        )

    report = measure_document(
        browser=arguments.browser,
        document=arguments.document,
        selectors=arguments.selector,
        width=arguments.width,
        height=arguments.height,
        screenshot=arguments.screenshot,
        seek_seconds=arguments.seek,
        frames_directory=arguments.frames_directory,
        frame_times=frame_times,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
