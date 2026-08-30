"""Immutable binding for one authorized stage-requirement profile.

The binding identifies the exact branch-local profile record consumed by the
controller.  It does not grant design, stage-acceptance, persistence, or
canonical-write authority; it only prevents a caller from swapping in an
unrecorded profile digest while presenting an otherwise satisfied closure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    require_identifier,
)
from archflow.state.operational_state import require_logical_ref


class StageProfileBindingError(ValueError):
    """A stage profile binding is malformed, stale, or authority-ambiguous."""


def _record_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(value: object, field: str) -> ProjectRecordRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "relative_path",
        "sha256",
        "media_type",
    }:
        raise StageProfileBindingError(f"{field} schema drifted")
    return ProjectRecordRef(
        project_id=value["project_id"],
        relative_path=value["relative_path"],
        sha256=value["sha256"],
        media_type=value["media_type"],
    )


@dataclass(frozen=True, slots=True)
class StageRequirementProfileBinding:
    """Exact P036 record and authority chain selected for one stage exit."""

    binding_id: str
    profile_id: str
    profile_digest: str
    branch: BranchRef
    stage_id: str
    stage_subject_ref: str
    subject_digest: str
    profile_ref: ProjectRecordRef
    stage_subject_inventory_ref: ProjectRecordRef | None
    stage_subject_inventory_digest: str | None
    authority_refs: tuple[ProjectRecordRef, ...]
    _legacy_read_only: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    SCHEMA = "StageRequirementProfileBinding@2"
    LEGACY_SCHEMA = "StageRequirementProfileBinding@1"

    def __post_init__(self) -> None:
        self._validate(allow_legacy_inventory=False)

    def _validate(self, *, allow_legacy_inventory: bool) -> None:
        require_identifier(self.binding_id, "binding_id")
        require_identifier(self.profile_id, "profile_id")
        require_identifier(self.stage_id, "stage_id")
        require_exact_branch(self.branch)
        require_logical_ref(self.stage_subject_ref, "stage_subject_ref")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        object.__setattr__(
            self,
            "subject_digest",
            require_sha256(self.subject_digest, "subject_digest"),
        )
        if not isinstance(self.profile_ref, ProjectRecordRef):
            raise TypeError("profile_ref must be a ProjectRecordRef")
        project_id = self.branch.run.project_id
        if self.profile_ref.project_id != project_id:
            raise StageProfileBindingError(
                "profile_ref belongs to another project"
            )
        expected_prefix = (
            f"runs/{self.branch.run.run_id}/branches/"
            f"{self.branch.branch_id}/records/"
        )
        if not self.profile_ref.relative_path.startswith(expected_prefix):
            raise StageProfileBindingError(
                "profile_ref is not retained on the exact branch"
            )
        if self.profile_ref.sha256 != self.profile_digest:
            raise StageProfileBindingError(
                "profile_ref digest does not identify the bound profile"
            )
        if allow_legacy_inventory:
            if (
                self.stage_subject_inventory_ref is not None
                or self.stage_subject_inventory_digest is not None
            ):
                raise StageProfileBindingError(
                    "legacy stage profile binding cannot carry an inventory"
                )
        else:
            if not isinstance(
                self.stage_subject_inventory_ref,
                ProjectRecordRef,
            ):
                raise TypeError(
                    "stage_subject_inventory_ref must be a ProjectRecordRef"
                )
            object.__setattr__(
                self,
                "stage_subject_inventory_digest",
                require_sha256(
                    self.stage_subject_inventory_digest,
                    "stage_subject_inventory_digest",
                ),
            )
            if self.stage_subject_inventory_ref.project_id != project_id:
                raise StageProfileBindingError(
                    "stage_subject_inventory_ref belongs to another project"
                )
            if not self.stage_subject_inventory_ref.relative_path.startswith(
                expected_prefix
            ):
                raise StageProfileBindingError(
                    "stage_subject_inventory_ref is not retained on the exact branch"
                )
            # The record ref authenticates the complete persisted JSON payload,
            # while ``stage_subject_inventory_digest`` authenticates the typed
            # semantic content inside that payload.  StageSubjectInventory@1
            # includes its semantic digest in ``to_dict()``, so these two
            # digests are intentionally distinct and must be checked at their
            # respective P036 readback boundaries instead of compared here.
        if (
            not isinstance(self.authority_refs, tuple)
            or not self.authority_refs
            or any(
                not isinstance(item, ProjectRecordRef)
                for item in self.authority_refs
            )
        ):
            raise StageProfileBindingError(
                "authority_refs must contain retained ProjectRecordRef values"
            )
        if any(item.project_id != project_id for item in self.authority_refs):
            raise StageProfileBindingError(
                "authority_refs belong to another project"
            )
        identities = tuple(
            (item.uri, item.sha256, item.media_type)
            for item in self.authority_refs
        )
        if len(identities) != len(set(identities)):
            raise StageProfileBindingError("authority_refs contain duplicates")
        object.__setattr__(
            self,
            "authority_refs",
            tuple(
                sorted(
                    self.authority_refs,
                    key=lambda item: (item.uri, item.sha256, item.media_type),
                )
            ),
        )

    @property
    def binding_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def is_legacy_read_only(self) -> bool:
        return self._legacy_read_only

    def _content_dict(self) -> dict[str, object]:
        payload = {
            "schema": (
                self.LEGACY_SCHEMA if self._legacy_read_only else self.SCHEMA
            ),
            "binding_id": self.binding_id,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "subject_digest": self.subject_digest,
            "profile_ref": _record_dict(self.profile_ref),
            "authority_refs": [
                _record_dict(item) for item in self.authority_refs
            ],
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }
        if not self._legacy_read_only:
            assert isinstance(
                self.stage_subject_inventory_ref,
                ProjectRecordRef,
            )
            assert isinstance(self.stage_subject_inventory_digest, str)
            payload["stage_subject_inventory_ref"] = _record_dict(
                self.stage_subject_inventory_ref
            )
            payload["stage_subject_inventory_digest"] = (
                self.stage_subject_inventory_digest
            )
        return payload

    def to_dict(self) -> dict[str, object]:
        if self._legacy_read_only:
            raise StageProfileBindingError(
                "legacy stage profile binding is read-only"
            )
        return self._content_dict()

    @classmethod
    def from_dict(cls, value: object) -> "StageRequirementProfileBinding":
        common = {
            "schema",
            "binding_id",
            "profile_id",
            "profile_digest",
            "branch",
            "stage_id",
            "stage_subject_ref",
            "subject_digest",
            "profile_ref",
            "authority_refs",
            "stage_acceptance_authority",
            "canonical_write_authority",
        }
        if not isinstance(value, Mapping):
            raise StageProfileBindingError(
                "unsupported stage profile binding schema"
            )
        schema = value.get("schema")
        if schema == cls.SCHEMA:
            expected = common | {
                "stage_subject_inventory_ref",
                "stage_subject_inventory_digest",
            }
        elif schema == cls.LEGACY_SCHEMA:
            expected = common
        else:
            raise StageProfileBindingError(
                "unsupported stage profile binding schema"
            )
        if set(value) != expected:
            raise StageProfileBindingError(
                "unsupported stage profile binding schema"
            )
        if (
            value.get("stage_acceptance_authority") is not False
            or value.get("canonical_write_authority") is not False
        ):
            raise StageProfileBindingError(
                "stage profile binding authority flags changed"
            )
        raw_authorities = value.get("authority_refs")
        if not isinstance(raw_authorities, list):
            raise TypeError("authority_refs must be a list")
        common_values = {
            "binding_id": value["binding_id"],
            "profile_id": value["profile_id"],
            "profile_digest": value["profile_digest"],
            "branch": branch_ref_from_dict(value["branch"]),
            "stage_id": value["stage_id"],
            "stage_subject_ref": value["stage_subject_ref"],
            "subject_digest": value["subject_digest"],
            "profile_ref": _record_from_dict(
                value["profile_ref"], "profile_ref"
            ),
            "authority_refs": tuple(
                _record_from_dict(item, "authority_ref")
                for item in raw_authorities
            ),
        }
        if schema == cls.SCHEMA:
            return cls(
                **common_values,
                stage_subject_inventory_ref=_record_from_dict(
                    value["stage_subject_inventory_ref"],
                    "stage_subject_inventory_ref",
                ),
                stage_subject_inventory_digest=value[
                    "stage_subject_inventory_digest"
                ],
            )

        legacy = object.__new__(cls)
        for name, item in common_values.items():
            object.__setattr__(legacy, name, item)
        object.__setattr__(legacy, "stage_subject_inventory_ref", None)
        object.__setattr__(legacy, "stage_subject_inventory_digest", None)
        object.__setattr__(legacy, "_legacy_read_only", True)
        legacy._validate(allow_legacy_inventory=True)
        return legacy


__all__ = [
    "StageProfileBindingError",
    "StageRequirementProfileBinding",
]
