from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from automation_tool.control_plane.logging import install_control_plane_log_redaction
from automation_tool.logging_redaction import REDACTED_LOG_VALUE, redact_log_text

PAGE_CONTENT = "private author page and reply"
PRIVATE_URL = "https://example.com/private/page?token=hidden"
PRIVATE_PATH = "/Users/alice/Library/Application Support/automation-tool/state.sqlite3"
PRIVATE_TOKEN = "atds1.private-control-plane-session"


def test_control_plane_log_record_boundary_discards_dynamic_values_exceptions_and_extras(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_factory = logging.getLogRecordFactory()
    try:
        install_control_plane_log_redaction()
        installed_factory = logging.getLogRecordFactory()
        install_control_plane_log_redaction()
        assert logging.getLogRecordFactory() is installed_factory

        logger = logging.getLogger("automation_tool.control_plane.acceptance")
        with caplog.at_level(logging.ERROR):
            try:
                raise RuntimeError(
                    f"page_content={PAGE_CONTENT} url={PRIVATE_URL} path={PRIVATE_PATH}"
                )
            except RuntimeError:
                logger.exception(
                    "Control Plane operation failed page_content=%s url=%s token=%s",
                    PAGE_CONTENT,
                    PRIVATE_URL,
                    PRIVATE_TOKEN,
                    extra={"request_id": "safe-request-42"},
                )

        assert "Control Plane operation failed" in caplog.text
        assert "[REDACTED]" in caplog.text
        for private in (PAGE_CONTENT, PRIVATE_URL, PRIVATE_PATH, PRIVATE_TOKEN):
            assert private not in caplog.text
        record = caplog.records[-1]
        assert record.exc_info is None
        assert record.stack_info is None
        assert record.pathname == "[REDACTED]"
        assert record.request_id == "safe-request-42"  # type: ignore[attr-defined]
    finally:
        logging.setLogRecordFactory(original_factory)


def test_uvicorn_access_record_is_collapsed_before_any_handler_can_render_the_target(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_factory = logging.getLogRecordFactory()
    try:
        install_control_plane_log_redaction()
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1:50000",
                "GET",
                f"/api/v1/tasks?keyword={PAGE_CONTENT}&token={PRIVATE_TOKEN}",
                "1.1",
                200,
            )

        assert caplog.records[-1].getMessage() == "Control Plane request"
        assert PAGE_CONTENT not in caplog.text
        assert PRIVATE_TOKEN not in caplog.text
    finally:
        logging.setLogRecordFactory(original_factory)


def test_control_plane_record_boundary_is_fail_closed_and_utf8_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_factory = logging.getLogRecordFactory()
    try:
        install_control_plane_log_redaction()
        control_plane_logger = logging.getLogger("automation_tool.control_plane.acceptance")
        unrelated_logger = logging.getLogger("unrelated.acceptance")
        long_message = "界" * 2000

        with caplog.at_level(logging.INFO):
            control_plane_logger.info(long_message, stack_info=True)
            control_plane_logger.info(object())
            unrelated_logger.info("unrelated message stays visible")

        long_record, object_record, unrelated_record = caplog.records[-3:]
        assert len(long_record.getMessage().encode("utf-8")) <= 4096
        assert long_record.stack_info is None
        assert object_record.getMessage() == REDACTED_LOG_VALUE
        assert unrelated_record.getMessage() == "unrelated message stays visible"
        assert unrelated_record.pathname != REDACTED_LOG_VALUE
    finally:
        logging.setLogRecordFactory(original_factory)


def test_shared_log_redactor_rejects_non_string_values() -> None:
    assert redact_log_text(object()) == REDACTED_LOG_VALUE


def test_control_plane_production_logging_calls_accept_only_literal_messages() -> None:
    source_root = Path(__file__).resolve().parents[3] / "src/automation_tool/control_plane"
    violations: list[str] = []
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {
                "critical",
                "debug",
                "error",
                "exception",
                "info",
                "log",
                "warning",
            }:
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
                continue
            if (
                len(node.args) != 1
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
            ):
                violations.append(f"{source_path.relative_to(source_root)}:{node.lineno}")
            for keyword in node.keywords:
                if keyword.arg != "extra":
                    continue
                if not isinstance(keyword.value, ast.Dict) or any(
                    not isinstance(key, ast.Constant) or key.value != "request_id"
                    for key in keyword.value.keys
                ):
                    violations.append(f"{source_path.relative_to(source_root)}:{node.lineno}")

    assert violations == []
