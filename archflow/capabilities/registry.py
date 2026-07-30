"""Minimal capability metadata and discovery contract."""

from __future__ import annotations

from dataclasses import dataclass


class DuplicateCapabilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    capability_id: str
    kind: str
    adapter: str
    description: str
    side_effects: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.capability_id, "capability_id"),
            (self.kind, "kind"),
            (self.adapter, "adapter"),
            (self.description, "description"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")


class CapabilityRegistry:
    """Registry order is not a workflow or invocation schedule."""

    def __init__(self) -> None:
        self._items: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if spec.capability_id in self._items:
            raise DuplicateCapabilityError(spec.capability_id)
        self._items[spec.capability_id] = spec

    def get(self, capability_id: str) -> CapabilitySpec:
        return self._items[capability_id]

    def discover(self, *, kind: str | None = None) -> tuple[CapabilitySpec, ...]:
        items = self._items.values()
        if kind is not None:
            items = (item for item in items if item.kind == kind)
        return tuple(sorted(items, key=lambda item: item.capability_id))
