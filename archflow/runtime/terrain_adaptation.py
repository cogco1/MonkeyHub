"""Compile and validate building-scoped responses to uneven terrain.

The framework preserves alternatives and exact-base evidence.  It never
interprets project-authored strategy codes or picks an outcome on behalf of
the Architect, and it owns no persistence or external-platform handle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from archflow.project import BranchRef, ProjectVersionRef
from archflow.realization.sandbox import HybridScene
from archflow.state import (
    FactEpistemicStatus,
    OperationalMarkovState,
    StateDomain,
    StateFact,
)
from archflow.state.site_context import GroundModelKind, SiteContext


_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 256
_MAX_TEXT = 4_000
_COMPILER_VERSION = "archflow.terrain-adaptation@1"


class TerrainAdaptationError(ValueError):
    """Terrain evidence, alternatives, or realization bindings are invalid."""


@dataclass(frozen=True, slots=True)
class TerrainResponseOption:
    option_id: str
    expert_id: str
    strategy_code: str
    site_context_digest: str
    summary: str
    tradeoffs: tuple[str, ...]
    assumptions: tuple[str, ...]
    resolved_obligation_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "TerrainResponseOption@2"

    def __post_init__(self) -> None:
        _id(self.option_id, "option_id")
        _id(self.expert_id, "expert_id")
        _id(self.strategy_code, "strategy_code")
        _sha(self.site_context_digest, "site_context_digest")
        _text(self.summary, "summary")
        _texts(self.tradeoffs, "tradeoffs")
        _texts(self.assumptions, "assumptions", allow_empty=True)
        _texts(
            self.resolved_obligation_ids,
            "resolved_obligation_ids",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def option_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "option_id": self.option_id,
            "expert_id": self.expert_id,
            "strategy_code": self.strategy_code,
            "site_context_digest": self.site_context_digest,
            "summary": self.summary,
            "tradeoffs": list(self.tradeoffs),
            "assumptions": list(self.assumptions),
            "resolved_obligation_ids": list(
                self.resolved_obligation_ids
            ),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainResponseOption:
        payload = _mapping(value, "terrain option")
        _exact(
            payload,
            {
                "schema",
                "option_id",
                "expert_id",
                "strategy_code",
                "site_context_digest",
                "summary",
                "tradeoffs",
                "assumptions",
                "resolved_obligation_ids",
                "evidence_refs",
            },
            "terrain option",
        )
        if payload["schema"] != cls.SCHEMA:
            raise TerrainAdaptationError("terrain option schema changed")
        return cls(
            option_id=payload["option_id"],
            expert_id=payload["expert_id"],
            strategy_code=payload["strategy_code"],
            site_context_digest=payload["site_context_digest"],
            summary=payload["summary"],
            tradeoffs=_string_tuple(payload["tradeoffs"], "tradeoffs"),
            assumptions=_string_tuple(payload["assumptions"], "assumptions"),
            resolved_obligation_ids=_string_tuple(
                payload["resolved_obligation_ids"],
                "resolved_obligation_ids",
            ),
            evidence_refs=_string_tuple(payload["evidence_refs"], "evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class TerrainSelection:
    selection_id: str
    selected_option_id: str
    authority_id: str
    decision_ref: str
    rationale: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "TerrainSelection@1"

    def __post_init__(self) -> None:
        _id(self.selection_id, "selection_id")
        _id(self.selected_option_id, "selected_option_id")
        _id(self.authority_id, "authority_id")
        _ref(self.decision_ref, "decision_ref")
        _text(self.rationale, "rationale")
        _refs(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "selection_id": self.selection_id,
            "selected_option_id": self.selected_option_id,
            "authority_id": self.authority_id,
            "decision_ref": self.decision_ref,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainSelection:
        payload = _mapping(value, "terrain selection")
        _exact(
            payload,
            {
                "schema",
                "selection_id",
                "selected_option_id",
                "authority_id",
                "decision_ref",
                "rationale",
                "evidence_refs",
            },
            "terrain selection",
        )
        if payload["schema"] != cls.SCHEMA:
            raise TerrainAdaptationError("terrain selection schema changed")
        return cls(
            selection_id=payload["selection_id"],
            selected_option_id=payload["selected_option_id"],
            authority_id=payload["authority_id"],
            decision_ref=payload["decision_ref"],
            rationale=payload["rationale"],
            evidence_refs=_string_tuple(payload["evidence_refs"], "evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class TerrainStateBindingReceipt:
    site_context_digest: str
    predecessor_state_digest: str
    result_state_digest: str
    fact_refs: tuple[str, ...]
    obligation_ids: tuple[str, ...]

    SCHEMA = "TerrainStateBindingReceipt@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.site_context_digest, "site_context_digest"),
            (self.predecessor_state_digest, "predecessor_state_digest"),
            (self.result_state_digest, "result_state_digest"),
        ):
            _sha(value, field)
        _refs(self.fact_refs, "fact_refs")
        _texts(self.obligation_ids, "obligation_ids", allow_empty=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "site_context_digest": self.site_context_digest,
            "predecessor_state_digest": self.predecessor_state_digest,
            "result_state_digest": self.result_state_digest,
            "fact_refs": list(self.fact_refs),
            "obligation_ids": list(self.obligation_ids),
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainStateBindingReceipt:
        payload = _mapping(value, "terrain state binding receipt")
        _exact(
            payload,
            {
                "schema",
                "site_context_digest",
                "predecessor_state_digest",
                "result_state_digest",
                "fact_refs",
                "obligation_ids",
                "canonical_write_authority",
            },
            "terrain state binding receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["canonical_write_authority"] is not False
        ):
            raise TerrainAdaptationError(
                "terrain state binding authority drifted"
            )
        return cls(
            site_context_digest=payload["site_context_digest"],
            predecessor_state_digest=payload["predecessor_state_digest"],
            result_state_digest=payload["result_state_digest"],
            fact_refs=_string_tuple(payload["fact_refs"], "fact_refs"),
            obligation_ids=_string_tuple(
                payload["obligation_ids"],
                "obligation_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class TerrainAdaptationPlan:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    operational_state_digest: str
    site_context_digest: str
    alternatives: tuple[TerrainResponseOption, ...]
    selection: TerrainSelection
    resolved_obligation_ids: tuple[str, ...]
    open_obligation_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "TerrainAdaptationPlan@2"

    def __post_init__(self) -> None:
        _id(self.project_id, "project_id")
        _id(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise TerrainAdaptationError("plan and base belong to different projects")
        _sha(self.operational_state_digest, "operational_state_digest")
        _sha(self.site_context_digest, "site_context_digest")
        if (
            not isinstance(self.alternatives, tuple)
            or len(self.alternatives) < 2
            or any(not isinstance(item, TerrainResponseOption) for item in self.alternatives)
        ):
            raise TerrainAdaptationError("plan requires at least two typed alternatives")
        option_ids = tuple(item.option_id for item in self.alternatives)
        if option_ids != tuple(sorted(set(option_ids))):
            raise TerrainAdaptationError("alternatives require deterministic identities")
        if any(item.site_context_digest != self.site_context_digest for item in self.alternatives):
            raise TerrainAdaptationError("terrain alternative context drifted")
        if not isinstance(self.selection, TerrainSelection):
            raise TypeError("selection must be TerrainSelection")
        if self.selection.selected_option_id not in option_ids:
            raise TerrainAdaptationError("selection names an unknown terrain option")
        _texts(self.resolved_obligation_ids, "resolved_obligation_ids", allow_empty=True)
        _texts(self.open_obligation_ids, "open_obligation_ids", allow_empty=True)
        if set(self.resolved_obligation_ids) & set(self.open_obligation_ids):
            raise TerrainAdaptationError("terrain obligation cannot be open and resolved")
        _refs(self.source_refs, "source_refs")

    @property
    def selected_option(self) -> TerrainResponseOption:
        return next(
            item for item in self.alternatives
            if item.option_id == self.selection.selected_option_id
        )

    @property
    def rejected_option_ids(self) -> tuple[str, ...]:
        return tuple(
            item.option_id for item in self.alternatives
            if item.option_id != self.selection.selected_option_id
        )

    @property
    def plan_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "operational_state_digest": self.operational_state_digest,
            "site_context_digest": self.site_context_digest,
            "alternatives": [item.to_dict() for item in self.alternatives],
            "selection": self.selection.to_dict(),
            "resolved_obligation_ids": list(self.resolved_obligation_ids),
            "open_obligation_ids": list(self.open_obligation_ids),
            "source_refs": list(self.source_refs),
            "rejected_option_ids": list(self.rejected_option_ids),
            "geometry_binding_required": True,
            "automatic_winner": False,
            "canonical_write_authority": False,
            "external_platform_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainAdaptationPlan:
        payload = _mapping(value, "terrain plan")
        expected = {
            "schema", "project_id", "run_id", "base",
            "operational_state_digest", "site_context_digest", "alternatives",
            "selection", "resolved_obligation_ids", "open_obligation_ids",
            "source_refs", "rejected_option_ids", "geometry_binding_required",
            "automatic_winner", "canonical_write_authority",
            "external_platform_authority",
        }
        _exact(payload, expected, "terrain plan")
        if (
            payload["schema"] != cls.SCHEMA
            or payload["geometry_binding_required"] is not True
            or payload["automatic_winner"] is not False
            or payload["canonical_write_authority"] is not False
            or payload["external_platform_authority"] is not False
        ):
            raise TerrainAdaptationError("terrain plan authority or schema drifted")
        alternatives = tuple(
            TerrainResponseOption.from_dict(item)
            for item in _list(payload["alternatives"], "alternatives")
        )
        plan = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            operational_state_digest=payload["operational_state_digest"],
            site_context_digest=payload["site_context_digest"],
            alternatives=alternatives,
            selection=TerrainSelection.from_dict(payload["selection"]),
            resolved_obligation_ids=_string_tuple(payload["resolved_obligation_ids"], "resolved_obligation_ids"),
            open_obligation_ids=_string_tuple(payload["open_obligation_ids"], "open_obligation_ids"),
            source_refs=_string_tuple(payload["source_refs"], "source_refs"),
        )
        if list(plan.rejected_option_ids) != payload["rejected_option_ids"]:
            raise TerrainAdaptationError("rejected option lineage drifted")
        return plan


@dataclass(frozen=True, slots=True, order=True)
class TerrainContactInterface:
    interface_id: str
    supporting_object_id: str
    supported_object_id: str
    maximum_gap: float = 0.01
    maximum_penetration: float = 0.01

    SCHEMA = "TerrainContactInterface@1"

    def __post_init__(self) -> None:
        _id(self.interface_id, "interface_id")
        _id(self.supporting_object_id, "supporting_object_id")
        _id(self.supported_object_id, "supported_object_id")
        for value, field in (
            (self.maximum_gap, "maximum_gap"),
            (self.maximum_penetration, "maximum_penetration"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise TerrainAdaptationError(f"{field} must be a non-negative number")
            object.__setattr__(self, field, float(value))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "interface_id": self.interface_id,
            "supporting_object_id": self.supporting_object_id,
            "supported_object_id": self.supported_object_id,
            "maximum_gap": self.maximum_gap,
            "maximum_penetration": self.maximum_penetration,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainContactInterface:
        payload = _mapping(value, "terrain contact interface")
        _exact(
            payload,
            {
                "schema",
                "interface_id",
                "supporting_object_id",
                "supported_object_id",
                "maximum_gap",
                "maximum_penetration",
            },
            "terrain contact interface",
        )
        if payload["schema"] != cls.SCHEMA:
            raise TerrainAdaptationError(
                "terrain contact interface schema changed"
            )
        return cls(
            interface_id=payload["interface_id"],
            supporting_object_id=payload["supporting_object_id"],
            supported_object_id=payload["supported_object_id"],
            maximum_gap=payload["maximum_gap"],
            maximum_penetration=payload["maximum_penetration"],
        )


@dataclass(frozen=True, slots=True)
class TerrainRelationshipFinding:
    code: str
    interface_id: str
    measured: str

    def __post_init__(self) -> None:
        _id(self.code, "finding code")
        _id(self.interface_id, "interface_id")
        _text(self.measured, "measured")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "interface_id": self.interface_id,
            "measured": self.measured,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainRelationshipFinding:
        payload = _mapping(value, "terrain relationship finding")
        _exact(
            payload,
            {"code", "interface_id", "measured"},
            "terrain relationship finding",
        )
        return cls(
            code=payload["code"],
            interface_id=payload["interface_id"],
            measured=payload["measured"],
        )


@dataclass(frozen=True, slots=True)
class TerrainRelationshipReceipt:
    plan_digest: str
    geometry_program_digest: str
    scene_digest: str
    interfaces: tuple[TerrainContactInterface, ...]
    findings: tuple[TerrainRelationshipFinding, ...]

    SCHEMA = "TerrainRelationshipReceipt@1"

    def __post_init__(self) -> None:
        _sha(self.plan_digest, "plan_digest")
        _sha(self.geometry_program_digest, "geometry_program_digest")
        _sha(self.scene_digest, "scene_digest")
        if not isinstance(self.interfaces, tuple) or not self.interfaces:
            raise TerrainAdaptationError("terrain validation requires interfaces")
        if any(not isinstance(item, TerrainContactInterface) for item in self.interfaces):
            raise TypeError("interfaces contains an invalid item")
        if tuple(item.interface_id for item in self.interfaces) != tuple(
            sorted({item.interface_id for item in self.interfaces})
        ):
            raise TerrainAdaptationError("interfaces require deterministic identities")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, TerrainRelationshipFinding) for item in self.findings
        ):
            raise TypeError("findings contains an invalid item")

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "plan_digest": self.plan_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "scene_digest": self.scene_digest,
            "interfaces": [item.to_dict() for item in self.interfaces],
            "findings": [item.to_dict() for item in self.findings],
            "passed": self.passed,
            "hard_usability_authority": False,
            "canonical_write_authority": False,
            "external_platform_execution": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainRelationshipReceipt:
        payload = _mapping(value, "terrain relationship receipt")
        _exact(
            payload,
            {
                "schema",
                "plan_digest",
                "geometry_program_digest",
                "scene_digest",
                "interfaces",
                "findings",
                "passed",
                "hard_usability_authority",
                "canonical_write_authority",
                "external_platform_execution",
            },
            "terrain relationship receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["hard_usability_authority"] is not False
            or payload["canonical_write_authority"] is not False
            or payload["external_platform_execution"] is not False
        ):
            raise TerrainAdaptationError(
                "terrain relationship authority drifted"
            )
        interfaces = payload["interfaces"]
        findings = payload["findings"]
        if not isinstance(interfaces, list) or not isinstance(findings, list):
            raise TypeError("terrain receipt collections must be lists")
        result = cls(
            plan_digest=payload["plan_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            scene_digest=payload["scene_digest"],
            interfaces=tuple(
                TerrainContactInterface.from_dict(item)
                for item in interfaces
            ),
            findings=tuple(
                TerrainRelationshipFinding.from_dict(item)
                for item in findings
            ),
        )
        if payload["passed"] is not result.passed:
            raise TerrainAdaptationError(
                "terrain relationship verdict drifted"
            )
        return result


@dataclass(frozen=True, slots=True)
class TerrainRetryStopReceipt:
    plan_digest: str
    source_relationship_receipt_digest: str
    code: str = "terrain.retry.unchanged_failed_plan"

    SCHEMA = "TerrainRetryStopReceipt@1"

    def __post_init__(self) -> None:
        _sha(self.plan_digest, "plan_digest")
        _sha(self.source_relationship_receipt_digest, "source receipt digest")
        _id(self.code, "code")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "plan_digest": self.plan_digest,
            "source_relationship_receipt_digest": self.source_relationship_receipt_digest,
            "code": self.code,
            "message": (
                "The unchanged terrain plan already failed its declared contact "
                "interfaces; revise, replace, or leave the response unresolved."
            ),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> TerrainRetryStopReceipt:
        payload = _mapping(value, "terrain retry stop receipt")
        _exact(
            payload,
            {
                "schema",
                "plan_digest",
                "source_relationship_receipt_digest",
                "code",
                "message",
                "design_authority",
                "canonical_write_authority",
            },
            "terrain retry stop receipt",
        )
        expected_message = (
            "The unchanged terrain plan already failed its declared contact "
            "interfaces; revise, replace, or leave the response unresolved."
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["message"] != expected_message
            or payload["design_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise TerrainAdaptationError(
                "terrain retry stop authority or message drifted"
            )
        return cls(
            plan_digest=payload["plan_digest"],
            source_relationship_receipt_digest=payload[
                "source_relationship_receipt_digest"
            ],
            code=payload["code"],
        )


def bind_terrain_context(
    state: OperationalMarkovState,
    site_context: SiteContext,
) -> tuple[OperationalMarkovState, TerrainStateBindingReceipt]:
    """Enter exact-base site facts and obligations into D_v,k."""

    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    _same_scope(state.branch, site_context)
    source_ref = f"site-context:{site_context.context_digest}"
    elevation_range = site_context.ground_model.elevation_range
    site_facts = (
        StateFact(
            StateDomain.PARAMETER,
            "site-context-digest",
            site_context.context_digest,
            source_ref,
            FactEpistemicStatus.DERIVED,
        ),
        StateFact(
            StateDomain.PARAMETER,
            "site-ground-kind",
            site_context.ground_model.kind.value,
            source_ref,
            FactEpistemicStatus.OBSERVED,
        ),
        StateFact(
            StateDomain.PARAMETER,
            "site-elevation-range",
            list(elevation_range) if elevation_range is not None else "unknown",
            source_ref,
            FactEpistemicStatus.OBSERVED,
        ),
    )
    facts = {item.ref: item for item in state.facts}
    for item in site_facts:
        current = facts.get(item.ref)
        if current is not None and current != item:
            raise TerrainAdaptationError(f"terrain fact conflicts with state: {item.ref}")
        facts[item.ref] = item
    obligations = {item.obligation_id: item for item in state.obligations}
    for item in site_context.obligations:
        current = obligations.get(item.obligation_id)
        if current is not None and current != item:
            raise TerrainAdaptationError(
                f"terrain obligation conflicts with state: {item.obligation_id}"
            )
        obligations[item.obligation_id] = item
    result = OperationalMarkovState(
        branch=state.branch,
        compiler_version=_COMPILER_VERSION,
        phase="terrain-adaptation",
        facts=tuple(sorted(facts.values(), key=lambda item: item.ref)),
        bindings=state.bindings,
        locks=state.locks,
        commitments=state.commitments,
        obligations=tuple(sorted(obligations.values(), key=lambda item: item.obligation_id)),
        dependencies=state.dependencies,
        invalidated_refs=state.invalidated_refs,
        evidence_refs=tuple(sorted({*state.evidence_refs, *site_context.evidence_refs, source_ref})),
    )
    receipt = TerrainStateBindingReceipt(
        site_context_digest=site_context.context_digest,
        predecessor_state_digest=state.state_digest,
        result_state_digest=result.state_digest,
        fact_refs=tuple(item.ref for item in site_facts),
        obligation_ids=tuple(item.obligation_id for item in site_context.obligations),
    )
    return result, receipt


def compile_terrain_adaptation(
    state: OperationalMarkovState,
    site_context: SiteContext,
    alternatives: Sequence[TerrainResponseOption],
    selection: TerrainSelection,
) -> TerrainAdaptationPlan:
    """Compile an explicit Architect selection without selecting a response."""

    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    if site_context.ground_model.kind is not GroundModelKind.UNEVEN:
        raise TerrainAdaptationError("terrain adaptation requires observed uneven ground")
    _same_scope(state.branch, site_context)
    if state.value_for_ref("fact:parameter:site-context-digest") != site_context.context_digest:
        raise TerrainAdaptationError("operational state lacks the exact site context")
    options = tuple(sorted(tuple(alternatives), key=lambda item: item.option_id))
    if any(not isinstance(item, TerrainResponseOption) for item in options):
        raise TypeError("alternatives contains an invalid item")
    if not isinstance(selection, TerrainSelection):
        raise TypeError("selection must be TerrainSelection")
    context_ref = f"site-context:{site_context.context_digest}"
    if any(
        item.site_context_digest != site_context.context_digest
        or context_ref not in item.evidence_refs
        for item in options
    ):
        raise TerrainAdaptationError("terrain alternative lost exact context evidence")
    selected = next(
        (item for item in options if item.option_id == selection.selected_option_id),
        None,
    )
    if selected is None:
        raise TerrainAdaptationError("selection names an unknown terrain option")
    ground_obligation_id = "resolve.site.ground-response"
    all_ids = tuple(item.obligation_id for item in site_context.obligations)
    if ground_obligation_id not in all_ids:
        raise TerrainAdaptationError("state lacks the uneven-ground response obligation")
    resolved = tuple(sorted(selected.resolved_obligation_ids))
    unknown_resolutions = set(resolved) - set(all_ids)
    if unknown_resolutions:
        raise TerrainAdaptationError(
            "terrain option resolves unknown site obligations: "
            f"{sorted(unknown_resolutions)}"
        )
    open_ids = tuple(item for item in all_ids if item not in resolved)
    return TerrainAdaptationPlan(
        project_id=site_context.project_id,
        run_id=site_context.run_id,
        base=site_context.base,
        operational_state_digest=state.state_digest,
        site_context_digest=site_context.context_digest,
        alternatives=options,
        selection=selection,
        resolved_obligation_ids=resolved,
        open_obligation_ids=open_ids,
        source_refs=tuple(
            sorted(
                {
                    *state.evidence_refs,
                    *selection.evidence_refs,
                    *(ref for item in options for ref in item.evidence_refs),
                    selection.decision_ref,
                    context_ref,
                }
            )
        ),
    )


def validate_terrain_relationship(
    plan: TerrainAdaptationPlan,
    scene: HybridScene,
    interfaces: Sequence[TerrainContactInterface],
) -> TerrainRelationshipReceipt:
    """Validate declared support contacts from exact neutral geometry bounds."""

    if not isinstance(plan, TerrainAdaptationPlan):
        raise TypeError("plan must be TerrainAdaptationPlan")
    if not isinstance(scene, HybridScene):
        raise TypeError("scene must be HybridScene")
    if (scene.project_id, scene.run_id, scene.base) != (
        plan.project_id,
        plan.run_id,
        plan.base,
    ):
        raise TerrainAdaptationError("terrain plan and scene scope disagree")
    ordered = tuple(sorted(tuple(interfaces), key=lambda item: item.interface_id))
    if any(not isinstance(item, TerrainContactInterface) for item in ordered):
        raise TypeError("interfaces contains an invalid item")
    objects = {item.object_id: item for item in scene.objects}
    findings: list[TerrainRelationshipFinding] = []
    for interface in ordered:
        supporting = objects.get(interface.supporting_object_id)
        supported = objects.get(interface.supported_object_id)
        if supporting is None or supported is None or not supporting.physical or not supported.physical:
            findings.append(
                TerrainRelationshipFinding(
                    "terrain.interface.object_missing",
                    interface.interface_id,
                    "one or both declared physical objects are absent",
                )
            )
            continue
        overlap_x = min(supporting.bounds.maximum[0], supported.bounds.maximum[0]) - max(
            supporting.bounds.minimum[0], supported.bounds.minimum[0]
        )
        overlap_z = min(supporting.bounds.maximum[2], supported.bounds.maximum[2]) - max(
            supporting.bounds.minimum[2], supported.bounds.minimum[2]
        )
        if overlap_x <= 0 or overlap_z <= 0:
            findings.append(
                TerrainRelationshipFinding(
                    "terrain.interface.horizontal_miss",
                    interface.interface_id,
                    f"overlap_x={overlap_x:g}, overlap_z={overlap_z:g}",
                )
            )
            continue
        if supporting.bounds.minimum[1] > supported.bounds.minimum[1]:
            findings.append(
                TerrainRelationshipFinding(
                    "terrain.interface.supporting_object_above",
                    interface.interface_id,
                    "supporting object begins above the supported object",
                )
            )
            continue
        gap = supported.bounds.minimum[1] - supporting.bounds.maximum[1]
        if gap > interface.maximum_gap:
            findings.append(
                TerrainRelationshipFinding(
                    "terrain.interface.vertical_gap",
                    interface.interface_id,
                    f"gap={gap:g} > maximum_gap={interface.maximum_gap:g}",
                )
            )
        elif -gap > interface.maximum_penetration:
            findings.append(
                TerrainRelationshipFinding(
                    "terrain.interface.excessive_penetration",
                    interface.interface_id,
                    f"penetration={-gap:g} > maximum_penetration={interface.maximum_penetration:g}",
                )
            )
    return TerrainRelationshipReceipt(
        plan_digest=plan.plan_digest,
        geometry_program_digest=scene.geometry_program_digest,
        scene_digest=scene.scene_digest,
        interfaces=ordered,
        findings=tuple(findings),
    )


def guard_unchanged_terrain_retry(
    plan: TerrainAdaptationPlan,
    previous: TerrainRelationshipReceipt,
) -> TerrainRetryStopReceipt | None:
    if not isinstance(plan, TerrainAdaptationPlan):
        raise TypeError("plan must be TerrainAdaptationPlan")
    if not isinstance(previous, TerrainRelationshipReceipt):
        raise TypeError("previous must be TerrainRelationshipReceipt")
    if previous.passed or previous.plan_digest != plan.plan_digest:
        return None
    return TerrainRetryStopReceipt(
        plan_digest=plan.plan_digest,
        source_relationship_receipt_digest=previous.receipt_digest,
    )


def _same_scope(branch: BranchRef, site_context: SiteContext) -> None:
    if (
        branch.run.project_id != site_context.project_id
        or branch.run.run_id != site_context.run_id
        or branch.run.base != site_context.base
    ):
        raise TerrainAdaptationError("terrain context does not match exact branch base")


def _base_dict(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise TerrainAdaptationError(f"{field} must be bounded non-empty text")
    if any(character.isspace() for character in value):
        raise TerrainAdaptationError(f"{field} cannot contain whitespace")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise TerrainAdaptationError(f"{field} must be bounded non-empty text")
    return value


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value.lower())
    ):
        raise TerrainAdaptationError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _ref(value: object, field: str) -> str:
    _text(value, field)
    if ":" not in value:
        raise TerrainAdaptationError(f"{field} must be a logical reference")
    return value


def _texts(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS or (not value and not allow_empty):
        raise TerrainAdaptationError(f"{field} must be a bounded tuple")
    for item in value:
        _text(item, field)
    if len(value) != len(set(value)):
        raise TerrainAdaptationError(f"{field} contains duplicates")
    return value


def _refs(value: object, field: str) -> tuple[str, ...]:
    _texts(value, field)
    for item in value:
        _ref(item, field)
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded list")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must contain strings")
    return tuple(values)


def _exact(value: Mapping[str, object], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise TerrainAdaptationError(f"{label} schema drifted")


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
