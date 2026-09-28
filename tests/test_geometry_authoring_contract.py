"""#419: the provider contract states relations and results, not boolean idioms."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from archflow.state.geometry_program import AssemblyRole
from monkeyarch.capabilities import geometry_proposal
from monkeyarch.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalStatus,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from tests.support import COMMITMENT, IDENTITY, ProducerFixture, ScriptedProvider


class AuthoringContractTextTests(unittest.TestCase):
    def test_no_invariant_or_instruction_prescribes_a_boolean(self) -> None:
        contract = geometry_proposal._authoring_output_contract(
            (), realization_contract=geometry_proposal._realization_authoring_contract((), ()))
        ids = {item.get("id") for item in contract["cross_field_invariants"]}
        self.assertNotIn("host_cut_is_aperture_volume", ids)
        self.assertNotIn("host_cut_depends_on_named_host", ids)
        self.assertIn("host_cut_relates_to_named_host", ids)
        text = json.dumps({"invariants": contract["cross_field_invariants"],
                           "realization": contract["realization_contract"]}).lower()
        for idiom in ("boolean_intersection", "explicit boolean", "boolean_scope", "host_cut_scope", "voxel"):
            self.assertNotIn(idiom, text)

    def test_an_operation_may_state_no_semantic_binding(self) -> None:
        schema = geometry_proposal._authoring_output_contract(())["json_schema"]
        operation = schema["properties"]["proposal_body"]["properties"]["operations"]["items"]
        self.assertEqual(operation["properties"]["semantic_binding_ids"]["minItems"], 0)

    def test_extrusion_and_loft_may_be_retained_hidden_for_inspection(self) -> None:
        for kind in ("extrusion", "loft"):
            names = {item["name"] for item in geometry_proposal._FUNCTION_CONTRACTS[kind]["parameters"]}
            self.assertLessEqual({"retain_for_inspection", "hidden_for_inspection"}, names, kind)


class HostCutRoundTests(ProducerFixture):
    async def test_a_host_cut_that_is_the_region_consumed_with_its_host_is_accepted(self) -> None:
        assembly = self.proposal.assemblies[0]
        members = tuple(
            replace(member, object_ids=("opening-tool",)) if member.role is AssemblyRole.HOST_CUT else member
            for member in assembly.members
        )
        proposal = replace(self.proposal, assemblies=(replace(assembly, members=members),))
        result = await produce_geometry_program_proposal(
            self.repository, ScriptedProvider((proposal_authoring_output(proposal),)), run=self.run,
            destination=self.destination, spatial_option_ref=self.option_ref, design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,), provider_identity=IDENTITY, policy=GeometryProposalPolicy(1),
        )
        issues = [self.repository.load_json(ref).get("issues") for ref in result.round_refs]
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED, issues)


if __name__ == "__main__":
    unittest.main()
