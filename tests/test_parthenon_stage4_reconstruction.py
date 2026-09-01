from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import rhino3dm

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.project import BranchRef, ProjectVersionRef, RunRef
from archflow.state.operational_state import ObligationStatus
from tools import run_parthenon_reconstruction as stage3
from tools import run_parthenon_stage4_reconstruction as stage4


HUMAN_AUTHORIZATION_REF = (
    "decision:human-authorized-stage4-principal-door-closed-double-leaf"
)


def _stage3_operations() -> tuple[dict[str, object], ...]:
    sources = {
        decision.decision_ref: (f"evidence:{decision.decision_ref.split(':')[-1]}",)
        for decision in stage3.DECISIONS
    }
    return stage3.build_stage_operations(3, sources)


def _full_stage4_operations():
    predecessor = _stage3_operations()
    operations, lineage = stage4.compile_full_building_stage4_operations(
        predecessor,
        column_source_refs=("evidence:penrose-column",),
        entablature_source_refs=("evidence:perseus-entablature",),
        opening_source_refs=("evidence:bsa-windows",),
        visual_manifest_ref="evidence:selected-visual-regions",
        human_authorization_ref=HUMAN_AUTHORIZATION_REF,
    )
    return predecessor, operations, lineage


def _passing_close_gates():
    refs = {
        name: f"evidence:stage-4/{name}"
        for name in stage4.STAGE4_CLOSE_GATE_NAMES
    }
    receipts = {
        name: {"schema": f"{name}@1", "status": "PASSED", "passed": True, "failures": []}
        for name in stage4.STAGE4_CLOSE_GATE_NAMES
        if name != "component_coverage"
    }
    receipts["component_coverage"] = {
        "schema": "StageComponentCoverageReceipt@1",
        "status": "PASS",
        "operation_count": 271,
        "component_count": 11,
    }
    return receipts, refs


class ParthenonStage4OperationTests(unittest.TestCase):
    def test_full_building_preflight_closes_before_fixed_run_persistence(self) -> None:
        predecessor = _stage3_operations()
        _, failed_lineage = stage4.compile_stage4_operations(
            predecessor,
            column_source_refs=("evidence:failed-column",),
            entablature_source_refs=("evidence:failed-entablature",),
            opening_source_refs=("evidence:failed-opening",),
            visual_manifest_ref="evidence:failed-visual-manifest",
        )
        visual_manifest = {
            "schema": "ParthenonVisualRegionSelection@1",
            "branch_id": stage4.BRANCH_ID,
            "selected_candidate_ids": [
                *stage4.REQUIRED_STAGE4_SELECTED_ROIS,
                *(
                    f"selected-{index}"
                    for index in range(
                        14 - len(stage4.REQUIRED_STAGE4_SELECTED_ROIS)
                    )
                ),
            ],
            "parked_candidate_ids": [f"parked-{index}" for index in range(13)],
            "rejected_candidate_ids": [
                f"rejected-{index}" for index in range(5)
            ],
        }
        authorization_capture = stage4._compile_human_authorization_capture(
            authorization_ref=HUMAN_AUTHORIZATION_REF,
            statement="人工授权的复原，注意依赖关系完整",
            captured_at="2026-08-29T20:00:00Z",
        )
        predecessor_run = RunRef(
            stage4.PROJECT_ID,
            stage4.PREDECESSOR_RUN_ID,
            ProjectVersionRef(
                stage4.PROJECT_ID,
                stage4.CANONICAL_VERSION,
                stage4.CANONICAL_STATE_SHA256,
            ),
        )
        predecessor_state = stage3._initial_operational_state(
            predecessor_run,
            "evidence:branch-selection",
        )
        predecessor_state = replace(
            predecessor_state,
            obligations=tuple(
                replace(item, status=ObligationStatus.SATISFIED)
                for item in predecessor_state.obligations
            ),
        )
        receipt = stage4.preflight_stage4_candidate(
            predecessor,
            human_authorization_ref=HUMAN_AUTHORIZATION_REF,
            visual_manifest=visual_manifest,
            failed_attempt_lineage=failed_lineage,
            authorization_capture=authorization_capture,
            predecessor_state=predecessor_state,
        )

        self.assertTrue(receipt["passed"])
        self.assertEqual(687, receipt["operation_count"])
        self.assertGreater(receipt["relation_contract_count"], 0)
        self.assertEqual(
            {
                "component_coverage",
                "correction_provenance",
                "detail",
                "door_assembly",
                "evidence",
                "inner_colonnade",
                "material_bindings",
                "model_readback",
                "roof_eaves_pediment",
                "spatial",
                "successor_relations",
            },
            set(receipt["gate_names"]),
        )
        self.assertEqual(
            receipt["operation_execution_digest"],
            receipt["reference_invariance"][
                "variant_operation_execution_digest"
            ],
        )
        self.assertEqual(
            receipt["relation_contract_digest"],
            receipt["reference_invariance"][
                "variant_relation_contract_digest"
            ],
        )
        self.assertEqual(
            11,
            len(receipt["preflight_gate_receipt_snapshot_digests"]),
        )
        self.assertTrue(receipt["closure_preflight"]["stage_ready"])
        self.assertEqual(
            "COMPLETE",
            receipt["closure_preflight"]["pack_status"],
        )

    def test_run_project_does_not_create_fixed_runs_when_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Mock()
            repository.read_head.return_value = ProjectVersionRef(
                stage4.PROJECT_ID,
                stage4.CANONICAL_VERSION,
                stage4.CANONICAL_STATE_SHA256,
            )
            repository.layout.run.side_effect = lambda run_id: SimpleNamespace(
                root=Path(temporary) / run_id
            )
            predecessor = {
                "selected_visual_ids": stage4.REQUIRED_STAGE4_SELECTED_ROIS,
                "predecessor_operations": _stage3_operations(),
                "visual_manifest": {},
                "predecessor_state": Mock(),
            }
            with (
                patch.object(
                    stage4.FilesystemProjectRepository,
                    "open",
                    return_value=repository,
                ),
                patch.object(
                    stage4,
                    "_load_exact_predecessors",
                    return_value=predecessor,
                ),
                patch.object(
                    stage4,
                    "_load_exact_incomplete_stage4_attempt",
                    return_value={"lineage": {}},
                ),
                patch.object(
                    stage4,
                    "preflight_stage4_candidate",
                    side_effect=stage4.ParthenonStage4Error("relation probe"),
                ),
            ):
                with self.assertRaisesRegex(
                    stage4.ParthenonStage4Error,
                    "relation probe",
                ):
                    stage4.run_project(
                        Path(temporary),
                        captured_at="2026-08-29T20:00:00Z",
                        human_authorization_ref=HUMAN_AUTHORIZATION_REF,
                        human_authorization_statement=(
                            "人工授权的复原，注意依赖关系完整"
                        ),
                    )

        repository.create_run_batch.assert_not_called()

    def test_detail_delta_is_explicit_and_preserves_metope_topology(self) -> None:
        predecessor = _stage3_operations()
        operations, lineage = stage4.compile_stage4_operations(
            predecessor,
            column_source_refs=("evidence:penrose-column",),
            entablature_source_refs=("evidence:perseus-entablature",),
            opening_source_refs=("evidence:bsa-windows",),
            visual_manifest_ref="evidence:selected-visual-regions",
        )

        ids = {str(item["operation_id"]) for item in operations}
        self.assertEqual(len(ids), len(operations))
        self.assertEqual(92, sum(item.startswith("metope-") for item in ids))
        self.assertEqual(96, sum(item.startswith("triglyph-") for item in ids))
        self.assertEqual(
            46,
            sum(
                item["kind"] == "doric_shaft"
                and str(item["operation_id"]).startswith("peristyle-")
                for item in operations
            ),
        )
        self.assertEqual(
            12,
            sum(
                item["kind"] == "doric_shaft"
                and str(item["operation_id"]).startswith("porch-")
                for item in operations
            ),
        )
        self.assertEqual(
            58,
            sum(str(item["operation_id"]).endswith("-echinus") for item in operations),
        )
        self.assertEqual(
            58,
            sum(str(item["operation_id"]).endswith("-abacus") for item in operations),
        )
        self.assertEqual(
            58,
            sum(str(item["operation_id"]).endswith("-neck") for item in operations),
        )
        self.assertEqual(271, lineage["predecessor_operation_count"])
        self.assertEqual(len(operations), lineage["current_operation_count"])
        self.assertEqual(
            sorted(lineage["superseded_operation_ids"]),
            sorted(lineage["replacement_allowlist"]),
        )
        self.assertEqual(110, len(lineage["replacement_allowlist"]))
        self.assertEqual(161, lineage["preserved_operation_count"])
        self.assertEqual(161, len(lineage["preserved_operation_fingerprints"]))
        self.assertFalse(
            {f"entablature-{side}" for side in ("east", "north", "south", "west")}
            & ids
        )
        self.assertEqual(12, sum(item["kind"] == "entablature_layer" for item in operations))
        self.assertEqual(
            {"east": 15, "north": 33, "south": 33, "west": 15},
            {
                side: sum(item.startswith(f"triglyph-{side}-") for item in ids)
                for side in ("east", "north", "south", "west")
            },
        )
        self.assertNotIn("cella-wall-east-left", ids)
        self.assertNotIn("cella-wall-east-right", ids)
        self.assertTrue(stage4.validate_window_voids(operations)["passed"])

        predecessor_by_id = {
            str(item["operation_id"]): item for item in predecessor
        }
        allowlist = set(lineage["replacement_allowlist"])
        for operation_id, expected in lineage[
            "preserved_operation_fingerprints"
        ].items():
            self.assertNotIn(operation_id, allowlist)
            self.assertEqual(
                stage4._operation_fingerprint(predecessor_by_id[operation_id]),
                expected,
            )
        for item in operations:
            if str(item["operation_id"]) in lineage[
                "preserved_operation_fingerprints"
            ]:
                continue
            self.assertTrue(
                "replaces_operation_id" in item
                or "refines_operation_id" in item
            )
            self.assertTrue(item["source_refs"])
            self.assertTrue(item["visual_region_refs"])
            self.assertIn("parameter_basis", item)

    def test_full_building_compiler_replaces_roof_pediment_and_cornice_proxies(self) -> None:
        predecessor, operations, lineage = _full_stage4_operations()
        ids = {str(item["operation_id"]) for item in operations}

        self.assertEqual(687, len(operations))
        self.assertFalse(set(stage4.stage4_roof.COARSE_HOST_IDS) & ids)
        self.assertTrue(
            stage4.stage4_roof.validate_roof_eaves_pediment_operations(operations)[
                "passed"
            ]
        )
        self.assertEqual(
            len(operations),
            len(lineage["successor_to_predecessor_operation_ids"]),
        )
        self.assertTrue(lineage["all_successors_bound_to_exact_stage3"])
        self.assertEqual(106, lineage["exact_unchanged_predecessor_count"])
        self.assertEqual(
            20,
            len(
                lineage["successor_to_predecessor_operation_ids"][
                    "naos-u-architrave-north"
                ]
            ),
        )
        inner_lineage = lineage["inner_colonnade_delta"]
        self.assertEqual(650, inner_lineage["current_operation_count"])
        self.assertIn(
            "evidence:bsa-windows",
            inner_lineage["textual_evidence_refs"],
        )
        self.assertEqual(
            {
                "evidence:selected-visual-regions#candidate=roi_ysma_11_1_inner_colonnade",
                "evidence:selected-visual-regions#candidate=roi_ysma_12_11_center_fluting",
            },
            set(inner_lineage["selected_visual_refs"]),
        )
        door_lineage = lineage["door_assembly_delta"]
        self.assertEqual(18, door_lineage["delta_operation_count"])
        self.assertEqual(
            HUMAN_AUTHORIZATION_REF,
            door_lineage["human_authorization_ref"],
        )
        self.assertEqual(
            {
                "evidence:selected-visual-regions#candidate="
                "roi_ysma_11_6_central_doorway",
            },
            set(door_lineage["parameter_basis"]["selected_visual_refs"]),
        )
        self.assertTrue(
            stage4.stage4_doors.validate_door_assembly_operations(
                operations,
                resolution=(
                    stage4.stage4_doors.DoorAssemblyResolution
                    .AUTHORIZED_CLOSED_DOUBLE_LEAF
                ),
                resolution_receipt=door_lineage,
            )["passed"]
        )
        self.assertEqual(
            ["cella-door-east"],
            lineage["successor_to_predecessor_operation_ids"][
                "principal-door-east-leaf-left"
            ],
        )
        self.assertEqual(
            ["cella-wall-east-left"],
            lineage["successor_to_predecessor_operation_ids"][
                "cella-wall-east-left-window-inner-pier"
            ],
        )

        coverage = stage4.compile_stage4_component_coverage(
            predecessor,
            operations,
            lineage=lineage,
            lineage_ref="evidence:stage-4/program",
            evidence_refs=("evidence:stage-4/evidence-gate",),
            relational_revalidation_refs=("revalidation:stage-4/all-successor-relations",),
        )
        self.assertEqual("PASS", coverage.status.value)
        self.assertEqual(271, coverage.operation_count)
        self.assertEqual(11, coverage.component_count)
        self.assertEqual(len(operations), len(coverage.successor_operations))

    def test_component_coverage_rejects_missing_stage3_denominator_operation(self) -> None:
        predecessor, operations, lineage = _full_stage4_operations()
        with self.assertRaisesRegex(
            stage4.ParthenonStage4Error,
            "exact 271-operation",
        ):
            stage4.compile_stage4_component_coverage(
                predecessor[:-1],
                operations,
                lineage=lineage,
                lineage_ref="evidence:stage-4/program",
                evidence_refs=("evidence:stage-4/evidence-gate",),
                relational_revalidation_refs=("revalidation:stage-4/relations",),
            )

    def test_full_building_compiler_requires_typed_door_authorization(self) -> None:
        predecessor = _stage3_operations()
        self.assertEqual(
            HUMAN_AUTHORIZATION_REF,
            stage4._require_human_authorization_ref(HUMAN_AUTHORIZATION_REF),
        )
        for authorization in (None, "", "decision:assistant-invented-door"):
            with self.subTest(authorization=authorization):
                with self.assertRaises(stage4.ParthenonStage4Error):
                    stage4._require_human_authorization_ref(authorization)
                with self.assertRaises(stage4.stage4_doors.ParthenonDoorAssemblyError):
                    stage4.compile_full_building_stage4_operations(
                        predecessor,
                        column_source_refs=("evidence:penrose-column",),
                        entablature_source_refs=("evidence:perseus-entablature",),
                        opening_source_refs=("evidence:bsa-windows",),
                        visual_manifest_ref="evidence:selected-visual-regions",
                        human_authorization_ref=authorization,
                    )

    def test_full_building_roof_delta_has_realized_3dm_objects(self) -> None:
        _, operations, lineage = _full_stage4_operations()
        program_digest = "c" * 64
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "stage4-full-building.3dm"
            stage4.create_stage4_model(
                model_path,
                operations=operations,
                program_digest=program_digest,
            )
            inspection = inspect_three_dm(model_path)
            roof_validation = stage4.stage4_roof.validate_roof_eaves_pediment_operations(
                operations
            )
            inner_validation = (
                stage4.stage4_inner_colonnade.validate_inner_colonnade_operations(
                    operations
                )
            )
            door_validation = stage4.stage4_doors.validate_door_assembly_operations(
                operations,
                resolution=(
                    stage4.stage4_doors.DoorAssemblyResolution
                    .AUTHORIZED_CLOSED_DOUBLE_LEAF
                ),
                resolution_receipt=lineage["door_assembly_delta"],
            )
            relation_validation = (
                stage4.stage4_relations.validate_stage4_successor_relations(
                    operations,
                    model_path=model_path,
                )
            )
            material_validation = stage4.validate_stage4_material_bindings(
                model_path,
                operations=operations,
            )

        self.assertEqual(len(operations), inspection.top_level_object_count)
        self.assertTrue(roof_validation["passed"], roof_validation)
        self.assertTrue(inner_validation["passed"], inner_validation)
        self.assertTrue(door_validation["passed"], door_validation)
        self.assertEqual(
            0,
            relation_validation["checks"]["window_projection"]["obstruction_count"],
        )
        self.assertEqual(
            0,
            relation_validation["checks"]["interior_column_stacks"][
                "direct_shaft_contact_count"
            ],
        )
        self.assertFalse(relation_validation["passed"])
        self.assertTrue(material_validation["passed"], material_validation)

    def test_generated_model_is_metre_z_up_and_passes_detail_readback(self) -> None:
        operations, _ = stage4.compile_stage4_operations(
            _stage3_operations(),
            column_source_refs=("evidence:penrose-column",),
            entablature_source_refs=("evidence:perseus-entablature",),
            opening_source_refs=("evidence:bsa-windows",),
            visual_manifest_ref="evidence:selected-visual-regions",
        )
        program_digest = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "stage4.3dm"
            stage4.create_stage4_model(
                model_path,
                operations=operations,
                program_digest=program_digest,
            )
            inspection = inspect_three_dm(model_path)
            detail = stage4.validate_stage4_model(
                model_path,
                operations=operations,
                program_digest=program_digest,
            )
            materials = stage4.validate_stage4_material_bindings(
                model_path,
                operations=operations,
            )

            mismatched_operations = copy.deepcopy(operations)
            mismatched_operations[0]["material_id"] = (
                "pentelic-marble"
                if mismatched_operations[0]["material_id"] != "pentelic-marble"
                else "bronze-timber-candidate"
            )
            mismatched_materials = stage4.validate_stage4_material_bindings(
                model_path,
                operations=mismatched_operations,
            )

        self.assertEqual("Meters", inspection.units["name"])
        self.assertEqual(len(operations), inspection.top_level_object_count)
        self.assertTrue(detail["passed"], detail)
        self.assertEqual(20, detail["checks"]["exterior_flute_count"])
        self.assertEqual(92, detail["checks"]["metope_count"])
        self.assertEqual(96, detail["checks"]["triglyph_count"])
        self.assertTrue(detail["checks"]["actual_twenty_flute_shafts"])
        self.assertTrue(detail["checks"]["capital_entablature_continuity"])
        self.assertTrue(detail["checks"]["detail_collision_free"])
        self.assertTrue(detail["checks"]["object_operation_bijection"])
        self.assertTrue(materials["passed"], materials)
        self.assertEqual(1.0, materials["checks"]["resolved_binding_coverage"])
        self.assertEqual(0, materials["checks"]["display_color_only_count"])
        self.assertEqual([], materials["checks"]["texture_file_references"])
        self.assertEqual(
            {"MaterialFromObject": len(operations)},
            materials["checks"]["material_source_counts"],
        )
        self.assertTrue(detail["checks"]["material_bindings"])
        self.assertEqual(1.0, detail["checks"]["material_binding_coverage"])
        self.assertFalse(mismatched_materials["passed"])

    def test_material_gate_resolves_layer_material_but_rejects_display_color(self) -> None:
        operation = {
            "operation_id": "material-probe",
            "material_id": "pentelic-marble",
        }
        with tempfile.TemporaryDirectory() as temporary:
            valid_path = Path(temporary) / "layer-material.3dm"
            model = rhino3dm.File3dm()
            material = rhino3dm.Material()
            material.Name = "pentelic-marble"
            material_index = model.Materials.Add(material)
            layer = rhino3dm.Layer()
            layer.Name = "material-probe"
            layer.RenderMaterialIndex = material_index
            layer_index = model.Layers.Add(layer)
            attributes = rhino3dm.ObjectAttributes()
            attributes.Name = "material-probe"
            attributes.LayerIndex = layer_index
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromLayer
            attributes.SetUserString("archflow:material_id", "pentelic-marble")
            model.Objects.AddPoint(rhino3dm.Point3d(0.0, 0.0, 0.0), attributes)
            self.assertTrue(model.Write(str(valid_path), 8))
            valid = stage4.validate_stage4_material_bindings(
                valid_path,
                operations=(operation,),
            )

            display_only_path = Path(temporary) / "display-color-only.3dm"
            display_only = rhino3dm.File3dm()
            display_only_material = rhino3dm.Material()
            display_only_material.Name = "pentelic-marble"
            display_only.Materials.Add(display_only_material)
            display_layer = rhino3dm.Layer()
            display_layer.Name = "material-probe"
            display_layer.Color = (226, 219, 198, 255)
            display_layer_index = display_only.Layers.Add(display_layer)
            display_attributes = rhino3dm.ObjectAttributes()
            display_attributes.Name = "material-probe"
            display_attributes.LayerIndex = display_layer_index
            display_attributes.MaterialSource = (
                rhino3dm.ObjectMaterialSource.MaterialFromLayer
            )
            display_attributes.SetUserString(
                "archflow:material_id", "pentelic-marble"
            )
            display_only.Objects.AddPoint(
                rhino3dm.Point3d(0.0, 0.0, 0.0), display_attributes
            )
            self.assertTrue(display_only.Write(str(display_only_path), 8))
            invalid = stage4.validate_stage4_material_bindings(
                display_only_path,
                operations=(operation,),
            )

        self.assertTrue(valid["passed"], valid)
        self.assertEqual(
            {"MaterialFromLayer": 1}, valid["checks"]["material_source_counts"]
        )
        self.assertEqual(1.0, valid["checks"]["resolved_binding_coverage"])
        self.assertFalse(invalid["passed"])
        self.assertEqual(1, invalid["checks"]["display_color_only_count"])

    def test_actual_model_window_intrusion_fails_closed(self) -> None:
        operations, _ = stage4.compile_stage4_operations(
            _stage3_operations(),
            column_source_refs=("evidence:penrose-column",),
            entablature_source_refs=("evidence:perseus-entablature",),
            opening_source_refs=("evidence:bsa-windows",),
            visual_manifest_ref="evidence:selected-visual-regions",
        )
        mutated = list(copy.deepcopy(operations))
        rogue = copy.deepcopy(
            next(item for item in mutated if item["operation_id"] == "cella-wall-east-left-window-sill")
        )
        rogue["operation_id"] = "cella-wall-east-left-window-rogue-infill"
        rogue["parameters"]["origin"] = [-7.5, 22.26, 4.0]
        rogue["parameters"]["size"] = [2.5, 1.35, 2.75]
        mutated.append(rogue)
        self.assertFalse(stage4.validate_window_voids(mutated)["passed"])

        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "window-intrusion.3dm"
            stage4.create_stage4_model(
                model_path,
                operations=mutated,
                program_digest="b" * 64,
            )
            detail = stage4.validate_stage4_model(
                model_path,
                operations=mutated,
                program_digest="b" * 64,
            )
        self.assertFalse(detail["passed"])
        self.assertFalse(detail["checks"]["actual_window_voids_clear"])

    def test_visual_manifest_cannot_leak_parked_or_rejected_regions(self) -> None:
        manifest = {
            "schema": "ParthenonVisualRegionSelection@1",
            "branch_id": stage4.BRANCH_ID,
            "selected_candidate_ids": ["roi-selected"],
            "parked_candidate_ids": ["roi-parked"],
            "rejected_candidate_ids": ["roi-rejected"],
        }
        self.assertEqual(
            ("roi-selected",), stage4.validate_visual_manifest(manifest)
        )
        manifest["selected_candidate_ids"].append("roi-rejected")
        with self.assertRaisesRegex(ValueError, "overlap"):
            stage4.validate_visual_manifest(manifest)


class ParthenonStage4StateTests(unittest.TestCase):
    def test_cross_run_rebind_keeps_facts_and_locks_and_opens_stage4(self) -> None:
        predecessor_run = RunRef(
            stage4.PROJECT_ID,
            stage4.PREDECESSOR_RUN_ID,
            ProjectVersionRef(stage4.PROJECT_ID, 0, "b" * 64),
        )
        predecessor = stage3._initial_operational_state(
            predecessor_run, "evidence:branch-selection"
        )
        predecessor = replace(
            predecessor,
            obligations=tuple(
                replace(item, status=ObligationStatus.SATISFIED)
                for item in predecessor.obligations
            ),
        )
        successor_run = RunRef(
            stage4.PROJECT_ID,
            stage4.RUN_ID,
            predecessor_run.base,
        )

        opened, receipt = stage4.rebind_predecessor_state(
            predecessor,
            successor_run,
            binding_ref="evidence:cross-run-binding",
        )
        self.assertEqual(predecessor.facts, opened.facts)
        self.assertEqual(predecessor.locks, opened.locks)
        self.assertEqual(predecessor.branch.epoch + 1, opened.branch.epoch)
        stage4_obligation = next(
            item for item in opened.obligations if item.obligation_id == "close-stage-4"
        )
        self.assertIs(ObligationStatus.OPEN, stage4_obligation.status)
        self.assertTrue(receipt["facts_unchanged"])
        self.assertTrue(receipt["locks_unchanged"])
        self.assertTrue(receipt["evidence_refs_unchanged"])
        self.assertEqual(predecessor.evidence_refs, opened.evidence_refs)

        gate_receipts, gate_refs = _passing_close_gates()
        closed, convergence = stage4.close_stage4_state(
            opened,
            gate_receipts=gate_receipts,
            gate_refs=gate_refs,
        )
        stage4_obligation = next(
            item for item in closed.obligations if item.obligation_id == "close-stage-4"
        )
        self.assertIs(ObligationStatus.SATISFIED, stage4_obligation.status)
        self.assertTrue(convergence.stage_ready)
        self.assertEqual(
            tuple(sorted(gate_refs.values())),
            convergence.potential_before.revalidation_refs,
        )
        self.assertEqual((), convergence.potential_after.revalidation_refs)

    def test_close_fails_for_any_required_stage4_gate(self) -> None:
        predecessor_run = RunRef(
            stage4.PROJECT_ID,
            stage4.PREDECESSOR_RUN_ID,
            ProjectVersionRef(stage4.PROJECT_ID, 0, "b" * 64),
        )
        predecessor = stage3._initial_operational_state(
            predecessor_run, "evidence:branch-selection"
        )
        predecessor = replace(
            predecessor,
            obligations=tuple(
                replace(item, status=ObligationStatus.SATISFIED)
                for item in predecessor.obligations
            ),
        )
        opened, _ = stage4.rebind_predecessor_state(
            predecessor,
            RunRef(stage4.PROJECT_ID, stage4.RUN_ID, predecessor_run.base),
            binding_ref="evidence:cross-run-binding",
        )

        cases = {
            "missing roof": ("roof_eaves_pediment", None),
            "coverage missing one Stage 3 op": (
                "component_coverage",
                {
                    "schema": "StageComponentCoverageReceipt@1",
                    "status": "PASS",
                    "operation_count": 270,
                    "component_count": 11,
                },
            ),
            "relations failed": (
                "successor_relations",
                {"status": "FAILED", "passed": False, "failures": ["collision"]},
            ),
            "inner colonnade failed": (
                "inner_colonnade",
                {"status": "FAILED", "passed": False, "failures": ["stack"]},
            ),
            "door assembly failed": (
                "door_assembly",
                {
                    "status": "FAILED",
                    "passed": False,
                    "failures": ["authorization"],
                },
            ),
            "material failed": (
                "material_bindings",
                {"status": "FAILED", "passed": False, "failures": ["unbound"]},
            ),
        }
        for label, (gate_name, replacement_receipt) in cases.items():
            with self.subTest(label=label):
                gate_receipts, gate_refs = _passing_close_gates()
                if replacement_receipt is None:
                    gate_receipts.pop(gate_name)
                    gate_refs.pop(gate_name)
                else:
                    gate_receipts[gate_name] = replacement_receipt
                with self.assertRaisesRegex(
                    stage4.ParthenonStage4Error,
                    "closure gates failed",
                ):
                    stage4.close_stage4_state(
                        opened,
                        gate_receipts=gate_receipts,
                        gate_refs=gate_refs,
                    )


class ParthenonStage4AssetTests(unittest.TestCase):
    def test_evidence_gate_rejects_required_roi_when_it_is_only_parked(self) -> None:
        required = list(stage4.REQUIRED_STAGE4_SELECTED_ROIS)
        missing = required.pop()
        selected = [
            *required,
            *(
                f"selected-filler-{index}"
                for index in range(14 - len(required))
            ),
        ]
        manifest = {
            "schema": "ParthenonVisualRegionSelection@1",
            "branch_id": stage4.BRANCH_ID,
            "selected_candidate_ids": selected,
            "parked_candidate_ids": [missing],
            "rejected_candidate_ids": [],
        }

        gate = stage4.compile_stage4_evidence_gate(
            visual_manifest=manifest
        )

        self.assertFalse(gate["passed"])
        self.assertEqual([missing], gate["missing_required_visual_ids"])

    def test_asset_document_has_required_provenance_fields(self) -> None:
        document = stage4.render_asset_provenance_document(
            captured_at="2026-08-29T18:00:00Z"
        )
        for label in (
            "来源 URL",
            "作者/发布者",
            "许可证",
            "下载时间",
            "SHA-256",
            "格式",
            "单位",
            "上轴",
            "用途",
            "采纳裁决",
        ):
            self.assertIn(label, document)
        self.assertIn("程序化生成", document)
        self.assertIn("需要登录", document)
        self.assertIn("未采纳", document)

    def test_login_asset_cannot_be_adopted_without_user_login(self) -> None:
        candidate = dict(stage4.OPEN_ASSET_CANDIDATES[-1])
        candidate["adoption_decision"] = "ADOPTED"
        with self.assertRaises(stage4.AssetLoginRequired):
            stage4.guard_open_asset_candidates((candidate,))

    def test_unknown_units_or_axis_cannot_be_adopted(self) -> None:
        candidate = dict(stage4.OPEN_ASSET_CANDIDATES[0])
        candidate["adoption_decision"] = "ADOPTED"
        with self.assertRaisesRegex(
            stage4.ParthenonStage4Error,
            "lacks provenance",
        ):
            stage4.guard_open_asset_candidates((candidate,))


class ParthenonStage4ExactBindingTests(unittest.TestCase):
    def test_exact_predecessor_literals_are_not_discoverable_aliases(self) -> None:
        self.assertEqual(
            "exports/parthenon-progress-snapshot-"
            "ac778e607e87f1f9625121f3e70742b28c0dffcc1426365f893d3b98b4f4799a.json",
            stage4.PREDECESSOR_PROGRESS_REF.relative_path,
        )
        self.assertIn("runs/reconstruction-004/branches/", stage4.PREDECESSOR_PACK_REF.relative_path)
        self.assertEqual(
            "9f54dbc72b758948d6c78aa738c9877195ec8cd616bba35d845dfac53ec948c6",
            stage4.PREDECESSOR_PROGRAM_DIGEST,
        )
        self.assertEqual(
            "d3ff9f427bbe4126d8346ad3be18831b638ea8f08d18e3b007234d7e9a4da7bd",
            stage4.VISUAL_MANIFEST_REF.sha256,
        )

    def test_corrected_stage4_uses_new_sibling_runs_and_persisted_human_chain(self) -> None:
        self.assertEqual("research-007", stage4.RESEARCH_RUN_ID)
        self.assertEqual("reconstruction-006", stage4.RUN_ID)
        self.assertEqual("reconstruction-004", stage4.PREDECESSOR_RUN_ID)

        predecessor = _stage3_operations()
        failed_operations, failed_lineage = stage4.compile_stage4_operations(
            predecessor,
            column_source_refs=("evidence:column",),
            entablature_source_refs=("evidence:entablature",),
            opening_source_refs=("evidence:opening",),
            visual_manifest_ref="evidence:selected-visual-regions",
        )
        experience = (
            stage4.stage4_corrections.compile_incomplete_stage4_experience_record(
                stage3_root_operation_ids=tuple(
                    str(item["operation_id"]) for item in predecessor
                ),
                refined_stage3_root_ids=failed_lineage["replacement_allowlist"],
                copied_stage3_root_ids=tuple(
                    failed_lineage["preserved_operation_fingerprints"]
                ),
                failed_attempt_evidence_refs=(
                    stage4.stage4_corrections
                    .FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS
                ),
            )
        )
        experience_ref = (
            "project://parthenon-reconstruction/runs/research-007/branches/"
            "idealized-periclean-original/records/experience-"
            + "a" * 64
            + ".json"
        )
        diagnosis_ref = (
            "project://parthenon-reconstruction/runs/research-007/branches/"
            "idealized-periclean-original/records/door-diagnosis-content-hash.json"
        )
        capture_ref = (
            "project://parthenon-reconstruction/runs/research-007/branches/"
            "idealized-periclean-original/records/human-authorization-"
            + "b" * 64
            + ".json"
        )
        capture = stage4._compile_human_authorization_capture(
            authorization_ref="human-authorization:test-stage4-door",
            statement="人工授权的复原，注意依赖关系完整",
            captured_at="2026-08-29T20:00:00Z",
        )
        decision = (
            stage4.stage4_corrections.compile_authorized_door_decision_record(
                authorization_ref=capture_ref,
                failed_attempt_experience_ref=experience_ref,
                failed_attempt_experience_digest=str(experience["record_digest"]),
                door_dependency_diagnosis_ref=diagnosis_ref,
                selected_visual_manifest_ref=stage4.VISUAL_MANIFEST_REF.uri,
            )
        )
        gate = stage4._compile_correction_provenance_gate(
            experience_record=experience,
            experience_ref=experience_ref,
            door_decision_record=decision,
            door_decision_ref=(
                "project://parthenon-reconstruction/runs/research-007/branches/"
                "idealized-periclean-original/records/door-decision-"
                + "c" * 64
                + ".json"
            ),
            authorization_capture=capture,
            authorization_capture_ref=capture_ref,
        )
        self.assertTrue(gate["passed"], gate["failures"])
        self.assertEqual(509, len(failed_operations))

        tampered = copy.deepcopy(decision)
        tampered["only_stage_predecessor"]["run_id"] = "reconstruction-005"
        failed_gate = stage4._compile_correction_provenance_gate(
            experience_record=experience,
            experience_ref=experience_ref,
            door_decision_record=tampered,
            door_decision_ref=(
                "project://parthenon-reconstruction/runs/research-007/branches/"
                "idealized-periclean-original/records/door-decision-"
                + "d" * 64
                + ".json"
            ),
            authorization_capture=capture,
            authorization_capture_ref=capture_ref,
        )
        self.assertFalse(failed_gate["passed"])


if __name__ == "__main__":
    unittest.main()
