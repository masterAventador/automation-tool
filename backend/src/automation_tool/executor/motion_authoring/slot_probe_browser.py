"""The packaged-Chromium slot probe: one session, every document, end-frame reads.

This is the runtime half of PC-14. The judgement (`require_no_new_overflow`)
is pure logic in `slot_overflow_probe.py`; what lives here is the only part
that needs a browser — loading the marked working copies and reading the
scroll/client boxes off the marked elements.

Two measurement rules inherited from the probe that froze the budgets
(`frontend/scripts/measure-motion-part-slots.mjs`):

* Seek the part's own timeline to its end before reading. Measured at load, 14
  of the 48 frozen slots reported overflow mid-animation and one container was
  still scaled toward zero — a reading of a frame nobody sees.
* Read overflow at the settled end frame, not the widest one.

Everything is measured by ONE browser session on purpose: the baseline and the
substituted document are compared against each other, and a per-document
browser would reintroduce the launch-to-launch variance the same-session
design exists to cancel.

The launch goes through `BrowserRuntime` — the Executor's one Playwright
primitive — with an explicit executable the App already authorized (EB-07) and
a private profile directory under the render workspace, cleaned up with the
workspace itself. Headless, always: this probe runs mid-authoring and must
never flash a window at the user.

(English docstring for the reason `part_document.py` gives: the branding gate
reads Chinese-bearing literals in a `.py` source as operator copy.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
)
from automation_tool.executor.motion_authoring.slot_overflow_probe import (
    SLOT_PROBE_JS,
    ProbeReading,
)

# Where the probe's throwaway Chromium profile lives, relative to the render
# workspace root. Inside the workspace so the App's workspace deletion is the
# cleanup; beside `catalog/` and `catalog-baseline/`, never inside them.
SLOT_PROBE_PROFILE_DIRECTORY: Final = "slot-probe-profile"

# The settle waits the budget probe validated: 400ms for the document to load
# its runtime, 200ms for the seeked frame to paint.
_LOAD_SETTLE_MILLISECONDS: Final = 400
_SEEK_SETTLE_MILLISECONDS: Final = 200

# The same seek the render Worker makes; a part that registers no timeline is
# measured as it loads, which is what it is.
_SEEK_TO_END_JS: Final = """
() => {
  const timelines = window.__timelines;
  if (!timelines) return;
  for (const timeline of Object.values(timelines)) {
    if (typeof timeline.seek === "function") {
      timeline.seek(typeof timeline.duration === "function" ? timeline.duration() : 999);
    }
  }
}
"""


class PackagedSlotProbe:
    """Measure every document handed over, in one headless packaged browser."""

    def __init__(
        self,
        *,
        browser_executable: Path,
        profile_directory: Path,
        runtime: BrowserRuntime | None = None,
    ) -> None:
        self._browser_executable = browser_executable
        self._profile_directory = profile_directory
        self._runtime = runtime if runtime is not None else BrowserRuntime()

    def __repr__(self) -> str:
        return "PackagedSlotProbe(<redacted>)"

    def __call__(self, documents: tuple[Path, ...]) -> list[ProbeReading]:
        self._profile_directory.mkdir(parents=True, exist_ok=True)
        request = BrowserLaunchRequest(
            # Resolved so the request's symlink refusal judges the real path —
            # macOS render workspaces routinely sit under /var, a symlink.
            executable_path=self._browser_executable.resolve(),
            profile_directory=self._profile_directory.resolve(),
            headless=True,
        )
        readings: list[ProbeReading] = []
        with self._runtime.running(request):
            page = self._runtime.primary_window().playwright_page
            for document in documents:
                page.goto(document.resolve().as_uri())  # type: ignore[attr-defined]
                page.wait_for_timeout(_LOAD_SETTLE_MILLISECONDS)  # type: ignore[attr-defined]
                page.evaluate(_SEEK_TO_END_JS)  # type: ignore[attr-defined]
                page.wait_for_timeout(_SEEK_SETTLE_MILLISECONDS)  # type: ignore[attr-defined]
                measured = dict(page.evaluate(SLOT_PROBE_JS))  # type: ignore[attr-defined]
                stage = measured["stage"]
                readings.append(
                    ProbeReading(
                        slots={
                            int(index): (
                                int(pixels[0]),
                                int(pixels[1]),
                                int(pixels[2]),
                                int(pixels[3]),
                            )
                            for index, pixels in dict(measured["slots"]).items()
                        },
                        stage=(int(stage[0]), int(stage[1])),
                    )
                )
        return readings


__all__ = [
    "SLOT_PROBE_PROFILE_DIRECTORY",
    "PackagedSlotProbe",
]
