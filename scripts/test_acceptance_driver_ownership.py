#!/usr/bin/env python3
"""Tests for the derived acceptance-driver ownership gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from check_acceptance_driver_ownership import audit_repository, conclusions

SCRIPT_ROOT = Path(__file__).resolve().parent


class AcceptanceDriverOwnershipTests(unittest.TestCase):
    def make_repository(
        self,
        *,
        package_scripts: dict[str, str] | None = None,
        registry: dict[str, object] | None = None,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="acceptance-owner-test-")
        root = Path(temporary.name)
        (root / "scripts").mkdir()
        (root / "frontend").mkdir()
        (root / "frontend/package.json").write_text(
            json.dumps({"scripts": package_scripts or {}}),
            encoding="utf-8",
        )
        (root / "scripts/acceptance_driver_ownership.v1.json").write_text(
            json.dumps(
                registry
                or {
                    "version": "acceptance-driver-ownership.v1",
                    "pythonSubprocessOwners": {},
                    "blockedProfiles": {},
                }
            ),
            encoding="utf-8",
        )
        return temporary, root

    def test_source_reader_does_not_hide_a_driver_without_execution_ownership(
        self,
    ) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        driver = root / "scripts/run_demo_acceptance.py"
        driver.write_text("def main():\\n    return 0\\n", encoding="utf-8")
        (root / "scripts/test_demo_contract.py").write_text(
            (
                "from pathlib import Path\\n"
                "SOURCE = Path('scripts/run_demo_acceptance.py').read_text()\\n"
                "assert 'main' in SOURCE\\n"
            ),
            encoding="utf-8",
        )

        errors = audit_repository(root)

        self.assertIn(
            "run_demo_acceptance.py has no execution owner or explicit blocker",
            errors,
        )

    def test_package_script_is_a_real_execution_owner(self) -> None:
        temporary, root = self.make_repository(
            package_scripts={
                "test:demo": "../backend/.venv/bin/python ../scripts/run_demo_acceptance.py"
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

        errors: list[str] = []
        inventory = conclusions(root, errors)

        self.assertEqual(errors, [])
        self.assertEqual(
            inventory[0].execution_owners,
            ("frontend/package.json#test:demo",),
        )

    def test_package_script_that_only_prints_a_driver_name_is_not_an_owner(
        self,
    ) -> None:
        temporary, root = self.make_repository(
            package_scripts={
                "test:demo": "node -e \"console.log('run_demo_acceptance.py')\""
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

        errors = audit_repository(root)

        self.assertIn(
            "run_demo_acceptance.py has no execution owner or explicit blocker",
            errors,
        )

    def test_explicit_blocker_requires_conditions_and_resources(self) -> None:
        temporary, root = self.make_repository(
            registry={
                "version": "acceptance-driver-ownership.v1",
                "blockedProfiles": {
                    "device": {
                        "reason": "requires a physical signed-device run",
                        "conditions": [],
                        "resources": [],
                        "drivers": ["run_demo_acceptance.py"],
                    }
                },
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

        errors = audit_repository(root)

        self.assertIn("blocker profile device has no explicit conditions", errors)
        self.assertIn("blocker profile device has no explicit resources", errors)

    def test_source_only_reference_is_reported_but_never_becomes_an_owner(self) -> None:
        temporary, root = self.make_repository(
            registry={
                "version": "acceptance-driver-ownership.v1",
                "blockedProfiles": {
                    "manual": {
                        "reason": "deliberate manual entrypoint",
                        "conditions": ["operator explicitly selects the driver"],
                        "resources": ["project runtime"],
                        "drivers": ["run_demo_acceptance.py"],
                    }
                },
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (root / "scripts/test_reader.py").write_text(
            "Path('scripts/run_demo_acceptance.py').read_text()\n",
            encoding="utf-8",
        )

        errors: list[str] = []
        inventory = conclusions(root, errors)

        self.assertEqual(errors, [])
        self.assertEqual(inventory[0].execution_owners, ())
        self.assertEqual(
            inventory[0].source_contract_readers,
            ("source-shape-only:scripts/test_reader.py",),
        )
        self.assertEqual(inventory[0].non_executing_references, ())

    def test_plain_text_reference_is_labeled_without_claiming_source_reading(
        self,
    ) -> None:
        temporary, root = self.make_repository(
            registry={
                "version": "acceptance-driver-ownership.v1",
                "blockedProfiles": {
                    "manual": {
                        "reason": "deliberate manual entrypoint",
                        "conditions": ["operator explicitly selects the driver"],
                        "resources": ["project runtime"],
                        "drivers": ["run_demo_acceptance.py"],
                    }
                },
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (root / "scripts/helper.py").write_text(
            'raise SystemExit("Use scripts/run_demo_acceptance.py")\n',
            encoding="utf-8",
        )

        errors: list[str] = []
        inventory = conclusions(root, errors)

        self.assertEqual(errors, [])
        self.assertEqual(inventory[0].source_contract_readers, ())
        self.assertEqual(
            inventory[0].non_executing_references,
            ("non-executing-reference:scripts/helper.py",),
        )

    def test_marker_checked_inside_another_runner_is_not_this_driver_source(
        self,
    ) -> None:
        temporary, root = self.make_repository(
            registry={
                "version": "acceptance-driver-ownership.v1",
                "blockedProfiles": {
                    "manual": {
                        "reason": "deliberate manual entrypoint",
                        "conditions": ["operator explicitly selects the driver"],
                        "resources": ["project runtime"],
                        "drivers": ["run_demo_acceptance.py"],
                    }
                },
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (root / "frontend/tests").mkdir()
        (root / "frontend/tests/marker.test.mjs").write_text(
            (
                'const parent = await read("scripts/run_parent_acceptance.py");\n'
                'for (const marker of ["run_demo_acceptance.py"]) {\n'
                "  assert.ok(parent.includes(marker));\n"
                "}\n"
            ),
            encoding="utf-8",
        )

        errors: list[str] = []
        inventory = conclusions(root, errors)

        self.assertEqual(errors, [])
        self.assertEqual(inventory[0].source_contract_readers, ())
        self.assertEqual(
            inventory[0].non_executing_references,
            ("non-executing-reference:frontend/tests/marker.test.mjs",),
        )

    def test_python_subprocess_call_is_derived_without_a_copied_owner_list(
        self,
    ) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (root / "scripts/run_parent_acceptance.py").write_text(
            (
                "import subprocess\n"
                "subprocess.run(['python', 'scripts/run_demo_acceptance.py'], check=True)\n"
            ),
            encoding="utf-8",
        )
        registry = {
            "version": "acceptance-driver-ownership.v1",
            "blockedProfiles": {
                "manual": {
                    "reason": "parent itself remains deliberate",
                    "conditions": ["operator selects parent"],
                    "resources": ["project runtime"],
                    "drivers": ["run_parent_acceptance.py"],
                }
            },
        }
        (root / "scripts/acceptance_driver_ownership.v1.json").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

        errors: list[str] = []
        inventory = {item.driver: item for item in conclusions(root, errors)}

        self.assertEqual(errors, [])
        self.assertEqual(
            inventory["run_demo_acceptance.py"].execution_owners,
            ("python-subprocess:scripts/run_parent_acceptance.py",),
        )

    def test_driver_name_in_a_non_command_wrapper_argument_is_not_execution(
        self,
    ) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        (root / "scripts/label_only.py").write_text(
            (
                "import subprocess\n"
                "def run(command, *, label):\n"
                "    subprocess.run(command, check=True)\n"
                "run(['echo', 'ok'], label='run_demo_acceptance.py')\n"
            ),
            encoding="utf-8",
        )

        errors = audit_repository(root)

        self.assertIn(
            "run_demo_acceptance.py has no execution owner or explicit blocker",
            errors,
        )

    def test_unknown_and_duplicate_blocker_entries_fail_closed(self) -> None:
        temporary, root = self.make_repository(
            registry={
                "version": "acceptance-driver-ownership.v1",
                "blockedProfiles": {
                    "first": {
                        "reason": "first",
                        "conditions": ["condition"],
                        "resources": ["resource"],
                        "drivers": ["run_demo_acceptance.py"],
                    },
                    "second": {
                        "reason": "second",
                        "conditions": ["condition"],
                        "resources": ["resource"],
                        "drivers": [
                            "run_demo_acceptance.py",
                            "run_stale_acceptance.py",
                        ],
                    },
                },
            }
        )
        self.addCleanup(temporary.cleanup)
        (root / "scripts/run_demo_acceptance.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

        errors = audit_repository(root)

        self.assertTrue(
            any("duplicated in blocker profiles" in error for error in errors)
        )
        self.assertTrue(any("unknown driver" in error for error in errors))

    def test_repository_script_aggregate_discovers_this_gate(self) -> None:
        repository_root = SCRIPT_ROOT.parent
        from run_script_tests import discover

        discovered = {path.resolve() for path in discover(repository_root)}

        self.assertIn(Path(__file__).resolve(), discovered)


if __name__ == "__main__":
    unittest.main()
