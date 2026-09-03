"""Presentation-only projection of component functional-control status.

The projection gives viewers a deterministic diagnostic colour without
changing authored materials or granting readback, stage-acceptance, or
canonical-write authority.  It is deliberately independent of every CAD
adapter: consumers may render the receipt, but cannot treat it as geometry or
design evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from archflow.contracts.canonical import canonical_digest
from archflow.contracts.fields import (
    deterministic_identifiers,
    exact_mapping,
    identifier,
    logical_ref,
)


class FunctionDiagnosticError(ValueError):
    """A functional diagnostic entry or projection is unsafe or malformed."""


class FunctionStatus(StrEnum):
    """Control status exposed by the presentation projection."""

    FUNCTION_ORPHAN = "FUNCTION_ORPHAN"
    OPEN = "OPEN"
    FAIL = "FAIL"
    SATISFIED = "SATISFIED"


class FunctionDiagnosticColor(StrEnum):
    """The fixed, framework-owned diagnostic colours."""

    FUNCTION_ORPHAN = "#FF00FF"
    OPEN = "#FFBF00"
    FAIL = "#FF0000"


FUNCTION_DIAGNOSTIC_PALETTE = MappingProxyType(
    {
        FunctionStatus.FUNCTION_ORPHAN: FunctionDiagnosticColor.FUNCTION_ORPHAN,
        FunctionStatus.OPEN: FunctionDiagnosticColor.OPEN,
        FunctionStatus.FAIL: FunctionDiagnosticColor.FAIL,
        FunctionStatus.SATISFIED: None,
    }
)

NO_FUNCTION_CONTRACT = "NONE"
PRESENTATION_ONLY = "PRESENTATION_ONLY"

_AUTHORITY_FIELDS = {
    "material_override": False,
    "readback_authority": False,
    "stage_acceptance_authority": False,
    "canonical_write_authority": False,
}


def _expected_color(status: FunctionStatus) -> str | None:
    color = FUNCTION_DIAGNOSTIC_PALETTE[status]
    return None if color is None else color.value


def _validate_entry_policy(entry: "FunctionDiagnosticEntry") -> None:
    expected_color = _expected_color(entry.status)
    if entry.diagnostic_color != expected_color:
        if entry.status is FunctionStatus.SATISFIED:
            raise FunctionDiagnosticError(
                "SATISFIED functional diagnostics cannot carry a colour"
            )
        raise FunctionDiagnosticError(
            "functional diagnostic colour is not the fixed palette value"
        )
    if entry.status is FunctionStatus.FUNCTION_ORPHAN:
        if entry.function_contract_ref != NO_FUNCTION_CONTRACT:
            raise FunctionDiagnosticError(
                "FUNCTION_ORPHAN contract must be NONE"
            )
    elif entry.function_contract_ref == NO_FUNCTION_CONTRACT:
        raise FunctionDiagnosticError(
            "only FUNCTION_ORPHAN may use the NONE contract"
        )


@dataclass(frozen=True, slots=True)
class FunctionDiagnosticEntry:
    """One component and the geometry objects it owns in the diagnostic view."""

    component_ref: str
    geometry_object_ids: tuple[str, ...]
    function_ledger_ref: str
    function_contract_ref: str
    stage_claim_ref: str
    status: FunctionStatus
    diagnostic_color: str | None

    SCHEMA = "FunctionDiagnosticEntry@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "function diagnostic component_ref"),
        )
        object.__setattr__(
            self,
            "geometry_object_ids",
            deterministic_identifiers(
                self.geometry_object_ids,
                "function diagnostic geometry_object_ids",
            ),
        )
        object.__setattr__(
            self,
            "function_ledger_ref",
            logical_ref(
                self.function_ledger_ref,
                "function diagnostic function_ledger_ref",
            ),
        )
        if self.function_contract_ref != NO_FUNCTION_CONTRACT:
            object.__setattr__(
                self,
                "function_contract_ref",
                logical_ref(
                    self.function_contract_ref,
                    "function diagnostic function_contract_ref",
                ),
            )
        object.__setattr__(
            self,
            "stage_claim_ref",
            logical_ref(
                self.stage_claim_ref,
                "function diagnostic stage_claim_ref",
            ),
        )
        if not isinstance(self.status, FunctionStatus):
            raise TypeError("status must be a FunctionStatus")
        if self.diagnostic_color is not None and not isinstance(
            self.diagnostic_color, str
        ):
            raise TypeError("diagnostic_color must be text or None")
        _validate_entry_policy(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_ref": self.component_ref,
            "geometry_object_ids": list(self.geometry_object_ids),
            "function_ledger_ref": self.function_ledger_ref,
            "function_contract_ref": self.function_contract_ref,
            "stage_claim_ref": self.stage_claim_ref,
            "status": self.status.value,
            "diagnostic_color": self.diagnostic_color,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionDiagnosticEntry":
        payload = exact_mapping(
            value,
            {
                "schema",
                "component_ref",
                "geometry_object_ids",
                "function_ledger_ref",
                "function_contract_ref",
                "stage_claim_ref",
                "status",
                "diagnostic_color",
            },
            "function diagnostic entry",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionDiagnosticError(
                "unsupported functional diagnostic entry schema"
            )
        object_ids = payload["geometry_object_ids"]
        if not isinstance(object_ids, list):
            raise TypeError("geometry_object_ids must be a list")
        return cls(
            component_ref=payload["component_ref"],
            geometry_object_ids=tuple(object_ids),
            function_ledger_ref=payload["function_ledger_ref"],
            function_contract_ref=payload["function_contract_ref"],
            stage_claim_ref=payload["stage_claim_ref"],
            status=FunctionStatus(payload["status"]),
            diagnostic_color=payload["diagnostic_color"],
        )


@dataclass(frozen=True, slots=True)
class FunctionDiagnosticProjection:
    """Deterministic presentation receipt with no design-system authority."""

    projection_id: str
    entries: tuple[FunctionDiagnosticEntry, ...]

    SCHEMA = "FunctionDiagnosticProjection@1"

    def __post_init__(self) -> None:
        identifier(self.projection_id, "function diagnostic projection_id")
        if not isinstance(self.entries, tuple) or not self.entries:
            raise FunctionDiagnosticError(
                "functional diagnostic projection entries must not be empty"
            )
        if any(not isinstance(item, FunctionDiagnosticEntry) for item in self.entries):
            raise TypeError(
                "entries must contain FunctionDiagnosticEntry values"
            )
        ordered = tuple(sorted(self.entries, key=lambda item: item.component_ref))
        if ordered != self.entries:
            raise FunctionDiagnosticError(
                "functional diagnostic entries must be sorted by component_ref"
            )
        _validate_projection_ownership(self.entries)

    @property
    def projection_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "projection_id": self.projection_id,
            "projection_mode": PRESENTATION_ONLY,
            "entries": [item.to_dict() for item in self.entries],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "projection_digest": self.projection_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionDiagnosticProjection":
        payload = exact_mapping(
            value,
            {
                "schema",
                "projection_id",
                "projection_mode",
                "entries",
                "projection_digest",
                *_AUTHORITY_FIELDS,
            },
            "function diagnostic projection",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionDiagnosticError(
                "unsupported functional diagnostic projection schema"
            )
        if payload["projection_mode"] != PRESENTATION_ONLY:
            raise FunctionDiagnosticError(
                "functional diagnostic projection is not presentation-only"
            )
        if payload["material_override"] is not False:
            raise FunctionDiagnosticError(
                "functional diagnostic projection overrode material"
            )
        entries = payload["entries"]
        if not isinstance(entries, list):
            raise TypeError("functional diagnostic entries must be a list")
        result = cls(
            projection_id=payload["projection_id"],
            entries=tuple(FunctionDiagnosticEntry.from_dict(item) for item in entries),
        )
        if payload["projection_digest"] != result.projection_digest:
            raise FunctionDiagnosticError(
                "functional diagnostic projection digest changed"
            )
        return result


def _validate_projection_ownership(
    entries: tuple[FunctionDiagnosticEntry, ...],
) -> None:
    components: set[str] = set()
    object_owners: dict[str, str] = {}
    for entry in entries:
        _validate_entry_policy(entry)
        if entry.component_ref in components:
            raise FunctionDiagnosticError(
                "duplicate functional diagnostic component ownership"
            )
        components.add(entry.component_ref)
        for object_id in entry.geometry_object_ids:
            owner = object_owners.get(object_id)
            if owner is not None:
                raise FunctionDiagnosticError(
                    "duplicate functional diagnostic object ownership: "
                    f"{object_id} belongs to {owner} and {entry.component_ref}"
                )
            object_owners[object_id] = entry.component_ref


def compile_function_diagnostic_projection(
    *,
    projection_id: str,
    entries: tuple[FunctionDiagnosticEntry, ...],
) -> FunctionDiagnosticProjection:
    """Compile an authority-free, deterministic presentation projection."""

    if not isinstance(entries, tuple):
        raise TypeError("entries must be a tuple")
    if any(not isinstance(item, FunctionDiagnosticEntry) for item in entries):
        raise TypeError("entries must contain FunctionDiagnosticEntry values")
    _validate_projection_ownership(entries)
    return FunctionDiagnosticProjection(
        projection_id=projection_id,
        entries=tuple(sorted(entries, key=lambda item: item.component_ref)),
    )


__all__ = [
    "FUNCTION_DIAGNOSTIC_PALETTE",
    "NO_FUNCTION_CONTRACT",
    "FunctionDiagnosticColor",
    "FunctionDiagnosticEntry",
    "FunctionDiagnosticError",
    "FunctionDiagnosticProjection",
    "FunctionStatus",
    "compile_function_diagnostic_projection",
]
