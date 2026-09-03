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

from dataclasses import dataclass

import importlib
from collections.abc import Mapping
from types import ModuleType

from archflow.contracts.authority import no_authority
from archflow.state.operational_state import DependencyEdge, DependencyEffect
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceDestination, require_destination
from archflow.project.refs import ProjectRecordRef, RunRef
from archive.archflow.state.component_template import (
    ComponentTemplate,
    ComponentTemplateError,
    require_library_votes,
    ComponentInstance,
    instance_edition_edge,
    template_edition_ref,
)

MATHEMATICS_REF_PREFIX = "capability:"

TEMPLATE_RECORD_KIND = "component-template"
PROMOTION_RECEIPT_KIND = "component-promotion-receipt"
IMPORT_RECEIPT_KIND = "component-import-receipt"

_RECEIPT_AUTHORITY = (
    "canonical_write_authority",
    "design_authority",
    "stage_acceptance_authority",
)


def resolve_mathematics_ref(ref: str) -> ModuleType:
    """Resolve a template's mathematics reference to the solver module.

    ``capability:<dotted.module>`` must import and expose at least one
    public ``solve_*`` or ``compile_*`` callable — a template may not
    point at mathematics that does not exist in the architecture.
    """

    if not isinstance(ref, str) or not ref.startswith(MATHEMATICS_REF_PREFIX):
        raise ComponentTemplateError(
            f"mathematics_ref must start with {MATHEMATICS_REF_PREFIX!r}"
        )
    dotted = ref[len(MATHEMATICS_REF_PREFIX):]
    if dotted.startswith("archive.archflow."):
        dotted = dotted[len("archive."):]
    if not dotted.startswith("archflow."):
        raise ComponentTemplateError(
            "mathematics_ref must name an archflow module, got " + repr(dotted)
        )
    try:
        try:
            module = importlib.import_module(dotted)
        except ModuleNotFoundError:  # the module lives in the archive since the one-spine move
            module = importlib.import_module("archive." + dotted)
    except ImportError as exc:
        raise ComponentTemplateError(
            f"mathematics_ref {ref!r} does not resolve: {exc}"
        ) from exc
    entry = [
        name for name in dir(module)
        if (name.startswith("solve_") or name.startswith("compile_"))
        and callable(getattr(module, name))
    ]
    if not entry:
        raise ComponentTemplateError(
            f"mathematics_ref {ref!r} resolves to a module without a "
            "solve_*/compile_* entry point"
        )
    return module


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
    resolve_mathematics_ref(template.mathematics_ref)
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


# ---------------------------------------------------------------- P099
_MAX_CLOSURE = 10_000
PROPAGATION_RECORD_KIND = "template-edition-propagation"


def revalidation_closure(
    roots: tuple[str, ...], dependencies: tuple[DependencyEdge, ...]
) -> tuple[str, ...]:
    """Everything downstream of ``roots`` along invalidating edges (P063 semantics)."""

    adjacency: dict[str, set[str]] = {}
    for edge in dependencies:
        if not isinstance(edge, DependencyEdge):
            raise ComponentTemplateError("dependencies must be DependencyEdge items")
        if edge.effect not in {DependencyEffect.INVALIDATES, DependencyEffect.REQUIRES_REVALIDATION}:
            continue
        adjacency.setdefault(edge.upstream_ref, set()).add(edge.downstream_ref)
    visited = set(roots)
    queue = sorted(roots)
    while queue:
        current = queue.pop(0)
        for downstream in sorted(adjacency.get(current, ())):
            if downstream in visited:
                continue
            visited.add(downstream)
            if len(visited) > _MAX_CLOSURE:
                raise ComponentTemplateError("revalidation closure exceeds bounded item count")
            queue.append(downstream)
    return tuple(sorted(visited))


@dataclass(frozen=True, slots=True)
class EditionPropagation:
    """What a new template edition reopens, and what it leaves untouched."""

    template_id: str
    from_editions: tuple[int, ...]
    to_edition: int
    promoted_ref: str
    reopened: tuple[str, ...]
    retained: tuple[tuple[str, str], ...]
    closure: tuple[str, ...]

    SCHEMA = "TemplateEditionPropagation@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "template_id": self.template_id,
            "from_editions": list(self.from_editions),
            "to_edition": self.to_edition,
            "promoted_ref": self.promoted_ref,
            "reopened_instance_ids": list(self.reopened),
            "retained_instances": [{"instance_id": i, "digest": d} for i, d in self.retained],
            "closure": list(self.closure),
            "effect": DependencyEffect.REQUIRES_REVALIDATION.value,
            **no_authority(_RECEIPT_AUTHORITY),
        }


def propagate_template_edition(
    instances: tuple[ComponentInstance, ...],
    promoted: ComponentTemplate,
    *,
    promoted_ref: str,
    dependencies: tuple[DependencyEdge, ...] = (),
) -> EditionPropagation:
    """Mark the instances a new edition reopens; nothing is rewritten.

    Every instance of an earlier edition of the promoted template, and
    everything downstream of it along invalidating edges (a program, a
    stage receipt), lands in ``reopened``; instances of other templates
    or of the same edition are retained by digest. The accepted programs
    stay as they are: reopening is a revalidation duty, not an edit.
    """

    if not isinstance(promoted, ComponentTemplate):
        raise ComponentTemplateError("promoted must be a ComponentTemplate")
    if not isinstance(instances, tuple) or any(not isinstance(i, ComponentInstance) for i in instances):
        raise ComponentTemplateError("instances must be ComponentInstance items")
    ids = [i.instance_id for i in instances]
    if len(set(ids)) != len(ids):
        raise ComponentTemplateError("instance ids must be unique")
    same = [i for i in instances if i.template_id == promoted.template_id]
    if any(i.edition > promoted.edition for i in same):
        raise ComponentTemplateError("an edition change must move forward")
    older = tuple(sorted({i.edition for i in same if i.edition < promoted.edition}))
    roots = tuple(template_edition_ref(promoted.template_id, e) for e in older)
    edges = tuple(instance_edition_edge(i) for i in instances) + tuple(dependencies)
    closure = revalidation_closure(roots, edges) if roots else ()
    reopened = tuple(sorted(i.instance_id for i in instances if i.ref in closure))
    retained = tuple((i.instance_id, i.digest) for i in instances if i.instance_id not in reopened)
    return EditionPropagation(
        template_id=promoted.template_id, from_editions=older, to_edition=promoted.edition,
        promoted_ref=promoted_ref, reopened=reopened, retained=retained, closure=closure,
    )


def record_edition_propagation(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    propagation: EditionPropagation,
) -> ProjectRecordRef:
    destination = require_destination(destination, producer="template edition propagation")
    if not isinstance(propagation, EditionPropagation):
        raise ComponentTemplateError("propagation must be an EditionPropagation")
    return repository.put_json(
        run=run, destination=destination, record_kind=PROPAGATION_RECORD_KIND, payload=propagation.to_dict()
    )

