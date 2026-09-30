"""The record's frame, read in MonkeyArch: levels, axes, what names them and what a change reaches (#519).

The Runtime answers ``GET /api/state/frame`` and ``POST /api/state/closure``
from these two functions, and its HTTP contract stays in its own suite. Here
they are read off a record built in place: no project, no server. Every
closure is compared with the kernel's own ``StateRecord.closure``, never with
a list written down beside it.
"""

from __future__ import annotations

import unittest

from archflow.state.dependencies import PROPAGATING_EFFECTS
from archflow.state.state_record import StateRecord
from monkeyarch.authoring.frame import FrameError, closure_of_refs, frame_of

EVIDENCE = "evidence:frame-fixture"
LEVEL, AXIS, AXIS_ROLE = "level-ground", "axis-a", "front"


def _prism(entity_id: str, references: dict) -> dict:
    return {
        "entity_id": entity_id, "schema": "Element@1", "parent_id": "building",
        "fields": {"component_id": "building", "producer": "prism", "references": references,
                   "params": {"profile": [[0, 0], [4, 0], [4, 2], [0, 2]], "height": 0.5}},
        "basis_refs": [EVIDENCE],
    }


def _record(*, origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0), level=True, placed_on_axis=True) -> StateRecord:
    """A base standing on the ground level (and, unless told otherwise, on the front axis), a cornice on the base."""

    base = {"base": {"level": LEVEL}} if level else {}
    if placed_on_axis:
        base["at"] = {"axis_point": {"axis": AXIS_ROLE, "along": 1.5}}
    entities = [
        {"entity_id": "building", "schema": "Component@1",
         "fields": {"semantic_kind": "building", "intent": "the building", "source_refs": [EVIDENCE]}},
        {"entity_id": AXIS, "schema": "GridAxis@1",
         "fields": {"role": AXIS_ROLE, "origin": list(origin), "direction": list(direction)}, "basis_refs": [EVIDENCE]},
    ]
    if level:
        entities.append({"entity_id": LEVEL, "schema": "Level@1", "fields": {"role": "ground", "elevation": 0.0},
                         "basis_refs": [EVIDENCE]})
        entities += [_prism("base", base), _prism("cornice", {"base": {"datum": "base-top"}})]
    return StateRecord.from_dict({
        "schema": "StateRecord@1", "project_id": "frame-fixture", "run_id": "authored",
        "evidence_refs": [EVIDENCE], "entities": entities,
        "parameters": [
            {"key": "module", "value": 1.2, "unit": "m", "epistemic_status": "declared"},
            {"key": "bay", "value": 2.4, "unit": "m", "expr": "2 * module", "inputs": ["module"]},
        ],
    })


class FrameTests(unittest.TestCase):
    def test_a_level_lists_what_names_it_and_the_kernels_closure(self) -> None:
        record = _record()
        level, = frame_of(record).levels
        self.assertEqual((level.level_id, level.role, level.elevation), (LEVEL, "ground", 0.0))
        # Only the element that names the level; the cornice stands on the base's datum.
        self.assertEqual(level.elements_on, ("base",))
        self.assertEqual(level.closure, record.closure((f"entity:{LEVEL}",)))
        self.assertEqual(set(level.closure), {f"entity:{LEVEL}", "entity:base", "entity:cornice"})

    def test_an_axis_is_a_plan_line_and_lists_what_names_its_role(self) -> None:
        record = _record()
        axis, = frame_of(record).axes
        # Direction (0, 0, 1) is a line of constant world x at origin[0].
        self.assertEqual((axis.axis_id, axis.role, axis.const, axis.value), (AXIS, AXIS_ROLE, "x", 0.0))
        self.assertEqual((axis.origin, axis.direction), ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        self.assertEqual(axis.elements_on, ("base",))
        self.assertEqual(axis.closure, record.closure((f"entity:{AXIS}",)))
        self.assertEqual(frame_of(record).honesty, ())

    def test_a_diagonal_axis_has_no_constant_and_says_so(self) -> None:
        frame = frame_of(_record(direction=(1.0, 0.0, 1.0)))
        axis, = frame.axes
        self.assertIsNone(axis.const)
        self.assertIsNone(axis.value)
        self.assertEqual(axis.direction, (1.0, 0.0, 1.0))
        self.assertIn(f"axis {AXIS} ({AXIS_ROLE}) is parallel to neither world axis: it has no single constant "
                      "this panel can show", frame.honesty)

    def test_what_the_record_does_not_carry_is_stated(self) -> None:
        frame = frame_of(_record(level=False))
        self.assertEqual(frame.levels, ())
        self.assertEqual(frame.honesty, (
            "no Level@1 in the record: nothing here places an element in elevation",
            "no element references a grid axis role: the axes are declared and nothing is placed against them",
        ))

    def test_an_axis_that_is_not_three_numbers_is_refused(self) -> None:
        with self.assertRaises(FrameError) as raised:
            frame_of(_record(origin=(0.0, 0.0, 0.0, 0.0)))
        self.assertEqual(raised.exception.code, "STATE_RECORD_INVALID")
        self.assertIn("three numbers", str(raised.exception))


class ClosureTests(unittest.TestCase):
    def test_the_closure_is_the_kernels_with_the_edges_inside_it(self) -> None:
        record = _record()
        answer = closure_of_refs(record, ("parameter:module", "parameter:module"))
        self.assertEqual(answer.closure, record.closure(("parameter:module",)))
        self.assertEqual(set(answer.closure), {"parameter:module", "parameter:bay"})
        self.assertTrue(answer.edges)
        for edge in answer.edges:
            self.assertIn(edge.effect, PROPAGATING_EFFECTS)
            self.assertIn(edge.upstream_ref, answer.closure)
            self.assertIn(edge.downstream_ref, answer.closure)

    def test_a_ref_the_record_does_not_carry_is_refused_by_name(self) -> None:
        record = _record()
        for refs, named in (
            (("entity:level-grond",), "entity:level-grond"),
            ((LEVEL,), LEVEL),
            (("parameter:nothing", f"entity:{LEVEL}"), "parameter:nothing"),
        ):
            with self.subTest(refs=refs), self.assertRaises(FrameError) as raised:
                closure_of_refs(record, refs)
            self.assertEqual(raised.exception.code, "UNKNOWN_REF")
            self.assertIn(named, str(raised.exception))

    def test_an_empty_question_is_refused(self) -> None:
        with self.assertRaises(FrameError) as raised:
            closure_of_refs(_record(), ())
        self.assertEqual(raised.exception.code, "UNKNOWN_REF")
        self.assertEqual(str(raised.exception), "changedRefs must name at least one ref of the record")


if __name__ == "__main__":
    unittest.main()
