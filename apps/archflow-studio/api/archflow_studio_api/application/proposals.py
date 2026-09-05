"""Where a proposal is kept between being made and being looked at again.

This is a dictionary in one process. It is **not** version history and it is
not a design record: it holds nothing after a restart, it is not written to the
project, and no issue, comparison or audit may ever read from it. The
project's own history is P036's, retained by the kernel; a chat that
accumulated its own parallel history would be a second story about the same
building, and the two would drift the moment anybody restarted anything.

So the store is deliberately small and deliberately says so: every proposal it
hands back carries the string ``PERSISTENCE`` on the wire, and the only reason
it exists at all is that ``POST /api/proposals`` must be able to answer a later
``GET`` with the same proposal rather than re-deriving one.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from typing import TYPE_CHECKING, Any, Mapping

from archflow.state.decision_operator import DecisionOperator

from ..transport.errors import StudioError
from .impact import Impact

if TYPE_CHECKING:  # the pending intent is a value, not a dependency
    from .clarification import PendingIntent

# What the DTO says about itself, verbatim. A client that shows this has been
# told the truth about what it is looking at.
PERSISTENCE = "in-memory (not version history)"


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposal: the base it was made against, and what it would do.

    Both digests are kept because they answer different questions later:
    ``base_state_digest`` is the exact base the operator refuses to run
    without, and ``record_digest`` is the content the change was read from.
    """

    proposal_id: str
    status: str
    base_state_digest: str
    record_digest: str
    component_id: str
    element_id: str | None
    target_ref: str
    key: str
    old: int | float
    new: int | float
    unit: str | None
    protected: tuple[str, ...]
    operator: DecisionOperator
    impact: Impact
    utterance: str
    created_at: str
    # The receipt of the model call that compiled the words, when a model was
    # called at all: ``ModelInvocationReceipt@2`` as a mapping, exactly as the
    # shared contract serialises it. ``None`` for a sentence already in the
    # grammar, which no model read. It travels with the proposal so that a run
    # made from it can retain what answered; nothing here reads it.
    compilation_receipt: Mapping[str, Any] | None = None
    # The pending intent this proposal came out of, when the request went
    # through ``POST /api/intents``: the same frozen value the pending-intent
    # store holds, carried by reference and never copied field by field. It is
    # here so a judgement about this proposal can say what was actually asked —
    # the request id, the slots the resolver filled, the semantic property the
    # architect named — instead of re-reading the sentence and guessing. A
    # sentence sent straight to ``POST /api/proposals`` opened no pending
    # intent and carries ``None``. Nothing in this module reads it.
    pending: "PendingIntent | None" = None
    # An explicit editing base; None keeps the project's default projection.
    source_run_id: str | None = None



def closure_of(proposal: "Proposal") -> frozenset[str]:
    """Everything this change touches, as the record's refs: the target and
    its component, the kernel's direct and propagated impact, and what the
    sentence protected. Two proposals whose closures intersect are never run
    at the same time."""

    return frozenset(
        {proposal.target_ref, f"component:{proposal.component_id}"}
        | set(proposal.impact.direct)
        | set(proposal.impact.propagated)
        | set(proposal.protected)
    )


def proposal_from(parts: Mapping[str, Any]) -> Proposal:
    """One proposal out of what the ``IntentProvider`` port returned.

    The port answers with a ``Mapping`` — it is a seam another provider could
    one day sit behind — so the mapping has to become a typed proposal
    somewhere. That happens here rather than in the route: a provider whose
    keys drift is an application-layer fault and it should raise where the
    contract is, naming the keys, instead of arriving as an unexplained 500
    from route code that only meant to call a function.
    """

    expected = {field.name for field in fields(Proposal)}
    # A field with a default is the route's to fill in afterwards, not the
    # port's to answer with: the port describes the change, and what compiled
    # the words is attached where that is known.
    required = {
        field.name
        for field in fields(Proposal)
        if field.default is MISSING and field.default_factory is MISSING
    }
    missing = sorted(required - set(parts))
    unexpected = sorted(set(parts) - expected)
    if missing or unexpected:
        raise TypeError(
            "the intent provider's proposal does not match Proposal: "
            f"missing {missing or 'nothing'}, unexpected "
            f"{unexpected or 'nothing'}"
        )
    return Proposal(**parts)


class ProposalStore:
    """An in-process dict of proposals, keyed by id, lost on restart."""

    def __init__(self) -> None:
        self._by_id: dict[str, Proposal] = {}

    def put(self, proposal: Proposal) -> Proposal:
        self._by_id[proposal.proposal_id] = proposal
        return proposal

    def for_state(self, state_digest: str) -> tuple[Proposal, ...]:
        """Every proposal this process holds against one exact base.

        The order is the order they were made in, which is what makes "the
        other options that were on the table" a list somebody can read rather
        than a set. A proposal made against another base is not on this table
        at all: it was proposed about a different building.
        """

        return tuple(
            proposal
            for proposal in self._by_id.values()
            if proposal.base_state_digest == state_digest
        )

    def get(self, proposal_id: str) -> Proposal:
        """One proposal, or a 404 that says where proposals do not survive."""

        proposal = self._by_id.get(proposal_id)
        if proposal is None:
            raise StudioError(
                404,
                "PROPOSAL_NOT_FOUND",
                f"no proposal {proposal_id} in this process. Proposals are "
                f"held {PERSISTENCE}: one made before a restart, or by another "
                "process, is gone rather than hidden.",
            )
        return proposal
