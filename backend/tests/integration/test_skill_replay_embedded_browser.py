"""SA-01..07 first real-browser acceptance of the self-healing chain.

Until now every SA stage was proven against ``FakePage``/golden fixtures only —
the deterministic replayer had never touched a real DOM. This test drives the
whole chain on the digest-verified staged embedded Chromium against a self-built
portal-complexity fixture site (thousands of nodes, decoy anchors, hidden
duplicates), served on the platform origin through route interception exactly
like the other embedded-browser integration tests: this machine has no
controlled Douyin account, so the platform pages themselves are the only
substitution, and the real-account acceptance item stays pending in SA-07.

The chain under test, every stage the production one:

1. a real action sequence performed on the live page becomes the raw trajectory;
2. SA-02 ``clean_trajectory`` distils it into a candidate;
3. SA-04 ``replay_skill`` replays the candidate **on the real page** through the
   real ``PlaywrightReplayPage`` adapter — the replay-sandbox gate;
4. SA-03 ``sign_candidate`` + ``SkillRegistry.publish`` make it immutable v1;
5. a page revision (renamed publish button) makes replay fail **before** the
   external step → SA-05 hands back to Browser Use from the checkpoint;
6. the recovery flow on the revised page yields candidate v2 with
   ``parentVersion: 1`` — replayed for real, signed, published;
7. SA-06 routes each page fingerprint to its version, drift routes to none;
8. SA-07's management view shows the version tree over the real registry;
9. a failure **after** the external click (success page broken) reports
   ``dispatched=True`` → SA-05 refuses continue/resend, reconcile only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
)
from automation_tool.executor.skill_handback import decide_handback
from automation_tool.executor.skill_management import build_management_view
from automation_tool.executor.skill_registry import SkillRegistry, sign_candidate
from automation_tool.executor.skill_replay_page import PlaywrightReplayPage
from automation_tool.executor.skill_replayer import ReplayFailed, replay_skill
from automation_tool.executor.skill_router import PageContext, VersionStats
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND_ROOT / "tests/fixtures/skill_portal_pages"

DOMAIN = "www.douyin.com"
BASE_PATH = "/automation-tool-sa-portal"
STUDIO_PATH = f"{BASE_PATH}/studio"
DONE_PATH = f"{BASE_PATH}/studio/done"
ENTRY_URL = f"https://{DOMAIN}{BASE_PATH}"

SIGNING_SEED = bytes(range(32))
APPROVAL = {
    "reviewer": "aventador",
    "decision": "approved",
    "reviewedAt": "2026-08-06T00:00:00+00:00",
}
TITLE_PARAMETER = "验收标题真实回放一二三"


def _serve_portal(page: Any, state: dict[str, str]) -> None:
    """Serve the fixture site on the platform origin; variant per path via state."""

    files = {
        BASE_PATH: "home",
        STUDIO_PATH: "studio",
        DONE_PATH: "done",
    }

    def handler(route: Any) -> None:
        path = urlsplit(route.request.url).path
        key = files.get(path)
        if key is None:
            route.fulfill(status=404, content_type="text/plain", body="not found")
            return
        body = (FIXTURES / state[key]).read_text(encoding="utf-8")
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    page.route(f"https://{DOMAIN}{BASE_PATH}**", handler)


def _record_real_trajectory(page: Any, *, publish_button: str) -> dict[str, object]:
    """Perform the flow for real on the live page and record what happened.

    This plays the Browser Use role: every action below actually runs against
    the real DOM, and the recorded URLs/coordinates are read back from the page,
    not typed in.
    """
    page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
    entry_url = str(page.url)
    viewport = page.viewport_size
    assert viewport is not None

    page.get_by_role("link", name="创作中心", exact=True).click(timeout=10_000)
    page.wait_for_url(f"**{STUDIO_PATH}", timeout=10_000)
    studio_url = str(page.url)

    page.get_by_role("textbox", name="标题内容", exact=True).fill(
        TITLE_PARAMETER, timeout=10_000
    )

    publish = page.get_by_role("button", name=publish_button, exact=True)
    box = publish.bounding_box()
    assert box is not None
    publish.click(timeout=10_000)
    page.wait_for_url(f"**{DONE_PATH}", timeout=10_000)
    done_url = str(page.url)
    assert page.get_by_role("dialog", name="发布成功", exact=True).is_visible()

    return {
        "schemaVersion": 1,
        "account": {"loggedIn": True, "handle": "automation-tool-sa-test-account"},
        "platform": "douyin",
        "domain": DOMAIN,
        "language": "zh-CN",
        "viewport": {"width": viewport["width"], "height": viewport["height"]},
        "entryUrl": entry_url,
        "actions": [
            {
                "external": False,
                "kind": "click",
                "target": {"role": "link", "name": "创作中心"},
                "resultingUrl": studio_url,
            },
            {
                "external": False,
                "kind": "fill",
                "target": {"role": "textbox", "name": "标题内容"},
                "value": {"parameter": "title"},
            },
            {
                "external": True,
                "kind": "click",
                "target": {
                    "role": "button",
                    "name": publish_button,
                    "x": int(box["x"] + box["width"] / 2),
                    "y": int(box["y"] + box["height"] / 2),
                },
                "resultingUrl": done_url,
                "resultingVisible": {"role": "dialog", "name": "发布成功"},
            },
        ],
        "successEvidence": [{"kind": "url_matches", "url": done_url}],
    }


def test_sa_chain_end_to_end_on_the_embedded_chromium(
    staged_embedded_chromium: Path, tmp_path: Path
) -> None:
    profile = tmp_path / "automation-tool-sa-e2e-profile"
    create_private_profile_directory(profile)
    state = {"home": "home.html", "studio": "studio.html", "done": "done.html"}
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=staged_embedded_chromium,
            profile_directory=profile,
            headless=True,
        )
    ):
        page = cast(Any, runtime.primary_window().playwright_page)
        _serve_portal(page, state)
        replay_page = PlaywrightReplayPage(page, action_timeout_seconds=10)

        # 1+2 — a real learning pass on the live complex page, then SA-02.
        raw = _record_real_trajectory(page, publish_button="发布内容")
        candidate_v1 = clean_trajectory(raw)
        assert candidate_v1["pathPattern"] == BASE_PATH

        # 3 — the replay-sandbox gate runs on the real page, fresh navigation.
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
        sandbox_outcome = replay_skill(
            _parse(candidate_v1), replay_page, parameters={"title": TITLE_PARAMETER}
        )
        assert sandbox_outcome.passed
        assert sandbox_outcome.completed_steps == 3
        assert sandbox_outcome.external_side_effects == 1

        # 4 — sign and publish immutable v1 through the real gate chain.
        registry = SkillRegistry(trusted_public_key=_public_key())
        signed_v1 = sign_candidate(
            candidate_v1, approval=APPROVAL, seed=SIGNING_SEED, replay=sandbox_outcome
        )
        published_v1 = registry.publish(signed_v1)
        skill_id = published_v1.skill.skill_id

        # 5 — the page revision: publish button renamed. Replay must fail at
        # the anchor, before the external click, and hand back from the last
        # internal checkpoint.
        state["studio"] = "studio-drift.html"
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
        with pytest.raises(ReplayFailed) as drift_failure:
            replay_skill(
                published_v1.skill, replay_page, parameters={"title": TITLE_PARAMETER}
            )
        assert drift_failure.value.dispatched is False
        assert drift_failure.value.failed_index == 3
        assert drift_failure.value.checkpoint_index == 2
        handback = decide_handback(published_v1.skill, drift_failure.value)
        assert handback.action == "resume_browser_use"
        assert handback.resume_from_checkpoint == 2
        assert handback.remaining_step_indexes == [2, 3]
        assert handback.may_resend is False

        # 6 — Browser Use completes the flow on the revised page for real; the
        # diff becomes candidate v2 with the published v1 as parent.
        raw_drift = _record_real_trajectory(page, publish_button="立即发表")
        cleaned_drift = clean_trajectory(raw_drift)
        candidate_v2 = dict(cleaned_drift)
        candidate_v2["skillId"] = skill_id
        candidate_v2["version"] = 2
        candidate_v2["parentVersion"] = 1

        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
        v2_outcome = replay_skill(
            _parse(candidate_v2), replay_page, parameters={"title": TITLE_PARAMETER}
        )
        assert v2_outcome.passed
        signed_v2 = sign_candidate(
            candidate_v2, approval=APPROVAL, seed=SIGNING_SEED, replay=v2_outcome
        )
        registry.publish(signed_v2)

        # 7+8 — routing over the real registry: each fingerprint reaches its
        # own version, an unknown page reaches none, and the management view
        # shows the whole tree.
        v1_fingerprint = published_v1.skill.fingerprint_sha256
        v2_fingerprint = str(
            cast(dict[str, object], candidate_v2["entryFingerprint"])["sha256"]
        )
        assert v1_fingerprint != v2_fingerprint
        stats = {
            (skill_id, 1): VersionStats(successes=5, failures=1, last_hit=10),
            (skill_id, 2): VersionStats(successes=1, failures=0, last_hit=12),
        }
        view = build_management_view(registry, stats, disabled=set())
        assert len(view) == 1
        versions = cast(list[dict[str, object]], view[0]["versions"])
        assert [node["version"] for node in versions] == [1, 2]
        assert versions[1]["parentVersion"] == 1
        applicable = view[0]["applicableVersionFor"]
        width = published_v1.skill.viewport_width
        assert (
            applicable(PageContext(v1_fingerprint, "zh-CN", width)) == 1
        )
        assert (
            applicable(PageContext(v2_fingerprint, "zh-CN", width)) == 2
        )
        assert applicable(PageContext("0" * 64, "zh-CN", width)) is None

        # 9 — failure AFTER the external click: the success page is broken, so
        # the outcome is uncertain and the only safe move is reconciliation.
        state["studio"] = "studio.html"
        state["done"] = "done-broken.html"
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
        with pytest.raises(ReplayFailed) as dispatched_failure:
            replay_skill(
                published_v1.skill, replay_page, parameters={"title": TITLE_PARAMETER}
            )
        assert dispatched_failure.value.dispatched is True
        assert dispatched_failure.value.failed_index == 3
        reconcile = decide_handback(published_v1.skill, dispatched_failure.value)
        assert reconcile.action == "reconcile_only"
        assert reconcile.may_continue is False
        assert reconcile.may_resend is False

    assert not runtime.is_running
    assert_private_profile_directory(profile)


def _parse(candidate: dict[str, object]) -> Any:
    from automation_tool.executor.automation_skill import parse_automation_skill

    return parse_automation_skill(candidate)


def _public_key() -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return (
        Ed25519PrivateKey.from_private_bytes(SIGNING_SEED).public_key().public_bytes_raw()
    )
