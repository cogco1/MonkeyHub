"""C1: a straight flight from ``producer='stair'`` is one closed stepped solid, executed through OCCT.

Not a handwritten loft: every row here is produced by the stair producer,
compiled by the geometry compiler, persisted through a temporary P036
project, realized by ``execute_occt_export`` into a caller-supplied
speculative workspace and cold-read back from the STEP bytes on disk. No
process is started and Rhino is never involved.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters import occt_backend
from archflow.adapters.cad_execution import (
    CadExecutionStatus,
    CadProgramBinding,
    OcctExecutionReceipt,
    RhinoCadProgramBinding,
    execute_occt_export,
)
from monkeyarch.capabilities.element_producers import ElementRow, ProductionContext, produce_rows, production_order
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from archflow.state.geometry_program import CompiledGeometryProgram
from monkeyarch.compilers.geometry import compile_geometry_program
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import stage_geometry_program
from archflow.project.refs import BranchRef
from archflow.state.geometry_program import GeometryOperationKind, ProjectGridAxis, ProjectGrids, ProjectLevel, ProjectLevels
from tests.support import shared_bound_state
from tests.test_geometry_compiler import COMMITMENT, _only, _proposal, _state

NEEDS_OCCT = unittest.skipUnless(
    occt_backend.occt_available(), "cadquery-ocp is not installed: python -m pip install -e '.[cad-occt]'"
)

BASIS = ("reading:plate",)
GROUND, UPPER = "level-ground", "level-upper"
RISE, GOING, WIDTH, COUNT = 0.2, 0.3, 1.2, 3
RUN = COUNT * GOING                                  # 0.9 m
HEIGHT = COUNT * RISE                                # 0.6 m
VOLUME = WIDTH * sum((k + 1) * RISE * GOING for k in range(COUNT))   # 0.432 m3: three treads of 0.2, 0.4 and 0.6 m over 0.3 m


def _grids() -> ProjectGrids:
    axes = (
        ProjectGridAxis("axis-run", "RUN", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), BASIS),          # +x through the origin
        ProjectGridAxis("axis-skew", "SKEW", (2.0, 0.0, -1.0), (0.6, 0.0, 0.8), BASIS),       # an oblique 3-4-5 line
    )
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=axes)


def _levels() -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel(GROUND, "terrain-grade", 0.0, BASIS), ProjectLevel(UPPER, "upper-floor", HEIGHT, BASIS)))


def _on(axis: str, along: float) -> dict:
    return {"axis_point": {"axis": axis, "along": along}}


def _flight(start: dict, end: dict, base: dict, top: dict | None = None, **params) -> ElementRow:
    p = {"count": COUNT, "rise": RISE, "width": WIDTH}
    p.update(params)
    references = {"from": start, "to": end, "base": base}
    if top is not None:
        references["top"] = top
    return ElementRow("stair-east", "primary-support", "stair", references, p, BASIS)


def _compile(rows: tuple[ElementRow, ...]) -> tuple[CompiledGeometryProgram, ProductionContext]:
    """The stair producer, then the compiler, exactly as the authored-record tests do it."""

    levels = _levels()
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=levels), published={}, frame_id="world")
    produced = produce_rows(production_order(rows), context)
    state = _state()
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for element in produced for op in element.operations)
    datums = tuple(sorted(list(context.published.values()) + list(levels.datums()), key=lambda d: d.datum_id))
    result = compile_geometry_program(
        state,
        _only(_proposal(state, extra_operations=operations), operations, ()),
        active_commitment_refs=(COMMITMENT,),
        interface_datums=datums,
        datum_bindings=tuple(b for element in produced for b in element.bindings),
    )
    if result.program is None:
        raise AssertionError([(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
    return result.program, context


def _persisted_binding(program: CompiledGeometryProgram, stage_id: str) -> RhinoCadProgramBinding:
    """Persist the compiled program through the temporary P036 project first and bind to that record, as the runner does."""

    repository, run, _, _ = shared_bound_state()
    branch = BranchRef(run=run, branch_id="runner-v1", epoch=1)
    destination = PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=branch.branch_id)
    program_ref = repository.put_json(
        run=run, destination=destination, record_kind=stage_geometry_program(stage_id), payload=program.to_dict()
    )
    return CadProgramBinding(
        program_ref=program_ref, branch=branch, stage_id=stage_id, program_digest=program.program_digest,
        design_state_digest=program.proposal.design_state_digest, predecessor_program_digest=None,
    )


def _refuse_process(*args, **kwargs):
    raise AssertionError(f"the OCCT executor must not start a process: {args[:1]}")


def _export(program: CompiledGeometryProgram, stage_id: str, stem: str) -> tuple[OcctExecutionReceipt, dict[str, occt_backend.StepEntry], float]:
    """Execute into a temporary speculative workspace; return the receipt, the cold-read STEP entries and the wall-clock seconds."""

    binding = _persisted_binding(program, stage_id)
    with tempfile.TemporaryDirectory() as tmp, patch.multiple(subprocess, Popen=_refuse_process, run=_refuse_process):
        workspace = Path(tmp).resolve()
        started = time.perf_counter()
        receipt = execute_occt_export(program, binding=binding, speculative_workspace=workspace, artifact_stem=stem,
                                      provenance={"export_path": "occt-test"})
        elapsed = time.perf_counter() - started
        if receipt.status is not CadExecutionStatus.SUCCEEDED:
            raise AssertionError(receipt.failures)
        step = workspace / receipt.exact_artifact["relative_path"]
        preview = workspace / receipt.preview_artifact["relative_path"]
        if sorted(p.name for p in workspace.iterdir()) != sorted([step.name, preview.name]):
            raise AssertionError(sorted(p.name for p in workspace.iterdir()))
        entries = occt_backend.read_step(step, length_unit="meter")
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise AssertionError(f"duplicate names in STEP: {names}")
    print(f"\n[occt] {stem}: {elapsed:.3f} s wall clock; timings={ {k: round(v, 3) for k, v in receipt.timings.items()} }")
    return receipt, {entry.name: entry for entry in entries}, elapsed


def _params(operation) -> dict:
    return {p.name: json.loads(p.value_json) for p in operation.parameters}


class WholeStairTests(unittest.TestCase):
    """What the producer emits before any kernel runs: one loft of two stepped sides, one object, one top."""

    def test_the_producer_emits_one_loft_of_two_identical_stepped_sides(self) -> None:
        program, context = _compile((_flight(_on("RUN", 0.0), _on("RUN", RUN), {"level": GROUND}, {"level": UPPER}),))
        (operation,) = [op for op in program.proposal.operations if op.op_id == "stair-east"]
        params = _params(operation)
        self.assertEqual((operation.kind, operation.output_object_ids), (GeometryOperationKind.LOFT, ("obj-stair-east",)))
        self.assertEqual((params["profile_size"], len(params["profiles"]), params["cap_ends"], params["loft_type"]), (8, 16, True, "straight"))
        self.assertNotIn("base_offset", params)
        near, far = params["profiles"][:8], params["profiles"][8:]
        self.assertEqual([(p[0], p[1]) for p in near], [(0.0, 0.0), (0.9, 0.0), (0.9, 0.6), (0.6, 0.6), (0.6, 0.4), (0.3, 0.4), (0.3, 0.2), (0.0, 0.2)])
        self.assertEqual([(p[0], p[1]) for p in far], [(p[0], p[1]) for p in near])
        self.assertEqual(({p[2] for p in near}, {p[2] for p in far}), ({0.6}, {-0.6}))
        self.assertAlmostEqual(context.datum_value("stair-east-top"), HEIGHT)
        self.assertEqual(program.operation_order.count("stair-east"), 1)


@NEEDS_OCCT
class WholeStairExecutionTests(unittest.TestCase):
    """Three steps, 0.9 m run, 1.2 m wide, 0.6 m rise: one valid closed STEP solid of 0.432 m3, from the stair producer."""

    def test_three_steps_are_one_closed_stepped_solid_carrying_a_landing_on_its_top(self) -> None:
        landing = ElementRow("landing", "primary-support", "prism", {"base": {"datum": "stair-east-top"}},
                             {"profile": [[RUN, -0.6], [RUN + 1.0, -0.6], [RUN + 1.0, 0.6], [RUN, 0.6]], "height": 0.15}, BASIS)
        program, _ = _compile((landing, _flight(_on("RUN", 0.0), _on("RUN", RUN), {"level": GROUND}, {"level": UPPER})))
        receipt, entries, elapsed = _export(program, "stage-occt-whole-stair", "whole-stair@occt")

        self.assertLess(elapsed, 30.0, "in-process execution must not take a Rhino-sized time")
        self.assertEqual(sorted(receipt.physical_object_ids), ["obj-landing", "obj-stair-east"])
        self.assertEqual(sorted(entries), ["obj-landing", "obj-stair-east"])
        self.assertTrue(receipt.exact_artifact["exact_brep"])

        flight = entries["obj-stair-east"]
        self.assertEqual(flight.layers, ("archflow::building",))
        measure = occt_backend.measure_shape(flight.shape)
        self.assertTrue(measure.valid)
        self.assertEqual((measure.solid_count, measure.closed), (1, True))
        self.assertEqual(measure.face_count, 2 + 2 * COUNT + 2)                     # floor, three treads, three risers, the back, two side caps
        self.assertAlmostEqual(measure.volume, VOLUME, places=6)
        self.assertAlmostEqual(VOLUME, 0.432, places=9)
        # endpoints, width and height in the CAD frame (x, plan z, up y): from x=0 to x=0.9, 0.6 m each side, floor to 0.6 m
        for actual, expected in zip(measure.bbox_min + measure.bbox_max, (0.0, -0.6, 0.0, RUN, 0.6, HEIGHT)):
            self.assertAlmostEqual(actual, expected, places=5)
        # the step profile, not just its hull: inside each tread, outside above its nosing and beyond the width
        probes = {
            (0.15, 0.1, 0.0): "inside", (0.15, 0.3, 0.0): "outside",
            (0.45, 0.3, 0.0): "inside", (0.45, 0.5, 0.0): "outside",
            (0.75, 0.5, 0.0): "inside", (0.75, 0.7, 0.0): "outside",
            (0.45, 0.3, 0.59): "inside", (0.45, 0.3, 0.61): "outside",
            (0.45, 0.3, -0.59): "inside", (0.45, 0.3, -0.61): "outside",
            (-0.01, 0.1, 0.0): "outside", (0.91, 0.3, 0.0): "outside",
        }
        for point, expected in probes.items():
            self.assertEqual(occt_backend.classify_program_point(flight.shape, point), expected, point)
        # the receipt's own cold read agrees with the analytic predictor, which is the true hull of the stepped solid
        row = receipt.readback["obj-stair-east"]
        self.assertEqual((row["solid_count"], row["closed"], row["valid"]), (1, True, True))
        predicted = receipt.expected_bounds["obj-stair-east"]
        for actual, expected in zip(predicted["min"] + predicted["max"], (0.0, -0.6, 0.0, RUN, 0.6, HEIGHT)):
            self.assertAlmostEqual(actual, expected, places=9)

        # the published top is the whole solid's top: the landing bound to it starts exactly where the flight ends
        seated = occt_backend.measure_shape(entries["obj-landing"].shape)
        self.assertAlmostEqual(seated.bbox_min[2], HEIGHT, places=6)
        self.assertAlmostEqual(seated.bbox_max[2], HEIGHT + 0.15, places=6)
        self.assertAlmostEqual(seated.bbox_min[0], RUN, places=6)
        self.assertEqual(occt_backend.classify_program_point(entries["obj-landing"].shape, (RUN + 0.5, HEIGHT + 0.05, 0.0)), "inside")
        self.assertEqual(occt_backend.classify_program_point(flight.shape, (RUN + 0.5, HEIGHT + 0.05, 0.0)), "outside")

    def test_an_oblique_reversed_flight_on_a_base_offset_lands_on_its_references(self) -> None:
        # from SKEW@0.9 = (2.54, -0.28) back to SKEW@0.0 = (2.0, -1.0): the run descends the axis, so the flight climbs
        # towards the axis origin; it stands 0.3 m above the ground and its declared top is that offset above the upper level
        offset = 0.3
        row = _flight(_on("SKEW", RUN), _on("SKEW", 0.0), {"datum": GROUND, "offset": offset}, {"offset_from": {"level": UPPER, "offset": offset}})
        program, context = _compile((row,))
        self.assertAlmostEqual(context.datum_value("stair-east-top"), offset + HEIGHT)
        (operation,) = [op for op in program.proposal.operations if op.op_id == "stair-east"]
        self.assertAlmostEqual(_params(operation)["base_offset"], offset)
        receipt, entries, elapsed = _export(program, "stage-occt-skew-stair", "skew-stair@occt")

        self.assertLess(elapsed, 30.0)
        self.assertEqual(list(entries), ["obj-stair-east"])
        measure = occt_backend.measure_shape(entries["obj-stair-east"].shape)
        self.assertTrue(measure.valid)
        self.assertEqual((measure.solid_count, measure.closed, measure.face_count), (1, True, 10))
        self.assertAlmostEqual(measure.volume, VOLUME, places=6)
        # plan: start (2.54, -0.28), end (2.0, -1.0), unit run u = (-0.6, -0.8), normal n = (-0.8, 0.6), half width 0.6
        start, end, u, n = (2.54, -0.28), (2.0, -1.0), (-0.6, -0.8), (-0.8, 0.6)
        corners = [(p[0] + s * n[0] * 0.6, p[1] + s * n[1] * 0.6) for p in (start, end) for s in (-1.0, 1.0)]
        low = (min(c[0] for c in corners), min(c[1] for c in corners), offset)
        high = (max(c[0] for c in corners), max(c[1] for c in corners), offset + HEIGHT)
        for actual, expected in zip(measure.bbox_min + measure.bbox_max, low + high):
            self.assertAlmostEqual(actual, expected, places=5)
        predicted = receipt.expected_bounds["obj-stair-east"]                         # the predictor reports the CAD frame too
        for actual, expected in zip(predicted["min"] + predicted["max"], low + high):
            self.assertAlmostEqual(actual, expected, places=9)

        def at(along: float, up: float, across: float = 0.0) -> tuple[float, float, float]:
            return (start[0] + u[0] * along + n[0] * across, up, start[1] + u[1] * along + n[1] * across)

        # the lowest tread is at the `from` end, the highest at the `to` end; nothing below the base offset
        for k in range(COUNT):
            centre = (k + 0.5) * GOING
            self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(centre, offset + k * RISE + 0.1)), "inside", k)
            self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(centre, offset + (k + 1) * RISE + 0.1)), "outside", k)
            self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(centre, offset - 0.1)), "outside", k)
        self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(0.45, offset + 0.3, 0.59)), "inside")
        self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(0.45, offset + 0.3, 0.61)), "outside")
        self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(0.45, offset + 0.3, -0.61)), "outside")
        self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(-0.01, offset + 0.1)), "outside")
        self.assertEqual(occt_backend.classify_program_point(entries["obj-stair-east"].shape, at(RUN + 0.01, offset + 0.5)), "outside")


if __name__ == "__main__":
    unittest.main()
