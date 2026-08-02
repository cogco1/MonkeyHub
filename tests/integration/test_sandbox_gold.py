from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import proposal_authoring_output
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
)
from archflow.realization.sandbox import SandboxAssetPayload
from archflow.runtime.sandbox_gold import (
    RawSandboxRequest,
    SandboxGoldError,
    execute_sandbox_gold,
    load_sandbox_gold_inputs,
    reload_sandbox_gold,
)
from archflow.state.candidate_program import CandidateProgramProjection
from archflow.state.geometry_program import (
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    DetailMaturity,
    HostedAssembly,
    LengthUnit,
)
from tests.test_sandbox_realization import compiled_room


PROBE_ROOT = (
    Path(__file__).resolve().parents[2] / "probes" / "p026-sandbox-gold"
)


def _semantic_proposal(*, revised: bool, complete_zones: bool) -> dict[str, object]:
    functions = [
        {
            "function_id": "gathering",
            "label": "Gathering room",
            "capacity": 8,
            "area_m2": 20.0,
        },
        {
            "function_id": "storage",
            "label": "Shared storage",
            "capacity": 2,
            "area_m2": 5.0,
        },
    ]
    footprint = [[x, z] for x in range(5) for z in range(5)]
    zones = [
        {
            "zone_id": "gathering-zone",
            "function_ids": ["gathering"],
            "level_ids": ["ground"],
            "volume_ids": ["main-volume"],
        }
    ]
    if complete_zones:
        zones.append(
            {
                "zone_id": "storage-zone",
                "function_ids": ["storage"],
                "level_ids": ["ground"],
                "volume_ids": ["main-volume"],
            }
        )
    return {
        "schema": (
            "SandboxArchitectProposal@1"
            if revised
            else "SandboxArchitectConcept@1"
        ),
        "proposal_id": "revised-shelter" if revised else "concept-shelter",
        "functions": functions,
        "relations": [],
        "semantic_components": [
            {
                "component_id": "daylight-opening",
                "semantic_kind": "daylight-opening",
                "function_ids": ["gathering"],
                "assembly_kind": "window",
                "intent": "Admit daylight into the gathering room.",
                "rationale": "The raw request asks for daylight.",
            },
            {
                "component_id": "sheltered-entrance",
                "semantic_kind": "entrance",
                "function_ids": ["storage", "gathering"],
                "assembly_kind": "door",
                "intent": "Provide one sheltered entrance to the gathering room.",
                "rationale": "The raw request asks for one sheltered entrance.",
            },
        ],
        "envelope": {
            "width_m": 5,
            "depth_m": 5,
            "clear_height_m": 3,
        },
        "performance_requirements": {
            "minimum_clear_height_m": 2,
            "circulation_min_width_m": 1,
        },
        "spatial_option": {
            "grid_size_m": 1,
            "footprint_cells": footprint,
            "levels": [
                {"level_id": "ground", "base_y": 0, "height": 4}
            ],
            "volumes": [
                {
                    "volume_id": "main-volume",
                    "minimum": [0, 0, 0],
                    "maximum": [5, 4, 5],
                    "level_ids": ["ground"],
                }
            ],
            "zones": zones,
            "typology_hypothesis": "single enclosed shared volume",
            "rationale": "The model places both requested uses in one shared volume.",
        },
        "material_strategy": "warm timber over a stone plinth",
        "rationale": "A compact shared shelter answers the raw request.",
    }


def _geometry_output(request) -> dict[str, object]:
    projection = CandidateProgramProjection.from_dict(
        request.payload["candidate_program"]
    )
    templates = request.payload["available_template_records"]
    if len(templates) != 1:
        raise AssertionError("the scripted model expects one project asset input")
    template = templates[0]
    asset_ref = template["ref"]
    asset_input = template["payload"]
    asset_payload = SandboxAssetPayload(
        asset_id=asset_input["asset_id"],
        vertices=tuple(tuple(item) for item in asset_input["vertices"]),
        faces=tuple(tuple(item) for item in asset_input["faces"]),
    )
    asset_sockets = tuple(asset_input["sockets"])
    asset_uri = (
        f"project://{asset_ref['project_id']}/"
        f"{asset_ref['relative_path']}"
    )
    spatial_uri = request.payload["spatial_option_record"]["ref"]
    spatial_uri = (
        f"project://{spatial_uri['project_id']}/"
        f"{spatial_uri['relative_path']}"
    )
    _, compiled, _ = compiled_room(include_asset=True)
    proposal = compiled.proposal
    asset = replace(
        proposal.assets[0],
        asset_id=asset_payload.asset_id,
        uri=asset_uri,
        media_type=asset_input["media_type"],
        sha256=asset_payload.payload_digest,
        native_unit={"m": LengthUnit.METER}[asset_input["native_unit"]],
        sockets=asset_sockets,
        provenance_refs=tuple(
            sorted({asset_uri, *asset_input["provenance_refs"]})
        ),
    )
    original_binding = proposal.semantic_bindings[0]
    component_value_ids = {
        value.decoded_value["component_id"]: value.value_id
        for value in projection.values
        if isinstance(value.decoded_value, dict)
        and "component_id" in value.decoded_value
    }
    if set(component_value_ids) != {"daylight-opening", "sheltered-entrance"}:
        raise AssertionError("semantic component candidate values are missing")
    general_value_ids = tuple(
        value.value_id
        for value in projection.values
        if value.value_id not in set(component_value_ids.values())
    )
    operation_by_id = {item.op_id: item for item in proposal.operations}
    window_names = {
        "clearance": "window-clearance",
        "frame": "window-frame",
        "hardware": "window-hardware",
        "inner": "window-glazing",
        "opening": "window-opening",
        "opening-tool": "window-opening-tool",
    }
    window_operations = []
    for source_id, target_id in window_names.items():
        source_operation = operation_by_id[source_id]
        remapped_inputs = tuple(
            sorted(
                window_names.get(object_id, object_id)
                for object_id in source_operation.input_object_ids
            )
        )
        window_operations.append(
            replace(
                source_operation,
                op_id=target_id,
                output_object_ids=(target_id,),
                input_object_ids=remapped_inputs,
                semantic_binding_ids=(
                    "daylight-opening-binding",
                    "program-binding",
                ),
            )
        )
    all_object_ids = tuple(
        sorted(
            {
                *original_binding.object_ids,
                *(item for item in window_names.values()),
            }
        )
    )
    evidence_refs = tuple(sorted((spatial_uri, asset_uri)))
    program_binding = replace(
        original_binding,
        binding_id="program-binding",
        object_ids=all_object_ids,
        candidate_value_ids=general_value_ids,
        commitment_refs=("commitment:maintain-egress",),
        evidence_refs=evidence_refs,
    )
    door = proposal.assemblies[0]
    door_objects = tuple(
        sorted(
            {
                door.host_object_id,
                *(
                    object_id
                    for member in door.members
                    for object_id in member.object_ids
                ),
            }
        )
    )
    door_binding = replace(
        original_binding,
        binding_id="sheltered-entrance-binding",
        object_ids=door_objects,
        candidate_value_ids=(component_value_ids["sheltered-entrance"],),
        commitment_refs=("commitment:maintain-egress",),
        evidence_refs=evidence_refs,
    )
    door = replace(
        door,
        semantic_binding_ids=(door_binding.binding_id,),
    )
    window_member_objects = (
        "window-clearance",
        "window-frame",
        "window-glazing",
        "window-hardware",
        "window-opening",
    )
    window_binding = replace(
        original_binding,
        binding_id="daylight-opening-binding",
        object_ids=tuple(sorted({door.host_object_id, *window_member_objects})),
        candidate_value_ids=(component_value_ids["daylight-opening"],),
        commitment_refs=("commitment:maintain-egress",),
        evidence_refs=evidence_refs,
    )
    window = HostedAssembly(
        assembly_id="daylight-window",
        kind=AssemblyKind.WINDOW,
        host_object_id=door.host_object_id,
        host_socket_id="daylight-axis",
        members=tuple(
            sorted(
                (
                    AssemblyMember(AssemblyRole.CLEARANCE, ("window-clearance",)),
                    AssemblyMember(AssemblyRole.FRAME, ("window-frame",)),
                    AssemblyMember(AssemblyRole.GLAZING, ("window-glazing",)),
                    AssemblyMember(AssemblyRole.HARDWARE, ("window-hardware",)),
                    AssemblyMember(AssemblyRole.HOST_CUT, ("window-opening",)),
                ),
                key=lambda item: item.role.value,
            )
        ),
        interface_refs=("interface:interior-to-daylight",),
        semantic_binding_ids=(window_binding.binding_id,),
        maturity=DetailMaturity.FUNCTIONAL,
    )
    rebound = replace(
        proposal,
        proposal_id=f"model-geometry-{projection.selected_revision_digest[:16]}",
        project_id=projection.project_id,
        run_id=projection.run_id,
        base=projection.base,
        candidate_program_digest=projection.projection_digest,
        frames=tuple(
            replace(frame, source_refs=(spatial_uri,))
            for frame in proposal.frames
        ),
        operations=tuple(
            sorted(
                (
                    *(
                        replace(
                            operation,
                            semantic_binding_ids=tuple(
                                sorted(
                                    {
                                        "program-binding",
                                        *(
                                            ("sheltered-entrance-binding",)
                                            if set(operation.output_object_ids)
                                            & set(door_objects)
                                            else ()
                                        ),
                                        *(
                                            ("daylight-opening-binding",)
                                            if door.host_object_id
                                            in operation.output_object_ids
                                            else ()
                                        ),
                                    }
                                )
                            ),
                            asset_id=asset_payload.asset_id,
                            asset_socket_id=asset_sockets[0],
                        )
                        if operation.asset_id is not None
                        else replace(
                            operation,
                            semantic_binding_ids=tuple(
                                sorted(
                                    {
                                        "program-binding",
                                        *(
                                            ("sheltered-entrance-binding",)
                                            if set(operation.output_object_ids)
                                            & set(door_objects)
                                            else ()
                                        ),
                                        *(
                                            ("daylight-opening-binding",)
                                            if door.host_object_id
                                            in operation.output_object_ids
                                            else ()
                                        ),
                                    }
                                )
                            ),
                        )
                        for operation in proposal.operations
                    ),
                    *window_operations,
                ),
                key=lambda item: item.op_id,
            )
        ),
        assets=(asset,),
        semantic_bindings=tuple(
            sorted(
                (program_binding, door_binding, window_binding),
                key=lambda item: item.binding_id,
            )
        ),
        assemblies=tuple(
            sorted((door, window), key=lambda item: item.assembly_id)
        ),
    )
    return proposal_authoring_output(
        rebound,
        selected_template_refs=(asset_uri,),
    )


class _ScriptedArchitectProvider:
    """Model stand-in; all building answers originate in provider output."""

    def __init__(
        self,
        *,
        concept_has_all_zones: bool = False,
        repair_first_geometry_round: bool = False,
        substitute_revision_model: bool = False,
        invalid_component_reference: bool = False,
    ) -> None:
        self.concept_has_all_zones = concept_has_all_zones
        self.repair_first_geometry_round = repair_first_geometry_round
        self.substitute_revision_model = substitute_revision_model
        self.invalid_component_reference = invalid_component_reference
        self.requests = []
        self._geometry_attempts: dict[str, int] = {}

    async def invoke(self, request) -> ModelInvocationReceipt:
        self.requests.append(request)
        if request.payload.get("schema") == "GeometryProposalAuthoringRequest@1":
            checkpoint = request.checkpoint_digest
            attempt = self._geometry_attempts.get(checkpoint, 0) + 1
            self._geometry_attempts[checkpoint] = attempt
            output = _geometry_output(request)
            if self.repair_first_geometry_round and attempt == 1:
                output = json.loads(json.dumps(output))
                output["proposal_body"]["operations"][0]["kind"] = (
                    "undeclared_building_primitive"
                )
        elif request.request_id.endswith("-concept"):
            output = _semantic_proposal(
                revised=False,
                complete_zones=self.concept_has_all_zones,
            )
            if self.invalid_component_reference:
                output["semantic_components"][0]["function_ids"] = [
                    "unknown-function"
                ]
        elif request.request_id.endswith("-revision"):
            output = _semantic_proposal(revised=True, complete_zones=True)
        else:
            raise AssertionError(f"unexpected model request: {request.request_id}")
        output_json = json.dumps(output, sort_keys=True, separators=(",", ":"))
        encoded = output_json.encode("utf-8")
        return ModelInvocationReceipt(
            receipt_id=f"receipt-{len(self.requests):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id="scripted-architect",
            model_id=(
                "silent-replacement"
                if self.substitute_revision_model
                and request.request_id.endswith("-revision")
                else "scripted-model"
            ),
            provider_version="1.0",
            provider_fingerprint="scripted-architect-fingerprint",
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded),
            output_sha256=hashlib.sha256(encoded).hexdigest(),
            output_json=output_json,
        )


def _copy_case_inputs(repository: FilesystemProjectRepository):
    source = FilesystemProjectRepository.open(PROBE_ROOT)
    source_inputs = load_sandbox_gold_inputs(
        source,
        run=source.load_run("input-bootstrap"),
    )
    destination = PersistenceDestination(PersistenceArea.INPUT)
    run = repository.create_run("input-bootstrap")

    def copy(ref, record_kind: str):
        return repository.put_json(
            run=run,
            destination=destination,
            record_kind=record_kind,
            payload=source.load_json(ref),
        )

    request_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="raw-sandbox-request",
        payload=source.load_json(source_inputs.request_ref),
    )
    scenario_ref = copy(
        source_inputs.scenario_ref,
        "sandbox-scenario",
    )
    policy_ref = copy(
        source_inputs.approval_policy_ref,
        "sandbox-approval-policy",
    )
    event_ref = copy(
        source_inputs.authorization_event_ref,
        "sandbox-preauthorization",
    )
    asset_refs = tuple(
        copy(ref, f"sandbox-detail-asset-{index:02d}")
        for index, ref in enumerate(source_inputs.asset_payload_refs, start=1)
    )
    return request_ref, scenario_ref, policy_ref, event_ref, asset_refs


class SandboxGoldTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(temp.name) / "p026-sandbox-gold",
            project_id="p026-sandbox-gold",
            initial_state={"phase": "request", "commitments": []},
        )
        self.inputs = _copy_case_inputs(self.repository)

    def test_runtime_has_no_framework_owned_geometry_template(self) -> None:
        runtime_path = (
            Path(__file__).resolve().parents[2]
            / "archflow"
            / "runtime"
            / "sandbox_gold.py"
        )
        tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        forbidden_constructors = {
            "AffineTransform",
            "AssetReference",
            "CoordinateFrame",
            "GeometryOperation",
            "GeometryProgramProposal",
            "HostedAssembly",
            "SemanticBinding",
        }
        self.assertFalse(forbidden_constructors & called_names)
        self.assertIn("produce_geometry_program_proposal", called_names)

    def _execute(self, provider, *, run_id: str):
        run = self.repository.create_run(run_id)
        request_ref, scenario_ref, policy_ref, event_ref, asset_refs = self.inputs
        return asyncio.run(
            execute_sandbox_gold(
                self.repository,
                run,
                provider,
                request_ref=request_ref,
                scenario_ref=scenario_ref,
                approval_policy_ref=policy_ref,
                authorization_event_ref=event_ref,
                asset_payload_refs=asset_refs,
            )
        )

    def test_gold_run_rejects_unbound_space_then_repairs_and_reloads(self) -> None:
        provider = _ScriptedArchitectProvider(repair_first_geometry_round=True)
        result = self._execute(provider, run_id="gold-001")

        self.assertEqual(result.committed.version, 1)
        self.assertIsNotNone(result.rejected_ref)
        summary = reload_sandbox_gold(self.repository, run_id="gold-001")
        self.assertEqual(summary["accepted_variant"], "revised")
        self.assertIn(
            "usability.use_zone.missing_or_unbound",
            summary["concept_findings"],
        )
        reopened = FilesystemProjectRepository.open(
            self.repository.layout.root
        )
        resurvived = reload_sandbox_gold(reopened, run_id="gold-001")
        self.assertEqual(resurvived["scene_digest"], summary["scene_digest"])

    def test_honest_concept_may_pass_without_staged_rejection(self) -> None:
        provider = _ScriptedArchitectProvider(concept_has_all_zones=True)
        result = self._execute(provider, run_id="gold-direct")

        self.assertIsNone(result.rejected_ref)
        self.assertEqual(len(provider.requests), 2)
        summary = reload_sandbox_gold(self.repository, run_id="gold-direct")
        self.assertEqual(summary["accepted_variant"], "concept")
        self.assertEqual(summary["concept_findings"], [])
        self.assertEqual(len(summary["semantic_geometry_digest"]), 64)
        concept_contract = provider.requests[0].payload[
            "required_output_contract"
        ]
        self.assertFalse(concept_contract["additionalProperties"])
        self.assertEqual(
            set(concept_contract["required"]),
            set(concept_contract["properties"]),
        )
        self.assertIn("rationale", concept_contract["required"])
        component_contract = concept_contract["properties"][
            "semantic_components"
        ]["items"]
        self.assertEqual(
            set(component_contract["required"]),
            set(component_contract["properties"]),
        )
        encoded_contract = json.dumps(concept_contract, sort_keys=True)
        self.assertNotIn('"default"', encoded_contract)
        self.assertNotIn('"examples"', encoded_contract)
        accepted = self.repository.load_json(
            ProjectRecordRef(**summary["accepted_candidate_ref"])
        )
        proposal = accepted["geometry_program"]["proposal"]
        bindings = {
            item["binding_id"]: item
            for item in proposal["semantic_bindings"]
        }
        claimed_members = set()
        for assembly in proposal["assemblies"]:
            self.assertEqual(len(assembly["semantic_binding_ids"]), 1)
            binding = bindings[assembly["semantic_binding_ids"][0]]
            self.assertEqual(len(binding["candidate_value_ids"]), 1)
            self.assertTrue(
                binding["candidate_value_ids"][0].startswith(
                    "semantic-component-"
                )
            )
            members = {
                object_id
                for member in assembly["members"]
                for object_id in member["object_ids"]
            }
            self.assertFalse(claimed_members & members)
            claimed_members.update(members)

    def test_revision_cannot_silently_replace_the_model(self) -> None:
        provider = _ScriptedArchitectProvider(
            substitute_revision_model=True,
        )

        with self.assertRaisesRegex(
            SandboxGoldError,
            "silent substitution rejected",
        ):
            self._execute(provider, run_id="gold-identity-drift")

        self.assertEqual(self.repository.read_head().version, 0)

    def test_invalid_semantic_draft_is_rejected_but_receipt_persists(self) -> None:
        provider = _ScriptedArchitectProvider(
            invalid_component_reference=True,
        )

        with self.assertRaisesRegex(
            SandboxGoldError,
            "function_ids are invalid",
        ):
            self._execute(provider, run_id="gold-invalid-semantic")

        run = self.repository.load_run("gold-invalid-semantic")
        records = self.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        payloads = [self.repository.load_json(ref) for ref in records]
        invocation = next(
            item
            for item in payloads
            if item.get("schema") == "SandboxArchitectInvocationRecord@1"
        )
        self.assertEqual(invocation["variant"], "concept")
        self.assertEqual(invocation["receipt"]["status"], "success")
        self.assertFalse(invocation["canonical_write_authority"])
        self.assertEqual(self.repository.read_head().version, 0)


if __name__ == "__main__":
    unittest.main()
