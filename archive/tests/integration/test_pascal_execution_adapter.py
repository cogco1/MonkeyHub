from __future__ import annotations

from dataclasses import replace
import unittest

from archive.archflow.adapters.mcp_stdio import McpClientTimeout, McpServerInfo
from archive.archflow.adapters.pascal_execution import (
    APPLY_PATCH_TOOL,
    GET_SCENE_TOOL,
    LOAD_TOOL,
    STATUS_TOOL,
    VALIDATE_TOOL,
    VERIFY_TOOL,
    PascalAxisMapping,
    PascalBridgeError,
    PascalExecutionRequest,
    PascalExecutionStatus,
    PascalMcpAdapter,
    PascalMcpConfig,
    PascalSceneTarget,
    _extrusion_mesh,
    _frustum_mesh,
    _loft_mesh,
    _mesh_topology,
    _open_boolean_preview,
    compile_pascal_block_patch,
)
from archflow.project.refs import ProjectVersionRef
from archflow.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
)
from archive.tests.test_design_development import _coordinated_state


DEVELOPED_STATE = _coordinated_state()[3]
BASE = DEVELOPED_STATE.base
EVIDENCE = "evidence:pascal-adapter-test"
BEFORE_HASH = "a" * 64
AFTER_HASH = "b" * 64
LEVEL_ID = "level-main"
TOOLS = (
    STATUS_TOOL,
    LOAD_TOOL,
    GET_SCENE_TOOL,
    APPLY_PATCH_TOOL,
    VALIDATE_TOOL,
    VERIFY_TOOL,
)


def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.VECTOR3,
        value=value,
        unit=LengthUnit.METER,
    )


def _points(name: str, value: list[list[float]]) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.POINTS3,
        value=value,
        unit=LengthUnit.METER,
    )


def _compiled(*, kind: GeometryOperationKind = GeometryOperationKind.SOLID):
    state = DEVELOPED_STATE
    parameters = (
        (
            _vector("origin", [1.0, 2.0, 3.0]),
            _vector("size", [4.0, 5.0, 6.0]),
        )
        if kind is GeometryOperationKind.SOLID
        else (_points("points", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),)
    )
    operation = GeometryOperation(
        op_id="massing-operation",
        kind=kind,
        output_object_ids=("massing-object",),
        input_object_ids=(),
        frame_id="world",
        parameters=parameters,
        semantic_binding_ids=("massing-binding",),
    )
    proposal = GeometryProgramProposal(
        proposal_id="massing-proposal",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=(EVIDENCE,),
            ),
        ),
        assets=(),
        semantic_bindings=(
            SemanticBinding(
                binding_id="massing-binding",
                component_id="building",
                object_ids=("massing-object",),
                commitment_refs=(),
                evidence_refs=(EVIDENCE,),
            ),
        ),
        operations=(operation,),
        assemblies=(),
    )
    result = compile_geometry_program(state, proposal)
    if result.program is None:
        raise AssertionError(result.receipt.to_dict())
    return result.program, result.receipt


def _target(**changes: object) -> PascalSceneTarget:
    values: dict[str, object] = {
        "scene_id": "archflow-stage-one",
        "level_id": LEVEL_ID,
        "branch_id": "branch-a",
        "stage": 1,
        "base": BASE,
        "expected_scene_version": 4,
        "expected_graph_sha256": BEFORE_HASH,
    }
    values.update(changes)
    return PascalSceneTarget(**values)  # type: ignore[arg-type]


def _request(*, kind: GeometryOperationKind = GeometryOperationKind.SOLID):
    program, receipt = _compiled(kind=kind)
    return PascalExecutionRequest(program, receipt, _target())


def _result(payload: dict[str, object]) -> dict[str, object]:
    return {"structuredContent": payload}


class _ScriptedClient:
    def __init__(
        self,
        *,
        tools: tuple[str, ...] = TOOLS,
        stale: bool = False,
        fail_apply: bool = False,
        readback_drift: bool = False,
        validation_valid: bool = True,
        verification_has_issues: bool = False,
    ) -> None:
        self.tools = tools
        self.stale = stale
        self.fail_apply = fail_apply
        self.readback_drift = readback_drift
        self.validation_valid = validation_valid
        self.verification_has_issues = verification_has_issues
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.applied = False
        self.after_nodes: dict[str, dict[str, object]] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def initialize(self) -> McpServerInfo:
        return McpServerInfo("pascal", "test", "2025-03-26")

    def list_tools(self) -> tuple[str, ...]:
        return self.tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append((name, arguments))
        if name == STATUS_TOOL:
            if self.applied:
                return _result(self._status(5, AFTER_HASH))
            version = 5 if self.stale else 4
            return _result(self._status(version, BEFORE_HASH))
        if name == LOAD_TOOL:
            version = 5 if self.stale else 4
            return _result(self._status(version, BEFORE_HASH))
        if name == GET_SCENE_TOOL:
            nodes: dict[str, object] = {
                LEVEL_ID: {
                    "object": "node",
                    "id": LEVEL_ID,
                    "type": "level",
                    "children": [],
                    "level": 0,
                }
            }
            nodes.update(self.after_nodes)
            return _result({"nodes": nodes, "rootNodeIds": [LEVEL_ID]})
        if name == APPLY_PATCH_TOOL:
            if self.fail_apply:
                raise McpClientTimeout("scripted apply timeout")
            patches = arguments.get("patches")
            if not isinstance(patches, list):
                raise AssertionError("patch list missing")
            created: list[str] = []
            deleted: list[str] = []
            for patch in patches:
                if not isinstance(patch, dict):
                    raise AssertionError("invalid patch")
                if patch.get("op") == "create":
                    node = patch.get("node")
                    if not isinstance(node, dict):
                        raise AssertionError("create node missing")
                    copied = dict(node)
                    if self.readback_drift:
                        copied["metadata"] = {"archflow": {"object_id": "drift"}}
                    node_id = str(copied["id"])
                    created.append(node_id)
                    self.after_nodes[node_id] = copied
                elif patch.get("op") == "delete":
                    deleted.append(str(patch["id"]))
            self.applied = True
            return _result(
                {
                    "appliedOps": len(patches),
                    "createdIds": created,
                    "deletedIds": deleted,
                }
            )
        if name == VALIDATE_TOOL:
            return _result(
                {
                    "valid": self.validation_valid,
                    "errors": [] if self.validation_valid else [{"message": "bad"}],
                }
            )
        if name == VERIFY_TOOL:
            return _result(
                {
                    "valid": True,
                    "levels": [],
                    "issues": ["placeholder"] if self.verification_has_issues else [],
                    "hasIssues": self.verification_has_issues,
                }
            )
        raise AssertionError(f"unexpected tool: {name}")

    @staticmethod
    def _status(version: int, graph_hash: str) -> dict[str, object]:
        return {
            "id": "archflow-stage-one",
            "version": version,
            "graphHash": graph_hash,
            "levelIds": [LEVEL_ID],
            "defaultLevelId": LEVEL_ID,
        }


class _Adapter(PascalMcpAdapter):
    def __init__(
        self,
        client: _ScriptedClient,
        *,
        allow_scene_write: bool,
    ) -> None:
        super().__init__(
            PascalMcpConfig(
                command=("unused-scripted-client",),
                allow_scene_write=allow_scene_write,
            )
        )
        self.scripted = client

    def _client(self):
        return self.scripted


class PascalPatchCompilerTests(unittest.TestCase):
    def test_transport_command_cannot_embed_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not embed credentials"):
            PascalMcpConfig(
                command=("pascal", "--auth-token", "secret"),
            )

    def test_compiled_solid_becomes_bound_right_handed_pascal_block(self) -> None:
        request = _request()
        scene = {
            "nodes": {LEVEL_ID: {"id": LEVEL_ID, "type": "level"}},
            "rootNodeIds": [LEVEL_ID],
        }
        plan = compile_pascal_block_patch(request, scene)
        self.assertEqual(len(plan.patches), 1)
        patch = plan.patches[0]
        self.assertEqual(patch["op"], "create")
        node = patch["node"]
        self.assertEqual(node["type"], "block")
        self.assertEqual(node["parentId"], LEVEL_ID)
        binding = node["metadata"]["archflow"]
        self.assertEqual(binding["object_id"], "massing-object")
        self.assertEqual(binding["stage"], 1)
        self.assertEqual(
            binding["base"]["state_sha256"],
            BASE.require_digest(),
        )
        positions = {
            tuple(vertex["position"])
            for vertex in node["topology"]["vertices"]
        }
        self.assertIn((1.0, 3.0, -2.0), positions)
        self.assertIn((5.0, 9.0, -7.0), positions)
        payload = plan.to_dict()
        self.assertFalse(payload["scene_write_authority"])
        self.assertFalse(payload["hard_gate_authority"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_y_up_source_mapping_preserves_pascal_y_up_coordinates(self) -> None:
        original = _request()
        request = replace(
            original,
            target=replace(
                original.target,
                axis_mapping=PascalAxisMapping.Y_UP_RIGHT_HANDED,
            ),
        )
        plan = compile_pascal_block_patch(
            request,
            {
                "nodes": {LEVEL_ID: {"id": LEVEL_ID, "type": "level"}},
                "rootNodeIds": [LEVEL_ID],
            },
        )
        positions = {
            tuple(vertex["position"])
            for vertex in plan.patches[0]["node"]["topology"]["vertices"]
        }
        self.assertIn((1.0, 2.0, 3.0), positions)
        self.assertIn((5.0, 7.0, 9.0), positions)

    def test_exact_existing_object_is_an_unchanged_noop(self) -> None:
        request = _request()
        empty_scene = {
            "nodes": {LEVEL_ID: {"id": LEVEL_ID, "type": "level"}},
            "rootNodeIds": [LEVEL_ID],
        }
        initial = compile_pascal_block_patch(request, empty_scene)
        node = initial.patches[0]["node"]
        replay_scene = {
            "nodes": {
                LEVEL_ID: {"id": LEVEL_ID, "type": "level"},
                node["id"]: node,
            },
            "rootNodeIds": [LEVEL_ID],
        }
        replay = compile_pascal_block_patch(request, replay_scene)
        self.assertEqual(replay.patches, ())

    def test_polygonal_lowerers_produce_pascal_valid_boundary_edges(self) -> None:
        extrusion = _extrusion_mesh(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            (0.0, 0.0, 2.0),
            "extrusion-test",
        )
        frustum = _frustum_mesh(
            (0.0, 0.0, 0.0),
            (0.0, 2.0, 0.0),
            1.0,
            0.5,
            0.01,
            "revolve-test",
        )
        loft = _loft_mesh(
            (
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 2.0, 0.0),
                (0.5, 2.0, 0.0),
                (0.5, 2.0, 0.5),
                (0.0, 2.0, 0.5),
            ),
            4,
            closed_profile=True,
            cap_ends=True,
            operation_id="loft-test",
        )
        for operation_id, mesh in (
            ("extrusion-test", extrusion),
            ("revolve-test", frustum),
            ("loft-test", loft),
        ):
            topology = _mesh_topology(list(mesh.vertices), mesh.faces, operation_id)
            edge_pairs = {
                tuple(sorted(edge["vertexIds"])) for edge in topology["edges"]
            }
            for face in topology["faces"]:
                ids = face["vertexIds"]
                for index, start in enumerate(ids):
                    self.assertIn(
                        tuple(sorted((start, ids[(index + 1) % len(ids)]))),
                        edge_pairs,
                    )

    def test_boolean_preview_is_explicitly_issue_bearing_and_open(self) -> None:
        base = _extrusion_mesh(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            (0.0, 0.0, 1.0),
            "base",
        )
        cutter = _extrusion_mesh(
            [(0.0, 0.0, 0.9), (1.0, 0.0, 0.9), (1.0, 1.0, 0.9), (0.0, 1.0, 0.9)],
            (0.0, 0.0, 0.2),
            "cutter",
        )
        preview = _open_boolean_preview(base, (cutter,), 0.001, "difference")
        self.assertEqual(len(preview.faces), len(base.faces) - 1)
        self.assertEqual(len(preview.issues), 1)
        self.assertIn("exact neutral CSG mesh", preview.issues[0])

    def test_unsupported_neutral_operation_fails_before_pascal_mutation(self) -> None:
        client = _ScriptedClient()
        receipt = _Adapter(client, allow_scene_write=True).execute(
            _request(kind=GeometryOperationKind.CURVE)
        )
        self.assertEqual(
            receipt.status,
            PascalExecutionStatus.TRANSLATION_REJECTED,
        )
        self.assertEqual(receipt.failure_code, "PASCAL_TRANSLATION_UNSUPPORTED_OPERATION")
        self.assertFalse(receipt.scene_may_have_changed)
        self.assertNotIn(APPLY_PATCH_TOOL, [name for name, _ in client.calls])

    def test_request_rejects_drifted_compilation_receipt(self) -> None:
        program, receipt = _compiled()
        drifted = replace(receipt, compiled_program_digest="9" * 64)
        with self.assertRaisesRegex(
            PascalBridgeError,
            "COMPILATION_RECEIPT_MISMATCH",
        ):
            PascalExecutionRequest(program, drifted, _target())


class PascalMcpAdapterTests(unittest.TestCase):
    def test_missing_required_capability_fails_before_apply(self) -> None:
        client = _ScriptedClient(
            tools=tuple(name for name in TOOLS if name != VERIFY_TOOL)
        )
        receipt = _Adapter(client, allow_scene_write=True).execute(_request())
        self.assertEqual(receipt.status, PascalExecutionStatus.CAPABILITY_MISSING)
        self.assertEqual(receipt.failure_code, "PASCAL_CAPABILITY_MISSING")
        self.assertFalse(receipt.scene_may_have_changed)
        self.assertEqual(client.calls, [])

    def test_write_disabled_prepares_patch_without_external_mutation(self) -> None:
        client = _ScriptedClient()
        receipt = _Adapter(client, allow_scene_write=False).execute(_request())
        self.assertEqual(receipt.status, PascalExecutionStatus.WRITE_DISABLED)
        self.assertEqual(receipt.patch_count, 1)
        self.assertFalse(receipt.scene_may_have_changed)
        self.assertNotIn(APPLY_PATCH_TOOL, [name for name, _ in client.calls])
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])

    def test_success_is_read_back_but_remains_candidate_evidence(self) -> None:
        client = _ScriptedClient()
        receipt = _Adapter(client, allow_scene_write=True).execute(_request())
        self.assertEqual(
            receipt.status,
            PascalExecutionStatus.CANDIDATE_EVIDENCE_READY,
        )
        self.assertEqual(receipt.observed_before_version, 4)
        self.assertEqual(receipt.observed_after_version, 5)
        self.assertEqual(receipt.observed_after_graph_sha256, AFTER_HASH)
        self.assertEqual(receipt.server_name, "pascal")
        self.assertEqual(receipt.server_version, "test")
        self.assertEqual(receipt.protocol_version, "2025-03-26")
        self.assertTrue(receipt.validation_passed)
        self.assertTrue(receipt.verification_passed)
        self.assertTrue(receipt.scene_may_have_changed)
        payload = receipt.to_dict()
        self.assertFalse(payload["design_acceptance_authority"])
        self.assertFalse(payload["hard_gate_authority"])
        self.assertFalse(payload["canonical_state_mutated"])
        self.assertFalse(payload["canonical_write_authority"])
        self.assertEqual(
            [name for name, _ in client.calls].count(APPLY_PATCH_TOOL),
            1,
        )

    def test_stale_pascal_version_fails_before_apply(self) -> None:
        client = _ScriptedClient(stale=True)
        receipt = _Adapter(client, allow_scene_write=True).execute(_request())
        self.assertEqual(
            receipt.status,
            PascalExecutionStatus.EXACT_BASE_MISMATCH,
        )
        self.assertEqual(receipt.failure_code, "PASCAL_SCENE_VERSION_MISMATCH")
        self.assertFalse(receipt.scene_may_have_changed)
        self.assertNotIn(APPLY_PATCH_TOOL, [name for name, _ in client.calls])

    def test_apply_timeout_is_outcome_unknown_and_never_replayed(self) -> None:
        client = _ScriptedClient(fail_apply=True)
        receipt = _Adapter(client, allow_scene_write=True).execute(_request())
        self.assertEqual(receipt.status, PascalExecutionStatus.OUTCOME_UNKNOWN)
        self.assertEqual(receipt.failure_code, "PASCAL_TIMEOUT_OUTCOME_UNKNOWN")
        self.assertTrue(receipt.scene_may_have_changed)
        self.assertEqual(
            [name for name, _ in client.calls].count(APPLY_PATCH_TOOL),
            1,
        )

    def test_readback_binding_drift_is_not_reported_as_success(self) -> None:
        client = _ScriptedClient(readback_drift=True)
        receipt = _Adapter(client, allow_scene_write=True).execute(_request())
        self.assertEqual(receipt.status, PascalExecutionStatus.READBACK_MISMATCH)
        self.assertEqual(receipt.failure_code, "PASCAL_READBACK_MISMATCH")
        self.assertTrue(receipt.scene_may_have_changed)

    def test_backend_validation_and_verification_are_evidence_not_acceptance(self) -> None:
        invalid = _Adapter(
            _ScriptedClient(validation_valid=False),
            allow_scene_write=True,
        ).execute(_request())
        self.assertEqual(invalid.status, PascalExecutionStatus.VALIDATION_FAILED)
        self.assertFalse(invalid.validation_passed)
        self.assertFalse(invalid.to_dict()["design_acceptance_authority"])

        issues = _Adapter(
            _ScriptedClient(verification_has_issues=True),
            allow_scene_write=True,
        ).execute(_request())
        self.assertEqual(
            issues.status,
            PascalExecutionStatus.CANDIDATE_EVIDENCE_WITH_ISSUES,
        )
        self.assertFalse(issues.verification_passed)
        self.assertEqual(issues.verification_issues, ("placeholder",))
        self.assertFalse(issues.to_dict()["canonical_write_authority"])


if __name__ == "__main__":
    unittest.main()
