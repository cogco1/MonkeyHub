"""Project-derived architectural usability over exact neutral artifacts.

The framework owns comparison mechanics only.  Criterion vocabulary, values,
subjects, thresholds, and provenance are supplied by one exact project.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.realization.sandbox import (
    HybridScene,
    RealizationStatus,
    SandboxRealizationReceipt,
)
from archflow.compilers.geometry import CompiledGeometryProgram
from archflow.state.design_brief import (
    BriefClaimKind,
    DesignBrief,
)
from archflow.state.developed_design import DevelopedDesignState


class ArchitecturalUsabilityError(ValueError):
    """The validation contract is malformed or not exact-project bound."""


class CriterionSourceKind(StrEnum):
    BRIEF_FACT = "brief_fact"
    COMMITMENT = "commitment"
    ADOPTED_RETRIEVAL = "adopted_retrieval"
    PROGRAM = "program"
    SITE = "site"
    BUILD_POLICY = "build_policy"
    CURRENT_OBLIGATION = "current_obligation"


class CriterionOperator(StrEnum):
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    CONTAINS = "contains"
    MEMBER_OF = "member_of"


class CriterionFindingStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class ArchitecturalUsabilityStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


def canonical_value(value: object) -> str:
    """Encode one deterministic JSON measurement or expected value."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ArchitecturalUsabilityError(
            "criterion values must be finite JSON values"
        ) from exc
    if json.loads(encoded) != value:
        raise ArchitecturalUsabilityError(
            "criterion values must round-trip through JSON"
        )
    return encoded


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value.lower())
    ):
        raise ArchitecturalUsabilityError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArchitecturalUsabilityError(f"{field} must be non-empty text")
    return value


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (
        not values and not allow_empty
    ):
        raise ArchitecturalUsabilityError(
            f"{field} must be a{' non-empty' if not allow_empty else ''} tuple"
        )
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ArchitecturalUsabilityError(f"{field} contains an invalid ref")
    if values != tuple(sorted(set(values))):
        raise ArchitecturalUsabilityError(
            f"{field} must be unique and deterministic"
        )
    return values


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    result = _refs(values, field, allow_empty=allow_empty)
    for item in result:
        require_identifier(item, field)
    return result


def _value_json(value: object, field: str) -> str:
    _text(value, field)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ArchitecturalUsabilityError(
            f"{field} must be canonical JSON"
        ) from exc
    if canonical_value(decoded) != value:
        raise ArchitecturalUsabilityError(f"{field} must be canonical JSON")
    return value


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(
    value: Mapping[str, object],
    fields: set[str],
    name: str,
) -> None:
    if set(value) != fields:
        raise ArchitecturalUsabilityError(f"{name} schema drifted")


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class AuthorizedCriterionSource:
    source_ref: str
    kind: CriterionSourceKind
    authority_ref: str
    adoption_ref: str | None = None

    SCHEMA = "AuthorizedArchitecturalCriterionSource@1"

    def __post_init__(self) -> None:
        _text(self.source_ref, "source_ref")
        if not isinstance(self.kind, CriterionSourceKind):
            raise TypeError("kind must be CriterionSourceKind")
        _text(self.authority_ref, "authority_ref")
        if self.kind is CriterionSourceKind.ADOPTED_RETRIEVAL:
            if self.adoption_ref is None:
                raise ArchitecturalUsabilityError(
                    "retrieval evidence requires an adopted project record"
                )
            _text(self.adoption_ref, "adoption_ref")
            if self.adoption_ref == self.source_ref:
                raise ArchitecturalUsabilityError(
                    "retrieval output cannot adopt itself"
                )
        elif self.adoption_ref is not None:
            raise ArchitecturalUsabilityError(
                "only retrieved evidence may carry adoption_ref"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_ref": self.source_ref,
            "kind": self.kind.value,
            "authority_ref": self.authority_ref,
            "adoption_ref": self.adoption_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> AuthorizedCriterionSource:
        payload = _mapping(value, "criterion source")
        _exact(
            payload,
            {"schema", "source_ref", "kind", "authority_ref", "adoption_ref"},
            "criterion source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalUsabilityError("criterion source schema changed")
        try:
            kind = CriterionSourceKind(payload["kind"])
        except ValueError as exc:
            raise ArchitecturalUsabilityError(
                "unsupported criterion source kind"
            ) from exc
        return cls(
            source_ref=payload["source_ref"],
            kind=kind,
            authority_ref=payload["authority_ref"],
            adoption_ref=payload["adoption_ref"],
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalCriterion:
    criterion_id: str
    measurement_key: str
    operator: CriterionOperator
    expected_json: str
    unit: str | None
    mandatory: bool
    source_refs: tuple[str, ...]
    component_ids: tuple[str, ...] = ()
    geometry_object_ids: tuple[str, ...] = ()
    obligation_refs: tuple[str, ...] = ()

    SCHEMA = "ProjectArchitecturalCriterion@1"

    def __post_init__(self) -> None:
        require_identifier(self.criterion_id, "criterion_id")
        require_identifier(self.measurement_key, "measurement_key")
        if not isinstance(self.operator, CriterionOperator):
            raise TypeError("operator must be CriterionOperator")
        _value_json(self.expected_json, "expected_json")
        if self.unit is not None:
            require_identifier(self.unit, "unit")
        if not isinstance(self.mandatory, bool):
            raise TypeError("mandatory must be bool")
        _refs(self.source_refs, "criterion source_refs")
        _ids(self.component_ids, "criterion component_ids", allow_empty=True)
        _ids(
            self.geometry_object_ids,
            "criterion geometry_object_ids",
            allow_empty=True,
        )
        _refs(
            self.obligation_refs,
            "criterion obligation_refs",
            allow_empty=True,
        )
        expected = json.loads(self.expected_json)
        if self.operator in {
            CriterionOperator.MINIMUM,
            CriterionOperator.MAXIMUM,
        } and not _is_number(expected):
            raise ArchitecturalUsabilityError(
                "threshold criteria require a numeric expected value"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "criterion_id": self.criterion_id,
            "measurement_key": self.measurement_key,
            "operator": self.operator.value,
            "expected_json": self.expected_json,
            "unit": self.unit,
            "mandatory": self.mandatory,
            "source_refs": list(self.source_refs),
            "component_ids": list(self.component_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "obligation_refs": list(self.obligation_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalCriterion:
        payload = _mapping(value, "architectural criterion")
        _exact(
            payload,
            {
                "schema",
                "criterion_id",
                "measurement_key",
                "operator",
                "expected_json",
                "unit",
                "mandatory",
                "source_refs",
                "component_ids",
                "geometry_object_ids",
                "obligation_refs",
            },
            "architectural criterion",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalUsabilityError("criterion schema changed")
        try:
            operator = CriterionOperator(payload["operator"])
        except ValueError as exc:
            raise ArchitecturalUsabilityError(
                "unsupported architectural criterion operator"
            ) from exc
        return cls(
            criterion_id=payload["criterion_id"],
            measurement_key=payload["measurement_key"],
            operator=operator,
            expected_json=payload["expected_json"],
            unit=payload["unit"],
            mandatory=payload["mandatory"],
            source_refs=_string_tuple(payload["source_refs"], "source_refs"),
            component_ids=_string_tuple(
                payload["component_ids"], "component_ids"
            ),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            obligation_refs=_string_tuple(
                payload["obligation_refs"], "obligation_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalValidationContext:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    component_tree_digest: str
    geometry_program_digest: str
    realization_receipt_digest: str
    scene_digest: str
    artifact_ref: str
    brief_digest: str | None
    component_ids: tuple[str, ...]
    geometry_object_ids: tuple[str, ...]
    semantic_ownership: tuple[tuple[str, tuple[str, ...]], ...]
    obligation_refs: tuple[str, ...]

    SCHEMA = "ArchitecturalValidationContext@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ArchitecturalUsabilityError("context and base disagree")
        self.base.require_digest()
        for value, field in (
            (self.design_state_digest, "design_state_digest"),
            (self.component_tree_digest, "component_tree_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
            (self.realization_receipt_digest, "realization_receipt_digest"),
            (self.scene_digest, "scene_digest"),
        ):
            _sha(value, field)
        if self.brief_digest is not None:
            _sha(self.brief_digest, "brief_digest")
        if not isinstance(self.artifact_ref, str) or not self.artifact_ref.startswith(
            f"project://{self.project_id}/"
        ):
            raise ArchitecturalUsabilityError(
                "artifact_ref must be a stable reference in this project"
            )
        _ids(self.component_ids, "component_ids")
        _ids(self.geometry_object_ids, "geometry_object_ids")
        _refs(self.obligation_refs, "obligation_refs", allow_empty=True)
        if not isinstance(self.semantic_ownership, tuple):
            raise TypeError("semantic_ownership must be a tuple")
        owner_ids: list[str] = []
        owned_ids: set[str] = set()
        for component_id, object_ids in self.semantic_ownership:
            require_identifier(component_id, "semantic owner component_id")
            _ids(object_ids, "semantic owner object_ids")
            owner_ids.append(component_id)
            overlap = owned_ids & set(object_ids)
            if overlap:
                raise ArchitecturalUsabilityError(
                    f"geometry objects have multiple semantic owners: {sorted(overlap)}"
                )
            owned_ids.update(object_ids)
        if tuple(owner_ids) != tuple(sorted(set(owner_ids))):
            raise ArchitecturalUsabilityError(
                "semantic ownership requires deterministic components"
            )
        if not set(owner_ids) <= set(self.component_ids):
            raise ArchitecturalUsabilityError("semantic owner is not a component")
        if not owned_ids <= set(self.geometry_object_ids):
            raise ArchitecturalUsabilityError(
                "semantic ownership names an unknown geometry object"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "design_state_digest": self.design_state_digest,
            "component_tree_digest": self.component_tree_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "realization_receipt_digest": self.realization_receipt_digest,
            "scene_digest": self.scene_digest,
            "artifact_ref": self.artifact_ref,
            "brief_digest": self.brief_digest,
            "component_ids": list(self.component_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "semantic_ownership": [
                {"component_id": component_id, "object_ids": list(object_ids)}
                for component_id, object_ids in self.semantic_ownership
            ],
            "obligation_refs": list(self.obligation_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalValidationContext:
        payload = _mapping(value, "architectural validation context")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "design_state_digest",
                "component_tree_digest",
                "geometry_program_digest",
                "realization_receipt_digest",
                "scene_digest",
                "artifact_ref",
                "brief_digest",
                "component_ids",
                "geometry_object_ids",
                "semantic_ownership",
                "obligation_refs",
            },
            "architectural validation context",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalUsabilityError("context schema changed")
        ownership = payload["semantic_ownership"]
        if not isinstance(ownership, list):
            raise TypeError("semantic_ownership must be a list")
        pairs: list[tuple[str, tuple[str, ...]]] = []
        for item in ownership:
            entry = _mapping(item, "semantic ownership entry")
            _exact(entry, {"component_id", "object_ids"}, "semantic ownership")
            pairs.append(
                (
                    entry["component_id"],
                    _string_tuple(entry["object_ids"], "object_ids"),
                )
            )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            design_state_digest=payload["design_state_digest"],
            component_tree_digest=payload["component_tree_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            realization_receipt_digest=payload["realization_receipt_digest"],
            scene_digest=payload["scene_digest"],
            artifact_ref=payload["artifact_ref"],
            brief_digest=payload["brief_digest"],
            component_ids=_string_tuple(payload["component_ids"], "component_ids"),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            semantic_ownership=tuple(pairs),
            obligation_refs=_string_tuple(
                payload["obligation_refs"], "obligation_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalUsabilityContract:
    context: ArchitecturalValidationContext
    authorized_record_refs: tuple[str, ...]
    sources: tuple[AuthorizedCriterionSource, ...]
    criteria: tuple[ArchitecturalCriterion, ...]

    SCHEMA = "ProjectArchitecturalUsabilityContract@1"

    def __post_init__(self) -> None:
        if not isinstance(self.context, ArchitecturalValidationContext):
            raise TypeError("context must be ArchitecturalValidationContext")
        _refs(self.authorized_record_refs, "authorized_record_refs")
        if not isinstance(self.sources, tuple) or not self.sources or any(
            not isinstance(item, AuthorizedCriterionSource) for item in self.sources
        ):
            raise ArchitecturalUsabilityError("sources must be non-empty")
        if not isinstance(self.criteria, tuple) or not self.criteria or any(
            not isinstance(item, ArchitecturalCriterion) for item in self.criteria
        ):
            raise ArchitecturalUsabilityError("criteria must be non-empty")
        source_refs = tuple(item.source_ref for item in self.sources)
        if source_refs != tuple(sorted(set(source_refs))):
            raise ArchitecturalUsabilityError(
                "criterion sources require deterministic identities"
            )
        criterion_ids = tuple(item.criterion_id for item in self.criteria)
        if criterion_ids != tuple(sorted(set(criterion_ids))):
            raise ArchitecturalUsabilityError(
                "criteria require deterministic identities"
            )
        if not any(item.mandatory for item in self.criteria):
            raise ArchitecturalUsabilityError(
                "architectural contract needs a mandatory criterion"
            )
        known_sources = set(source_refs)
        known_authority = set(self.authorized_record_refs)
        for source in self.sources:
            if source.authority_ref not in known_authority:
                raise ArchitecturalUsabilityError(
                    "criterion source lacks an authorized project record"
                )
            if (
                source.adoption_ref is not None
                and source.adoption_ref not in known_authority
            ):
                raise ArchitecturalUsabilityError(
                    "retrieval adoption is not an authorized project record"
                )
        owner_map = {
            component_id: set(object_ids)
            for component_id, object_ids in self.context.semantic_ownership
        }
        known_components = set(self.context.component_ids)
        known_objects = set(self.context.geometry_object_ids)
        known_obligations = set(self.context.obligation_refs)
        for criterion in self.criteria:
            if not set(criterion.source_refs) <= known_sources:
                raise ArchitecturalUsabilityError(
                    "criterion cites an undeclared source"
                )
            if not set(criterion.component_ids) <= known_components:
                raise ArchitecturalUsabilityError(
                    "criterion cites an unknown semantic component"
                )
            if not set(criterion.geometry_object_ids) <= known_objects:
                raise ArchitecturalUsabilityError(
                    "criterion cites an unknown geometry object"
                )
            if not set(criterion.obligation_refs) <= known_obligations:
                raise ArchitecturalUsabilityError(
                    "criterion cites an unknown current obligation"
                )
            if criterion.component_ids and criterion.geometry_object_ids:
                owned = set().union(
                    *(owner_map.get(item, set()) for item in criterion.component_ids)
                )
                if not set(criterion.geometry_object_ids) <= owned:
                    raise ArchitecturalUsabilityError(
                        "criterion component and geometry locality disagree"
                    )

    @property
    def contract_digest(self) -> str:
        return _digest(self._identity())

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context": self.context.to_dict(),
            "authorized_record_refs": list(self.authorized_record_refs),
            "sources": [item.to_dict() for item in self.sources],
            "criteria": [item.to_dict() for item in self.criteria],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "contract_digest": self.contract_digest,
            "building_answers_owned_by_framework": False,
            "model_self_certification_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalUsabilityContract:
        payload = _mapping(value, "architectural usability contract")
        _exact(
            payload,
            {
                "schema",
                "context",
                "authorized_record_refs",
                "sources",
                "criteria",
                "contract_digest",
                "building_answers_owned_by_framework",
                "model_self_certification_authority",
                "canonical_write_authority",
            },
            "architectural usability contract",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["building_answers_owned_by_framework"] is not False
            or payload["model_self_certification_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArchitecturalUsabilityError("contract authority drifted")
        sources = payload["sources"]
        criteria = payload["criteria"]
        if not isinstance(sources, list) or not isinstance(criteria, list):
            raise TypeError("sources and criteria must be lists")
        contract = cls(
            context=ArchitecturalValidationContext.from_dict(payload["context"]),
            authorized_record_refs=_string_tuple(
                payload["authorized_record_refs"], "authorized_record_refs"
            ),
            sources=tuple(
                AuthorizedCriterionSource.from_dict(item) for item in sources
            ),
            criteria=tuple(ArchitecturalCriterion.from_dict(item) for item in criteria),
        )
        if payload["contract_digest"] != contract.contract_digest:
            raise ArchitecturalUsabilityError("contract digest changed")
        return contract


@dataclass(frozen=True, slots=True)
class ArchitecturalObservation:
    observation_id: str
    contract_digest: str
    measurement_key: str
    value_json: str
    unit: str | None
    component_ids: tuple[str, ...]
    geometry_object_ids: tuple[str, ...]
    obligation_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "ArchitecturalObservation@1"

    def __post_init__(self) -> None:
        require_identifier(self.observation_id, "observation_id")
        _sha(self.contract_digest, "contract_digest")
        require_identifier(self.measurement_key, "measurement_key")
        _value_json(self.value_json, "value_json")
        if self.unit is not None:
            require_identifier(self.unit, "unit")
        _ids(self.component_ids, "observation component_ids", allow_empty=True)
        _ids(
            self.geometry_object_ids,
            "observation geometry_object_ids",
            allow_empty=True,
        )
        _refs(
            self.obligation_refs,
            "observation obligation_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "observation evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "contract_digest": self.contract_digest,
            "measurement_key": self.measurement_key,
            "value_json": self.value_json,
            "unit": self.unit,
            "component_ids": list(self.component_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "obligation_refs": list(self.obligation_refs),
            "evidence_refs": list(self.evidence_refs),
            "model_assertion": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalObservation:
        payload = _mapping(value, "architectural observation")
        _exact(
            payload,
            {
                "schema",
                "observation_id",
                "contract_digest",
                "measurement_key",
                "value_json",
                "unit",
                "component_ids",
                "geometry_object_ids",
                "obligation_refs",
                "evidence_refs",
                "model_assertion",
            },
            "architectural observation",
        )
        if payload["schema"] != cls.SCHEMA or payload["model_assertion"] is not False:
            raise ArchitecturalUsabilityError("observation authority drifted")
        return cls(
            observation_id=payload["observation_id"],
            contract_digest=payload["contract_digest"],
            measurement_key=payload["measurement_key"],
            value_json=payload["value_json"],
            unit=payload["unit"],
            component_ids=_string_tuple(
                payload["component_ids"], "component_ids"
            ),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            obligation_refs=_string_tuple(
                payload["obligation_refs"], "obligation_refs"
            ),
            evidence_refs=_string_tuple(payload["evidence_refs"], "evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalFinding:
    criterion_id: str
    status: CriterionFindingStatus
    code: str
    expected_json: str
    observed_json: str | None
    unit: str | None
    source_refs: tuple[str, ...]
    component_ids: tuple[str, ...]
    geometry_object_ids: tuple[str, ...]
    obligation_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    detail: str
    mandatory: bool

    SCHEMA = "ArchitecturalUsabilityFinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.criterion_id, "criterion_id")
        if not isinstance(self.status, CriterionFindingStatus):
            raise TypeError("status must be CriterionFindingStatus")
        require_identifier(self.code, "finding code")
        _value_json(self.expected_json, "expected_json")
        if self.observed_json is not None:
            _value_json(self.observed_json, "observed_json")
        if self.unit is not None:
            require_identifier(self.unit, "unit")
        _refs(self.source_refs, "finding source_refs")
        _ids(self.component_ids, "finding component_ids", allow_empty=True)
        _ids(
            self.geometry_object_ids,
            "finding geometry_object_ids",
            allow_empty=True,
        )
        _refs(
            self.obligation_refs,
            "finding obligation_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "finding evidence_refs", allow_empty=True)
        _text(self.detail, "finding detail")
        if not isinstance(self.mandatory, bool):
            raise TypeError("mandatory must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "criterion_id": self.criterion_id,
            "status": self.status.value,
            "code": self.code,
            "expected_json": self.expected_json,
            "observed_json": self.observed_json,
            "unit": self.unit,
            "source_refs": list(self.source_refs),
            "component_ids": list(self.component_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "obligation_refs": list(self.obligation_refs),
            "evidence_refs": list(self.evidence_refs),
            "detail": self.detail,
            "mandatory": self.mandatory,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalFinding:
        payload = _mapping(value, "architectural finding")
        _exact(
            payload,
            {
                "schema",
                "criterion_id",
                "status",
                "code",
                "expected_json",
                "observed_json",
                "unit",
                "source_refs",
                "component_ids",
                "geometry_object_ids",
                "obligation_refs",
                "evidence_refs",
                "detail",
                "mandatory",
            },
            "architectural finding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalUsabilityError("finding schema changed")
        return cls(
            criterion_id=payload["criterion_id"],
            status=CriterionFindingStatus(payload["status"]),
            code=payload["code"],
            expected_json=payload["expected_json"],
            observed_json=payload["observed_json"],
            unit=payload["unit"],
            source_refs=_string_tuple(payload["source_refs"], "source_refs"),
            component_ids=_string_tuple(
                payload["component_ids"], "component_ids"
            ),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            obligation_refs=_string_tuple(
                payload["obligation_refs"], "obligation_refs"
            ),
            evidence_refs=_string_tuple(payload["evidence_refs"], "evidence_refs"),
            detail=payload["detail"],
            mandatory=payload["mandatory"],
        )


@dataclass(frozen=True, slots=True)
class ArchitecturalUsabilityReceipt:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    contract_digest: str
    design_state_digest: str
    geometry_program_digest: str
    realization_receipt_digest: str
    scene_digest: str
    artifact_ref: str
    status: ArchitecturalUsabilityStatus
    findings: tuple[ArchitecturalFinding, ...]

    SCHEMA = "ArchitecturalUsabilityReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ArchitecturalUsabilityError("receipt and base disagree")
        self.base.require_digest()
        for value, field in (
            (self.contract_digest, "contract_digest"),
            (self.design_state_digest, "design_state_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
            (self.realization_receipt_digest, "realization_receipt_digest"),
            (self.scene_digest, "scene_digest"),
        ):
            _sha(value, field)
        _text(self.artifact_ref, "artifact_ref")
        if not isinstance(self.status, ArchitecturalUsabilityStatus):
            raise TypeError("status must be ArchitecturalUsabilityStatus")
        if not isinstance(self.findings, tuple) or not self.findings or any(
            not isinstance(item, ArchitecturalFinding) for item in self.findings
        ):
            raise ArchitecturalUsabilityError("findings must be non-empty")
        if tuple(item.criterion_id for item in self.findings) != tuple(
            sorted(item.criterion_id for item in self.findings)
        ):
            raise ArchitecturalUsabilityError(
                "findings require deterministic criterion order"
            )
        blockers = tuple(item for item in self.findings if item.mandatory)
        expected = (
            ArchitecturalUsabilityStatus.FAILED
            if any(item.status is CriterionFindingStatus.FAIL for item in blockers)
            else ArchitecturalUsabilityStatus.UNKNOWN
            if any(item.status is CriterionFindingStatus.UNKNOWN for item in blockers)
            else ArchitecturalUsabilityStatus.PASSED
        )
        if self.status is not expected:
            raise ArchitecturalUsabilityError("receipt status and findings disagree")

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def accepted(self) -> bool:
        return self.status is ArchitecturalUsabilityStatus.PASSED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "contract_digest": self.contract_digest,
            "design_state_digest": self.design_state_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "realization_receipt_digest": self.realization_receipt_digest,
            "scene_digest": self.scene_digest,
            "artifact_ref": self.artifact_ref,
            "status": self.status.value,
            "findings": [item.to_dict() for item in self.findings],
            "artifact_presence_claimed": True,
            "architectural_usability_claimed": self.accepted,
            "model_self_certification_authority": False,
            "geometry_mutation_authority": False,
            "review_authority": False,
            "promotion_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitecturalUsabilityReceipt:
        payload = _mapping(value, "architectural usability receipt")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "contract_digest",
                "design_state_digest",
                "geometry_program_digest",
                "realization_receipt_digest",
                "scene_digest",
                "artifact_ref",
                "status",
                "findings",
                "artifact_presence_claimed",
                "architectural_usability_claimed",
                "model_self_certification_authority",
                "geometry_mutation_authority",
                "review_authority",
                "promotion_authority",
                "canonical_write_authority",
            },
            "architectural usability receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["artifact_presence_claimed"] is not True
            or payload["model_self_certification_authority"] is not False
            or payload["geometry_mutation_authority"] is not False
            or payload["review_authority"] is not False
            or payload["promotion_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArchitecturalUsabilityError("receipt authority drifted")
        findings = payload["findings"]
        if not isinstance(findings, list):
            raise TypeError("findings must be a list")
        receipt = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            contract_digest=payload["contract_digest"],
            design_state_digest=payload["design_state_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            realization_receipt_digest=payload["realization_receipt_digest"],
            scene_digest=payload["scene_digest"],
            artifact_ref=payload["artifact_ref"],
            status=ArchitecturalUsabilityStatus(payload["status"]),
            findings=tuple(ArchitecturalFinding.from_dict(item) for item in findings),
        )
        if payload["architectural_usability_claimed"] is not receipt.accepted:
            raise ArchitecturalUsabilityError("receipt usability claim drifted")
        return receipt


def compile_architectural_usability_contract(
    *,
    design_state: DevelopedDesignState,
    geometry_program: CompiledGeometryProgram,
    scene: HybridScene,
    realization_receipt: SandboxRealizationReceipt,
    artifact_ref: str,
    authorized_record_refs: tuple[str, ...],
    sources: tuple[AuthorizedCriterionSource, ...],
    criteria: tuple[ArchitecturalCriterion, ...],
    brief: DesignBrief | None = None,
) -> ArchitecturalUsabilityContract:
    """Bind project-authored criteria to one realized current design state."""

    if not isinstance(design_state, DevelopedDesignState):
        raise TypeError("design_state must be DevelopedDesignState")
    if not isinstance(geometry_program, CompiledGeometryProgram):
        raise TypeError("geometry_program must be CompiledGeometryProgram")
    if not isinstance(scene, HybridScene):
        raise TypeError("scene must be HybridScene")
    if not isinstance(realization_receipt, SandboxRealizationReceipt):
        raise TypeError("realization_receipt must be SandboxRealizationReceipt")
    proposal = geometry_program.proposal
    exact = (design_state.project_id, design_state.run_id, design_state.base)
    if (proposal.project_id, proposal.run_id, proposal.base) != exact:
        raise ArchitecturalUsabilityError(
            "design state and neutral geometry do not share one exact project base"
        )
    if (scene.project_id, scene.run_id, scene.base) != exact:
        raise ArchitecturalUsabilityError(
            "sandbox scene does not share the design project base"
        )
    if proposal.design_state_digest != design_state.state_digest:
        raise ArchitecturalUsabilityError("neutral geometry is stale for design state")
    if scene.geometry_program_digest != geometry_program.program_digest:
        raise ArchitecturalUsabilityError("scene is stale for neutral geometry")
    if (
        realization_receipt.status is not RealizationStatus.REALIZED
        or realization_receipt.geometry_program_digest
        != geometry_program.program_digest
        or realization_receipt.workspace_id != scene.workspace_id
        or realization_receipt.scene_digest != scene.scene_digest
    ):
        raise ArchitecturalUsabilityError(
            "artifact presence requires one successful exact sandbox realization"
        )
    if scene.semantic_binding_digests != geometry_program.semantic_binding_digests:
        raise ArchitecturalUsabilityError(
            "scene and neutral geometry semantic bindings disagree"
        )
    if brief is not None:
        if not isinstance(brief, DesignBrief):
            raise TypeError("brief must be DesignBrief or None")
        if (brief.project_id, brief.run_id, brief.base) != exact:
            raise ArchitecturalUsabilityError("brief is not exact-project bound")

    component_ids = tuple(
        item.component_id
        for item in design_state.selected_schematic.option.proposal.components
    )
    compiled_object_ids = {item.object_id for item in geometry_program.objects}
    scene_object_ids = {item.object_id for item in scene.objects}
    if compiled_object_ids != scene_object_ids:
        raise ArchitecturalUsabilityError(
            "compiled geometry and scene object identities disagree"
        )
    ownership: dict[str, set[str]] = {}
    for binding in proposal.semantic_bindings:
        if binding.component_id not in component_ids:
            raise ArchitecturalUsabilityError(
                "neutral geometry binding names a non-current component"
            )
        unknown = set(binding.object_ids) - compiled_object_ids
        if unknown:
            raise ArchitecturalUsabilityError(
                f"semantic binding names unknown objects: {sorted(unknown)}"
            )
        ownership.setdefault(binding.component_id, set()).update(binding.object_ids)

    authorized = set(authorized_record_refs)
    brief_claims = (
        {item.ref: item for item in brief.claims} if brief is not None else {}
    )
    obligations = {item.ref for item in design_state.obligations}
    for source in sources:
        if source.kind is CriterionSourceKind.BRIEF_FACT:
            claim = brief_claims.get(source.source_ref)
            if claim is None or claim.kind not in {
                BriefClaimKind.USER_FACT,
                BriefClaimKind.PROHIBITION,
            }:
                raise ArchitecturalUsabilityError(
                    "brief criterion must cite a declared fact or prohibition"
                )
        elif source.kind is CriterionSourceKind.ADOPTED_RETRIEVAL:
            claim = brief_claims.get(source.source_ref)
            if claim is None or claim.kind is not BriefClaimKind.RETRIEVED_EVIDENCE:
                raise ArchitecturalUsabilityError(
                    "adopted retrieval source must cite retrieved brief evidence"
                )
            if source.adoption_ref not in authorized:
                raise ArchitecturalUsabilityError(
                    "retrieved evidence lacks exact-project adoption"
                )
        elif source.kind is CriterionSourceKind.CURRENT_OBLIGATION:
            if source.source_ref not in obligations:
                raise ArchitecturalUsabilityError(
                    "current-obligation source is absent from design state"
                )
        elif source.source_ref not in authorized:
            raise ArchitecturalUsabilityError(
                "criterion source is absent from authorized project records"
            )

    context = ArchitecturalValidationContext(
        project_id=design_state.project_id,
        run_id=design_state.run_id,
        base=design_state.base,
        design_state_digest=design_state.state_digest,
        component_tree_digest=(
            design_state.selected_schematic.option.proposal.proposal_digest
        ),
        geometry_program_digest=geometry_program.program_digest,
        realization_receipt_digest=realization_receipt.receipt_digest,
        scene_digest=scene.scene_digest,
        artifact_ref=artifact_ref,
        brief_digest=brief.brief_digest if brief is not None else None,
        component_ids=tuple(sorted(component_ids)),
        geometry_object_ids=tuple(sorted(compiled_object_ids)),
        semantic_ownership=tuple(
            (component_id, tuple(sorted(object_ids)))
            for component_id, object_ids in sorted(ownership.items())
        ),
        obligation_refs=tuple(sorted(obligations)),
    )
    return ArchitecturalUsabilityContract(
        context=context,
        authorized_record_refs=authorized_record_refs,
        sources=sources,
        criteria=criteria,
    )


def evaluate_architectural_usability(
    contract: ArchitecturalUsabilityContract,
    observations: tuple[ArchitecturalObservation, ...],
) -> ArchitecturalUsabilityReceipt:
    """Evaluate measurements without mutating design, geometry, or project state."""

    if not isinstance(contract, ArchitecturalUsabilityContract):
        raise TypeError("contract must be ArchitecturalUsabilityContract")
    if not isinstance(observations, tuple) or any(
        not isinstance(item, ArchitecturalObservation) for item in observations
    ):
        raise TypeError("observations contains an invalid item")
    keys = tuple(item.measurement_key for item in observations)
    if len(keys) != len(set(keys)):
        raise ArchitecturalUsabilityError(
            "observations require one value per measurement key"
        )
    by_key = {item.measurement_key: item for item in observations}
    contradictions = _contradictory_criterion_ids(contract.criteria)
    findings: list[ArchitecturalFinding] = []
    for criterion in contract.criteria:
        observation = by_key.get(criterion.measurement_key)
        if criterion.criterion_id in contradictions:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "contradictory_mandatory_criteria",
                    observation,
                    "mandatory project sources compile contradictory conditions",
                )
            )
            continue
        if observation is None:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "observation_missing",
                    None,
                    "the realized artifact has no measurement for this criterion",
                )
            )
            continue
        if observation.contract_digest != contract.contract_digest:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "observation_stale",
                    observation,
                    "the observation targets another contract digest",
                )
            )
            continue
        if observation.unit != criterion.unit:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "unit_mismatch",
                    observation,
                    "the observation and criterion units differ",
                )
            )
            continue
        if (
            observation.component_ids != criterion.component_ids
            or observation.geometry_object_ids != criterion.geometry_object_ids
            or observation.obligation_refs != criterion.obligation_refs
        ):
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "observation_locality_mismatch",
                    observation,
                    "the observation is not bound to the criterion locality",
                )
            )
            continue
        expected = json.loads(criterion.expected_json)
        observed = json.loads(observation.value_json)
        result = _compare(criterion.operator, observed, expected)
        if result is None:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.UNKNOWN,
                    "measurement_not_evaluable",
                    observation,
                    "the measured value does not support this generic operator",
                )
            )
        elif result:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.PASS,
                    "criterion_passed",
                    observation,
                    "the exact-project measurement satisfies the criterion",
                )
            )
        else:
            findings.append(
                _finding(
                    criterion,
                    CriterionFindingStatus.FAIL,
                    "criterion_failed",
                    observation,
                    "the exact-project measurement violates the criterion",
                )
            )
    ordered = tuple(sorted(findings, key=lambda item: item.criterion_id))
    mandatory = tuple(item for item in ordered if item.mandatory)
    status = (
        ArchitecturalUsabilityStatus.FAILED
        if any(item.status is CriterionFindingStatus.FAIL for item in mandatory)
        else ArchitecturalUsabilityStatus.UNKNOWN
        if any(item.status is CriterionFindingStatus.UNKNOWN for item in mandatory)
        else ArchitecturalUsabilityStatus.PASSED
    )
    context = contract.context
    return ArchitecturalUsabilityReceipt(
        project_id=context.project_id,
        run_id=context.run_id,
        base=context.base,
        contract_digest=contract.contract_digest,
        design_state_digest=context.design_state_digest,
        geometry_program_digest=context.geometry_program_digest,
        realization_receipt_digest=context.realization_receipt_digest,
        scene_digest=context.scene_digest,
        artifact_ref=context.artifact_ref,
        status=status,
        findings=ordered,
    )


def _finding(
    criterion: ArchitecturalCriterion,
    status: CriterionFindingStatus,
    code: str,
    observation: ArchitecturalObservation | None,
    detail: str,
) -> ArchitecturalFinding:
    return ArchitecturalFinding(
        criterion_id=criterion.criterion_id,
        status=status,
        code=code,
        expected_json=criterion.expected_json,
        observed_json=(observation.value_json if observation is not None else None),
        unit=criterion.unit,
        source_refs=criterion.source_refs,
        component_ids=criterion.component_ids,
        geometry_object_ids=criterion.geometry_object_ids,
        obligation_refs=criterion.obligation_refs,
        evidence_refs=(
            observation.evidence_refs if observation is not None else ()
        ),
        detail=detail,
        mandatory=criterion.mandatory,
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(
    operator: CriterionOperator,
    observed: object,
    expected: object,
) -> bool | None:
    if operator is CriterionOperator.EQUAL:
        return observed == expected
    if operator is CriterionOperator.NOT_EQUAL:
        return observed != expected
    if operator is CriterionOperator.MINIMUM:
        return observed >= expected if _is_number(observed) else None
    if operator is CriterionOperator.MAXIMUM:
        return observed <= expected if _is_number(observed) else None
    if operator is CriterionOperator.CONTAINS:
        if isinstance(observed, (list, str, dict)):
            try:
                return expected in observed
            except TypeError:
                return None
        return None
    if operator is CriterionOperator.MEMBER_OF:
        if isinstance(expected, (list, str, dict)):
            try:
                return observed in expected
            except TypeError:
                return None
        return None
    return None


def _contradictory_criterion_ids(
    criteria: tuple[ArchitecturalCriterion, ...],
) -> set[str]:
    groups: dict[
        tuple[str, str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        list[ArchitecturalCriterion],
    ] = {}
    for item in criteria:
        if not item.mandatory:
            continue
        key = (
            item.measurement_key,
            item.unit,
            item.component_ids,
            item.geometry_object_ids,
            item.obligation_refs,
        )
        groups.setdefault(key, []).append(item)
    result: set[str] = set()
    for items in groups.values():
        if _criteria_contradict(items):
            result.update(item.criterion_id for item in items)
    return result


def _criteria_contradict(criteria: list[ArchitecturalCriterion]) -> bool:
    equals = [
        json.loads(item.expected_json)
        for item in criteria
        if item.operator is CriterionOperator.EQUAL
    ]
    if equals and any(value != equals[0] for value in equals[1:]):
        return True
    not_equals = {
        item.expected_json
        for item in criteria
        if item.operator is CriterionOperator.NOT_EQUAL
    }
    if equals and canonical_value(equals[0]) in not_equals:
        return True
    minima = [
        json.loads(item.expected_json)
        for item in criteria
        if item.operator is CriterionOperator.MINIMUM
    ]
    maxima = [
        json.loads(item.expected_json)
        for item in criteria
        if item.operator is CriterionOperator.MAXIMUM
    ]
    if minima and maxima and max(minima) > min(maxima):
        return True
    if equals and _is_number(equals[0]):
        if minima and equals[0] < max(minima):
            return True
        if maxima and equals[0] > min(maxima):
            return True
    return False
