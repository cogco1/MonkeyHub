"""Phase metadata and order-neutral discovery for read-only experts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from archflow.capabilities.experts import (
    ExpertRegistry,
    ExpertSnapshot,
    ExpertSpec,
)
from archflow.state.design_maturity import (
    DELIVERABLE_ROLE_PHASE,
    DeliverableRole,
    DesignPhase,
)


class PhaseCapabilityError(ValueError):
    """Expert capability metadata is missing or invalid for this phase."""


@dataclass(frozen=True, slots=True)
class PhaseExpertMetadata:
    """Admissible phases for one registered, read-only capability."""

    expert_id: str
    allowed_phases: frozenset[DesignPhase]
    advisory_deliverable_roles: frozenset[DeliverableRole] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.expert_id, str) or not self.expert_id.strip():
            raise ValueError("expert_id must be non-empty text")
        if not isinstance(self.allowed_phases, frozenset) or not (
            self.allowed_phases
        ):
            raise ValueError(
                "allowed_phases must be a non-empty frozenset"
            )
        if any(
            not isinstance(item, DesignPhase)
            for item in self.allowed_phases
        ):
            raise TypeError(
                "allowed_phases must contain DesignPhase values"
            )
        if not isinstance(self.advisory_deliverable_roles, frozenset):
            raise TypeError(
                "advisory_deliverable_roles must be a frozenset"
            )
        if any(
            not isinstance(item, DeliverableRole)
            for item in self.advisory_deliverable_roles
        ):
            raise TypeError(
                "advisory_deliverable_roles must contain DeliverableRole"
            )
        inadmissible = {
            role
            for role in self.advisory_deliverable_roles
            if DELIVERABLE_ROLE_PHASE[role] not in self.allowed_phases
        }
        if inadmissible:
            raise PhaseCapabilityError(
                "expert cannot declare deliverables from a disallowed phase"
            )


def discover_phase_experts(
    registry: ExpertRegistry,
    snapshot: ExpertSnapshot,
    *,
    phase: DesignPhase,
    metadata: Mapping[str, PhaseExpertMetadata],
) -> tuple[ExpertSpec, ...]:
    """Intersect current obligations, evidence, phase, and capability metadata."""

    if not isinstance(registry, ExpertRegistry):
        raise TypeError("registry must be an ExpertRegistry")
    if not isinstance(snapshot, ExpertSnapshot):
        raise TypeError("snapshot must be an ExpertSnapshot")
    if not isinstance(phase, DesignPhase):
        raise TypeError("phase must be a DesignPhase")
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")

    discovered = registry.discover(snapshot)
    result: list[ExpertSpec] = []
    for spec in discovered:
        phase_metadata = metadata.get(spec.expert_id)
        if phase_metadata is None:
            continue
        if not isinstance(phase_metadata, PhaseExpertMetadata):
            raise TypeError(
                "metadata values must be PhaseExpertMetadata"
            )
        if phase_metadata.expert_id != spec.expert_id:
            raise PhaseCapabilityError(
                "phase metadata key and expert_id disagree"
            )
        if phase in phase_metadata.allowed_phases:
            result.append(spec)
    return tuple(sorted(result, key=lambda item: item.expert_id))


def validate_architect_selected_expert_order(
    selected_expert_ids: Sequence[str],
    discovered: Sequence[ExpertSpec],
) -> tuple[str, ...]:
    """Validate membership while preserving the Architect's chosen order."""

    if isinstance(selected_expert_ids, (str, bytes)) or not isinstance(
        selected_expert_ids,
        Sequence,
    ):
        raise TypeError("selected_expert_ids must be a sequence")
    if isinstance(discovered, (str, bytes)) or not isinstance(
        discovered,
        Sequence,
    ):
        raise TypeError("discovered must be a sequence")
    selected = tuple(selected_expert_ids)
    if any(
        not isinstance(item, str) or not item.strip() for item in selected
    ):
        raise ValueError("selected expert ids must be non-empty text")
    if len(selected) != len(set(selected)):
        raise PhaseCapabilityError(
            "selected expert order contains duplicates"
        )
    available = {
        item.expert_id
        for item in discovered
        if isinstance(item, ExpertSpec)
    }
    if len(available) != len(discovered):
        raise TypeError("discovered must contain ExpertSpec values")
    unavailable = set(selected) - available
    if unavailable:
        raise PhaseCapabilityError(
            f"selected experts were not discovered: {sorted(unavailable)}"
        )
    return selected
