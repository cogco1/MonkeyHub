from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import (
    _FUNCTION_CONTRACTS,
    _authoring_output_contract,
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    load_geometry_proposal_lineage,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.realization import realize_geometry
from archflow.state import (
    ComponentMaturity,
    DesignComponent,
    MassingVolume,
    SiteBounds,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialConnection,
    SpatialZone,
)
from archflow.state.geometry_program import (
    AssemblyKind,
    GeometryOperationKind,
    required_assembly_roles,
)
from tests.test_geometry_compiler import COMMITMENT, EVIDENCE
from tests.test_sandbox_realization import compiled_room


IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-provider",
    model_id="scripted-model",
    provider_version="1",
    provider_fingerprint="f" * 64,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class _ScriptedProvider:
    def __init__(
        self,
        outputs: tuple[dict[str, object] | ModelInvocationStatus, ...],
        *,
        identity: GeometryProposalProviderIdentity = IDENTITY,
    ) -> None:
        self.outputs = list(outputs)
        self.identity = identity
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, ModelInvocationStatus):
            return ModelInvocationReceipt(
                receipt_id=f"scripted-receipt-{len(self.requests):02d}",
                status=output,
                request=request,
                provider_id=self.identity.provider_id,
                model_id=self.identity.model_id,
                provider_version=self.identity.provider_version,
                provider_fingerprint=self.identity.provider_fingerprint,
                input_bytes=len(request.payload_json.encode("utf-8")),
                output_bytes=0,
                output_sha256=None,
                output_json=None,
                error_code=output.value,
                message="scripted refusal",
            )
        encoded = _canonical_json(output)
        return ModelInvocationReceipt(
            receipt_id=f"scripted-receipt-{len(self.requests):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=self.identity.provider_id,
            model_id=self.identity.model_id,
            provider_version=self.identity.provider_version,
            provider_fingerprint=self.identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            output_json=encoded,
        )


def _spatial_option() -> SpatialOptionProposal:
    evidence = ("evidence:spatial-option",)
    return SpatialOptionProposal(
        option_id="selected-option",
        label="Project supplied selected option",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(1.0, "square-meter", evidence),
        footprint_cells=((0, 0), (0, 1), (1, 0), (1, 1)),
        levels=(SpatialLevel("ground", 0, 3, evidence),),
        volumes=(
            MassingVolume(
                "main-volume",
                SiteBounds((0, 0, 0), (1, 2, 1)),
                ("ground",),
                evidence,
            ),
        ),
        zones=(
            SpatialZone(
                "main-zone",
                ("program-node:main",),
                ("ground",),
                ("main-volume",),
                evidence,
            ),
            SpatialZone(
                "entry-zone",
                ("program-node:main",),
                ("ground",),
                ("main-volume",),
                evidence,
            ),
        ),
        components=(
            DesignComponent(
                component_id="building",
                parent_component_id=None,
                semantic_kind="building",
                intent="Own the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=("main-volume",),
                unresolved_child_roles=(),
                source_refs=evidence,
            ),
            DesignComponent(
                component_id="primary-support",
                parent_component_id="building",
                semantic_kind="structural-support",
                intent="Carry the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=evidence,
            ),
            DesignComponent(
                component_id="primary-surface",
                parent_component_id="building",
                semantic_kind="enclosure-surface",
                intent="Resolve the selected schematic envelope.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=evidence,
            ),
        ),
        connections=(
            SpatialConnection(
                connection_id="outside-to-room",
                source_zone_id="entry-zone",
                target_zone_id="main-zone",
                relationship_refs=("interface:outside-to-room",),
                directed=False,
                source_refs=evidence,
            ),
        ),
        constraint_responses=(),
        typology_hypothesis="Project supplied typology hypothesis",
        palette_refs=(),
        rationale="Selected from project-authored spatial alternatives.",
        responds_to_refs=("program:main",),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )


class GeometryProposalProducerTests(unittest.IsolatedAsyncioTestCase):
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
            record_kind="spatial-option",
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
            evidence_refs=(EVIDENCE, self.option_ref.uri),
        )
        self.proposal = replace(
            original_program.proposal,
            project_id=self.run.project_id,
            run_id=self.run.run_id,
            base=self.run.base,
            design_state_digest=self.design_state.state_digest,
            semantic_bindings=(binding,),
        )

    async def _produce(
        self,
        provider,
        *,
        rounds: int = 2,
        prior_program=None,
        realization_requirements=(),
    ):
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
            prior_program=prior_program,
            realization_requirements=realization_requirements,
        )

    async def test_accepted_record_reloads_compiles_and_realizes(self) -> None:
        provider = _ScriptedProvider(
            (proposal_authoring_output(self.proposal),)
        )
        result = await self._produce(provider)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        self.assertIsNotNone(result.program)
        self.assertIsNotNone(result.proposal_ref)
        loaded = load_geometry_proposal_lineage(
            FilesystemProjectRepository.open(self.root),
            result.lineage_ref,
        )
        assert loaded.proposal is not None and result.program is not None
        self.assertEqual(
            loaded.proposal.proposal_digest,
            result.program.proposal.proposal_digest,
        )
        self.assertEqual(
            loaded.lineage.provider_identity,
            IDENTITY,
        )
        realized = realize_geometry(
            result.program,
            workspace_id="record-driven-proposal",
        )
        self.assertIsNotNone(realized.scene)
        self.assertFalse(
            self.repository.load_json(result.proposal_ref)[
                "hard_gate_authority"
            ]
        )

    async def test_request_publishes_and_enforces_available_interfaces(self) -> None:
        provider = _ScriptedProvider(
            (proposal_authoring_output(self.proposal),)
        )
        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        payload = provider.requests[0].payload
        self.assertEqual(
            payload["available_interface_refs"]["refs"],
            ["interface:outside-to-room"],
        )
        body = payload["required_output_contract"]["json_schema"][
            "properties"
        ]["proposal_body"]
        interface_item = body["properties"]["assemblies"]["items"][
            "properties"
        ]["interface_refs"]["items"]
        self.assertEqual(interface_item["enum"], ["interface:outside-to-room"])

    async def test_request_publishes_exact_relational_compiler_contract(self) -> None:
        supplied_requirement = {
            "schema": "GeometryRealizationRequirement@1",
            "requirement_id": "minimum-clear-height",
            "source_refs": ["candidate-value:minimum-clear-height"],
            "property": "walkable_clear_height_cells",
            "relation": "minimum",
            "threshold_json": "3",
            "unit": "sandbox-cell",
        }
        provider = _ScriptedProvider((proposal_authoring_output(self.proposal),))
        result = await self._produce(
            provider,
            rounds=1,
            realization_requirements=(supplied_requirement,),
        )

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        payload = provider.requests[0].payload
        self.assertIsNone(payload["available_predecessor_program_digest"])
        self.assertEqual(
            payload["geometry_coordinate_convention"],
            {
                "schema": "GeometryCoordinateConvention@1",
                "handedness": "right-handed",
                "axis_order": ["x", "y", "z"],
                "axes": {
                    "x": "horizontal width",
                    "y": "vertical up and height",
                    "z": "horizontal depth",
                },
                "footprint_cell_order": ["x", "z"],
                "spatial_bounds_order": ["x", "y", "z"],
                "vector_parameter_order": ["x", "y", "z"],
                "size_parameter_order": [
                    "width_x",
                    "height_y",
                    "depth_z",
                ],
            },
        )
        self.assertEqual(
            payload["required_output_contract"]["coordinate_convention"],
            payload["geometry_coordinate_convention"],
        )
        self.assertTrue(
            any(
                "Never reinterpret Z as vertical" in instruction
                for instruction in payload["instructions"]
            )
        )
        body = payload["required_output_contract"]["json_schema"][
            "properties"
        ]["proposal_body"]
        self.assertIsNone(
            body["properties"]["predecessor_program_digest"]["const"]
        )
        invariants = {
            item.get("id"): item
            for item in payload["required_output_contract"][
                "cross_field_invariants"
            ]
            if "id" in item
        }
        self.assertEqual(
            invariants["predecessor_matches_available_program"]["target"],
            "available_predecessor_program_digest",
        )
        self.assertEqual(
            invariants["assembly_host_is_not_a_member"]["relation"],
            "not_member_of",
        )
        self.assertEqual(
            invariants["host_cut_depends_on_named_host"]["relation"],
            "produced_by_operation_with_input",
        )
        for invariant in invariants.values():
            self.assertIn(invariant["instruction"], payload["instructions"])
        realization = payload["realization_contract"]
        self.assertEqual(
            realization["terminal_physical_rule"],
            "output_not_consumed_and_not_reference_and_not_curve",
        )
        self.assertEqual(
            realization["terminal_solid_semantics"],
            "occupied_material_volume",
        )
        self.assertEqual(
            realization["host_cut_scope"],
            "aperture_volume_equals_host_intersection_cutter",
        )
        self.assertEqual(
            realization["required_properties"],
            [
                supplied_requirement,
                {
                    "schema": "GeometryRealizationPropertyRequirement@1",
                    "source_ref": COMMITMENT,
                    "property": "connected_walkable_region",
                    "minimum_count": 1,
                    "support": "occupied_material_below",
                    "clearance": "empty_space_above",
                }
            ],
        )
        self.assertEqual(
            payload["required_output_contract"]["realization_contract"],
            realization,
        )
        for instruction in realization["instructions"]:
            self.assertIn(instruction, payload["instructions"])

    async def test_noncanonical_realization_threshold_is_rejected(self) -> None:
        requirement = {
            "schema": "GeometryRealizationRequirement@1",
            "requirement_id": "minimum-clear-height",
            "source_refs": ["candidate-value:minimum-clear-height"],
            "property": "walkable_clear_height_cells",
            "relation": "minimum",
            "threshold_json": "{\"cells\": 3}",
            "unit": "sandbox-cell",
        }

        with self.assertRaisesRegex(
            ValueError,
            "threshold_json is not canonical",
        ):
            await self._produce(
                _ScriptedProvider((proposal_authoring_output(self.proposal),)),
                rounds=1,
                realization_requirements=(requirement,),
            )

    async def test_prior_program_digest_is_published_as_exact_predecessor(self) -> None:
        _, prior_program, _ = compiled_room()
        revision = replace(
            self.proposal,
            proposal_id="revision-proposal",
            predecessor_program_digest=prior_program.program_digest,
        )
        provider = _ScriptedProvider((proposal_authoring_output(revision),))
        result = await self._produce(
            provider,
            rounds=1,
            prior_program=prior_program,
        )

        payload = provider.requests[0].payload
        self.assertEqual(
            payload["available_predecessor_program_digest"],
            prior_program.program_digest,
        )
        self.assertEqual(
            payload["available_predecessor_program"],
            prior_program.to_dict(),
        )
        self.assertTrue(
            any(
                "revise that exact geometry program instead of redrawing"
                in instruction
                for instruction in payload["instructions"]
            )
        )
        body = payload["required_output_contract"]["json_schema"][
            "properties"
        ]["proposal_body"]
        self.assertEqual(
            body["properties"]["predecessor_program_digest"]["const"],
            prior_program.program_digest,
        )
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertNotIn(
            "compiler.predecessor_mismatch",
            {issue.code for issue in loaded.rounds[0].issues},
        )

    async def test_host_cut_without_host_dependency_remains_rejected(self) -> None:
        invalid = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        opening = next(
            item
            for item in invalid["proposal_body"]["operations"]
            if "opening" in item["output_object_ids"]
        )
        opening["input_object_ids"].remove("shell")
        result = await self._produce(
            _ScriptedProvider((invalid,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertIn(
            "host-cut geometry does not depend on its named host",
            loaded.rounds[0].issues[0].detail,
        )

    async def test_components_are_supplied_directly_without_projection_rules(
        self,
    ) -> None:
        provider = _ScriptedProvider((proposal_authoring_output(self.proposal),))
        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        payload = provider.requests[0].payload
        self.assertNotIn("candidate_program", payload)
        self.assertEqual(
            {
                item["component_id"]
                for item in payload["developed_design_state"][
                    "selected_schematic"
                ]["option"]["proposal"]["components"]
            },
            {"building", "primary-support", "primary-surface"},
        )
        self.assertEqual(
            payload["required_hosted_component_bindings"],
            [],
        )

    async def test_unavailable_interface_is_persisted_before_compilation(self) -> None:
        invalid = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        invalid["proposal_body"]["assemblies"][0]["interface_refs"] = [
            "interface:not-supplied"
        ]
        result = await self._produce(
            _ScriptedProvider((invalid,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertEqual(
            loaded.rounds[0].issues[0].code,
            "malformed_model_output",
        )
        self.assertIn(
            "absent from the supplied spatial option",
            loaded.rounds[0].issues[0].detail,
        )
        self.assertIsNone(loaded.rounds[0].compiler_receipt_json)

    async def test_bare_interface_is_rejected_by_the_published_typed_shape(self) -> None:
        invalid = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        invalid["proposal_body"]["assemblies"][0]["interface_refs"] = [
            "outside-to-room"
        ]
        result = await self._produce(
            _ScriptedProvider((invalid,)),
            rounds=1,
        )

        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertIn(
            "portable logical reference",
            loaded.rounds[0].issues[0].detail,
        )

    async def test_out_of_vocabulary_round_is_persisted_and_repaired(self) -> None:
        invalid = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        invalid["proposal_body"]["operations"][0]["kind"] = (
            "undeclared_building_primitive"
        )
        provider = _ScriptedProvider(
            (
                invalid,
                proposal_authoring_output(self.proposal),
            )
        )
        result = await self._produce(provider)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        self.assertEqual(len(result.round_refs), 2)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertEqual(loaded.rounds[0].status.value, "rejected")
        self.assertEqual(
            loaded.rounds[0].issues[0].code,
            "malformed_model_output",
        )
        repair_issues = provider.requests[1].payload["repair_issues"]
        self.assertEqual(
            repair_issues[0]["code"],
            "malformed_model_output",
        )
        self.assertIn("operations[0]", repair_issues[0]["detail"])

    async def test_top_level_and_nested_drift_receive_path_specific_repair(
        self,
    ) -> None:
        valid = proposal_authoring_output(self.proposal)
        top_level = json.loads(_canonical_json(valid))
        top_level["geometry_functions"] = top_level.pop("proposal_body")
        nested = json.loads(_canonical_json(valid))
        nested["proposal_body"]["operations"][0].pop("asset_scale")
        provider = _ScriptedProvider(
            (
                top_level,
                nested,
                valid,
            )
        )

        result = await self._produce(provider, rounds=3)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        first_repair = provider.requests[1].payload["repair_issues"][0]
        self.assertIn("missing=['proposal_body']", first_repair["detail"])
        self.assertIn("unexpected=['geometry_functions']", first_repair["detail"])
        second_repair = provider.requests[2].payload["repair_issues"][0]
        self.assertIn("operations[0]", second_repair["detail"])
        self.assertIn("missing=['asset_scale']", second_repair["detail"])

    async def test_unsorted_set_like_json_is_canonicalized(self) -> None:
        unsorted = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        binding = unsorted["proposal_body"]["semantic_bindings"][0]
        binding["object_ids"] = list(reversed(binding["object_ids"]))
        binding["evidence_refs"] = list(reversed(binding["evidence_refs"]))
        operation = next(
            item
            for item in unsorted["proposal_body"]["operations"]
            if len(item["input_object_ids"]) > 1
        )
        operation["input_object_ids"] = list(
            reversed(operation["input_object_ids"])
        )

        result = await self._produce(_ScriptedProvider((unsorted,)))

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        assert result.proposal is not None
        decoded_binding = result.proposal.semantic_bindings[0]
        self.assertEqual(
            decoded_binding.object_ids,
            tuple(sorted(decoded_binding.object_ids)),
        )
        self.assertEqual(
            decoded_binding.evidence_refs,
            tuple(sorted(decoded_binding.evidence_refs)),
        )

    async def test_unsorted_keyed_objects_are_canonicalized(self) -> None:
        unsorted = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        body = unsorted["proposal_body"]
        body["operations"] = list(reversed(body["operations"]))
        parameterized = next(
            item for item in body["operations"] if len(item["parameters"]) > 1
        )
        parameterized["parameters"] = list(
            reversed(parameterized["parameters"])
        )
        body["assemblies"][0]["members"] = list(
            reversed(body["assemblies"][0]["members"])
        )

        result = await self._produce(_ScriptedProvider((unsorted,)))

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        assert result.proposal is not None
        self.assertEqual(
            tuple(item.op_id for item in result.proposal.operations),
            tuple(sorted(item.op_id for item in result.proposal.operations)),
        )
        self.assertEqual(
            tuple(
                item.role.value
                for item in result.proposal.assemblies[0].members
            ),
            tuple(
                sorted(
                    item.role.value
                    for item in result.proposal.assemblies[0].members
                )
            ),
        )
        decoded_operation = next(
            item
            for item in result.proposal.operations
            if item.op_id == parameterized["op_id"]
        )
        self.assertEqual(
            tuple(item.name for item in decoded_operation.parameters),
            tuple(sorted(item.name for item in decoded_operation.parameters)),
        )

    async def test_parameter_json_serialization_is_canonicalized(self) -> None:
        noncanonical = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        body = noncanonical["proposal_body"]
        operation = next(
            item
            for item in body["operations"]
            if any(
                parameter["kind"] == "vector3"
                for parameter in item["parameters"]
            )
        )
        parameter = next(
            item
            for item in operation["parameters"]
            if item["kind"] == "vector3"
        )
        parameter["value_json"] = "[-0.2,3, -0.2]"

        result = await self._produce(_ScriptedProvider((noncanonical,)))

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        assert result.proposal is not None
        decoded_operation = next(
            item
            for item in result.proposal.operations
            if item.op_id == operation["op_id"]
        )
        decoded_parameter = next(
            item
            for item in decoded_operation.parameters
            if item.name == parameter["name"]
        )
        self.assertEqual(
            decoded_parameter.value_json,
            _canonical_json([-0.2, 3.0, -0.2]),
        )

    async def test_invalid_parameter_json_values_remain_rejected(self) -> None:
        malformed = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        malformed_parameter = next(
            parameter
            for operation in malformed["proposal_body"]["operations"]
            for parameter in operation["parameters"]
            if parameter["kind"] == "vector3"
        )
        malformed_parameter["value_json"] = "[0,1,"
        mismatched = json.loads(_canonical_json(malformed))
        mismatched_parameter = next(
            parameter
            for operation in mismatched["proposal_body"]["operations"]
            for parameter in operation["parameters"]
            if parameter["kind"] == "vector3"
        )
        mismatched_parameter["value_json"] = "true"

        result = await self._produce(
            _ScriptedProvider((malformed, mismatched)),
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertIn(
            "geometry parameter must contain JSON",
            loaded.rounds[0].issues[0].detail,
        )
        self.assertIn(
            "must be a 3-vector",
            loaded.rounds[1].issues[0].detail,
        )

    async def test_duplicate_member_role_remains_rejected(self) -> None:
        duplicate = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        members = duplicate["proposal_body"]["assemblies"][0]["members"]
        members.append(json.loads(_canonical_json(members[0])))

        result = await self._produce(
            _ScriptedProvider((duplicate,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        issue = loaded.rounds[0].issues[0]
        self.assertIn("assemblies[0]", issue.detail)
        self.assertIn("unique deterministic roles", issue.detail)

    async def test_duplicate_proposal_identity_remains_rejected(self) -> None:
        duplicate = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        operations = duplicate["proposal_body"]["operations"]
        operations.append(json.loads(_canonical_json(operations[0])))

        result = await self._produce(
            _ScriptedProvider((duplicate,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertIn(
            "operations requires unique deterministic identities",
            loaded.rounds[0].issues[0].detail,
        )

    async def test_missing_required_assembly_roles_remains_rejected(self) -> None:
        incomplete = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        members = incomplete["proposal_body"]["assemblies"][0]["members"]
        incomplete["proposal_body"]["assemblies"][0]["members"] = [
            next(item for item in members if item["role"] == "leaf")
        ]

        result = await self._produce(
            _ScriptedProvider((incomplete,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        issue = loaded.rounds[0].issues[0]
        self.assertIn("assemblies[0]", issue.detail)
        self.assertIn("hosted assembly lacks required roles", issue.detail)

    async def test_duplicate_set_like_json_remains_rejected(self) -> None:
        duplicate = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        binding = duplicate["proposal_body"]["semantic_bindings"][0]
        binding["evidence_refs"].append(binding["evidence_refs"][0])

        result = await self._produce(
            _ScriptedProvider((duplicate,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertIn("semantic_bindings[0]", loaded.rounds[0].issues[0].detail)
        self.assertIn("duplicate values", loaded.rounds[0].issues[0].detail)

    async def test_response_subset_contradiction_remains_rejected(self) -> None:
        invalid = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        operation = invalid["proposal_body"]["operations"][0]
        self.assertEqual(operation["input_object_ids"], [])
        operation["responds_to_object_ids"] = ["unconsumed-object"]

        result = await self._produce(
            _ScriptedProvider((invalid,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        issue = loaded.rounds[0].issues[0]
        self.assertIn("operations[0]", issue.detail)
        self.assertIn("object responses must name operation inputs", issue.detail)

    async def test_provider_refusal_persists_without_fallback(self) -> None:
        provider = _ScriptedProvider(
            (ModelInvocationStatus.BUDGET_EXHAUSTED,)
        )
        result = await self._produce(provider)

        self.assertIs(result.status, GeometryProposalStatus.REFUSED)
        self.assertIsNone(result.proposal)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertEqual(loaded.rounds[0].issues[0].code, "provider_refused")
        self.assertIsNone(loaded.proposal)

    async def test_silent_model_substitution_is_rejected(self) -> None:
        substituted = replace(IDENTITY, model_id="silent-replacement")
        provider = _ScriptedProvider(
            (proposal_authoring_output(self.proposal),),
            identity=substituted,
        )
        result = await self._produce(provider)

        self.assertIs(result.status, GeometryProposalStatus.REFUSED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        self.assertEqual(
            loaded.rounds[0].issues[0].code,
            "provider_identity_mismatch",
        )

    def test_function_contract_matches_declared_vocabulary(self) -> None:
        self.assertEqual(
            set(_FUNCTION_CONTRACTS),
            {item.value for item in GeometryOperationKind},
        )
        curve = _FUNCTION_CONTRACTS["curve"]
        self.assertEqual(
            curve["input_arity"],
            {"minimum": 0, "maximum": 0},
        )
        basis = next(
            item for item in curve["parameters"] if item["name"] == "basis"
        )
        self.assertEqual(basis["kind"], "text")
        self.assertFalse(basis["required"])
        self.assertEqual(
            basis["allowed_value_json"],
            ['"bezier"', '"polyline"'],
        )
        solid = _FUNCTION_CONTRACTS["solid"]
        self.assertEqual(
            {(item["name"], item["kind"], item["unit"])
             for item in solid["parameters"]},
            {("origin", "vector3", "meter"), ("size", "vector3", "meter")},
        )

    async def test_typed_but_unsupported_function_parameter_is_repaired(
        self,
    ) -> None:
        unsupported = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        curve = next(
            operation
            for operation in unsupported["proposal_body"]["operations"]
            if operation["kind"] == "curve"
        )
        curve["parameters"].append(
            {
                "schema": "GeometryParameter@1",
                "name": "basis",
                "kind": "text",
                "value_json": '"nurbs"',
                "unit": None,
            }
        )
        curve["parameters"] = sorted(
            curve["parameters"], key=lambda item: item["name"]
        )

        result = await self._produce(
            _ScriptedProvider((unsupported,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        issue = loaded.rounds[0].issues[0]
        self.assertEqual(issue.code, "malformed_model_output")
        self.assertIn("value_json must be one of", issue.detail)
        self.assertIn('received=\'"nurbs"\'', issue.detail)

    async def test_host_cut_residual_boolean_is_rejected(self) -> None:
        residual = json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )
        body = residual["proposal_body"]
        host_cut_id = next(
            object_id
            for assembly in body["assemblies"]
            for member in assembly["members"]
            if member["role"] == "host_cut"
            for object_id in member["object_ids"]
        )
        operation = next(
            item
            for item in body["operations"]
            if host_cut_id in item["output_object_ids"]
        )
        operation["kind"] = "boolean_difference"
        operation["parameters"] = [
            {
                "schema": "GeometryParameter@1",
                "name": "base_index",
                "kind": "integer",
                "value_json": "0",
                "unit": None,
            }
        ]

        result = await self._produce(
            _ScriptedProvider((residual,)),
            rounds=1,
        )

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        issue = loaded.rounds[0].issues[0]
        self.assertIn("boolean_intersection aperture volume", issue.detail)
        self.assertIn("not a residual boolean result", issue.detail)

    def test_authoring_request_contract_exposes_exact_generic_topology(
        self,
    ) -> None:
        contract = _authoring_output_contract()
        root = contract["json_schema"]
        self.assertFalse(root["additionalProperties"])
        self.assertEqual(
            set(root["required"]),
            {"schema", "selected_template_refs", "proposal_body"},
        )
        body = root["properties"]["proposal_body"]
        self.assertFalse(body["additionalProperties"])
        self.assertEqual(
            set(body["required"]),
            {
                "schema",
                "proposal_id",
                "predecessor_program_digest",
                "length_unit",
                "tolerance",
                "frames",
                "assets",
                "semantic_bindings",
                "operations",
                "assemblies",
                "revisions",
                "retirements",
            },
        )
        operation = body["properties"]["operations"]["items"]
        self.assertEqual(
            set(operation["required"]),
            {
                "schema",
                "op_id",
                "kind",
                "output_object_ids",
                "input_object_ids",
                "frame_id",
                "parameters",
                "semantic_binding_ids",
                "asset_id",
                "asset_socket_id",
                "asset_scale",
                "responds_to_object_ids",
                "responds_to_frame_ids",
                "responds_to_binding_ids",
            },
        )
        encoded = _canonical_json(contract)
        self.assertNotIn('"default"', encoded)
        self.assertNotIn('"examples"', encoded)
        self.assertEqual(
            contract["authority"],
            {
                "proposal_only": True,
                "hard_gate": False,
                "canonical_write": False,
                "platform_mutation": False,
            },
        )
        invariants = contract["cross_field_invariants"]
        self.assertIn(
            {
                "field": "proposal_body.operations[*].responds_to_object_ids",
                "relation": "subset_of",
                "target": "proposal_body.operations[*].input_object_ids",
            },
            invariants,
        )
        self.assertIn(
            "zero inputs",
            operation["properties"]["input_object_ids"]["description"],
        )
        self.assertEqual(
            contract["required_assembly_roles"],
            {
                kind.value: [
                    role.value for role in required_assembly_roles(kind)
                ]
                for kind in AssemblyKind
            },
        )
        self.assertIn(
            {
                "field": "proposal_body.assemblies[*].members[*].role",
                "relation": "contains_all_unique",
                "target": "required_assembly_roles[kind]",
            },
            invariants,
        )


if __name__ == "__main__":
    unittest.main()
