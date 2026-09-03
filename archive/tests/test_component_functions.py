from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archive.archflow.control.baseline import StageBaselineLevel
from archive.archflow.control.component_functions import (
    DEFAULT_COMPONENT_FUNCTION_POLICY,
    ComponentFunctionContract,
    ComponentFunctionError,
    ComponentFunctionId,
    ComponentFunctionLedger,
    ComponentFunctionPolicy,
    FunctionApplicability,
    FunctionApplicabilityDecision,
    FunctionClaimStatus,
    FunctionEndpointBinding,
    FunctionEvaluationStatus,
    FunctionMaturity,
    FunctionObligationClaim,
    compile_component_function_ledger,
    compile_function_diagnostic_entries,
)
from archive.archflow.control.function_diagnostics import FunctionStatus
from archive.archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch(epoch: int = 0) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="function-fixture",
            run_id="research-001",
            base=ProjectVersionRef("function-fixture", 1, SHA_A),
        ),
        branch_id="main",
        epoch=epoch,
    )


def inventory(stage_id: str = "stage-2") -> StageSubjectInventory:
    selected_branch = branch()
    prefix = "runs/research-001/branches/main/records"
    return StageSubjectInventory(
        inventory_id=f"{stage_id}-subjects",
        branch=selected_branch,
        stage_id=stage_id,
        stage_subject_ref=f"stage-subject:{stage_id}",
        stage_subject_digest=SHA_B,
        baseline_level=StageBaselineLevel.PRE_GEOMETRY,
        component_proposal_ref=ProjectRecordRef(
            "function-fixture", f"{prefix}/proposal.json", SHA_A
        ),
        component_proposal_digest=SHA_A,
        component_index_ref=ProjectRecordRef(
            "function-fixture", f"{prefix}/index.json", SHA_B
        ),
        component_index_digest=SHA_B,
        entries=(
            StageSubjectInventoryEntry(
                component_id="building",
                identity_ref="design-component:building",
                parent_component_id=None,
                semantic_kind="building",
                component_digest=SHA_A,
                geometry_object_ids=("building-object",),
                binding_ids=(),
                role_obligations=(),
            ),
            StageSubjectInventoryEntry(
                component_id="door",
                identity_ref="design-component:door",
                parent_component_id="building",
                semantic_kind="door",
                component_digest=SHA_B,
                geometry_object_ids=("door-object",),
                binding_ids=(),
                role_obligations=(),
            ),
            StageSubjectInventoryEntry(
                component_id="wall",
                identity_ref="design-component:wall",
                parent_component_id="building",
                semantic_kind="wall",
                component_digest=SHA_C,
                geometry_object_ids=("wall-object",),
                binding_ids=(),
                role_obligations=(),
            ),
        ),
    )


def claim(
    function_id: ComponentFunctionId,
    component_ref: str,
    peer_ref: str,
    *,
    status: FunctionClaimStatus = FunctionClaimStatus.PASS,
    evidence: tuple[str, ...] = ("evidence:source-1",),
    authority: tuple[str, ...] = ("authority:review-1",),
    contradiction: tuple[str, ...] = (),
) -> FunctionObligationClaim:
    spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
    bindings = []
    for role in spec.endpoint_roles:
        bindings.append(
            FunctionEndpointBinding(
                role=role.role,
                endpoint_refs=(component_ref,) if role.component_slot else (peer_ref,),
            )
        )
    return FunctionObligationClaim(
        obligation_ref=spec.obligation_ref,
        endpoint_bindings=tuple(bindings),
        maturity=spec.required_maturity,
        status=status,
        evidence_refs=evidence,
        authority_refs=authority,
        contradiction_refs=contradiction,
    )


def applicability_decisions(
    *,
    required: tuple[ComponentFunctionId, ...] = (),
    unknown: tuple[ComponentFunctionId, ...] = (),
) -> tuple[FunctionApplicabilityDecision, ...]:
    required_set = set(required)
    unknown_set = set(unknown)
    if required_set & unknown_set:
        raise ValueError("test applicability sets overlap")
    return tuple(
        FunctionApplicabilityDecision(
            function_id=function_id,
            applicability=(
                FunctionApplicability.REQUIRED
                if function_id in required_set
                else FunctionApplicability.UNKNOWN
                if function_id in unknown_set
                else FunctionApplicability.NOT_APPLICABLE
            ),
            evidence_refs=(f"evidence:applicability-{function_id.value.lower()}",),
            authority_refs=("authority:function-program",),
        )
        for function_id in ComponentFunctionId
    )


def contract(
    subjects: StageSubjectInventory,
    component: str,
    function_id: ComponentFunctionId,
    *,
    status: FunctionClaimStatus = FunctionClaimStatus.PASS,
    claims: tuple[FunctionObligationClaim, ...] | None = None,
) -> ComponentFunctionContract:
    entry = next(item for item in subjects.entries if item.component_id == component)
    component_ref = entry.identity_ref
    if claims is None:
        claims = (claim(function_id, component_ref, "design-component:building" if component != "building" else "design-component:wall", status=status),)
    return ComponentFunctionContract(
        contract_id=f"{subjects.stage_id}-{component}-functions",
        branch=subjects.branch,
        stage_id=subjects.stage_id,
        subject_inventory_digest=subjects.inventory_digest,
        component_ref=component_ref,
        component_digest=entry.component_digest,
        applicability_decisions=applicability_decisions(
            required=(function_id,)
        ),
        claims=claims,
    )


class ComponentFunctionLedgerTests(unittest.TestCase):
    def test_policy_is_versioned_complete_and_access_has_from_to_slots(self) -> None:
        policy = DEFAULT_COMPONENT_FUNCTION_POLICY
        self.assertEqual(1, policy.policy_version)
        self.assertEqual(set(ComponentFunctionId), {item.function_id for item in policy.obligations})
        for spec in policy.obligations:
            self.assertTrue(spec.purpose)
            self.assertTrue(spec.obligation_ref.startswith("function-obligation:"))
            self.assertIsInstance(spec.required_maturity, FunctionMaturity)
            self.assertEqual(1, sum(role.component_slot for role in spec.endpoint_roles))
            for role in spec.endpoint_roles:
                self.assertGreaterEqual(role.minimum, 1)
        access = policy.spec_for(ComponentFunctionId.PROVIDE_ACCESS)
        self.assertEqual(
            {"access_from", "access_to", "access_provider"},
            {item.role for item in access.endpoint_roles},
        )
        self.assertEqual(policy, ComponentFunctionPolicy.from_dict(policy.to_dict()))

    def test_contract_requires_exact_typed_applicability_denominator(self) -> None:
        subjects = inventory()
        door = contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS)

        self.assertEqual("ComponentFunctionContract@2", door.SCHEMA)
        self.assertEqual(
            tuple(sorted(ComponentFunctionId, key=lambda item: item.value)),
            tuple(item.function_id for item in door.applicability_decisions),
        )
        self.assertEqual(
            (ComponentFunctionId.PROVIDE_ACCESS,),
            door.required_functions,
        )
        self.assertEqual((), door.unknown_functions)
        self.assertEqual(
            set(ComponentFunctionId) - {ComponentFunctionId.PROVIDE_ACCESS},
            set(door.not_applicable_functions),
        )

        with self.assertRaisesRegex(ComponentFunctionError, "exactly classify"):
            replace(
                door,
                applicability_decisions=door.applicability_decisions[:-1],
            )
        with self.assertRaisesRegex(ComponentFunctionError, "more than once"):
            replace(
                door,
                applicability_decisions=(
                    *door.applicability_decisions,
                    door.applicability_decisions[0],
                ),
            )

        wrong_claim = claim(
            ComponentFunctionId.BE_SUPPORTED,
            door.component_ref,
            "design-component:building",
        )
        with self.assertRaisesRegex(ComponentFunctionError, "only to REQUIRED"):
            replace(door, claims=(wrong_claim,))

    def test_applicability_evidence_failures_are_retained_in_ledger(self) -> None:
        subjects = inventory()
        building = contract(
            subjects,
            "building",
            ComponentFunctionId.BE_SUPPORTED,
        )
        decisions = tuple(
            replace(item, evidence_refs=())
            if item.function_id is ComponentFunctionId.BE_SUPPORTED
            else replace(item, authority_refs=())
            if item.function_id is ComponentFunctionId.BE_HOSTED
            else replace(
                item,
                contradiction_refs=("evidence:applicability-conflict",),
            )
            if item.function_id is ComponentFunctionId.ENCLOSE_SPACE
            else item
            for item in building.applicability_decisions
        )
        bad = replace(building, applicability_decisions=decisions)

        ledger = compile_component_function_ledger(
            ledger_id="bad-applicability-evidence",
            inventory=subjects,
            contracts=(bad,),
        )
        row = ledger.rows[0]

        self.assertIs(row.status, FunctionStatus.FAIL)
        self.assertEqual(
            {
                "APPLICABILITY_AUTHORITY_INCOMPLETE",
                "APPLICABILITY_CONTRADICTORY_EVIDENCE",
                "APPLICABILITY_EVIDENCE_INCOMPLETE",
            },
            set(row.failure_codes),
        )
        self.assertEqual(decisions, row.applicability_decisions)
        self.assertEqual(
            ledger,
            ComponentFunctionLedger.from_dict(ledger.to_dict()),
        )

    def test_unknown_applicability_is_open_and_needs_no_claim(self) -> None:
        subjects = inventory()
        door = contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS)
        unknown_function = ComponentFunctionId.BE_HOSTED
        decisions = tuple(
            replace(
                item,
                applicability=FunctionApplicability.UNKNOWN,
                evidence_refs=(),
                authority_refs=(),
            )
            if item.function_id is unknown_function
            else item
            for item in door.applicability_decisions
        )
        open_contract = replace(door, applicability_decisions=decisions)

        ledger = compile_component_function_ledger(
            ledger_id="unknown-applicability",
            inventory=subjects,
            contracts=(open_contract,),
        )
        row = next(
            item for item in ledger.rows if item.component_ref == door.component_ref
        )

        self.assertIs(row.status, FunctionStatus.OPEN)
        self.assertEqual((unknown_function,), row.unknown_functions)
        self.assertEqual((), row.failure_codes)
        self.assertEqual(decisions, row.applicability_decisions)

    def test_inventory_is_exhaustive_denominator_and_diagnostics_are_mechanical(self) -> None:
        subjects = inventory()
        ledger = compile_component_function_ledger(
            ledger_id="stage-2-function-ledger",
            inventory=subjects,
            contracts=(
                contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS),
                contract(subjects, "wall", ComponentFunctionId.SUPPORT_OTHERS),
            ),
        )
        self.assertEqual(
            tuple(item.identity_ref for item in subjects.entries),
            tuple(row.component_ref for row in ledger.rows),
        )
        self.assertEqual(
            [FunctionStatus.FUNCTION_ORPHAN, FunctionStatus.SATISFIED, FunctionStatus.SATISFIED],
            [row.status for row in ledger.rows],
        )
        diagnostics = compile_function_diagnostic_entries(
            ledger=ledger,
            inventory=subjects,
            stage_claim_ref="stage-claim:stage-2-hold",
        )
        self.assertEqual("#FF00FF", diagnostics[0].diagnostic_color)
        self.assertEqual("NONE", diagnostics[0].function_contract_ref)
        self.assertIsNone(diagnostics[1].diagnostic_color)
        self.assertTrue(all(item.function_ledger_ref == ledger.ledger_ref for item in diagnostics))

    def test_unknown_is_open_but_incomplete_or_contradictory_evidence_is_fail(self) -> None:
        subjects = inventory()
        open_contract = contract(
            subjects,
            "building",
            ComponentFunctionId.BE_SUPPORTED,
            status=FunctionClaimStatus.UNKNOWN,
        )
        bad_evidence = replace(
            contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS),
            claims=(
                claim(
                    ComponentFunctionId.PROVIDE_ACCESS,
                    "design-component:door",
                    "design-component:building",
                    evidence=(),
                ),
            ),
        )
        contradiction = replace(
            contract(subjects, "wall", ComponentFunctionId.SUPPORT_OTHERS),
            claims=(
                claim(
                    ComponentFunctionId.SUPPORT_OTHERS,
                    "design-component:wall",
                    "design-component:building",
                    contradiction=("evidence:conflict-1",),
                ),
            ),
        )
        ledger = compile_component_function_ledger(
            ledger_id="status-ledger",
            inventory=subjects,
            contracts=(open_contract, bad_evidence, contradiction),
        )
        self.assertEqual(
            [FunctionStatus.OPEN, FunctionStatus.FAIL, FunctionStatus.FAIL],
            [row.status for row in ledger.rows],
        )
        self.assertEqual(FunctionEvaluationStatus.UNKNOWN, ledger.rows[0].evaluations[0].status)
        self.assertIn("EVIDENCE_INCOMPLETE", ledger.rows[1].evaluations[0].failure_codes)
        self.assertIn("CONTRADICTORY_EVIDENCE", ledger.rows[2].evaluations[0].failure_codes)

    def test_missing_obligation_and_bad_endpoint_or_maturity_fail(self) -> None:
        subjects = inventory()
        missing = contract(
            subjects,
            "building",
            ComponentFunctionId.BE_SUPPORTED,
            claims=(),
        )
        original = claim(
            ComponentFunctionId.PROVIDE_ACCESS,
            "design-component:door",
            "design-component:building",
        )
        wrong_endpoint = replace(
            original,
            endpoint_bindings=tuple(
                replace(binding, endpoint_refs=("design-component:wall",))
                if binding.role == "access_provider"
                else binding
                for binding in original.endpoint_bindings
            ),
            maturity=FunctionMaturity.DECLARED,
        )
        ledger = compile_component_function_ledger(
            ledger_id="incomplete-ledger",
            inventory=subjects,
            contracts=(
                missing,
                contract(
                    subjects,
                    "door",
                    ComponentFunctionId.PROVIDE_ACCESS,
                    claims=(wrong_endpoint,),
                ),
            ),
        )
        self.assertEqual(FunctionEvaluationStatus.MISSING, ledger.rows[0].evaluations[0].status)
        self.assertEqual(FunctionStatus.FAIL, ledger.rows[1].status)
        self.assertEqual(
            {"COMPONENT_ENDPOINT_MISMATCH", "MATURITY_INCOMPLETE"},
            set(ledger.rows[1].evaluations[0].failure_codes),
        )

    def test_duplicate_foreign_stale_and_cross_stage_contracts_are_rejected(self) -> None:
        subjects = inventory()
        door = contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS)
        with self.assertRaisesRegex(ComponentFunctionError, "duplicate component"):
            compile_component_function_ledger(
                ledger_id="duplicate",
                inventory=subjects,
                contracts=(door, replace(door, contract_id="other-id")),
            )
        with self.assertRaisesRegex(ComponentFunctionError, "stale"):
            compile_component_function_ledger(
                ledger_id="stale-inventory",
                inventory=subjects,
                contracts=(replace(door, subject_inventory_digest=SHA_A),),
            )
        with self.assertRaisesRegex(ComponentFunctionError, "component digest is stale"):
            compile_component_function_ledger(
                ledger_id="stale-component",
                inventory=subjects,
                contracts=(replace(door, component_digest=SHA_A),),
            )
        with self.assertRaisesRegex(ComponentFunctionError, "branch or stage"):
            compile_component_function_ledger(
                ledger_id="cross-stage",
                inventory=subjects,
                contracts=(replace(door, stage_id="stage-3"),),
            )
        with self.assertRaisesRegex(ComponentFunctionError, "branch or stage"):
            compile_component_function_ledger(
                ledger_id="cross-branch",
                inventory=subjects,
                contracts=(replace(door, branch=branch(epoch=1)),),
            )
        with self.assertRaisesRegex(ComponentFunctionError, "foreign"):
            compile_component_function_ledger(
                ledger_id="foreign",
                inventory=subjects,
                contracts=(replace(door, component_ref="design-component:window"),),
            )

    def test_all_components_not_applicable_is_forbidden(self) -> None:
        subjects = inventory()
        contracts = []
        for entry in subjects.entries:
            contracts.append(
                ComponentFunctionContract(
                    contract_id=f"na-{entry.component_id}",
                    branch=subjects.branch,
                    stage_id=subjects.stage_id,
                    subject_inventory_digest=subjects.inventory_digest,
                    component_ref=entry.identity_ref,
                    component_digest=entry.component_digest,
                    applicability_decisions=applicability_decisions(),
                    claims=(),
                )
            )
        with self.assertRaisesRegex(ComponentFunctionError, "all components"):
            compile_component_function_ledger(
                ledger_id="all-na", inventory=subjects, contracts=tuple(contracts)
            )

        unknown_contracts = tuple(
            ComponentFunctionContract(
                contract_id=f"unknown-{entry.component_id}",
                branch=subjects.branch,
                stage_id=subjects.stage_id,
                subject_inventory_digest=subjects.inventory_digest,
                component_ref=entry.identity_ref,
                component_digest=entry.component_digest,
                applicability_decisions=applicability_decisions(
                    unknown=tuple(ComponentFunctionId)
                ),
                claims=(),
            )
            for entry in subjects.entries
        )
        unknown_ledger = compile_component_function_ledger(
            ledger_id="all-unknown",
            inventory=subjects,
            contracts=unknown_contracts,
        )
        self.assertTrue(
            all(row.status is FunctionStatus.OPEN for row in unknown_ledger.rows)
        )

    def test_round_trip_digest_and_authority_drift_are_strict(self) -> None:
        subjects = inventory()
        door = contract(subjects, "door", ComponentFunctionId.PROVIDE_ACCESS)
        self.assertEqual(door, ComponentFunctionContract.from_dict(door.to_dict()))
        ledger = compile_component_function_ledger(
            ledger_id="round-trip", inventory=subjects, contracts=(door,)
        )
        restored = ComponentFunctionLedger.from_dict(ledger.to_dict())
        self.assertEqual(ledger, restored)
        self.assertEqual(ledger.ledger_digest, restored.ledger_digest)

        digest_drift = copy.deepcopy(ledger.to_dict())
        digest_drift["ledger_id"] = "round-trip-drifted"
        with self.assertRaisesRegex(ComponentFunctionError, "digest changed"):
            ComponentFunctionLedger.from_dict(digest_drift)

    def test_semantic_kind_and_name_never_resolve_orphan(self) -> None:
        subjects = inventory()
        ledger = compile_component_function_ledger(
            ledger_id="no-inference", inventory=subjects, contracts=()
        )
        self.assertTrue(
            all(row.status is FunctionStatus.FUNCTION_ORPHAN for row in ledger.rows)
        )


if __name__ == "__main__":
    unittest.main()
