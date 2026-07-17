"""Deterministic OpenAPI export and drift checks."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from automation_tool.control_plane.bootstrap.app import create_app


class OpenApiDriftError(RuntimeError):
    """The committed snapshot differs from the application contract."""


def render_openapi_document() -> str:
    """Render the database-independent API schema in a stable text format."""

    schema = create_app(database=None).openapi()
    return f"{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}\n"


def export_openapi(output: Path, *, check: bool = False) -> None:
    """Write the schema or fail when a committed snapshot has drifted."""

    rendered = render_openapi_document()
    if check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise OpenApiDriftError("OpenAPI snapshot is out of date")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def main(arguments: Sequence[str] | None = None) -> None:
    """Export or verify a snapshot from the installed backend package."""

    parser = argparse.ArgumentParser(description="Export the Control Plane OpenAPI schema")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parsed = parser.parse_args(arguments)

    try:
        export_openapi(parsed.output, check=parsed.check)
    except OpenApiDriftError as error:
        parser.error(str(error))
