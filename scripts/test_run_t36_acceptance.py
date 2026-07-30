from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import desktop_e2e_prerequisites as prerequisites  # noqa: E402


def _stub_module(name: str, **attributes: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    return module


def _load_run_t36_acceptance() -> types.ModuleType:
    def noop(*args: object, **kwargs: object) -> None:
        return None

    stubs = {
        # `install` 是 T36 验收现在用来把零件目录装进调试资源树的入口。
        # 桩少一个名字，这条门禁就会以 ImportError 整个红掉——而红的不是被测的行为。
        "prepare_video_runtime": _stub_module(
            "prepare_video_runtime", prepare=noop, install=noop
        ),
        "run_e4_14_acceptance": _stub_module(
            "run_e4_14_acceptance",
            require_port_available=noop,
            start_control_plane=noop,
        ),
        "run_i2_13_acceptance": _stub_module(
            "run_i2_13_acceptance",
            BACKEND_ROOT=ROOT / "backend",
            REPOSITORY_ROOT=ROOT,
            compose_command=noop,
        ),
        "run_vf_06_acceptance": _stub_module(
            "run_vf_06_acceptance",
            DEBUG_APP_RESOURCE_ROOT=ROOT / "frontend/src-tauri/target/debug",
            FRONTEND=ROOT / "frontend",
            pnpm_executable=noop,
            require_port_closed=noop,
            require_staged_embedded_browser=noop,
            require_staged_video_runtime=noop,
            stage_video_runtime=noop,
            unused_loopback_port=noop,
        ),
    }
    spec = importlib.util.spec_from_file_location(
        "run_t36_acceptance_under_test",
        ROOT / "scripts/run_t36_acceptance.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("run_t36_acceptance.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class AuthoringExecutorCacheTests(unittest.TestCase):
    def test_rebuild_removes_the_same_digest_cache_path_that_ensure_uses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_root = root / "executor-package"
            private_app_data = root / "private-app-data"
            fake_builder = _stub_module("run_e4_07_acceptance")

            def build_signed_executor(workspace: Path, *, build_id: str) -> Path:
                self.assertEqual(build_id, prerequisites.SHARED_EXECUTOR_BUILD_ID)
                package = workspace / "dist/automation-tool-executor"
                package.mkdir(parents=True)
                (package / prerequisites.EXECUTOR_MANIFEST_NAME).write_text(
                    "{}", encoding="utf-8"
                )
                (package / prerequisites.EXECUTOR_MANIFEST_SIGNATURE_NAME).write_text(
                    "test", encoding="utf-8"
                )
                return package

            fake_builder.build_signed_executor = build_signed_executor  # type: ignore[attr-defined]
            with (
                patch.object(
                    prerequisites,
                    "EXECUTOR_PACKAGE_CACHE_ROOT",
                    cache_root,
                ),
                patch.object(
                    prerequisites,
                    "executor_package_input_digest",
                    return_value="a" * 64,
                ),
                patch.dict(sys.modules, {"run_e4_07_acceptance": fake_builder}),
            ):
                ensured_cache_path = prerequisites.ensure_signed_executor_package()
                run_t36_acceptance = _load_run_t36_acceptance()
                with (
                    patch.object(
                        run_t36_acceptance,
                        "EXECUTOR_PACKAGE_CACHE_ROOT",
                        cache_root,
                    ),
                    patch.object(
                        run_t36_acceptance,
                        "_answers_the_authoring_protocol",
                        side_effect=(False, True),
                    ),
                    patch.object(run_t36_acceptance, "install_signed_executor_package"),
                    patch.object(run_t36_acceptance.shutil, "rmtree") as remove_tree,
                ):
                    run_t36_acceptance.require_authoring_capable_executor(
                        private_app_data
                    )

        self.assertEqual(
            remove_tree.call_args_list[0],
            call(ensured_cache_path, ignore_errors=True),
        )


class ShotStructureEvidenceTests(unittest.TestCase):
    def test_t36_accepts_real_decoded_counts_with_one_frame_boundary_tolerance(
        self,
    ) -> None:
        run_t36_acceptance = _load_run_t36_acceptance()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "shots.json"
            evidence.write_text(
                json.dumps(
                    [
                        {
                            "index": 1,
                            "startFrame": 0,
                            "frameCount": 90,
                            "renderedStartFrame": 0,
                            "renderedFrameCount": 90,
                            "part": None,
                            "narrationSeconds": 2.5,
                        },
                        {
                            "index": 2,
                            "startFrame": 90,
                            "frameCount": 144,
                            "renderedStartFrame": 90,
                            "renderedFrameCount": 145,
                            "part": "lt-bold-block",
                            "narrationSeconds": 4.24,
                        },
                    ]
                ),
                encoding="utf-8",
            )
            run_t36_acceptance.inspect_shot_structure(
                evidence,
                final_frame_count=235,
            )

    def test_t36_rejects_a_final_film_that_does_not_equal_its_decoded_shots(
        self,
    ) -> None:
        run_t36_acceptance = _load_run_t36_acceptance()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "shots.json"
            evidence.write_text(
                json.dumps(
                    [
                        {
                            "index": 1,
                            "startFrame": 0,
                            "frameCount": 90,
                            "renderedStartFrame": 0,
                            "renderedFrameCount": 90,
                            "part": None,
                            "narrationSeconds": None,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "delivered artifact"):
                run_t36_acceptance.inspect_shot_structure(
                    evidence,
                    final_frame_count=89,
                )


if __name__ == "__main__":
    unittest.main()
