from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_third_party_sources  # noqa: E402

POLICY_PATH = ROOT / "contracts/quality/asset-rights-policy.v1.json"
UTM_FONT_PATH = ROOT / "vendor/moneyprinterturbo/resource/fonts/UTM Kabel KT.ttf"
UTM_ATTRIBUTION = (
    'Thiet ke boi Michael Dinh Kien - "In God We Trust - Free for everyone" '
    "Email: fontchudep@gmail.com; www.fontchudep.com; www.fontchudep.vn"
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain an object")
    return value


def _entries(policy: dict[str, object]) -> dict[str, dict[str, object]]:
    values = policy.get("entries")
    if not isinstance(values, list):
        raise AssertionError("asset rights policy has no entries")
    indexed = {
        entry.get("id"): entry
        for entry in values
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if len(indexed) != len(values):
        raise AssertionError("asset rights entries must be objects with unique ids")
    return indexed


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FontRightsPolicyTests(unittest.TestCase):
    def _assert_gate_rejects(self, policy: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "asset-rights-policy.v1.json"
            candidate.write_text(
                json.dumps(policy, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.object(check_third_party_sources, "RIGHTS_PATH", candidate),
                self.assertRaises(SystemExit),
            ):
                check_third_party_sources.validate_asset_rights()

    def test_utm_kabel_is_bound_to_the_exact_binary_and_denied_as_undetermined(
        self,
    ) -> None:
        policy = _load(POLICY_PATH)
        entries = _entries(policy)
        self.assertIn("font-utm-kabel-kt", entries)
        entry = entries["font-utm-kabel-kt"]

        self.assertEqual(entry["category"], "font")
        self.assertEqual(entry["sha256"], _sha256(UTM_FONT_PATH))
        self.assertEqual(entry["bytes"], UTM_FONT_PATH.stat().st_size)
        self.assertEqual(entry["attribution"], UTM_ATTRIBUTION)
        self.assertEqual(entry["license"], "NOASSERTION")
        self.assertEqual(entry["rightsStatus"], "undetermined")
        self.assertEqual(entry["distributionDecision"], "deny")
        for permission in (
            "redistributionAllowed",
            "commercialUseAllowed",
            "embeddingAllowed",
        ):
            self.assertIs(entry[permission], False)
        self.assertIn("no explicit", str(entry["rightsBlocker"]).lower())
        evidence = entry["rightsEvidence"]
        self.assertIsInstance(evidence, list)
        self.assertTrue(
            any(
                isinstance(item, dict)
                and item.get("type") == "font-name-table"
                and item.get("value") == UTM_ATTRIBUTION
                for item in evidence
            ),
            "the decision must preserve the exact first-party notice in the binary",
        )

    def test_gate_rejects_a_duplicate_registered_asset_id(self) -> None:
        policy = _load(POLICY_PATH)
        entries = policy["entries"]
        self.assertIsInstance(entries, list)
        duplicate = copy.deepcopy(_entries(policy)["font-utm-kabel-kt"])
        entries.append(duplicate)
        self._assert_gate_rejects(policy)

    def test_gate_rejects_an_undetermined_font_changed_to_allow(self) -> None:
        policy = _load(POLICY_PATH)
        entry = _entries(policy)["font-utm-kabel-kt"]
        entry["redistributionAllowed"] = True
        self._assert_gate_rejects(policy)


if __name__ == "__main__":
    unittest.main()
