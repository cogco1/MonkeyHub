"""Recoverable orchestration for one typed semantic-geometry production step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from archflow.capabilities.spatial import validate_spatial_authoring_context
from archflow.production.responsibility import InvocationEnvelope
from archflow.contracts.canonical import canonical_digest
from archflow.runtime.persistence.production_transition import (
    ArchivedFailedProductionAttempt,
    ProductionTransitionArchive,
    ProductionTransitionPort,
    load_production_transition,
    persist_compiled_production_transition,
    persist_failed_production_attempt,
    production_intent_digest,
)
from archflow.project.refs import ProjectRecordRef, RunRef, require_identifier
from archflow.runtime.semantic_geometry_lifecycle import (
    InitialSemanticGeometryResult,
    SemanticGeometryLifecycleResult,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.build_policy import BuildPolicy
from archflow.state.design_maturity import DesignMaturityState, PhaseGateReceipt
from archflow.state.design_program import DesignProgram
from archflow.state.operational_state import (
    OperationalMarkovState,
    require_logical_ref,
)
from archflow.state.site_context import SiteContext


class ProductionRuntimeError(RuntimeError):
    """A production run input or compiled result is not exact-base durable."""


class ProductionContextError(ValueError):
    """A production context is stale, incomplete, or over-authorized."""


class ProductionStepCompilationFailed(RuntimeError):
    """A bounded production compiler stopped after validated provider work."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "production.compilation_failed",
    ) -> None:
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("error_code must be non-empty text")
        self.error_code = error_code
        super().__init__(message)


class ProductionRuntimeStepFailed(RuntimeError):
    """A failed production attempt was durably recorded through P036."""

    def __init__(self, attempt: ArchivedFailedProductionAttempt) -> None:
        if not isinstance(attempt, ArchivedFailedProductionAttempt):
            raise TypeError("attempt must be ArchivedFailedProductionAttempt")
        self.attempt = attempt
        super().__init__(
            f"{attempt.receipt.error_code}: {attempt.receipt.message}; "
            f"evidence={attempt.ref.uri}"
        )

    def to_dict(self) -> dict[str, object]:
        receipt = self.attempt.receipt
        return {
            "schema": "ProductionRuntimeFailure@1",
            "project_id": receipt.project_id,
            "run_id": receipt.run_id,
            "step_id": receipt.step_id,
            "intent_digest": receipt.intent_digest,
            "attempt_id": receipt.attempt_id,
            "attempt_index": receipt.attempt_index,
            "attempt_ref": self.attempt.ref.uri,
            "retry_of_ref": receipt.retry_of_ref,
            "error_code": receipt.error_code,
            "message": receipt.message,
            "transition_checkpoint_ref": None,
            "lifecycle_successor": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ProductionAuthoringContext:
    """Existing current-project state; never a second design tree."""

    state: OperationalMarkovState
    maturity: DesignMaturityState
    phase_gate: PhaseGateReceipt
    program: DesignProgram
    site_context: SiteContext
    build_policy: BuildPolicy
    architect_id: str
    required_commitment_refs: tuple[str, ...]

    SCHEMA = "ProductionAuthoringContext@1"

    def __post_init__(self) -> None:
        typed = (
            (self.state, OperationalMarkovState, "state"),
            (self.maturity, DesignMaturityState, "maturity"),
            (self.phase_gate, PhaseGateReceipt, "phase_gate"),
            (self.program, DesignProgram, "program"),
            (self.site_context, SiteContext, "site_context"),
            (self.build_policy, BuildPolicy, "build_policy"),
        )
        for value, expected, field in typed:
            if not isinstance(value, expected):
                raise TypeError(f"{field} must be {expected.__name__}")
        require_identifier(self.architect_id, "architect_id")
        if (
            not isinstance(self.required_commitment_refs, tuple)
            or self.required_commitment_refs
            != tuple(sorted(set(self.required_commitment_refs)))
        ):
            raise ProductionContextError(
                "required_commitment_refs must be a sorted tuple"
            )
        for ref in self.required_commitment_refs:
            require_logical_ref(ref, "required_commitment_ref")
            if self.state.value_for_ref(ref) is None:
                raise ProductionContextError(
                    f"required commitment is absent from current state: {ref}"
                )
        validate_spatial_authoring_context(
            state=self.state,
            maturity=self.maturity,
            phase_gate=self.phase_gate,
            program=self.program,
            site_context=self.site_context,
            build_policy=self.build_policy,
        )

    @property
    def run(self) -> RunRef:
        return self.state.branch.run

    @property
    def context_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def require_run(self, run: RunRef) -> None:
        if not isinstance(run, RunRef):
            raise TypeError("run must be RunRef")
        if self.run != run:
            raise ProductionContextError(
                "production context does not bind the exact P036 run base"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "state": self.state.to_dict(),
            "maturity": self.maturity.to_dict(),
            "phase_gate": self.phase_gate.to_dict(),
            "program": self.program.to_dict(),
            "site_context": self.site_context.to_dict(),
            "build_policy": self.build_policy.to_dict(),
            "architect_id": self.architect_id,
            "required_commitment_refs": list(self.required_commitment_refs),
            "generation_authority": False,
            "selection_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProductionAuthoringContext":
        if not isinstance(value, Mapping):
            raise TypeError("production context must be an object")
        expected = {
            "schema", "state", "maturity", "phase_gate", "program",
            "site_context", "build_policy", "architect_id",
            "required_commitment_refs", "generation_authority",
            "selection_authority", "persistence_authority",
            "canonical_write_authority",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise ProductionContextError("production context schema drifted")
        if any(
            value.get(field) is not False
            for field in (
                "generation_authority", "selection_authority",
                "persistence_authority", "canonical_write_authority",
            )
        ):
            raise ProductionContextError("production context acquired authority")
        refs = value["required_commitment_refs"]
        if not isinstance(refs, list) or any(
            not isinstance(item, str) for item in refs
        ):
            raise TypeError("required_commitment_refs must be a string list")
        return cls(
            state=OperationalMarkovState.from_dict(value["state"]),
            maturity=DesignMaturityState.from_dict(value["maturity"]),
            phase_gate=PhaseGateReceipt.from_dict(value["phase_gate"]),
            program=DesignProgram.from_dict(value["program"]),
            site_context=SiteContext.from_dict(value["site_context"]),
            build_policy=BuildPolicy.from_dict(value["build_policy"]),
            architect_id=value["architect_id"],
            required_commitment_refs=tuple(refs),
        )


class ProductionRuntimePort(ProductionTransitionPort, Protocol):
    def load_run(self, run_id: str) -> RunRef: ...


class ProductionStepCompiler(Protocol):
    @property
    def intent_record_refs(self) -> tuple[ProjectRecordRef, ...]: ...

    def invocation_evidence_cursor(self) -> int: ...

    def invocation_evidence_since(
        self,
        cursor: int,
    ) -> tuple[InvocationEnvelope, ...]: ...

    async def compile(
        self,
        *,
        run: RunRef,
        raw_request: ProjectRecordRef,
        prompt: str,
    ) -> "CompiledProductionStep": ...


@dataclass(frozen=True, slots=True)
class CompiledProductionStep:
    current_design_state: DevelopedDesignState
    lifecycle: SemanticGeometryLifecycleResult | InitialSemanticGeometryResult
    invocation_envelopes: tuple[InvocationEnvelope, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.current_design_state, DevelopedDesignState):
            raise TypeError("current_design_state must be DevelopedDesignState")
        if not isinstance(
            self.lifecycle,
            (SemanticGeometryLifecycleResult, InitialSemanticGeometryResult),
        ):
            raise TypeError("lifecycle must be a semantic-geometry result")
        if not isinstance(self.invocation_envelopes, tuple) or any(
            not isinstance(item, InvocationEnvelope)
            for item in self.invocation_envelopes
        ):
            raise TypeError("invocation_envelopes contains an invalid item")


@dataclass(frozen=True, slots=True)
class ProductionRuntimeResult:
    run: RunRef
    raw_request: ProjectRecordRef
    step_id: str
    archive: ProductionTransitionArchive

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunRef):
            raise TypeError("run must be RunRef")
        if not isinstance(self.raw_request, ProjectRecordRef):
            raise TypeError("raw_request must be ProjectRecordRef")
        require_identifier(self.step_id, "step_id")
        if not isinstance(self.archive, ProductionTransitionArchive):
            raise TypeError("archive must be ProductionTransitionArchive")

    @property
    def resumed(self) -> bool:
        return self.archive.resumed

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionRuntimeResult@1",
            "project_id": self.run.project_id,
            "run_id": self.run.run_id,
            "base": {
                "project_id": self.run.base.project_id,
                "version": self.run.base.version,
                "state_sha256": self.run.base.require_digest(),
            },
            "raw_request_ref": self.raw_request.uri,
            "step_id": self.step_id,
            "intent_digest": self.archive.intent_digest,
            "transition_digest": self.archive.transition_digest,
            "checkpoint_ref": self.archive.checkpoint_ref.uri,
            "record_refs": [item.ref.uri for item in self.archive.records],
            "resumed": self.resumed,
            "canonical_write_authority": False,
        }


async def run_or_resume_production_step(
    repository: ProductionRuntimePort,
    *,
    run: RunRef,
    raw_request: ProjectRecordRef,
    prompt: str,
    step_id: str,
    compiler: ProductionStepCompiler,
) -> ProductionRuntimeResult:
    """Resume before provider execution, otherwise compile and checkpoint once."""

    require_identifier(step_id, "step_id")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty text")
    if not isinstance(raw_request, ProjectRecordRef):
        raise TypeError("raw_request must be ProjectRecordRef")
    durable_run = repository.load_run(run.run_id)
    if durable_run != run:
        raise ProductionRuntimeError("supplied run does not match P036")
    if raw_request.project_id != run.project_id:
        raise ProductionRuntimeError("raw request belongs to another project")
    request_payload = repository.load_json(raw_request)
    if set(request_payload) != {"schema", "prompt"} or (
        request_payload.get("schema") != "RawProjectRequest@1"
        or request_payload.get("prompt") != prompt
    ):
        raise ProductionRuntimeError(
            "raw prompt does not match its immutable project input"
        )
    intent_refs = _intent_refs(compiler, run)
    intent_digest = production_step_intent_digest(
        run,
        raw_request=raw_request,
        step_id=step_id,
        compiler_context_refs=intent_refs,
    )
    completed = load_production_transition(
        repository,
        run=run,
        intent_digest=intent_digest,
    )
    if completed is not None:
        return ProductionRuntimeResult(
            run=run,
            raw_request=raw_request,
            step_id=step_id,
            archive=completed,
        )

    compile_step = getattr(compiler, "compile", None)
    if not callable(compile_step):
        raise TypeError("compiler must implement ProductionStepCompiler")
    evidence_cursor = compiler.invocation_evidence_cursor()
    try:
        compiled = await compile_step(
            run=run,
            raw_request=raw_request,
            prompt=prompt,
        )
    except ProductionStepCompilationFailed as exc:
        envelopes = compiler.invocation_evidence_since(evidence_cursor)
        if not envelopes:
            raise
        attempt = persist_failed_production_attempt(
            repository,
            run=run,
            intent_digest=intent_digest,
            step_id=step_id,
            error_code=exc.error_code,
            message=str(exc),
            invocation_envelopes=envelopes,
        )
        raise ProductionRuntimeStepFailed(attempt) from exc
    if not isinstance(compiled, CompiledProductionStep):
        raise TypeError("production compiler returned an invalid step")
    archive = persist_compiled_production_transition(
        repository,
        run=run,
        intent_digest=intent_digest,
        current_design_state=compiled.current_design_state,
        result=compiled.lifecycle,
        invocation_envelopes=compiled.invocation_envelopes,
    )
    return ProductionRuntimeResult(
        run=run,
        raw_request=raw_request,
        step_id=step_id,
        archive=archive,
    )


def production_step_intent_digest(
    run: RunRef,
    *,
    raw_request: ProjectRecordRef,
    step_id: str,
    compiler_context_refs: tuple[ProjectRecordRef, ...] = (),
) -> str:
    """Name one exact raw-request/current-context production step."""

    require_identifier(step_id, "step_id")
    if not isinstance(raw_request, ProjectRecordRef):
        raise TypeError("raw_request must be ProjectRecordRef")
    if raw_request.project_id != run.project_id:
        raise ProductionRuntimeError("raw request belongs to another project")
    if not isinstance(compiler_context_refs, tuple) or any(
        not isinstance(item, ProjectRecordRef) for item in compiler_context_refs
    ):
        raise TypeError("compiler_context_refs must contain record refs")
    if any(item.project_id != run.project_id for item in compiler_context_refs):
        raise ProductionRuntimeError("compiler context crosses project boundary")
    if compiler_context_refs != tuple(
        sorted(set(compiler_context_refs), key=lambda item: item.uri)
    ):
        raise ProductionRuntimeError(
            "compiler context refs must be sorted and unique"
        )
    return production_intent_digest(
        run,
        intent={
            "schema": "ProductionRuntimeStepIntent@1",
            "step_id": step_id,
            "raw_request": {
                "project_id": raw_request.project_id,
                "relative_path": raw_request.relative_path,
                "sha256": raw_request.sha256,
                "media_type": raw_request.media_type,
            },
            "compiler_context_refs": [
                {
                    "project_id": ref.project_id,
                    "relative_path": ref.relative_path,
                    "sha256": ref.sha256,
                    "media_type": ref.media_type,
                }
                for ref in compiler_context_refs
            ],
        },
    )


def _intent_refs(
    compiler: ProductionStepCompiler,
    run: RunRef,
) -> tuple[ProjectRecordRef, ...]:
    refs = getattr(compiler, "intent_record_refs", ())
    if not isinstance(refs, tuple) or any(
        not isinstance(item, ProjectRecordRef) for item in refs
    ):
        raise TypeError("compiler intent_record_refs must contain record refs")
    if any(item.project_id != run.project_id for item in refs):
        raise ProductionRuntimeError("compiler context crosses project boundary")
    if refs != tuple(sorted(set(refs), key=lambda item: item.uri)):
        raise ProductionRuntimeError(
            "compiler context refs must be sorted and unique"
        )
    return refs
