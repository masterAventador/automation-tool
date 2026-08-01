#!/usr/bin/env python3
"""Focused diagnostics tests for the shared acceptance PostgreSQL lifecycle."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acceptance_postgres


class NativePostgresDiagnosticTest(unittest.TestCase):
    def test_complete_native_windows_toolchain_keeps_the_native_backend(self) -> None:
        native_calls: list[dict[str, object]] = []

        @contextmanager
        def native(**kwargs: object) -> Iterator[None]:
            native_calls.append(kwargs)
            yield

        with (
            mock.patch.object(
                acceptance_postgres.platform, "system", return_value="Windows"
            ),
            mock.patch.object(
                acceptance_postgres.shutil,
                "which",
                side_effect=lambda name: f"C:/PostgreSQL/{name}.exe",
            ),
            mock.patch.object(
                acceptance_postgres, "_native_windows_postgres", side_effect=native
            ),
            mock.patch.object(acceptance_postgres.subprocess, "run") as run,
            acceptance_postgres.managed_test_postgres(
                compose=["docker", "compose"],
                database_port=54321,
                environment={"PATH": "trusted"},
                repository_root=ROOT,
            ),
        ):
            pass

        self.assertEqual(
            native_calls,
            [{"database_port": 54321, "environment": {"PATH": "trusted"}}],
        )
        run.assert_not_called()

    def test_partial_native_windows_toolchain_fails_closed(self) -> None:
        discovered = {"initdb": "C:/PostgreSQL/initdb.exe"}
        with (
            mock.patch.object(
                acceptance_postgres.platform, "system", return_value="Windows"
            ),
            mock.patch.object(
                acceptance_postgres.shutil,
                "which",
                side_effect=lambda name: discovered.get(name),
            ),
            self.assertRaisesRegex(RuntimeError, "partially installed"),
            acceptance_postgres.managed_test_postgres(
                compose=["docker", "compose"],
                database_port=54321,
                environment={"PATH": "trusted"},
                repository_root=ROOT,
            ),
        ):
            pass

    def test_windows_without_native_tools_uses_isolated_docker_compose(self) -> None:
        commands: list[list[str]] = []
        docker_configs: list[Path] = []
        original_environment = {
            "PATH": "trusted",
            "DOCKER_CONFIG": "C:/private-docker-config",
        }

        def run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            environment = kwargs["env"]
            self.assertIsInstance(environment, dict)
            docker_config = Path(environment["DOCKER_CONFIG"])  # type: ignore[index]
            self.assertEqual(
                (docker_config / "config.json").read_text(encoding="utf-8"),
                "{}\n",
            )
            docker_configs.append(docker_config)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch.object(
                acceptance_postgres.platform, "system", return_value="Windows"
            ),
            mock.patch.object(acceptance_postgres.shutil, "which", return_value=None),
            mock.patch.object(acceptance_postgres.subprocess, "run", side_effect=run),
            acceptance_postgres.managed_test_postgres(
                compose=["docker", "compose", "--project-name", "isolated"],
                database_port=54321,
                environment=original_environment,
                repository_root=ROOT,
            ),
        ):
            pass

        self.assertEqual(
            commands,
            [
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "isolated",
                    "up",
                    "--detach",
                    "--wait",
                    "postgres-test",
                ],
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "isolated",
                    "down",
                    "--volumes",
                    "--remove-orphans",
                ],
            ],
        )
        self.assertEqual(len(docker_configs), 2)
        self.assertEqual(docker_configs[0], docker_configs[1])
        self.assertFalse(docker_configs[0].exists())
        self.assertEqual(
            original_environment,
            {"PATH": "trusted", "DOCKER_CONFIG": "C:/private-docker-config"},
        )

    def test_parent_owned_windows_root_uses_native_inherited_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            environment = {
                acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT: str(root)
            }
            with (
                mock.patch.object(Path, "mkdir") as mkdir,
                acceptance_postgres._windows_postgres_root(environment) as yielded,
            ):
                self.assertEqual(yielded, root)

        mkdir.assert_called_once_with()

    def test_checked_command_reports_the_captured_postgres_error(self) -> None:
        failure = subprocess.CalledProcessError(
            1,
            ["initdb", "--pgdata", "isolated"],
            output="",
            stderr="fixed initdb reason",
        )
        with (
            mock.patch.object(
                acceptance_postgres.subprocess,
                "run",
                side_effect=failure,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "initdb failed: fixed initdb reason",
            ),
        ):
            acceptance_postgres._run_captured_postgres_command(
                ["initdb", "--pgdata", "isolated"],
                environment={"PATH": "trusted"},
            )


if __name__ == "__main__":
    unittest.main()
