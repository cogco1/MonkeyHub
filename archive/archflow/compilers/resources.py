"""Compile explicit resource policy into BuildPolicy@1 without a palette."""

from __future__ import annotations

from dataclasses import dataclass

from archive.archflow.state.build_policy import (
    BuildAssumption,
    BuildBudgetLimit,
    BuildPolicy,
    BuildStagingMode,
    ConstructabilityConstraint,
    ConstructabilityTopic,
    PolicyConstraintStrength,
    PolicyProvenance,
    ProtectedBlockAction,
    ProtectedBlockRule,
    ResourceAvailability,
    ResourceDemand,
    ResourcePolicyMode,
    StagingAssumption,
)
from archive.archflow.state.design_brief import DesignBrief
from archive.archflow.state.design_program import DesignProgram
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    require_local_id,
    require_logical_ref,
)
from archflow.state.site_context import SiteContext
from archflow.contracts.canonical import canonical_digest


_COMPILER_ID = "archflow.resource-constructability-compiler"
_COMPILER_VERSION = "1"
_MAX_ITEMS = 1_024


class ResourceCompilationError(ValueError):
    """Resource evidence or policy does not match the current design state."""


@dataclass(frozen=True, slots=True)
class BuildAssumptionProposal:
    assumption_id: str
    statement: str
    authority_id: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceAvailabilityProposal:
    resource_ref: str
    minimum_available: float | None
    maximum_available: float | None
    unit: str
    epistemic_status: FactEpistemicStatus
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceDemandProposal:
    demand_id: str
    resource_ref: str
    minimum_required: float
    maximum_required: float
    unit: str
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProtectedBlockRuleProposal:
    rule_id: str
    target_ref: str
    action: ProtectedBlockAction
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildBudgetProposal:
    budget_id: str
    metric: str
    maximum: float
    unit: str
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StagingAssumptionProposal:
    stage_id: str
    statement: str
    predecessor_ids: tuple[str, ...]
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConstructabilityConstraintProposal:
    constraint_id: str
    topic: ConstructabilityTopic
    strength: PolicyConstraintStrength
    statement: str
    subject_refs: tuple[str, ...]
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildPolicyProposal:
    resource_mode: ResourcePolicyMode
    staging_mode: BuildStagingMode
    disposable_sandbox: bool
    unbounded_resources: bool
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    assumptions: tuple[BuildAssumptionProposal, ...] = ()
    availability: tuple[ResourceAvailabilityProposal, ...] = ()
    demands: tuple[ResourceDemandProposal, ...] = ()
    protected_rules: tuple[ProtectedBlockRuleProposal, ...] = ()
    budget_limits: tuple[BuildBudgetProposal, ...] = ()
    staging_assumptions: tuple[StagingAssumptionProposal, ...] = ()
    constraints: tuple[ConstructabilityConstraintProposal, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.resource_mode, ResourcePolicyMode):
            raise TypeError(
                "resource_mode must be ResourcePolicyMode"
            )
        if not isinstance(self.staging_mode, BuildStagingMode):
            raise TypeError("staging_mode must be BuildStagingMode")
        if not isinstance(self.disposable_sandbox, bool):
            raise TypeError("disposable_sandbox must be boolean")
        if not isinstance(self.unbounded_resources, bool):
            raise TypeError("unbounded_resources must be boolean")
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")
        _ids(self.assumption_ids, "assumption_ids", allow_empty=True)
        _refs(self.evidence_refs, "evidence_refs", allow_empty=True)
        _typed(
            self.assumptions,
            BuildAssumptionProposal,
            "assumptions",
        )
        _typed(
            self.availability,
            ResourceAvailabilityProposal,
            "availability",
        )
        _typed(self.demands, ResourceDemandProposal, "demands")
        _typed(
            self.protected_rules,
            ProtectedBlockRuleProposal,
            "protected_rules",
        )
        _typed(
            self.budget_limits,
            BuildBudgetProposal,
            "budget_limits",
        )
        _typed(
            self.staging_assumptions,
            StagingAssumptionProposal,
            "staging_assumptions",
        )
        _typed(
            self.constraints,
            ConstructabilityConstraintProposal,
            "constraints",
        )


@dataclass(frozen=True, slots=True)
class ResourceCompilationReceipt:
    compilation_id: str
    project_id: str
    run_id: str
    base_state_sha256: str
    program_digest: str
    site_context_digest: str
    policy_digest: str
    resource_mode: str
    staging_mode: str
    open_obligation_ids: tuple[str, ...]

    SCHEMA = "ResourceCompilationReceipt@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "compilation_id": self.compilation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "program_digest": self.program_digest,
            "site_context_digest": self.site_context_digest,
            "policy_digest": self.policy_digest,
            "resource_mode": self.resource_mode,
            "staging_mode": self.staging_mode,
            "open_obligation_ids": list(self.open_obligation_ids),
            "generation_authority": False,
            "palette_selected": False,
        }


@dataclass(frozen=True, slots=True)
class CompiledBuildPolicy:
    policy: BuildPolicy
    receipt: ResourceCompilationReceipt


def compile_build_policy(
    *,
    brief: DesignBrief,
    program: DesignProgram,
    site_context: SiteContext,
    proposal: BuildPolicyProposal,
    compiler_id: str = _COMPILER_ID,
    compiler_version: str = _COMPILER_VERSION,
) -> CompiledBuildPolicy:
    """Compile explicit policy and evidence without selecting design material."""

    if not isinstance(brief, DesignBrief):
        raise TypeError("brief must be DesignBrief")
    if not isinstance(program, DesignProgram):
        raise TypeError("program must be DesignProgram")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    if not isinstance(proposal, BuildPolicyProposal):
        raise TypeError("proposal must be BuildPolicyProposal")
    _text(compiler_id, "compiler_id")
    _text(compiler_version, "compiler_version")
    if (
        {brief.project_id, program.project_id, site_context.project_id}
        != {brief.project_id}
        or {brief.run_id, program.run_id, site_context.run_id}
        != {brief.run_id}
        or brief.base != program.base
        or brief.base != site_context.base
    ):
        raise ResourceCompilationError(
            "brief, program, and site context are not exact-base aligned"
        )
    if program.brief_digest != brief.brief_digest:
        raise ResourceCompilationError(
            "program was compiled from another brief"
        )

    base_digest = brief.base.require_digest()
    evidence_refs = tuple(
        sorted(
            {
                *brief.evidence_refs,
                *program.evidence_refs,
                *site_context.evidence_refs,
                *proposal.evidence_refs,
                *proposal.source_refs,
                *(
                    ref
                    for item in _proposal_items(proposal)
                    for ref in item.source_refs
                ),
                f"design-program:{program.program_digest}",
                f"site-context:{site_context.context_digest}",
            }
        )
    )
    for ref in evidence_refs:
        if ref.startswith("project://") and not ref.startswith(
            f"project://{brief.project_id}/"
        ):
            raise ResourceCompilationError(
                "resource evidence belongs to another project"
            )
    evidence = set(evidence_refs)
    for item in (proposal, *_proposal_items(proposal)):
        if not set(item.source_refs) <= evidence:
            raise ResourceCompilationError(
                "policy item source is absent from evidence"
            )

    _unique(
        tuple(item.assumption_id for item in proposal.assumptions),
        "assumption ids",
    )
    assumption_ids = {
        item.assumption_id for item in proposal.assumptions
    }
    for item in (proposal, *_proposal_items(proposal)):
        if not set(getattr(item, "assumption_ids", ())) <= assumption_ids:
            raise ResourceCompilationError(
                "policy item cites an unknown assumption"
            )

    assumptions = tuple(
        BuildAssumption(
            assumption_id=item.assumption_id,
            statement=item.statement,
            authority_id=item.authority_id,
            source_refs=item.source_refs,
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposal.assumptions,
            key=lambda value: value.assumption_id,
        )
    )

    def provenance(item) -> PolicyProvenance:
        return PolicyProvenance(
            authority_id=item.authority_id,
            source_refs=item.source_refs,
            assumption_refs=tuple(
                f"build-assumption:{value}"
                for value in item.assumption_ids
            ),
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )

    availability = tuple(
        ResourceAvailability(
            resource_ref=item.resource_ref,
            minimum_available=item.minimum_available,
            maximum_available=item.maximum_available,
            unit=item.unit,
            epistemic_status=item.epistemic_status,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.availability,
            key=lambda value: value.resource_ref,
        )
    )
    demands = tuple(
        ResourceDemand(
            demand_id=item.demand_id,
            resource_ref=item.resource_ref,
            minimum_required=item.minimum_required,
            maximum_required=item.maximum_required,
            unit=item.unit,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.demands,
            key=lambda value: value.demand_id,
        )
    )
    protected_rules = [
        ProtectedBlockRule(
            rule_id=item.rule_id,
            target_ref=item.target_ref,
            action=item.action,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.protected_rules,
            key=lambda value: value.rule_id,
        )
    ]
    if site_context.protected_cells:
        site_sources = (
            site_context.protection_source_refs
            or (f"site-context:{site_context.context_digest}",)
        )
        protected_rules.append(
            ProtectedBlockRule(
                rule_id="site-protected-cells",
                target_ref=(
                    f"site-context:{site_context.context_digest}"
                    "#protected-cells"
                ),
                action=ProtectedBlockAction.DO_NOT_REPLACE,
                provenance=PolicyProvenance(
                    authority_id=site_context.authority_id,
                    source_refs=site_sources,
                    assumption_refs=(),
                    compiler_id=compiler_id,
                    base_state_sha256=base_digest,
                ),
            )
        )
    protected_rules_tuple = tuple(
        sorted(protected_rules, key=lambda item: item.rule_id)
    )
    budgets = tuple(
        BuildBudgetLimit(
            budget_id=item.budget_id,
            metric=item.metric,
            maximum=item.maximum,
            unit=item.unit,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.budget_limits,
            key=lambda value: value.budget_id,
        )
    )
    stages = tuple(
        StagingAssumption(
            stage_id=item.stage_id,
            statement=item.statement,
            predecessor_ids=item.predecessor_ids,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.staging_assumptions,
            key=lambda value: value.stage_id,
        )
    )
    constraints = tuple(
        ConstructabilityConstraint(
            constraint_id=item.constraint_id,
            topic=item.topic,
            strength=item.strength,
            statement=item.statement,
            subject_refs=item.subject_refs,
            provenance=provenance(item),
        )
        for item in sorted(
            proposal.constraints,
            key=lambda value: value.constraint_id,
        )
    )
    obligations = _compile_obligations(
        proposal=proposal,
        availability=availability,
        demands=demands,
    )
    policy = BuildPolicy(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base=brief.base,
        brief_digest=brief.brief_digest,
        program_digest=program.program_digest,
        site_context_digest=site_context.context_digest,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        resource_mode=proposal.resource_mode,
        staging_mode=proposal.staging_mode,
        disposable_sandbox=proposal.disposable_sandbox,
        unbounded_resources=proposal.unbounded_resources,
        policy_provenance=provenance(proposal),
        assumptions=assumptions,
        availability=availability,
        demands=demands,
        protected_rules=protected_rules_tuple,
        budget_limits=budgets,
        staging_assumptions=stages,
        constraints=constraints,
        obligations=obligations,
        evidence_refs=evidence_refs,
    )
    compilation_id = canonical_digest(
        {
            "project_id": brief.project_id,
            "run_id": brief.run_id,
            "base_state_sha256": base_digest,
            "program_digest": program.program_digest,
            "site_context_digest": site_context.context_digest,
            "policy_digest": policy.policy_digest,
        }
    )[:24]
    return CompiledBuildPolicy(
        policy=policy,
        receipt=ResourceCompilationReceipt(
            compilation_id=f"resource-compilation.{compilation_id}",
            project_id=brief.project_id,
            run_id=brief.run_id,
            base_state_sha256=base_digest,
            program_digest=program.program_digest,
            site_context_digest=site_context.context_digest,
            policy_digest=policy.policy_digest,
            resource_mode=policy.resource_mode.value,
            staging_mode=policy.staging_mode.value,
            open_obligation_ids=tuple(
                item.obligation_id for item in obligations
            ),
        ),
    )


def _compile_obligations(
    *,
    proposal: BuildPolicyProposal,
    availability: tuple[ResourceAvailability, ...],
    demands: tuple[ResourceDemand, ...],
) -> tuple[DesignObligation, ...]:
    source_ref = (
        "build-policy-proposal:"
        + canonical_digest(
            {
                "resource_mode": proposal.resource_mode.value,
                "staging_mode": proposal.staging_mode.value,
                "authority_id": proposal.authority_id,
                "source_refs": proposal.source_refs,
            }
        )
    )
    obligations: dict[str, DesignObligation] = {}

    if proposal.resource_mode is ResourcePolicyMode.UNKNOWN:
        _add(
            obligations,
            "resolve.build-policy.resource-mode",
            "Authorize one explicit build and resource policy.",
            source_ref,
        )
    if proposal.unbounded_resources and (
        proposal.resource_mode is not ResourcePolicyMode.CREATIVE
        or not proposal.disposable_sandbox
    ):
        raise ResourceCompilationError(
            "unbounded resources require explicit creative disposable sandbox"
        )
    explicitly_unbounded = (
        proposal.resource_mode is ResourcePolicyMode.CREATIVE
        and proposal.disposable_sandbox
        and proposal.unbounded_resources
    )
    if not explicitly_unbounded and not availability:
        _add(
            obligations,
            "resolve.resource.availability",
            "Obtain bounded resource availability or preserve it as unknown.",
            source_ref,
        )
    if (
        proposal.staging_mode is BuildStagingMode.STAGED
        and not proposal.staging_assumptions
    ):
        _add(
            obligations,
            "resolve.build-policy.staging",
            "Provide evidence-backed staging assumptions without selecting geometry.",
            source_ref,
        )
    if proposal.staging_mode is BuildStagingMode.UNKNOWN:
        _add(
            obligations,
            "resolve.build-policy.staging-mode",
            "Authorize single-pass or staged construction without inferring either.",
            source_ref,
        )

    availability_by_ref = {
        item.resource_ref: item for item in availability
    }
    for demand in demands:
        if explicitly_unbounded:
            continue
        available = availability_by_ref.get(demand.resource_ref)
        suffix = demand.demand_id
        if available is None or available.epistemic_status is FactEpistemicStatus.UNKNOWN:
            _add(
                obligations,
                f"resolve.resource.unknown.{suffix}",
                "Resolve availability for the named resource demand.",
                source_ref,
            )
        elif available.unit != demand.unit:
            _add(
                obligations,
                f"resolve.resource.unit.{suffix}",
                "Reconcile resource availability and demand units.",
                source_ref,
            )
        elif available.maximum_available < demand.minimum_required:
            _add(
                obligations,
                f"resolve.resource.shortage.{suffix}",
                "Resolve the evidenced resource shortage without selecting a substitute palette.",
                source_ref,
            )
        elif available.minimum_available < demand.maximum_required:
            _add(
                obligations,
                f"verify.resource.sufficiency.{suffix}",
                "Verify the overlapping supply and demand ranges before committing an alternative.",
                source_ref,
            )
    return tuple(obligations[key] for key in sorted(obligations))


def _add(
    obligations: dict[str, DesignObligation],
    obligation_id: str,
    statement: str,
    source_ref: str,
) -> None:
    obligations.setdefault(
        obligation_id,
        DesignObligation(
            obligation_id=obligation_id,
            statement=statement,
            source_ref=source_ref,
        ),
    )


def _proposal_items(proposal: BuildPolicyProposal) -> tuple[object, ...]:
    return (
        *proposal.assumptions,
        *proposal.availability,
        *proposal.demands,
        *proposal.protected_rules,
        *proposal.budget_limits,
        *proposal.staging_assumptions,
        *proposal.constraints,
    )


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        require_logical_ref(item, field)
    _unique(tuple(value), field)


def _ids(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        require_local_id(item, field)
    _unique(tuple(value), field)


def _typed(value: object, item_type: type, field: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) > _MAX_ITEMS
        or any(not isinstance(item, item_type) for item in value)
    ):
        raise TypeError(f"{field} contains the wrong item type")


def _unique(values: tuple[object, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


__all__ = [
    "ResourceCompilationError",
    "BuildAssumptionProposal",
    "ResourceAvailabilityProposal",
    "ResourceDemandProposal",
    "ProtectedBlockRuleProposal",
    "BuildBudgetProposal",
    "StagingAssumptionProposal",
    "ConstructabilityConstraintProposal",
    "BuildPolicyProposal",
    "ResourceCompilationReceipt",
    "CompiledBuildPolicy",
    "compile_build_policy",
]

