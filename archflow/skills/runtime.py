"""State-responsive discovery and bounded execution for Skill packages."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, Mapping

from archflow.skills.contracts import (
    AvailableTool,
    DetachedSkillContext,
    SkillAdvice,
    SkillAuthority,
    SkillContractError,
    SkillInvocationInput,
    SkillOutput,
    SkillProposal,
    canonical_json,
    digest_json,
)
from archflow.skills.package import SkillPackage
from archflow.state.design_state import ContextSlice


class SkillRuntimeError(SkillContractError):
    """A Skill is ineligible or produced an invalid detached result."""


@dataclass(frozen=True, slots=True)
class SkillDiscoveryMatch:
    package: SkillPackage
    matched_obligation_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.package, SkillPackage):
            raise TypeError("package must be a SkillPackage")
        if not self.matched_obligation_refs:
            raise SkillRuntimeError(
                "discovered Skill must match at least one current obligation"
            )


def _available_tool_map(
    available_tools: Iterable[AvailableTool],
) -> dict[str, AvailableTool]:
    values = tuple(available_tools)
    if any(not isinstance(item, AvailableTool) for item in values):
        raise TypeError("available_tools must contain AvailableTool values")
    result: dict[str, AvailableTool] = {}
    for item in values:
        if item.grant_ref in result:
            raise SkillRuntimeError("available_tools contains duplicates")
        result[item.grant_ref] = item
    return result


def _validate_topics(
    context: ContextSlice,
    obligation_topics: Mapping[str, str],
) -> None:
    expected = {item.obligation_id for item in context.obligations}
    if set(obligation_topics) != expected:
        raise SkillRuntimeError(
            "obligation topics must exactly cover the current ContextSlice"
        )
    if any(
        not isinstance(value, str) or not value.strip()
        for value in obligation_topics.values()
    ):
        raise SkillRuntimeError("obligation topics must be non-empty text")


def discover_skills(
    packages: Iterable[SkillPackage],
    *,
    context: ContextSlice,
    phase: str,
    obligation_topics: Mapping[str, str],
    evidence_kinds: frozenset[str],
    available_tools: Iterable[AvailableTool],
) -> tuple[SkillDiscoveryMatch, ...]:
    """Recompute relevance from current state; returned order is not a plan."""

    if not isinstance(context, ContextSlice):
        raise TypeError("context must be a ContextSlice")
    if not isinstance(phase, str) or not phase.strip():
        raise SkillRuntimeError("phase must be non-empty text")
    _validate_topics(context, obligation_topics)
    if not isinstance(evidence_kinds, frozenset):
        raise TypeError("evidence_kinds must be a frozenset")
    tool_map = _available_tool_map(available_tools)
    values = tuple(packages)
    if any(not isinstance(item, SkillPackage) for item in values):
        raise TypeError("packages must contain SkillPackage values")
    identities = tuple(item.spec.identity for item in values)
    if len(identities) != len(set(identities)):
        raise SkillRuntimeError("duplicate Skill id/version in discovery input")

    matches: list[SkillDiscoveryMatch] = []
    for package in values:
        spec = package.spec
        if "*" not in spec.admissible_phases and phase not in (
            spec.admissible_phases
        ):
            continue
        if not set(spec.evidence_requirements) <= evidence_kinds:
            continue
        required_grants = {
            requirement.grant_ref for requirement in spec.tool_requirements
        }
        if not required_grants <= set(tool_map):
            continue
        if "*" in spec.obligation_topics:
            matched_ids = set(obligation_topics)
        else:
            matched_ids = {
                obligation_id
                for obligation_id, topic in obligation_topics.items()
                if topic in spec.obligation_topics
            }
        if not matched_ids:
            continue
        matches.append(
            SkillDiscoveryMatch(
                package=package,
                matched_obligation_refs=tuple(
                    f"obligation:{item}" for item in sorted(matched_ids)
                ),
            )
        )
    return tuple(
        sorted(
            matches,
            key=lambda item: (
                item.package.spec.skill_id,
                item.package.spec.version,
                item.package.spec.package_digest,
            ),
        )
    )


class SkillInvocationStatus(StrEnum):
    ADVICE = "advice"
    PROPOSAL = "proposal"
    ERROR = "error"
    TIMEOUT = "timeout"
    OVERSIZED = "oversized"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True, slots=True)
class SkillInvocationReceipt:
    receipt_id: str
    status: SkillInvocationStatus
    skill_id: str
    skill_version: str
    package_digest: str
    provider_surface: str
    context_digest: str
    obligation_refs: tuple[str, ...]
    declared_tools: tuple[str, ...]
    input_digest: str
    output_digest: str | None
    attempts_used: int
    elapsed_milliseconds: int
    timeout_seconds: float
    max_output_bytes: int
    max_attempts: int
    output_bytes: int
    failure_code: str | None
    failure_message: str | None
    produced_output_ref: str | None
    responds_to_refs: tuple[str, ...]

    SCHEMA = "SkillInvocationReceipt@1"

    def __post_init__(self) -> None:
        if not self.receipt_id:
            raise SkillRuntimeError("receipt_id must be non-empty")
        if not isinstance(self.status, SkillInvocationStatus):
            raise TypeError("status must be a SkillInvocationStatus")
        for value, field in (
            (self.skill_id, "skill_id"),
            (self.skill_version, "skill_version"),
            (self.provider_surface, "provider_surface"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SkillRuntimeError(f"{field} must be non-empty text")
        for value, field in (
            (self.package_digest, "package_digest"),
            (self.context_digest, "context_digest"),
            (self.input_digest, "input_digest"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value.lower())
            ):
                raise SkillRuntimeError(f"{field} must be a SHA-256 digest")
        for values, field in (
            (self.obligation_refs, "obligation_refs"),
            (self.declared_tools, "declared_tools"),
            (self.responds_to_refs, "responds_to_refs"),
        ):
            if not isinstance(values, tuple) or len(values) != len(set(values)):
                raise SkillRuntimeError(f"{field} must be a unique tuple")
        if self.declared_tools != tuple(sorted(self.declared_tools)):
            raise SkillRuntimeError("declared_tools must be sorted")
        if self.attempts_used < 1 or self.attempts_used > self.max_attempts:
            raise SkillRuntimeError("attempt usage exceeds declared budget")
        if self.elapsed_milliseconds < 0:
            raise SkillRuntimeError("elapsed time cannot be negative")
        if self.output_bytes < 0:
            raise SkillRuntimeError("output usage cannot be negative")
        success = self.status in {
            SkillInvocationStatus.ADVICE,
            SkillInvocationStatus.PROPOSAL,
        }
        if success:
            if (
                self.output_digest is None
                or self.produced_output_ref is None
                or not self.responds_to_refs
                or self.failure_code is not None
                or self.failure_message is not None
                or not 0 < self.output_bytes <= self.max_output_bytes
            ):
                raise SkillRuntimeError("successful receipt is incomplete")
        elif (
            self.output_digest is not None
            or self.produced_output_ref is not None
            or self.responds_to_refs
            or self.failure_code is None
        ):
            raise SkillRuntimeError("failed receipt acquired output authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "package_digest": self.package_digest,
            "provider_surface": self.provider_surface,
            "context_digest": self.context_digest,
            "obligation_refs": list(self.obligation_refs),
            "declared_tools": list(self.declared_tools),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "budget": {
                "timeout_seconds": self.timeout_seconds,
                "max_output_bytes": self.max_output_bytes,
                "max_attempts": self.max_attempts,
            },
            "budget_use": {
                "attempts": self.attempts_used,
                "elapsed_milliseconds": self.elapsed_milliseconds,
                "output_bytes": self.output_bytes,
            },
            "failure": (
                None
                if self.failure_code is None
                else {
                    "code": self.failure_code,
                    "message": self.failure_message,
                }
            ),
            "produced_output_ref": self.produced_output_ref,
            "responds_to_refs": list(self.responds_to_refs),
            "candidate_only": True,
            "verified": False,
            "accepted": False,
            "canonical": False,
            "world_write": False,
        }


@dataclass(frozen=True, slots=True)
class SkillInvocationResult:
    receipt: SkillInvocationReceipt
    output: SkillOutput | None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, SkillInvocationReceipt):
            raise TypeError("receipt must be a SkillInvocationReceipt")
        if self.output is not None and not isinstance(
            self.output, (SkillAdvice, SkillProposal)
        ):
            raise TypeError("output must be detached Skill output")
        if (self.output is None) == (
            self.receipt.status
            in {SkillInvocationStatus.ADVICE, SkillInvocationStatus.PROPOSAL}
        ):
            raise SkillRuntimeError("result output disagrees with receipt")


SkillHandler = Callable[[SkillInvocationInput], SkillOutput]


def _call_bounded(
    handler: SkillHandler,
    invocation_input: SkillInvocationInput,
    *,
    timeout_seconds: float,
) -> tuple[str, object]:
    results: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            results.put_nowait(("ok", handler(invocation_input)))
        except Exception as exc:  # Converted into a bounded receipt.
            results.put_nowait(("error", exc))

    thread = threading.Thread(
        target=run,
        daemon=True,
        name="archflow-skill",
    )
    thread.start()
    try:
        return results.get(timeout=timeout_seconds)
    except queue.Empty:
        return ("timeout", None)


def _response_refs(context: DetachedSkillContext) -> frozenset[str]:
    payload = ContextSlice.from_dict(json.loads(context.context_payload_json))
    return frozenset(
        {
            *(f"obligation:{item.obligation_id}" for item in payload.obligations),
            *(f"commitment:{item.commitment_id}" for item in payload.commitments),
            *(item.ref for item in payload.interfaces),
            *payload.phase_deliverable_refs,
            f"phase-task:{context.phase}",
        }
    )


def _validate_output(
    package: SkillPackage,
    context: DetachedSkillContext,
    output: object,
) -> SkillOutput:
    if not isinstance(output, (SkillAdvice, SkillProposal)):
        raise SkillRuntimeError(
            "Skill handler must return SkillAdvice or SkillProposal"
        )
    if output.SCHEMA not in package.spec.output_schemas:
        raise SkillRuntimeError("Skill returned an undeclared output schema")
    known_responses = _response_refs(context)
    unknown = set(output.responds_to_refs) - known_responses
    if unknown:
        raise SkillRuntimeError(
            f"Skill output responds to unavailable refs: {sorted(unknown)}"
        )
    payload = ContextSlice.from_dict(json.loads(context.context_payload_json))
    unknown_evidence = set(output.evidence_refs) - set(payload.evidence_refs)
    if unknown_evidence:
        raise SkillRuntimeError(
            f"Skill output cites unavailable evidence: {sorted(unknown_evidence)}"
        )
    if isinstance(output, SkillProposal):
        if package.spec.authority_ceiling is not SkillAuthority.PROPOSAL:
            raise SkillRuntimeError("advice-only Skill returned a proposal")
        if output.operator.base_state_digest != context.target_state_digest:
            raise SkillRuntimeError("Skill proposal is stale against exact base")
        if output.operator.authority_id not in payload.allowed_authority_ids:
            raise SkillRuntimeError(
                "Skill proposal uses authority absent from ContextSlice"
            )
    return output


def _receipt(
    *,
    package: SkillPackage,
    provider_surface: str,
    invocation_input: SkillInvocationInput,
    status: SkillInvocationStatus,
    attempts: int,
    elapsed_ms: int,
    output: SkillOutput | None = None,
    output_bytes: int = 0,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> SkillInvocationReceipt:
    output_digest = (
        None if output is None else digest_json(output.to_dict())
    )
    produced_ref = None if output is None else output.ref
    responds = () if output is None else output.responds_to_refs
    identity = {
        "skill_id": package.spec.skill_id,
        "skill_version": package.spec.version,
        "package_digest": package.spec.package_digest,
        "provider_surface": provider_surface,
        "input_digest": invocation_input.input_digest,
        "status": status.value,
        "attempts": attempts,
        "output_digest": output_digest,
        "failure_code": failure_code,
    }
    return SkillInvocationReceipt(
        receipt_id=digest_json(identity)[:24],
        status=status,
        skill_id=package.spec.skill_id,
        skill_version=package.spec.version,
        package_digest=package.spec.package_digest,
        provider_surface=provider_surface,
        context_digest=invocation_input.context.context_digest,
        obligation_refs=invocation_input.context.obligation_refs,
        declared_tools=tuple(
            item.grant_ref for item in invocation_input.context.tool_grants
        ),
        input_digest=invocation_input.input_digest,
        output_digest=output_digest,
        attempts_used=attempts,
        elapsed_milliseconds=elapsed_ms,
        timeout_seconds=package.spec.budget.timeout_seconds,
        max_output_bytes=package.spec.budget.max_output_bytes,
        max_attempts=package.spec.budget.max_attempts,
        output_bytes=output_bytes,
        failure_code=failure_code,
        failure_message=failure_message,
        produced_output_ref=produced_ref,
        responds_to_refs=responds,
    )


def invoke_skill(
    package: SkillPackage,
    *,
    provider_surface: str,
    context: ContextSlice,
    phase: str,
    obligation_topics: Mapping[str, str],
    evidence_kinds: frozenset[str],
    available_tools: Iterable[AvailableTool],
    handler: SkillHandler,
) -> SkillInvocationResult:
    """Invoke one eligible package without granting a live writer or world."""

    if not isinstance(package, SkillPackage):
        raise TypeError("package must be a SkillPackage")
    if (
        not isinstance(provider_surface, str)
        or not provider_surface.strip()
        or len(provider_surface) > 64
    ):
        raise SkillRuntimeError("provider_surface must be bounded text")
    if not callable(handler):
        raise TypeError("handler must be callable")
    available = tuple(available_tools)
    matches = discover_skills(
        (package,),
        context=context,
        phase=phase,
        obligation_topics=obligation_topics,
        evidence_kinds=evidence_kinds,
        available_tools=available,
    )
    if not matches:
        raise SkillRuntimeError("Skill is not eligible for the current state")
    tool_map = _available_tool_map(available)
    grants = tuple(
        tool_map[item.grant_ref] for item in package.spec.tool_requirements
    )
    detached = DetachedSkillContext.detach(
        context,
        phase=phase,
        obligation_topics=obligation_topics,
        evidence_kinds=evidence_kinds,
        tool_grants=grants,
    )
    invocation_input = SkillInvocationInput(
        package_id=package.spec.skill_id,
        package_version=package.spec.version,
        package_digest=package.spec.package_digest,
        instructions=package.instructions,
        references=package.references,
        context=detached,
    )
    started = time.monotonic()
    for attempt in range(1, package.spec.budget.max_attempts + 1):
        outcome, value = _call_bounded(
            handler,
            invocation_input,
            timeout_seconds=package.spec.budget.timeout_seconds,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if outcome == "timeout":
            receipt = _receipt(
                package=package,
                provider_surface=provider_surface,
                invocation_input=invocation_input,
                status=SkillInvocationStatus.TIMEOUT,
                attempts=attempt,
                elapsed_ms=elapsed_ms,
                failure_code="skill.timeout",
                failure_message=(
                    "Skill exceeded its declared timeout; detached daemon "
                    "work receives no runtime authority"
                ),
            )
            return SkillInvocationResult(receipt, None)
        if outcome == "error":
            if attempt < package.spec.budget.max_attempts:
                continue
            message = f"{type(value).__name__}: {value}"[:1000]
            receipt = _receipt(
                package=package,
                provider_surface=provider_surface,
                invocation_input=invocation_input,
                status=SkillInvocationStatus.ERROR,
                attempts=attempt,
                elapsed_ms=elapsed_ms,
                failure_code="skill.exception",
                failure_message=message,
            )
            return SkillInvocationResult(receipt, None)
        try:
            output = _validate_output(package, detached, value)
        except (SkillContractError, TypeError, ValueError) as exc:
            receipt = _receipt(
                package=package,
                provider_surface=provider_surface,
                invocation_input=invocation_input,
                status=SkillInvocationStatus.INVALID_OUTPUT,
                attempts=attempt,
                elapsed_ms=elapsed_ms,
                failure_code="skill.invalid_output",
                failure_message=str(exc)[:1000],
            )
            return SkillInvocationResult(receipt, None)
        serialized = canonical_json(output.to_dict()).encode("utf-8")
        if len(serialized) > package.spec.budget.max_output_bytes:
            receipt = _receipt(
                package=package,
                provider_surface=provider_surface,
                invocation_input=invocation_input,
                status=SkillInvocationStatus.OVERSIZED,
                attempts=attempt,
                elapsed_ms=elapsed_ms,
                output_bytes=len(serialized),
                failure_code="skill.oversized_output",
                failure_message="Skill output exceeds declared byte budget",
            )
            return SkillInvocationResult(receipt, None)
        status = (
            SkillInvocationStatus.ADVICE
            if isinstance(output, SkillAdvice)
            else SkillInvocationStatus.PROPOSAL
        )
        receipt = _receipt(
            package=package,
            provider_surface=provider_surface,
            invocation_input=invocation_input,
            status=status,
            attempts=attempt,
            elapsed_ms=elapsed_ms,
            output=output,
            output_bytes=len(serialized),
        )
        return SkillInvocationResult(receipt, output)
    raise AssertionError("bounded Skill attempt loop must return")
