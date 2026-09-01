from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from archflow.research.index import (
    compile_branch_decision_context,
)
from archflow.capabilities.declaration import (
    DeclarationField,
    DeclarationKind,
    DeclarationQuadrant,
    GeometryCheck,
    StageDeclarationContract,
)
from archflow.capabilities.design_development import (
    initialize_developed_design,
)
from archflow.state import compile_selected_branch_handoff
from archflow.state.stage_convergence import (
    StageConvergenceOutcome,
    StageConvergencePotential,
    StageConvergenceReceipt,
    StageTransitionKind,
)
from tests.test_branch_conditioned_rag import (
    ACTIVE_DECISIONS,
    _covered_branch_index,
    _index_ref,
    _p079_inputs,
    _selected_scope,
)
from tests.test_design_development import _obligations
from tools import run_pantheon_reconstruction as P


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _payload_digest(payload) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contract() -> StageDeclarationContract:
    return StageDeclarationContract(
        stage="schematic-design",
        fields=(
            DeclarationField(
                field_id="column-order",
                quadrant=DeclarationQuadrant.STRUCTURE,
                kind=DeclarationKind.NUMBER,
                unit=None,
                minimum=0.0,
                maximum=1.0,
                geometry_check=GeometryCheck.NONE,
                source_refs=("project:evidence/column-order",),
                statement="Adopt the evidenced column order.",
            ),
            DeclarationField(
                field_id="roof-form",
                quadrant=DeclarationQuadrant.STRUCTURE,
                kind=DeclarationKind.NUMBER,
                unit=None,
                minimum=0.0,
                maximum=1.0,
                geometry_check=GeometryCheck.NONE,
                source_refs=("project:evidence/roof-form",),
                statement="Adopt the evidenced roof form.",
            ),
        ),
        tolerance_ratio=0.08,
    )


def _zero_potential() -> StageConvergencePotential:
    return StageConvergencePotential(
        hard_gate_failure_refs=(),
        conflict_refs=(),
        tolerance_failure_refs=(),
        missing_mandatory_obligation_refs=(),
        blocked_mandatory_obligation_refs=(),
        open_mandatory_obligation_refs=(),
        invalidated_refs=(),
        revalidation_refs=(),
    )


def _closure_inputs():
    _, operational, _, _, selected, scope = _selected_scope()
    handoff = compile_selected_branch_handoff(
        selected,
        expected_portfolio_digest=selected.portfolio_digest,
        expected_revision_digest=selected.selected_branch.head.revision_digest,
    )
    design_state = initialize_developed_design(
        handoff,
        obligations=_obligations(handoff),
        assumption_refs=("project:assumption/p081-stage-test",),
    )
    index = _covered_branch_index(scope)
    inputs = _p079_inputs(scope, ACTIVE_DECISIONS)
    context = compile_branch_decision_context(
        index,
        index_record=_index_ref(index),
        decision_refs=ACTIVE_DECISIONS,
        universe=inputs[0],
        policy=inputs[1],
        closure=inputs[2],
        sufficiency=inputs[3],
        frontier=inputs[4],
    )
    receipt = StageConvergenceReceipt(
        receipt_id="p081-pantheon-stage-ready",
        outcome=StageConvergenceOutcome.PROGRESS,
        request_id="p081-pantheon-stage-close",
        stage="schematic-design",
        transition_kind=StageTransitionKind.RESOLVE,
        branch=operational.branch,
        policy_digest=_hash("p081-pantheon-stage-policy"),
        parent_state_digest=_hash("p081-pantheon-parent"),
        child_state_digest=operational.state_digest,
        parent_sufficient_digest=_hash("p081-pantheon-parent-sufficient"),
        child_sufficient_digest=operational.sufficient_digest,
        parent_evidence_digest=_hash("p081-pantheon-parent-evidence"),
        child_evidence_digest=_hash("p081-pantheon-child-evidence"),
        potential_before=_zero_potential(),
        potential_after=_zero_potential(),
        protected_refs=ACTIVE_DECISIONS,
        changed_protected_refs=(),
        mandatory_obligation_ids=(),
        added_mandatory_obligation_ids=(),
        dependency_closure=(),
        authorization_ref=None,
        reason_codes=(),
    )
    return (
        scope.run,
        design_state,
        P.PantheonStageClosure(context, receipt, operational),
    )


def _parameter(operation, name: str):
    return json.loads(
        next(item.value_json for item in operation.parameters if item.name == name)
    )


def _geometry(stage: int):
    _, design_state, _ = _closure_inputs()
    return SimpleNamespace(
        proposal=P._pantheon_geometry(design_state, stage=stage)
    )


class PantheonBranchStageClosureTests(unittest.TestCase):
    def test_exact_complete_context_and_locked_hard_decisions_pass(self):
        run, design_state, closure = _closure_inputs()

        required = P.require_pantheon_stage_closure(
            stage=1,
            run=run,
            design_state=design_state,
            contract=_contract(),
            closure=closure,
        )

        self.assertEqual(ACTIVE_DECISIONS, required)

    def test_soft_score_cannot_replace_an_unlocked_hard_decision(self):
        run, design_state, closure = _closure_inputs()
        receipt = replace(
            closure.convergence_receipt,
            protected_refs=("declaration:column-order",),
        )

        with self.assertRaisesRegex(
            P.PantheonStageClosureError,
            "not protected",
        ):
            P.require_pantheon_stage_closure(
                stage=1,
                run=run,
                design_state=design_state,
                contract=_contract(),
                closure=replace(closure, convergence_receipt=receipt),
            )

    def test_stale_or_open_convergence_receipt_fails_closed(self):
        run, design_state, closure = _closure_inputs()
        stale = replace(
            closure.convergence_receipt,
            child_state_digest=_hash("another-operational-state"),
        )
        with self.assertRaisesRegex(
            P.PantheonStageClosureError,
            "stale",
        ):
            P.require_pantheon_stage_closure(
                stage=1,
                run=run,
                design_state=design_state,
                contract=_contract(),
                closure=replace(closure, convergence_receipt=stale),
            )

        open_receipt = replace(
            closure.convergence_receipt,
            potential_after=replace(
                _zero_potential(),
                hard_gate_failure_refs=("evaluation:code-gate",),
            ),
        )
        with self.assertRaisesRegex(
            P.PantheonStageClosureError,
            "rejected, open, expanding",
        ):
            P.require_pantheon_stage_closure(
                stage=1,
                run=run,
                design_state=design_state,
                contract=_contract(),
                closure=replace(
                    closure,
                    convergence_receipt=open_receipt,
                ),
            )

    def test_runner_writes_nothing_when_later_stage_has_no_resolver(self):
        run, design_state, _ = _closure_inputs()
        contract = _contract()
        repository = Mock()
        original_persist = Mock()
        contracts = P.stage_contracts(
            {
                "walls": "project:evidence/walls",
                "orders": "project:evidence/orders",
                "front": "project:evidence/front",
                "typology": "project:evidence/typology",
                "junction": "project:evidence/junction",
            }
        )
        contracts[1] = (
            contract,
            {"column-order": 0.5, "roof-form": 0.5},
        )
        profile = replace(
            P.DEFAULT_RUNNER_PROFILE,
            project_id=run.project_id,
            run_id=run.run_id,
        )
        hooks = replace(
            P.DEFAULT_RUNNER_HOOKS,
            persist=lambda _context, *args, **kwargs: original_persist(
                *args, **kwargs
            ),
        )
        runner_context = P.create_runner_context(
            profile=profile,
            hooks=hooks,
            contracts=contracts,
        )

        with self.assertRaisesRegex(
            P.PantheonStageClosureError,
            "requires a configured",
        ):
            P._gated_persist_stage(
                repository,
                run=run,
                state=design_state,
                program=object(),
                stage=1,
                runner_context=runner_context,
            )

        repository.put_json.assert_not_called()
        original_persist.assert_not_called()

    def test_runner_persists_inspectable_binding_before_base_archive(self):
        run, design_state, closure = _closure_inputs()
        contract = _contract()
        repository = Mock()
        original_persist = Mock(return_value={"archive": "accepted"})
        contracts = P.stage_contracts(
            {
                "walls": "project:evidence/walls",
                "orders": "project:evidence/orders",
                "front": "project:evidence/front",
                "typology": "project:evidence/typology",
                "junction": "project:evidence/junction",
            }
        )
        contracts[1] = (
            contract,
            {"column-order": 0.5, "roof-form": 0.5},
        )
        profile = replace(
            P.DEFAULT_RUNNER_PROFILE,
            project_id=run.project_id,
            run_id=run.run_id,
        )
        hooks = replace(
            P.DEFAULT_RUNNER_HOOKS,
            persist=lambda _context, *args, **kwargs: original_persist(
                *args, **kwargs
            ),
        )
        runner_context = P.create_runner_context(
            profile=profile,
            hooks=hooks,
            contracts=contracts,
            stage_closure_resolver=Mock(return_value=closure),
        )
        result = P._gated_persist_stage(
            repository,
            run=run,
            state=design_state,
            program=object(),
            stage=1,
            runner_context=runner_context,
        )

        self.assertEqual({"archive": "accepted"}, result)
        payload = repository.put_json.call_args.kwargs["payload"]
        self.assertEqual("P069StageDeclarations@2", payload["schema"])
        self.assertEqual(
            list(ACTIVE_DECISIONS),
            payload["required_hard_decision_refs"],
        )
        self.assertEqual(
            "PantheonStageClosure@1",
            payload["branch_stage_closure"]["schema"],
        )
        original_persist.assert_called_once()


class PantheonCandidateGeometrySelfCheckTests(unittest.TestCase):
    """Project-local checks over the neutral proposal, without Rhino."""

    def test_profile_injection_preserves_frozen_stage_outputs(self):
        _, design_state, _ = _closure_inputs()
        runner_context = P.create_runner_context()
        expected = (
            (
                "39502459053020bb4586fd995006a74948ea55e24b550c64bf898551be8bd8f6",
                "443fc3ae7f27bbbf7ab1f86df1f012e19956a4bf57fdacfa9e2441eb9108ad74",
                "76f370d4a3e977009feb49739b41c0978cbc07b4574fd58c1278954f0272f19d",
            ),
            (
                "5cd94736437ef62ead85f42c8953fde9cf5047a92450fec4970d875c88b651f4",
                "b70df27a7a9aa27525ce8fe8851183f1024a41baf3d7e7ead97dae9ab309d925",
                "4fa795074005760fb4d226a42b4f1dd812545078a7e8685abc54b744d1d31683",
            ),
            (
                "96879252de22a5aea06535a428177d0831a0596e3e1ea40db3c4986464f049d6",
                "74f7ee15028879fe68d6e98227a01e4d79660606644871603e393f833127d653",
                "01b71fe74e2573a6ca51b9765ede627b119ab9f22f35f0a8b5f98773467b4e4a",
            ),
            (
                "5df2659b0fc6972c795e49d18468980e2e1fea0c5727c529b03f04bed3e6a34a",
                "662b5f4e5d888b1c5634e0c5dcc11c54fb53289b798f71d1f0977f7d900d7f5e",
                "f4dc3c023e68cddaea975cb96512c4eb93377a98f7e351d5aac3b3cd7368ec77",
            ),
        )

        for stage, digests in enumerate(expected):
            proposal = P._pantheon_geometry(
                design_state,
                stage=stage,
                profile=runner_context.profile,
            )
            program = SimpleNamespace(proposal=proposal)
            with self.subTest(stage=stage):
                self.assertEqual(digests[0], proposal.proposal_digest)
                self.assertEqual(
                    digests[1],
                    _payload_digest(P._measure_program_structure(program)),
                )
                self.assertEqual(
                    digests[2],
                    _payload_digest(
                        P._realized_declaration_values(
                            program,
                            stage,
                            runner_context=runner_context,
                        )
                    ),
                )

    def test_non_default_profile_center_translates_stage_2_and_3_geometry(self):
        _, design_state, _ = _closure_inputs()
        delta_x, delta_z = 7.0, -5.0
        shifted_profile = replace(
            P.DEFAULT_RUNNER_PROFILE,
            center_x=P.DEFAULT_RUNNER_PROFILE.center_x + delta_x,
            center_z=P.DEFAULT_RUNNER_PROFILE.center_z + delta_z,
        )

        def points(stage: int, profile, op_id: str, parameter: str):
            proposal = P._pantheon_geometry(
                design_state,
                stage=stage,
                profile=profile,
            )
            operation = next(
                item for item in proposal.operations if item.op_id == op_id
            )
            return proposal, _parameter(operation, parameter)

        cases = (
            (2, "niche-cutter-ne", "profile"),
            (3, "coffer-cutter-r0-c0", "profiles"),
        )
        shifted_stage_3 = None
        for stage, op_id, parameter in cases:
            _, original = points(
                stage,
                P.DEFAULT_RUNNER_PROFILE,
                op_id,
                parameter,
            )
            proposal, shifted = points(
                stage,
                shifted_profile,
                op_id,
                parameter,
            )
            if stage == 3:
                shifted_stage_3 = proposal
            with self.subTest(stage=stage, op_id=op_id):
                for before, after in zip(original, shifted):
                    self.assertAlmostEqual(delta_x, after[0] - before[0])
                    self.assertAlmostEqual(0.0, after[1] - before[1])
                    self.assertAlmostEqual(delta_z, after[2] - before[2])

        shifted_program = SimpleNamespace(
            proposal=shifted_stage_3,
            program_digest=_hash("shifted-stage-3"),
        )
        detail = P._detail_enrichment_plan(
            shifted_program,
            profile=shifted_profile,
        )
        self.assertEqual(
            shifted_profile.center_x,
            detail["massing_locks"]["axis_center_x_m"],
        )

    def test_rhino_workspace_declares_metric_units_before_geometry(self):
        setup = "\n".join(P._rhino_metric_document_setup_lines())

        self.assertIn(
            "AdjustModelUnitSystem(Rhino.UnitSystem.Meters, False)",
            setup,
        )
        self.assertIn(
            "_doc.ModelUnitSystem != Rhino.UnitSystem.Meters",
            setup,
        )
        self.assertIn("Rhino model units are not meters", setup)

    def test_headless_3dm_gate_rejects_unit_or_object_count_drift(self):
        model = SimpleNamespace(
            file_sha256="a" * 64,
            file_bytes=2048,
            three_dm_version=8,
            archive_version=80,
            units={"name": "Inches", "code": 8},
            object_count=58,
            top_level_object_count=58,
            layers=(),
            instance_definitions=(),
            instance_references=(),
            aggregate_bbox={"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
        )
        with patch(
            "archflow.adapters.three_dm_inspector.inspect_three_dm",
            return_value=model,
        ):
            result = P._headless_three_dm_gate(
                P.Path("candidate.3dm"),
                {"model_unit_system": "Meters"},
                expected_object_count=59,
            )

        self.assertFalse(result["passed"])
        self.assertEqual(
            [
                "saved 3dm unit system is not Meters: Inches",
                "saved 3dm object count diverged: expected 59, got 58",
            ],
            result["issues"],
        )
        self.assertFalse(result["stage_acceptance_authority"])
        self.assertFalse(result["canonical_write_authority"])

    def test_detail_plan_freezes_massing_and_corrects_the_column_order_proxy(self):
        program = _geometry(3)
        program.program_digest = _hash("stage-3-detail-predecessor")

        plan = P._detail_enrichment_plan(program)

        self.assertEqual(
            program.program_digest,
            plan["predecessor_program_digest"],
        )
        self.assertEqual(
            [8, 4, 4],
            plan["massing_locks"]["column_row_distribution"],
        )
        self.assertEqual(
            16,
            len(plan["massing_locks"]["column_centres"]),
        )
        self.assertEqual([], plan["massing_locks"]["massing_mutations"])
        order = plan["detail_decisions"][0]["implementation"]
        self.assertEqual("smooth-entasis-no-fluting", order["shaft_surface"])
        self.assertAlmostEqual(
            P.DETAIL_TOTAL_COLUMN_H,
            order["base_height_m"]
            + order["shaft_height_m"]
            + order["capital_height_m"],
        )
        self.assertEqual(
            P.DETAIL_INSCRIPTION,
            plan["detail_decisions"][1]["implementation"]["text"],
        )
        self.assertFalse(plan["asset_candidates"][0]["selected"])
        self.assertTrue(plan["asset_candidates"][1]["selected"])

    def test_detail_rhino_overlay_is_offline_and_keeps_external_asset_hidden(self):
        program = _geometry(3)
        program.program_digest = _hash("stage-3-detail-script")
        plan = P._detail_enrichment_plan(program)

        script = P._detail_rhino_overlay_script(
            plan,
            external_asset_path=None,
        )

        compile(script, "<pantheon-detail-overlay>", "exec")
        self.assertIn("rs.AddText", script)
        self.assertIn("rs.AddBlock", script)
        self.assertIn("rs.InsertBlock", script)
        self.assertIn("Rhino.FileIO.FileStl.Read", script)
        self.assertIn(
            "rs.LayerVisible(_detail_layers['external'][0], False)",
            script,
        )
        self.assertNotIn("urllib", script)
        self.assertNotIn("requests.", script)

    def test_cad_summary_separates_strict_oracle_from_meter_grid_gate(self):
        from archflow.adapters.cad_program import (
            expected_object_bounds,
            expected_object_semantics,
        )

        proposal = _geometry(3).proposal
        pending = list(proposal.operations)
        produced: set[str] = set()
        order: list[str] = []
        while pending:
            ready = [
                item
                for item in pending
                if set(item.input_object_ids).issubset(produced)
            ]
            self.assertTrue(ready)
            for item in ready:
                order.append(item.op_id)
                produced.update(item.output_object_ids)
                pending.remove(item)
        program = SimpleNamespace(
            proposal=proposal,
            operation_order=tuple(order),
        )
        measures = json.loads(json.dumps(expected_object_bounds(program)))
        measures["transition-block-object"]["bbox_max"][2] -= 0.4374

        summary = P._candidate_cad_verification_summary(
            program,
            measures=measures,
            semantics=expected_object_semantics(program),
        )

        self.assertEqual("diverged", summary["strict"]["status"])
        self.assertEqual("equivalent", summary["contract"]["status"])
        self.assertEqual("verified", summary["semantics"]["status"])
        self.assertEqual(
            "boolean-difference-base-envelope-conservative",
            summary["strict_difference_classification"]["code"],
        )

    def test_stage1_columns_are_symmetric_eight_four_four_revolves(self):
        program = _geometry(1)
        metrics = P._measure_program_structure(program)

        self.assertEqual([8, 4, 4], metrics["column_row_distribution"])
        self.assertEqual(16, sum(metrics["column_row_distribution"]))
        self.assertIn("revolve", metrics["column_operation_kinds"])
        self.assertNotIn("solid", metrics["column_operation_kinds"])
        self.assertEqual((), P._candidate_structure_issues(1, metrics))

        shaft_rows: dict[int, list] = {}
        for operation in program.proposal.operations:
            if not operation.op_id.startswith("column-shaft-r"):
                continue
            row = int(operation.op_id.split("-r", 1)[1].split("-", 1)[0])
            shaft_rows.setdefault(row, []).append(operation)

        self.assertEqual([8, 4, 4], [len(shaft_rows[key]) for key in sorted(shaft_rows)])
        self.assertTrue(
            all(
                item.kind.value == "revolve"
                for row in shaft_rows.values()
                for item in row
            )
        )
        self.assertEqual(
            16,
            len(
                {
                    output
                    for row in shaft_rows.values()
                    for operation in row
                    for output in operation.output_object_ids
                }
            ),
        )
        for row in shaft_rows.values():
            x_coordinates = sorted(
                float(_parameter(item, "axis_start")[0]) for item in row
            )
            for left, right in zip(x_coordinates, reversed(x_coordinates)):
                self.assertAlmostEqual(
                    2.0 * P.CENTER_X,
                    left + right,
                    places=6,
                )

    def test_stage2_wall_voids_are_cut_and_apse_is_semantically_distinct(self):
        program = _geometry(2)
        metrics = P._measure_program_structure(program)

        self.assertEqual(3, metrics["exedra_cutter_count"])
        self.assertEqual(4, metrics["diagonal_niche_cutter_count"])
        self.assertEqual(
            7,
            metrics["exedra_cutter_count"]
            + metrics["diagonal_niche_cutter_count"],
        )
        self.assertEqual(8, metrics["aedicula_count"])
        self.assertTrue(metrics["apse_binding_present"])
        self.assertEqual((), P._candidate_structure_issues(2, metrics))

        drum_boolean = next(
            item
            for item in program.proposal.operations
            if item.op_id == "drum-wall"
        )
        self.assertEqual("boolean_difference", drum_boolean.kind.value)
        self.assertTrue(
            set(metrics["drum_cutter_ids"]).issubset(
                set(drum_boolean.input_object_ids)
            )
        )
        self.assertTrue(
            any(
                item.component_id == "apse"
                for item in program.proposal.semantic_bindings
            )
        )

    def test_stage3_has_real_coffer_cutters_and_no_invented_statuary(self):
        program = _geometry(3)
        metrics = P._measure_program_structure(program)

        self.assertEqual(5, metrics["coffer_ring_count"])
        self.assertEqual([28] * 5, metrics["coffers_per_ring"])
        self.assertEqual(140, metrics["coffer_cutter_count"])
        self.assertFalse(metrics["statuary_present"])
        self.assertEqual((), P._candidate_structure_issues(3, metrics))

        dome_boolean = next(
            item
            for item in program.proposal.operations
            if item.op_id == "dome-shell"
        )
        coffer_cutters = {
            object_id
            for object_id in metrics["dome_cutter_ids"]
            if "coffer" in object_id
        }
        self.assertEqual(140, len(coffer_cutters))
        self.assertTrue(coffer_cutters.issubset(dome_boolean.input_object_ids))
        self.assertFalse(
            any(
                "statuary" in item.component_id
                for item in program.proposal.semantic_bindings
            )
        )

    def test_every_declaration_is_measured_from_its_stage_program(self):
        refs = {
            "walls": "project:evidence/walls",
            "orders": "project:evidence/orders",
            "front": "project:evidence/front",
            "typology": "project:evidence/typology",
            "junction": "project:evidence/junction",
        }
        contracts = P.stage_contracts(refs)
        mismatches = []
        runner_context = P.create_runner_context(contracts=contracts)
        for stage in range(4):
            contract, declared = contracts[stage]
            program = _geometry(stage)
            realized = P._realized_declaration_values(
                program,
                stage,
                runner_context=runner_context,
            )
            field_ids = {field.field_id for field in contract.fields}
            with self.subTest(stage=stage):
                self.assertEqual(field_ids, set(realized))
                self.assertEqual(field_ids, set(declared))
                for field_id in sorted(field_ids):
                    if round(
                        float(declared[field_id])
                        - float(realized[field_id]),
                        6,
                    ):
                        mismatches.append(
                            (
                                stage,
                                field_id,
                                declared[field_id],
                                realized[field_id],
                            )
                        )

        self.assertEqual([], mismatches)

        stage1 = P._measure_program_structure(_geometry(1))
        stage2 = P._measure_program_structure(_geometry(2))
        stage3 = P._measure_program_structure(_geometry(3))
        self.assertEqual([8, 4, 4], stage1["column_row_distribution"])
        self.assertEqual(16, sum(stage1["column_row_distribution"]))
        self.assertEqual(
            7,
            stage2["exedra_cutter_count"]
            + stage2["diagonal_niche_cutter_count"],
        )
        self.assertEqual(5, stage3["coffer_ring_count"])
        self.assertEqual([28] * 5, stage3["coffers_per_ring"])


if __name__ == "__main__":
    unittest.main()
