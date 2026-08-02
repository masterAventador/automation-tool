#!/usr/bin/env python3
"""BM-16 acceptance harness contract tests.

Proves the determinism-and-release acceptance harness exists with every
mandated cross-platform phase before the heavyweight run is attempted: deterministic
gates, the 134-item per-item render sweep, the 12-style render sweep, the
same-input double-run comparison, and the no-URL-entry verification.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_bm_16_acceptance.py"

REQUIRED_PHASES = (
    "run_deterministic_gates",
    "stage_release_directory",
    "run_item_render_sweep",
    "run_style_render_sweep",
    "run_double_run_determinism",
    "verify_no_url_entry",
    "require_no_run_processes",
    "cleanup_run_root",
    "remove_run_root",
)


def main() -> int:
    assert sys.version_info >= (3, 10), (
        "BM-16 acceptance requires python3.10+ (use python3.12)"
    )
    assert RUNNER.is_file(), "scripts/run_bm_16_acceptance.py is missing"

    specification = importlib.util.spec_from_file_location(
        "run_bm_16_acceptance", RUNNER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["run_bm_16_acceptance"] = module
    specification.loader.exec_module(module)

    for phase in REQUIRED_PHASES:
        assert hasattr(module, phase), f"acceptance runner is missing phase: {phase}"

    source = RUNNER.read_text(encoding="utf-8")
    assert "process_ids_matching" in source, (
        "acceptance runner must inspect leftover processes cross-platform"
    )
    assert '["pgrep", "-f"' not in source, (
        "acceptance runner must not invoke the POSIX-only pgrep command directly"
    )
    assert "rmtree(run_root, ignore_errors=True)" not in source, (
        "acceptance runner must not silently ignore run-directory cleanup failures"
    )

    with tempfile.TemporaryDirectory(prefix="bm16-cleanup-contract-") as parent:
        run_root = Path(parent) / "run"
        run_root.mkdir()
        read_only_file = run_root / "release-file.txt"
        read_only_file.write_text("locked release payload", encoding="utf-8")
        read_only_file.chmod(stat.S_IREAD)
        module.remove_run_root(run_root)
        assert not run_root.exists(), "acceptance runner left its run directory behind"

    cleanup_events: list[str] = []
    process_baseline = {41, 73}
    module.cleanup_run_root(
        Path("owned-run"),
        process_baseline,
        matching=lambda marker: (
            cleanup_events.append(f"detect:{marker}") or set(process_baseline)
        ),
        terminate_owned=lambda marker, *, baseline, observed: (
            cleanup_events.append(
                f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
            )
            or set()
        ),
        require_none=lambda path, *, baseline: cleanup_events.append(
            f"inspect:{path}:{sorted(baseline)}"
        ),
        remove=lambda path: cleanup_events.append(f"remove:{path}"),
    )
    assert cleanup_events == [
        "detect:owned-run",
        "terminate:owned-run:[41, 73]:[]",
        "inspect:owned-run:[41, 73]",
        "remove:owned-run",
        "detect:owned-run",
        "terminate:owned-run:[41, 73]:[]",
        "inspect:owned-run:[41, 73]",
    ], (
        "acceptance cleanup must inspect owned processes before and after deleting files"
    )

    failed_inspection_events: list[str] = []
    inspection_count = 0

    def fail_the_first_inspection(path: Path, *, baseline: set[int]) -> None:
        nonlocal inspection_count
        inspection_count += 1
        failed_inspection_events.append(f"inspect:{path}:{sorted(baseline)}")
        if inspection_count == 1:
            raise RuntimeError("the first cleanup inspection still saw a process")

    try:
        module.cleanup_run_root(
            Path("first-inspection-failed"),
            process_baseline,
            matching=lambda marker: (
                failed_inspection_events.append(f"detect:{marker}")
                or set(process_baseline)
            ),
            terminate_owned=lambda marker, *, baseline, observed: (
                failed_inspection_events.append(
                    f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
                )
                or set()
            ),
            require_none=fail_the_first_inspection,
            remove=lambda path: failed_inspection_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "first cleanup inspection" in str(error), error
    else:
        raise AssertionError("the failed first process inspection was hidden")
    assert failed_inspection_events == [
        "detect:first-inspection-failed",
        "terminate:first-inspection-failed:[41, 73]:[]",
        "inspect:first-inspection-failed:[41, 73]",
        "remove:first-inspection-failed",
        "detect:first-inspection-failed",
        "terminate:first-inspection-failed:[41, 73]:[]",
        "inspect:first-inspection-failed:[41, 73]",
    ], "a failed first inspection must not skip directory removal or the final cleanup pass"

    failed_termination_events: list[str] = []
    termination_count = 0

    def fail_the_first_termination(
        marker: str, *, baseline: set[int], observed: set[int]
    ) -> set[int]:
        nonlocal termination_count
        termination_count += 1
        failed_termination_events.append(
            f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
        )
        if termination_count == 1:
            raise RuntimeError("the first process termination probe failed")
        return set()

    try:
        module.cleanup_run_root(
            Path("first-termination-failed"),
            process_baseline,
            matching=lambda marker: (
                failed_termination_events.append(f"detect:{marker}")
                or set(process_baseline)
            ),
            terminate_owned=fail_the_first_termination,
            require_none=lambda path, *, baseline: failed_termination_events.append(
                f"inspect:{path}:{sorted(baseline)}"
            ),
            remove=lambda path: failed_termination_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "process cleanup tool failed" in str(error), error
    else:
        raise AssertionError("the failed first process termination was hidden")
    assert failed_termination_events == [
        "detect:first-termination-failed",
        "terminate:first-termination-failed:[41, 73]:[]",
        "inspect:first-termination-failed:[41, 73]",
        "remove:first-termination-failed",
        "detect:first-termination-failed",
        "terminate:first-termination-failed:[41, 73]:[]",
        "inspect:first-termination-failed:[41, 73]",
    ], "a failed termination probe must not skip removal or the final cleanup pass"

    failed_rescan_events: list[str] = []
    scan_count = 0

    def fail_the_second_scan(marker: str) -> set[int]:
        nonlocal scan_count
        scan_count += 1
        failed_rescan_events.append(f"detect:{marker}")
        if scan_count == 2:
            raise RuntimeError("the post-removal process scan failed")
        return set(process_baseline)

    try:
        module.cleanup_run_root(
            Path("post-removal-scan-failed"),
            process_baseline,
            matching=fail_the_second_scan,
            terminate_owned=lambda marker, *, baseline, observed: (
                failed_rescan_events.append(
                    f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
                )
                or set()
            ),
            require_none=lambda path, *, baseline: failed_rescan_events.append(
                f"inspect:{path}:{sorted(baseline)}"
            ),
            remove=lambda path: failed_rescan_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "process cleanup tool failed" in str(error), error
    else:
        raise AssertionError("the failed post-removal process scan was hidden")
    assert failed_rescan_events == [
        "detect:post-removal-scan-failed",
        "terminate:post-removal-scan-failed:[41, 73]:[]",
        "inspect:post-removal-scan-failed:[41, 73]",
        "remove:post-removal-scan-failed",
        "detect:post-removal-scan-failed",
        "terminate:post-removal-scan-failed:[41, 73]:[]",
        "inspect:post-removal-scan-failed:[41, 73]",
    ], "a failed post-removal scan must not skip the final termination and inspection"

    leak_events: list[str] = []

    def terminate_late_process(
        marker: str, *, baseline: set[int], observed: set[int]
    ) -> set[int]:
        leak_events.append(
            f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
        )
        observed.add(99)
        return set()

    try:
        module.cleanup_run_root(
            Path("leaking-run"),
            process_baseline,
            matching=lambda marker: (
                leak_events.append(f"detect:{marker}")
                or set(process_baseline)
            ),
            terminate_owned=terminate_late_process,
            require_none=lambda path, *, baseline: leak_events.append(
                f"inspect:{path}:{sorted(baseline)}"
            ),
            remove=lambda path: leak_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "99" in str(error), error
    else:
        raise AssertionError("a run-owned process leak was silently repaired")
    assert leak_events == [
        "detect:leaking-run",
        "terminate:leaking-run:[41, 73]:[]",
        "inspect:leaking-run:[41, 73]",
        "remove:leaking-run",
        "detect:leaking-run",
        "terminate:leaking-run:[41, 73]:[99]",
        "inspect:leaking-run:[41, 73]",
    ], "a leaking run must still clean every owned resource before failing"

    post_remove_events: list[str] = []
    scans = iter((set(process_baseline), {*process_baseline, 101}))

    def terminate_post_remove_process(
        marker: str, *, baseline: set[int], observed: set[int]
    ) -> set[int]:
        post_remove_events.append(
            f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
        )
        return set()

    try:
        module.cleanup_run_root(
            Path("post-remove-run"),
            process_baseline,
            matching=lambda marker: (
                post_remove_events.append(f"detect:{marker}") or next(scans)
            ),
            terminate_owned=terminate_post_remove_process,
            require_none=lambda path, *, baseline: post_remove_events.append(
                f"inspect:{path}:{sorted(baseline)}"
            ),
            remove=lambda path: post_remove_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "101" in str(error), error
    else:
        raise AssertionError("a process that appeared after directory removal was missed")
    assert post_remove_events == [
        "detect:post-remove-run",
        "terminate:post-remove-run:[41, 73]:[]",
        "inspect:post-remove-run:[41, 73]",
        "remove:post-remove-run",
        "detect:post-remove-run",
        "terminate:post-remove-run:[41, 73]:[101]",
        "inspect:post-remove-run:[41, 73]",
    ], "post-removal process discovery must be cleaned and reported"

    final_inspection_events: list[str] = []
    final_inspection_count = 0
    final_termination_count = 0

    def discover_during_final_inspection(
        path: Path, *, baseline: set[int]
    ) -> None:
        nonlocal final_inspection_count
        final_inspection_count += 1
        final_inspection_events.append(f"inspect:{path}:{sorted(baseline)}")
        if final_inspection_count == 2:
            raise RuntimeError("final inspection discovered PID 102")

    def terminate_final_process(
        marker: str, *, baseline: set[int], observed: set[int]
    ) -> set[int]:
        nonlocal final_termination_count
        final_termination_count += 1
        final_inspection_events.append(
            f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
        )
        if final_termination_count == 3:
            observed.add(102)
        return set()

    try:
        module.cleanup_run_root(
            Path("final-inspection-run"),
            process_baseline,
            matching=lambda marker: (
                final_inspection_events.append(f"detect:{marker}")
                or set(process_baseline)
            ),
            terminate_owned=terminate_final_process,
            require_none=discover_during_final_inspection,
            remove=lambda path: final_inspection_events.append(f"remove:{path}"),
        )
    except RuntimeError as error:
        assert "102" in str(error), error
    else:
        raise AssertionError("a process discovered by final inspection was left running")
    assert final_inspection_events == [
        "detect:final-inspection-run",
        "terminate:final-inspection-run:[41, 73]:[]",
        "inspect:final-inspection-run:[41, 73]",
        "remove:final-inspection-run",
        "detect:final-inspection-run",
        "terminate:final-inspection-run:[41, 73]:[]",
        "inspect:final-inspection-run:[41, 73]",
        "terminate:final-inspection-run:[41, 73]:[]",
        "inspect:final-inspection-run:[41, 73]",
    ], "a process first seen by final inspection must be terminated and rechecked"

    locked_directory_events: list[str] = []
    removal_count = 0

    def fail_removal_until_processes_stop(path: Path) -> None:
        nonlocal removal_count
        removal_count += 1
        locked_directory_events.append(f"remove:{path}")
        if removal_count == 1:
            raise PermissionError("the browser still holds its profile")

    module.cleanup_run_root(
        Path("locked-directory-run"),
        process_baseline,
        matching=lambda marker: (
            locked_directory_events.append(f"detect:{marker}")
            or set(process_baseline)
        ),
        terminate_owned=lambda marker, *, baseline, observed: (
            locked_directory_events.append(
                f"terminate:{marker}:{sorted(baseline)}:{sorted(observed)}"
            )
            or set()
        ),
        require_none=lambda path, *, baseline: locked_directory_events.append(
            f"inspect:{path}:{sorted(baseline)}"
        ),
        remove=fail_removal_until_processes_stop,
    )
    assert locked_directory_events == [
        "detect:locked-directory-run",
        "terminate:locked-directory-run:[41, 73]:[]",
        "inspect:locked-directory-run:[41, 73]",
        "remove:locked-directory-run",
        "detect:locked-directory-run",
        "terminate:locked-directory-run:[41, 73]:[]",
        "remove:locked-directory-run",
        "inspect:locked-directory-run:[41, 73]",
    ], "a directory unlocked by final termination must be removed before cleanup returns"

    assert (ROOT / "docs/development/BM-16.md").is_file(), (
        "docs/development/BM-16.md is missing"
    )
    roadmap = (
        ROOT / "docs/embedded-browser-video-studio-roadmap.md"
    ).read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-16 |")]
    assert len(rows) == 1 and any(
        rows[0].endswith(f"| {status} |")
        for status in ("🧪 RED", "🚧 实现中", "🔍 待验收", "✅ 已完成")
    ), "BM-16 roadmap row is missing, duplicated or inactive"

    print("BM-16 acceptance harness contract tests passed")
    print("executed checks: 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
