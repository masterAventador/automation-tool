"""SA-02: distil a successful Browser Use trajectory into a candidate skill.

The output is an SA-01 document (``parse_automation_skill`` accepts it), so this
module owns exactly the transformations that stand between a noisy trace and a
replayable, auditable skill:

* **coordinates never anchor** — a target's ``x``/``y`` are dropped from the
  goal and, only for the external side-effect step, preserved as a
  ``click_point_v1`` evidence entry;
* **secrets and incidental state are stripped** — the entry URL becomes a bare
  path pattern (query and fragment removed), and any free-text that carries a
  forbidden word or character is refused rather than silently kept;
* **the boundary, account and domain survive** — a logged-out trace, missing
  success evidence, a cross-domain result URL, or more external actions than the
  declared boundary are all refused fail-closed.

What this does NOT do is decide *which* version a cleaned skill becomes, or sign
it — that is SA-03. Here every cleaned trajectory is a candidate ``version: 1``
with no parent; SA-03/SA-06 own lineage.
"""

from __future__ import annotations

import hashlib
from typing import Final, NoReturn
from urllib.parse import urlsplit

from automation_tool.executor.automation_skill import (
    FORBIDDEN_TEXT_CHARACTERS,
    FORBIDDEN_TEXT_WORDS,
    contract,
)

_MAX_ACTIONS: Final = 80


class TrajectoryRejected(ValueError):
    """The raw trajectory is not one a skill can be distilled from."""


def _reject(message: str) -> NoReturn:
    raise TrajectoryRejected(message)


def _seq(value: object) -> list[object]:
    """The contract's vocabularies are JSON arrays; narrow `object` to one."""
    return value if isinstance(value, list) else []


def _clean_text(value: object, where: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        _reject(f"{where} must be bounded text")
    lowered = value.lower()
    if any(character in FORBIDDEN_TEXT_CHARACTERS for character in value) or any(
        word in lowered for word in FORBIDDEN_TEXT_WORDS
    ):
        # Refused, not scrubbed: a name that carries a secret is not a name the
        # page actually shows, so keeping a "cleaned" version of it would invent
        # an anchor that never existed.
        _reject(f"{where} contains forbidden material")
    return value


def _path_of(url: object, domain: str, where: str) -> str:
    if not isinstance(url, str):
        _reject(f"{where} must be a URL string")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname != domain:
        _reject(f"{where} is on a different domain than the trajectory")
    # Query and fragment are dropped wholesale: that is where session ids,
    # tokens and one-shot nonces live.
    return parts.path or "/"


def _clean_goal(
    target: object, roles: list[object], positions: list[object], maximum: int
) -> dict[str, object]:
    if not isinstance(target, dict) or "role" not in target or "name" not in target:
        _reject("action target must name a role and a name")
    if target["role"] not in roles:
        _reject("action target role is not in the closed vocabulary")
    relative = target.get("relativePosition")
    if relative is not None and relative not in positions:
        _reject("action target relative position is not in the closed vocabulary")
    return {
        "role": target["role"],
        "name": _clean_text(target["name"], "target name", maximum=maximum),
        "nearText": (
            None
            if target.get("nearText") is None
            else _clean_text(target["nearText"], "target near-text", maximum=maximum)
        ),
        "relativePosition": relative,
    }


def _clean_action(
    action: dict[str, object], kinds: list[object]
) -> tuple[dict[str, object], dict[str, object] | None]:
    kind = action.get("kind")
    if kind not in kinds:
        _reject("action kind is not in the closed vocabulary")
    if kind == "fill":
        value = action.get("value")
        if not isinstance(value, dict) or set(value) != {"parameter"}:
            _reject("fill actions may only reference a runtime parameter")
        return {"kind": "fill", "value": value}, None
    if kind == "press_key":
        return {"kind": "press_key", "key": action.get("key")}, None
    if kind == "scroll":
        return {"kind": "scroll", "direction": action.get("direction")}, None
    return {"kind": kind}, None


def clean_trajectory(raw: object, *, with_metadata: bool = False) -> dict[str, object]:
    vocab = contract()
    limits = vocab["limits"]
    assert isinstance(limits, dict)
    maximum = int(limits["maxTextCharacters"])
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        _reject("trajectory schemaVersion must be 1")
    account = raw.get("account")
    if not isinstance(account, dict) or account.get("loggedIn") is not True:
        _reject("a trajectory must come from a logged in session")
    domain = raw.get("domain")
    if not isinstance(domain, str) or not domain:
        _reject("a trajectory must name its domain")
    if raw.get("platform") not in _seq(vocab["platforms"]):
        _reject("platform is not in the closed vocabulary")
    if raw.get("language") not in _seq(vocab["languages"]):
        _reject("language is not in the closed vocabulary")
    viewport = raw.get("viewport")
    if not isinstance(viewport, dict):
        _reject("a trajectory must carry a viewport")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= _MAX_ACTIONS:
        _reject("a trajectory must carry a bounded, non-empty action list")

    entry_path = _path_of(raw.get("entryUrl"), domain, "entry url")
    max_external = int(limits["maxExternalSteps"])

    steps: list[dict[str, object]] = []
    goal_names: list[str] = []
    click_evidence: list[dict[str, object]] = []
    external_count = 0
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            _reject("each action must be an object")
        external = action.get("external")
        if not isinstance(external, bool):
            _reject("each action must declare whether it is external")
        target = action.get("target")
        goal = _clean_goal(
            target, _seq(vocab["goalRoles"]), _seq(vocab["relativePositions"]), maximum
        )
        goal_names.append(str(goal["name"]))
        cleaned_action, _ = _clean_action(action, _seq(vocab["actionKinds"]))
        # A resulting URL, when present, must stay on-domain — a redirect to
        # another host is exactly the drift a stored skill must never encode.
        postconditions: list[dict[str, object]] = []
        resulting_url = action.get("resultingUrl")
        if resulting_url is not None:
            postconditions.append(
                {"kind": "url_matches", "pattern": _path_of(resulting_url, domain, "action url")}
            )
        resulting_visible = action.get("resultingVisible")
        if isinstance(resulting_visible, dict):
            postconditions.append(
                {
                    "kind": "element_visible",
                    "role": resulting_visible.get("role"),
                    "name": _clean_text(
                        resulting_visible.get("name"), "resulting element name", maximum=maximum
                    ),
                }
            )
        if external:
            external_count += 1
            if isinstance(target, dict) and isinstance(target.get("x"), int) and isinstance(
                target.get("y"), int
            ):
                click_evidence.append(
                    {
                        "kind": "click_point_v1",
                        "stepIndex": index,
                        "x": target["x"],
                        "y": target["y"],
                    }
                )
        steps.append(
            {
                "index": index,
                "goal": goal,
                "action": cleaned_action,
                "preconditions": [],
                "postconditions": postconditions,
                "timeoutSeconds": 60 if external else 20,
                "external": external,
                "checkpoint": False,  # assigned below, needs the full sequence
            }
        )
    if external_count > max_external:
        _reject("trajectory has more external actions than the boundary allows")

    # Checkpoints are the points a failed replay may safely resume from: the
    # entry step, plus the last internal step before each external one — the
    # final safe point before an irreversible action. Marking only step 1 made
    # SA-05's resume_from permanently 1, i.e. a full re-run every time
    # (REVIEW-2026-08-06 SA#9).
    for position, step in enumerate(steps):
        next_is_external = position + 1 < len(steps) and bool(
            steps[position + 1]["external"]
        )
        step["checkpoint"] = step["index"] == 1 or (
            next_is_external and not step["external"]
        )

    raw_evidence = raw.get("successEvidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        _reject("a trajectory must carry success evidence")
    success_evidence: list[dict[str, object]] = []
    for item in raw_evidence:
        if not isinstance(item, dict) or item.get("kind") != "url_matches":
            _reject("only url_matches success evidence is understood")
        success_evidence.append(
            {"kind": "url_matches", "pattern": _path_of(item.get("url"), domain, "evidence url")}
        )
    success_evidence.extend(click_evidence)

    fingerprint = hashlib.sha256(
        "\0".join(
            [domain, entry_path, *goal_names]
        ).encode("utf-8")
    ).hexdigest()

    document = {
        "schemaVersion": 1,
        "skillId": _deterministic_uuid(fingerprint),
        "version": 1,
        "parentVersion": None,
        "platform": raw["platform"],
        "domain": domain,
        "pathPattern": entry_path,
        "entryFingerprint": {"kind": "dom_outline_v1", "sha256": fingerprint},
        "language": raw["language"],
        "viewport": {"width": viewport.get("width"), "height": viewport.get("height")},
        "riskLevel": "high" if external_count else "medium",
        "sideEffectBoundary": {"maxExternalSteps": max_external},
        "steps": steps,
        "successEvidence": success_evidence,
    }
    if not with_metadata:
        return document
    return {
        "skill": document,
        "metadata": {"account": account.get("handle"), "domain": domain},
    }


def _deterministic_uuid(fingerprint: str) -> str:
    """A canonical UUIDv4 derived from the fingerprint, so the same trajectory
    yields the same candidate id without a clock or randomness."""
    raw = list(bytes.fromhex(fingerprint)[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    digest = bytes(raw).hex()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


__all__ = ["TrajectoryRejected", "clean_trajectory"]
