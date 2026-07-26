"""Contract tests for the container ingress Nginx configuration.

This edge is a second implementation of the same public boundary the host
Nginx serves in `deploy/cloud`. Both must honour the identical request
contract, so the header rules that the desktop App depends on are asserted
here as well rather than only where the currently deployed edge lives.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

INGRESS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(INGRESS_ROOT))

import render_config  # noqa: E402


def _load_template() -> str:
    return (INGRESS_ROOT / "nginx.conf.template").read_text(encoding="utf-8")


class IngressRequestIdContract(unittest.TestCase):
    """A caller-supplied correlation ID must survive the proxy hop."""

    def setUp(self) -> None:
        self.template = _load_template()

    def test_forwards_the_caller_request_id_instead_of_replacing_it(self) -> None:
        # The desktop App correlates every reply by comparing the echoed
        # x-request-id against the one it sent, and rejects a mismatch as a
        # protocol violation. An edge that overwrites a supplied header makes
        # every App call fail after the Control Plane already accepted it.
        self.assertNotIn("proxy_set_header X-Request-ID $request_id;", self.template)
        self.assertIn("proxy_set_header X-Request-ID $forwarded_request_id;", self.template)
        self.assertRegex(
            self.template,
            r"map\s+\$http_x_request_id\s+\$forwarded_request_id\s*\{",
        )

    def test_still_renders_for_a_valid_demo_host(self) -> None:
        rendered = render_config.render(host="api.demo.example.com", template=self.template)
        self.assertNotIn("__DEMO_HOST__", rendered)
        self.assertIn("proxy_set_header X-Request-ID $forwarded_request_id;", rendered)
        self.assertIsNotNone(
            re.search(r"map\s+\$http_x_request_id\s+\$forwarded_request_id\s*\{", rendered)
        )


if __name__ == "__main__":
    unittest.main()
