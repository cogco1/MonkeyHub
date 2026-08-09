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
from archflow.runtime.geometry_compiler import compile_geometry_program
from archflow.runtime.sandbox_gold import (
    RawSandboxRequest,
    SandboxGoldError,
    _program,
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
    ObjectRetirement,
    ObjectRevisionPrecondition,
)
from tests.test_sandbox_realization import compiled_room


PROBE_ROOT = (
    Path(__file__).resolve().parents[2] / "probes" / "p026-sandbox-gold"
)


def _semantic_proposal(
    *,
    revised: bool,
    complete_zones: bool,
    oversized_envelope: bool = False,
) -> dict[str, object]:
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
            "width_m": 8 if oversized_envelope else 5,
            "depth_m": 8 if oversized_envelope else 5,
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
    component_interface_refs = {
        value.decoded_value["component_id"]: value.ref
        for value in projection.values
        if isinstance(value.decoded_value, dict)
        and "component_id" in value.decoded_value
        and value.decoded_value.get("assembly_kind") is not None
    }
    if set(component_interface_refs) != {
        "daylight-opening",
        "sheltered-entrance",
    }:
        raise AssertionError("semantic component interface refs are missing")
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
        interface_refs=(component_interface_refs["sheltered-entrance"],),
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
        interface_refs=(component_interface_refs["daylight-opening"],),
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
    predecessor = request.payload.get("available_predecessor_program")
    if predecessor is not None:
        revision_reason = ("finding:sandbox-geometry-revision",)
        rebound = replace(
            rebound,
            operations=tuple(
                replace(
                    operation,
                    responds_to_object_ids=operation.input_object_ids,
                    responds_to_frame_ids=(operation.frame_id,),
                    responds_to_binding_ids=operation.semantic_binding_ids,
                )
                for operation in rebound.operations
            ),
            predecessor_program_digest=None,
            revisions=(),
            retirements=(),
        )
        current = compile_geometry_program(
            projection,
            rebound,
            active_commitment_refs=("commitment:maintain-egress",),
            available_asset_digests={asset.asset_id: asset.sha256},
        )
        if current.program is None:
            raise AssertionError(current.receipt.to_dict())
        prior_objects = {
            item["object_id"]: item["object_digest"]
            for item in predecessor["objects"]
        }
        current_objects = {
            item.object_id: item.object_digest
            for item in current.program.objects
        }
        rebound = replace(
            rebound,
            predecessor_program_digest=request.payload[
                "available_predecessor_program_digest"
            ],
            revisions=tuple(
                ObjectRevisionPrecondition(
                    object_id=object_id,
                    expected_digest=prior_objects[object_id],
                    reason_refs=revision_reason,
                )
                for object_id in sorted(set(prior_objects) & set(current_objects))
                if prior_objects[object_id] != current_objects[object_id]
            ),
            retirements=tuple(
                ObjectRetirement(
                    object_id=object_id,
                    expected_digest=prior_objects[object_id],
                    reason_refs=revision_reason,
                )
                for object_id in sorted(set(prior_objects) - set(current_objects))
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
        concept_oversized_envelope: bool = False,
        repair_first_geometry_round: bool = False,
        substitute_revision_model: bool = False,
        invalid_component_reference: bool = False,
        omit_semantic_kind_first: bool = False,
        substitute_concept_repair_model: bool = False,
        block_first_candidate: bool = False,
    ) -> None:
        self.concept_has_all_zones = concept_has_all_zones
        self.concept_oversized_envelope = concept_oversized_envelope
        self.repair_first_geometry_round = repair_first_geometry_round
        self.substitute_revision_model = substitute_revision_model
        self.invalid_component_reference = invalid_component_reference
        self.omit_semantic_kind_first = omit_semantic_kind_first
        self.substitute_concept_repair_model = (
            substitute_concept_repair_model
        )
        self.block_first_candidate = block_first_candidate
        self.requests = []
        self._geometry_attempts: dict[str, int] = {}
        self._semantic_attempts: dict[str, int] = {}
        self._geometry_request_count = 0

    async def invoke(self, request) -> ModelInvocationReceipt:
        self.requests.append(request)
        if request.payload.get("schema") == "GeometryProposalAuthoringRequest@1":
            self._geometry_request_count += 1
            checkpoint = request.checkpoint_digest
            attempt = self._geometry_attempts.get(checkpoint, 0) + 1
            self._geometry_attempts[checkpoint] = attempt
            output = _geometry_output(request)
            if self.repair_first_geometry_round and attempt == 1:
                output = json.loads(json.dumps(output))
                output["proposal_body"]["operations"][0]["kind"] = (
                    "undeclared_building_primitive"
                )
            if self.block_first_candidate and self._geometry_request_count == 1:
                output = json.loads(json.dumps(output))
                body = output["proposal_body"]
                blocker = next(
                    json.loads(json.dumps(operation))
                    for operation in body["operations"]
                    if operation["op_id"] == "outer"
                )
                blocker["op_id"] = "blocking-solid"
                blocker["output_object_ids"] = ["blocking-solid"]
                body["operations"].append(blocker)
                program_binding = next(
                    binding
                    for binding in body["semantic_bindings"]
                    if binding["binding_id"] == "program-binding"
                )
                program_binding["object_ids"].append("blocking-solid")
        elif request.payload.get("authoring_variant") in {"concept", "revised"}:
            variant = request.payload["authoring_variant"]
            attempt = self._semantic_attempts.get(variant, 0) + 1
            self._semantic_attempts[variant] = attempt
            output = _semantic_proposal(
                revised=variant == "revised",
                complete_zones=(
                    True
                    if variant == "revised"
                    else self.concept_has_all_zones
                ),
                oversized_envelope=(
                    variant == "concept"
                    and self.concept_oversized_envelope
                ),
            )
            if self.invalid_component_reference:
                output["semantic_components"][0]["function_ids"] = [
                    "unknown-function"
                ]
            if self.omit_semantic_kind_first and attempt == 1:
                output["semantic_components"][0].pop("semantic_kind")
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
                if (
                    self.substitute_revision_model
                    and request.payload.get("authoring_variant") == "revised"
                )
                or (
                    self.substitute_concept_repair_model
                    and request.payload.get("authoring_variant") == "concept"
                    and request.payload.get("authoring_round") == 2
                )
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

    def test_validation_program_conservatively_quantizes_meter_values(self) -> None:
        proposal = _semantic_proposal(
            revised=False,
            complete_zones=True,
        )
        proposal["envelope"] = {
            "width_m": 5.01,
            "depth_m": 4.01,
            "clear_height_m": 3.0,
        }
        proposal["performance_requirements"] = {
            "minimum_clear_height_m": 2.4,
            "circulation_min_width_m": 1.2,
        }

        program = _program(proposal)

        self.assertEqual(program.footprint.width_blocks, 6)
        self.assertEqual(program.footprint.depth_blocks, 5)
        self.assertEqual(program.minimum_clear_height, 3)
        self.assertEqual(program.circulation_min_width, 2)

    def test_promoted_live_agent_gold_and_prior_rejection_reload(self) -> None:
        repository = FilesystemProjectRepository.open(PROBE_ROOT)
        summary = reload_sandbox_gold(
            repository,
            run_id="gold-agent-cli-020",
        )

        self.assertTrue(summary["accepted"])
        self.assertEqual(summary["accepted_variant"], "concept")
        self.assertEqual(summary["concept_findings"], [])
        self.assertEqual(
            summary["provider_identity"]["provider_id"],
            "codex-agent-cli",
        )
        self.assertEqual(
            summary["provider_identity"]["model_id"],
            "gpt-5.6-sol",
        )
        self.assertEqual(repository.read_head().version, 1)
        accepted = repository.load_json(
            ProjectRecordRef(**summary["accepted_candidate_ref"])
        )
        self.assertTrue(accepted["usability"]["passed"])
        self.assertTrue(accepted["hard_validation"]["passed"])
        self.assertTrue(accepted["readiness"]["ready"])
        self.assertEqual(len(accepted["render_set"]["views"]), 5)
        self.assertFalse(accepted["platform_export_authority"])
        self.assertFalse(accepted["canonical_write_authority"])
        self.assertEqual(
            {
                item["component_id"]
                for item in accepted["proposal"]["semantic_components"]
            },
            {
                "daylight-window-east",
                "daylight-window-west",
                "entrance-door",
                "storage-cabinetry",
            },
        )
        self.assertEqual(
            {item["kind"] for item in accepted["geometry_program"]["proposal"]["assemblies"]},
            {"door", "window"},
        )

        rejected_run = repository.load_run("gold-agent-cli-019")
        rejected_payloads = [
            repository.load_json(ref)
            for ref in repository.list_json(
                run=rejected_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_CANDIDATE,
                    run_id=rejected_run.run_id,
                ),
            )
        ]
        self.assertTrue(
            any(
                payload.get("variant") == "revised"
                and payload.get("sandbox_archive", {}).get("disposition")
                == "rejected"
                and not payload.get("usability", {}).get("passed", True)
                for payload in rejected_payloads
            )
        )

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
        geometry_request = next(
            request
            for request in provider.requests
            if request.payload.get("schema")
            == "GeometryProposalAuthoringRequest@1"
        )
        realization_requirements = geometry_request.payload[
            "realization_contract"
        ]["required_properties"]
        by_id = {
            item["requirement_id"]: item
            for item in realization_requirements
            if item["schema"] == "GeometryRealizationRequirement@1"
        }
        self.assertEqual(by_id["minimum-clear-height"]["threshold_json"], "2")
        self.assertEqual(
            by_id["validation-voxel-resolution"],
            {
                "schema": "GeometryRealizationRequirement@1",
                "requirement_id": "validation-voxel-resolution",
                "source_refs": ["runtime-policy:sandbox-validation-view"],
                "property": "validation_voxel_resolution_m",
                "relation": "exact",
                "threshold_json": "1.0",
                "unit": "meter",
            },
        )
        self.assertEqual(
            by_id["minimum-circulation-width"]["threshold_json"],
            "1",
        )
        self.assertEqual(
            by_id["minimum-exterior-entrances"]["threshold_json"],
            "1",
        )
        self.assertEqual(
            {
                item["requirement_id"]
                for item in realization_requirements
                if item.get("property")
                == "bound_observed_region_for_function"
            },
            {"use-zone-gathering", "use-zone-storage"},
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

    def test_empty_walkable_candidate_becomes_revision_evidence(self) -> None:
        provider = _ScriptedArchitectProvider(
            concept_has_all_zones=True,
            block_first_candidate=True,
        )
        result = self._execute(provider, run_id="gold-empty-walkable-repair")

        self.assertEqual(result.committed.version, 1)
        self.assertIsNotNone(result.rejected_ref)
        summary = reload_sandbox_gold(
            self.repository,
            run_id="gold-empty-walkable-repair",
        )
        self.assertEqual(summary["accepted_variant"], "revised")
        self.assertIn(
            "usability.connectivity.disconnected",
            summary["concept_findings"],
        )
        rejected = self.repository.load_json(result.rejected_ref)
        self.assertFalse(rejected["usability"]["passed"])
        self.assertEqual(
            rejected["sandbox_archive"]["disposition"],
            "rejected",
        )
        self.assertIsNotNone(rejected["sandbox_archive"]["scene_digest"])
        revision_request = next(
            request
            for request in provider.requests
            if request.payload.get("authoring_variant") == "revised"
        )
        repair_findings = revision_request.payload["context"][
            "hard_gate_findings"
        ]
        connectivity = next(
            item
            for item in repair_findings
            if item["code"] == "usability.connectivity.disconnected"
        )
        self.assertEqual(connectivity["source"], "usability")
        self.assertEqual(connectivity["measured"], "0 connected regions")
        self.assertEqual(connectivity["threshold"], "exactly 1 connected region")
        self.assertIsInstance(connectivity["evidence_refs"], list)
        revised_geometry_request = next(
            request
            for request in provider.requests
            if request.payload.get("schema")
            == "GeometryProposalAuthoringRequest@1"
            and request.payload["repair_issues"]
            and any(
                item["code"]
                == "sandbox.usability.usability.connectivity.disconnected"
                for item in request.payload["repair_issues"]
            )
        )
        geometry_connectivity = next(
            item
            for item in revised_geometry_request.payload["repair_issues"]
            if item["code"]
            == "sandbox.usability.usability.connectivity.disconnected"
        )
        self.assertEqual(
            json.loads(geometry_connectivity["detail"]),
            connectivity,
        )
        predecessor = revised_geometry_request.payload[
            "available_predecessor_program"
        ]
        self.assertIsNotNone(predecessor)
        self.assertEqual(
            revised_geometry_request.payload[
                "available_predecessor_program_digest"
            ],
            rejected["geometry_receipt"]["compiled_program_digest"],
        )
        self.assertEqual(
            predecessor["proposal_digest"],
            rejected["geometry_program"]["proposal_digest"],
        )

    def test_usability_only_rejection_reloads_cleanly(self) -> None:
        provider = _ScriptedArchitectProvider(
            concept_has_all_zones=True,
            concept_oversized_envelope=True,
        )
        result = self._execute(provider, run_id="gold-usability-only")

        self.assertIsNotNone(result.rejected_ref)
        rejected = self.repository.load_json(result.rejected_ref)
        # Exactly one gate failed: the execution path stored the concept as
        # rejected, and the reload audit must accept that legitimate record.
        self.assertFalse(rejected["usability"]["passed"])
        self.assertTrue(rejected["hard_validation"]["passed"])
        summary = reload_sandbox_gold(
            self.repository,
            run_id="gold-usability-only",
        )
        self.assertEqual(
            summary["concept_findings"],
            ["usability.size.outside_target"],
        )
        self.assertEqual(summary["accepted_variant"], "revised")

    def test_scenario_window_beyond_policy_validity_fails_closed(self) -> None:
        provider = _ScriptedArchitectProvider(concept_has_all_zones=True)
        request_ref, scenario_ref, policy_ref, event_ref, asset_refs = self.inputs
        scenario = self.repository.load_json(scenario_ref)
        # The approval policy caps validity at 3600 seconds; declare ten years.
        excessive = dict(scenario, valid_until_utc="2036-08-02T10:00:00Z")
        excessive_ref = self.repository.put_json(
            run=self.repository.load_run("input-bootstrap"),
            destination=PersistenceDestination(PersistenceArea.INPUT),
            record_kind="sandbox-scenario-excessive",
            payload=excessive,
        )
        run = self.repository.create_run("gold-excessive-window")

        with self.assertRaisesRegex(
            SandboxGoldError,
            "approval issuance rejected",
        ):
            asyncio.run(
                execute_sandbox_gold(
                    self.repository,
                    run,
                    provider,
                    request_ref=request_ref,
                    scenario_ref=excessive_ref,
                    approval_policy_ref=policy_ref,
                    authorization_event_ref=event_ref,
                    asset_payload_refs=asset_refs,
                )
            )

        self.assertEqual(self.repository.read_head().version, 0)

    def test_approval_binds_persisted_build_policy_record(self) -> None:
        provider = _ScriptedArchitectProvider(concept_has_all_zones=True)
        self._execute(provider, run_id="gold-build-policy")

        summary = reload_sandbox_gold(self.repository, run_id="gold-build-policy")
        accepted = self.repository.load_json(
            ProjectRecordRef(**summary["accepted_candidate_ref"])
        )
        build_binding = next(
            item
            for item in accepted["assembly"]["policies"]
            if item["kind"] == "build"
        )
        prefix = f"project://{self.repository.layout.project_id}/"
        self.assertTrue(build_binding["policy_ref"].startswith(prefix))
        relative_path = build_binding["policy_ref"][len(prefix):]
        record_path = self.repository.layout.root / relative_path
        self.assertTrue(record_path.is_file())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "BuildPolicy@1")
        self.assertTrue(record["disposable_sandbox"])

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
        invocations = sorted(
            (
            item
            for item in payloads
            if item.get("schema") == "SandboxArchitectInvocationRecord@2"
            ),
            key=lambda item: item["round_index"],
        )
        self.assertEqual(len(invocations), 2)
        self.assertEqual(invocations[0]["variant"], "concept")
        self.assertEqual(invocations[0]["receipt"]["status"], "success")
        self.assertIn("function_ids are invalid", invocations[0]["validation_issue"])
        self.assertEqual(
            invocations[1]["repair_issues"],
            [invocations[0]["validation_issue"]],
        )
        self.assertFalse(invocations[1]["canonical_write_authority"])
        self.assertEqual(self.repository.read_head().version, 0)

    def test_missing_semantic_field_repairs_without_silent_fill(self) -> None:
        provider = _ScriptedArchitectProvider(
            concept_has_all_zones=True,
            omit_semantic_kind_first=True,
        )

        result = self._execute(provider, run_id="gold-semantic-schema-repair")

        self.assertEqual(result.committed.version, 1)
        semantic_requests = [
            request
            for request in provider.requests
            if request.payload.get("authoring_variant") == "concept"
        ]
        self.assertEqual(len(semantic_requests), 2)
        repair = semantic_requests[1].payload
        self.assertEqual(repair["authoring_round"], 2)
        self.assertIn("semantic_kind", repair["repair_issues"][0])
        self.assertNotIn(
            "semantic_kind",
            repair["previous_output"]["semantic_components"][0],
        )
        run = self.repository.load_run("gold-semantic-schema-repair")
        records = self.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        invocations = sorted(
            (
                self.repository.load_json(ref)
                for ref in records
                if self.repository.load_json(ref).get("schema")
                == "SandboxArchitectInvocationRecord@2"
            ),
            key=lambda item: item["round_index"],
        )
        self.assertEqual(len(invocations), 2)
        self.assertIsNotNone(invocations[0]["validation_issue"])
        self.assertIsNone(invocations[1]["validation_issue"])
        self.assertEqual(
            invocations[1]["predecessor_invocation_ref"],
            next(
                ref.uri
                for ref in records
                if self.repository.load_json(ref) == invocations[0]
            ),
        )
        reload_sandbox_gold(
            FilesystemProjectRepository.open(self.repository.layout.root),
            run_id="gold-semantic-schema-repair",
        )

    def test_semantic_authoring_repair_rejects_model_substitution(self) -> None:
        provider = _ScriptedArchitectProvider(
            concept_has_all_zones=True,
            omit_semantic_kind_first=True,
            substitute_concept_repair_model=True,
        )

        with self.assertRaisesRegex(
            SandboxGoldError,
            "silent substitution rejected",
        ):
            self._execute(provider, run_id="gold-authoring-identity-drift")

        run = self.repository.load_run("gold-authoring-identity-drift")
        records = self.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        invocations = sorted(
            (
                self.repository.load_json(ref)
                for ref in records
                if self.repository.load_json(ref).get("schema")
                == "SandboxArchitectInvocationRecord@2"
            ),
            key=lambda item: item["round_index"],
        )
        self.assertEqual(len(invocations), 2)
        self.assertIn(
            "silent substitution rejected",
            invocations[1]["validation_issue"],
        )
        self.assertEqual(self.repository.read_head().version, 0)


if __name__ == "__main__":
    unittest.main()
