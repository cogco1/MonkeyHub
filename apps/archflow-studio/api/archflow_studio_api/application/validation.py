"""What a finished candidate validates to: the kernel's receipt, and a verdict.

Two answers travel together here and they are deliberately not the same answer.

The **receipt** is the kernel's. ``validate_submission`` is called with the
three production validators and whatever it says is what this module reports —
its findings verbatim, its ``passed`` unedited. No check is repeated on this
side of the boundary, no finding is filtered, and there is no studio-owned
validator anywhere in this file. A gate re-implemented beside the kernel is a
second opinion, and two opinions about whether a design is admissible is one
too many.

The **verdict** is the server's, and it is a fixed conjunction of four named
clauses: the receipt passed, the runner finished its seats, no relation was
violated, and every declared relation was actually checked. ``blockedBy`` names
each clause that failed, by the same name every time, because "cannot advance"
without a reason is a red light nobody can act on. The fourth clause is the
point of the other three: ``held`` is true whenever nothing was violated —
including when nothing was checked — so a candidate whose relations nobody
could check is never green.

What the receipt could *not* prove is stated rather than implied. HEAD in a
P036 project is a ref-based ``CanonicalProjectState@1``: it carries no facts,
no commitments and no open obligations, so two of the three validators run over
an empty state and find nothing to object to. That is not the same as passing
them, and ``effective_checks`` and the validator note say which check the
receipt actually stands on (kernel card P110).

Nothing here writes. A validation is a reading of records the run already
retained; HEAD, ``canonical/`` and ``input/`` are untouched, and no candidate
is promoted by having been validated.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from archflow.state.model import ArtifactRef, CanonicalState
from archflow.submission.model import CandidateDelta, CandidateSubmission, Claim
from archflow.validation.engine import (
    ArtifactPresentValidator,
    AuthorizedCommitmentClaimsValidator,
    ObligationDischargeValidator,
    validate_submission,
)
from archflow.validation.model import ValidationReceipt

from ..ports import StudioEventSink
from .binding import RECORD_NAME, ProjectBinding
from .candidate import CandidateRun, RelationTotals
from .proposals import Proposal

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

# What the studio could not put into the submission, in a line the UI shows
# verbatim. A seat that named a program record the P036 rule cannot parse has
# its program dropped, and a dropped program that nobody mentioned would make
# the receipt look like it covered more than it did.
UNNAMEABLE_PROGRAM = (
    "seat {seat_id}: program record name could not be parsed; its program "
    "was not submitted for validation"
)

CANONICAL_FACTS = (
    "unavailable: HEAD is a ref-based CanonicalProjectState@1 (card P110)"
)

VALIDATOR_NOTE = (
    "obligation-discharge and authorized-commitment-claims have no facts to "
    "check on a ref-only canonical state (P110); this receipt effectively "
    "proves artifact presence and base match only"
)

# The four clauses of the advance verdict, named exactly as they travel.
RECEIPT_CLAUSE = "validation.receipt"
SEATS_CLAUSE = "runner.seat_execution_complete"
HELD_CLAUSE = "relations.held"
CHECKED_CLAUSE = "relations.fully_checked"

VALIDATION_COMPUTED = "validation.computed"


@dataclass(frozen=True, slots=True)
class Verdict:
    """The server's advance decision, and every clause that refused it."""

    advance: bool
    blocked_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateValidation:
    """One candidate's receipt and verdict, as the route hands them on."""

    candidate_id: str
    receipt: ValidationReceipt
    seat_execution_complete: bool
    # Copied from the candidate, never recomputed: the readout and the verdict
    # answer for the same three states or they are two different candidates.
    relation_checks: RelationTotals
    advance: bool
    blocked_by: tuple[str, ...]
    # What the submission could not carry. Normally empty; never absent.
    honesty: tuple[str, ...]


def submission_for(
    candidate: CandidateRun, proposal: Proposal
) -> CandidateSubmission:
    """The candidate as something the kernel can be asked about.

    Everything in it came off the run's own records. The artifacts are the
    seats' compiled geometry programs — identified by the program digest,
    located by the record the runner retained, and digested by that record's
    own sha — and each artifact id is repeated in ``evidence_refs`` because
    ``ArtifactPresentValidator`` reports ``artifact.evidence_missing`` for an
    added artifact that is not also evidence.

    The base is the candidate's own, never HEAD. A candidate stood on the
    version it was created against; if the project has moved since, that is a
    fact the kernel states as ``state.base_mismatch``, and re-basing the
    submission to make it agree would be the studio answering a question it was
    asked to pose.
    """

    return _submission(candidate, proposal, _artifacts_of(candidate)[0])


def _submission(
    candidate: CandidateRun,
    proposal: Proposal,
    artifacts: tuple[ArtifactRef, ...],
) -> CandidateSubmission:
    return CandidateSubmission(
        submission_id=candidate.candidate_id,
        base=candidate.base,
        workspace_id=candidate.candidate_id,
        intent=proposal.utterance,
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


def verdict(receipt: ValidationReceipt, candidate: CandidateRun) -> Verdict:
    """The advance decision: four clauses, all of which must hold.

    ``relations.held`` is the flag, not the count — it says nothing was
    violated — and it is exactly why ``relations.fully_checked`` stands beside
    it. A run that checked nothing has violated nothing, and a verdict built on
    the first clause alone would call that a pass.
    """

    relations = candidate.relation_checks
    blocked = tuple(
        name
        for name, holds in (
            (RECEIPT_CLAUSE, receipt.passed),
            (SEATS_CLAUSE, candidate.seat_execution_complete),
            (HELD_CLAUSE, relations.held_flag),
            (CHECKED_CLAUSE, relations.fully_checked),
        )
        if not holds
    )
    return Verdict(advance=not blocked, blocked_by=blocked)


def validate_candidate(
    binding: ProjectBinding,
    candidate: CandidateRun,
    proposal: Proposal,
    *,
    events: StudioEventSink,
) -> CandidateValidation:
    """Ask the kernel about one finished candidate, then issue the verdict.

    The state the submission is checked against is the project's HEAD as a
    ``CanonicalState`` carrying nothing but its ref: the canonical document a
    P036 project holds is not a facts-and-commitments state, and inventing
    facts to fill it would be inventing the very things the validators check.
    An empty state is honest and it is why ``effective_checks`` is narrower
    than ``validators``.
    """

    artifacts, honesty = _artifacts_of(candidate)
    receipt = validate_submission(
        CanonicalState(ref=binding.head()),
        _submission(candidate, proposal, artifacts),
        tuple(validator() for validator in VALIDATORS),
    )
    decision = verdict(receipt, candidate)
    events.publish(
        event={
            "type": VALIDATION_COMPUTED,
            "job_id": candidate.job_id,
            "candidate_id": candidate.candidate_id,
            "proposal_id": candidate.proposal_id,
            "run_id": candidate.candidate_id,
            "advance": decision.advance,
            "blocked_by": list(decision.blocked_by),
        }
    )
    return CandidateValidation(
        candidate_id=candidate.candidate_id,
        receipt=receipt,
        seat_execution_complete=candidate.seat_execution_complete,
        relation_checks=candidate.relation_checks,
        advance=decision.advance,
        blocked_by=decision.blocked_by,
        honesty=honesty,
    )


def _artifacts_of(
    candidate: CandidateRun,
) -> tuple[tuple[ArtifactRef, ...], tuple[str, ...]]:
    """The seats' compiled programs, and what could not be made into one.

    A seat that produced no program contributes nothing and confesses nothing:
    its row already says it was empty. A seat that *named* a program record
    whose name the P036 rule does not recognize is different — the studio will
    not invent a digest for a record it cannot name, so the program is left out
    of the submission and the seat is named in ``honesty``. If that leaves no
    artifact at all, the kernel answers ``artifact.missing``, which is the same
    refusal from the side of the boundary that owns it.
    """

    artifacts: list[ArtifactRef] = []
    honesty: list[str] = []
    for seat in candidate.seat_results:
        if seat.program_ref is None:
            continue
        sha = _record_sha(seat.program_ref)
        if sha is None or seat.program_digest is None:
            honesty.append(UNNAMEABLE_PROGRAM.format(seat_id=seat.seat_id))
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


class ValidationStore:
    """One validation per candidate, computed once and kept in this process.

    A finished candidate's records do not change, and neither does the verdict
    read off them, so the second request for one is the same answer as the
    first. Recomputing it would be harmless; *republishing* it would not — a
    client polling the readout would appear on the event stream as the server
    deciding over and over, and an event log that counts readings is not a log
    of what happened.

    Like the proposal store and the job registry, this is memory and not
    history: it holds one entry per candidate this process validated, it is
    lost on restart, and nothing may read it as the record of what a project
    decided. The compute runs under the lock so two simultaneous readers of the
    same candidate produce one verdict and one event rather than two of each.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_candidate: dict[str, CandidateValidation] = {}

    def remembered(
        self, candidate_id: str, compute: Callable[[], CandidateValidation]
    ) -> CandidateValidation:
        """The candidate's validation, computing it the first time only."""

        with self._lock:
            validation = self._by_candidate.get(candidate_id)
            if validation is None:
                validation = compute()
                self._by_candidate[candidate_id] = validation
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
    match = RECORD_NAME.match(uri.rsplit("/", 1)[-1])
    return None if match is None else match.group("sha")
