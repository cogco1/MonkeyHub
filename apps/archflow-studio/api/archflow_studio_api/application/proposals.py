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

from dataclasses import MISSING, dataclass, fields, replace
from typing import TYPE_CHECKING, Any, Mapping

from archflow.state.decision_operator import DecisionOperator
from archflow.state.state_record import (
    StateRecord, StateRecordEditKind, StateRecordError, StateRecordOperator,
    apply_state_record_operator,
)
from archflow.project.refs import ProjectRecordRef

from ..transport.errors import StudioError
from .impact import Impact

if TYPE_CHECKING:  # the pending intent is a value, not a dependency
    from .clarification import PendingIntent
    from .artifacts import ModelSource
    from .projection import StateProjection

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
    key: str | None
    old: int | float | None
    new: int | float | None
    unit: str | None
    protected: tuple[str, ...]
    operator: DecisionOperator | None
    impact: Impact
    utterance: str
    created_at: str
    # The receipt of the model call that compiled the words, when a model was
    # called at all: ``ModelInvocationReceipt`` as a mapping, exactly as the
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
    source_stage_ref: ProjectRecordRef | None = None
    # Structured component edits carry the same kernel operator the worker
    # replays. The review is domain data, never a CAD program or a second run.
    state_record_operator: StateRecordOperator | None = None
    semantic_edit: Mapping[str, Any] | None = None
    # Exact saved source/page ink and submitted words, read again on candidate execution.
    document_comment_ref: ProjectRecordRef | None = None
    model_source: "ModelSource | None" = None



def operator_of(proposal: Proposal, record: StateRecord) -> StateRecordOperator:
    """The existing scalar or component operator, at its unchanged exact base."""

    return proposal.state_record_operator or StateRecordOperator(
        kind=StateRecordEditKind.SET_SCALAR,
        base_record_digest=proposal.record_digest,
        base_state_digest=record.state_digest,
        protected=tuple(sorted(set(proposal.protected))),
        target_ref=proposal.target_ref, key=proposal.key, value=proposal.new,
    )


def continue_proposal(
    base: "StateProjection", previous: Proposal, proposal: Proposal,
) -> Proposal:
    """Fold another checked edit into one proposal on the first exact base.

    The store still contains only ordinary, immutable proposal values. The
    intermediate record is calculated here and discarded; only the existing
    candidate endpoint can create a run or export geometry.
    """

    from .intent import component_edit_proposal

    if proposal.status == "conflict":
        raise StudioError(409, "PROPOSAL_CHAIN_CONFLICT", "The next edit reaches protected refs: " + ", ".join(proposal.impact.conflicts))
    prior_operator = operator_of(previous, base.record)
    if (prior_operator.kind is StateRecordEditKind.SET_PARAMETER_LOCKS
            or (proposal.state_record_operator is not None
                and proposal.state_record_operator.kind is StateRecordEditKind.SET_PARAMETER_LOCKS)):
        raise StudioError(409, "LOCK_PROPOSAL_REQUIRES_CANDIDATE", "Run the parameter lock proposal as a candidate, then continue from its exact retained state.")
    prior_record = apply_state_record_operator(base.record, prior_operator)
    try:
        # Earlier keep conditions apply to the state in which they were made,
        # including a form first drawn during this chain.
        successor = apply_state_record_operator(
            prior_record,
            replace(operator_of(proposal, prior_record), protected=previous.protected),
        )
    except StateRecordError as exc:
        raise StudioError(409, "PROPOSAL_CHAIN_CONFLICT", str(exc)) from exc

    edit: dict[str, Any] = {}
    for name, identity, removal in (
        ("entities", "entity_id", "removeEntityIds"),
        ("parameters", "key", "removeParameterKeys"),
        ("relations", "relation_id", "removeRelationIds"),
    ):
        before = {getattr(item, identity): item for item in getattr(base.record, name)}
        after = {getattr(item, identity): item for item in getattr(successor, name)}
        # These are authorable fields. Existing lineage/locks are retained by
        # the component compiler, not submitted as fresh authored authority.
        edit[name] = [
            {field: value for field, value in item.to_dict().items()
             if field not in {"lineage", "lock_authority"}}
            for key, item in after.items() if before.get(key) != item
        ]
        edit[removal] = sorted(before.keys() - after.keys())
    if not any(edit.values()):
        raise StudioError(422, "PROPOSAL_CHAIN_NO_CHANGE", "The editing chain returns to its starting state; no checkpoint is needed.")

    # A keep introduced after a change protects that intermediate value, not
    # the older value on the retained base. It was enforced above, and travels
    # in Proposal.protected for subsequent edits. Only protections already
    # true on the first base belong on the final operator replayed there.
    earlier_changes = set(previous.impact.direct) | set(previous.impact.propagated)
    root_protected = set(prior_operator.protected) | (set(proposal.protected) - earlier_changes)
    protected = tuple(sorted(set(previous.protected) | set(proposal.protected)))
    kept = list(dict.fromkeys(
        list((previous.semantic_edit or {}).get("kept", ()))
        + list((proposal.semantic_edit or {}).get("kept", ()))
    ))
    edit.update(summary=proposal.utterance, protected=sorted(root_protected), kept=kept)
    combined = proposal_from(component_edit_proposal(
        base, edit, utterance=proposal.utterance, component_id=proposal.component_id,
    ))
    return replace(
        combined, source_run_id=previous.source_run_id,
        source_stage_ref=previous.source_stage_ref, model_source=previous.model_source,
        compilation_receipt=previous.compilation_receipt,
        document_comment_ref=previous.document_comment_ref, pending=previous.pending,
        # The cumulative keep list also governs later proposal continuations;
        # the final operator and its impact remain relative to the first base.
        protected=protected,
    )


def read_refs_of(proposal: "Proposal") -> frozenset[str]:
    """The protected design inputs this proposal must preserve."""

    return frozenset(proposal.protected) | frozenset(proposal.impact.protected)


def write_refs_of(proposal: "Proposal") -> frozenset[str]:
    """The target and the kernel's direct and propagated changes.

    An element edit does not write its entire containing component, and a
    protected input is not a write merely because it was read.
    """

    return frozenset(
        {proposal.target_ref}
        | set(proposal.impact.direct)
        | set(proposal.impact.propagated)
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
