"""P098: project levels and grids as shared datums.

Levels and axes are published once per run; seats derive their LEVEL and
PLANE datums from them and may not restate them; assembly templates bind
required levels to project levels or fail typed.
"""
from __future__ import annotations

import json
import unittest

from archflow.capabilities.discipline_seats import (
    SeatError,
    check_seat_datums,
    project_seat_context,
)
from archive.archflow.state.assembly_template import (
    AssemblyTemplateError,
    RoleBinding,
    bind_assembly_template,
)
from archive.archflow.state.component_template import ParameterForm, TemplateParameter
from archflow.state.geometry_program import (
    GeometryProgramError,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
    ProjectGridAxis,
    ProjectGrids,
    ProjectLevel,
    ProjectLevels,
    verify_project_datums,
)
from archive.tests.test_assembly_template import BASIS, _template
from tests.test_discipline_seats import _seats
from tests.test_geometry_compiler import COMMITMENT
from tests.test_sandbox_realization import compiled_room

EVIDENCE = ("evidence:survey-section-aa",)


def _levels(**overrides) -> ProjectLevels:
    fields = dict(
        project_id="demo",
        published_by="seat-coordination",
        levels=(
            ProjectLevel("level-ground", "ground", 0.0, EVIDENCE),
            ProjectLevel("level-piano-nobile", "piano-nobile", 3.57, EVIDENCE),
        ),
    )
    fields.update(overrides)
    return ProjectLevels(**fields)


def _grids() -> ProjectGrids:
    return ProjectGrids(
        project_id="demo",
        published_by="seat-coordination",
        axes=(
            ProjectGridAxis("axis-a", "A", (0.0, 0.0, -4.0), (1.0, 0.0, 0.0), EVIDENCE),
            ProjectGridAxis("axis-b", "B", (0.0, 0.0, 4.0), (1.0, 0.0, 0.0), EVIDENCE),
        ),
    )


class SchemaTests(unittest.TestCase):
    def test_level_without_evidence_is_refused(self) -> None:
        with self.assertRaises(GeometryProgramError):
            ProjectLevel("level-x", "x", 1.0, ())

    def test_levels_derive_level_datums_from_the_publisher(self) -> None:
        levels = _levels()
        datum = levels.datum("piano-nobile")
        self.assertEqual(datum.datum_id, "level-piano-nobile")
        self.assertIs(datum.kind, InterfaceDatumKind.LEVEL)
        self.assertEqual(datum.published_by, "seat-coordination")
        self.assertEqual(json.loads(datum.value_json), 3.57)
        self.assertIs(datum.unit, LengthUnit.METER)
        self.assertEqual(datum.basis_refs, EVIDENCE)
        self.assertEqual(levels.datum_ids, ("level-ground", "level-piano-nobile"))
        self.assertEqual(ProjectLevels.from_dict(levels.to_dict()), levels)
        with self.assertRaises(GeometryProgramError):
            levels.level("attic")

    def test_levels_require_unique_sorted_ids_and_unique_roles(self) -> None:
        with self.assertRaises(GeometryProgramError):
            _levels(levels=(
                ProjectLevel("level-piano-nobile", "piano-nobile", 3.57, EVIDENCE),
                ProjectLevel("level-ground", "ground", 0.0, EVIDENCE),
            ))
        with self.assertRaises(GeometryProgramError):
            _levels(levels=(
                ProjectLevel("level-a", "ground", 0.0, EVIDENCE),
                ProjectLevel("level-b", "ground", 1.0, EVIDENCE),
            ))

    def test_grid_axis_is_a_vertical_plane_through_a_plan_line(self) -> None:
        grids = _grids()
        datum = grids.datum("A")
        self.assertIs(datum.kind, InterfaceDatumKind.PLANE)
        self.assertEqual(json.loads(datum.value_json), {"normal": [0.0, 0.0, -1.0], "origin": [0.0, 0.0, -4.0]})
        self.assertEqual(ProjectGrids.from_dict(grids.to_dict()), grids)
        with self.assertRaises(GeometryProgramError):
            ProjectGridAxis("axis-v", "V", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), EVIDENCE)
        with self.assertRaises(GeometryProgramError):
            ProjectGridAxis("axis-z", "Z", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), EVIDENCE)


class DerivationTests(unittest.TestCase):
    def test_seat_datum_naming_a_level_must_be_that_level(self) -> None:
        levels = _levels()
        derived = levels.datum("piano-nobile")
        own = InterfaceDatum.create(
            datum_id="west-column-top", kind=InterfaceDatumKind.LEVEL,
            published_by="obj-column-west-0", value=10.2, unit=LengthUnit.METER,
        )
        self.assertEqual(verify_project_datums((derived, own), levels), ())
        restated = InterfaceDatum.create(
            datum_id="level-piano-nobile", kind=InterfaceDatumKind.LEVEL,
            published_by="obj-landing-bridge-west", value=3.57, unit=LengthUnit.METER,
        )
        moved = InterfaceDatum.create(
            datum_id="level-piano-nobile", kind=InterfaceDatumKind.LEVEL,
            published_by="seat-coordination", value=3.6, unit=LengthUnit.METER,
        )
        rekinded = InterfaceDatum.create(
            datum_id="level-ground", kind=InterfaceDatumKind.SERIES,
            published_by="seat-coordination", value={"start": 0.0, "step": 0.1, "count": 2},
        )
        violations = verify_project_datums((restated, moved, rekinded), levels)
        self.assertEqual(len(violations), 3)
        self.assertTrue(any("published by obj-landing-bridge-west" in v for v in violations))
        self.assertTrue(any("restates project value 3.57 as 3.6" in v for v in violations))
        self.assertTrue(any("as a series" in v for v in violations))

    def test_grid_axis_restated_is_a_violation(self) -> None:
        grids = _grids()
        shifted = InterfaceDatum.create(
            datum_id="axis-a", kind=InterfaceDatumKind.PLANE, published_by="seat-coordination",
            value={"origin": [0.0, 0.0, -4.1], "normal": [0.0, 0.0, -1.0]}, unit=LengthUnit.METER,
        )
        self.assertEqual(len(verify_project_datums((shifted,), None, grids)), 1)
        self.assertEqual(verify_project_datums((grids.datum("A"),), None, grids), ())


class SeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, _, _ = compiled_room()
        self.seats = _seats(self.state.active_phase)
        self.levels = _levels()
        self.grids = _grids()

    def test_project_datums_enter_every_seat_context(self) -> None:
        context = project_seat_context(
            seat=self.seats["envelope"], design_state=self.state,
            inherited_commitment_refs=(COMMITMENT,),
            project_levels=self.levels, project_grids=self.grids,
        )
        self.assertEqual(
            [d.datum_id for d in context.project_datums],
            ["axis-a", "axis-b", "level-ground", "level-piano-nobile"],
        )
        payload = context.to_dict()
        self.assertEqual(len(payload["project_datums"]), 4)
        with self.assertRaises(SeatError):
            project_seat_context(
                seat=self.seats["envelope"], design_state=self.state,
                inherited_commitment_refs=(COMMITMENT,), project_levels="levels",
            )

    def test_seat_overwriting_a_level_fails_typed(self) -> None:
        overwrite = InterfaceDatum.create(
            datum_id="level-piano-nobile", kind=InterfaceDatumKind.LEVEL,
            published_by="obj-landing", value=3.4, unit=LengthUnit.METER,
        )
        with self.assertRaises(SeatError) as caught:
            check_seat_datums(
                seat=self.seats["structure"], published_datums=(overwrite,),
                project_levels=self.levels,
            )
        self.assertIn("overwrites project datums", str(caught.exception))
        check_seat_datums(
            seat=self.seats["structure"],
            published_datums=(self.levels.datum("piano-nobile"),),
            project_levels=self.levels, project_grids=self.grids,
        )
        with self.assertRaises(SeatError):
            check_seat_datums(
                seat=self.seats["review"], published_datums=(), project_levels=self.levels,
            )


class AssemblyBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, _, _ = compiled_room()
        self.components = self.state.selected_schematic.option.proposal.components
        self.template = _template()
        self.count = TemplateParameter.create(
            name="portico-count", form=ParameterForm.COUNT,
            value={"min": 1, "max": 1, "adopted": 1}, basis_refs=BASIS,
        )
        self.roles = (
            RoleBinding("building", component_ids=("building",)),
            RoleBinding("portico", component_ids=("building",)),
            RoleBinding("portico-columns", component_ids=("primary-support",)),
            RoleBinding("portico-entablature", component_ids=("primary-surface",)),
            RoleBinding("portico-landings", component_ids=("primary-support",)),
        )

    def _bind(self, datum_bindings, **extra):
        return bind_assembly_template(
            self.template, template_ref="project://lib/runs/c/records/t.json",
            project_id="demo", run_id="run", components=self.components,
            role_bindings=self.roles, datum_bindings=datum_bindings,
            parameters=(self.count,), **extra,
        )

    def test_required_level_resolves_to_a_project_level(self) -> None:
        binding = self._bind(
            (("column-top", "west-column-top"), ("landing-top", "level-piano-nobile")),
            project_levels=_levels(levels=(
                ProjectLevel("level-piano-nobile", "piano-nobile", 3.57, EVIDENCE),
                ProjectLevel("west-column-top", "column-top", 10.2, EVIDENCE),
            )),
        )
        self.assertEqual(len(binding.datum_bindings), 2)

    def test_missing_project_level_fails_typed(self) -> None:
        with self.assertRaises(AssemblyTemplateError) as caught:
            self._bind(
                (("column-top", "west-column-top"), ("landing-top", "west-landing-top")),
                project_levels=_levels(),
            )
        self.assertIn("not a project level", str(caught.exception))
        with self.assertRaises(AssemblyTemplateError):
            self._bind((("column-top", "d"),), project_levels="levels")


if __name__ == "__main__":
    unittest.main()
