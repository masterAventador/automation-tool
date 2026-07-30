#!/usr/bin/env python3
"""PC-13 gate: refuse a package that cannot serve a typeface some part asks for.

The failure this exists to catch has no symptom at build time and a very quiet
one at run time. A part asks for `"Space Mono"` at weight 700; the package
declares that family only at 400; Chromium picks the weight bucket first, finds
no 700 face, and silently draws the text in whatever the host machine has. The
render succeeds, every other gate stays green, and the film ships with the
wrong typeface -- or, for Chinese, with tofu.

So the check re-derives, never reads back:

* every ``(family, weight)`` is rescanned out of the read-only submodule
  through the frozen catalog's own file list, and the digest of that whole
  table is compared with the contract -- a part that starts naming a new
  typeface turns this red instead of rendering in the host font;
* every scanned family must carry a declared policy, and a substitution must
  name a replacement that is itself packaged;
* every resolved pair must have a woff2 in the offline lock at that exact
  weight. This is the omission detector the whole task turns on: it holds
  whether the gap came from a new part, a dropped download or a typo.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion_part_typography import (
    CATALOG_CONTRACT_PATH,
    OFFLINE_LOCK_PATH,
    POLICIES,
    POLICY_HOST,
    POLICY_SUBSTITUTED,
    TYPOGRAPHY_CONTRACT_PATH,
    families_without_policy,
    family_policies,
    load_json,
    packaged_weights,
    resolve_faces,
    scan_catalog_parts,
    scan_digest,
)

_EXPECTED_PARTS: Final = 134


class CheckError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"motion part typography check failed: {message}")


def audit(
    *,
    contract: dict,
    catalog: dict,
    lock: dict,
    scanned: dict[str, frozenset[tuple[str, int]]],
) -> list[str]:
    """Every problem found, so one run reports all of them rather than the first."""
    problems: list[str] = []

    catalog_digest = hashlib.sha256(
        CATALOG_CONTRACT_PATH.read_bytes()
    ).hexdigest()
    pinned = contract["source"]["motionCatalog"]["sha256"]
    if catalog_digest != pinned:
        problems.append(
            f"catalog contract drifted from the pinned input: {catalog_digest} != {pinned}"
        )

    if len(scanned) != _EXPECTED_PARTS:
        problems.append(f"scanned {len(scanned)} parts, expected {_EXPECTED_PARTS}")

    policies = family_policies(contract)
    for family, policy in sorted(policies.items()):
        if policy.policy not in POLICIES:
            problems.append(f"{family}: unknown policy {policy.policy!r}")
            continue
        if policy.policy == POLICY_SUBSTITUTED:
            if not policy.replacement:
                problems.append(f"{family}: substituted without a replacement")
            elif policy.replacement not in policies:
                problems.append(
                    f"{family}: replacement {policy.replacement!r} has no policy of its own"
                )
            if not policy.reason or not policy.visual_difference:
                problems.append(
                    f"{family}: a substitution changes what the part looks like, so it must "
                    "record both a reason and the visual difference"
                )
        if policy.policy == POLICY_HOST and not policy.reason:
            problems.append(f"{family}: left to the host font without a recorded reason")

    unknown = families_without_policy(scanned, contract)
    if unknown:
        problems.append(f"parts name typefaces with no declared policy: {sorted(unknown)}")

    declared_but_unused = set(policies) - {
        family for pairs in scanned.values() for family, _ in pairs
    }
    if declared_but_unused:
        # A policy nobody needs is how the table rots into a wish list.
        problems.append(f"declared policies no part uses: {sorted(declared_but_unused)}")

    available = packaged_weights(lock)
    every_pair = sorted({pair for pairs in scanned.values() for pair in pairs})
    _, unmet = resolve_faces(every_pair, policies=policies, packaged_weights=available)
    if unmet:
        problems.append(
            "the package cannot serve these (family, weight) requests, so those parts "
            f"would silently fall back to the host font: {list(unmet)}"
        )

    digest = scan_digest(scanned)
    recorded = contract["generated"]["scanSha256"]
    if digest != recorded:
        problems.append(f"scan digest drifted: {digest} != {recorded}")
    generated = contract["generated"]
    if generated["parts"] != len(scanned):
        problems.append("generated.parts drifted")
    if generated["families"] != len(policies):
        problems.append("generated.families drifted")
    if generated["pairs"] != len(every_pair):
        problems.append("generated.pairs drifted")

    chinese = contract["chineseFace"]["artifactPath"]
    built = {font["localPath"]: font for font in lock.get("builtFonts", [])}
    downloaded = {artifact["localPath"] for artifact in lock["artifacts"]}
    if chinese not in built and chinese not in downloaded:
        problems.append(f"the Chinese face is not a locked artifact: {chinese}")
    elif chinese in built:
        record = built[chinese]
        if record["sourceFamily"] != contract["chineseFace"]["sourceFamily"]:
            problems.append(
                "the Chinese face is served by a different font than the contract claims: "
                f"{record['sourceFamily']} != {contract['chineseFace']['sourceFamily']}"
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=TYPOGRAPHY_CONTRACT_PATH)
    parser.add_argument("--lock", type=Path, default=OFFLINE_LOCK_PATH)
    arguments = parser.parse_args()

    contract = load_json(arguments.contract)
    catalog = load_json(CATALOG_CONTRACT_PATH)
    lock = load_json(arguments.lock)
    scanned = scan_catalog_parts(catalog_contract=catalog)
    problems = audit(contract=contract, catalog=catalog, lock=lock, scanned=scanned)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise CheckError(f"{len(problems)} problem(s)")
    policies = family_policies(contract)
    substituted = sum(1 for policy in policies.values() if policy.policy == POLICY_SUBSTITUTED)
    host = sum(1 for policy in policies.values() if policy.policy == POLICY_HOST)
    print(
        "motion part typography is valid: "
        f"{len(scanned)} parts, {len(policies)} families "
        f"({len(policies) - substituted - host} packaged / {substituted} substituted / "
        f"{host} host), {contract['generated']['pairs']} (family, weight) pairs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
