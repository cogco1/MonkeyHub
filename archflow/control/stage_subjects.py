"""Typed, no-authority subject inventory contracts for stage baselines.

The inventory is intentionally persistence-neutral.  It records the exact
semantic component universe mechanically projected by the runtime compiler;
it never grants permission to add, omit, accept, persist, or canonically write
components.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import deterministic_refs, identifier, logical_ref
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
    StageBaselineRole,
)
from archflow.project.refs import BranchRef, ProjectRecordRef


class StageSubjectInventoryError(ValueError):
    """A stage subject inventory is malformed, incomplete, or self-authored."""


class StageSubjectDisposition(StrEnum):
    REQUIRED = "REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _exact_mapping(
    value: object,
    expected: set[str],
    field: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise StageSubjectInventoryError(f"{field} schema drifted")
    return value


def _require_false_authority(payload: Mapping[str, object]) -> None:
    if any(payload.get(field) is not expected for field, expected in _AUTHORITY_FIELDS.items()):
        raise StageSubjectInventoryError("stage subject authority flags changed")


def _record_to_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    try:
        return ProjectRecordRef(
            project_id=payload["project_id"],
            relative_path=payload["relative_path"],
            sha256=payload["sha256"],
            media_type=payload["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise StageSubjectInventoryError(f"{field} is invalid") from exc


def _require_branch_record(
    ref: ProjectRecordRef,
    branch: BranchRef,
    field: str,
) -> None:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError(f"{field} must be a ProjectRecordRef")
    expected_prefix = (
        f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
    )
    if (
        ref.project_id != branch.run.project_id
        or not ref.relative_path.startswith(expected_prefix)
    ):
        raise StageSubjectInventoryError(
            f"{field} is not retained on the exact stage branch"
        )
    if ref.media_type != "application/json":
        raise StageSubjectInventoryError(f"{field} must be a JSON record")


@dataclass(frozen=True, slots=True)
class StageSubjectRoleObligation:
    """One exact baseline-role disposition for one semantic component."""

    role: StageBaselineRole
    disposition: StageSubjectDisposition
    target_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA = "StageSubjectRoleObligation@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, StageBaselineRole):
            raise TypeError("role must be a StageBaselineRole")
        if not isinstance(self.disposition, StageSubjectDisposition):
            raise TypeError("disposition must be a StageSubjectDisposition")
        object.__setattr__(
            self,
            "target_refs",
            deterministic_refs(
                self.target_refs,
                "stage subject obligation target_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(
                self.evidence_refs,
                "stage subject obligation evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(
                self.authority_refs,
                "stage subject obligation authority_refs",
            ),
        )
        if self.disposition is StageSubjectDisposition.REQUIRED:
            if not self.target_refs:
                raise StageSubjectInventoryError(
                    "required stage subject role needs target_refs"
                )
        elif self.target_refs:
            raise StageSubjectInventoryError(
                "not-applicable stage subject role cannot name target_refs"
            )

    @property
    def obligation_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "disposition": self.disposition.value,
            "target_refs": list(self.target_refs),
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "obligation_digest": self.obligation_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageSubjectRoleObligation":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "role",
                "disposition",
                "target_refs",
                "evidence_refs",
                "authority_refs",
                "obligation_digest",
                *_AUTHORITY_FIELDS,
            },
            "stage subject role obligation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageSubjectInventoryError(
                "unsupported stage subject role obligation schema"
            )
        _require_false_authority(payload)
        for field in ("target_refs", "evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            role=StageBaselineRole(payload["role"]),
            disposition=StageSubjectDisposition(payload["disposition"]),
            target_refs=tuple(payload["target_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != dict(payload):
            raise StageSubjectInventoryError(
                "stage subject role obligation digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageSubjectInventoryEntry:
    """Mechanical projection of one exact proposal/index component join."""

    component_id: str
    identity_ref: str
    parent_component_id: str | None
    semantic_kind: str
    component_digest: str
    geometry_object_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    role_obligations: tuple[StageSubjectRoleObligation, ...]

    SCHEMA = "StageSubjectInventoryEntry@1"

    def __post_init__(self) -> None:
        identifier(self.component_id, "stage subject component_id")
        object.__setattr__(
            self,
            "identity_ref",
            logical_ref(self.identity_ref, "stage subject identity_ref"),
        )
        if self.identity_ref != f"design-component:{self.component_id}":
            raise StageSubjectInventoryError(
                "stage subject identity_ref changed component identity"
            )
        if self.parent_component_id is not None:
            identifier(
                self.parent_component_id,
                "stage subject parent_component_id",
            )
            if self.parent_component_id == self.component_id:
                raise StageSubjectInventoryError(
                    "stage subject component cannot parent itself"
                )
        identifier(self.semantic_kind, "stage subject semantic_kind")
        object.__setattr__(
            self,
            "component_digest",
            require_sha256(self.component_digest, "component_digest"),
        )
        for field in ("geometry_object_ids", "binding_ids"):
            values = getattr(self, field)
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            normalized = tuple(sorted(values))
            if len(normalized) != len(set(normalized)):
                raise StageSubjectInventoryError(
                    f"{field} contains duplicate identities"
                )
            for item in normalized:
                identifier(item, field)
            object.__setattr__(self, field, normalized)
        if not isinstance(self.role_obligations, tuple) or any(
            not isinstance(item, StageSubjectRoleObligation)
            for item in self.role_obligations
        ):
            raise TypeError(
                "role_obligations must contain StageSubjectRoleObligation"
            )
        obligations = tuple(
            sorted(self.role_obligations, key=lambda item: item.role.value)
        )
        roles = tuple(item.role for item in obligations)
        if len(roles) != len(set(roles)):
            raise StageSubjectInventoryError(
                "stage subject entry duplicates a baseline role"
            )
        object.__setattr__(self, "role_obligations", obligations)

    def require_level(self, level: StageBaselineLevel) -> None:
        if not isinstance(level, StageBaselineLevel):
            raise TypeError("level must be a StageBaselineLevel")
        expected = tuple(
            sorted(BASELINE_LEVEL_ROLES[level], key=lambda item: item.value)
        )
        actual = tuple(item.role for item in self.role_obligations)
        if actual != expected:
            raise StageSubjectInventoryError(
                "stage subject role obligations do not exactly cover baseline roles"
            )

    @property
    def entry_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "identity_ref": self.identity_ref,
            "parent_component_id": self.parent_component_id,
            "semantic_kind": self.semantic_kind,
            "component_digest": self.component_digest,
            "geometry_object_ids": list(self.geometry_object_ids),
            "binding_ids": list(self.binding_ids),
            "role_obligations": [
                item.to_dict() for item in self.role_obligations
            ],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "entry_digest": self.entry_digest}

    @classmethod
    def from_dict(cls, value: object) -> "StageSubjectInventoryEntry":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "component_id",
                "identity_ref",
                "parent_component_id",
                "semantic_kind",
                "component_digest",
                "geometry_object_ids",
                "binding_ids",
                "role_obligations",
                "entry_digest",
                *_AUTHORITY_FIELDS,
            },
            "stage subject inventory entry",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageSubjectInventoryError(
                "unsupported stage subject inventory entry schema"
            )
        _require_false_authority(payload)
        for field in (
            "geometry_object_ids",
            "binding_ids",
            "role_obligations",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            component_id=payload["component_id"],
            identity_ref=payload["identity_ref"],
            parent_component_id=payload["parent_component_id"],
            semantic_kind=payload["semantic_kind"],
            component_digest=payload["component_digest"],
            geometry_object_ids=tuple(payload["geometry_object_ids"]),
            binding_ids=tuple(payload["binding_ids"]),
            role_obligations=tuple(
                StageSubjectRoleObligation.from_dict(item)
                for item in payload["role_obligations"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageSubjectInventoryError(
                "stage subject inventory entry digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageSubjectInventory:
    """Exact semantic stage universe bound to proposal and component index."""

    inventory_id: str
    branch: BranchRef
    stage_id: str
    stage_subject_ref: str
    stage_subject_digest: str
    baseline_level: StageBaselineLevel
    component_proposal_ref: ProjectRecordRef
    component_proposal_digest: str
    component_index_ref: ProjectRecordRef
    component_index_digest: str
    entries: tuple[StageSubjectInventoryEntry, ...]

    SCHEMA = "StageSubjectInventory@1"

    def __post_init__(self) -> None:
        identifier(self.inventory_id, "stage subject inventory_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "stage subject stage_id")
        object.__setattr__(
            self,
            "stage_subject_ref",
            logical_ref(self.stage_subject_ref, "stage_subject_ref"),
        )
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(self.stage_subject_digest, "stage_subject_digest"),
        )
        if not isinstance(self.baseline_level, StageBaselineLevel):
            raise TypeError("baseline_level must be a StageBaselineLevel")
        _require_branch_record(
            self.component_proposal_ref,
            self.branch,
            "component_proposal_ref",
        )
        _require_branch_record(
            self.component_index_ref,
            self.branch,
            "component_index_ref",
        )
        object.__setattr__(
            self,
            "component_proposal_digest",
            require_sha256(
                self.component_proposal_digest,
                "component_proposal_digest",
            ),
        )
        object.__setattr__(
            self,
            "component_index_digest",
            require_sha256(
                self.component_index_digest,
                "component_index_digest",
            ),
        )
        # ProjectRecordRef.sha256 authenticates the persisted JSON bytes.
        # The two explicit digest fields authenticate the parsed typed content.
        # They are deliberately separate: P036 readback verifies the record
        # SHA, then this contract/compiler verifies the semantic digest.
        if not isinstance(self.entries, tuple) or not self.entries or any(
            not isinstance(item, StageSubjectInventoryEntry)
            for item in self.entries
        ):
            raise TypeError(
                "entries must contain StageSubjectInventoryEntry values"
            )
        entries = tuple(sorted(self.entries, key=lambda item: item.component_id))
        component_ids = tuple(item.component_id for item in entries)
        if len(component_ids) != len(set(component_ids)):
            raise StageSubjectInventoryError(
                "stage subject inventory contains duplicate components"
            )
        component_by_id = {item.component_id: item for item in entries}
        roots = tuple(
            item for item in entries if item.parent_component_id is None
        )
        if len(roots) != 1:
            raise StageSubjectInventoryError(
                "stage subject inventory requires one semantic root"
            )
        for entry in entries:
            entry.require_level(self.baseline_level)
            parent_id = entry.parent_component_id
            if parent_id is not None and parent_id not in component_by_id:
                raise StageSubjectInventoryError(
                    "stage subject inventory component parent is missing"
                )
            seen = {entry.component_id}
            while parent_id is not None:
                if parent_id in seen:
                    raise StageSubjectInventoryError(
                        "stage subject inventory ancestry cycles"
                    )
                seen.add(parent_id)
                parent_id = component_by_id[parent_id].parent_component_id
        object.__setattr__(self, "entries", entries)

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(item.component_id for item in self.entries)

    @property
    def inventory_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "inventory_id": self.inventory_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "stage_subject_digest": self.stage_subject_digest,
            "baseline_level": self.baseline_level.value,
            "component_proposal_ref": _record_to_dict(
                self.component_proposal_ref
            ),
            "component_proposal_digest": self.component_proposal_digest,
            "component_index_ref": _record_to_dict(self.component_index_ref),
            "component_index_digest": self.component_index_digest,
            "entries": [item.to_dict() for item in self.entries],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "inventory_digest": self.inventory_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageSubjectInventory":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "inventory_id",
                "branch",
                "stage_id",
                "stage_subject_ref",
                "stage_subject_digest",
                "baseline_level",
                "component_proposal_ref",
                "component_proposal_digest",
                "component_index_ref",
                "component_index_digest",
                "entries",
                "inventory_digest",
                *_AUTHORITY_FIELDS,
            },
            "stage subject inventory",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageSubjectInventoryError(
                "unsupported stage subject inventory schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["entries"], list):
            raise TypeError("entries must be a list")
        result = cls(
            inventory_id=payload["inventory_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            stage_subject_ref=payload["stage_subject_ref"],
            stage_subject_digest=payload["stage_subject_digest"],
            baseline_level=StageBaselineLevel(payload["baseline_level"]),
            component_proposal_ref=_record_from_dict(
                payload["component_proposal_ref"],
                "component_proposal_ref",
            ),
            component_proposal_digest=payload["component_proposal_digest"],
            component_index_ref=_record_from_dict(
                payload["component_index_ref"],
                "component_index_ref",
            ),
            component_index_digest=payload["component_index_digest"],
            entries=tuple(
                StageSubjectInventoryEntry.from_dict(item)
                for item in payload["entries"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageSubjectInventoryError(
                "stage subject inventory digest changed"
            )
        return result


__all__ = [
    "StageSubjectDisposition",
    "StageSubjectInventory",
    "StageSubjectInventoryEntry",
    "StageSubjectInventoryError",
    "StageSubjectRoleObligation",
]
