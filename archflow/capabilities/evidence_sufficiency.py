"""Pure research-universe closure and evidence-sufficiency contracts.

The framework owns mechanics only: exact identifiers and digests, dependency
closure, source-family counting, conflict/uncertainty checks, saturation, and
typed frontier construction.  Projects own every architectural term, edge
kind, discovery lens, source-family identity, qualifier, tolerance, and
resolution policy supplied to these functions.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from archflow.state.geometry_program import digest_value


class EvidenceSufficiencyError(ValueError):
    """A universe, policy, or evidence input is malformed."""


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
        raise EvidenceSufficiencyError(f"{field} must be bounded non-empty text")
    return value


def _sha(value: str | None, field: str) -> None:
    if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceSufficiencyError(f"{field} must be SHA-256 or None")


def _unique(values: Iterable[str], field: str) -> tuple[str, ...]:
    result = tuple(values)
    for value in result:
        _text(value, field)
    if len(result) != len(set(result)):
        raise EvidenceSufficiencyError(f"{field} must be unique")
    return result


class TargetKind(StrEnum):
    DECISION = "decision"
    EDGE = "edge"


class EvidenceModality(StrEnum):
    """Transport-neutral evidence form; projects still own its semantics."""

    TEXT_FACT = "text_fact"
    VISUAL_REGION = "visual_region"
    DRAWING_OBSERVATION = "drawing_observation"
    MEASUREMENT = "measurement"
    DERIVATION = "derivation"


class EpistemicRole(StrEnum):
    OBSERVATION = "observation"
    AUTHOR_DECLARATION = "author_declaration"
    HYPOTHESIS = "hypothesis"
    DERIVATION = "derivation"


class ResolutionMode(StrEnum):
    RETRIEVE = "retrieve"
    DERIVE = "derive"
    DECIDE = "decide"
    MEASURE = "measure"
    ADJUDICATE = "adjudicate"
    DISCOVER = "discover"


class ExpansionKind(StrEnum):
    NODE = "node"
    EDGE = "edge"


class ExpansionDisposition(StrEnum):
    PENDING = "pending"
    ADOPTED = "adopted"
    REJECTED = "rejected"


class ClosureStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class SufficiencyStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    SUFFICIENT = "sufficient"


class FrontierStatus(StrEnum):
    CONTINUE = "continue"
    UNIVERSE_EXPANSION_REQUIRED = "universe_expansion_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    COMPLETE = "complete"


class GapReason(StrEnum):
    PENDING_UNIVERSE_EXPANSION = "pending_universe_expansion"
    ADOPTED_EXPANSION_NOT_APPLIED = "adopted_expansion_not_applied"
    REQUIRED_EDGE_UNRESOLVED = "required_edge_unresolved"
    DISCOVERY_LENS_UNSWEPT = "discovery_lens_unswept"
    SATURATION_NOT_REACHED = "saturation_not_reached"
    MISSING_POLICY_RULE = "missing_policy_rule"
    MISSING_BASIS = "missing_basis"
    INSUFFICIENT_SOURCE_FAMILIES = "insufficient_source_families"
    MISSING_QUALIFIER = "missing_qualifier"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    UNCERTAINTY_SEMANTICS_MISMATCH = "uncertainty_semantics_mismatch"
    UNCERTAINTY_EXCEEDS_TOLERANCE = "uncertainty_exceeds_tolerance"


@dataclass(frozen=True, slots=True)
class DecisionNode:
    decision_ref: str
    ontology_kind: str
    required: bool = True

    def __post_init__(self) -> None:
        _text(self.decision_ref, "decision_ref")
        _text(self.ontology_kind, "ontology_kind")
        if type(self.required) is not bool:
            raise TypeError("required must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_ref": self.decision_ref,
            "ontology_kind": self.ontology_kind,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class DecisionEdge:
    edge_ref: str
    source_ref: str
    target_ref: str
    relation_kind: str
    required: bool = True

    def __post_init__(self) -> None:
        for value, field in (
            (self.edge_ref, "edge_ref"),
            (self.source_ref, "source_ref"),
            (self.target_ref, "target_ref"),
            (self.relation_kind, "relation_kind"),
        ):
            _text(value, field)
        if self.source_ref == self.target_ref:
            raise EvidenceSufficiencyError("decision edge cannot be a self edge")
        if type(self.required) is not bool:
            raise TypeError("required must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_ref": self.edge_ref,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "relation_kind": self.relation_kind,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class DecisionUniverseRevision:
    universe_id: str
    revision_id: str
    scope_digest: str
    ontology_ref: str
    nodes: tuple[DecisionNode, ...]
    edges: tuple[DecisionEdge, ...] = ()
    seed_refs: tuple[str, ...] = ()
    parent_digest: str | None = None

    SCHEMA = "DecisionUniverseRevision@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.universe_id, "universe_id"),
            (self.revision_id, "revision_id"),
            (self.ontology_ref, "ontology_ref"),
        ):
            _text(value, field)
        _sha(self.scope_digest, "scope_digest")
        _sha(self.parent_digest, "parent_digest")
        if not self.nodes or any(not isinstance(item, DecisionNode) for item in self.nodes):
            raise EvidenceSufficiencyError("universe requires DecisionNode values")
        if any(not isinstance(item, DecisionEdge) for item in self.edges):
            raise TypeError("edges must be DecisionEdge values")
        node_refs = [item.decision_ref for item in self.nodes]
        edge_refs = [item.edge_ref for item in self.edges]
        if len(node_refs) != len(set(node_refs)) or len(edge_refs) != len(set(edge_refs)):
            raise EvidenceSufficiencyError("universe node and edge refs must be unique")
        if set(node_refs) & set(edge_refs):
            raise EvidenceSufficiencyError("node and edge refs must not overlap")
        if any(
            edge.source_ref not in node_refs or edge.target_ref not in node_refs
            for edge in self.edges
        ):
            raise EvidenceSufficiencyError("every edge endpoint must be a universe node")
        _unique(self.seed_refs, "seed_ref")
        if not set(self.seed_refs) <= set(node_refs):
            raise EvidenceSufficiencyError("universe seeds must name universe nodes")

    @property
    def universe_digest(self) -> str:
        return digest_value(self.to_dict())

    @property
    def target_refs(self) -> frozenset[str]:
        return frozenset(
            [item.decision_ref for item in self.nodes]
            + [item.edge_ref for item in self.edges]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "universe_id": self.universe_id,
            "revision_id": self.revision_id,
            "scope_digest": self.scope_digest,
            "ontology_ref": self.ontology_ref,
            "nodes": [
                item.to_dict()
                for item in sorted(self.nodes, key=lambda item: item.decision_ref)
            ],
            "edges": [
                item.to_dict()
                for item in sorted(self.edges, key=lambda item: item.edge_ref)
            ],
            "seed_refs": sorted(self.seed_refs),
            "parent_digest": self.parent_digest,
            "design_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class UniverseExpansionCandidate:
    candidate_id: str
    kind: ExpansionKind
    proposed_ref: str
    predecessor_universe_digest: str
    scope_digest: str
    evidence_refs: tuple[str, ...]
    blocking: bool = True
    disposition: ExpansionDisposition = ExpansionDisposition.PENDING
    disposition_ref: str | None = None

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        _text(self.proposed_ref, "proposed_ref")
        _sha(self.predecessor_universe_digest, "predecessor_universe_digest")
        _sha(self.scope_digest, "expansion scope_digest")
        if not isinstance(self.kind, ExpansionKind):
            raise TypeError("kind must be ExpansionKind")
        if not isinstance(self.disposition, ExpansionDisposition):
            raise TypeError("disposition must be ExpansionDisposition")
        if type(self.blocking) is not bool:
            raise TypeError("blocking must be bool")
        if not _unique(self.evidence_refs, "expansion evidence_ref"):
            raise EvidenceSufficiencyError("expansion candidate requires evidence")
        if self.disposition is ExpansionDisposition.PENDING:
            if self.disposition_ref is not None:
                raise EvidenceSufficiencyError("pending expansion cannot have disposition_ref")
        elif self.disposition_ref is None:
            raise EvidenceSufficiencyError("resolved expansion requires disposition_ref")
        else:
            _text(self.disposition_ref, "disposition_ref")


@dataclass(frozen=True, slots=True)
class DiscoverySweepReceipt:
    wave_index: int
    lens_ref: str
    complete: bool
    candidate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.wave_index) is not int or self.wave_index < 1:
            raise EvidenceSufficiencyError("wave_index must be a positive integer")
        _text(self.lens_ref, "lens_ref")
        if type(self.complete) is not bool:
            raise TypeError("complete must be bool")
        _unique(self.candidate_ids, "sweep candidate_id")


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    resolution_id: str
    target_ref: str
    mode: ResolutionMode
    refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.resolution_id, "resolution_id")
        _text(self.target_ref, "resolution target_ref")
        if not isinstance(self.mode, ResolutionMode):
            raise TypeError("mode must be ResolutionMode")
        if self.mode in (ResolutionMode.ADJUDICATE, ResolutionMode.DISCOVER):
            raise EvidenceSufficiencyError("resolution record has a non-terminal mode")
        if not _unique(self.refs, "resolution ref"):
            raise EvidenceSufficiencyError("resolution requires at least one ref")


@dataclass(frozen=True, slots=True)
class NumericUncertainty:
    lower: float
    upper: float
    unit_ref: str
    datum_ref: str

    def __post_init__(self) -> None:
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (self.lower, self.upper)):
            raise EvidenceSufficiencyError("uncertainty bounds must be finite numbers")
        if self.lower > self.upper:
            raise EvidenceSufficiencyError("uncertainty lower bound exceeds upper")
        _text(self.unit_ref, "uncertainty unit_ref")
        _text(self.datum_ref, "uncertainty datum_ref")

    @property
    def width(self) -> float:
        return float(self.upper) - float(self.lower)


@dataclass(frozen=True, slots=True)
class EvidenceClaimBinding:
    binding_id: str
    obligation_id: str
    target_ref: str
    fact_ref: str
    source_ref: str
    source_family_ref: str
    claim_key: str
    position_key: str
    modality: EvidenceModality = EvidenceModality.TEXT_FACT
    epistemic_role: EpistemicRole = EpistemicRole.OBSERVATION
    region_ref: str | None = None
    model_ref: str | None = None
    authority_ref: str | None = None
    quantified_annotation: bool = False
    qualifiers: tuple[str, ...] = ()
    uncertainty: NumericUncertainty | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.binding_id, "binding_id"),
            (self.obligation_id, "obligation_id"),
            (self.target_ref, "claim target_ref"),
            (self.fact_ref, "fact_ref"),
            (self.source_ref, "source_ref"),
            (self.source_family_ref, "source_family_ref"),
            (self.claim_key, "claim_key"),
            (self.position_key, "position_key"),
        ):
            _text(value, field)
        _unique(self.qualifiers, "qualifier")
        if not isinstance(self.modality, EvidenceModality):
            raise TypeError("modality must be EvidenceModality")
        if not isinstance(self.epistemic_role, EpistemicRole):
            raise TypeError("epistemic_role must be EpistemicRole")
        for value, field in (
            (self.region_ref, "region_ref"),
            (self.model_ref, "model_ref"),
            (self.authority_ref, "authority_ref"),
        ):
            if value is not None:
                _text(value, field)
        if type(self.quantified_annotation) is not bool:
            raise TypeError("quantified_annotation must be bool")
        if self.modality in (
            EvidenceModality.VISUAL_REGION,
            EvidenceModality.DRAWING_OBSERVATION,
        ) and (self.region_ref is None or self.model_ref is None):
            raise EvidenceSufficiencyError(
                "visual and drawing claims require source-region and model refs"
            )
        if (
            self.modality is EvidenceModality.VISUAL_REGION
            and self.epistemic_role is EpistemicRole.AUTHOR_DECLARATION
        ):
            raise EvidenceSufficiencyError(
                "observational image evidence cannot become an author declaration"
            )
        if self.epistemic_role is EpistemicRole.AUTHOR_DECLARATION and self.authority_ref is None:
            raise EvidenceSufficiencyError("author declaration requires authority_ref")
        if self.modality is EvidenceModality.DRAWING_OBSERVATION:
            if self.quantified_annotation and self.epistemic_role is not EpistemicRole.AUTHOR_DECLARATION:
                raise EvidenceSufficiencyError(
                    "quantified drawing annotation requires an author declaration"
                )
            if not self.quantified_annotation and self.epistemic_role is not EpistemicRole.HYPOTHESIS:
                raise EvidenceSufficiencyError(
                    "unquantified drawing marks may enter only as hypotheses"
                )
        if self.uncertainty is not None and not isinstance(self.uncertainty, NumericUncertainty):
            raise TypeError("uncertainty must be NumericUncertainty or None")


@dataclass(frozen=True, slots=True)
class ConflictDisposition:
    claim_key: str
    accepted_position_keys: tuple[str, ...]
    authority_ref: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.claim_key, "conflict claim_key")
        if not _unique(self.accepted_position_keys, "accepted position_key"):
            raise EvidenceSufficiencyError("conflict disposition requires a position")
        _text(self.authority_ref, "conflict authority_ref")
        if not _unique(self.evidence_refs, "conflict evidence_ref"):
            raise EvidenceSufficiencyError("conflict disposition requires evidence")


@dataclass(frozen=True, slots=True)
class EvidenceRule:
    obligation_id: str
    target_ref: str
    allowed_modes: tuple[ResolutionMode, ...] = (ResolutionMode.RETRIEVE,)
    minimum_bindings: int = 1
    minimum_source_families: int = 1
    required_qualifiers: tuple[str, ...] = ()
    maximum_uncertainty_width: float | None = None
    uncertainty_unit_ref: str | None = None
    uncertainty_datum_ref: str | None = None

    def __post_init__(self) -> None:
        _text(self.obligation_id, "obligation_id")
        _text(self.target_ref, "rule target_ref")
        if not self.allowed_modes or any(not isinstance(item, ResolutionMode) for item in self.allowed_modes):
            raise EvidenceSufficiencyError("rule requires allowed resolution modes")
        if len(self.allowed_modes) != len(set(self.allowed_modes)):
            raise EvidenceSufficiencyError("allowed_modes must be unique")
        if any(item in (ResolutionMode.ADJUDICATE, ResolutionMode.DISCOVER) for item in self.allowed_modes):
            raise EvidenceSufficiencyError("rule allowed_modes must be terminal")
        for value, field in (
            (self.minimum_bindings, "minimum_bindings"),
            (self.minimum_source_families, "minimum_source_families"),
        ):
            if type(value) is not int or value < 0:
                raise EvidenceSufficiencyError(f"{field} must be non-negative integer")
        _unique(self.required_qualifiers, "required qualifier")
        if self.maximum_uncertainty_width is not None:
            if (
                type(self.maximum_uncertainty_width) not in (int, float)
                or not math.isfinite(self.maximum_uncertainty_width)
                or self.maximum_uncertainty_width < 0
            ):
                raise EvidenceSufficiencyError("maximum uncertainty width is invalid")
            if self.uncertainty_unit_ref is None or self.uncertainty_datum_ref is None:
                raise EvidenceSufficiencyError("uncertainty tolerance requires unit and datum")
        for value, field in (
            (self.uncertainty_unit_ref, "uncertainty_unit_ref"),
            (self.uncertainty_datum_ref, "uncertainty_datum_ref"),
        ):
            if value is not None:
                _text(value, field)


@dataclass(frozen=True, slots=True)
class EvidenceSufficiencyPolicy:
    policy_id: str
    rules: tuple[EvidenceRule, ...]
    required_discovery_lenses: tuple[str, ...] = ()
    required_no_novelty_waves: int = 0
    allowed_conflict_authority_refs: tuple[str, ...] = ()

    SCHEMA = "EvidenceSufficiencyPolicy@1"

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id")
        if any(not isinstance(item, EvidenceRule) for item in self.rules):
            raise TypeError("rules must be EvidenceRule values")
        ids = [item.obligation_id for item in self.rules]
        targets = [item.target_ref for item in self.rules]
        if len(ids) != len(set(ids)) or len(targets) != len(set(targets)):
            raise EvidenceSufficiencyError("policy rules require unique ids and targets")
        _unique(self.required_discovery_lenses, "discovery lens")
        _unique(
            self.allowed_conflict_authority_refs,
            "allowed conflict authority_ref",
        )
        if type(self.required_no_novelty_waves) is not int or self.required_no_novelty_waves < 0:
            raise EvidenceSufficiencyError("required_no_novelty_waves must be non-negative")
        if self.required_no_novelty_waves and not self.required_discovery_lenses:
            raise EvidenceSufficiencyError("saturation waves require discovery lenses")

    @property
    def policy_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "rules": [
                {
                    "obligation_id": rule.obligation_id,
                    "target_ref": rule.target_ref,
                    "allowed_modes": sorted(item.value for item in rule.allowed_modes),
                    "minimum_bindings": rule.minimum_bindings,
                    "minimum_source_families": rule.minimum_source_families,
                    "required_qualifiers": sorted(rule.required_qualifiers),
                    "maximum_uncertainty_width": rule.maximum_uncertainty_width,
                    "uncertainty_unit_ref": rule.uncertainty_unit_ref,
                    "uncertainty_datum_ref": rule.uncertainty_datum_ref,
                }
                for rule in sorted(self.rules, key=lambda item: item.obligation_id)
            ],
            "required_discovery_lenses": sorted(self.required_discovery_lenses),
            "required_no_novelty_waves": self.required_no_novelty_waves,
            "allowed_conflict_authority_refs": sorted(
                self.allowed_conflict_authority_refs
            ),
            "selection_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class Gap:
    reason: GapReason
    target_ref: str
    route: ResolutionMode
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reason, GapReason):
            raise TypeError("reason must be GapReason")
        if not isinstance(self.route, ResolutionMode):
            raise TypeError("route must be ResolutionMode")
        _text(self.target_ref, "gap target_ref")
        _unique(self.refs, "gap ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "target_ref": self.target_ref,
            "route": self.route.value,
            "refs": sorted(self.refs),
        }


def _gap_key(gap: Gap) -> tuple[str, str, str, tuple[str, ...]]:
    return (gap.reason.value, gap.target_ref, gap.route.value, tuple(sorted(gap.refs)))


@dataclass(frozen=True, slots=True)
class DecisionUniverseClosure:
    universe_digest: str
    policy_digest: str
    no_novelty_streak: int
    required_no_novelty_waves: int
    gaps: tuple[Gap, ...]
    status: ClosureStatus

    SCHEMA = "DecisionUniverseClosure@1"

    @property
    def closure_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "universe_digest": self.universe_digest,
            "policy_digest": self.policy_digest,
            "no_novelty_streak": self.no_novelty_streak,
            "required_no_novelty_waves": self.required_no_novelty_waves,
            "gaps": [item.to_dict() for item in self.gaps],
            "status": self.status.value,
            "authority": False,
        }


@dataclass(frozen=True, slots=True)
class EvidenceSufficiencyReceipt:
    universe_digest: str
    policy_digest: str
    basis_present_target_refs: tuple[str, ...]
    missing_basis_target_refs: tuple[str, ...]
    gaps: tuple[Gap, ...]
    status: SufficiencyStatus

    SCHEMA = "EvidenceSufficiencyReceipt@1"

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "universe_digest": self.universe_digest,
            "policy_digest": self.policy_digest,
            "basis_present_target_refs": list(self.basis_present_target_refs),
            "missing_basis_target_refs": list(self.missing_basis_target_refs),
            "gaps": [item.to_dict() for item in self.gaps],
            "status": self.status.value,
            "authority": False,
        }


@dataclass(frozen=True, slots=True)
class FrontierWorkItem:
    work_id: str
    origin: str
    gap: Gap

    def to_dict(self) -> dict[str, object]:
        return {"work_id": self.work_id, "origin": self.origin, **self.gap.to_dict()}


@dataclass(frozen=True, slots=True)
class ResearchFrontier:
    universe_digest: str
    policy_digest: str
    closure_digest: str
    sufficiency_receipt_digest: str
    work_items: tuple[FrontierWorkItem, ...]
    status: FrontierStatus

    SCHEMA = "ResearchFrontier@1"

    @property
    def frontier_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "universe_digest": self.universe_digest,
            "policy_digest": self.policy_digest,
            "closure_digest": self.closure_digest,
            "sufficiency_receipt_digest": self.sufficiency_receipt_digest,
            "work_items": [item.to_dict() for item in self.work_items],
            "status": self.status.value,
            "authority": False,
        }


def _route(rule: EvidenceRule | None, *, uncertainty: bool = False) -> ResolutionMode:
    if uncertainty and rule is not None and ResolutionMode.MEASURE in rule.allowed_modes:
        return ResolutionMode.MEASURE
    if rule is None:
        return ResolutionMode.DECIDE
    return sorted(rule.allowed_modes, key=lambda item: item.value)[0]


def compile_decision_universe_closure(
    universe: DecisionUniverseRevision,
    policy: EvidenceSufficiencyPolicy,
    *,
    expansions: tuple[UniverseExpansionCandidate, ...] = (),
    sweeps: tuple[DiscoverySweepReceipt, ...] = (),
    resolutions: tuple[ResolutionRecord, ...] = (),
) -> DecisionUniverseClosure:
    """Derive relative closure without inventing project ontology or policy."""

    if not isinstance(universe, DecisionUniverseRevision):
        raise TypeError("universe must be DecisionUniverseRevision")
    if not isinstance(policy, EvidenceSufficiencyPolicy):
        raise TypeError("policy must be EvidenceSufficiencyPolicy")
    if any(not isinstance(item, UniverseExpansionCandidate) for item in expansions):
        raise TypeError("expansions must be UniverseExpansionCandidate values")
    if any(not isinstance(item, DiscoverySweepReceipt) for item in sweeps):
        raise TypeError("sweeps must be DiscoverySweepReceipt values")
    if any(not isinstance(item, ResolutionRecord) for item in resolutions):
        raise TypeError("resolutions must be ResolutionRecord values")
    candidate_by_id = {item.candidate_id: item for item in expansions}
    if len(candidate_by_id) != len(expansions):
        raise EvidenceSufficiencyError("expansion candidate ids must be unique")
    resolution_by_target = {item.target_ref: item for item in resolutions}
    if len(resolution_by_target) != len(resolutions):
        raise EvidenceSufficiencyError("targets may have only one terminal resolution")
    unknown_resolution_targets = set(resolution_by_target) - universe.target_refs
    if unknown_resolution_targets:
        raise EvidenceSufficiencyError("resolution names a target outside the universe")
    rule_by_target = {item.target_ref: item for item in policy.rules}

    gaps: list[Gap] = []
    node_refs = {item.decision_ref for item in universe.nodes}
    edge_refs = {item.edge_ref for item in universe.edges}
    for item in expansions:
        if (
            item.predecessor_universe_digest != universe.universe_digest
            or item.scope_digest != universe.scope_digest
        ):
            raise EvidenceSufficiencyError(
                "expansion candidate crosses its predecessor universe or scope"
            )
        if item.blocking and item.disposition is ExpansionDisposition.PENDING:
            gaps.append(Gap(GapReason.PENDING_UNIVERSE_EXPANSION, item.proposed_ref, ResolutionMode.DECIDE, (item.candidate_id,)))
        if item.disposition is ExpansionDisposition.ADOPTED:
            applied = item.proposed_ref in (node_refs if item.kind is ExpansionKind.NODE else edge_refs)
            if not applied:
                gaps.append(Gap(GapReason.ADOPTED_EXPANSION_NOT_APPLIED, item.proposed_ref, ResolutionMode.DECIDE, (item.candidate_id,)))
    for edge in universe.edges:
        if edge.required and edge.edge_ref not in resolution_by_target:
            rule = rule_by_target.get(edge.edge_ref)
            gaps.append(Gap(GapReason.REQUIRED_EDGE_UNRESOLVED, edge.edge_ref, _route(rule), (edge.source_ref, edge.target_ref)))

    required_lenses = set(policy.required_discovery_lenses)
    seen_sweeps: dict[tuple[int, str], DiscoverySweepReceipt] = {}
    for sweep in sweeps:
        if sweep.lens_ref not in required_lenses:
            raise EvidenceSufficiencyError("sweep names a lens outside the policy")
        key = (sweep.wave_index, sweep.lens_ref)
        if key in seen_sweeps:
            raise EvidenceSufficiencyError("duplicate discovery lens in one wave")
        if not set(sweep.candidate_ids) <= set(candidate_by_id):
            raise EvidenceSufficiencyError("sweep names an unknown expansion candidate")
        seen_sweeps[key] = sweep

    streak = 0
    if required_lenses:
        latest = max((item.wave_index for item in sweeps), default=0)
        for lens in sorted(required_lenses):
            latest_sweep = seen_sweeps.get((latest, lens))
            if latest_sweep is None or not latest_sweep.complete:
                gaps.append(Gap(GapReason.DISCOVERY_LENS_UNSWEPT, lens, ResolutionMode.DISCOVER, (f"wave:{latest}",)))
        wave = latest
        while wave > 0:
            rows = [seen_sweeps.get((wave, lens)) for lens in required_lenses]
            if any(row is None or not row.complete for row in rows):
                break
            candidate_ids = {candidate_id for row in rows if row is not None for candidate_id in row.candidate_ids}
            if any(candidate_by_id[candidate_id].blocking for candidate_id in candidate_ids):
                break
            streak += 1
            wave -= 1
    if streak < policy.required_no_novelty_waves:
        gaps.append(Gap(GapReason.SATURATION_NOT_REACHED, universe.universe_id, ResolutionMode.DISCOVER, (f"streak:{streak}", f"required:{policy.required_no_novelty_waves}")))

    ordered = tuple(sorted(gaps, key=_gap_key))
    return DecisionUniverseClosure(
        universe_digest=universe.universe_digest,
        policy_digest=policy.policy_digest,
        no_novelty_streak=streak,
        required_no_novelty_waves=policy.required_no_novelty_waves,
        gaps=ordered,
        status=ClosureStatus.CLOSED if not ordered else ClosureStatus.OPEN,
    )


def compile_evidence_sufficiency(
    universe: DecisionUniverseRevision,
    policy: EvidenceSufficiencyPolicy,
    *,
    claims: tuple[EvidenceClaimBinding, ...] = (),
    resolutions: tuple[ResolutionRecord, ...] = (),
    conflict_dispositions: tuple[ConflictDisposition, ...] = (),
) -> EvidenceSufficiencyReceipt:
    """Evaluate every required universe target against the project policy."""

    if any(not isinstance(item, EvidenceClaimBinding) for item in claims):
        raise TypeError("claims must be EvidenceClaimBinding values")
    if any(not isinstance(item, ResolutionRecord) for item in resolutions):
        raise TypeError("resolutions must be ResolutionRecord values")
    if any(not isinstance(item, ConflictDisposition) for item in conflict_dispositions):
        raise TypeError("conflict_dispositions must be ConflictDisposition values")
    for items, field, key in (
        (claims, "binding ids", lambda item: item.binding_id),
        (resolutions, "resolution ids", lambda item: item.resolution_id),
        (conflict_dispositions, "conflict claim keys", lambda item: item.claim_key),
    ):
        values = [key(item) for item in items]
        if len(values) != len(set(values)):
            raise EvidenceSufficiencyError(f"{field} must be unique")
    if any(item.target_ref not in universe.target_refs for item in (*claims, *resolutions)):
        raise EvidenceSufficiencyError("evidence names a target outside the universe")
    rule_by_target = {item.target_ref: item for item in policy.rules}
    required_targets = {
        item.decision_ref for item in universe.nodes if item.required
    } | {item.edge_ref for item in universe.edges if item.required}
    unknown_rules = set(rule_by_target) - universe.target_refs
    if unknown_rules:
        raise EvidenceSufficiencyError("policy rule names a target outside the universe")

    gaps: list[Gap] = []
    for target_ref in sorted(required_targets - set(rule_by_target)):
        gaps.append(Gap(GapReason.MISSING_POLICY_RULE, target_ref, ResolutionMode.DECIDE))
    dispositions = {item.claim_key: item for item in conflict_dispositions}
    foreign_authorities = {
        item.authority_ref for item in conflict_dispositions
    } - set(policy.allowed_conflict_authority_refs)
    if foreign_authorities:
        raise EvidenceSufficiencyError(
            "conflict disposition authority is not allowed by policy"
        )
    basis_present: set[str] = set()
    missing_basis: set[str] = set()

    for rule in sorted(policy.rules, key=lambda item: item.obligation_id):
        target_claims = [
            item for item in claims
            if item.target_ref == rule.target_ref and item.obligation_id == rule.obligation_id
        ]
        terminal = [
            item for item in resolutions
            if item.target_ref == rule.target_ref and item.mode in rule.allowed_modes
            and item.mode is not ResolutionMode.RETRIEVE
        ]
        if terminal:
            basis_present.add(rule.target_ref)
            continue
        if ResolutionMode.RETRIEVE not in rule.allowed_modes:
            missing_basis.add(rule.target_ref)
            gaps.append(Gap(GapReason.MISSING_BASIS, rule.target_ref, _route(rule), (rule.obligation_id,)))
            continue
        if target_claims:
            basis_present.add(rule.target_ref)
        else:
            missing_basis.add(rule.target_ref)
            gaps.append(Gap(GapReason.MISSING_BASIS, rule.target_ref, _route(rule), (rule.obligation_id,)))
            continue

        selected: list[EvidenceClaimBinding] = []
        for claim_key in sorted({item.claim_key for item in target_claims}):
            rows = [item for item in target_claims if item.claim_key == claim_key]
            positions = {item.position_key for item in rows}
            disposition = dispositions.get(claim_key)
            if len(positions) > 1 and disposition is None:
                gaps.append(Gap(GapReason.UNRESOLVED_CONFLICT, rule.target_ref, ResolutionMode.ADJUDICATE, tuple(sorted(positions))))
                selected.extend(rows)
                continue
            if disposition is not None:
                accepted = set(disposition.accepted_position_keys)
                if not accepted <= positions:
                    raise EvidenceSufficiencyError("conflict disposition selects an unknown position")
                rows = [item for item in rows if item.position_key in accepted]
            selected.extend(rows)

        if len(selected) < rule.minimum_bindings:
            gaps.append(Gap(GapReason.MISSING_BASIS, rule.target_ref, _route(rule), (f"bindings:{len(selected)}", f"required:{rule.minimum_bindings}")))
        family_count = len({item.source_family_ref for item in selected})
        if family_count < rule.minimum_source_families:
            gaps.append(Gap(GapReason.INSUFFICIENT_SOURCE_FAMILIES, rule.target_ref, ResolutionMode.RETRIEVE, (f"families:{family_count}", f"required:{rule.minimum_source_families}")))
        missing_qualifiers = sorted({
            qualifier
            for qualifier in rule.required_qualifiers
            if any(qualifier not in item.qualifiers for item in selected)
        })
        if missing_qualifiers:
            gaps.append(Gap(GapReason.MISSING_QUALIFIER, rule.target_ref, ResolutionMode.RETRIEVE, tuple(missing_qualifiers)))
        if rule.maximum_uncertainty_width is not None:
            mismatched = [
                item.binding_id for item in selected
                if item.uncertainty is not None and (
                    item.uncertainty.unit_ref != rule.uncertainty_unit_ref
                    or item.uncertainty.datum_ref != rule.uncertainty_datum_ref
                )
            ]
            if mismatched:
                gaps.append(Gap(GapReason.UNCERTAINTY_SEMANTICS_MISMATCH, rule.target_ref, _route(rule, uncertainty=True), tuple(sorted(mismatched))))
            outside = [
                item.binding_id for item in selected
                if item.uncertainty is not None
                and item.uncertainty.width > float(rule.maximum_uncertainty_width)
            ]
            if outside:
                gaps.append(Gap(GapReason.UNCERTAINTY_EXCEEDS_TOLERANCE, rule.target_ref, _route(rule, uncertainty=True), tuple(sorted(outside))))

    ordered = tuple(sorted(gaps, key=_gap_key))
    return EvidenceSufficiencyReceipt(
        universe_digest=universe.universe_digest,
        policy_digest=policy.policy_digest,
        basis_present_target_refs=tuple(sorted(basis_present)),
        missing_basis_target_refs=tuple(sorted(missing_basis)),
        gaps=ordered,
        status=SufficiencyStatus.SUFFICIENT if not ordered else SufficiencyStatus.INSUFFICIENT,
    )


def build_research_frontier(
    closure: DecisionUniverseClosure,
    sufficiency: EvidenceSufficiencyReceipt,
) -> ResearchFrontier:
    """Combine closure and sufficiency failures into one deterministic queue."""

    if not isinstance(closure, DecisionUniverseClosure):
        raise TypeError("closure must be DecisionUniverseClosure")
    if not isinstance(sufficiency, EvidenceSufficiencyReceipt):
        raise TypeError("sufficiency must be EvidenceSufficiencyReceipt")
    if (
        closure.universe_digest != sufficiency.universe_digest
        or closure.policy_digest != sufficiency.policy_digest
    ):
        raise EvidenceSufficiencyError("closure and sufficiency inputs cross scope")
    rows: list[tuple[str, Gap]] = [
        *(('closure', item) for item in closure.gaps),
        *(('sufficiency', item) for item in sufficiency.gaps),
    ]
    rows.sort(key=lambda item: (item[0], *_gap_key(item[1])))
    work_items = tuple(
        FrontierWorkItem(
            work_id=f"frontier-{index:03d}",
            origin=origin,
            gap=gap,
        )
        for index, (origin, gap) in enumerate(rows, start=1)
    )
    complete = (
        closure.status is ClosureStatus.CLOSED
        and sufficiency.status is SufficiencyStatus.SUFFICIENT
        and not work_items
    )
    reasons = {item.gap.reason for item in work_items}
    if GapReason.UNRESOLVED_CONFLICT in reasons:
        status = FrontierStatus.HUMAN_REVIEW_REQUIRED
    elif reasons & {
        GapReason.PENDING_UNIVERSE_EXPANSION,
        GapReason.ADOPTED_EXPANSION_NOT_APPLIED,
    }:
        status = FrontierStatus.UNIVERSE_EXPANSION_REQUIRED
    elif complete:
        status = FrontierStatus.COMPLETE
    else:
        status = FrontierStatus.CONTINUE
    return ResearchFrontier(
        universe_digest=closure.universe_digest,
        policy_digest=closure.policy_digest,
        closure_digest=closure.closure_digest,
        sufficiency_receipt_digest=sufficiency.receipt_digest,
        work_items=work_items,
        status=status,
    )
