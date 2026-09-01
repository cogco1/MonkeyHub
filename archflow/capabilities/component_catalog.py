"""Catalog confrontation and harvest obligations (P093).

Two gates make component reuse a property of the mechanism rather than
of anyone's memory. At the entry of geometry authoring, every intended
component family must be answered: a selected catalog template, or a
typed declination with a reason — the legitimate escape valve that
keeps the library from ossifying. At acceptance, every family the run
produced inline receives a harvest obligation (or a recorded waiver),
so converged search cannot die untextualized; a later harvest closes
the obligation by naming the template it produced.

Both gates are authority-free: they refuse typed, persist receipts
through the injected repository ports, and never select, accept, or
write canonical state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from archflow.contracts.authority import no_authority
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceDestination,
    ProjectRecordRef,
    RunRef,
    require_destination,
)
from archflow.project.refs import require_identifier

CONFRONTATION_RECORD_KIND = "catalog-confrontation"
OBLIGATION_RECORD_KIND = "component-harvest-obligation"
WAIVER_RECORD_KIND = "component-harvest-waiver"
CLOSURE_RECORD_KIND = "component-harvest-closure"

_AUTHORITY = (
    "canonical_write_authority",
    "design_authority",
    "stage_acceptance_authority",
)
_MAX_TEXT = 2_000


class CatalogGateError(ValueError):
    """A catalog or harvest gate input violates its contract."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogGateError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise CatalogGateError(f"{field} exceeds the text bound")
    return value


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One template the catalog offers for a family."""

    family: str
    template_id: str
    template_ref: str

    def __post_init__(self) -> None:
        require_identifier(self.family, "catalog entry family")
        require_identifier(self.template_id, "catalog entry template_id")
        _text(self.template_ref, "catalog entry template_ref")


@dataclass(frozen=True, slots=True)
class FamilySelection:
    """The session answers a family by selecting a catalog template."""

    family: str
    template_ref: str

    def __post_init__(self) -> None:
        require_identifier(self.family, "selection family")
        _text(self.template_ref, "selection template_ref")


@dataclass(frozen=True, slots=True)
class FamilyDeclination:
    """The session answers a family by declining the catalog, with reason."""

    family: str
    reason: str

    def __post_init__(self) -> None:
        require_identifier(self.family, "declination family")
        _text(self.reason, "declination reason")


def confront_catalog(
    *,
    intended_families: tuple[str, ...],
    catalog: tuple[CatalogEntry, ...],
    selections: tuple[FamilySelection, ...] = (),
    declinations: tuple[FamilyDeclination, ...] = (),
) -> dict[str, object]:
    """Answer every intended family or fail typed; return the receipt payload.

    A family without an answer, an answer without a family, a doubled
    answer, or a selection outside the catalog for that family all
    refuse. Declinations pass with their reasons on the record — they
    are the signal that a new family needs building and later
    harvesting.
    """

    if not isinstance(intended_families, tuple) or not intended_families:
        raise CatalogGateError("intended_families must be a non-empty tuple")
    families = tuple(intended_families)
    for family in families:
        require_identifier(family, "intended family")
    if families != tuple(sorted(set(families))):
        raise CatalogGateError(
            "intended_families require unique deterministic identities"
        )
    if not isinstance(catalog, tuple) or any(
        not isinstance(item, CatalogEntry) for item in catalog
    ):
        raise CatalogGateError("catalog contains an invalid entry")
    if not isinstance(selections, tuple) or any(
        not isinstance(item, FamilySelection) for item in selections
    ):
        raise CatalogGateError("selections contains an invalid item")
    if not isinstance(declinations, tuple) or any(
        not isinstance(item, FamilyDeclination) for item in declinations
    ):
        raise CatalogGateError("declinations contains an invalid item")

    offered: dict[str, dict[str, str]] = {}
    for entry in catalog:
        offered.setdefault(entry.family, {})[entry.template_ref] = (
            entry.template_id
        )

    answers: dict[str, dict[str, object]] = {}
    for selection in selections:
        if selection.family in answers:
            raise CatalogGateError(
                f"family {selection.family!r} is answered more than once"
            )
        family_offer = offered.get(selection.family, {})
        if selection.template_ref not in family_offer:
            raise CatalogGateError(
                f"selection for family {selection.family!r} names a template "
                "outside the offered catalog"
            )
        answers[selection.family] = {
            "family": selection.family,
            "answer": "selected",
            "template_ref": selection.template_ref,
            "template_id": family_offer[selection.template_ref],
        }
    for declination in declinations:
        if declination.family in answers:
            raise CatalogGateError(
                f"family {declination.family!r} is answered more than once"
            )
        answers[declination.family] = {
            "family": declination.family,
            "answer": "declined",
            "reason": declination.reason,
        }

    unanswered = tuple(
        family for family in families if family not in answers
    )
    if unanswered:
        raise CatalogGateError(
            "every intended family needs a catalog answer; missing: "
            f"{list(unanswered)}"
        )
    stray = tuple(sorted(set(answers) - set(families)))
    if stray:
        raise CatalogGateError(
            f"answers name families the session does not intend: {list(stray)}"
        )

    return {
        "schema": "CatalogConfrontationReceipt@1",
        "intended_families": list(families),
        "offered": {
            family: sorted(refs) for family, refs in sorted(offered.items())
        },
        "answers": [answers[family] for family in families],
        "declined_families": [
            family
            for family in families
            if answers[family]["answer"] == "declined"
        ],
        **no_authority(_AUTHORITY),
    }


def record_catalog_confrontation(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    payload: Mapping[str, object],
) -> ProjectRecordRef:
    destination = require_destination(
        destination, producer="catalog confrontation"
    )
    if payload.get("schema") != "CatalogConfrontationReceipt@1":
        raise CatalogGateError(
            "payload must be a CatalogConfrontationReceipt@1"
        )
    return repository.put_json(
        run=run,
        destination=destination,
        record_kind=CONFRONTATION_RECORD_KIND,
        payload=dict(payload),
    )


def emit_harvest_obligations(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    source_ref: ProjectRecordRef,
    inline_families: tuple[str, ...],
    waivers: Mapping[str, str] | None = None,
    assigned_to: str | None = None,
) -> tuple[tuple[ProjectRecordRef, ...], tuple[ProjectRecordRef, ...]]:
    """Emit one obligation — or one recorded waiver — per inline family.

    ``source_ref`` binds the evidence that these families were produced
    inline (a confrontation receipt, an audit, a stage receipt). A
    waiver needs a reason and passes without blocking — the emergency
    path stays open, on the record.
    """

    destination = require_destination(
        destination, producer="harvest obligation emission"
    )
    if not isinstance(source_ref, ProjectRecordRef):
        raise CatalogGateError("source_ref must be a ProjectRecordRef")
    if not isinstance(inline_families, tuple) or not inline_families:
        raise CatalogGateError("inline_families must be a non-empty tuple")
    families = tuple(inline_families)
    for family in families:
        require_identifier(family, "inline family")
    if families != tuple(sorted(set(families))):
        raise CatalogGateError(
            "inline_families require unique deterministic identities"
        )
    waivers = dict(waivers or {})
    stray = tuple(sorted(set(waivers) - set(families)))
    if stray:
        raise CatalogGateError(
            f"waivers name families not produced inline: {list(stray)}"
        )
    if assigned_to is not None:
        _text(assigned_to, "assigned_to")

    obligation_refs: list[ProjectRecordRef] = []
    waiver_refs: list[ProjectRecordRef] = []
    for family in families:
        if family in waivers:
            waiver_refs.append(
                repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind=WAIVER_RECORD_KIND,
                    payload={
                        "schema": "ComponentHarvestWaiver@1",
                        "family": family,
                        "reason": _text(
                            waivers[family], f"waiver reason for {family}"
                        ),
                        "source_ref": source_ref.uri,
                        **no_authority(_AUTHORITY),
                    },
                )
            )
            continue
        obligation_refs.append(
            repository.put_json(
                run=run,
                destination=destination,
                record_kind=OBLIGATION_RECORD_KIND,
                payload={
                    "schema": "ComponentHarvestObligation@1",
                    "family": family,
                    "status": "OPEN",
                    "source_ref": source_ref.uri,
                    "assigned_to": assigned_to,
                    "closes_with": (
                        "a component-template harvest for this family, "
                        "bound by a component-harvest-closure record"
                    ),
                    **no_authority(_AUTHORITY),
                },
            )
        )
    return tuple(obligation_refs), tuple(waiver_refs)


def close_harvest_obligation(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    obligation_ref: ProjectRecordRef,
    template_ref: ProjectRecordRef,
) -> ProjectRecordRef:
    """Close one obligation by naming the harvested template.

    The obligation record itself is immutable; closure is a new record
    binding the two, and the family must match.
    """

    destination = require_destination(
        destination, producer="harvest obligation closure"
    )
    for name, ref in (
        ("obligation_ref", obligation_ref),
        ("template_ref", template_ref),
    ):
        if not isinstance(ref, ProjectRecordRef):
            raise CatalogGateError(f"{name} must be a ProjectRecordRef")
    obligation = repository.load_json(obligation_ref)
    if obligation.get("schema") != "ComponentHarvestObligation@1":
        raise CatalogGateError(
            "obligation_ref does not name a harvest obligation"
        )
    template = repository.load_json(template_ref)
    if template.get("schema") != "ComponentTemplate@1":
        raise CatalogGateError(
            "template_ref does not name a component template"
        )
    if template.get("family") != obligation.get("family"):
        raise CatalogGateError(
            "closure template family does not match the obligation family"
        )
    return repository.put_json(
        run=run,
        destination=destination,
        record_kind=CLOSURE_RECORD_KIND,
        payload={
            "schema": "ComponentHarvestClosure@1",
            "family": obligation["family"],
            "obligation_ref": obligation_ref.uri,
            "template_ref": template_ref.uri,
            **no_authority(_AUTHORITY),
        },
    )
