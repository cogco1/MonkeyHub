"""P096: building assembly templates — schema, binding, harvest, library."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.capabilities.assembly_library import (
    harvest_assembly_template,
    promote_assembly_template,
    record_assembly_binding,
    record_assembly_candidate,
    relations_from_datum_bindings,
    roles_from_design_state,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.state.assembly_template import (
    _reject_coordinates,
    AssemblyRelation,
    AssemblyRole,
    AssemblyTemplateError,
    BuildingAssemblyTemplate,
    Cardinality,
    CheckKind,
    RequiredCheck,
    RequiredDatum,
    RoleBinding,
    bind_assembly_template,
)
from archflow.state.component_template import CaseVote, ParameterForm, TemplateParameter
from archflow.state.design_maturity import DesignPhase
from archflow.state.geometry_program import (
    DatumBinding,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from tests.test_sandbox_realization import compiled_room

BASIS = ("evidence:treatise-page", "project://demo/runs/run/records/receipt-aa.json")
VOTE = CaseVote(project_id="demo", run_id="run", receipt_ref="project://demo/runs/run/records/receipt-aa.json")
PHASE = DesignPhase.SCHEMATIC_DESIGN


def _roles():
    return (
        AssemblyRole("building", None, "whole-building", Cardinality(value=1), PHASE),
        AssemblyRole("portico", "building", "arrival-and-buttress", Cardinality(parameter="portico-count"), PHASE, indexing=()),
        AssemblyRole("portico-columns", "portico", "vertical-support", Cardinality(value=1), PHASE, required_datum_roles=("landing-top",)),
        AssemblyRole("portico-entablature", "portico", "horizontal-load-transfer", Cardinality(value=1), PHASE, required_datum_roles=("column-top",)),
        AssemblyRole("portico-landings", "portico", "stair-to-floor-interface", Cardinality(value=1), PHASE),
    )


def _template(**overrides):
    fields = dict(
        template_id="centralized-test-villa", typology="centralized-villa-with-cardinal-porticos", edition=1,
        roles=_roles(),
        relations=(
            AssemblyRelation("columns-support-entablature", "portico-columns", ArchitecturalRelationKind.SUPPORT, "portico-entablature", "column-top", BASIS),
            AssemblyRelation("landing-supports-columns", "portico-landings", ArchitecturalRelationKind.SUPPORT, "portico-columns", "landing-top", BASIS),
        ),
        datums=(
            RequiredDatum("column-top", InterfaceDatumKind.LEVEL, "portico-columns"),
            RequiredDatum("landing-top", InterfaceDatumKind.LEVEL, "portico-landings"),
        ),
        checks=(RequiredCheck("entablature-bearing", CheckKind.SUPPORT_CONTACT, ("portico-columns", "portico-entablature")),),
        parameters=(TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 4, "max": 4, "adopted": 4}, basis_refs=BASIS),),
        applicability=("centralized plan with cardinal porticos",), basis_refs=BASIS, case_votes=(VOTE,),
        harvested_from_project="demo", harvested_from_run="run",
    )
    fields.update(overrides)
    return BuildingAssemblyTemplate(**fields)


class SchemaTests(unittest.TestCase):
    def test_round_trip_and_digest(self) -> None:
        t = _template()
        self.assertEqual(BuildingAssemblyTemplate.from_dict(t.to_dict()).digest, t.digest)
        self.assertEqual(t.distinct_vote_projects(), ("demo",))

    def test_refuses_coordinates(self) -> None:
        # the guard the schema runs over its own payload: no floats, no numeric vectors
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates({"origin": [-10.71, 3.57, 0.0]}, "t", allow_float=False)
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates({"level": 3.57}, "t", allow_float=False)
        _reject_coordinates({"band": {"min": 0.18, "max": 0.22, "adopted": 0.2}}, "t", allow_float=True)
        # a dimensionless ratio band is not a coordinate
        _template(parameters=(
            TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 4, "max": 4, "adopted": 4}, basis_refs=BASIS),
            TemplateParameter.create(name="entablature-to-column", form=ParameterForm.MODULE_RATIO, value={"min": 0.18, "max": 0.22, "adopted": 0.2}, basis_refs=BASIS),
        ))
        payload = _template().to_dict()
        payload["roles"][0]["function"] = 3.57
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates(payload, "t", allow_float=False)

    def test_structural_invariants(self) -> None:
        with self.assertRaises(AssemblyTemplateError):
            _template(roles=_roles() + (AssemblyRole("orphan", "nowhere", "x", Cardinality(value=1), PHASE),))
        with self.assertRaises(AssemblyTemplateError):
            _template(relations=(AssemblyRelation("bad", "portico-columns", ArchitecturalRelationKind.SUPPORT, "ghost", None, BASIS),))
        with self.assertRaises(AssemblyTemplateError):
            _template(relations=(AssemblyRelation("bad", "portico-columns", ArchitecturalRelationKind.SUPPORT, "portico-entablature", "no-such-datum", BASIS),))
        with self.assertRaises(AssemblyTemplateError):
            _template(parameters=())  # project-derived portico count without a COUNT parameter
        with self.assertRaises(AssemblyTemplateError):
            _template(datums=(RequiredDatum("column-top", InterfaceDatumKind.LEVEL, "ghost"), RequiredDatum("landing-top", InterfaceDatumKind.LEVEL, "portico-landings")))
        with self.assertRaises(AssemblyTemplateError):
            AssemblyRole("portico", "building", "x", Cardinality(value=4), PHASE, indexing=("north", "east"))


class BindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, _, _ = compiled_room()
        self.components = self.state.selected_schematic.option.proposal.components
        self.template = _template()
        self.count = TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 1, "max": 1, "adopted": 1}, basis_refs=BASIS)

    def _bindings(self, **override):
        rows = {
            "building": RoleBinding("building", component_ids=("building",)),
            "portico": RoleBinding("portico", component_ids=("building",)),
            "portico-columns": RoleBinding("portico-columns", component_ids=("primary-support",)),
            "portico-entablature": RoleBinding("portico-entablature", component_ids=("primary-surface",)),
            "portico-landings": RoleBinding("portico-landings", declined_reason="no exterior stair in this project"),
        }
        rows.update(override)
        return tuple(rows.values())

    def test_binds_with_declination_and_project_count(self) -> None:
        binding = bind_assembly_template(
            self.template, template_ref="project://lib/runs/c/records/t.json", project_id="demo", run_id="run",
            components=self.components, role_bindings=self._bindings(),
            datum_bindings=(("column-top", "room-column-top"),), parameters=(self.count,),
        )
        self.assertEqual(binding.template_digest, self.template.digest)
        self.assertEqual(len(binding.role_bindings), 5)

    def test_unbound_role_unmet_count_and_unbound_datum_fail_typed(self) -> None:
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings()[:-1], datum_bindings=(("column-top", "d"),), parameters=(self.count,),
            )
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building", "primary-support"))),
                datum_bindings=(("column-top", "d"),), parameters=(self.count,),
            )
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(), datum_bindings=(), parameters=(self.count,),
            )

    def test_project_derived_indexed_role_accepts_a_subset(self) -> None:
        indexed = replace(self.template, roles=tuple(
            replace(r, indexing=("east", "north", "south", "west")) if r.role_id == "portico" else r for r in self.template.roles
        ))
        one = TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 1, "max": 4, "adopted": 1}, basis_refs=BASIS)
        binding = bind_assembly_template(
            indexed, template_ref="t", project_id="demo", run_id="run", components=self.components,
            role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building",), indices=("south",))),
            datum_bindings=(("column-top", "d"),), parameters=(one,),
        )
        self.assertEqual(binding.role_bindings[1].indices, ("south",))
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                indexed, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building",), indices=("up",))),
                datum_bindings=(("column-top", "d"),), parameters=(one,),
            )


class HarvestAndLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.library = FilesystemProjectRepository.initialize(
            Path(self.temporary.name) / "lib", project_id="component-library", initial_state={"schema": "TestState@1"}
        )
        self.run = self.library.create_run("catalog")
        self.destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=self.run.run_id)

    def _harvest(self):
        roles = roles_from_design_state(self.state, required_datum_roles={"primary-surface": ("bearing-level",)})
        datum = InterfaceDatum.create(datum_id="room-bearing-level", kind=InterfaceDatumKind.LEVEL, published_by="floor", value=3.57, unit=LengthUnit.METER)
        relations = relations_from_datum_bindings(
            datums=(datum,), bindings=(DatumBinding("b", "room-bearing-level", "outer", "base_level"),),
            object_role={"floor": "primary-support"}, op_role={"outer": "primary-surface"},
            datum_role_of={"room-bearing-level": "bearing-level"}, basis_refs=BASIS,
        )
        return harvest_assembly_template(
            template_id="room-assembly", typology="single-room-shell", design_state=self.state, roles=roles,
            relations=relations, datums=(RequiredDatum("bearing-level", InterfaceDatumKind.LEVEL, "primary-support"),),
            checks=(RequiredCheck("shell-bearing", CheckKind.SUPPORT_CONTACT, ("primary-support", "primary-surface")),),
            parameters=(), applicability=("one-room shell",), basis_refs=BASIS,
            case_vote=CaseVote(project_id=self.state.project_id, run_id=self.state.run_id, receipt_ref=BASIS[1]),
            harvested_from_run=self.state.run_id,
        )

    def test_harvest_is_deterministic_and_coordinate_free(self) -> None:
        first, second = self._harvest(), self._harvest()
        self.assertEqual(first.digest, second.digest)
        self.assertEqual([r.role_id for r in first.roles], ["building", "primary-support", "primary-surface"])
        self.assertEqual(first.relations[0].predicate, ArchitecturalRelationKind.SUPPORT)
        self.assertEqual(first.relations[0].datum_role, "bearing-level")
        self.assertNotIn("3.57", first.to_dict().__repr__())

    def test_candidate_carries_open_obligation_and_promotion_needs_two_votes(self) -> None:
        template = self._harvest()
        source = self.library.put_json(run=self.run, destination=self.destination, record_kind="source", payload={"x": 1})
        candidate_ref, obligation_ref = record_assembly_candidate(
            self.library, library_run=self.run, library_destination=self.destination, template=template, source_ref=source
        )
        obligation = self.library.load_json(obligation_ref)
        self.assertEqual(obligation["status"], "OPEN")
        self.assertEqual(obligation["candidate_ref"], candidate_ref.uri)
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library, library_run=self.run, library_destination=self.destination, template=template, candidate_ref=candidate_ref
            )
        two = replace(template, case_votes=template.case_votes + (CaseVote(project_id="other", run_id="r", receipt_ref=BASIS[1]),))
        template_ref, receipt_ref = promote_assembly_template(
            self.library, library_run=self.run, library_destination=self.destination, template=two, candidate_ref=candidate_ref
        )
        self.assertEqual(self.library.load_json(receipt_ref)["vote_projects"], sorted(["other", self.state.project_id]))

    def test_binding_record_stays_in_its_project(self) -> None:
        template = self._harvest()
        binding = bind_assembly_template(
            template, template_ref="project://lib/runs/c/records/t.json", project_id="demo", run_id="run",
            components=self.state.selected_schematic.option.proposal.components,
            role_bindings=(
                RoleBinding("building", component_ids=("building",)),
                RoleBinding("primary-support", component_ids=("primary-support",)),
                RoleBinding("primary-surface", component_ids=("primary-surface",)),
            ),
            datum_bindings=(("bearing-level", "room-bearing-level"),),
        )
        project = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = project.create_run("run")
        ref = record_assembly_binding(project, run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run"), binding=binding)
        self.assertEqual(project.load_json(ref)["template_digest"], template.digest)


if __name__ == "__main__":
    unittest.main()
