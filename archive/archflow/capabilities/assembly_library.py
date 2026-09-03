"""Assembly library flows: harvest, candidate, promote, bind (P096).

The harvester turns a retained design state (its component tree) plus
declared relations, datum roles, checks and counts into a
`BuildingAssemblyTemplate@1` — organisation only, no coordinates. A
candidate enters the library with an OPEN second-case obligation; it is
promoted only under the two-vote rule or a recorded waiver. All flows
are authority-free and persist through the injected repository ports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from archflow.state.stage_workflow import CompositeStageClosureReceipt, StageClosureStatus
from archflow.contracts.authority import no_authority
from archflow.contracts.canonical import require_sha256
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceDestination, require_destination
from archflow.project.refs import ProjectRecordRef, RunRef
from archive.archflow.state.assembly_template import (
    AssemblyRelation,
    AssemblyRole,
    AssemblyTemplateBinding,
    AssemblyTemplateError,
    BuildingAssemblyTemplate,
    Cardinality,
    RequiredCheck,
    RequiredDatum,
    require_assembly_votes,
)
from archive.archflow.state.component_template import CaseVote, TemplateParameter
from archflow.state.stage_workflow import DesignPhase
from archive.archflow.state.design_maturity import DesignMaturityState, StageEntryProof
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import DatumBinding, InterfaceDatum
from archflow.state.spatial import ComponentMaturity
from archflow.state.stage_workflow import (
    ProjectStageWorkflow,
    StageExitBinding,
    StageExitStatus,
    StageRunEnvelope,
    require_stage_exit_binding,
)

CANDIDATE_RECORD_KIND = "assembly-template-candidate"
TEMPLATE_RECORD_KIND = "assembly-template"
PROMOTION_RECEIPT_KIND = "assembly-promotion-receipt"
SECOND_CASE_OBLIGATION_KIND = "assembly-second-case-obligation"
BINDING_RECORD_KIND = "assembly-template-binding"

_RECEIPT_AUTHORITY = ("canonical_write_authority", "design_authority", "stage_acceptance_authority")

_PHASE_BY_MATURITY = {
    ComponentMaturity.MASSING: DesignPhase.SCHEMATIC_DESIGN,
    ComponentMaturity.SCHEMATIC: DesignPhase.SCHEMATIC_DESIGN,
    ComponentMaturity.DEVELOPED: DesignPhase.DESIGN_DEVELOPMENT,
    ComponentMaturity.DETAILED: DesignPhase.CANDIDATE_COORDINATION,
}

VoteRecordLoader = Callable[[str], Mapping[str, object]]


def _load_vote_record(
    loader: VoteRecordLoader,
    ref: str,
    *,
    field: str,
) -> Mapping[str, object]:
    try:
        payload = loader(ref)
    except Exception as exc:
        raise AssemblyTemplateError(
            f"cannot load {field} {ref!r}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AssemblyTemplateError(f"{field} loader returned a non-object")
    return payload


def _verify_stage_qualified_vote(
    vote: CaseVote,
    loader: VoteRecordLoader,
) -> None:
    """Resolve and verify one exact binding/state/stage-proof chain."""

    if not vote.stage_qualified:
        raise AssemblyTemplateError(
            "organisation_only vote cannot enter stage verification"
        )

    binding_payload = _load_vote_record(
        loader,
        vote.receipt_ref,
        field="assembly binding receipt",
    )
    binding_fields = {
        "schema",
        "template_ref",
        "template_digest",
        "project_id",
        "run_id",
        "role_bindings",
        "datum_bindings",
        "parameters",
        "evidence_rebinding",
        "canonical_write_authority",
        "design_authority",
    }
    if (
        set(binding_payload) != binding_fields
        or binding_payload.get("schema") != AssemblyTemplateBinding.SCHEMA
        or not isinstance(binding_payload.get("role_bindings"), list)
        or not binding_payload.get("role_bindings")
        or not isinstance(binding_payload.get("datum_bindings"), list)
        or not isinstance(binding_payload.get("parameters"), list)
        or not isinstance(binding_payload.get("evidence_rebinding"), list)
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote receipt is not an "
            f"{AssemblyTemplateBinding.SCHEMA} record"
        )
    if (
        binding_payload.get("project_id") != vote.project_id
        or binding_payload.get("run_id") != vote.run_id
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote receipt belongs to another project or run"
        )
    try:
        require_sha256(
            binding_payload.get("template_digest"),
            "assembly binding template_digest",
        )
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote receipt has no valid template digest"
        ) from exc

    workflow_payload = _load_vote_record(
        loader,
        vote.workflow_ref,
        field="project stage workflow",
    )
    try:
        workflow = ProjectStageWorkflow.from_dict(workflow_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote workflow is not a "
            "ProjectStageWorkflow@1"
        ) from exc
    if (
        workflow.project_id != vote.project_id
        or workflow.workflow_digest != vote.workflow_digest
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote workflow is cross-project or has a "
            "different digest"
        )

    envelope_payload = _load_vote_record(
        loader,
        vote.stage_envelope_ref,
        field="stage run envelope",
    )
    try:
        envelope = StageRunEnvelope.from_dict(envelope_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote envelope is not a StageRunEnvelope@1"
        ) from exc
    if envelope.envelope_digest != vote.envelope_digest:
        raise AssemblyTemplateError(
            "stage-qualified vote envelope digest does not match the "
            "loaded envelope"
        )
    try:
        workflow_stage = workflow.stage_at(vote.stage_index)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote stage_index is outside the workflow"
        ) from exc
    if (
        envelope.project_id != vote.project_id
        or envelope.run_id != vote.run_id
        or envelope.branch_id != vote.branch_id
        or envelope.branch_epoch != vote.branch_epoch
        or envelope.workflow_ref != vote.workflow_ref
        or envelope.workflow_digest != vote.workflow_digest
        or envelope.stage_id != vote.stage_id
        or envelope.stage_index != vote.stage_index
        or envelope.phase is not vote.phase
        or envelope.subject_ref != vote.state_ref
        or envelope.state_digest != vote.state_digest
        or workflow_stage.stage_id != vote.stage_id
        or workflow_stage.phase is not vote.phase
        or envelope.required_roles != workflow_stage.required_roles
        or envelope.required_checks != workflow_stage.required_checks
        or envelope.close_obligation.obligation_id
        != workflow_stage.close_obligation_id
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote envelope is cross-scope, stale, or does "
            "not match the exact workflow Stage and state"
        )

    state_payload = _load_vote_record(
        loader,
        vote.state_ref,
        field="design maturity state",
    )
    try:
        maturity = DesignMaturityState.from_dict(state_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote state is not a DesignMaturityState@1"
        ) from exc
    if maturity.state_digest != vote.state_digest:
        raise AssemblyTemplateError(
            "stage-qualified vote state digest does not match the loaded state"
        )
    branch = maturity.branch
    if (
        branch.run.project_id != vote.project_id
        or branch.run.run_id != vote.run_id
        or branch.run.base.version != envelope.base_version
        or branch.run.base.require_digest() != envelope.base_state_sha256
        or branch.branch_id != vote.branch_id
        or branch.epoch != vote.branch_epoch
        or maturity.phase is not vote.phase
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote state is cross-project, cross-run, "
            "cross-branch, stale, or wrong-stage"
        )

    proof_payload = _load_vote_record(
        loader,
        vote.stage_proof_ref,
        field="stage-entry proof",
    )
    try:
        proof = StageEntryProof.from_dict(proof_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote proof is not a StageEntryProof@1"
        ) from exc
    if proof.proof_digest != vote.stage_proof_digest:
        raise AssemblyTemplateError(
            "stage-qualified vote proof digest does not match the loaded proof"
        )
    if vote.stage_proof_ref.startswith("stage-entry-proof:") and (
        vote.stage_proof_ref != proof.ref
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote proof reference does not match its digest"
        )
    if (
        proof.successor_branch != branch
        or proof.phase_gate.to_phase is not vote.phase
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote proof does not enter the loaded exact state"
        )

    exit_payload = _load_vote_record(
        loader,
        vote.stage_exit_ref,
        field="stage exit binding",
    )
    try:
        exit_binding = StageExitBinding.from_dict(exit_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote exit is not a StageExitBinding@1"
        ) from exc
    if exit_binding.exit_digest != vote.stage_exit_digest:
        raise AssemblyTemplateError(
            "stage-qualified vote exit digest does not match the loaded "
            "binding"
        )
    try:
        require_stage_exit_binding(
            envelope,
            exit_binding,
            envelope_ref=vote.stage_envelope_ref,
        )
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote exit does not bind the exact envelope"
        ) from exc
    if (
        exit_binding.status is not StageExitStatus.SATISFIED
        or exit_binding.closure_ref != vote.stage_closure_ref
        or exit_binding.closure_digest != vote.closure_digest
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote exit is not SATISFIED by the named exact "
            "closure"
        )

    closure_payload = _load_vote_record(
        loader,
        vote.stage_closure_ref,
        field="stage closure",
    )
    try:
        closure = CompositeStageClosureReceipt.from_dict(closure_payload)
    except (TypeError, ValueError) as exc:
        raise AssemblyTemplateError(
            "stage-qualified vote closure is not a "
            "CompositeStageClosureReceipt@1"
        ) from exc
    if (
        closure.receipt_digest != vote.closure_digest
        or closure.status is not StageClosureStatus.SATISFIED
        or closure.stage_id != vote.stage_id
        or closure.branch != branch
        or closure.stage_subject_ref != vote.state_ref
        or closure.subject_digest != vote.state_digest
    ):
        raise AssemblyTemplateError(
            "stage-qualified vote closure is not SATISFIED for the exact "
            "workflow Stage, branch, and state"
        )


def roles_from_design_state(
    design_state: DevelopedDesignState,
    *,
    required_datum_roles: Mapping[str, tuple[str, ...]] | None = None,
    component_template_families: Mapping[str, str] | None = None,
) -> tuple[AssemblyRole, ...]:
    """One role per retained component: id, parent, function, first phase."""

    if not isinstance(design_state, DevelopedDesignState):
        raise AssemblyTemplateError("design_state must be DevelopedDesignState")
    required = dict(required_datum_roles or {})
    families = dict(component_template_families or {})
    roles = []
    for c in design_state.selected_schematic.option.proposal.components:
        roles.append(
            AssemblyRole(
                role_id=c.component_id, parent_role=c.parent_component_id, function=c.semantic_kind,
                cardinality=Cardinality(value=1), phase=_PHASE_BY_MATURITY[c.maturity],
                component_template_family=families.get(c.component_id),
                required_datum_roles=tuple(sorted(required.get(c.component_id, ()))),
            )
        )
    return tuple(sorted(roles, key=lambda r: r.role_id))


def relations_from_datum_bindings(
    *,
    datums: tuple[InterfaceDatum, ...],
    bindings: tuple[DatumBinding, ...],
    object_role: Mapping[str, str],
    op_role: Mapping[str, str],
    datum_role_of: Mapping[str, str],
    basis_refs: tuple[str, ...],
) -> tuple[AssemblyRelation, ...]:
    """SUPPORT relations a project's datum bindings already prove.

    Each binding says: the op's role sits on the datum published by the
    publisher's role. ``datum_role_of`` generalises a project datum id
    ("west-column-top") to a template datum role ("column-top").
    """

    from archflow.relations.contracts import ArchitecturalRelationKind

    by_id = {d.datum_id: d for d in datums}
    seen: dict[tuple[str, str, str], AssemblyRelation] = {}
    for b in bindings:
        datum = by_id.get(b.datum_id)
        if datum is None:
            raise AssemblyTemplateError(f"binding {b.binding_id!r} names unknown datum {b.datum_id!r}")
        publisher = object_role.get(datum.published_by)
        consumer = op_role.get(b.op_id)
        role = datum_role_of.get(datum.datum_id)
        if publisher is None or consumer is None or role is None:
            raise AssemblyTemplateError(
                f"binding {b.binding_id!r} cannot be generalised: publisher={publisher} consumer={consumer} datum_role={role}"
            )
        if publisher == consumer:
            continue
        key = (publisher, consumer, role)
        if key not in seen:
            seen[key] = AssemblyRelation(
                relation_id=f"{publisher}-supports-{consumer}-via-{role}", subject_role=publisher,
                predicate=ArchitecturalRelationKind.SUPPORT, object_role=consumer, datum_role=role,
                basis_refs=basis_refs,
            )
    return tuple(sorted(seen.values(), key=lambda r: r.relation_id))


def harvest_assembly_template(
    *,
    template_id: str,
    typology: str,
    design_state: DevelopedDesignState,
    roles: tuple[AssemblyRole, ...],
    relations: tuple[AssemblyRelation, ...],
    datums: tuple[RequiredDatum, ...],
    checks: tuple[RequiredCheck, ...],
    parameters: tuple[TemplateParameter, ...],
    applicability: tuple[str, ...],
    basis_refs: tuple[str, ...],
    case_vote: CaseVote,
    harvested_from_run: str,
    open_boundaries: tuple[str, ...] = (),
) -> BuildingAssemblyTemplate:
    """Assemble and validate one candidate; the schema does the refusing."""

    if not isinstance(design_state, DevelopedDesignState):
        raise AssemblyTemplateError("design_state must be DevelopedDesignState")
    if not isinstance(case_vote, CaseVote):
        raise AssemblyTemplateError("case_vote must be a CaseVote")
    return BuildingAssemblyTemplate(
        template_id=template_id, typology=typology, edition=1,
        roles=tuple(sorted(roles, key=lambda r: r.role_id)),
        relations=tuple(sorted(relations, key=lambda r: r.relation_id)),
        datums=tuple(sorted(datums, key=lambda d: d.datum_role)),
        checks=tuple(sorted(checks, key=lambda c: c.check_id)),
        parameters=tuple(sorted(parameters, key=lambda p: p.name)),
        applicability=tuple(sorted(set(applicability))), basis_refs=tuple(sorted(set(basis_refs))),
        case_votes=(case_vote,), harvested_from_project=design_state.project_id,
        harvested_from_run=harvested_from_run, open_boundaries=tuple(sorted(set(open_boundaries))),
    )


def record_assembly_candidate(
    library: FilesystemProjectRepository,
    *,
    library_run: RunRef,
    library_destination: PersistenceDestination,
    template: BuildingAssemblyTemplate,
    source_ref: ProjectRecordRef,
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Enter the library as a candidate with an OPEN second-case obligation."""

    library_destination = require_destination(library_destination, producer="assembly candidate record")
    if not isinstance(template, BuildingAssemblyTemplate):
        raise AssemblyTemplateError("template must be a BuildingAssemblyTemplate")
    if not isinstance(source_ref, ProjectRecordRef):
        raise AssemblyTemplateError("source_ref must be a ProjectRecordRef")
    votes = template.distinct_vote_projects()
    qualified_votes = template.distinct_stage_qualified_vote_projects()
    candidate_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=CANDIDATE_RECORD_KIND, payload=template.to_dict()
    )
    obligation_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=SECOND_CASE_OBLIGATION_KIND,
        payload={
            "schema": "AssemblySecondCaseObligation@2", "template_id": template.template_id, "typology": template.typology,
            "candidate_ref": candidate_ref.uri, "source_ref": source_ref.uri, "source_sha256": source_ref.sha256,
            "vote_projects": list(votes),
            "stage_qualified_vote_projects": list(qualified_votes),
            "status": "OPEN" if len(qualified_votes) < 2 else "SATISFIED",
            "statement": "Two non-isomorphic projects of the same typology must bind this template under exact standard-stage state and stage-entry proof before promotion.",
            **no_authority(_RECEIPT_AUTHORITY),
        },
    )
    return candidate_ref, obligation_ref


def promote_assembly_template(
    library: FilesystemProjectRepository,
    *,
    library_run: RunRef,
    library_destination: PersistenceDestination,
    template: BuildingAssemblyTemplate,
    candidate_ref: ProjectRecordRef,
    record_loader: VoteRecordLoader | None = None,
    waiver_ref: str | None = None,
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Promote verified stage-qualified votes or a recorded waiver."""

    library_destination = require_destination(library_destination, producer="assembly promotion")
    require_assembly_votes(template, waiver_ref=waiver_ref)
    verified_votes: tuple[CaseVote, ...] = ()
    if waiver_ref is None:
        if record_loader is None or not callable(record_loader):
            raise AssemblyTemplateError(
                "assembly promotion requires a vote record loader"
            )
        verified = []
        for vote in template.case_votes:
            if not vote.stage_qualified:
                continue
            _verify_stage_qualified_vote(vote, record_loader)
            verified.append(vote)
        verified_votes = tuple(verified)
        verified_projects = tuple(
            sorted({vote.project_id for vote in verified_votes})
        )
        if len(verified_projects) < 2:
            raise AssemblyTemplateError(
                "assembly promotion requires two independently verified "
                "stage-qualified projects"
            )
    else:
        verified_projects = ()
    template_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=TEMPLATE_RECORD_KIND, payload=template.to_dict()
    )
    receipt_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=PROMOTION_RECEIPT_KIND,
        payload={
            "schema": "AssemblyPromotionReceipt@2", "template_id": template.template_id, "edition": template.edition,
            "template_ref": template_ref.uri, "candidate_ref": candidate_ref.uri, "candidate_sha256": candidate_ref.sha256,
            "vote_projects": list(verified_projects),
            "declared_stage_qualified_vote_projects": list(
                template.distinct_stage_qualified_vote_projects()
            ),
            "organisation_only_vote_projects": sorted(
                {
                    vote.project_id
                    for vote in template.case_votes
                    if not vote.stage_qualified
                }
            ),
            "verified_votes": [vote.to_dict() for vote in verified_votes],
            "two_vote_waiver_ref": waiver_ref,
            **no_authority(_RECEIPT_AUTHORITY),
        },
    )
    return template_ref, receipt_ref


def record_assembly_binding(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    binding: AssemblyTemplateBinding,
) -> ProjectRecordRef:
    destination = require_destination(destination, producer="assembly binding record")
    if not isinstance(binding, AssemblyTemplateBinding):
        raise AssemblyTemplateError("binding must be an AssemblyTemplateBinding")
    if binding.project_id != run.project_id:
        raise AssemblyTemplateError("binding project does not match the writing project")
    return repository.put_json(run=run, destination=destination, record_kind=BINDING_RECORD_KIND, payload=binding.to_dict())


__all__ = [
    "BINDING_RECORD_KIND", "CANDIDATE_RECORD_KIND", "PROMOTION_RECEIPT_KIND", "SECOND_CASE_OBLIGATION_KIND",
    "TEMPLATE_RECORD_KIND", "VoteRecordLoader", "harvest_assembly_template", "promote_assembly_template", "record_assembly_binding",
    "record_assembly_candidate", "relations_from_datum_bindings", "roles_from_design_state",
]
