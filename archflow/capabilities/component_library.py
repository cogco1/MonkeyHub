"""Component library flows: harvest, promote, import (P091).

Templates are project records first. Promotion moves an accepted
template into the shared library project under the two-vote rule (or a
recorded waiver), with a receipt binding the exact source. Import
brings a library template into a receiving project; provenance stays
intact and every cited basis reference must be re-bound to a local
record — reuse is re-derivation, so the receiving project supplies its
own premises. All flows are authority-free and persist only through the
injected repository ports.
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
from archflow.state.component_template import (
    ComponentTemplate,
    ComponentTemplateError,
    require_library_votes,
)

TEMPLATE_RECORD_KIND = "component-template"
PROMOTION_RECEIPT_KIND = "component-promotion-receipt"
IMPORT_RECEIPT_KIND = "component-import-receipt"

_RECEIPT_AUTHORITY = (
    "canonical_write_authority",
    "design_authority",
    "stage_acceptance_authority",
)


def harvest_component_template(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    template: ComponentTemplate,
) -> ProjectRecordRef:
    """Persist a freshly harvested template as a project record."""

    destination = require_destination(
        destination, producer="component template harvest"
    )
    if not isinstance(template, ComponentTemplate):
        raise ComponentTemplateError("template must be a ComponentTemplate")
    if template.harvested_from_project != run.project_id:
        raise ComponentTemplateError(
            "template harvest project does not match the writing project"
        )
    return repository.put_json(
        run=run,
        destination=destination,
        record_kind=TEMPLATE_RECORD_KIND,
        payload=template.to_dict(),
    )


def promote_component_template(
    library: FilesystemProjectRepository,
    *,
    library_run: RunRef,
    library_destination: PersistenceDestination,
    template: ComponentTemplate,
    source_ref: ProjectRecordRef,
    waiver_ref: str | None = None,
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Promote a template into the shared library under the two-vote rule."""

    library_destination = require_destination(
        library_destination, producer="component template promotion"
    )
    if not isinstance(template, ComponentTemplate):
        raise ComponentTemplateError("template must be a ComponentTemplate")
    if not isinstance(source_ref, ProjectRecordRef):
        raise ComponentTemplateError(
            "source_ref must be a ProjectRecordRef"
        )
    require_library_votes(template, waiver_ref=waiver_ref)
    template_ref = library.put_json(
        run=library_run,
        destination=library_destination,
        record_kind=TEMPLATE_RECORD_KIND,
        payload=template.to_dict(),
    )
    receipt_ref = library.put_json(
        run=library_run,
        destination=library_destination,
        record_kind=PROMOTION_RECEIPT_KIND,
        payload={
            "schema": "ComponentPromotionReceipt@1",
            "template_id": template.template_id,
            "family": template.family,
            "edition": template.edition,
            "template_ref": template_ref.uri,
            "source_ref": source_ref.uri,
            "source_sha256": source_ref.sha256,
            "vote_projects": list(template.distinct_vote_projects()),
            "two_vote_waiver_ref": waiver_ref,
            **no_authority(_RECEIPT_AUTHORITY),
        },
    )
    return template_ref, receipt_ref


def import_component_template(
    target: FilesystemProjectRepository,
    *,
    target_run: RunRef,
    target_destination: PersistenceDestination,
    template: ComponentTemplate,
    library_ref: ProjectRecordRef,
    evidence_rebinding: Mapping[str, str],
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Import a library template into a receiving project.

    Every basis reference the template cites must be re-bound to a
    record the receiving project owns; an incomplete rebinding fails
    closed. The template payload itself travels unchanged — provenance
    is preserved, and the binding map lives in the import receipt.
    """

    target_destination = require_destination(
        target_destination, producer="component template import"
    )
    if not isinstance(template, ComponentTemplate):
        raise ComponentTemplateError("template must be a ComponentTemplate")
    if not isinstance(library_ref, ProjectRecordRef):
        raise ComponentTemplateError(
            "library_ref must be a ProjectRecordRef"
        )
    if not isinstance(evidence_rebinding, Mapping):
        raise ComponentTemplateError("evidence_rebinding must be a mapping")
    cited = template.cited_basis_refs()
    missing = tuple(
        ref for ref in cited if ref not in evidence_rebinding
    )
    if missing:
        raise ComponentTemplateError(
            "evidence rebinding is incomplete; the receiving project must "
            f"bind: {list(missing)}"
        )
    binding_rows = []
    for ref in cited:
        local = evidence_rebinding[ref]
        if not isinstance(local, str) or not local.strip():
            raise ComponentTemplateError(
                f"evidence rebinding for {ref!r} must name a local record"
            )
        binding_rows.append({"template_ref": ref, "local_ref": local})
    template_ref = target.put_json(
        run=target_run,
        destination=target_destination,
        record_kind=TEMPLATE_RECORD_KIND,
        payload=template.to_dict(),
    )
    receipt_ref = target.put_json(
        run=target_run,
        destination=target_destination,
        record_kind=IMPORT_RECEIPT_KIND,
        payload={
            "schema": "ComponentImportReceipt@1",
            "template_id": template.template_id,
            "family": template.family,
            "edition": template.edition,
            "template_ref": template_ref.uri,
            "library_ref": library_ref.uri,
            "library_sha256": library_ref.sha256,
            "evidence_rebinding": binding_rows,
            **no_authority(_RECEIPT_AUTHORITY),
        },
    )
    return template_ref, receipt_ref
