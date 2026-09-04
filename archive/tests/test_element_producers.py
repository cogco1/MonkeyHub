"""P100 / P102: reference-reading producers — the portico vertical slice.

Column → capital → entablature → pediment all evaluate from shared datums:
the column array publishes its top, the capitals bind it and publish
theirs, the entablature binds the capital top, the pediment binds the
entablature top. No operation carries an offset unless the row declares an
engagement with a depth.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from archflow.adapters.cad_program import expected_object_bounds
from archflow.capabilities.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    produce_rows,
)
from archflow.capabilities.reference_resolver import ReferenceContext
from archflow.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import GeometryOperationKind, ProjectGridAxis, ProjectGrids, ProjectLevel, ProjectLevels, SemanticBinding
from archive.tests.test_geometry_compiler import COMMITMENT, _proposal, _state
from archive.tests.test_wall_window_families import _only

BASIS = ("reading:plate",)
PN = "level-piano-nobile"


def _grids() -> ProjectGrids:
    axes = [ProjectGridAxis(f"axis-{k + 1}", str(k + 1), ((k - 2.5) * 1.6065, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS) for k in range(6)]
    axes.append(ProjectGridAxis("axis-w", "W", (0.0, 0.0, -13.85), (1.0, 0.0, 0.0), BASIS))       # the west facade line
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=tuple(sorted(axes, key=lambda a: a.axis_id)))


def _levels(piano: float = 3.57) -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel(PN, "piano-nobile", piano, BASIS)))


def _rows(column_height: float = 6.426, engagement: float | None = None) -> tuple[ElementRow, ...]:
    capital_params = {"height": 0.18, "half_extent": 0.56}
    if engagement is not None:
        capital_params["engagement"] = {"depth": engagement}
    return (
        ElementRow("columns-west", "portico-columns", "column-array", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "base": {"level": PN}},
                   {"radius": 0.357, "height": column_height, "segments": 24}, BASIS),
        ElementRow("capitals-west", "portico-capitals", "capitals", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "columns": "columns-west", "base": {"datum": "columns-west-top"}},
                   capital_params, BASIS),
        ElementRow("entablature-west", "portico-entablature", "beam", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "capitals-west-top"}, "support": "capitals-west"},
                   {"depth": 0.92, "height": 1.33875, "end_overhang": 0.36}, BASIS),
        ElementRow("pediment-west", "portico-pediments", "pediment", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "entablature-west-top"}, "support": "entablature-west"},
                   {"rise": 1.78, "thickness": 0.3}, BASIS),
    )


def _produce(rows, levels=None):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=levels or _levels()), published={})
    return produce_rows(rows, context), context


class VerticalSliceTests(unittest.TestCase):
    def test_every_interface_is_one_published_datum(self) -> None:
        produced, context = _produce(_rows())
        columns, capitals, entablature, pediment = produced
        self.assertEqual(columns.datums[0].datum_id, "columns-west-top")
        self.assertEqual(capitals.datums[0].datum_id, "capitals-west-top")
        self.assertEqual(entablature.datums[0].datum_id, "entablature-west-top")
        self.assertTrue(all(b.datum_id == PN for b in columns.bindings))
        self.assertTrue(all(b.datum_id == "columns-west-top" for b in capitals.bindings))
        self.assertEqual(entablature.bindings[0].datum_id, "capitals-west-top")
        self.assertEqual(pediment.bindings[0].datum_id, "entablature-west-top")
        self.assertAlmostEqual(context.datum_value("columns-west-top"), 3.57 + 6.426)
        self.assertAlmostEqual(context.datum_value("capitals-west-top"), 3.57 + 6.426 + 0.18)
        self.assertAlmostEqual(context.datum_value("entablature-west-top"), 3.57 + 6.426 + 0.18 + 1.33875)
        # no unexplained epsilon: no operation carries a base_offset without a declared engagement
        for element in produced:
            for op in element.operations:
                self.assertNotIn("base_offset", {p.name for p in op.parameters}, op.op_id)
        # relations are produced by construction, with the datum they hold
        kinds = [(r.kind, r.subject, r.object, r.datum_id) for e in produced for r in e.relations]
        self.assertIn(("support", "columns-west", "capitals-west", "columns-west-top"), kinds)
        self.assertIn(("support", "capitals-west", "entablature-west", "capitals-west-top"), kinds)
        self.assertIn(("support", "entablature-west", "pediment-west", "entablature-west-top"), kinds)

    def test_a_declared_engagement_is_the_only_way_to_embed(self) -> None:
        produced, _ = _produce(_rows(engagement=0.02))
        capitals = produced[1]
        params = {p.name: json.loads(p.value_json) for p in capitals.operations[0].parameters}
        self.assertAlmostEqual(params["base_offset"], -0.02)                     # embed by exactly the declared depth
        self.assertAlmostEqual(params["vector"][1], 0.18 + 0.02)
        self.assertEqual(capitals.relations[0].parameters, {"engagement_depth": 0.02})
        with self.assertRaises(ElementProducerError):
            _produce((replace(_rows()[1], params={"height": 0.18, "half_extent": 0.56, "engagement": {"why": "it touched"}}),))

    def test_supports_must_come_first(self) -> None:
        rows = _rows()
        with self.assertRaises(ElementProducerError):
            _produce((rows[1],))                                                 # capitals before their columns published a top

    def test_slice_compiles_and_a_column_height_change_moves_the_whole_chain(self) -> None:
        def compile_slice(height: float):
            # the compiler fixture knows one component ("building") and one frame ("world"); the slice binds to them
            context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
            produced = produce_rows(_rows(column_height=height), context)
            operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
            bindings = tuple(b for e in produced for b in e.bindings)
            datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
            state = _state()
            proposal = _only(_proposal(state, extra_operations=operations), operations, ())
            result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=bindings)
            self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
            return expected_object_bounds(result.program)

        a = compile_slice(6.426)
        self.assertAlmostEqual(a["obj-columns-west-0"]["bbox_max"][1], a["obj-capitals-west-0"]["bbox_min"][1])          # contact by construction
        self.assertAlmostEqual(a["obj-capitals-west-0"]["bbox_max"][1], a["obj-entablature-west"]["bbox_min"][1])
        self.assertAlmostEqual(a["obj-entablature-west"]["bbox_max"][1], a["obj-pediment-west"]["bbox_min"][1])
        self.assertAlmostEqual((a["obj-columns-west-0"]["bbox_min"][0] + a["obj-columns-west-0"]["bbox_max"][0]) / 2, -2.5 * 1.6065)   # on axis 1
        b = compile_slice(7.0)
        for oid in ("obj-capitals-west-0", "obj-entablature-west", "obj-pediment-west"):
            self.assertAlmostEqual(b[oid]["bbox_min"][1] - a[oid]["bbox_min"][1], 7.0 - 6.426)                     # the chain moved as one


if __name__ == "__main__":
    unittest.main()
