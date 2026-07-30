"""Open capability discovery without a fixed expert graph."""

from archflow.capabilities.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    DuplicateCapabilityError,
)

__all__ = ["CapabilityRegistry", "CapabilitySpec", "DuplicateCapabilityError"]
