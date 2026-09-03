"""Exact-run rebasing for persisted monument authoring contexts.

This is project-tool support, not a framework default.  It changes only
project/run/base identity and the dependent digests that must follow that
identity; it supplies no architectural dimensions, topology, or materials.
"""

from __future__ import annotations

from dataclasses import replace

from archflow.project.refs import RunRef
from archive.archflow.runtime.production_runtime import ProductionAuthoringContext
from archflow.state.design_maturity import (
    PhaseGateRequest,
    evaluate_forward_phase_gate,
)


def _rebase_program(program, run: RunRef):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()
    return replace(
        program,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        assumptions=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.assumptions
        ),
        nodes=tuple(
            replace(item, base_state_sha256=digest) for item in program.nodes
        ),
        ranges=tuple(
            replace(item, base_state_sha256=digest) for item in program.ranges
        ),
        relationships=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.relationships
        ),
        scenarios=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.scenarios
        ),
    )


def _rebase_policy(policy, run: RunRef, program, site):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()

    def provenance(value):  # type: ignore[no-untyped-def]
        return replace(value, base_state_sha256=digest)

    def provenanced(values):  # type: ignore[no-untyped-def]
        return tuple(
            replace(item, provenance=provenance(item.provenance))
            for item in values
        )

    return replace(
        policy,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        program_digest=program.program_digest,
        site_context_digest=site.context_digest,
        policy_provenance=provenance(policy.policy_provenance),
        assumptions=tuple(
            replace(item, base_state_sha256=digest)
            for item in policy.assumptions
        ),
        availability=provenanced(policy.availability),
        demands=provenanced(policy.demands),
        protected_rules=provenanced(policy.protected_rules),
        budget_limits=provenanced(policy.budget_limits),
        staging_assumptions=provenanced(policy.staging_assumptions),
        constraints=provenanced(policy.constraints),
    )


def rebase_authoring_context(
    context: ProductionAuthoringContext,
    run: RunRef,
) -> ProductionAuthoringContext:
    """Bind one validated context to an exact target run and current base."""

    if not isinstance(context, ProductionAuthoringContext):
        raise TypeError("context must be ProductionAuthoringContext")
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    branch = replace(context.state.branch, run=run)
    state = replace(context.state, branch=branch)
    deliverables = tuple(
        replace(
            item,
            branch=branch,
            base_state_digest=state.state_digest,
        )
        for item in context.maturity.deliverables
    )
    maturity = replace(
        context.maturity,
        branch=branch,
        operational_state_digest=state.state_digest,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id=context.phase_gate.request_id,
            branch=branch,
            base_state_digest=state.state_digest,
            from_phase=context.phase_gate.from_phase,
            to_phase=context.phase_gate.to_phase,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    program = _rebase_program(context.program, run)
    site = replace(
        context.site_context,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
    )
    policy = _rebase_policy(context.build_policy, run, program, site)
    return replace(
        context,
        state=state,
        maturity=maturity,
        phase_gate=gate,
        program=program,
        site_context=site,
        build_policy=policy,
    )


__all__ = ["rebase_authoring_context"]
