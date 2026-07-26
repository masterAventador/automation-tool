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
OVERLAY_PATH = ROOT / "contracts/quality/motion-asset-overlay.v1.json"
OFFLINE_DEPENDENCIES_PATH = ROOT / "contracts/video/offline-motion-dependencies.v1.json"
UTM_FONT_PATH = ROOT / "vendor/moneyprinterturbo/resource/fonts/UTM Kabel KT.ttf"
BIG_SHOULDERS_FONT_PATH = (
    ROOT / "assets/motion-catalog-overlay/fonts/big-shoulders-display-latin.woff2"
)
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

    def test_big_shoulders_registration_matches_the_shipped_woff2_and_ofl_lock(
        self,
    ) -> None:
        policy = _load(POLICY_PATH)
        entries = _entries(policy)
        self.assertIn("font-big-shoulders-display", entries)
        entry = entries["font-big-shoulders-display"]
        overlay = _load(OVERLAY_PATH)
        offline = _load(OFFLINE_DEPENDENCIES_PATH)

        overlay_assets = overlay.get("assets")
        self.assertIsInstance(overlay_assets, list)
        overlay_entry = next(
            asset
            for asset in overlay_assets
            if isinstance(asset, dict)
            and asset.get("id") == "font-big-shoulders-display"
        )
        font_families = offline.get("fontFamilies")
        self.assertIsInstance(font_families, list)
        family = next(
            font
            for font in font_families
            if isinstance(font, dict) and font.get("family") == "Big Shoulders Display"
        )

        self.assertEqual(entry["category"], "font")
        self.assertEqual(entry["sha256"], _sha256(BIG_SHOULDERS_FONT_PATH))
        self.assertEqual(entry["bytes"], BIG_SHOULDERS_FONT_PATH.stat().st_size)
        for field in ("sourceUrl", "sha256", "bytes"):
            self.assertEqual(entry[field], overlay_entry[field])
        self.assertEqual(entry["license"], "OFL-1.1")
        self.assertEqual(entry["licenseTextSha256"], family["licenseFileSha256"])
        for permission in (
            "redistributionAllowed",
            "commercialUseAllowed",
            "embeddingAllowed",
        ):
            self.assertIs(entry[permission], True)
        self.assertEqual(
            entry["attribution"],
            "Copyright 2019 The Big Shoulders Project Authors "
            "(https://github.com/xotypeco/big_shoulders)",
        )
        evidence_urls = {
            item.get("url")
            for item in entry["rightsEvidence"]
            if isinstance(item, dict)
        }
        self.assertTrue(
            any(
                isinstance(url, str)
                and url.startswith("https://github.com/google/fonts/blob/")
                and url.endswith("/ofl/bigshouldersdisplay/OFL.txt")
                for url in evidence_urls
            )
        )
        self.assertIn(
            "https://openfontlicense.org/open-font-license-official-text/",
            evidence_urls,
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
