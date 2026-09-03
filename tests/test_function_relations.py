"""Exact component-function to relation requirement bridge tests."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archflow.control.baseline import StageBaselineLevel
from archflow.control.component_functions import (
    DEFAULT_COMPONENT_FUNCTION_POLICY,
    ComponentFunctionContract,
    ComponentFunctionId,
    FunctionApplicability,
    FunctionApplicabilityDecision,
    FunctionClaimStatus,
    FunctionEndpointBinding,
    FunctionMaturity,
    FunctionObligationClaim,
    compile_component_function_ledger,
)
from archflow.control.function_relations import (
    FunctionRelationConsumer,
    FunctionRelationEndpoint,
    FunctionRelationEndpointBinding,
    FunctionRelationError,
    FunctionRelationEvidenceEnvelope,
    FunctionRelationRequirementSet,
    compile_function_relation_requirements,
)
from archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.relations.contracts import (
    ArchitecturalRelationKind,
    RelationProjection,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def branch(*, epoch: int = 0) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="function-relation-fixture",
            run_id="run-001",
            base=ProjectVersionRef("function-relation-fixture", 0, SHA_A),
        ),
        branch_id="candidate",
        epoch=epoch,
    )


def inventory(*, selected_branch: BranchRef | None = None) -> StageSubjectInventory:
    selected_branch = selected_branch or branch()
    prefix = "runs/run-001/branches/candidate/records"
    return StageSubjectInventory(
        inventory_id="stage-2-function-relation-subjects",
        branch=selected_branch,
        stage_id="stage-2",
        stage_subject_ref="stage-subject:stage-2",
        stage_subject_digest=SHA_D,
        baseline_level=StageBaselineLevel.PRE_GEOMETRY,
        component_proposal_ref=ProjectRecordRef(
            "function-relation-fixture", f"{prefix}/proposal.json", SHA_A
        ),
        component_proposal_digest=SHA_A,
        component_index_ref=ProjectRecordRef(
            "function-relation-fixture", f"{prefix}/index.json", SHA_B
        ),
        component_index_digest=SHA_B,
        entries=(
            StageSubjectInventoryEntry(
                component_id="beam-a",
                identity_ref="design-component:beam-a",
                parent_component_id="foundation",
                semantic_kind="beam",
                component_digest=SHA_A,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=(),
            ),
            StageSubjectInventoryEntry(
                component_id="beam-b",
                identity_ref="design-component:beam-b",
                parent_component_id="foundation",
                semantic_kind="beam",
                component_digest=SHA_B,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=(),
            ),
            StageSubjectInventoryEntry(
                component_id="foundation",
                identity_ref="design-component:foundation",
                parent_component_id=None,
                semantic_kind="foundation",
                component_digest=SHA_C,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=(),
            ),
        ),
    )


def support_contract(
    subjects: StageSubjectInventory,
    component_id: str,
    *,
    supporter_ref: str = "design-component:foundation",
) -> ComponentFunctionContract:
    entry = next(item for item in subjects.entries if item.component_id == component_id)
    spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(ComponentFunctionId.BE_SUPPORTED)
    claim = FunctionObligationClaim(
        obligation_ref=spec.obligation_ref,
        endpoint_bindings=(
            FunctionEndpointBinding(
                role="supported_component", endpoint_refs=(entry.identity_ref,)
            ),
            FunctionEndpointBinding(
                role="supporting_component", endpoint_refs=(supporter_ref,)
            ),
        ),
        maturity=FunctionMaturity.GEOMETRIC,
        status=FunctionClaimStatus.UNKNOWN,
        evidence_refs=("evidence:function-claim",),
        authority_refs=("authority:function-review",),
        contradiction_refs=(),
    )
    return ComponentFunctionContract(
        contract_id=f"{component_id}-functions",
        branch=subjects.branch,
        stage_id=subjects.stage_id,
        subject_inventory_digest=subjects.inventory_digest,
        component_ref=entry.identity_ref,
        component_digest=entry.component_digest,
        applicability_decisions=tuple(
            FunctionApplicabilityDecision(
                function_id=item,
                applicability=(
                    FunctionApplicability.REQUIRED
                    if item is ComponentFunctionId.BE_SUPPORTED
                    else FunctionApplicability.NOT_APPLICABLE
                ),
                evidence_refs=("evidence:function-claim",),
                authority_refs=("authority:function-review",),
            )
            for item in ComponentFunctionId
        ),
        claims=(claim,),
    )


def compiled_ledger(subjects: StageSubjectInventory):
    return compile_component_function_ledger(
        ledger_id="stage-2-functions",
        inventory=subjects,
        contracts=(
            support_contract(subjects, "beam-a"),
            support_contract(subjects, "beam-b"),
        ),
    )


def envelope(
    subjects: StageSubjectInventory,
    ledger,
    component_id: str,
    *,
    relation_kind: ArchitecturalRelationKind = ArchitecturalRelationKind.SUPPORT,
    projection: RelationProjection = RelationProjection.SUPPORT,
) -> FunctionRelationEvidenceEnvelope:
    entry = next(item for item in subjects.entries if item.component_id == component_id)
    foundation = next(
        item for item in subjects.entries if item.component_id == "foundation"
    )
    if relation_kind is ArchitecturalRelationKind.SUPPORT:
        subject_role, counted_role = "supported", "supporter"
    else:
        subject_role, counted_role = "sender", "receiver"
    spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(ComponentFunctionId.BE_SUPPORTED)
    return FunctionRelationEvidenceEnvelope(
        envelope_id=f"{component_id}-support-relation",
        branch=subjects.branch,
        stage_id=subjects.stage_id,
        subject_inventory_digest=subjects.inventory_digest,
        function_ledger_ref=ledger.ledger_ref,
        function_ledger_digest=ledger.ledger_digest,
        component_ref=entry.identity_ref,
        component_digest=entry.component_digest,
        functional_obligation_ref=spec.obligation_ref,
        projection=projection,
        relation_kind=relation_kind,
        scenario_ref="scenario:gravity",
        endpoint_bindings=(
            FunctionRelationEndpointBinding(
                function_role="supported_component",
                relation_role=subject_role,
                endpoints=(FunctionRelationEndpoint(entry.identity_ref, entry.component_digest),),
            ),
            FunctionRelationEndpointBinding(
                function_role="supporting_component",
                relation_role=counted_role,
                endpoints=(
                    FunctionRelationEndpoint(
                        foundation.identity_ref, foundation.component_digest
                    ),
                ),
            ),
        ),
        counted_function_role="supporting_component",
        basis_ids=(f"{component_id}-gravity-basis",),
        evidence_refs=(f"evidence:{component_id}-gravity",),
        authority_refs=(f"authority:{component_id}-gravity-review",),
        prompt=f"Identify the authored support path for {component_id}.",
    )


class FunctionRelationBridgeTests(unittest.TestCase):
    def test_exact_once_component_filter_prevents_semantic_kind_cross_product(self) -> None:
        subjects = inventory()
        ledger = compiled_ledger(subjects)
        envelopes = (
            envelope(subjects, ledger, "beam-a"),
            envelope(
                subjects,
                ledger,
                "beam-b",
                relation_kind=ArchitecturalRelationKind.LOAD_TRANSFER,
            ),
        )
        result = compile_function_relation_requirements(
            set_id="function-relations",
            ledger=ledger,
            inventory=subjects,
            envelopes=envelopes,
        )

        self.assertEqual(2, len(result.requirements))
        self.assertEqual(
            ("design-component:beam-a", "design-component:beam-b"),
            tuple(item.slot.node_ref for item in result.requirements),
        )
        self.assertEqual(
            (ArchitecturalRelationKind.SUPPORT, ArchitecturalRelationKind.LOAD_TRANSFER),
            tuple(item.rule.relation_kind for item in result.requirements),
        )
        self.assertEqual(
            {"design-component:foundation"},
            {item.question.target_refs[0] for item in result.requirements},
        )
        self.assertEqual(2, len(result.topology_questions))
        self.assertEqual(2, len(result.exact_relation_slots))
        self.assertEqual(2, len(result.realization_requirements))
        for requirement in result.requirements:
            self.assertEqual(
                DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(
                    ComponentFunctionId.BE_SUPPORTED
                ).obligation_ref,
                requirement.functional_obligation_ref,
            )
            self.assertIn("supported", requirement.purpose)
            self.assertEqual(FunctionMaturity.GEOMETRIC, requirement.required_maturity)
            self.assertEqual(
                {"supported_component", "supporting_component"},
                {item.role for item in requirement.endpoint_roles},
            )
            self.assertEqual(
                (
                    FunctionRelationConsumer.STAGE2_TOPOLOGY,
                    FunctionRelationConsumer.STAGE3_REALIZATION_READBACK,
                ),
                requirement.consumers,
            )
            self.assertFalse(requirement.question.to_dict()["design_authority"])
            self.assertFalse(requirement.slot.to_dict()["stage_acceptance_authority"])

    def test_supporter_function_separates_traversal_start_from_rule_subject(self) -> None:
        subjects = inventory()
        foundation = next(
            item for item in subjects.entries if item.component_id == "foundation"
        )
        beam = next(item for item in subjects.entries if item.component_id == "beam-a")
        spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(
            ComponentFunctionId.SUPPORT_OTHERS
        )
        contract = ComponentFunctionContract(
            contract_id="foundation-support-functions",
            branch=subjects.branch,
            stage_id=subjects.stage_id,
            subject_inventory_digest=subjects.inventory_digest,
            component_ref=foundation.identity_ref,
            component_digest=foundation.component_digest,
            applicability_decisions=tuple(
                FunctionApplicabilityDecision(
                    function_id=item,
                    applicability=(
                        FunctionApplicability.REQUIRED
                        if item is ComponentFunctionId.SUPPORT_OTHERS
                        else FunctionApplicability.NOT_APPLICABLE
                    ),
                    evidence_refs=("evidence:foundation-support",),
                    authority_refs=("authority:structural-review",),
                )
                for item in ComponentFunctionId
            ),
            claims=(
                FunctionObligationClaim(
                    obligation_ref=spec.obligation_ref,
                    endpoint_bindings=(
                        FunctionEndpointBinding(
                            role="supported_component",
                            endpoint_refs=(beam.identity_ref,),
                        ),
                        FunctionEndpointBinding(
                            role="supporting_component",
                            endpoint_refs=(foundation.identity_ref,),
                        ),
                    ),
                    maturity=spec.required_maturity,
                    status=FunctionClaimStatus.UNKNOWN,
                    evidence_refs=("evidence:foundation-support",),
                    authority_refs=("authority:structural-review",),
                    contradiction_refs=(),
                ),
            ),
        )
        ledger = compile_component_function_ledger(
            ledger_id="foundation-support-ledger",
            inventory=subjects,
            contracts=(contract,),
        )
        relation = FunctionRelationEvidenceEnvelope(
            envelope_id="foundation-support-relation",
            branch=subjects.branch,
            stage_id=subjects.stage_id,
            subject_inventory_digest=subjects.inventory_digest,
            function_ledger_ref=ledger.ledger_ref,
            function_ledger_digest=ledger.ledger_digest,
            component_ref=foundation.identity_ref,
            component_digest=foundation.component_digest,
            functional_obligation_ref=spec.obligation_ref,
            projection=RelationProjection.SUPPORT,
            relation_kind=ArchitecturalRelationKind.SUPPORT,
            scenario_ref="scenario:gravity",
            endpoint_bindings=(
                FunctionRelationEndpointBinding(
                    function_role="supported_component",
                    relation_role="supported",
                    endpoints=(
                        FunctionRelationEndpoint(
                            beam.identity_ref,
                            beam.component_digest,
                        ),
                    ),
                ),
                FunctionRelationEndpointBinding(
                    function_role="supporting_component",
                    relation_role="supporter",
                    endpoints=(
                        FunctionRelationEndpoint(
                            foundation.identity_ref,
                            foundation.component_digest,
                        ),
                    ),
                ),
            ),
            counted_function_role="supported_component",
            basis_ids=("foundation-support-basis",),
            evidence_refs=("evidence:foundation-support",),
            authority_refs=("authority:structural-review",),
            prompt="Identify what this foundation supports.",
        )
        result = compile_function_relation_requirements(
            set_id="foundation-support-relations",
            ledger=ledger,
            inventory=subjects,
            envelopes=(relation,),
        )
        requirement = result.requirements[0]

        self.assertEqual((beam.identity_ref,), requirement.question.subject_refs)
        self.assertEqual(
            (foundation.identity_ref,), requirement.question.target_refs
        )
        self.assertEqual(
            (foundation.identity_ref,), requirement.question.rule_subject_refs
        )
        self.assertEqual(foundation.identity_ref, requirement.slot.node_ref)

    def test_missing_duplicate_foreign_stale_and_cross_boundary_fail_closed(self) -> None:
        subjects = inventory()
        ledger = compiled_ledger(subjects)
        beam_a = envelope(subjects, ledger, "beam-a")
        beam_b = envelope(subjects, ledger, "beam-b")

        with self.assertRaisesRegex(FunctionRelationError, "missing envelope"):
            compile_function_relation_requirements(
                set_id="missing",
                ledger=ledger,
                inventory=subjects,
                envelopes=(beam_a,),
            )
        with self.assertRaisesRegex(FunctionRelationError, "duplicate envelope"):
            compile_function_relation_requirements(
                set_id="duplicate",
                ledger=ledger,
                inventory=subjects,
                envelopes=(beam_a, replace(beam_a, envelope_id="beam-a-other"), beam_b),
            )
        foreign = replace(
            beam_a,
            component_ref="design-component:foundation",
            component_digest=SHA_C,
        )
        with self.assertRaisesRegex(FunctionRelationError, "foreign"):
            compile_function_relation_requirements(
                set_id="foreign",
                ledger=ledger,
                inventory=subjects,
                envelopes=(foreign, beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "inventory is stale"):
            compile_function_relation_requirements(
                set_id="stale-inventory",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, subject_inventory_digest=SHA_A), beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "ledger is stale"):
            compile_function_relation_requirements(
                set_id="stale-ledger",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, function_ledger_digest=SHA_A), beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "component is stale"):
            compile_function_relation_requirements(
                set_id="stale-component",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, component_digest=SHA_D), beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "crossed branch or stage"):
            compile_function_relation_requirements(
                set_id="cross-stage",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, stage_id="stage-3"), beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "crossed branch or stage"):
            compile_function_relation_requirements(
                set_id="cross-branch",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, branch=branch(epoch=1)), beam_b),
            )

    def test_foreign_stale_and_ambiguous_targets_fail_closed(self) -> None:
        subjects = inventory()
        ledger = compiled_ledger(subjects)
        beam_a = envelope(subjects, ledger, "beam-a")
        beam_b = envelope(subjects, ledger, "beam-b")
        supporter = beam_a.endpoint_bindings[1]

        foreign = replace(
            beam_a,
            endpoint_bindings=(
                beam_a.endpoint_bindings[0],
                replace(
                    supporter,
                    endpoints=(
                        FunctionRelationEndpoint("design-component:foreign", SHA_D),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(FunctionRelationError, "foreign target"):
            compile_function_relation_requirements(
                set_id="foreign-target",
                ledger=ledger,
                inventory=subjects,
                envelopes=(foreign, beam_b),
            )

        stale = replace(
            beam_a,
            endpoint_bindings=(
                beam_a.endpoint_bindings[0],
                replace(
                    supporter,
                    endpoints=(
                        FunctionRelationEndpoint("design-component:foundation", SHA_D),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(FunctionRelationError, "target digest is stale"):
            compile_function_relation_requirements(
                set_id="stale-target",
                ledger=ledger,
                inventory=subjects,
                envelopes=(stale, beam_b),
            )

        ambiguous_subjects = inventory()
        ambiguous_ledger = compile_component_function_ledger(
            ledger_id="ambiguous-functions",
            inventory=ambiguous_subjects,
            contracts=(
                support_contract(
                    ambiguous_subjects,
                    "beam-a",
                    supporter_ref="design-component:beam-a",
                ),
            ),
        )
        ambiguous = envelope(ambiguous_subjects, ambiguous_ledger, "beam-a")
        ambiguous = replace(
            ambiguous,
            endpoint_bindings=(
                ambiguous.endpoint_bindings[0],
                replace(
                    ambiguous.endpoint_bindings[1],
                    endpoints=(FunctionRelationEndpoint("design-component:beam-a", SHA_A),),
                ),
            ),
        )
        with self.assertRaisesRegex(FunctionRelationError, "ambiguous"):
            compile_function_relation_requirements(
                set_id="ambiguous-target",
                ledger=ambiguous_ledger,
                inventory=ambiguous_subjects,
                envelopes=(ambiguous,),
            )

    def test_projection_role_and_target_must_be_authored_not_guessed(self) -> None:
        subjects = inventory()
        ledger = compiled_ledger(subjects)
        beam_a = envelope(subjects, ledger, "beam-a")
        beam_b = envelope(subjects, ledger, "beam-b")
        with self.assertRaisesRegex(ValueError, "incompatible with its projection"):
            compile_function_relation_requirements(
                set_id="bad-projection",
                ledger=ledger,
                inventory=subjects,
                envelopes=(replace(beam_a, projection=RelationProjection.HOST), beam_b),
            )
        with self.assertRaisesRegex(FunctionRelationError, "invalid for relation kind"):
            replace(
                beam_a,
                endpoint_bindings=(
                    replace(beam_a.endpoint_bindings[0], relation_role="hosted"),
                    beam_a.endpoint_bindings[1],
                ),
            )

    def test_envelope_and_requirement_set_roundtrip_digest_and_tamper_are_strict(self) -> None:
        subjects = inventory()
        ledger = compiled_ledger(subjects)
        beam_a = envelope(subjects, ledger, "beam-a")
        beam_b = envelope(subjects, ledger, "beam-b")
        self.assertEqual(
            beam_a,
            FunctionRelationEvidenceEnvelope.from_dict(beam_a.to_dict()),
        )
        result = compile_function_relation_requirements(
            set_id="roundtrip-relations",
            ledger=ledger,
            inventory=subjects,
            envelopes=(beam_a, beam_b),
        )
        restored = FunctionRelationRequirementSet.from_dict(result.to_dict())
        self.assertEqual(result, restored)
        self.assertEqual(result.set_digest, restored.set_digest)

        digest_drift = copy.deepcopy(result.to_dict())
        digest_drift["requirements"][0]["purpose"] = "Tampered purpose."
        with self.assertRaisesRegex(FunctionRelationError, "digest changed"):
            FunctionRelationRequirementSet.from_dict(digest_drift)

        envelope_drift = copy.deepcopy(beam_a.to_dict())
        envelope_drift["prompt"] = "A drifted project instruction."
        with self.assertRaisesRegex(FunctionRelationError, "digest changed"):
            FunctionRelationEvidenceEnvelope.from_dict(envelope_drift)


if __name__ == "__main__":
    unittest.main()
