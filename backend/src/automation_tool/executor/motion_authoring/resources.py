"""Where the read-only data this package needs lives, decided once.

Frozen into the Executor package the contracts and the locked authoring
reference sit beside the binary; from a source checkout they sit in the
repository. Resolving both here is what lets the packaged build and the test
build read the same files through the same code — the alternative, a build-time
switch on where to look, is the shape that has already cost this project a
release.

It lives in its own module rather than in `agent.py` because more than one
module now needs it, and a second copy of "where our files are" is how a
packaged build and a checkout start disagreeing about what exists.

(English docstring for the reason `part_document.py` gives:
`check_user_facing_branding.py` reads Chinese-bearing literals in a `.py` source
as operator copy.)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final


def _resource_root() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen, str):
        return Path(frozen)
    return Path(__file__).resolve().parents[5]


RESOURCE_ROOT: Final = _resource_root()
CONTRACTS_ROOT: Final = RESOURCE_ROOT / "contracts"

__all__ = ["CONTRACTS_ROOT", "RESOURCE_ROOT"]
