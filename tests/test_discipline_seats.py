"""P095: discipline seats — handover, projection, write scope, schedule.

The three-seat demonstration runs through the real producer: the
structure seat publishes a bearing level, the envelope seat consumes it
through a compiled handover and derives from it, and the detail seat is
refused when it reaches into the envelope subtree.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.capabilities.discipline_seats import (
    DeclaredEngagement,
    HandoverKind,
    check_handover_exclusions,
    SeatError,
    SeatSpec,
    ancestors,
    compile_handover,
    owned_subtree,
    project_seat_context,
    schedule_seats,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalStatus,
    proposal_authoring_output,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import (
    DevelopmentCoordinationStatus,
    DevelopmentDiscipline,
    DevelopmentObligationStatus,
)
from archflow.state.geometry_program import (
    DatumBinding,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from tests.test_geometry_compiler import COMMITMENT
from tests.test_geometry_proposal_producer import _ScriptedProvider
from tests.test_production_wiring import _ProducerFixture
from tests.test_sandbox_realization import compiled_room


def _seats(phase: DesignPhase) -> dict[str, SeatSpec]:
    return {
        "structure": SeatSpec(
            seat_id="seat-structure",
            disciplines=(DevelopmentDiscipline.STRUCTURE_SUPPORT,),
            owned_component_ids=("primary-support",),
            phases=(phase,),
            quadrants=(DeclarationQuadrant.STRUCTURE,),
        ),
        "envelope": SeatSpec(
            seat_id="seat-envelope",
            disciplines=(DevelopmentDiscipline.ENVELOPE_OPENINGS,),
            owned_component_ids=("primary-surface",),
            phases=(phase,),
            quadrants=(DeclarationQuadrant.OPENINGS,),
            consumes=("seat-structure",),
        ),
        "detail": SeatSpec(
            seat_id="seat-detail",
            disciplines=(
                DevelopmentDiscipline.CONSTRUCTION,
                DevelopmentDiscipline.MATERIALS,
            ),
            owned_component_ids=("primary-support",),
            phases=(phase,),
            quadrants=(DeclarationQuadrant.DETAIL,),
            consumes=("seat-envelope", "seat-structure"),
        ),
        "review": SeatSpec(
            seat_id="seat-review",
            disciplines=(DevelopmentDiscipline.USE,),
            owned_component_ids=(),
            phases=(phase,),
            quadrants=(),
            reviewer=True,
        ),
    }


class SeatSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.proposal = self.state.selected_schematic.option.proposal

    def test_reviewer_owns_nothing_and_authors_own_something(self) -> None:
        with self.assertRaises(SeatError):
            SeatSpec(
                seat_id="bad", disciplines=(), owned_component_ids=("building",),
                phases=(self.state.active_phase,), quadrants=(), reviewer=True,
            )
        with self.assertRaises(SeatError):
            SeatSpec(
                seat_id="bad", disciplines=(), owned_component_ids=(),
                phases=(self.state.active_phase,), quadrants=(),
            )
        with self.assertRaises(SeatError):
            SeatSpec(
                seat_id="loop", disciplines=(), owned_component_ids=("building",),
                phases=(self.state.active_phase,), quadrants=(), consumes=("loop",),
            )

    def test_subtree_and_ancestors(self) -> None:
        self.assertEqual(
            owned_subtree(self.proposal, ("building",)),
            ("building", "primary-support", "primary-surface"),
        )
        self.assertEqual(
            owned_subtree(self.proposal, ("primary-support",)), ("primary-support",)
        )
        self.assertEqual(ancestors(self.proposal, ("primary-support",)), ("building",))
        with self.assertRaises(SeatError):
            owned_subtree(self.proposal, ("no-such-component",))


class HandoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.seats = _seats(self.state.active_phase)
        binding = self.program.proposal.semantic_bindings[0]
        self.bindings = (
            replace(
                binding, binding_id="structure-binding",
                component_id="primary-support", object_ids=("floor", "frame"),
            ),
            replace(
                binding, binding_id="envelope-binding",
                component_id="primary-surface", object_ids=("leaf", "outer"),
            ),
        )
        self.bearing = InterfaceDatum.create(
            datum_id="bearing-level", kind=InterfaceDatumKind.LEVEL,
            published_by="floor", value=3.57, unit=LengthUnit.METER,
        )
        self.foreign = InterfaceDatum.create(
            datum_id="sill-level", kind=InterfaceDatumKind.LEVEL,
            published_by="leaf", value=0.9, unit=LengthUnit.METER,
        )

    def _handover(self, state=None):
        return compile_handover(
            from_seat=self.seats["structure"], to_seat=self.seats["envelope"],
            design_state=state or self.state,
            published_datums=(self.foreign, self.bearing),
            program_bindings=self.bindings,
            realized_bounds={
                "floor": ((0.0, 0.0, 0.0), (4.0, 4.0, 0.2)),
                "leaf": ((1.0, 0.0, 0.0), (2.0, 0.1, 2.1)),
            },
            object_digests={"floor": "a" * 64},
        )

    def test_deterministic_and_scoped(self) -> None:
        first, second = self._handover(), self._handover()
        self.assertEqual(first.digest, second.digest)
        self.assertEqual([d.datum_id for d in first.datums], ["bearing-level"])
        kinds = [(c.kind, c.subject_id) for c in first.constraints]
        self.assertIn((HandoverKind.PUBLISHED_DATUM, "floor"), kinds)
        self.assertIn((HandoverKind.EXCLUSION_BOUNDS, "floor"), kinds)
        self.assertNotIn((HandoverKind.EXCLUSION_BOUNDS, "leaf"), kinds)
        payload = first.to_dict()
        self.assertFalse(payload["canonical_write_authority"])
        self.assertFalse(payload["design_authority"])

    def test_open_obligations_of_the_receiving_discipline_cross(self) -> None:
        obligations = tuple(
            replace(item, status=DevelopmentObligationStatus.OPEN)
            if item.discipline is DevelopmentDiscipline.ENVELOPE_OPENINGS
            else item
            for item in self.state.obligations
        )
        state = replace(
            self.state,
            obligations=obligations,
            coordination_status=DevelopmentCoordinationStatus.IN_PROGRESS,
        )
        handover = self._handover(state)
        open_rows = [
            c for c in handover.constraints if c.kind is HandoverKind.OPEN_OBLIGATION
        ]
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0].subject_id, "coordinate-envelope_openings")
        # resolved obligations never cross
        self.assertEqual(
            [c for c in self._handover().constraints if c.kind is HandoverKind.OPEN_OBLIGATION],
            [],
        )

    def test_reviewer_hands_nothing(self) -> None:
        with self.assertRaises(SeatError):
            compile_handover(
                from_seat=self.seats["review"], to_seat=self.seats["envelope"],
                design_state=self.state,
            )


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.seats = _seats(self.state.active_phase)

    def test_sibling_subtree_does_not_leak(self) -> None:
        context = project_seat_context(
            seat=self.seats["envelope"], design_state=self.state,
            inherited_commitment_refs=(COMMITMENT,),
        )
        self.assertEqual(context.owned_component_ids, ("primary-surface",))
        self.assertEqual(context.visible_component_ids, ("building", "primary-surface"))
        self.assertNotIn(
            "primary-support", [c.component_id for c in context.components]
        )
        self.assertEqual(context.inherited_commitment_refs, (COMMITMENT,))

    def test_wrong_phase_and_foreign_handover_fail_typed(self) -> None:
        other_phase = next(
            p for p in DesignPhase if p is not self.state.active_phase
        )
        seat = replace(self.seats["envelope"], phases=(other_phase,))
        with self.assertRaises(SeatError):
            project_seat_context(
                seat=seat, design_state=self.state, inherited_commitment_refs=()
            )
        handover = compile_handover(
            from_seat=self.seats["envelope"], to_seat=self.seats["structure"],
            design_state=self.state,
        )
        with self.assertRaises(SeatError):
            project_seat_context(
                seat=self.seats["structure"], design_state=self.state,
                inherited_commitment_refs=(), handovers=(handover,),
            )


class ScheduleTests(unittest.TestCase):
    def test_rounds_follow_handover_dependency(self) -> None:
        seats = _seats(DesignPhase.DESIGN_DEVELOPMENT)
        circulation = SeatSpec(
            seat_id="seat-circulation",
            disciplines=(DevelopmentDiscipline.CIRCULATION,),
            owned_component_ids=("building",),
            phases=(DesignPhase.DESIGN_DEVELOPMENT,),
            quadrants=(DeclarationQuadrant.DIMENSIONS,),
        )
        rounds = schedule_seats(tuple(seats.values()) + (circulation,))
        self.assertEqual(
            rounds,
            (
                ("seat-circulation", "seat-structure"),
                ("seat-envelope",),
                ("seat-detail",),
                ("seat-review",),
            ),
        )

    def test_cycle_and_unknown_fail_typed(self) -> None:
        phase = DesignPhase.DESIGN_DEVELOPMENT
        a = SeatSpec(
            seat_id="a", disciplines=(), owned_component_ids=("x",),
            phases=(phase,), quadrants=(), consumes=("b",),
        )
        b = SeatSpec(
            seat_id="b", disciplines=(), owned_component_ids=("y",),
            phases=(phase,), quadrants=(), consumes=("a",),
        )
        with self.assertRaises(SeatError):
            schedule_seats((a, b))
        with self.assertRaises(SeatError):
            schedule_seats((a,))


class ThreeSeatDemonstrationTests(_ProducerFixture):
    """Structure -> envelope -> detail through the real producer."""

    def setUp(self) -> None:
        super().setUp()
        self.seats = _seats(self.design_state.active_phase)
        self.tree = self.design_state.selected_schematic.option.proposal
        self.original_binding = self.proposal.semantic_bindings[0]

    def _proposal_for(self, component_id: str, binding_id: str):
        # Operations and assemblies reference the binding by id, so the seat
        # re-homes the binding to its own component and keeps the id.
        del binding_id
        binding = replace(self.original_binding, component_id=component_id)
        return replace(self.proposal, semantic_bindings=(binding,))

    def _issues(self, result) -> list[dict]:
        rows = []
        for ref in result.round_refs:
            rows.extend(self.repository.load_json(ref).get("issues", []))
        return rows

    async def test_out_of_scope_binding_is_refused(self) -> None:
        scope = owned_subtree(self.tree, self.seats["envelope"].owned_component_ids)
        provider = _ScriptedProvider((proposal_authoring_output(self.proposal),))
        result = await self._produce(provider, seat_scope=scope)
        self.assertIsNot(result.status, GeometryProposalStatus.ACCEPTED)
        codes = {row["code"] for row in self._issues(result)}
        self.assertIn("seat_scope_violation", codes)

    async def test_structure_publishes_envelope_derives_detail_refused(self) -> None:
        # Round 1: structure seat publishes the bearing level on its own object.
        bearing = InterfaceDatum.create(
            datum_id="bearing-level", kind=InterfaceDatumKind.LEVEL,
            published_by="floor", value=3.57, unit=LengthUnit.METER,
        )
        structure_scope = owned_subtree(
            self.tree, self.seats["structure"].owned_component_ids
        )
        structure = await self._produce(
            _ScriptedProvider(
                (proposal_authoring_output(
                    self._proposal_for("primary-support", "structure-binding")
                ),)
            ),
            seat_scope=structure_scope,
            interface_datums=(bearing,),
            datum_bindings=(
                DatumBinding(
                    binding_id="bind-floor", datum_id="bearing-level",
                    op_id="floor", parameter_name="bearing_level",
                ),
            ),
        )
        self.assertIs(structure.status, GeometryProposalStatus.ACCEPTED)
        assert structure.program is not None
        self.assertEqual(
            [d.datum_id for d in structure.program.interface_datums], ["bearing-level"]
        )

        # Handover: only what structure committed crosses to envelope.
        handover = compile_handover(
            from_seat=self.seats["structure"], to_seat=self.seats["envelope"],
            design_state=self.design_state,
            published_datums=structure.program.interface_datums,
            program_bindings=structure.program.proposal.semantic_bindings,
            realized_bounds={"floor": ((0.0, 0.0, 0.0), (4.0, 4.0, 0.2))},
            object_digests={
                o.object_id: o.object_digest for o in structure.program.objects
            },
        )
        self.assertEqual([d.datum_id for d in handover.datums], ["bearing-level"])
        context = project_seat_context(
            seat=self.seats["envelope"], design_state=self.design_state,
            inherited_commitment_refs=(COMMITMENT,), handovers=(handover,),
        )
        self.assertNotIn(
            "primary-support", [c.component_id for c in context.components]
        )

        # Round 2: envelope seat derives from the handed-over datum.
        envelope_scope = owned_subtree(
            self.tree, self.seats["envelope"].owned_component_ids
        )
        envelope = await self._produce(
            _ScriptedProvider(
                (proposal_authoring_output(
                    self._proposal_for("primary-surface", "envelope-binding")
                ),)
            ),
            seat_scope=envelope_scope,
            interface_datums=context.handovers[0].datums,
            datum_bindings=(
                DatumBinding(
                    binding_id="bind-sill", datum_id="bearing-level",
                    op_id="floor", parameter_name="sill_level",
                ),
            ),
        )
        self.assertIs(envelope.status, GeometryProposalStatus.ACCEPTED)
        assert envelope.program is not None
        floor_op = next(
            op for op in envelope.program.proposal.operations if op.op_id == "floor"
        )
        derived = {p.name: p.value_json for p in floor_op.parameters}
        self.assertEqual(derived["sill_level"], "3.57")

        # Round 3: detail seat reaches into the envelope subtree -> refused.
        detail_scope = owned_subtree(
            self.tree, self.seats["detail"].owned_component_ids
        )
        detail = await self._produce(
            _ScriptedProvider(
                (proposal_authoring_output(
                    self._proposal_for("primary-surface", "detail-binding")
                ),)
            ),
            seat_scope=detail_scope,
        )
        self.assertIsNot(detail.status, GeometryProposalStatus.ACCEPTED)
        self.assertIn(
            "seat_scope_violation", {row["code"] for row in self._issues(detail)}
        )
        self.assertEqual(
            schedule_seats(tuple(self.seats.values())),
            (("seat-structure",), ("seat-envelope",), ("seat-detail",), ("seat-review",)),
        )


class ExclusionEnforcementTests(unittest.TestCase):
    """M097: exclusion bounds carried by a handover are checked, not just carried."""

    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.seats = _seats(self.state.active_phase)
        binding = self.program.proposal.semantic_bindings[0]
        structure_bindings = (replace(binding, binding_id="structure-binding", component_id="primary-support", object_ids=("floor",)),)
        self.handover = compile_handover(
            from_seat=self.seats["structure"], to_seat=self.seats["detail"], design_state=self.state,
            program_bindings=structure_bindings, realized_bounds={"floor": ((0.0, 0.0, 0.0), (4.0, 0.2, 4.0))},
        )

    def test_entering_is_reported_contact_is_not(self) -> None:
        bounds = {
            "moulding": ((1.0, 0.1, 1.0), (2.0, 0.5, 2.0)),   # enters the floor by 0.1 in Y
            "column": ((1.0, 0.2, 1.0), (1.5, 6.0, 1.5)),     # sits on the floor: shared face
        }
        violations = check_handover_exclusions(self.handover, bounds)
        self.assertEqual([(v.subject_id, v.host_id) for v in violations], [("moulding", "floor")])
        self.assertAlmostEqual(violations[0].depth[1], 0.1)

    def test_declared_engagement_silences_exactly_its_pair(self) -> None:
        bounds = {"moulding": ((1.0, 0.1, 1.0), (2.0, 0.5, 2.0)), "other": ((3.0, 0.1, 3.0), (3.5, 0.3, 3.5))}
        engagement = DeclaredEngagement("moulding", "floor", "carved into the slab", ("evidence:x",))
        remaining = check_handover_exclusions(self.handover, bounds, engagements=(engagement,))
        self.assertEqual([v.subject_id for v in remaining], ["other"])
        with self.assertRaises(SeatError):
            DeclaredEngagement("moulding", "floor", "", ("evidence:x",))


if __name__ == "__main__":
    unittest.main()
