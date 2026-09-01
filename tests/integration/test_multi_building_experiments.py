from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.evaluation.experiment import (
    ExperimentAssignment,
    ExperimentAssignmentLifecycle,
    ExperimentAttemptIntent,
    ExperimentAttemptReceipt,
    ExperimentAttemptStatus,
    ExperimentCase,
    ExperimentEvidenceBinding,
    ExperimentOutcome,
    ExperimentPreregistration,
    ExperimentProtocolError,
    ExperimentProviderReceiptBinding,
    ExperimentResultIndex,
    ExperimentStoppingRule,
    ExperimentTerminalRequirement,
    compile_experiment_attempt,
    compile_experiment_attempt_intent,
    compile_experiment_outcome,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    bootstrap_raw_request_project,
    canonical_json_sha256,
    locate_project,
)
from archflow.runtime.family_compiler import (
    bind_component_family_realization,
    compile_component_families,
)
from archflow.runtime.production_runtime import ProductionAuthoringContext
from archflow.runtime.semantic_geometry_lifecycle import (
    InitialSemanticGeometryReceipt,
)
from archflow.state.geometry_program import digest_value
from archflow.validation.architectural import evaluate_architectural_usability
from tests.integration.test_project_derived_architectural_usability import (
    _compile_declared_fact_contract,
)
from tests.test_component_family_protocol import _family_set, _parametric_fixture
from tests.test_experiment_protocol import (
    FULL_TERMINAL_ROLES,
    ISSUED_AT,
    REGISTERED_AT,
    _conditions,
    _measured_observations,
    _metric_specs,
    _profile,
)
from tools.run_experiment import (
    PROGRAM_RELATIONSHIP_CONTEXT_ID,
    _main as run_experiment_main,
    bind_preregistered_cases,
    bind_provider_record,
    bind_terminal_record,
    bind_terminal_chain,
    persist_attempt_intent,
    persist_attempt_receipt,
    persist_generation_context_ablation,
    persist_outcome,
    persist_preregistration,
    persist_result_index,
    rebuild_result_index,
    select_assignment_context,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _project_root(project_id: str) -> Path:
    return locate_project(
        project_id,
        local_projects_root=REPOSITORY_ROOT / "probes",
    ).root


CASE_SPECS = (
    (
        "p062-clinic-case",
        "Design a compact one-storey outpatient clinic organized as a linear "
        "sequence of reception, consultation, treatment, and staff support. "
        "Keep public and staff circulation legible and provide a sheltered "
        "entrance without selecting a material palette in advance.",
    ),
    (
        "p062-workshop-case",
        "Design a three-storey urban making centre with a double-height "
        "fabrication hall, upper studios, a public exhibition route, and a "
        "separate service path. Preserve the vertical relationship between "
        "the hall and studios without assuming a facade style.",
    ),
    (
        "p062-courtyard-case",
        "Design a low public learning pavilion around an open courtyard with "
        "a multipurpose room, two smaller teaching rooms, and a shaded edge "
        "route. Maintain direct courtyard access while keeping the geometric "
        "response and component choices project-derived.",
    ),
)


def _write_cli_payload(root: Path, name: str, payload: dict[str, object]) -> Path:
    path = root / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _run_experiment_cli(*arguments: str) -> str:
    output = StringIO()
    with redirect_stdout(output):
        status = run_experiment_main(arguments)
    if status != 0:
        raise AssertionError(f"experiment CLI returned {status}")
    return output.getvalue().strip()


def _record_for_schema(
    repository,
    run,
    schema,
    *,
    role=None,
    content_schema=None,
):
    matches = [
        ref
        for ref in repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        if repository.load_json(ref).get("schema") == schema
        and (
            role is None
            or repository.load_json(ref).get("role") == role
        )
        and (
            content_schema is None
            or (
                isinstance(repository.load_json(ref).get("content"), dict)
                and repository.load_json(ref)["content"].get("schema")
                == content_schema
            )
        )
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {schema} record")
    return matches[0]


def _bootstrap_study(root: Path):
    study = bootstrap_raw_request_project(
        root / "p062-experiment-study",
        project_id="p062-experiment-study",
        prompt=(
            "Retain a preregistered multi-building comparison without "
            "executing or fabricating any assignment result."
        ),
        run_id="study-001",
        synthetic_test=True,
    )
    cases = tuple(
        bootstrap_raw_request_project(
            root / project_id,
            project_id=project_id,
            prompt=prompt,
            run_id="experiment-001",
            synthetic_test=True,
        )
        for project_id, prompt in CASE_SPECS
    )
    return study, cases


def _preregistration(cases) -> ExperimentPreregistration:
    experiment_cases = tuple(
        ExperimentCase(
            case_id=f"case-{index}",
            project_id=item.project_id,
            run_id=item.run.run_id,
            base=item.run.base,
            raw_request_ref=item.request.uri,
            raw_request_digest=item.request.sha256,
            input_record_refs=(item.request.uri,),
        )
        for index, item in enumerate(cases, start=1)
    )
    conditions = tuple(
        replace(
            condition,
            terminal_requirements=tuple(
                ExperimentTerminalRequirement(
                    role=role,
                    source_schema="P062DeterministicTerminalEvidence@1",
                    accepted_statuses=("passed",),
                )
                for role in condition.required_terminal_roles
            ),
        )
        for condition in _conditions()
    )
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
                for case in experiment_cases
                for condition in conditions
            ),
            key=lambda item: item.assignment_id,
        )
    )
    return ExperimentPreregistration(
        study_id="p062-deterministic-integration",
        protocol_version="protocol-v1",
        registered_at=REGISTERED_AT,
        code_identity_digest="4" * 64,
        contract_identity_digest="5" * 64,
        provider_profiles=(_profile(),),
        cases=experiment_cases,
        conditions=conditions,
        metric_specs=_metric_specs(),
        assignments=assignments,
        maximum_attempts_per_assignment=2,
        study_wall_clock_limit_ms=3_600_000,
        stopping_rule=ExperimentStoppingRule.COMPLETE_ALL_ASSIGNMENTS,
    )


def _provider_source(case_repository, case_run):
    request = ModelInvocationRequest.create(
        request_id="p062-deterministic-provider-request",
        phase=ModelPhase.CAPABILITY_SELECTION,
        checkpoint_digest="6" * 64,
        context_digest="7" * 64,
        payload={
            "schema": "P062DeterministicProviderInput@1",
            "test_only": True,
        },
    )
    output_json = '{"schema":"P062DeterministicProviderOutput@1"}'
    receipt = ModelInvocationReceipt(
        receipt_id="p062-deterministic-provider-receipt",
        status=ModelInvocationStatus.SUCCESS,
        request=request,
        provider_id="agent-cli-provider",
        model_id="reasoning-model",
        provider_version="provider-v1",
        provider_fingerprint="a" * 64,
        input_bytes=512,
        output_bytes=len(output_json.encode("utf-8")),
        output_sha256=hashlib.sha256(output_json.encode("utf-8")).hexdigest(),
        duration_ms=1_500,
        output_json=output_json,
    )
    provider_receipt_json = json.dumps(
        receipt.to_dict(),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    authority_binding_digest = "8" * 64
    envelope = {
        "schema": "ProductionInvocationEnvelope@2",
        "authority": {
            "schema": "ProductionAuthorityToken@2",
            "responsibility_id": "model.p062-deterministic",
            "contract_owner_id": "archflow.p062-test",
            "provider": {
                "provider_id": receipt.provider_id,
                "version": receipt.provider_version,
                "fingerprint": receipt.provider_fingerprint,
            },
            "authority_epoch": 1,
            "binding_digest": authority_binding_digest,
            "production_authority": True,
            "grant_nonce": "9" * 32,
            "canonical_write_authority": False,
            "grant_signature": "a" * 64,
        },
        "provider_receipt_json": provider_receipt_json,
        "provider_receipt_digest": digest_value(receipt.to_dict()),
        "canonical_write_authority": False,
        "envelope_signature": "b" * 64,
    }
    content_digest = digest_value(envelope)
    payload = {
        "schema": "ProductionTransitionRecord@1",
        "project_id": case_run.project_id,
        "run_id": case_run.run_id,
        "base": {
            "project_id": case_run.base.project_id,
            "version": case_run.base.version,
            "state_sha256": case_run.base.require_digest(),
        },
        "role": "provider-invocation",
        "content": envelope,
        "content_sha256": content_digest,
        "semantic_digest": content_digest,
        "canonical_write_authority": False,
        "synthetic_test": True,
        "empirical_result_claimed": False,
    }
    ref = case_repository.put_json(
        run=case_run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=case_run.run_id,
        ),
        record_kind="deterministic-provider-receipt",
        payload=payload,
    )
    return bind_provider_record(
        case_repository,
        run=case_run,
        ref=ref,
    )


def _terminal_sources(case_repository, case_run):
    evidence = []
    for role in FULL_TERMINAL_ROLES:
        requirement = ExperimentTerminalRequirement(
            role=role,
            source_schema="P062DeterministicTerminalEvidence@1",
            accepted_statuses=("passed",),
        )
        payload = {
            "schema": requirement.source_schema,
            "project_id": case_run.project_id,
            "run_id": case_run.run_id,
            "base": {
                "project_id": case_run.base.project_id,
                "version": case_run.base.version,
                "state_sha256": case_run.base.require_digest(),
            },
            "role": role,
            "status": "passed",
            "synthetic_test": True,
            "empirical_result_claimed": False,
        }
        ref = case_repository.put_json(
            run=case_run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=case_run.run_id,
            ),
            record_kind=f"deterministic-{role}",
            payload=payload,
        )
        evidence.append(
            bind_terminal_record(
                case_repository,
                run=case_run,
                ref=ref,
                requirement=requirement,
            )
        )
    return tuple(sorted(evidence, key=lambda item: item.record_ref))


class MultiBuildingExperimentPersistenceTests(unittest.TestCase):
    def test_generation_ablation_removes_only_program_relationship_context(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        source_repository = FilesystemProjectRepository.open(
            _project_root("p062-clinic-case")
        )
        source_run = source_repository.load_run("experiment-001")
        source_refs = [
            ref
            for ref in source_repository.list_json(
                run=source_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=source_run.run_id,
                ),
            )
            if source_repository.load_json(ref).get("schema")
            == ProductionAuthoringContext.SCHEMA
            and ProductionAuthoringContext.from_dict(
                source_repository.load_json(ref)
            ).program.relationships
            and ProductionAuthoringContext.from_dict(
                source_repository.load_json(ref)
            ).required_commitment_refs
        ]
        self.assertEqual(1, len(source_refs))
        source_ref = source_refs[0]
        source_payload = source_repository.load_json(source_ref)
        input_ref = source_repository.list_json(
            run=source_run,
            destination=PersistenceDestination(PersistenceArea.INPUT),
        )[0]
        prompt = source_repository.load_json(input_ref)["prompt"]
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = bootstrap_raw_request_project(
                Path(directory) / source_run.project_id,
                project_id=source_run.project_id,
                prompt=prompt,
                run_id=source_run.run_id,
                synthetic_test=True,
            )
            repository = FilesystemProjectRepository.open(
                Path(directory) / source_run.project_id
            )
            copied_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=bootstrap.run.run_id,
                ),
                record_kind="source-production-authoring-context",
                payload=source_payload,
            )
            context_ref, receipt_ref = persist_generation_context_ablation(
                repository,
                run=bootstrap.run,
                source_context_ref=copied_ref,
                condition_id="condition-generation-context",
                withheld_context_ids=(PROGRAM_RELATIONSHIP_CONTEXT_ID,),
            )
            original = ProductionAuthoringContext.from_dict(
                repository.load_json(copied_ref)
            )
            ablated = ProductionAuthoringContext.from_dict(
                repository.load_json(context_ref)
            )
            receipt = repository.load_json(receipt_ref)
            self.assertTrue(original.program.relationships)
            self.assertEqual((), ablated.program.relationships)
            self.assertEqual(original.program.nodes, ablated.program.nodes)
            self.assertEqual(original.program.ranges, ablated.program.ranges)
            self.assertEqual(
                ablated.program.program_digest,
                ablated.build_policy.program_digest,
            )
            self.assertEqual(
                [item.relationship_id for item in original.program.relationships],
                receipt["removed_relationship_ids"],
            )
            self.assertEqual("P053/P056", receipt["unchanged_production_route"])
            self.assertTrue(receipt["provider_profile_unchanged"])
            self.assertFalse(receipt["generation_authority"])
            self.assertEqual(source_payload, repository.load_json(copied_ref))
            self.assertEqual(
                (context_ref, receipt_ref),
                persist_generation_context_ablation(
                    repository,
                    run=bootstrap.run,
                    source_context_ref=copied_ref,
                    condition_id="condition-generation-context",
                    withheld_context_ids=(PROGRAM_RELATIONSHIP_CONTEXT_ID,),
                ),
            )
            full_condition, generation_condition, validation_condition = (
                _conditions()
            )
            generation_condition = replace(
                generation_condition,
                withheld_context_ids=(PROGRAM_RELATIONSHIP_CONTEXT_ID,),
            )
            self.assertEqual(
                (copied_ref, original),
                select_assignment_context(
                    repository,
                    run=bootstrap.run,
                    condition=full_condition,
                    full_context_ref=copied_ref,
                ),
            )
            self.assertEqual(
                (context_ref, ablated),
                select_assignment_context(
                    repository,
                    run=bootstrap.run,
                    condition=generation_condition,
                    full_context_ref=copied_ref,
                    ablation_receipt_ref=receipt_ref,
                ),
            )
            with self.assertRaisesRegex(
                ExperimentProtocolError,
                "detached and does not invoke a provider",
            ):
                select_assignment_context(
                    repository,
                    run=bootstrap.run,
                    condition=validation_condition,
                    full_context_ref=copied_ref,
                )
            repository.verify()

    def test_promoted_case_inputs_are_non_synthetic_and_results_are_governed(self) -> None:
        root = Path(__file__).resolve().parents[2]
        context_digests = set()
        program_digests = set()
        prompts = set()
        expected_schemas = {
            "AuthorizedSiteObservation@1",
            "BriefCompilationReceipt@1",
            "BuildPolicy@1",
            "DesignBrief@1",
            "DesignProgram@1",
            "ExperimentGenerationContextAblationReceipt@1",
            "P062AssignmentPreflightReceipt@1",
            "P062CaseEvaluationBrief@1",
            "P062CaseFamilyBrief@1",
            "ProductionAuthoringContext@1",
            "ProgramCompilationReceipt@1",
            "ProjectBootstrapReceipt@1",
            "ResourceCompilationReceipt@1",
            "SiteCompilationReceipt@1",
            "SiteContext@2",
        }
        for project_id, _ in CASE_SPECS:
            repository = FilesystemProjectRepository.open(
                _project_root(project_id)
            )
            run = repository.load_run("experiment-001")
            records = tuple(
                repository.load_json(ref)
                for ref in repository.list_json(
                    run=run,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )
            )
            schemas = {item.get("schema") for item in records}
            case_schemas = set(expected_schemas)
            if project_id == "p062-clinic-case":
                case_schemas.update(
                    {
                        "ProductionFailedAttemptReceipt@1",
                        "ProviderLiveInvocationEvidence@1",
                        "SemanticSpatialAuthoringReceipt@1",
                        "SchematicOptionSet@1",
                        "SpatialCompilationReceipt@1",
                    }
                )
            # Since study-028 every case retains executed terminal-chain
            # evidence, so promoted inputs are a subset rather than the
            # exact record population.
            self.assertTrue(case_schemas <= schemas)
            self.assertTrue(
                {
                    "ArchitecturalUsabilityReceipt@1",
                    "ComponentFamilyCompilationReceipt@1",
                    "ComponentFamilyRealizationReceipt@1",
                    "GeometryProposalLineage@2",
                    "HybridSandboxScene@1",
                    "ProductionTransitionRecord@1",
                    "SandboxRealizationReceipt@1",
                }
                <= schemas
            )
            # Every record carries an explicit schema. The only exception is
            # a record named as defective by a retained schema-correction
            # note that also names its corrected successor.
            corrected_defects = {
                uri
                for item in records
                if item.get("schema") == "P062RecordSchemaCorrection@1"
                for uri in item["defective_record_refs"]
            }
            schemaless = tuple(
                ref.uri
                for ref in repository.list_json(
                    run=run,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )
                if not repository.load_json(ref).get("schema")
            )
            self.assertTrue(set(schemaless) <= corrected_defects)
            self.assertTrue(
                all(
                    isinstance(schema, str) and schema
                    for schema in schemas
                    if schema is not None
                )
            )
            bootstrap = next(
                item
                for item in records
                if item.get("schema") == "ProjectBootstrapReceipt@1"
            )
            self.assertFalse(bootstrap["synthetic_test"])
            contexts = tuple(
                ProductionAuthoringContext.from_dict(item)
                for item in records
                if item.get("schema") == ProductionAuthoringContext.SCHEMA
            )
            self.assertEqual(4, len(contexts))
            full_contexts = tuple(
                item for item in contexts if item.program.relationships
            )
            ablated_contexts = tuple(
                item for item in contexts if not item.program.relationships
            )
            self.assertEqual(2, len(full_contexts))
            self.assertEqual(2, len(ablated_contexts))
            ready_full = tuple(
                item for item in full_contexts if item.required_commitment_refs
            )
            ready_ablated = tuple(
                item
                for item in ablated_contexts
                if item.required_commitment_refs
            )
            self.assertEqual(1, len(ready_full))
            self.assertEqual(1, len(ready_ablated))
            self.assertEqual(
                1,
                sum(not item.required_commitment_refs for item in full_contexts),
            )
            self.assertEqual(
                1,
                sum(
                    not item.required_commitment_refs
                    for item in ablated_contexts
                ),
            )
            context = ready_full[0]
            context.require_run(run)
            ready_ablated[0].require_run(run)
            self.assertEqual(
                context.required_commitment_refs,
                ready_ablated[0].required_commitment_refs,
            )
            context_digests.add(context.context_digest)
            program_digests.add(context.program.program_digest)
            evaluation = next(
                item
                for item in records
                if item.get("schema") == "P062CaseEvaluationBrief@1"
            )
            family = next(
                item
                for item in records
                if item.get("schema") == "P062CaseFamilyBrief@1"
            )
            self.assertFalse(evaluation["geometry_answer_present"])
            self.assertFalse(family["geometry_answer_present"])
            self.assertIsNone(family["selected_component_id"])
            input_refs = repository.list_json(
                run=run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
            )
            self.assertEqual(1, len(input_refs))
            raw = repository.load_json(input_refs[0])
            self.assertEqual("RawProjectRequest@1", raw["schema"])
            prompts.add(raw["prompt"])
            self.assertFalse(
                any(
                    str(schema).startswith(
                        (
                            "ExperimentAttempt",
                            "ExperimentOutcome",
                            "ExperimentResult",
                        )
                    )
                    for schema in schemas
                )
            )
            repository.verify()
        self.assertEqual(3, len(context_digests))
        self.assertEqual(3, len(program_digests))
        self.assertEqual(3, len(prompts))

    def test_promoted_study_retains_pre_provider_rejection_honestly(self) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        run = repository.load_run("study-001")
        refs = repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        payloads = tuple(repository.load_json(ref) for ref in refs)
        by_schema = {
            schema: tuple(
                item for item in payloads if item.get("schema") == schema
            )
            for schema in {item.get("schema") for item in payloads}
        }
        self.assertFalse(by_schema["ProjectBootstrapReceipt@1"][0]["synthetic_test"])
        manifest = by_schema["P062StudyIdentityManifest@1"][0]
        preregistration = ExperimentPreregistration.from_dict(
            by_schema[ExperimentPreregistration.SCHEMA][0]
        )
        indexes = tuple(
            ExperimentResultIndex.from_dict(item)
            for item in by_schema[ExperimentResultIndex.SCHEMA]
        )
        self.assertEqual(2, len(indexes))
        index = next(
            item for item in indexes if item.lifecycle_counts["planned"] == 9
        )
        running_index = next(
            item for item in indexes if item.lifecycle_counts["running"] == 1
        )
        self.assertEqual(3, len(preregistration.cases))
        self.assertEqual(3, len(preregistration.conditions))
        self.assertEqual(9, len(preregistration.assignments))
        self.assertEqual(13, len(preregistration.metric_specs))
        self.assertEqual(1, preregistration.maximum_attempts_per_assignment)
        profile = preregistration.provider_profiles[0]
        self.assertEqual("codex-agent-cli", profile.provider_id)
        self.assertEqual("gpt-5.6-sol", profile.model_id)
        self.assertEqual("codex-cli-0.145.0", profile.provider_version)
        self.assertEqual(
            "299bef65742013eeef2a4bb247d9d7b9738aa8d538fae3914cd77bcf21f587f8",
            profile.provider_fingerprint,
        )
        self.assertEqual(120_000, profile.timeout_ms)
        generation = preregistration.condition(
            "condition-generation-context"
        )
        self.assertEqual(
            (PROGRAM_RELATIONSHIP_CONTEXT_ID,),
            generation.withheld_context_ids,
        )
        self.assertEqual(
            preregistration.preregistration_digest,
            index.preregistration_digest,
        )
        self.assertEqual(9, index.lifecycle_counts["planned"])
        self.assertTrue(
            all(
                item.lifecycle is ExperimentAssignmentLifecycle.PLANNED
                for item in index.assignments
            )
        )
        self.assertEqual(0, index.comparable_sample_size)
        self.assertFalse(index.evidence_table_ready)
        self.assertEqual(8, running_index.lifecycle_counts["planned"])
        self.assertEqual(0, running_index.comparable_sample_size)
        self.assertFalse(running_index.evidence_table_ready)
        self.assertEqual(
            preregistration.code_identity_digest,
            manifest["code_identity_digest"],
        )
        self.assertEqual(
            canonical_json_sha256({"files": manifest["source_files"]}),
            manifest["code_identity_digest"],
        )
        for item in manifest["source_files"]:
            self.assertEqual(64, len(item["sha256"]))
            self.assertTrue(
                all(character in "0123456789abcdef" for character in item["sha256"])
            )
        self.assertEqual(
            canonical_json_sha256(manifest["provider_configuration"]),
            manifest["provider_configuration_digest"],
        )
        self.assertEqual(
            canonical_json_sha256(manifest["contract_identity"]),
            preregistration.contract_identity_digest,
        )
        intents = by_schema[ExperimentAttemptIntent.SCHEMA]
        self.assertEqual(1, len(intents))
        rejection = by_schema[
            "P062AssignmentPreflightRejectionReceipt@1"
        ][0]
        self.assertEqual("rejected-before-provider", rejection["status"])
        self.assertEqual(0, rejection["provider_invocation_count"])
        self.assertFalse(rejection["empirical_result_claimed"])
        supersession = by_schema["P062StudySupersessionReceipt@1"][0]
        self.assertEqual("superseded-before-provider", supersession["status"])
        self.assertEqual("study-002", supersession["successor_run_id"])
        self.assertEqual(0, supersession["provider_invocation_count"])
        self.assertFalse(
            {ExperimentAttemptReceipt.SCHEMA, ExperimentOutcome.SCHEMA}
            & set(by_schema)
        )
        source_repositories = {
            project_id: FilesystemProjectRepository.open(
                _project_root(project_id)
            )
            for project_id, _ in CASE_SPECS
        }
        bind_preregistered_cases(
            preregistration,
            study_run=run,
            source_repositories=source_repositories,
        )
        repository.verify()

    def test_successor_study_freezes_only_preflight_ready_contexts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        run = repository.load_run("study-002")
        payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run.run_id,
                ),
            )
        )
        preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        indexes = tuple(
            ExperimentResultIndex.from_dict(item)
            for item in payloads
            if item.get("schema") == ExperimentResultIndex.SCHEMA
        )
        index = max(indexes, key=lambda item: item.generated_at)
        manifest = next(
            item
            for item in payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        self.assertEqual(
            "p062-semantic-geometry-study-v2",
            preregistration.study_id,
        )
        self.assertEqual("study-001", manifest["supersedes_run_id"])
        self.assertEqual(8, index.lifecycle_counts["planned"])
        self.assertEqual(1, index.lifecycle_counts["failed"])
        self.assertEqual(0, index.comparable_sample_size)
        self.assertFalse(index.evidence_table_ready)
        attempt = ExperimentAttemptReceipt.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
            )
        )
        outcome = ExperimentOutcome.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentOutcome.SCHEMA
            )
        )
        self.assertEqual(ExperimentAttemptStatus.PROVIDER_FAILED, attempt.status)
        self.assertEqual(["offline"], [item.status for item in attempt.provider_receipts])
        self.assertEqual("model.provider_os_error", attempt.error_code)
        self.assertFalse(outcome.eligible_for_comparison)
        self.assertTrue(
            all(
                item.status.value == "unknown"
                for item in outcome.observations
            )
        )
        diagnosis = next(
            item
            for item in payloads
            if item.get("schema")
            == "P062ProviderExecutableDiagnosisReceipt@1"
        )
        self.assertEqual("codex", diagnosis["failed_executable"])
        self.assertEqual("codex.cmd", diagnosis["verified_executable"])
        self.assertFalse(diagnosis["model_invocation_performed"])
        self.assertFalse(diagnosis["retry_performed"])
        source_repositories = {
            project_id: FilesystemProjectRepository.open(
                _project_root(project_id)
            )
            for project_id, _ in CASE_SPECS
        }
        bind_preregistered_cases(
            preregistration,
            study_run=run,
            source_repositories=source_repositories,
        )
        for case in preregistration.cases:
            case_repository = source_repositories[case.project_id]
            case_run = case_repository.load_run(case.run_id)
            retained = {
                ref.uri: case_repository.load_json(ref)
                for destination in (
                    PersistenceDestination(PersistenceArea.INPUT),
                    PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=case_run.run_id,
                    ),
                )
                for ref in case_repository.list_json(
                    run=case_run,
                    destination=destination,
                )
                if ref.uri in case.input_record_refs
            }
            contexts = tuple(
                ProductionAuthoringContext.from_dict(item)
                for item in retained.values()
                if item.get("schema") == ProductionAuthoringContext.SCHEMA
            )
            self.assertEqual(2, len(contexts))
            self.assertTrue(
                all(item.required_commitment_refs for item in contexts)
            )
            self.assertEqual(
                1,
                sum(
                    item.get("schema") == "P062AssignmentPreflightReceipt@1"
                    and item.get("status") == "passed"
                    for item in retained.values()
                ),
            )
        repository.verify()

    def test_windows_wrapper_timeout_is_retained_without_rewriting_the_study(self) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        run = repository.load_run("study-003")
        payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run.run_id,
                ),
            )
        )
        preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        manifest = next(
            item
            for item in payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        rejection = next(
            item
            for item in payloads
            if item.get("schema") == "P062AttemptBindingRejectionReceipt@1"
        )
        supersession = next(
            item
            for item in payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        index = max(
            (
                ExperimentResultIndex.from_dict(item)
                for item in payloads
                if item.get("schema") == ExperimentResultIndex.SCHEMA
            ),
            key=lambda item: item.generated_at,
        )
        self.assertEqual(
            "p062-semantic-geometry-study-v3",
            preregistration.study_id,
        )
        self.assertEqual("study-002", manifest["supersedes_run_id"])
        self.assertEqual(
            "codex.cmd",
            manifest["provider_configuration"]["command"][0],
        )
        self.assertEqual(120_000, rejection["provider_deadline_ms"])
        self.assertEqual(167_109, rejection["observed_provider_duration_ms"])
        self.assertEqual("timeout", rejection["provider_status"])
        self.assertFalse(rejection["outcome_persisted"])
        self.assertFalse(rejection["retry_performed"])
        self.assertFalse(rejection["fallback_used"])
        self.assertEqual("study-004", supersession["successor_run_id"])
        self.assertEqual(1, index.lifecycle_counts["running"])
        self.assertFalse(
            {
                ExperimentAttemptReceipt.SCHEMA,
                ExperimentOutcome.SCHEMA,
            }
            & {item.get("schema") for item in payloads}
        )

        successor_run = repository.load_run("study-004")
        successor_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=successor_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=successor_run.run_id,
                ),
            )
        )
        successor = ExperimentPreregistration.from_dict(
            next(
                item
                for item in successor_payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        successor_manifest = next(
            item
            for item in successor_payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        authorization = next(
            item
            for item in successor_payloads
            if item.get("schema") == "P062LiveExecutionAuthorizationRequest@2"
        )
        correction = next(
            item
            for item in successor_payloads
            if item.get("schema")
            == "P062AuthorizationRequestCorrectionReceipt@1"
        )
        successor_index = max(
            (
                ExperimentResultIndex.from_dict(item)
                for item in successor_payloads
                if item.get("schema") == ExperimentResultIndex.SCHEMA
            ),
            key=lambda item: item.generated_at,
        )
        self.assertEqual(
            "p062-semantic-geometry-study-v4",
            successor.study_id,
        )
        self.assertEqual("study-003", successor_manifest["supersedes_run_id"])
        self.assertEqual(
            "configured-deadline-plus-observed-post-timeout-process-cleanup-wall-clock",
            successor_manifest["contract_identity"][
                "provider_timeout_observation_policy"
            ],
        )
        self.assertEqual(9, successor_index.lifecycle_counts["planned"])
        self.assertEqual(0, successor_index.comparable_sample_size)
        self.assertFalse(successor_index.evidence_table_ready)
        self.assertEqual(
            "pending-user-authorization",
            authorization["decision_status"],
        )
        self.assertEqual(300_000, authorization["proposed_timeout_ms"])
        self.assertEqual(0, authorization["provider_invocation_count"])
        self.assertFalse(authorization["mechanical_retry_allowed"])
        self.assertFalse(authorization["fallback_allowed"])
        self.assertNotEqual(
            successor_manifest["provider_fingerprint"],
            authorization["proposed_provider_fingerprint"],
        )
        self.assertEqual(
            authorization["supersedes_request_ref"],
            correction["superseded_request_ref"],
        )
        self.assertEqual(
            64,
            len(authorization["predecessor_request_digest"]),
        )
        self.assertEqual(
            64,
            len(authorization["predecessor_context_digest"]),
        )
        self.assertFalse(
            {
                ExperimentAttemptIntent.SCHEMA,
                ExperimentAttemptReceipt.SCHEMA,
                ExperimentOutcome.SCHEMA,
            }
            & {item.get("schema") for item in successor_payloads}
        )
        self.assertEqual(
            canonical_json_sha256(
                {"files": successor_manifest["source_files"]}
            ),
            successor.code_identity_digest,
        )
        for item in successor_manifest["source_files"]:
            self.assertEqual(64, len(item["sha256"]))
            self.assertTrue(
                all(character in "0123456789abcdef" for character in item["sha256"])
            )
        repository.verify()

    def test_provider_success_rejection_recovery_starts_a_new_frozen_study(self) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        failed_run = repository.load_run("study-005")
        failed_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=failed_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=failed_run.run_id,
                ),
            )
        )
        diagnostic = next(
            item
            for item in failed_payloads
            if item.get("schema") == "P062AttemptArchiveFailureReceipt@1"
        )
        supersession = next(
            item
            for item in failed_payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        self.assertEqual(
            "stopped-before-p036-attempt-receipt",
            diagnostic["status"],
        )
        self.assertTrue(diagnostic["provider_success_observed_in_controller_process"])
        self.assertFalse(diagnostic["provider_envelope_retained"])
        self.assertFalse(diagnostic["provider_receipt_reconstructed"])
        self.assertEqual(
            "spatial_authoring.proposal_rejected",
            diagnostic["deterministic_rejection_error_code"],
        )
        self.assertFalse(diagnostic["empirical_result_claimed"])
        self.assertFalse(diagnostic["building_result_claimed"])
        self.assertEqual("study-006", supersession["successor_run_id"])
        self.assertFalse(
            {
                ExperimentAttemptReceipt.SCHEMA,
                ExperimentOutcome.SCHEMA,
            }
            & {item.get("schema") for item in failed_payloads}
        )

        successor_run = repository.load_run("study-006")
        successor_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=successor_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=successor_run.run_id,
                ),
            )
        )
        preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in successor_payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        manifest = next(
            item
            for item in successor_payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        indexes = tuple(
            ExperimentResultIndex.from_dict(item)
            for item in successor_payloads
            if item.get("schema") == ExperimentResultIndex.SCHEMA
        )
        planned_index = next(
            item for item in indexes if item.lifecycle_counts["planned"] == 9
        )
        terminal_index = next(
            item for item in indexes if item.lifecycle_counts["failed"] == 1
        )
        attempt = ExperimentAttemptReceipt.from_dict(
            next(
                item
                for item in successor_payloads
                if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
            )
        )
        outcome = ExperimentOutcome.from_dict(
            next(
                item
                for item in successor_payloads
                if item.get("schema") == ExperimentOutcome.SCHEMA
            )
        )
        self.assertEqual(
            "p062-semantic-geometry-study-rejection-recovery",
            preregistration.study_id,
        )
        self.assertEqual("study-005", manifest["supersedes_run_id"])
        self.assertEqual(
            "successful-P053-plus-deterministic-rejection-is-durable-failure",
            manifest["contract_identity"]["post_provider_rejection_policy"],
        )
        self.assertEqual(9, planned_index.lifecycle_counts["planned"])
        self.assertEqual(8, terminal_index.lifecycle_counts["planned"])
        self.assertEqual(1, terminal_index.lifecycle_counts["failed"])
        self.assertEqual(0, terminal_index.comparable_sample_size)
        self.assertFalse(terminal_index.evidence_table_ready)
        self.assertEqual(
            ExperimentAttemptStatus.PIPELINE_REJECTED,
            attempt.status,
        )
        self.assertEqual(["success"], [item.status for item in attempt.provider_receipts])
        self.assertEqual("production.compilation_failed", attempt.error_code)
        self.assertEqual((), attempt.terminal_evidence)
        self.assertFalse(outcome.eligible_for_comparison)
        self.assertTrue(
            all(item.status.value == "unknown" for item in outcome.observations)
        )
        self.assertEqual(
            canonical_json_sha256({"files": manifest["source_files"]}),
            preregistration.code_identity_digest,
        )
        for item in manifest["source_files"]:
            self.assertEqual(64, len(item["sha256"]))
            self.assertTrue(
                all(character in "0123456789abcdef" for character in item["sha256"])
            )
        repository.verify()

    def test_selection_contract_study_retains_two_successes_and_timeout(self) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        predecessor = repository.load_run("study-010")
        predecessor_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=predecessor,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=predecessor.run_id,
                ),
            )
        )
        supersession = next(
            item
            for item in predecessor_payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        self.assertEqual(
            "superseded-after-retained-selection-field-mismatch",
            supersession["status"],
        )
        self.assertEqual("study-011", supersession["successor_run_id"])
        self.assertEqual(3, supersession["provider_invocation_count"])
        self.assertFalse(supersession["building_result_claimed"])

        run = repository.load_run("study-011")
        payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run.run_id,
                ),
            )
        )
        preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        manifest = next(
            item
            for item in payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        attempt = ExperimentAttemptReceipt.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
            )
        )
        outcome = ExperimentOutcome.from_dict(
            next(
                item
                for item in payloads
                if item.get("schema") == ExperimentOutcome.SCHEMA
            )
        )
        indexes = tuple(
            ExperimentResultIndex.from_dict(item)
            for item in payloads
            if item.get("schema") == ExperimentResultIndex.SCHEMA
        )
        terminal = next(
            item for item in indexes if item.lifecycle_counts["failed"] == 1
        )

        self.assertEqual(
            "p062-semantic-geometry-study-selection-contract",
            preregistration.study_id,
        )
        self.assertEqual("study-010", manifest["supersedes_run_id"])
        self.assertEqual(
            "SchematicOptionSelectionContract@1",
            manifest["contract_identity"][
                "schematic_selection_contract_schema"
            ],
        )
        self.assertEqual(
            canonical_json_sha256({"files": manifest["source_files"]}),
            preregistration.code_identity_digest,
        )
        self.assertEqual(
            canonical_json_sha256(manifest["contract_identity"]),
            preregistration.contract_identity_digest,
        )
        self.assertEqual(ExperimentAttemptStatus.TIMED_OUT, attempt.status)
        self.assertEqual(786_432, attempt.duration_ms)
        self.assertEqual(
            {"success": 2, "timeout": 1},
            {
                status: sum(
                    item.status == status for item in attempt.provider_receipts
                )
                for status in {item.status for item in attempt.provider_receipts}
            },
        )
        self.assertEqual("model.timeout", attempt.error_code)
        self.assertEqual((), attempt.terminal_evidence)
        self.assertFalse(outcome.eligible_for_comparison)
        self.assertTrue(
            all(item.status.value == "unknown" for item in outcome.observations)
        )
        self.assertEqual(8, terminal.lifecycle_counts["planned"])
        self.assertEqual(0, terminal.comparable_sample_size)
        self.assertFalse(terminal.evidence_table_ready)

        selection_timeout_supersession = next(
            item
            for item in payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        self.assertEqual(
            "superseded-after-retained-schematic-selection-timeout",
            selection_timeout_supersession["status"],
        )
        self.assertEqual(
            "study-012",
            selection_timeout_supersession["successor_run_id"],
        )
        projection_run = repository.load_run("study-012")
        projection_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=projection_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=projection_run.run_id,
                ),
            )
        )
        projection_preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in projection_payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        projection_manifest = next(
            item
            for item in projection_payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        projection_attempt = ExperimentAttemptReceipt.from_dict(
            next(
                item
                for item in projection_payloads
                if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
            )
        )
        projection_outcome = ExperimentOutcome.from_dict(
            next(
                item
                for item in projection_payloads
                if item.get("schema") == ExperimentOutcome.SCHEMA
            )
        )
        self.assertEqual(
            "p062-semantic-geometry-study-selection-projection",
            projection_preregistration.study_id,
        )
        self.assertEqual("study-011", projection_manifest["supersedes_run_id"])
        self.assertEqual(
            "SchematicOptionDecisionProjection@1",
            projection_manifest["contract_identity"][
                "schematic_option_decision_projection_schema"
            ],
        )
        self.assertEqual(
            canonical_json_sha256(
                {"files": projection_manifest["source_files"]}
            ),
            projection_preregistration.code_identity_digest,
        )
        self.assertEqual(
            ExperimentAttemptStatus.PIPELINE_REJECTED,
            projection_attempt.status,
        )
        self.assertEqual(199_662, projection_attempt.duration_ms)
        self.assertEqual(
            ["success"],
            [item.status for item in projection_attempt.provider_receipts],
        )
        self.assertEqual(
            "spatial_authoring.source_rejected",
            projection_attempt.error_code,
        )
        self.assertEqual((), projection_attempt.terminal_evidence)
        self.assertFalse(projection_outcome.eligible_for_comparison)
        self.assertTrue(
            all(
                item.status.value == "unknown"
                for item in projection_outcome.observations
            )
        )
        projection_supersession = next(
            item
            for item in projection_payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        self.assertEqual(
            "superseded-after-retained-semantic-spatial-source-rejection",
            projection_supersession["status"],
        )
        self.assertEqual("study-013", projection_supersession["successor_run_id"])

        repair_run = repository.load_run("study-013")
        repair_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=repair_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=repair_run.run_id,
                ),
            )
        )
        repair_preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in repair_payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        repair_manifest = next(
            item
            for item in repair_payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        diagnostic = next(
            item
            for item in repair_payloads
            if item.get("schema") == "P062AttemptArchiveFailureReceipt@1"
        )
        repair_supersession = next(
            item
            for item in repair_payloads
            if item.get("schema") == "P062StudySupersessionReceipt@1"
        )
        self.assertEqual(
            "p062-semantic-geometry-study-single-repair",
            repair_preregistration.study_id,
        )
        self.assertEqual(
            "SemanticSpatialRepairFeedback@1",
            repair_manifest["contract_identity"][
                "semantic_spatial_repair_feedback_schema"
            ],
        )
        self.assertEqual("stopped-before-p036-attempt-receipt", diagnostic["status"])
        self.assertEqual(2, diagnostic["provider_invocation_count"])
        self.assertTrue(diagnostic["authoring_receipts_persisted"])
        self.assertFalse(diagnostic["provider_envelope_retained"])
        self.assertFalse(diagnostic["provider_receipt_reconstructed"])
        self.assertFalse(diagnostic["empirical_result_claimed"])
        self.assertFalse(diagnostic["building_result_claimed"])
        self.assertFalse(
            {ExperimentAttemptReceipt.SCHEMA, ExperimentOutcome.SCHEMA}
            & {item.get("schema") for item in repair_payloads}
        )
        self.assertEqual(
            "superseded-after-unarchived-cross-option-conflict",
            repair_supersession["status"],
        )
        self.assertEqual("study-014", repair_supersession["successor_run_id"])

        alternative_run = repository.load_run("study-014")
        alternative_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=alternative_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=alternative_run.run_id,
                ),
            )
        )
        alternative_preregistration = ExperimentPreregistration.from_dict(
            next(
                item
                for item in alternative_payloads
                if item.get("schema") == ExperimentPreregistration.SCHEMA
            )
        )
        alternative_manifest = next(
            item
            for item in alternative_payloads
            if item.get("schema") == "P062StudyIdentityManifest@1"
        )
        self.assertEqual(
            "p062-semantic-geometry-study-alternative-set",
            alternative_preregistration.study_id,
        )
        self.assertEqual(
            "SpatialAlternativeAuthoringContext@1",
            alternative_manifest["contract_identity"][
                "spatial_alternative_authoring_context_schema"
            ],
        )
        self.assertEqual(
            canonical_json_sha256(
                {"files": alternative_manifest["source_files"]}
            ),
            alternative_preregistration.code_identity_digest,
        )
        alternative_attempt = ExperimentAttemptReceipt.from_dict(
            next(
                item
                for item in alternative_payloads
                if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
            )
        )
        alternative_outcome = ExperimentOutcome.from_dict(
            next(
                item
                for item in alternative_payloads
                if item.get("schema") == ExperimentOutcome.SCHEMA
            )
        )
        alternative_index = ExperimentResultIndex.from_dict(
            next(
                item
                for item in alternative_payloads
                if item.get("schema") == ExperimentResultIndex.SCHEMA
                and item.get("comparable_sample_size") == 1
            )
        )
        self.assertEqual(
            ExperimentAttemptStatus.PIPELINE_REJECTED,
            alternative_attempt.status,
        )
        self.assertEqual(821_072, alternative_attempt.duration_ms)
        self.assertEqual(5, len(alternative_attempt.provider_receipts))
        self.assertTrue(
            all(item.status == "success" for item in alternative_attempt.provider_receipts)
        )
        self.assertEqual(
            {
                "component-family-compilation",
                "component-family-realization",
                "sandbox-realization",
                "semantic-geometry-production",
            },
            {item.role for item in alternative_attempt.terminal_evidence},
        )
        self.assertEqual(
            "architectural_usability.failed",
            alternative_attempt.error_code,
        )
        self.assertTrue(alternative_outcome.eligible_for_comparison)
        observations = {
            item.metric_id: item for item in alternative_outcome.observations
        }
        self.assertFalse(observations["architectural-usable"].value)
        self.assertFalse(observations["completion"].value)
        self.assertEqual(0.2, observations["family-binding-coverage"].value)
        self.assertEqual(5, observations["model-call-count"].value)
        self.assertEqual(0.75, observations["project-constraint-pass-rate"].value)
        self.assertEqual(1, observations["repair-count"].value)
        self.assertEqual(821_072, observations["wall-clock-ms"].value)
        self.assertEqual(
            {
                "completed": 0,
                "failed": 1,
                "planned": 8,
                "running": 0,
                "terminal_unmeasured": 0,
            },
            alternative_index.lifecycle_counts,
        )
        self.assertEqual(1, alternative_index.comparable_sample_size)
        self.assertFalse(alternative_index.evidence_table_ready)

        clinic_repository = FilesystemProjectRepository.open(
            _project_root("p062-clinic-case")
        )
        clinic_run = clinic_repository.load_run("experiment-001")
        clinic_payloads = tuple(
            clinic_repository.load_json(ref)
            for ref in clinic_repository.list_json(
                run=clinic_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=clinic_run.run_id,
                ),
            )
        )
        architectural = next(
            item
            for item in clinic_payloads
            if item.get("schema") == "ArchitecturalUsabilityReceipt@1"
        )
        findings = {
            item["criterion_id"]: item for item in architectural["findings"]
        }
        self.assertEqual("failed", architectural["status"])
        self.assertEqual("fail", findings["usable-main-entry"]["status"])
        self.assertEqual("0", findings["usable-main-entry"]["observed_json"])
        self.assertEqual("1", findings["usable-main-entry"]["expected_json"])
        family_compilation = next(
            item
            for item in clinic_payloads
            if item.get("schema") == "ComponentFamilyCompilationReceipt@1"
        )
        family_realization = next(
            item
            for item in clinic_payloads
            if item.get("schema") == "ComponentFamilyRealizationReceipt@1"
        )
        self.assertEqual("compiled", family_compilation["status"])
        self.assertEqual(1, len(family_compilation["compiled_instances"]))
        self.assertEqual("realized", family_realization["status"])
        clinic_repository.verify()
        repository.verify()

    def test_same_project_terminal_chain_cross_checks_all_exact_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap = bootstrap_raw_request_project(
                root / "p062-terminal-chain",
                project_id="p062-terminal-chain",
                prompt="Synthetic contract fixture; no empirical claim.",
                run_id="chain-001",
                synthetic_test=True,
            )
            repository = FilesystemProjectRepository.open(
                root / "p062-terminal-chain"
            )
            run = bootstrap.run
            (
                state,
                program,
                scene,
                sandbox,
                _,
                contract,
                observation,
            ) = _compile_declared_fact_contract(
                project_id=run.project_id,
                run_id=run.run_id,
                base=run.base,
                artifact_ref=bootstrap.request.uri,
                request_ref=bootstrap.request.uri,
            )
            architectural = evaluate_architectural_usability(
                contract,
                (observation,),
            )
            _, _, template_set = _parametric_fixture()
            family_set = _family_set(
                state,
                program,
                template_set.instances[0],
            )
            compilation = compile_component_families(
                state,
                program,
                family_set,
            )
            family_realization = bind_component_family_realization(
                compilation,
                scene,
                sandbox,
            )
            initial = InitialSemanticGeometryReceipt(
                transaction_id="p062-terminal-chain",
                project_id=run.project_id,
                run_id=run.run_id,
                base_state_digest=run.base.require_digest(),
                design_state_digest=state.state_digest,
                component_proposal_digest=(
                    state.selected_schematic.option.proposal.proposal_digest
                ),
                geometry_proposal_digest=program.proposal.proposal_digest,
                geometry_program_digest=program.program_digest,
                source_refs=(bootstrap.request.uri,),
            )
            destination = PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            )
            content = initial.to_dict()
            production_ref = repository.put_json(
                run=run,
                destination=destination,
                record_kind="production-lifecycle",
                payload={
                    "schema": "ProductionTransitionRecord@1",
                    "project_id": run.project_id,
                    "run_id": run.run_id,
                    "base": {
                        "project_id": run.base.project_id,
                        "version": run.base.version,
                        "state_sha256": run.base.require_digest(),
                    },
                    "role": "lifecycle-receipt",
                    "semantic_digest": digest_value(content),
                    "content_sha256": canonical_json_sha256(content),
                    "content": content,
                    "canonical_write_authority": False,
                },
            )
            refs = {
                "semantic-geometry-production": production_ref,
                "sandbox-realization": repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind="sandbox-realization",
                    payload=sandbox.to_dict(),
                ),
                "architectural-usability": repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind="architectural-usability",
                    payload=architectural.to_dict(),
                ),
                "component-family-compilation": repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind="component-family-compilation",
                    payload=compilation.to_dict(),
                ),
                "component-family-realization": repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind="component-family-realization",
                    payload=family_realization.to_dict(),
                ),
            }
            chain = bind_terminal_chain(
                repository,
                run=run,
                condition=_conditions()[0],
                record_refs=refs,
            )
            self.assertEqual(FULL_TERMINAL_ROLES, tuple(sorted(item.role for item in chain)))

            drifted = replace(
                architectural,
                geometry_program_digest="f" * 64,
            )
            with self.assertRaises(ExperimentProtocolError):
                bind_terminal_chain(
                    repository,
                    run=run,
                    condition=_conditions()[0],
                    record_refs={
                        **refs,
                        "architectural-usability": repository.put_json(
                            run=run,
                            destination=destination,
                            record_kind="drifted-architectural-usability",
                            payload=drifted.to_dict(),
                        ),
                    },
                )

            empty_compilation = compile_component_families(
                state,
                program,
                replace(family_set, instances=()),
            )
            empty_realization = bind_component_family_realization(
                empty_compilation,
                scene,
                sandbox,
            )
            with self.assertRaises(ExperimentProtocolError):
                bind_terminal_chain(
                    repository,
                    run=run,
                    condition=_conditions()[0],
                    record_refs={
                        **refs,
                        "component-family-compilation": repository.put_json(
                            run=run,
                            destination=destination,
                            record_kind="empty-family-compilation",
                            payload=empty_compilation.to_dict(),
                        ),
                        "component-family-realization": repository.put_json(
                            run=run,
                            destination=destination,
                            record_kind="empty-family-realization",
                            payload=empty_realization.to_dict(),
                        ),
                    },
                )

    def test_known_p056_p060_p061_receipts_require_exact_schema_and_status(self) -> None:
        root = Path(__file__).resolve().parents[2]
        p060 = FilesystemProjectRepository.open(
            _project_root("p060-architectural-usability")
        )
        p060_run = p060.load_run("architectural-usability-001")
        architectural = bind_terminal_record(
            p060,
            run=p060_run,
            ref=_record_for_schema(
                p060,
                p060_run,
                "ArchitecturalUsabilityReceipt@1",
            ),
            requirement=ExperimentTerminalRequirement(
                role="architectural-usability",
                source_schema="ArchitecturalUsabilityReceipt@1",
                accepted_statuses=("passed",),
            ),
        )
        self.assertEqual("passed", architectural.source_status)

        p061 = FilesystemProjectRepository.open(
            _project_root("p061-component-family-protocol")
        )
        p061_run = p061.load_run("mesh-001")
        compilation = bind_terminal_record(
            p061,
            run=p061_run,
            ref=_record_for_schema(
                p061,
                p061_run,
                "ComponentFamilyCompilationReceipt@1",
            ),
            requirement=ExperimentTerminalRequirement(
                role="component-family-compilation",
                source_schema="ComponentFamilyCompilationReceipt@1",
                accepted_statuses=("compiled",),
            ),
        )
        realization = bind_terminal_record(
            p061,
            run=p061_run,
            ref=_record_for_schema(
                p061,
                p061_run,
                "ComponentFamilyRealizationReceipt@1",
            ),
            requirement=ExperimentTerminalRequirement(
                role="component-family-realization",
                source_schema="ComponentFamilyRealizationReceipt@1",
                accepted_statuses=("realized",),
            ),
        )
        self.assertEqual("compiled", compilation.source_status)
        self.assertEqual("realized", realization.source_status)

        try:
            from tools._probe_paths import resolve_probe_root

            p058_root = resolve_probe_root("p058-progressive-pantheon")
        except Exception:
            p058_root = root / "probes" / "p058-progressive-pantheon"
        if not (p058_root / "project.json").is_file():
            self.skipTest(
                "external workspace evidence probe p058 unavailable"
            )
        p058 = FilesystemProjectRepository.open(p058_root)
        p058_run = p058.load_run("fresh-pantheon-001")
        production = bind_terminal_record(
            p058,
            run=p058_run,
            ref=_record_for_schema(
                p058,
                p058_run,
                "ProductionTransitionRecord@1",
                role="lifecycle-receipt",
                content_schema="InitialSemanticGeometryReceipt@1",
            ),
            requirement=ExperimentTerminalRequirement(
                role="semantic-geometry-production",
                source_schema="ProductionTransitionRecord@1",
                accepted_statuses=("persisted",),
            ),
        )
        self.assertEqual("persisted", production.source_status)
        with self.assertRaises(ExperimentProtocolError):
            bind_terminal_record(
                p061,
                run=p061_run,
                ref=_record_for_schema(
                    p061,
                    p061_run,
                    "ComponentFamilyCompilationReceipt@1",
                ),
                requirement=ExperimentTerminalRequirement(
                    role="component-family-compilation",
                    source_schema="ComponentFamilyCompilationReceipt@1",
                    accepted_statuses=("rejected",),
                ),
            )

    def test_preregistration_reloads_three_separate_p036_projects_without_results(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study, cases = _bootstrap_study(root)
            preregistration = _preregistration(cases)
            repository = FilesystemProjectRepository.open(
                root / "p062-experiment-study"
            )
            case_repositories = {
                case.project_id: FilesystemProjectRepository.open(
                    root / case.project_id
                )
                for case in cases
            }
            with self.assertRaisesRegex(
                ExperimentProtocolError,
                "source repository is unavailable",
            ):
                persist_preregistration(
                    repository,
                    run=study.run,
                    preregistration=preregistration,
                    source_repositories={},
                )
            invented_case = replace(
                preregistration.cases[0],
                input_record_refs=tuple(
                    sorted(
                        (
                            *preregistration.cases[0].input_record_refs,
                            (
                                f"project://{cases[0].project_id}/runs/"
                                "experiment-001/records/missing.json"
                            ),
                        )
                    )
                ),
            )
            with self.assertRaisesRegex(
                ExperimentProtocolError,
                "not retained exactly once",
            ):
                persist_preregistration(
                    repository,
                    run=study.run,
                    preregistration=replace(
                        preregistration,
                        cases=(invented_case, *preregistration.cases[1:]),
                    ),
                    source_repositories=case_repositories,
                )
            changed_raw_case = replace(
                preregistration.cases[0],
                raw_request_digest="0" * 64,
            )
            with self.assertRaisesRegex(
                ExperimentProtocolError,
                "raw request digest changed",
            ):
                persist_preregistration(
                    repository,
                    run=study.run,
                    preregistration=replace(
                        preregistration,
                        cases=(changed_raw_case, *preregistration.cases[1:]),
                    ),
                    source_repositories=case_repositories,
                )
            ref = persist_preregistration(
                repository,
                run=study.run,
                preregistration=preregistration,
                source_repositories=case_repositories,
            )

            reloaded = ExperimentPreregistration.from_dict(
                repository.load_json(ref)
            )
            self.assertEqual(preregistration, reloaded)
            self.assertEqual(3, len(reloaded.cases))
            self.assertEqual(9, len(reloaded.assignments))
            records = repository.list_json(
                run=study.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=study.run.run_id,
                ),
            )
            schemas = {repository.load_json(item).get("schema") for item in records}
            self.assertNotIn(ExperimentAttemptIntent.SCHEMA, schemas)
            self.assertNotIn(ExperimentAttemptReceipt.SCHEMA, schemas)
            self.assertNotIn(ExperimentOutcome.SCHEMA, schemas)
            planned_index = rebuild_result_index(
                repository,
                run=study.run,
                generated_at="2026-08-17T14:20:00+08:00",
            )
            index_ref = persist_result_index(
                repository,
                run=study.run,
                index=planned_index,
            )
            self.assertEqual(
                {ExperimentAssignmentLifecycle.PLANNED},
                {item.lifecycle for item in planned_index.assignments},
            )
            self.assertFalse(planned_index.evidence_table_ready)
            self.assertEqual(
                planned_index,
                ExperimentResultIndex.from_dict(repository.load_json(index_ref)),
            )
            for case in cases:
                FilesystemProjectRepository.open(
                    root / case.project_id
                ).verify()
            repository.verify()

            with self.assertRaises(ExperimentProtocolError):
                persist_preregistration(
                    repository,
                    run=study.run,
                    preregistration=replace(
                        preregistration,
                        study_wall_clock_limit_ms=7_200_000,
                    ),
                    source_repositories={
                        case.project_id: FilesystemProjectRepository.open(
                            root / case.project_id
                        )
                        for case in cases
                    },
                )

    def test_p036_attempt_lifecycle_is_ordered_idempotent_and_reloadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study, cases = _bootstrap_study(root)
            preregistration = _preregistration(cases)
            study_repository = FilesystemProjectRepository.open(
                root / "p062-experiment-study"
            )
            prereg_ref = persist_preregistration(
                study_repository,
                run=study.run,
                preregistration=preregistration,
                source_repositories={
                    case.project_id: FilesystemProjectRepository.open(
                        root / case.project_id
                    )
                    for case in cases
                },
            )
            self.assertEqual(
                prereg_ref.uri,
                _run_experiment_cli(
                    "preregister",
                    "--study-root",
                    str(root / "p062-experiment-study"),
                    "--run-id",
                    study.run.run_id,
                    "--payload",
                    str(
                        _write_cli_payload(
                            root,
                            "preregistration.json",
                            preregistration.to_dict(),
                        )
                    ),
                    *(
                        value
                        for case in cases
                        for value in (
                            "--source-project",
                            f"{case.project_id}={root / case.project_id}",
                        )
                    ),
                ),
            )
            intent = compile_experiment_attempt_intent(
                preregistration,
                assignment_id="assignment-case-1-condition-full",
                attempt_id="attempt-case-1-full-0",
                attempt_index=0,
                issued_at=ISSUED_AT,
            )
            intent_ref = persist_attempt_intent(
                study_repository,
                run=study.run,
                preregistration_ref=prereg_ref,
                intent=intent,
            )
            self.assertEqual(
                intent_ref.uri,
                _run_experiment_cli(
                    "intent",
                    "--study-root",
                    str(root / "p062-experiment-study"),
                    "--run-id",
                    study.run.run_id,
                    "--payload",
                    str(_write_cli_payload(root, "intent.json", intent.to_dict())),
                ),
            )
            running_index = rebuild_result_index(
                study_repository,
                run=study.run,
                generated_at="2026-08-17T14:21:00+08:00",
            )
            self.assertEqual(
                ExperimentAssignmentLifecycle.RUNNING,
                running_index.assignments[0].lifecycle,
            )
            first_case = cases[0]
            case_repository = FilesystemProjectRepository.open(
                root / first_case.project_id
            )
            provider = _provider_source(case_repository, first_case.run)
            terminal = _terminal_sources(case_repository, first_case.run)
            attempt = compile_experiment_attempt(
                preregistration,
                intent,
                status=ExperimentAttemptStatus.COMPLETED,
                duration_ms=1_800,
                provider_receipts=(provider,),
                terminal_evidence=terminal,
            )
            attempt_ref = persist_attempt_receipt(
                study_repository,
                run=study.run,
                intent_ref=intent_ref,
                receipt=attempt,
                source_repositories={first_case.project_id: case_repository},
            )
            self.assertEqual(
                attempt_ref.uri,
                _run_experiment_cli(
                    "receipt",
                    "--study-root",
                    str(root / "p062-experiment-study"),
                    "--run-id",
                    study.run.run_id,
                    "--payload",
                    str(_write_cli_payload(root, "receipt.json", attempt.to_dict())),
                    "--source-project",
                    f"{first_case.project_id}={root / first_case.project_id}",
                ),
            )
            unmeasured_index = rebuild_result_index(
                study_repository,
                run=study.run,
                generated_at="2026-08-17T14:22:00+08:00",
            )
            self.assertEqual(
                ExperimentAssignmentLifecycle.TERMINAL_UNMEASURED,
                unmeasured_index.assignments[0].lifecycle,
            )
            with self.assertRaises(ExperimentProtocolError):
                persist_attempt_receipt(
                    study_repository,
                    run=study.run,
                    intent_ref=intent_ref,
                    receipt=replace(
                        attempt,
                        provider_receipts=(
                            replace(
                                provider,
                                receipt_id="forged-detached-receipt-id",
                            ),
                        ),
                    ),
                    source_repositories={
                        first_case.project_id: case_repository
                    },
                )
            observations = tuple(
                replace(item, evidence=(terminal[0],))
                for item in _measured_observations(
                    preregistration.case("case-1")
                )
            )
            outcome = compile_experiment_outcome(
                preregistration,
                attempt,
                observations,
            )
            outcome_ref = persist_outcome(
                study_repository,
                run=study.run,
                attempt_ref=attempt_ref,
                outcome=outcome,
                source_repositories={first_case.project_id: case_repository},
            )
            self.assertEqual(
                outcome_ref.uri,
                _run_experiment_cli(
                    "outcome",
                    "--study-root",
                    str(root / "p062-experiment-study"),
                    "--run-id",
                    study.run.run_id,
                    "--payload",
                    str(_write_cli_payload(root, "outcome.json", outcome.to_dict())),
                    "--source-project",
                    f"{first_case.project_id}={root / first_case.project_id}",
                ),
            )
            completed_index = rebuild_result_index(
                study_repository,
                run=study.run,
                generated_at="2026-08-17T14:23:00+08:00",
            )
            completed_index_ref = persist_result_index(
                study_repository,
                run=study.run,
                index=completed_index,
            )
            self.assertEqual(
                completed_index_ref.uri,
                _run_experiment_cli(
                    "index",
                    "--study-root",
                    str(root / "p062-experiment-study"),
                    "--run-id",
                    study.run.run_id,
                    "--generated-at",
                    completed_index.generated_at,
                ),
            )
            self.assertEqual(
                ExperimentAssignmentLifecycle.COMPLETED,
                completed_index.assignments[0].lifecycle,
            )
            self.assertEqual(1, completed_index.comparable_sample_size)
            self.assertFalse(completed_index.evidence_table_ready)
            self.assertEqual(
                completed_index,
                ExperimentResultIndex.from_dict(
                    study_repository.load_json(completed_index_ref)
                ),
            )

            self.assertEqual(
                intent_ref,
                persist_attempt_intent(
                    study_repository,
                    run=study.run,
                    preregistration_ref=prereg_ref,
                    intent=intent,
                ),
            )
            self.assertEqual(
                outcome,
                ExperimentOutcome.from_dict(
                    study_repository.load_json(outcome_ref)
                ),
            )
            with self.assertRaises(ExperimentProtocolError):
                persist_attempt_intent(
                    study_repository,
                    run=study.run,
                    preregistration_ref=prereg_ref,
                    intent=replace(intent, attempt_id="different-attempt-id"),
                )
            study_repository.verify()
            case_repository.verify()

    def test_architectural_revision_studies_retain_failures_without_result_claims(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        repository = FilesystemProjectRepository.open(
            _project_root("p062-experiment-study")
        )
        expected = {
            "study-016": (ExperimentAttemptStatus.PIPELINE_REJECTED, 4, 0),
            "study-017": (ExperimentAttemptStatus.TIMED_OUT, 4, 1),
            "study-018": (ExperimentAttemptStatus.TIMED_OUT, 2, 1),
            "study-019": (ExperimentAttemptStatus.PIPELINE_REJECTED, 4, 0),
        }
        for run_id, (status, calls, failures) in expected.items():
            run = repository.load_run(run_id)
            payloads = tuple(
                repository.load_json(ref)
                for ref in repository.list_json(
                    run=run,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )
            )
            attempt = ExperimentAttemptReceipt.from_dict(
                next(
                    item
                    for item in payloads
                    if item.get("schema") == ExperimentAttemptReceipt.SCHEMA
                )
            )
            outcome = ExperimentOutcome.from_dict(
                next(
                    item
                    for item in payloads
                    if item.get("schema") == ExperimentOutcome.SCHEMA
                )
            )
            indexes = tuple(
                ExperimentResultIndex.from_dict(item)
                for item in payloads
                if item.get("schema") == ExperimentResultIndex.SCHEMA
            )
            latest = max(indexes, key=lambda item: item.generated_at)
            self.assertIs(attempt.status, status)
            self.assertEqual(calls, len(attempt.provider_receipts))
            self.assertEqual(
                failures,
                sum(not item.successful for item in attempt.provider_receipts),
            )
            self.assertEqual(attempt.receipt_digest, outcome.attempt_receipt_digest)
            self.assertFalse(outcome.eligible_for_comparison)
            self.assertEqual(1, latest.lifecycle_counts["failed"])
            self.assertEqual(8, latest.lifecycle_counts["planned"])
            self.assertEqual(0, latest.comparable_sample_size)
            self.assertFalse(latest.evidence_table_ready)
            if run_id != "study-019":
                supersession = next(
                    item
                    for item in payloads
                    if item.get("schema") == "P062StudySupersessionReceipt@1"
                )
                self.assertFalse(supersession["building_result_claimed"])
                self.assertFalse(supersession["fallback_used"])

        terminal_run = repository.load_run("study-019")
        terminal_payloads = tuple(
            repository.load_json(ref)
            for ref in repository.list_json(
                run=terminal_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=terminal_run.run_id,
                ),
            )
        )
        boundary = next(
            item
            for item in terminal_payloads
            if item.get("schema")
            == "P062ArchitecturalRevisionBoundaryReceipt@1"
        )
        self.assertEqual(
            "current-provider-profile-exhausted-without-usable-successor",
            boundary["status"],
        )
        self.assertEqual([65064, 65562, 65939], boundary["geometry_input_bytes"])
        self.assertTrue(boundary["exact_revision_tokens_published"])
        self.assertTrue(boundary["required_changed_components_published"])
        self.assertTrue(boundary["spatial_proposal_single_copy"])
        self.assertFalse(boundary["usable_building_claimed"])
        self.assertFalse(boundary["same_profile_retry_authorized"])
        repository.verify()


if __name__ == "__main__":
    unittest.main()
