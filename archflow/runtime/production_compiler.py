"""Formal root compiler from current project state to initial semantic geometry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)

from archflow.capabilities.design_development import initialize_developed_design
from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    produce_geometry_program_proposal,
)
from archflow.capabilities.semantic_spatial_authoring import (
    SemanticSpatialAuthoringStatus,
    author_semantic_spatial_option,
    semantic_spatial_repair_feedback,
)
from archflow.capabilities.spatial import compile_spatial_options
from archflow.production import (
    AuthorizedAsyncModelProvider,
    InvocationEvidenceCollector,
    InvocationEnvelope,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef, require_identifier
from archflow.realization import RealizationStatus, realize_geometry
from archflow.runtime.production_runtime import (
    CompiledProductionStep,
    ProductionAuthoringContext,
    ProductionStepCompilationFailed,
)
from archflow.runtime.semantic_geometry_lifecycle import (
    bind_initial_semantic_geometry,
)
from archflow.state.design_portfolio import (
    SelectionPolicy,
    compile_selected_branch_handoff,
    initialize_design_portfolio,
    select_branch,
)
from archflow.state.developed_design import (
    DevelopmentDiscipline,
    DevelopmentObligation,
    DevelopmentObligationPriority,
    DevelopmentObligationStatus,
    SelectedSchematicInput,
)
from archflow.state.spatial import (
    SchematicOption,
    SchematicOptionSet,
    SpatialProposalError,
)


class ProductionRootCompilationError(ProductionStepCompilationFailed):
    """The current project state could not reach initial semantic geometry."""


class SchematicSelectionStatus(StrEnum):
    SELECTED = "selected"
    PROVIDER_FAILED = "provider_failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SchematicSelectionReceipt:
    selection_id: str
    status: SchematicSelectionStatus
    request: ModelInvocationRequest
    model_receipt: ModelInvocationReceipt
    option_set_digest: str
    selected_option_id: str | None
    rationale: str | None
    error_code: str | None = None

    SCHEMA = "SchematicSelectionReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.selection_id, "selection_id")
        if not isinstance(self.status, SchematicSelectionStatus):
            raise TypeError("status must be SchematicSelectionStatus")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(self.model_receipt, ModelInvocationReceipt):
            raise TypeError("model_receipt must be ModelInvocationReceipt")
        _selection_sha(self.option_set_digest, "option_set_digest")
        if self.status is SchematicSelectionStatus.SELECTED:
            if (
                self.selected_option_id is None
                or self.rationale is None
                or self.error_code is not None
            ):
                raise ValueError("selected receipt is incomplete")
            require_identifier(self.selected_option_id, "selected_option_id")
            _selection_text(self.rationale, "rationale")
        elif (
            self.selected_option_id is not None
            or self.rationale is not None
            or self.error_code is None
        ):
            raise ValueError("failed selection receipt is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return _selection_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "selection_id": self.selection_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "model_receipt": self.model_receipt.to_dict(),
            "option_set_digest": self.option_set_digest,
            "selected_option_id": self.selected_option_id,
            "rationale": self.rationale,
            "error_code": self.error_code,
            "proposal_only": True,
            "selection_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class SchematicSelectionResult:
    receipt: SchematicSelectionReceipt
    option: SchematicOption | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, SchematicSelectionReceipt):
            raise TypeError("receipt must be SchematicSelectionReceipt")
        if self.receipt.status is SchematicSelectionStatus.SELECTED:
            if not isinstance(self.option, SchematicOption):
                raise TypeError("selected result requires SchematicOption")
            if self.option.option_id != self.receipt.selected_option_id:
                raise ValueError("selected option and receipt disagree")
        elif self.option is not None:
            raise ValueError("failed result cannot expose an option")


def schematic_selection_output(
    request: ModelInvocationRequest,
    *,
    selected_option_id: str,
    rationale: str,
) -> dict[str, object]:
    require_identifier(selected_option_id, "selected_option_id")
    _selection_text(rationale, "rationale")
    return {
        "schema": "SchematicOptionSelectionOutput@1",
        "exact_option_set_digest": request.checkpoint_digest,
        "selected_option_id": selected_option_id,
        "rationale": rationale,
    }


def schematic_option_decision_projection(
    option: SchematicOption,
) -> dict[str, object]:
    """Project one validated option into a bounded, identity-bound choice view.

    The projection is not reloadable as a schematic option and carries no
    validation or mutation authority.  The original ``SchematicOption`` remains
    the only value that can be returned after deterministic id validation.
    """

    if not isinstance(option, SchematicOption):
        raise TypeError("option must be SchematicOption")
    proposal = option.proposal.to_dict()
    components = proposal["components"]
    levels = proposal["levels"]
    volumes = proposal["volumes"]
    zones = proposal["zones"]
    connections = proposal["connections"]
    responses = proposal["constraint_responses"]
    assert isinstance(components, list)
    assert isinstance(levels, list)
    assert isinstance(volumes, list)
    assert isinstance(zones, list)
    assert isinstance(connections, list)
    assert isinstance(responses, list)
    return {
        "schema": "SchematicOptionDecisionProjection@1",
        "option_id": option.option_id,
        "option_digest": option.option_digest,
        "proposal_digest": option.proposal.proposal_digest,
        "label": proposal["label"],
        "typology_hypothesis": proposal["typology_hypothesis"],
        "rationale": proposal["rationale"],
        "program_scenario_ref": proposal["program_scenario_ref"],
        "footprint_area": option.footprint_area,
        "footprint_cell_count": len(option.proposal.footprint_cells),
        "horizontal_area_per_cell": proposal["grid_basis"][
            "horizontal_area_per_cell"
        ],
        "topology_signature": option.topology_signature,
        "levels": [
            {
                "level_id": item["level_id"],
                "base_y": item["base_y"],
                "height": item["height"],
            }
            for item in levels
        ],
        "volumes": [
            {
                "volume_id": item["volume_id"],
                "bounds": item["bounds"],
                "level_ids": item["level_ids"],
            }
            for item in volumes
        ],
        "components": [
            {
                "component_id": item["component_id"],
                "parent_component_id": item["parent_component_id"],
                "semantic_kind": item["semantic_kind"],
                "intent": item["intent"],
                "maturity": item["maturity"],
                "volume_ids": item["volume_ids"],
                "unresolved_child_roles": item["unresolved_child_roles"],
            }
            for item in components
        ],
        "zones": [
            {
                "zone_id": item["zone_id"],
                "program_node_refs": item["program_node_refs"],
                "level_ids": item["level_ids"],
                "volume_ids": item["volume_ids"],
            }
            for item in zones
        ],
        "connections": [
            {
                "connection_id": item["connection_id"],
                "source_zone_id": item["source_zone_id"],
                "target_zone_id": item["target_zone_id"],
                "directed": item["directed"],
                "relationship_refs": item["relationship_refs"],
            }
            for item in connections
        ],
        "constraint_responses": [
            {
                "constraint_ref": item["constraint_ref"],
                "status": item["status"],
                "rationale": item["rationale"],
            }
            for item in responses
        ],
        "decision_projection_only": True,
        "option_mutation_authority": False,
        "validation_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }


async def select_schematic_option(
    provider: AsyncModelProvider,
    *,
    request_id: str,
    option_set: SchematicOptionSet,
    raw_request: ProjectRecordRef,
) -> SchematicSelectionResult:
    """Ask the active Architect to choose; deterministic code validates it."""

    if not isinstance(option_set, SchematicOptionSet):
        raise TypeError("option_set must be SchematicOptionSet")
    if not isinstance(raw_request, ProjectRecordRef):
        raise TypeError("raw_request must be ProjectRecordRef")
    if raw_request.project_id != option_set.project_id:
        raise ValueError("raw request and option set cross project boundary")
    payload = {
        "schema": "SchematicOptionSelectionPrompt@1",
        "instructions": (
            "Select exactly one supplied validated schematic option for the "
            "raw project request. Use only the supplied alternatives and give "
            "a bounded tradeoff rationale. Do not modify geometry or persist."
        ),
        "raw_request_ref": raw_request.uri,
        "exact_option_set_digest": option_set.option_set_digest,
        "option_decision_projections": [
            schematic_option_decision_projection(item)
            for item in option_set.options
        ],
        "option_projection_contract": {
            "schema": "SchematicOptionDecisionProjectionContract@1",
            "source_option_set_digest": option_set.option_set_digest,
            "projection_is_not_an_option": True,
            "original_options_remain_authoritative": True,
            "option_mutation_authority": False,
            "validation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        },
        "required_output_schema": "SchematicOptionSelectionOutput@1",
        "output_contract": {
            "schema": "SchematicOptionSelectionContract@1",
            "exact_fields": [
                "schema",
                "exact_option_set_digest",
                "selected_option_id",
                "rationale",
            ],
            "fixed_values": {
                "schema": "SchematicOptionSelectionOutput@1",
                "exact_option_set_digest": option_set.option_set_digest,
            },
            "allowed_option_ids": [
                item.option_id for item in option_set.options
            ],
            "option_mutation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        },
    }
    request = ModelInvocationRequest.create(
        request_id=request_id,
        phase=ModelPhase.ACTION_PROPOSAL,
        checkpoint_digest=option_set.option_set_digest,
        context_digest=_selection_digest(payload),
        payload=payload,
    )
    receipt = await provider.invoke(request)
    if not isinstance(receipt, ModelInvocationReceipt):
        raise TypeError("provider must return ModelInvocationReceipt")
    if receipt.request != request:
        return _failed_selection(
            request,
            receipt,
            SchematicSelectionStatus.REJECTED,
            "schematic_selection.provider_request_mismatch",
        )
    if receipt.status is not ModelInvocationStatus.SUCCESS:
        return _failed_selection(
            request,
            receipt,
            SchematicSelectionStatus.PROVIDER_FAILED,
            receipt.error_code or "schematic_selection.provider_failed",
        )
    try:
        output = receipt.output
        expected_fields = {
            "schema",
            "exact_option_set_digest",
            "selected_option_id",
            "rationale",
        }
        if not isinstance(output, dict):
            return _failed_selection(
                request,
                receipt,
                SchematicSelectionStatus.REJECTED,
                "schematic_selection.output_not_object",
            )
        if set(output) != expected_fields:
            missing = ",".join(sorted(expected_fields - set(output))) or "none"
            extra = ",".join(sorted(set(output) - expected_fields)) or "none"
            return _failed_selection(
                request,
                receipt,
                SchematicSelectionStatus.REJECTED,
                "schematic_selection.fields_mismatch:"
                f"missing={missing};extra={extra}",
            )
        if output["schema"] != "SchematicOptionSelectionOutput@1":
            raise ValueError("selection output schema is unsupported")
        if output["exact_option_set_digest"] != option_set.option_set_digest:
            return _failed_selection(
                request,
                receipt,
                SchematicSelectionStatus.REJECTED,
                "schematic_selection.stale_option_set",
            )
        selected_id = output["selected_option_id"]
        require_identifier(selected_id, "selected_option_id")
        rationale = output["rationale"]
        _selection_text(rationale, "rationale")
        option = next(
            (item for item in option_set.options if item.option_id == selected_id),
            None,
        )
        if option is None:
            raise ValueError("selected option is outside the supplied set")
    except (TypeError, ValueError) as exc:
        return _failed_selection(
            request,
            receipt,
            SchematicSelectionStatus.REJECTED,
            f"schematic_selection.malformed_output:{type(exc).__name__}",
        )
    selection_receipt = SchematicSelectionReceipt(
        selection_id=f"selection-{_selection_digest(request.to_dict())[:24]}",
        status=SchematicSelectionStatus.SELECTED,
        request=request,
        model_receipt=receipt,
        option_set_digest=option_set.option_set_digest,
        selected_option_id=selected_id,
        rationale=rationale,
    )
    return SchematicSelectionResult(selection_receipt, option)


def _failed_selection(
    request: ModelInvocationRequest,
    receipt: ModelInvocationReceipt,
    status: SchematicSelectionStatus,
    error_code: str,
) -> SchematicSelectionResult:
    return SchematicSelectionResult(
        SchematicSelectionReceipt(
            selection_id=(
                f"selection-{_selection_digest(request.to_dict())[:24]}"
            ),
            status=status,
            request=request,
            model_receipt=receipt,
            option_set_digest=request.checkpoint_digest,
            selected_option_id=None,
            rationale=None,
            error_code=error_code,
        )
    )


def _selection_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _selection_sha(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _selection_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4_000:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class ProductionRootCompiler:
    repository: object
    context_ref: ProjectRecordRef
    context: ProductionAuthoringContext
    provider: AuthorizedAsyncModelProvider
    evidence_collector: InvocationEvidenceCollector
    geometry_provider_identity: GeometryProposalProviderIdentity
    geometry_policy: GeometryProposalPolicy = GeometryProposalPolicy(3)
    declaration_contract: object | None = None

    def __post_init__(self) -> None:
        for method in ("load_json", "put_json"):
            if not callable(getattr(self.repository, method, None)):
                raise TypeError(f"repository must implement {method}")
        if not isinstance(self.context_ref, ProjectRecordRef):
            raise TypeError("context_ref must be ProjectRecordRef")
        if not isinstance(self.context, ProductionAuthoringContext):
            raise TypeError("context must be ProductionAuthoringContext")
        if not isinstance(self.provider, AuthorizedAsyncModelProvider):
            raise TypeError("provider must be P053-authorized")
        if not isinstance(self.evidence_collector, InvocationEvidenceCollector):
            raise TypeError("evidence_collector must be InvocationEvidenceCollector")
        if not isinstance(
            self.geometry_provider_identity,
            GeometryProposalProviderIdentity,
        ):
            raise TypeError("geometry_provider_identity is invalid")
        if not isinstance(self.geometry_policy, GeometryProposalPolicy):
            raise TypeError("geometry_policy is invalid")

    @property
    def intent_record_refs(self) -> tuple[ProjectRecordRef, ...]:
        return (self.context_ref,)

    def invocation_evidence_cursor(self) -> int:
        return self.evidence_collector.cursor()

    def invocation_evidence_since(
        self,
        cursor: int,
    ) -> tuple[InvocationEnvelope, ...]:
        return self.evidence_collector.since(cursor)

    async def compile(
        self,
        *,
        run: RunRef,
        raw_request: ProjectRecordRef,
        prompt: str,
    ) -> CompiledProductionStep:
        del prompt  # Exact prompt equality is enforced before this compiler.
        self.context.require_run(run)
        if self.context_ref.project_id != run.project_id:
            raise ProductionRootCompilationError(
                "production context crosses project boundary"
            )
        loaded_context = ProductionAuthoringContext.from_dict(
            self.repository.load_json(self.context_ref)
        )
        if loaded_context != self.context:
            raise ProductionRootCompilationError(
                "production context record does not match the supplied value"
            )
        if not self.context.required_commitment_refs:
            raise ProductionRootCompilationError(
                "initial geometry requires at least one current commitment"
            )
        cursor = self.evidence_collector.cursor()
        destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )
        authored = []
        authoring_refs = []
        for index in range(1, 3):
            repair_feedback = None
            alternative_context = None
            if authored:
                existing = authored[0].option
                assert existing is not None
                alternative_context = {
                    "schema": "SpatialAlternativeAuthoringContext@1",
                    "excluded_option_ids": [existing.option_id],
                    "excluded_option_digests": [existing.option_digest],
                    "excluded_topology_signatures": [
                        existing.topology_signature
                    ],
                    "existing_option_projection": (
                        schematic_option_decision_projection(existing)
                    ),
                    "instructions": (
                        "Author a complete independent alternative with a new "
                        "option_id and meaningfully different spatial topology. "
                        "Do not rename, patch, or select the existing option."
                    ),
                    "complete_alternative_required": True,
                    "option_mutation_authority": False,
                    "selection_authority": False,
                    "validation_authority": False,
                    "persistence_authority": False,
                    "canonical_write_authority": False,
                }
            for attempt_index in range(2):
                result = await author_semantic_spatial_option(
                    self.provider,
                    request_id=(
                        f"spatial-{run.run_id}-{index:02d}-"
                        f"attempt-{attempt_index:02d}"
                    ),
                    state=self.context.state,
                    maturity=self.context.maturity,
                    phase_gate=self.context.phase_gate,
                    program=self.context.program,
                    site_context=self.context.site_context,
                    build_policy=self.context.build_policy,
                    repair_feedback=repair_feedback,
                    alternative_context=alternative_context,
                    declaration_contract=self.declaration_contract,
                )
                authoring_refs.append(
                    self.repository.put_json(
                        run=run,
                        destination=destination,
                        record_kind=(
                            f"semantic-spatial-authoring-{index:02d}-"
                            f"attempt-{attempt_index:02d}"
                        ),
                        payload=result.receipt.to_dict(),
                    )
                )
                if (
                    result.receipt.status
                    is SemanticSpatialAuthoringStatus.ACCEPTED
                    and result.proposal is not None
                ):
                    authored.append(result)
                    break
                if attempt_index == 0:
                    try:
                        repair_feedback = semantic_spatial_repair_feedback(result)
                    except (TypeError, ValueError):
                        repair_feedback = None
                    if repair_feedback is not None:
                        continue
                error_code = (
                    result.receipt.error_code
                    or "spatial_authoring.rejected_without_code"
                )
                diagnostic = result.receipt.message or "no diagnostic supplied"
                raise ProductionRootCompilationError(
                    "semantic-spatial authoring did not produce a valid option: "
                    f"{error_code}; {diagnostic}",
                    error_code=error_code,
                )

        try:
            spatial = compile_spatial_options(
                state=self.context.state,
                maturity=self.context.maturity,
                phase_gate=self.context.phase_gate,
                program=self.context.program,
                site_context=self.context.site_context,
                build_policy=self.context.build_policy,
                proposals=tuple(item.proposal for item in authored),
            )
        except SpatialProposalError as exc:
            raise ProductionRootCompilationError(
                "accepted semantic-spatial options cannot form one current "
                f"alternative set: {type(exc).__name__}: {exc}",
                error_code="spatial_authoring.option_set_rejected",
            ) from exc
        authoring_refs = tuple(authoring_refs)
        option_set_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="schematic-option-set",
            payload=spatial.option_set.to_dict(),
        )
        spatial_receipt_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="schematic-option-compilation",
            payload=spatial.receipt.to_dict(),
        )

        selection = await select_schematic_option(
            self.provider,
            request_id=f"select-{run.run_id}-schematic",
            option_set=spatial.option_set,
            raw_request=raw_request,
        )
        if (
            selection.receipt.status is not SchematicSelectionStatus.SELECTED
            or selection.option is None
            or selection.receipt.rationale is None
        ):
            raise ProductionRootCompilationError(
                "Architect did not select a current schematic option: "
                f"{selection.receipt.error_code}",
                error_code=(
                    selection.receipt.error_code
                    or "schematic_selection.rejected_without_code"
                ),
            )
        selection_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="schematic-selection",
            payload=selection.receipt.to_dict(),
        )
        portfolio = initialize_design_portfolio(
            spatial.option_set,
            portfolio_id=f"portfolio-{run.run_id}",
            selection_policy=SelectionPolicy(
                authority_ids=(self.context.architect_id,),
                source_refs=tuple(sorted((raw_request.uri, self.context_ref.uri))),
            ),
            architect_id=self.context.architect_id,
        )
        portfolio = select_branch(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            branch_id=selection.option.option_id,
            authority_id=self.context.architect_id,
            decision_ref=selection_ref.uri,
            rationale=selection.receipt.rationale,
            evidence_refs=(selection_ref.uri,),
            transition_id=f"select-{run.run_id}-schematic",
        )
        handoff = compile_selected_branch_handoff(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            expected_revision_digest=portfolio.selected_branch.head.revision_digest,
        )
        selected_input = SelectedSchematicInput.from_handoff(handoff)
        obligations = tuple(
            DevelopmentObligation(
                obligation_id=f"coordinate-{discipline.value}",
                discipline=discipline,
                statement=(
                    f"Resolve {discipline.value} coordination before design "
                    "development completion is claimed."
                ),
                priority=DevelopmentObligationPriority.BLOCKING,
                status=DevelopmentObligationStatus.OPEN,
                source_refs=(selection_ref.uri,),
                dependency_refs=(selected_input.ref,),
            )
            for discipline in sorted(
                DevelopmentDiscipline,
                key=lambda item: item.value,
            )
        )
        developed = initialize_developed_design(
            handoff,
            obligations=obligations,
            assumption_refs=(),
        )
        proposal_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="selected-spatial-option",
            payload=selection.option.proposal.to_dict(),
        )
        geometry = await produce_geometry_program_proposal(
            self.repository,
            self.provider,
            run=run,
            destination=destination,
            spatial_option_ref=proposal_ref,
            design_state=developed,
            required_commitment_refs=self.context.required_commitment_refs,
            provider_identity=self.geometry_provider_identity,
            policy=self.geometry_policy,
        )
        if (
            geometry.status is not GeometryProposalStatus.ACCEPTED
            or geometry.program is None
            or geometry.proposal_ref is None
        ):
            issue_codes = []
            for ref in geometry.round_refs:
                payload = self.repository.load_json(ref)
                issues = payload.get("issues", [])
                if isinstance(issues, list):
                    issue_codes.extend(
                        f"{item.get('code', 'unknown')}:{item.get('detail', '')}"
                        for item in issues
                        if isinstance(item, dict)
                    )
            raise ProductionRootCompilationError(
                "geometry authoring exhausted without an accepted program: "
                f"{tuple(issue_codes)}"
            )
        realization = realize_geometry(
            geometry.program,
            workspace_id=f"sandbox-{run.run_id}",
        )
        if (
            realization.receipt.status is not RealizationStatus.REALIZED
            or realization.scene is None
        ):
            raise ProductionRootCompilationError(
                "compiled geometry could not be realized in the sandbox: "
                f"{tuple(item.code for item in realization.receipt.issues)}"
            )
        scene_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="sandbox-scene",
            payload=realization.scene.to_dict(),
        )
        realization_ref = self.repository.put_json(
            run=run,
            destination=destination,
            record_kind="sandbox-realization",
            payload=realization.receipt.to_dict(),
        )
        source_refs = tuple(
            sorted(
                {
                    raw_request.uri,
                    self.context_ref.uri,
                    *[item.uri for item in authoring_refs],
                    option_set_ref.uri,
                    spatial_receipt_ref.uri,
                    selection_ref.uri,
                    proposal_ref.uri,
                    geometry.lineage_ref.uri,
                    geometry.proposal_ref.uri,
                    scene_ref.uri,
                    realization_ref.uri,
                }
            )
        )
        root = bind_initial_semantic_geometry(
            transaction_id=f"initial-{run.run_id}",
            current_state=developed,
            current_proposal=selection.option.proposal,
            geometry_program=geometry.program,
            source_refs=source_refs,
        )
        envelopes = self.evidence_collector.since(cursor)
        if not envelopes:
            raise ProductionRootCompilationError(
                "P053 produced no validated invocation evidence"
            )
        return CompiledProductionStep(
            current_design_state=developed,
            lifecycle=root,
            invocation_envelopes=envelopes,
        )
