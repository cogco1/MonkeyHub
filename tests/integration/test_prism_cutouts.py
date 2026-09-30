"""Rectangular panel edits use the prism producer and wall subtraction owner."""
from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.adapters.cad_program import expected_object_bounds
from archflow.adapters.cad_patch import select_patch_operations
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import Entity, Parameter, StateRecord, StateRecordEditKind, StateRecordOperator, apply_state_record_operator
from monkeyarch.capabilities.element_producers import ElementProducerError, ElementRow, ProductionContext, produce_rows
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.capabilities.wall_solver import WallSolverError, subtract_rectangular_cutouts
from monkeyarch.compilers.geometry import compile_geometry_program
from tests.test_element_producers import BASIS, PN, _grids, _levels, _op_params
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state
from tests import test_project_runner as runner_support


def _cut(cutout_id="window", span0=0.5, span1=1.5, bottom=1.0, top=2.0):
    return dict(cutout_id=cutout_id, span0=span0, span1=span1, bottom=bottom, top=top)


def _row(cutouts=None, *, profile=None):
    params = {"profile": profile or [[0, 0], [2, 0], [2, 0.4], [0, 0.4]], "height": 3.0}
    if cutouts is not None:
        params["rectangular_cutouts"] = cutouts
    return ElementRow("panel", "building", "prism", {"base": {"datum": PN, "offset": 0.2}}, params, BASIS)


def _produce(row):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
    return produce_rows((row,), context)[0], context


class RectangularSubtractionTests(unittest.TestCase):
    def test_an_internal_hole_returns_four_disjoint_rectangles_with_the_right_area(self):
        pieces = subtract_rectangular_cutouts((0, 2, 0, 3), [_cut()])
        self.assertEqual(dict(pieces), {
            "-cut-window-left": (0, 0.5, 0, 3),
            "-cut-window-right": (1.5, 2, 0, 3),
            "-cut-window-below": (0.5, 1.5, 0, 1),
            "-cut-window-above": (0.5, 1.5, 2, 3),
        })
        self.assertAlmostEqual(sum((r - l) * (t - b) for _, (l, r, b, t) in pieces), 5.0)
        for i, (_, a) in enumerate(pieces):
            for _, b in pieces[i + 1:]:
                self.assertTrue(min(a[1], b[1]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[2], b[2]))

    def test_crossing_the_module_edge_clips_only_the_overlap(self):
        pieces = subtract_rectangular_cutouts((2.5, 3.0, 0, 0.2), [_cut(span0=1.22, span1=2.82, bottom=-1, top=1)])
        self.assertEqual(pieces, (("-cut-window-right", (2.82, 3.0, 0, 0.2)),))

    def test_overlapping_cutouts_have_stable_names_and_geometry_when_input_order_changes(self):
        cuts = [_cut("window", 0.5, 1.5, 1, 2), _cut("lintel", 0.4, 1.6, 1.9, 2.2)]
        a = subtract_rectangular_cutouts((0, 2, 0, 3), cuts)
        self.assertEqual(a, subtract_rectangular_cutouts((0, 2, 0, 3), cuts[::-1]))
        self.assertAlmostEqual(sum((r - l) * (t - b) for _, (l, r, b, t) in a), 6 - 1 - 0.36 + 0.1)

    def test_invalid_cutouts_are_refused_even_when_outside_the_panel(self):
        invalid = [None, {}, [1], [_cut(span0=True)], [_cut(span1=float("inf"))],
                   [_cut(bottom=float("nan"))], [_cut(top="2")], [_cut(span1=0.5)],
                   [_cut(top=1.0)], [_cut(cutout_id="invalid/path")], [_cut(), _cut()],
                   [{**_cut(), "axis": "z"}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(WallSolverError):
                subtract_rectangular_cutouts((10, 12, 0, 3), value)


class PrismCutoutTests(unittest.TestCase):
    def test_nonintersecting_or_touching_holes_leave_the_original_prism_unchanged(self):
        plain, _ = _produce(_row())
        for cuts in ([], [_cut(span0=2, span1=3)], [_cut(bottom=3, top=4)]):
            with self.subTest(cuts=cuts):
                cut, context = _produce(_row(cuts))
                self.assertEqual(cut, plain)
                self.assertIn("panel-top", context.published)

    def test_full_removal_and_restoration_generate_no_degenerate_objects(self):
        empty, context = _produce(_row([_cut(span0=-1, span1=3, bottom=-1, top=4)]))
        self.assertEqual(empty.operations, ())
        self.assertEqual(empty.bindings, ())
        self.assertEqual(empty.datums, ())
        self.assertNotIn("panel-top", context.published)
        before, _ = _produce(_row([_cut(span0=-1, span1=1, bottom=-1, top=4)]))
        restored, _ = _produce(_row([_cut(span0=-1, span1=1, bottom=-1, top=4)]))
        self.assertEqual(before, restored)
        self.assertEqual(before.operations[0].output_object_ids, ("obj-panel-cut-window-right",))
        self.assertTrue(all(_op_params(op)["vector"][1] > 0 for op in restored.operations))

    def test_cut_panels_publish_no_fictitious_whole_top(self):
        cut, context = _produce(_row([_cut()]))
        self.assertEqual(cut.datums, ())
        self.assertNotIn("panel-top", context.published)
        upper = ElementRow("upper", "building", "prism", {"base": {"datum": "panel-top"}},
                           {"profile": [[0, 0], [2, 0], [2, 0.4], [0, 0.4]], "height": 0.2}, BASIS)
        with self.assertRaisesRegex(ElementProducerError, "not published yet"):
            produce_rows((upper,), context)

    def test_nonrectangular_and_crossed_profiles_are_refused_only_for_cutouts(self):
        profiles = [
            [[0, 0], [2, 0], [0, 0.4]],
            [[0, 0], [2, 0], [1.9, 0.4], [0, 0.4]],
            [[0, 0], [2, 0.4], [2, 0], [0, 0.4]],
            [[0, 0], [2, 0], [2, 0], [0, 0]],
        ]
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaisesRegex(ElementProducerError, "four ordered axis-aligned"):
                _produce(_row([_cut()], profile=profile))
        plain, _ = _produce(_row(profile=profiles[0]))
        self.assertEqual(len(plain.operations), 1)

    def test_compiled_piece_bounds_include_the_declared_base_and_full_panel_thickness(self):
        produced, context = _produce(_row([_cut()]))
        operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for op in produced.operations)
        state = _state()
        proposal = _only(_proposal(state, extra_operations=operations), operations, ())
        result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,),
            interface_datums=_levels().datums(), datum_bindings=produced.bindings)
        self.assertIsNotNone(result.program, [(i.code.value, i.detail) for i in result.receipt.issues])
        bounds = expected_object_bounds(result.program)
        self.assertEqual(len(bounds), 4)
        for piece in bounds.values():
            self.assertAlmostEqual(piece["bbox_min"][2], 0)
            self.assertAlmostEqual(piece["bbox_max"][2], 0.4)
        above = bounds["obj-panel-cut-window-above"]
        self.assertAlmostEqual(above["bbox_min"][1], 3.57 + 0.2 + 2)
        self.assertAlmostEqual(above["bbox_max"][1], 3.57 + 0.2 + 3)


@runner_support.NEEDS_OCCT
class PrismCutoutRunTests(unittest.TestCase):
    _run = runner_support.IncrementalSourceRunTests.run_source

    def test_saved_source_can_remove_and_restore_a_panel_while_reusing_its_neighbour(self):
        from monkeyarch.capabilities.geometry_proposal import load_compiled_geometry_program

        for patcher in runner_support._no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)
        params = _row([_cut(span0=-1, span1="@cut_end", bottom=-1, top=4)]).params
        panel = Entity("panel", "Element@1", {"component_id": "portico-columns", "producer": "prism",
                       "references": {"base": {"level": "level-ground"}}, "params": params},
                       parent_id="portico-columns", basis_refs=BASIS)
        fixed = replace(panel, entity_id="fixed", parent_id="exterior-walls", fields={**panel.fields, "component_id": "exterior-walls", "params": {
            "profile": [[4, 0], [5, 0], [5, 0.4], [4, 0.4]], "height": 3.0}})
        initial = runner_support._record(elements=(), extra_entities=(panel, fixed),
            parameters=(Parameter("cut_end", 1.0, "m", epistemic_status="declared"),))
        project = runner_support._ExportProject(self, initial)
        head = project.repository.read_head()
        first = self._run(project, initial, "run-1")

        def revise(receipt, cut_end, run_id):
            # A fresh repository reads the source state and runner; no in-memory
            # producer result from the previous run is handed to the new one.
            project.repository = FilesystemProjectRepository.open(project.root / "demo")
            source = StateRecord.from_dict(project.repository.load_json(record_ref_from_uri(receipt["state_record_ref"], "demo")))
            operator = StateRecordOperator(StateRecordEditKind.SET_SCALAR, source.digest, source.state_digest,
                                           target_ref="parameter:cut_end", key="cut_end", value=cut_end)
            return self._run(project, apply_state_record_operator(source, operator), run_id, receipt)

        second = revise(first, 3.0, "run-2")
        third = revise(second, 1.0, "run-3")
        programs = []
        for receipt in (first, second, third):
            self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
            seat = receipt["seat_results"][0]
            self.assertTrue(seat["cad"]["readback_verified"], seat["cad"])
            programs.append(load_compiled_geometry_program(project.repository.load_json(record_ref_from_uri(seat["program_ref"], "demo"))))
        panel_id = "obj-panel-cut-window-right"
        self.assertEqual(select_patch_operations(programs[1], programs[0]).retired_object_ids, (panel_id,))
        self.assertEqual(select_patch_operations(programs[2], programs[1]).added_object_ids, (panel_id,))
        self.assertEqual(expected_object_bounds(programs[0]), expected_object_bounds(programs[2]))
        for receipt in (second, third):
            self.assertIn("obj-fixed", receipt["seat_results"][0]["cad"]["reused_object_ids"])
        self.assertEqual(project.repository.read_head(), head)


if __name__ == "__main__":
    unittest.main()
