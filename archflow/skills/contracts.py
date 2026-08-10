"""Provider-neutral contracts for detached, state-responsive Skills.

The contracts in this module deliberately stop at advice or an exact-base
proposal.  They contain no persistence, promotion, validation-waiver, or
external-world authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping

from archflow.state.decision_operator import DecisionOperator
from archflow.state.design_state import ContextSlice
from archflow.state.operational_state import require_logical_ref


_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 1024
_MAX_TEXT = 100_000


class SkillContractError(ValueError):
    """A Skill contract is malformed or has crossed its authority boundary."""


class ToolKind(StrEnum):
    CLI = "cli"
    RETRIEVAL = "retrieval"
    MCP = "mcp"


class ToolAccess(StrEnum):
    READ_ONLY = "read_only"
    CANDIDATE_MUTATION = "candidate_mutation"


class SkillSideEffect(StrEnum):
    READ_ONLY = "read_only"
    CANDIDATE_ONLY = "candidate_only"


class SkillAuthority(StrEnum):
    ADVICE = "advice"
    PROPOSAL = "proposal"


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SkillContractError("value must be canonical JSON data") from exc


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, field: str, *, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillContractError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise SkillContractError(f"{field} exceeds {maximum} characters")
    return value


def _identifier(value: object, field: str) -> str:
    text = _text(value, field, maximum=63)
    if _ID.fullmatch(text) is None:
        raise SkillContractError(
            f"{field} must use lowercase letters, digits, and single hyphens"
        )
    return text


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    lowered = value.lower()
    if len(lowered) != 64 or any(char not in _HEX for char in lowered):
        raise SkillContractError(f"{field} must be a SHA-256 digest")
    return lowered


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise SkillContractError(f"{field} exceeds bounded item count")
    return value


def _strings(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    items = _tuple(value, field)
    if not allow_empty and not items:
        raise SkillContractError(f"{field} cannot be empty")
    for item in items:
        _text(item, f"{field} item", maximum=512)
    if len(items) != len(set(items)):
        raise SkillContractError(f"{field} contains duplicates")
    return items


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(
    payload: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(payload) != expected:
        missing = sorted(expected - set(payload))
        extra = sorted(set(payload) - expected)
        raise SkillContractError(
            f"{field} schema drifted; missing={missing}, extra={extra}"
        )


@dataclass(frozen=True, slots=True)
class SkillBudget:
    timeout_seconds: float
    max_output_bytes: int
    max_attempts: int

    def __post_init__(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds must be numeric")
        if not 0 < float(self.timeout_seconds) <= 60:
            raise SkillContractError(
                "timeout_seconds must be greater than zero and at most 60"
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if (
            type(self.max_output_bytes) is not int
            or not 128 <= self.max_output_bytes <= 1_000_000
        ):
            raise SkillContractError(
                "max_output_bytes must be between 128 and 1000000"
            )
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise SkillContractError("max_attempts must be between 1 and 3")

    def to_dict(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, value: object) -> SkillBudget:
        payload = _mapping(value, "skill budget")
        _exact(
            payload,
            {"timeout_seconds", "max_output_bytes", "max_attempts"},
            "skill budget",
        )
        return cls(
            timeout_seconds=payload["timeout_seconds"],
            max_output_bytes=payload["max_output_bytes"],
            max_attempts=payload["max_attempts"],
        )


@dataclass(frozen=True, slots=True)
class ToolRequirement:
    tool_id: str
    kind: ToolKind
    access: ToolAccess

    def __post_init__(self) -> None:
        _text(self.tool_id, "tool_id", maximum=128)
        if not isinstance(self.kind, ToolKind):
            raise TypeError("kind must be a ToolKind")
        if not isinstance(self.access, ToolAccess):
            raise TypeError("access must be a ToolAccess")
        if (
            self.access is ToolAccess.CANDIDATE_MUTATION
            and self.kind is not ToolKind.MCP
        ):
            raise SkillContractError(
                "candidate mutation may only be declared for an MCP tool"
            )

    @property
    def grant_ref(self) -> str:
        return f"{self.kind.value}:{self.tool_id}:{self.access.value}"

    def to_dict(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "kind": self.kind.value,
            "access": self.access.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> ToolRequirement:
        payload = _mapping(value, "tool requirement")
        _exact(payload, {"tool_id", "kind", "access"}, "tool requirement")
        try:
            kind = ToolKind(payload["kind"])
            access = ToolAccess(payload["access"])
        except (TypeError, ValueError) as exc:
            raise SkillContractError("tool requirement enum is invalid") from exc
        return cls(tool_id=payload["tool_id"], kind=kind, access=access)


@dataclass(frozen=True, slots=True)
class SkillSpec:
    skill_id: str
    version: str
    package_digest: str
    description: str
    instruction_entrypoint: str
    reference_entrypoints: tuple[str, ...]
    resource_entrypoints: tuple[str, ...]
    input_schema: str
    output_schemas: tuple[str, ...]
    obligation_topics: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    admissible_phases: tuple[str, ...]
    tool_requirements: tuple[ToolRequirement, ...]
    side_effect_class: SkillSideEffect
    authority_ceiling: SkillAuthority
    budget: SkillBudget

    SCHEMA = "SkillSpec@1"
    SUPPORTED_INPUT_SCHEMA = "DetachedSkillContext@1"
    SUPPORTED_OUTPUT_SCHEMAS = frozenset(
        {"SkillAdvice@1", "SkillProposal@1"}
    )

    def __post_init__(self) -> None:
        _identifier(self.skill_id, "skill_id")
        if not isinstance(self.version, str) or _SEMVER.fullmatch(
            self.version
        ) is None:
            raise SkillContractError("version must be semantic version text")
        _sha256(self.package_digest, "package_digest")
        _text(self.description, "description", maximum=2048)
        _text(
            self.instruction_entrypoint,
            "instruction_entrypoint",
            maximum=512,
        )
        _strings(
            self.reference_entrypoints,
            "reference_entrypoints",
            allow_empty=True,
        )
        _strings(
            self.resource_entrypoints,
            "resource_entrypoints",
            allow_empty=True,
        )
        _text(self.input_schema, "input_schema", maximum=128)
        if self.input_schema != self.SUPPORTED_INPUT_SCHEMA:
            raise SkillContractError("unsupported Skill input schema")
        output_schemas = _strings(self.output_schemas, "output_schemas")
        if not set(output_schemas) <= self.SUPPORTED_OUTPUT_SCHEMAS:
            raise SkillContractError("unsupported Skill output schema")
        _strings(self.obligation_topics, "obligation_topics")
        _strings(
            self.evidence_requirements,
            "evidence_requirements",
            allow_empty=True,
        )
        _strings(self.admissible_phases, "admissible_phases")
        _tuple(self.tool_requirements, "tool_requirements")
        if any(
            not isinstance(item, ToolRequirement)
            for item in self.tool_requirements
        ):
            raise TypeError(
                "tool_requirements must contain ToolRequirement values"
            )
        grant_refs = tuple(item.grant_ref for item in self.tool_requirements)
        if len(grant_refs) != len(set(grant_refs)):
            raise SkillContractError("tool_requirements contains duplicates")
        if not isinstance(self.side_effect_class, SkillSideEffect):
            raise TypeError("side_effect_class must be a SkillSideEffect")
        if not isinstance(self.authority_ceiling, SkillAuthority):
            raise TypeError("authority_ceiling must be a SkillAuthority")
        if not isinstance(self.budget, SkillBudget):
            raise TypeError("budget must be a SkillBudget")
        has_mutation_tool = any(
            item.access is ToolAccess.CANDIDATE_MUTATION
            for item in self.tool_requirements
        )
        if self.side_effect_class is SkillSideEffect.READ_ONLY and has_mutation_tool:
            raise SkillContractError(
                "read-only Skill cannot request candidate mutation"
            )
        if (
            self.authority_ceiling is SkillAuthority.ADVICE
            and "SkillProposal@1" in self.output_schemas
        ):
            raise SkillContractError(
                "advice-only Skill cannot declare proposal output"
            )

    @property
    def identity(self) -> tuple[str, str]:
        return (self.skill_id, self.version)

    @property
    def content_entrypoints(self) -> tuple[str, ...]:
        values = (
            self.instruction_entrypoint,
            *self.reference_entrypoints,
            *self.resource_entrypoints,
        )
        if len(values) != len(set(values)):
            raise SkillContractError("Skill content entrypoints overlap")
        return values

    def with_package_digest(self, package_digest: str) -> SkillSpec:
        return replace(self, package_digest=package_digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "skill_id": self.skill_id,
            "version": self.version,
            "package_digest": self.package_digest,
            "description": self.description,
            "instruction_entrypoint": self.instruction_entrypoint,
            "reference_entrypoints": list(self.reference_entrypoints),
            "resource_entrypoints": list(self.resource_entrypoints),
            "input_schema": self.input_schema,
            "output_schemas": list(self.output_schemas),
            "obligation_topics": list(self.obligation_topics),
            "evidence_requirements": list(self.evidence_requirements),
            "admissible_phases": list(self.admissible_phases),
            "tool_requirements": [
                item.to_dict() for item in self.tool_requirements
            ],
            "side_effect_class": self.side_effect_class.value,
            "authority_ceiling": self.authority_ceiling.value,
            "budget": self.budget.to_dict(),
        }

    def digest_payload(self) -> dict[str, object]:
        payload = self.to_dict()
        payload["package_digest"] = "0" * 64
        return payload

    @classmethod
    def from_dict(cls, value: object) -> SkillSpec:
        payload = _mapping(value, "SkillSpec")
        expected = {
            "schema",
            "skill_id",
            "version",
            "package_digest",
            "description",
            "instruction_entrypoint",
            "reference_entrypoints",
            "resource_entrypoints",
            "input_schema",
            "output_schemas",
            "obligation_topics",
            "evidence_requirements",
            "admissible_phases",
            "tool_requirements",
            "side_effect_class",
            "authority_ceiling",
            "budget",
        }
        _exact(payload, expected, "SkillSpec")
        if payload["schema"] != cls.SCHEMA:
            raise SkillContractError("unsupported SkillSpec schema")

        def text_tuple(field: str) -> tuple[str, ...]:
            raw = payload[field]
            if not isinstance(raw, list) or any(
                not isinstance(item, str) for item in raw
            ):
                raise TypeError(f"{field} must be a string list")
            return tuple(raw)

        tools = payload["tool_requirements"]
        if not isinstance(tools, list):
            raise TypeError("tool_requirements must be a list")
        try:
            side_effect = SkillSideEffect(payload["side_effect_class"])
            authority = SkillAuthority(payload["authority_ceiling"])
        except (TypeError, ValueError) as exc:
            raise SkillContractError("Skill authority enum is invalid") from exc
        return cls(
            skill_id=payload["skill_id"],
            version=payload["version"],
            package_digest=payload["package_digest"],
            description=payload["description"],
            instruction_entrypoint=payload["instruction_entrypoint"],
            reference_entrypoints=text_tuple("reference_entrypoints"),
            resource_entrypoints=text_tuple("resource_entrypoints"),
            input_schema=payload["input_schema"],
            output_schemas=text_tuple("output_schemas"),
            obligation_topics=text_tuple("obligation_topics"),
            evidence_requirements=text_tuple("evidence_requirements"),
            admissible_phases=text_tuple("admissible_phases"),
            tool_requirements=tuple(
                ToolRequirement.from_dict(item) for item in tools
            ),
            side_effect_class=side_effect,
            authority_ceiling=authority,
            budget=SkillBudget.from_dict(payload["budget"]),
        )


@dataclass(frozen=True, slots=True)
class AvailableTool:
    tool_id: str
    kind: ToolKind
    access: ToolAccess

    def __post_init__(self) -> None:
        ToolRequirement(self.tool_id, self.kind, self.access)

    @property
    def grant_ref(self) -> str:
        return f"{self.kind.value}:{self.tool_id}:{self.access.value}"

    def to_dict(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "kind": self.kind.value,
            "access": self.access.value,
        }


@dataclass(frozen=True, slots=True)
class DetachedSkillContext:
    context_digest: str
    target_node_ref: str
    target_state_digest: str
    phase: str
    context_payload_json: str
    obligation_topics: tuple[tuple[str, str], ...]
    evidence_kinds: tuple[str, ...]
    tool_grants: tuple[AvailableTool, ...]

    SCHEMA = "DetachedSkillContext@1"

    def __post_init__(self) -> None:
        _sha256(self.context_digest, "context_digest")
        require_logical_ref(self.target_node_ref, "target_node_ref")
        _sha256(self.target_state_digest, "target_state_digest")
        _text(self.phase, "phase", maximum=128)
        payload = json.loads(self.context_payload_json)
        if canonical_json(payload) != self.context_payload_json:
            raise SkillContractError(
                "context_payload_json must be canonical JSON"
            )
        if not isinstance(payload, dict) or payload.get("schema") != (
            ContextSlice.SCHEMA
        ):
            raise SkillContractError("detached payload is not ContextSlice@1")
        forbidden = {
            "raw_history",
            "transcript",
            "canonical_writer",
            "committer",
            "repository",
            "workspace_path",
            "world_handle",
        }
        overlap = forbidden & _nested_keys(payload)
        if overlap:
            raise SkillContractError(
                f"detached context contains forbidden handles: {sorted(overlap)}"
            )
        _tuple(self.obligation_topics, "obligation_topics")
        ids: list[str] = []
        for item in self.obligation_topics:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("obligation_topics items must be pairs")
            obligation_id, topic = item
            _text(obligation_id, "obligation id", maximum=128)
            _text(topic, "obligation topic", maximum=128)
            ids.append(obligation_id)
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise SkillContractError(
                "obligation_topics must be unique and sorted by id"
            )
        _strings(self.evidence_kinds, "evidence_kinds", allow_empty=True)
        _tuple(self.tool_grants, "tool_grants")
        if any(not isinstance(item, AvailableTool) for item in self.tool_grants):
            raise TypeError("tool_grants must contain AvailableTool values")
        grant_refs = tuple(item.grant_ref for item in self.tool_grants)
        if grant_refs != tuple(sorted(grant_refs)):
            raise SkillContractError("tool_grants must be sorted")

    @classmethod
    def detach(
        cls,
        context: ContextSlice,
        *,
        phase: str,
        obligation_topics: Mapping[str, str],
        evidence_kinds: frozenset[str],
        tool_grants: tuple[AvailableTool, ...],
    ) -> DetachedSkillContext:
        if not isinstance(context, ContextSlice):
            raise TypeError("context must be a ContextSlice")
        expected_ids = {item.obligation_id for item in context.obligations}
        if set(obligation_topics) != expected_ids:
            raise SkillContractError(
                "obligation topics must exactly cover the current context"
            )
        if not isinstance(evidence_kinds, frozenset):
            raise TypeError("evidence_kinds must be a frozenset")
        return cls(
            context_digest=context.context_digest,
            target_node_ref=context.target_node_ref,
            target_state_digest=context.target_state_digest,
            phase=phase,
            context_payload_json=canonical_json(context.to_dict()),
            obligation_topics=tuple(sorted(obligation_topics.items())),
            evidence_kinds=tuple(sorted(evidence_kinds)),
            tool_grants=tuple(
                sorted(tool_grants, key=lambda item: item.grant_ref)
            ),
        )

    @property
    def obligation_refs(self) -> tuple[str, ...]:
        return tuple(
            f"obligation:{obligation_id}"
            for obligation_id, _ in self.obligation_topics
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context_digest": self.context_digest,
            "target_node_ref": self.target_node_ref,
            "target_state_digest": self.target_state_digest,
            "phase": self.phase,
            "context": json.loads(self.context_payload_json),
            "obligation_topics": [
                {"obligation_id": obligation_id, "topic": topic}
                for obligation_id, topic in self.obligation_topics
            ],
            "evidence_kinds": list(self.evidence_kinds),
            "tool_grants": [item.to_dict() for item in self.tool_grants],
        }


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key).casefold())
            keys.update(_nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_nested_keys(child))
    return keys


@dataclass(frozen=True, slots=True)
class SkillAdvice:
    advice_id: str
    summary: str
    responds_to_refs: tuple[str, ...]
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    SCHEMA = "SkillAdvice@1"

    def __post_init__(self) -> None:
        _identifier(self.advice_id, "advice_id")
        _text(self.summary, "summary")
        _strings(self.responds_to_refs, "responds_to_refs")
        _strings(self.findings, "findings", allow_empty=True)
        _strings(self.evidence_refs, "evidence_refs", allow_empty=True)
        for ref in (*self.responds_to_refs, *self.evidence_refs):
            require_logical_ref(ref, "Skill advice ref")

    @property
    def advice_digest(self) -> str:
        return digest_json(self.to_dict())

    @property
    def ref(self) -> str:
        return f"skill-advice:{self.advice_id}:{self.advice_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "advice_id": self.advice_id,
            "summary": self.summary,
            "responds_to_refs": list(self.responds_to_refs),
            "findings": list(self.findings),
            "evidence_refs": list(self.evidence_refs),
            "read_only": True,
            "verified": False,
            "accepted": False,
            "canonical": False,
        }


@dataclass(frozen=True, slots=True)
class SkillProposal:
    proposal_id: str
    operator: DecisionOperator
    responds_to_refs: tuple[str, ...]
    rationale: str
    evidence_refs: tuple[str, ...] = ()

    SCHEMA = "SkillProposal@1"

    def __post_init__(self) -> None:
        _identifier(self.proposal_id, "proposal_id")
        if not isinstance(self.operator, DecisionOperator):
            raise TypeError("operator must be a DecisionOperator")
        _strings(self.responds_to_refs, "responds_to_refs")
        _text(self.rationale, "rationale")
        _strings(self.evidence_refs, "evidence_refs", allow_empty=True)
        for ref in (*self.responds_to_refs, *self.evidence_refs):
            require_logical_ref(ref, "Skill proposal ref")

    @property
    def proposal_digest(self) -> str:
        return digest_json(self.to_dict())

    @property
    def ref(self) -> str:
        return f"skill-proposal:{self.proposal_id}:{self.proposal_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "proposal_id": self.proposal_id,
            "operator": self.operator.to_dict(),
            "responds_to_refs": list(self.responds_to_refs),
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "candidate_only": True,
            "verified": False,
            "accepted": False,
            "canonical": False,
            "world_write": False,
        }


SkillOutput = SkillAdvice | SkillProposal


@dataclass(frozen=True, slots=True)
class SkillInvocationInput:
    package_id: str
    package_version: str
    package_digest: str
    instructions: str
    references: tuple[tuple[str, str], ...]
    context: DetachedSkillContext

    SCHEMA = "SkillInvocationInput@1"

    def __post_init__(self) -> None:
        _identifier(self.package_id, "package_id")
        if _SEMVER.fullmatch(self.package_version) is None:
            raise SkillContractError("package_version must be semantic version")
        _sha256(self.package_digest, "package_digest")
        _text(self.instructions, "instructions")
        _tuple(self.references, "references")
        paths: list[str] = []
        for item in self.references:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("references must contain path/text pairs")
            path, text = item
            _text(path, "reference path", maximum=512)
            _text(text, "reference content")
            paths.append(path)
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise SkillContractError("references must be unique and sorted")
        if not isinstance(self.context, DetachedSkillContext):
            raise TypeError("context must be DetachedSkillContext")

    @property
    def input_digest(self) -> str:
        return digest_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "package_digest": self.package_digest,
            "instructions": self.instructions,
            "references": [
                {"path": path, "content": content}
                for path, content in self.references
            ],
            "context": self.context.to_dict(),
        }
