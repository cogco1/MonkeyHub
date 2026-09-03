"""Persistence-neutral CAD readback and preview contracts.

The readback validator derives its result from exact object records.  A source
summary cannot make the check pass.  Preview values are deliberately a
different, read-only projection type and carry neither readback nor stage
closure authority.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum

from archive.archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier, logical_ref, text
from archflow.project.refs import BranchRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_MAX_ITEMS = 4_096
_BBOX_TOLERANCE = 1e-9


class CadReadbackError(ValueError):
    """A CAD readback profile, observation, or preview is malformed."""


class CadUpAxis(StrEnum):
    X = "X"
    Y = "Y"
    Z = "Z"


def _sorted_refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
    allow_duplicates: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise CadReadbackError(f"{field} has an invalid item count")
    normalized = tuple(logical_ref(item, field) for item in values)
    if not allow_duplicates and len(normalized) != len(set(normalized)):
        raise CadReadbackError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


def _attributes(
    values: object,
    field: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple) or len(values) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded tuple")
    normalized: list[tuple[str, str]] = []
    for item in values:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            raise TypeError(f"{field} entries must be (name, value) text pairs")
        name, value = item
        identifier(name, f"{field} name")
        text(value, f"{field} value", maximum=1_000)
        normalized.append((name, value))
    names = [name for name, _value in normalized]
    if len(names) != len(set(names)):
        raise CadReadbackError(f"{field} contains duplicate names")
    return tuple(sorted(normalized))


def _attributes_to_dict(
    values: tuple[tuple[str, str], ...],
) -> list[dict[str, str]]:
    return [{"name": name, "value": value} for name, value in values]


def _attributes_from_dict(value: object, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    result: list[tuple[str, str]] = []
    for item in value:
        payload = exact_mapping(item, {"name", "value"}, field)
        result.append((payload["name"], payload["value"]))
    return tuple(result)


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return text(value, field, maximum=100)


@dataclass(frozen=True, slots=True)
class CadBoundingBox:
    """Finite axis-aligned bounding box in an explicitly selected frame."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    SCHEMA = "CadBoundingBox@1"

    def __post_init__(self) -> None:
        for field in ("minimum", "maximum"):
            value = getattr(self, field)
            if (
                not isinstance(value, tuple)
                or len(value) != 3
                or any(
                    not isinstance(item, (int, float))
                    or isinstance(item, bool)
                    or not math.isfinite(float(item))
                    for item in value
                )
            ):
                raise CadReadbackError(f"{field} must be a finite XYZ tuple")
            object.__setattr__(
                self,
                field,
                tuple(float(item) for item in value),
            )
        if any(low > high for low, high in zip(self.minimum, self.maximum)):
            raise CadReadbackError("bounding box minimum exceeds maximum")

    def contains(
        self,
        other: "CadBoundingBox",
        *,
        tolerance: float = _BBOX_TOLERANCE,
    ) -> bool:
        if not isinstance(other, CadBoundingBox):
            raise TypeError("other must be CadBoundingBox")
        return all(
            outer_low - tolerance <= inner_low
            and inner_high <= outer_high + tolerance
            for outer_low, outer_high, inner_low, inner_high in zip(
                self.minimum,
                self.maximum,
                other.minimum,
                other.maximum,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadBoundingBox":
        payload = exact_mapping(
            value,
            {"schema", "minimum", "maximum"},
            "CAD bounding box",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CadReadbackError("unsupported CAD bounding-box schema")
        if not isinstance(payload["minimum"], list) or not isinstance(
            payload["maximum"], list
        ):
            raise TypeError("serialized bounding-box coordinates must be lists")
        return cls(
            minimum=tuple(payload["minimum"]),
            maximum=tuple(payload["maximum"]),
        )


@dataclass(frozen=True, slots=True)
class CadObjectRequirement:
    """One exact object/operation pair and its readback obligations."""

    requirement_id: str
    object_ref: str
    operation_ref: str
    required_layer_ref: str
    required_attributes: tuple[tuple[str, str], ...]
    predecessor_envelope: CadBoundingBox | None = None
    host_ref: str | None = None
    host_local_envelope: CadBoundingBox | None = None

    SCHEMA = "CadObjectRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        logical_ref(self.object_ref, "object_ref")
        logical_ref(self.operation_ref, "operation_ref")
        logical_ref(self.required_layer_ref, "required_layer_ref")
        object.__setattr__(
            self,
            "required_attributes",
            _attributes(self.required_attributes, "required_attributes"),
        )
        if self.predecessor_envelope is not None and not isinstance(
            self.predecessor_envelope, CadBoundingBox
        ):
            raise TypeError("predecessor_envelope must be CadBoundingBox or None")
        if self.host_local_envelope is not None and not isinstance(
            self.host_local_envelope, CadBoundingBox
        ):
            raise TypeError("host_local_envelope must be CadBoundingBox or None")
        if self.host_ref is not None:
            logical_ref(self.host_ref, "host_ref")
        if bool(self.predecessor_envelope) == bool(self.host_local_envelope):
            raise CadReadbackError(
                "require exactly one predecessor or host-local envelope"
            )
        if self.host_local_envelope is not None and self.host_ref is None:
            raise CadReadbackError("host-local envelope requires host_ref")
        if self.predecessor_envelope is not None and self.host_ref is not None:
            raise CadReadbackError(
                "predecessor-envelope requirement cannot also name host_ref"
            )

    @property
    def binding_ref(self) -> str:
        return f"cad-binding:{self.requirement_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "object_ref": self.object_ref,
            "operation_ref": self.operation_ref,
            "required_layer_ref": self.required_layer_ref,
            "required_attributes": _attributes_to_dict(
                self.required_attributes
            ),
            "predecessor_envelope": (
                self.predecessor_envelope.to_dict()
                if self.predecessor_envelope is not None
                else None
            ),
            "host_ref": self.host_ref,
            "host_local_envelope": (
                self.host_local_envelope.to_dict()
                if self.host_local_envelope is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadObjectRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "object_ref",
                "operation_ref",
                "required_layer_ref",
                "required_attributes",
                "predecessor_envelope",
                "host_ref",
                "host_local_envelope",
            },
            "CAD object requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CadReadbackError("unsupported CAD object requirement schema")
        predecessor = payload["predecessor_envelope"]
        host_local = payload["host_local_envelope"]
        return cls(
            requirement_id=payload["requirement_id"],
            object_ref=payload["object_ref"],
            operation_ref=payload["operation_ref"],
            required_layer_ref=payload["required_layer_ref"],
            required_attributes=_attributes_from_dict(
                payload["required_attributes"], "required_attributes"
            ),
            predecessor_envelope=(
                CadBoundingBox.from_dict(predecessor)
                if predecessor is not None
                else None
            ),
            host_ref=payload["host_ref"],
            host_local_envelope=(
                CadBoundingBox.from_dict(host_local)
                if host_local is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CadReadbackProfile:
    """Exact project branch, stage, program, and object denominator."""

    profile_id: str
    branch: BranchRef
    stage_id: str
    scope_digest: str
    program_digest: str
    length_unit: str
    up_axis: CadUpAxis
    required_layer_refs: tuple[str, ...]
    object_requirements: tuple[CadObjectRequirement, ...]

    SCHEMA = "CadReadbackProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        object.__setattr__(
            self,
            "program_digest",
            require_sha256(self.program_digest, "program_digest"),
        )
        text(self.length_unit, "length_unit", maximum=100)
        if not isinstance(self.up_axis, CadUpAxis):
            raise TypeError("up_axis must be CadUpAxis")
        object.__setattr__(
            self,
            "required_layer_refs",
            _sorted_refs(self.required_layer_refs, "required_layer_refs"),
        )
        if (
            not isinstance(self.object_requirements, tuple)
            or not self.object_requirements
            or len(self.object_requirements) > _MAX_ITEMS
        ):
            raise CadReadbackError(
                "object_requirements must be a non-empty bounded tuple"
            )
        if any(
            not isinstance(item, CadObjectRequirement)
            for item in self.object_requirements
        ):
            raise TypeError(
                "object_requirements must contain CadObjectRequirement"
            )
        ordered = tuple(
            sorted(
                self.object_requirements,
                key=lambda item: item.requirement_id,
            )
        )
        for values, field in (
            (
                tuple(item.requirement_id for item in ordered),
                "requirement ids",
            ),
            (tuple(item.object_ref for item in ordered), "object refs"),
            (tuple(item.operation_ref for item in ordered), "operation refs"),
        ):
            if len(values) != len(set(values)):
                raise CadReadbackError(f"profile has duplicate {field}")
        missing_layers = {
            item.required_layer_ref for item in ordered
        } - set(self.required_layer_refs)
        if missing_layers:
            raise CadReadbackError(
                "object requirement names a layer outside required_layer_refs"
            )
        object.__setattr__(self, "object_requirements", ordered)
        if len(self.check_denominator) > _MAX_ITEMS:
            raise CadReadbackError("check_denominator exceeds receipt limits")

    @property
    def ref(self) -> str:
        return f"cad-readback-profile:{self.profile_id}"

    @property
    def stage_ref(self) -> str:
        return f"stage:{self.stage_id}"

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(item.binding_ref for item in self.object_requirements)
        )

    @property
    def check_denominator(self) -> tuple[str, ...]:
        """Exact closure denominator for this CAD readback check."""

        refs = {
            self.ref,
            self.stage_ref,
            *self.denominator_refs,
            *self.required_layer_refs,
        }
        for requirement in self.object_requirements:
            refs.update(
                (
                    requirement.object_ref,
                    requirement.operation_ref,
                    requirement.required_layer_ref,
                )
            )
            if requirement.host_ref is not None:
                refs.add(requirement.host_ref)
        return tuple(sorted(refs))

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "scope_digest": self.scope_digest,
            "program_digest": self.program_digest,
            "length_unit": self.length_unit,
            "up_axis": self.up_axis.value,
            "required_layer_refs": list(self.required_layer_refs),
            "object_requirements": [
                item.to_dict() for item in self.object_requirements
            ],
            "check_denominator": list(self.check_denominator),
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadReadbackProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "branch",
                "stage_id",
                "scope_digest",
                "program_digest",
                "length_unit",
                "up_axis",
                "required_layer_refs",
                "object_requirements",
                "check_denominator",
                "stage_acceptance_authority",
                "canonical_write_authority",
            },
            "CAD readback profile",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CadReadbackError("unsupported CAD readback profile schema")
        if not isinstance(payload["required_layer_refs"], list):
            raise TypeError("required_layer_refs must be a list")
        if not isinstance(payload["object_requirements"], list):
            raise TypeError("object_requirements must be a list")
        if not isinstance(payload["check_denominator"], list):
            raise TypeError("check_denominator must be a list")
        result = cls(
            profile_id=payload["profile_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            scope_digest=payload["scope_digest"],
            program_digest=payload["program_digest"],
            length_unit=payload["length_unit"],
            up_axis=CadUpAxis(payload["up_axis"]),
            required_layer_refs=tuple(payload["required_layer_refs"]),
            object_requirements=tuple(
                CadObjectRequirement.from_dict(item)
                for item in payload["object_requirements"]
            ),
        )
        if tuple(payload["check_denominator"]) != result.check_denominator:
            raise CadReadbackError("CAD check denominator drifted")
        return result


@dataclass(frozen=True, slots=True)
class CadObjectReadback:
    """One top-level CAD object record recovered by a read-only inspector."""

    object_ref: str
    operation_ref: str
    layer_ref: str | None
    attributes: tuple[tuple[str, str], ...]
    world_bbox: CadBoundingBox | None = None
    host_ref: str | None = None
    host_local_bbox: CadBoundingBox | None = None

    SCHEMA = "CadObjectReadback@1"

    def __post_init__(self) -> None:
        logical_ref(self.object_ref, "object_ref")
        logical_ref(self.operation_ref, "operation_ref")
        if self.layer_ref is not None:
            logical_ref(self.layer_ref, "layer_ref")
        object.__setattr__(
            self,
            "attributes",
            _attributes(self.attributes, "attributes"),
        )
        for field in ("world_bbox", "host_local_bbox"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, CadBoundingBox):
                raise TypeError(f"{field} must be CadBoundingBox or None")
        if self.host_ref is not None:
            logical_ref(self.host_ref, "host_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_ref": self.object_ref,
            "operation_ref": self.operation_ref,
            "layer_ref": self.layer_ref,
            "attributes": _attributes_to_dict(self.attributes),
            "world_bbox": (
                self.world_bbox.to_dict()
                if self.world_bbox is not None
                else None
            ),
            "host_ref": self.host_ref,
            "host_local_bbox": (
                self.host_local_bbox.to_dict()
                if self.host_local_bbox is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadObjectReadback":
        payload = exact_mapping(
            value,
            {
                "schema",
                "object_ref",
                "operation_ref",
                "layer_ref",
                "attributes",
                "world_bbox",
                "host_ref",
                "host_local_bbox",
            },
            "CAD object readback",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CadReadbackError("unsupported CAD object readback schema")
        world = payload["world_bbox"]
        host_local = payload["host_local_bbox"]
        return cls(
            object_ref=payload["object_ref"],
            operation_ref=payload["operation_ref"],
            layer_ref=payload["layer_ref"],
            attributes=_attributes_from_dict(payload["attributes"], "attributes"),
            world_bbox=(
                CadBoundingBox.from_dict(world) if world is not None else None
            ),
            host_ref=payload["host_ref"],
            host_local_bbox=(
                CadBoundingBox.from_dict(host_local)
                if host_local is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CadReadbackSnapshot:
    """Raw readback facts; optional source summaries have no check authority."""

    project_id: str | None
    branch: BranchRef
    stage_id: str | None
    profile_digest: str
    program_digest: str | None
    length_unit: str | None
    up_axis: CadUpAxis | None
    declared_layer_refs: tuple[str, ...]
    operation_refs: tuple[str, ...]
    objects: tuple[CadObjectReadback, ...]
    reported_summary_passed: bool | None = None

    SCHEMA = "CadReadbackSnapshot@1"

    def __post_init__(self) -> None:
        if self.project_id is not None:
            identifier(self.project_id, "project_id")
        require_exact_branch(self.branch)
        if self.stage_id is not None:
            identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        if self.program_digest is not None:
            object.__setattr__(
                self,
                "program_digest",
                require_sha256(self.program_digest, "program_digest"),
            )
        _optional_text(self.length_unit, "length_unit")
        if self.up_axis is not None and not isinstance(self.up_axis, CadUpAxis):
            raise TypeError("up_axis must be CadUpAxis or None")
        object.__setattr__(
            self,
            "declared_layer_refs",
            _sorted_refs(
                self.declared_layer_refs,
                "declared_layer_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "operation_refs",
            _sorted_refs(
                self.operation_refs,
                "operation_refs",
                allow_empty=True,
                allow_duplicates=True,
            ),
        )
        if not isinstance(self.objects, tuple) or len(self.objects) > _MAX_ITEMS:
            raise CadReadbackError("objects must be a bounded tuple")
        if any(not isinstance(item, CadObjectReadback) for item in self.objects):
            raise TypeError("objects must contain CadObjectReadback")
        object.__setattr__(
            self,
            "objects",
            tuple(
                sorted(
                    self.objects,
                    key=lambda item: (
                        item.object_ref,
                        item.operation_ref,
                        canonical_digest(item.to_dict()),
                    ),
                )
            ),
        )
        if self.reported_summary_passed is not None and type(
            self.reported_summary_passed
        ) is not bool:
            raise TypeError("reported_summary_passed must be bool or None")

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "profile_digest": self.profile_digest,
            "program_digest": self.program_digest,
            "length_unit": self.length_unit,
            "up_axis": self.up_axis.value if self.up_axis is not None else None,
            "declared_layer_refs": list(self.declared_layer_refs),
            "operation_refs": list(self.operation_refs),
            "objects": [item.to_dict() for item in self.objects],
            "reported_summary_passed": self.reported_summary_passed,
            "summary_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadReadbackSnapshot":
        payload = exact_mapping(
            value,
            {
                "schema",
                "project_id",
                "branch",
                "stage_id",
                "profile_digest",
                "program_digest",
                "length_unit",
                "up_axis",
                "declared_layer_refs",
                "operation_refs",
                "objects",
                "reported_summary_passed",
                "summary_authority",
                "canonical_write_authority",
            },
            "CAD readback snapshot",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CadReadbackError("unsupported CAD readback snapshot schema")
        for field in ("declared_layer_refs", "operation_refs", "objects"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        up_axis = payload["up_axis"]
        return cls(
            project_id=payload["project_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            profile_digest=payload["profile_digest"],
            program_digest=payload["program_digest"],
            length_unit=payload["length_unit"],
            up_axis=CadUpAxis(up_axis) if up_axis is not None else None,
            declared_layer_refs=tuple(payload["declared_layer_refs"]),
            operation_refs=tuple(payload["operation_refs"]),
            objects=tuple(
                CadObjectReadback.from_dict(item) for item in payload["objects"]
            ),
            reported_summary_passed=payload["reported_summary_passed"],
        )


@dataclass(frozen=True, slots=True)
class CadPreviewProjection:
    """Read-only visual projection that cannot stand in for CAD readback."""

    preview_id: str
    projection_ref: str
    branch: BranchRef
    stage_id: str
    profile_digest: str
    program_digest: str
    projected_object_refs: tuple[str, ...]

    SCHEMA = "CadPreviewProjection@1"

    def __post_init__(self) -> None:
        identifier(self.preview_id, "preview_id")
        logical_ref(self.projection_ref, "projection_ref")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "stage_id")
        for field in ("profile_digest", "program_digest"):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "projected_object_refs",
            _sorted_refs(
                self.projected_object_refs,
                "projected_object_refs",
                allow_empty=True,
            ),
        )

    @property
    def preview_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "preview_id": self.preview_id,
            "projection_ref": self.projection_ref,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "profile_digest": self.profile_digest,
            "program_digest": self.program_digest,
            "projected_object_refs": list(self.projected_object_refs),
            "read_only_projection": True,
            "readback_authority": False,
            "closure_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CadPreviewProjection":
        payload = exact_mapping(
            value,
            {
                "schema",
                "preview_id",
                "projection_ref",
                "branch",
                "stage_id",
                "profile_digest",
                "program_digest",
                "projected_object_refs",
                "read_only_projection",
                "readback_authority",
                "closure_authority",
                "canonical_write_authority",
            },
            "CAD preview projection",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["read_only_projection"] is not True
        ):
            raise CadReadbackError("unsupported CAD preview projection schema")
        if not isinstance(payload["projected_object_refs"], list):
            raise TypeError("projected_object_refs must be a list")
        return cls(
            preview_id=payload["preview_id"],
            projection_ref=payload["projection_ref"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            profile_digest=payload["profile_digest"],
            program_digest=payload["program_digest"],
            projected_object_refs=tuple(payload["projected_object_refs"]),
        )


def _finding(
    code: str,
    severity: FindingSeverity,
    message: str,
    *subject_refs: str,
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=message,
        subject_refs=tuple(sorted(set(subject_refs))),
    )


def validate_cad_readback(
    profile: CadReadbackProfile,
    snapshot: CadReadbackSnapshot,
    *,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Validate one exact CAD readback without trusting its summary flag."""

    if not isinstance(profile, CadReadbackProfile):
        raise TypeError("profile must be CadReadbackProfile")
    if isinstance(snapshot, CadPreviewProjection):
        raise TypeError("CAD preview cannot substitute for readback or closure")
    if not isinstance(snapshot, CadReadbackSnapshot):
        raise TypeError("snapshot must be CadReadbackSnapshot")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )

    findings: list[CheckFinding] = []

    def global_finding(
        code: str,
        severity: FindingSeverity,
        message: str,
    ) -> None:
        findings.append(
            _finding(code, severity, message, profile.ref, profile.stage_ref)
        )

    project_exact = snapshot.project_id == profile.branch.run.project_id
    branch_exact = snapshot.branch == profile.branch
    stage_exact = snapshot.stage_id == profile.stage_id
    profile_digest_exact = snapshot.profile_digest == profile.profile_digest
    program_digest_exact = snapshot.program_digest == profile.program_digest
    length_unit_exact = snapshot.length_unit == profile.length_unit
    up_axis_exact = snapshot.up_axis is profile.up_axis
    if snapshot.project_id is None:
        global_finding(
            "missing-project-id",
            FindingSeverity.UNKNOWN,
            "CAD readback did not report project identity",
        )
    elif snapshot.project_id != profile.branch.run.project_id:
        global_finding(
            "project-id-mismatch",
            FindingSeverity.ERROR,
            "CAD readback belongs to a different project",
        )
    if not branch_exact:
        global_finding(
            "branch-mismatch",
            FindingSeverity.ERROR,
            "CAD readback crossed the profile's exact branch",
        )
    if snapshot.stage_id is None:
        global_finding(
            "missing-stage-id",
            FindingSeverity.UNKNOWN,
            "CAD readback did not report stage identity",
        )
    elif snapshot.stage_id != profile.stage_id:
        global_finding(
            "stage-id-mismatch",
            FindingSeverity.ERROR,
            "CAD readback belongs to a different design stage",
        )
    if not profile_digest_exact:
        global_finding(
            "profile-digest-mismatch",
            FindingSeverity.ERROR,
            "CAD readback does not name the exact profile digest",
        )
    if snapshot.program_digest is None:
        global_finding(
            "missing-program-digest",
            FindingSeverity.UNKNOWN,
            "CAD readback did not report geometry program digest",
        )
    elif snapshot.program_digest != profile.program_digest:
        global_finding(
            "program-digest-mismatch",
            FindingSeverity.ERROR,
            "CAD readback names a different geometry program digest",
        )
    if snapshot.length_unit is None:
        global_finding(
            "missing-length-unit",
            FindingSeverity.UNKNOWN,
            "CAD readback did not report model units",
        )
    elif snapshot.length_unit != profile.length_unit:
        global_finding(
            "length-unit-mismatch",
            FindingSeverity.ERROR,
            "CAD readback model units differ from the profile",
        )
    if snapshot.up_axis is None:
        global_finding(
            "missing-up-axis",
            FindingSeverity.UNKNOWN,
            "CAD readback did not report the model up axis",
        )
    elif snapshot.up_axis is not profile.up_axis:
        global_finding(
            "up-axis-mismatch",
            FindingSeverity.ERROR,
            "CAD readback model up axis differs from the profile",
        )

    missing_layers = set(profile.required_layer_refs) - set(
        snapshot.declared_layer_refs
    )
    for layer_ref in sorted(missing_layers):
        findings.append(
            _finding(
                "missing-required-layer",
                FindingSeverity.ERROR,
                "CAD readback is missing a required layer",
                profile.stage_ref,
                layer_ref,
            )
        )

    expected_operations = tuple(
        item.operation_ref for item in profile.object_requirements
    )
    operation_counts = Counter(snapshot.operation_refs)
    for operation_ref, count in sorted(operation_counts.items()):
        if count > 1:
            findings.append(
                _finding(
                    "duplicate-operation-ref",
                    FindingSeverity.ERROR,
                    "CAD readback operation denominator contains duplicates: "
                    f"{operation_ref}",
                    profile.ref,
                    profile.stage_ref,
                )
            )
    for operation_ref in sorted(set(expected_operations) - set(operation_counts)):
        findings.append(
            _finding(
                "missing-operation-ref",
                FindingSeverity.ERROR,
                "CAD readback is missing a required operation",
                operation_ref,
            )
        )
    for operation_ref in sorted(set(operation_counts) - set(expected_operations)):
        findings.append(
            _finding(
                "orphan-operation-ref",
                FindingSeverity.ERROR,
                "CAD readback reports an operation outside the profile: "
                f"{operation_ref}",
                profile.ref,
                profile.stage_ref,
            )
        )

    by_object: dict[str, list[CadObjectReadback]] = defaultdict(list)
    by_operation: dict[str, list[CadObjectReadback]] = defaultdict(list)
    for item in snapshot.objects:
        by_object[item.object_ref].append(item)
        by_operation[item.operation_ref].append(item)
    expected_objects = {
        item.object_ref: item for item in profile.object_requirements
    }
    expected_by_operation = {
        item.operation_ref: item for item in profile.object_requirements
    }
    for object_ref in sorted(set(by_object) - set(expected_objects)):
        orphan_operations = {
            item.operation_ref for item in by_object[object_ref]
        }
        for operation_ref in sorted(orphan_operations):
            findings.append(
                _finding(
                    "orphan-object",
                    FindingSeverity.ERROR,
                    "CAD readback object is outside the profile denominator: "
                    f"{object_ref}/{operation_ref}",
                    profile.ref,
                    profile.stage_ref,
                )
            )
    for operation_ref in sorted(set(by_operation) - set(expected_by_operation)):
        findings.append(
            _finding(
                "orphan-object-operation",
                FindingSeverity.ERROR,
                "CAD object resolves to an operation outside the profile: "
                f"{operation_ref}",
                profile.ref,
                profile.stage_ref,
            )
        )

    covered: set[str] = set()
    global_identity_exact = all(
        (
            project_exact,
            branch_exact,
            stage_exact,
            profile_digest_exact,
            program_digest_exact,
            length_unit_exact,
            up_axis_exact,
        )
    )
    if global_identity_exact:
        covered.update((profile.ref, profile.stage_ref))
    covered.update(
        layer_ref
        for layer_ref in profile.required_layer_refs
        if layer_ref in snapshot.declared_layer_refs
    )
    for requirement in profile.object_requirements:
        local_error = False
        local_unknown = False
        operation_registry_exact = (
            operation_counts[requirement.operation_ref] == 1
        )
        operation_object_exact = (
            len(by_operation.get(requirement.operation_ref, [])) == 1
            and by_operation[requirement.operation_ref][0].object_ref
            == requirement.object_ref
        )
        if operation_registry_exact and operation_object_exact:
            covered.add(requirement.operation_ref)
        candidates = by_object.get(requirement.object_ref, [])
        if not candidates:
            local_error = True
            findings.append(
                _finding(
                    "missing-object",
                    FindingSeverity.ERROR,
                    "required CAD object is absent from readback",
                    requirement.binding_ref,
                    requirement.object_ref,
                    requirement.operation_ref,
                )
            )
        elif len(candidates) != 1:
            local_error = True
            findings.append(
                _finding(
                    "duplicate-object",
                    FindingSeverity.ERROR,
                    "required CAD object appears more than once",
                    requirement.binding_ref,
                    requirement.object_ref,
                )
            )
        else:
            observed = candidates[0]
            if observed.operation_ref != requirement.operation_ref:
                local_error = True
                findings.append(
                    _finding(
                        "object-operation-mismatch",
                        FindingSeverity.ERROR,
                        "CAD object does not map to its exact required operation",
                        requirement.binding_ref,
                        requirement.object_ref,
                        requirement.operation_ref,
                    )
                )
            if len(by_operation.get(requirement.operation_ref, [])) != 1:
                local_error = True
                findings.append(
                    _finding(
                        "operation-object-cardinality",
                        FindingSeverity.ERROR,
                        "required operation does not map to exactly one CAD object",
                        requirement.binding_ref,
                        requirement.operation_ref,
                    )
                )
            if observed.layer_ref is None:
                local_unknown = True
                findings.append(
                    _finding(
                        "missing-object-layer",
                        FindingSeverity.UNKNOWN,
                        "CAD object readback did not report its layer",
                        requirement.binding_ref,
                        requirement.object_ref,
                    )
                )
            elif observed.layer_ref != requirement.required_layer_ref:
                local_error = True
                findings.append(
                    _finding(
                        "object-layer-mismatch",
                        FindingSeverity.ERROR,
                        "CAD object is not on its required layer",
                        requirement.binding_ref,
                        requirement.object_ref,
                        requirement.required_layer_ref,
                    )
                )
            observed_attributes = dict(observed.attributes)
            for name, expected_value in requirement.required_attributes:
                if name not in observed_attributes:
                    local_error = True
                    findings.append(
                        _finding(
                            "missing-required-attribute",
                            FindingSeverity.ERROR,
                            f"CAD object is missing required attribute {name}",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )
                elif observed_attributes[name] != expected_value:
                    local_error = True
                    findings.append(
                        _finding(
                            "attribute-value-mismatch",
                            FindingSeverity.ERROR,
                            f"CAD object attribute {name} has the wrong value",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )

            if requirement.predecessor_envelope is not None:
                if observed.world_bbox is None:
                    local_unknown = True
                    findings.append(
                        _finding(
                            "missing-world-bbox",
                            FindingSeverity.UNKNOWN,
                            "CAD readback did not report predecessor-frame bounds",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )
                elif not requirement.predecessor_envelope.contains(
                    observed.world_bbox
                ):
                    local_error = True
                    findings.append(
                        _finding(
                            "predecessor-envelope-exceeded",
                            FindingSeverity.ERROR,
                            "CAD object lies outside its predecessor envelope",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )
            else:
                if observed.host_ref != requirement.host_ref:
                    local_error = True
                    findings.append(
                        _finding(
                            "host-ref-mismatch",
                            FindingSeverity.ERROR,
                            "CAD object names a different host-local frame",
                            requirement.binding_ref,
                            requirement.object_ref,
                            requirement.host_ref,
                        )
                    )
                if observed.host_local_bbox is None:
                    local_unknown = True
                    findings.append(
                        _finding(
                            "missing-host-local-bbox",
                            FindingSeverity.UNKNOWN,
                            "CAD readback did not report host-local bounds",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )
                elif not requirement.host_local_envelope.contains(
                    observed.host_local_bbox
                ):
                    local_error = True
                    findings.append(
                        _finding(
                            "host-local-envelope-exceeded",
                            FindingSeverity.ERROR,
                            "CAD object lies outside its host-local envelope",
                            requirement.binding_ref,
                            requirement.object_ref,
                        )
                    )

            if observed.operation_ref == requirement.operation_ref:
                covered.add(requirement.object_ref)
            if (
                requirement.host_ref is not None
                and observed.host_ref == requirement.host_ref
            ):
                covered.add(requirement.host_ref)

        if (
            global_identity_exact
            and operation_registry_exact
            and requirement.required_layer_ref
            in snapshot.declared_layer_refs
            and not local_error
            and not local_unknown
        ):
            covered.add(requirement.binding_ref)

    findings_tuple = tuple(
        sorted(
            findings,
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    has_error = any(
        item.severity is FindingSeverity.ERROR for item in findings_tuple
    )
    has_unknown = any(
        item.severity is FindingSeverity.UNKNOWN for item in findings_tuple
    )
    status = (
        CheckStatus.FAIL
        if has_error
        else CheckStatus.UNKNOWN
        if has_unknown
        else CheckStatus.PASS
    )
    covered_refs = (
        profile.check_denominator
        if status is CheckStatus.PASS
        else tuple(sorted(covered))
    )

    return CheckReceiptEnvelope(
        check_id="cad-readback",
        checker_id="cad-readback-validator",
        checker_version="1.0.0",
        branch=profile.branch,
        scope_digest=profile.scope_digest,
        subject_refs=profile.check_denominator,
        subject_digest=stage_subject_digest,
        status=status,
        findings=findings_tuple,
        measurements=(
            CheckMeasurement(
                measurement_id="covered-ref-count",
                subject_ref=profile.ref,
                name="covered-ref-count",
                value=len(covered_refs),
                unit_ref="unit:count",
            ),
            CheckMeasurement(
                measurement_id="input-digest",
                subject_ref=profile.ref,
                name="input-digest",
                value=snapshot.snapshot_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="observed-object-count",
                subject_ref=profile.ref,
                name="observed-object-count",
                value=len(snapshot.objects),
                unit_ref="unit:count",
            ),
            CheckMeasurement(
                measurement_id="profile-digest",
                subject_ref=profile.ref,
                name="profile-digest",
                value=profile.profile_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="program-digest",
                subject_ref=profile.ref,
                name="program-digest",
                value=profile.program_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="required-ref-count",
                subject_ref=profile.ref,
                name="required-ref-count",
                value=len(profile.check_denominator),
                unit_ref="unit:count",
            ),
        ),
        coverage_denominator=profile.check_denominator,
        covered_refs=covered_refs,
    )


__all__ = [
    "CadBoundingBox",
    "CadObjectReadback",
    "CadObjectRequirement",
    "CadPreviewProjection",
    "CadReadbackError",
    "CadReadbackProfile",
    "CadReadbackSnapshot",
    "CadUpAxis",
    "validate_cad_readback",
]
