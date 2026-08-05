"""SA-03: compile, audit and sign a candidate skill; store it immutably.

Publishing is a gate, not a setter. A candidate is signed only after it passes,
in order: the SA-01 schema (``parse_automation_skill``), a lint pass (structural
rules a schema-valid document can still violate), and an explicit human approval
record. The signature is Ed25519 over the canonical bytes of the skill plus the
approval, so neither can be altered after the fact.

Two escalation paths are closed by construction:

* the model cannot self-approve — ``sign_candidate`` refuses without an approval
  record, and the approval rides *outside* the skill document, so a skill that
  carries its own ``approved`` flag is just an unknown key the schema rejects;
* the model cannot overwrite a live version — ``SkillRegistry`` is append-only
  per ``(skillId, version)``, and a new version must name the previous one as
  ``parentVersion``. v1 is never deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from automation_tool.executor.automation_skill import (
    AutomationSkill,
    AutomationSkillRejected,
    parse_automation_skill,
)

_SIGNATURE_DOMAIN = b"automation-tool.automation-skill.v1\0"
_APPROVAL_KEYS = frozenset({"reviewer", "decision", "reviewedAt"})


class SkillPublicationRejected(ValueError):
    """The candidate cannot be published as it stands."""


def _reject(message: str) -> NoReturn:
    raise SkillPublicationRejected(message)


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _schema_ok(candidate: object) -> AutomationSkill:
    try:
        return parse_automation_skill(candidate)
    except AutomationSkillRejected as error:
        raise SkillPublicationRejected(f"schema rejected the candidate: {error}") from error


def _lint(skill: AutomationSkill) -> None:
    # Structural rules the schema allows but a publishable skill must not break.
    if not any(step.checkpoint for step in skill.steps):
        _reject("a publishable skill must declare at least one checkpoint")
    if skill.external_step_count and not any(
        evidence.kind == "url_matches" for evidence in skill.success_evidence
    ):
        _reject("a skill with an external step must prove its outcome by URL")


def _valid_approval(approval: object) -> dict[str, object]:
    if approval is None:
        _reject("publishing needs a human review record")
    if not isinstance(approval, dict) or set(approval) != _APPROVAL_KEYS:
        _reject("the review record is malformed")
    if approval.get("decision") != "approved":
        _reject("the review record does not approve this candidate")
    return approval


def sign_candidate(
    candidate: object, *, approval: object, seed: bytes
) -> dict[str, object]:
    """Return a signed, publishable record for a reviewed candidate skill."""
    skill = _schema_ok(candidate)
    _lint(skill)
    review = _valid_approval(approval)
    if len(seed) != 32:
        _reject("the signing seed must be 32 bytes")
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    assert isinstance(candidate, dict)
    payload = _SIGNATURE_DOMAIN + _canonical(
        {"skill": candidate, "approval": review}
    )
    signature = private_key.sign(payload)
    public_key = private_key.public_key().public_bytes_raw()
    return {
        "skill": candidate,
        "approval": review,
        "publicKey": public_key.hex(),
        "signature": signature.hex(),
    }


@dataclass(frozen=True)
class SignedSkill:
    skill: AutomationSkill
    version: int
    approval: dict[str, object]
    public_key: bytes
    document: dict[str, object]


def verify_signed_skill(signed: object) -> SignedSkill:
    if not isinstance(signed, dict) or set(signed) != {
        "skill",
        "approval",
        "publicKey",
        "signature",
    }:
        _reject("the signed skill record is malformed")
    skill = _schema_ok(signed["skill"])
    review = _valid_approval(signed["approval"])
    try:
        public_key_bytes = bytes.fromhex(str(signed["publicKey"]))
        signature = bytes.fromhex(str(signed["signature"]))
    except ValueError as error:
        raise SkillPublicationRejected("signature material is malformed") from error
    payload = _SIGNATURE_DOMAIN + _canonical(
        {"skill": signed["skill"], "approval": review}
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, payload)
    except (InvalidSignature, ValueError) as error:
        raise SkillPublicationRejected("signature does not verify") from error
    return SignedSkill(
        skill=skill,
        version=skill.version,
        approval=review,
        public_key=public_key_bytes,
        document=signed,
    )


class SkillRegistry:
    """An append-only store: one immutable record per (skillId, version)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], SignedSkill] = {}

    def publish(self, signed: object) -> SignedSkill:
        record = verify_signed_skill(signed)
        key = (record.skill.skill_id, record.version)
        if key in self._records:
            _reject("this version is already published and records are immutable")
        if record.version == 1:
            if record.skill.parent_version is not None:
                _reject("a first version must not name a parent")
        else:
            parent = record.skill.parent_version
            if parent is None or (record.skill.skill_id, parent) not in self._records:
                _reject("a new version must name a published parent version")
        self._records[key] = record
        return record

    def at(self, skill_id: str, version: int) -> SignedSkill:
        try:
            return self._records[(skill_id, version)]
        except KeyError:
            _reject("no such published skill version")

    def live(self, skill_id: str) -> SignedSkill:
        versions = [
            record for (identifier, _), record in self._records.items() if identifier == skill_id
        ]
        if not versions:
            _reject("no such published skill")
        return max(versions, key=lambda record: record.version)


__all__ = [
    "SignedSkill",
    "SkillPublicationRejected",
    "SkillRegistry",
    "sign_candidate",
    "verify_signed_skill",
]
