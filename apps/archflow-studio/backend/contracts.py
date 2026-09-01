"""Stable, read-only contracts exposed by the first Studio gateway slice."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CapabilityAvailability(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_id: str
    label: str
    availability: CapabilityAvailability
    authority: str
    detail: str

    SCHEMA = "StudioCapability@1"

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.SCHEMA,
            "id": self.capability_id,
            "label": self.label,
            "availability": self.availability.value,
            "authority": self.authority,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StudioHealthSnapshot:
    status: str
    service: str
    kernel_importable: bool

    SCHEMA = "StudioHealth@1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "status": self.status,
            "service": self.service,
            "mode": "preview",
            "readOnly": True,
            "kernelImportable": self.kernel_importable,
            "canonicalWriteAuthority": False,
        }


@dataclass(frozen=True, slots=True)
class StudioSessionSnapshot:
    project_id: str | None = None
    run_id: str | None = None
    branch_id: str | None = None
    stage: int | None = None

    SCHEMA = "StudioSessionSnapshot@1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "mode": "preview",
            "projectId": self.project_id,
            "runId": self.run_id,
            "branchId": self.branch_id,
            "stage": self.stage,
            "projectBound": self.project_id is not None,
            "canonicalWriteAuthority": False,
            "message": "No project is bound in the first read-only slice.",
        }


def capabilities_payload(
    capabilities: tuple[CapabilityDescriptor, ...],
) -> dict[str, Any]:
    return {
        "schema": "StudioCapabilities@1",
        "mode": "preview",
        "capabilities": [item.to_dict() for item in capabilities],
    }

