from __future__ import annotations

import hashlib
import inspect
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
                "99ba494a951e3c73146afc1e355dcb1eaac11fccb72b4c02029d67a61bda3a7d",
                "443fc3ae7f27bbbf7ab1f86df1f012e19956a4bf57fdacfa9e2441eb9108ad74",
                "76f370d4a3e977009feb49739b41c0978cbc07b4574fd58c1278954f0272f19d",
            ),
            (
                "d78d08ba20ea435158c73c618dca6e34dd9317bc4c3c23e589852826786d43e0",
                "b70df27a7a9aa27525ce8fe8851183f1024a41baf3d7e7ead97dae9ab309d925",
                "4fa795074005760fb4d226a42b4f1dd812545078a7e8685abc54b744d1d31683",
            ),
            (
                "fccd894082579dac5ae9475c2f7be1c879a9dc881931bd9de8ae9f9746a5ff67",
                "74f7ee15028879fe68d6e98227a01e4d79660606644871603e393f833127d653",
                "01b71fe74e2573a6ca51b9765ede627b119ab9f22f35f0a8b5f98773467b4e4a",
            ),
            (
                "99b2a6677ed1f297b9ddab5d88afa141129ab37c60997b0428cf942b9121f199",
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
                {
                    "model_unit_system": "Meters",
                    "source_vertical_axis": (
                        P._PANTHEON_COORDINATE_TRANSFORM[
                            "source_vertical_axis"
                        ]
                    ),
                    "rhino_vertical_axis": (
                        P._PANTHEON_COORDINATE_TRANSFORM[
                            "rhino_vertical_axis"
                        ]
                    ),
                    "coordinate_transform": (
                        P._PANTHEON_COORDINATE_TRANSFORM["mapping"]
                    ),
                },
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
        from archflow.adapters.cad_program import expected_object_semantics

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
        measures = json.loads(
            json.dumps(P._candidate_expected_object_bounds(program))
        )
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


    def test_headless_3dm_gate_rejects_axis_transform_drift(self):
        model = SimpleNamespace(
            file_sha256="a" * 64,
            file_bytes=2048,
            three_dm_version=8,
            archive_version=80,
            units={"name": "Meters", "code": 4},
            object_count=59,
            top_level_object_count=59,
            layers=(),
            instance_definitions=(),
            instance_references=(),
            aggregate_bbox={
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 1.0],
            },
        )
        with patch(
            "archflow.adapters.three_dm_inspector.inspect_three_dm",
            return_value=model,
        ):
            result = P._headless_three_dm_gate(
                P.Path("candidate.3dm"),
                {
                    "model_unit_system": "Meters",
                    "source_vertical_axis": "Y",
                    "rhino_vertical_axis": "Y",
                    "coordinate_transform": "identity",
                },
                expected_object_count=59,
            )

        self.assertFalse(result["passed"])
        self.assertEqual(2, len(result["issues"]))
        self.assertTrue(
            all("coordinate" in issue for issue in result["issues"])
        )

    def test_headless_3dm_gate_reads_saved_column_as_z_up_witness(self):
        witness_name = "column-shaft-r0-c0-object"
        model = SimpleNamespace(
            file_sha256="a" * 64,
            file_bytes=2048,
            three_dm_version=8,
            archive_version=80,
            units={"name": "Meters", "code": 4},
            object_count=59,
            top_level_object_count=59,
            layers=(),
            instance_definitions=(),
            instance_references=(),
            aggregate_bbox={
                "min": [0.0, 0.0, 0.0],
                "max": [56.0, 77.0, 46.1],
            },
            named_object_bboxes=(
                {
                    "name": witness_name,
                    "bbox": {
                        # Rhino's normal loft overshoots the tapered endpoint
                        # radius by 15 mm; the Z-axis endpoints remain exact.
                        "min": [11.835, 3.235, 2.0],
                        "max": [13.365, 4.765, 13.9],
                    },
                },
                {
                    "name": "bronze-door-left",
                    "bbox": {
                        "min": [25.775, 18.86, 1.0],
                        "max": [27.97, 18.98, 8.53],
                    },
                },
                {
                    "name": "bronze-door-right",
                    "bbox": {
                        "min": [28.03, 18.86, 1.0],
                        "max": [30.225, 18.98, 8.53],
                    },
                },
            ),
        )
        status = {
            "model_unit_system": "Meters",
            "source_vertical_axis": "Y",
            "rhino_vertical_axis": "Z",
            "coordinate_transform": "(x,y,z)->(x,z,y)",
        }
        source_bbox = {
            "bbox_min": [11.85, 2.0, 3.25],
            "bbox_max": [13.35, 13.9, 4.75],
        }
        with patch(
            "archflow.adapters.three_dm_inspector.inspect_three_dm",
            return_value=model,
        ):
            result = P._headless_three_dm_gate(
                P.Path("candidate.3dm"),
                status,
                expected_object_count=59,
                axis_witness={
                    "name": witness_name,
                    "source_bbox": source_bbox,
                },
                named_vertical_witnesses={
                    "bronze-door-left": P.DOOR_THRESHOLD_Y,
                    "bronze-door-right": P.DOOR_THRESHOLD_Y,
                },
            )

        self.assertTrue(result["passed"])
        self.assertTrue(result["axis_witness"]["passed"])
        self.assertEqual(0.02, result["axis_witness"]["tolerance_m"])
        self.assertGreater(
            result["axis_witness"]["actual_rhino_bbox"]["max"][2],
            result["axis_witness"]["actual_rhino_bbox"]["max"][1],
        )
        self.assertTrue(
            all(
                item["passed"]
                for item in result["named_vertical_witnesses"]
            )
        )

    def test_realized_cad_datum_readback_fails_on_reintroduced_one_metre_gap(self):
        measures = {
            "plinth-object": {
                "bbox_min": [0.0, -0.5, 0.0],
                "bbox_max": [56.0, 0.0, 77.0],
            },
            "rotunda-floor-object": {
                "bbox_min": [0.0, 0.0, 21.0],
                "bbox_max": [56.0, 1.0, 77.0],
            },
            "transition-floor-object": {
                "bbox_min": [11.25, 0.0, 15.0],
                "bbox_max": [44.75, 1.0, 21.0],
            },
            "portico-floor-object": {
                "bbox_min": [11.45, 0.0, 3.0],
                "bbox_max": [44.55, 1.0, 15.0],
            },
            "transition-block-object": {
                "bbox_min": [11.25, 1.0, 15.0],
                "bbox_max": [44.75, 26.0, 26.5],
            },
        }
        for index in range(P.FRONT_STEP_COUNT):
            measures[f"front-step-{index}-object"] = {
                "bbox_min": [11.45, 0.0, index * P.FRONT_STEP_TREAD],
                "bbox_max": [
                    44.55,
                    P.FRONT_STEP_RISE * (index + 1),
                    P.FRONT_STEP_TREAD * (index + 1),
                ],
            }
        for row, count in ((0, 8), (1, 4), (2, 4)):
            for column in range(count):
                measures[f"column-shaft-r{row}-c{column}-object"] = {
                    "bbox_min": [0.0, 1.0, 0.0],
                    "bbox_max": [1.0, 12.9, 1.0],
                }

        passing = P._candidate_cad_datum_readback(measures)
        self.assertTrue(passing["passed"])

        drifted = json.loads(json.dumps(measures))
        drifted["portico-floor-object"]["bbox_min"][1] = 1.0
        drifted["portico-floor-object"]["bbox_max"][1] = 2.0
        failed = P._candidate_cad_datum_readback(drifted)
        self.assertFalse(failed["passed"])
        self.assertTrue(
            any("portico-floor-object" in issue for issue in failed["issues"])
        )

    def test_stage3_material_ledger_covers_every_semantic_component(self):
        program = _geometry(3)
        ledger = P._candidate_material_ledger(program)
        bindings = [
            {
                "component_id": item.component_id,
                "binding_id": item.binding_id,
                "object_ids": list(item.object_ids),
            }
            for item in program.proposal.semantic_bindings
        ]
        from archflow.capabilities.material import ledger_coverage

        coverage = ledger_coverage(bindings, ledger)

        self.assertEqual(1.0, coverage["coverage_ratio"])
        self.assertEqual([], coverage["unassigned"])
        self.assertEqual([], coverage["orphan_assignments"])
        self.assertEqual(
            "reference-void",
            ledger.material_of("main-entry"),
        )

    def test_detail_wrapper_uses_rhino_python_compatible_json_writes(self):
        source = inspect.getsource(P._write_speculative_detail_rhino_workspace)

        self.assertNotIn("encoding='utf-8'", source)
        self.assertIn("ensure_ascii=True", source)
        self.assertIn("Rhino.RhinoApp.Exit(False)", source)

    def test_stage1_grade_steps_landing_and_entry_share_one_datum_chain(self):
        program = _geometry(1)
        operations = {
            operation.op_id: operation
            for operation in program.proposal.operations
        }

        self.assertEqual(0.0, P.GRADE_Y)
        self.assertEqual(
            P.FLOOR_Y,
            P.GRADE_Y + P.STYLOBATE_THICKNESS,
        )

        plinth_origin = _parameter(operations["plinth"], "origin")
        plinth_size = _parameter(operations["plinth"], "size")
        self.assertEqual(P.FOUNDATION_BASE_Y, plinth_origin[1])
        self.assertEqual(P.GRADE_Y, plinth_origin[1] + plinth_size[1])

        rotunda_floor = operations["rotunda-floor"]
        self.assertEqual(
            P.ROTUNDA_FLOOR_BASE_Y,
            _parameter(rotunda_floor, "axis_start")[1],
        )
        self.assertEqual(
            P.FLOOR_Y,
            _parameter(rotunda_floor, "axis_end")[1],
        )

        transition_floor = operations["transition-floor"]
        transition_origin = _parameter(transition_floor, "origin")
        transition_size = _parameter(transition_floor, "size")
        self.assertEqual(P.TRANSITION_FLOOR_BASE_Y, transition_origin[1])
        self.assertEqual(
            P.FLOOR_Y,
            transition_origin[1] + transition_size[1],
        )

        step_tops = []
        for index in range(P.FRONT_STEP_COUNT):
            step = operations[f"front-step-{index}"]
            origin = _parameter(step, "origin")
            size = _parameter(step, "size")
            self.assertEqual(P.GRADE_Y, origin[1])
            self.assertEqual(index * P.FRONT_STEP_TREAD, origin[2])
            self.assertEqual(
                P.FRONT_STEP_RISE * (index + 1),
                size[1],
            )
            step_tops.append(origin[1] + size[1])
        self.assertEqual(
            [
                P.GRADE_Y + P.FRONT_STEP_RISE * (index + 1)
                for index in range(P.FRONT_STEP_COUNT)
            ],
            step_tops,
        )
        self.assertEqual(P.FLOOR_Y, step_tops[-1])

        landing = operations["portico-floor"]
        landing_origin = _parameter(landing, "origin")
        landing_size = _parameter(landing, "size")
        landing_top = landing_origin[1] + landing_size[1]
        self.assertEqual(P.PORTICO_FLOOR_BASE_Y, landing_origin[1])
        self.assertEqual(P.PORTICO_FLOOR_THICKNESS, landing_size[1])
        self.assertEqual(step_tops[-1], landing_top)

        door_threshold = _parameter(operations["door-tool"], "origin")[1]
        interior_floor = _parameter(operations["drum-inner"], "axis_start")[1]
        self.assertEqual(P.FLOOR_Y, door_threshold)
        self.assertEqual(landing_top, door_threshold)
        self.assertEqual(door_threshold, interior_floor)

        column_bases = {
            _parameter(operation, "axis_start")[1]
            for operation in program.proposal.operations
            if operation.op_id.startswith("column-shaft-r")
        }
        self.assertEqual({landing_top}, column_bases)

        entablature_origin = _parameter(
            operations["portico-mass"], "origin"
        )
        self.assertEqual(
            landing_top + P.COLUMN_SHAFT + P.CAPITAL_H,
            entablature_origin[1],
        )

    def test_transition_height_is_grade_to_top_not_wall_solid_thickness(self):
        program = _geometry(0)
        operations = {
            operation.op_id: operation
            for operation in program.proposal.operations
        }
        floor = operations["transition-floor"]
        wall = operations["transition-box"]
        floor_origin = _parameter(floor, "origin")
        wall_origin = _parameter(wall, "origin")
        wall_size = _parameter(wall, "size")
        wall_top = wall_origin[1] + wall_size[1]

        self.assertEqual(P.GRADE_Y, floor_origin[1])
        self.assertEqual(P.FLOOR_Y, wall_origin[1])
        self.assertEqual(P.TRANSITION_TOP_Y, wall_top)
        self.assertEqual(P.TRANSITION_SUPERSTRUCTURE_HEIGHT, wall_size[1])
        self.assertEqual(25.0, wall_size[1])

        refs = {
            "walls": "project:evidence/walls",
            "orders": "project:evidence/orders",
            "front": "project:evidence/front",
            "typology": "project:evidence/typology",
            "junction": "project:evidence/junction",
        }
        runner_context = P.create_runner_context(contracts=P.stage_contracts(refs))
        realized = P._realized_declaration_values(
            program,
            0,
            runner_context=runner_context,
        )
        self.assertEqual(
            wall_top - floor_origin[1],
            realized["transition-height-m"],
        )
        self.assertEqual(26.0, realized["transition-height-m"])
        self.assertNotEqual(
            wall_size[1],
            realized["transition-height-m"],
        )

    def test_stage1_adjacent_physical_layers_touch_without_volume_overlap(self):
        program = _geometry(1)
        operations = {
            operation.op_id: operation
            for operation in program.proposal.operations
        }

        def bounds(operation):
            if operation.kind.value == "solid":
                origin = _parameter(operation, "origin")
                size = _parameter(operation, "size")
                return (
                    tuple(origin),
                    tuple(origin[index] + size[index] for index in range(3)),
                )
            if operation.kind.value == "revolve":
                start = _parameter(operation, "axis_start")
                end = _parameter(operation, "axis_end")
                start_radius = _parameter(operation, "start_radius")
                end_radius = _parameter(operation, "end_radius")
                return (
                    (
                        min(start[0] - start_radius, end[0] - end_radius),
                        min(start[1], end[1]),
                        min(start[2] - start_radius, end[2] - end_radius),
                    ),
                    (
                        max(start[0] + start_radius, end[0] + end_radius),
                        max(start[1], end[1]),
                        max(start[2] + start_radius, end[2] + end_radius),
                    ),
                )
            if operation.kind.value == "extrusion":
                profile = _parameter(operation, "profile")
                vector = _parameter(operation, "vector")
                points = [
                    *profile,
                    *[
                        [point[index] + vector[index] for index in range(3)]
                        for point in profile
                    ],
                ]
                return (
                    tuple(min(point[index] for point in points) for index in range(3)),
                    tuple(max(point[index] for point in points) for index in range(3)),
                )
            self.fail(f"unsupported primitive in contact test: {operation.op_id}")

        def assert_contact(first_id, second_id, axis):
            first = bounds(operations[first_id])
            second = bounds(operations[second_id])
            self.assertTrue(
                first[1][axis] == second[0][axis]
                or second[1][axis] == first[0][axis],
                (first_id, second_id, first, second),
            )
            overlaps = tuple(
                min(first[1][index], second[1][index])
                - max(first[0][index], second[0][index])
                for index in range(3)
            )
            self.assertEqual(0.0, overlaps[axis])
            self.assertTrue(
                all(overlaps[index] > 0.0 for index in range(3) if index != axis),
                (first_id, second_id, overlaps),
            )
            self.assertFalse(all(overlap > 0.0 for overlap in overlaps))

        expected_y_spans = {
            "plinth": (P.FOUNDATION_BASE_Y, P.GRADE_Y),
            "rotunda-floor": (P.GRADE_Y, P.FLOOR_Y),
            "transition-floor": (P.GRADE_Y, P.FLOOR_Y),
            "portico-floor": (P.GRADE_Y, P.FLOOR_Y),
        }
        for op_id, expected in expected_y_spans.items():
            actual = bounds(operations[op_id])
            self.assertEqual(expected, (actual[0][1], actual[1][1]))

        step_ids = [
            f"front-step-{index}" for index in range(P.FRONT_STEP_COUNT)
        ]
        self.assertEqual(
            [0.2, 0.4, 0.6000000000000001, 0.8, 1.0],
            [bounds(operations[op_id])[1][1] for op_id in step_ids],
        )
        self.assertEqual(
            P.FLOOR_Y,
            bounds(operations["door-tool"])[0][1],
        )
        shaft_ids = sorted(
            op_id for op_id in operations if op_id.startswith("column-shaft-r")
        )
        self.assertTrue(shaft_ids)
        self.assertEqual(
            {P.FLOOR_Y},
            {bounds(operations[op_id])[0][1] for op_id in shaft_ids},
        )

        for floor_id in (
            "rotunda-floor", "transition-floor", "portico-floor", *step_ids
        ):
            assert_contact("plinth", floor_id, 1)
        assert_contact("rotunda-floor", "transition-floor", 2)
        assert_contact("transition-floor", "portico-floor", 2)
        for first, second in zip(step_ids, step_ids[1:]):
            assert_contact(first, second, 2)
        assert_contact(step_ids[-1], "portico-floor", 2)
        assert_contact("rotunda-floor", "drum-outer", 1)
        assert_contact("transition-floor", "transition-box", 1)
        assert_contact("transition-floor", "door-tool", 1)
        for shaft_id in shaft_ids:
            assert_contact("portico-floor", shaft_id, 1)
            capital_id = shaft_id.replace("column-shaft-", "column-capital-")
            assert_contact(shaft_id, capital_id, 1)
            assert_contact(capital_id, "portico-mass", 1)
        assert_contact("portico-mass", "pediment", 1)


if __name__ == "__main__":
    unittest.main()
