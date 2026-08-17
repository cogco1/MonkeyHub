from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
    proposal_authoring_output,
)
from archflow.capabilities.spatial import compile_spatial_options
from archflow.capabilities.semantic_spatial_authoring import (
    semantic_spatial_authoring_output,
)
from archflow.production import (
    InvocationEvidenceCollector,
    ProviderIdentity,
    ProviderUnavailable,
    activate_model_provider,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    RuntimePaths,
    bootstrap_external_project,
)
from archflow.project.digests import canonical_json_sha256
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.runtime import main, run_external_production
from archflow.runtime.production_compiler import (
    ProductionRootCompiler,
    SchematicSelectionStatus,
    schematic_selection_output,
    select_schematic_option,
)
from archflow.runtime.production_runtime import (
    CompiledProductionStep,
    ProductionAuthoringContext,
    ProductionContextError,
    ProductionRuntimeError,
    run_or_resume_production_step,
)
from archflow.state import ComponentMaturity, DesignComponent
from archflow.state.commitments import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
)
from archflow.state.design_maturity import (
    DesignPhase,
    DesignMaturityState,
    PhaseGateRequest,
    evaluate_forward_phase_gate,
)
from archflow.state.developed_design import DevelopedDesignState
from tests.test_geometry_compiler import COMMITMENT, EVIDENCE
from tests.test_sandbox_realization import compiled_room
from tests.test_semantic_spatial_authoring import _ScriptedProvider
from tests.test_spatial_proposals import _inputs, _proposal as _spatial
from tests.test_production_transition import _compiled_transition


IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-production-provider",
    model_id="scripted-production-model",
    provider_version="1",
    provider_fingerprint="f" * 64,
)


def _rebase_program(program, run):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()
    return replace(
        program,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        assumptions=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.assumptions
        ),
        nodes=tuple(
            replace(item, base_state_sha256=digest) for item in program.nodes
        ),
        ranges=tuple(
            replace(item, base_state_sha256=digest) for item in program.ranges
        ),
        relationships=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.relationships
        ),
        scenarios=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.scenarios
        ),
    )


def _rebase_policy(policy, run, program, site):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()

    def provenance(value):  # type: ignore[no-untyped-def]
        return replace(value, base_state_sha256=digest)

    def provenanced(values):  # type: ignore[no-untyped-def]
        return tuple(
            replace(item, provenance=provenance(item.provenance))
            for item in values
        )

    return replace(
        policy,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        program_digest=program.program_digest,
        site_context_digest=site.context_digest,
        policy_provenance=provenance(policy.policy_provenance),
        assumptions=tuple(
            replace(item, base_state_sha256=digest)
            for item in policy.assumptions
        ),
        availability=provenanced(policy.availability),
        demands=provenanced(policy.demands),
        protected_rules=provenanced(policy.protected_rules),
        budget_limits=provenanced(policy.budget_limits),
        staging_assumptions=provenanced(policy.staging_assumptions),
        constraints=provenanced(policy.constraints),
    )


def _rebase_context(
    context: ProductionAuthoringContext,
    run,
):  # type: ignore[no-untyped-def]
    branch = replace(context.state.branch, run=run)
    state = replace(context.state, branch=branch)
    deliverables = tuple(
        replace(
            item,
            branch=branch,
            base_state_digest=state.state_digest,
        )
        for item in context.maturity.deliverables
    )
    maturity = replace(
        context.maturity,
        branch=branch,
        operational_state_digest=state.state_digest,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id=context.phase_gate.request_id,
            branch=branch,
            base_state_digest=state.state_digest,
            from_phase=context.phase_gate.from_phase,
            to_phase=context.phase_gate.to_phase,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    program = _rebase_program(context.program, run)
    site = replace(
        context.site_context,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
    )
    policy = _rebase_policy(context.build_policy, run, program, site)
    return replace(
        context,
        state=state,
        maturity=maturity,
        phase_gate=gate,
        program=program,
        site_context=site,
        build_policy=policy,
    )


class _MemoryRepository:
    def __init__(self) -> None:
        self.records: dict[ProjectRecordRef, dict[str, object]] = {}

    def put_json(self, *, run, destination, record_kind, payload):  # type: ignore[no-untyped-def]
        del destination
        material = dict(payload)
        digest = canonical_json_sha256(material)
        ref = ProjectRecordRef(
            project_id=run.project_id,
            relative_path=f"runs/{run.run_id}/records/{record_kind}-{digest}.json",
            sha256=digest,
            media_type="application/json",
        )
        self.records[ref] = material
        return ref

    def load_json(self, ref):  # type: ignore[no-untyped-def]
        return dict(self.records[ref])


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _with_components(proposal):  # type: ignore[no-untyped-def]
    evidence = proposal.evidence_refs
    return replace(
        proposal,
        components=(
            DesignComponent(
                component_id="building",
                parent_component_id=None,
                semantic_kind="building",
                intent="Own the current schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=tuple(item.volume_id for item in proposal.volumes),
                unresolved_child_roles=("structure", "envelope"),
                source_refs=evidence,
            ),
        ),
    )


class _ProductionProvider:
    def __init__(self, options) -> None:  # type: ignore[no-untyped-def]
        self.options = iter(options)
        self.calls = []

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        schema = request.payload["schema"]
        if schema == "SemanticSpatialAuthoringPrompt@1":
            output = semantic_spatial_authoring_output(request, next(self.options))
        elif schema == "SchematicOptionSelectionPrompt@1":
            output = schematic_selection_output(
                request,
                selected_option_id="two-level-option",
                rationale="The two-level option better resolves the supplied relationships.",
            )
        elif schema == "GeometryProposalAuthoringRequest@1":
            state = DevelopedDesignState.from_dict(
                request.payload["developed_design_state"]
            )
            spatial_record = request.payload["spatial_option_record"]["ref"]
            spatial_ref = ProjectRecordRef(
                project_id=spatial_record["project_id"],
                relative_path=spatial_record["relative_path"],
                sha256=spatial_record["sha256"],
                media_type=spatial_record["media_type"],
            )
            _, original_program, _ = compiled_room()
            proposal = replace(
                original_program.proposal,
                project_id=state.project_id,
                run_id=state.run_id,
                base=state.base,
                design_state_digest=state.state_digest,
            )
            binding = replace(
                proposal.semantic_bindings[0],
                commitment_refs=(COMMITMENT,),
                evidence_refs=tuple(sorted((EVIDENCE, spatial_ref.uri))),
            )
            output = proposal_authoring_output(
                replace(proposal, semantic_bindings=(binding,), assemblies=())
            )
        else:
            raise AssertionError(f"unexpected request schema: {schema}")
        encoded = _canonical(output)
        return ModelInvocationReceipt(
            receipt_id=f"scripted-{len(self.calls):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=IDENTITY.provider_id,
            model_id=IDENTITY.model_id,
            provider_version=IDENTITY.provider_version,
            provider_fingerprint=IDENTITY.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            output_json=encoded,
        )


def _context_and_options():
    brief, program, site, policy, state, maturity, _ = _inputs()
    commitment = Commitment(
        commitment_id=COMMITMENT.removeprefix("commitment:"),
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority.user",
        source_event_ref=brief.raw_request_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="maintain-egress",
            provider_id="validator.egress",
        ),
        evidence_refs=(brief.raw_request_ref,),
        authorized_by="authority.user",
    )
    state = replace(state, commitments=(commitment,))
    deliverables = tuple(
        replace(item, base_state_digest=state.state_digest)
        for item in maturity.deliverables
    )
    maturity = DesignMaturityState.from_operational_state(
        state,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id="production-enter-schematic",
            branch=state.branch,
            base_state_digest=state.state_digest,
            from_phase=maturity.phase,
            to_phase=DesignPhase.SCHEMATIC_DESIGN,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    context = ProductionAuthoringContext(
        state=state,
        maturity=maturity,
        phase_gate=gate,
        program=program,
        site_context=site,
        build_policy=policy,
        architect_id="primary-architect",
        required_commitment_refs=(COMMITMENT,),
    )
    options = (
        _with_components(
            _spatial(
                option_id="single-level-option",
                program=program,
                brief=brief,
                two_levels=False,
            )
        ),
        _with_components(
            _spatial(
                option_id="two-level-option",
                program=program,
                brief=brief,
                two_levels=True,
            )
        ),
    )
    return context, options


class ProductionAuthoringContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context, _ = _context_and_options()

    def test_round_trip_retains_one_existing_state_tree(self) -> None:
        loaded = ProductionAuthoringContext.from_dict(self.context.to_dict())

        self.assertEqual(self.context, loaded)
        self.assertEqual(self.context.state.branch.run, loaded.run)
        self.assertEqual(self.context.context_digest, loaded.context_digest)
        self.assertFalse(loaded.to_dict()["selection_authority"])

    def test_exact_p036_run_base_is_required(self) -> None:
        wrong = RunRef(
            project_id=self.context.run.project_id,
            run_id=self.context.run.run_id,
            base=ProjectVersionRef(
                project_id=self.context.run.project_id,
                version=self.context.run.base.version,
                state_sha256="0" * 64,
            ),
        )

        with self.assertRaisesRegex(ProductionContextError, "exact P036"):
            self.context.require_run(wrong)

    def test_serialized_context_cannot_acquire_selection_authority(self) -> None:
        payload = self.context.to_dict()
        payload["selection_authority"] = True

        with self.assertRaisesRegex(ProductionContextError, "acquired authority"):
            ProductionAuthoringContext.from_dict(payload)


class ProductionSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        brief, program, site, policy, state, maturity, gate = _inputs()
        self.option_set = compile_spatial_options(
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            proposals=(
                _spatial(
                    option_id="courtyard-option",
                    program=program,
                    brief=brief,
                    two_levels=False,
                ),
                _spatial(
                    option_id="hall-option",
                    program=program,
                    brief=brief,
                    two_levels=True,
                ),
            ),
        ).option_set
        self.raw_request = ProjectRecordRef(
            project_id=self.option_set.project_id,
            relative_path="input/raw-request-" + "1" * 64 + ".json",
            sha256="1" * 64,
            media_type="application/json",
        )

    async def test_architect_selects_only_from_validated_option_set(self) -> None:
        provider = _ScriptedProvider(
            lambda request: schematic_selection_output(
                request,
                selected_option_id="hall-option",
                rationale="The hall option better satisfies the current brief.",
            )
        )

        result = await select_schematic_option(
            provider,
            request_id="choose-schematic-option",
            option_set=self.option_set,
            raw_request=self.raw_request,
        )

        self.assertIs(result.receipt.status, SchematicSelectionStatus.SELECTED)
        assert result.option is not None
        self.assertEqual("hall-option", result.option.option_id)
        self.assertFalse(result.receipt.to_dict()["selection_authority"])

    async def test_unknown_or_stale_selection_is_rejected(self) -> None:
        unknown = _ScriptedProvider(
            lambda request: schematic_selection_output(
                request,
                selected_option_id="invented-option",
                rationale="Invent an option.",
            )
        )
        unknown_result = await select_schematic_option(
            unknown,
            request_id="unknown-selection",
            option_set=self.option_set,
            raw_request=self.raw_request,
        )
        self.assertIs(unknown_result.receipt.status, SchematicSelectionStatus.REJECTED)
        self.assertIsNone(unknown_result.option)

        stale = _ScriptedProvider(
            lambda request: {
                **schematic_selection_output(
                    request,
                    selected_option_id="hall-option",
                    rationale="Use a stale choice.",
                ),
                "exact_option_set_digest": hashlib.sha256(b"stale").hexdigest(),
            }
        )
        stale_result = await select_schematic_option(
            stale,
            request_id="stale-selection",
            option_set=self.option_set,
            raw_request=self.raw_request,
        )
        self.assertEqual(
            "schematic_selection.stale_option_set",
            stale_result.receipt.error_code,
        )


class ProductionRootCompilerTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_state_reaches_selected_initial_semantic_geometry(self):
        context, options = _context_and_options()
        repository = _MemoryRepository()
        context_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="production-context",
            payload=context.to_dict(),
        )
        raw_ref = ProjectRecordRef(
            project_id=context.run.project_id,
            relative_path="input/raw-request-" + "a" * 64 + ".json",
            sha256="a" * 64,
            media_type="application/json",
        )
        repository.records[raw_ref] = {
            "schema": "RawProjectRequest@1",
            "prompt": "Design from this current project state.",
        }
        collector = InvocationEvidenceCollector()
        provider = _ProductionProvider(options)
        authorized = activate_model_provider(
            provider,
            identity=ProviderIdentity(
                provider_id=IDENTITY.provider_id,
                version=IDENTITY.provider_version,
                fingerprint=IDENTITY.provider_fingerprint,
            ),
            responsibility_id="model.production-root",
            contract_owner_id="archflow.production-root",
            verification_evidence_refs=(context_ref.uri,),
            envelope_observer=collector.observe,
        )
        compiler = ProductionRootCompiler(
            repository=repository,
            context_ref=context_ref,
            context=context,
            provider=authorized,
            evidence_collector=collector,
            geometry_provider_identity=IDENTITY,
        )

        compiled = await compiler.compile(
            run=context.run,
            raw_request=raw_ref,
            prompt="Design from this current project state.",
        )

        self.assertEqual("two-level-option", compiled.current_design_state.selected_schematic.option.option_id)
        self.assertEqual(
            compiled.current_design_state.state_digest,
            compiled.lifecycle.receipt.design_state_digest,
        )
        self.assertEqual(4, len(compiled.invocation_envelopes))
        self.assertEqual(4, len(provider.calls))
        schemas = {payload["schema"] for payload in repository.records.values()}
        self.assertIn("HybridSandboxScene@1", schemas)
        self.assertIn("SandboxRealizationReceipt@1", schemas)


class _ScriptedCompiler:
    def __init__(self) -> None:
        self.calls = 0

    async def compile(self, *, run, raw_request, prompt):  # type: ignore[no-untyped-def]
        self.calls += 1
        state, lifecycle = _compiled_transition(run)
        return CompiledProductionStep(state, lifecycle)


class _UnavailableCompiler:
    def __init__(self) -> None:
        self.calls = 0

    async def compile(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise ProviderUnavailable("configured provider is unavailable")


class ProductionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = (
            Path(self.temp.name)
            / "workspace"
            / "projects"
            / "runtime-demo"
        )
        self.repo = FilesystemProjectRepository.initialize(
            self.root,
            project_id="runtime-demo",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "initialized",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        )
        self.run = self.repo.create_run("run-001")
        self.prompt = "Develop the semantic dome shell for this project."
        self.request = self.repo.put_json(
            run=self.run,
            destination=PersistenceDestination(PersistenceArea.INPUT),
            record_kind="raw-request",
            payload={"schema": "RawProjectRequest@1", "prompt": self.prompt},
        )

    async def test_run_then_resume_short_circuits_before_compiler(self):
        compiler = _ScriptedCompiler()
        first = await run_or_resume_production_step(
            self.repo,
            run=self.run,
            raw_request=self.request,
            prompt=self.prompt,
            step_id="semantic-geometry-001",
            compiler=compiler,
        )
        self.assertFalse(first.resumed)
        self.assertEqual(1, compiler.calls)

        reopened = FilesystemProjectRepository.open(self.root)
        unavailable = _UnavailableCompiler()
        resumed = await run_or_resume_production_step(
            reopened,
            run=reopened.load_run("run-001"),
            raw_request=self.request,
            prompt=self.prompt,
            step_id="semantic-geometry-001",
            compiler=unavailable,
        )
        self.assertTrue(resumed.resumed)
        self.assertEqual(0, unavailable.calls)
        self.assertEqual(
            first.archive.checkpoint_ref,
            resumed.archive.checkpoint_ref,
        )
        self.assertFalse(resumed.to_dict()["canonical_write_authority"])

    async def test_unavailable_provider_leaves_no_completion_and_can_retry(self):
        unavailable = _UnavailableCompiler()
        with self.assertRaises(ProviderUnavailable):
            await run_or_resume_production_step(
                self.repo,
                run=self.run,
                raw_request=self.request,
                prompt=self.prompt,
                step_id="semantic-geometry-001",
                compiler=unavailable,
            )
        self.assertEqual(1, unavailable.calls)

        scripted = _ScriptedCompiler()
        recovered = await run_or_resume_production_step(
            self.repo,
            run=self.run,
            raw_request=self.request,
            prompt=self.prompt,
            step_id="semantic-geometry-001",
            compiler=scripted,
        )
        self.assertFalse(recovered.resumed)
        self.assertEqual(1, scripted.calls)

    async def test_prompt_must_match_immutable_raw_request(self):
        compiler = _ScriptedCompiler()
        with self.assertRaisesRegex(ProductionRuntimeError, "raw prompt"):
            await run_or_resume_production_step(
                self.repo,
                run=self.run,
                raw_request=self.request,
                prompt="A different prompt.",
                step_id="semantic-geometry-001",
                compiler=compiler,
            )
        self.assertEqual(0, compiler.calls)

    async def test_supplied_run_must_match_p036_manifest(self):
        other = FilesystemProjectRepository.initialize(
            Path(self.temp.name) / "other",
            project_id="runtime-demo",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "other",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        ).create_run("run-001")
        compiler = _ScriptedCompiler()
        with self.assertRaisesRegex(ProductionRuntimeError, "does not match P036"):
            await run_or_resume_production_step(
                self.repo,
                run=other,
                raw_request=self.request,
                prompt=self.prompt,
                step_id="semantic-geometry-001",
                compiler=compiler,
            )
        self.assertEqual(0, compiler.calls)


class ProductionCliTests(unittest.TestCase):
    def test_official_cli_starts_then_resumes_p036_sandbox_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = RuntimePaths(
                workspace_root=root / "workspace",
                cache_root=root / "cache",
                temp_root=root / "temp",
            )
            initial_context, options = _context_and_options()
            project_id = initial_context.run.project_id
            run_id = initial_context.run.run_id
            prompt = "Design from the supplied current project state."

            bootstrap_external_project(
                paths,
                project_id=project_id,
                prompt=prompt,
                run_id=run_id,
            )
            repository = FilesystemProjectRepository.open(
                paths.project(project_id)
            )
            context = _rebase_context(
                initial_context,
                repository.load_run(run_id),
            )
            context_path = root / "production-context.json"
            context_path.write_text(
                json.dumps(
                    context.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            provider = _ProductionProvider(options)

            def configured_provider(**kwargs):  # type: ignore[no-untyped-def]
                return activate_model_provider(
                    provider,
                    identity=ProviderIdentity(
                        provider_id=IDENTITY.provider_id,
                        version=IDENTITY.provider_version,
                        fingerprint=IDENTITY.provider_fingerprint,
                    ),
                    responsibility_id=kwargs["responsibility_id"],
                    contract_owner_id=kwargs["contract_owner_id"],
                    verification_evidence_refs=kwargs[
                        "verification_evidence_refs"
                    ],
                    envelope_observer=kwargs["envelope_observer"],
                )

            with patch(
                "archflow.production.activate_codex_agent_cli_provider",
                side_effect=configured_provider,
            ):
                started = run_external_production(
                    paths,
                    project_id=project_id,
                    prompt=prompt,
                    run_id=run_id,
                    context_path=context_path,
                    agent_executable="unused-scripted-agent",
                    model_id=IDENTITY.model_id,
                    provider_version=IDENTITY.provider_version,
                    timeout_seconds=1.0,
                )
            self.assertFalse(started.resumed)
            self.assertEqual(4, len(provider.calls))

            reopened = FilesystemProjectRepository.open(
                paths.project(project_id)
            )
            schemas = {
                reopened.load_json(ref)["schema"]
                for ref in reopened.list_json(
                    run=reopened.load_run(run_id),
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run_id,
                    ),
                )
            }
            self.assertIn("HybridSandboxScene@1", schemas)
            self.assertIn("SandboxRealizationReceipt@1", schemas)
            self.assertFalse((paths.workspace_root / ".runs").exists())

            resume_provider = _ProductionProvider(options)

            def resume_configured(**kwargs):  # type: ignore[no-untyped-def]
                return activate_model_provider(
                    resume_provider,
                    identity=ProviderIdentity(
                        provider_id=IDENTITY.provider_id,
                        version=IDENTITY.provider_version,
                        fingerprint=IDENTITY.provider_fingerprint,
                    ),
                    responsibility_id=kwargs["responsibility_id"],
                    contract_owner_id=kwargs["contract_owner_id"],
                    verification_evidence_refs=kwargs[
                        "verification_evidence_refs"
                    ],
                    envelope_observer=kwargs["envelope_observer"],
                )

            config = root / "runtime.json"
            config.write_text(
                json.dumps(
                    paths.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with patch(
                "archflow.production.activate_codex_agent_cli_provider",
                side_effect=resume_configured,
            ), redirect_stdout(stdout):
                code = main(
                    [
                        "--config",
                        str(config),
                        "--repository-root",
                        str(Path(__file__).resolve().parents[1]),
                        "run-project",
                        "--project-id",
                        project_id,
                        "--prompt",
                        prompt,
                        "--run-id",
                        run_id,
                        "--model",
                        IDENTITY.model_id,
                    ]
                )
            self.assertEqual(0, code)
            self.assertTrue(json.loads(stdout.getvalue())["resumed"])
            self.assertEqual([], resume_provider.calls)

if __name__ == "__main__":
    unittest.main()
