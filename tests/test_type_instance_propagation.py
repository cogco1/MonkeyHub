"""P099: type to instance propagation.

An instance binds to a template edition through a dependency edge; a new
edition reopens exactly the instances' closure and retains the rest by
digest. A type placed N times compiles to one definition and N
placements (ARRAY), and the CAD translation carries one block definition
per repeated member instead of per-instance coordinates.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.adapters.cad_program import expected_object_bounds, expected_object_semantics
from archflow.capabilities.component_library import (
    EditionPropagation,
    propagate_template_edition,
    record_edition_propagation,
    revalidation_closure,
)
from archflow.capabilities.opening_solver import solve_window
from archflow.capabilities.wall_solver import OpeningKind, OpeningRequest, WallSolverError, solve_wall
from archflow.compilers.geometry import compile_geometry_program
from archflow.project import FilesystemProjectRepository, PersistenceArea, PersistenceDestination
from archflow.state.component_template import (
    ComponentInstance,
    ComponentTemplateError,
    instance_edition_edge,
    template_edition_ref,
)
from archflow.state.geometry_program import (
    AssemblyRole,
    GeometryOperationKind,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from archflow.state.operational_state import DependencyEdge, DependencyEffect
from tests.test_component_templates import _stair_template as _template
from tests.test_geometry_compiler import COMMITMENT, _proposal, _state
from tests.test_wall_window_families import BASE, BINDING, LEVEL, WINDOW_TYPE, _only, _wall

TEMPLATE_REF = "project://demo/runs/run/records/component-template-aa.json"


def _instance(instance_id: str, template_id: str = "palladian-exterior-stair", edition: int = 1, **overrides) -> ComponentInstance:
    fields = dict(
        instance_id=instance_id, template_id=template_id, edition=edition, template_ref=TEMPLATE_REF,
        project_id="demo", run_id="run", placement_refs=(f"operation:{instance_id}",), host_ref="object:obj-wall-west",
    )
    fields.update(overrides)
    return ComponentInstance(**fields)


class InstanceRecordTests(unittest.TestCase):
    def test_instance_names_template_and_edition_and_round_trips(self) -> None:
        instance = _instance("stair-west")
        self.assertEqual(instance.edition_ref, "template:palladian-exterior-stair-edition-1")
        self.assertEqual(instance.ref, "instance:stair-west")
        self.assertEqual(ComponentInstance.from_dict(instance.to_dict()), instance)
        self.assertEqual(len(instance.digest), 64)
        with self.assertRaises(ComponentTemplateError):
            _instance("bad", edition=0)
        with self.assertRaises(ComponentTemplateError):
            _instance("bad", placement_refs=("frame-window-left-bottom",))   # a bare id is not a reference
        with self.assertRaises(ComponentTemplateError):
            template_edition_ref("x", True)

    def test_binding_is_a_revalidating_dependency_edge(self) -> None:
        edge = instance_edition_edge(_instance("stair-west"))
        self.assertIsInstance(edge, DependencyEdge)
        self.assertEqual(edge.upstream_ref, "template:palladian-exterior-stair-edition-1")
        self.assertEqual(edge.downstream_ref, "instance:stair-west")
        self.assertIs(edge.effect, DependencyEffect.REQUIRES_REVALIDATION)
        self.assertEqual(edge.source_ref, TEMPLATE_REF)


class PropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stair_v1 = _template()
        self.stair_v2 = replace(self.stair_v1, edition=2, predecessor_ref=TEMPLATE_REF)
        self.instances = (
            _instance("stair-east"), _instance("stair-north"), _instance("stair-west"),
            _instance("window-left", template_id="palladian-window-frame"),
            _instance("stair-old-south", edition=1),
        )

    def test_new_edition_reopens_exactly_the_old_editions_instances(self) -> None:
        propagation = propagate_template_edition(self.instances, self.stair_v2, promoted_ref=TEMPLATE_REF)
        self.assertEqual(propagation.reopened, ("stair-east", "stair-north", "stair-old-south", "stair-west"))
        self.assertEqual([i for i, _ in propagation.retained], ["window-left"])
        digest = next(i.digest for i in self.instances if i.instance_id == "window-left")
        self.assertEqual(propagation.retained[0][1], digest)
        self.assertEqual(propagation.from_editions, (1,))
        self.assertIn("template:palladian-exterior-stair-edition-1", propagation.closure)
        payload = propagation.to_dict()
        self.assertEqual(payload["effect"], "requires_revalidation")
        self.assertFalse(payload["canonical_write_authority"])

    def test_closure_reaches_downstream_programs_and_stops_at_supporting_edges(self) -> None:
        downstream = (
            DependencyEdge("instance:stair-west", "program:west-structure", "realized_by", TEMPLATE_REF, DependencyEffect.REQUIRES_REVALIDATION),
            DependencyEdge("program:west-structure", "receipt:stage-5", "accepted_as", TEMPLATE_REF, DependencyEffect.INVALIDATES),
            DependencyEdge("instance:stair-east", "note:east-photo", "documented_by", TEMPLATE_REF, DependencyEffect.SUPPORTS_ONLY),
        )
        propagation = propagate_template_edition(self.instances, self.stair_v2, promoted_ref=TEMPLATE_REF, dependencies=downstream)
        self.assertIn("program:west-structure", propagation.closure)
        self.assertIn("receipt:stage-5", propagation.closure)
        self.assertNotIn("note:east-photo", propagation.closure)
        self.assertEqual(revalidation_closure(("node:a",), (DependencyEdge("node:a", "node:b", "r", TEMPLATE_REF, DependencyEffect.SUPPORTS_ONLY),)), ("node:a",))

    def test_same_edition_reopens_nothing_and_backward_edition_fails_typed(self) -> None:
        propagation = propagate_template_edition(self.instances, self.stair_v1, promoted_ref=TEMPLATE_REF)
        self.assertEqual(propagation.reopened, ())
        self.assertEqual(len(propagation.retained), 5)
        with self.assertRaises(ComponentTemplateError):
            propagate_template_edition(
                (_instance("stair-future", edition=3),), self.stair_v2, promoted_ref=TEMPLATE_REF,
            )
        with self.assertRaises(ComponentTemplateError):
            propagate_template_edition((_instance("a"), _instance("a")), self.stair_v2, promoted_ref=TEMPLATE_REF)

    def test_propagation_is_recorded_without_touching_programs(self) -> None:
        propagation = propagate_template_edition(self.instances, self.stair_v2, promoted_ref=TEMPLATE_REF)
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run")
            destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run")
            ref = record_edition_propagation(repository, run=run, destination=destination, propagation=propagation)
            payload = repository.load_json(ref)
            self.assertEqual(payload["schema"], EditionPropagation.SCHEMA)
            self.assertEqual(payload["reopened_instance_ids"], list(propagation.reopened))


def _level() -> InterfaceDatum:
    return InterfaceDatum.create(datum_id=LEVEL, kind=InterfaceDatumKind.LEVEL, published_by="seat-coordination", value=BASE, unit=LengthUnit.METER)


class ArrayedInstanceTests(unittest.TestCase):
    """A window type placed eight times: one definition, eight placements."""

    def setUp(self) -> None:
        self.request = OpeningRequest(opening_id="attic", kind=OpeningKind.WINDOW, along=2.0, width=0.78, sill=8.08, head=8.93,
                                      binding_id=BINDING, count=8, step=2.4)
        self.wall = solve_wall(_wall(), (self.request,))
        self.window = solve_window(self.wall.voids[0], WINDOW_TYPE, binding_id=BINDING)

    def test_eight_placements_are_one_definition_and_one_array_per_member(self) -> None:
        kinds = [op.kind for op in self.wall.operations]
        # the wall repeats its void per placement (booleans consume solids, not block instances)
        self.assertEqual(kinds.count(GeometryOperationKind.EXTRUSION), 9)          # wall + eight tools
        self.assertEqual(kinds.count(GeometryOperationKind.BOOLEAN_INTERSECTION), 8)
        self.assertEqual(kinds.count(GeometryOperationKind.ARRAY), 0)
        self.assertEqual(self.wall.voids[0].tool_object_id, "obj-wall-west-void-attic-0")
        self.assertEqual(len(self.wall.voids[0].aperture_object_ids), 8)
        self.assertEqual(self.window.assembly.objects_for(AssemblyRole.HOST_CUT), self.wall.voids[0].aperture_object_ids)
        # the window type is authored once and placed eight times
        self.assertEqual(len([op for op in self.window.operations if op.kind is GeometryOperationKind.EXTRUSION]), 5)
        arrays = [op for op in self.window.operations if op.kind is GeometryOperationKind.ARRAY]
        self.assertEqual(len(arrays), 5)
        params = {p.name: json.loads(p.value_json) for p in arrays[0].parameters}
        self.assertEqual(params["count"], 8)
        self.assertEqual(params["step"], [0.0, 0.0, 2.4])
        self.assertEqual(self.window.assembly.objects_for(AssemblyRole.GLAZING), ("obj-glazing-attic-array",))
        self.assertEqual(len(self.window.datum_bindings), 5)                         # definitions bind the datum; arrays do not
        self.assertEqual(self.request.instances()[-1], (2.0 - 0.39 + 7 * 2.4, 2.0 + 0.39 + 7 * 2.4))

    def test_placements_are_validated_one_by_one(self) -> None:
        with self.assertRaises(WallSolverError):
            solve_wall(_wall(), (replace(self.request, count=9),))        # the ninth placement leaves the wall
        with self.assertRaises(WallSolverError):
            OpeningRequest(opening_id="x", kind=OpeningKind.WINDOW, along=2.0, width=0.78, sill=1.0, head=2.0, binding_id=BINDING, count=2, step=0.5)
        abutment = ((-10.754, 11.287, -6.69), (-10.19, 13.16, 6.69))
        with self.assertRaises(WallSolverError) as caught:
            solve_wall(_wall(), (self.request,), exclusions=(abutment,), base_elevation=BASE)
        self.assertIn("placement 1 intersects exclusion 0", str(caught.exception))

    def test_compiles_with_family_bounds_and_one_block_definition_per_member(self) -> None:
        state = _state()
        operations = self.wall.operations + self.window.operations
        proposal = _only(_proposal(state, extra_operations=operations), operations, (self.window.assembly,))
        result = compile_geometry_program(
            state, proposal, active_commitment_refs=(COMMITMENT,),
            interface_datums=(_level(),), datum_bindings=self.wall.datum_bindings + self.window.datum_bindings,
        )
        self.assertIsNotNone(result.program, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues])
        bounds = expected_object_bounds(result.program)
        pane = bounds["obj-glazing-attic-array"]
        self.assertAlmostEqual(pane["bbox_min"][2], -10.71 + 2.0 - 0.39 + 0.09)
        self.assertAlmostEqual(pane["bbox_max"][2], -10.71 + 2.0 + 0.39 + 7 * 2.4 - 0.09)
        self.assertAlmostEqual(pane["bbox_min"][1], BASE + 8.08 + 0.09)
        cut = bounds["obj-wall-west-cut"]
        self.assertAlmostEqual(cut["bbox_max"][1], BASE + 9.818)
        self.assertNotIn("obj-glazing-attic", bounds)                             # the definition is consumed by its array
        semantics = expected_object_semantics(result.program)
        blocks = semantics["blocks"]
        self.assertEqual(blocks["archflow-family-glazing-attic-array"], 8)
        self.assertEqual(sum(1 for name in blocks if name.startswith("archflow-family-frame-attic-")), 4)
        self.assertEqual(len(blocks), 5)                                             # one block per repeated member, none for voids
        self.assertEqual(len(bounds["obj-wall-west-aperture-attic-3"]["bbox_min"]), 3)


if __name__ == "__main__":
    unittest.main()
