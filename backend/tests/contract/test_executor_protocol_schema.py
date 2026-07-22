from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from automation_tool.protocol import ExecutorProtocolError, parse_executor_message
from automation_tool.protocol.schema import (
    ExecutorSchemaDriftError,
    export_executor_schema,
    main,
    render_executor_schema_document,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
SCHEMA_PATH = REPOSITORY_ROOT / "contracts/protocol/executor-v1.schema.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/fixtures/executor-v1"
VALID_FIXTURE_ROOT = FIXTURE_ROOT / "valid"
INVALID_FIXTURE_ROOT = FIXTURE_ROOT / "invalid"
FIXTURE_DOCUMENT = FIXTURE_ROOT / "README.md"

EXPECTED_VALID_FIXTURES = {
    "action-accept.json",
    "action-execute.json",
    "executor-heartbeat.json",
    "executor-hello.json",
    "microsecond-deadline.json",
    "platform-session-health.json",
    "step-progress.json",
    "task-accept.json",
    "task-discover.json",
    "task-discovery-batch.json",
    "task-discovery-completed.json",
    "task-offer.json",
}
EXPECTED_INVALID_FIXTURES = {
    "deadline-before-send.json",
    "deadline-before-send-microsecond.json",
    "deadline-equals-send.json",
    "discovery-command-unknown-field.json",
    "duplicate-key.json",
    "inline-data-uri.json",
    "invalid-idempotency-key.json",
    "invalid-message-id.json",
    "invalid-sequence-type.json",
    "invalid-sequence-zero.json",
    "invalid-version.json",
    "lifecycle-with-task-scope.json",
    "missing-protocol-version.json",
    "naive-sent-at.json",
    "negative-zero-offset.json",
    "non-finite-number.json",
    "non-utc-sent-at.json",
    "payload-too-deep.json",
    "payload-too-many-fields.json",
    "platform-session-health-with-task-scope.json",
    "private-path.json",
    "sensitive-assignment.json",
    "sensitive-cookie-field.json",
    "task-control-with-event-baseline.json",
    "task-missing-attempt.json",
    "task-offer-missing-event-baseline.json",
    "unknown-envelope-field.json",
    "unknown-message-type.json",
    "unsafe-sequence.json",
}
SEMANTIC_ONLY_INVALID_FIXTURES = {
    "deadline-before-send.json",
    "deadline-before-send-microsecond.json",
    "deadline-equals-send.json",
    "duplicate-key.json",
    "inline-data-uri.json",
    "non-finite-number.json",
    "payload-too-deep.json",
    "private-path.json",
    "sensitive-assignment.json",
    "sensitive-cookie-field.json",
    "task-control-with-event-baseline.json",
    "task-offer-missing-event-baseline.json",
}


def fixture_names(root: Path) -> set[str]:
    return {path.name for path in root.glob("*.json")}


def parse_json_fixture(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_validator() -> Draft202012Validator:
    return Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )


def test_committed_executor_schema_is_deterministic_and_draft_2020_12() -> None:
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    schema = json.loads(committed)

    assert committed == render_executor_schema_document()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (
        schema["$id"] == "https://automation-tool.local/contracts/protocol/executor-v1.schema.json"
    )
    assert schema["x-wire-limits"] == {
        "maxMessageBytes": 32768,
        "maxPayloadBytes": 16384,
        "maxPayloadDepth": 8,
        "maxCollectionItems": 64,
        "maxStringLength": 4096,
    }
    Draft202012Validator.check_schema(schema)


def test_schema_export_check_detects_missing_and_drifted_snapshots(tmp_path: Path) -> None:
    snapshot = tmp_path / "executor-v1.schema.json"

    with pytest.raises(ExecutorSchemaDriftError, match="Executor schema snapshot is out of date"):
        export_executor_schema(snapshot, check=True)

    export_executor_schema(snapshot)
    export_executor_schema(snapshot, check=True)
    snapshot.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ExecutorSchemaDriftError, match="Executor schema snapshot is out of date"):
        export_executor_schema(snapshot, check=True)


def test_schema_export_cli_writes_checks_and_reports_only_fixed_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "executor-v1.schema.json"

    main(["--output", str(snapshot)])
    main(["--output", str(snapshot), "--check"])
    snapshot.write_text("{}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as captured:
        main(["--output", str(snapshot), "--check"])

    assert captured.value.code == 2
    error_output = capsys.readouterr().err
    assert "Executor schema snapshot is out of date" in error_output
    assert str(snapshot) not in error_output


def test_fixture_inventory_is_exact_documented_and_nontrivial() -> None:
    document = FIXTURE_DOCUMENT.read_text(encoding="utf-8")

    assert fixture_names(VALID_FIXTURE_ROOT) == EXPECTED_VALID_FIXTURES
    assert fixture_names(INVALID_FIXTURE_ROOT) == EXPECTED_INVALID_FIXTURES
    assert len(EXPECTED_VALID_FIXTURES) == 12
    assert len(EXPECTED_INVALID_FIXTURES) == 29
    assert len(SEMANTIC_ONLY_INVALID_FIXTURES) == 12
    for fixture_name in sorted(SEMANTIC_ONLY_INVALID_FIXTURES):
        assert f"`{fixture_name}`" in document
    assert "12 个语义层无效样例" in document
    assert "其余 17 个结构层无效样例" in document


@pytest.mark.parametrize(
    "fixture_name",
    sorted(EXPECTED_VALID_FIXTURES),
)
def test_valid_fixtures_pass_formal_parser_schema_and_round_trip(fixture_name: str) -> None:
    path = VALID_FIXTURE_ROOT / fixture_name
    raw = path.read_text(encoding="utf-8")
    parsed = parse_executor_message(raw)

    assert list(schema_validator().iter_errors(parse_json_fixture(path))) == []
    assert (
        parse_executor_message(
            json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        )
        == parsed
    )


@pytest.mark.parametrize(
    "fixture_name",
    sorted(EXPECTED_INVALID_FIXTURES),
)
def test_invalid_fixtures_fail_the_formal_parser_with_one_safe_error(fixture_name: str) -> None:
    raw = (INVALID_FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8")

    with pytest.raises(ExecutorProtocolError) as captured:
        parse_executor_message(raw)

    assert str(captured.value) == "Invalid Executor protocol message"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "fixture_name",
    sorted(EXPECTED_INVALID_FIXTURES - SEMANTIC_ONLY_INVALID_FIXTURES),
)
def test_standard_schema_rejects_every_structural_invalid_fixture(fixture_name: str) -> None:
    payload = parse_json_fixture(INVALID_FIXTURE_ROOT / fixture_name)

    assert list(schema_validator().iter_errors(payload))


@pytest.mark.parametrize(
    "fixture_name",
    sorted(SEMANTIC_ONLY_INVALID_FIXTURES - {"duplicate-key.json", "non-finite-number.json"}),
)
def test_semantic_only_fixtures_are_intentionally_beyond_standard_schema(
    fixture_name: str,
) -> None:
    payload = parse_json_fixture(INVALID_FIXTURE_ROOT / fixture_name)

    assert list(schema_validator().iter_errors(payload)) == []
