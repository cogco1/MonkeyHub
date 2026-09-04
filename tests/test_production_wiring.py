"""P090/P093 on the write path: datums and catalog gates fire in production.

The kernels were built with green unit tests but no production caller
(review of 2026-09-01). These tests drive the real producer and assert
that (a) supplied datums reach the compiled program and derive bound
parameters, and (b) a catalog confrontation constrains what the model
may select — a declined family cannot be quietly re-selected.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.capabilities.component_catalog import (
    CatalogEntry,
    FamilyDeclination,
    FamilySelection,
    confront_catalog,
    record_catalog_confrontation,
)
from archive.archflow.capabilities.component_library import (
    harvest_component_template,
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
from archflow.state.geometry_program import (
    DatumBinding,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from tests.test_component_templates import _stair_template
from tests.test_geometry_compiler import COMMITMENT
from tests.test_geometry_proposal_producer import (
    IDENTITY,
    _ScriptedProvider,
    _spatial_option,
)
from tests.test_sandbox_realization import compiled_room


class _ProducerFixture(unittest.IsolatedAsyncioTestCase):
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
            evidence_refs=("evidence:geometry-compiler", self.option_ref.uri),
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

    async def _produce(self, provider, **extra):
        return await produce_geometry_program_proposal(
            self.repository,
            provider,
            run=self.run,
            destination=self.destination,
            spatial_option_ref=self.option_ref,
            design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,),
            provider_identity=IDENTITY,
            policy=GeometryProposalPolicy(extra.pop("rounds", 1)),
            template_refs=(self.template_ref,),
            **extra,
        )


class DatumsOnTheWritePathTests(_ProducerFixture):
    async def test_supplied_datums_derive_a_bound_parameter(self) -> None:
        operation = self.proposal.operations[0]
        publisher = operation.output_object_ids[0]
        datum = InterfaceDatum.create(
            datum_id="datum-bearing-level",
            kind=InterfaceDatumKind.LEVEL,
            published_by=publisher,
            value=3.57,
            unit=LengthUnit.METER,
        )
        binding = DatumBinding(
            binding_id="bind-bearing",
            datum_id="datum-bearing-level",
            op_id=operation.op_id,
            parameter_name="bearing_level",
        )
        provider = _ScriptedProvider(
            (proposal_authoring_output(self.proposal),)
        )
        result = await self._produce(
            provider,
            interface_datums=(datum,),
            datum_bindings=(binding,),
        )
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        program = result.program
        assert program is not None
        self.assertEqual(len(program.interface_datums), 1)
        compiled_op = next(
            item for item in program.proposal.operations
            if item.op_id == operation.op_id
        )
        names = {item.name: item for item in compiled_op.parameters}
        self.assertIn("bearing_level", names)
        self.assertEqual(names["bearing_level"].value_json, "3.57")
        # the authored proposal never restated the value
        authored = {item.name for item in operation.parameters}
        self.assertNotIn("bearing_level", authored)


class CatalogGateOnTheWritePathTests(_ProducerFixture):
    def _confrontation_ref(self, *, selected: bool):
        family = "exterior-stair"
        catalog = (
            CatalogEntry(
                family=family,
                template_id="palladian-exterior-stair",
                template_ref=self.template_ref.uri,
            ),
        )
        if selected:
            payload = confront_catalog(
                intended_families=(family,),
                catalog=catalog,
                selections=(
                    FamilySelection(
                        family=family, template_ref=self.template_ref.uri
                    ),
                ),
            )
        else:
            payload = confront_catalog(
                intended_families=(family,),
                catalog=catalog,
                declinations=(
                    FamilyDeclination(
                        family=family, reason="searching a new form"
                    ),
                ),
            )
        return record_catalog_confrontation(
            self.repository,
            run=self.run,
            destination=self.destination,
            payload=payload,
        )

    async def test_selected_confrontation_admits_the_template(self) -> None:
        provider = _ScriptedProvider(
            (
                proposal_authoring_output(
                    self.proposal,
                    selected_template_refs=(self.template_ref.uri,),
                ),
            )
        )
        result = await self._produce(
            provider,
            catalog_confrontation_ref=self._confrontation_ref(selected=True),
        )
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)

    async def test_declined_family_cannot_be_reselected_by_the_model(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            (
                proposal_authoring_output(
                    self.proposal,
                    selected_template_refs=(self.template_ref.uri,),
                ),
            )
        )
        accepted = None
        try:
            result = await self._produce(
                provider,
                catalog_confrontation_ref=self._confrontation_ref(
                    selected=False
                ),
            )
            accepted = result.status is GeometryProposalStatus.ACCEPTED
        except GeometryProposalProductionError:
            accepted = False
        self.assertFalse(accepted)

    async def test_wrong_record_kind_fails_typed(self) -> None:
        provider = _ScriptedProvider(
            (proposal_authoring_output(self.proposal),)
        )
        with self.assertRaises(GeometryProposalProductionError):
            await self._produce(
                provider, catalog_confrontation_ref=self.template_ref
            )


if __name__ == "__main__":
    unittest.main()
