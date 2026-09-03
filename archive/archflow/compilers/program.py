"""Compile evidence-backed program proposals into DesignProgram@1.

The compiler preserves proposals from a model, retrieval tool, or expert.  It
does not map a building label to functions, areas, topology, or form.
"""

from __future__ import annotations

from dataclasses import dataclass

from archive.archflow.state.design_brief import (
    BriefConstraintOperator,
    BriefSlot,
    BriefSlotStatus,
    DesignBrief,
)
from archive.archflow.state.design_program import (
    DesignProgram,
    ProgramAssumption,
    ProgramMetricApplicability,
    ProgramMetricApplicabilityDecision,
    ProgramMetricKind,
    ProgramNode,
    ProgramNodeKind,
    ProgramRange,
    ProgramRelationship,
    ProgramRelationshipKind,
    ProgramRelationshipStrength,
    ProgramScenario,
)
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    ObligationStatus,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest


_COMPILER_ID = "archflow.program-compiler"
_COMPILER_VERSION = "1"
_MAX_ITEMS = 512
_MAX_TEXT = 1_000


class ProgramCompilationError(ValueError):
    """A proposal cannot be proven against the current brief."""


@dataclass(frozen=True, slots=True)
class ProgramAssumptionProposal:
    assumption_id: str
    statement: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.assumption_id, "assumption_id")
        _text(self.statement, "statement")
        _refs(self.source_refs, "source_refs")


@dataclass(frozen=True, slots=True)
class ProgramNodeProposal:
    node_id: str
    kind: ProgramNodeKind
    label: str
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_local_id(self.node_id, "node_id")
        if not isinstance(self.kind, ProgramNodeKind):
            raise TypeError("kind must be ProgramNodeKind")
        _text(self.label, "label")
        _status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _ids(self.assumption_ids, "assumption_ids", allow_empty=True)


@dataclass(frozen=True, slots=True)
class ProgramRangeProposal:
    range_id: str
    metric: ProgramMetricKind
    applies_to_node_id: str | None
    minimum: float
    maximum: float
    unit: str
    scenario_id: str | None
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.range_id, "range_id")
        if not isinstance(self.metric, ProgramMetricKind):
            raise TypeError("metric must be ProgramMetricKind")
        if self.applies_to_node_id is not None:
            require_local_id(
                self.applies_to_node_id,
                "applies_to_node_id",
            )
        if self.scenario_id is not None:
            require_local_id(self.scenario_id, "scenario_id")
        _text(self.unit, "unit")
        _status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _ids(self.assumption_ids, "assumption_ids")
        # ProgramRange performs the numeric and non-point validation.


@dataclass(frozen=True, slots=True)
class ProgramRelationshipProposal:
    relationship_id: str
    kind: ProgramRelationshipKind
    source_node_id: str
    target_node_id: str
    strength: ProgramRelationshipStrength
    directed: bool
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_local_id(self.relationship_id, "relationship_id")
        if not isinstance(self.kind, ProgramRelationshipKind):
            raise TypeError("kind must be ProgramRelationshipKind")
        require_local_id(self.source_node_id, "source_node_id")
        require_local_id(self.target_node_id, "target_node_id")
        if not isinstance(self.strength, ProgramRelationshipStrength):
            raise TypeError(
                "strength must be ProgramRelationshipStrength"
            )
        if not isinstance(self.directed, bool):
            raise TypeError("directed must be boolean")
        _status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _ids(self.assumption_ids, "assumption_ids", allow_empty=True)


@dataclass(frozen=True, slots=True)
class ProgramScenarioProposal:
    scenario_id: str
    label: str
    range_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.scenario_id, "scenario_id")
        _text(self.label, "label")
        _ids(self.range_ids, "range_ids")
        _refs(self.source_refs, "source_refs")
        _ids(self.assumption_ids, "assumption_ids")


@dataclass(frozen=True, slots=True)
class ProgramMetricApplicabilityBinding:
    """Bind a typed applicability decision to its exact P036 record."""

    decision: ProgramMetricApplicabilityDecision
    decision_ref: str

    def __post_init__(self) -> None:
        if not isinstance(
            self.decision,
            ProgramMetricApplicabilityDecision,
        ):
            raise TypeError(
                "decision must be ProgramMetricApplicabilityDecision"
            )
        require_logical_ref(self.decision_ref, "decision_ref")


@dataclass(frozen=True, slots=True)
class ProgramProposalBundle:
    assumptions: tuple[ProgramAssumptionProposal, ...] = ()
    nodes: tuple[ProgramNodeProposal, ...] = ()
    ranges: tuple[ProgramRangeProposal, ...] = ()
    relationships: tuple[ProgramRelationshipProposal, ...] = ()
    scenarios: tuple[ProgramScenarioProposal, ...] = ()
    metric_applicability: tuple[
        ProgramMetricApplicabilityBinding,
        ...,
    ] = ()

    def __post_init__(self) -> None:
        _typed(
            self.assumptions,
            ProgramAssumptionProposal,
            "assumptions",
        )
        _typed(self.nodes, ProgramNodeProposal, "nodes")
        _typed(self.ranges, ProgramRangeProposal, "ranges")
        _typed(
            self.relationships,
            ProgramRelationshipProposal,
            "relationships",
        )
        _typed(
            self.scenarios,
            ProgramScenarioProposal,
            "scenarios",
        )
        _typed(
            self.metric_applicability,
            ProgramMetricApplicabilityBinding,
            "metric_applicability",
        )


@dataclass(frozen=True, slots=True)
class ProgramCompilationReceipt:
    compilation_id: str
    project_id: str
    run_id: str
    base_state_sha256: str
    brief_digest: str
    compiler_id: str
    compiler_version: str
    program_digest: str
    proposal_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    open_obligation_ids: tuple[str, ...]

    SCHEMA = "ProgramCompilationReceipt@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "compilation_id": self.compilation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "brief_digest": self.brief_digest,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "program_digest": self.program_digest,
            "proposal_ids": list(self.proposal_ids),
            "scenario_ids": list(self.scenario_ids),
            "open_obligation_ids": list(self.open_obligation_ids),
            "generation_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CompiledDesignProgram:
    program: DesignProgram
    receipt: ProgramCompilationReceipt


def compile_design_program(
    *,
    brief: DesignBrief,
    proposals: ProgramProposalBundle,
    compiler_id: str = _COMPILER_ID,
    compiler_version: str = _COMPILER_VERSION,
) -> CompiledDesignProgram:
    """Preserve bounded proposals without inventing a program answer."""

    if not isinstance(brief, DesignBrief):
        raise TypeError("brief must be DesignBrief")
    if not isinstance(proposals, ProgramProposalBundle):
        raise TypeError("proposals must be ProgramProposalBundle")
    _text(compiler_id, "compiler_id")
    _text(compiler_version, "compiler_version")

    base_digest = brief.base.require_digest()
    claim_refs = tuple(item.ref for item in brief.claims)
    constraint_refs = tuple(
        f"brief-constraint:{item.proposal_id}"
        for item in brief.constraint_proposals
    )
    evidence_refs = tuple(
        sorted(
            {
                *brief.evidence_refs,
                *claim_refs,
                *constraint_refs,
            }
        )
    )
    allowed_sources = set(evidence_refs)

    _unique(
        tuple(item.assumption_id for item in proposals.assumptions),
        "assumption ids",
    )
    _unique(
        tuple(item.node_id for item in proposals.nodes),
        "node ids",
    )
    _unique(
        tuple(item.range_id for item in proposals.ranges),
        "range ids",
    )
    _unique(
        tuple(
            item.relationship_id for item in proposals.relationships
        ),
        "relationship ids",
    )
    _unique(
        tuple(item.scenario_id for item in proposals.scenarios),
        "scenario ids",
    )
    _unique(
        tuple(
            item.decision.decision_id
            for item in proposals.metric_applicability
        ),
        "metric applicability decision ids",
    )
    _unique(
        tuple(
            item.decision.metric.value
            for item in proposals.metric_applicability
        ),
        "metric applicability metrics",
    )
    for binding in proposals.metric_applicability:
        decision = binding.decision
        if (
            decision.project_id != brief.project_id
            or decision.run_id != brief.run_id
            or decision.base != brief.base
        ):
            raise ProgramCompilationError(
                "metric applicability decision is not exact-base"
            )
        if binding.decision_ref not in allowed_sources:
            raise ProgramCompilationError(
                "metric applicability decision is absent from the "
                "exact-base brief"
            )
        if not binding.decision_ref.startswith(
            f"project://{brief.project_id}/runs/{brief.run_id}/"
        ):
            raise ProgramCompilationError(
                "metric applicability decision reference is outside the run"
            )
        if not set(decision.source_refs) <= allowed_sources:
            raise ProgramCompilationError(
                "metric applicability source is absent from the "
                "exact-base brief"
            )
    for item in (
        *proposals.assumptions,
        *proposals.nodes,
        *proposals.ranges,
        *proposals.relationships,
        *proposals.scenarios,
    ):
        if not set(item.source_refs) <= allowed_sources:
            raise ProgramCompilationError(
                "proposal source is absent from the exact-base brief"
            )

    assumption_ids = {
        item.assumption_id for item in proposals.assumptions
    }
    for item in (
        *proposals.nodes,
        *proposals.ranges,
        *proposals.relationships,
        *proposals.scenarios,
    ):
        if not set(item.assumption_ids) <= assumption_ids:
            raise ProgramCompilationError(
                "proposal cites an unknown program assumption"
            )

    node_ids = {item.node_id for item in proposals.nodes}
    scenario_ids = {
        item.scenario_id for item in proposals.scenarios
    }
    range_ids = {item.range_id for item in proposals.ranges}
    for item in proposals.ranges:
        if (
            item.applies_to_node_id is None
            and item.scenario_id is None
        ):
            raise ProgramCompilationError(
                "range must apply to a node or a named scenario"
            )
        if (
            item.applies_to_node_id is not None
            and item.applies_to_node_id not in node_ids
        ):
            raise ProgramCompilationError(
                "range cites an unknown program node"
            )
        if (
            item.scenario_id is not None
            and item.scenario_id not in scenario_ids
        ):
            raise ProgramCompilationError(
                "range cites an unknown scenario"
            )
    for item in proposals.relationships:
        if (
            item.source_node_id not in node_ids
            or item.target_node_id not in node_ids
        ):
            raise ProgramCompilationError(
                "relationship cites an unknown program node"
            )
    for item in proposals.scenarios:
        if not set(item.range_ids) <= range_ids:
            raise ProgramCompilationError(
                "scenario cites an unknown range"
            )
        if any(
            value.scenario_id != item.scenario_id
            for value in proposals.ranges
            if value.range_id in item.range_ids
        ):
            raise ProgramCompilationError(
                "scenario and range assignments disagree"
            )
    assigned_range_ids = {
        range_id
        for item in proposals.scenarios
        for range_id in item.range_ids
    }
    if any(
        item.scenario_id is not None
        and item.range_id not in assigned_range_ids
        for item in proposals.ranges
    ):
        raise ProgramCompilationError(
            "scenario-assigned range is absent from its scenario"
        )
    ranged_metrics = {item.metric for item in proposals.ranges}
    if any(
        binding.decision.applicability
        is ProgramMetricApplicability.NOT_APPLICABLE
        and binding.decision.metric in ranged_metrics
        for binding in proposals.metric_applicability
    ):
        raise ProgramCompilationError(
            "not-applicable metric cannot also have a program range"
        )

    size_status = next(
        item.status
        for item in brief.slots
        if item.slot is BriefSlot.SIZE
    )
    if size_status is BriefSlotStatus.UNKNOWN and proposals.ranges:
        if len(proposals.scenarios) < 2:
            raise ProgramCompilationError(
                "unknown scale requires multiple named bounded scenarios"
            )
        if any(item.scenario_id is None for item in proposals.ranges):
            raise ProgramCompilationError(
                "unknown-scale ranges must belong to named scenarios"
            )

    assumptions = tuple(
        ProgramAssumption(
            assumption_id=item.assumption_id,
            statement=item.statement,
            source_refs=item.source_refs,
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposals.assumptions,
            key=lambda value: value.assumption_id,
        )
    )
    nodes = tuple(
        ProgramNode(
            node_id=item.node_id,
            kind=item.kind,
            label=item.label,
            epistemic_status=item.epistemic_status,
            source_refs=item.source_refs,
            assumption_refs=tuple(
                f"program-assumption:{value}"
                for value in item.assumption_ids
            ),
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposals.nodes,
            key=lambda value: value.node_id,
        )
    )
    ranges = tuple(
        ProgramRange(
            range_id=item.range_id,
            metric=item.metric,
            applies_to_ref=(
                f"program-node:{item.applies_to_node_id}"
                if item.applies_to_node_id is not None
                else f"program-scenario:{item.scenario_id}"
            ),
            minimum=item.minimum,
            maximum=item.maximum,
            unit=item.unit,
            scenario_id=item.scenario_id,
            epistemic_status=item.epistemic_status,
            source_refs=item.source_refs,
            assumption_refs=tuple(
                f"program-assumption:{value}"
                for value in item.assumption_ids
            ),
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposals.ranges,
            key=lambda value: value.range_id,
        )
    )
    relationships = tuple(
        ProgramRelationship(
            relationship_id=item.relationship_id,
            kind=item.kind,
            source_node_ref=f"program-node:{item.source_node_id}",
            target_node_ref=f"program-node:{item.target_node_id}",
            strength=item.strength,
            directed=item.directed,
            epistemic_status=item.epistemic_status,
            source_refs=item.source_refs,
            assumption_refs=tuple(
                f"program-assumption:{value}"
                for value in item.assumption_ids
            ),
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposals.relationships,
            key=lambda value: value.relationship_id,
        )
    )
    scenarios = tuple(
        ProgramScenario(
            scenario_id=item.scenario_id,
            label=item.label,
            range_ids=item.range_ids,
            source_refs=item.source_refs,
            assumption_refs=tuple(
                f"program-assumption:{value}"
                for value in item.assumption_ids
            ),
            compiler_id=compiler_id,
            base_state_sha256=base_digest,
        )
        for item in sorted(
            proposals.scenarios,
            key=lambda value: value.scenario_id,
        )
    )

    obligations = _open_obligations(
        brief=brief,
        nodes=nodes,
        ranges=ranges,
        relationships=relationships,
        scenarios=scenarios,
        metric_applicability=proposals.metric_applicability,
    )
    program = DesignProgram(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base=brief.base,
        brief_digest=brief.brief_digest,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        assumptions=assumptions,
        nodes=nodes,
        ranges=ranges,
        relationships=relationships,
        scenarios=scenarios,
        obligations=obligations,
        evidence_refs=evidence_refs,
        external_constraint_refs=constraint_refs,
    )
    proposal_ids = tuple(
        sorted(
            (
                *(
                    f"assumption:{item.assumption_id}"
                    for item in proposals.assumptions
                ),
                *(
                    f"node:{item.node_id}"
                    for item in proposals.nodes
                ),
                *(
                    f"range:{item.range_id}"
                    for item in proposals.ranges
                ),
                *(
                    f"relationship:{item.relationship_id}"
                    for item in proposals.relationships
                ),
                *(
                    f"scenario:{item.scenario_id}"
                    for item in proposals.scenarios
                ),
                *(
                    "metric-applicability:"
                    f"{item.decision.decision_id}"
                    for item in proposals.metric_applicability
                ),
            )
        )
    )
    compilation_id = canonical_digest(
        {
            "project_id": brief.project_id,
            "run_id": brief.run_id,
            "base_state_sha256": base_digest,
            "brief_digest": brief.brief_digest,
            "compiler_id": compiler_id,
            "compiler_version": compiler_version,
            "proposal_ids": proposal_ids,
        }
    )[:24]
    receipt = ProgramCompilationReceipt(
        compilation_id=f"program-compilation.{compilation_id}",
        project_id=brief.project_id,
        run_id=brief.run_id,
        base_state_sha256=base_digest,
        brief_digest=brief.brief_digest,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        program_digest=program.program_digest,
        proposal_ids=proposal_ids,
        scenario_ids=tuple(
            item.scenario_id for item in scenarios
        ),
        open_obligation_ids=tuple(
            item.obligation_id
            for item in obligations
            if item.status
            in {ObligationStatus.OPEN, ObligationStatus.BLOCKED}
        ),
    )
    return CompiledDesignProgram(program=program, receipt=receipt)


def _open_obligations(
    *,
    brief: DesignBrief,
    nodes: tuple[ProgramNode, ...],
    ranges: tuple[ProgramRange, ...],
    relationships: tuple[ProgramRelationship, ...],
    scenarios: tuple[ProgramScenario, ...],
    metric_applicability: tuple[
        ProgramMetricApplicabilityBinding,
        ...,
    ],
) -> tuple[DesignObligation, ...]:
    source = f"design-brief:{brief.brief_digest}"
    obligations: list[DesignObligation] = []
    applicability_by_metric = {
        item.decision.metric: item for item in metric_applicability
    }
    checks = (
        (
            not any(item.kind is ProgramNodeKind.FUNCTION for item in nodes),
            None,
            "program.functions",
            "Derive or explicitly leave unresolved the project functions.",
        ),
        (
            not any(
                item.metric is ProgramMetricKind.CAPACITY
                for item in ranges
            ),
            ProgramMetricKind.CAPACITY,
            "program.capacity",
            "Derive bounded capacity hypotheses or preserve capacity as unknown.",
        ),
        (
            not any(
                item.metric is ProgramMetricKind.NET_AREA
                for item in ranges
            ),
            ProgramMetricKind.NET_AREA,
            "program.net-area",
            "Derive bounded net-area hypotheses or preserve net area as unknown.",
        ),
        (
            not any(
                item.metric is ProgramMetricKind.GROSS_ALLOWANCE
                for item in ranges
            ),
            ProgramMetricKind.GROSS_ALLOWANCE,
            "program.gross-allowance",
            "Derive a bounded gross allowance or preserve it as unknown.",
        ),
        (
            not any(
                item.metric is ProgramMetricKind.TOTAL_FLOOR_AREA
                for item in ranges
            ),
            ProgramMetricKind.TOTAL_FLOOR_AREA,
            "program.total-floor-area",
            "Derive bounded total-floor-area hypotheses or preserve them as unknown.",
        ),
        (
            not relationships,
            None,
            "program.relationships",
            "Derive functional relationship hypotheses without selecting layout.",
        ),
    )
    for missing, metric, suffix, statement in checks:
        if missing:
            binding = (
                applicability_by_metric.get(metric)
                if metric is not None
                else None
            )
            if (
                binding is not None
                and binding.decision.applicability
                is ProgramMetricApplicability.NOT_APPLICABLE
            ):
                obligations.append(
                    DesignObligation(
                        obligation_id=f"resolve.{suffix}",
                        statement=(
                            "Metric is explicitly not applicable to this "
                            f"project type: {binding.decision.rationale}"
                        ),
                        source_ref=binding.decision_ref,
                        status=ObligationStatus.WAIVED,
                        subject_refs=(
                            f"program-metric:{metric.value}",
                        ),
                        validator_ref=binding.decision_ref,
                    )
                )
                continue
            obligations.append(
                DesignObligation(
                    obligation_id=f"resolve.{suffix}",
                    statement=statement,
                    source_ref=source,
                )
            )
    footprint_binding = applicability_by_metric.get(
        ProgramMetricKind.FOOTPRINT
    )
    if (
        footprint_binding is not None
        and footprint_binding.decision.applicability
        is ProgramMetricApplicability.NOT_APPLICABLE
        and not any(
            item.metric is ProgramMetricKind.FOOTPRINT
            for item in ranges
        )
    ):
        obligations.append(
            DesignObligation(
                obligation_id="resolve.program.footprint",
                statement=(
                    "Metric is explicitly not applicable to this project "
                    f"type: {footprint_binding.decision.rationale}"
                ),
                source_ref=footprint_binding.decision_ref,
                status=ObligationStatus.WAIVED,
                subject_refs=("program-metric:footprint",),
                validator_ref=footprint_binding.decision_ref,
            )
        )
    size_status = next(
        item.status
        for item in brief.slots
        if item.slot is BriefSlot.SIZE
    )
    if size_status is BriefSlotStatus.UNKNOWN and not scenarios:
        obligations.append(
            DesignObligation(
                obligation_id="resolve.program.scale-scenarios",
                statement=(
                    "Provide multiple evidence-backed bounded scale scenarios "
                    "or explicitly leave scale unresolved."
                ),
                source_ref=source,
            )
        )
    return tuple(sorted(obligations, key=lambda item: item.obligation_id))


def maximum_footprint_constraint_refs(
    brief: DesignBrief,
) -> tuple[str, ...]:
    """Expose exact brief constraints without converting them into facts."""

    if not isinstance(brief, DesignBrief):
        raise TypeError("brief must be DesignBrief")
    return tuple(
        f"brief-constraint:{item.proposal_id}"
        for item in brief.constraint_proposals
        if item.slot is BriefSlot.SIZE
        and item.operator is BriefConstraintOperator.MAXIMUM
        and "footprint" in item.parameter_key.lower()
    )


def _status(value: object) -> None:
    if value not in {
        FactEpistemicStatus.DECLARED,
        FactEpistemicStatus.OBSERVED,
        FactEpistemicStatus.DERIVED,
        FactEpistemicStatus.HYPOTHESIS,
    }:
        raise ValueError("unsupported program epistemic status")


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _typed(value: object, item_type: type, field: str) -> None:
    items = _tuple(value, field)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{field} contains the wrong item type")


def _refs(value: object, field: str) -> None:
    items = _tuple(value, field)
    if not items:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_logical_ref(item, field)
    _unique(tuple(items), field)


def _ids(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    items = _tuple(value, field)
    if not items and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_local_id(item, field)
    _unique(tuple(items), field)


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


__all__ = [
    "ProgramCompilationError",
    "ProgramAssumptionProposal",
    "ProgramNodeProposal",
    "ProgramRangeProposal",
    "ProgramRelationshipProposal",
    "ProgramScenarioProposal",
    "ProgramMetricApplicabilityBinding",
    "ProgramProposalBundle",
    "ProgramCompilationReceipt",
    "CompiledDesignProgram",
    "compile_design_program",
    "maximum_footprint_constraint_refs",
]
