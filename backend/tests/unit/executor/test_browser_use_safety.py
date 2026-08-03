"""BU-05: untrusted-input redaction and confirmation gates for Browser Use."""

from __future__ import annotations

import pytest

from automation_tool.executor.browser_use_safety import (
    BrowserUseSafetyRejected,
    SensitiveDataGate,
    SideEffectConfirmationGate,
    redact_untrusted_text,
)

CDP = "http://127.0.0.1:53211"


class TestRedaction:
    def test_strips_cookies_tokens_keys_and_local_paths(self) -> None:
        raw = (
            "Cookie: sessionid=abcd1234; csrftoken=zzz\n"
            "Authorization: Bearer sk-ws-SECRETKEY.abc.def\n"
            "profile at /Users/aventador/Library/App/private/profile\n"
            "cdp endpoint http://127.0.0.1:53211/devtools\n"
            "windows path C:\\Users\\op\\AppData\\browser\n"
        )
        redacted = redact_untrusted_text(raw)
        for secret in (
            "sessionid=abcd1234",
            "csrftoken=zzz",
            "sk-ws-SECRETKEY.abc.def",
            "/Users/aventador/Library",
            "127.0.0.1:53211",
            "C:\\Users\\op",
        ):
            assert secret not in redacted, secret
        assert "[redacted" in redacted

    def test_keeps_ordinary_page_text_intact(self) -> None:
        raw = "发布你的第一条作品，点击右上角的发布按钮。"
        assert redact_untrusted_text(raw) == raw

    def test_non_string_is_rejected(self) -> None:
        with pytest.raises(BrowserUseSafetyRejected):
            redact_untrusted_text(b"bytes")  # type: ignore[arg-type]

    def test_redaction_is_idempotent(self) -> None:
        raw = "token=Bearer sk-abcdefghijklmnopqrstuvwxyz012345"
        once = redact_untrusted_text(raw)
        assert redact_untrusted_text(once) == once


class TestSensitiveDataGate:
    def test_model_only_ever_sees_placeholders(self) -> None:
        gate = SensitiveDataGate()
        gate.register("douyin_password", "real-secret-value")
        prompt = "登录时在密码框输入 <secret>douyin_password</secret>"
        assert gate.model_visible(prompt) == prompt
        assert "real-secret-value" not in gate.model_visible(prompt)

    def test_unregistered_placeholder_in_prompt_is_rejected(self) -> None:
        gate = SensitiveDataGate()
        with pytest.raises(BrowserUseSafetyRejected):
            gate.model_visible("输入 <secret>unknown_key</secret>")

    def test_real_value_leaking_into_model_text_is_rejected(self) -> None:
        gate = SensitiveDataGate()
        gate.register("douyin_password", "real-secret-value")
        with pytest.raises(BrowserUseSafetyRejected):
            gate.model_visible("历史里混入了 real-secret-value 明文")

    def test_reveal_requires_prior_confirmation(self) -> None:
        gate = SensitiveDataGate()
        gate.register("douyin_password", "real-secret-value")
        with pytest.raises(BrowserUseSafetyRejected):
            gate.reveal("douyin_password")
        gate.confirm_send("douyin_password")
        assert gate.reveal("douyin_password") == "real-secret-value"

    def test_confirmation_is_single_use(self) -> None:
        gate = SensitiveDataGate()
        gate.register("douyin_password", "real-secret-value")
        gate.confirm_send("douyin_password")
        assert gate.reveal("douyin_password") == "real-secret-value"
        with pytest.raises(BrowserUseSafetyRejected):
            gate.reveal("douyin_password")

    def test_invalid_keys_and_values_are_rejected(self) -> None:
        gate = SensitiveDataGate()
        for key in ("", "Bad Key", "x" * 65):
            with pytest.raises(BrowserUseSafetyRejected):
                gate.register(key, "value")
        with pytest.raises(BrowserUseSafetyRejected):
            gate.register("ok_key", "")


class TestSideEffectConfirmationGate:
    def test_dispatch_requires_confirmed_critical_point(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish",
            target_account="creator-123",
            content_hash="a" * 64,
        )
        assert "creator-123" in approval.summary
        with pytest.raises(BrowserUseSafetyRejected):
            gate.authorize_dispatch(approval.confirmation_id, confirmed=False)
        token = gate.authorize_dispatch(approval.confirmation_id, confirmed=True)
        gate.consume_dispatch(token, content_hash="a" * 64)

    def test_dispatch_token_is_single_use(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish", target_account="creator-1", content_hash="b" * 64
        )
        token = gate.authorize_dispatch(approval.confirmation_id, confirmed=True)
        gate.consume_dispatch(token, content_hash="b" * 64)
        with pytest.raises(BrowserUseSafetyRejected):
            gate.consume_dispatch(token, content_hash="b" * 64)

    def test_content_hash_must_match_what_was_confirmed(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish", target_account="creator-1", content_hash="c" * 64
        )
        token = gate.authorize_dispatch(approval.confirmation_id, confirmed=True)
        with pytest.raises(BrowserUseSafetyRejected):
            gate.consume_dispatch(token, content_hash="d" * 64)

    def test_unknown_confirmation_and_token_are_rejected(self) -> None:
        gate = SideEffectConfirmationGate()
        with pytest.raises(BrowserUseSafetyRejected):
            gate.authorize_dispatch("00000000-0000-4000-8000-000000000000", confirmed=True)
        with pytest.raises(BrowserUseSafetyRejected):
            gate.consume_dispatch("f" * 64, content_hash="c" * 64)

    def test_invalid_presentation_inputs_are_rejected(self) -> None:
        gate = SideEffectConfirmationGate()
        for kwargs in (
            {"action": "", "target_account": "a", "content_hash": "e" * 64},
            {"action": "publish", "target_account": "", "content_hash": "e" * 64},
            {"action": "publish", "target_account": "a", "content_hash": "short"},
        ):
            with pytest.raises(BrowserUseSafetyRejected):
                gate.present(**kwargs)

    def test_summary_and_repr_never_leak_the_dispatch_token(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish", target_account="creator-1", content_hash="a" * 64
        )
        token = gate.authorize_dispatch(approval.confirmation_id, confirmed=True)
        assert token not in repr(gate)
        assert token not in approval.summary


class TestGateInputBoundaries:
    def test_only_text_can_be_checked_for_leaked_secrets(self) -> None:
        gate = SensitiveDataGate()
        gate.register("password", "the-real-password")

        for value in (None, 42, b"bytes", ["text"]):
            with pytest.raises(BrowserUseSafetyRejected):
                gate.model_visible(value)  # type: ignore[arg-type]

    def test_a_secret_that_was_never_registered_cannot_be_confirmed(self) -> None:
        gate = SensitiveDataGate()

        with pytest.raises(BrowserUseSafetyRejected):
            gate.confirm_send("password")

    def test_the_gate_repr_names_its_keys_and_never_their_values(self) -> None:
        gate = SensitiveDataGate()
        gate.register("password", "the-real-password")
        gate.register("otp", "123456")

        text = repr(gate)

        assert text == "SensitiveDataGate(keys=['otp', 'password'])"
        assert "the-real-password" not in text

    def test_an_approval_repr_carries_its_identifier_and_not_its_summary(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish",
            target_account="自动化运营测试账号",
            content_hash="a" * 64,
        )

        text = repr(approval)

        assert approval.confirmation_id in text
        assert "自动化运营测试账号" not in text

    def test_a_dispatch_token_that_is_not_even_text_is_refused(self) -> None:
        gate = SideEffectConfirmationGate()
        approval = gate.present(
            action="douyin_publish",
            target_account="自动化运营测试账号",
            content_hash="a" * 64,
        )
        token = gate.authorize_dispatch(approval.confirmation_id, confirmed=True)

        for wrong_token, wrong_hash in ((42, "a" * 64), (token, 42)):
            with pytest.raises(BrowserUseSafetyRejected):
                gate.consume_dispatch(wrong_token, content_hash=wrong_hash)  # type: ignore[arg-type]
        gate.consume_dispatch(token, content_hash="a" * 64)
