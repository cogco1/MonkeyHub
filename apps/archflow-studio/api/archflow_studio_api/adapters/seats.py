"""The seats a project declares, read onto the kernel's ``SeatSpec``.

Seats are people, not state: who owns which components, who reviews, who
consumes whose handover. The project authors that beside its authored record,
and the runner takes it as given — so this module turns what
``archflow.project.inputs`` read into ``SeatSpec`` and does nothing else. There
is deliberately no default seat and no fallback: a studio that invented a seat
when the file was missing would run somebody's design under an ownership
nobody declared, and the receipt would say it was fine.

A missing seat pack is therefore a named failure carrying the path, which is
the one piece of information whoever has to fix it needs.
"""

from __future__ import annotations

from typing import Any, Mapping

from monkeyarch.capabilities.declaration import DeclarationQuadrant
from monkeyarch.capabilities.discipline_seats import SeatSpec
from archflow.project.inputs import (
    SeatPackInvalid,
    SeatPackMissing,
    load_seat_pack_file,
)
from archflow.project.layout import SEAT_PACK_PATH
from archflow.project.repository import FilesystemProjectRepository
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
