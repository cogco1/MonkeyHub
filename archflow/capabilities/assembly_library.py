"""Assembly library flows: harvest, candidate, promote, bind (P096).

The harvester turns a retained design state (its component tree) plus
declared relations, datum roles, checks and counts into a
`BuildingAssemblyTemplate@1` — organisation only, no coordinates. A
candidate enters the library with an OPEN second-case obligation; it is
promoted only under the two-vote rule or a recorded waiver. All flows
are authority-free and persist through the injected repository ports.
"""

from __future__ import annotations

from collections.abc import Mapping

from archflow.contracts.authority import no_authority
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceDestination,
    ProjectRecordRef,
    RunRef,
    require_destination,
)
from archflow.state.assembly_template import (
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
from archflow.state.component_template import CaseVote, TemplateParameter
from archflow.state.design_maturity import DesignPhase
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import DatumBinding, InterfaceDatum
from archflow.state.spatial import ComponentMaturity

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
    candidate_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=CANDIDATE_RECORD_KIND, payload=template.to_dict()
    )
    obligation_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=SECOND_CASE_OBLIGATION_KIND,
        payload={
            "schema": "AssemblySecondCaseObligation@1", "template_id": template.template_id, "typology": template.typology,
            "candidate_ref": candidate_ref.uri, "source_ref": source_ref.uri, "source_sha256": source_ref.sha256,
            "vote_projects": list(votes), "status": "OPEN" if len(votes) < 2 else "SATISFIED",
            "statement": "A second, non-isomorphic project of the same typology must bind this template and its stable roles must survive the comparison before promotion.",
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
    waiver_ref: str | None = None,
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Promote under the two-vote rule or a recorded waiver."""

    library_destination = require_destination(library_destination, producer="assembly promotion")
    require_assembly_votes(template, waiver_ref=waiver_ref)
    template_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=TEMPLATE_RECORD_KIND, payload=template.to_dict()
    )
    receipt_ref = library.put_json(
        run=library_run, destination=library_destination, record_kind=PROMOTION_RECEIPT_KIND,
        payload={
            "schema": "AssemblyPromotionReceipt@1", "template_id": template.template_id, "edition": template.edition,
            "template_ref": template_ref.uri, "candidate_ref": candidate_ref.uri, "candidate_sha256": candidate_ref.sha256,
            "vote_projects": list(template.distinct_vote_projects()), "two_vote_waiver_ref": waiver_ref,
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
    "TEMPLATE_RECORD_KIND", "harvest_assembly_template", "promote_assembly_template", "record_assembly_binding",
    "record_assembly_candidate", "relations_from_datum_bindings", "roles_from_design_state",
]
