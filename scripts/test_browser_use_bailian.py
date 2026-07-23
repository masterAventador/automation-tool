#!/usr/bin/env python3
"""BU-06 deterministic tests for the locked Bailian model catalog and gateway.

Runs inside the locked browser-use-contract environment (real ChatOpenAI, no
network): the catalog is a closed capability snapshot; a model that cannot see
screenshots can never be selected for a vision run; a real ChatOpenAI is
built only against the locked base URL and model id; the api key never
appears in repr.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/browser-use-contract"))

from browser_use_bailian import (  # noqa: E402
    BailianModelRejected,
    BailianModelSnapshot,
    build_bailian_chat_model,
    load_bailian_model_catalog,
    redacted_model_descriptor,
    select_bailian_model,
)

CATALOG_PATH = ROOT / "contracts/browser-use/bailian-model-catalog.v1.json"
API_KEY = "sk-ws-T.SYNTHETIC0.9Zz9.MEUCIQTestOnlyTestOnlyTestOnlyTestOnly-42"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_bailian_model_catalog(CATALOG_PATH)

    def test_catalog_locks_the_three_verified_models(self) -> None:
        ids = {model.model_id for model in self.catalog.models}
        self.assertEqual(
            ids, {"qwen3.7-max-2026-06-08", "glm-5.2", "deepseek-v4-pro"}
        )
        self.assertEqual(self.catalog.base_url, BASE_URL)
        self.assertEqual(self.catalog.vision_default_model_id, "qwen3.7-max-2026-06-08")

    def test_capability_flags_match_the_verified_reality(self) -> None:
        by_id = {model.model_id: model for model in self.catalog.models}
        qwen = by_id["qwen3.7-max-2026-06-08"]
        self.assertTrue(qwen.vision and qwen.text and qwen.function_calling)
        self.assertFalse(qwen.dom_only_acceptance_required)
        for text_only in ("glm-5.2", "deepseek-v4-pro"):
            self.assertFalse(by_id[text_only].vision)
            self.assertTrue(by_id[text_only].dom_only_acceptance_required)
        self.assertFalse(by_id["deepseek-v4-pro"].function_calling)

    def test_unknown_model_id_is_rejected(self) -> None:
        with self.assertRaises(BailianModelRejected):
            select_bailian_model(self.catalog, model_id="gpt-4o", requires_vision=True)


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_bailian_model_catalog(CATALOG_PATH)

    def test_vision_run_only_admits_a_multimodal_model(self) -> None:
        snapshot = select_bailian_model(
            self.catalog, model_id="qwen3.7-max-2026-06-08", requires_vision=True
        )
        self.assertIsInstance(snapshot, BailianModelSnapshot)
        self.assertTrue(snapshot.vision)

    def test_text_model_for_a_vision_run_is_blocked_not_degraded(self) -> None:
        for text_only in ("glm-5.2", "deepseek-v4-pro"):
            with self.assertRaises(BailianModelRejected):
                select_bailian_model(
                    self.catalog, model_id=text_only, requires_vision=True
                )

    def test_dom_only_models_require_the_dom_only_acceptance_flag(self) -> None:
        with self.assertRaises(BailianModelRejected):
            select_bailian_model(
                self.catalog,
                model_id="deepseek-v4-pro",
                requires_vision=False,
            )
        snapshot = select_bailian_model(
            self.catalog,
            model_id="deepseek-v4-pro",
            requires_vision=False,
            dom_only_accepted=True,
        )
        self.assertEqual(snapshot.model_id, "deepseek-v4-pro")

    def test_multimodal_model_also_serves_dom_only_runs(self) -> None:
        snapshot = select_bailian_model(
            self.catalog, model_id="qwen3.7-max-2026-06-08", requires_vision=False
        )
        self.assertEqual(snapshot.model_id, "qwen3.7-max-2026-06-08")


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_bailian_model_catalog(CATALOG_PATH)

    def test_builds_locked_chat_openai_bound_to_the_snapshot(self) -> None:
        from browser_use.llm import ChatOpenAI

        snapshot = select_bailian_model(
            self.catalog, model_id="qwen3.7-max-2026-06-08", requires_vision=True
        )
        model = build_bailian_chat_model(
            catalog=self.catalog, snapshot=snapshot, api_key=API_KEY
        )
        self.assertIsInstance(model, ChatOpenAI)
        self.assertEqual(model.model, "qwen3.7-max-2026-06-08")
        self.assertEqual(model.base_url, BASE_URL)

    def test_redacted_descriptor_is_the_only_key_free_representation(self) -> None:
        # The upstream ChatOpenAI repr leaks the api key; production code logs
        # only the redacted descriptor, which never carries the key.
        snapshot = select_bailian_model(
            self.catalog, model_id="qwen3.7-max-2026-06-08", requires_vision=True
        )
        descriptor = redacted_model_descriptor(self.catalog, snapshot)
        self.assertEqual(descriptor["model_id"], "qwen3.7-max-2026-06-08")
        self.assertEqual(descriptor["base_url"], BASE_URL)
        self.assertNotIn(API_KEY, json.dumps(descriptor))

    def test_invalid_api_key_shape_is_rejected(self) -> None:
        snapshot = select_bailian_model(
            self.catalog, model_id="glm-5.2", requires_vision=False, dom_only_accepted=True
        )
        for invalid in ("", "not-a-key", "sk-short"):
            with self.assertRaises(BailianModelRejected):
                build_bailian_chat_model(
                    catalog=self.catalog, snapshot=snapshot, api_key=invalid
                )

    def test_no_general_menu_only_catalog_snapshots_are_buildable(self) -> None:
        with self.assertRaises(BailianModelRejected):
            build_bailian_chat_model(
                catalog=self.catalog,
                snapshot="qwen3.7-max-2026-06-08",  # type: ignore[arg-type]
                api_key=API_KEY,
            )


if __name__ == "__main__":
    unittest.main()
