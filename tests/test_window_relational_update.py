"""One width edit through declared parameters, the existing solvers and real CAD.

The left jamb stays at x=2 m. The fixture's 150 mm lintel coverage is an
explicit test condition, not a structural design standard.
"""
from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters import occt_backend
from archflow.adapters.cad_execution import CadProgramBinding, execute_occt_export
from archflow.adapters.cad_patch import select_patch_operations
from archflow.adapters.cad_program import expected_object_bounds
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD, stage_geometry_program
from archflow.project.refs import BranchRef, record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import (
    Entity,
    Parameter,
    Relation,
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    ValidatorBinding,
    apply_state_record_operator,
    evaluate_parameters,
    resolve_element_bindings,
)
from monkeyarch.capabilities.geometry_proposal import load_compiled_geometry_program
from monkeyarch.runtime import project_runner
from tests import test_project_runner as runner_support


HOST = "wall-south"
LINTEL = "window-lintel"
FIXED = "fixed-building"
APERTURE = "obj-wall-south-aperture-window"
FRAME = "obj-frame-wall-south-window"
GLASS = "obj-glazing-wall-south-window"
LINTEL_OBJECT = "obj-window-lintel"
FIXED_OBJECT = "obj-fixed-building"
BEARING_RELATION = "window-lintel-bearing"


def _record(*, bearing: float = 0.15, checked: bool = False) -> StateRecord:
    parameters = (
        Parameter("window_left", 2.0, "m", epistemic_status="declared"),
        Parameter("window_width", 1.2, "m", epistemic_status="declared"),
        Parameter("window_center", 2.6, "m", expr="window_left + window_width / 2"),
        Parameter("bearing", bearing, "m", epistemic_status="declared"),
        Parameter("lintel_left", 2.0 - bearing, "m", expr="window_left - bearing"),
        Parameter("lintel_right", 3.2 + bearing, "m", expr="window_left + window_width + bearing"),
    )
    wall = Entity(HOST, "Element@1", {
        "component_id": "exterior-walls", "producer": "wall",
        "references": {"line": {"from": {"grid": ["W", "S"]}, "to": {"grid": ["E", "S"]}},
                       "base": {"level": "level-ground"}},
        "params": {"thickness": 0.3, "height": 3.0,
                   "types": [{"type_id": "window-type", "frame_width": 0.09, "frame_depth": 0.18,
                              "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01}],
                   "openings": [{"opening_id": "window", "kind": "window", "along": "@window_center",
                                 "width": "@window_width", "sill": 0.9, "head": 2.4, "type_id": "window-type",
                                 "interface_ref": "relation:window-inside-outside"}]},
    }, parent_id="exterior-walls", basis_refs=runner_support.BASIS)
    lintel = Entity(LINTEL, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"offset_from": {"level": "level-ground", "offset": 2.4}}},
        "params": {"profile": [["@lintel_left", -0.3], ["@lintel_right", -0.3],
                               ["@lintel_right", 0.0], ["@lintel_left", 0.0]], "height": 0.2},
    }, parent_id="portico-columns", basis_refs=runner_support.BASIS)
    fixed = Entity(FIXED, "Element@1", {
        "component_id": "portico-columns", "producer": "prism",
        "references": {"base": {"level": "level-ground"}},
        "params": {"profile": [[8.0, 3.0], [10.0, 3.0], [10.0, 5.0], [8.0, 5.0]], "height": 3.0},
    }, parent_id="portico-columns", basis_refs=runner_support.BASIS)
    relation = Relation(BEARING_RELATION, "dependency", HOST, LINTEL,
                        validator=ValidatorBinding("lintel_minimum_bearing", tolerance=0.001),
                        parameters={"opening_object_id": APERTURE, "lintel_object_id": LINTEL_OBJECT,
                                    "span_axis": "x", "minimum_bearing_m": 0.15}) if checked else None
    outside = (
        Entity("outside-volume", "Volume@1", {"min": [0, 0, -3], "max": [12, 3, -1], "level_ids": ["ground"]}),
        Entity("outside", "Space@1", {"program_node_refs": ["program-node:outside"], "level_ids": ["ground"], "volume_ids": ["outside-volume"]}),
        Entity("window-interface", "Connection@1", {"source_zone_id": "hall", "target_zone_id": "outside",
                                                   "relationship_refs": ["relation:window-inside-outside"]}),
    )
    interface = Relation("window-inside-outside", "interface", "hall", "outside", propagation="unchanged")
    record = runner_support._record(elements=(), extra_entities=(wall, lintel, fixed, *outside), parameters=parameters,
                                    relations=(interface,) if relation is None else (interface, relation))
    return replace(record, entities=tuple(replace(entity, fields={**entity.fields, "volume_ids": ["block", "outside-volume"]})
                                         if entity.entity_id == "building" else entity for entity in record.entities))


def _width_edit(record: StateRecord, value: float) -> StateRecordOperator:
    return StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR,
                               base_record_digest=record.digest, base_state_digest=record.state_digest,
                               target_ref="parameter:window_width", key="window_width", value=value,
                               protected=(f"entity:{FIXED}", "parameter:window_left"))


def _load_record(repository, receipt) -> StateRecord:
    return StateRecord.from_dict(repository.load_json(record_ref_from_uri(receipt["state_record_ref"], "demo")))


def _program(repository, receipt):
    seat = receipt["seat_results"][0]
    return load_compiled_geometry_program(repository.load_json(record_ref_from_uri(seat["program_ref"], "demo")))


def _measured(receipt):
    path = Path(receipt["seat_results"][0]["cad"]["model"])
    return {item.name: occt_backend.measure_shape(item.shape)
            for item in occt_backend.read_step(path, length_unit="meter")}


class WindowSourceParameterTests(unittest.TestCase):
    def test_p036_reload_preserves_bindings_and_refuses_stale_missing_or_cyclic_sources(self) -> None:
        project = runner_support._ExportProject(self, _record())
        record = project.record.bound_to(project.run)
        reference = project.repository.put_json(
            run=project.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=project.run.run_id),
            record_kind=STATE_RECORD, payload=record.to_dict())
        repository = FilesystemProjectRepository.open(project.root / "demo")
        restored = StateRecord.from_dict(repository.load_json(reference))
        operator = _width_edit(restored, 1.6)
        successor = apply_state_record_operator(restored, operator)
        self.assertEqual(successor.parameter("window_center").value, 2.8)
        self.assertEqual(successor.parameter("lintel_left").value, 1.85)
        self.assertEqual(successor.parameter("lintel_right").value, 3.75)
        self.assertEqual(successor.entity(FIXED), restored.entity(FIXED))
        self.assertEqual(successor.entity(HOST).fields["params"]["openings"][0]["width"], "@window_width")
        closure = set(restored.closure(("parameter:window_width",)))
        self.assertTrue({f"entity:{HOST}", f"entity:{LINTEL}", "parameter:window_center", "parameter:lintel_right"} <= closure)
        self.assertNotIn(f"entity:{FIXED}", closure)
        self.assertNotIn("parameter:window_left", closure)
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(successor, operator)
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(restored.bound_to(repository.create_run("another-base")), operator)
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs"):
            apply_state_record_operator(restored, replace(operator, protected=(f"entity:{HOST}",)))

        for name, expression, message in (
            ("window_width", "window_center - window_left", "cycle among parameters"),
            ("window_center", "window_left + missing_width / 2", "reads unknown name 'missing_width'"),
        ):
            broken = replace(restored, parameters=tuple(replace(parameter, expr=expression)
                             if parameter.key == name else parameter for parameter in restored.parameters))
            with self.subTest(expression=expression), self.assertRaisesRegex(StateRecordError, message):
                evaluate_parameters(broken)
        after = repository.put_json(
            run=project.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=project.run.run_id),
            record_kind=STATE_RECORD, payload=successor.to_dict())
        reloaded = StateRecord.from_dict(FilesystemProjectRepository.open(project.root / "demo").load_json(after))
        self.assertEqual(reloaded.digest, successor.digest)
        self.assertEqual(resolve_element_bindings(reloaded)[HOST]["params"]["openings"][0]["width"], 1.6)
        self.assertEqual(repository.read_head(), project.run.base)


@runner_support.NEEDS_OCCT
class WindowRelationalCadTests(unittest.TestCase):
    # Reuse the existing test fixture's one-seat run_project invocation.
    _run = runner_support.IncrementalSourceRunTests.run_source

    def setUp(self) -> None:
        for patcher in runner_support._no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_width_update_rebuilds_host_cut_frame_glass_and_lintel_but_reuses_same_seat_fixed_geometry(self) -> None:
        project = runner_support._ExportProject(self, _record(checked=True))
        head = project.repository.read_head()
        checks = ("lintel_minimum_bearing", "support_contact")
        first = self._run(project, project.record, "run-1", required_checks=checks)
        self.assertTrue(first["seat_execution_complete"], first["seat_results"])
        source_path = Path(first["seat_results"][0]["cad"]["model"])
        source_sha = runner_support._sha256_of(source_path)
        before = _measured(first)
        project.repository = FilesystemProjectRepository.open(project.root / "demo")
        restored = _load_record(project.repository, first)
        successor = apply_state_record_operator(restored, _width_edit(restored, 1.6))
        with patch.object(project_runner, "produce_rows", wraps=project_runner.produce_rows) as produce, patch.object(
            occt_backend, "_build_operation", wraps=occt_backend._build_operation
        ) as build:
            second = self._run(project, successor, "run-2", first, required_checks=checks)
        self.assertTrue(second["seat_execution_complete"], second["seat_results"])
        self.assertEqual(second["closure_status"], "SATISFIED")
        self.assertCountEqual([row.element_id for call in produce.call_args_list for row in call.args[0]], [HOST, LINTEL])
        executed_ops = {call.args[2].op_id for call in build.call_args_list}
        self.assertNotIn(FIXED, executed_ops)
        self.assertTrue({LINTEL, "wall-south-cut", "wall-south-aperture-window", "frame-wall-south-window",
                         "glazing-wall-south-window"} <= executed_ops)
        seat = second["seat_results"][0]
        round_receipt = project.repository.load_json(record_ref_from_uri(seat["receipt_ref"], "demo"))
        self.assertEqual(round_receipt["reused_element_ids"], [FIXED])
        self.assertEqual(seat["cad"]["path"], "incremental")
        execution = project.repository.load_json(record_ref_from_uri(seat["cad"]["execution_ref"], "demo"))
        self.assertTrue(execution["readback_verified"], execution["failures"])
        self.assertEqual(execution["reused_object_ids"], [FIXED_OBJECT])
        self.assertEqual(execution["identity"]["binding"]["run_id"], "run-2")
        for receipt in (first, second):
            report = project.repository.load_json(record_ref_from_uri(receipt["seat_results"][0]["relation_check_ref"], "demo"))
            bearing = next(item for item in report["checks"] if item["relation_id"] == BEARING_RELATION)
            self.assertEqual(bearing["status"], "held", bearing)
            self.assertAlmostEqual(bearing["measured"]["left_bearing_m"], 0.15)
            self.assertAlmostEqual(bearing["measured"]["right_bearing_m"], 0.15)
        old_program, new_program = _program(project.repository, first), _program(project.repository, second)
        self.assertEqual({item.object_id for item in old_program.objects}, {item.object_id for item in new_program.objects})
        self.assertEqual({binding.binding_id: binding.object_ids for binding in old_program.proposal.semantic_bindings},
                         {binding.binding_id: binding.object_ids for binding in new_program.proposal.semantic_bindings})
        selection = select_patch_operations(new_program, old_program)
        self.assertTrue({APERTURE, FRAME, GLASS, LINTEL_OBJECT, "obj-wall-south-cut"} <= set(selection.changed_object_ids))
        self.assertIn(FIXED_OBJECT, selection.kept_object_ids)
        bounds = expected_object_bounds(new_program)
        self.assertAlmostEqual(bounds[APERTURE]["bbox_min"][0], 2.0)
        self.assertAlmostEqual(bounds[APERTURE]["bbox_max"][0], 3.6)
        self.assertAlmostEqual(bounds[LINTEL_OBJECT]["bbox_min"][0], 1.85)
        self.assertAlmostEqual(bounds[LINTEL_OBJECT]["bbox_max"][0], 3.75)

        after = _measured(second)
        self.assertEqual(set(after), set(before))
        for name in (APERTURE, FRAME, GLASS, LINTEL_OBJECT):
            with self.subTest(object=name):
                self.assertAlmostEqual(after[name].bbox_min[0], before[name].bbox_min[0], places=6)
                self.assertAlmostEqual(after[name].bbox_max[0] - before[name].bbox_max[0], 0.4, places=6)
                self.assertGreater(after[name].volume, before[name].volume)
        self.assertAlmostEqual(after[APERTURE].bbox_min[0] - after[LINTEL_OBJECT].bbox_min[0], 0.15, places=6)
        self.assertAlmostEqual(after[LINTEL_OBJECT].bbox_max[0] - after[APERTURE].bbox_max[0], 0.15, places=6)
        self.assertAlmostEqual(before["obj-wall-south-cut"].volume - after["obj-wall-south-cut"].volume,
                               0.4 * 1.5 * 0.3, places=6)
        self.assertAlmostEqual(after[FIXED_OBJECT].volume, before[FIXED_OBJECT].volume, places=6)
        self.assertEqual(after[FIXED_OBJECT].bbox_min, before[FIXED_OBJECT].bbox_min)
        self.assertEqual(after[FIXED_OBJECT].bbox_max, before[FIXED_OBJECT].bbox_max)
        self.assertEqual(runner_support._sha256_of(source_path), source_sha)
        cold = FilesystemProjectRepository.open(project.root / "demo")
        final_record = _load_record(cold, second)
        self.assertEqual(final_record.digest, successor.digest)
        self.assertEqual(final_record.parameter("window_width").value, 1.6)
        self.assertEqual(final_record.entity(HOST).fields["params"]["openings"][0]["along"], "@window_center")
        self.assertEqual(cold.read_head(), head)

    def test_cad_can_build_a_lintel_that_fails_the_independent_declared_bearing_condition(self) -> None:
        project = runner_support._ExportProject(self, _record(bearing=0.05, checked=True))
        head = project.repository.read_head()
        result = self._run(project, project.record, "run-1", required_checks=("lintel_minimum_bearing",))
        seat = result["seat_results"][0]
        self.assertEqual(seat["status"], "proposal_accepted", seat)
        report = project.repository.load_json(record_ref_from_uri(seat["relation_check_ref"], "demo"))
        check = next(item for item in report["checks"] if item["relation_id"] == BEARING_RELATION)
        self.assertEqual(check["status"], "violated", check)
        self.assertAlmostEqual(check["measured"]["left_bearing_m"], 0.05)
        self.assertAlmostEqual(check["measured"]["right_bearing_m"], 0.05)
        self.assertIsNone(seat["cad"])
        self.assertNotEqual(result["closure_status"], "SATISFIED")
        self.assertIsNone(result["exit_binding_ref"])
        # The normal runner refuses export on this architectural condition. An
        # explicit adapter call proves that valid CAD alone would not catch it.
        program = _program(project.repository, result)
        program_ref = project.repository.put_json(
            run=project.run, destination=PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=project.run.run_id,
                                                               branch_id=project.options.branch_id),
            record_kind=stage_geometry_program("bearing-cad-check"), payload=program.to_dict())
        binding = CadProgramBinding(program_ref=program_ref,
                                    branch=BranchRef(project.run, project.options.branch_id, project.options.branch_epoch),
                                    stage_id="bearing-cad-check", program_digest=program.program_digest,
                                    design_state_digest=program.proposal.design_state_digest, predecessor_program_digest=None)
        cad = execute_occt_export(program, binding=binding, speculative_workspace=project.workspace("seat-structure"),
                                  artifact_stem="insufficient-bearing", preview=True)
        self.assertEqual(cad.status.value, "succeeded", cad.failures)
        self.assertTrue(cad.readback_verified, cad.failures)
        self.assertIn(LINTEL_OBJECT, cad.physical_object_ids)
        self.assertEqual(project.repository.read_head(), head)


if __name__ == "__main__":
    unittest.main()
