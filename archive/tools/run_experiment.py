#!/usr/bin/env python3
"""Persist P062 study records through P036 without executing a provider."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.archflow.evaluation.experiment import (  # noqa: E402
    ExperimentAttemptIntent,
    ExperimentAttemptReceipt,
    ExperimentAttemptStatus,
    ExperimentCondition,
    ExperimentConditionKind,
    ExperimentEvidenceBinding,
    ExperimentOutcome,
    ExperimentPreregistration,
    ExperimentProtocolError,
    ExperimentProviderReceiptBinding,
    ExperimentResultIndex,
    ExperimentStudyRecordBinding,
    ExperimentTerminalRequirement,
    compile_experiment_result_index,
)
from archflow.adapters.model_provider import ModelInvocationReceipt  # noqa: E402
from archflow.contracts.canonical import canonical_digest  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.contracts.canonical import canonical_digest
from archive.archflow.realization.sandbox import SandboxRealizationReceipt
from archive.archflow.runtime.family_compiler import (  # noqa: E402
    ComponentFamilyCompilationReceipt,
    ComponentFamilyRealizationReceipt,
)
from archive.archflow.runtime.production_runtime import (  # noqa: E402
    ProductionAuthoringContext,
)
from archive.archflow.validation.architectural import (  # noqa: E402
    ArchitecturalUsabilityReceipt,
)


def _destination(run: RunRef) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)


def _require_run(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> None:
    if repository.load_run(run.run_id) != run:
        raise ExperimentProtocolError("study run does not match P036")


def _payloads(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> list[tuple[ProjectRecordRef, dict[str, object]]]:
    return [
        (item, repository.load_json(item))
        for item in repository.list_json(
            run=run,
            destination=_destination(run),
        )
    ]


def _load_payload(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentProtocolError(f"cannot load experiment payload: {path}") from exc
    if not isinstance(payload, dict):
        raise ExperimentProtocolError("experiment payload must be a JSON object")
    return payload


def _one_study_record(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    schema: str,
    predicate: Callable[[Mapping[str, object]], bool] | None = None,
) -> ProjectRecordRef:
    matches = [
        ref
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == schema
        and (predicate is None or predicate(payload))
    ]
    if len(matches) != 1:
        raise ExperimentProtocolError(
            f"study requires exactly one retained {schema} record"
        )
    return matches[0]


def _source_repositories(
    specifications: Sequence[str],
) -> dict[str, FilesystemProjectRepository]:
    repositories: dict[str, FilesystemProjectRepository] = {}
    for specification in specifications:
        project_id, separator, root = specification.partition("=")
        if not separator or not project_id or not root:
            raise ExperimentProtocolError(
                "source project must use PROJECT_ID=ROOT"
            )
        if project_id in repositories:
            raise ExperimentProtocolError(
                f"duplicate source project root: {project_id}"
            )
        repository = FilesystemProjectRepository.open(Path(root))
        if repository.load_manifest().project_id != project_id:
            raise ExperimentProtocolError(
                f"source project label does not match its P036 manifest: {project_id}"
            )
        repositories[project_id] = repository
    return repositories


def bind_provider_record(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    ref: ProjectRecordRef,
) -> ExperimentProviderReceiptBinding:
    """Validate one retained P053 envelope and expose detached receipt facts."""

    _require_run(repository, run)
    if ref.project_id != run.project_id:
        raise ExperimentProtocolError("provider record crosses project identity")
    payload = repository.load_json(ref)
    schema = payload.get("schema")
    if schema == "ProductionTransitionRecord@1":
        if (
            payload.get("project_id") != run.project_id
            or payload.get("run_id") != run.run_id
            or payload.get("role") != "provider-invocation"
        ):
            raise ExperimentProtocolError("production provider record drifted")
        _require_base_payload(payload.get("base"), run.base)
        envelope = payload.get("content")
        if (
            not isinstance(envelope, Mapping)
            or payload.get("content_sha256") != canonical_digest(envelope)
            or payload.get("semantic_digest") != canonical_digest(envelope)
        ):
            raise ExperimentProtocolError("provider envelope content digest changed")
    elif schema == "ProviderLiveInvocationEvidence@1":
        if (
            payload.get("project_id") != run.project_id
            or payload.get("run_id") != run.run_id
        ):
            raise ExperimentProtocolError("live provider evidence drifted")
        envelope = payload.get("provider_envelope")
    else:
        raise ExperimentProtocolError(
            "provider binding requires a retained P053 envelope record"
        )
    if not isinstance(envelope, Mapping) or (
        envelope.get("schema") != "ProductionInvocationEnvelope@2"
    ):
        raise ExperimentProtocolError("P053 envelope schema or authority drifted")
    authority = envelope.get("authority")
    if not isinstance(authority, Mapping) or (
        authority.get("schema") != "ProductionAuthorityToken@2"
        or authority.get("production_authority") is not True
    ):
        raise ExperimentProtocolError("P053 authority token drifted")
    provider = authority.get("provider")
    if not isinstance(provider, Mapping):
        raise ExperimentProtocolError("P053 provider identity is absent")
    receipt_json = envelope.get("provider_receipt_json")
    if not isinstance(receipt_json, str):
        raise ExperimentProtocolError("P053 provider receipt JSON is absent")
    try:
        receipt_payload = json.loads(receipt_json)
    except json.JSONDecodeError as exc:
        raise ExperimentProtocolError("P053 provider receipt is malformed") from exc
    if envelope.get("provider_receipt_digest") != canonical_digest(receipt_payload):
        raise ExperimentProtocolError("P053 provider receipt digest changed")
    receipt = ModelInvocationReceipt.from_dict(receipt_payload)
    if (
        provider.get("provider_id") != receipt.provider_id
        or provider.get("version") != receipt.provider_version
        or provider.get("fingerprint") != receipt.provider_fingerprint
    ):
        raise ExperimentProtocolError(
            "P053 authority and model receipt provider identities disagree"
        )
    binding_digest = authority.get("binding_digest")
    if not isinstance(binding_digest, str):
        raise ExperimentProtocolError("P053 binding digest is absent")
    return ExperimentProviderReceiptBinding(
        receipt_id=receipt.receipt_id,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        source_schema=schema,
        record_ref=ref.uri,
        record_digest=ref.sha256,
        request_digest=canonical_digest(receipt.request.to_dict()),
        p053_envelope_digest=canonical_digest(envelope),
        authority_binding_digest=binding_digest,
        provider_id=receipt.provider_id,
        model_id=receipt.model_id,
        provider_version=receipt.provider_version,
        provider_fingerprint=receipt.provider_fingerprint,
        status=receipt.status.value,
        duration_ms=receipt.duration_ms,
        input_bytes=receipt.input_bytes,
        output_bytes=receipt.output_bytes,
    )


def bind_evidence_record(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    ref: ProjectRecordRef,
    role: str,
    expected_schema: str,
    expected_status: str,
) -> ExperimentEvidenceBinding:
    """Bind one exact P036 source record without upgrading its status."""

    _require_run(repository, run)
    if ref.project_id != run.project_id:
        raise ExperimentProtocolError("source evidence crosses project identity")
    payload = repository.load_json(ref)
    if (
        payload.get("schema") != expected_schema
        or payload.get("project_id") != run.project_id
        or payload.get("run_id") != run.run_id
        or payload.get("status") != expected_status
    ):
        raise ExperimentProtocolError("source evidence identity or status drifted")
    _require_base_payload(payload.get("base"), run.base)
    return ExperimentEvidenceBinding(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        role=role,
        source_schema=expected_schema,
        record_ref=ref.uri,
        record_digest=ref.sha256,
        source_status=expected_status,
    )


def bind_terminal_record(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    ref: ProjectRecordRef,
    requirement: ExperimentTerminalRequirement,
) -> ExperimentEvidenceBinding:
    """Parse a known authority receipt or an exact project-local extension."""

    _require_run(repository, run)
    if ref.project_id != run.project_id:
        raise ExperimentProtocolError("terminal record crosses project identity")
    payload = repository.load_json(ref)
    if payload.get("schema") != requirement.source_schema:
        raise ExperimentProtocolError("terminal record schema changed")
    schema = requirement.source_schema
    known_role = {
        ArchitecturalUsabilityReceipt.SCHEMA: "architectural-usability",
        ComponentFamilyCompilationReceipt.SCHEMA: (
            "component-family-compilation"
        ),
        ComponentFamilyRealizationReceipt.SCHEMA: (
            "component-family-realization"
        ),
        SandboxRealizationReceipt.SCHEMA: "sandbox-realization",
        "ProductionTransitionRecord@1": "semantic-geometry-production",
    }.get(schema)
    if known_role is not None and requirement.role != known_role:
        raise ExperimentProtocolError(
            "known terminal receipt is assigned to the wrong authority role"
        )
    if schema == ArchitecturalUsabilityReceipt.SCHEMA:
        receipt = ArchitecturalUsabilityReceipt.from_dict(payload)
        _require_receipt_project(receipt, run)
        status = receipt.status.value
    elif schema == ComponentFamilyCompilationReceipt.SCHEMA:
        receipt = ComponentFamilyCompilationReceipt.from_dict(payload)
        _require_receipt_project(receipt, run)
        status = receipt.status.value
    elif schema == ComponentFamilyRealizationReceipt.SCHEMA:
        receipt = ComponentFamilyRealizationReceipt.from_dict(payload)
        _require_receipt_project(receipt, run)
        status = receipt.status.value
    elif schema == SandboxRealizationReceipt.SCHEMA:
        receipt = SandboxRealizationReceipt.from_dict(payload)
        status = receipt.status.value
    elif schema == "ProductionTransitionRecord@1":
        status = _validate_production_terminal_record(payload, run)
    else:
        if (
            payload.get("project_id") != run.project_id
            or payload.get("run_id") != run.run_id
        ):
            raise ExperimentProtocolError(
                "extension terminal record lacks exact project identity"
            )
        _require_base_payload(payload.get("base"), run.base)
        status = payload.get("status")
        if not isinstance(status, str):
            raise ExperimentProtocolError(
                "extension terminal record lacks explicit status"
            )
    if status not in requirement.accepted_statuses:
        raise ExperimentProtocolError(
            "terminal record status is outside preregistration"
        )
    return ExperimentEvidenceBinding(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        role=requirement.role,
        source_schema=schema,
        record_ref=ref.uri,
        record_digest=ref.sha256,
        source_status=status,
    )


def bind_terminal_chain(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    condition: ExperimentCondition,
    record_refs: Mapping[str, ProjectRecordRef],
) -> tuple[ExperimentEvidenceBinding, ...]:
    """Bind and cross-check one complete same-project terminal chain."""

    if not isinstance(condition, ExperimentCondition):
        raise TypeError("condition must be ExperimentCondition")
    requirements = {
        item.role: item for item in condition.terminal_requirements
    }
    if set(record_refs) != set(requirements):
        raise ExperimentProtocolError(
            "terminal chain does not contain the preregistered role set"
        )
    bindings = tuple(
        sorted(
            (
                bind_terminal_record(
                    repository,
                    run=run,
                    ref=record_refs[role],
                    requirement=requirement,
                )
                for role, requirement in requirements.items()
            ),
            key=lambda item: item.record_ref,
        )
    )
    payloads = {
        role: repository.load_json(record_refs[role])
        for role in requirements
    }
    _require_terminal_chain_consistency(payloads)
    return bindings


def _require_terminal_chain_consistency(
    payloads: Mapping[str, Mapping[str, object]],
) -> None:
    parsed: dict[str, object] = {}
    for role, payload in payloads.items():
        schema = payload.get("schema")
        if schema == ArchitecturalUsabilityReceipt.SCHEMA:
            parsed[role] = ArchitecturalUsabilityReceipt.from_dict(payload)
        elif schema == ComponentFamilyCompilationReceipt.SCHEMA:
            parsed[role] = ComponentFamilyCompilationReceipt.from_dict(payload)
        elif schema == ComponentFamilyRealizationReceipt.SCHEMA:
            parsed[role] = ComponentFamilyRealizationReceipt.from_dict(payload)
        elif schema == SandboxRealizationReceipt.SCHEMA:
            parsed[role] = SandboxRealizationReceipt.from_dict(payload)
        elif schema == "ProductionTransitionRecord@1":
            parsed[role] = payload["content"]
    known_roles = {
        "semantic-geometry-production",
        "sandbox-realization",
        "architectural-usability",
        "component-family-compilation",
        "component-family-realization",
    }
    if not set(parsed) <= known_roles:
        raise ExperimentProtocolError("known terminal schema uses an unknown role")
    if not parsed:
        return

    geometry_digests: set[str] = set()
    design_digests: set[str] = set()
    component_digests: set[str] = set()
    scene_digests: set[str] = set()
    sandbox_receipt_digests: set[str] = set()
    production = parsed.get("semantic-geometry-production")
    if isinstance(production, Mapping):
        _collect_digest(
            geometry_digests,
            production.get("geometry_program_digest")
            or production.get("current_program_digest"),
            "production geometry digest",
        )
        _collect_digest(
            design_digests,
            production.get("design_state_digest")
            or production.get("current_design_state_digest"),
            "production design digest",
        )
        _collect_digest(
            component_digests,
            production.get("component_proposal_digest")
            or production.get("current_component_digest"),
            "production component digest",
        )
    sandbox = parsed.get("sandbox-realization")
    if isinstance(sandbox, SandboxRealizationReceipt):
        geometry_digests.add(sandbox.geometry_program_digest)
        if sandbox.scene_digest is None:
            raise ExperimentProtocolError("sandbox terminal has no scene")
        scene_digests.add(sandbox.scene_digest)
        sandbox_receipt_digests.add(sandbox.receipt_digest)
    architectural = parsed.get("architectural-usability")
    if isinstance(architectural, ArchitecturalUsabilityReceipt):
        geometry_digests.add(architectural.geometry_program_digest)
        design_digests.add(architectural.design_state_digest)
        scene_digests.add(architectural.scene_digest)
        sandbox_receipt_digests.add(
            architectural.realization_receipt_digest
        )
    compilation = parsed.get("component-family-compilation")
    if isinstance(compilation, ComponentFamilyCompilationReceipt):
        if not compilation.compiled_instances:
            raise ExperimentProtocolError(
                "completed experiment family chain cannot be an empty no-op"
            )
        geometry_digests.add(compilation.geometry_program_digest)
        design_digests.add(compilation.design_state_digest)
        component_digests.add(compilation.component_tree_digest)
    family_realization = parsed.get("component-family-realization")
    if isinstance(family_realization, ComponentFamilyRealizationReceipt):
        geometry_digests.add(family_realization.geometry_program_digest)
        if family_realization.scene_digest is None:
            raise ExperimentProtocolError("family terminal has no scene")
        scene_digests.add(family_realization.scene_digest)
        sandbox_receipt_digests.add(
            family_realization.sandbox_realization_receipt_digest
        )
        if not isinstance(compilation, ComponentFamilyCompilationReceipt) or (
            family_realization.family_compilation_receipt_digest
            != compilation.receipt_digest
            or family_realization.family_set_digest
            != compilation.family_set_digest
        ):
            raise ExperimentProtocolError(
                "family realization does not bind its exact compilation"
            )
    for values, field in (
        (geometry_digests, "geometry"),
        (design_digests, "design state"),
        (component_digests, "component tree"),
        (scene_digests, "scene"),
        (sandbox_receipt_digests, "sandbox receipt"),
    ):
        if len(values) > 1:
            raise ExperimentProtocolError(
                f"terminal chain {field} digests disagree"
            )


def _collect_digest(values: set[str], value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ExperimentProtocolError(f"{field} is absent")
    values.add(value)


def _require_receipt_project(receipt: object, run: RunRef) -> None:
    if (
        getattr(receipt, "project_id", None) != run.project_id
        or getattr(receipt, "run_id", None) != run.run_id
        or getattr(receipt, "base", None) != run.base
    ):
        raise ExperimentProtocolError("terminal receipt crosses project or base")


def _validate_production_terminal_record(
    payload: Mapping[str, object],
    run: RunRef,
) -> str:
    if set(payload) != {
        "schema",
        "project_id",
        "run_id",
        "base",
        "role",
        "semantic_digest",
        "content_sha256",
        "content",
        "canonical_write_authority",
    } or (
        payload.get("project_id") != run.project_id
        or payload.get("run_id") != run.run_id
        or payload.get("role") != "lifecycle-receipt"
    ):
        raise ExperimentProtocolError("production terminal identity drifted")
    _require_base_payload(payload.get("base"), run.base)
    content = payload.get("content")
    if not isinstance(content, Mapping) or content.get("schema") not in {
        "InitialSemanticGeometryReceipt@1",
        "SemanticGeometryLifecycleReceipt@1",
    } or (
        payload.get("content_sha256") != canonical_digest(content)
        or payload.get("semantic_digest") != canonical_digest(content)
    ):
        raise ExperimentProtocolError("production terminal content drifted")
    return "persisted"


def bind_study_record(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    ref: ProjectRecordRef,
    value: ExperimentAttemptIntent | ExperimentAttemptReceipt | ExperimentOutcome,
) -> ExperimentStudyRecordBinding:
    """Bind an exact typed study record retained by P036."""

    _require_run(repository, run)
    if ref.project_id != run.project_id:
        raise ExperimentProtocolError("study record crosses project identity")
    payload = repository.load_json(ref)
    if payload != value.to_dict():
        raise ExperimentProtocolError("typed study value differs from P036 record")
    return ExperimentStudyRecordBinding(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        source_schema=value.SCHEMA,
        record_ref=ref.uri,
        record_digest=ref.sha256,
        content_digest=canonical_digest(payload),
    )


def _require_base_payload(value: object, base: ProjectVersionRef) -> None:
    if not isinstance(value, Mapping) or dict(value) != {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }:
        raise ExperimentProtocolError("source record base drifted")


def _require_bound_record(
    repositories: Mapping[str, FilesystemProjectRepository],
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    record_ref: str,
    record_digest: str,
    source_schema: str,
) -> tuple[
    FilesystemProjectRepository,
    RunRef,
    ProjectRecordRef,
]:
    repository = repositories.get(project_id)
    if not isinstance(repository, FilesystemProjectRepository):
        raise ExperimentProtocolError(
            f"source repository is unavailable for {project_id}"
        )
    run = repository.load_run(run_id)
    if run.base != base:
        raise ExperimentProtocolError("source repository base changed")
    records = repository.list_json(
        run=run,
        destination=_destination(run),
    )
    matches = [item for item in records if item.uri == record_ref]
    if (
        len(matches) != 1
        or matches[0].sha256 != record_digest
        or repository.load_json(matches[0]).get("schema") != source_schema
    ):
        raise ExperimentProtocolError("detached source binding is not retained")
    return repository, run, matches[0]


def bind_preregistered_cases(
    preregistration: ExperimentPreregistration,
    *,
    study_run: RunRef,
    source_repositories: Mapping[str, FilesystemProjectRepository],
) -> None:
    """Prove every frozen case input already exists in its exact P036 project."""

    for case in preregistration.cases:
        if case.project_id == study_run.project_id:
            raise ExperimentProtocolError(
                "experiment case must be separate from the study project"
            )
        repository = source_repositories.get(case.project_id)
        if not isinstance(repository, FilesystemProjectRepository):
            raise ExperimentProtocolError(
                f"source repository is unavailable for {case.project_id}"
            )
        if repository.load_manifest().project_id != case.project_id:
            raise ExperimentProtocolError("case repository identity changed")
        run = repository.load_run(case.run_id)
        if run.base != case.base:
            raise ExperimentProtocolError("case repository base changed")
        available = tuple(
            ref
            for destination in (
                PersistenceDestination(PersistenceArea.INPUT),
                _destination(run),
            )
            for ref in repository.list_json(run=run, destination=destination)
        )
        by_uri: dict[str, list[ProjectRecordRef]] = {}
        for ref in available:
            by_uri.setdefault(ref.uri, []).append(ref)
        for logical_ref in case.input_record_refs:
            matches = by_uri.get(logical_ref, [])
            if len(matches) != 1:
                raise ExperimentProtocolError(
                    "preregistered case input is not retained exactly once"
                )
        raw_matches = by_uri.get(case.raw_request_ref, [])
        if len(raw_matches) != 1 or raw_matches[0].sha256 != case.raw_request_digest:
            raise ExperimentProtocolError("preregistered raw request digest changed")
        raw_payload = repository.load_json(raw_matches[0])
        if set(raw_payload) != {"schema", "prompt"} or (
            raw_payload.get("schema") != "RawProjectRequest@1"
            or not isinstance(raw_payload.get("prompt"), str)
            or not raw_payload["prompt"].strip()
        ):
            raise ExperimentProtocolError("case raw request schema changed")


PROGRAM_RELATIONSHIP_CONTEXT_ID = "program-relationship-context"


def persist_generation_context_ablation(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    source_context_ref: ProjectRecordRef,
    condition_id: str,
    withheld_context_ids: tuple[str, ...],
) -> tuple[ProjectRecordRef, ProjectRecordRef]:
    """Persist one exact context-only ablation without changing P053/P056."""

    _require_run(repository, run)
    if source_context_ref.project_id != run.project_id:
        raise ExperimentProtocolError("source context crosses project identity")
    if withheld_context_ids != (PROGRAM_RELATIONSHIP_CONTEXT_ID,):
        raise ExperimentProtocolError(
            "generation ablation must name the supported context exactly"
        )
    source_payload = repository.load_json(source_context_ref)
    source = ProductionAuthoringContext.from_dict(source_payload)
    source.require_run(run)
    removed = tuple(
        sorted(item.relationship_id for item in source.program.relationships)
    )
    if not removed:
        raise ExperimentProtocolError(
            "program relationship context is already empty"
        )
    ablated_program = replace(source.program, relationships=())
    ablated_policy = replace(
        source.build_policy,
        program_digest=ablated_program.program_digest,
    )
    ablated = replace(
        source,
        program=ablated_program,
        build_policy=ablated_policy,
    )
    ablated.require_run(run)
    destination = _destination(run)
    context_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"experiment-context-{condition_id}",
        payload=ablated.to_dict(),
    )
    receipt = {
        "schema": "ExperimentGenerationContextAblationReceipt@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "condition_id": condition_id,
        "source_context_ref": source_context_ref.uri,
        "source_context_record_digest": source_context_ref.sha256,
        "source_context_digest": source.context_digest,
        "ablated_context_ref": context_ref.uri,
        "ablated_context_record_digest": context_ref.sha256,
        "ablated_context_digest": ablated.context_digest,
        "withheld_context_ids": list(withheld_context_ids),
        "removed_relationship_ids": list(removed),
        "unchanged_production_route": "P053/P056",
        "provider_profile_unchanged": True,
        "generation_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"experiment-context-ablation-{condition_id}",
        payload=receipt,
    )
    return context_ref, receipt_ref


def select_assignment_context(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    condition: ExperimentCondition,
    full_context_ref: ProjectRecordRef,
    ablation_receipt_ref: ProjectRecordRef | None = None,
) -> tuple[ProjectRecordRef, ProductionAuthoringContext]:
    """Select the exact retained P056 context for one provider assignment."""

    _require_run(repository, run)
    if not isinstance(condition, ExperimentCondition):
        raise TypeError("condition must be ExperimentCondition")
    if full_context_ref.project_id != run.project_id:
        raise ExperimentProtocolError("full context crosses project identity")
    full_payload = repository.load_json(full_context_ref)
    full = ProductionAuthoringContext.from_dict(full_payload)
    full.require_run(run)
    if not full.program.relationships:
        raise ExperimentProtocolError(
            "full condition requires retained program relationship context"
        )

    if condition.kind is ExperimentConditionKind.FULL:
        if ablation_receipt_ref is not None:
            raise ExperimentProtocolError(
                "full condition cannot select an ablation receipt"
            )
        return full_context_ref, full
    if condition.kind is ExperimentConditionKind.VALIDATION_ABLATION:
        raise ExperimentProtocolError(
            "validation ablation is detached and does not invoke a provider"
        )
    if condition.withheld_context_ids != (PROGRAM_RELATIONSHIP_CONTEXT_ID,):
        raise ExperimentProtocolError(
            "generation assignment names an unsupported context ablation"
        )
    if ablation_receipt_ref is None:
        raise ExperimentProtocolError(
            "generation ablation requires its exact retained receipt"
        )
    if ablation_receipt_ref.project_id != run.project_id:
        raise ExperimentProtocolError("ablation receipt crosses project identity")
    receipt = repository.load_json(ablation_receipt_ref)
    if set(receipt) != {
        "schema",
        "project_id",
        "run_id",
        "base",
        "condition_id",
        "source_context_ref",
        "source_context_record_digest",
        "source_context_digest",
        "ablated_context_ref",
        "ablated_context_record_digest",
        "ablated_context_digest",
        "withheld_context_ids",
        "removed_relationship_ids",
        "unchanged_production_route",
        "provider_profile_unchanged",
        "generation_authority",
        "persistence_authority",
        "canonical_write_authority",
    } or (
        receipt.get("schema")
        != "ExperimentGenerationContextAblationReceipt@1"
        or receipt.get("project_id") != run.project_id
        or receipt.get("run_id") != run.run_id
        or receipt.get("condition_id") != condition.condition_id
        or receipt.get("source_context_ref") != full_context_ref.uri
        or receipt.get("source_context_record_digest") != full_context_ref.sha256
        or receipt.get("source_context_digest") != full.context_digest
        or receipt.get("withheld_context_ids")
        != list(condition.withheld_context_ids)
        or receipt.get("removed_relationship_ids")
        != sorted(item.relationship_id for item in full.program.relationships)
        or receipt.get("unchanged_production_route") != "P053/P056"
        or receipt.get("provider_profile_unchanged") is not True
    ):
        raise ExperimentProtocolError("generation ablation receipt drifted")
    _require_base_payload(receipt.get("base"), run.base)

    ablated_uri = receipt.get("ablated_context_ref")
    ablated_digest = receipt.get("ablated_context_record_digest")
    matches = [
        ref
        for ref in repository.list_json(
            run=run,
            destination=_destination(run),
        )
        if ref.uri == ablated_uri
    ]
    if (
        len(matches) != 1
        or matches[0].sha256 != ablated_digest
        or matches[0] == full_context_ref
    ):
        raise ExperimentProtocolError(
            "ablated assignment context is not retained exactly once"
        )
    ablated_ref = matches[0]
    ablated = ProductionAuthoringContext.from_dict(
        repository.load_json(ablated_ref)
    )
    ablated.require_run(run)
    expected_program = replace(full.program, relationships=())
    expected = replace(
        full,
        program=expected_program,
        build_policy=replace(
            full.build_policy,
            program_digest=expected_program.program_digest,
        ),
    )
    if (
        ablated != expected
        or receipt.get("ablated_context_digest") != ablated.context_digest
    ):
        raise ExperimentProtocolError(
            "generation assignment changed more than relationship context"
        )
    return ablated_ref, ablated


def persist_preregistration(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    preregistration: ExperimentPreregistration,
    source_repositories: Mapping[str, FilesystemProjectRepository],
) -> ProjectRecordRef:
    """Freeze one study protocol before any attempt intent is durable."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    _require_run(repository, run)
    bind_preregistered_cases(
        preregistration,
        study_run=run,
        source_repositories=source_repositories,
    )
    existing = [
        (ref, ExperimentPreregistration.from_dict(payload))
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentPreregistration.SCHEMA
    ]
    if existing:
        ref, retained = existing[0]
        if len(existing) != 1 or retained != preregistration:
            raise ExperimentProtocolError(
                "study preregistration is immutable after first persistence"
            )
        return ref
    return repository.put_json(
        run=run,
        destination=_destination(run),
        record_kind="experiment-preregistration",
        payload=preregistration.to_dict(),
    )


def persist_attempt_intent(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    preregistration_ref: ProjectRecordRef,
    intent: ExperimentAttemptIntent,
) -> ProjectRecordRef:
    """Make one assignment attempt durable before any provider call."""

    preregistration = ExperimentPreregistration.from_dict(
        repository.load_json(preregistration_ref)
    )
    _require_run(repository, run)
    if (
        preregistration_ref.project_id != run.project_id
        or intent.preregistration_digest
        != preregistration.preregistration_digest
    ):
        raise ExperimentProtocolError("attempt intent names another study")
    if intent.retry_of_attempt_receipt_digest is not None:
        predecessor_exists = any(
            payload.get("schema") == ExperimentAttemptReceipt.SCHEMA
            and ExperimentAttemptReceipt.from_dict(payload).receipt_digest
            == intent.retry_of_attempt_receipt_digest
            for _, payload in _payloads(repository, run)
        )
        if not predecessor_exists:
            raise ExperimentProtocolError(
                "retry predecessor is not retained in the study project"
            )
    matches = [
        (ref, ExperimentAttemptIntent.from_dict(payload))
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentAttemptIntent.SCHEMA
        and payload.get("assignment_id") == intent.assignment_id
        and payload.get("attempt_index") == intent.attempt_index
    ]
    if matches:
        ref, retained = matches[0]
        if len(matches) != 1 or retained != intent:
            raise ExperimentProtocolError(
                "assignment attempt index already has another intent"
            )
        return ref
    return repository.put_json(
        run=run,
        destination=_destination(run),
        record_kind="experiment-attempt-intent",
        payload=intent.to_dict(),
    )


def persist_attempt_receipt(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    intent_ref: ProjectRecordRef,
    receipt: ExperimentAttemptReceipt,
    source_repositories: Mapping[str, FilesystemProjectRepository],
) -> ProjectRecordRef:
    """Close one retained attempt exactly once, whether successful or failed."""

    intent = ExperimentAttemptIntent.from_dict(repository.load_json(intent_ref))
    _require_run(repository, run)
    if (
        intent_ref.project_id != run.project_id
        or receipt.attempt_intent_digest != intent.intent_digest
        or receipt.attempt_id != intent.attempt_id
    ):
        raise ExperimentProtocolError("attempt receipt does not close the intent")
    preregistrations = [
        ExperimentPreregistration.from_dict(payload)
        for _, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentPreregistration.SCHEMA
    ]
    if len(preregistrations) != 1:
        raise ExperimentProtocolError(
            "attempt persistence requires one retained preregistration"
        )
    condition = preregistrations[0].condition(receipt.condition_id)
    for item in receipt.provider_receipts:
        source_repository, source_run, source_ref = _require_bound_record(
            source_repositories,
            project_id=item.project_id,
            run_id=item.run_id,
            base=item.base,
            record_ref=item.record_ref,
            record_digest=item.record_digest,
            source_schema=item.source_schema,
        )
        if bind_provider_record(
            source_repository,
            run=source_run,
            ref=source_ref,
        ) != item:
            raise ExperimentProtocolError(
                "detached provider binding disagrees with its P053 source"
            )
    requirements = {item.role: item for item in condition.terminal_requirements}
    terminal_refs: dict[str, ProjectRecordRef] = {}
    for item in receipt.terminal_evidence:
        source_repository, source_run, source_ref = _require_bound_record(
            source_repositories,
            project_id=item.project_id,
            run_id=item.run_id,
            base=item.base,
            record_ref=item.record_ref,
            record_digest=item.record_digest,
            source_schema=item.source_schema,
        )
        requirement = requirements.get(item.role)
        if requirement is None or bind_terminal_record(
            source_repository,
            run=source_run,
            ref=source_ref,
            requirement=requirement,
        ) != item:
            raise ExperimentProtocolError(
                "detached terminal binding disagrees with its P036 source"
            )
        terminal_refs[item.role] = source_ref
    if receipt.status is ExperimentAttemptStatus.COMPLETED:
        case_repository = source_repositories.get(receipt.project_id)
        if not isinstance(case_repository, FilesystemProjectRepository):
            raise ExperimentProtocolError("completed case repository is unavailable")
        case_run = case_repository.load_run(receipt.run_id)
        if bind_terminal_chain(
            case_repository,
            run=case_run,
            condition=condition,
            record_refs=terminal_refs,
        ) != receipt.terminal_evidence:
            raise ExperimentProtocolError(
                "completed terminal chain changed after cross-validation"
            )
    if receipt.source_attempt_receipt_digest is not None:
        source_exists = any(
            payload.get("schema") == ExperimentAttemptReceipt.SCHEMA
            and ExperimentAttemptReceipt.from_dict(payload).receipt_digest
            == receipt.source_attempt_receipt_digest
            for _, payload in _payloads(repository, run)
        )
        if not source_exists:
            raise ExperimentProtocolError(
                "validation source attempt is not retained in the study project"
            )
    matches = [
        (ref, ExperimentAttemptReceipt.from_dict(payload))
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentAttemptReceipt.SCHEMA
        and payload.get("attempt_intent_digest") == intent.intent_digest
    ]
    if matches:
        ref, retained = matches[0]
        if len(matches) != 1 or retained != receipt:
            raise ExperimentProtocolError("attempt intent already has another receipt")
        return ref
    return repository.put_json(
        run=run,
        destination=_destination(run),
        record_kind="experiment-attempt-receipt",
        payload=receipt.to_dict(),
    )


def persist_outcome(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    attempt_ref: ProjectRecordRef,
    outcome: ExperimentOutcome,
    source_repositories: Mapping[str, FilesystemProjectRepository],
) -> ProjectRecordRef:
    """Persist measurements only after the exact attempt receipt is durable."""

    attempt = ExperimentAttemptReceipt.from_dict(
        repository.load_json(attempt_ref)
    )
    _require_run(repository, run)
    if (
        attempt_ref.project_id != run.project_id
        or outcome.attempt_receipt_digest != attempt.receipt_digest
        or outcome.assignment_id != attempt.assignment_id
    ):
        raise ExperimentProtocolError("outcome does not bind the retained attempt")
    for observation in outcome.observations:
        for item in observation.evidence:
            source_repository, source_run, source_ref = _require_bound_record(
                source_repositories,
                project_id=item.project_id,
                run_id=item.run_id,
                base=item.base,
                record_ref=item.record_ref,
                record_digest=item.record_digest,
                source_schema=item.source_schema,
            )
            if bind_evidence_record(
                source_repository,
                run=source_run,
                ref=source_ref,
                role=item.role,
                expected_schema=item.source_schema,
                expected_status=item.source_status,
            ) != item:
                raise ExperimentProtocolError(
                    "detached metric binding disagrees with its P036 source"
                )
    matches = [
        (ref, ExperimentOutcome.from_dict(payload))
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentOutcome.SCHEMA
        and payload.get("attempt_receipt_digest") == attempt.receipt_digest
    ]
    if matches:
        ref, retained = matches[0]
        if len(matches) != 1 or retained != outcome:
            raise ExperimentProtocolError("attempt already has another outcome")
        return ref
    return repository.put_json(
        run=run,
        destination=_destination(run),
        record_kind="experiment-outcome",
        payload=outcome.to_dict(),
    )


def rebuild_result_index(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    generated_at: str,
) -> ExperimentResultIndex:
    """Reconstruct one index from the current exact P036 study record set."""

    _require_run(repository, run)
    records = _payloads(repository, run)
    preregistrations = [
        ExperimentPreregistration.from_dict(payload)
        for _, payload in records
        if payload.get("schema") == ExperimentPreregistration.SCHEMA
    ]
    if len(preregistrations) != 1:
        raise ExperimentProtocolError(
            "result indexing requires exactly one retained preregistration"
        )
    preregistration = preregistrations[0]
    intents = []
    receipts = []
    outcomes = []
    for ref, payload in records:
        schema = payload.get("schema")
        if schema == ExperimentAttemptIntent.SCHEMA:
            value = ExperimentAttemptIntent.from_dict(payload)
            intents.append(
                (bind_study_record(repository, run=run, ref=ref, value=value), value)
            )
        elif schema == ExperimentAttemptReceipt.SCHEMA:
            value = ExperimentAttemptReceipt.from_dict(payload)
            receipts.append(
                (bind_study_record(repository, run=run, ref=ref, value=value), value)
            )
        elif schema == ExperimentOutcome.SCHEMA:
            value = ExperimentOutcome.from_dict(payload)
            outcomes.append(
                (bind_study_record(repository, run=run, ref=ref, value=value), value)
            )
    return compile_experiment_result_index(
        preregistration,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        generated_at=generated_at,
        intents=tuple(sorted(intents, key=lambda item: item[0].record_ref)),
        receipts=tuple(sorted(receipts, key=lambda item: item[0].record_ref)),
        outcomes=tuple(sorted(outcomes, key=lambda item: item[0].record_ref)),
    )


def persist_result_index(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    index: ExperimentResultIndex,
) -> ProjectRecordRef:
    """Persist only the index exactly rebuildable from retained study records."""

    rebuilt = rebuild_result_index(
        repository,
        run=run,
        generated_at=index.generated_at,
    )
    if rebuilt != index:
        raise ExperimentProtocolError(
            "result index is not derivable from retained study records"
        )
    matches = [
        (ref, ExperimentResultIndex.from_dict(payload))
        for ref, payload in _payloads(repository, run)
        if payload.get("schema") == ExperimentResultIndex.SCHEMA
        and payload.get("generated_at") == index.generated_at
    ]
    if matches:
        ref, retained = matches[0]
        if len(matches) != 1 or retained != index:
            raise ExperimentProtocolError(
                "result index timestamp already names different content"
            )
        return ref
    return repository.put_json(
        run=run,
        destination=_destination(run),
        record_kind="experiment-result-index",
        payload=index.to_dict(),
    )


def _add_payload_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    *,
    source_projects: bool = False,
) -> None:
    command = subparsers.add_parser(name)
    command.add_argument("--study-root", type=Path, required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--payload", type=Path, required=True)
    if source_projects:
        command.add_argument(
            "--source-project",
            action="append",
            default=[],
            metavar="PROJECT_ID=ROOT",
            help="Exact P036 case repository; repeat for each referenced project.",
        )


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persist or verify P062 study evidence through P036."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_payload_command(subparsers, "preregister", source_projects=True)
    _add_payload_command(subparsers, "intent")
    _add_payload_command(subparsers, "receipt", source_projects=True)
    _add_payload_command(subparsers, "outcome", source_projects=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--study-root", type=Path, required=True)
    index = subparsers.add_parser("index")
    index.add_argument("--study-root", type=Path, required=True)
    index.add_argument("--run-id", required=True)
    index.add_argument("--generated-at", required=True)
    args = parser.parse_args(argv)

    repository = FilesystemProjectRepository.open(args.study_root)
    if args.command == "verify":
        repository.verify()
        print("P062 study project integrity PASS")
        return 0
    if args.command == "index":
        run = repository.load_run(args.run_id)
        result_index = rebuild_result_index(
            repository,
            run=run,
            generated_at=args.generated_at,
        )
        ref = persist_result_index(
            repository,
            run=run,
            index=result_index,
        )
        repository.verify()
        print(ref.uri)
        return 0
    run = repository.load_run(args.run_id)
    payload = _load_payload(args.payload)
    if args.command == "preregister":
        ref = persist_preregistration(
            repository,
            run=run,
            preregistration=ExperimentPreregistration.from_dict(payload),
            source_repositories=_source_repositories(args.source_project),
        )
    elif args.command == "intent":
        preregistration_ref = _one_study_record(
            repository,
            run,
            schema=ExperimentPreregistration.SCHEMA,
        )
        ref = persist_attempt_intent(
            repository,
            run=run,
            preregistration_ref=preregistration_ref,
            intent=ExperimentAttemptIntent.from_dict(payload),
        )
    elif args.command == "receipt":
        receipt = ExperimentAttemptReceipt.from_dict(payload)
        intent_ref = _one_study_record(
            repository,
            run,
            schema=ExperimentAttemptIntent.SCHEMA,
            predicate=lambda item: ExperimentAttemptIntent.from_dict(item).intent_digest
            == receipt.attempt_intent_digest,
        )
        ref = persist_attempt_receipt(
            repository,
            run=run,
            intent_ref=intent_ref,
            receipt=receipt,
            source_repositories=_source_repositories(args.source_project),
        )
    else:
        outcome = ExperimentOutcome.from_dict(payload)
        attempt_ref = _one_study_record(
            repository,
            run,
            schema=ExperimentAttemptReceipt.SCHEMA,
            predicate=lambda item: ExperimentAttemptReceipt.from_dict(
                item
            ).receipt_digest
            == outcome.attempt_receipt_digest,
        )
        ref = persist_outcome(
            repository,
            run=run,
            attempt_ref=attempt_ref,
            outcome=outcome,
            source_repositories=_source_repositories(args.source_project),
        )
    repository.verify()
    print(ref.uri)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
