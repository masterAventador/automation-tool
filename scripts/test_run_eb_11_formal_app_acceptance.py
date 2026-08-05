from __future__ import annotations

import base64
import builtins
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from unittest import mock


def load_runner() -> ModuleType:
    path = Path(__file__).with_name("run_eb_11_formal_app_acceptance.py")
    spec = importlib.util.spec_from_file_location("eb11_formal_app_acceptance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("EB-11 acceptance runner is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CrossPlatformImportTests(unittest.TestCase):
    def test_loading_the_macos_runner_does_not_import_fcntl_on_windows(self) -> None:
        real_import = builtins.__import__

        def import_without_fcntl(
            name: str,
            globals: Mapping[str, object] | None = None,
            locals: Mapping[str, object] | None = None,
            fromlist: Sequence[str] = (),
            level: int = 0,
        ) -> object:
            if name == "fcntl":
                raise ModuleNotFoundError("No module named 'fcntl'")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch.object(builtins, "__import__", side_effect=import_without_fcntl):
            runner = load_runner()

        self.assertEqual(runner.SCAN_CHECKPOINT, "douyin_scan_confirmed")


class VisibleRevisionTests(unittest.TestCase):
    def test_action_message_cannot_impersonate_a_new_observation(self) -> None:
        runner = load_runner()
        before = "当前状态登录正常最近检查：2026/8/1 12:00:00打开登录处理我已处理，重新检查安全注销"
        after = (
            "当前状态登录正常最近检查：2026/8/1 12:00:00"
            "登录正常打开登录处理我已处理，重新检查安全注销"
        )

        self.assertEqual(
            runner.observed_revision(before),
            runner.observed_revision(after),
        )

    def test_unstructured_last_check_text_fails_closed(self) -> None:
        runner = load_runner()

        with self.assertRaises(runner.AcceptanceFailed):
            runner.observed_revision("当前状态登录正常最近检查：刚刚打开登录处理")

    def test_older_observation_cannot_satisfy_a_recheck(self) -> None:
        runner = load_runner()
        before = "当前状态登录正常最近检查：2026/8/1 12:00:00"
        older = "当前状态登录正常最近检查：2026/8/1 11:00:00"

        with (
            mock.patch.object(runner, "press"),
            mock.patch.object(runner, "visible_ui_text", return_value=older),
            mock.patch.object(runner.time, "sleep"),
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 0.0, 151.0]),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.recheck_healthy_session(42, before)


class AccountPageReadinessTests(unittest.TestCase):
    def test_press_refuses_a_control_that_is_only_visible_not_enabled(self) -> None:
        """按名字找到的元素**被禁用**时，AXPress 什么也不会发生。

        2026-08-04 实测：`PlatformSessions.tsx` 上「打开登录处理」「我已处理，重新检查」
        「安全注销」三个按钮都带 `disabled={pending !== null}`，而禁用元素照样有
        accessibility name。原实现只按名字匹配后 `perform action "AXPress"` 并把
        AppleScript 的返回当成点击生效——于是「点了」和「点了个灰按钮」输出完全相同。

        这正是本仓 CLAUDE.md「命令必须能区分『没有』和『没查』」所指的形态，只不过
        出在驱动侧：脚本据此认为自己操作了 App，实际上 App 一动没动。
        """
        runner = load_runner()

        def inspect_script(source: str, *, timeout: float = 30.0) -> str:
            del timeout
            # 必须在按下之前问一句「它是不是可用的」。
            self.assertIn("enabled of elementReference", source)
            return "disabled"

        # 直接点名 macOS 那一支：`press` 现在按宿主分发，在 Windows 上跑这条会
        # 走 UIA 而根本不碰 apple_script——照样抛 AcceptanceFailed，于是用例变成
        # 「绿得毫无意义」。要验 AppleScript 的形状，就得调 AppleScript 那个实现。
        with (
            mock.patch.object(runner, "apple_script", side_effect=inspect_script),
            self.assertRaises(runner.AcceptanceFailed) as raised,
        ):
            runner.macos_press(42, "打开登录处理")
        self.assertIn("打开登录处理", str(raised.exception))

    def test_press_binds_a_webkit_accessibility_name_before_comparing_it(self) -> None:
        runner = load_runner()

        def inspect_script(source: str, *, timeout: float = 30.0) -> str:
            del timeout
            self.assertIn("set allElements to entire contents of front window", source)
            self.assertIn("repeat with elementReference in allElements", source)
            self.assertIn("set elementName to name of elementReference", source)
            self.assertIn('(elementName as text) is "账号与平台"', source)
            return "pressed"

        with mock.patch.object(runner, "apple_script", side_effect=inspect_script):
            runner.macos_press(42, "账号与平台")

    def test_visible_text_materializes_the_webkit_accessibility_tree(self) -> None:
        runner = load_runner()

        def inspect_script(source: str, *, timeout: float = 30.0) -> str:
            del timeout
            self.assertIn("set allElements to entire contents of front window", source)
            self.assertIn("repeat with elementReference in allElements", source)
            return "登录正常"

        with mock.patch.object(runner, "apple_script", side_effect=inspect_script):
            self.assertEqual(runner.macos_visible_ui_text(42), "登录正常")

    def test_navigation_retries_until_the_normal_control_is_mounted(self) -> None:
        runner = load_runner()
        healthy = "当前状态登录正常最近检查：2026/8/1 12:00:00"

        # 第一次点不到（页面还没挂上），重试一次成功；随后界面先是「正在启动」，
        # 稳定后才呈现登录态。返回值同时带出「已经登录了没有」。
        with (
            mock.patch.object(
                runner,
                "press",
                side_effect=[runner.AcceptanceFailed("not ready"), None],
            ) as press,
            mock.patch.object(
                runner,
                "visible_ui_text",
                side_effect=["正在启动", "正在启动", healthy],
            ),
            mock.patch.object(runner.time, "sleep"),
        ):
            rendered, already_signed_in = runner.open_account_page(42)

        self.assertEqual(rendered, healthy)
        self.assertTrue(already_signed_in)
        self.assertEqual(press.call_count, 2)


class RuntimeSamplingTests(unittest.TestCase):
    def test_short_lived_chromium_between_two_ui_reads_is_still_observed(self) -> None:
        """浏览器起了又退，正好落在两次界面读取之间——原实现会漏掉它。

        2026-08-04 实测：`recheck_healthy_session()` 每轮先采一次进程、再跑一次
        `visible_ui_text()`，而后者要遍历整个窗口的可访问性树，是这一轮里最慢的一步。
        登录态复查由执行器经 Playwright 拉起内置 Chromium 完成，进程很短命；它一旦
        落在两次采样之间，`require_complete_runtime()` 就报
        `EB-11 did not observe the embedded Chromium`——而 App 侧其实一切正常。

        判据不是「采样够不够快」，而是**观察不能和慢速操作串在同一条线上**：
        运行时采样必须在动作窗口内独立持续进行。
        """
        runner = load_runner()
        contract = object()
        instance = object()

        # 第 1 次采样：还没起浏览器。第 2 次：浏览器在跑。第 3 次起：已经退了。
        samples = [
            runner.RuntimeObservation(executor_observed=True),
            runner.RuntimeObservation(executor_observed=True, embedded_browser_observed=True),
            runner.RuntimeObservation(executor_observed=True),
        ]
        calls = {"n": 0}

        def observe(_contract: object, _instance: object) -> object:
            index = min(calls["n"], len(samples) - 1)
            calls["n"] += 1
            return samples[index]

        with mock.patch.object(runner, "observe_instance_runtime", side_effect=observe):
            with runner.continuous_runtime_observation(contract, instance) as watcher:
                # 模拟「一次很慢的界面读取」——采样必须在这段时间里自己继续跑。
                deadline = time.monotonic() + 1.0
                while calls["n"] < 3 and time.monotonic() < deadline:
                    time.sleep(0.01)
            observed = watcher.result()

        self.assertTrue(observed.embedded_browser_observed)


class ColdStartTests(unittest.TestCase):
    def test_account_page_accepts_a_signed_out_app_instead_of_hanging(self) -> None:
        """干净机上第一次跑，App 显示的是「需要登录」而不是「登录正常」。

        2026-08-04 用户实测：正式公证包 + 全新 Profile，`open_account_page()` 死等
        `HEALTHY_LABEL` 直到超时，报 `did not expose required UI state: 登录正常`，
        然后杀掉 App 退出。这条脚本因此**在干净机上无法完成第一次扫码**——而干净机
        正是真实用户的状态，也正是 EB-17 的验收对象。

        它验的是「已登录 → 注销 → 重扫 → 重启复用」这条生命周期，起点必须有登录态；
        但起点是否已登录不该由脚本假设，而该由它自己观察后决定走哪条路。
        """
        runner = load_runner()
        signed_out = "当前状态需要登录"

        with (
            mock.patch.object(runner, "press"),
            mock.patch.object(runner, "visible_ui_text", return_value=signed_out),
            mock.patch.object(runner.time, "sleep"),
        ):
            rendered, already_signed_in = runner.open_account_page(42)

        self.assertFalse(already_signed_in)
        self.assertIn(runner.LOGIN_REQUIRED_LABEL, rendered)

    def test_account_page_still_reports_an_existing_healthy_session(self) -> None:
        """有登录态时必须照旧识别出来，否则「冷启动兼容」会变成永远走冷启动分支。"""
        runner = load_runner()
        healthy = "当前状态登录正常最近检查：2026/8/1 12:00:00"

        with (
            mock.patch.object(runner, "press"),
            mock.patch.object(runner, "visible_ui_text", return_value=healthy),
            mock.patch.object(runner.time, "sleep"),
        ):
            rendered, already_signed_in = runner.open_account_page(42)

        self.assertTrue(already_signed_in)
        self.assertEqual(rendered, healthy)

    def test_account_page_accepts_a_session_that_was_never_checked(self) -> None:
        """真正的干净机不是「需要登录」，是「尚未确认」。

        2026-08-05 用户实测：Windows 正式包全新安装，账号页落在

            当前状态 / 尚未确认 / 尚无检查记录

        既不是 `登录正常` 也不是 `需要登录`，于是 `open_account_page()` 空转到超时并报
        `settled on neither a signed-in nor a signed-out state`——**在提供二维码之前就
        结束了**，和 2026-08-04 那次冷启动失败是同一个形状：脚本手里的状态清单不全。

        `unknown` 表示服务端还没有任何检查记录，它当然不是已登录；正确处置是走冷启动
        分支，由操作者扫一次把登录态建立起来。
        """
        runner = load_runner()
        never_checked = "当前状态\n尚未确认\n尚无检查记录"

        with (
            mock.patch.object(runner, "press"),
            mock.patch.object(runner, "visible_ui_text", return_value=never_checked),
            mock.patch.object(runner.time, "sleep"),
        ):
            rendered, already_signed_in = runner.open_account_page(42)

        self.assertFalse(already_signed_in)
        self.assertEqual(rendered, never_checked)

    def test_account_page_accepts_every_state_the_app_can_publish(self) -> None:
        """五种状态里只有 `登录正常` 算已登录，其余四种都必须走冷启动分支。"""
        runner = load_runner()

        for label in sorted(runner.SESSION_STATE_LABELS):
            with self.subTest(label=label):
                with (
                    mock.patch.object(runner, "press"),
                    mock.patch.object(
                        runner, "visible_ui_text", return_value=f"当前状态\n{label}"
                    ),
                    mock.patch.object(runner.time, "sleep"),
                ):
                    _, already_signed_in = runner.open_account_page(42)

                self.assertEqual(already_signed_in, label == runner.HEALTHY_LABEL)

    def test_the_state_labels_match_the_page_that_renders_them(self) -> None:
        """两份清单不许各走各的。

        这条脚本读的是界面文字，而那些文字由 `PlatformSessions.tsx` 的 `STATE_LABELS`
        产生。上面两次失败（`需要登录` 漏了、`尚未确认` 漏了）都是同一个原因：那是一个
        封闭集合，而这里只抄了其中几项。抄写本身没法避免，能避免的是**抄漏了没人发现**。
        """
        source = (
            Path(__file__).resolve().parents[1]
            / "frontend/src/features/platform-sessions/PlatformSessions.tsx"
        ).read_text(encoding="utf-8")
        block = re.search(
            r"const STATE_LABELS[^=]*=\s*\{(.*?)\}", source, re.DOTALL
        )
        self.assertIsNotNone(block, "PlatformSessions.tsx no longer declares STATE_LABELS")
        rendered = set(re.findall(r':\s*"([^"]+)"', block.group(1)))
        self.assertEqual(rendered, set(load_runner().SESSION_STATE_LABELS))


class FormalLoginLifecycleTests(unittest.TestCase):
    def test_qr_login_and_restart_must_reuse_the_exact_profile_inode(self) -> None:
        runner = load_runner()
        first_path = Path(
            "/tmp/embedded-browser-profiles/douyin/6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        )
        replacement_path = Path(
            "/tmp/embedded-browser-profiles/douyin/46ea626c-fddb-49bc-9269-a50368f687ca"
        )

        def observation(path: Path, identity: tuple[int, int]) -> object:
            binding = runner.ProfileDirectoryBinding(
                path=path,
                parent_fd=10,
                directory_fd=11,
                parent_identity=(1, 2),
                identity=identity,
            )
            return runner.RuntimeObservation(
                executor_observed=True,
                embedded_browser_observed=True,
                app_owned_profile_observed=True,
                profile_directories=(path,),
                profile_bindings=(binding,),
            )

        qr_open = observation(first_path, (1, 3))
        qr_recheck = observation(first_path, (1, 3))
        restarted = observation(first_path, (1, 3))
        runner.require_same_profile_reuse(qr_open, qr_recheck, restarted)

        with self.assertRaises(runner.AcceptanceFailed):
            runner.require_same_profile_reuse(
                qr_open,
                observation(replacement_path, (1, 4)),
                restarted,
            )
        with self.assertRaises(runner.AcceptanceFailed):
            runner.require_same_profile_reuse(
                qr_open,
                qr_recheck,
                observation(first_path, (1, 5)),
            )

    def test_safe_logout_uses_the_visible_confirmation_and_waits_for_missing(self) -> None:
        runner = load_runner()
        missing = "当前状态需要登录最近检查：2026/8/1 12:00:01"
        with (
            mock.patch.object(runner, "press") as press,
            mock.patch.object(runner, "wait_for_text", side_effect=["确认注销", missing]) as wait,
        ):
            rendered = runner.logout_current_session(42)

        self.assertEqual(rendered, missing)
        self.assertEqual(
            press.call_args_list,
            [mock.call(42, "安全注销"), mock.call(42, "确认注销")],
        )
        self.assertEqual(
            wait.call_args_list,
            [
                mock.call(42, "确认注销"),
                mock.call(42, "需要登录", timeout=runner.ACTION_TIMEOUT_SECONDS),
            ],
        )

    def test_scan_confirmation_is_exact_and_cannot_be_skipped(self) -> None:
        runner = load_runner()
        with (
            mock.patch("builtins.input", return_value="done"),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.confirm_scan_checkpoint()

        with mock.patch("builtins.input", return_value="douyin_scan_confirmed"):
            runner.confirm_scan_checkpoint()

    def test_safe_logout_requires_the_old_profile_marker_lock_and_process_to_be_gone(
        self,
    ) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "Formal Product.app"
            browser = app / "Contents/Resources/embedded-browser/Browser"
            profile_root = root / "embedded-browser-profiles"
            profile_id = "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
            profile = profile_root / "douyin" / profile_id
            lock = profile.parent / f".automation-tool-profile-lease-v1-{profile_id}"
            marker = profile_root / "current-douyin-profile-v1"
            profile.mkdir(parents=True)
            profile_root.chmod(0o700)
            profile.parent.chmod(0o700)
            profile.chmod(0o700)
            lock.write_text("", encoding="utf-8")
            marker.write_text(profile_id, encoding="utf-8")
            parent_fd = os.open(profile.parent, os.O_RDONLY | os.O_DIRECTORY)
            profile_fd = os.open(profile, os.O_RDONLY | os.O_DIRECTORY)
            parent_metadata = os.fstat(parent_fd)
            profile_metadata = os.fstat(profile_fd)
            binding = runner.ProfileDirectoryBinding(
                path=profile,
                parent_fd=parent_fd,
                directory_fd=profile_fd,
                parent_identity=(parent_metadata.st_dev, parent_metadata.st_ino),
                identity=(profile_metadata.st_dev, profile_metadata.st_ino),
            )
            instance = runner.ProcessRecord(
                10,
                1,
                os.fspath(app / "Contents/MacOS/Product"),
                "started",
            )
            browser_process = runner.ProcessRecord(
                11,
                10,
                f"{browser} --user-data-dir={profile}",
                "browser-started",
            )
            contract = runner.RuntimeContract(
                app_path=app,
                executor_path=app / "Contents/Resources/local-executor/executor",
                browser_path=browser,
                profile_root=profile_root,
            )
            observed = runner.RuntimeObservation(
                executor_observed=True,
                embedded_browser_observed=True,
                app_owned_profile_observed=True,
                profile_directories=(profile,),
                profile_bindings=(binding,),
            )

            with (
                mock.patch.object(runner, "process_snapshot", return_value=[instance]),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.require_safe_logout_cleanup(contract, instance, observed)

            lock.unlink()
            profile.rmdir()
            with (
                mock.patch.object(runner, "process_snapshot", return_value=[instance]),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.require_safe_logout_cleanup(contract, instance, observed)

            marker.unlink()
            with (
                mock.patch.object(
                    runner,
                    "process_snapshot",
                    return_value=[instance, browser_process],
                ),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.require_safe_logout_cleanup(contract, instance, observed)

            try:
                with mock.patch.object(runner, "process_snapshot", return_value=[instance]):
                    runner.require_safe_logout_cleanup(contract, instance, observed)
            finally:
                observed.close()

    def test_safe_logout_rejects_an_old_profile_hidden_by_parent_rename(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "Formal Product.app"
            profile_root = root / "embedded-browser-profiles"
            platform_root = profile_root / "douyin"
            profile_id = "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
            profile = platform_root / profile_id
            profile.mkdir(parents=True, mode=0o700)
            profile_root.chmod(0o700)
            platform_root.chmod(0o700)
            parent_fd = os.open(platform_root, os.O_RDONLY | os.O_DIRECTORY)
            profile_fd = os.open(profile, os.O_RDONLY | os.O_DIRECTORY)
            parent_metadata = os.fstat(parent_fd)
            profile_metadata = os.fstat(profile_fd)
            binding = runner.ProfileDirectoryBinding(
                path=profile,
                parent_fd=parent_fd,
                directory_fd=profile_fd,
                parent_identity=(parent_metadata.st_dev, parent_metadata.st_ino),
                identity=(profile_metadata.st_dev, profile_metadata.st_ino),
            )
            instance = runner.ProcessRecord(
                10,
                1,
                os.fspath(app / "Contents/MacOS/Product"),
                "started",
            )
            contract = runner.RuntimeContract(
                app_path=app,
                executor_path=app / "Contents/Resources/local-executor/executor",
                browser_path=app / "Contents/Resources/embedded-browser/Browser",
                profile_root=profile_root,
            )
            observed = runner.RuntimeObservation(
                executor_observed=True,
                embedded_browser_observed=True,
                app_owned_profile_observed=True,
                profile_directories=(profile,),
                profile_bindings=(binding,),
            )

            platform_root.rename(profile_root / "saved-douyin")
            platform_root.mkdir(mode=0o700)
            try:
                with (
                    mock.patch.object(runner, "process_snapshot", return_value=[instance]),
                    self.assertRaises(runner.AcceptanceFailed),
                ):
                    runner.require_safe_logout_cleanup(contract, instance, observed)
            finally:
                observed.close()


class EvidenceBoundaryTests(unittest.TestCase):
    def test_evidence_path_cannot_become_new_release_source(self) -> None:
        runner = load_runner()
        with self.assertRaisesRegex(runner.AcceptanceFailed, "source inventory"):
            runner.require_source_stable_evidence_path(runner.REPOSITORY_ROOT / "eb11.json")

        accepted = runner.require_source_stable_evidence_path(
            runner.REPOSITORY_ROOT / ".local" / "eb11-evidence.json"
        )
        self.assertEqual(
            accepted,
            (runner.REPOSITORY_ROOT / ".local" / "eb11-evidence.json").resolve(),
        )

    def test_evidence_cannot_be_published_inside_the_app_bundle(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Product.app"
            (app / "Contents").mkdir(parents=True)

            with self.assertRaises(runner.AcceptanceFailed):
                runner.require_evidence_outside_app(app, app / "Contents" / "eb11.json")

    def test_evidence_cannot_be_published_inside_production_app_data(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Product.app"
            app.mkdir()

            with self.assertRaises(runner.AcceptanceFailed):
                runner.require_evidence_outside_app(
                    app,
                    runner.app_data_root() / "embedded-browser-profiles" / "eb11.json",
                )

    @unittest.skipUnless(os.name == "posix", "POSIX evidence publication contract")
    def test_opened_evidence_directory_is_rechecked_after_a_path_redirect(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "Product.app"
            protected = app / "Contents"
            safe = root / "safe"
            displaced = root / "displaced"
            protected.mkdir(parents=True)
            safe.mkdir()
            evidence = runner.require_evidence_outside_app(app, safe / "evidence.json")

            safe.rename(displaced)
            safe.symlink_to(protected, target_is_directory=True)
            with self.assertRaises(runner.AcceptanceFailed):
                runner.open_protected_evidence_target(app, evidence)

            self.assertFalse((protected / "evidence.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX evidence publication contract")
    def test_fixed_parent_handle_prevents_path_replacement_redirect(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verified = root / "verified"
            original = root / "original"
            redirect = root / "redirect"
            verified.mkdir()
            redirect.mkdir()
            target = runner.open_evidence_target(verified / "evidence.json")
            try:
                verified.rename(original)
                verified.symlink_to(redirect, target_is_directory=True)
                with self.assertRaises(runner.AcceptanceFailed):
                    runner.write_evidence(target, {"schema": "test"})
            finally:
                target.close()

            self.assertFalse((original / "evidence.json").exists())
            self.assertFalse((redirect / "evidence.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX evidence publication contract")
    def test_failed_write_leaves_no_final_or_temporary_evidence(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            target = runner.open_evidence_target(evidence)
            try:
                with (
                    mock.patch.object(runner.os, "fsync", side_effect=OSError("disk full")),
                    self.assertRaises(OSError),
                ):
                    runner.write_evidence(target, {"schema": "test"})
            finally:
                target.close()

            self.assertFalse(evidence.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX evidence publication contract")
    def test_successful_write_is_complete_private_and_non_overwriting(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            target = runner.open_evidence_target(evidence)
            try:
                runner.write_evidence(target, {"schema": "test"})

                self.assertEqual(evidence.read_text(), '{\n  "schema": "test"\n}\n')
                self.assertEqual(os.stat(evidence).st_mode & 0o777, 0o600)
                self.assertEqual(list(Path(directory).iterdir()), [evidence])
                with self.assertRaises(FileExistsError):
                    runner.write_evidence(target, {"schema": "replacement"})
            finally:
                target.close()

    @unittest.skipUnless(os.name == "posix", "POSIX evidence publication contract")
    def test_operator_interrupt_rolls_back_already_published_evidence(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            target = runner.open_evidence_target(evidence)
            try:
                with (
                    mock.patch.object(
                        runner.os,
                        "fsync",
                        side_effect=[None, KeyboardInterrupt],
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    runner.write_evidence(target, {"schema": "test"})
            finally:
                target.close()

            self.assertFalse(evidence.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_main_rolls_back_evidence_when_pass_reporting_is_interrupted(self) -> None:
        runner = load_runner()

        class Publication:
            path = Path("/tmp/eb11-evidence.json")
            rolled_back = False
            committed = False

            def rollback(self) -> None:
                self.rolled_back = True

            def commit(self) -> None:
                self.committed = True

        publication = Publication()
        with (
            mock.patch.object(runner, "parse_arguments"),
            mock.patch.object(runner, "run_acceptance", return_value=publication),
            mock.patch("builtins.print", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            runner.main()

        self.assertTrue(publication.rolled_back)
        self.assertTrue(publication.committed)

    def test_main_does_not_report_pass_when_evidence_commit_fails(self) -> None:
        runner = load_runner()

        class Publication:
            path = Path("/tmp/eb11-evidence.json")
            rolled_back = False

            def rollback(self) -> None:
                self.rolled_back = True

            def commit(self) -> None:
                raise runner.AcceptanceFailed("commit failed")

        publication = Publication()
        stdout = []
        stderr = []
        with (
            mock.patch.object(runner, "parse_arguments"),
            mock.patch.object(runner, "run_acceptance", return_value=publication),
            mock.patch("builtins.print") as output,
        ):
            exit_code = runner.main()

        for call in output.call_args_list:
            rendered = str(call.args[0])
            if call.kwargs.get("file") is runner.sys.stderr:
                stderr.append(rendered)
            else:
                stdout.append(rendered)
        self.assertEqual(exit_code, 1)
        self.assertFalse(any("PASS" in line for line in stdout))
        self.assertTrue(any("FAIL" in line for line in stderr))
        self.assertTrue(publication.rolled_back)


class ReleaseIdentityTests(unittest.TestCase):
    def test_cli_cannot_supply_self_proving_artifact_identity(self) -> None:
        runner = load_runner()
        with mock.patch.object(
            runner.sys,
            "argv",
            [
                "runner",
                "--interactive-device-acceptance",
                "--app",
                "/Applications/Product.app",
                "--deployment-profile",
                "/tmp/deployment.json",
                "--evidence",
                "/tmp/evidence.json",
            ],
        ):
            arguments = runner.parse_arguments()

        self.assertEqual(arguments.app, Path("/Applications/Product.app"))
        self.assertFalse(hasattr(arguments, "expected_bundle_tree_sha256"))
        self.assertFalse(hasattr(arguments, "expected_executor_build_id"))

    def test_signed_identity_must_match_source_profile_and_packaged_executor(self) -> None:
        runner = load_runner()
        artifact = runner.ArtifactFacts(
            authority="Developer ID Application: Example",
            team_id="TEAM123",
            bundle_cdhash="c" * 40,
            bundle_tree_sha256="a" * 64,
            bundle_bytes=123,
            executor_build_id="eb11-current",
        )
        app_identity = runner.AppIdentity(
            bundle_identifier="com.aventador.automationtool",
            version="0.1.0",
            executable_path=Path("/Applications/Product.app/Contents/MacOS/Product"),
        )
        source = runner.SourceFacts(git_commit="d" * 40, tree_sha256="e" * 64)
        release = runner.SignedReleaseIdentity(
            source_git_commit="d" * 40,
            source_tree_sha256="e" * 64,
            executor_build_id="eb11-current",
            target="macos-arm64",
            architecture="aarch64",
            deployment_profile_id="demo-xuanbai",
        )
        profile_root = runner.app_data_root() / "profiles/demo-xuanbai/embedded-browser-profiles"

        runner.require_release_identity(
            release,
            artifact=artifact,
            app_identity=app_identity,
            profile_root=profile_root,
            source=source,
        )
        with self.assertRaises(runner.AcceptanceFailed):
            runner.require_release_identity(
                release,
                artifact=artifact,
                app_identity=app_identity,
                profile_root=profile_root,
                source=runner.SourceFacts(git_commit="d" * 40, tree_sha256="f" * 64),
            )
        with self.assertRaises(runner.AcceptanceFailed):
            runner.require_release_identity(
                runner.SignedReleaseIdentity(
                    source_git_commit="d" * 40,
                    source_tree_sha256="e" * 64,
                    executor_build_id="eb11-old",
                    target="macos-arm64",
                    architecture="aarch64",
                    deployment_profile_id="demo-xuanbai",
                ),
                artifact=artifact,
                app_identity=app_identity,
                profile_root=profile_root,
                source=source,
            )

    def test_signed_identity_accepts_an_ancestor_commit_for_the_exact_source_tree(self) -> None:
        runner = load_runner()
        artifact = runner.ArtifactFacts(
            authority="Developer ID Application: Example",
            team_id="TEAM123",
            bundle_cdhash="c" * 40,
            bundle_tree_sha256="a" * 64,
            bundle_bytes=123,
            executor_build_id="eb11-current",
        )
        app_identity = runner.AppIdentity(
            bundle_identifier="com.aventador.automationtool",
            version="0.1.0",
            executable_path=Path("/Applications/Product.app/Contents/MacOS/Product"),
        )
        release = runner.SignedReleaseIdentity(
            source_git_commit="c" * 40,
            source_tree_sha256="e" * 64,
            executor_build_id="eb11-current",
            target="macos-arm64",
            architecture="aarch64",
            deployment_profile_id="demo-xuanbai",
        )
        current = runner.SourceFacts(git_commit="d" * 40, tree_sha256="e" * 64)
        profile_root = runner.app_data_root() / "profiles/demo-xuanbai/embedded-browser-profiles"

        with mock.patch.object(
            runner,
            "source_commit_is_ancestor",
            return_value=True,
        ):
            runner.require_release_identity(
                release,
                artifact=artifact,
                app_identity=app_identity,
                profile_root=profile_root,
                source=current,
            )

        with (
            mock.patch.object(
                runner,
                "source_commit_is_ancestor",
                return_value=False,
            ),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.require_release_identity(
                release,
                artifact=artifact,
                app_identity=app_identity,
                profile_root=profile_root,
                source=current,
            )


class ProfileBoundaryTests(unittest.TestCase):
    """What one sample of the Profile boundary is allowed to conclude."""

    def _scene(self, runner: ModuleType) -> tuple[object, object, list[object]]:
        app = Path("/Applications/Formal Product.app")
        profile_root = (
            runner.app_data_root() / "profiles/demo-xuanbai/embedded-browser-profiles"
        )
        candidate = profile_root / "douyin" / "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=app / "Contents/Resources/local-executor/package/executor",
            browser_path=app / "Contents/Resources/embedded-browser/Browser",
            profile_root=profile_root,
            executor_identity=runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40),
            browser_identity=runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40),
        )
        browser = runner.ProcessRecord(
            12,
            11,
            f"{contract.browser_path} --user-data-dir={candidate}",
            "C",
        )
        return contract, browser, [browser]

    @staticmethod
    def _private_directory_identity(counter: list[int]):
        def opener(_directory: Path) -> tuple[int, tuple[int, int]]:
            counter[0] += 1
            return os.open(os.devnull, os.O_RDONLY), (1, 2)

        return opener

    def test_a_sample_that_sees_nothing_yet_is_not_a_violation(self) -> None:
        """Absence at one instant is not proof the Profile was not used.

        2026-08-05, Windows, installed release package: the QR flow opened and
        the run then died with `Chromium did not open the App-owned Profile`.
        It was not reproducible — polling this check directly from the moment
        Chromium appeared bound the Profile on every one of ~60 consecutive
        samples, and the next real run bound it on 12 of 13. So the failing
        sample caught an instant when the browser tree held nothing on disk
        inside the Profile, and read that instant as a finding.

        A sample that observes nothing carries no binding; it is not evidence of
        a violation, and it must not end the run. The guarantee is unchanged
        because `require_complete_runtime` still fails unless some sample did
        observe the App-owned Profile — the same way `read_process_open_paths`
        returning `None` for a process that exited mid-audit already means
        "this sample says nothing" rather than "the Profile was not used".
        """
        runner = load_runner()
        contract, browser, scoped = self._scene(runner)
        opened = [0]

        with (
            mock.patch.object(
                runner,
                "require_private_directory_identity",
                side_effect=self._private_directory_identity(opened),
            ),
            mock.patch.object(runner, "read_process_open_paths", return_value=[]),
        ):
            binding = runner.require_browser_profile_boundary(contract, browser, scoped)

        self.assertIsNone(binding)
        self.assertEqual(opened[0], 3)

    def test_a_sample_that_sees_the_daily_profile_is_still_fatal(self) -> None:
        """A path inside the operator's own browser Profile is proof, so it fails.

        This is the half that must survive the change above: `CLAUDE.md` §5 says
        the packaged browser never opens the operator's day-to-day Profile, and
        one sighting is enough to know it did.
        """
        runner = load_runner()
        contract, browser, scoped = self._scene(runner)
        daily = runner.device_driver().daily_browser_profile_roots()[0] / "Default/Cookies"

        with (
            mock.patch.object(
                runner,
                "require_private_directory_identity",
                side_effect=self._private_directory_identity([0]),
            ),
            mock.patch.object(runner, "read_process_open_paths", return_value=[daily]),
            self.assertRaisesRegex(runner.AcceptanceFailed, "default browser Profile"),
        ):
            runner.require_browser_profile_boundary(contract, browser, scoped)


class RuntimeObservationTests(unittest.TestCase):
    def test_one_browser_with_its_own_children_is_one_browser(self) -> None:
        """Chromium's children are not additional browsers.

        2026-08-05, Windows, installed release package: the QR flow opened
        correctly and the observation then failed with
        `observed duplicate packaged runtime processes`. Measured on the live
        App: eleven processes ran the packaged `chrome.exe` — one browser and
        ten of its own children (`--type=renderer`, `--type=gpu-process`,
        `--type=utility`, `--type=crashpad-handler`), every one of them
        parented to that browser.

        macOS never showed this because its helper processes run a *different*
        binary inside the bundle, so they never matched `browser_path` at all.
        Windows runs the same executable for all of them, so counting by
        executable turns one browser into ten.

        The discriminator is Chromium's own: the browser process carries no
        `--type=`, and every process that does is a child of one. That is true
        on both platforms, so it is one rule rather than a host seam.
        """
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executor = app / "Contents/Resources/local-executor/package/executor"
        browser = app / "Contents/Resources/embedded-browser/Browser"
        profile = (
            runner.app_data_root()
            / "profiles/demo-xuanbai/embedded-browser-profiles/douyin"
            / "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        )
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=executor,
            browser_path=browser,
            profile_root=profile.parents[1],
            executor_identity=runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40),
            browser_identity=runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40),
        )
        instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/App"), "A")
        executor_process = runner.ProcessRecord(11, 10, os.fspath(executor), "B")
        browser_process = runner.ProcessRecord(
            12,
            11,
            f"{browser} --user-data-dir={profile}",
            "C",
        )
        children = [
            runner.ProcessRecord(
                pid,
                12,
                f"{browser} --type={kind} --user-data-dir={profile}",
                "D",
            )
            for pid, kind in (
                (13, "renderer"),
                (14, "gpu-process"),
                (15, "utility"),
                (16, "crashpad-handler"),
            )
        ]
        binding = runner.ProfileDirectoryBinding(
            path=profile,
            parent_fd=10,
            directory_fd=11,
            parent_identity=(1, 2),
            identity=(1, 3),
        )

        with (
            mock.patch.object(
                runner,
                "process_snapshot",
                return_value=[instance, executor_process, browser_process, *children],
            ),
            mock.patch.object(runner, "verify_runtime_process_identity", return_value=True),
            mock.patch.object(
                runner,
                "require_browser_profile_boundary",
                return_value=binding,
            ),
        ):
            observed = runner.observe_instance_runtime(contract, instance)

        self.assertTrue(observed.executor_observed)
        self.assertTrue(observed.embedded_browser_observed)
        self.assertEqual(observed.profile_directories, (profile,))

    def test_a_second_real_browser_is_still_a_duplicate(self) -> None:
        """The filter must not turn the duplicate check off.

        A second process running the packaged browser with no `--type=` is a
        second browser, and that is exactly what the check exists to catch.
        """
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executor = app / "Contents/Resources/local-executor/package/executor"
        browser = app / "Contents/Resources/embedded-browser/Browser"
        profile = (
            runner.app_data_root()
            / "profiles/demo-xuanbai/embedded-browser-profiles/douyin"
            / "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        )
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=executor,
            browser_path=browser,
            profile_root=profile.parents[1],
            executor_identity=runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40),
            browser_identity=runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40),
        )
        instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/App"), "A")
        executor_process = runner.ProcessRecord(11, 10, os.fspath(executor), "B")
        first = runner.ProcessRecord(12, 11, f"{browser} --user-data-dir={profile}", "C")
        second = runner.ProcessRecord(13, 11, f"{browser} --user-data-dir={profile}", "D")

        with (
            mock.patch.object(
                runner,
                "process_snapshot",
                return_value=[instance, executor_process, first, second],
            ),
            mock.patch.object(runner, "verify_runtime_process_identity", return_value=True),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.observe_instance_runtime(contract, instance)

    def test_runtime_observation_requires_browser_to_descend_from_executor(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executor = app / "Contents/Resources/local-executor/package/executor"
        browser = app / "Contents/Resources/embedded-browser/Browser"
        profile = (
            runner.app_data_root()
            / "profiles/demo-xuanbai/embedded-browser-profiles/douyin"
            / "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        )
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=executor,
            browser_path=browser,
            profile_root=profile.parents[1],
            executor_identity=runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40),
            browser_identity=runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40),
        )
        instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/App"), "A")
        executor_process = runner.ProcessRecord(11, 10, os.fspath(executor), "B")
        sibling_browser = runner.ProcessRecord(
            12,
            10,
            f"{browser} --user-data-dir={profile}",
            "C",
        )
        with (
            mock.patch.object(
                runner,
                "process_snapshot",
                return_value=[instance, executor_process, sibling_browser],
            ),
            mock.patch.object(runner, "verify_runtime_process_identity"),
            mock.patch.object(
                runner,
                "require_browser_profile_boundary",
                return_value=profile,
            ),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.observe_instance_runtime(contract, instance)

    def test_runtime_observation_rejects_a_same_path_replacement_process(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executor = app / "Contents/Resources/local-executor/package/executor"
        browser = app / "Contents/Resources/embedded-browser/Browser"
        profile = (
            runner.app_data_root()
            / "profiles/demo-xuanbai/embedded-browser-profiles/douyin"
            / "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        )
        expected_executor = runner.CodeIdentity(
            identifier="com.aventador.automationtool.executor",
            authority="Developer ID Application: Expected",
            team_id="EXPECTEDTEAM",
            cdhash="a" * 40,
        )
        expected_browser = runner.CodeIdentity(
            identifier="com.google.Chrome.for.Testing",
            authority="Developer ID Application: Expected",
            team_id="EXPECTEDTEAM",
            cdhash="b" * 40,
        )
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=executor,
            browser_path=browser,
            profile_root=profile.parents[1],
            executor_identity=expected_executor,
            browser_identity=expected_browser,
        )
        instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/Product"), "A")
        executor_process = runner.ProcessRecord(11, 10, os.fspath(executor), "B")
        browser_process = runner.ProcessRecord(
            12,
            11,
            f"{browser} --user-data-dir={profile}",
            "C",
        )
        verified = subprocess.CompletedProcess(["codesign"], 0, stdout="", stderr="")
        executor_details = subprocess.CompletedProcess(
            ["codesign"],
            0,
            stdout="",
            stderr=(
                f"Identifier={expected_executor.identifier}\n"
                f"Authority={expected_executor.authority}\n"
                f"TeamIdentifier={expected_executor.team_id}\n"
                f"CDHash={expected_executor.cdhash}\n"
            ),
        )
        replacement_browser_details = subprocess.CompletedProcess(
            ["codesign"],
            0,
            stdout="",
            stderr=(
                f"Identifier={expected_browser.identifier}\n"
                f"Authority={expected_browser.authority}\n"
                f"TeamIdentifier={expected_browser.team_id}\n"
                f"CDHash={'c' * 40}\n"
            ),
        )

        with (
            # macOS 形状的固定装置（`.app` / `Contents/…` / codesign 输出），
            # 验的是替换进程会被识别出来这条性质本身。宿主钉在 macOS driver 上，
            # 否则 `verify_runtime_process_identity` 会走 Windows 那一支的摘要比对。
            mock.patch.object(
                runner, "device_driver", return_value=runner.MacosDeviceDriver()
            ),
            mock.patch.object(
                runner,
                "process_snapshot",
                return_value=[instance, executor_process, browser_process],
            ),
            mock.patch.object(
                runner,
                "run_checked",
                side_effect=[
                    verified,
                    executor_details,
                    verified,
                    replacement_browser_details,
                ],
            ) as checked,
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.observe_instance_runtime(contract, instance)

        self.assertEqual(
            [call.args[0][-1] for call in checked.call_args_list],
            ["11", "11", "12", "12"],
        )

    def test_runtime_identity_sampling_treats_process_exit_as_transient(self) -> None:
        runner = load_runner()
        record = runner.ProcessRecord(
            12,
            11,
            "/Applications/Formal Product.app/Contents/Resources/browser",
            "C",
        )
        expected = runner.CodeIdentity(
            identifier="com.google.Chrome.for.Testing",
            authority="Developer ID Application: Expected",
            team_id="EXPECTEDTEAM",
            cdhash="b" * 40,
        )
        verified = subprocess.CompletedProcess(["codesign"], 0, stdout="", stderr="")
        details = subprocess.CompletedProcess(
            ["codesign"],
            0,
            stdout="",
            stderr=(
                f"Identifier={expected.identifier}\n"
                f"Authority={expected.authority}\n"
                f"TeamIdentifier={expected.team_id}\n"
                f"CDHash={expected.cdhash}\n"
            ),
        )

        with (
            mock.patch.object(
                runner,
                "process_snapshot",
                side_effect=[[record], []],
            ),
            mock.patch.object(runner, "run_checked", side_effect=[verified, details]),
        ):
            self.assertFalse(runner.macos_verify_runtime_process_identity(record, expected))

    def test_runtime_identity_sampling_treats_codesign_failure_after_exit_as_transient(
        self,
    ) -> None:
        runner = load_runner()
        record = runner.ProcessRecord(
            12,
            11,
            "/Applications/Formal Product.app/Contents/Resources/browser",
            "C",
        )
        expected = runner.CodeIdentity(
            identifier="com.google.Chrome.for.Testing",
            authority="Developer ID Application: Expected",
            team_id="EXPECTEDTEAM",
            cdhash="b" * 40,
        )
        verified = subprocess.CompletedProcess(["codesign"], 0, stdout="", stderr="")
        signing_failure = subprocess.CalledProcessError(1, ["codesign"])

        for signing_results in ([signing_failure], [verified, signing_failure]):
            with (
                self.subTest(signing_results=signing_results),
                mock.patch.object(
                    runner,
                    "process_snapshot",
                    side_effect=[[record], []],
                ),
                mock.patch.object(
                    runner,
                    "run_checked",
                    side_effect=signing_results,
                ),
            ):
                self.assertFalse(runner.macos_verify_runtime_process_identity(record, expected))

        with (
            mock.patch.object(
                runner,
                "process_snapshot",
                side_effect=[[record], [record]],
            ),
            mock.patch.object(runner, "run_checked", side_effect=signing_failure),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.macos_verify_runtime_process_identity(record, expected)

    def test_open_file_sampling_retries_only_when_a_sampled_process_exits(self) -> None:
        runner = load_runner()
        record = runner.ProcessRecord(
            12,
            11,
            "/Applications/Formal Product.app/Contents/Resources/browser",
            "C",
        )
        failure = subprocess.CalledProcessError(1, ["/usr/sbin/lsof"])

        # 点名 macOS 那一支：`read_process_open_paths` 已按宿主分发，验的是 `lsof`
        # 失败之后怎么区分「进程没了」和「审计坏了」，那是 AppleScript/lsof 侧的形状。
        # Windows 侧同一条性质由 `WindowsAccessibilityTests` 里对应的用例守。
        with (
            mock.patch.object(runner, "run_checked", side_effect=failure),
            mock.patch.object(runner, "process_snapshot", return_value=[]),
        ):
            self.assertIsNone(runner.macos_read_process_open_paths([record]))

        with (
            mock.patch.object(runner, "run_checked", side_effect=failure),
            mock.patch.object(runner, "process_snapshot", return_value=[record]),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.macos_read_process_open_paths([record])

    @unittest.skipUnless(os.name == "posix", "POSIX Profile boundary contract")
    def test_runtime_observation_rejects_a_profile_symlinked_outside_app_data(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "Formal Product.app"
            browser = app / "Contents/Resources/embedded-browser/Browser"
            executor = app / "Contents/Resources/local-executor/package/executor"
            profile_root = root / "embedded-browser-profiles"
            platform_root = profile_root / "douyin"
            external = root / "external-default-profile"
            profile_id = "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
            platform_root.mkdir(parents=True, mode=0o700)
            external.mkdir(mode=0o700)
            profile = platform_root / profile_id
            profile.symlink_to(external, target_is_directory=True)
            expected_executor = runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40)
            expected_browser = runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40)
            contract = runner.RuntimeContract(
                app_path=app,
                executor_path=executor,
                browser_path=browser,
                profile_root=profile_root,
                executor_identity=expected_executor,
                browser_identity=expected_browser,
            )
            instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/App"), "A")
            browser_process = runner.ProcessRecord(
                12,
                10,
                f"{browser} --user-data-dir={profile}",
                "B",
            )
            verified = subprocess.CompletedProcess(["codesign"], 0, stdout="", stderr="")
            details = subprocess.CompletedProcess(
                ["codesign"],
                0,
                stdout="",
                stderr=(
                    "Identifier=browser\nAuthority=Expected\nTeamIdentifier=TEAM\n"
                    f"CDHash={expected_browser.cdhash}\n"
                ),
            )
            with (
                mock.patch.object(
                    runner,
                    "process_snapshot",
                    return_value=[instance, browser_process],
                ),
                mock.patch.object(runner, "run_checked", side_effect=[verified, details]),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.observe_instance_runtime(contract, instance)

    @unittest.skipUnless(os.name == "posix", "POSIX Profile boundary contract")
    def test_runtime_observation_binds_profile_identity_across_open_file_audit(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "Formal Product.app"
            browser = app / "Contents/Resources/embedded-browser/Browser"
            executor = app / "Contents/Resources/local-executor/package/executor"
            profile_root = root / "embedded-browser-profiles"
            platform_root = profile_root / "douyin"
            profile_id = "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
            profile = platform_root / profile_id
            profile.mkdir(parents=True, mode=0o700)
            profile_root.chmod(0o700)
            platform_root.chmod(0o700)
            expected_executor = runner.CodeIdentity("executor", "Expected", "TEAM", "a" * 40)
            expected_browser = runner.CodeIdentity("browser", "Expected", "TEAM", "b" * 40)
            contract = runner.RuntimeContract(
                app_path=app,
                executor_path=executor,
                browser_path=browser,
                profile_root=profile_root,
                executor_identity=expected_executor,
                browser_identity=expected_browser,
            )
            instance = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/App"), "A")
            browser_process = runner.ProcessRecord(
                12,
                10,
                f"{browser} --user-data-dir={profile}",
                "B",
            )
            verified = subprocess.CompletedProcess(["codesign"], 0, stdout="", stderr="")
            details = subprocess.CompletedProcess(
                ["codesign"],
                0,
                stdout="",
                stderr=(
                    "Identifier=browser\nAuthority=Expected\nTeamIdentifier=TEAM\n"
                    f"CDHash={expected_browser.cdhash}\n"
                ),
            )

            def checked(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[0] == "/usr/sbin/lsof":
                    profile.rename(platform_root / "displaced")
                    profile.mkdir(mode=0o700)
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=f"p12\nfcwd\nn{profile}\n",
                        stderr="",
                    )
                return verified if "--verify" in command else details

            with (
                mock.patch.object(
                    runner,
                    "process_snapshot",
                    return_value=[instance, browser_process],
                ),
                mock.patch.object(runner, "run_checked", side_effect=checked),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.observe_instance_runtime(contract, instance)

    def test_deployment_profile_must_be_absolute_and_cannot_be_a_symlink(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            executable = root / "Product"
            profile = root / "deployment.json"
            link = root / "profile-link.json"
            deployment = {
                "profileId": "demo-xuanbai",
                "baseUrl": "https://at.xuanbai.tech",
                "allowedHosts": ["at.xuanbai.tech"],
            }
            manifest = {
                "version": runner.DEMO_PROFILE_VERSION,
                "profile": runner.DEMO_PROFILE_KIND,
                **deployment,
            }
            encoded = base64.urlsafe_b64encode(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).rstrip(b"=")
            executable.write_bytes(encoded)
            profile.write_text(json.dumps(deployment), encoding="utf-8")
            link.symlink_to(profile)

            previous = Path.cwd()
            os.chdir(root)
            try:
                with self.assertRaises(runner.AcceptanceFailed):
                    runner.compiled_deployment_profile_root(
                        executable,
                        Path("deployment.json"),
                    )
            finally:
                os.chdir(previous)
            with self.assertRaises(runner.AcceptanceFailed):
                runner.compiled_deployment_profile_root(executable, link)

    def test_compiled_demo_profile_selects_the_nested_app_data_root(self) -> None:
        runner = load_runner()
        deployment = {
            "profileId": "demo-xuanbai",
            "baseUrl": "https://at.xuanbai.tech",
            "allowedHosts": ["at.xuanbai.tech"],
        }
        manifest = {
            "version": "customer-demo-profile.v1",
            "profile": "demo",
            **deployment,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(
                manifest,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).rstrip(b"=")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            executable = root / "Product"
            executable.write_bytes(b"prefix" + encoded + b"suffix")
            profile = root / "deployment.json"
            profile.write_text(json.dumps(deployment), encoding="utf-8")

            self.assertEqual(
                runner.compiled_deployment_profile_root(executable, profile),
                runner.app_data_root()
                / "profiles"
                / "demo-xuanbai"
                / "embedded-browser-profiles",
            )

            executable.write_bytes(b"different build")
            with self.assertRaises(runner.AcceptanceFailed):
                runner.compiled_deployment_profile_root(executable, profile)

    def test_instance_scope_rejects_foreign_bundle_processes_without_owning_them(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        root = runner.ProcessRecord(10, 1, os.fspath(app / "Contents/MacOS/Product"), "A")
        own_child = runner.ProcessRecord(
            11,
            10,
            os.fspath(app / "Contents/Resources/local-executor/executor"),
            "B",
        )
        foreign_root = runner.ProcessRecord(
            20,
            1,
            os.fspath(app / "Contents/MacOS/Product"),
            "C",
        )

        with self.assertRaises(runner.AcceptanceFailed):
            runner.instance_process_records(app, root, [root, own_child, foreign_root])
        self.assertEqual(
            runner.instance_process_records(
                app,
                root,
                [root, own_child, foreign_root],
                reject_foreign=False,
            ),
            [root, own_child],
        )

    def test_instance_scope_keeps_nonce_owned_reparented_browser_helpers(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        root = runner.ProcessRecord(
            10,
            1,
            os.fspath(app / "Contents/MacOS/Product"),
            "A",
            launch_nonce="owned-launch",
        )
        own_child = runner.ProcessRecord(
            11,
            10,
            os.fspath(app / "Contents/Resources/local-executor/executor"),
            "B",
        )
        reparented_helper = runner.ProcessRecord(
            12,
            1,
            os.fspath(
                app
                / "Contents/Resources/embedded-browser/Browser.app/Contents/"
                "Frameworks/Browser Framework.framework/Helpers/chrome_crashpad_handler"
            ),
            "C",
        )

        with mock.patch.object(
            runner,
            "process_has_launch_nonce",
            side_effect=lambda record, nonce: (
                record == reparented_helper and nonce == "owned-launch"
            ),
        ):
            self.assertEqual(
                runner.instance_process_records(
                    app,
                    root,
                    [root, own_child, reparented_helper],
                ),
                [root, own_child, reparented_helper],
            )

    def test_instance_scope_ignores_nonce_helper_that_exits_during_sampling(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        root = runner.ProcessRecord(
            10,
            1,
            os.fspath(app / "Contents/MacOS/Product"),
            "A",
            launch_nonce="owned-launch",
        )
        reparented_helper = runner.ProcessRecord(
            12,
            1,
            os.fspath(
                app
                / "Contents/Resources/embedded-browser/Browser.app/Contents/"
                "Frameworks/Browser Framework.framework/Helpers/chrome_crashpad_handler"
            ),
            "C",
        )

        with (
            mock.patch.object(runner, "process_has_launch_nonce", return_value=False),
            mock.patch.object(runner, "process_snapshot", return_value=[root]),
        ):
            self.assertEqual(
                runner.instance_process_records(app, root, [root, reparented_helper]),
                [root],
            )

        with (
            mock.patch.object(runner, "process_has_launch_nonce", return_value=False),
            mock.patch.object(
                runner,
                "process_snapshot",
                return_value=[root, reparented_helper],
            ),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.instance_process_records(app, root, [root, reparented_helper])

    def test_instance_scope_rejects_reused_root_pid(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        expected = runner.ProcessRecord(
            10, 1, os.fspath(app / "Contents/MacOS/Product"), "original-start"
        )
        reused = runner.ProcessRecord(
            10, 1, os.fspath(app / "Contents/MacOS/Product"), "later-start"
        )
        child = runner.ProcessRecord(
            11,
            10,
            os.fspath(app / "Contents/Resources/local-executor/executor"),
            "later-child",
        )

        self.assertEqual(
            runner.instance_process_records(app, expected, [reused, child]),
            [],
        )

    def test_only_packaged_executor_browser_and_owned_profile_complete_observation(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executor = app / "Contents/Resources/local-executor/package/automation-tool-executor"
        browser = (
            app
            / "Contents/Resources/embedded-browser/chrome-mac-arm64"
            / "Browser.app/Contents/MacOS/Browser"
        )
        contract = runner.RuntimeContract(
            app_path=app,
            executor_path=executor,
            browser_path=browser,
            profile_root=(
                runner.app_data_root()
                / "profiles"
                / "demo-xuanbai"
                / "embedded-browser-profiles"
            ),
        )
        canonical_profile_id = "6d9221cb-e9dc-4359-9f6b-34f7fbc55316"
        wrong = runner.observe_runtime(
            contract,
            [
                runner.ProcessRecord(1, 0, os.fspath(executor)),
                runner.ProcessRecord(
                    2,
                    1,
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"--user-data-dir={contract.profile_root / 'douyin' / canonical_profile_id}",
                ),
                runner.ProcessRecord(3, 1, f"{browser} --user-data-dir=/tmp/profile"),
            ],
        )
        self.assertTrue(wrong.executor_observed)
        self.assertTrue(wrong.embedded_browser_observed)
        self.assertFalse(wrong.app_owned_profile_observed)
        with self.assertRaises(runner.AcceptanceFailed):
            runner.require_complete_runtime(wrong)

        complete = wrong.merge(
            runner.observe_runtime(
                contract,
                [
                    runner.ProcessRecord(
                        4,
                        1,
                        f"{browser} --user-data-dir="
                        f"{contract.profile_root / 'douyin' / canonical_profile_id}",
                    )
                ],
                (contract.profile_root / "douyin" / canonical_profile_id,),
            )
        )
        runner.require_complete_runtime(complete)

        nested_or_noncanonical = runner.observe_runtime(
            contract,
            [
                runner.ProcessRecord(
                    5,
                    1,
                    f"{browser} --user-data-dir="
                    f"{contract.profile_root / 'douyin' / canonical_profile_id / 'nested'}",
                ),
                runner.ProcessRecord(
                    6,
                    1,
                    f"{browser} --user-data-dir={contract.profile_root / 'douyin' / 'profile'}",
                ),
            ],
        )
        self.assertFalse(nested_or_noncanonical.app_owned_profile_observed)

        duplicate_profile_arguments = runner.observe_runtime(
            contract,
            [
                runner.ProcessRecord(
                    7,
                    1,
                    f"{browser} --user-data-dir="
                    f"{contract.profile_root / 'douyin' / canonical_profile_id} "
                    "--user-data-dir=/Users/example/Library/Application Support/Google/Chrome",
                )
            ],
        )
        self.assertFalse(duplicate_profile_arguments.app_owned_profile_observed)


class LaunchCleanupTests(unittest.TestCase):
    def test_force_cleanup_kills_a_helper_discovered_after_the_first_kill(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        root = runner.ProcessRecord(
            10,
            1,
            os.fspath(app / "Contents/MacOS/Product"),
            "started",
        )
        late = runner.ProcessRecord(
            11,
            1,
            os.fspath(app / "Contents/Resources/late-helper"),
            "late-started",
        )

        with (
            mock.patch.object(
                runner,
                "owned_process_records",
                side_effect=[[root], [root], [late], []],
            ),
            mock.patch.object(
                runner,
                "still_running",
                side_effect=[[root], [], []],
            ),
            mock.patch.object(
                runner.time,
                "monotonic",
                side_effect=[0.0, 9.0, 10.0, 10.0, 11.0],
            ),
            mock.patch.object(runner.time, "sleep"),
            mock.patch.object(runner, "terminate_records") as terminate,
        ):
            runner.cleanup_owned_runtime(app, root)

        self.assertEqual(
            terminate.call_args_list,
            [
                mock.call([root], runner.signal.SIGTERM),
                mock.call([root], runner.signal.SIGKILL),
                mock.call([late], runner.signal.SIGKILL),
            ],
        )

    def test_running_process_must_have_the_verified_release_cdhash(self) -> None:
        runner = load_runner()
        instance = runner.ProcessRecord(
            10,
            1,
            "/Applications/Formal Product.app/Contents/MacOS/Product",
            "started",
        )
        release = runner.VerifiedRelease(
            app_identity=runner.AppIdentity(
                bundle_identifier="com.aventador.automationtool",
                version="0.1.0",
                executable_path=Path(instance.command),
            ),
            runtime_contract=runner.RuntimeContract(
                app_path=Path("/Applications/Formal Product.app"),
                executor_path=Path("/Applications/Formal Product.app/Contents/Resources/executor"),
                browser_path=Path("/Applications/Formal Product.app/Contents/Resources/browser"),
                profile_root=Path("/tmp/profiles"),
            ),
            artifact=runner.ArtifactFacts(
                authority="Developer ID Application: Expected",
                team_id="EXPECTEDTEAM",
                bundle_cdhash="a" * 40,
                bundle_tree_sha256="b" * 64,
                bundle_bytes=123,
                executor_build_id="current",
            ),
            release_identity=runner.SignedReleaseIdentity(
                source_git_commit="c" * 40,
                source_tree_sha256="d" * 64,
                executor_build_id="current",
                target="macos-arm64",
                architecture="aarch64",
                deployment_profile_id="demo-xuanbai",
            ),
            profile_root=Path("/tmp/profiles"),
        )
        replacement = subprocess.CompletedProcess(
            ["codesign"],
            0,
            stdout="",
            stderr=(
                "Identifier=com.aventador.automationtool\n"
                "Authority=Developer ID Application: Expected\n"
                "TeamIdentifier=EXPECTEDTEAM\n"
                f"CDHash={'e' * 40}\n"
            ),
        )

        with (
            mock.patch.object(runner, "process_snapshot", return_value=[instance]),
            mock.patch.object(runner, "run_checked", return_value=replacement),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.macos_require_running_release(instance, release)

        expected = subprocess.CompletedProcess(
            ["codesign"],
            0,
            stdout="",
            stderr=replacement.stderr.replace("e" * 40, "a" * 40),
        )
        with (
            mock.patch.object(runner, "process_snapshot", return_value=[instance]),
            mock.patch.object(runner, "run_checked", return_value=expected),
        ):
            runner.macos_require_running_release(instance, release)

    def test_launch_open_interruption_reclaims_nonce_owned_processes(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executable = app / "Contents/MacOS/Product"
        instance = runner.ProcessRecord(10, 1, os.fspath(executable), "started")

        with (
            mock.patch.object(runner.secrets, "token_urlsafe", return_value="launch-nonce"),
            # 打断的是「启动 App」这一步，不是 `run_checked`：启动已按宿主分发，
            # Windows 上根本不经过 run_checked，继续 patch 它等于什么都没打断。
            mock.patch.object(runner, "start_app", side_effect=KeyboardInterrupt),
            mock.patch.object(
                runner,
                "nonce_owned_process_records",
                side_effect=[[instance], [], [], []],
            ) as nonce_records,
            mock.patch.object(runner, "cleanup_owned_runtime") as cleanup,
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 0.0, 46.0]),
            mock.patch.object(runner.time, "sleep"),
            self.assertRaises(KeyboardInterrupt),
        ):
            runner.launch_app(app, executable)

        self.assertEqual(
            nonce_records.call_args_list,
            [mock.call(app, "launch-nonce")] * 4,
        )
        cleanup.assert_called_once_with(app, None, [instance])

    def test_launch_interruption_waits_for_a_delayed_nonce_owned_process(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executable = app / "Contents/MacOS/Product"
        instance = runner.ProcessRecord(10, 1, os.fspath(executable), "started")

        with (
            mock.patch.object(runner.secrets, "token_urlsafe", return_value="launch-nonce"),
            # 打断的是「启动 App」这一步，不是 `run_checked`：启动已按宿主分发，
            # Windows 上根本不经过 run_checked，继续 patch 它等于什么都没打断。
            mock.patch.object(runner, "start_app", side_effect=KeyboardInterrupt),
            mock.patch.object(
                runner,
                "nonce_owned_process_records",
                side_effect=[[], [instance], [], [], []],
            ) as nonce_records,
            mock.patch.object(runner, "cleanup_owned_runtime") as cleanup,
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 46.0]),
            mock.patch.object(runner.time, "sleep"),
            self.assertRaises(KeyboardInterrupt),
        ):
            runner.launch_app(app, executable)

        self.assertGreaterEqual(nonce_records.call_count, 3)
        cleanup.assert_called_once_with(app, None, [instance])

    def test_delayed_launch_cleanup_covers_startup_timeout_and_final_quiet_polls(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        late = runner.ProcessRecord(
            10,
            1,
            os.fspath(app / "Contents/MacOS/Product"),
            "started",
        )
        self.assertGreaterEqual(
            runner.LAUNCH_CLEANUP_DISCOVERY_SECONDS,
            runner.WINDOW_TIMEOUT_SECONDS,
        )
        with (
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 46.0]),
            mock.patch.object(
                runner,
                "nonce_owned_process_records",
                side_effect=[[], [], [late], [], [], []],
            ),
            mock.patch.object(runner.time, "sleep"),
            mock.patch.object(runner, "cleanup_owned_runtime") as cleanup,
        ):
            runner.cleanup_delayed_nonce_owned_runtime(app, "launch-nonce")

        cleanup.assert_called_once_with(app, None, [late])

    def test_nonce_scope_reclaims_reparented_descendant_after_root_disappears(
        self,
    ) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executable = app / "Contents/MacOS/Product"
        instance = runner.ProcessRecord(
            10,
            1,
            os.fspath(executable),
            "started",
            launch_nonce="launch-nonce",
        )
        orphan = runner.ProcessRecord(
            11,
            1,
            os.fspath(app / "Contents/Resources/local-executor/package/executor"),
            "child-started",
        )

        with (
            mock.patch.object(runner, "process_snapshot", return_value=[orphan]),
            mock.patch.object(runner, "process_has_launch_nonce", return_value=True),
        ):
            self.assertEqual(runner.owned_process_records(app, instance), [orphan])

    def test_launch_cleans_the_identified_instance_when_accessibility_is_ambiguous(
        self,
    ) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executable = app / "Contents/MacOS/Product"
        instance = runner.ProcessRecord(10, 1, os.fspath(executable), "started")

        with (
            # 这两条用的是 macOS 形状的固定装置（`.app` / `Contents/MacOS`），
            # 验的是启动失败后的清理逻辑本身，不是宿主。启动打桩掉，宿主
            # 钉在 macOS driver 上，`packaged_process_records` 才认得这个前缀。
            mock.patch.object(runner, "start_app"),
            mock.patch.object(
                runner, "device_driver", return_value=runner.MacosDeviceDriver()
            ),
            mock.patch.object(
                runner,
                "process_snapshot",
                side_effect=[[instance], [instance]],
            ),
            mock.patch.object(runner, "bundle_process_ids", return_value={10, 20}),
            mock.patch.object(runner, "process_has_launch_nonce", return_value=True),
            mock.patch.object(runner, "cleanup_owned_runtime") as cleanup,
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 0.0]),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.launch_app(app, executable)

        cleanup.assert_called_once_with(app, instance)

    def test_launch_cleans_the_identified_instance_when_operator_cancels(self) -> None:
        runner = load_runner()
        app = Path("/Applications/Formal Product.app")
        executable = app / "Contents/MacOS/Product"
        instance = runner.ProcessRecord(10, 1, os.fspath(executable), "started")

        with (
            # 这两条用的是 macOS 形状的固定装置（`.app` / `Contents/MacOS`），
            # 验的是启动失败后的清理逻辑本身，不是宿主。启动打桩掉，宿主
            # 钉在 macOS driver 上，`packaged_process_records` 才认得这个前缀。
            mock.patch.object(runner, "start_app"),
            mock.patch.object(
                runner, "device_driver", return_value=runner.MacosDeviceDriver()
            ),
            mock.patch.object(runner, "process_snapshot", return_value=[instance]),
            mock.patch.object(runner, "process_has_launch_nonce", return_value=True),
            mock.patch.object(runner, "bundle_process_ids", side_effect=KeyboardInterrupt),
            mock.patch.object(runner, "cleanup_owned_runtime") as cleanup,
            mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 0.0]),
            self.assertRaises(KeyboardInterrupt),
        ):
            runner.launch_app(app, executable)

        cleanup.assert_called_once_with(app, instance)


class DeviceDriverSeamTests(unittest.TestCase):
    """One runner, two hosts — the lifecycle must not fork into a second script.

    Everything this runner *decides* is platform-neutral: sign in, re-check,
    log out and prove the old Profile is gone, scan again, restart and prove the
    same Profile came back, exit and prove nothing of ours is left. Only the
    *observations* are macOS-specific — AppleScript for the accessibility tree,
    `codesign` against a live PID, `lsof` for open files, `F_GETPATH` to prove
    an inode has no name.

    Splitting those into a second Windows runner would fork the definition of
    what EB-11 means, which is the valuable part and the part that must stay
    single. So the platform sits behind a driver, and `require_device_boundary`
    picks one instead of refusing every host that is not a Mac.
    """

    def test_a_driver_exists_for_this_host(self) -> None:
        runner = load_runner()

        driver = runner.device_driver()

        self.assertEqual(sys.platform, driver.platform)

    def test_an_unimplemented_capability_names_itself(self) -> None:
        """A gap has to say which gap it is.

        `EB-11 formal App acceptance requires macOS` told an operator on
        Windows nothing about what was missing or what would fix it. Each
        capability the Windows driver has yet to grow reports its own name, so
        the message points at the next piece of work rather than at the host.
        """
        runner = load_runner()

        # The base driver, not a host one: which capabilities are still missing
        # changes as they land, and this is about how a gap reports itself, not
        # about which gap happens to be open today.
        driver = runner.DeviceDriver()

        with self.assertRaisesRegex(runner.AcceptanceFailed, "accessibility"):
            driver.press(4321, "确认注销")
        with self.assertRaisesRegex(runner.AcceptanceFailed, "signed release identity"):
            driver.read_release_identity(Path("/nowhere"))

    def test_the_windows_driver_reads_the_release_identity_it_ships(self) -> None:
        """`build_release_package --platform windows` writes this file.

        macOS carries the same seven fields in `Info.plist` under the outer
        Developer ID seal. An NSIS package has no plist, so the release writes
        `release-identity.v1.json` into the payload; this is the reader for it,
        and the two must agree field for field or a Windows package can never
        be matched against the source tree it came from.
        """
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release-identity.v1.json").write_text(
                json.dumps(
                    {
                        "architecture": "x86_64",
                        "buildId": "eb11-windows",
                        "deploymentProfileId": "local",
                        "schema": runner.RELEASE_IDENTITY_SCHEMA,
                        "sourceGitCommit": "c" * 40,
                        "sourceTreeSha256": "d" * 64,
                        "target": "windows-x86_64",
                    }
                ),
                encoding="utf-8",
            )

            identity = runner.WindowsDeviceDriver().read_release_identity(root)

        self.assertEqual(identity.target, "windows-x86_64")
        self.assertEqual(identity.source_tree_sha256, "d" * 64)
        self.assertEqual(identity.deployment_profile_id, "local")

    def test_the_windows_driver_refuses_an_identity_with_the_wrong_fields(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release-identity.v1.json").write_text(
                json.dumps({"target": "windows-x86_64"}), encoding="utf-8"
            )

            with self.assertRaisesRegex(runner.AcceptanceFailed, "invalid"):
                runner.WindowsDeviceDriver().read_release_identity(root)


class WindowsAccessibilityTests(unittest.TestCase):
    """The Windows half of "drive the App like a person would".

    Feasibility is measured, not assumed. WebView2 builds no accessibility tree
    until a client asks for one, so the runner has to launch the App with
    `--force-renderer-accessibility` (FINDING-webview2-accessibility-on-windows.md),
    and pressing was proven separately against the installed formal package on
    2026-08-05: an external UIA client invoked `重新检查` and the App opened a
    fresh connection to the control-plane origin, 1 → 2 on a counting listener.
    A transient "checking" screen would have been a far weaker witness.

    These tests hold the shape of that mechanism without needing a desktop, the
    way the macOS ones hold the AppleScript shape without needing a Mac.
    """

    def test_press_asks_whether_the_control_is_enabled_before_invoking_it(self) -> None:
        """The macOS lesson, re-checked on the host that answers it differently.

        `AXPress` on a disabled element silently does nothing and reports the
        same as a real press, which cost a run on 2026-08-04. UIA does not have
        that hole — `IsEnabled` is a first-class property — but the order still
        has to be right: read it, refuse, and only then invoke.
        """
        runner = load_runner()
        seen: dict[str, object] = {}

        def inspect(source: str, *, environment: dict[str, str], timeout: float = 30.0) -> str:
            del timeout
            seen["source"] = source
            seen["environment"] = environment
            self.assertLess(
                source.index("IsEnabled"),
                source.index("Invoke()"),
                "the enabled check must come before the press, not after it",
            )
            return "disabled"

        with (
            mock.patch.object(runner, "power_shell", side_effect=inspect),
            self.assertRaises(runner.AcceptanceFailed) as raised,
        ):
            runner.WindowsDeviceDriver().press(42, "打开登录处理")

        self.assertIn("打开登录处理", str(raised.exception))
        self.assertEqual(seen["environment"]["AUTOMATION_TOOL_EB11_UIA_PID"], "42")
        self.assertEqual(
            seen["environment"]["AUTOMATION_TOOL_EB11_UIA_LABEL"], "打开登录处理"
        )

    def test_the_label_never_becomes_part_of_the_script(self) -> None:
        """A UI label is data. Interpolating it would make it code.

        The macOS side escapes quotes into AppleScript source; PowerShell has
        more ways to be surprised by a string than that guard covers, and every
        one of them would run inside a script this runner wrote. The label goes
        through the environment instead, so the script stays a constant.
        """
        runner = load_runner()
        hostile = '"; Write-Output pressed; #'

        def inspect(source: str, *, environment: dict[str, str], timeout: float = 30.0) -> str:
            del timeout
            self.assertNotIn(hostile, source)
            self.assertEqual(environment["AUTOMATION_TOOL_EB11_UIA_LABEL"], hostile)
            return "not_found"

        with (
            mock.patch.object(runner, "power_shell", side_effect=inspect),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            runner.WindowsDeviceDriver().press(42, hostile)

    def test_press_accepts_only_the_word_the_script_prints_for_a_real_invoke(
        self,
    ) -> None:
        runner = load_runner()

        for answer in ("not_found", "no_invoke_pattern", "no_window", "no_process", ""):
            with (
                self.subTest(answer=answer),
                mock.patch.object(runner, "power_shell", return_value=answer),
                self.assertRaises(runner.AcceptanceFailed),
            ):
                runner.WindowsDeviceDriver().press(42, "安全注销")

        with mock.patch.object(runner, "power_shell", return_value="pressed"):
            runner.WindowsDeviceDriver().press(42, "安全注销")

    def test_visible_text_reads_names_and_values_out_of_the_webview_tree(self) -> None:
        runner = load_runner()

        def inspect(source: str, *, environment: dict[str, str], timeout: float = 30.0) -> str:
            del timeout, environment
            self.assertIn("Descendants", source)
            self.assertIn("ValuePattern", source)
            return "当前状态\n登录正常"

        with mock.patch.object(runner, "power_shell", side_effect=inspect):
            rendered = runner.WindowsDeviceDriver().visible_ui_text(42)

        self.assertIn("登录正常", rendered)

    def test_the_lifecycle_drives_through_the_host_driver(self) -> None:
        """The seam only counts if the flow actually goes through it.

        `WindowsDeviceDriver.press` existing changes nothing while
        `open_account_page`, `logout_current_session` and the rest still call
        the AppleScript implementation by name — the driver would be a class
        nobody constructs on the host that needs it.
        """
        runner = load_runner()
        calls: list[tuple[object, ...]] = []

        class RecordingDriver:
            platform = "recording"

            def press(self, process_id: int, label: str) -> None:
                calls.append(("press", process_id, label))

            def visible_ui_text(self, process_id: int) -> str:
                calls.append(("text", process_id))
                return "登录正常"

            def wait_for_window(self, process_id: int) -> None:
                calls.append(("window", process_id))

        with mock.patch.object(runner, "device_driver", return_value=RecordingDriver()):
            runner.press(7, "安全注销")
            rendered = runner.visible_ui_text(7)
            runner.wait_for_window(7)

        self.assertEqual(rendered, "登录正常")
        self.assertEqual(
            calls, [("press", 7, "安全注销"), ("text", 7), ("window", 7)]
        )

    def test_waiting_for_a_window_polls_until_the_tree_is_there(self) -> None:
        runner = load_runner()
        answers = ["no_window", "no_window", "ready"]

        with (
            mock.patch.object(runner, "power_shell", side_effect=answers),
            mock.patch.object(runner.time, "sleep"),
        ):
            runner.WindowsDeviceDriver().wait_for_window(42)

    def test_a_process_that_is_gone_is_not_waited_out(self) -> None:
        """A dead pid is an answer, not a reason to burn the whole timeout."""
        runner = load_runner()

        with (
            mock.patch.object(runner, "power_shell", return_value="no_process"),
            mock.patch.object(runner.time, "sleep"),
            self.assertRaisesRegex(runner.AcceptanceFailed, "process identity"),
        ):
            runner.WindowsDeviceDriver().wait_for_window(42)

    def test_the_windows_artifact_is_bound_by_digest_and_says_so(self) -> None:
        """No certificate on this host, so identity is the bytes, not a signer.

        macOS binds the artifact through the Developer ID chain: `codesign
        --verify`, `spctl` and `stapler` together say "the OS trusts this and it
        has not been altered since signing". Windows has no counterpart here —
        measured 2026-08-05, both certificate stores hold 0 code-signing certs
        and the installed binary reads `NotSigned`.

        The chosen replacement (user decision, 2026-08-05) is a digest binding:
        it proves the install *is* the package built from this source tree, and
        it does not pretend to prove that the OS trusts it or that nobody edited
        it. So the signer fields stay empty rather than being filled with
        something that reads like a signature, and the measured Authenticode
        status is carried explicitly.
        """
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nested").mkdir()
            (root / "automation-tool-desktop.exe").write_bytes(b"MZ binary")
            (root / "nested" / "resource.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(
                runner.WindowsDeviceDriver,
                "authenticode_status",
                return_value="NotSigned",
            ):
                facts = runner.WindowsDeviceDriver().verify_artifact(root, "windows-release")

            self.assertEqual(facts.executor_build_id, "windows-release")
            self.assertEqual(facts.authority, "")
            self.assertEqual(facts.team_id, "")
            self.assertEqual(facts.bundle_cdhash, "")
            self.assertEqual(facts.code_signing, "NotSigned")
            self.assertRegex(facts.bundle_tree_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(facts.bundle_bytes, len(b"MZ binary") + len("{}"))

            # The digest has to move when the tree moves, or it binds nothing.
            (root / "nested" / "resource.json").write_text('{"x":1}', encoding="utf-8")
            with mock.patch.object(
                runner.WindowsDeviceDriver,
                "authenticode_status",
                return_value="NotSigned",
            ):
                changed = runner.WindowsDeviceDriver().verify_artifact(root, "windows-release")
            self.assertNotEqual(facts.bundle_tree_sha256, changed.bundle_tree_sha256)

    def test_a_signed_windows_package_is_not_silently_accepted_as_unsigned(self) -> None:
        """If a certificate ever appears, the evidence must stop saying NotSigned.

        The digest binding is what this host can honestly claim today. It is not
        a decision to ignore Authenticode forever — the status is measured on
        every run and recorded, so the day a real signature exists the evidence
        changes by itself instead of quietly under-reporting.
        """
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "automation-tool-desktop.exe").write_bytes(b"MZ binary")

            with mock.patch.object(
                runner.WindowsDeviceDriver,
                "authenticode_status",
                return_value="Valid",
            ):
                facts = runner.WindowsDeviceDriver().verify_artifact(root, "windows-release")

        self.assertEqual(facts.code_signing, "Valid")

    def test_the_tree_digest_runs_on_this_host(self) -> None:
        """`O_NOFOLLOW` does not exist on Windows, and the digest opened with it.

        A digest binding that cannot be computed on the host it binds is not a
        binding. This is the one place the macOS implementation reaches for a
        POSIX-only flag.
        """
        runner = load_runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "file.bin").write_bytes(b"\x00\x01\x02")

            digest, total = runner.bundle_tree_digest(root)

        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(total, 3)

    def test_the_windows_snapshot_describes_real_processes(self) -> None:
        """The `ps` replacement, measured against processes that exist.

        Every ownership decision in this runner compares whole `ProcessRecord`s,
        so all four fields have to be real: a missing `started_at` makes a
        recycled pid compare equal to the process this run launched, and a
        `command` that does not begin with the image path makes
        `packaged_process_records` match nothing at all.
        """
        runner = load_runner()

        records = runner.WindowsDeviceDriver().process_snapshot()

        by_pid = {record.pid: record for record in records}
        self.assertIn(os.getpid(), by_pid)
        mine = by_pid[os.getpid()]
        self.assertIn(mine.ppid, by_pid)
        self.assertNotEqual(mine.started_at, "")
        # Not compared against `sys.executable`: a uv virtualenv launches through
        # a trampoline, so the interpreter reports the venv path while the image
        # actually running is the base interpreter. The property under test is
        # the shape — an unquoted absolute image path at the front, which is what
        # `packaged_process_records` prefix-matches on.
        self.assertFalse(
            mine.command.startswith('"'),
            f"a quoted argv[0] matches no install prefix: {mine.command!r}",
        )
        head, separator, _ = mine.command.partition(".exe")
        self.assertEqual(separator, ".exe")
        self.assertTrue(Path(head + separator).is_file(), mine.command)
        self.assertTrue(
            all(record.command for record in records),
            "a process nobody can read still needs some command, or it vanishes "
            "from every prefix match silently",
        )

    def test_arguments_survive_into_the_snapshot_command(self) -> None:
        """`--user-data-dir` is read out of this string, so it has to be here."""
        runner = load_runner()
        marker = "--eb11-snapshot-probe"
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", marker]
        )
        try:
            rendered = ""
            for _ in range(50):
                found = [
                    record
                    for record in runner.WindowsDeviceDriver().process_snapshot()
                    if record.pid == child.pid
                ]
                if found and marker in found[0].command:
                    rendered = found[0].command
                    break
                time.sleep(0.1)
        finally:
            child.terminate()
            child.wait(timeout=15)

        self.assertIn(marker, rendered)
        self.assertFalse(rendered.startswith('"'))
        self.assertLess(
            rendered.index(".exe"),
            rendered.index(marker),
            "the image path has to come first, or the prefix match reads an argument",
        )

    def test_the_launch_nonce_is_read_out_of_the_process_environment(self) -> None:
        """What keeps a reparented Chromium helper ours.

        macOS reads it with `ps eww`. On Windows the same fact lives in the
        process parameters block. Without it a helper that has been reparented
        away from the App is indistinguishable from another instance's, and the
        run would either abandon it or refuse to start.
        """
        runner = load_runner()
        nonce = "eb11-nonce-probe-7f3a"
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env=dict(os.environ, **{runner.LAUNCH_NONCE_ENVIRONMENT: nonce}),
        )
        plain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            driver = runner.WindowsDeviceDriver()
            owned = runner.ProcessRecord(pid=child.pid, ppid=os.getpid(), command="")
            other = runner.ProcessRecord(pid=plain.pid, ppid=os.getpid(), command="")
            carried = False
            for _ in range(50):
                carried = driver.process_has_launch_nonce(owned, nonce)
                if carried:
                    break
                time.sleep(0.1)

            self.assertTrue(carried)
            self.assertFalse(driver.process_has_launch_nonce(other, nonce))
            self.assertFalse(driver.process_has_launch_nonce(owned, "a-different-nonce"))
        finally:
            for process in (child, plain):
                process.terminate()
                process.wait(timeout=15)

    def test_the_packaged_prefix_is_the_install_root_not_a_bundle(self) -> None:
        """`Contents/` is a macOS bundle path. A Windows package has no such level.

        Left unchanged this matches nothing, so every packaged process reads as
        foreign — the run would see zero of its own processes and conclude the
        App never started.
        """
        runner = load_runner()
        app = Path(r"C:\Users\someone\AppData\Local\Product")
        bundle = Path("/Applications/P.app")

        prefix = runner.WindowsDeviceDriver().packaged_prefix(app)

        self.assertEqual(prefix, f"{app}{os.sep}")
        self.assertNotIn("Contents", prefix)
        self.assertEqual(
            runner.MacosDeviceDriver().packaged_prefix(bundle),
            f"{bundle}{os.sep}Contents{os.sep}",
        )

    def test_forceful_termination_names_a_signal_this_host_has(self) -> None:
        """`signal.SIGKILL` does not exist on Windows.

        `cleanup_owned_runtime` escalates SIGTERM → SIGKILL, and the escalation
        is what the launch-failure paths rely on to leave nothing behind. Naming
        `signal.SIGKILL` directly would raise `AttributeError` on this host — at
        cleanup time, inside an exception handler, which is the worst place for a
        second failure.
        """
        runner = load_runner()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            # From the snapshot, not hand-built: `ProcessRecord` compares its
            # command and start time too, and `terminate_records` skips anything
            # that is not in the current snapshot. A hand-built record is simply
            # never recognised, so a broken kill would look like a passing test.
            record = next(
                item for item in runner.process_snapshot() if item.pid == child.pid
            )

            runner.terminate_records([record], runner.FORCEFUL_SIGNAL)

            self.assertEqual(child.wait(timeout=15), runner.FORCEFUL_SIGNAL)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=15)

    def test_terminating_an_already_dead_process_is_not_an_error(self) -> None:
        """Cleanup runs after things have gone wrong, so it races with exits."""
        runner = load_runner()
        child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
        child.wait(timeout=15)
        record = runner.ProcessRecord(pid=child.pid, ppid=os.getpid(), command="")

        runner.terminate_records([record], runner.FORCEFUL_SIGNAL)

    def test_open_file_audit_tells_a_vanished_process_from_a_broken_audit(self) -> None:
        """`None` means "sample again", an exception means "stop the run".

        The Chromium processes this audits are short-lived by nature, so one
        exiting between the snapshot and the handle walk is ordinary and must
        not end the run. An audit that genuinely cannot be taken must, or the
        run would loop on a broken instrument reporting nothing was open.
        """
        runner = load_runner()
        record = runner.ProcessRecord(12, 11, r"C:\Product\browser.exe", "C")
        import windows_processes

        broken = windows_processes.WindowsProcessesUnavailable("no handle table")
        driver = runner.WindowsDeviceDriver()

        with (
            mock.patch.object(windows_processes, "open_file_paths", side_effect=broken),
            mock.patch.object(runner, "process_snapshot", return_value=[]),
        ):
            self.assertIsNone(driver.read_process_open_paths([record]))

        with (
            mock.patch.object(windows_processes, "open_file_paths", side_effect=broken),
            mock.patch.object(runner, "process_snapshot", return_value=[record]),
            self.assertRaises(runner.AcceptanceFailed),
        ):
            driver.read_process_open_paths([record])

        # And a process that exits during a *successful* walk is still a resample.
        with (
            mock.patch.object(windows_processes, "open_file_paths", return_value=[]),
            mock.patch.object(runner, "process_snapshot", return_value=[]),
        ):
            self.assertIsNone(driver.read_process_open_paths([record]))

    def test_the_packaged_manifests_are_where_this_package_puts_them(self) -> None:
        """`Contents/Resources/` is a macOS bundle path, and there is no such level here.

        The Executor build id and the browser's distribution manifest are read
        out of these two files, and both feed the release match. A Windows
        install root holds the same trees directly — the resource contract says
        so (`resourceRoot.windows` is empty) — so a hard-coded bundle path makes
        both reads fail with "packaged runtime manifest is unavailable", which
        reads like a broken package rather than a wrong path.
        """
        runner = load_runner()
        app = Path(r"C:\Users\someone\AppData\Local\Product")

        driver = runner.WindowsDeviceDriver()

        self.assertEqual(
            driver.executor_manifest(app),
            app / "local-executor/package/executor-manifest.v1.json",
        )
        self.assertEqual(
            driver.browser_manifest(app),
            app / "embedded-browser/distribution-manifest.v1.json",
        )
        bundle = Path("/Applications/P.app")
        self.assertEqual(
            runner.MacosDeviceDriver().executor_manifest(bundle),
            bundle / "Contents/Resources/local-executor/package/executor-manifest.v1.json",
        )

    def test_the_expected_release_target_names_this_host(self) -> None:
        """A package built for another target must not be accepted here.

        The map held only `macos-arm64`, so on Windows every release — including
        a correct one — failed as "does not match this Mac". The check is worth
        keeping; it just has to know two hosts.
        """
        runner = load_runner()

        self.assertEqual(
            runner.WindowsDeviceDriver().expected_release_target("amd64"),
            ("windows-x86_64", "x86_64"),
        )
        self.assertEqual(
            runner.MacosDeviceDriver().expected_release_target("arm64"),
            ("macos-arm64", "aarch64"),
        )
        self.assertIsNone(
            runner.WindowsDeviceDriver().expected_release_target("itanium")
        )
        self.assertIsNone(runner.MacosDeviceDriver().expected_release_target("amd64"))

    def test_a_private_directory_is_the_one_the_product_actually_writes(self) -> None:
        """`0o700` has a Windows counterpart, and the product already writes it.

        `st_uid` and `st_mode` are the meaningless part here: CPython reports 0
        and `0o777` for every directory on this host, so the macOS comparison is
        not merely wrong, it can never be true. The *property* is very much
        alive — `browser_profiles_windows.rs` builds a security descriptor whose
        DACL holds exactly one access-allowed ACE for the token user and sets
        `SE_DACL_PROTECTED` so nothing is inherited. Measured on the real
        Profile root, `icacls` shows one entry and no `(I)`, against four
        inherited entries on `%LOCALAPPDATA%` one directory away.

        So this asserts what the product guarantees rather than deleting a
        comparison that cannot hold.
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        private = self._private_directory()

        descriptor, identity = driver.require_private_directory(private)
        try:
            expected = os.stat(private)
            self.assertEqual(identity, (expected.st_dev, expected.st_ino))
            self.assertNotEqual(expected.st_ino, 0)
        finally:
            os.close(descriptor)

    def test_an_inherited_directory_is_refused(self) -> None:
        """A directory that anyone else can read is exactly what this catches.

        `%LOCALAPPDATA%` on this machine grants `CodexSandboxUsers` read access
        through inheritance. A Profile created without breaking inheritance
        would carry that too — the Douyin session cookies would be readable by
        every process in that group, and nothing else in the run would notice.
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        inherited = Path(tempfile.mkdtemp(prefix="eb11-inherited-"))
        self.addCleanup(shutil.rmtree, inherited, True)

        with self.assertRaises(runner.AcceptanceFailed):
            driver.require_private_directory(inherited)

    def test_a_directory_that_only_looks_private_is_refused(self) -> None:
        """One ACE for me is not enough — it has to be *mine*, not inherited.

        A child of a private parent inherits exactly one entry granting exactly
        this user full access. Count and identity both match; only the protected
        bit tells it apart from a directory the product created. Without that
        check, re-parenting a Profile under a laxer directory later would
        silently loosen it and every count-based assertion would stay green.
        """
        runner = load_runner()
        private = self._private_directory()
        inheriting = private / "child"
        inheriting.mkdir()

        with self.assertRaisesRegex(runner.AcceptanceFailed, "inherits"):
            runner.WindowsDeviceDriver().require_private_directory(inheriting)

    def test_a_file_is_not_a_private_directory(self) -> None:
        runner = load_runner()
        private = self._private_directory()
        target = private / "not-a-directory"
        target.write_text("x", encoding="utf-8")

        with self.assertRaises(runner.AcceptanceFailed):
            runner.WindowsDeviceDriver().require_private_directory(target)

    def _private_directory(self) -> Path:
        """A directory carrying the DACL the product writes for a Profile.

        Built with `icacls` rather than by hand: what is under test is whether
        the reader recognises the shape the *product* produces, and a fixture
        assembled with the same ctypes calls as the reader would only prove the
        two agree with each other.
        """
        root = Path(tempfile.mkdtemp(prefix="eb11-private-"))
        self.addCleanup(shutil.rmtree, root, True)
        directory = root / "profile"
        directory.mkdir()
        user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
        for arguments in (
            ["/inheritance:r"],
            ["/grant", f"{user}:(OI)(CI)(F)"],
            ["/setowner", user],
        ):
            completed = subprocess.run(
                ["icacls", str(directory), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return directory

    def test_the_app_identity_comes_from_the_installed_package(self) -> None:
        """`Info.plist` has no Windows counterpart, so the three facts are found elsewhere.

        The identifier is *verified*, not read: an NSIS install root carries no
        manifest naming it, but the binary has the Tauri configuration compiled
        in, and `compiled_deployment_profile_root` already proves things about
        this binary by looking for a byte sequence in it. Same technique, so a
        package built for a different product cannot pass by being silent.
        """
        runner = load_runner()
        app = INSTALLED_APP
        if not (app / "automation-tool-desktop.exe").is_file():
            self.skipTest("the installed Windows package is required")

        identity = runner.WindowsDeviceDriver().read_identity(app)

        self.assertEqual(identity.bundle_identifier, runner.APP_IDENTIFIER)
        self.assertRegex(identity.version, r"^\d+\.\d+\.\d+")
        self.assertEqual(identity.executable_path, runner.windows_product_binary(app))

    def test_a_package_that_is_not_this_product_is_refused(self) -> None:
        runner = load_runner()
        root = Path(tempfile.mkdtemp(prefix="eb11-foreign-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / "someone-elses.exe").write_bytes(b"MZ not our product")

        with self.assertRaises(runner.AcceptanceFailed):
            runner.WindowsDeviceDriver().read_identity(root)

    def test_the_app_data_root_is_where_tauri_actually_puts_it(self) -> None:
        """Measured against the running product, not guessed from the docs.

        Tauri's `app_data_dir()` is `%APPDATA%\\<identifier>` on Windows —
        Roaming, not Local — and the App really has created it there. Every
        Profile path in this run hangs off it, so a wrong root makes the whole
        Profile lifecycle assert against directories nothing writes to.
        """
        runner = load_runner()

        root = runner.WindowsDeviceDriver().app_data_root()

        self.assertEqual(root, Path(os.environ["APPDATA"]) / runner.APP_IDENTIFIER)
        self.assertTrue(
            root.is_dir(),
            "the product has run on this machine, so this directory must exist",
        )

    def test_a_running_process_is_matched_by_the_bytes_it_is_running(self) -> None:
        """The digest binding, applied to a live process instead of a package.

        macOS asks `codesign` about a pid, which answers about the code actually
        loaded. There is no such question to ask here, so what is checked is
        that the process's image path is the executable that was verified and
        that the file at that path still hashes to what was verified. Windows
        keeps the image mapped while the process runs, so it cannot be replaced
        underneath — it can be renamed, which is why the digest is compared and
        not only the path.
        """
        import windows_processes

        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None
            # The self-reported pid, not `Popen.pid`: a uv virtualenv launches
            # through a trampoline, and the image being run belongs to the
            # grandchild.
            process_id = int(child.stdout.readline().strip())
            record = next(
                item for item in runner.process_snapshot() if item.pid == process_id
            )
            image = windows_processes.image_path(process_id)
            self.assertIsNotNone(image)
            assert image is not None
            expected = driver.code_identity(Path(image))

            self.assertTrue(driver.verify_runtime_process_identity(record, expected))

            # Same path, different bytes: the digest is what has to catch this,
            # because a renamed-in executable would keep the path intact.
            forged = runner.CodeIdentity(
                identifier=expected.identifier, image_sha256="0" * 64
            )
            with self.assertRaises(runner.AcceptanceFailed):
                driver.verify_runtime_process_identity(record, forged)

            # Same bytes, different path: a second process running an identical
            # copy is still not the process that was verified.
            elsewhere = runner.CodeIdentity(
                identifier=r"C:\somewhere\else.exe",
                image_sha256=expected.image_sha256,
            )
            with self.assertRaises(runner.AcceptanceFailed):
                driver.verify_runtime_process_identity(record, elsewhere)
        finally:
            child.terminate()
            child.wait(timeout=15)

    def test_an_exited_process_is_a_resample_not_a_mismatch(self) -> None:
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        record = next(
            item for item in runner.process_snapshot() if item.pid == child.pid
        )
        child.terminate()
        child.wait(timeout=15)

        identity = driver.code_identity(Path(sys.executable))

        self.assertFalse(driver.verify_runtime_process_identity(record, identity))

    def test_a_deleted_profile_has_no_name_left(self) -> None:
        """Safe logout's disk-side proof, in NTFS terms.

        macOS unlinks an open directory and asks the descriptor for its path
        with `F_GETPATH`. NTFS answers the same question differently and very
        precisely: measured 2026-08-05, a directory deleted while this run holds
        a handle open (the handle is opened `FILE_SHARE_DELETE`, so the delete
        is allowed) disappears from its parent immediately, and
        `GetFinalPathNameByHandleW` then reports
        `\\\\?\\C:\\$Extend\\$Deleted\\…` — NTFS's holding area for
        delete-pending files. That is exactly "the inode has no name".
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        root = Path(tempfile.mkdtemp(prefix="eb11-unlink-"))
        self.addCleanup(shutil.rmtree, root, True)
        profile = root / "a3b06c48"
        profile.mkdir()
        (profile / "Cookies").write_text("x", encoding="utf-8")
        identity = (os.stat(profile).st_dev, os.stat(profile).st_ino)

        descriptor = driver.open_directory(profile)
        try:
            self.assertEqual(driver.open_directory_identity(descriptor), identity)
            self.assertIn(
                identity, driver.directory_entry_identities(driver.open_directory(root))
            )

            shutil.rmtree(profile)

            self.assertIsNone(
                driver.open_directory_identity(descriptor),
                "a deleted directory must report no surviving name",
            )
            self.assertNotIn(
                identity,
                driver.directory_entry_identities(driver.open_directory(root)),
            )
        finally:
            os.close(descriptor)

    def test_a_renamed_profile_still_counts_as_surviving(self) -> None:
        """Renaming is not deleting, and safe logout must not accept it.

        The old Profile carries the platform session. A run that treated a
        rename as removal would report the cookies gone while they sat one
        directory away under another name.
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        root = Path(tempfile.mkdtemp(prefix="eb11-rename-"))
        self.addCleanup(shutil.rmtree, root, True)
        profile = root / "a3b06c48"
        profile.mkdir()
        identity = (os.stat(profile).st_dev, os.stat(profile).st_ino)
        descriptor = driver.open_directory(profile)
        try:
            profile.rename(root / "staged-removal")

            self.assertEqual(driver.open_directory_identity(descriptor), identity)
            self.assertIn(
                identity, driver.directory_entry_identities(driver.open_directory(root))
            )
        finally:
            os.close(descriptor)

    def test_evidence_is_published_and_verified_on_this_host(self) -> None:
        """The whole publication dance, on a host that has no `dir_fd` at all.

        Five of the evidence tests are marked "POSIX evidence publication
        contract" and skip here, so before this the Windows path was not
        exercised anywhere: `os.stat(..., dir_fd=)`, `os.link(..., src_dir_fd=)`,
        `os.fchmod` and `os.fsync` on a directory are each unavailable or
        meaningless, and the first of them would have raised at the very end of
        a run that had already driven a real login.
        """
        runner = load_runner()
        directory = Path(tempfile.mkdtemp(prefix="eb11-evidence-"))
        self.addCleanup(shutil.rmtree, directory, True)
        evidence = directory / "eb-11.json"

        target = runner.open_evidence_target(evidence)
        self.addCleanup(target.close)
        identity = runner.write_evidence(target, {"schema": "probe", "值": "中文"})
        publication = runner.EvidencePublication(target=target, identity=identity)
        publication.commit()
        publication.finish_report()

        self.assertTrue(evidence.is_file())
        document = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(document["值"], "中文")
        self.assertEqual(
            (evidence.stat().st_dev, evidence.stat().st_ino),
            identity,
            "the published file must be the one that was written, not a copy",
        )
        self.assertEqual(
            [item.name for item in directory.iterdir()],
            [evidence.name],
            "the temporary file must not survive publication",
        )

    def test_evidence_refuses_to_overwrite_on_this_host(self) -> None:
        runner = load_runner()
        directory = Path(tempfile.mkdtemp(prefix="eb11-evidence-clash-"))
        self.addCleanup(shutil.rmtree, directory, True)
        evidence = directory / "eb-11.json"
        evidence.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(runner.AcceptanceFailed, "overwrite"):
            runner.open_evidence_target(evidence)

    def test_the_boundary_accepts_an_install_root_and_still_refuses_the_rest(
        self,
    ) -> None:
        """The gate opens last, and only for the shape this host actually has.

        `.app` is a macOS bundle suffix and `os.geteuid` does not exist here, so
        both had to move behind the driver before this could stop refusing every
        Windows host outright. Everything else the boundary asserts — an
        interactive console, an absolute path, no reparse points, evidence that
        cannot overwrite and sits outside the package — is platform-neutral and
        stays exactly as it was.
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()

        driver.require_app_path(INSTALLED_APP)
        with self.assertRaisesRegex(runner.AcceptanceFailed, "absolute"):
            driver.require_app_path(Path("relative/path"))
        with self.assertRaises(runner.AcceptanceFailed):
            driver.require_app_path(INSTALLED_APP / "does-not-exist")
        self.assertFalse(driver.running_as_administrator() is None)

    def test_the_daily_browser_profiles_it_must_not_touch_are_this_host_s(self) -> None:
        """"Do not touch the user's own Chrome" needs this host's Chrome.

        The list held `~/Library/Application Support/Google/Chrome`. On Windows
        nothing is ever inside that path, so the check could never fire — and it
        is the check that catches the packaged browser opening the operator's
        real profile, cookies and all. `CLAUDE.md` §5 forbids exactly that, and
        an assertion that cannot fail forbids nothing.
        """
        runner = load_runner()

        roots = runner.WindowsDeviceDriver().daily_browser_profile_roots()

        local = Path(os.environ["LOCALAPPDATA"])
        self.assertIn(local / "Google/Chrome/User Data", roots)
        self.assertIn(local / "Microsoft/Edge/User Data", roots)
        for root in roots:
            self.assertTrue(root.is_absolute())
            self.assertNotIn("Library", root.parts)

    def test_the_running_app_is_checked_against_the_release_without_codesign(
        self,
    ) -> None:
        """The one instrument the port missed, found by running the thing.

        `verify_running_release_process` still shelled out to `codesign` on a
        live pid. Every other macOS instrument had moved behind the driver, and
        a grep for the helper *names* missed this one because it invokes the
        tool directly. It failed as `[WinError 2] 系统找不到指定的文件` —
        accurate and useless — after the run had already verified the artifact
        and launched the App.
        """
        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            import windows_processes

            assert child.stdout is not None
            process_id = int(child.stdout.readline().strip())
            record = next(
                item for item in runner.process_snapshot() if item.pid == process_id
            )
            image = windows_processes.image_path(process_id)
            assert image is not None
            release = runner.VerifiedRelease(
                app_identity=runner.AppIdentity(
                    bundle_identifier=runner.APP_IDENTIFIER,
                    version="0.1.0.0",
                    executable_path=Path(image),
                ),
                runtime_contract=runner.RuntimeContract(
                    app_path=Path(image).parent,
                    executor_path=Path(image),
                    browser_path=Path(image),
                    profile_root=Path(image).parent,
                ),
                artifact=runner.ArtifactFacts(
                    bundle_tree_sha256="a" * 64,
                    bundle_bytes=1,
                    executor_build_id="customer-demo-xuanbai",
                ),
                release_identity=runner.SignedReleaseIdentity(
                    source_git_commit="c" * 40,
                    source_tree_sha256="d" * 64,
                    executor_build_id="customer-demo-xuanbai",
                    target="windows-x86_64",
                    architecture="x86_64",
                    deployment_profile_id="demo-xuanbai",
                ),
                profile_root=Path(image).parent,
            )

            driver.require_running_release(record, release)

            # A process running something else is refused, or this proves nothing.
            elsewhere = runner.VerifiedRelease(
                app_identity=runner.AppIdentity(
                    bundle_identifier=runner.APP_IDENTIFIER,
                    version="0.1.0.0",
                    executable_path=Path(r"C:\somewhere\else.exe"),
                ),
                runtime_contract=release.runtime_contract,
                artifact=release.artifact,
                release_identity=release.release_identity,
                profile_root=release.profile_root,
            )
            with self.assertRaises(runner.AcceptanceFailed):
                driver.require_running_release(record, elsewhere)
        finally:
            child.terminate()
            child.wait(timeout=15)

    def test_a_vanished_window_is_reported_as_the_app_disappearing(self) -> None:
        runner = load_runner()

        with (
            mock.patch.object(runner, "power_shell", return_value="no_window"),
            self.assertRaisesRegex(runner.AcceptanceFailed, "disappeared"),
        ):
            runner.WindowsDeviceDriver().visible_ui_text(42)


INSTALLED_APP = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "自动化运营工具"
    if os.name == "nt"
    else Path("/nonexistent")
)


@unittest.skipUnless(
    os.name == "nt" and (INSTALLED_APP / "automation-tool-desktop.exe").is_file(),
    "the installed Windows package is required",
)
class WindowsAppLifecycleTests(unittest.TestCase):
    """Start it, find it, ask it to quit, and prove nothing of ours is left.

    Against the real installed package, because every one of these steps is a
    claim about the operating system rather than about this code: that the
    environment reaches the process, that a foreign instance would be visible,
    that a window close request is honoured rather than the process being shot.

    `quit_app` is the reason the last one matters. EB-11's final assertion is
    that a *normal* exit leaves no Executor and no Chromium behind; killing the
    process would prove nothing about that, so the Windows path has to close the
    window the way a person does.
    """

    def test_the_app_starts_carrying_what_the_runner_put_in_its_environment(
        self,
    ) -> None:
        import windows_processes

        runner = load_runner()
        driver = runner.WindowsDeviceDriver()
        nonce = "eb11-lifecycle-probe-2c8d"
        driver.start_app(INSTALLED_APP, nonce)
        started: set[int] = set()
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                started = driver.bundle_process_ids(INSTALLED_APP)
                if started:
                    break
                time.sleep(0.5)

            self.assertEqual(len(started), 1, started)
            process_id = next(iter(started))
            values = windows_processes.environment(process_id)
            self.assertIsNotNone(values)
            assert values is not None
            self.assertEqual(values.get(runner.LAUNCH_NONCE_ENVIRONMENT), nonce)
            self.assertEqual(
                values.get(runner.WEBVIEW_ACCESSIBILITY_ENVIRONMENT),
                runner.WEBVIEW_ACCESSIBILITY_ARGUMENT,
                "without this the WebView2 tree is empty and nothing can be driven",
            )

            driver.wait_for_window(process_id)
            driver.request_quit(process_id)

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if not driver.bundle_process_ids(INSTALLED_APP):
                    break
                time.sleep(0.5)
            self.assertEqual(
                driver.bundle_process_ids(INSTALLED_APP),
                set(),
                "a normal quit has to end the process, or EB-11's final "
                "assertion measures a kill instead of an exit",
            )
            started = set()
        finally:
            for process_id in started:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/F"], capture_output=True
                )


if __name__ == "__main__":
    unittest.main()
