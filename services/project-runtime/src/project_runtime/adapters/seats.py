"""The seats a project declares, read onto the kernel's ``SeatSpec``.

Seats are people, not state: who owns which components, who reviews, who
consumes whose handover. The project authors that beside its authored record,
and the runner takes it as given. A candidate continues its exact source's
retained seats; explicit component removals retire only those roots for the
new run, without changing the project's authored pack. There
is deliberately no default seat and no fallback: a studio that invented a seat
when the file was missing would run somebody's design under an ownership
nobody declared, and the receipt would say it was fine.

A missing seat pack is therefore a named failure carrying the path, which is
the one piece of information whoever has to fix it needs.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any, Mapping

from monkeyarch.capabilities.declaration import DeclarationQuadrant
from monkeyarch.capabilities.discipline_seats import SeatSpec
from archflow.project.inputs import (
    SeatPackInvalid,
    SeatPackMissing,
    load_seat_pack_file,
)
from archflow.project.layout import SEAT_PACK_PATH
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DISCIPLINE_SEAT, SEAT_ROUND_RECEIPT
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, StateRecordOperator
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline


class SeatsError(ValueError):
    """The project's seat pack is absent or cannot be read as seats.

    Deliberately not a ``StudioError``: loading seats happens inside the
    candidate job, on a worker thread, where there is no request to answer.
    The job reports it as a failure with this message in it.
    """


def seat_from(payload: Mapping[str, Any]) -> SeatSpec:
    """One authored seat as the kernel's ``SeatSpec``."""

    return SeatSpec(
        seat_id=payload["seat_id"],
        disciplines=tuple(
            DevelopmentDiscipline(d) for d in payload["disciplines"]
        ),
        owned_component_ids=tuple(sorted(payload.get("owned_component_ids", ()))),
        phases=tuple(DesignPhase(p) for p in payload["phases"]),
        quadrants=tuple(
            DeclarationQuadrant(q) for q in payload.get("quadrants", ())
        ),
        consumes=tuple(sorted(payload.get("consumes", ()))),
        reviewer=bool(payload.get("reviewer", False)),
    )


def load_seat_pack(
    repository: FilesystemProjectRepository,
) -> Mapping[str, Any]:
    """The project's authored seat pack, or a refusal naming the file.

    The pack carries more than the seats — the commitment the run is made
    under and the provider identity that answers for its geometry — so it is
    returned whole and the caller reads what it needs from it.
    """

    layout = repository.layout
    try:
        pack = load_seat_pack_file(repository)
    except SeatPackMissing as exc:
        raise SeatsError(
            f"SEATS_NOT_FOUND: {layout.project_id} declares no seats at "
            f"{SEAT_PACK_PATH} under {layout.root}. A candidate runs the "
            "seats the project authored; it never invents one."
        ) from exc
    except SeatPackInvalid as exc:
        raise SeatsError(str(exc)) from exc
    if not pack.payload.get("seats"):
        raise SeatsError(
            f"{SEAT_PACK_PATH}: no seats declared. A run with no seat "
            "produces no geometry and would report an empty success."
        )
    return pack.payload


def seats_of(payload: Mapping[str, Any]) -> tuple[SeatSpec, ...]:
    """Every declared seat, in the order the project authored them."""

    try:
        return tuple(seat_from(seat) for seat in payload["seats"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SeatsError(f"{SEAT_PACK_PATH}: {exc}") from exc


def candidate_seats(
    repository: FilesystemProjectRepository,
    payload: Mapping[str, Any],
    source: StateRecord,
    operator: StateRecordOperator,
    *,
    source_receipt: Mapping[str, Any] | None = None,
) -> tuple[SeatSpec, ...]:
    """Continue exact retained ownership, retiring only explicitly removed roots."""
    seats = _retained_seats(repository, source, source_receipt)
    if seats is None:
        # Authored/legacy bases have no retained seat pack. Missing roots in
        # this pack still reach owned_subtree and fail; absence is not deletion.
        seats = seats_of(payload)
    removed = {
        entity.entity_id for entity in source.entities_of("Component@1")
        if entity.entity_id in operator.remove_entity_ids
    }
    retired = {
        seat.seat_id for seat in seats
        if not seat.reviewer and set(seat.owned_component_ids) <= removed
    }
    result = tuple(
        replace(seat,
                owned_component_ids=tuple(root for root in seat.owned_component_ids if root not in removed),
                consumes=tuple(name for name in seat.consumes if name not in retired))
        for seat in seats if seat.seat_id not in retired
    )
    if not any(not seat.reviewer for seat in result):
        raise SeatsError("The component removal leaves no authoring seat to run.")
    return result


def _retained_seats(
    repository: FilesystemProjectRepository,
    source: StateRecord,
    receipt: Mapping[str, Any] | None,
) -> tuple[SeatSpec, ...] | None:
    """Read only the selected run, using its round receipts' exact seat refs."""
    if receipt is None or "rounds" not in receipt:
        return None
    try:
        run = source.run_ref
        if (receipt.get("project_id") != run.project_id or receipt.get("run_id") != run.run_id
                or receipt.get("state_record_digest") != source.digest):
            raise ValueError("the source runner receipt does not match its state record")
        expected_parent = PurePosixPath("runs", run.run_id, "records")

        def load(uri: str, kind: str) -> Mapping[str, Any]:
            ref = record_ref_from_uri(uri, run.project_id)
            if ref.record_kind != kind or PurePosixPath(ref.relative_path).parent != expected_parent:
                raise ValueError("a source seat reference belongs to another run or record kind")
            return repository.load_json(ref)

        ids = [seat_id for round_ids in receipt["rounds"] for seat_id in round_ids]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("the source runner receipt has no unique seat schedule")
        rows = {row["seat_id"]: row for row in receipt.get("seat_results", ())}
        if len(rows) != len(receipt.get("seat_results", ())) or set(rows) - set(ids):
            raise ValueError("the source seat results disagree with its schedule")
        result = []
        unreferenced = None
        for seat_id in ids:
            row = rows.get(seat_id)
            if row is not None and row.get("receipt_ref"):
                round_receipt = load(row["receipt_ref"], SEAT_ROUND_RECEIPT)
                if (round_receipt.get("schema") != "SeatRoundReceipt@1"
                        or round_receipt.get("seat_id") != seat_id
                        or round_receipt.get("program_ref") != row.get("program_ref")):
                    raise ValueError("the source seat round disagrees with its runner receipt")
                saved = load(round_receipt["seat_ref"], DISCIPLINE_SEAT)
            else:
                if row is not None and row.get("status") != "empty":
                    raise ValueError(f"source seat {seat_id!r} has no retained round receipt")
                # Empty and reviewer seats have no round receipt. The source
                # schedule must identify one unambiguous retained definition.
                if unreferenced is None:
                    refs = repository.list_json(run=run, destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD, run_id=run.run_id))
                    unreferenced = [repository.load_json(ref) for ref in refs if ref.record_kind == DISCIPLINE_SEAT]
                matches = [value for value in unreferenced if value.get("seat_id") == seat_id]
                if len(matches) != 1:
                    raise ValueError(f"source seat {seat_id!r} has no unique retained definition")
                saved = matches[0]
            if saved.get("schema") != SeatSpec.SCHEMA or saved.get("seat_id") != seat_id:
                raise ValueError("the retained seat identity does not match its schedule")
            seat = seat_from(saved)
            if row is None and not seat.reviewer:
                raise ValueError(f"source authoring seat {seat_id!r} has no execution result")
            if row is not None and seat.reviewer:
                raise ValueError(f"source reviewer seat {seat_id!r} has an authoring result")
            result.append(seat)
        return tuple(result)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise SeatsError(f"SOURCE_SEATS_INVALID: {exc}") from exc
