"""Read-only terrain experts discovered from the current operational state."""

from __future__ import annotations

from archive.archflow.capabilities.experts import (
    ExpertEvidence,
    ExpertObligation,
    ExpertSnapshot,
)
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.model import StateRef
from archflow.state.site_context import GroundModelKind, SiteContext


class TerrainCapabilityError(ValueError):
    """Terrain expert discovery received stale or mismatched state."""


def build_terrain_expert_snapshot(
    state: OperationalMarkovState,
    site_context: SiteContext,
) -> ExpertSnapshot:
    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    if (
        state.branch.run.project_id != site_context.project_id
        or state.branch.run.run_id != site_context.run_id
        or state.canonical_base != site_context.base
        or state.value_for_ref("fact:parameter:site-context-digest")
        != site_context.context_digest
    ):
        raise TerrainCapabilityError("terrain expert snapshot is stale")
    if site_context.ground_model.kind is not GroundModelKind.UNEVEN:
        raise TerrainCapabilityError("terrain experts require uneven-ground evidence")
    obligations = tuple(
        ExpertObligation(
            obligation_id=item.obligation_id,
            topic="terrain",
            statement=item.statement,
            source_ref=item.source_ref,
        )
        for item in state.obligations
        if item.obligation_id.startswith("resolve.site.")
    )
    if not obligations:
        raise TerrainCapabilityError("terrain snapshot has no site obligation")
    elevation_range = site_context.ground_model.elevation_range
    summary = (
        "Observed uneven ground"
        if elevation_range is None
        else f"Observed uneven ground elevation range {elevation_range[0]} to {elevation_range[1]}."
    )
    return ExpertSnapshot(
        base_state=StateRef(
            site_context.project_id,
            site_context.base.version,
            site_context.base.require_digest(),
        ),
        program_json=None,
        obligations=obligations,
        evidence=(
            ExpertEvidence(
                kind="site_context",
                evidence_ref=f"site-context:{site_context.context_digest}",
                summary=summary,
            ),
        ),
    )
