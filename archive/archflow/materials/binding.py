"""Exact, branch-bound material assignment verification.

The contracts in this module describe what a caller requires and what a
read-only inspection observed.  They neither choose materials nor write
project state.  A passing receipt means that every semantic subject in the
profile's explicit denominator resolves through one exact ledger component,
one exact material intent, and the declared geometry-object references.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from archive.archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier, logical_ref
from archive.archflow.materials.ledger import MaterialLedger
from archflow.project.refs import BranchRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_LEDGER_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")
_MAX_ITEMS = 4_096


class MaterialBindingError(ValueError):
    """A material-binding profile or observation is malformed."""


class MaterialBindingResolution(StrEnum):
    """Whether an inspector resolved the complete binding chain."""

    RESOLVED = "resolved"
    UNKNOWN = "unknown"


def _ledger_id(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if _LEDGER_ID.fullmatch(value) is None:
        raise MaterialBindingError(f"{field} must be a kebab identifier")
    return value


def _sorted_refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise MaterialBindingError(f"{field} has an invalid item count")
    normalized = tuple(logical_ref(value, field) for value in values)
    if len(normalized) != len(set(normalized)):
        raise MaterialBindingError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class MaterialBindingRequirement:
    """One exact semantic-subject to material-and-geometry obligation."""

    requirement_id: str
    semantic_subject_ref: str
    ledger_component_id: str
    material_id: str
    material_intent_ref: str
    geometry_object_refs: tuple[str, ...]

    SCHEMA = "MaterialBindingRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        logical_ref(self.semantic_subject_ref, "semantic_subject_ref")
        _ledger_id(self.ledger_component_id, "ledger_component_id")
        _ledger_id(self.material_id, "material_id")
        logical_ref(self.material_intent_ref, "material_intent_ref")
        object.__setattr__(
            self,
            "geometry_object_refs",
            _sorted_refs(self.geometry_object_refs, "geometry_object_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "semantic_subject_ref": self.semantic_subject_ref,
            "ledger_component_id": self.ledger_component_id,
            "material_id": self.material_id,
            "material_intent_ref": self.material_intent_ref,
            "geometry_object_refs": list(self.geometry_object_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialBindingRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "semantic_subject_ref",
                "ledger_component_id",
                "material_id",
                "material_intent_ref",
                "geometry_object_refs",
            },
            "material binding requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialBindingError("unsupported material requirement schema")
        if not isinstance(payload["geometry_object_refs"], list):
            raise TypeError("geometry_object_refs must be a list")
        return cls(
            requirement_id=payload["requirement_id"],
            semantic_subject_ref=payload["semantic_subject_ref"],
            ledger_component_id=payload["ledger_component_id"],
            material_id=payload["material_id"],
            material_intent_ref=payload["material_intent_ref"],
            geometry_object_refs=tuple(payload["geometry_object_refs"]),
        )


@dataclass(frozen=True, slots=True)
class MaterialBindingProfile:
    """Immutable branch/scope-bound denominator for material verification."""

    profile_id: str
    branch: BranchRef
    scope_digest: str
    ledger_ref: str
    ledger_digest: str
    requirements: tuple[MaterialBindingRequirement, ...]

    SCHEMA = "MaterialBindingProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        logical_ref(self.ledger_ref, "ledger_ref")
        object.__setattr__(
            self,
            "ledger_digest",
            require_sha256(self.ledger_digest, "ledger_digest"),
        )
        if (
            not isinstance(self.requirements, tuple)
            or not self.requirements
            or len(self.requirements) > _MAX_ITEMS
        ):
            raise MaterialBindingError(
                "requirements must be a non-empty bounded tuple"
            )
        if any(
            not isinstance(item, MaterialBindingRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain MaterialBindingRequirement"
            )
        ordered = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        requirement_ids = tuple(item.requirement_id for item in ordered)
        subjects = tuple(item.semantic_subject_ref for item in ordered)
        components = tuple(item.ledger_component_id for item in ordered)
        geometry_refs = tuple(
            ref for item in ordered for ref in item.geometry_object_refs
        )
        for values, field in (
            (requirement_ids, "requirement ids"),
            (subjects, "semantic subjects"),
            (components, "ledger components"),
            (geometry_refs, "geometry object refs"),
        ):
            if len(values) != len(set(values)):
                raise MaterialBindingError(f"profile has duplicate {field}")
        object.__setattr__(self, "requirements", ordered)
        if len(self.check_denominator) > _MAX_ITEMS:
            raise MaterialBindingError("check_denominator exceeds receipt limits")

    @property
    def ref(self) -> str:
        return f"material-binding-profile:{self.profile_id}"

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(item.semantic_subject_ref for item in self.requirements)
        )

    @property
    def check_denominator(self) -> tuple[str, ...]:
        """Exact closure denominator for this material check."""

        refs = {self.ref, self.ledger_ref, *self.denominator_refs}
        for requirement in self.requirements:
            refs.add(_component_ref(requirement.ledger_component_id))
            refs.add(requirement.material_intent_ref)
            refs.update(requirement.geometry_object_refs)
        return tuple(sorted(refs))

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "ledger_ref": self.ledger_ref,
            "ledger_digest": self.ledger_digest,
            "requirements": [item.to_dict() for item in self.requirements],
            "check_denominator": list(self.check_denominator),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialBindingProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "branch",
                "scope_digest",
                "ledger_ref",
                "ledger_digest",
                "requirements",
                "check_denominator",
                "design_authority",
                "canonical_write_authority",
            },
            "material binding profile",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialBindingError("unsupported material profile schema")
        if not isinstance(payload["requirements"], list):
            raise TypeError("requirements must be a list")
        if not isinstance(payload["check_denominator"], list):
            raise TypeError("check_denominator must be a list")
        result = cls(
            profile_id=payload["profile_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            ledger_ref=payload["ledger_ref"],
            ledger_digest=payload["ledger_digest"],
            requirements=tuple(
                MaterialBindingRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
        )
        if tuple(payload["check_denominator"]) != result.check_denominator:
            raise MaterialBindingError("material check denominator drifted")
        return result


@dataclass(frozen=True, slots=True)
class MaterialBindingObservation:
    """One read-only observation of a semantic material binding."""

    semantic_subject_ref: str
    resolution: MaterialBindingResolution
    ledger_component_id: str | None
    material_intent_ref: str | None
    geometry_object_refs: tuple[str, ...]

    SCHEMA = "MaterialBindingObservation@1"

    def __post_init__(self) -> None:
        logical_ref(self.semantic_subject_ref, "semantic_subject_ref")
        if not isinstance(self.resolution, MaterialBindingResolution):
            raise TypeError("resolution must be MaterialBindingResolution")
        if self.ledger_component_id is not None:
            _ledger_id(self.ledger_component_id, "ledger_component_id")
        if self.material_intent_ref is not None:
            logical_ref(self.material_intent_ref, "material_intent_ref")
        object.__setattr__(
            self,
            "geometry_object_refs",
            _sorted_refs(
                self.geometry_object_refs,
                "geometry_object_refs",
                allow_empty=True,
            ),
        )
        if self.resolution is MaterialBindingResolution.RESOLVED and (
            self.ledger_component_id is None
            or self.material_intent_ref is None
            or not self.geometry_object_refs
        ):
            raise MaterialBindingError(
                "resolved observation requires component, intent, and geometry refs"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "semantic_subject_ref": self.semantic_subject_ref,
            "resolution": self.resolution.value,
            "ledger_component_id": self.ledger_component_id,
            "material_intent_ref": self.material_intent_ref,
            "geometry_object_refs": list(self.geometry_object_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialBindingObservation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "semantic_subject_ref",
                "resolution",
                "ledger_component_id",
                "material_intent_ref",
                "geometry_object_refs",
            },
            "material binding observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialBindingError("unsupported material observation schema")
        if not isinstance(payload["geometry_object_refs"], list):
            raise TypeError("geometry_object_refs must be a list")
        return cls(
            semantic_subject_ref=payload["semantic_subject_ref"],
            resolution=MaterialBindingResolution(payload["resolution"]),
            ledger_component_id=payload["ledger_component_id"],
            material_intent_ref=payload["material_intent_ref"],
            geometry_object_refs=tuple(payload["geometry_object_refs"]),
        )


@dataclass(frozen=True, slots=True)
class MaterialBindingSnapshot:
    """Branch-bound material inspection input; its summary is non-authoritative."""

    branch: BranchRef
    scope_digest: str
    profile_digest: str
    ledger_ref: str
    ledger_digest: str
    observations: tuple[MaterialBindingObservation, ...]
    reported_summary_passed: bool | None = None

    SCHEMA = "MaterialBindingSnapshot@1"

    def __post_init__(self) -> None:
        require_exact_branch(self.branch)
        for field in ("scope_digest", "profile_digest", "ledger_digest"):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        logical_ref(self.ledger_ref, "ledger_ref")
        if not isinstance(self.observations, tuple) or len(
            self.observations
        ) > _MAX_ITEMS:
            raise MaterialBindingError("observations must be a bounded tuple")
        if any(
            not isinstance(item, MaterialBindingObservation)
            for item in self.observations
        ):
            raise TypeError(
                "observations must contain MaterialBindingObservation"
            )
        object.__setattr__(
            self,
            "observations",
            tuple(
                sorted(
                    self.observations,
                    key=lambda item: (
                        item.semantic_subject_ref,
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
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "profile_digest": self.profile_digest,
            "ledger_ref": self.ledger_ref,
            "ledger_digest": self.ledger_digest,
            "observations": [item.to_dict() for item in self.observations],
            "reported_summary_passed": self.reported_summary_passed,
            "summary_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialBindingSnapshot":
        payload = exact_mapping(
            value,
            {
                "schema",
                "branch",
                "scope_digest",
                "profile_digest",
                "ledger_ref",
                "ledger_digest",
                "observations",
                "reported_summary_passed",
                "summary_authority",
                "canonical_write_authority",
            },
            "material binding snapshot",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialBindingError("unsupported material snapshot schema")
        if not isinstance(payload["observations"], list):
            raise TypeError("observations must be a list")
        return cls(
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            profile_digest=payload["profile_digest"],
            ledger_ref=payload["ledger_ref"],
            ledger_digest=payload["ledger_digest"],
            observations=tuple(
                MaterialBindingObservation.from_dict(item)
                for item in payload["observations"]
            ),
            reported_summary_passed=payload["reported_summary_passed"],
        )


def _component_ref(component_id: str) -> str:
    return f"ledger-component:{component_id}"


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


def validate_material_bindings(
    profile: MaterialBindingProfile,
    ledger: MaterialLedger,
    snapshot: MaterialBindingSnapshot,
    *,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Derive a fail-closed receipt from exact profile, ledger, and readback."""

    if not isinstance(profile, MaterialBindingProfile):
        raise TypeError("profile must be MaterialBindingProfile")
    if not isinstance(ledger, MaterialLedger):
        raise TypeError("ledger must be MaterialLedger")
    if not isinstance(snapshot, MaterialBindingSnapshot):
        raise TypeError("snapshot must be MaterialBindingSnapshot")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )

    findings: list[CheckFinding] = []

    def global_failure(code: str, message: str, subject_ref: str) -> None:
        findings.append(
            _finding(code, FindingSeverity.ERROR, message, subject_ref)
        )

    branch_exact = snapshot.branch == profile.branch
    scope_exact = snapshot.scope_digest == profile.scope_digest
    profile_digest_exact = snapshot.profile_digest == profile.profile_digest
    if not branch_exact:
        global_failure(
            "branch-mismatch",
            "material snapshot crossed the profile's exact branch",
            profile.ref,
        )
    if not scope_exact:
        global_failure(
            "scope-digest-mismatch",
            "material snapshot crossed the profile scope digest",
            profile.ref,
        )
    if not profile_digest_exact:
        global_failure(
            "profile-digest-mismatch",
            "material snapshot does not name the exact profile digest",
            profile.ref,
        )
    ledger_ref_exact = snapshot.ledger_ref == profile.ledger_ref
    if not ledger_ref_exact:
        global_failure(
            "ledger-ref-mismatch",
            "material snapshot names a different ledger ref",
            profile.ledger_ref,
        )

    actual_ledger_digest = canonical_digest(ledger.to_dict())
    profile_ledger_digest_exact = profile.ledger_digest == actual_ledger_digest
    snapshot_ledger_digest_exact = snapshot.ledger_digest == actual_ledger_digest
    if not profile_ledger_digest_exact:
        global_failure(
            "profile-ledger-digest-mismatch",
            "profile ledger digest does not match the supplied ledger",
            profile.ledger_ref,
        )
    if not snapshot_ledger_digest_exact:
        global_failure(
            "snapshot-ledger-digest-mismatch",
            "snapshot ledger digest does not match the supplied ledger",
            profile.ledger_ref,
        )

    requirements = {
        item.semantic_subject_ref: item for item in profile.requirements
    }
    expected_components = {
        item.ledger_component_id for item in profile.requirements
    }
    observations: dict[str, list[MaterialBindingObservation]] = defaultdict(list)
    for observation in snapshot.observations:
        observations[observation.semantic_subject_ref].append(observation)

    for component_id, _material_id in ledger.assignments:
        if component_id not in expected_components:
            findings.append(
                _finding(
                    "orphan-ledger-assignment",
                    FindingSeverity.ERROR,
                    "ledger assignment has no requirement in this exact profile: "
                    f"{component_id}",
                    profile.ref,
                    profile.ledger_ref,
                )
            )
    for subject_ref in sorted(set(observations) - set(requirements)):
        findings.append(
            _finding(
                "orphan-binding-observation",
                FindingSeverity.ERROR,
                "material observation is outside the profile denominator: "
                f"{subject_ref}",
                profile.ref,
            )
        )

    intent_ids = {item.material_id for item in ledger.intents}
    covered: set[str] = set()
    profile_exact = branch_exact and scope_exact and profile_digest_exact
    ledger_exact = (
        ledger_ref_exact
        and profile_ledger_digest_exact
        and snapshot_ledger_digest_exact
    )
    if profile_exact:
        covered.add(profile.ref)
    if ledger_exact:
        covered.add(profile.ledger_ref)
    for subject_ref in profile.denominator_refs:
        requirement = requirements[subject_ref]
        subject_errors = False
        subject_unknown = False
        assigned_material = ledger.material_of(requirement.ledger_component_id)
        ledger_assignment_exact = assigned_material == requirement.material_id
        if assigned_material is None:
            subject_errors = True
            findings.append(
                _finding(
                    "unassigned-ledger-component",
                    FindingSeverity.ERROR,
                    "required ledger component has no material assignment",
                    subject_ref,
                    _component_ref(requirement.ledger_component_id),
                )
            )
        elif assigned_material != requirement.material_id:
            subject_errors = True
            findings.append(
                _finding(
                    "material-assignment-mismatch",
                    FindingSeverity.ERROR,
                    "ledger assignment does not match the required material intent",
                    subject_ref,
                    requirement.material_intent_ref,
                )
            )
        intent_declared = requirement.material_id in intent_ids
        if not intent_declared:
            subject_errors = True
            findings.append(
                _finding(
                    "missing-material-intent",
                    FindingSeverity.ERROR,
                    "required material intent is absent from the supplied ledger",
                    subject_ref,
                    requirement.material_intent_ref,
                )
            )

        candidates = observations.get(subject_ref, [])
        observation_component_exact = False
        observation_intent_exact = False
        observation_geometry_exact = False
        if not candidates:
            subject_errors = True
            findings.append(
                _finding(
                    "missing-binding-observation",
                    FindingSeverity.ERROR,
                    "required semantic subject has no binding observation",
                    subject_ref,
                )
            )
        elif len(candidates) != 1:
            subject_errors = True
            findings.append(
                _finding(
                    "duplicate-binding-observation",
                    FindingSeverity.ERROR,
                    "required semantic subject has multiple binding observations",
                    subject_ref,
                )
            )
        else:
            observation = candidates[0]
            if observation.resolution is MaterialBindingResolution.UNKNOWN:
                subject_unknown = True
                findings.append(
                    _finding(
                        "unknown-material-binding",
                        FindingSeverity.UNKNOWN,
                        "inspector could not resolve the complete material binding",
                        subject_ref,
                    )
                )
            else:
                observation_component_exact = (
                    observation.ledger_component_id
                    == requirement.ledger_component_id
                )
                observation_intent_exact = (
                    observation.material_intent_ref
                    == requirement.material_intent_ref
                )
                observation_geometry_exact = (
                    observation.geometry_object_refs
                    == requirement.geometry_object_refs
                )
                if (
                    not observation_component_exact
                ):
                    subject_errors = True
                    findings.append(
                        _finding(
                            "ledger-component-mismatch",
                            FindingSeverity.ERROR,
                            "observation resolves to a different ledger component",
                            subject_ref,
                        )
                    )
                if (
                    not observation_intent_exact
                ):
                    subject_errors = True
                    findings.append(
                        _finding(
                            "material-intent-ref-mismatch",
                            FindingSeverity.ERROR,
                            "observation resolves to a different material intent ref",
                            subject_ref,
                            requirement.material_intent_ref,
                        )
                    )
                if (
                    not observation_geometry_exact
                ):
                    subject_errors = True
                    findings.append(
                        _finding(
                            "geometry-object-refs-mismatch",
                            FindingSeverity.ERROR,
                            "observation does not match the exact geometry refs",
                            subject_ref,
                        )
                    )
        component_ref = _component_ref(requirement.ledger_component_id)
        if ledger_assignment_exact and observation_component_exact:
            covered.add(component_ref)
        if (
            ledger_assignment_exact
            and intent_declared
            and observation_intent_exact
        ):
            covered.add(requirement.material_intent_ref)
        if observation_geometry_exact:
            covered.update(requirement.geometry_object_refs)
        if (
            profile_exact
            and ledger_exact
            and not subject_errors
            and not subject_unknown
        ):
            covered.add(subject_ref)

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
        check_id="material-binding",
        checker_id="material-binding-validator",
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
                measurement_id="ledger-digest",
                subject_ref=profile.ref,
                name="ledger-digest",
                value=actual_ledger_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="profile-digest",
                subject_ref=profile.ref,
                name="profile-digest",
                value=profile.profile_digest,
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
    "MaterialBindingError",
    "MaterialBindingObservation",
    "MaterialBindingProfile",
    "MaterialBindingRequirement",
    "MaterialBindingResolution",
    "MaterialBindingSnapshot",
    "validate_material_bindings",
]
