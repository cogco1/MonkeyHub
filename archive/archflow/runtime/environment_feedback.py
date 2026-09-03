"""Compile bounded MCP/environment feedback into Architect-owned obligations.

Tool failure is evidence about an attempted state transition. It is neither a
hard usability verdict nor permission for the runtime to edit a design.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from archive.archflow.capabilities.experts import (
    ExpertEvidence,
    ExpertObligation,
    ExpertSnapshot,
)
from archflow.state.model import StateRef
from archive.archflow.submission.repair import RepairFinding, RepairObligation, obligations_from_findings
from archflow.contracts.canonical import canonical_json_bytes

_MAX_JSON_BYTES = 1_000_000
_SUPPORT_PATTERN = re.compile(
    r"(?P<count>\d+) support pillars.*?"
    r"(?P<element>[\w.-]+) gap=(?P<gap>\d+) suggestedY=(?P<y>-?\d+)",
    re.IGNORECASE,
)


class EnvironmentFeedbackError(ValueError):
    """A feedback receipt is malformed, unbounded, or inconsistently bound."""


class PlanBinding(StrEnum):
    EXACT = "exact"
    UNPROVEN = "unproven"


@dataclass(frozen=True, slots=True)
class EnvironmentIssue:
    code: str
    topic: str
    summary: str
    measurements: tuple[tuple[str, str], ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic": self.topic,
            "summary": self.summary,
            "measurements": dict(self.measurements),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ToolEnvironmentObservation:
    SCHEMA = "ToolEnvironmentObservation@1"

    observation_id: str
    source_receipt_id: str
    base_state: StateRef
    workspace_id: str
    capability_id: str
    phase: str
    failure_code: str
    plan_binding: PlanBinding
    plan_sha256: str | None
    binding_refs: tuple[str, ...]
    issues: tuple[EnvironmentIssue, ...]
    canonical_state_mutated: bool
    world_may_have_changed: bool
    source_ref: str

    @property
    def actionable(self) -> bool:
        return (
            self.plan_binding is PlanBinding.EXACT
            and not self.canonical_state_mutated
            and not self.world_may_have_changed
        )

    @property
    def issue_signature(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        return tuple((item.code, item.measurements) for item in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "source_receipt_id": self.source_receipt_id,
            "base_state": _state_json(self.base_state),
            "workspace_id": self.workspace_id,
            "capability_id": self.capability_id,
            "phase": self.phase,
            "failure_code": self.failure_code,
            "plan_binding": self.plan_binding.value,
            "plan_sha256": self.plan_sha256,
            "binding_refs": list(self.binding_refs),
            "issues": [item.to_dict() for item in self.issues],
            "canonical_state_mutated": self.canonical_state_mutated,
            "world_may_have_changed": self.world_may_have_changed,
            "source_ref": self.source_ref,
            "actionable": self.actionable,
        }


@dataclass(frozen=True, slots=True)
class RetryStopReceipt:
    SCHEMA = "RetryStopReceipt@1"

    receipt_id: str
    code: str
    base_state: StateRef
    plan_sha256: str
    source_observation_id: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "code": self.code,
            "base_state": _state_json(self.base_state),
            "plan_sha256": self.plan_sha256,
            "source_observation_id": self.source_observation_id,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentArchitectContext:
    """Detached choice set; it contains no adapter, committer, or world handle."""

    base_state: StateRef
    observation: ToolEnvironmentObservation
    obligations: tuple[RepairObligation, ...]
    discovered_expert_ids: tuple[str, ...]
    allowed_actions: tuple[str, ...] = ("revise", "replace", "unresolved")


def write_tool_invocation_intent(
    path: Path,
    *,
    base_state: StateRef,
    workspace_id: str,
    capability_id: str,
    plan: Mapping[str, Any],
) -> Path:
    """Freeze the exact intended plan before an adapter is invoked."""

    frozen = _freeze_mapping(plan, "plan")
    payload = {
        "schema": "ToolInvocationIntent@1",
        "base_state": _state_json(base_state),
        "workspace_id": _text(workspace_id, "workspace_id"),
        "capability_id": _text(capability_id, "capability_id"),
        "plan_sha256": plan_sha256(frozen),
        "plan": frozen,
    }
    _write_json(path, payload)
    return path


def load_tool_environment_observation(
    failure_receipt_path: Path,
    *,
    invocation_intent_path: Path | None = None,
    failure_link_path: Path | None = None,
) -> ToolEnvironmentObservation:
    """Load a failure as read-only evidence and prove plan binding when possible."""

    receipt_path = failure_receipt_path.resolve()
    receipt_bytes, receipt = _read_json(receipt_path)
    if receipt.get("schema") != "MinecraftMcpFailureReceipt@1":
        raise EnvironmentFeedbackError("failure receipt schema is invalid")
    base_state = _state(receipt.get("base_state"), "failure.base_state")
    workspace_id = _text(receipt.get("workspace_id"), "failure.workspace_id")
    canonical_mutated = _bool(
        receipt.get("canonical_state_mutated"),
        "failure.canonical_state_mutated",
    )
    world_may_have_changed = _bool(
        receipt.get("world_may_have_changed"),
        "failure.world_may_have_changed",
    )
    capability_id = "minecraft.voxel.build_plan"
    binding = PlanBinding.UNPROVEN
    digest: str | None = None
    binding_refs: tuple[str, ...] = ()

    if (invocation_intent_path is None) != (failure_link_path is None):
        raise EnvironmentFeedbackError(
            "exact binding requires both invocation intent and failure link"
        )
    if invocation_intent_path is not None and failure_link_path is not None:
        intent_path = invocation_intent_path.resolve()
        if intent_path.parent != receipt_path.parent:
            raise EnvironmentFeedbackError(
                "intent and failure receipt must share one workspace"
            )
        _, intent = _read_json(intent_path)
        if intent.get("schema") != "ToolInvocationIntent@1":
            raise EnvironmentFeedbackError("invocation intent schema is invalid")
        if _state(intent.get("base_state"), "intent.base_state") != base_state:
            raise EnvironmentFeedbackError("intent exact base does not match failure")
        if intent.get("workspace_id") != workspace_id:
            raise EnvironmentFeedbackError("intent workspace does not match failure")
        capability_id = _text(intent.get("capability_id"), "intent.capability_id")
        frozen_plan = _freeze_mapping(intent.get("plan"), "intent.plan")
        digest = plan_sha256(frozen_plan)
        if intent.get("plan_sha256") != digest:
            raise EnvironmentFeedbackError("intent plan digest is invalid")
        link_path = failure_link_path.resolve()
        if link_path.parent != receipt_path.parent:
            raise EnvironmentFeedbackError(
                "failure link and receipt must share one workspace"
            )
        _, link = _read_json(link_path)
        if link.get("schema") != "ToolInvocationFailureLink@1":
            raise EnvironmentFeedbackError("failure link schema is invalid")
        if link.get("intent_sha256") != _file_sha256(intent_path):
            raise EnvironmentFeedbackError("failure link intent digest drifted")
        if link.get("failure_sha256") != hashlib.sha256(receipt_bytes).hexdigest():
            raise EnvironmentFeedbackError("failure link receipt digest drifted")
        if link.get("base_state") != _state_json(base_state):
            raise EnvironmentFeedbackError("failure link base state drifted")
        if link.get("workspace_id") != workspace_id:
            raise EnvironmentFeedbackError("failure link workspace drifted")
        if link.get("capability_id") != capability_id:
            raise EnvironmentFeedbackError("failure link capability drifted")
        if link.get("plan_sha256") != digest:
            raise EnvironmentFeedbackError("failure link plan digest drifted")
        binding = PlanBinding.EXACT
        binding_refs = (intent_path.as_uri(), link_path.as_uri())

    receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_id = f"mcp-failure-{receipt_digest[:20]}"
    evidence_ref = receipt_path.as_uri()
    issues = _issues(receipt, evidence_ref)
    identity = {
        "receipt": receipt_id,
        "base": _state_json(base_state),
        "workspace": workspace_id,
        "capability": capability_id,
        "phase": receipt.get("phase"),
        "code": receipt.get("code"),
        "binding": binding.value,
        "plan": digest,
        "issues": [item.to_dict() for item in issues],
    }
    observation_digest = hashlib.sha256(canonical_json_bytes(identity, ascii=False)).hexdigest()
    return ToolEnvironmentObservation(
        observation_id=f"environment-observation-{observation_digest[:20]}",
        source_receipt_id=receipt_id,
        base_state=base_state,
        workspace_id=workspace_id,
        capability_id=capability_id,
        phase=_text(receipt.get("phase"), "failure.phase"),
        failure_code=_text(receipt.get("code"), "failure.code"),
        plan_binding=binding,
        plan_sha256=digest,
        binding_refs=binding_refs,
        issues=issues,
        canonical_state_mutated=canonical_mutated,
        world_may_have_changed=world_may_have_changed,
        source_ref=evidence_ref,
    )


def compile_environment_obligations(
    observation: ToolEnvironmentObservation,
) -> tuple[RepairObligation, ...]:
    """Compile questions for the Architect, never geometry edits or hard gates."""

    if not observation.actionable:
        return ()
    findings = tuple(
        RepairFinding(
            code=issue.code,
            message=_obligation_statement(issue),
            source_receipt_id=observation.source_receipt_id,
            evidence_refs=issue.evidence_refs,
        )
        for issue in observation.issues
    )
    return obligations_from_findings(findings)


def write_tool_environment_observation(
    path: Path,
    observation: ToolEnvironmentObservation,
) -> Path:
    _write_json(path, observation.to_dict())
    return path


def write_tool_failure_link(
    path: Path,
    *,
    invocation_intent_path: Path,
    failure_receipt_path: Path,
) -> Path:
    """Bind the exact controlled invocation to the exception receipt it raised."""

    intent_path = invocation_intent_path.resolve()
    receipt_path = failure_receipt_path.resolve()
    target = path.resolve()
    if not (intent_path.parent == receipt_path.parent == target.parent):
        raise EnvironmentFeedbackError(
            "intent, failure receipt, and link must share one workspace"
        )
    _, intent = _read_json(intent_path)
    receipt_bytes, receipt = _read_json(receipt_path)
    if intent.get("schema") != "ToolInvocationIntent@1":
        raise EnvironmentFeedbackError("invocation intent schema is invalid")
    if receipt.get("schema") != "MinecraftMcpFailureReceipt@1":
        raise EnvironmentFeedbackError("failure receipt schema is invalid")
    if intent.get("base_state") != receipt.get("base_state"):
        raise EnvironmentFeedbackError("intent and failure base state drifted")
    if intent.get("workspace_id") != receipt.get("workspace_id"):
        raise EnvironmentFeedbackError("intent and failure workspace drifted")
    payload = {
        "schema": "ToolInvocationFailureLink@1",
        "base_state": intent["base_state"],
        "workspace_id": intent["workspace_id"],
        "capability_id": intent["capability_id"],
        "plan_sha256": intent["plan_sha256"],
        "intent_sha256": _file_sha256(intent_path),
        "failure_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "failure_code": receipt.get("code"),
        "failure_phase": receipt.get("phase"),
    }
    _write_json(target, payload)
    return target


def build_environment_expert_snapshot(
    observation: ToolEnvironmentObservation,
    obligations: tuple[RepairObligation, ...],
    *,
    program_json: str | None,
) -> ExpertSnapshot:
    """Translate speculative obligations into detached discovery input."""

    if any(
        item.source_receipt_id != observation.source_receipt_id
        for item in obligations
    ):
        raise EnvironmentFeedbackError(
            "expert obligations must originate from this observation"
        )
    issue_topics = {item.code: item.topic for item in observation.issues}
    detached_obligations = tuple(
        ExpertObligation(
            obligation_id=item.obligation_id,
            topic=issue_topics.get(item.finding_code, "environment"),
            statement=item.statement,
            source_ref=item.source_receipt_id,
        )
        for item in obligations
    )
    summary = "; ".join(item.summary for item in observation.issues)[:1_000]
    return ExpertSnapshot(
        base_state=StateRef(
            observation.base_state.run_id,
            observation.base_state.version,
        ),
        program_json=program_json,
        obligations=detached_obligations,
        evidence=(
            ExpertEvidence(
                kind="environment_observation",
                evidence_ref=observation.source_ref,
                summary=summary,
            ),
        ),
    )


def guard_identical_retry(
    observations: Sequence[ToolEnvironmentObservation],
    *,
    base_state: StateRef,
    plan: Mapping[str, Any],
) -> RetryStopReceipt | None:
    """Stop an unchanged failed plan; changed designs remain Architect-owned."""

    digest = plan_sha256(plan)
    for observation in reversed(tuple(observations)):
        if (
            observation.plan_binding is PlanBinding.EXACT
            and observation.base_state == base_state
            and observation.plan_sha256 == digest
            and observation.issues
        ):
            identity = {
                "base": _state_json(base_state),
                "plan": digest,
                "observation": observation.observation_id,
            }
            receipt_digest = hashlib.sha256(canonical_json_bytes(identity, ascii=False)).hexdigest()
            return RetryStopReceipt(
                receipt_id=f"retry-stop-{receipt_digest[:20]}",
                code="environment.retry.identical_failed_plan",
                base_state=base_state,
                plan_sha256=digest,
                source_observation_id=observation.observation_id,
                message=(
                    "The exact plan already failed against the same base. "
                    "The Architect must change its proposal, replace the site "
                    "strategy, or declare the issue unresolved."
                ),
            )
    return None


def write_environment_feedback_trace(
    path: Path,
    *,
    observation: ToolEnvironmentObservation,
    obligations: tuple[RepairObligation, ...],
    discovered_expert_ids: tuple[str, ...],
    architect_action: str,
    architect_rationale: str,
    retry_stop: RetryStopReceipt | None = None,
) -> Path:
    allowed = ("revise", "replace", "unresolved")
    if architect_action not in allowed:
        raise EnvironmentFeedbackError("architect action is invalid")
    if not architect_rationale.strip():
        raise EnvironmentFeedbackError("architect rationale is required")
    payload = {
        "schema": "EnvironmentFeedbackTrace@1",
        "observation": observation.to_dict(),
        "obligations": [_obligation_json(item) for item in obligations],
        "discovered_expert_ids": list(discovered_expert_ids),
        "architect_decision": {
            "action": architect_action,
            "rationale": architect_rationale,
            "allowed_actions": list(allowed),
        },
        "retry_stop": retry_stop.to_dict() if retry_stop is not None else None,
        "hard_usability_verdict": None,
        "canonical_state_transition": None,
    }
    _write_json(path, payload)
    return path


def load_environment_feedback_trace(path: Path) -> dict[str, Any]:
    """Reload a trace and verify the no-verdict/no-write architecture boundary."""

    _, payload = _read_json(path.resolve())
    if payload.get("schema") != "EnvironmentFeedbackTrace@1":
        raise EnvironmentFeedbackError("feedback trace schema is invalid")
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        raise EnvironmentFeedbackError("feedback trace observation is missing")
    if observation.get("schema") != ToolEnvironmentObservation.SCHEMA:
        raise EnvironmentFeedbackError("feedback observation schema is invalid")
    base = _state(observation.get("base_state"), "observation.base_state")
    if observation.get("plan_binding") == PlanBinding.EXACT.value:
        if not isinstance(observation.get("plan_sha256"), str):
            raise EnvironmentFeedbackError("exact observation lacks plan digest")
        binding_refs = observation.get("binding_refs")
        if not isinstance(binding_refs, list) or len(binding_refs) != 2:
            raise EnvironmentFeedbackError(
                "exact observation lacks intent/failure-link references"
            )
    obligations = payload.get("obligations")
    if not isinstance(obligations, list):
        raise EnvironmentFeedbackError("feedback obligations must be a list")
    for obligation in obligations:
        if not isinstance(obligation, dict):
            raise EnvironmentFeedbackError("feedback obligation is invalid")
        if obligation.get("source_receipt_id") != observation.get(
            "source_receipt_id"
        ):
            raise EnvironmentFeedbackError("feedback obligation provenance drifted")
    decision = payload.get("architect_decision")
    if not isinstance(decision, dict):
        raise EnvironmentFeedbackError("architect decision is missing")
    if decision.get("action") not in {"revise", "replace", "unresolved"}:
        raise EnvironmentFeedbackError("architect action is invalid")
    if payload.get("hard_usability_verdict") is not None:
        raise EnvironmentFeedbackError(
            "environment feedback acquired hard-gate authority"
        )
    if payload.get("canonical_state_transition") is not None:
        raise EnvironmentFeedbackError(
            "environment feedback acquired canonical-write authority"
        )
    retry_stop = payload.get("retry_stop")
    if retry_stop is not None:
        if not isinstance(retry_stop, dict):
            raise EnvironmentFeedbackError("retry stop receipt is invalid")
        if retry_stop.get("base_state") != _state_json(base):
            raise EnvironmentFeedbackError("retry stop exact base drifted")
        if retry_stop.get("source_observation_id") != observation.get(
            "observation_id"
        ):
            raise EnvironmentFeedbackError("retry stop provenance drifted")
    return payload


def plan_sha256(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(_freeze_mapping(plan, "plan"), ascii=False)).hexdigest()


def _issues(
    receipt: Mapping[str, Any],
    evidence_ref: str,
) -> tuple[EnvironmentIssue, ...]:
    structured = _structured_payload(receipt)
    raw_issues = structured.get("issues") if structured else None
    support_count = _support_count(structured)
    parsed: list[EnvironmentIssue] = []
    if isinstance(raw_issues, list):
        for raw in raw_issues[:32]:
            if not isinstance(raw, dict):
                continue
            issue = str(raw.get("issue", "")).strip().lower()
            if issue != "floating":
                continue
            measurements = (
                ("element", str(raw.get("cuboid", "candidate"))[:200]),
                ("gap_below", str(raw.get("gapBelow", "unknown"))[:200]),
                ("suggested_y", str(raw.get("suggestedY", "unknown"))[:200]),
                ("support_count", support_count),
            )
            parsed.append(
                EnvironmentIssue(
                    code="environment.terrain.support_unresolved",
                    topic="support",
                    summary=(
                        "The preview reports an unresolved candidate-to-site "
                        "support relationship."
                    ),
                    measurements=measurements,
                    evidence_refs=(evidence_ref,),
                )
            )
    if parsed:
        return tuple(parsed)

    message = str(receipt.get("message", ""))
    match = _SUPPORT_PATTERN.search(message)
    if match:
        return (
            EnvironmentIssue(
                code="environment.terrain.support_unresolved",
                topic="support",
                summary=(
                    "The preview reports an unresolved candidate-to-site "
                    "support relationship."
                ),
                measurements=(
                    ("element", match.group("element")),
                    ("gap_below", match.group("gap")),
                    ("suggested_y", match.group("y")),
                    ("support_count", match.group("count")),
                ),
                evidence_refs=(evidence_ref,),
            ),
        )
    return (
        EnvironmentIssue(
            code="environment.tool.execution_unavailable",
            topic="tool",
            summary="The tool could not complete the requested preview.",
            evidence_refs=(evidence_ref,),
        ),
    )


def _structured_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    structured = receipt.get("structured")
    if isinstance(structured, dict):
        return dict(structured)
    message = receipt.get("message")
    if not isinstance(message, str):
        return {}
    marker = "MCP_TOOL_FAILED:"
    if marker not in message:
        return {}
    literal = message.partition(marker)[2].strip()
    try:
        parsed = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _support_count(structured: Mapping[str, Any]) -> str:
    error = structured.get("error")
    if not isinstance(error, str):
        return "unknown"
    match = re.search(r"(\d+) support pillars", error, re.IGNORECASE)
    return match.group(1) if match else "unknown"


def _obligation_statement(issue: EnvironmentIssue) -> str:
    if issue.code == "environment.terrain.support_unresolved":
        return (
            "Resolve or explicitly defer the candidate-to-site support "
            "relationship before another preview. The Architect retains the "
            "choice to revise the candidate, replace the site strategy, seek "
            "expert advice, or declare the issue unresolved."
        )
    return (
        "Resolve or explicitly defer the tool/environment condition before "
        "another attempt; no geometry response is prescribed."
    )


def _obligation_json(obligation: RepairObligation) -> dict[str, Any]:
    return {
        "obligation_id": obligation.obligation_id,
        "statement": obligation.statement,
        "finding_code": obligation.finding_code,
        "source_receipt_id": obligation.source_receipt_id,
        "evidence_refs": list(obligation.evidence_refs),
    }


def _state(value: object, field: str) -> StateRef:
    if not isinstance(value, dict):
        raise EnvironmentFeedbackError(f"{field} must be an object")
    run_id = value.get("run_id")
    version = value.get("version")
    if not isinstance(run_id, str) or not run_id.strip():
        raise EnvironmentFeedbackError(f"{field}.run_id is invalid")
    if type(version) is not int or version < 0:
        raise EnvironmentFeedbackError(f"{field}.version is invalid")
    return StateRef(run_id=run_id, version=version)


def _state_json(state: StateRef) -> dict[str, Any]:
    return {"run_id": state.run_id, "version": state.version}


def _read_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file():
        raise EnvironmentFeedbackError(f"JSON file is not loadable: {path}")
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise EnvironmentFeedbackError("JSON file exceeds bounded size")
    encoded = path.read_bytes()
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentFeedbackError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise EnvironmentFeedbackError(f"{path.name} must contain an object")
    return encoded, value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _freeze_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise EnvironmentFeedbackError(f"{field} must be a non-empty object")
    try:
        frozen = json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise EnvironmentFeedbackError(f"{field} is not finite JSON") from exc
    if not isinstance(frozen, dict):
        raise EnvironmentFeedbackError(f"{field} must be an object")
    return frozen


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EnvironmentFeedbackError(f"{field} must be non-empty text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise EnvironmentFeedbackError(f"{field} must be boolean")
    return value
