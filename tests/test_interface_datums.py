"""P090: interface datum co-derivation — contact correct by construction."""

from __future__ import annotations

import unittest

from archflow.capabilities.geometry_proposal import (
    load_compiled_geometry_program,
)
from archflow.compilers.geometry import (
    GeometryCompileStatus,
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state.geometry_program import (
    DatumBinding,
    GeometryParameterKind,
    GeometryProgramError,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
    verify_datum_directions,
)
from tests.test_geometry_compiler import COMMITMENT, _codes, _proposal, _state


def _level(datum_id: str = "wall-bearing-level", value: float = 3.33):
    return InterfaceDatum.create(
        datum_id=datum_id,
        kind=InterfaceDatumKind.LEVEL,
        published_by="wall",
        value=value,
        unit=LengthUnit.METER,
    )


def _series(step: float = 0.155):
    return InterfaceDatum.create(
        datum_id="riser-series",
        kind=InterfaceDatumKind.SERIES,
        published_by="wall",
        value={"start": 0.0, "step": step, "count": 23},
        unit=LengthUnit.METER,
    )


def _binding(
    binding_id: str,
    *,
    datum_id: str,
    op_id: str,
    parameter_name: str,
    component=None,
) -> DatumBinding:
    return DatumBinding(
        binding_id=binding_id,
        datum_id=datum_id,
        op_id=op_id,
        parameter_name=parameter_name,
        component=component,
    )


class InterfaceDatumContractTests(unittest.TestCase):
    def test_level_resolves_and_rejects_components(self) -> None:
        datum = _level()
        kind, value = datum.resolve(None)
        self.assertIs(kind, GeometryParameterKind.NUMBER)
        self.assertEqual(value, 3.33)
        with self.assertRaises(GeometryProgramError):
            datum.resolve("origin")

    def test_plane_resolves_origin_and_normal(self) -> None:
        datum = InterfaceDatum.create(
            datum_id="cheek-step-plane",
            kind=InterfaceDatumKind.PLANE,
            published_by="cheek",
            value={"origin": [0, 0, 1.542], "normal": [0, 0, 1]},
        )
        kind, origin = datum.resolve("origin")
        self.assertIs(kind, GeometryParameterKind.VECTOR3)
        self.assertEqual(origin, [0.0, 0.0, 1.542])
        _, normal = datum.resolve("normal")
        self.assertEqual(normal, [0.0, 0.0, 1.0])
        with self.assertRaises(GeometryProgramError):
            datum.resolve(0)

    def test_plane_zero_normal_fails_closed(self) -> None:
        with self.assertRaises(GeometryProgramError):
            InterfaceDatum.create(
                datum_id="bad-plane",
                kind=InterfaceDatumKind.PLANE,
                published_by="cheek",
                value={"origin": [0, 0, 0], "normal": [0, 0, 0]},
            )

    def test_series_resolves_indexed_values(self) -> None:
        datum = _series()
        kind, value = datum.resolve(10)
        self.assertIs(kind, GeometryParameterKind.NUMBER)
        self.assertAlmostEqual(value, 1.55)
        with self.assertRaises(GeometryProgramError):
            datum.resolve(23)
        with self.assertRaises(GeometryProgramError):
            datum.resolve(None)
        with self.assertRaises(GeometryProgramError):
            datum.resolve(True)

    def test_series_count_fails_closed(self) -> None:
        for count in (0, -1, 1.5, True):
            with self.assertRaises((GeometryProgramError, TypeError)):
                InterfaceDatum.create(
                    datum_id="bad-series",
                    kind=InterfaceDatumKind.SERIES,
                    published_by="wall",
                    value={"start": 0.0, "step": 0.1, "count": count},
                )

    def test_round_trip(self) -> None:
        for datum in (_level(), _series()):
            self.assertEqual(
                InterfaceDatum.from_dict(datum.to_dict()), datum
            )
        binding = _binding(
            "seat", datum_id="riser-series", op_id="leaf",
            parameter_name="seat_level", component=3,
        )
        self.assertEqual(DatumBinding.from_dict(binding.to_dict()), binding)


class DatumCompileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _state()
        self.proposal = _proposal(self.state)

    def _compile(self, datums, bindings):
        return compile_geometry_program(
            self.state,
            self.proposal,
            active_commitment_refs=(COMMITMENT,),
            interface_datums=datums,
            datum_bindings=bindings,
        )

    def test_bound_parameter_is_derived_not_restated(self) -> None:
        result = self._compile(
            (_level(),),
            (
                _binding(
                    "leaf-seat",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="bearing_level",
                ),
            ),
        )
        self.assertIs(
            result.receipt.status, GeometryCompileStatus.COMPILED
        )
        leaf = next(
            item
            for item in result.program.proposal.operations
            if item.op_id == "leaf"
        )
        bound = {item.name: item for item in leaf.parameters}
        self.assertIn("bearing_level", bound)
        self.assertEqual(bound["bearing_level"].value_json, "3.33")
        self.assertEqual(
            result.receipt.proposal_digest, self.proposal.proposal_digest
        )
        self.assertNotEqual(
            result.program.proposal.proposal_digest,
            self.proposal.proposal_digest,
        )
        rendered = result.program.to_dict()
        self.assertEqual(len(rendered["interface_datums"]), 1)
        self.assertEqual(len(rendered["datum_bindings"]), 1)

    def test_restated_literal_fails_closed(self) -> None:
        result = self._compile(
            (_level(),),
            (
                _binding(
                    "leaf-thickness",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="thickness",
                ),
            ),
        )
        self.assertIs(
            result.receipt.status, GeometryCompileStatus.REJECTED
        )
        self.assertIn(
            GeometryIssueCode.RESTATED_DATUM_PARAMETER, _codes(result)
        )

    def test_unknown_datum_and_operation_fail_closed(self) -> None:
        result = self._compile(
            (_level(),),
            (
                _binding(
                    "ghost-datum",
                    datum_id="missing_datum",
                    op_id="leaf",
                    parameter_name="a_level",
                ),
                _binding(
                    "ghost-op",
                    datum_id="wall-bearing-level",
                    op_id="missing_op",
                    parameter_name="b_level",
                ),
            ),
        )
        self.assertIs(
            result.receipt.status, GeometryCompileStatus.REJECTED
        )
        codes = _codes(result)
        self.assertIn(GeometryIssueCode.UNKNOWN_DATUM, codes)
        self.assertIn(GeometryIssueCode.INVALID_DATUM_BINDING, codes)

    def test_double_binding_of_one_parameter_fails_closed(self) -> None:
        result = self._compile(
            (_level(),),
            (
                _binding(
                    "first",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="seat_level",
                ),
                _binding(
                    "second",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="seat_level",
                ),
            ),
        )
        self.assertIs(
            result.receipt.status, GeometryCompileStatus.REJECTED
        )
        self.assertIn(
            GeometryIssueCode.INVALID_DATUM_BINDING, _codes(result)
        )

    def test_no_datums_is_identity(self) -> None:
        plain = compile_geometry_program(
            self.state,
            self.proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        explicit = self._compile((), ())
        self.assertEqual(
            plain.program.program_digest, explicit.program.program_digest
        )

    def test_riser_series_change_propagates_to_exact_closure(self) -> None:
        bindings = (
            _binding(
                "frame-seat",
                datum_id="riser-series",
                op_id="frame",
                parameter_name="seat_level",
                component=10,
            ),
            _binding(
                "leaf-seat",
                datum_id="riser-series",
                op_id="leaf",
                parameter_name="seat_level",
                component=11,
            ),
        )
        first = self._compile((_series(0.155),), bindings)
        second = self._compile((_series(0.160),), bindings)
        self.assertIs(first.receipt.status, GeometryCompileStatus.COMPILED)
        self.assertIs(second.receipt.status, GeometryCompileStatus.COMPILED)
        before = {
            item.object_id: item.object_digest
            for item in first.program.objects
        }
        after = {
            item.object_id: item.object_digest
            for item in second.program.objects
        }
        changed = {
            object_id
            for object_id in before
            if before[object_id] != after[object_id]
        }
        self.assertEqual(changed, {"frame", "leaf"})
        self.assertNotEqual(
            first.program.program_digest, second.program.program_digest
        )


class DatumDirectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = _proposal(_state())

    def test_declared_edge_and_self_consumption_pass(self) -> None:
        datums = (_level(), )
        bindings = (
            _binding(
                "leaf-seat",
                datum_id="wall-bearing-level",
                op_id="leaf",
                parameter_name="seat_level",
            ),
        )
        violations = verify_datum_directions(
            datums, bindings, self.proposal.operations, (("wall", "leaf"),)
        )
        self.assertEqual(violations, ())
        self_bound = (
            _binding(
                "wall-own",
                datum_id="wall-bearing-level",
                op_id="wall",
                parameter_name="own_level",
            ),
        )
        self.assertEqual(
            verify_datum_directions(
                datums, self_bound, self.proposal.operations, ()
            ),
            (),
        )

    def test_missing_edge_is_a_violation(self) -> None:
        violations = verify_datum_directions(
            (_level(),),
            (
                _binding(
                    "leaf-seat",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="seat_level",
                ),
            ),
            self.proposal.operations,
            (),
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("no declared edge", violations[0])


class CompiledProgramReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _state()
        self.proposal = _proposal(self.state)

    def test_datum_program_round_trips(self) -> None:
        result = compile_geometry_program(
            self.state,
            self.proposal,
            active_commitment_refs=(COMMITMENT,),
            interface_datums=(_level(),),
            datum_bindings=(
                DatumBinding(
                    binding_id="leaf-seat",
                    datum_id="wall-bearing-level",
                    op_id="leaf",
                    parameter_name="seat_level",
                ),
            ),
        )
        rendered = result.program.to_dict()
        reloaded = load_compiled_geometry_program(rendered)
        self.assertEqual(reloaded.to_dict(), rendered)
        self.assertEqual(len(reloaded.interface_datums), 1)

    def test_legacy_v2_record_reloads_with_empty_datums(self) -> None:
        plain = compile_geometry_program(
            self.state,
            self.proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        rendered = plain.program.to_dict()
        legacy = {
            key: value
            for key, value in rendered.items()
            if key not in ("interface_datums", "datum_bindings")
        }
        legacy["schema"] = "CompiledGeometryProgram@2"
        reloaded = load_compiled_geometry_program(legacy)
        self.assertEqual(reloaded.interface_datums, ())
        self.assertEqual(reloaded.datum_bindings, ())
        self.assertEqual(
            reloaded.program_digest, plain.program.program_digest
        )


if __name__ == "__main__":
    unittest.main()
