"""Issuing a run as the published design (ADR-007 rule 5).

ISO 19650 calls the act that moves a container from *shared* to *published* an
**issue** (出图). ADR-007 fixed the published container on the P036 ``HEAD``
file and fixed the condition: an issue cites the closure of the stage the
workflow says is next, there is no issue without a satisfied closure, and no
closure without a retained envelope. This module is that act, and nothing
else: it reads one run, refuses every reason it must, and calls the two
repository operations that already own the move.

*Which* stage was next is not re-decided here. ``StageExecutionGuard`` decided
it before the run wrote anything, against the retained workflow and the
predecessor's exit binding (ADR-007 rule 2), and the closure this module cites
is the closure of the stage the run opened under. An issue that re-derived the
ladder would be a second opinion about the same order.

``HEAD``, ``PromotionDecision@1``, ``prepare_transition``, ``compare_and_swap``
and ``read_head`` keep their names here because retained digests and the
repository format bind them (ADR-004): the decision receipt's key set is
checked byte-for-byte by ``prepare_transition``, and the file is named ``HEAD``
by the format. The word a person reads is *published*, and the act is *issue*.

What is refused, and why
------------------------

* a run whose ``base`` is not the published version - ``StaleBase``. A run that
  was not developed against what is published now cannot replace it; the
  compare-and-swap would refuse it anyway, and refusing here says which run and
  which version rather than which digest.
* a run with no ``stage-closure`` that says ``SATISFIED``, more than one, a
  closure whose findings left it ``OPEN``, or a satisfied closure with no
  ``stage-exit-binding`` naming it - ``NoSatisfiedClosure``. A closure and its
  exit binding are one fact in two records; either alone is not a closed stage.
* a run with no ``runner-run-receipt``, or one whose seats did not all execute,
  or one naming records this run does not retain - ``RunNotComplete``.

The replacement canonical state
-------------------------------

The published state is the ``CanonicalProjectState@1`` the P036 snapshot has
always held: ``schema``, ``authoritative_record_refs``, ``derived_record_refs``
and ``phase``. Nothing is added to it, and the facts an issue must carry land
on the three content fields it already has:

* ``authoritative_record_refs`` - one URI: the run's retained ``state-record``,
  the authored record it executed. It is the authority for the design content;
  everything else was computed from it.
* ``derived_record_refs`` - two URIs, sorted: the run's
  ``developed-design-state`` and the ``stage-closure`` that closed its stage.
  Sorted so the state's digest does not depend on the order this module
  happened to build them in; the kind in each file name says which is which.
* ``phase`` - the ``stage_id`` of the closed stage. The published design is at
  that stage and no further.

A retained record's file name is ``<kind>-<sha256>.json``, so every URI above
*is* that record's digest as well as its address. The two **content** digests -
``StateRecord.digest`` and the developed state's ``state_digest`` - are not
copied here: each is a field of the record its URI names, and the URI binds
that record's bytes. Two identities, never three (ADR-003); a third copy of an
identity is a third place for it to disagree.

``decided_by`` and ``note`` are the issuer's words and are **not retained**.
``prepare_transition`` compares the decision receipt's key set against an exact
set of six, so a ``PromotionDecision@1`` carrying either would be refused as
schema drift, and that schema is bound by retained digests (ADR-004). They are
checked and printed by whoever ran the issue - ``tools/issue_project.py`` puts
both on the console - and they are on no record. Retaining them needs a
``PromotionDecision@2`` and a change to the repository's promotion gate, which
is a separate decision. Until then, do not read a project's history for who
issued it: read it for what was issued.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    PROMOTION_DECISION,
    RUNNER_RUN_RECEIPT,
    STAGE_CLOSURE,
    STAGE_EXIT_BINDING,
)
from archflow.project.refs import ProjectRecordRef, RunRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.stage_workflow import (
    CompositeStageClosureReceipt,
    StageClosureError,
    StageClosureStatus,
    StageExitBinding,
    StageWorkflowError,
)
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_derived as _register_derived_fields,
)


CANONICAL_STATE_SCHEMA = "CanonicalProjectState@1"
DECISION_SCHEMA = "PromotionDecision@1"
DECISION_ACCEPTED = "accepted"

STATE_RECORD_REF = "state_record_ref"
DESIGN_STATE_REF = "design_state_ref"


class IssueError(RuntimeError):
    """This run cannot be issued as the published design."""


class StaleBase(IssueError):
    """The run was developed against a version that is no longer published."""


class NoSatisfiedClosure(IssueError):
    """The run's stage did not close, or its closure has no exit binding."""


class RunNotComplete(IssueError):
    """The run did not finish, or does not retain what it says it does."""


@dataclass(frozen=True, slots=True)
class IssueReceipt:
    """What one issue was: the version it made, and the records it stood on.

    ``issue`` is the new published version and ``previous`` the one it
    replaced, so a caller can say "issue 3, from issue 2" without reading
    ``HEAD`` again. Everything else names a record the repository verified.
    """

    issue: int
    previous: int
    run_id: str
    stage_id: str
    closure_ref: str
    decision_ref: str
    state_sha256: str


def issue_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str,
    decided_by: str,
    note: str = "",
) -> IssueReceipt:
    """Issue one run as the published design, or refuse and say why.

    The order is the order of the refusals: the base first, because a stale
    run is not worth reading; then the closure, because a run that did not
    close its stage cannot be issued whatever else it holds; then the receipt.
    Only after all three does anything get written, so a refused issue leaves
    the project exactly as it was.
    """

    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("decided_by must name who issued this design")
    if not isinstance(note, str):
        raise TypeError("note must be text")
    require_identifier(run_id, "run_id")

    run = repository.load_run(run_id)
    published = repository.read_head()
    if run.base != published:
        raise StaleBase(
            f"run {run_id!r} was developed against version "
            f"{run.base.version}, and version {published.version} is "
            "published; re-run against what is published now"
        )

    records = _run_records(repository, run)
    closure, closure_ref = _satisfied_closure(records, run_id)
    _exit_binding(records, closure, closure_ref, run_id)
    receipt = _run_receipt(records, run_id)
    authored_ref = _retained_ref(receipt, records, STATE_RECORD_REF, run_id)
    developed_ref = _retained_ref(receipt, records, DESIGN_STATE_REF, run_id)

    replacement_state = {
        "schema": CANONICAL_STATE_SCHEMA,
        "authoritative_record_refs": [authored_ref],
        "derived_record_refs": sorted((developed_ref, closure_ref.uri)),
        "phase": closure.stage_id,
    }
    decision_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
        record_kind=PROMOTION_DECISION,
        # Exactly the six keys prepare_transition compares its expected set
        # against; the receipt is the gate, so it carries no word of its own.
        payload={
            "schema": DECISION_SCHEMA,
            "status": DECISION_ACCEPTED,
            "project_id": run.project_id,
            "run_id": run.run_id,
            "checked_state": published.to_dict(),
            "candidate_ref": closure_ref.uri,
        },
    )
    prepared = repository.prepare_transition(
        run=run,
        expected=published,
        replacement_state=replacement_state,
        decision_receipt=decision_ref,
    )
    issued = repository.compare_and_swap(
        expected=prepared.expected,
        event=prepared.event,
        replacement=prepared.replacement,
    )
    return IssueReceipt(
        issue=issued.version,
        previous=published.version,
        run_id=run.run_id,
        stage_id=closure.stage_id,
        closure_ref=closure_ref.uri,
        decision_ref=decision_ref.uri,
        state_sha256=issued.require_digest(),
    )


def _run_records(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...]:
    """Every record the run retained, as (kind, ref, payload).

    The ref travels beside the payload because an issue cites records by URI
    and a content-addressed record cannot carry its own reference (ADR-005).
    A file whose name is not ``<kind>-<sha256>.json`` has no kind and comes
    back as ``None`` rather than as a guess.
    """

    rows: list[tuple[str | None, ProjectRecordRef, Mapping[str, Any]]] = []
    for ref in repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
    ):
        try:
            kind: str | None = ref.record_kind
        except ValueError:
            kind = None
        rows.append((kind, ref, repository.load_json(ref)))
    return tuple(rows)


def _of_kind(
    records: tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...],
    kind: str,
) -> tuple[tuple[ProjectRecordRef, Mapping[str, Any]], ...]:
    return tuple(
        (ref, payload) for found, ref, payload in records if found == kind
    )


def _satisfied_closure(
    records: tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...],
    run_id: str,
) -> tuple[CompositeStageClosureReceipt, ProjectRecordRef]:
    """The one SATISFIED closure this run retains, with the ref that names it.

    Every closure the run holds is parsed, not only the satisfied one: a
    closure the reader cannot understand is a reason to refuse an issue, and a
    run whose stage is still open should be told what its findings were.
    """

    closures = _of_kind(records, STAGE_CLOSURE)
    if not closures:
        raise NoSatisfiedClosure(
            f"run {run_id!r} retains no stage closure; a stage closes through "
            "the runner's own checks, and there is no issue without one"
        )
    parsed: list[tuple[CompositeStageClosureReceipt, ProjectRecordRef]] = []
    for ref, payload in closures:
        try:
            parsed.append((CompositeStageClosureReceipt.from_dict(payload), ref))
        except (StageClosureError, TypeError, ValueError) as exc:
            raise NoSatisfiedClosure(
                f"run {run_id!r} retains a stage closure that cannot be read "
                f"as CompositeStageClosureReceipt@1: {exc}"
            ) from exc
    satisfied = [
        item
        for item in parsed
        if item[0].status is StageClosureStatus.SATISFIED
    ]
    if not satisfied:
        findings = sorted(
            {
                finding.code.value
                for closure, _ in parsed
                for finding in closure.findings
            }
        )
        raise NoSatisfiedClosure(
            f"run {run_id!r} closed no stage: its closure is OPEN on "
            f"{', '.join(findings) or 'no recorded finding'}"
        )
    if len(satisfied) > 1:
        stages = sorted(closure.stage_id for closure, _ in satisfied)
        raise NoSatisfiedClosure(
            f"run {run_id!r} retains {len(satisfied)} satisfied closures "
            f"({', '.join(stages)}); an issue cites one stage"
        )
    return satisfied[0]


def _exit_binding(
    records: tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...],
    closure: CompositeStageClosureReceipt,
    closure_ref: ProjectRecordRef,
    run_id: str,
) -> StageExitBinding:
    """The exit binding derived from exactly this closure.

    The binding is matched on the closure's own URI and re-checked against the
    closure's digest and stage, so a run holding two stages' records cannot
    issue one stage's closure under another stage's exit.
    """

    bindings = _of_kind(records, STAGE_EXIT_BINDING)
    parsed: list[StageExitBinding] = []
    for _, payload in bindings:
        try:
            parsed.append(StageExitBinding.from_dict(payload))
        except (StageWorkflowError, TypeError, ValueError) as exc:
            raise NoSatisfiedClosure(
                f"run {run_id!r} retains a stage exit binding that cannot be "
                f"read as StageExitBinding@1: {exc}"
            ) from exc
    matched = [item for item in parsed if item.closure_ref == closure_ref.uri]
    if not matched:
        raise NoSatisfiedClosure(
            f"run {run_id!r} has a satisfied closure for stage "
            f"{closure.stage_id!r} and no stage exit binding naming it"
        )
    if len(matched) > 1:
        raise NoSatisfiedClosure(
            f"run {run_id!r} retains {len(matched)} exit bindings for one "
            "closure; a closed stage has one exit"
        )
    binding = matched[0]
    if binding.closure_digest != closure.receipt_digest:
        raise NoSatisfiedClosure(
            f"run {run_id!r} has an exit binding whose closure digest is not "
            "the digest of the closure it names"
        )
    if binding.stage_id != closure.stage_id:
        raise NoSatisfiedClosure(
            f"run {run_id!r} has an exit binding for stage "
            f"{binding.stage_id!r} bound to a closure of stage "
            f"{closure.stage_id!r}"
        )
    return binding


def _run_receipt(
    records: tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...],
    run_id: str,
) -> Mapping[str, Any]:
    """The one run receipt, and the seats it says all executed."""

    receipts = _of_kind(records, RUNNER_RUN_RECEIPT)
    if len(receipts) != 1:
        raise RunNotComplete(
            f"run {run_id!r} retains {len(receipts)} runner run receipts; a "
            "finished run retains exactly one"
        )
    payload = receipts[0][1]
    if not payload.get("seat_execution_complete"):
        raise RunNotComplete(
            f"run {run_id!r} did not finish: its receipt does not say every "
            "seat executed"
        )
    return payload


def _retained_ref(
    receipt: Mapping[str, Any],
    records: tuple[tuple[str | None, ProjectRecordRef, Mapping[str, Any]], ...],
    field: str,
    run_id: str,
) -> str:
    """One URI the receipt names, checked to be a record this run retains.

    The published state names records, so every one of them is a record the
    repository has verified in this run - not a string the receipt happened to
    carry.
    """

    value = receipt.get(field)
    if not isinstance(value, str) or not value:
        raise RunNotComplete(
            f"run {run_id!r} receipt names no {field}; there is nothing to "
            "publish"
        )
    if value not in {ref.uri for _, ref, _ in records}:
        raise RunNotComplete(
            f"run {run_id!r} receipt names a {field} the run does not retain"
        )
    return value


# A promotion decision states the exact published version it checked, and
# derives nothing from it.
VERSION_REF_POINTERS = {"PromotionDecision@1": ("/checked_state",)}

_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("PromotionDecision@1", ())
