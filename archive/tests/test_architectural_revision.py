from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace

from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import proposal_edit_authoring_output
from archive.archflow.capabilities.evidence_sufficiency import (
    DecisionNode,
    DecisionUniverseRevision,
    EvidenceClaimBinding,
    EvidenceRule,
    EvidenceSufficiencyPolicy,
    FrontierStatus,
    build_research_frontier,
    compile_decision_universe_closure,
    compile_evidence_sufficiency,
)
from archive.archflow.capabilities.semantic_spatial_authoring import (
    semantic_spatial_authoring_output,
)
from archive.archflow.production.provider_runtime import InvocationEvidenceCollector, activate_model_provider
from archive.archflow.production.responsibility import ProviderIdentity
from archflow.project.refs import ProjectRecordRef
from archive.archflow.runtime.architectural_revision import (
    ArchitecturalRevisionCompiler,
    ArchitecturalRevisionError,
    _unchanged_invalidated_descendants,
    compile_architectural_revision_feedback,
)
from archive.archflow.runtime.production_compiler import ProductionRootCompiler
from archflow.state.spatial import ComponentMaturity, DesignComponent
from archflow.state.spatial import SchematicOptionSet, compile_component_transition
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import SemanticBinding
from archive.archflow.validation.architectural import (
    ArchitecturalCriterion,
    ArchitecturalObservation,
    ArchitecturalUsabilityContract,
    ArchitecturalValidationContext,
    AuthorizedCriterionSource,
    CriterionOperator,
    CriterionSourceKind,
    canonical_value,
    evaluate_architectural_usability,
)
from archive.tests.test_geometry_compiler import EVIDENCE
from archive.tests.test_production_root_compiler import (
    IDENTITY,
    _MemoryRepository,
    _ProductionProvider,
    _context_and_options,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _p079_acceptance(scope_digest: str):
    decision_ref = "decision:fixture-layout"
    universe = DecisionUniverseRevision(
        universe_id="revision-universe",
        revision_id="revision-universe-001",
        scope_digest=scope_digest,
        ontology_ref="project:ontology/revision-001",
        nodes=(DecisionNode(decision_ref, "project:decision"),),
        seed_refs=(decision_ref,),
    )
    policy = EvidenceSufficiencyPolicy(
        policy_id="revision-policy",
        rules=(
            EvidenceRule(
                obligation_id="fixture-layout-basis",
                target_ref=decision_ref,
            ),
        ),
    )
    closure = compile_decision_universe_closure(universe, policy)
    sufficiency = compile_evidence_sufficiency(
        universe,
        policy,
        claims=(
            EvidenceClaimBinding(
                binding_id="fixture-layout-binding",
                obligation_id="fixture-layout-basis",
                target_ref=decision_ref,
                fact_ref="fact:fixture-layout",
                source_ref="source:fixture-layout",
                source_family_ref="family:primary",
                claim_key="claim:fixture-layout",
                position_key="position:accepted",
            ),
        ),
    )
    frontier = build_research_frontier(closure, sufficiency)
    return universe, policy, closure, sufficiency, frontier


def _failed_entry_contract(
    state,
    program,
    *,
    authority_ref: str,
):  # type: ignore[no-untyped-def]
    ownership = tuple(
        sorted(
            (
                binding.component_id,
                tuple(sorted(binding.object_ids)),
            )
            for binding in program.proposal.semantic_bindings
        )
    )
    context = ArchitecturalValidationContext(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        component_tree_digest=(
            state.selected_schematic.option.proposal.proposal_digest
        ),
        geometry_program_digest=program.program_digest,
        realization_receipt_digest="5" * 64,
        scene_digest="6" * 64,
        artifact_ref=(
            f"project://{state.project_id}/runs/{state.run_id}/"
            "artifacts/sandbox-scene.json"
        ),
        brief_digest=None,
        component_ids=tuple(
            sorted(
                item.component_id
                for item in state.selected_schematic.option.proposal.components
            )
        ),
        geometry_object_ids=tuple(
            sorted(item.object_id for item in program.objects)
        ),
        semantic_ownership=ownership,
        obligation_refs=tuple(sorted(item.ref for item in state.obligations)),
    )
    criterion = ArchitecturalCriterion(
        criterion_id="usable-main-entry",
        measurement_key="usable-main-entry-count",
        operator=CriterionOperator.MINIMUM,
        expected_json=canonical_value(1),
        unit="count",
        mandatory=True,
        source_refs=(authority_ref,),
    )
    contract = ArchitecturalUsabilityContract(
        context=context,
        authorized_record_refs=(authority_ref,),
        sources=(
            AuthorizedCriterionSource(
                source_ref=authority_ref,
                kind=CriterionSourceKind.BUILD_POLICY,
                authority_ref=authority_ref,
            ),
        ),
        criteria=(criterion,),
    )
    observation = ArchitecturalObservation(
        observation_id="observe-usable-main-entry",
        contract_digest=contract.contract_digest,
        measurement_key=criterion.measurement_key,
        value_json=canonical_value(0),
        unit="count",
        component_ids=(),
        geometry_object_ids=(),
        obligation_refs=(),
        evidence_refs=(authority_ref,),
    )
    return contract, evaluate_architectural_usability(
        contract,
        (observation,),
    )


class ArchitecturalRevisionFeedbackTests(unittest.TestCase):
    def test_failed_minimum_becomes_exact_semantic_and_geometry_feedback(self):
        context, options = _context_and_options()
        predecessor = options[1]
        repository = _MemoryRepository()
        authority_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="authority",
            payload={"schema": "AuthorityFixture@1"},
        ).uri
        provider = _ProductionProvider(options)
        # Obtain a real compiled program and developed state from the existing
        # production contract instead of constructing a second ownership tree.
        collector = InvocationEvidenceCollector()
        authorized = activate_model_provider(
            provider,
            identity=ProviderIdentity(
                provider_id=IDENTITY.provider_id,
                version=IDENTITY.provider_version,
                fingerprint=IDENTITY.provider_fingerprint,
            ),
            responsibility_id="model.feedback-fixture",
            contract_owner_id="archflow.tests",
            verification_evidence_refs=(authority_ref,),
            envelope_observer=collector.observe,
        )
        context_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="context",
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
            "prompt": "Design the fixture.",
        }

        async def compile_fixture():
            return await ProductionRootCompiler(
                repository=repository,
                context_ref=context_ref,
                context=context,
                provider=authorized,
                evidence_collector=collector,
                geometry_provider_identity=IDENTITY,
            ).compile(
                run=context.run,
                raw_request=raw_ref,
                prompt="Design the fixture.",
            )

        compiled = __import__("asyncio").run(compile_fixture())
        contract, receipt = _failed_entry_contract(
            compiled.current_design_state,
            compiled.lifecycle.geometry_program,
            authority_ref=authority_ref,
        )
        feedback = compile_architectural_revision_feedback(
            contract=contract,
            receipt=receipt,
            predecessor_proposal=(
                compiled.current_design_state.selected_schematic.option.proposal
            ),
        )

        self.assertEqual(1, len(feedback.geometry_issues))
        self.assertEqual(
            "usable-main-entry-count",
            feedback.realization_requirements[0]["property"],
        )
        self.assertEqual("minimum", feedback.realization_requirements[0]["relation"])
        self.assertFalse(
            feedback.to_dict()["building_answer_authored_by_framework"]
        )
        with self.assertRaisesRegex(ArchitecturalRevisionError, "only a failed"):
            passed = replace(
                receipt,
                status=__import__(
                    "archive.archflow.validation.architectural",
                    fromlist=["ArchitecturalUsabilityStatus"],
                ).ArchitecturalUsabilityStatus.PASSED,
                findings=tuple(
                    replace(
                        item,
                        status=__import__(
                            "archive.archflow.validation.architectural",
                            fromlist=["CriterionFindingStatus"],
                        ).CriterionFindingStatus.PASS,
                    )
                    for item in receipt.findings
                ),
            )
            compile_architectural_revision_feedback(
                contract=contract,
                receipt=passed,
                predecessor_proposal=(
                    compiled.current_design_state.selected_schematic.option.proposal
                ),
            )


class _RevisionProvider:
    def __init__(self, predecessor_state, predecessor_program) -> None:  # type: ignore[no-untyped-def]
        self.predecessor_state = predecessor_state
        self.predecessor_program = predecessor_program
        self.calls = []

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        schema = request.payload["schema"]
        if schema == "SemanticSpatialAuthoringPrompt@1":
            prior = self.predecessor_state.selected_schematic.option.proposal
            root = prior.components[0]
            entry = DesignComponent(
                component_id="main-entry",
                parent_component_id=root.component_id,
                semantic_kind="entrance",
                intent="Provide the required usable main entry.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=prior.evidence_refs,
            )
            successor = replace(
                prior,
                components=tuple(
                    sorted(
                        (root, entry),
                        key=lambda item: item.component_id,
                    )
                ),
                rationale=(
                    prior.rationale
                    + " The successor explicitly owns a usable main entry."
                ),
            )
            output = semantic_spatial_authoring_output(request, successor)
        elif schema == "GeometryProposalAuthoringRequest@1":
            state = DevelopedDesignState.from_dict(
                request.payload["developed_design_state"]
            )
            ref = request.payload["spatial_option_record"]["ref"]
            spatial_uri = ProjectRecordRef(
                project_id=ref["project_id"],
                relative_path=ref["relative_path"],
                sha256=ref["sha256"],
                media_type=ref["media_type"],
            ).uri
            prior = self.predecessor_program.proposal
            existing = prior.semantic_bindings[0]
            entry_binding = SemanticBinding(
                binding_id="main-entry-binding",
                component_id="main-entry",
                object_ids=("main-entry-object",),
                commitment_refs=(),
                evidence_refs=tuple(sorted((EVIDENCE, spatial_uri))),
            )
            seed = next(item for item in prior.operations if item.op_id == "floor")
            entry_operation = replace(
                seed,
                op_id="main-entry-solid",
                output_object_ids=("main-entry-object",),
                semantic_binding_ids=("main-entry-binding",),
            )
            proposal = replace(
                prior,
                proposal_id="architectural-entry-revision",
                design_state_digest=state.state_digest,
                predecessor_program_digest=(
                    self.predecessor_program.program_digest
                ),
                semantic_bindings=tuple(
                    sorted(
                        (existing, entry_binding),
                        key=lambda item: item.binding_id,
                    )
                ),
                operations=tuple(
                    sorted(
                        (*prior.operations, entry_operation),
                        key=lambda item: item.op_id,
                    )
                ),
            )
            output = proposal_edit_authoring_output(
                self.predecessor_program,
                proposal,
            )
        else:
            raise AssertionError(f"unexpected revision request: {schema}")
        encoded = _canonical(output)
        return ModelInvocationReceipt(
            receipt_id=f"revision-{len(self.calls):02d}",
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


class ArchitecturalRevisionCompilerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_entry_drives_atomic_model_authored_successor(self):
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
            relative_path="input/raw-request-" + "b" * 64 + ".json",
            sha256="b" * 64,
            media_type="application/json",
        )
        repository.records[raw_ref] = {
            "schema": "RawProjectRequest@1",
            "prompt": "Design the fixture.",
        }
        root_collector = InvocationEvidenceCollector()
        root_provider = _ProductionProvider(options)
        root_authorized = activate_model_provider(
            root_provider,
            identity=ProviderIdentity(
                provider_id=IDENTITY.provider_id,
                version=IDENTITY.provider_version,
                fingerprint=IDENTITY.provider_fingerprint,
            ),
            responsibility_id="model.revision-root",
            contract_owner_id="archflow.tests",
            verification_evidence_refs=(context_ref.uri,),
            envelope_observer=root_collector.observe,
        )
        initial = await ProductionRootCompiler(
            repository=repository,
            context_ref=context_ref,
            context=context,
            provider=root_authorized,
            evidence_collector=root_collector,
            geometry_provider_identity=IDENTITY,
        ).compile(
            run=context.run,
            raw_request=raw_ref,
            prompt="Design the fixture.",
        )
        predecessor_state = initial.current_design_state
        predecessor_program = initial.lifecycle.geometry_program
        state_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="predecessor-state",
            payload=predecessor_state.to_dict(),
        )
        program_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="predecessor-program",
            payload=predecessor_program.to_dict(),
        )

        def ref_for(predicate):  # type: ignore[no-untyped-def]
            matches = [
                ref for ref, payload in repository.records.items() if predicate(payload)
            ]
            self.assertEqual(1, len(matches))
            return matches[0]

        option_set_ref = ref_for(
            lambda value: value.get("schema") == "SchematicOptionSet@1"
        )
        selection_ref = ref_for(
            lambda value: value.get("schema") == "SchematicSelectionReceipt@1"
        )
        predecessor_spatial_ref = ref_for(
            lambda value: value == predecessor_state.selected_schematic.option.proposal.to_dict()
        )
        contract, receipt = _failed_entry_contract(
            predecessor_state,
            predecessor_program,
            authority_ref=context_ref.uri,
        )
        contract_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="architectural-contract",
            payload=contract.to_dict(),
        )
        receipt_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="architectural-receipt",
            payload=receipt.to_dict(),
        )
        revision_collector = InvocationEvidenceCollector()
        revision_provider = _RevisionProvider(
            predecessor_state,
            predecessor_program,
        )
        revision_authorized = activate_model_provider(
            revision_provider,
            identity=ProviderIdentity(
                provider_id=IDENTITY.provider_id,
                version=IDENTITY.provider_version,
                fingerprint=IDENTITY.provider_fingerprint,
            ),
            responsibility_id="model.architectural-revision",
            contract_owner_id="archflow.architectural-revision",
            verification_evidence_refs=(receipt_ref.uri,),
            envelope_observer=revision_collector.observe,
        )
        compiler = ArchitecturalRevisionCompiler(
            repository=repository,
            context_ref=context_ref,
            context=context,
            predecessor_state_ref=state_ref,
            predecessor_program_ref=program_ref,
            option_set_ref=option_set_ref,
            selection_ref=selection_ref,
            predecessor_spatial_ref=predecessor_spatial_ref,
            architectural_contract_ref=contract_ref,
            architectural_receipt_ref=receipt_ref,
            provider=revision_authorized,
            evidence_collector=revision_collector,
            geometry_provider_identity=IDENTITY,
        )
        with self.assertRaisesRegex(
            ArchitecturalRevisionError,
            "requires its persisted RAG index",
        ):
            replace(
                compiler,
                selection_ref=ProjectRecordRef(
                    project_id=context.run.project_id,
                    relative_path=(
                        f"runs/{context.run.run_id}/records/"
                        f"branch-selection-{'a' * 64}.json"
                    ),
                    sha256="a" * 64,
                ),
            )

        branch_scope_digest = "b" * 64
        branch_index_ref = ProjectRecordRef(
            project_id=context.run.project_id,
            relative_path=(
                f"runs/{context.run.run_id}/branches/classical/records/"
                f"branch-basis-{'b' * 64}.json"
            ),
            sha256="b" * 64,
        )
        with self.assertRaisesRegex(
            ArchitecturalRevisionError,
            "exact P079 acceptance",
        ):
            replace(
                compiler,
                branch_basis_index_ref=branch_index_ref,
                expected_branch_scope_digest=branch_scope_digest,
                branch_decision_refs=("decision:fixture-layout",),
            )

        acceptance = _p079_acceptance(branch_scope_digest)
        branch_compiler = replace(
            compiler,
            branch_basis_index_ref=branch_index_ref,
            expected_branch_scope_digest=branch_scope_digest,
            branch_decision_refs=("decision:fixture-layout",),
            branch_decision_universe=acceptance[0],
            branch_evidence_policy=acceptance[1],
            branch_universe_closure=acceptance[2],
            branch_evidence_sufficiency=acceptance[3],
            branch_research_frontier=acceptance[4],
        )
        self.assertIs(
            branch_compiler.branch_research_frontier.status,
            FrontierStatus.COMPLETE,
        )
        with self.assertRaisesRegex(
            ArchitecturalRevisionError,
            "crosses the expected research scope",
        ):
            replace(
                branch_compiler,
                branch_decision_universe=replace(
                    acceptance[0],
                    scope_digest="c" * 64,
                ),
            )

        revised = await compiler.compile(
            run=context.run,
            raw_request=raw_ref,
            prompt="Design the fixture.",
        )

        self.assertEqual(2, len(revision_provider.calls))
        self.assertEqual(2, len(revised.invocation_envelopes))
        self.assertIn(
            "revision_context",
            revision_provider.calls[0].payload,
        )
        geometry_request = revision_provider.calls[1].payload
        self.assertEqual(
            predecessor_program.program_digest,
            geometry_request["available_predecessor_program_digest"],
        )
        self.assertIn(
            "usable-main-entry-count",
            {
                item["property"]
                for item in geometry_request["realization_contract"][
                    "required_properties"
                ]
            },
        )
        self.assertEqual(
            geometry_request["required_geometry_component_ids"],
            ["main-entry"],
        )
        components = {
            item.component_id
            for item in revised.current_design_state.selected_schematic.option.proposal.components
        }
        self.assertIn("main-entry", components)
        self.assertIn(
            "main-entry-object",
            {item.object_id for item in revised.lifecycle.geometry_program.objects},
        )
        portfolio_refs = [
            ref
            for ref, payload in repository.records.items()
            if payload.get("schema") == "DesignOptionPortfolio@1"
        ]
        self.assertEqual(1, len(portfolio_refs))
        chained = replace(
            compiler,
            predecessor_portfolio_ref=portfolio_refs[0],
        )
        reconstructed = chained._reconstruct_predecessor_portfolio(
            run=context.run,
            raw_request=raw_ref,
            option_set=SchematicOptionSet.from_dict(
                repository.load_json(option_set_ref)
            ),
            predecessor_state=revised.current_design_state,
        )
        self.assertEqual(
            revised.current_design_state.selected_schematic.option,
            reconstructed.selected_branch.head.option,
        )

    async def test_unchanged_descendants_are_revalidated_without_patching(self):
        context, options = _context_and_options()
        repository = _MemoryRepository()
        context_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="context",
            payload=context.to_dict(),
        )
        raw_ref = ProjectRecordRef(
            project_id=context.run.project_id,
            relative_path="input/raw-request-" + "c" * 64 + ".json",
            sha256="c" * 64,
            media_type="application/json",
        )
        repository.records[raw_ref] = {
            "schema": "RawProjectRequest@1",
            "prompt": "Design the fixture.",
        }
        collector = InvocationEvidenceCollector()
        provider = activate_model_provider(
            _ProductionProvider(options),
            identity=ProviderIdentity(
                provider_id=IDENTITY.provider_id,
                version=IDENTITY.provider_version,
                fingerprint=IDENTITY.provider_fingerprint,
            ),
            responsibility_id="model.revalidation-fixture",
            contract_owner_id="archflow.tests",
            verification_evidence_refs=(context_ref.uri,),
            envelope_observer=collector.observe,
        )
        compiled = await ProductionRootCompiler(
            repository=repository,
            context_ref=context_ref,
            context=context,
            provider=provider,
            evidence_collector=collector,
            geometry_provider_identity=IDENTITY,
        ).compile(
            run=context.run,
            raw_request=raw_ref,
            prompt="Design the fixture.",
        )
        predecessor = compiled.current_design_state.selected_schematic.option.proposal
        root = next(
            item for item in predecessor.components
            if item.parent_component_id is None
        )
        successor = replace(
            predecessor,
            components=tuple(
                sorted(
                    (
                        replace(
                            root,
                            intent=root.intent + " Refined without moving descendants.",
                            revision=root.revision + 1,
                        ),
                        *(
                            item for item in predecessor.components
                            if item.component_id != root.component_id
                        ),
                    ),
                    key=lambda item: item.component_id,
                )
            ),
        )
        transition = compile_component_transition(predecessor, successor)

        revalidated = _unchanged_invalidated_descendants(
            transition,
            predecessor,
            successor,
            compiled.lifecycle.geometry_program,
            compiled.lifecycle.geometry_program,
        )

        self.assertEqual(
            revalidated,
            tuple(
                sorted(
                    item.component_id for item in predecessor.components
                    if item.component_id != root.component_id
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
