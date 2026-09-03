"""Where a proposal is kept between being made and being looked at again.

This is a dictionary in one process. It is **not** version history and it is
not a design record: it holds nothing after a restart, it is not written to the
project, and no promotion, comparison or audit may ever read from it. The
project's own history is P036's, retained by the kernel; a chat that
accumulated its own parallel history would be a second story about the same
building, and the two would drift the moment anybody restarted anything.

So the store is deliberately small and deliberately says so: every proposal it
hands back carries the string ``PERSISTENCE`` on the wire, and the only reason
it exists at all is that ``POST /api/proposals`` must be able to answer a later
``GET`` with the same proposal rather than re-deriving one.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from archflow.state.decision_operator import DecisionOperator

from ..transport.errors import StudioError
from .impact import Impact

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
    missing = sorted(expected - set(parts))
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
