"""What a finished candidate validates to: a receipt and review readiness.

Two answers travel together here and they are deliberately not the same answer.

The **receipt** is the kernel's. ``validate_submission`` is called with the
three production validators and whatever it says is what this module reports —
its findings verbatim, its ``passed`` unedited. No check is repeated on this
side of the boundary, no finding is filtered, and there is no studio-owned
validator anywhere in this file. A gate re-implemented beside the kernel is a
second opinion, and two opinions about whether a design is admissible is one
too many.

The **review readiness** is the server's, and it is a fixed conjunction of five named
clauses: the receipt passed, the runner finished its seats, no relation was
violated, every declared relation was actually checked, and every artifact the
run was asked to export is available and its export succeeded. ``blockedBy``
names each clause that failed, by the same name every time, because "cannot
review" without a reason is a red light nobody can act on. The fourth clause
is the point of the other three: ``held`` is true whenever nothing was
violated — including when nothing was checked — so a candidate whose
relations nobody could check is never green. The fifth reads the run receipt's
own seat rows beside the artifact records: a seat the runner exported for
carries a ``cad`` block, and a block with no available artifact behind it
blocks review readiness. It is vacuous only for a candidate no seat of which
attempted an export, which is now something the records say rather than
something an empty list was taken to mean.

The coverage description reads the published references and the candidate's
exact retained StateRecord. A record can declare obligations without supplying
an executable criterion or an authorized commitment. These sources are shown
separately from the receipt: project conditions are not yet projected into its
two normative gates (P110), and their empty input is not a successful check.

Nothing here writes. A validation is a reading of records the run already
retained; the published position, ``canonical/`` and ``input/`` are untouched,
and nothing is issued by being ready for review. Only ``project.issue`` can
issue a run or advance a formal workflow stage.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable, Mapping

from archflow.state.model import ArtifactRef, CanonicalState
from archflow.state.state_record import StateRecord
from archflow.submission.model import CandidateDelta, CandidateSubmission, Claim
from archflow.validation.engine import (
    ArtifactPresentValidator,
    AuthorizedCommitmentClaimsValidator,
    ObligationDischargeValidator,
    validate_submission,
)
from archflow.validation.model import ValidationReceipt

from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
    parse_record_file_name,
    record_ref_from_uri,
)
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.repository import ProjectRepositoryError

from ..ports import StudioEventSink
from ..transport.errors import StudioError, error_sentence
from .artifacts import ArtifactRecord
from .binding import ProjectBinding, ReferenceRun
from .candidate import CandidateRun, RelationTotals, SeatOutcome

# The claim a candidate makes about itself: this run happened, and here is the
# record that says so. It is not a design assertion and it gates nothing; it
# exists so the submission names its own run in a form the kernel can check.
RUN_CLAIM_KEY = "studio.candidate.run_id"

# The seat programs travel as JSON records, which is what they are on disk.
PROGRAM_MEDIA_TYPE = "application/json"

# The three production validators, in the order they are run. The
# compatibility-only ``RequiredClaimsValidator`` is deliberately absent: it
# gates against a ``GoalContract`` that production state does not carry, and
# would answer ``compatibility.goal_contract_missing`` on every real project —
# a refusal about the validator, not about the design.
VALIDATORS = (
    ArtifactPresentValidator,
    ObligationDischargeValidator,
    AuthorizedCommitmentClaimsValidator,
)

# Read off the validators rather than retyped, so the wire cannot name a gate
# that is not run.
VALIDATOR_NAMES = tuple(validator.name for validator in VALIDATORS)

# Of those three, the one that had anything to check. See the module docstring.
# Read off the validator for the same reason the full list is.
EFFECTIVE_CHECKS = (ArtifactPresentValidator.name,)

# What the studio could not put into the submission, in lines the UI shows
# verbatim. A dropped program that nobody mentioned would make the receipt look
# like it covered more than it did — and a confession that named the wrong
# cause would send whoever reads it looking in the wrong place, so the two ways
# a program can fail to be nameable say which one happened.
UNPARSED_PROGRAM_NAME = (
    "seat {seat_id}: program record name could not be parsed; its program "
    "was not submitted for validation"
)
MISSING_PROGRAM_DIGEST = (
    "seat {seat_id}: seat carries no program digest; its program was not "
    "submitted for validation"
)

VALIDATOR_NOTE = (
    "Project conditions remain unchecked (P110): retained obligations are not "
    "projected into obligation-discharge, and this submission carries no "
    "obligation discharge. No source-backed authorized commitment and matching "
    "claim are supplied to authorized-commitment-claims. Record declarations, "
    "parameters and evidence citations do not establish that authorization. "
    "These gates check discharge IDs and claim/evidence presence; they do not "
    "execute architectural criteria. This receipt checks artifact presence "
    "and base match only. Declared relation results are reported separately."
)

# The five clauses of review readiness, named exactly as they travel.
RECEIPT_CLAUSE = "validation.receipt"
SEATS_CLAUSE = "runner.seat_execution_complete"
HELD_CLAUSE = "relations.held"
CHECKED_CLAUSE = "relations.fully_checked"
EXPORTS_CLAUSE = "runner.exports_available"

VALIDATION_COMPUTED = "validation.computed"

# One honesty line per artifact the candidate could not deliver: named by the
# stage that was supposed to produce it, the file it was supposed to be, and
# what the run actually says happened. A field the record does not have is
# ``-``, never a guessed reason.
EXPORT_UNAVAILABLE = (
    "export of {stage_id} ({file_name}) is not available: status {status}, "
    "reason {reason}"
)

# One honesty line per seat whose run receipt says an export was attempted and
# whose result no artifact record accounts for. The artifact lines above answer
# for records the project has; this one answers for the export that left none,
# which is the failure an artifact list alone cannot show.
EXPORT_UNMATCHED = (
    "export of {seat_id} was attempted ({status}) but no artifact record is "
    "available for it"
)


@dataclass(frozen=True, slots=True)
class ReviewReadiness:
    """Whether a candidate is ready for review, and every refusing clause."""

    review_ready: bool
    blocked_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateValidation:
    """One candidate's receipt and review readiness for the route."""

    candidate_id: str
    receipt: ValidationReceipt
    seat_execution_complete: bool
    # Copied from the candidate, never recomputed: the readout and readiness
    # answer for the same three states or they are two different candidates.
    relation_checks: RelationTotals
    review_ready: bool
    blocked_by: tuple[str, ...]
    # What the submission could not carry. Normally empty; never absent.
    honesty: tuple[str, ...]
    # Source description, not a claim that the receipt checked these duties.
    canonical_facts: str


def _record_conditions(ref: ProjectRecordRef, record: StateRecord) -> str:
    """Describe retained duties without interpreting them as check results."""

    details = "; ".join(
        f"{item.obligation_id} [{item.status.value}]: {item.statement} "
        f"(source {item.source_ref}; validator {item.validator_ref or 'not declared'})"
        for item in record.obligations
    )
    return (
        f"{ref.uri} declares {len(record.obligations)} obligation(s)"
        + (f": {details}." if details else ".")
    )


def _condition_sources(
    binding: ProjectBinding,
    head: ProjectVersionRef,
    candidate: CandidateRun,
    published: Mapping[str, Any],
) -> str:
    """Read only the published refs and this candidate's bound record.

    The strings are coverage information, not a CanonicalState projection.
    Existing P036 readers verify bytes and the binding owner verifies the
    candidate's record, run, base and content digest.
    """

    repository = binding.repository
    lines = [f"Published version {head.version} ({head.state_sha256})."]
    references = published.get("authoritative_record_refs")
    if references is None:
        lines.append("This published state does not declare authoritative_record_refs.")
    elif not isinstance(references, list):
        lines.append("Published authoritative_record_refs cannot be read as a list.")
    elif not references:
        lines.append("No authoritative record references are published.")
    else:
        for uri in references:
            try:
                ref = record_ref_from_uri(uri, head.project_id)
                payload = repository.load_json(ref)
                if ref.record_kind == STATE_RECORD and payload.get("schema") == StateRecord.SCHEMA:
                    record = StateRecord.from_dict(payload)
                    if record.project_id != head.project_id:
                        raise ValueError("published StateRecord belongs to another project")
                    lines.append("Published StateRecord: " + _record_conditions(ref, record))
                else:
                    lines.append(f"Published source {ref.uri}: condition content is not inspected.")
            except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
                lines.append(
                    f"Published source {uri} could not be verified: {error_sentence(exc)}."
                )

    try:
        run = binding.load_run(candidate.candidate_id)
        receipt = repository.load_json(
            record_ref_from_uri(candidate.receipt_ref, head.project_id)
        )
        ref, record = binding.exact_state_record(
            ReferenceRun(run=run, source="validation", receipt=receipt)
        )
        lines.append(
            f"Candidate {run.run_id}, based on version {run.base.version} "
            f"({run.base.state_sha256}): " + _record_conditions(ref, record)
        )
    except (ProjectRepositoryError, StudioError, OSError, TypeError, ValueError) as exc:
        lines.append(
            f"Candidate source named by {candidate.receipt_ref} could not be "
            f"verified: {error_sentence(exc)}."
        )
    lines.append(
        "The candidate record is not substituted for the published sources. "
        "Recorded obligation statuses are declarations, not results of this "
        "validation; project conditions remain unchecked (P110)."
    )
    return " ".join(lines)


def _submission(
    candidate: CandidateRun,
    artifacts: tuple[ArtifactRef, ...],
) -> CandidateSubmission:
    """The candidate as something the kernel can be asked about.

    Everything in it came off the run's own records. The artifacts are the
    seats' compiled geometry programs — identified by the program digest,
    located by the record the runner retained, and digested by that record's
    own sha — and each artifact id is repeated in ``evidence_refs`` because
    ``ArtifactPresentValidator`` reports ``artifact.evidence_missing`` for an
    added artifact that is not also evidence.

    The base is the candidate's own, never the published version. A candidate
    stood on the
    version it was created against; if the project has moved since, that is a
    fact the kernel states as ``state.base_mismatch``, and re-basing the
    submission to make it agree would be the studio answering a question it was
    asked to pose.
    """

    return CandidateSubmission(
        submission_id=candidate.candidate_id,
        base=candidate.base,
        workspace_id=candidate.candidate_id,
        # Validation answers for the retained run, not for process-local
        # proposal memory.  The candidate id is the one intent label that is
        # still exact after restart and for option/program candidates, which
        # were never made from a Proposal in the first place.
        intent=f"validate retained candidate {candidate.candidate_id}",
        delta=CandidateDelta(artifacts_add=artifacts),
        claims=(
            Claim(
                key=RUN_CLAIM_KEY,
                value=candidate.candidate_id,
                evidence_refs=(candidate.receipt_ref,),
            ),
        ),
        evidence_refs=(
            candidate.receipt_ref,
            *(artifact.artifact_id for artifact in artifacts),
        ),
    )


def review_readiness(
    receipt: ValidationReceipt,
    candidate: CandidateRun,
) -> ReviewReadiness:
    """Review readiness: five clauses, all of which must hold.

    ``relations.held`` is the flag, not the count — it says nothing was
    violated — and it is exactly why ``relations.fully_checked`` stands beside
    it. A run that checked nothing has violated nothing, and readiness built on
    the first clause alone would call that a pass.

    ``runner.exports_available`` is read from two records, not one. Every
    native artifact the candidate carries must be ``available`` with
    ``status == "succeeded"``; a registered complete model must bind the exact
    candidate state and bytes. Every seat whose run-receipt row carries a ``cad``
    block — the runner writes one only when the run was asked to export — must
    have succeeded *and* be matched by such an artifact, found by the very
    ``execution_ref`` the runner wrote into that row. A seat that exported and
    left no artifact record blocks, because an empty artifact list cannot
    otherwise be told from "nothing was ever asked to export". A run with no
    ``cad`` block anywhere and no artifacts holds the clause by construction,
    and now says so on the receipt's own evidence.
    """

    relations = candidate.relation_checks
    blocked = tuple(
        name
        for name, holds in (
            (RECEIPT_CLAUSE, receipt.passed),
            (SEATS_CLAUSE, candidate.seat_execution_complete),
            (HELD_CLAUSE, relations.held_flag),
            (CHECKED_CLAUSE, relations.fully_checked),
            (
                EXPORTS_CLAUSE,
                all(
                    _artifact_ready(record, candidate)
                    for record in candidate.artifacts
                )
                and not _unmatched_exports(candidate),
            ),
        )
        if not holds
    )
    return ReviewReadiness(review_ready=not blocked, blocked_by=blocked)


def _unmatched_exports(candidate: CandidateRun) -> tuple[SeatOutcome, ...]:
    """The seats that exported and have nothing available to show for it.

    ``cad is None`` is the runner saying this seat was never asked to export,
    and it is passed over. Everything else is a seat that was: it holds only
    if its own status is ``succeeded`` and this run retained an artifact,
    still available, for the execution it names.
    """

    return tuple(
        seat
        for seat in candidate.seat_results
        if seat.cad is not None and not _export_delivered(seat, candidate)
    )


def _artifact_ready(record: ArtifactRecord, candidate: CandidateRun) -> bool:
    if not record.available:
        return False
    if record.representation != "composed":
        return record.status == "succeeded"
    source = record.model_source
    return (
        record.status == "registered"
        and source is not None
        and source.run_id == candidate.candidate_id
        and source.state_digest == candidate.state_digest
        and source.asset_sha256 == record.sha256
    )


def _export_delivered(seat: SeatOutcome, candidate: CandidateRun) -> bool:
    """Whether one seat's export both succeeded and can still be had.

    Matched by ``execution_ref``: the runner writes the retained
    ``seat-rhino-execution`` record's own uri into the seat row, and the
    artifact listing carries that uri as ``receipt_ref``. Matching on the
    stage id would work too and would be looser — two receipts of one stage
    would answer for each other — so the exact ref is what is compared.
    """

    cad = seat.cad or {}
    if cad.get("status") != "succeeded":
        return False
    execution_ref = cad.get("execution_ref")
    return isinstance(execution_ref, str) and any(
        record.receipt_ref == execution_ref
        and record.available
        and record.status == "succeeded"
        for record in candidate.artifacts
    )


def validate_candidate(
    head: ProjectVersionRef,
    candidate: CandidateRun,
    *,
    binding: ProjectBinding,
    events: StudioEventSink,
) -> CandidateValidation:
    """Ask the kernel about one finished candidate, then assess review readiness.

    ``head`` is passed in rather than read here so that the version this
    readiness is *about* is the same one its caller keyed it under. A receipt
    names the state it checked, and a caller that remembered it under a
    different one would be able to serve an answer about a version the project
    has left.

    Source coverage follows the published references and the candidate's own
    bound record. P110's requirement-to-result projection remains incomplete:
    the normative gates still receive no obligations or commitments. Source
    descriptions therefore never expand ``effective_checks``.
    """

    published = binding.repository.load_current_state()
    if binding.head() != head:
        raise StudioError(409, "VALIDATION_HEAD_CHANGED", "The published version changed while reading validation sources; request validation again against the current version.")
    return _validate(head, candidate, binding=binding, events=events, published=published)


def validate_design_candidate(
    source_stage_ref: ProjectRecordRef,
    candidate: CandidateRun,
    *,
    binding: ProjectBinding,
    events: StudioEventSink,
) -> CandidateValidation:
    """Validate a retained design continuation against its exact historical base."""

    stage = binding.design_stage(source_stage_ref)
    source_run = binding.load_run(stage.candidate_id)
    receipt = binding.repository.load_json(stage.runner_ref)
    record_ref, _ = binding.exact_state_record(ReferenceRun(run=source_run, source="design-validation", receipt=receipt))
    if record_ref != stage.record_ref or candidate.base != source_run.base:
        raise StudioError(409, "CANDIDATE_STAGE_MISMATCH", "The candidate and its accepted source Stage do not have the same exact base.")
    published = binding.repository.load_version_state(source_run.base)
    return _validate(source_run.base, candidate, binding=binding, events=events, published=published)


def _validate(
    head: ProjectVersionRef,
    candidate: CandidateRun,
    *,
    binding: ProjectBinding,
    events: StudioEventSink,
    published: Mapping[str, Any],
) -> CandidateValidation:
    canonical_facts = _condition_sources(binding, head, candidate, published)
    artifacts, honesty = _artifacts_of(candidate)
    honesty = honesty + _exports_of(candidate)
    receipt = validate_submission(
        CanonicalState(ref=head),
        _submission(candidate, artifacts),
        tuple(validator() for validator in VALIDATORS),
    )
    readiness = review_readiness(receipt, candidate)
    events.publish(
        event={
            "type": VALIDATION_COMPUTED,
            "job_id": candidate.job_id,
            "candidate_id": candidate.candidate_id,
            "proposal_id": candidate.proposal_id,
            "run_id": candidate.candidate_id,
            "review_ready": readiness.review_ready,
            "blocked_by": list(readiness.blocked_by),
        }
    )
    return CandidateValidation(
        candidate_id=candidate.candidate_id,
        receipt=receipt,
        seat_execution_complete=candidate.seat_execution_complete,
        relation_checks=candidate.relation_checks,
        review_ready=readiness.review_ready,
        blocked_by=readiness.blocked_by,
        honesty=honesty,
        canonical_facts=canonical_facts,
    )


def _artifacts_of(
    candidate: CandidateRun,
) -> tuple[tuple[ArtifactRef, ...], tuple[str, ...]]:
    """The seats' compiled programs, and what could not be made into one.

    A seat that produced no program contributes nothing and confesses nothing:
    its row already says it was empty. A seat that *named* a program the studio
    cannot identify is different — it will not invent a digest for a record it
    cannot name, nor a name for a digest it was not given — so the program is
    left out of the submission and the seat is named in ``honesty``, with which
    of the two went wrong. If that leaves no artifact at all, the kernel
    answers ``artifact.missing``, which is the same refusal from the side of
    the boundary that owns it.
    """

    artifacts: list[ArtifactRef] = []
    honesty: list[str] = []
    for seat in candidate.seat_results:
        if seat.program_ref is None:
            continue
        sha = _record_sha(seat.program_ref)
        if sha is None:
            honesty.append(
                UNPARSED_PROGRAM_NAME.format(seat_id=seat.seat_id)
            )
            continue
        if seat.program_digest is None:
            honesty.append(
                MISSING_PROGRAM_DIGEST.format(seat_id=seat.seat_id)
            )
            continue
        artifacts.append(
            ArtifactRef(
                artifact_id=seat.program_digest,
                uri=seat.program_ref,
                media_type=PROGRAM_MEDIA_TYPE,
                sha256=sha,
            )
        )
    return tuple(artifacts), tuple(honesty)


def _exports_of(candidate: CandidateRun) -> tuple[str, ...]:
    """One honesty line for every way the exports clause can refuse.

    Two kinds, and they are different facts. An artifact record the project
    holds and cannot serve is named by the stage that made it, the file it was
    supposed to be, and what the run says happened — never
    ``readback_verified``, which the runner has already folded into ``status``.
    A seat whose receipt says an export was attempted and that no available
    artifact accounts for is named by the seat: there is no artifact row to
    describe, and that absence is the finding. A candidate that did not export
    has neither and confesses nothing.
    """

    return tuple(
        EXPORT_UNAVAILABLE.format(
            stage_id=record.stage_id if record.stage_id is not None else "-",
            file_name=record.file_name,
            status=record.status if record.status is not None else "-",
            reason=(
                record.unavailable_reason
                if record.unavailable_reason is not None
                else "-"
            ),
        )
        for record in candidate.artifacts
        if not _artifact_ready(record, candidate)
    ) + tuple(
        EXPORT_UNMATCHED.format(
            seat_id=seat.seat_id,
            status=_status_of(seat.cad),
        )
        for seat in _unmatched_exports(candidate)
    )


def _status_of(cad: Mapping[str, Any] | None) -> str:
    """What the seat row says the export did; ``-`` when it says nothing."""

    status = (cad or {}).get("status")
    return status if isinstance(status, str) else "-"


def validation_key(
    candidate_id: str, head: ProjectVersionRef
) -> tuple[str, int, str | None]:
    """What remembered review readiness is *about*: this candidate, at this issue.

    Both halves are load-bearing. The candidate is obvious. The published
    version is there because the receipt names the state it checked and
    ``passed`` depends on the submission's base matching it: readiness kept
    under the candidate id alone would go on saying ``reviewReady: true`` after the
    project issued a version that candidate is no longer based on, which is
    exactly the stale green this whole slice exists to prevent. Same issue,
    same key, so polling still dedupes; a new issue is a different question and
    gets a new answer and a new event.
    """

    return (candidate_id, head.version, head.state_sha256)


class ValidationStore:
    """One validation per candidate-and-issue, computed once in this process.

    A finished candidate's records do not change, and neither does readiness
    read off them while the project stands where it stood, so the second
    request for one is the same answer as the first. Recomputing it would be
    harmless; *republishing* it would not — a client polling the readout would
    appear on the event stream as the server deciding over and over, and an
    event log that counts readings is not a log of what happened.

    Like the proposal store and the job registry, this is memory and not
    history: it is lost on restart, and nothing may read it as the record of
    what a project decided.

    Each key gets its own lock, created under the store's mutex and dropped
    once the answer is in. Two simultaneous readers of the same key therefore
    still produce one readiness result and one event, while two readers of different
    candidates do not queue behind each other for work that has nothing to do
    with them.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._by_key: dict[tuple[str, int, str | None], CandidateValidation] = {}
        self._locks: dict[tuple[str, int, str | None], threading.Lock] = {}

    def receipt_ids(self, candidate_id: str) -> tuple[str, ...]:
        """The validation receipts this process has computed for one candidate.

        One per issue the candidate was validated against, in no particular
        order and computed nowhere here: this is a read of what was already
        decided, so a judgement can name the validation receipts that were in front of
        whoever made it.
        """

        with self._mutex:
            return tuple(
                sorted(
                    validation.receipt.receipt_id
                    for key, validation in self._by_key.items()
                    if key[0] == candidate_id
                )
            )

    def remembered(
        self,
        key: tuple[str, int, str | None],
        compute: Callable[[], CandidateValidation],
    ) -> CandidateValidation:
        """The validation and readiness for one key, computed only once."""

        with self._mutex:
            validation = self._by_key.get(key)
            if validation is not None:
                return validation
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._mutex:
                validation = self._by_key.get(key)
            if validation is not None:
                # Another reader of this key finished while this one waited.
                return validation
            validation = compute()
            with self._mutex:
                # Written and unlocked together, so a reader arriving after
                # the lock is gone always finds the answer that replaced it.
                self._by_key[key] = validation
                self._locks.pop(key, None)
        return validation


def _record_sha(uri: str | None) -> str | None:
    """The record digest in a P036 record URI, or None if it is not one.

    A seat that compiled no program names no record, and a name the P036 rule
    does not recognize is not a record this module will claim a digest for.
    Either way the submission simply carries one artifact fewer, and if that
    leaves none the kernel says ``artifact.missing`` — which is the right
    answer from the right place.
    """

    if uri is None:
        return None
    try:
        return parse_record_file_name(uri.rsplit("/", 1)[-1])[1]
    except (TypeError, ValueError):
        return None
