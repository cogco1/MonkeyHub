"""The program sheet as the studio serves it: read it, and apply it.

Two directions, and the kernel owns both of them. ``state.program_sheet``
derives a sheet from a record and applies a sheet to one; nothing here reads a
zone, measures a footprint or resolves a semantic id. What this module owns is
the arrangement: *which* sheet a reader gets, whether the sheet the architect
authored still belongs to the record it was written against, and how a sheet
becomes a candidate run without touching the record anybody authored.

**Two sheets, and which one you got.** A project may hold the architect's own
``input/runner/program-sheet.json`` — the brief being written, work in progress
like the authored record and the seat pack (ADR-007) — and it always has the
sheet the record itself states. A current input sheet wins because it is the
document a person typed; a stale one remains on disk while the derivation is
returned, because silently rebinding old rows would guess. ``source`` names
which one this is, and ``honesty`` names any stale authored file left aside.

**Applying writes no authored file.** A sheet becomes a *candidate*: the
successor record goes through the same path a proposal's candidate takes
(``candidate.run_operator``), under a detached run, and the authored record is
never rewritten. The one file this can write is the architect's own sheet, and
only when the request asks for it, and only on a local studio: a shared server
has no per-user authoring yet, and one architect's brief overwriting another's
is not a thing to discover afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from typing import Any, Mapping

from archflow.project.inputs import (
    ProgramSheetInvalid,
    ProgramSheetMissing,
    load_program_sheet_file,
    write_program_sheet_file,
)
from archflow.project.layout import PROGRAM_SHEET_PATH
from archflow.semantics.conditions import CONDITIONS
from archflow.semantics.roles import ROLES
from archflow.state.program_sheet import (
    ProgramSheetError,
    compile_sheet_operator,
    sheet_from_record,
    totals_of,
)
from archflow.state.state_record import (
    StateRecord,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
    developed_design_view,
)

from ..settings import LOCAL_MODE, StudioSettings
from ..transport.errors import StudioError
from .binding import ProjectBinding
from .projection import (
    BRANCH_ID,
    PORTFOLIO_ID,
    SELECTION_DECISION_REF,
    StateProjection,
)

# Which sheet a reader was given.
INPUT = "input"
DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class ProgramView:
    """One answer to ``GET /api/program``: a sheet and where it came from."""

    sheet: Mapping[str, Any]
    source: str
    # The digest of the record the sheet is being shown beside — always the
    # state that answers now, whatever the sheet itself claims.
    state_digest: str | None


@dataclass(frozen=True, slots=True)
class ProgramCandidate:
    """One applied sheet: the run it will become, and what it totals."""

    candidate_id: str
    job_id: str
    status: str
    totals: Mapping[str, Any]
    saved_input: bool
    honesty: tuple[str, ...]


def read_program(
    binding: ProjectBinding, projection: StateProjection
) -> ProgramView:
    """The architect's sheet where one exists, else the record's own.

    A current input sheet is returned as authored, with its totals recomputed
    and its honesty extended — never edited otherwise. An input sheet from
    another exact record remains on disk, but this read returns the current
    record's derived sheet so reading again produces something that can be
    applied without silently rebinding the architect's old rows.
    """

    derived = sheet_from_record(projection.record)
    try:
        authored = load_program_sheet_file(binding.repository)
    except ProgramSheetMissing:
        return ProgramView(derived, DERIVED, projection.state_digest)
    except ProgramSheetInvalid as exc:
        raise StudioError(
            422,
            "PROGRAM_SHEET_INVALID",
            f"{PROGRAM_SHEET_PATH}: {exc}",
        ) from exc
    sheet = dict(authored.payload)
    claimed_state = sheet.get("state_digest")
    claimed_record = sheet.get("record_digest")
    if (
        projection.state_digest is None
        or claimed_state != projection.record.state_digest
        or claimed_record != projection.record_digest
    ):
        current = dict(derived)
        current["honesty"] = [
            *(
                str(line)
                for line in current.get("honesty", ())
                if isinstance(line, str)
            ),
            f"{PROGRAM_SHEET_PATH} was written against record {claimed_record} "
            f"and state {claimed_state}, while the project now answers record "
            f"{projection.record_digest} and state "
            f"{projection.record.state_digest}; "
            "the authored file remains unchanged, but it was not returned or "
            "silently rebound because its mapped zones may have moved",
        ]
        return ProgramView(current, DERIVED, projection.state_digest)
    honesty = [
        f"this is the architect's own sheet, read from {PROGRAM_SHEET_PATH}; "
        "the record's own reading of its zones is not what you are looking at",
        *(
            str(line)
            for line in sheet.get("honesty", ())
            if isinstance(line, str)
        ),
    ]
    honesty.append(
        "it was written against the record and state that answer now "
        f"({projection.record_digest}, {projection.record.state_digest})"
    )
    sheet["honesty"] = honesty
    sheet["totals"] = totals_of(sheet)
    return ProgramView(sheet, INPUT, projection.state_digest)


def semantic_terms() -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """Every registered role and condition: (table, id, meaning, aliases).

    The one place the API says what the record's semantic vocabulary is. A
    client that wants a function dropdown asks the server; it does not carry a
    copy of two Python tables that the record can refuse it for.
    """

    return tuple(
        (table, term.id, term.meaning, term.aliases)
        for table, terms in (("role", ROLES), ("condition", CONDITIONS))
        for term in terms
    )


def operator_for(
    sheet: Mapping[str, Any], projection: StateProjection
) -> StateRecordOperator:
    """The typed program operator, preflighted against the state on screen.

    The kernel refuses a sheet it cannot apply — an unregistered function, a
    requirement it has no relation kind for, a space id that would rewrite an
    entity — and every one of those is the request's fault, not the project's.
    They arrive as 422 with the kernel's own sentence, because the sentence
    names the row.

    The successor is then *viewed*, here, before any run exists. The candidate
    would build exactly this view on its worker thread as its first act, and a
    record the kernel will not view is a refusal the architect can be given now
    instead of a failed job in a minute. Nothing is written by the check: the
    view is a pure function, and the run it binds to is the one the projection
    already answered for.
    """

    try:
        operator = compile_sheet_operator(
            projection.record,
            sheet,
        )
        successor = apply_state_record_operator(projection.record, operator)
    except ProgramSheetError as exc:
        raise StudioError(422, "PROGRAM_SHEET_INVALID", str(exc)) from exc
    except StateRecordError as exc:
        # The sheet was well formed and the record it would make is not one.
        raise StudioError(
            422,
            "PROGRAM_SHEET_NOT_APPLICABLE",
            f"applying this sheet would not produce a valid record: {exc}",
        ) from exc
    try:
        developed_design_view(
            successor,
            run=projection.run,
            portfolio_id=PORTFOLIO_ID,
            branch_id=BRANCH_ID,
            selection_decision_ref=SELECTION_DECISION_REF,
        )
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise StudioError(
            422,
            "PROGRAM_SHEET_NOT_APPLICABLE",
            "the kernel would not build a design view of the record this "
            f"sheet makes: {exc}. A Space@1 the sheet adds carries no volume, "
            "and in a record that already draws massing every zone is an "
            "occupied region — so a space of the brief that nothing has been "
            "drawn for yet cannot enter that record as a zone.",
        ) from exc
    return operator


def save_input_sheet(
    binding: ProjectBinding,
    settings: StudioSettings,
    sheet: Mapping[str, Any],
    *,
    candidate_id: str,
    job_id: str,
) -> None:
    """Write the architect's sheet to its work-in-progress path, or refuse.

    Local only. On a shared server this API has one project and no idea whose
    request it is answering, so writing an authored file would let one
    architect's brief silently replace another's. The candidate is made either
    way — running the sheet is a read of the record, and it is the *saving*
    that has no owner yet — so the refusal names the run that was made, and the
    caller keeps it.
    """

    if settings.mode != LOCAL_MODE:
        raise StudioError(
            409,
            "WIP_WRITE_REMOTE",
            f"{PROGRAM_SHEET_PATH} is written only by a local studio. This "
            "server runs in remote mode and cannot say whose sheet it would "
            "be saving, so the file is left alone until per-user authoring "
            f"lands. The sheet still ran: candidate {candidate_id}, job "
            f"{job_id}. Ask again with saveInput false and this refusal goes "
            "away.",
        )
    try:
        write_program_sheet_file(binding.repository, sheet)
    except ProgramSheetInvalid as exc:
        raise StudioError(
            422, "PROGRAM_SHEET_INVALID", f"{PROGRAM_SHEET_PATH}: {exc}"
        ) from exc


def closure_of_sheet(sheet: Mapping[str, Any]) -> frozenset[str]:
    """Everything applying this sheet touches, as the record's own refs.

    The queue's contract, answered for a sheet the way ``proposals.closure_of``
    answers it for a proposal: two jobs whose closures intersect never run at
    the same time. A sheet touches the zones it maps to and the zones it adds.
    """

    refs: set[str] = set()
    for department in sheet.get("departments", ()):
        if not isinstance(department, Mapping):
            continue
        for space in department.get("spaces", ()):
            if not isinstance(space, Mapping):
                continue
            named = space.get("zone_id") or space.get("space_id")
            if isinstance(named, str) and named:
                refs.add(f"entity:{named}")
    return frozenset(refs)


def candidate_run_id() -> str:
    """The run id an applied sheet becomes: when, what from, and which one.

    The shape ``candidate._run_id`` mints for a proposal, with ``program``
    where a proposal id would be — the four random hex characters are what
    keeps two applications made inside one second from naming one run.
    """

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"studio-cand-{stamp}-program-{secrets.token_hex(2)}"


def candidate_honesty(saved: bool, settings: StudioSettings) -> tuple[str, ...]:
    """What an applied sheet did not do, said where a client will read it."""

    lines = [
        "the authored record was not rewritten: this sheet was applied to a "
        "copy of it, and the copy is what the candidate run executed",
        "GET /api/jobs/{jobId} tracks this work while it runs; after the run "
        "finishes, GET /api/candidates/{id} reads the candidate from its "
        "retained project records even though it came from a sheet",
    ]
    if not saved:
        lines.append(
            f"{PROGRAM_SHEET_PATH} was not written: this sheet lives in the "
            "run it produced and in your browser, nowhere else"
        )
    if settings.rhino_export:
        lines.append(
            "this run exports, so it waits for the one Rhino lane on this "
            "machine"
        )
    return tuple(lines)
