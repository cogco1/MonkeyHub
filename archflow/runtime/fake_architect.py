"""A deterministic stand-in for the future open-ended Architect Agent."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.adapters import FakeVoxelAdapter
from archflow.state import CanonicalState
from archflow.submission import CandidateDelta, CandidateSubmission, Claim
from archflow.workspace import WorkspaceRef


@dataclass(slots=True)
class FakeArchitect:
    """Compatibility-only deterministic actor for boundary tests."""

    adapter: FakeVoxelAdapter
    omit_claims: frozenset[str] = frozenset()

    def propose(
        self, state: CanonicalState, workspace: WorkspaceRef
    ) -> CandidateSubmission:
        if workspace.base != state.ref:
            raise ValueError("workspace was forked from another canonical state")
        if state.goal is None:
            raise ValueError("FakeArchitect requires compatibility GoalContract state")
        artifact = self.adapter.build(state, workspace)
        claims = tuple(
            Claim(
                key=required,
                value="satisfied by fake artifact",
                evidence_refs=(artifact.artifact_id,),
            )
            for required in state.goal.must
            if required not in self.omit_claims
        )
        claimed = {claim.key for claim in claims}
        discharged = tuple(
            item.obligation_id
            for item in state.open_obligations
            if item.statement in claimed
        )
        unresolved = tuple(
            f"missing claim: {required}"
            for required in state.goal.must
            if required not in claimed
        )
        return CandidateSubmission(
            submission_id=f"submission-{workspace.workspace_id}",
            base=state.ref,
            workspace_id=workspace.workspace_id,
            intent="Produce one loadable fake voxel building artifact.",
            delta=CandidateDelta(
                obligations_discharge=discharged,
                artifacts_add=(artifact,),
            ),
            claims=claims,
            evidence_refs=(artifact.artifact_id,),
            unresolved=unresolved,
        )
