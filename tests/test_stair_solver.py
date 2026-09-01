from __future__ import annotations

import copy
from dataclasses import replace
import unittest

from archflow.capabilities.stair_solver import (
    StairDimensionBand,
    StairDimensionValue,
    StairFlightConstraint,
    StairInterface,
    StairInterfaceRole,
    StairLayout,
    StairObligationKind,
    StairPlanEnvelope,
    StairPreferenceOrder,
    StairRiserTreadRule,
    StairRiserTreadRuleMode,
    StairSolveRequest,
    StairSolveResult,
    StairSolveStatus,
    StairSolverError,
    StairTerminalLandingOwnership,
    require_solved_stair_assembly,
    solve_stair,
)
from archflow.state.geometry_program import LengthUnit


EVIDENCE = ("evidence:project-stair-criteria",)
AUTHORITY = ("authority:project-stair-adoption",)


def _request(
    *,
    layout: StairLayout = StairLayout.STRAIGHT,
    lower: float = 0.0,
    upper: float = 3.0,
    envelope: tuple[float, float] = (20.0, 20.0),
    riser: tuple[float, float, float] = (0.15, 0.19, 0.175),
    tread: tuple[float, float, float] = (0.25, 0.32, 0.28),
    flight: tuple[int, int, int] = (3, 20, 9),
    lower_landing_ownership: StairTerminalLandingOwnership = (
        StairTerminalLandingOwnership.STAIR_ASSEMBLY
    ),
    upper_landing_ownership: StairTerminalLandingOwnership = (
        StairTerminalLandingOwnership.STAIR_ASSEMBLY
    ),
    missing: str | None = None,
) -> StairSolveRequest:
    values = {
        "width": StairDimensionValue(1.0, EVIDENCE, AUTHORITY),
        "riser_height": StairDimensionBand(*riser, EVIDENCE, AUTHORITY),
        "tread_depth": StairDimensionBand(*tread, EVIDENCE, AUTHORITY),
        "riser_tread_rule": StairRiserTreadRule(
            StairRiserTreadRuleMode.ADOPTED_BAND,
            0.55,
            0.70,
            0.63,
            EVIDENCE,
            AUTHORITY,
        ),
        "landing_depth": StairDimensionBand(0.9, 1.2, 1.0, EVIDENCE, AUTHORITY),
        "flight_risers": StairFlightConstraint(*flight, EVIDENCE, AUTHORITY),
        "preference_order": StairPreferenceOrder.LANDING_THEN_TREAD,
    }
    if missing is not None:
        values[missing] = None
    return StairSolveRequest(
        request_id="stair-a",
        length_unit=LengthUnit.METER,
        lower_interface=StairInterface(
            StairInterfaceRole.LOWER,
            "interface:stair-a-lower",
            "relation:stair-a-lower",
            "level:lower",
            "fact:lower-datum",
            lower,
        ),
        upper_interface=StairInterface(
            StairInterfaceRole.UPPER,
            "interface:stair-a-upper",
            "relation:stair-a-upper",
            "level:upper",
            "fact:upper-datum",
            upper,
        ),
        plan_envelope=StairPlanEnvelope(
            "envelope:stair-a",
            "relation:stair-a-envelope",
            (0.0, 0.0),
            envelope,
            EVIDENCE,
            AUTHORITY,
        ),
        layout=layout,
        lower_landing_ownership=lower_landing_ownership,
        upper_landing_ownership=upper_landing_ownership,
        **values,
    )


class StairSolverTests(unittest.TestCase):
    def test_reuses_height_semantics_and_closes_each_exact_datum(self) -> None:
        first = solve_stair(_request(upper=3.0))
        second = solve_stair(_request(upper=3.2))

        self.assertEqual(first.status, StairSolveStatus.SOLVED)
        self.assertEqual(second.status, StairSolveStatus.SOLVED)
        assert first.assembly is not None
        assert second.assembly is not None
        self.assertAlmostEqual(
            first.assembly.actual_riser_height * first.assembly.total_risers,
            3.0,
        )
        self.assertAlmostEqual(
            second.assembly.actual_riser_height * second.assembly.total_risers,
            3.2,
        )
        self.assertAlmostEqual(first.assembly.runs[-1].end_datum, 3.0)
        self.assertAlmostEqual(second.assembly.runs[-1].end_datum, 3.2)
        self.assertNotEqual(first.assembly.actual_riser_height, second.assembly.actual_riser_height)

    def test_inclusive_riser_boundary_is_solved(self) -> None:
        result = solve_stair(_request(upper=3.04, riser=(0.19, 0.19, 0.19)))
        self.assertEqual(result.status, StairSolveStatus.SOLVED)
        assert result.assembly is not None
        self.assertEqual(result.assembly.total_risers, 16)
        self.assertAlmostEqual(result.assembly.actual_riser_height, 0.19)

    def test_every_supported_layout_has_semantic_runs_landings_and_treads(self) -> None:
        expected = {
            StairLayout.STRAIGHT: (1, 2),
            StairLayout.QUARTER_TURN: (2, 3),
            StairLayout.HALF_TURN: (2, 3),
        }
        for layout, (run_count, landing_count) in expected.items():
            with self.subTest(layout=layout):
                result = solve_stair(_request(layout=layout))
                self.assertEqual(result.status, StairSolveStatus.SOLVED)
                assert result.assembly is not None
                self.assertEqual(len(result.assembly.runs), run_count)
                self.assertEqual(len(result.assembly.landings), landing_count)
                self.assertEqual(
                    len(result.assembly.treads),
                    sum(run.riser_count - 1 for run in result.assembly.runs),
                )
                self.assertLessEqual(result.assembly.plan_bounds_maximum[0], 20.0)
                self.assertLessEqual(result.assembly.plan_bounds_maximum[1], 20.0)

    def test_half_turn_odd_riser_count_uses_real_bounds_and_canonical_split(self) -> None:
        result = solve_stair(_request(layout=StairLayout.HALF_TURN, upper=3.0))
        self.assertEqual(result.status, StairSolveStatus.SOLVED)
        assert result.assembly is not None
        self.assertEqual(result.assembly.total_risers, 17)
        self.assertEqual(
            tuple(run.riser_count for run in result.assembly.runs),
            (9, 8),
        )
        plan_size = tuple(
            high - low
            for low, high in zip(
                result.assembly.plan_bounds_minimum,
                result.assembly.plan_bounds_maximum,
            )
        )
        goings = tuple(run.going for run in result.assembly.runs)
        self.assertAlmostEqual(plan_size[0], 2.0 * result.assembly.width)
        self.assertAlmostEqual(
            plan_size[1],
            result.assembly.actual_landing_depth
            + max(
                result.assembly.width,
                result.assembly.actual_landing_depth,
            )
            + max(goings),
        )

    def test_solution_is_deterministic_and_roundtrips_exactly(self) -> None:
        request = _request(layout=StairLayout.HALF_TURN)
        self.assertEqual(request.to_dict()["schema"], "StairSolveRequest@3")
        first = solve_stair(request)
        second = solve_stair(StairSolveRequest.from_dict(request.to_dict()))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.ref, second.ref)
        self.assertEqual(StairSolveResult.from_dict(first.to_dict()), first)
        assert first.assembly is not None
        self.assertEqual(first.assembly.source_request_digest, request.digest)
        self.assertIs(
            require_solved_stair_assembly(request, first.assembly),
            first.assembly,
        )

    def test_assembly_and_result_semantic_invariants_fail_closed(self) -> None:
        request = _request()
        result = solve_stair(request)
        assert result.assembly is not None
        assembly = result.assembly

        with self.assertRaisesRegex(StairSolverError, "datum endpoints"):
            replace(
                assembly,
                runs=(
                    replace(
                        assembly.runs[0],
                        end_datum=assembly.runs[0].end_datum + 1.0,
                    ),
                ),
            )
        with self.assertRaisesRegex(StairSolverError, "walking datum"):
            replace(
                assembly,
                treads=(
                    replace(
                        assembly.treads[0],
                        walking_datum=(
                            assembly.treads[0].walking_datum + 1.0
                        ),
                    ),
                    *assembly.treads[1:],
                ),
            )
        with self.assertRaisesRegex(StairSolverError, "tread count"):
            replace(assembly, treads=assembly.treads[:-1])
        with self.assertRaisesRegex(StairSolverError, "request digest"):
            replace(result, request_digest="f" * 64)
        with self.assertRaisesRegex(StairSolverError, "exact request"):
            require_solved_stair_assembly(
                request,
                replace(assembly, source_request_digest="f" * 64),
            )

        turning_request = _request(layout=StairLayout.HALF_TURN)
        turning = solve_stair(turning_request)
        assert turning.assembly is not None
        middle = next(
            item
            for item in turning.assembly.landings
            if item.role.value == "intermediate"
        )
        with self.assertRaisesRegex(StairSolverError, "intermediate landing"):
            replace(
                turning.assembly,
                landings=tuple(
                    replace(
                        item,
                        datum=item.datum + 1.0,
                        local_origin=(
                            item.local_origin[0],
                            item.local_origin[1],
                            item.local_origin[2] + 1.0,
                        ),
                    )
                    if item is middle
                    else item
                    for item in turning.assembly.landings
                ),
            )

    def test_adjoining_interfaces_own_terminal_landings_outside_envelope(self) -> None:
        request = _request(
            upper=3.0,
            envelope=(4.48, 1.0),
            flight=(17, 17, 17),
            missing="landing_depth",
            lower_landing_ownership=(
                StairTerminalLandingOwnership.ADJOINING_INTERFACE
            ),
            upper_landing_ownership=(
                StairTerminalLandingOwnership.ADJOINING_INTERFACE
            ),
        )

        result = solve_stair(request)

        self.assertEqual(result.status, StairSolveStatus.SOLVED)
        assert result.assembly is not None
        self.assertEqual(result.assembly.total_risers, 17)
        self.assertEqual(result.assembly.landings, ())
        self.assertIsNone(result.assembly.actual_landing_depth)
        self.assertAlmostEqual(result.assembly.runs[0].going, 4.48)
        self.assertAlmostEqual(result.assembly.plan_bounds_minimum[0], 0.0)
        self.assertAlmostEqual(result.assembly.plan_bounds_maximum[0], 4.48)
        self.assertEqual(
            StairSolveRequest.from_dict(request.to_dict()),
            request,
        )
        self.assertEqual(StairSolveResult.from_dict(result.to_dict()), result)

    def test_exact_floating_plan_boundary_does_not_escape_after_fitting(self) -> None:
        request = _request(
            upper=3.0,
            envelope=(5.8, 1.0),
            riser=(3.0 / 21.0, 3.0 / 21.0, 3.0 / 21.0),
            tread=(0.29, 0.29, 0.29),
            flight=(21, 21, 21),
            lower_landing_ownership=(
                StairTerminalLandingOwnership.ADJOINING_INTERFACE
            ),
            upper_landing_ownership=(
                StairTerminalLandingOwnership.ADJOINING_INTERFACE
            ),
        )

        result = solve_stair(request)

        self.assertEqual(result.status, StairSolveStatus.SOLVED)
        assert result.assembly is not None
        self.assertLessEqual(
            result.assembly.plan_bounds_maximum[0],
            request.plan_envelope.maximum[0] + 1.0e-9,
        )

    def test_required_landing_depth_is_unknown_not_an_invented_default(self) -> None:
        result = solve_stair(_request(missing="landing_depth"))
        self.assertEqual(result.status, StairSolveStatus.UNKNOWN)
        self.assertEqual(result.reason_code, "missing_project_constraints")
        self.assertEqual(result.missing_fields, ("landing_depth",))
        self.assertIsNone(result.assembly)

        turning = solve_stair(
            _request(
                layout=StairLayout.HALF_TURN,
                lower_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
                upper_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
                missing="landing_depth",
            )
        )
        self.assertEqual(turning.status, StairSolveStatus.UNKNOWN)
        self.assertEqual(turning.missing_fields, ("landing_depth",))

    def test_no_integer_riser_count_is_unsat(self) -> None:
        result = solve_stair(_request(upper=1.0, riser=(0.30, 0.31, 0.305)))
        self.assertEqual(result.status, StairSolveStatus.UNSAT)
        self.assertEqual(result.reason_code, "no_integer_riser_count")

    def test_no_legal_flight_distribution_is_unsat(self) -> None:
        result = solve_stair(
            _request(
                layout=StairLayout.STRAIGHT,
                upper=3.0,
                flight=(3, 10, 9),
            )
        )
        self.assertEqual(result.status, StairSolveStatus.UNSAT)
        self.assertEqual(result.reason_code, "no_flight_distribution")

    def test_too_small_plan_envelope_is_unsat(self) -> None:
        result = solve_stair(
            _request(layout=StairLayout.HALF_TURN, envelope=(1.5, 1.5))
        )
        self.assertEqual(result.status, StairSolveStatus.UNSAT)
        self.assertEqual(result.reason_code, "plan_envelope_too_small")

    def test_combined_riser_tread_rule_is_evidence_bound_and_fail_closed(self) -> None:
        payload = _request(
            upper=3.04,
            riser=(0.19, 0.19, 0.19),
        ).to_dict()
        payload["tread_depth"] = StairDimensionBand(
            0.35,
            0.35,
            0.35,
            EVIDENCE,
            AUTHORITY,
        ).to_dict()
        result = solve_stair(StairSolveRequest.from_dict(payload))
        self.assertEqual(result.status, StairSolveStatus.UNSAT)
        self.assertEqual(result.reason_code, "riser_tread_rule_unsatisfied")

        unresolved = solve_stair(_request(missing="riser_tread_rule"))
        self.assertEqual(unresolved.status, StairSolveStatus.UNKNOWN)
        self.assertEqual(unresolved.missing_fields, ("riser_tread_rule",))

        not_applicable_payload = _request().to_dict()
        not_applicable_payload["riser_tread_rule"] = StairRiserTreadRule(
            StairRiserTreadRuleMode.NOT_APPLICABLE,
            None,
            None,
            None,
            ("evidence:historic-stair-rule-disposition",),
            ("authority:historic-stair-rule-disposition",),
        ).to_dict()
        not_applicable = solve_stair(
            StairSolveRequest.from_dict(not_applicable_payload)
        )
        self.assertEqual(not_applicable.status, StairSolveStatus.SOLVED)

    def test_unit_is_part_of_request_identity(self) -> None:
        request = _request()
        other = replace(request, length_unit=LengthUnit.MILLIMETER)
        self.assertNotEqual(request.digest, other.digest)
        self.assertEqual(other.to_dict()["length_unit"], "millimeter")

    def test_single_host_owned_riser_is_typed_unsat_not_a_raw_crash(self) -> None:
        result = solve_stair(
            _request(
                upper=0.17,
                riser=(0.17, 0.17, 0.17),
                flight=(1, 1, 1),
                lower_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
                upper_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
                missing="landing_depth",
            )
        )
        self.assertEqual(result.status, StairSolveStatus.UNSAT)
        self.assertEqual(result.reason_code, "no_walking_surface_members")

    def test_all_downstream_coordination_remains_open(self) -> None:
        result = solve_stair(_request())
        self.assertEqual(
            tuple(item.kind for item in result.obligations),
            tuple(StairObligationKind),
        )
        self.assertTrue(all(item.status == "OPEN" for item in result.obligations))
        self.assertTrue(all(not item.to_dict()["verification_authority"] for item in result.obligations))

    def test_schema_drift_and_authority_escalation_are_rejected(self) -> None:
        payload = _request().to_dict()
        payload["design_authority"] = True
        with self.assertRaisesRegex(StairSolverError, "authority"):
            StairSolveRequest.from_dict(payload)

        previous_shape = _request().to_dict()
        previous_shape["schema"] = "StairSolveRequest@2"
        with self.assertRaisesRegex(StairSolverError, "unsupported"):
            StairSolveRequest.from_dict(previous_shape)

        result_payload = solve_stair(_request()).to_dict()
        nested = copy.deepcopy(result_payload)
        assert isinstance(nested["assembly"], dict)
        nested["assembly"]["persistence_authority"] = True
        with self.assertRaisesRegex(StairSolverError, "authority"):
            StairSolveResult.from_dict(nested)

        missing_owned_depth = solve_stair(_request()).to_dict()
        assert isinstance(missing_owned_depth["assembly"], dict)
        missing_owned_depth["assembly"]["actual_landing_depth"] = None
        with self.assertRaisesRegex(StairSolverError, "exactly when"):
            StairSolveResult.from_dict(missing_owned_depth)

        invented_interface_depth = solve_stair(
            _request(
                missing="landing_depth",
                lower_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
                upper_landing_ownership=(
                    StairTerminalLandingOwnership.ADJOINING_INTERFACE
                ),
            )
        ).to_dict()
        assert isinstance(invented_interface_depth["assembly"], dict)
        invented_interface_depth["assembly"]["actual_landing_depth"] = 1.0
        with self.assertRaisesRegex(StairSolverError, "exactly when"):
            StairSolveResult.from_dict(invented_interface_depth)

        extra = solve_stair(_request()).to_dict()
        extra["unreviewed_acceptance"] = True
        with self.assertRaisesRegex(ValueError, "schema drifted"):
            StairSolveResult.from_dict(extra)

    def test_present_parameters_cannot_drop_evidence_or_authority_refs(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            StairDimensionValue(1.0, (), AUTHORITY)
        with self.assertRaisesRegex(ValueError, "authority_refs"):
            StairDimensionBand(0.15, 0.19, 0.175, EVIDENCE, ())
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            StairFlightConstraint(3, 20, 9, (), AUTHORITY)
        with self.assertRaisesRegex(ValueError, "authority_refs"):
            StairPlanEnvelope(
                "envelope:stair-a",
                "relation:stair-a-envelope",
                (0.0, 0.0),
                (20.0, 20.0),
                EVIDENCE,
                (),
            )


if __name__ == "__main__":
    unittest.main()
