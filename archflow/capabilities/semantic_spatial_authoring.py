"""Typed provider boundary for semantic and coarse-geometry co-authoring.

The provider proposes one existing ``SpatialOptionProposal``.  It never sees a
writer or path, and deterministic validation decides whether its component
tree and massing volumes form one current schematic option.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.capabilities.spatial import (
    SpatialCompilationError,
    validate_spatial_authoring_context,
    validate_spatial_option,
)
from archflow.state.build_policy import BuildPolicy
from archflow.state.design_maturity import (
    DesignMaturityState,
    PhaseGateReceipt,
)
from archflow.state.design_program import DesignProgram
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.site_context import SiteContext
from archflow.state.spatial import (
    SpatialOptionProposal,
    SpatialProposalError,
    SchematicOption,
)


AUTHORING_INSTRUCTIONS = (
    "Author one schematic option from only the supplied current records. "
    "Create the existing DesignComponent tree and its coarse massing volumes "
    "in the same proposal. Every volume must have exactly one component "
    "owner. Preserve project evidence references and return only the required "
    "output object; do not select, persist, execute, or claim validation."
)


class SemanticSpatialAuthoringStatus(StrEnum):
    ACCEPTED = "accepted"
    PROVIDER_FAILED = "provider_failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SemanticSpatialAuthoringReceipt:
    receipt_id: str
    status: SemanticSpatialAuthoringStatus
    request: ModelInvocationRequest
    model_receipt: ModelInvocationReceipt
    proposal_digest: str | None
    option_digest: str | None
    error_code: str | None = None
    message: str | None = None

    SCHEMA = "SemanticSpatialAuthoringReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, str) or not self.receipt_id:
            raise ValueError("receipt_id must be non-empty")
        if not isinstance(self.status, SemanticSpatialAuthoringStatus):
            raise TypeError("status must be SemanticSpatialAuthoringStatus")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(self.model_receipt, ModelInvocationReceipt):
            raise TypeError("model_receipt must be ModelInvocationReceipt")
        for value, field in (
            (self.proposal_digest, "proposal_digest"),
            (self.option_digest, "option_digest"),
        ):
            if value is not None:
                _sha256(value, field)
        if self.status is SemanticSpatialAuthoringStatus.ACCEPTED:
            if (
                self.proposal_digest is None
                or self.option_digest is None
                or self.error_code is not None
            ):
                raise ValueError("accepted authoring receipt is incomplete")
        elif (
            self.proposal_digest is not None
            or self.option_digest is not None
            or self.error_code is None
        ):
            raise ValueError("failed authoring receipt is inconsistent")
        if self.message is not None and (
            not isinstance(self.message, str)
            or not self.message
            or len(self.message) > 2_000
        ):
            raise ValueError("message must contain 1..2000 characters")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "model_receipt": self.model_receipt.to_dict(),
            "proposal_digest": self.proposal_digest,
            "option_digest": self.option_digest,
            "error_code": self.error_code,
            "message": self.message,
            "proposal_only": True,
            "selection_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class SemanticSpatialAuthoringResult:
    receipt: SemanticSpatialAuthoringReceipt
    proposal: SpatialOptionProposal | None = None
    option: SchematicOption | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, SemanticSpatialAuthoringReceipt):
            raise TypeError("receipt must be SemanticSpatialAuthoringReceipt")
        accepted = (
            self.receipt.status is SemanticSpatialAuthoringStatus.ACCEPTED
        )
        if accepted:
            if not isinstance(self.proposal, SpatialOptionProposal):
                raise TypeError("accepted result requires proposal")
            if not isinstance(self.option, SchematicOption):
                raise TypeError("accepted result requires option")
            if (
                self.receipt.proposal_digest
                != self.proposal.proposal_digest
                or self.receipt.option_digest != self.option.option_digest
            ):
                raise ValueError("accepted result digests disagree")
        elif self.proposal is not None or self.option is not None:
            raise ValueError("failed result cannot carry an accepted option")


def semantic_spatial_authoring_contract() -> dict[str, object]:
    """Return the machine-facing output envelope and ownership contract."""

    return {
        "schema": "SemanticSpatialAuthoringContract@1",
        "required_output_schema": "SemanticSpatialAuthoringOutput@1",
        "required_output_fields": [
            "schema",
            "exact_base_state_digest",
            "context_digest",
            "proposal",
        ],
        "proposal_schema": SpatialOptionProposal.SCHEMA,
        "proposal_required_fields": [
            "schema",
            "option_id",
            "label",
            "program_scenario_ref",
            "footprint_range_ref",
            "grid_basis",
            "footprint_cells",
            "levels",
            "volumes",
            "zones",
            "components",
            "connections",
            "constraint_responses",
            "typology_hypothesis",
            "palette_refs",
            "rationale",
            "responds_to_refs",
            "expert_advice_refs",
            "evidence_refs",
            "proposal_only",
            "selected",
            "hard_usability_verdict",
            "design_development_complete",
            "execution_ready",
        ],
        "component_geometry_invariants": [
            "components form exactly one rooted acyclic tree",
            "every volume id exists and is owned by exactly one component",
            "component and volume source refs are present in evidence_refs",
            "proposal_only is true and all downstream authority flags are false",
        ],
    }


def semantic_spatial_authoring_output(
    request: ModelInvocationRequest,
    proposal: SpatialOptionProposal,
) -> dict[str, object]:
    """Build the exact output envelope used by scripted or remote providers."""

    if not isinstance(request, ModelInvocationRequest):
        raise TypeError("request must be ModelInvocationRequest")
    if request.phase is not ModelPhase.SPATIAL_PROPOSAL:
        raise ValueError("request is not a spatial proposal request")
    if not isinstance(proposal, SpatialOptionProposal):
        raise TypeError("proposal must be SpatialOptionProposal")
    return {
        "schema": "SemanticSpatialAuthoringOutput@1",
        "exact_base_state_digest": request.checkpoint_digest,
        "context_digest": request.context_digest,
        "proposal": proposal.to_dict(),
    }


async def author_semantic_spatial_option(
    provider: AsyncModelProvider,
    *,
    request_id: str,
    state: OperationalMarkovState,
    maturity: DesignMaturityState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> SemanticSpatialAuthoringResult:
    """Ask one provider for a co-authored component and massing proposal."""

    validate_spatial_authoring_context(
        state=state,
        maturity=maturity,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    prompt = {
        "schema": "SemanticSpatialAuthoringPrompt@1",
        "instructions": AUTHORING_INSTRUCTIONS,
        "exact_base_state_digest": state.state_digest,
        "maturity": maturity.to_dict(),
        "phase_gate": phase_gate.to_dict(),
        "program": program.to_dict(),
        "site_context": site_context.to_dict(),
        "build_policy": build_policy.to_dict(),
        "output_contract": semantic_spatial_authoring_contract(),
    }
    request = ModelInvocationRequest.create(
        request_id=request_id,
        phase=ModelPhase.SPATIAL_PROPOSAL,
        checkpoint_digest=state.state_digest,
        context_digest=_digest(prompt),
        payload=prompt,
    )
    model_receipt = await provider.invoke(request)
    if not isinstance(model_receipt, ModelInvocationReceipt):
        raise TypeError("provider must return ModelInvocationReceipt")
    if model_receipt.request != request:
        return _failed(
            request,
            model_receipt,
            SemanticSpatialAuthoringStatus.REJECTED,
            "spatial_authoring.provider_request_mismatch",
            "provider receipt does not bind the current authoring request",
        )
    if model_receipt.status is not ModelInvocationStatus.SUCCESS:
        return _failed(
            request,
            model_receipt,
            SemanticSpatialAuthoringStatus.PROVIDER_FAILED,
            model_receipt.error_code or "spatial_authoring.provider_failed",
            model_receipt.message,
        )
    try:
        output = _mapping(model_receipt.output, "spatial authoring output")
        if set(output) != {
            "schema",
            "exact_base_state_digest",
            "context_digest",
            "proposal",
        }:
            raise ValueError("spatial authoring output fields drifted")
        if output["schema"] != "SemanticSpatialAuthoringOutput@1":
            raise ValueError("spatial authoring output schema is unsupported")
        if (
            output["exact_base_state_digest"] != state.state_digest
            or output["context_digest"] != request.context_digest
        ):
            return _failed(
                request,
                model_receipt,
                SemanticSpatialAuthoringStatus.REJECTED,
                "spatial_authoring.stale_base",
                "provider output does not bind the current state and context",
            )
        proposal = SpatialOptionProposal.from_dict(output["proposal"])
        option = validate_spatial_option(
            state=state,
            maturity=maturity,
            phase_gate=phase_gate,
            program=program,
            site_context=site_context,
            build_policy=build_policy,
            proposal=proposal,
        )
    except SpatialProposalError as exc:
        return _rejected_proposal(request, model_receipt, exc)
    except SpatialCompilationError as exc:
        return _rejected_compilation(request, model_receipt, exc)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _failed(
            request,
            model_receipt,
            SemanticSpatialAuthoringStatus.REJECTED,
            "spatial_authoring.malformed_output",
            f"{type(exc).__name__}: {exc}",
        )
    return SemanticSpatialAuthoringResult(
        receipt=_receipt(
            request,
            model_receipt,
            SemanticSpatialAuthoringStatus.ACCEPTED,
            proposal_digest=proposal.proposal_digest,
            option_digest=option.option_digest,
        ),
        proposal=proposal,
        option=option,
    )


def _rejected_proposal(
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    exc: SpatialProposalError,
) -> SemanticSpatialAuthoringResult:
    message = str(exc)
    lowered = message.lower()
    if any(
        token in lowered
        for token in ("owner", "volume", "semantic owner")
    ):
        code = "spatial_authoring.ownership_rejected"
    elif any(
        token in lowered for token in ("parent", "tree", "cycle", "ancestry")
    ):
        code = "spatial_authoring.topology_rejected"
    elif "source" in lowered or "evidence" in lowered:
        code = "spatial_authoring.source_rejected"
    else:
        code = "spatial_authoring.proposal_rejected"
    return _failed(
        request,
        model_receipt,
        SemanticSpatialAuthoringStatus.REJECTED,
        code,
        f"{type(exc).__name__}: {message}",
    )


def _rejected_compilation(
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    exc: SpatialCompilationError,
) -> SemanticSpatialAuthoringResult:
    message = str(exc)
    code = (
        "spatial_authoring.source_rejected"
        if any(
            token in message.lower()
            for token in ("evidence", "unknown", "stale reference")
        )
        else "spatial_authoring.context_rejected"
    )
    return _failed(
        request,
        model_receipt,
        SemanticSpatialAuthoringStatus.REJECTED,
        code,
        f"{type(exc).__name__}: {message}",
    )


def _failed(
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    status: SemanticSpatialAuthoringStatus,
    error_code: str,
    message: str | None,
) -> SemanticSpatialAuthoringResult:
    return SemanticSpatialAuthoringResult(
        receipt=_receipt(
            request,
            model_receipt,
            status,
            error_code=error_code,
            message=message,
        )
    )


def _receipt(
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    status: SemanticSpatialAuthoringStatus,
    *,
    proposal_digest: str | None = None,
    option_digest: str | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> SemanticSpatialAuthoringReceipt:
    identity = {
        "request_id": request.request_id,
        "model_receipt_id": model_receipt.receipt_id,
        "status": status.value,
        "proposal_digest": proposal_digest,
        "option_digest": option_digest,
        "error_code": error_code,
    }
    return SemanticSpatialAuthoringReceipt(
        receipt_id=f"semantic-spatial-{_digest(identity)[:24]}",
        status=status,
        request=request,
        model_receipt=model_receipt,
        proposal_digest=proposal_digest,
        option_digest=option_digest,
        error_code=error_code,
        message=message,
    )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value
