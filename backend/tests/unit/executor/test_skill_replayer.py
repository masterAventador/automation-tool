"""SA-04: deterministic replay of a signed skill against a semantic page.

The replayer executes by the skill's semantic anchors and pre/postconditions —
it does not call a vision model per step. Each step gets a timeout, at most one
external side effect, its own postcondition acceptance, and the whole run is
bounded by the skill's declared external boundary. A checkpoint step is where a
failed replay may later be handed back to Browser Use (SA-05).

The page is injected, so these tests drive real replay logic without a browser:
a scripted ``FakePage`` answers ``find`` / ``holds`` / ``current_path`` and
records the side effects it was asked to perform.
"""

from __future__ import annotations

import pytest
from tests.unit.executor.test_skill_trajectory_cleaner import raw

from automation_tool.executor.automation_skill import parse_automation_skill
from automation_tool.executor.skill_replayer import (
    ReplayFailed,
    ReplayOutcome,
    replay_skill,
)
from automation_tool.executor.skill_trajectory_cleaner import clean_trajectory


def skill():
    return parse_automation_skill(clean_trajectory(raw()))


def skill_with_key_and_scroll():
    """滚动找到入口、按 Escape 关掉活动弹窗，再走原来的三步。

    REVIEW-2026-08-06 SA#4：契约与清洗器都承载按哪个键、往哪滚，但
    act() 只在 fill 时携带载荷——页面适配层收到 press_key/scroll 时
    无从知道键名与方向，接上真实浏览器会静默做错而不是报错。
    """
    document = raw()
    document["actions"] = [
        {
            "kind": "scroll",
            "direction": "down",
            "target": {"role": "listitem", "name": "更多作品", "x": 100, "y": 300},
            "external": False,
            "resultingVisible": None,
        },
        {
            "kind": "press_key",
            "key": "Escape",
            "target": {"role": "dialog", "name": "活动弹窗", "x": 200, "y": 200},
            "external": False,
            "resultingVisible": None,
        },
        *document["actions"],
    ]
    return parse_automation_skill(clean_trajectory(document))


def skill_with_a_step_after_the_external_one():
    """发布，然后关确认弹窗——外部步之后还有内部步的最常见真实形态。

    REVIEW-2026-08-06 SA#1/SA#8：三步 fixture 的外部步恰好是最后一步，
    「外部步成功之后再失败」这一端点因此从未被任何用例踩到过。
    """
    document = raw()
    document["actions"] = [
        *document["actions"],
        {
            "kind": "click",
            "target": {"role": "button", "name": "知道了", "x": 420, "y": 480},
            "external": False,
            "resultingVisible": {"role": "listitem", "name": "已发布"},
        },
    ]
    return parse_automation_skill(clean_trajectory(document))


class FakePage:
    """A scripted page: every anchor is findable and every condition holds."""

    def __init__(
        self,
        *,
        missing: set[str] | None = None,
        failing: set[str] | None = None,
        path: str = "/creator-micro/content/manage",
    ):
        self.missing = missing or set()
        self.failing = failing or set()
        self.path = path
        self.side_effects: list[tuple[str, str]] = []
        self.visited: list[str] = []

    def find(self, role: str, name: str) -> object | None:
        return None if name in self.missing else (role, name)

    def holds(self, kind: str, *, role=None, name=None, pattern=None) -> bool:
        token = pattern or name
        return token not in self.failing

    def act(self, kind: str, handle: object, value: str | None) -> None:
        _role, name = handle  # type: ignore[misc]
        self.side_effects.append((kind, name, value))

    def current_path(self) -> str:
        return self.path


class TestDeterministicReplay:
    def test_a_clean_skill_replays_and_reports_success(self) -> None:
        page = FakePage()
        outcome = replay_skill(skill(), page, parameters={"caption": "今天的护肤心得"})

        assert isinstance(outcome, ReplayOutcome)
        assert outcome.passed is True
        assert outcome.completed_steps == 3
        # Exactly one external side effect was performed (the 发布 click).
        assert outcome.external_side_effects == 1

    def test_fill_values_resolve_from_runtime_parameters_only(self) -> None:
        page = FakePage()
        replay_skill(skill(), page, parameters={"caption": "用户提供的标题"})

        fills = [effect for effect in page.side_effects if effect[0] == "fill"]
        assert fills == [("fill", "作品标题", "用户提供的标题")]

    def test_a_missing_parameter_fails_before_any_side_effect(self) -> None:
        page = FakePage()
        with pytest.raises(ReplayFailed, match="parameter"):
            replay_skill(skill(), page, parameters={})
        assert page.side_effects == []


class TestSafetyProperties:
    def test_a_missing_anchor_stops_at_its_checkpoint(self) -> None:
        page = FakePage(missing={"上传视频"})
        with pytest.raises(ReplayFailed) as raised:
            replay_skill(skill(), page, parameters={"caption": "x"})
        # The failure names the checkpoint it stopped at, for SA-05 handback.
        assert raised.value.checkpoint_index == 1
        assert page.side_effects == []

    def test_a_failed_postcondition_is_a_replay_failure(self) -> None:
        page = FakePage(failing={"/creator-micro/content/manage"})
        with pytest.raises(ReplayFailed, match="postcondition"):
            replay_skill(skill(), page, parameters={"caption": "x"})

    def test_either_of_two_success_urls_is_enough(self) -> None:
        """REVIEW-2026-08-06 SA#11：多条成功 URL 的语义是「任一成立」。

        current_path 只有一个值，逐条相等的旧语义让两条不同 pattern 必然
        互斥——这样的技能永远回放不过，又因为发布门要求回放通过，它也
        永远发布不了。
        """
        document = raw()
        document["successEvidence"] = [
            {
                "kind": "url_matches",
                "url": "https://creator.douyin.com/creator-micro/content/manage",
            },
            {
                "kind": "url_matches",
                "url": "https://creator.douyin.com/creator-micro/content/published",
            },
        ]
        two_exits = parse_automation_skill(clean_trajectory(document))

        outcome = replay_skill(two_exits, FakePage(), parameters={"caption": "x"})
        assert outcome.passed is True

        # 否定端点：两条都不匹配仍然必须失败——「任一」不是「永真」。
        with pytest.raises(ReplayFailed, match="success evidence"):
            replay_skill(
                two_exits,
                FakePage(path="/somewhere/else"),
                parameters={"caption": "x"},
            )

    def test_press_key_and_scroll_payloads_reach_the_page(self) -> None:
        page = FakePage()
        replay_skill(skill_with_key_and_scroll(), page, parameters={"caption": "x"})

        assert ("scroll", "更多作品", "down") in page.side_effects
        assert ("press_key", "活动弹窗", "Escape") in page.side_effects

    def test_dispatched_survives_a_failure_in_a_later_step(self) -> None:
        """发布已经点了，之后无论哪一步失败，dispatched 都必须还是 True。

        这个标志曾在每步循环开头被重置：外部步做完、下一步（关确认弹窗）
        后置条件失败，失败对象却报 dispatched=False——SA-05 于是判成可以
        交回 Browser Use 从头重跑，剩余步骤里还列着那个已经发生的发布。
        真实平台上这就是重复投稿（REVIEW-2026-08-06 SA#1）。
        """
        page = FakePage(failing={"已发布"})
        with pytest.raises(ReplayFailed) as raised:
            replay_skill(
                skill_with_a_step_after_the_external_one(),
                page,
                parameters={"caption": "x"},
            )

        assert ("click", "发布", None) in page.side_effects, "the external step ran"
        assert raised.value.dispatched is True

    def test_replay_refuses_a_skill_whose_boundary_lies(self) -> None:
        """真正触发边界分支，而不是对成功回放断言一个恒真式。

        这条用例的前身名为 never_exceeds_the_external_side_effect_boundary，
        断言 external_side_effects <= boundary——对成功返回恒真（失败走
        异常），真正的边界代码一次都没红过（REVIEW-2026-08-06 SA#7）。
        parse 层会拒掉声明超界的文档，但 replayer 的 docstring 承诺自己
        不信任这一点；那就绕过 parse 直接构造一个撒谎的技能来验证。
        """
        import dataclasses

        liar = dataclasses.replace(skill(), max_external_steps=0)
        page = FakePage()

        with pytest.raises(ReplayFailed, match="boundary"):
            replay_skill(liar, page, parameters={"caption": "x"})
        assert page.side_effects == [], "the boundary must hold before any side effect"

        # 合规技能的报数本身也要是准的：恰好一次外部动作（发布）。
        outcome = replay_skill(skill(), FakePage(), parameters={"caption": "x"})
        assert outcome.external_side_effects == 1


class RaisingPage(FakePage):
    """A page whose driver dies mid-action — what a real click timeout does."""

    def __init__(self, *, raising: set[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.raising = raising

    def act(self, kind: str, handle: object, value: str | None) -> None:
        _role, name = handle  # type: ignore[misc]
        if name in self.raising:
            raise RuntimeError("driver timed out mid-action")
        super().act(kind, handle, value)


class TestDriverExceptions:
    """真实适配层接入后，act() 会抛原生驱动异常（点击超时、页面被关）。

    SA 真实浏览器验收（2026-08-06）发现的缺口：这些异常裸穿 replay_skill，
    既不带 checkpoint 也不带 dispatched——上层若把外部步的点击超时按
    「可重试」处理，就是重复投稿的形状（SA#1 的变体）。
    """

    def test_an_act_exception_on_the_external_step_is_outcome_uncertain(self) -> None:
        page = RaisingPage(raising={"发布"})
        with pytest.raises(ReplayFailed) as raised:
            replay_skill(skill(), page, parameters={"caption": "x"})

        # The click was *attempted*: whether the platform saw it is unknowable,
        # so the failure must be outcome-uncertain, never safely resumable.
        assert raised.value.dispatched is True
        assert raised.value.failed_index == 3
        assert raised.value.checkpoint_index == 2

    def test_an_act_exception_on_an_internal_step_stays_resumable(self) -> None:
        page = RaisingPage(raising={"上传视频"})
        with pytest.raises(ReplayFailed) as raised:
            replay_skill(skill(), page, parameters={"caption": "x"})

        assert raised.value.dispatched is False
        assert raised.value.failed_index == 1
        assert raised.value.checkpoint_index == 1
        assert page.side_effects == []
