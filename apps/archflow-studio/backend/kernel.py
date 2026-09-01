"""Narrow, read-only facade over the current ArchFlow checkout."""

from __future__ import annotations

from importlib.util import find_spec

from backend.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    StudioHealthSnapshot,
    StudioSessionSnapshot,
)


def _module_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


class ArchFlowKernelFacade:
    """Expose kernel presence without creating a second mutation authority."""

    _CORE_MODULES = (
        "archflow.state.decision_operator",
        "archflow.runtime.design_controller",
        "archflow.runtime.geometry_compiler",
        "archflow.validation",
        "archflow.commit.committer",
    )

    def health(self) -> StudioHealthSnapshot:
        available = all(_module_available(name) for name in self._CORE_MODULES)
        return StudioHealthSnapshot(
            status="ok" if available else "degraded",
            service="archflow-studio-gateway",
            kernel_importable=available,
        )

    def session(self) -> StudioSessionSnapshot:
        return StudioSessionSnapshot()

    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        checked = (
            (
                "typed-design-state",
                "Typed design state",
                "archflow.state.decision_operator",
                "proposal_only",
                "Exact-base, typed state transition contracts.",
            ),
            (
                "design-controller",
                "Design controller",
                "archflow.runtime.design_controller",
                "proposal_only",
                "Phase-aware coordination with explicit authority pauses.",
            ),
            (
                "geometry-compiler",
                "Geometry compiler",
                "archflow.runtime.geometry_compiler",
                "compile_only",
                "Platform-neutral geometry compilation and receipts.",
            ),
            (
                "validator",
                "Validation boundary",
                "archflow.validation",
                "validation_only",
                "Validation remains independent from the browser.",
            ),
            (
                "committer",
                "Canonical committer",
                "archflow.commit.committer",
                "kernel_only",
                "Present in the kernel but not callable by this preview API.",
            ),
            (
                "pascal-preview",
                "Pascal preview adapter",
                "archflow.adapters.pascal_execution",
                "speculative_only",
                "Optional execution adapter; not required by Studio UI.",
            ),
        )
        result = [
            CapabilityDescriptor(
                capability_id=capability_id,
                label=label,
                availability=(
                    CapabilityAvailability.AVAILABLE
                    if _module_available(module_name)
                    else CapabilityAvailability.DISABLED
                ),
                authority=authority,
                detail=detail,
            )
            for capability_id, label, module_name, authority, detail in checked
        ]
        result.extend(
            (
                CapabilityDescriptor(
                    capability_id="intent-provider",
                    label="Conversational intent provider",
                    availability=CapabilityAvailability.RESERVED,
                    authority="proposal_only",
                    detail="Port reserved; no model provider is connected.",
                ),
                CapabilityDescriptor(
                    capability_id="retrieval-provider",
                    label="Stage-aware retrieval provider",
                    availability=CapabilityAvailability.RESERVED,
                    authority="evidence_only",
                    detail="Port reserved; no RAG source is connected.",
                ),
                CapabilityDescriptor(
                    capability_id="rhino-preview",
                    label="Rhino execution adapter",
                    availability=CapabilityAvailability.RESERVED,
                    authority="speculative_only",
                    detail="Direct local 3DM viewing is available in the browser; Rhino execution is not connected.",
                ),
                CapabilityDescriptor(
                    capability_id="studio-canonical-write",
                    label="Studio canonical write",
                    availability=CapabilityAvailability.DISABLED,
                    authority="none",
                    detail="The preview gateway has no canonical write endpoint.",
                ),
            )
        )
        return tuple(result)

