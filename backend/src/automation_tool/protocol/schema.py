"""Deterministic Executor protocol JSON Schema export and drift checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from automation_tool.protocol.executor_envelope import (
    MAX_EXECUTOR_COLLECTION_ITEMS,
    MAX_EXECUTOR_MESSAGE_BYTES,
    MAX_EXECUTOR_PAYLOAD_BYTES,
    MAX_EXECUTOR_PAYLOAD_DEPTH,
    MAX_EXECUTOR_STRING_LENGTH,
    ExecutorMessage,
)

EXECUTOR_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
EXECUTOR_SCHEMA_ID = "https://automation-tool.local/contracts/protocol/executor-v1.schema.json"
UTC_RFC3339_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"


class ExecutorSchemaDriftError(RuntimeError):
    """The committed Executor schema differs from the Pydantic source."""


def executor_schema() -> dict[str, Any]:
    """Build the public Draft 2020-12 document from the Pydantic union."""

    schema = ExecutorMessage.model_json_schema()
    schema["$schema"] = EXECUTOR_SCHEMA_DIALECT
    schema["$id"] = EXECUTOR_SCHEMA_ID
    schema["title"] = "Automation Tool Executor Protocol v1"
    schema["x-wire-limits"] = {
        "maxMessageBytes": MAX_EXECUTOR_MESSAGE_BYTES,
        "maxPayloadBytes": MAX_EXECUTOR_PAYLOAD_BYTES,
        "maxPayloadDepth": MAX_EXECUTOR_PAYLOAD_DEPTH,
        "maxCollectionItems": MAX_EXECUTOR_COLLECTION_ITEMS,
        "maxStringLength": MAX_EXECUTOR_STRING_LENGTH,
    }
    schema["x-semantic-validation-required"] = [
        "wire input must be a bounded UTF-8 JSON object with no duplicate keys",
        "sent_at and deadline_at must use RFC3339 UTC",
        "deadline_at must be later than sent_at",
        "payload encoded size and recursive depth must stay within x-wire-limits",
        "payload strings and field names must not carry credentials or private local data",
        "payload numbers must be finite JSON numbers",
    ]
    definitions = schema["$defs"]
    for model_name in (
        "ExecutorLifecycleEnvelope",
        "PlatformSessionHealthEnvelope",
        "TaskActionCommandEnvelope",
        "TaskCommandEnvelope",
        "TaskCommandResultEnvelope",
        "TaskDiscoveryBatchEnvelope",
        "TaskDiscoveryCommandEnvelope",
        "TaskDiscoveryCompletedEnvelope",
        "TaskEventEnvelope",
    ):
        properties = definitions[model_name]["properties"]
        properties["payload"]["maxProperties"] = MAX_EXECUTOR_COLLECTION_ITEMS
        for timestamp_name in ("sent_at", "deadline_at"):
            properties[timestamp_name]["pattern"] = UTC_RFC3339_PATTERN
    return schema


def render_executor_schema_document() -> str:
    """Render the protocol schema with deterministic formatting."""

    return f"{json.dumps(executor_schema(), ensure_ascii=False, indent=2, sort_keys=True)}\n"


def export_executor_schema(output: Path, *, check: bool = False) -> None:
    """Write the schema or fail when the committed snapshot has drifted."""

    rendered = render_executor_schema_document()
    if check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise ExecutorSchemaDriftError("Executor schema snapshot is out of date")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def main(arguments: Sequence[str] | None = None) -> None:
    """Export or verify the committed Executor protocol schema."""

    parser = argparse.ArgumentParser(description="Export the Executor v1 JSON Schema")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parsed = parser.parse_args(arguments)
    try:
        export_executor_schema(parsed.output, check=parsed.check)
    except ExecutorSchemaDriftError as error:
        parser.error(str(error))


__all__ = [
    "EXECUTOR_SCHEMA_DIALECT",
    "EXECUTOR_SCHEMA_ID",
    "UTC_RFC3339_PATTERN",
    "ExecutorSchemaDriftError",
    "executor_schema",
    "export_executor_schema",
    "render_executor_schema_document",
]
