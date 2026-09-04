"""M096: a datum-bound base_level moves geometry, not just a parameter.

P090 lowered "derived, not restated" to the coordinate level in the
compiler, but extrusion and loft profiles carried their elevation inside
the profile points, so a bound datum never reached the geometry. Here the
same datum flows from the producer through the compiler into the sandbox
realization, the analytic CAD bounds, and the emitted Rhino script.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.adapters.cad_program import (
    CadTranslationError,
    expected_object_bounds,
    lift_to_base_level,
    translate_to_rhino_python,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalStatus,
    proposal_authoring_output,
)
from archive.archflow.realization.sandbox import realize_geometry
from archflow.state.geometry_program import (
    DatumBinding,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from archive.tests.test_geometry_proposal_producer import _ScriptedProvider
from archive.tests.test_production_wiring import _ProducerFixture

BASE = 1.25
PROFILE = [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 2.0], [0.0, 0.0, 2.0]]


class LiftHelperTests(unittest.TestCase):
    def test_without_base_level_points_pass_through(self) -> None:
        self.assertEqual(
            lift_to_base_level(PROFILE, {}, "op"),
            [tuple(p) for p in PROFILE],
        )

    def test_lift_moves_lowest_point_onto_the_level(self) -> None:
        lifted = lift_to_base_level(PROFILE, {"base_level": BASE}, "op")
        self.assertEqual(min(p[1] for p in lifted), BASE)
        self.assertEqual([p[0] for p in lifted], [p[0] for p in PROFILE])

    def test_non_finite_level_fails_typed(self) -> None:
        with self.assertRaises(CadTranslationError):
            lift_to_base_level(PROFILE, {"base_level": float("nan")}, "op")
        with self.assertRaises(CadTranslationError):
            lift_to_base_level(PROFILE, {"base_level": "1.0"}, "op")


class DatumReachesGeometryTests(_ProducerFixture):
    def _extrusion_proposal(self):
        floor = next(op for op in self.proposal.operations if op.op_id == "floor")
        extrusion = replace(
            floor,
            kind=GeometryOperationKind.EXTRUSION,
            parameters=(
                GeometryParameter.create(
                    name="profile",
                    kind=GeometryParameterKind.POINTS3,
                    value=PROFILE,
                    unit=LengthUnit.METER,
                ),
                GeometryParameter.create(
                    name="vector",
                    kind=GeometryParameterKind.VECTOR3,
                    value=[0.0, 0.5, 0.0],
                    unit=LengthUnit.METER,
                ),
            ),
        )
        operations = tuple(
            extrusion if op.op_id == "floor" else op
            for op in self.proposal.operations
        )
        return replace(self.proposal, operations=operations)

    async def test_bound_base_level_moves_sandbox_cad_and_script(self) -> None:
        datum = InterfaceDatum.create(
            datum_id="slab-level", kind=InterfaceDatumKind.LEVEL,
            published_by="clearance", value=BASE, unit=LengthUnit.METER,
        )
        binding = DatumBinding(
            binding_id="bind-slab", datum_id="slab-level",
            op_id="floor", parameter_name="base_level",
        )
        result = await self._produce(
            _ScriptedProvider((proposal_authoring_output(self._extrusion_proposal()),)),
            interface_datums=(datum,),
            datum_bindings=(binding,),
        )
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        program = result.program
        assert program is not None

        floor_op = next(op for op in program.proposal.operations if op.op_id == "floor")
        params = {p.name: p.value_json for p in floor_op.parameters}
        self.assertEqual(params["base_level"], str(BASE))
        # the authored profile still sits at Y = 0; the level is not restated
        self.assertIn("[0.0,0.0,0.0]", params["profile"].replace(" ", ""))

        # The room fixture's boolean shell is outside the analytic replay's
        # domain, so the bounds check lives in AnalyticBoundsTests below.
        script = translate_to_rhino_python(program).script
        self.assertIn(f"(0.0,0.0,{BASE})", script.replace(" ", ""))

        realized = realize_geometry(program, workspace_id="base-level-test")
        floor_bounds = next(
            item.bounds for item in realized.scene.objects if item.object_id == "floor"
        )
        self.assertAlmostEqual(float(floor_bounds.minimum[1]), BASE, places=6)


class AnalyticBoundsTests(unittest.TestCase):
    """The CAD bounds replay lifts an extrusion and a loft alike."""

    def _program(self, kind: str, **params):
        from tests.test_cad_program import op, program

        return program(op("piece", kind, ["piece-object"], **params))

    def test_extrusion_and_loft_bounds_follow_base_level(self) -> None:
        extrusion = self._program(
            "extrusion", profile=PROFILE, vector=[0.0, 0.5, 0.0], base_level=BASE
        )
        low = expected_object_bounds(extrusion)["piece-object"]["bbox_min"]
        self.assertAlmostEqual(low[1], BASE, places=9)
        ring = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 1.0], [0.0, 0.0, 1.0]]
        upper = [[p[0], 2.0, p[2]] for p in ring]
        loft = self._program(
            "loft", profiles=ring + upper, profile_size=4, base_level=BASE
        )
        bounds = expected_object_bounds(loft)["piece-object"]
        self.assertAlmostEqual(bounds["bbox_min"][1], BASE, places=9)
        self.assertAlmostEqual(bounds["bbox_max"][1], BASE + 2.0, places=9)


if __name__ == "__main__":
    unittest.main()
