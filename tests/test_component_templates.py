"""P091: component template records, two-vote promotion, rebound import."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.capabilities.component_library import (
    IMPORT_RECEIPT_KIND,
    harvest_component_template,
    import_component_template,
    promote_component_template,
    resolve_mathematics_ref,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProductionError,
    GeometryProposalStatus,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SELECTED_SPATIAL_OPTION
from archive.archflow.state.component_template import (
    CaseVote,
    ComponentTemplate,
    ComponentTemplateError,
    ObligationKind,
    ParameterForm,
    TemplateModule,
    TemplateObligation,
    TemplateParameter,
    TemplatePlate,
    require_library_votes,
    verify_template_datums,
)
from archflow.state.geometry_program import (
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from tests.test_geometry_compiler import COMMITMENT
from tests.test_geometry_proposal_producer import (
    IDENTITY,
    _ScriptedProvider,
    _spatial_option,
)
from archive.archflow.realization.sandbox import realize_geometry
from tests.test_sandbox_realization import compiled_room


_RETIRED_LANE_KINDS = (
    "a retired lane writes the record kinds this needs; put_json writes only "
    "kinds registered in archflow.project.record_kinds, and a kind no spine "
    "module writes, reads or names is not registered"
)

_BASIS = (
    "evidence:villa-run-016-stage-5-datum-migration-receipt",
)
_PLATE_SHA = "b" * 64


def _stair_template(
    *,
    votes: tuple[CaseVote, ...] | None = None,
) -> ComponentTemplate:
    if votes is None:
        votes = (
            CaseVote(
                project_id="villa-rotonda-reconstruction",
                run_id="reconstruction-016",
                receipt_ref=_BASIS[0],
            ),
        )
    return ComponentTemplate(
        template_id="palladian-exterior-stair",
        family="exterior-stair",
        edition=1,
        mathematics_ref="capability:archive.archflow.capabilities.stair_solver",
        module=TemplateModule(
            name="riser",
            definition=(
                "one riser height; derived per project as the landing "
                "datum height divided by the adopted riser count"
            ),
            unit="meter",
        ),
        parameters=(
            TemplateParameter.create(
                name="going_over_rise",
                form=ParameterForm.MODULE_RATIO,
                value={"min": 1.8, "max": 2.4, "adopted": 2.186},
                basis_refs=_BASIS,
            ),
            TemplateParameter.create(
                name="rise_expression",
                form=ParameterForm.EXPRESSION,
                value={
                    "expression": "landing_top/riser_count",
                    "adopted": 0.155217,
                },
                basis_refs=_BASIS,
            ),
            TemplateParameter.create(
                name="riser_count",
                form=ParameterForm.COUNT,
                value={"min": 21, "max": 25, "adopted": 23},
                basis_refs=_BASIS,
            ),
        ),
        obligations=(
            TemplateObligation(
                obligation_id="clears-underpass",
                kind=ObligationKind.CLEARANCE,
                counterpart="underpass",
                datum_role="passage-outer-face",
                interval_m=(2.05, 2.05),
                basis_refs=_BASIS,
            ),
            TemplateObligation(
                obligation_id="meets-landing",
                kind=ObligationKind.MEETS,
                counterpart="landing",
                datum_role="landing-top",
            ),
            TemplateObligation(
                obligation_id="supported-by-terrain",
                kind=ObligationKind.SUPPORTS,
                counterpart="terrain",
                datum_role="terrain-grade",
            ),
        ),
        plates=(
            TemplatePlate(
                plate_id="under-stair-witness",
                media_type="image/png",
                sha256=_PLATE_SHA,
                caption="solid mass with contract vault recess, west flight",
            ),
        ),
        applicability=(
            "cardinal exterior flights on a four-fold symmetric podium",
            "solid masonry construction; walking surface carried by "
            "continuous solid",
        ),
        basis_refs=_BASIS,
        case_votes=votes,
        harvested_from_project="villa-rotonda-reconstruction",
        harvested_from_run="reconstruction-016",
        open_boundaries=(
            "lateral daylighting of the underpass recess",
        ),
    )


class TemplateContractTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        template = _stair_template()
        self.assertEqual(
            ComponentTemplate.from_dict(template.to_dict()), template
        )

    def test_bare_number_fails_closed(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            TemplateParameter.create(
                name="going_over_rise",
                form=ParameterForm.MODULE_RATIO,
                value={"min": 1.8, "max": 2.4, "adopted": 2.0},
                basis_refs=(),
            )

    def test_adopted_value_must_stay_in_band(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            TemplateParameter.create(
                name="going_over_rise",
                form=ParameterForm.MODULE_RATIO,
                value={"min": 1.8, "max": 2.4, "adopted": 3.0},
                basis_refs=_BASIS,
            )

    def test_clearance_requires_interval(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            TemplateObligation(
                obligation_id="clears-underpass",
                kind=ObligationKind.CLEARANCE,
                counterpart="underpass",
            )
        with self.assertRaises(ComponentTemplateError):
            TemplateObligation(
                obligation_id="meets-landing",
                kind=ObligationKind.MEETS,
                counterpart="landing",
                interval_m=(0.0, 0.1),
            )

    def test_template_requires_plates_and_votes(self) -> None:
        template = _stair_template()
        with self.assertRaises(ComponentTemplateError):
            replace(template, plates=())
        with self.assertRaises(ComponentTemplateError):
            replace(template, case_votes=())

    def test_cited_basis_refs_aggregate(self) -> None:
        self.assertEqual(_stair_template().cited_basis_refs(), _BASIS)


class DatumRoleResolutionTests(unittest.TestCase):
    def _published(self):
        return (
            InterfaceDatum.create(
                datum_id="datum-landing-top",
                kind=InterfaceDatumKind.LEVEL,
                published_by="obj-landing-bridge-west",
                value=3.57,
                unit=LengthUnit.METER,
            ),
            InterfaceDatum.create(
                datum_id="datum-terrain-grade",
                kind=InterfaceDatumKind.LEVEL,
                published_by="obj-terrain-platform",
                value=0.0,
                unit=LengthUnit.METER,
            ),
        )

    def test_unbound_role_is_a_violation(self) -> None:
        violations = verify_template_datums(
            _stair_template(), self._published(),
            {"landing-top": "datum-landing-top",
             "terrain-grade": "datum-terrain-grade"},
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("passage-outer-face", violations[0])

    def test_binding_to_unpublished_datum_is_a_violation(self) -> None:
        violations = verify_template_datums(
            _stair_template(), self._published(),
            {"landing-top": "datum-landing-top",
             "terrain-grade": "datum-terrain-grade",
             "passage-outer-face": "datum-nobody-published"},
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("no published datum provides", violations[0])

    def test_fully_resolved_template_passes(self) -> None:
        published = self._published() + (
            InterfaceDatum.create(
                datum_id="datum-passage-outer-face-west",
                kind=InterfaceDatumKind.PLANE,
                published_by="obj-underpass-west-landing-left-wing",
                value={"origin": [-15.05, 0, 0], "normal": [-1, 0, 0]},
                unit=LengthUnit.METER,
            ),
        )
        self.assertEqual(
            verify_template_datums(
                _stair_template(), published,
                {"landing-top": "datum-landing-top",
                 "terrain-grade": "datum-terrain-grade",
                 "passage-outer-face": "datum-passage-outer-face-west"},
            ),
            (),
        )


class MathematicsRefTests(unittest.TestCase):
    def test_stair_solver_resolves(self) -> None:
        module = resolve_mathematics_ref(_stair_template().mathematics_ref)
        self.assertTrue(hasattr(module, "solve_exterior_stair") or any(
            name.startswith("solve_") for name in dir(module)
        ))

    def test_unknown_module_fails_closed(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            resolve_mathematics_ref("capability:archflow.capabilities.no_such_solver")

    def test_non_archflow_ref_fails_closed(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            resolve_mathematics_ref("capability:os.path")


class TwoVoteRuleTests(unittest.TestCase):
    def test_single_vote_blocks_promotion(self) -> None:
        with self.assertRaises(ComponentTemplateError):
            require_library_votes(_stair_template())

    def test_waiver_lifts_the_guard(self) -> None:
        require_library_votes(
            _stair_template(),
            waiver_ref="record:two-vote-waiver-first-harvest",
        )

    def test_two_distinct_projects_pass(self) -> None:
        votes = (
            CaseVote(
                project_id="pantheon-reconstruction",
                run_id="reconstruction-002",
                receipt_ref="evidence:pantheon-step-receipt",
            ),
            CaseVote(
                project_id="villa-rotonda-reconstruction",
                run_id="reconstruction-016",
                receipt_ref=_BASIS[0],
            ),
        )
        require_library_votes(_stair_template(votes=votes))

    def test_two_votes_same_project_block(self) -> None:
        votes = (
            CaseVote(
                project_id="villa-rotonda-reconstruction",
                run_id="reconstruction-014",
                receipt_ref="evidence:villa-run-014-stage-5-audit-2026-09-01",
            ),
            CaseVote(
                project_id="villa-rotonda-reconstruction",
                run_id="reconstruction-016",
                receipt_ref=_BASIS[0],
            ),
        )
        with self.assertRaises(ComponentTemplateError):
            require_library_votes(_stair_template(votes=votes))


class LibraryFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.source = FilesystemProjectRepository.initialize(
            base / "villa-rotonda-reconstruction",
            project_id="villa-rotonda-reconstruction",
            initial_state={"schema": "TestState@1"},
        )
        self.library = FilesystemProjectRepository.initialize(
            base / "component-library",
            project_id="component-library",
            initial_state={"schema": "TestState@1"},
        )
        self.target = FilesystemProjectRepository.initialize(
            base / "parthenon-reconstruction",
            project_id="parthenon-reconstruction",
            initial_state={"schema": "TestState@1"},
        )
        self.source_run = self.source.create_run("reconstruction-016")
        self.library_run = self.library.create_run("catalog-001")
        self.target_run = self.target.create_run("reconstruction-006")
        self.dest = lambda run: PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run.run_id
        )

    @unittest.skip(_RETIRED_LANE_KINDS)
    def test_harvest_promote_import_round_trip(self) -> None:
        template = _stair_template()
        harvest_ref = harvest_component_template(
            self.source,
            run=self.source_run,
            destination=self.dest(self.source_run),
            template=template,
        )
        reloaded = ComponentTemplate.from_dict(
            self.source.load_json(harvest_ref)
        )
        self.assertEqual(reloaded, template)

        with self.assertRaises(ComponentTemplateError):
            promote_component_template(
                self.library,
                library_run=self.library_run,
                library_destination=self.dest(self.library_run),
                template=template,
                source_ref=harvest_ref,
            )
        library_ref, promotion_ref = promote_component_template(
            self.library,
            library_run=self.library_run,
            library_destination=self.dest(self.library_run),
            template=template,
            source_ref=harvest_ref,
            waiver_ref="record:two-vote-waiver-first-harvest",
        )
        promotion = self.library.load_json(promotion_ref)
        self.assertEqual(promotion["source_ref"], harvest_ref.uri)
        self.assertEqual(
            promotion["two_vote_waiver_ref"],
            "record:two-vote-waiver-first-harvest",
        )

        with self.assertRaises(ComponentTemplateError):
            import_component_template(
                self.target,
                target_run=self.target_run,
                target_destination=self.dest(self.target_run),
                template=template,
                library_ref=library_ref,
                evidence_rebinding={},
            )
        rebinding = {
            _BASIS[0]: "record:parthenon-local-stair-basis",
        }
        imported_ref, import_receipt_ref = import_component_template(
            self.target,
            target_run=self.target_run,
            target_destination=self.dest(self.target_run),
            template=template,
            library_ref=library_ref,
            evidence_rebinding=rebinding,
        )
        receipt = self.target.load_json(import_receipt_ref)
        self.assertEqual(receipt["library_ref"], library_ref.uri)
        self.assertEqual(
            receipt["evidence_rebinding"],
            [
                {
                    "template_ref": _BASIS[0],
                    "local_ref": "record:parthenon-local-stair-basis",
                }
            ],
        )
        travelled = ComponentTemplate.from_dict(
            self.target.load_json(imported_ref)
        )
        self.assertEqual(travelled.basis_refs, template.basis_refs)


class ProducerSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id="demo",
            initial_state={"schema": "TestState@1"},
        )
        self.run = self.repository.create_run("run")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=self.run.run_id,
        )
        self.option = _spatial_option()
        self.option_ref = self.repository.put_json(
            run=self.run,
            destination=self.destination,
            record_kind=SELECTED_SPATIAL_OPTION,
            payload=self.option.to_dict(),
        )
        original_state, original_program, _ = compiled_room()
        self.design_state = replace(
            original_state,
            selected_schematic=replace(
                original_state.selected_schematic,
                project_id=self.run.project_id,
                run_id=self.run.run_id,
                base=self.run.base,
                option=replace(
                    original_state.selected_schematic.option,
                    proposal=self.option,
                ),
            ),
        )
        original_binding = original_program.proposal.semantic_bindings[0]
        binding = replace(
            original_binding,
            commitment_refs=(COMMITMENT,),
            evidence_refs=(
                "evidence:geometry-compiler",
                self.option_ref.uri,
            ),
        )
        self.proposal = replace(
            original_program.proposal,
            project_id=self.run.project_id,
            run_id=self.run.run_id,
            base=self.run.base,
            design_state_digest=self.design_state.state_digest,
            semantic_bindings=(binding,),
        )
        template = replace(
            _stair_template(),
            harvested_from_project="demo",
            harvested_from_run="run",
        )
        self.template_ref = harvest_component_template(
            self.repository,
            run=self.run,
            destination=self.destination,
            template=template,
        )

    async def _produce(self, provider, *, rounds: int = 2):
        return await produce_geometry_program_proposal(
            self.repository,
            provider,
            run=self.run,
            destination=self.destination,
            spatial_option_ref=self.option_ref,
            design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,),
            provider_identity=IDENTITY,
            policy=GeometryProposalPolicy(rounds),
            template_refs=(self.template_ref,),
        )

    async def test_catalog_selection_is_receipted(self) -> None:
        provider = _ScriptedProvider(
            (
                proposal_authoring_output(
                    self.proposal,
                    selected_template_refs=(self.template_ref.uri,),
                ),
            )
        )
        result = await self._produce(provider)
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        record = self.repository.load_json(result.proposal_ref)
        self.assertEqual(
            record["selected_template_refs"], [self.template_ref.uri]
        )
        # P091 acceptance: "...and realized on a test project"
        realized = realize_geometry(
            result.program, workspace_id="template-selected-proposal"
        )
        self.assertIsNotNone(realized.scene)

    async def test_out_of_catalog_selection_fails_typed(self) -> None:
        provider = _ScriptedProvider(
            (
                proposal_authoring_output(
                    self.proposal,
                    selected_template_refs=(
                        "project://demo/runs/run/records/"
                        "component-template-" + "c" * 64 + ".json",
                    ),
                ),
            )
        )
        accepted = None
        try:
            result = await self._produce(provider, rounds=1)
            accepted = result.status is GeometryProposalStatus.ACCEPTED
        except GeometryProposalProductionError:
            accepted = False
        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
