"""Exact visual and functional inputs for generic stage-control baselines.

These wrappers bind independently retained P036 records to one branch, stage,
and stage-subject inventory.  They grant no design, acceptance, persistence, or
canonical-write authority; the baseline compiler independently replays them.
"""

from __future__ import annotations

from dataclasses import dataclass

from archflow.capabilities.visual_inventory import VisualEvidenceInventoryReceipt
from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier
from archflow.control.component_functions import ComponentFunctionLedger
from archflow.control.function_relations import FunctionRelationRequirementSet
from archflow.project.refs import BranchRef, ProjectRecordRef


class StageControlSourceError(ValueError):
    """A visual or functional stage-control source is stale or malformed."""


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _record_payload(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_payload(value: object, field: str) -> ProjectRecordRef:
    payload = exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    return ProjectRecordRef(
        project_id=payload["project_id"],
        relative_path=payload["relative_path"],
        sha256=payload["sha256"],
        media_type=payload["media_type"],
    )


def _require_branch_record(
    ref: ProjectRecordRef,
    branch: BranchRef,
    field: str,
) -> None:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError(f"{field} must be a ProjectRecordRef")
    prefix = f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
    if (
        ref.project_id != branch.run.project_id
        or not ref.relative_path.startswith(prefix)
        or ref.media_type != "application/json"
    ):
        raise StageControlSourceError(
            f"{field} is not an exact JSON record on the requested branch"
        )


@dataclass(frozen=True, slots=True)
class VisualInventoryBaselineSource:
    branch: BranchRef
    stage_id: str
    stage_subject_inventory_digest: str
    inventory_ref: ProjectRecordRef
    inventory: VisualEvidenceInventoryReceipt

    SCHEMA = "VisualInventoryBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "visual baseline stage_id")
        object.__setattr__(
            self,
            "stage_subject_inventory_digest",
            require_sha256(
                self.stage_subject_inventory_digest,
                "stage_subject_inventory_digest",
            ),
        )
        _require_branch_record(self.inventory_ref, self.branch, "inventory_ref")
        if not isinstance(self.inventory, VisualEvidenceInventoryReceipt):
            raise TypeError("inventory must be VisualEvidenceInventoryReceipt")

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "stage_subject_inventory_digest": self.stage_subject_inventory_digest,
            "inventory_ref": _record_payload(self.inventory_ref),
            "inventory": self.inventory.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "VisualInventoryBaselineSource":
        payload = exact_mapping(
            value,
            {
                "schema",
                "branch",
                "stage_id",
                "stage_subject_inventory_digest",
                "inventory_ref",
                "inventory",
                "source_digest",
                *_AUTHORITY_FIELDS,
            },
            "visual inventory baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageControlSourceError(
                "unsupported visual inventory baseline source schema"
            )
        result = cls(
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            stage_subject_inventory_digest=payload[
                "stage_subject_inventory_digest"
            ],
            inventory_ref=_record_from_payload(
                payload["inventory_ref"], "inventory_ref"
            ),
            inventory=VisualEvidenceInventoryReceipt.from_dict(
                payload["inventory"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageControlSourceError(
                "visual inventory baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class ComponentFunctionBaselineSource:
    ledger_ref: ProjectRecordRef
    ledger: ComponentFunctionLedger
    relation_requirements_ref: ProjectRecordRef | None = None
    relation_requirements: FunctionRelationRequirementSet | None = None

    SCHEMA = "ComponentFunctionBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.ledger, ComponentFunctionLedger):
            raise TypeError("ledger must be ComponentFunctionLedger")
        _require_branch_record(self.ledger_ref, self.ledger.branch, "ledger_ref")
        if (self.relation_requirements_ref is None) != (
            self.relation_requirements is None
        ):
            raise StageControlSourceError(
                "relation requirement set record and content must be present together"
            )
        if self.relation_requirements is None:
            return
        if not isinstance(
            self.relation_requirements, FunctionRelationRequirementSet
        ):
            raise TypeError(
                "relation_requirements must be FunctionRelationRequirementSet"
            )
        assert self.relation_requirements_ref is not None
        _require_branch_record(
            self.relation_requirements_ref,
            self.ledger.branch,
            "relation_requirements_ref",
        )
        if (
            self.relation_requirements_ref.relative_path
            == self.ledger_ref.relative_path
        ):
            raise StageControlSourceError(
                "relation requirement set must use an independent P036 record"
            )
        requirement_set = self.relation_requirements
        if (
            requirement_set.branch != self.ledger.branch
            or requirement_set.stage_id != self.ledger.stage_id
            or requirement_set.subject_inventory_digest
            != self.ledger.subject_inventory_digest
        ):
            raise StageControlSourceError(
                "relation requirement set crossed the ledger branch, stage, or inventory"
            )
        if (
            requirement_set.function_ledger_ref != self.ledger.ledger_ref
            or requirement_set.function_ledger_digest != self.ledger.ledger_digest
        ):
            raise StageControlSourceError(
                "relation requirement set is stale against the exact function ledger"
            )

    @property
    def branch(self) -> BranchRef:
        return self.ledger.branch

    @property
    def stage_id(self) -> str:
        return self.ledger.stage_id

    @property
    def stage_subject_inventory_digest(self) -> str:
        return self.ledger.subject_inventory_digest

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "ledger_ref": _record_payload(self.ledger_ref),
            "ledger": self.ledger.to_dict(),
            "relation_requirements_ref": (
                None
                if self.relation_requirements_ref is None
                else _record_payload(self.relation_requirements_ref)
            ),
            "relation_requirements": (
                None
                if self.relation_requirements is None
                else self.relation_requirements.to_dict()
            ),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "ComponentFunctionBaselineSource":
        payload = exact_mapping(
            value,
            {
                "schema",
                "ledger_ref",
                "ledger",
                "relation_requirements_ref",
                "relation_requirements",
                "source_digest",
                *_AUTHORITY_FIELDS,
            },
            "component function baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageControlSourceError(
                "unsupported component function baseline source schema"
            )
        requirement_set_ref = payload["relation_requirements_ref"]
        requirement_set = payload["relation_requirements"]
        result = cls(
            ledger_ref=_record_from_payload(payload["ledger_ref"], "ledger_ref"),
            ledger=ComponentFunctionLedger.from_dict(payload["ledger"]),
            relation_requirements_ref=(
                None
                if requirement_set_ref is None
                else _record_from_payload(
                    requirement_set_ref,
                    "relation_requirements_ref",
                )
            ),
            relation_requirements=(
                None
                if requirement_set is None
                else FunctionRelationRequirementSet.from_dict(requirement_set)
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageControlSourceError(
                "component function baseline source digest changed"
            )
        return result


__all__ = [
    "ComponentFunctionBaselineSource",
    "StageControlSourceError",
    "VisualInventoryBaselineSource",
]
