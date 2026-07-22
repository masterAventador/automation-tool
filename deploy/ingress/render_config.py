"""Render one validated customer Demo hostname into the fixed Nginx template."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Final

_HOST: Final = re.compile(
    r"api\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
    re.ASCII,
)
_TOKEN: Final = "__DEMO_HOST__"


def render(*, host: str, template: str) -> str:
    if len(host) > 253 or _HOST.fullmatch(host) is None:
        raise ValueError("Demo hostname is invalid")
    if template.count(_TOKEN) < 2:
        raise ValueError("Ingress template is invalid")
    rendered = template.replace(_TOKEN, host)
    if _TOKEN in rendered:
        raise ValueError("Ingress template rendering failed")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    rendered = render(
        host=arguments.host,
        template=arguments.template.read_text(encoding="utf-8"),
    )
    arguments.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
