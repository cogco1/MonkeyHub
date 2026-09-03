from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import copy
import unittest

from archflow.evaluation.experiment import (
    ExperimentAssignment,
    ExperimentAssignmentLifecycle,
    ExperimentAttemptIntent,
    ExperimentAttemptReceipt,
    ExperimentAttemptStatus,
    ExperimentCase,
    ExperimentCondition,
    ExperimentConditionKind,
    ExperimentEvidenceBinding,
    ExperimentMetricObservation,
    ExperimentMetricSpec,
    ExperimentOutcome,
    ExperimentPreregistration,
    ExperimentProtocolError,
    ExperimentProviderProfile,
    ExperimentProviderReceiptBinding,
    ExperimentResultIndex,
    ExperimentStudyRecordBinding,
    ExperimentTerminalRequirement,
    ExperimentStoppingRule,
    MetricDirection,
    MetricObservationStatus,
    MetricValueKind,
    compile_experiment_attempt,
    compile_experiment_attempt_intent,
    compile_experiment_outcome,
    compile_experiment_result_index,
)
from archflow.project import ProjectVersionRef


REGISTERED_AT = "2026-08-17T13:58:00+08:00"
ISSUED_AT = "2026-08-17T14:00:00+08:00"
FULL_TERMINAL_ROLES = (
    "architectural-usability",
    "component-family-compilation",
    "component-family-realization",
    "sandbox-realization",
    "semantic-geometry-production",
)
VALIDATION_TERMINAL_ROLES = tuple(
    role for role in FULL_TERMINAL_ROLES if role != "architectural-usability"
)
TERMINAL_SCHEMAS = {
    "architectural-usability": "ArchitecturalUsabilityReceipt@1",
    "component-family-compilation": "ComponentFamilyCompilationReceipt@1",
    "component-family-realization": "ComponentFamilyRealizationReceipt@1",
    "sandbox-realization": "SandboxRealizationReceipt@1",
    "semantic-geometry-production": "ProductionTransitionRecord@1",
}
TERMINAL_STATUSES = {
    "architectural-usability": "passed",
    "component-family-compilation": "compiled",
    "component-family-realization": "realized",
    "sandbox-realization": "realized",
    "semantic-geometry-production": "persisted",
}


def _terminal_requirements(
    roles: tuple[str, ...],
) -> tuple[ExperimentTerminalRequirement, ...]:
    return tuple(
        ExperimentTerminalRequirement(
            role=role,
            source_schema=TERMINAL_SCHEMAS[role],
            accepted_statuses=(TERMINAL_STATUSES[role],),
        )
        for role in roles
    )


def _profile() -> ExperimentProviderProfile:
    return ExperimentProviderProfile(
        profile_id="provider-profile-a",
        provider_id="agent-cli-provider",
        model_id="reasoning-model",
        provider_version="provider-v1",
        provider_fingerprint="a" * 64,
        configuration_digest="a" * 64,
        timeout_ms=120_000,
        maximum_input_bytes=256_000,
        maximum_output_bytes=128_000,
        maximum_output_tokens=8_192,
        sampling_seed=None,
    )


def _case(index: int) -> ExperimentCase:
    project_id = f"experiment-project-{index}"
    run_id = "experiment-run-001"
    raw_ref = f"project://{project_id}/input/raw-request.json"
    return ExperimentCase(
        case_id=f"case-{index}",
        project_id=project_id,
        run_id=run_id,
        base=ProjectVersionRef(project_id, 0, f"{index}" * 64),
        raw_request_ref=raw_ref,
        raw_request_digest=f"{index + 3}" * 64,
        input_record_refs=(raw_ref,),
    )


def _conditions() -> tuple[ExperimentCondition, ...]:
    return (
        ExperimentCondition(
            condition_id="condition-full",
            kind=ExperimentConditionKind.FULL,
            provider_profile_id="provider-profile-a",
            description="Current production chain with all registered context.",
            terminal_requirements=_terminal_requirements(FULL_TERMINAL_ROLES),
            withheld_context_ids=(),
            withheld_evaluator_ids=(),
            source_condition_id=None,
        ),
        ExperimentCondition(
            condition_id="condition-generation-context",
            kind=ExperimentConditionKind.GENERATION_CONTEXT_ABLATION,
            provider_profile_id="provider-profile-a",
            description="Experimental request with one named context removed.",
            terminal_requirements=_terminal_requirements(FULL_TERMINAL_ROLES),
            withheld_context_ids=("semantic-component-context",),
            withheld_evaluator_ids=(),
            source_condition_id=None,
        ),
        ExperimentCondition(
            condition_id="condition-validation",
            kind=ExperimentConditionKind.VALIDATION_ABLATION,
            provider_profile_id="provider-profile-a",
            description="Reuse the full candidate with one evaluator omitted.",
            terminal_requirements=_terminal_requirements(
                VALIDATION_TERMINAL_ROLES
            ),
            withheld_context_ids=(),
            withheld_evaluator_ids=("architectural-usability",),
            source_condition_id="condition-full",
        ),
    )


def _metric_specs() -> tuple[ExperimentMetricSpec, ...]:
    return (
        ExperimentMetricSpec(
            metric_id="architectural-usable",
            value_kind=MetricValueKind.BOOLEAN,
            unit="boolean",
            direction=MetricDirection.HIGHER_IS_BETTER,
            required_for_comparison=True,
            description="Whether project-derived architectural usability passed.",
        ),
        ExperimentMetricSpec(
            metric_id="completion",
            value_kind=MetricValueKind.BOOLEAN,
            unit="boolean",
            direction=MetricDirection.HIGHER_IS_BETTER,
            required_for_comparison=True,
            description="Whether the assignment reached a terminal candidate.",
        ),
        ExperimentMetricSpec(
            metric_id="semantic-geometry-consistency",
            value_kind=MetricValueKind.RATIO,
            unit="ratio",
            direction=MetricDirection.HIGHER_IS_BETTER,
            required_for_comparison=True,
            description="Evidence-backed semantic geometry consistency ratio.",
        ),
        ExperimentMetricSpec(
            metric_id="wall-clock-ms",
            value_kind=MetricValueKind.DURATION_MS,
            unit="milliseconds",
            direction=MetricDirection.LOWER_IS_BETTER,
            required_for_comparison=True,
            description="Measured wall-clock duration for the assignment.",
        ),
    )


def _preregistration() -> ExperimentPreregistration:
    cases = tuple(_case(index) for index in range(1, 4))
    conditions = _conditions()
    assignments = tuple(
        sorted(
            (
                ExperimentAssignment(
                    assignment_id=(
                        f"assignment-{case.case_id}-{condition.condition_id}"
                    ),
                    case_id=case.case_id,
                    condition_id=condition.condition_id,
                )
                for case in cases
                for condition in conditions
            ),
            key=lambda item: item.assignment_id,
        )
    )
    return ExperimentPreregistration(
        study_id="p062-protocol-study",
        protocol_version="protocol-v1",
        registered_at=REGISTERED_AT,
        code_identity_digest="b" * 64,
        contract_identity_digest="c" * 64,
        provider_profiles=(_profile(),),
        cases=cases,
        conditions=conditions,
        metric_specs=_metric_specs(),
        assignments=assignments,
        maximum_attempts_per_assignment=2,
        study_wall_clock_limit_ms=3_600_000,
        stopping_rule=ExperimentStoppingRule.COMPLETE_ALL_ASSIGNMENTS,
    )


def _assignment_id(case_id: str, condition_id: str) -> str:
    return f"assignment-{case_id}-{condition_id}"


def _evidence(
    case: ExperimentCase,
    role: str = "architectural-usability",
    *,
    source_status: str | None = None,
) -> ExperimentEvidenceBinding:
    status = source_status or TERMINAL_STATUSES.get(role, "passed")
    return ExperimentEvidenceBinding(
        project_id=case.project_id,
        run_id=case.run_id,
        base=case.base,
        role=role,
        source_schema=TERMINAL_SCHEMAS.get(role, "ExperimentFixtureEvidence@1"),
        record_ref=(
            f"project://{case.project_id}/runs/{case.run_id}/records/"
            f"{role}.json"
        ),
        record_digest="d" * 64,
        source_status=status,
    )


def _terminal_evidence(
    case: ExperimentCase,
    roles: tuple[str, ...] = FULL_TERMINAL_ROLES,
) -> tuple[ExperimentEvidenceBinding, ...]:
    return tuple(
        sorted(
            (_evidence(case, role) for role in roles),
            key=lambda item: item.record_ref,
        )
    )


def _provider_receipt(
    case: ExperimentCase,
    *,
    status: str = "success",
    provider_id: str = "agent-cli-provider",
    duration_ms: int = 1_500,
) -> ExperimentProviderReceiptBinding:
    return ExperimentProviderReceiptBinding(
        receipt_id="provider-receipt-001",
        project_id=case.project_id,
        run_id=case.run_id,
        base=case.base,
        source_schema="ProductionTransitionRecord@1",
        record_ref=(
            f"project://{case.project_id}/runs/{case.run_id}/records/"
            "provider-receipt.json"
        ),
        record_digest="e" * 64,
        request_digest="f" * 64,
        p053_envelope_digest="1" * 64,
        authority_binding_digest="2" * 64,
        provider_id=provider_id,
        model_id="reasoning-model",
        provider_version="provider-v1",
        provider_fingerprint="a" * 64,
        status=status,
        duration_ms=duration_ms,
        input_bytes=1_024,
        output_bytes=2_048 if status == "success" else 0,
    )


def _intent(
    preregistration: ExperimentPreregistration,
    *,
    case_id: str = "case-1",
    condition_id: str = "condition-full",
) -> ExperimentAttemptIntent:
    return compile_experiment_attempt_intent(
        preregistration,
        assignment_id=_assignment_id(case_id, condition_id),
        attempt_id=f"attempt-{case_id}-{condition_id}-0",
        attempt_index=0,
        issued_at=ISSUED_AT,
    )


def _measured_observations(
    case: ExperimentCase,
) -> tuple[ExperimentMetricObservation, ...]:
    evidence = (_evidence(case),)
    return (
        ExperimentMetricObservation(
            metric_id="architectural-usable",
            status=MetricObservationStatus.MEASURED,
            value=True,
            evidence=evidence,
            reason=None,
        ),
        ExperimentMetricObservation(
            metric_id="completion",
            status=MetricObservationStatus.MEASURED,
            value=True,
            evidence=evidence,
            reason=None,
        ),
        ExperimentMetricObservation(
            metric_id="semantic-geometry-consistency",
            status=MetricObservationStatus.MEASURED,
            value=1.0,
            evidence=evidence,
            reason=None,
        ),
        ExperimentMetricObservation(
            metric_id="wall-clock-ms",
            status=MetricObservationStatus.MEASURED,
            value=1_800,
            evidence=evidence,
            reason=None,
        ),
    )


def _study_binding(
    value: ExperimentAttemptIntent | ExperimentAttemptReceipt | ExperimentOutcome,
    *,
    name: str,
) -> ExperimentStudyRecordBinding:
    project_id = "experiment-study-project"
    run_id = "study-run-001"
    if isinstance(value, ExperimentAttemptIntent):
        content_digest = value.intent_digest
    elif isinstance(value, ExperimentAttemptReceipt):
        content_digest = value.receipt_digest
    else:
        content_digest = value.outcome_digest
    return ExperimentStudyRecordBinding(
        project_id=project_id,
        run_id=run_id,
        base=ProjectVersionRef(project_id, 0, "9" * 64),
        source_schema=value.SCHEMA,
        record_ref=(
            f"project://{project_id}/runs/{run_id}/records/{name}.json"
        ),
        record_digest={
            "intent": "6" * 64,
            "receipt": "7" * 64,
            "outcome": "8" * 64,
        }[name],
        content_digest=content_digest,
    )


class ExperimentPreregistrationTests(unittest.TestCase):
    def test_three_project_full_factorial_preregistration_round_trips(self) -> None:
        preregistration = _preregistration()

        self.assertEqual(3, len(preregistration.cases))
        self.assertEqual(9, len(preregistration.assignments))
        self.assertEqual(
            ExperimentPreregistration.from_dict(preregistration.to_dict()),
            preregistration,
        )
        payload = preregistration.to_dict()
        self.assertFalse(payload["contains_run_results"])
        self.assertFalse(payload["provider_invocation_authority"])
        self.assertNotIn("outcomes", payload)
        self.assertEqual(
            len(preregistration.cases),
            len({item.project_id for item in preregistration.cases}),
        )

    def test_incomplete_matrix_duplicate_project_and_result_tamper_reject(self) -> None:
        preregistration = _preregistration()
        with self.assertRaises(ExperimentProtocolError):
            replace(
                preregistration,
                assignments=preregistration.assignments[:-1],
            )

        first, second, third = preregistration.cases
        duplicate_ref = (
            f"project://{first.project_id}/input/duplicate-raw-request.json"
        )
        duplicate = replace(
            second,
            project_id=first.project_id,
            base=replace(second.base, project_id=first.project_id),
            raw_request_ref=duplicate_ref,
            input_record_refs=(duplicate_ref,),
        )
        with self.assertRaises(ExperimentProtocolError):
            replace(preregistration, cases=(first, duplicate, third))

        payload = copy.deepcopy(preregistration.to_dict())
        payload["contains_run_results"] = True
        with self.assertRaises(ExperimentProtocolError):
            ExperimentPreregistration.from_dict(payload)

    def test_ablation_cannot_mutate_full_route_or_cross_provider_profile(self) -> None:
        preregistration = _preregistration()
        validation = preregistration.condition("condition-validation")
        with self.assertRaises(ExperimentProtocolError):
            replace(
                validation,
                withheld_context_ids=("semantic-component-context",),
            )
        with self.assertRaises(ExperimentProtocolError):
            replace(
                preregistration,
                conditions=tuple(
                    replace(item, source_condition_id="condition-missing")
                    if item.condition_id == validation.condition_id
                    else item
                    for item in preregistration.conditions
                ),
            )
        with self.assertRaises(ExperimentProtocolError):
            replace(
                preregistration,
                conditions=tuple(
                    replace(
                        item,
                        terminal_requirements=_terminal_requirements(
                            ("architectural-usability",)
                        ),
                    )
                    if item.condition_id == "condition-generation-context"
                    else item
                    for item in preregistration.conditions
                ),
            )

        second_profile = replace(
            _profile(),
            profile_id="provider-profile-b",
            configuration_digest="1" * 64,
        )
        with self.assertRaises(ExperimentProtocolError):
            replace(
                preregistration,
                provider_profiles=(_profile(), second_profile),
                conditions=tuple(
                    replace(item, provider_profile_id=second_profile.profile_id)
                    if item.condition_id == "condition-generation-context"
                    else item
                    for item in preregistration.conditions
                ),
            )


class ExperimentAttemptTests(unittest.TestCase):
    def test_intent_attempt_and_measured_outcome_round_trip_exactly(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=_terminal_evidence(case),
        )
        outcome = compile_experiment_outcome(
            preregistration,
            attempt,
            _measured_observations(case),
        )

        self.assertEqual(
            ExperimentAttemptIntent.from_dict(intent.to_dict()),
            intent,
        )
        self.assertEqual(
            ExperimentAttemptReceipt.from_dict(attempt.to_dict()),
            attempt,
        )
        self.assertEqual(
            ExperimentOutcome.from_dict(outcome.to_dict()),
            outcome,
        )
        self.assertEqual(intent.intent_digest, attempt.attempt_intent_digest)
        self.assertTrue(outcome.eligible_for_comparison)
        self.assertFalse(outcome.to_dict()["aggregate_winner_claimed"])
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.COMPLETED,
                duration_ms=1_800,
                provider_receipts=(_provider_receipt(case),),
                terminal_evidence=_terminal_evidence(case)[:-1],
            )

    def test_result_index_derives_lifecycle_without_promoting_unrun_work(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=_terminal_evidence(case),
        )
        outcome = compile_experiment_outcome(
            preregistration,
            attempt,
            _measured_observations(case),
        )
        common = {
            "project_id": "experiment-study-project",
            "run_id": "study-run-001",
            "base": ProjectVersionRef(
                "experiment-study-project",
                0,
                "9" * 64,
            ),
            "generated_at": "2026-08-17T14:30:00+08:00",
        }

        planned = compile_experiment_result_index(
            preregistration,
            intents=(),
            receipts=(),
            outcomes=(),
            **common,
        )
        self.assertEqual(
            {ExperimentAssignmentLifecycle.PLANNED},
            {item.lifecycle for item in planned.assignments},
        )
        self.assertFalse(planned.evidence_table_ready)
        self.assertEqual(0, planned.comparable_sample_size)

        intent_pair = (_study_binding(intent, name="intent"), intent)
        running = compile_experiment_result_index(
            preregistration,
            intents=(intent_pair,),
            receipts=(),
            outcomes=(),
            **common,
        )
        self.assertEqual(
            ExperimentAssignmentLifecycle.RUNNING,
            running.assignments[0].lifecycle,
        )

        receipt_pair = (_study_binding(attempt, name="receipt"), attempt)
        unmeasured = compile_experiment_result_index(
            preregistration,
            intents=(intent_pair,),
            receipts=(receipt_pair,),
            outcomes=(),
            **common,
        )
        self.assertEqual(
            ExperimentAssignmentLifecycle.TERMINAL_UNMEASURED,
            unmeasured.assignments[0].lifecycle,
        )

        complete = compile_experiment_result_index(
            preregistration,
            intents=(intent_pair,),
            receipts=(receipt_pair,),
            outcomes=((_study_binding(outcome, name="outcome"), outcome),),
            **common,
        )
        self.assertEqual(
            ExperimentAssignmentLifecycle.COMPLETED,
            complete.assignments[0].lifecycle,
        )
        self.assertEqual(1, complete.comparable_sample_size)
        self.assertEqual(
            8,
            complete.lifecycle_counts[ExperimentAssignmentLifecycle.PLANNED.value],
        )
        self.assertFalse(complete.evidence_table_ready)
        self.assertEqual(complete, ExperimentResultIndex.from_dict(complete.to_dict()))

        tampered = copy.deepcopy(complete.to_dict())
        tampered["lifecycle_counts"]["planned"] = 0
        with self.assertRaises(ExperimentProtocolError):
            ExperimentResultIndex.from_dict(tampered)

    def test_result_index_keeps_terminal_failure_distinct_from_completion(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.TIMED_OUT,
            duration_ms=120_000,
            provider_receipts=(_provider_receipt(case, status="timeout"),),
            terminal_evidence=(),
            error_code="provider-timeout",
        )
        outcome = compile_experiment_outcome(
            preregistration,
            attempt,
            tuple(
                ExperimentMetricObservation(
                    metric_id=spec.metric_id,
                    status=MetricObservationStatus.UNKNOWN,
                    value=None,
                    evidence=(),
                    reason="No terminal candidate exists after timeout.",
                )
                for spec in preregistration.metric_specs
            ),
        )
        index = compile_experiment_result_index(
            preregistration,
            project_id="experiment-study-project",
            run_id="study-run-001",
            base=ProjectVersionRef(
                "experiment-study-project",
                0,
                "9" * 64,
            ),
            generated_at="2026-08-17T14:31:00+08:00",
            intents=((_study_binding(intent, name="intent"), intent),),
            receipts=((_study_binding(attempt, name="receipt"), attempt),),
            outcomes=((_study_binding(outcome, name="outcome"), outcome),),
        )

        self.assertEqual(
            ExperimentAssignmentLifecycle.FAILED,
            index.assignments[0].lifecycle,
        )
        self.assertEqual(0, index.comparable_sample_size)
        self.assertEqual(8, index.lifecycle_counts["planned"])

    def test_provider_profile_timeout_attempt_bound_and_fallback_fail_closed(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.COMPLETED,
                duration_ms=1_800,
                provider_receipts=(
                    _provider_receipt(case, provider_id="different-provider"),
                ),
                terminal_evidence=_terminal_evidence(case),
            )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.TIMED_OUT,
                duration_ms=120_000,
                provider_receipts=(_provider_receipt(case),),
                terminal_evidence=(),
                error_code="provider-timeout",
            )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt_intent(
                preregistration,
                assignment_id=intent.assignment_id,
                attempt_id="attempt-over-bound",
                attempt_index=2,
                issued_at=ISSUED_AT,
            )

        timeout = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.TIMED_OUT,
            duration_ms=167_500,
            provider_receipts=(
                _provider_receipt(
                    case,
                    status="timeout",
                    duration_ms=167_109,
                ),
            ),
            terminal_evidence=(),
            error_code="provider-timeout",
        )
        self.assertEqual(167_109, timeout.provider_receipts[0].duration_ms)
        with self.assertRaisesRegex(
            ExperimentProtocolError,
            "provider receipt changed",
        ):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.PROVIDER_FAILED,
                duration_ms=167_500,
                provider_receipts=(
                    _provider_receipt(
                        case,
                        status="exit_error",
                        duration_ms=167_109,
                    ),
                ),
                terminal_evidence=(),
                error_code="provider-exit",
            )
        with self.assertRaisesRegex(
            ExperimentProtocolError,
            "provider receipt changed",
        ):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.TIMED_OUT,
                duration_ms=167_000,
                provider_receipts=(
                    _provider_receipt(
                        case,
                        status="timeout",
                        duration_ms=167_109,
                    ),
                ),
                terminal_evidence=(),
                error_code="provider-timeout",
            )

        payload = copy.deepcopy(_provider_receipt(case).to_dict())
        payload["fallback_used"] = True
        with self.assertRaises(ExperimentProtocolError):
            ExperimentProviderReceiptBinding.from_dict(payload)

    def test_timeout_failure_is_retained_without_terminal_claim(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.TIMED_OUT,
            duration_ms=120_000,
            provider_receipts=(_provider_receipt(case, status="timeout"),),
            terminal_evidence=(),
            error_code="provider-timeout",
            message="The preregistered provider wall-clock bound elapsed.",
        )
        retry_intent = compile_experiment_attempt_intent(
            preregistration,
            assignment_id=intent.assignment_id,
            attempt_id="attempt-case-1-condition-full-1",
            attempt_index=1,
            issued_at="2026-08-17T14:03:00+08:00",
            retry_of=attempt,
        )
        self.assertEqual(
            attempt.receipt_digest,
            retry_intent.retry_of_attempt_receipt_digest,
        )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt_intent(
                preregistration,
                assignment_id=_assignment_id(
                    "case-2",
                    "condition-full",
                ),
                attempt_id="attempt-wrong-retry",
                attempt_index=1,
                issued_at="2026-08-17T14:03:00+08:00",
                retry_of=attempt,
            )
        observations = tuple(
            ExperimentMetricObservation(
                metric_id=spec.metric_id,
                status=MetricObservationStatus.UNKNOWN,
                value=None,
                evidence=(),
                reason="No terminal candidate exists after provider timeout.",
            )
            for spec in preregistration.metric_specs
        )
        outcome = compile_experiment_outcome(
            preregistration,
            attempt,
            observations,
        )

        self.assertFalse(outcome.eligible_for_comparison)
        self.assertEqual("timed_out", outcome.to_dict()["attempt_status"])
        self.assertTrue(all(item.value is None for item in outcome.observations))

    def test_early_pipeline_rejection_needs_no_invented_terminal_evidence(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.PIPELINE_REJECTED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=(),
            error_code="spatial_authoring.proposal_rejected",
            message="Deterministic spatial validation rejected the proposal.",
        )
        observations = tuple(
            ExperimentMetricObservation(
                metric_id=spec.metric_id,
                status=MetricObservationStatus.UNKNOWN,
                value=None,
                evidence=(),
                reason="No terminal building exists after early rejection.",
            )
            for spec in preregistration.metric_specs
        )
        outcome = compile_experiment_outcome(
            preregistration,
            attempt,
            observations,
        )

        self.assertEqual(
            ExperimentAttemptStatus.PIPELINE_REJECTED,
            attempt.status,
        )
        self.assertEqual((), attempt.terminal_evidence)
        self.assertFalse(outcome.eligible_for_comparison)
        with self.assertRaisesRegex(
            ExperimentProtocolError,
            "rejection error",
        ):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.PIPELINE_REJECTED,
                duration_ms=1_800,
                provider_receipts=(_provider_receipt(case),),
                terminal_evidence=(),
            )

        completed = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=_terminal_evidence(case),
        )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt_intent(
                preregistration,
                assignment_id=intent.assignment_id,
                attempt_id="attempt-after-completion",
                attempt_index=1,
                issued_at="2026-08-17T14:04:00+08:00",
                retry_of=completed,
            )

    def test_validation_ablation_reuses_exact_attempt_without_provider_replay(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(
            preregistration,
            condition_id="condition-validation",
        )
        source_intent = _intent(preregistration)
        source_attempt = compile_experiment_attempt(
            preregistration,
            source_intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=_terminal_evidence(case),
        )
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=100,
            provider_receipts=(),
            terminal_evidence=_terminal_evidence(
                case,
                VALIDATION_TERMINAL_ROLES,
            ),
            source_attempt=source_attempt,
        )

        self.assertEqual((), attempt.provider_receipts)
        self.assertEqual(
            source_attempt.receipt_digest,
            attempt.source_attempt_receipt_digest,
        )
        changed_validation_evidence = tuple(
            replace(item, record_digest="0" * 64)
            if item.role == "component-family-realization"
            else item
            for item in _terminal_evidence(
                case,
                VALIDATION_TERMINAL_ROLES,
            )
        )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.COMPLETED,
                duration_ms=100,
                provider_receipts=(),
                terminal_evidence=changed_validation_evidence,
                source_attempt=source_attempt,
            )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.COMPLETED,
                duration_ms=100,
                provider_receipts=(_provider_receipt(case),),
                terminal_evidence=_terminal_evidence(
                    case,
                    VALIDATION_TERMINAL_ROLES,
                ),
                source_attempt=source_attempt,
            )

    def test_stale_attempt_missing_metric_and_invented_value_reject(self) -> None:
        preregistration = _preregistration()
        case = preregistration.case("case-1")
        intent = _intent(preregistration)
        attempt = compile_experiment_attempt(
            preregistration,
            intent,
            status=ExperimentAttemptStatus.COMPLETED,
            duration_ms=1_800,
            provider_receipts=(_provider_receipt(case),),
            terminal_evidence=_terminal_evidence(case),
        )
        stale = replace(attempt, preregistration_digest="9" * 64)
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_outcome(
                preregistration,
                stale,
                _measured_observations(case),
            )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_outcome(
                preregistration,
                attempt,
                _measured_observations(case)[:-1],
            )
        invented = tuple(
            replace(item, value=1.5)
            if item.metric_id == "semantic-geometry-consistency"
            else item
            for item in _measured_observations(case)
        )
        with self.assertRaises(ExperimentProtocolError):
            compile_experiment_outcome(
                preregistration,
                attempt,
                invented,
            )

    def test_framework_protocol_contains_no_case_or_platform_answer(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "archflow"
            / "evaluation"
            / "experiment.py"
        ).read_text(encoding="utf-8").lower()
        forbidden = (
            "pantheon",
            "minecraft",
            "rhino",
            "revit",
            "community library",
        )
        self.assertFalse(any(item in source for item in forbidden))


if __name__ == "__main__":
    unittest.main()
