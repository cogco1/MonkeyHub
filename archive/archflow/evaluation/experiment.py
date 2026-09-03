"""Read-only preregistration and outcome contracts for empirical studies.

The module never invokes a provider, loads a project repository, validates a
building, or writes a result.  It binds detached evidence produced by those
authorities into an immutable experiment protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.geometry_program import digest_value, require_sha256
from archflow.state.operational_state import require_logical_ref


class ExperimentProtocolError(ValueError):
    """The study protocol or detached evidence binding is inconsistent."""


class ExperimentConditionKind(StrEnum):
    FULL = "full"
    GENERATION_CONTEXT_ABLATION = "generation_context_ablation"
    VALIDATION_ABLATION = "validation_ablation"


class ExperimentStoppingRule(StrEnum):
    COMPLETE_ALL_ASSIGNMENTS = "complete_all_assignments"
    STOP_ON_STUDY_BUDGET = "stop_on_study_budget"


class ExperimentAttemptStatus(StrEnum):
    COMPLETED = "completed"
    PIPELINE_REJECTED = "pipeline_rejected"
    PROVIDER_FAILED = "provider_failed"
    TIMED_OUT = "timed_out"


class ExperimentAssignmentLifecycle(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    TERMINAL_UNMEASURED = "terminal_unmeasured"
    FAILED = "failed"
    COMPLETED = "completed"


class MetricValueKind(StrEnum):
    BOOLEAN = "boolean"
    COUNT = "count"
    DURATION_MS = "duration_ms"
    RATIO = "ratio"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    DESCRIPTIVE = "descriptive"


class MetricObservationStatus(StrEnum):
    MEASURED = "measured"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


_PROVIDER_STATUSES = frozenset(
    {
        "success",
        "timeout",
        "offline",
        "exit_error",
        "malformed",
        "oversized",
        "budget_exhausted",
    }
)


@dataclass(frozen=True, slots=True)
class ExperimentProviderProfile:
    profile_id: str
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str
    configuration_digest: str
    timeout_ms: int
    maximum_input_bytes: int
    maximum_output_bytes: int
    maximum_output_tokens: int
    sampling_seed: int | None

    SCHEMA = "ExperimentProviderProfile@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.profile_id, "profile_id"),
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (self.provider_version, "provider_version"),
        ):
            require_identifier(value, field)
        require_sha256(self.provider_fingerprint, "provider_fingerprint")
        require_sha256(self.configuration_digest, "configuration_digest")
        for value, field in (
            (self.timeout_ms, "timeout_ms"),
            (self.maximum_input_bytes, "maximum_input_bytes"),
            (self.maximum_output_bytes, "maximum_output_bytes"),
            (self.maximum_output_tokens, "maximum_output_tokens"),
        ):
            if type(value) is not int or value <= 0:
                raise ExperimentProtocolError(f"{field} must be positive")
        if self.sampling_seed is not None and (
            type(self.sampling_seed) is not int or self.sampling_seed < 0
        ):
            raise ExperimentProtocolError(
                "sampling_seed must be non-negative or None"
            )

    @property
    def profile_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
            "configuration_digest": self.configuration_digest,
            "timeout_ms": self.timeout_ms,
            "maximum_input_bytes": self.maximum_input_bytes,
            "maximum_output_bytes": self.maximum_output_bytes,
            "maximum_output_tokens": self.maximum_output_tokens,
            "sampling_seed": self.sampling_seed,
            "fallback_allowed": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentProviderProfile:
        payload = _mapping(value, "provider profile")
        _exact(
            payload,
            {
                "schema",
                "profile_id",
                "provider_id",
                "model_id",
                "provider_version",
                "provider_fingerprint",
                "configuration_digest",
                "timeout_ms",
                "maximum_input_bytes",
                "maximum_output_bytes",
                "maximum_output_tokens",
                "sampling_seed",
                "fallback_allowed",
            },
            "provider profile",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["fallback_allowed"] is not False
        ):
            raise ExperimentProtocolError("provider profile authority changed")
        return cls(
            profile_id=payload["profile_id"],
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
            configuration_digest=payload["configuration_digest"],
            timeout_ms=payload["timeout_ms"],
            maximum_input_bytes=payload["maximum_input_bytes"],
            maximum_output_bytes=payload["maximum_output_bytes"],
            maximum_output_tokens=payload["maximum_output_tokens"],
            sampling_seed=payload["sampling_seed"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentCase:
    case_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    raw_request_ref: str
    raw_request_digest: str
    input_record_refs: tuple[str, ...]

    SCHEMA = "ExperimentCase@1"

    def __post_init__(self) -> None:
        require_identifier(self.case_id, "case_id")
        require_identifier(self.project_id, "case project_id")
        require_identifier(self.run_id, "case run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("case and base cross projects")
        self.base.require_digest()
        require_sha256(self.raw_request_digest, "raw_request_digest")
        _project_ref(self.raw_request_ref, self.project_id, "raw_request_ref")
        _refs(self.input_record_refs, "input_record_refs")
        if self.raw_request_ref not in self.input_record_refs:
            raise ExperimentProtocolError(
                "input_record_refs must contain the raw request"
            )

    @property
    def case_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "case_id": self.case_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "raw_request_ref": self.raw_request_ref,
            "raw_request_digest": self.raw_request_digest,
            "input_record_refs": list(self.input_record_refs),
            "building_answer_owned_by_framework": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentCase:
        payload = _mapping(value, "experiment case")
        _exact(
            payload,
            {
                "schema",
                "case_id",
                "project_id",
                "run_id",
                "base",
                "raw_request_ref",
                "raw_request_digest",
                "input_record_refs",
                "building_answer_owned_by_framework",
            },
            "experiment case",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["building_answer_owned_by_framework"] is not False
        ):
            raise ExperimentProtocolError("experiment case authority changed")
        return cls(
            case_id=payload["case_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            raw_request_ref=payload["raw_request_ref"],
            raw_request_digest=payload["raw_request_digest"],
            input_record_refs=_string_tuple(
                payload["input_record_refs"], "input_record_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class ExperimentTerminalRequirement:
    role: str
    source_schema: str
    accepted_statuses: tuple[str, ...]

    SCHEMA = "ExperimentTerminalRequirement@1"

    def __post_init__(self) -> None:
        require_identifier(self.role, "terminal requirement role")
        _schema_name(self.source_schema, "terminal requirement source_schema")
        _ids(self.accepted_statuses, "terminal requirement accepted_statuses")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role,
            "source_schema": self.source_schema,
            "accepted_statuses": list(self.accepted_statuses),
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentTerminalRequirement:
        payload = _mapping(value, "terminal requirement")
        _exact(
            payload,
            {"schema", "role", "source_schema", "accepted_statuses"},
            "terminal requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("terminal requirement schema changed")
        return cls(
            role=payload["role"],
            source_schema=payload["source_schema"],
            accepted_statuses=_string_tuple(
                payload["accepted_statuses"],
                "terminal requirement accepted_statuses",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExperimentCondition:
    condition_id: str
    kind: ExperimentConditionKind
    provider_profile_id: str
    description: str
    terminal_requirements: tuple[ExperimentTerminalRequirement, ...]
    withheld_context_ids: tuple[str, ...]
    withheld_evaluator_ids: tuple[str, ...]
    source_condition_id: str | None

    SCHEMA = "ExperimentCondition@1"

    def __post_init__(self) -> None:
        require_identifier(self.condition_id, "condition_id")
        require_identifier(self.provider_profile_id, "provider_profile_id")
        _text(self.description, "condition description", maximum=2_000)
        _typed_sorted(
            self.terminal_requirements,
            ExperimentTerminalRequirement,
            "terminal_requirements",
            "role",
        )
        _ids(
            self.withheld_context_ids,
            "withheld_context_ids",
            allow_empty=True,
        )
        _ids(
            self.withheld_evaluator_ids,
            "withheld_evaluator_ids",
            allow_empty=True,
        )
        if self.source_condition_id is not None:
            require_identifier(self.source_condition_id, "source_condition_id")
        if self.kind is ExperimentConditionKind.FULL:
            if (
                self.withheld_context_ids
                or self.withheld_evaluator_ids
                or self.source_condition_id is not None
            ):
                raise ExperimentProtocolError(
                    "full condition cannot withhold context or evaluation"
                )
        elif self.kind is ExperimentConditionKind.GENERATION_CONTEXT_ABLATION:
            if (
                not self.withheld_context_ids
                or self.withheld_evaluator_ids
                or self.source_condition_id is not None
            ):
                raise ExperimentProtocolError(
                    "generation ablation requires only withheld context"
                )
        elif (
            not self.withheld_evaluator_ids
            or self.withheld_context_ids
            or self.source_condition_id is None
        ):
            raise ExperimentProtocolError(
                "validation ablation requires a source and withheld evaluator"
            )

    @property
    def invokes_provider(self) -> bool:
        return self.kind is not ExperimentConditionKind.VALIDATION_ABLATION

    @property
    def required_terminal_roles(self) -> tuple[str, ...]:
        return tuple(item.role for item in self.terminal_requirements)

    @property
    def condition_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "condition_id": self.condition_id,
            "kind": self.kind.value,
            "provider_profile_id": self.provider_profile_id,
            "description": self.description,
            "terminal_requirements": [
                item.to_dict() for item in self.terminal_requirements
            ],
            "withheld_context_ids": list(self.withheld_context_ids),
            "withheld_evaluator_ids": list(self.withheld_evaluator_ids),
            "source_condition_id": self.source_condition_id,
            "production_route_mutation_authority": False,
            "fallback_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentCondition:
        payload = _mapping(value, "experiment condition")
        _exact(
            payload,
            {
                "schema",
                "condition_id",
                "kind",
                "provider_profile_id",
                "description",
                "terminal_requirements",
                "withheld_context_ids",
                "withheld_evaluator_ids",
                "source_condition_id",
                "production_route_mutation_authority",
                "fallback_authority",
            },
            "experiment condition",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise ExperimentProtocolError("experiment condition authority changed")
        return cls(
            condition_id=payload["condition_id"],
            kind=ExperimentConditionKind(payload["kind"]),
            provider_profile_id=payload["provider_profile_id"],
            description=payload["description"],
            terminal_requirements=tuple(
                ExperimentTerminalRequirement.from_dict(item)
                for item in _list(
                    payload["terminal_requirements"],
                    "terminal_requirements",
                )
            ),
            withheld_context_ids=_string_tuple(
                payload["withheld_context_ids"], "withheld_context_ids"
            ),
            withheld_evaluator_ids=_string_tuple(
                payload["withheld_evaluator_ids"], "withheld_evaluator_ids"
            ),
            source_condition_id=payload["source_condition_id"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentMetricSpec:
    metric_id: str
    value_kind: MetricValueKind
    unit: str
    direction: MetricDirection
    required_for_comparison: bool
    description: str

    SCHEMA = "ExperimentMetricSpec@1"

    def __post_init__(self) -> None:
        require_identifier(self.metric_id, "metric_id")
        if not isinstance(self.value_kind, MetricValueKind):
            raise TypeError("value_kind must be MetricValueKind")
        require_identifier(self.unit, "metric unit")
        if not isinstance(self.direction, MetricDirection):
            raise TypeError("direction must be MetricDirection")
        if not isinstance(self.required_for_comparison, bool):
            raise TypeError("required_for_comparison must be bool")
        _text(self.description, "metric description", maximum=2_000)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "metric_id": self.metric_id,
            "value_kind": self.value_kind.value,
            "unit": self.unit,
            "direction": self.direction.value,
            "required_for_comparison": self.required_for_comparison,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentMetricSpec:
        payload = _mapping(value, "metric spec")
        _exact(
            payload,
            {
                "schema",
                "metric_id",
                "value_kind",
                "unit",
                "direction",
                "required_for_comparison",
                "description",
            },
            "metric spec",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("metric spec schema changed")
        return cls(
            metric_id=payload["metric_id"],
            value_kind=MetricValueKind(payload["value_kind"]),
            unit=payload["unit"],
            direction=MetricDirection(payload["direction"]),
            required_for_comparison=payload["required_for_comparison"],
            description=payload["description"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentAssignment:
    assignment_id: str
    case_id: str
    condition_id: str

    SCHEMA = "ExperimentAssignment@1"

    def __post_init__(self) -> None:
        require_identifier(self.assignment_id, "assignment_id")
        require_identifier(self.case_id, "assignment case_id")
        require_identifier(self.condition_id, "assignment condition_id")

    @property
    def assignment_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assignment_id": self.assignment_id,
            "case_id": self.case_id,
            "condition_id": self.condition_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentAssignment:
        payload = _mapping(value, "experiment assignment")
        _exact(
            payload,
            {"schema", "assignment_id", "case_id", "condition_id"},
            "experiment assignment",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("assignment schema changed")
        return cls(
            assignment_id=payload["assignment_id"],
            case_id=payload["case_id"],
            condition_id=payload["condition_id"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentPreregistration:
    study_id: str
    protocol_version: str
    registered_at: str
    code_identity_digest: str
    contract_identity_digest: str
    provider_profiles: tuple[ExperimentProviderProfile, ...]
    cases: tuple[ExperimentCase, ...]
    conditions: tuple[ExperimentCondition, ...]
    metric_specs: tuple[ExperimentMetricSpec, ...]
    assignments: tuple[ExperimentAssignment, ...]
    maximum_attempts_per_assignment: int
    study_wall_clock_limit_ms: int
    stopping_rule: ExperimentStoppingRule

    SCHEMA = "ExperimentPreregistration@1"

    def __post_init__(self) -> None:
        require_identifier(self.study_id, "study_id")
        require_identifier(self.protocol_version, "protocol_version")
        _timestamp(self.registered_at, "registered_at")
        require_sha256(self.code_identity_digest, "code_identity_digest")
        require_sha256(self.contract_identity_digest, "contract_identity_digest")
        _typed_sorted(
            self.provider_profiles,
            ExperimentProviderProfile,
            "provider_profiles",
            "profile_id",
        )
        _typed_sorted(self.cases, ExperimentCase, "cases", "case_id")
        _typed_sorted(
            self.conditions,
            ExperimentCondition,
            "conditions",
            "condition_id",
        )
        _typed_sorted(
            self.metric_specs,
            ExperimentMetricSpec,
            "metric_specs",
            "metric_id",
        )
        _typed_sorted(
            self.assignments,
            ExperimentAssignment,
            "assignments",
            "assignment_id",
        )
        project_ids = tuple(item.project_id for item in self.cases)
        if len(project_ids) != len(set(project_ids)):
            raise ExperimentProtocolError(
                "each experiment case requires a separate project identity"
            )
        profiles = {item.profile_id for item in self.provider_profiles}
        conditions = {item.condition_id: item for item in self.conditions}
        if any(item.provider_profile_id not in profiles for item in self.conditions):
            raise ExperimentProtocolError("condition names an unknown provider profile")
        used_profiles = {item.provider_profile_id for item in self.conditions}
        if len(used_profiles) != 1 or used_profiles != profiles:
            raise ExperimentProtocolError(
                "comparable conditions require one shared provider profile"
            )
        full_ids = {
            item.condition_id
            for item in self.conditions
            if item.kind is ExperimentConditionKind.FULL
        }
        if len(full_ids) != 1:
            raise ExperimentProtocolError(
                "preregistration requires exactly one full condition"
            )
        full = conditions[next(iter(full_ids))]
        for condition in self.conditions:
            if condition.kind is ExperimentConditionKind.GENERATION_CONTEXT_ABLATION:
                if condition.terminal_requirements != full.terminal_requirements:
                    raise ExperimentProtocolError(
                        "generation ablation changed the terminal authority chain"
                    )
            if condition.kind is ExperimentConditionKind.VALIDATION_ABLATION:
                source = conditions.get(condition.source_condition_id)
                if source is None or not source.invokes_provider:
                    raise ExperimentProtocolError(
                        "validation ablation source must invoke the provider"
                    )
                if source.provider_profile_id != condition.provider_profile_id:
                    raise ExperimentProtocolError(
                        "validation ablation source changed provider configuration"
                    )
                withheld = set(condition.withheld_evaluator_ids)
                source_by_role = {
                    item.role: item for item in source.terminal_requirements
                }
                expected = tuple(
                    item
                    for item in source.terminal_requirements
                    if item.role not in withheld
                )
                if not withheld < set(source_by_role) or (
                    condition.terminal_requirements != expected
                ):
                    raise ExperimentProtocolError(
                        "validation ablation terminal roles do not match its named omission"
                    )
        case_ids = {item.case_id for item in self.cases}
        assignment_pairs = {
            (item.case_id, item.condition_id) for item in self.assignments
        }
        expected_pairs = {
            (case_id, condition_id)
            for case_id in case_ids
            for condition_id in conditions
        }
        if assignment_pairs != expected_pairs:
            raise ExperimentProtocolError(
                "assignments must cover the complete case-condition matrix"
            )
        if type(self.maximum_attempts_per_assignment) is not int or not (
            1 <= self.maximum_attempts_per_assignment <= 100
        ):
            raise ExperimentProtocolError(
                "maximum_attempts_per_assignment must be within [1, 100]"
            )
        if type(self.study_wall_clock_limit_ms) is not int or (
            self.study_wall_clock_limit_ms <= 0
        ):
            raise ExperimentProtocolError(
                "study_wall_clock_limit_ms must be positive"
            )
        if not isinstance(self.stopping_rule, ExperimentStoppingRule):
            raise TypeError("stopping_rule must be ExperimentStoppingRule")

    @property
    def preregistration_digest(self) -> str:
        return digest_value(self._identity())

    def assignment(self, assignment_id: str) -> ExperimentAssignment:
        for item in self.assignments:
            if item.assignment_id == assignment_id:
                return item
        raise ExperimentProtocolError(f"unknown assignment: {assignment_id}")

    def case(self, case_id: str) -> ExperimentCase:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise ExperimentProtocolError(f"unknown case: {case_id}")

    def condition(self, condition_id: str) -> ExperimentCondition:
        for item in self.conditions:
            if item.condition_id == condition_id:
                return item
        raise ExperimentProtocolError(f"unknown condition: {condition_id}")

    def provider_profile(self, profile_id: str) -> ExperimentProviderProfile:
        for item in self.provider_profiles:
            if item.profile_id == profile_id:
                return item
        raise ExperimentProtocolError(f"unknown provider profile: {profile_id}")

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "study_id": self.study_id,
            "protocol_version": self.protocol_version,
            "registered_at": self.registered_at,
            "code_identity_digest": self.code_identity_digest,
            "contract_identity_digest": self.contract_identity_digest,
            "provider_profiles": [item.to_dict() for item in self.provider_profiles],
            "cases": [item.to_dict() for item in self.cases],
            "conditions": [item.to_dict() for item in self.conditions],
            "metric_specs": [item.to_dict() for item in self.metric_specs],
            "assignments": [item.to_dict() for item in self.assignments],
            "maximum_attempts_per_assignment": (
                self.maximum_attempts_per_assignment
            ),
            "study_wall_clock_limit_ms": self.study_wall_clock_limit_ms,
            "stopping_rule": self.stopping_rule.value,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "preregistration_digest": self.preregistration_digest,
            "contains_run_results": False,
            "provider_invocation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentPreregistration:
        payload = _mapping(value, "experiment preregistration")
        _exact(
            payload,
            {
                "schema",
                "study_id",
                "protocol_version",
                "registered_at",
                "code_identity_digest",
                "contract_identity_digest",
                "provider_profiles",
                "cases",
                "conditions",
                "metric_specs",
                "assignments",
                "maximum_attempts_per_assignment",
                "study_wall_clock_limit_ms",
                "stopping_rule",
                "preregistration_digest",
                "contains_run_results",
                "provider_invocation_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "experiment preregistration",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["contains_run_results"] is not False
        ):
            raise ExperimentProtocolError("preregistration authority changed")
        profiles = _list(payload["provider_profiles"], "provider_profiles")
        cases = _list(payload["cases"], "cases")
        conditions = _list(payload["conditions"], "conditions")
        metrics = _list(payload["metric_specs"], "metric_specs")
        assignments = _list(payload["assignments"], "assignments")
        result = cls(
            study_id=payload["study_id"],
            protocol_version=payload["protocol_version"],
            registered_at=payload["registered_at"],
            code_identity_digest=payload["code_identity_digest"],
            contract_identity_digest=payload["contract_identity_digest"],
            provider_profiles=tuple(
                ExperimentProviderProfile.from_dict(item) for item in profiles
            ),
            cases=tuple(ExperimentCase.from_dict(item) for item in cases),
            conditions=tuple(
                ExperimentCondition.from_dict(item) for item in conditions
            ),
            metric_specs=tuple(
                ExperimentMetricSpec.from_dict(item) for item in metrics
            ),
            assignments=tuple(
                ExperimentAssignment.from_dict(item) for item in assignments
            ),
            maximum_attempts_per_assignment=payload[
                "maximum_attempts_per_assignment"
            ],
            study_wall_clock_limit_ms=payload["study_wall_clock_limit_ms"],
            stopping_rule=ExperimentStoppingRule(payload["stopping_rule"]),
        )
        if payload["preregistration_digest"] != result.preregistration_digest:
            raise ExperimentProtocolError("preregistration digest changed")
        return result


@dataclass(frozen=True, slots=True)
class ExperimentProviderReceiptBinding:
    receipt_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    source_schema: str
    record_ref: str
    record_digest: str
    request_digest: str
    p053_envelope_digest: str
    authority_binding_digest: str
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str
    status: str
    duration_ms: int
    input_bytes: int
    output_bytes: int

    SCHEMA = "ExperimentProviderReceiptBinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "provider receipt_id")
        require_identifier(self.project_id, "provider project_id")
        require_identifier(self.run_id, "provider run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("provider base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("provider evidence crosses project base")
        self.base.require_digest()
        _schema_name(self.source_schema, "provider source_schema")
        _project_ref(self.record_ref, self.project_id, "provider record_ref")
        for value, field in (
            (self.record_digest, "provider record_digest"),
            (self.request_digest, "provider request_digest"),
            (self.p053_envelope_digest, "p053_envelope_digest"),
            (self.authority_binding_digest, "authority_binding_digest"),
        ):
            require_sha256(value, field)
        for value, field in (
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (self.provider_version, "provider_version"),
        ):
            require_identifier(value, field)
        require_sha256(self.provider_fingerprint, "provider_fingerprint")
        if self.status not in _PROVIDER_STATUSES:
            raise ExperimentProtocolError("unknown provider receipt status")
        for value, field in (
            (self.duration_ms, "provider duration_ms"),
            (self.input_bytes, "provider input_bytes"),
            (self.output_bytes, "provider output_bytes"),
        ):
            if type(value) is not int or value < 0:
                raise ExperimentProtocolError(f"{field} must be non-negative")

    @property
    def successful(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "source_schema": self.source_schema,
            "record_ref": self.record_ref,
            "record_digest": self.record_digest,
            "request_digest": self.request_digest,
            "p053_envelope_digest": self.p053_envelope_digest,
            "authority_binding_digest": self.authority_binding_digest,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
            "fallback_used": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentProviderReceiptBinding:
        payload = _mapping(value, "provider receipt binding")
        _exact(
            payload,
            {
                "schema",
                "receipt_id",
                "project_id",
                "run_id",
                "base",
                "source_schema",
                "record_ref",
                "record_digest",
                "request_digest",
                "p053_envelope_digest",
                "authority_binding_digest",
                "provider_id",
                "model_id",
                "provider_version",
                "provider_fingerprint",
                "status",
                "duration_ms",
                "input_bytes",
                "output_bytes",
                "fallback_used",
            },
            "provider receipt binding",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["fallback_used"] is not False
        ):
            raise ExperimentProtocolError("provider receipt authority changed")
        return cls(
            receipt_id=payload["receipt_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            source_schema=payload["source_schema"],
            record_ref=payload["record_ref"],
            record_digest=payload["record_digest"],
            request_digest=payload["request_digest"],
            p053_envelope_digest=payload["p053_envelope_digest"],
            authority_binding_digest=payload["authority_binding_digest"],
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
            status=payload["status"],
            duration_ms=payload["duration_ms"],
            input_bytes=payload["input_bytes"],
            output_bytes=payload["output_bytes"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentEvidenceBinding:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    role: str
    source_schema: str
    record_ref: str
    record_digest: str
    source_status: str

    SCHEMA = "ExperimentEvidenceBinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "evidence project_id")
        require_identifier(self.run_id, "evidence run_id")
        require_identifier(self.role, "evidence role")
        _schema_name(self.source_schema, "source_schema")
        require_identifier(self.source_status, "source_status")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("evidence base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("evidence and base cross projects")
        self.base.require_digest()
        _project_ref(self.record_ref, self.project_id, "evidence record_ref")
        require_sha256(self.record_digest, "evidence record_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "role": self.role,
            "source_schema": self.source_schema,
            "record_ref": self.record_ref,
            "record_digest": self.record_digest,
            "source_status": self.source_status,
            "source_authority_preserved": True,
            "claim_upgrade_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentEvidenceBinding:
        payload = _mapping(value, "experiment evidence")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "role",
                "source_schema",
                "record_ref",
                "record_digest",
                "source_status",
                "source_authority_preserved",
                "claim_upgrade_authority",
            },
            "experiment evidence",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["source_authority_preserved"] is not True
        ):
            raise ExperimentProtocolError("experiment evidence authority changed")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            role=payload["role"],
            source_schema=payload["source_schema"],
            record_ref=payload["record_ref"],
            record_digest=payload["record_digest"],
            source_status=payload["source_status"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentAttemptIntent:
    study_id: str
    preregistration_digest: str
    assignment_id: str
    assignment_digest: str
    case_id: str
    condition_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    attempt_id: str
    attempt_index: int
    issued_at: str
    retry_of_attempt_receipt_digest: str | None

    SCHEMA = "ExperimentAttemptIntent@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.study_id, "intent study_id"),
            (self.assignment_id, "intent assignment_id"),
            (self.case_id, "intent case_id"),
            (self.condition_id, "intent condition_id"),
            (self.project_id, "intent project_id"),
            (self.run_id, "intent run_id"),
            (self.attempt_id, "intent attempt_id"),
        ):
            require_identifier(value, field)
        for value, field in (
            (self.preregistration_digest, "preregistration_digest"),
            (self.assignment_digest, "assignment_digest"),
        ):
            require_sha256(value, field)
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("intent base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("attempt intent crosses project identity")
        self.base.require_digest()
        if type(self.attempt_index) is not int or self.attempt_index < 0:
            raise ExperimentProtocolError("attempt_index must be non-negative")
        _timestamp(self.issued_at, "issued_at")
        if self.retry_of_attempt_receipt_digest is not None:
            require_sha256(
                self.retry_of_attempt_receipt_digest,
                "retry_of_attempt_receipt_digest",
            )
        if self.attempt_index == 0 and self.retry_of_attempt_receipt_digest is not None:
            raise ExperimentProtocolError("first attempt cannot cite retry evidence")
        if self.attempt_index > 0 and self.retry_of_attempt_receipt_digest is None:
            raise ExperimentProtocolError("retry intent needs an exact predecessor")

    @property
    def intent_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "study_id": self.study_id,
            "preregistration_digest": self.preregistration_digest,
            "assignment_id": self.assignment_id,
            "assignment_digest": self.assignment_digest,
            "case_id": self.case_id,
            "condition_id": self.condition_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "issued_at": self.issued_at,
            "retry_of_attempt_receipt_digest": (
                self.retry_of_attempt_receipt_digest
            ),
            "contains_result": False,
            "provider_invocation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentAttemptIntent:
        payload = _mapping(value, "experiment attempt intent")
        _exact(
            payload,
            {
                "schema",
                "study_id",
                "preregistration_digest",
                "assignment_id",
                "assignment_digest",
                "case_id",
                "condition_id",
                "project_id",
                "run_id",
                "base",
                "attempt_id",
                "attempt_index",
                "issued_at",
                "retry_of_attempt_receipt_digest",
                "contains_result",
                "provider_invocation_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "experiment attempt intent",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["contains_result"] is not False
        ):
            raise ExperimentProtocolError("attempt intent authority changed")
        return cls(
            study_id=payload["study_id"],
            preregistration_digest=payload["preregistration_digest"],
            assignment_id=payload["assignment_id"],
            assignment_digest=payload["assignment_digest"],
            case_id=payload["case_id"],
            condition_id=payload["condition_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            issued_at=payload["issued_at"],
            retry_of_attempt_receipt_digest=payload[
                "retry_of_attempt_receipt_digest"
            ],
        )


@dataclass(frozen=True, slots=True)
class ExperimentAttemptReceipt:
    study_id: str
    preregistration_digest: str
    assignment_id: str
    assignment_digest: str
    case_id: str
    condition_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    attempt_id: str
    attempt_index: int
    attempt_intent_digest: str
    status: ExperimentAttemptStatus
    duration_ms: int
    provider_receipts: tuple[ExperimentProviderReceiptBinding, ...]
    terminal_evidence: tuple[ExperimentEvidenceBinding, ...]
    source_attempt_receipt_digest: str | None
    retry_of_attempt_receipt_digest: str | None
    error_code: str | None
    message: str | None

    SCHEMA = "ExperimentAttemptReceipt@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.study_id, "study_id"),
            (self.assignment_id, "assignment_id"),
            (self.case_id, "case_id"),
            (self.condition_id, "condition_id"),
            (self.project_id, "project_id"),
            (self.run_id, "run_id"),
            (self.attempt_id, "attempt_id"),
        ):
            require_identifier(value, field)
        for value, field in (
            (self.preregistration_digest, "preregistration_digest"),
            (self.assignment_digest, "assignment_digest"),
            (self.attempt_intent_digest, "attempt_intent_digest"),
        ):
            require_sha256(value, field)
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("attempt base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("attempt and base cross projects")
        self.base.require_digest()
        if type(self.attempt_index) is not int or self.attempt_index < 0:
            raise ExperimentProtocolError("attempt_index must be non-negative")
        if not isinstance(self.status, ExperimentAttemptStatus):
            raise TypeError("status must be ExperimentAttemptStatus")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ExperimentProtocolError("duration_ms must be non-negative")
        _typed_sorted(
            self.provider_receipts,
            ExperimentProviderReceiptBinding,
            "provider_receipts",
            "receipt_id",
            allow_empty=True,
        )
        _typed_sorted(
            self.terminal_evidence,
            ExperimentEvidenceBinding,
            "terminal_evidence",
            "record_ref",
            allow_empty=True,
        )
        for value, field in (
            (self.source_attempt_receipt_digest, "source_attempt_receipt_digest"),
            (self.retry_of_attempt_receipt_digest, "retry_of_attempt_receipt_digest"),
        ):
            if value is not None:
                require_sha256(value, field)
        if self.attempt_index == 0 and self.retry_of_attempt_receipt_digest is not None:
            raise ExperimentProtocolError("first attempt cannot cite retry evidence")
        if self.attempt_index > 0 and self.retry_of_attempt_receipt_digest is None:
            raise ExperimentProtocolError("retry attempt requires exact predecessor")
        if self.status is ExperimentAttemptStatus.COMPLETED:
            if not self.terminal_evidence or self.error_code is not None:
                raise ExperimentProtocolError(
                    "terminal attempt needs evidence and no execution error"
                )
        elif self.status is ExperimentAttemptStatus.PIPELINE_REJECTED:
            if self.error_code is None:
                raise ExperimentProtocolError(
                    "pipeline rejection needs an exact rejection error"
                )
        elif self.terminal_evidence or self.error_code is None:
            raise ExperimentProtocolError(
                "failed attempt needs an error and no terminal evidence"
            )
        if self.error_code is not None:
            require_identifier(self.error_code, "error_code")
        if self.message is not None:
            _text(self.message, "attempt message", maximum=2_000)

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "study_id": self.study_id,
            "preregistration_digest": self.preregistration_digest,
            "assignment_id": self.assignment_id,
            "assignment_digest": self.assignment_digest,
            "case_id": self.case_id,
            "condition_id": self.condition_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "attempt_intent_digest": self.attempt_intent_digest,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "provider_receipts": [
                item.to_dict() for item in self.provider_receipts
            ],
            "terminal_evidence": [item.to_dict() for item in self.terminal_evidence],
            "source_attempt_receipt_digest": self.source_attempt_receipt_digest,
            "retry_of_attempt_receipt_digest": (
                self.retry_of_attempt_receipt_digest
            ),
            "error_code": self.error_code,
            "message": self.message,
            "fallback_used": False,
            "provider_invocation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentAttemptReceipt:
        payload = _mapping(value, "experiment attempt")
        _exact(
            payload,
            {
                "schema",
                "study_id",
                "preregistration_digest",
                "assignment_id",
                "assignment_digest",
                "case_id",
                "condition_id",
                "project_id",
                "run_id",
                "base",
                "attempt_id",
                "attempt_index",
                "attempt_intent_digest",
                "status",
                "duration_ms",
                "provider_receipts",
                "terminal_evidence",
                "source_attempt_receipt_digest",
                "retry_of_attempt_receipt_digest",
                "error_code",
                "message",
                "fallback_used",
                "provider_invocation_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "experiment attempt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["fallback_used"] is not False
        ):
            raise ExperimentProtocolError("experiment attempt authority changed")
        provider = _list(payload["provider_receipts"], "provider_receipts")
        evidence = _list(payload["terminal_evidence"], "terminal_evidence")
        return cls(
            study_id=payload["study_id"],
            preregistration_digest=payload["preregistration_digest"],
            assignment_id=payload["assignment_id"],
            assignment_digest=payload["assignment_digest"],
            case_id=payload["case_id"],
            condition_id=payload["condition_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            attempt_intent_digest=payload["attempt_intent_digest"],
            status=ExperimentAttemptStatus(payload["status"]),
            duration_ms=payload["duration_ms"],
            provider_receipts=tuple(
                ExperimentProviderReceiptBinding.from_dict(item)
                for item in provider
            ),
            terminal_evidence=tuple(
                ExperimentEvidenceBinding.from_dict(item) for item in evidence
            ),
            source_attempt_receipt_digest=payload[
                "source_attempt_receipt_digest"
            ],
            retry_of_attempt_receipt_digest=payload[
                "retry_of_attempt_receipt_digest"
            ],
            error_code=payload["error_code"],
            message=payload["message"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentMetricObservation:
    metric_id: str
    status: MetricObservationStatus
    value: bool | int | float | None
    evidence: tuple[ExperimentEvidenceBinding, ...]
    reason: str | None

    SCHEMA = "ExperimentMetricObservation@1"

    def __post_init__(self) -> None:
        require_identifier(self.metric_id, "observation metric_id")
        if not isinstance(self.status, MetricObservationStatus):
            raise TypeError("status must be MetricObservationStatus")
        _typed_sorted(
            self.evidence,
            ExperimentEvidenceBinding,
            "metric evidence",
            "record_ref",
            allow_empty=True,
        )
        if self.status is MetricObservationStatus.MEASURED:
            if self.value is None or not self.evidence or self.reason is not None:
                raise ExperimentProtocolError(
                    "measured metric needs a value and evidence only"
                )
        elif self.value is not None or self.reason is None:
            raise ExperimentProtocolError(
                "unknown or inapplicable metric needs a reason and no value"
            )
        if self.reason is not None:
            _text(self.reason, "metric reason", maximum=2_000)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "metric_id": self.metric_id,
            "status": self.status.value,
            "value": self.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentMetricObservation:
        payload = _mapping(value, "metric observation")
        _exact(
            payload,
            {"schema", "metric_id", "status", "value", "evidence", "reason"},
            "metric observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("metric observation schema changed")
        evidence = _list(payload["evidence"], "metric evidence")
        return cls(
            metric_id=payload["metric_id"],
            status=MetricObservationStatus(payload["status"]),
            value=payload["value"],
            evidence=tuple(
                ExperimentEvidenceBinding.from_dict(item) for item in evidence
            ),
            reason=payload["reason"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    study_id: str
    preregistration_digest: str
    assignment_id: str
    assignment_digest: str
    attempt_receipt_digest: str
    attempt_status: ExperimentAttemptStatus
    observations: tuple[ExperimentMetricObservation, ...]
    eligible_for_comparison: bool

    SCHEMA = "ExperimentOutcome@1"

    def __post_init__(self) -> None:
        require_identifier(self.study_id, "outcome study_id")
        require_identifier(self.assignment_id, "outcome assignment_id")
        for value, field in (
            (self.preregistration_digest, "preregistration_digest"),
            (self.assignment_digest, "assignment_digest"),
            (self.attempt_receipt_digest, "attempt_receipt_digest"),
        ):
            require_sha256(value, field)
        if not isinstance(self.attempt_status, ExperimentAttemptStatus):
            raise TypeError("attempt_status must be ExperimentAttemptStatus")
        _typed_sorted(
            self.observations,
            ExperimentMetricObservation,
            "observations",
            "metric_id",
        )
        if not isinstance(self.eligible_for_comparison, bool):
            raise TypeError("eligible_for_comparison must be bool")

    @property
    def outcome_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "study_id": self.study_id,
            "preregistration_digest": self.preregistration_digest,
            "assignment_id": self.assignment_id,
            "assignment_digest": self.assignment_digest,
            "attempt_receipt_digest": self.attempt_receipt_digest,
            "attempt_status": self.attempt_status.value,
            "observations": [item.to_dict() for item in self.observations],
            "eligible_for_comparison": self.eligible_for_comparison,
            "aggregate_winner_claimed": False,
            "validation_override_authority": False,
            "promotion_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentOutcome:
        payload = _mapping(value, "experiment outcome")
        _exact(
            payload,
            {
                "schema",
                "study_id",
                "preregistration_digest",
                "assignment_id",
                "assignment_digest",
                "attempt_receipt_digest",
                "attempt_status",
                "observations",
                "eligible_for_comparison",
                "aggregate_winner_claimed",
                "validation_override_authority",
                "promotion_authority",
                "canonical_write_authority",
            },
            "experiment outcome",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["aggregate_winner_claimed"] is not False
        ):
            raise ExperimentProtocolError("experiment outcome authority changed")
        observations = _list(payload["observations"], "observations")
        return cls(
            study_id=payload["study_id"],
            preregistration_digest=payload["preregistration_digest"],
            assignment_id=payload["assignment_id"],
            assignment_digest=payload["assignment_digest"],
            attempt_receipt_digest=payload["attempt_receipt_digest"],
            attempt_status=ExperimentAttemptStatus(payload["attempt_status"]),
            observations=tuple(
                ExperimentMetricObservation.from_dict(item)
                for item in observations
            ),
            eligible_for_comparison=payload["eligible_for_comparison"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentStudyRecordBinding:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    source_schema: str
    record_ref: str
    record_digest: str
    content_digest: str

    SCHEMA = "ExperimentStudyRecordBinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "study record project_id")
        require_identifier(self.run_id, "study record run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("study record base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("study record and base cross projects")
        self.base.require_digest()
        _schema_name(self.source_schema, "study record source_schema")
        _project_ref(self.record_ref, self.project_id, "study record_ref")
        expected_prefix = (
            f"project://{self.project_id}/runs/{self.run_id}/records/"
        )
        if not self.record_ref.startswith(expected_prefix):
            raise ExperimentProtocolError("study record is outside its run")
        require_sha256(self.record_digest, "study record_digest")
        require_sha256(self.content_digest, "study content_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "source_schema": self.source_schema,
            "record_ref": self.record_ref,
            "record_digest": self.record_digest,
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentStudyRecordBinding:
        payload = _mapping(value, "experiment study record binding")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "source_schema",
                "record_ref",
                "record_digest",
                "content_digest",
            },
            "experiment study record binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("study record binding schema changed")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            source_schema=payload["source_schema"],
            record_ref=payload["record_ref"],
            record_digest=payload["record_digest"],
            content_digest=payload["content_digest"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentIndexedAttempt:
    attempt_id: str
    attempt_index: int
    intent: ExperimentStudyRecordBinding
    receipt_status: ExperimentAttemptStatus | None
    receipt: ExperimentStudyRecordBinding | None
    outcome: ExperimentStudyRecordBinding | None
    eligible_for_comparison: bool | None

    SCHEMA = "ExperimentIndexedAttempt@1"

    def __post_init__(self) -> None:
        require_identifier(self.attempt_id, "indexed attempt_id")
        if type(self.attempt_index) is not int or self.attempt_index < 0:
            raise ExperimentProtocolError(
                "indexed attempt_index must be non-negative"
            )
        if not isinstance(self.intent, ExperimentStudyRecordBinding) or (
            self.intent.source_schema != ExperimentAttemptIntent.SCHEMA
        ):
            raise ExperimentProtocolError(
                "indexed attempt requires an intent binding"
            )
        if self.receipt is None:
            if (
                self.receipt_status is not None
                or self.outcome is not None
                or self.eligible_for_comparison is not None
            ):
                raise ExperimentProtocolError(
                    "running attempt cannot carry terminal result fields"
                )
            return
        if not isinstance(self.receipt, ExperimentStudyRecordBinding) or (
            self.receipt.source_schema != ExperimentAttemptReceipt.SCHEMA
        ):
            raise ExperimentProtocolError(
                "indexed receipt binding schema changed"
            )
        if not isinstance(self.receipt_status, ExperimentAttemptStatus):
            raise TypeError("indexed receipt_status must be ExperimentAttemptStatus")
        if self.outcome is None:
            if self.eligible_for_comparison is not None:
                raise ExperimentProtocolError(
                    "unmeasured terminal attempt cannot claim comparability"
                )
            return
        if not isinstance(self.outcome, ExperimentStudyRecordBinding) or (
            self.outcome.source_schema != ExperimentOutcome.SCHEMA
        ):
            raise ExperimentProtocolError("indexed outcome binding schema changed")
        if not isinstance(self.eligible_for_comparison, bool):
            raise TypeError("measured attempt comparability must be bool")

    @property
    def terminal_recorded(self) -> bool:
        return self.receipt is not None

    @property
    def measured(self) -> bool:
        return self.outcome is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "intent": self.intent.to_dict(),
            "receipt_status": (
                self.receipt_status.value
                if self.receipt_status is not None
                else None
            ),
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "outcome": self.outcome.to_dict() if self.outcome is not None else None,
            "eligible_for_comparison": self.eligible_for_comparison,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentIndexedAttempt:
        payload = _mapping(value, "indexed attempt")
        _exact(
            payload,
            {
                "schema",
                "attempt_id",
                "attempt_index",
                "intent",
                "receipt_status",
                "receipt",
                "outcome",
                "eligible_for_comparison",
            },
            "indexed attempt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("indexed attempt schema changed")
        return cls(
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            intent=ExperimentStudyRecordBinding.from_dict(payload["intent"]),
            receipt_status=(
                ExperimentAttemptStatus(payload["receipt_status"])
                if payload["receipt_status"] is not None
                else None
            ),
            receipt=(
                ExperimentStudyRecordBinding.from_dict(payload["receipt"])
                if payload["receipt"] is not None
                else None
            ),
            outcome=(
                ExperimentStudyRecordBinding.from_dict(payload["outcome"])
                if payload["outcome"] is not None
                else None
            ),
            eligible_for_comparison=payload["eligible_for_comparison"],
        )


@dataclass(frozen=True, slots=True)
class ExperimentAssignmentResult:
    assignment_id: str
    assignment_digest: str
    case_id: str
    condition_id: str
    lifecycle: ExperimentAssignmentLifecycle
    attempts: tuple[ExperimentIndexedAttempt, ...]

    SCHEMA = "ExperimentAssignmentResult@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.assignment_id, "result assignment_id"),
            (self.case_id, "result case_id"),
            (self.condition_id, "result condition_id"),
        ):
            require_identifier(value, field)
        require_sha256(self.assignment_digest, "result assignment_digest")
        if not isinstance(self.lifecycle, ExperimentAssignmentLifecycle):
            raise TypeError("lifecycle must be ExperimentAssignmentLifecycle")
        _typed_sorted(
            self.attempts,
            ExperimentIndexedAttempt,
            "indexed attempts",
            "attempt_index",
            allow_empty=True,
        )
        if tuple(item.attempt_index for item in self.attempts) != tuple(
            range(len(self.attempts))
        ):
            raise ExperimentProtocolError(
                "indexed attempt sequence must be contiguous from zero"
            )
        expected = _assignment_lifecycle(self.attempts)
        if self.lifecycle is not expected:
            raise ExperimentProtocolError(
                "assignment lifecycle disagrees with retained records"
            )

    @property
    def latest_attempt_status(self) -> ExperimentAttemptStatus | None:
        if not self.attempts:
            return None
        return self.attempts[-1].receipt_status

    @property
    def eligible_for_comparison(self) -> bool:
        return bool(
            self.attempts
            and self.attempts[-1].eligible_for_comparison is True
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assignment_id": self.assignment_id,
            "assignment_digest": self.assignment_digest,
            "case_id": self.case_id,
            "condition_id": self.condition_id,
            "lifecycle": self.lifecycle.value,
            "attempts": [item.to_dict() for item in self.attempts],
            "latest_attempt_status": (
                self.latest_attempt_status.value
                if self.latest_attempt_status is not None
                else None
            ),
            "eligible_for_comparison": self.eligible_for_comparison,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentAssignmentResult:
        payload = _mapping(value, "assignment result")
        _exact(
            payload,
            {
                "schema",
                "assignment_id",
                "assignment_digest",
                "case_id",
                "condition_id",
                "lifecycle",
                "attempts",
                "latest_attempt_status",
                "eligible_for_comparison",
            },
            "assignment result",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ExperimentProtocolError("assignment result schema changed")
        attempts = tuple(
            ExperimentIndexedAttempt.from_dict(item)
            for item in _list(payload["attempts"], "indexed attempts")
        )
        result = cls(
            assignment_id=payload["assignment_id"],
            assignment_digest=payload["assignment_digest"],
            case_id=payload["case_id"],
            condition_id=payload["condition_id"],
            lifecycle=ExperimentAssignmentLifecycle(payload["lifecycle"]),
            attempts=attempts,
        )
        if payload["latest_attempt_status"] != (
            result.latest_attempt_status.value
            if result.latest_attempt_status is not None
            else None
        ) or payload["eligible_for_comparison"] is not (
            result.eligible_for_comparison
        ):
            raise ExperimentProtocolError("assignment result summary drifted")
        return result


@dataclass(frozen=True, slots=True)
class ExperimentResultIndex:
    study_id: str
    preregistration_digest: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    generated_at: str
    assignments: tuple[ExperimentAssignmentResult, ...]

    SCHEMA = "ExperimentResultIndex@1"

    def __post_init__(self) -> None:
        require_identifier(self.study_id, "result index study_id")
        require_sha256(self.preregistration_digest, "preregistration_digest")
        require_identifier(self.project_id, "result index project_id")
        require_identifier(self.run_id, "result index run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("result index base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ExperimentProtocolError("result index and base cross projects")
        self.base.require_digest()
        _timestamp(self.generated_at, "result index generated_at")
        _typed_sorted(
            self.assignments,
            ExperimentAssignmentResult,
            "assignment results",
            "assignment_id",
        )

    @property
    def lifecycle_counts(self) -> dict[str, int]:
        return {
            lifecycle.value: sum(
                item.lifecycle is lifecycle for item in self.assignments
            )
            for lifecycle in ExperimentAssignmentLifecycle
        }

    @property
    def comparable_sample_size(self) -> int:
        return sum(item.eligible_for_comparison for item in self.assignments)

    @property
    def evidence_table_ready(self) -> bool:
        return all(
            item.lifecycle
            in {
                ExperimentAssignmentLifecycle.FAILED,
                ExperimentAssignmentLifecycle.COMPLETED,
            }
            for item in self.assignments
        )

    @property
    def index_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "study_id": self.study_id,
            "preregistration_digest": self.preregistration_digest,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "generated_at": self.generated_at,
            "assignments": [item.to_dict() for item in self.assignments],
            "lifecycle_counts": self.lifecycle_counts,
            "comparable_sample_size": self.comparable_sample_size,
            "evidence_table_ready": self.evidence_table_ready,
            "unrun_results_included": False,
            "aggregate_winner_claimed": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExperimentResultIndex:
        payload = _mapping(value, "experiment result index")
        _exact(
            payload,
            {
                "schema",
                "study_id",
                "preregistration_digest",
                "project_id",
                "run_id",
                "base",
                "generated_at",
                "assignments",
                "lifecycle_counts",
                "comparable_sample_size",
                "evidence_table_ready",
                "unrun_results_included",
                "aggregate_winner_claimed",
                "persistence_authority",
                "canonical_write_authority",
            },
            "experiment result index",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["unrun_results_included"] is not False
            or payload["aggregate_winner_claimed"] is not False
        ):
            raise ExperimentProtocolError("result index authority changed")
        result = cls(
            study_id=payload["study_id"],
            preregistration_digest=payload["preregistration_digest"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            generated_at=payload["generated_at"],
            assignments=tuple(
                ExperimentAssignmentResult.from_dict(item)
                for item in _list(payload["assignments"], "assignment results")
            ),
        )
        if (
            payload["lifecycle_counts"] != result.lifecycle_counts
            or payload["comparable_sample_size"] != result.comparable_sample_size
            or payload["evidence_table_ready"] is not result.evidence_table_ready
        ):
            raise ExperimentProtocolError("result index summary drifted")
        return result


def compile_experiment_result_index(
    preregistration: ExperimentPreregistration,
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    generated_at: str,
    intents: tuple[
        tuple[ExperimentStudyRecordBinding, ExperimentAttemptIntent], ...
    ],
    receipts: tuple[
        tuple[ExperimentStudyRecordBinding, ExperimentAttemptReceipt], ...
    ],
    outcomes: tuple[
        tuple[ExperimentStudyRecordBinding, ExperimentOutcome], ...
    ],
) -> ExperimentResultIndex:
    """Rebuild assignment state from exact retained study-record bindings."""

    if not isinstance(preregistration, ExperimentPreregistration):
        raise TypeError("preregistration must be ExperimentPreregistration")
    require_identifier(project_id, "result index project_id")
    require_identifier(run_id, "result index run_id")
    if not isinstance(base, ProjectVersionRef) or base.project_id != project_id:
        raise ExperimentProtocolError("result index base crosses projects")
    base.require_digest()
    _timestamp(generated_at, "result index generated_at")
    all_pairs: tuple[tuple[object, object], ...] = (
        *intents,
        *receipts,
        *outcomes,
    )
    bindings: list[ExperimentStudyRecordBinding] = []
    for pair in all_pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError("study records must be binding/value pairs")
        binding = pair[0]
        if not isinstance(binding, ExperimentStudyRecordBinding):
            raise TypeError("study record binding is invalid")
        if (binding.project_id, binding.run_id, binding.base) != (
            project_id,
            run_id,
            base,
        ):
            raise ExperimentProtocolError("study record crosses result index")
        bindings.append(binding)
    if len({item.record_ref for item in bindings}) != len(bindings):
        raise ExperimentProtocolError("study record is indexed more than once")
    if len({item.content_digest for item in bindings}) != len(bindings):
        raise ExperimentProtocolError("study content is indexed more than once")

    intent_by_digest: dict[str, tuple[ExperimentStudyRecordBinding, ExperimentAttemptIntent]] = {}
    for binding, intent in intents:
        if not isinstance(intent, ExperimentAttemptIntent):
            raise TypeError("indexed intent is invalid")
        _require_index_content(
            preregistration,
            binding,
            intent,
            ExperimentAttemptIntent.SCHEMA,
            intent.intent_digest,
        )
        intent_by_digest[intent.intent_digest] = (binding, intent)

    receipt_by_intent: dict[str, tuple[ExperimentStudyRecordBinding, ExperimentAttemptReceipt]] = {}
    receipt_by_digest: dict[str, tuple[ExperimentStudyRecordBinding, ExperimentAttemptReceipt]] = {}
    for binding, receipt in receipts:
        if not isinstance(receipt, ExperimentAttemptReceipt):
            raise TypeError("indexed receipt is invalid")
        _require_index_content(
            preregistration,
            binding,
            receipt,
            ExperimentAttemptReceipt.SCHEMA,
            receipt.receipt_digest,
        )
        intent_pair = intent_by_digest.get(receipt.attempt_intent_digest)
        if intent_pair is None or _attempt_identity(intent_pair[1]) != (
            receipt.assignment_id,
            receipt.attempt_id,
            receipt.attempt_index,
        ):
            raise ExperimentProtocolError("receipt has no exact retained intent")
        if receipt.attempt_intent_digest in receipt_by_intent:
            raise ExperimentProtocolError("intent has more than one receipt")
        receipt_by_intent[receipt.attempt_intent_digest] = (binding, receipt)
        receipt_by_digest[receipt.receipt_digest] = (binding, receipt)

    outcome_by_receipt: dict[str, tuple[ExperimentStudyRecordBinding, ExperimentOutcome]] = {}
    for binding, outcome in outcomes:
        if not isinstance(outcome, ExperimentOutcome):
            raise TypeError("indexed outcome is invalid")
        _require_index_content(
            preregistration,
            binding,
            outcome,
            ExperimentOutcome.SCHEMA,
            outcome.outcome_digest,
        )
        receipt_pair = receipt_by_digest.get(outcome.attempt_receipt_digest)
        if receipt_pair is None or (
            receipt_pair[1].assignment_id != outcome.assignment_id
            or receipt_pair[1].assignment_digest != outcome.assignment_digest
            or receipt_pair[1].status is not outcome.attempt_status
        ):
            raise ExperimentProtocolError("outcome has no exact retained receipt")
        if outcome.attempt_receipt_digest in outcome_by_receipt:
            raise ExperimentProtocolError("receipt has more than one outcome")
        outcome_by_receipt[outcome.attempt_receipt_digest] = (binding, outcome)

    assignment_results = []
    for assignment in preregistration.assignments:
        assignment_intents = sorted(
            (
                pair
                for pair in intent_by_digest.values()
                if pair[1].assignment_id == assignment.assignment_id
            ),
            key=lambda pair: pair[1].attempt_index,
        )
        indexed_attempts = []
        prior_receipt: ExperimentAttemptReceipt | None = None
        for binding, intent in assignment_intents:
            if intent.attempt_index != len(indexed_attempts):
                raise ExperimentProtocolError(
                    "assignment attempts are not contiguous from zero"
                )
            if intent.attempt_index == 0:
                if intent.retry_of_attempt_receipt_digest is not None:
                    raise ExperimentProtocolError("first indexed attempt is a retry")
            elif prior_receipt is None or (
                intent.retry_of_attempt_receipt_digest
                != prior_receipt.receipt_digest
            ):
                raise ExperimentProtocolError(
                    "indexed retry does not cite its retained predecessor"
                )
            elif prior_receipt.status is ExperimentAttemptStatus.COMPLETED:
                raise ExperimentProtocolError(
                    "indexed completed assignment has an extra retry"
                )
            receipt_pair = receipt_by_intent.get(intent.intent_digest)
            if receipt_pair is None:
                prior_receipt = None
                indexed_attempts.append(
                    ExperimentIndexedAttempt(
                        attempt_id=intent.attempt_id,
                        attempt_index=intent.attempt_index,
                        intent=binding,
                        receipt_status=None,
                        receipt=None,
                        outcome=None,
                        eligible_for_comparison=None,
                    )
                )
                continue
            receipt_binding, receipt = receipt_pair
            outcome_pair = outcome_by_receipt.get(receipt.receipt_digest)
            indexed_attempts.append(
                ExperimentIndexedAttempt(
                    attempt_id=intent.attempt_id,
                    attempt_index=intent.attempt_index,
                    intent=binding,
                    receipt_status=receipt.status,
                    receipt=receipt_binding,
                    outcome=outcome_pair[0] if outcome_pair is not None else None,
                    eligible_for_comparison=(
                        outcome_pair[1].eligible_for_comparison
                        if outcome_pair is not None
                        else None
                    ),
                )
            )
            prior_receipt = receipt
        assignment_results.append(
            ExperimentAssignmentResult(
                assignment_id=assignment.assignment_id,
                assignment_digest=assignment.assignment_digest,
                case_id=assignment.case_id,
                condition_id=assignment.condition_id,
                lifecycle=_assignment_lifecycle(tuple(indexed_attempts)),
                attempts=tuple(indexed_attempts),
            )
        )
    return ExperimentResultIndex(
        study_id=preregistration.study_id,
        preregistration_digest=preregistration.preregistration_digest,
        project_id=project_id,
        run_id=run_id,
        base=base,
        generated_at=generated_at,
        assignments=tuple(sorted(assignment_results, key=lambda item: item.assignment_id)),
    )


def compile_experiment_attempt_intent(
    preregistration: ExperimentPreregistration,
    *,
    assignment_id: str,
    attempt_id: str,
    attempt_index: int,
    issued_at: str,
    retry_of: ExperimentAttemptReceipt | None = None,
) -> ExperimentAttemptIntent:
    """Create the immutable pre-execution boundary for one bounded attempt."""

    if not isinstance(preregistration, ExperimentPreregistration):
        raise TypeError("preregistration must be ExperimentPreregistration")
    assignment = preregistration.assignment(assignment_id)
    case = preregistration.case(assignment.case_id)
    if attempt_index >= preregistration.maximum_attempts_per_assignment:
        raise ExperimentProtocolError("attempt exceeds preregistered bound")
    if attempt_index == 0:
        if retry_of is not None:
            raise ExperimentProtocolError("first attempt cannot cite retry evidence")
    else:
        if not isinstance(retry_of, ExperimentAttemptReceipt):
            raise ExperimentProtocolError(
                "retry intent requires the exact predecessor receipt"
            )
        if (
            retry_of.study_id != preregistration.study_id
            or retry_of.preregistration_digest
            != preregistration.preregistration_digest
            or retry_of.assignment_id != assignment.assignment_id
            or retry_of.assignment_digest != assignment.assignment_digest
            or retry_of.attempt_index + 1 != attempt_index
        ):
            raise ExperimentProtocolError(
                "retry predecessor does not match the assignment or index"
            )
        if retry_of.status is ExperimentAttemptStatus.COMPLETED:
            raise ExperimentProtocolError(
                "completed assignment cannot be retried"
            )
    return ExperimentAttemptIntent(
        study_id=preregistration.study_id,
        preregistration_digest=preregistration.preregistration_digest,
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        case_id=case.case_id,
        condition_id=assignment.condition_id,
        project_id=case.project_id,
        run_id=case.run_id,
        base=case.base,
        attempt_id=attempt_id,
        attempt_index=attempt_index,
        issued_at=issued_at,
        retry_of_attempt_receipt_digest=(
            retry_of.receipt_digest if retry_of is not None else None
        ),
    )


def compile_experiment_attempt(
    preregistration: ExperimentPreregistration,
    intent: ExperimentAttemptIntent,
    *,
    status: ExperimentAttemptStatus,
    duration_ms: int,
    provider_receipts: tuple[ExperimentProviderReceiptBinding, ...],
    terminal_evidence: tuple[ExperimentEvidenceBinding, ...],
    source_attempt: ExperimentAttemptReceipt | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> ExperimentAttemptReceipt:
    """Bind one actual attempt to the preregistered assignment and policy."""

    if not isinstance(preregistration, ExperimentPreregistration):
        raise TypeError("preregistration must be ExperimentPreregistration")
    if not isinstance(intent, ExperimentAttemptIntent):
        raise TypeError("intent must be ExperimentAttemptIntent")
    assignment = preregistration.assignment(intent.assignment_id)
    case = preregistration.case(assignment.case_id)
    condition = preregistration.condition(assignment.condition_id)
    profile = preregistration.provider_profile(condition.provider_profile_id)
    if (
        intent.study_id != preregistration.study_id
        or intent.preregistration_digest
        != preregistration.preregistration_digest
        or intent.assignment_digest != assignment.assignment_digest
        or (intent.case_id, intent.condition_id)
        != (assignment.case_id, assignment.condition_id)
        or (intent.project_id, intent.run_id, intent.base)
        != (case.project_id, case.run_id, case.base)
        or intent.attempt_index
        >= preregistration.maximum_attempts_per_assignment
    ):
        raise ExperimentProtocolError(
            "attempt intent does not bind the exact preregistration"
        )
    if duration_ms > preregistration.study_wall_clock_limit_ms:
        raise ExperimentProtocolError("attempt exceeds preregistered study budget")
    if condition.invokes_provider:
        if not provider_receipts:
            raise ExperimentProtocolError(
                "provider-invoking condition needs exact receipt evidence"
            )
        for receipt in provider_receipts:
            if (
                receipt.project_id != case.project_id
                or receipt.run_id != case.run_id
                or receipt.base != case.base
                or receipt.provider_id != profile.provider_id
                or receipt.model_id != profile.model_id
                or receipt.provider_version != profile.provider_version
                or receipt.provider_fingerprint != profile.provider_fingerprint
                or receipt.duration_ms > duration_ms
                or (
                    receipt.status != "timeout"
                    and receipt.duration_ms > profile.timeout_ms
                )
            ):
                raise ExperimentProtocolError(
                    "provider receipt changed the preregistered profile"
                )
        if source_attempt is not None:
            raise ExperimentProtocolError(
                "provider condition cannot reuse a source attempt"
            )
    else:
        if provider_receipts or not isinstance(
            source_attempt, ExperimentAttemptReceipt
        ):
            raise ExperimentProtocolError(
                "validation ablation must reuse one exact source attempt"
            )
        if (
            source_attempt.study_id != preregistration.study_id
            or source_attempt.preregistration_digest
            != preregistration.preregistration_digest
            or source_attempt.case_id != case.case_id
            or source_attempt.condition_id != condition.source_condition_id
            or source_attempt.project_id != case.project_id
            or source_attempt.run_id != case.run_id
            or source_attempt.base != case.base
            or source_attempt.status
            not in {
                ExperimentAttemptStatus.COMPLETED,
                ExperimentAttemptStatus.PIPELINE_REJECTED,
            }
        ):
            raise ExperimentProtocolError(
                "validation ablation source is not the exact terminal source condition"
            )
    statuses = {item.status for item in provider_receipts}
    if status is ExperimentAttemptStatus.COMPLETED and any(
        not item.successful for item in provider_receipts
    ):
        raise ExperimentProtocolError(
            "completed attempt cannot contain a failed provider receipt"
        )
    if status is ExperimentAttemptStatus.PIPELINE_REJECTED and any(
        not item.successful for item in provider_receipts
    ):
        raise ExperimentProtocolError(
            "pipeline rejection cannot hide provider failure"
        )
    if status is ExperimentAttemptStatus.PROVIDER_FAILED and (
        not provider_receipts or all(item.successful for item in provider_receipts)
    ):
        raise ExperimentProtocolError(
            "provider failure requires a failed provider receipt"
        )
    if status is ExperimentAttemptStatus.TIMED_OUT and "timeout" not in statuses:
        raise ExperimentProtocolError("timeout requires an exact timeout receipt")
    for item in terminal_evidence:
        if (
            item.project_id != case.project_id
            or item.run_id != case.run_id
            or item.base != case.base
        ):
            raise ExperimentProtocolError(
                "terminal evidence crosses the assigned project run or base"
            )
    terminal_by_role = {item.role: item for item in terminal_evidence}
    if len(terminal_by_role) != len(terminal_evidence):
        raise ExperimentProtocolError(
            "attempt terminal evidence requires one record per authority role"
        )
    requirements = {
        item.role: item for item in condition.terminal_requirements
    }
    required_roles = set(requirements)
    actual_roles = set(terminal_by_role)
    for role, evidence in terminal_by_role.items():
        requirement = requirements.get(role)
        if requirement is None or (
            evidence.source_schema != requirement.source_schema
            or evidence.source_status not in requirement.accepted_statuses
        ):
            raise ExperimentProtocolError(
                "terminal evidence changed its preregistered schema or status"
            )
    if status is ExperimentAttemptStatus.COMPLETED and actual_roles != required_roles:
        raise ExperimentProtocolError(
            "completed attempt lacks the preregistered terminal authority chain"
        )
    if (
        status is ExperimentAttemptStatus.PIPELINE_REJECTED
        and not actual_roles <= required_roles
    ):
        raise ExperimentProtocolError(
            "pipeline rejection carries an unregistered terminal authority role"
        )
    if not condition.invokes_provider:
        source_by_role = {
            item.role: item for item in source_attempt.terminal_evidence
        }
        if any(
            source_by_role.get(role) != evidence
            for role, evidence in terminal_by_role.items()
        ):
            raise ExperimentProtocolError(
                "validation ablation changed retained non-withheld evidence"
            )
    return ExperimentAttemptReceipt(
        study_id=preregistration.study_id,
        preregistration_digest=preregistration.preregistration_digest,
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        case_id=case.case_id,
        condition_id=condition.condition_id,
        project_id=case.project_id,
        run_id=case.run_id,
        base=case.base,
        attempt_id=intent.attempt_id,
        attempt_index=intent.attempt_index,
        attempt_intent_digest=intent.intent_digest,
        status=status,
        duration_ms=duration_ms,
        provider_receipts=provider_receipts,
        terminal_evidence=terminal_evidence,
        source_attempt_receipt_digest=(
            source_attempt.receipt_digest if source_attempt is not None else None
        ),
        retry_of_attempt_receipt_digest=(
            intent.retry_of_attempt_receipt_digest
        ),
        error_code=error_code,
        message=message,
    )


def compile_experiment_outcome(
    preregistration: ExperimentPreregistration,
    attempt: ExperimentAttemptReceipt,
    observations: tuple[ExperimentMetricObservation, ...],
) -> ExperimentOutcome:
    """Validate metric completeness without upgrading source evidence."""

    if not isinstance(preregistration, ExperimentPreregistration):
        raise TypeError("preregistration must be ExperimentPreregistration")
    if not isinstance(attempt, ExperimentAttemptReceipt):
        raise TypeError("attempt must be ExperimentAttemptReceipt")
    assignment = preregistration.assignment(attempt.assignment_id)
    if (
        attempt.study_id != preregistration.study_id
        or attempt.preregistration_digest
        != preregistration.preregistration_digest
        or attempt.assignment_digest != assignment.assignment_digest
        or (attempt.case_id, attempt.condition_id)
        != (assignment.case_id, assignment.condition_id)
    ):
        raise ExperimentProtocolError(
            "attempt does not bind the exact preregistered assignment"
        )
    _typed_sorted(
        observations,
        ExperimentMetricObservation,
        "observations",
        "metric_id",
    )
    specs = {item.metric_id: item for item in preregistration.metric_specs}
    if {item.metric_id for item in observations} != set(specs):
        raise ExperimentProtocolError(
            "outcome must report every preregistered metric exactly once"
        )
    for observation in observations:
        spec = specs[observation.metric_id]
        if observation.status is MetricObservationStatus.MEASURED:
            _metric_value(observation.value, spec)
        elif (
            observation.status is MetricObservationStatus.NOT_APPLICABLE
            and spec.required_for_comparison
        ):
            raise ExperimentProtocolError(
                "required metric cannot become not applicable"
            )
    eligible = (
        attempt.status
        in {
            ExperimentAttemptStatus.COMPLETED,
            ExperimentAttemptStatus.PIPELINE_REJECTED,
        }
        and all(
            observation.status is MetricObservationStatus.MEASURED
            for observation in observations
            if specs[observation.metric_id].required_for_comparison
        )
    )
    return ExperimentOutcome(
        study_id=preregistration.study_id,
        preregistration_digest=preregistration.preregistration_digest,
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        attempt_receipt_digest=attempt.receipt_digest,
        attempt_status=attempt.status,
        observations=observations,
        eligible_for_comparison=eligible,
    )


def _assignment_lifecycle(
    attempts: tuple[ExperimentIndexedAttempt, ...],
) -> ExperimentAssignmentLifecycle:
    if not attempts:
        return ExperimentAssignmentLifecycle.PLANNED
    latest = attempts[-1]
    if not latest.terminal_recorded:
        return ExperimentAssignmentLifecycle.RUNNING
    if not latest.measured:
        return ExperimentAssignmentLifecycle.TERMINAL_UNMEASURED
    if latest.receipt_status is ExperimentAttemptStatus.COMPLETED:
        return ExperimentAssignmentLifecycle.COMPLETED
    return ExperimentAssignmentLifecycle.FAILED


def _require_index_content(
    preregistration: ExperimentPreregistration,
    binding: ExperimentStudyRecordBinding,
    value: ExperimentAttemptIntent | ExperimentAttemptReceipt | ExperimentOutcome,
    source_schema: str,
    content_digest: str,
) -> None:
    if (
        binding.source_schema != source_schema
        or binding.content_digest != content_digest
        or value.study_id != preregistration.study_id
        or value.preregistration_digest
        != preregistration.preregistration_digest
    ):
        raise ExperimentProtocolError(
            "indexed study content changed schema, digest, or preregistration"
        )
    assignment = preregistration.assignment(value.assignment_id)
    if value.assignment_digest != assignment.assignment_digest:
        raise ExperimentProtocolError(
            "indexed study content changed assignment identity"
        )


def _attempt_identity(
    intent: ExperimentAttemptIntent,
) -> tuple[str, str, int]:
    return intent.assignment_id, intent.attempt_id, intent.attempt_index


def _metric_value(value: object, spec: ExperimentMetricSpec) -> None:
    if spec.value_kind is MetricValueKind.BOOLEAN:
        valid = isinstance(value, bool)
    elif spec.value_kind in {MetricValueKind.COUNT, MetricValueKind.DURATION_MS}:
        valid = type(value) is int and value >= 0
    else:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
        )
    if not valid:
        raise ExperimentProtocolError(
            f"metric {spec.metric_id} value does not match {spec.value_kind.value}"
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ExperimentProtocolError(f"{name} schema drifted")


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must be a string list")
    return tuple(values)


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ExperimentProtocolError(f"{field} must be bounded non-empty text")
    return value


def _schema_name(value: object, field: str) -> str:
    _text(value, field, maximum=100)
    if any(character.isspace() for character in value):
        raise ExperimentProtocolError(f"{field} cannot contain whitespace")
    return value


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ExperimentProtocolError(f"{field} must be a deterministic tuple")
    if values != tuple(sorted(set(values))):
        raise ExperimentProtocolError(f"{field} must be sorted and unique")
    for value in values:
        require_identifier(value, field)
    return values


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ExperimentProtocolError(f"{field} must be a deterministic tuple")
    if values != tuple(sorted(set(values))):
        raise ExperimentProtocolError(f"{field} must be sorted and unique")
    for value in values:
        require_logical_ref(value, field)
    return values


def _project_ref(value: object, project_id: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    require_logical_ref(value, field)
    if not value.startswith(f"project://{project_id}/"):
        raise ExperimentProtocolError(f"{field} crosses project identity")
    return value


def _typed_sorted(
    values: object,
    item_type: type,
    field: str,
    id_field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ExperimentProtocolError(f"{field} must be a deterministic tuple")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field} contains an invalid item")
    identities = tuple(getattr(item, id_field) for item in values)
    if identities != tuple(sorted(set(identities))):
        raise ExperimentProtocolError(f"{field} identities must be sorted and unique")


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentProtocolError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExperimentProtocolError(f"{field} must include a timezone")
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
