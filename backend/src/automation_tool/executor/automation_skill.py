"""SA-01: the declarative AutomationSkill document and its fail-closed gate.

Self-healing compiles one successful Browser Use trajectory into this
restricted, auditable JSON — never into code. What the roadmap forbids is made
structurally impossible rather than discouraged:

* no field anywhere accepts a CSS selector, JavaScript, shell text or a
  screenshot — every free-text field is bounded and scanned, and there is no
  base64/image field to smuggle one into;
* raw coordinates exist only under ``successEvidence`` (``click_point_v1``);
  goals are semantic (role / name / near-text / relative position) only;
* ``fill`` values may only reference runtime parameters — a literal would put
  user prose into a stored, versioned, replayed document;
* the closed vocabularies live in ``contracts/browser-use/automation-skill
  .v1.json`` so SA-02..07 read the same single source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, NoReturn
from uuid import UUID

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
CONTRACT_PATH: Final = _REPOSITORY_ROOT / "contracts/browser-use/automation-skill.v1.json"

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_HOSTNAME: Final = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PATH_PATTERN: Final = re.compile(r"^/[A-Za-z0-9/_.-]{0,200}$")
_PARAMETER_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# 语义目标与附近文字里绝不该出现的东西：CSS 选择器、JS、Shell、密钥词。
# 判据 fail-closed：真实页面的按钮名不含这些字符与词；含了就不收。
# 公开导出：SA-02 清洗器复用同一套禁止集，避免两份定义各自漂移。
FORBIDDEN_TEXT_CHARACTERS: Final = frozenset('<>{}$`;|&\\#"')
FORBIDDEN_TEXT_WORDS: Final = (
    "javascript:",
    "data:",
    "cookie",
    "token",
    "secret",
    "password",
    "eval(",
    "script",
)


class AutomationSkillRejected(ValueError):
    """The document is not one the compiler ever produces."""


@lru_cache(maxsize=1)
def contract() -> dict[str, object]:
    document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise AutomationSkillRejected("contract schemaVersion drifted")
    return document


def _reject(message: str) -> NoReturn:
    raise AutomationSkillRejected(message)


def _exact_keys(value: object, keys: frozenset[str], where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject(f"{where} must be an object")
    if set(value) != keys:
        _reject(f"{where} carries unknown or missing keys")
    return value


def _choice(value: object, allowed: object, where: str) -> str:
    """Narrow `object` to a `str` that is one of a closed vocabulary."""
    if not isinstance(value, str) or not isinstance(allowed, list) or value not in allowed:
        _reject(f"{where} is not in the closed vocabulary")
    return value


def _flag(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        _reject(f"{where} must be a boolean")
    return value


def _bounded_int(value: object, where: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        _reject(f"{where} is out of bounds")
    return value


def _limits() -> dict[str, int]:
    limits = contract()["limits"]
    assert isinstance(limits, dict)
    return {key: int(value) for key, value in limits.items()}


def _safe_text(value: object, where: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        _reject(f"{where} must be bounded text")
    if any(character in FORBIDDEN_TEXT_CHARACTERS for character in value):
        _reject(f"{where} contains forbidden characters")
    lowered = value.lower()
    if any(word in lowered for word in FORBIDDEN_TEXT_WORDS):
        _reject(f"{where} contains forbidden words")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _reject(f"{where} contains forbidden control characters")
    return value


def _optional_safe_text(value: object, where: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _safe_text(value, where, maximum=maximum)


@dataclass(frozen=True)
class SkillGoal:
    role: str
    name: str
    near_text: str | None
    relative_position: str | None


@dataclass(frozen=True)
class SkillCondition:
    kind: str
    role: str | None
    name: str | None
    pattern: str | None


@dataclass(frozen=True)
class SkillAction:
    kind: str
    parameter: str | None
    key: str | None
    direction: str | None


@dataclass(frozen=True)
class SkillStep:
    index: int
    goal: SkillGoal
    action: SkillAction
    preconditions: tuple[SkillCondition, ...]
    postconditions: tuple[SkillCondition, ...]
    timeout_seconds: int
    external: bool
    checkpoint: bool


@dataclass(frozen=True)
class SkillEvidence:
    kind: str
    pattern: str | None
    role: str | None
    name: str | None
    step_index: int | None
    x: int | None
    y: int | None


@dataclass(frozen=True)
class AutomationSkill:
    skill_id: str
    version: int
    parent_version: int | None
    platform: str
    domain: str
    path_pattern: str
    fingerprint_kind: str
    fingerprint_sha256: str
    language: str
    viewport_width: int
    viewport_height: int
    risk_level: str
    max_external_steps: int
    steps: tuple[SkillStep, ...]
    success_evidence: tuple[SkillEvidence, ...]

    def __repr__(self) -> str:
        return f"AutomationSkill(skill_id={self.skill_id!r}, version={self.version})"

    @property
    def external_step_count(self) -> int:
        return sum(1 for step in self.steps if step.external)


_ROOT_KEYS: Final = frozenset(
    {
        "schemaVersion",
        "skillId",
        "version",
        "parentVersion",
        "platform",
        "domain",
        "pathPattern",
        "entryFingerprint",
        "language",
        "viewport",
        "riskLevel",
        "sideEffectBoundary",
        "steps",
        "successEvidence",
    }
)
_STEP_KEYS: Final = frozenset(
    {
        "index",
        "goal",
        "action",
        "preconditions",
        "postconditions",
        "timeoutSeconds",
        "external",
        "checkpoint",
    }
)
_GOAL_KEYS: Final = frozenset({"role", "name", "nearText", "relativePosition"})


def _path_pattern(value: object, where: str) -> str:
    if not isinstance(value, str) or _PATH_PATTERN.fullmatch(value) is None:
        _reject(f"{where} must be a bounded path pattern")
    return value


def _parse_condition(
    value: object, limits: dict[str, int], vocab: dict[str, object]
) -> SkillCondition:
    if not isinstance(value, dict) or "kind" not in value:
        _reject("condition must declare a kind")
    kind = _choice(value["kind"], vocab["conditionKinds"], "condition kind")
    maximum = limits["maxTextCharacters"]
    if kind in {"element_visible", "element_absent"}:
        record = _exact_keys(value, frozenset({"kind", "role", "name"}), "condition")
        return SkillCondition(
            kind=kind,
            role=_choice(record["role"], vocab["goalRoles"], "condition role"),
            name=_safe_text(record["name"], "condition name", maximum=maximum),
            pattern=None,
        )
    record = _exact_keys(value, frozenset({"kind", "pattern"}), "condition")
    return SkillCondition(
        kind=kind,
        role=None,
        name=None,
        pattern=_path_pattern(record["pattern"], "condition pattern"),
    )


def _parse_action(value: object, vocab: dict[str, object], limits: dict[str, int]) -> SkillAction:
    if not isinstance(value, dict) or "kind" not in value:
        _reject("action must declare a kind")
    kind = _choice(value["kind"], vocab["actionKinds"], "action kind")
    if kind == "fill":
        record = _exact_keys(value, frozenset({"kind", "value"}), "action")
        reference = record["value"]
        # 用户正文不入库：填充值只能是运行时参数引用。
        if not isinstance(reference, dict) or set(reference) != {"parameter"}:
            _reject("fill values may only reference a runtime parameter")
        name = reference["parameter"]
        if not isinstance(name, str) or _PARAMETER_NAME.fullmatch(name) is None:
            _reject("fill values may only reference a runtime parameter")
        return SkillAction(kind=kind, parameter=name, key=None, direction=None)
    if kind == "press_key":
        record = _exact_keys(value, frozenset({"kind", "key"}), "action")
        return SkillAction(
            kind=kind,
            parameter=None,
            key=_choice(record["key"], vocab["pressKeys"], "press key"),
            direction=None,
        )
    if kind == "scroll":
        record = _exact_keys(value, frozenset({"kind", "direction"}), "action")
        return SkillAction(
            kind=kind,
            parameter=None,
            key=None,
            direction=_choice(record["direction"], vocab["scrollDirections"], "scroll direction"),
        )
    _exact_keys(value, frozenset({"kind"}), "action")
    return SkillAction(kind=kind, parameter=None, key=None, direction=None)


def _parse_step(value: object, vocab: dict[str, object], limits: dict[str, int]) -> SkillStep:
    record = _exact_keys(value, _STEP_KEYS, "step (unknown keys)")
    goal = _exact_keys(record["goal"], _GOAL_KEYS, "goal (unknown keys)")
    maximum = limits["maxTextCharacters"]
    relative_raw = goal["relativePosition"]
    relative = (
        None
        if relative_raw is None
        else _choice(relative_raw, vocab["relativePositions"], "goal relative position")
    )
    index = _bounded_int(record["index"], "step index", minimum=1, maximum=limits["maxSteps"])
    timeout = _bounded_int(
        record["timeoutSeconds"],
        "step timeout",
        minimum=limits["timeoutSecondsMinimum"],
        maximum=limits["timeoutSecondsMaximum"],
    )
    external = _flag(record["external"], "step external")
    checkpoint = _flag(record["checkpoint"], "step checkpoint")
    conditions: dict[str, tuple[SkillCondition, ...]] = {}
    for side in ("preconditions", "postconditions"):
        values = record[side]
        if not isinstance(values, list) or len(values) > limits["maxConditionsPerStep"]:
            _reject(f"step {side} must be a bounded list")
        conditions[side] = tuple(_parse_condition(item, limits, vocab) for item in values)
    return SkillStep(
        index=index,
        goal=SkillGoal(
            role=_choice(goal["role"], vocab["goalRoles"], "goal role"),
            name=_safe_text(goal["name"], "goal name", maximum=maximum),
            near_text=_optional_safe_text(
                goal["nearText"], "goal near-text", maximum=maximum
            ),
            relative_position=relative,
        ),
        action=_parse_action(record["action"], vocab, limits),
        preconditions=conditions["preconditions"],
        postconditions=conditions["postconditions"],
        timeout_seconds=timeout,
        external=external,
        checkpoint=checkpoint,
    )


def _parse_evidence(
    value: object, vocab: dict[str, object], limits: dict[str, int], step_count: int
) -> SkillEvidence:
    if not isinstance(value, dict) or "kind" not in value:
        _reject("evidence must declare a kind")
    kind = _choice(value["kind"], vocab["evidenceKinds"], "evidence kind")
    if kind == "url_matches":
        record = _exact_keys(value, frozenset({"kind", "pattern"}), "evidence")
        return SkillEvidence(
            kind=kind,
            pattern=_path_pattern(record["pattern"], "evidence pattern"),
            role=None,
            name=None,
            step_index=None,
            x=None,
            y=None,
        )
    if kind == "element_visible":
        record = _exact_keys(value, frozenset({"kind", "role", "name"}), "evidence")
        return SkillEvidence(
            kind=kind,
            pattern=None,
            role=_choice(record["role"], vocab["goalRoles"], "evidence role"),
            name=_safe_text(
                record["name"], "evidence name", maximum=limits["maxTextCharacters"]
            ),
            step_index=None,
            x=None,
            y=None,
        )
    # click_point_v1：原始坐标唯一允许存在的位置——作为证据，永不作为目标。
    record = _exact_keys(value, frozenset({"kind", "stepIndex", "x", "y"}), "evidence")
    step_index = _bounded_int(
        record["stepIndex"], "evidence step index", minimum=1, maximum=step_count
    )
    return SkillEvidence(
        kind=kind,
        pattern=None,
        role=None,
        name=None,
        step_index=step_index,
        x=_bounded_int(record["x"], "evidence x", minimum=0, maximum=limits["viewportMaximum"]),
        y=_bounded_int(record["y"], "evidence y", minimum=0, maximum=limits["viewportMaximum"]),
    )


def parse_automation_skill(value: object) -> AutomationSkill:
    vocab = contract()
    limits = _limits()
    record = _exact_keys(value, _ROOT_KEYS, "skill (unknown keys)")
    if record["schemaVersion"] != 1:
        _reject("schemaVersion must be 1")
    skill_id = record["skillId"]
    try:
        parsed = UUID(str(skill_id))
    except (ValueError, TypeError, AttributeError):
        _reject("skill id must be a UUIDv4")
    if parsed.version != 4 or str(parsed) != skill_id:
        _reject("skill id must be a canonical UUIDv4")
    version = _bounded_int(record["version"], "version", minimum=1, maximum=1_000_000)
    parent_raw = record["parentVersion"]
    parent = (
        None
        if parent_raw is None
        else _bounded_int(parent_raw, "parent version", minimum=1, maximum=version - 1)
    )
    platform = _choice(record["platform"], vocab["platforms"], "platform")
    domain = record["domain"]
    if not isinstance(domain, str) or _HOSTNAME.fullmatch(domain) is None:
        _reject("domain must be a hostname")
    path_pattern = _path_pattern(record["pathPattern"], "path pattern")
    fingerprint = _exact_keys(
        record["entryFingerprint"], frozenset({"kind", "sha256"}), "fingerprint"
    )
    fingerprint_kind = _choice(
        fingerprint["kind"], vocab["fingerprintKinds"], "fingerprint kind"
    )
    digest = fingerprint["sha256"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        _reject("fingerprint digest must be a lowercase sha256")
    language = _choice(record["language"], vocab["languages"], "language")
    viewport = _exact_keys(record["viewport"], frozenset({"width", "height"}), "viewport")
    width = _bounded_int(
        viewport["width"], "viewport width",
        minimum=limits["viewportMinimum"], maximum=limits["viewportMaximum"],
    )
    height = _bounded_int(
        viewport["height"], "viewport height",
        minimum=limits["viewportMinimum"], maximum=limits["viewportMaximum"],
    )
    risk_level = _choice(record["riskLevel"], vocab["riskLevels"], "risk level")
    boundary = _exact_keys(
        record["sideEffectBoundary"], frozenset({"maxExternalSteps"}), "boundary"
    )
    max_external = _bounded_int(
        boundary["maxExternalSteps"], "external side-effect boundary",
        minimum=0, maximum=limits["maxExternalSteps"],
    )
    steps_value = record["steps"]
    if (
        not isinstance(steps_value, list)
        or not 1 <= len(steps_value) <= limits["maxSteps"]
    ):
        _reject("steps must be a bounded, non-empty list")
    steps = tuple(_parse_step(item, vocab, limits) for item in steps_value)
    for position, step in enumerate(steps, start=1):
        if step.index != position:
            _reject("step indexes must be consecutive from one")
    external_count = sum(1 for step in steps if step.external)
    if external_count > max_external:
        _reject("more external steps than the declared boundary")
    evidence_value = record["successEvidence"]
    if (
        not isinstance(evidence_value, list)
        or not 1 <= len(evidence_value) <= limits["maxEvidenceEntries"]
    ):
        _reject("success evidence must be a bounded, non-empty list")
    evidence = tuple(
        _parse_evidence(item, vocab, limits, len(steps)) for item in evidence_value
    )
    return AutomationSkill(
        skill_id=str(skill_id),
        version=version,
        parent_version=parent,
        platform=platform,
        domain=domain,
        path_pattern=path_pattern,
        fingerprint_kind=fingerprint_kind,
        fingerprint_sha256=digest,
        language=language,
        viewport_width=width,
        viewport_height=height,
        risk_level=risk_level,
        max_external_steps=max_external,
        steps=steps,
        success_evidence=evidence,
    )


__all__ = [
    "CONTRACT_PATH",
    "AutomationSkill",
    "AutomationSkillRejected",
    "SkillAction",
    "SkillCondition",
    "SkillEvidence",
    "SkillGoal",
    "SkillStep",
    "contract",
    "parse_automation_skill",
]
