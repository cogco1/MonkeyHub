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
    compile_spatial_authoring_reference_contract,
    compile_spatial_authoring_validation_contract,
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
    ComponentMaturity,
    ConstraintResponseStatus,
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


def semantic_spatial_authoring_contract(
    *,
    with_stage_declarations: bool = False,
) -> dict[str, object]:
    """Return the machine-facing output envelope and ownership contract.

    When a stage declaration contract gates the request, the output
    envelope itself must name ``stage_declarations`` — the format
    contract and the declaration contract may never disagree about the
    required field set.
    """

    required = [
        "schema",
        "exact_base_state_digest",
        "context_digest",
        "proposal",
    ]
    if with_stage_declarations:
        required.append("stage_declarations")
    return {
        "schema": "SemanticSpatialAuthoringContract@2",
        "required_output_schema": "SemanticSpatialAuthoringOutput@1",
        "required_output_fields": required,
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
        "proposal_fixed_values": {
            "proposal_only": True,
            "selected": False,
            "hard_usability_verdict": None,
            "design_development_complete": False,
            "execution_ready": False,
        },
        "proposal_nested_contracts": {
            "grid_basis": {
                "type": "object",
                "exact_fields": {
                    "horizontal_area_per_cell": "positive finite number",
                    "area_unit": "non-empty string",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "footprint_cells": {
                "type": "non-empty array",
                "item": "exactly [integer x, integer z]",
            },
            "levels": {
                "type": "non-empty array",
                "item_exact_fields": {
                    "level_id": "local-id string",
                    "base_y": "integer",
                    "height": "positive integer",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "volumes": {
                "type": "non-empty array",
                "item_exact_fields": {
                    "volume_id": "local-id string",
                    "bounds": {
                        "type": "object",
                        "exact_fields": {
                            "minimum": "exactly [integer x, integer y, integer z]",
                            "maximum": "exactly [integer x, integer y, integer z]",
                        },
                    },
                    "level_ids": "non-empty array of local-id strings",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "zones": {
                "type": "non-empty array",
                "item_exact_fields": {
                    "zone_id": "local-id string",
                    "program_node_refs": "non-empty array of logical-ref strings",
                    "level_ids": "non-empty array of local-id strings",
                    "volume_ids": "non-empty array of local-id strings",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "components": {
                "type": "non-empty array",
                "item_exact_fields": {
                    "schema": "fixed string DesignComponent@1",
                    "component_id": "local-id string",
                    "parent_component_id": "local-id string or null",
                    "semantic_kind": "local-id string",
                    "intent": "non-empty string",
                    "maturity": {
                        "type": "enum string",
                        "values": [item.value for item in ComponentMaturity],
                    },
                    "revision": "non-negative integer",
                    "volume_ids": "array of local-id strings; empty allowed",
                    "unresolved_child_roles": "array of local-id strings; empty allowed",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "connections": {
                "type": "array",
                "item_exact_fields": {
                    "connection_id": "local-id string",
                    "source_zone_id": "local-id string",
                    "target_zone_id": "different local-id string",
                    "relationship_refs": "non-empty array of logical-ref strings",
                    "directed": "boolean",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
            "constraint_responses": {
                "type": "array",
                "item_exact_fields": {
                    "response_id": "local-id string",
                    "constraint_ref": "logical-ref string",
                    "status": {
                        "type": "enum string",
                        "values": [
                            item.value for item in ConstraintResponseStatus
                        ],
                    },
                    "rationale": "non-empty string",
                    "source_refs": "non-empty array of logical-ref strings",
                },
            },
        },
        "component_geometry_invariants": [
            "components form exactly one rooted acyclic tree",
            "every volume id exists and is owned by exactly one component",
            "component and volume source refs are present in evidence_refs",
            "proposal authority fields equal proposal_fixed_values exactly",
        ],
    }


def semantic_spatial_authoring_output(
    request: ModelInvocationRequest,
    proposal: SpatialOptionProposal,
    stage_declarations: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the exact output envelope used by scripted or remote providers."""

    if not isinstance(request, ModelInvocationRequest):
        raise TypeError("request must be ModelInvocationRequest")
    if request.phase is not ModelPhase.SPATIAL_PROPOSAL:
        raise ValueError("request is not a spatial proposal request")
    if not isinstance(proposal, SpatialOptionProposal):
        raise TypeError("proposal must be SpatialOptionProposal")
    output: dict[str, object] = {
        "schema": "SemanticSpatialAuthoringOutput@1",
        "exact_base_state_digest": request.checkpoint_digest,
        "context_digest": request.context_digest,
        "proposal": proposal.to_dict(),
    }
    if stage_declarations is not None:
        output["stage_declarations"] = dict(stage_declarations)
    return output


def semantic_spatial_repair_feedback(
    result: SemanticSpatialAuthoringResult,
) -> dict[str, object]:
    """Bind one deterministic rejection for a complete model-authored replacement."""

    if not isinstance(result, SemanticSpatialAuthoringResult):
        raise TypeError("result must be SemanticSpatialAuthoringResult")
    receipt = result.receipt
    model_receipt = receipt.model_receipt
    if (
        receipt.status is not SemanticSpatialAuthoringStatus.REJECTED
        or model_receipt.status is not ModelInvocationStatus.SUCCESS
        or model_receipt.request != receipt.request
        or receipt.error_code
        == "spatial_authoring.provider_request_mismatch"
        or not isinstance(model_receipt.output, Mapping)
    ):
        raise ValueError(
            "repair feedback requires one bound successful provider output "
            "rejected by deterministic authoring validation"
        )
    rejected_output = dict(model_receipt.output)
    return {
        "schema": "SemanticSpatialRepairFeedback@1",
        "prior_request_id": receipt.request.request_id,
        "prior_model_receipt_id": model_receipt.receipt_id,
        "exact_base_state_digest": receipt.request.checkpoint_digest,
        "prior_context_digest": receipt.request.context_digest,
        "rejected_output_digest": _digest(rejected_output),
        "rejected_output": rejected_output,
        "rejection_code": receipt.error_code,
        "diagnostic": receipt.message,
        "instructions": (
            "Return one complete replacement output satisfying the unchanged "
            "contracts. Do not return a patch and do not reuse invalid fields."
        ),
        "complete_replacement_required": True,
        "field_patch_authority": False,
        "validation_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
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
    repair_feedback: Mapping[str, object] | None = None,
    alternative_context: Mapping[str, object] | None = None,
    revision_context: Mapping[str, object] | None = None,
    declaration_contract: "StageDeclarationContract | None" = None,
    decision_basis: Mapping[str, object] | None = None,
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
        "output_contract": semantic_spatial_authoring_contract(
            with_stage_declarations=declaration_contract is not None,
        ),
        "reference_contract": compile_spatial_authoring_reference_contract(
            state=state,
            phase_gate=phase_gate,
            program=program,
            site_context=site_context,
            build_policy=build_policy,
        ).to_dict(),
        "validation_contract": compile_spatial_authoring_validation_contract(
            program=program,
            site_context=site_context,
        ),
    }
    if declaration_contract is not None:
        from archflow.capabilities.declaration import (
            StageDeclarationContract,
        )

        if not isinstance(declaration_contract, StageDeclarationContract):
            raise TypeError(
                "declaration_contract must be StageDeclarationContract"
            )
        prompt["declaration_contract"] = declaration_contract.to_dict()
    if decision_basis is not None:
        prompt["decision_basis"] = _validated_decision_basis(decision_basis)
    if repair_feedback is not None:
        prompt["repair_feedback"] = _validated_repair_feedback(
            repair_feedback,
            exact_base_state_digest=state.state_digest,
        )
    if alternative_context is not None:
        prompt["alternative_context"] = _validated_alternative_context(
            alternative_context
        )
    if revision_context is not None:
        prompt["revision_context"] = _validated_revision_context(
            revision_context
        )
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
        expected_fields = {
            "schema",
            "exact_base_state_digest",
            "context_digest",
            "proposal",
        }
        if declaration_contract is not None:
            expected_fields = expected_fields | {"stage_declarations"}
        if set(output) != expected_fields:
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
    if declaration_contract is not None:
        from archflow.capabilities.declaration import (
            DeclarationError,
            validate_stage_declarations,
        )

        try:
            validate_stage_declarations(
                declaration_contract,
                _mapping(
                    output["stage_declarations"], "stage declarations"
                ),
                proposal,
            )
        except (DeclarationError, KeyError, TypeError, ValueError) as exc:
            return _failed(
                request,
                model_receipt,
                SemanticSpatialAuthoringStatus.REJECTED,
                "spatial_authoring.declaration_rejected",
                str(exc),
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


def _validated_repair_feedback(
    value: Mapping[str, object],
    *,
    exact_base_state_digest: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("repair_feedback must be a mapping")
    expected = {
        "schema",
        "prior_request_id",
        "prior_model_receipt_id",
        "exact_base_state_digest",
        "prior_context_digest",
        "rejected_output_digest",
        "rejected_output",
        "rejection_code",
        "diagnostic",
        "instructions",
        "complete_replacement_required",
        "field_patch_authority",
        "validation_authority",
        "persistence_authority",
        "canonical_write_authority",
    }
    if set(value) != expected or value.get("schema") != "SemanticSpatialRepairFeedback@1":
        raise ValueError("semantic-spatial repair feedback schema drifted")
    rejected_output = value.get("rejected_output")
    if not isinstance(rejected_output, Mapping) or (
        value.get("rejected_output_digest") != _digest(rejected_output)
    ):
        raise ValueError("semantic-spatial rejected output digest changed")
    if value.get("exact_base_state_digest") != exact_base_state_digest:
        raise ValueError("semantic-spatial repair feedback is stale")
    for field in (
        "prior_request_id",
        "prior_model_receipt_id",
        "prior_context_digest",
        "rejection_code",
        "diagnostic",
        "instructions",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"repair feedback {field} must be non-empty text")
    if (
        value.get("complete_replacement_required") is not True
        or value.get("field_patch_authority") is not False
        or value.get("validation_authority") is not False
        or value.get("persistence_authority") is not False
        or value.get("canonical_write_authority") is not False
    ):
        raise ValueError("semantic-spatial repair feedback acquired authority")
    return dict(value)


_MAX_DECISION_BASIS_CHARS = 20_000


def _validated_decision_basis(value: Mapping[str, object]) -> dict:
    """Bounded, decision-keyed adopted-fact slices for the prompt.

    The selection is made upstream (one shard per contract field); this
    guard only enforces shape and the hard size bound so a prompt can
    never quietly swallow the whole adoption store.
    """

    if not isinstance(value, Mapping):
        raise TypeError("decision_basis must be a mapping")
    validated: dict[str, list] = {}
    for decision_ref, facts in value.items():
        if not isinstance(decision_ref, str) or not decision_ref.strip():
            raise ValueError("decision_basis keys must be decision refs")
        if not isinstance(facts, (list, tuple)) or not facts:
            raise ValueError(
                f"{decision_ref}: decision basis must be a non-empty list"
            )
        rows = []
        for fact in facts:
            if not isinstance(fact, Mapping):
                raise ValueError(
                    f"{decision_ref}: each basis fact must be a mapping"
                )
            rows.append({str(k): fact[k] for k in sorted(fact)})
        validated[decision_ref] = rows
    encoded = json.dumps(validated, sort_keys=True, default=str)
    if len(encoded) > _MAX_DECISION_BASIS_CHARS:
        raise ValueError(
            "decision_basis exceeds the prompt bound "
            f"({len(encoded)} > {_MAX_DECISION_BASIS_CHARS} chars); "
            "select fewer shards"
        )
    return validated


def _validated_alternative_context(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("alternative_context must be a mapping")
    expected = {
        "schema",
        "excluded_option_ids",
        "excluded_option_digests",
        "excluded_topology_signatures",
        "existing_option_projection",
        "instructions",
        "complete_alternative_required",
        "option_mutation_authority",
        "selection_authority",
        "validation_authority",
        "persistence_authority",
        "canonical_write_authority",
    }
    if set(value) != expected or value.get("schema") != "SpatialAlternativeAuthoringContext@1":
        raise ValueError("spatial alternative context schema drifted")
    for field in (
        "excluded_option_ids",
        "excluded_option_digests",
        "excluded_topology_signatures",
    ):
        items = value.get(field)
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, str) or not item for item in items)
            or items != sorted(set(items))
        ):
            raise ValueError(f"{field} must be a non-empty sorted unique list")
    projection = value.get("existing_option_projection")
    if not isinstance(projection, Mapping) or (
        projection.get("schema") != "SchematicOptionDecisionProjection@1"
        or projection.get("option_id") not in value["excluded_option_ids"]
        or projection.get("option_digest") not in value["excluded_option_digests"]
        or projection.get("topology_signature")
        not in value["excluded_topology_signatures"]
        or projection.get("decision_projection_only") is not True
    ):
        raise ValueError("existing option projection is not exactly excluded")
    if not isinstance(value.get("instructions"), str) or not value["instructions"]:
        raise ValueError("alternative context instructions must be non-empty text")
    if (
        value.get("complete_alternative_required") is not True
        or value.get("option_mutation_authority") is not False
        or value.get("selection_authority") is not False
        or value.get("validation_authority") is not False
        or value.get("persistence_authority") is not False
        or value.get("canonical_write_authority") is not False
    ):
        raise ValueError("spatial alternative context acquired authority")
    return dict(value)


def _validated_revision_context(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("revision_context must be a mapping")
    expected = {
        "schema",
        "predecessor_proposal",
        "predecessor_proposal_digest",
        "architectural_contract_digest",
        "architectural_receipt_digest",
        "failed_mandatory_findings",
        "instructions",
        "complete_successor_required",
        "preserve_option_identity",
        "component_patch_authority",
        "geometry_patch_authority",
        "selection_authority",
        "validation_authority",
        "persistence_authority",
        "canonical_write_authority",
    }
    if (
        set(value) != expected
        or value.get("schema") != "SpatialArchitecturalRevisionContext@1"
    ):
        raise ValueError("spatial architectural revision context schema drifted")
    predecessor = value.get("predecessor_proposal")
    if not isinstance(predecessor, Mapping):
        raise TypeError("predecessor_proposal must be a mapping")
    parsed = SpatialOptionProposal.from_dict(predecessor)
    if value.get("predecessor_proposal_digest") != parsed.proposal_digest:
        raise ValueError("predecessor proposal digest changed")
    for field in (
        "architectural_contract_digest",
        "architectural_receipt_digest",
    ):
        _sha256(value.get(field), field)
    findings = value.get("failed_mandatory_findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("revision context needs failed mandatory findings")
    criterion_ids: list[str] = []
    for failure in findings:
        if not isinstance(failure, Mapping) or set(failure) != {
            "schema", "criterion", "finding"
        }:
            raise TypeError("architectural revision failure must be an exact mapping")
        if failure.get("schema") != "ArchitecturalRevisionFailure@1":
            raise ValueError("architectural revision failure schema changed")
        criterion = failure.get("criterion")
        finding = failure.get("finding")
        if not isinstance(criterion, Mapping) or not isinstance(finding, Mapping):
            raise TypeError("revision failure criterion and finding must be mappings")
        if (
            finding.get("schema") != "ArchitecturalUsabilityFinding@1"
            or finding.get("status") != "fail"
            or finding.get("mandatory") is not True
        ):
            raise ValueError("revision context includes a non-failed blocker")
        criterion_id = finding.get("criterion_id")
        if (
            not isinstance(criterion_id, str)
            or not criterion_id
            or criterion.get("schema") != "ProjectArchitecturalCriterion@1"
            or criterion.get("criterion_id") != criterion_id
            or criterion.get("mandatory") is not True
            or criterion.get("expected_json") != finding.get("expected_json")
            or criterion.get("unit") != finding.get("unit")
        ):
            raise ValueError("failed finding criterion_id must be non-empty")
        criterion_ids.append(criterion_id)
    if criterion_ids != sorted(set(criterion_ids)):
        raise ValueError("failed findings must be sorted and unique")
    if not isinstance(value.get("instructions"), str) or not value["instructions"]:
        raise ValueError("revision context instructions must be non-empty text")
    if (
        value.get("complete_successor_required") is not True
        or value.get("preserve_option_identity") is not True
        or value.get("component_patch_authority") is not False
        or value.get("geometry_patch_authority") is not False
        or value.get("selection_authority") is not False
        or value.get("validation_authority") is not False
        or value.get("persistence_authority") is not False
        or value.get("canonical_write_authority") is not False
    ):
        raise ValueError("spatial architectural revision context acquired authority")
    return dict(value)


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
