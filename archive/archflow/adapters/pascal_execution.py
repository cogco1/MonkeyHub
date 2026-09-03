"""Fail-closed ArchFlow execution boundary for Pascal Editor.

Pascal is treated as a speculative scene backend.  This module translates a
successfully compiled, platform-neutral geometry program into a bounded Pascal
``apply_patch`` request, observes the result, and returns detached evidence.
It deliberately owns no project repository, hard gate, design acceptance, or
canonical committer.

The bridge emits topology-backed Pascal ``block`` nodes.  Exact polygonal
operations (box solids, extrusions, frusta and lofts) are translated
deterministically.  High-level operations without an exact neutral mesh remain
fail-closed unless the caller explicitly enables a named preview approximation;
such approximations are retained as issues and can never become acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archive.archflow.adapters.mcp_stdio import (
    McpClientError,
    McpClientTimeout,
    McpServerInfo,
    StdioMcpClient,
)
from archflow.project.refs import ProjectVersionRef
from archflow.compilers.geometry import (
    CompiledGeometryProgram,
    GeometryCompilationReceipt,
    GeometryCompileStatus,
)
from archflow.state.geometry_program import GeometryOperation, GeometryOperationKind, LengthUnit
from archflow.contracts.canonical import canonical_digest


STATUS_TOOL = "get_project_status"
LOAD_TOOL = "load_scene"
GET_SCENE_TOOL = "get_scene"
APPLY_PATCH_TOOL = "apply_patch"
VALIDATE_TOOL = "validate_scene"
VERIFY_TOOL = "verify_scene"

_REQUIRED_TOOLS = (
    STATUS_TOOL,
    LOAD_TOOL,
    GET_SCENE_TOOL,
    APPLY_PATCH_TOOL,
    VALIDATE_TOOL,
    VERIFY_TOOL,
)
_SCENE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_HEX = frozenset("0123456789abcdef")


class PascalBridgeError(ValueError):
    """A named translation or exact-state boundary failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class PascalAxisMapping(StrEnum):
    """Explicit mapping into Pascal's right-handed X/Z-ground, Y-up frame."""

    Z_UP_RIGHT_HANDED = "z_up_right_handed"
    Y_UP_RIGHT_HANDED = "y_up_right_handed"


class PascalExecutionStatus(StrEnum):
    WRITE_DISABLED = "write_disabled"
    NO_CHANGE = "no_change"
    CANDIDATE_EVIDENCE_READY = "candidate_evidence_ready"
    CANDIDATE_EVIDENCE_WITH_ISSUES = "candidate_evidence_with_issues"
    EXACT_BASE_MISMATCH = "exact_base_mismatch"
    CAPABILITY_MISSING = "capability_missing"
    TRANSLATION_REJECTED = "translation_rejected"
    BACKEND_REJECTED = "backend_rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    READBACK_MISMATCH = "readback_mismatch"
    VALIDATION_FAILED = "validation_failed"


@dataclass(frozen=True, slots=True)
class PascalMcpConfig:
    command: tuple[str, ...]
    timeout_seconds: float = 12.0
    framing: str = "json-lines"
    allow_scene_write: bool = False

    def __post_init__(self) -> None:
        if not self.command or any(
            not isinstance(item, str) or not item.strip() for item in self.command
        ):
            raise ValueError("command must contain non-empty arguments")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.framing not in {"content-length", "json-lines"}:
            raise ValueError("framing must be content-length or json-lines")
        forbidden = {"--auth-token", "--api-key", "--token", "--password"}
        if any(item.strip().lower() in forbidden for item in self.command):
            raise ValueError(
                "command must not embed credentials; use a managed local connector"
            )

    @property
    def transport_fingerprint(self) -> str:
        return canonical_digest(
            {
                "command": list(self.command),
                "framing": self.framing,
            }
        )


@dataclass(frozen=True, slots=True)
class PascalSceneTarget:
    scene_id: str
    level_id: str
    branch_id: str
    stage: int
    base: ProjectVersionRef
    expected_scene_version: int
    expected_graph_sha256: str
    axis_mapping: PascalAxisMapping = PascalAxisMapping.Z_UP_RIGHT_HANDED
    allow_approximate_boolean_preview: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scene_id, str) or _SCENE_ID.fullmatch(
            self.scene_id
        ) is None:
            raise ValueError("scene_id must be a Pascal lowercase slug")
        for value, field in (
            (self.level_id, "level_id"),
            (self.branch_id, "branch_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty text")
        if self.stage not in {1, 2, 3, 4}:
            raise ValueError("stage must be 1, 2, 3, or 4")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if (
            not isinstance(self.expected_scene_version, int)
            or isinstance(self.expected_scene_version, bool)
            or self.expected_scene_version <= 0
        ):
            raise ValueError("expected_scene_version must be a positive integer")
        object.__setattr__(
            self,
            "expected_graph_sha256",
            _require_sha256(
                self.expected_graph_sha256,
                "expected_graph_sha256",
            ),
        )
        if not isinstance(self.axis_mapping, PascalAxisMapping):
            raise TypeError("axis_mapping must be PascalAxisMapping")
        if not isinstance(self.allow_approximate_boolean_preview, bool):
            raise TypeError("allow_approximate_boolean_preview must be bool")


@dataclass(frozen=True, slots=True)
class PascalExecutionRequest:
    program: CompiledGeometryProgram
    compilation_receipt: GeometryCompilationReceipt
    target: PascalSceneTarget

    def __post_init__(self) -> None:
        if not isinstance(self.program, CompiledGeometryProgram):
            raise TypeError("program must be CompiledGeometryProgram")
        if not isinstance(self.compilation_receipt, GeometryCompilationReceipt):
            raise TypeError(
                "compilation_receipt must be GeometryCompilationReceipt"
            )
        if not isinstance(self.target, PascalSceneTarget):
            raise TypeError("target must be PascalSceneTarget")
        proposal = self.program.proposal
        receipt = self.compilation_receipt
        if receipt.status is not GeometryCompileStatus.COMPILED:
            raise PascalBridgeError(
                "GEOMETRY_NOT_COMPILED",
                "Pascal execution requires an accepted geometry compilation",
            )
        if receipt.proposal_digest != proposal.proposal_digest:
            raise PascalBridgeError(
                "COMPILATION_RECEIPT_MISMATCH",
                "compilation receipt names another proposal",
            )
        if receipt.compiled_program_digest != self.program.program_digest:
            raise PascalBridgeError(
                "COMPILATION_RECEIPT_MISMATCH",
                "compilation receipt names another compiled program",
            )
        if self.target.base != proposal.base:
            raise PascalBridgeError(
                "ARCHFLOW_BASE_MISMATCH",
                "Pascal target and geometry proposal are not exact-base peers",
            )

    @property
    def compilation_receipt_digest(self) -> str:
        return canonical_digest(self.compilation_receipt.to_dict())

    @property
    def request_digest(self) -> str:
        return canonical_digest(
            {
                "schema": "PascalExecutionRequest@1",
                "compiled_program_digest": self.program.program_digest,
                "compilation_receipt_digest": self.compilation_receipt_digest,
                "target": _target_to_dict(self.target),
            }
        )


@dataclass(frozen=True, slots=True)
class PascalPatchPlan:
    request_digest: str
    proposal_digest: str
    compiled_program_digest: str
    compilation_receipt_digest: str
    scene_id: str
    level_id: str
    expected_scene_version: int
    expected_graph_sha256: str
    patches: tuple[dict[str, Any], ...]
    translation_issues: tuple[str, ...] = ()

    SCHEMA = "PascalPatchPlan@1"

    @property
    def patch_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_digest": self.request_digest,
            "proposal_digest": self.proposal_digest,
            "compiled_program_digest": self.compiled_program_digest,
            "compilation_receipt_digest": self.compilation_receipt_digest,
            "scene_id": self.scene_id,
            "level_id": self.level_id,
            "expected_scene_version": self.expected_scene_version,
            "expected_graph_sha256": self.expected_graph_sha256,
            "patches": [_clone_json(item) for item in self.patches],
            "translation_issues": list(self.translation_issues),
            "scene_write_authority": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class PascalExecutionReceipt:
    status: PascalExecutionStatus
    request_digest: str
    proposal_digest: str
    compiled_program_digest: str
    compilation_receipt_digest: str
    scene_id: str
    stage: int
    base: ProjectVersionRef
    expected_scene_version: int
    expected_graph_sha256: str
    transport_fingerprint: str
    server_name: str | None
    server_version: str | None
    protocol_version: str | None
    observed_before_version: int | None
    observed_before_graph_sha256: str | None
    observed_after_version: int | None
    observed_after_graph_sha256: str | None
    patch_digest: str | None
    patch_count: int
    readback_sha256: str | None
    validation_passed: bool | None
    verification_passed: bool | None
    validation_messages: tuple[str, ...]
    verification_issues: tuple[str, ...]
    translation_issues: tuple[str, ...]
    scene_may_have_changed: bool
    tool_trace: tuple[str, ...]
    failure_code: str | None = None
    failure_detail: str | None = None

    SCHEMA = "PascalExecutionReceipt@1"

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "status": self.status.value,
            "request_digest": self.request_digest,
            "proposal_digest": self.proposal_digest,
            "compiled_program_digest": self.compiled_program_digest,
            "compilation_receipt_digest": self.compilation_receipt_digest,
            "scene_id": self.scene_id,
            "stage": self.stage,
            "base": _base_to_dict(self.base),
            "expected_scene_version": self.expected_scene_version,
            "expected_graph_sha256": self.expected_graph_sha256,
            "transport_fingerprint": self.transport_fingerprint,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "protocol_version": self.protocol_version,
            "observed_before_version": self.observed_before_version,
            "observed_before_graph_sha256": self.observed_before_graph_sha256,
            "observed_after_version": self.observed_after_version,
            "observed_after_graph_sha256": self.observed_after_graph_sha256,
            "patch_digest": self.patch_digest,
            "patch_count": self.patch_count,
            "readback_sha256": self.readback_sha256,
            "validation_passed": self.validation_passed,
            "verification_passed": self.verification_passed,
            "validation_messages": list(self.validation_messages),
            "verification_issues": list(self.verification_issues),
            "translation_issues": list(self.translation_issues),
            "scene_may_have_changed": self.scene_may_have_changed,
            "tool_trace": list(self.tool_trace),
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "design_acceptance_authority": False,
            "hard_gate_authority": False,
            "canonical_state_mutated": False,
            "canonical_write_authority": False,
        }


class PascalMcpAdapter:
    """Execute a compiled program in Pascal as speculative candidate evidence."""

    capability_id = "pascal.scene.execute_compiled_geometry"

    def __init__(self, config: PascalMcpConfig) -> None:
        self._config = config

    def preview(self, request: PascalExecutionRequest) -> PascalPatchPlan:
        """Read the exact Pascal base and prepare a patch without mutating it."""

        with self._client() as client:
            client.initialize()
            self._require_tools(client.list_tools())
            _, scene = self._load_exact_scene(client, request.target)
            return compile_pascal_block_patch(request, scene)

    def execute(self, request: PascalExecutionRequest) -> PascalExecutionReceipt:
        """Apply once, read back once, and never promote canonical state."""

        trace: list[str] = []
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        plan: PascalPatchPlan | None = None
        readback: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        verification: dict[str, Any] | None = None
        server: McpServerInfo | None = None
        phase = "initialize"
        apply_started = False

        try:
            with self._client() as client:
                server = client.initialize()
                trace.append("initialize")
                phase = "tools/list"
                self._require_tools(client.list_tools())
                trace.append("tools/list")
                before, scene = self._load_exact_scene(
                    client,
                    request.target,
                    trace=trace,
                )
                phase = "translate"
                plan = compile_pascal_block_patch(request, scene)
                trace.append("translate")

                if not plan.patches:
                    return self._receipt(
                        request,
                        PascalExecutionStatus.NO_CHANGE,
                        trace,
                        server=server,
                        before=before,
                        after=before,
                        plan=plan,
                        readback=scene,
                    )
                if not self._config.allow_scene_write:
                    return self._receipt(
                        request,
                        PascalExecutionStatus.WRITE_DISABLED,
                        trace,
                        server=server,
                        before=before,
                        plan=plan,
                        failure_code="PASCAL_SCENE_WRITE_DISABLED",
                        failure_detail=(
                            "set allow_scene_write=True only for an explicitly "
                            "speculative Pascal scene"
                        ),
                    )

                phase = APPLY_PATCH_TOOL
                apply_started = True
                result = _tool_payload(
                    client.call_tool(
                        APPLY_PATCH_TOOL,
                        {"patches": [_clone_json(item) for item in plan.patches]},
                    )
                )
                trace.append(APPLY_PATCH_TOOL)
                _require_apply_ack(plan, result)

                phase = GET_SCENE_TOOL
                readback = _tool_payload(client.call_tool(GET_SCENE_TOOL, {}))
                trace.append(f"{GET_SCENE_TOOL}:after")
                _require_readback(plan, readback)

                phase = VALIDATE_TOOL
                validation = _tool_payload(client.call_tool(VALIDATE_TOOL, {}))
                trace.append(VALIDATE_TOOL)
                phase = VERIFY_TOOL
                verification = _tool_payload(client.call_tool(VERIFY_TOOL, {}))
                trace.append(VERIFY_TOOL)
                phase = STATUS_TOOL
                after = _tool_payload(
                    client.call_tool(STATUS_TOOL, {"id": request.target.scene_id})
                )
                trace.append(f"{STATUS_TOOL}:after")
                _require_after_status(request.target, before, after)

            validation_passed = validation.get("valid") is True
            verification_passed = (
                verification.get("valid") is True
                and verification.get("hasIssues") is not True
            )
            if not validation_passed:
                status = PascalExecutionStatus.VALIDATION_FAILED
                failure_code = "PASCAL_SCENE_INVALID"
            elif not verification_passed:
                status = PascalExecutionStatus.CANDIDATE_EVIDENCE_WITH_ISSUES
                failure_code = None
            elif plan.translation_issues:
                status = PascalExecutionStatus.CANDIDATE_EVIDENCE_WITH_ISSUES
                failure_code = None
            else:
                status = PascalExecutionStatus.CANDIDATE_EVIDENCE_READY
                failure_code = None
            return self._receipt(
                request,
                status,
                trace,
                server=server,
                before=before,
                after=after,
                plan=plan,
                readback=readback,
                validation=validation,
                verification=verification,
                scene_may_have_changed=True,
                failure_code=failure_code,
            )
        except Exception as exc:
            if isinstance(exc, PascalBridgeError):
                if exc.code in {
                    "ARCHFLOW_BASE_MISMATCH",
                    "PASCAL_SCENE_BASE_MISMATCH",
                    "PASCAL_SCENE_VERSION_MISMATCH",
                    "PASCAL_SCENE_HASH_MISMATCH",
                }:
                    status = PascalExecutionStatus.EXACT_BASE_MISMATCH
                elif exc.code == "PASCAL_CAPABILITY_MISSING":
                    status = PascalExecutionStatus.CAPABILITY_MISSING
                elif exc.code.startswith("PASCAL_TRANSLATION"):
                    status = PascalExecutionStatus.TRANSLATION_REJECTED
                elif apply_started:
                    status = PascalExecutionStatus.READBACK_MISMATCH
                else:
                    status = PascalExecutionStatus.BACKEND_REJECTED
                code = exc.code
            elif apply_started:
                status = PascalExecutionStatus.OUTCOME_UNKNOWN
                code = (
                    "PASCAL_TIMEOUT_OUTCOME_UNKNOWN"
                    if isinstance(exc, McpClientTimeout)
                    else "PASCAL_OUTCOME_UNKNOWN"
                )
            else:
                status = PascalExecutionStatus.BACKEND_REJECTED
                code = (
                    "PASCAL_TIMEOUT"
                    if isinstance(exc, McpClientTimeout)
                    else "PASCAL_BACKEND_FAILED"
                )
            return self._receipt(
                request,
                status,
                trace,
                server=server,
                before=before,
                after=after,
                plan=plan,
                readback=readback,
                validation=validation,
                verification=verification,
                scene_may_have_changed=apply_started,
                failure_code=code,
                failure_detail=_bounded_error(exc, phase),
            )

    def _client(self) -> StdioMcpClient:
        return StdioMcpClient(
            self._config.command,
            timeout_seconds=self._config.timeout_seconds,
            framing=self._config.framing,
        )

    @staticmethod
    def _require_tools(available: tuple[str, ...]) -> None:
        missing = sorted(set(_REQUIRED_TOOLS) - set(available))
        if missing:
            raise PascalBridgeError(
                "PASCAL_CAPABILITY_MISSING",
                f"required MCP tools are missing: {missing}",
            )

    @staticmethod
    def _load_exact_scene(
        client: StdioMcpClient,
        target: PascalSceneTarget,
        *,
        trace: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        status = _tool_payload(client.call_tool(STATUS_TOOL, {"id": target.scene_id}))
        if trace is not None:
            trace.append(f"{STATUS_TOOL}:before")
        _require_exact_status(target, status)
        loaded = _tool_payload(client.call_tool(LOAD_TOOL, {"id": target.scene_id}))
        if trace is not None:
            trace.append(LOAD_TOOL)
        _require_exact_status(target, loaded)
        scene = _tool_payload(client.call_tool(GET_SCENE_TOOL, {}))
        if trace is not None:
            trace.append(f"{GET_SCENE_TOOL}:before")
        return status, scene

    def _receipt(
        self,
        request: PascalExecutionRequest,
        status: PascalExecutionStatus,
        trace: list[str],
        *,
        server: McpServerInfo | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        plan: PascalPatchPlan | None = None,
        readback: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        scene_may_have_changed: bool = False,
        failure_code: str | None = None,
        failure_detail: str | None = None,
    ) -> PascalExecutionReceipt:
        return PascalExecutionReceipt(
            status=status,
            request_digest=request.request_digest,
            proposal_digest=request.program.proposal.proposal_digest,
            compiled_program_digest=request.program.program_digest,
            compilation_receipt_digest=request.compilation_receipt_digest,
            scene_id=request.target.scene_id,
            stage=request.target.stage,
            base=request.target.base,
            expected_scene_version=request.target.expected_scene_version,
            expected_graph_sha256=request.target.expected_graph_sha256,
            transport_fingerprint=self._config.transport_fingerprint,
            server_name=server.name if server is not None else None,
            server_version=server.version if server is not None else None,
            protocol_version=(
                server.protocol_version if server is not None else None
            ),
            observed_before_version=_optional_positive_int(before, "version"),
            observed_before_graph_sha256=_optional_sha256(before, "graphHash"),
            observed_after_version=_optional_positive_int(after, "version"),
            observed_after_graph_sha256=_optional_sha256(after, "graphHash"),
            patch_digest=plan.patch_digest if plan is not None else None,
            patch_count=len(plan.patches) if plan is not None else 0,
            readback_sha256=(
                canonical_digest(readback) if readback is not None else None
            ),
            validation_passed=(
                validation.get("valid") is True
                if validation is not None
                else None
            ),
            verification_passed=(
                verification.get("valid") is True
                and verification.get("hasIssues") is not True
                if verification is not None
                else None
            ),
            validation_messages=_bounded_messages(validation, "errors"),
            verification_issues=_bounded_messages(verification, "issues"),
            translation_issues=(
                plan.translation_issues if plan is not None else ()
            ),
            scene_may_have_changed=scene_may_have_changed,
            tool_trace=tuple(trace),
            failure_code=failure_code,
            failure_detail=failure_detail,
        )


@dataclass(frozen=True, slots=True)
class _PascalMesh:
    vertices: tuple[tuple[float, float, float], ...]
    faces: tuple[tuple[int, ...], ...]
    issues: tuple[str, ...] = ()


def compile_pascal_block_patch(
    request: PascalExecutionRequest,
    scene: dict[str, Any],
) -> PascalPatchPlan:
    """Compile one exact program transition to deterministic Pascal blocks."""

    nodes = scene.get("nodes")
    root_ids = scene.get("rootNodeIds")
    if not isinstance(nodes, dict) or not isinstance(root_ids, list):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_SCENE_INVALID",
            "get_scene must return nodes and rootNodeIds",
        )
    level = nodes.get(request.target.level_id)
    if not isinstance(level, dict) or level.get("type") != "level":
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_LEVEL_MISSING",
            f"target level does not exist: {request.target.level_id}",
        )

    program = request.program
    proposal = program.proposal
    frame_matrices = _frame_matrices(program)
    compiled_digests = {
        item.object_id: item.object_digest for item in program.objects
    }
    revisions = {item.object_id: item for item in proposal.revisions}
    retirements = {item.object_id: item for item in proposal.retirements}
    operations = {item.op_id: item for item in proposal.operations}
    ordered_operations: list[GeometryOperation] = []
    for operation_id in program.operation_order:
        operation = operations.get(operation_id)
        if operation is None:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_OPERATION_ORDER",
                f"compiled operation order names missing operation {operation_id}",
            )
        ordered_operations.append(operation)

    meshes: dict[str, _PascalMesh] = {}
    object_frames: dict[str, str] = {}
    consumed_object_ids = {
        object_id
        for operation in ordered_operations
        for object_id in operation.input_object_ids
    }
    for operation in ordered_operations:
        if len(operation.output_object_ids) != 1:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_OUTPUT_ARITY",
                "a Pascal block operation must produce exactly one object",
            )
        object_id = operation.output_object_ids[0]
        meshes[object_id] = _operation_mesh(
            request,
            operation,
            meshes,
            object_frames,
        )
        object_frames[object_id] = operation.frame_id

    patches: list[dict[str, Any]] = []
    translation_issues: list[str] = []
    for operation in ordered_operations:
        object_id = operation.output_object_ids[0]
        if object_id in retirements:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_RETIREMENT_CONFLICT",
                f"retired object is also produced: {object_id}",
            )
        object_digest = compiled_digests.get(object_id)
        if object_digest is None:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_OBJECT_MISSING",
                f"compiled program has no object digest for {object_id}",
            )
        node = _block_node(
            request,
            operation,
            object_id,
            object_digest,
            frame_matrices[operation.frame_id],
            meshes[object_id],
            visible=object_id not in consumed_object_ids,
        )
        translation_issues.extend(meshes[object_id].issues)
        node_id = str(node["id"])
        existing = nodes.get(node_id)
        revision = revisions.get(object_id)
        if revision is None:
            if existing is not None:
                _require_managed_node_digest(
                    existing,
                    object_id,
                    object_digest,
                )
                continue
            patches.append(
                {
                    "op": "create",
                    "node": node,
                    "parentId": request.target.level_id,
                }
            )
        else:
            _require_managed_node_digest(existing, object_id, revision.expected_digest)
            update = dict(node)
            update.pop("id", None)
            update.pop("type", None)
            update.pop("object", None)
            patches.append({"op": "update", "id": node_id, "data": update})

    for object_id, retirement in sorted(retirements.items()):
        node_id = _pascal_block_id(proposal.project_id, proposal.run_id, object_id)
        existing = nodes.get(node_id)
        _require_managed_node_digest(
            existing,
            object_id,
            retirement.expected_digest,
        )
        patches.append({"op": "delete", "id": node_id, "cascade": True})

    patches.sort(
        key=lambda item: (
            {"delete": 0, "update": 1, "create": 2}[str(item["op"])],
            str(item.get("id") or item.get("node", {}).get("id")),
        )
    )
    return PascalPatchPlan(
        request_digest=request.request_digest,
        proposal_digest=proposal.proposal_digest,
        compiled_program_digest=program.program_digest,
        compilation_receipt_digest=request.compilation_receipt_digest,
        scene_id=request.target.scene_id,
        level_id=request.target.level_id,
        expected_scene_version=request.target.expected_scene_version,
        expected_graph_sha256=request.target.expected_graph_sha256,
        patches=tuple(_clone_json(item) for item in patches),
        translation_issues=tuple(sorted(set(translation_issues))),
    )


def _block_node(
    request: PascalExecutionRequest,
    operation: GeometryOperation,
    object_id: str,
    object_digest: str,
    frame_matrix: tuple[float, ...],
    mesh: _PascalMesh,
    *,
    visible: bool,
) -> dict[str, Any]:
    source_unit = request.program.proposal.length_unit
    determinant = _linear_determinant(frame_matrix)
    if abs(determinant) < 1e-12:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_SINGULAR_FRAME",
            f"{operation.op_id} uses a singular frame",
        )
    unit_factor = _unit_to_meters(source_unit)
    positions: list[tuple[float, float, float]] = []
    for point in mesh.vertices:
        transformed = _transform_point(frame_matrix, point)
        mapped = _to_pascal_point(transformed, request.target.axis_mapping)
        positions.append(
            tuple(float(value * unit_factor) for value in mapped)
        )
    faces = tuple(
        tuple(reversed(face)) if determinant < 0 else face
        for face in mesh.faces
    )
    topology = _mesh_topology(positions, faces, operation.op_id)
    proposal = request.program.proposal
    return {
        "object": "node",
        "id": _pascal_block_id(proposal.project_id, proposal.run_id, object_id),
        "type": "block",
        "name": object_id,
        "parentId": request.target.level_id,
        "visible": visible,
        "metadata": {
            "archflow": {
                "schema": "ArchFlowPascalBinding@1",
                "project_id": proposal.project_id,
                "run_id": proposal.run_id,
                "branch_id": request.target.branch_id,
                "base": _base_to_dict(proposal.base),
                "proposal_digest": proposal.proposal_digest,
                "compiled_program_digest": request.program.program_digest,
                "compilation_receipt_digest": request.compilation_receipt_digest,
                "operation_id": operation.op_id,
                "object_id": object_id,
                "object_digest": object_digest,
                "stage": request.target.stage,
                "axis_mapping": request.target.axis_mapping.value,
                "translation_issues": list(mesh.issues),
            }
        },
        "children": [],
        "position": [0.0, 0.0, 0.0],
        "rotation": 0.0,
        "topology": topology,
        "slots": {},
        "slotNames": {"body": "Body"},
    }


def _operation_mesh(
    request: PascalExecutionRequest,
    operation: GeometryOperation,
    meshes: dict[str, _PascalMesh],
    object_frames: dict[str, str],
) -> _PascalMesh:
    parameters = {item.name: item for item in operation.parameters}
    source_unit = request.program.proposal.length_unit
    tolerance = request.program.proposal.tolerance.linear

    if operation.kind is GeometryOperationKind.SOLID:
        origin_param = _required_parameter(parameters, "origin", operation.op_id)
        size_param = _required_parameter(parameters, "size", operation.op_id)
        _require_parameter_unit(origin_param, source_unit, operation.op_id)
        _require_parameter_unit(size_param, source_unit, operation.op_id)
        origin = _parameter_vector(origin_param.value_json, "origin", operation.op_id)
        size = _parameter_vector(size_param.value_json, "size", operation.op_id)
        if any(value <= 0 for value in size):
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_SOLID_SIZE",
                f"{operation.op_id} size must be positive",
            )
        return _PascalMesh(
            vertices=_box_vertices(origin, size),
            faces=tuple(
                tuple(int(index) for index in face)
                for face in (
                    (0, 3, 2, 1),
                    (4, 5, 6, 7),
                    (0, 1, 5, 4),
                    (1, 2, 6, 5),
                    (2, 3, 7, 6),
                    (3, 0, 4, 7),
                )
            ),
        )

    if operation.kind is GeometryOperationKind.EXTRUSION:
        profile_param = _required_parameter(parameters, "profile", operation.op_id)
        vector_param = _required_parameter(parameters, "vector", operation.op_id)
        _require_parameter_unit(profile_param, source_unit, operation.op_id)
        _require_parameter_unit(vector_param, source_unit, operation.op_id)
        profile = list(
            _parameter_points(profile_param.value_json, "profile", operation.op_id)
        )
        vector = _parameter_vector(vector_param.value_json, "vector", operation.op_id)
        return _extrusion_mesh(profile, vector, operation.op_id)

    if operation.kind is GeometryOperationKind.REVOLVE:
        axis_start_param = _required_parameter(
            parameters, "axis_start", operation.op_id
        )
        axis_end_param = _required_parameter(parameters, "axis_end", operation.op_id)
        start_radius_param = _required_parameter(
            parameters, "start_radius", operation.op_id
        )
        end_radius_param = _required_parameter(
            parameters, "end_radius", operation.op_id
        )
        for parameter in (
            axis_start_param,
            axis_end_param,
            start_radius_param,
            end_radius_param,
        ):
            _require_parameter_unit(parameter, source_unit, operation.op_id)
        return _frustum_mesh(
            _parameter_vector(
                axis_start_param.value_json, "axis_start", operation.op_id
            ),
            _parameter_vector(axis_end_param.value_json, "axis_end", operation.op_id),
            _parameter_number(
                start_radius_param.value_json, "start_radius", operation.op_id
            ),
            _parameter_number(
                end_radius_param.value_json, "end_radius", operation.op_id
            ),
            tolerance,
            operation.op_id,
        )

    if operation.kind is GeometryOperationKind.LOFT:
        profiles_param = _required_parameter(parameters, "profiles", operation.op_id)
        _require_parameter_unit(profiles_param, source_unit, operation.op_id)
        profile_size = _parameter_integer(
            _required_parameter(parameters, "profile_size", operation.op_id).value_json,
            "profile_size",
            operation.op_id,
        )
        closed_profile = _parameter_boolean(
            _required_parameter(parameters, "closed_profile", operation.op_id).value_json,
            "closed_profile",
            operation.op_id,
        )
        cap_ends = _parameter_boolean(
            _required_parameter(parameters, "cap_ends", operation.op_id).value_json,
            "cap_ends",
            operation.op_id,
        )
        return _loft_mesh(
            _parameter_points(profiles_param.value_json, "profiles", operation.op_id),
            profile_size,
            closed_profile=closed_profile,
            cap_ends=cap_ends,
            operation_id=operation.op_id,
        )

    if operation.kind is GeometryOperationKind.BOOLEAN_DIFFERENCE:
        if not request.target.allow_approximate_boolean_preview:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_UNSUPPORTED_OPERATION",
                "boolean_difference requires a compiled neutral boundary mesh; "
                "enable only the named non-accepting preview approximation",
            )
        base_index = _parameter_integer(
            _required_parameter(parameters, "base_index", operation.op_id).value_json,
            "base_index",
            operation.op_id,
        )
        inputs = operation.input_object_ids
        if len(inputs) < 2 or base_index < 0 or base_index >= len(inputs):
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_BOOLEAN_PARAMETERS",
                f"{operation.op_id} has an invalid base_index or input arity",
            )
        if any(object_frames.get(item) != operation.frame_id for item in inputs):
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_BOOLEAN_FRAME",
                f"{operation.op_id} preview requires co-framed inputs",
            )
        try:
            base = meshes[inputs[base_index]]
            cutters = tuple(
                meshes[item] for index, item in enumerate(inputs) if index != base_index
            )
        except KeyError as exc:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_OPERATION_ORDER",
                f"{operation.op_id} input was not evaluated before the boolean",
            ) from exc
        return _open_boolean_preview(base, cutters, tolerance, operation.op_id)

    raise PascalBridgeError(
        "PASCAL_TRANSLATION_UNSUPPORTED_OPERATION",
        f"Pascal topology lowering does not support {operation.kind.value}",
    )


def _extrusion_mesh(
    profile: list[tuple[float, float, float]],
    vector: tuple[float, float, float],
    operation_id: str,
) -> _PascalMesh:
    if len(profile) < 3 or _vector_length(vector) <= 1e-12:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_EXTRUSION_PARAMETERS",
            f"{operation_id} requires a polygon and non-zero vector",
        )
    normal = _polygon_normal(profile)
    if abs(_dot(normal, vector)) <= 1e-12:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_EXTRUSION_PARAMETERS",
            f"{operation_id} profile is degenerate or parallel to its vector",
        )
    if _dot(normal, vector) < 0:
        profile.reverse()
    count = len(profile)
    vertices = tuple(profile) + tuple(_add(point, vector) for point in profile)
    faces: list[tuple[int, ...]] = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
    faces.extend(
        (index, (index + 1) % count, (index + 1) % count + count, index + count)
        for index in range(count)
    )
    return _PascalMesh(vertices=vertices, faces=tuple(faces))


def _frustum_mesh(
    axis_start: tuple[float, float, float],
    axis_end: tuple[float, float, float],
    start_radius: float,
    end_radius: float,
    tolerance: float,
    operation_id: str,
) -> _PascalMesh:
    axis = _subtract(axis_end, axis_start)
    axis_length = _vector_length(axis)
    if axis_length <= 1e-12 or start_radius <= 0 or end_radius <= 0:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_REVOLVE_PARAMETERS",
            f"{operation_id} requires a non-zero axis and positive radii",
        )
    direction = _scale(axis, 1.0 / axis_length)
    reference = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    radial_x = _normalize(_cross(direction, reference), operation_id)
    radial_y = _cross(direction, radial_x)
    segments = _circle_segment_count(max(start_radius, end_radius), tolerance)
    start_ring: list[tuple[float, float, float]] = []
    end_ring: list[tuple[float, float, float]] = []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        radial = _add(
            _scale(radial_x, math.cos(angle)),
            _scale(radial_y, math.sin(angle)),
        )
        start_ring.append(_add(axis_start, _scale(radial, start_radius)))
        end_ring.append(_add(axis_end, _scale(radial, end_radius)))
    vertices = tuple(start_ring + end_ring)
    faces: list[tuple[int, ...]] = [
        tuple(reversed(range(segments))),
        tuple(range(segments, 2 * segments)),
    ]
    faces.extend(
        (
            index,
            (index + 1) % segments,
            (index + 1) % segments + segments,
            index + segments,
        )
        for index in range(segments)
    )
    return _PascalMesh(vertices=vertices, faces=tuple(faces))


def _loft_mesh(
    points: tuple[tuple[float, float, float], ...],
    profile_size: int,
    *,
    closed_profile: bool,
    cap_ends: bool,
    operation_id: str,
) -> _PascalMesh:
    if profile_size < 3 or len(points) < profile_size * 2 or len(points) % profile_size:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_LOFT_PARAMETERS",
            f"{operation_id} profiles do not form equal-sized rings",
        )
    if not closed_profile:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_LOFT_PARAMETERS",
            f"{operation_id} open loft profiles are not a Pascal solid",
        )
    rings = [
        list(points[index : index + profile_size])
        for index in range(0, len(points), profile_size)
    ]
    direction = _subtract(_centroid(rings[-1]), _centroid(rings[0]))
    normal = _polygon_normal(rings[0])
    if _vector_length(direction) <= 1e-12 or abs(_dot(normal, direction)) <= 1e-12:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_LOFT_PARAMETERS",
            f"{operation_id} has degenerate or unordered loft rings",
        )
    if _dot(normal, direction) < 0:
        rings = [list(reversed(ring)) for ring in rings]
    vertices = tuple(point for ring in rings for point in ring)
    faces: list[tuple[int, ...]] = []
    if cap_ends:
        faces.append(tuple(reversed(range(profile_size))))
    for ring_index in range(len(rings) - 1):
        lower = ring_index * profile_size
        upper = (ring_index + 1) * profile_size
        faces.extend(
            (
                lower + index,
                lower + (index + 1) % profile_size,
                upper + (index + 1) % profile_size,
                upper + index,
            )
            for index in range(profile_size)
        )
    if cap_ends:
        offset = (len(rings) - 1) * profile_size
        faces.append(tuple(offset + index for index in range(profile_size)))
    return _PascalMesh(vertices=vertices, faces=tuple(faces))


def _open_boolean_preview(
    base: _PascalMesh,
    cutters: tuple[_PascalMesh, ...],
    tolerance: float,
    operation_id: str,
) -> _PascalMesh:
    bounds = tuple(_mesh_bounds(item) for item in cutters)

    def fully_inside_cutter(face: tuple[int, ...]) -> bool:
        return any(
            all(_point_in_bounds(base.vertices[index], bound, tolerance) for index in face)
            for bound in bounds
        )

    retained = tuple(face for face in base.faces if not fully_inside_cutter(face))
    removed = len(base.faces) - len(retained)
    if removed <= 0:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_BOOLEAN_PREVIEW_FAILED",
            f"{operation_id} preview could not identify a cutter-contained face",
        )
    issue = (
        f"{operation_id}: approximate open boolean preview removed {removed} face(s); "
        "exact neutral CSG mesh is required for validation or acceptance"
    )
    return _PascalMesh(vertices=base.vertices, faces=retained, issues=(issue,))


def _mesh_topology(
    positions: list[tuple[float, float, float]],
    faces: tuple[tuple[int, ...], ...],
    operation_id: str,
) -> dict[str, object]:
    if not positions or len(positions) > 20_000 or not faces or len(faces) > 40_000:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_TOPOLOGY_BUDGET",
            f"{operation_id} exceeds the per-block topology budget",
        )
    for point in positions:
        if len(point) != 3 or any(not math.isfinite(value) for value in point):
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_TOPOLOGY_INVALID",
                f"{operation_id} contains a non-finite vertex",
            )
    edge_keys: set[tuple[int, int]] = set()
    for face in faces:
        if len(face) < 3 or len(set(face)) < 3:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_TOPOLOGY_INVALID",
                f"{operation_id} contains a degenerate face",
            )
        if any(index < 0 or index >= len(positions) for index in face):
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_TOPOLOGY_INVALID",
                f"{operation_id} face references an unknown vertex",
            )
        for index, start in enumerate(face):
            end = face[(index + 1) % len(face)]
            if start == end:
                raise PascalBridgeError(
                    "PASCAL_TRANSLATION_TOPOLOGY_INVALID",
                    f"{operation_id} contains a zero-length topological edge",
                )
            edge_keys.add((min(start, end), max(start, end)))
    sorted_edges = sorted(edge_keys)
    return {
        "vertices": [
            {"id": f"v{index}", "position": list(point)}
            for index, point in enumerate(positions)
        ],
        "edges": [
            {"id": f"e{index}", "vertexIds": [f"v{start}", f"v{end}"]}
            for index, (start, end) in enumerate(sorted_edges)
        ],
        "faces": [
            {
                "id": f"f{index}",
                "vertexIds": [f"v{vertex}" for vertex in face],
                "materialSlot": "body",
            }
            for index, face in enumerate(faces)
        ],
    }


def _required_parameter(
    parameters: dict[str, Any],
    name: str,
    operation_id: str,
) -> Any:
    parameter = parameters.get(name)
    if parameter is None:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} requires parameter {name}",
        )
    return parameter


def _require_parameter_unit(parameter: Any, unit: LengthUnit, operation_id: str) -> None:
    if parameter.unit is not None and parameter.unit is not unit:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_MIXED_UNITS",
            f"{operation_id} parameter units must match proposal length_unit",
        )


def _parameter_points(
    value_json: str,
    name: str,
    operation_id: str,
) -> tuple[tuple[float, float, float], ...]:
    value = json.loads(value_json)
    if not isinstance(value, list) or len(value) < 3:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} parameter {name} must contain at least three points",
        )
    return tuple(
        _parameter_vector(json.dumps(point), name, operation_id) for point in value
    )


def _parameter_number(value_json: str, name: str, operation_id: str) -> float:
    value = json.loads(value_json)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} parameter {name} must be numeric",
        )
    result = float(value)
    if not math.isfinite(result):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} parameter {name} must be finite",
        )
    return result


def _parameter_integer(value_json: str, name: str, operation_id: str) -> int:
    value = json.loads(value_json)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} parameter {name} must be an integer",
        )
    return value


def _parameter_boolean(value_json: str, name: str, operation_id: str) -> bool:
    value = json.loads(value_json)
    if not isinstance(value, bool):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_PARAMETERS",
            f"{operation_id} parameter {name} must be boolean",
        )
    return value


def _circle_segment_count(radius: float, tolerance: float) -> int:
    if tolerance <= 0:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_TOLERANCE",
            "geometry tolerance must be positive",
        )
    if tolerance >= radius:
        return 12
    angle = math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
    return max(12, min(2_048, math.ceil(math.pi / angle)))


def _polygon_normal(
    points: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    x = y = z = 0.0
    for index, current in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        x += (current[1] - nxt[1]) * (current[2] + nxt[2])
        y += (current[2] - nxt[2]) * (current[0] + nxt[0])
        z += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return (x, y, z)


def _centroid(
    points: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    return tuple(
        sum(point[axis] for point in points) / len(points) for axis in range(3)
    )  # type: ignore[return-value]


def _mesh_bounds(
    mesh: _PascalMesh,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    return (
        tuple(min(point[axis] for point in mesh.vertices) for axis in range(3)),
        tuple(max(point[axis] for point in mesh.vertices) for axis in range(3)),
    )  # type: ignore[return-value]


def _point_in_bounds(
    point: tuple[float, float, float],
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
    tolerance: float,
) -> bool:
    return all(
        bounds[0][axis] - tolerance <= point[axis] <= bounds[1][axis] + tolerance
        for axis in range(3)
    )


def _add(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _scale(
    vector: tuple[float, float, float],
    factor: float,
) -> tuple[float, float, float]:
    return tuple(value * factor for value in vector)  # type: ignore[return-value]


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _vector_length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _normalize(
    vector: tuple[float, float, float],
    operation_id: str,
) -> tuple[float, float, float]:
    length = _vector_length(vector)
    if length <= 1e-12:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_REVOLVE_PARAMETERS",
            f"{operation_id} cannot construct a radial basis",
        )
    return _scale(vector, 1.0 / length)


def _require_exact_status(
    target: PascalSceneTarget,
    status: dict[str, Any],
) -> None:
    if status.get("id") != target.scene_id:
        raise PascalBridgeError(
            "PASCAL_SCENE_BASE_MISMATCH",
            "Pascal returned another scene identity",
        )
    if status.get("version") != target.expected_scene_version:
        raise PascalBridgeError(
            "PASCAL_SCENE_VERSION_MISMATCH",
            "Pascal scene version no longer matches the prepared target",
        )
    if status.get("graphHash") != target.expected_graph_sha256:
        raise PascalBridgeError(
            "PASCAL_SCENE_HASH_MISMATCH",
            "Pascal graph hash no longer matches the prepared target",
        )
    default_level = status.get("defaultLevelId")
    level_ids = status.get("levelIds")
    if isinstance(level_ids, list) and target.level_id not in level_ids:
        raise PascalBridgeError(
            "PASCAL_SCENE_BASE_MISMATCH",
            f"target level is absent; default is {default_level}",
        )


def _require_after_status(
    target: PascalSceneTarget,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    if before is None or after.get("id") != target.scene_id:
        raise PascalBridgeError(
            "PASCAL_READBACK_IDENTITY_MISMATCH",
            "post-apply status names another scene",
        )
    before_version = before.get("version")
    after_version = after.get("version")
    if (
        not isinstance(before_version, int)
        or not isinstance(after_version, int)
        or after_version <= before_version
    ):
        raise PascalBridgeError(
            "PASCAL_READBACK_VERSION_MISMATCH",
            "post-apply scene version did not advance",
        )
    graph_hash = after.get("graphHash")
    if (
        not isinstance(graph_hash, str)
        or graph_hash == target.expected_graph_sha256
        or len(graph_hash) != 64
        or any(char not in _HEX for char in graph_hash.lower())
    ):
        raise PascalBridgeError(
            "PASCAL_READBACK_HASH_MISMATCH",
            "post-apply graph hash did not bind a new scene",
        )


def _require_apply_ack(plan: PascalPatchPlan, result: dict[str, Any]) -> None:
    if result.get("appliedOps") != len(plan.patches):
        raise PascalBridgeError(
            "PASCAL_APPLY_ACK_MISMATCH",
            "apply_patch did not acknowledge every prepared operation",
        )
    expected_created = sorted(
        str(item["node"]["id"])
        for item in plan.patches
        if item["op"] == "create"
    )
    expected_deleted = sorted(
        str(item["id"]) for item in plan.patches if item["op"] == "delete"
    )
    created = result.get("createdIds")
    deleted = result.get("deletedIds")
    if not isinstance(created, list) or sorted(created) != expected_created:
        raise PascalBridgeError(
            "PASCAL_APPLY_ACK_MISMATCH",
            "apply_patch createdIds drifted from the prepared patch",
        )
    if not isinstance(deleted, list) or sorted(deleted) != expected_deleted:
        raise PascalBridgeError(
            "PASCAL_APPLY_ACK_MISMATCH",
            "apply_patch deletedIds drifted from the prepared patch",
        )


def _require_readback(plan: PascalPatchPlan, scene: dict[str, Any]) -> None:
    nodes = scene.get("nodes")
    if not isinstance(nodes, dict):
        raise PascalBridgeError(
            "PASCAL_READBACK_INVALID",
            "post-apply get_scene returned no node mapping",
        )
    for patch in plan.patches:
        if patch["op"] == "delete":
            if patch["id"] in nodes:
                raise PascalBridgeError(
                    "PASCAL_READBACK_MISMATCH",
                    f"deleted node remains present: {patch['id']}",
                )
            continue
        node = patch["node"] if patch["op"] == "create" else None
        node_id = node["id"] if node is not None else patch["id"]
        actual = nodes.get(node_id)
        if not isinstance(actual, dict):
            raise PascalBridgeError(
                "PASCAL_READBACK_MISMATCH",
                f"prepared node is missing: {node_id}",
            )
        expected_metadata = (
            node["metadata"] if node is not None else patch["data"]["metadata"]
        )
        if actual.get("metadata") != expected_metadata:
            raise PascalBridgeError(
                "PASCAL_READBACK_MISMATCH",
                f"ArchFlow binding metadata drifted: {node_id}",
            )
        expected_fields = node if node is not None else patch["data"]
        for key, value in expected_fields.items():
            if actual.get(key) != value:
                raise PascalBridgeError(
                    "PASCAL_READBACK_MISMATCH",
                    f"prepared field drifted: {node_id}.{key}",
                )


def _require_managed_node_digest(
    node: object,
    object_id: str,
    expected_digest: str,
) -> None:
    if not isinstance(node, dict):
        raise PascalBridgeError(
            "PASCAL_SCENE_BASE_MISMATCH",
            f"expected managed Pascal node is missing for {object_id}",
        )
    metadata = node.get("metadata")
    binding = metadata.get("archflow") if isinstance(metadata, dict) else None
    if (
        not isinstance(binding, dict)
        or binding.get("object_id") != object_id
        or binding.get("object_digest") != expected_digest
    ):
        raise PascalBridgeError(
            "PASCAL_SCENE_BASE_MISMATCH",
            f"managed Pascal node lineage drifted for {object_id}",
        )


def _frame_matrices(
    program: CompiledGeometryProgram,
) -> dict[str, tuple[float, ...]]:
    frames = {item.frame_id: item for item in program.proposal.frames}
    result: dict[str, tuple[float, ...]] = {}
    visiting: set[str] = set()

    def resolve(frame_id: str) -> tuple[float, ...]:
        if frame_id in result:
            return result[frame_id]
        if frame_id in visiting:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_FRAME_CYCLE",
                f"coordinate frame cycle includes {frame_id}",
            )
        frame = frames.get(frame_id)
        if frame is None:
            raise PascalBridgeError(
                "PASCAL_TRANSLATION_FRAME_MISSING",
                f"missing coordinate frame {frame_id}",
            )
        visiting.add(frame_id)
        local = frame.transform_from_parent.matrix
        if frame.parent_frame_id is None:
            resolved = local
        else:
            resolved = _matrix_multiply(resolve(frame.parent_frame_id), local)
        visiting.remove(frame_id)
        result[frame_id] = resolved
        return resolved

    for frame_id in sorted(frames):
        resolve(frame_id)
    return result


def _matrix_multiply(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple(
        sum(left[row * 4 + k] * right[k * 4 + column] for k in range(4))
        for row in range(4)
        for column in range(4)
    )


def _transform_point(
    matrix: tuple[float, ...],
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    value = (*point, 1.0)
    return tuple(
        sum(matrix[row * 4 + column] * value[column] for column in range(4))
        for row in range(3)
    )  # type: ignore[return-value]


def _linear_determinant(matrix: tuple[float, ...]) -> float:
    a, b, c = matrix[0], matrix[1], matrix[2]
    d, e, f = matrix[4], matrix[5], matrix[6]
    g, h, i = matrix[8], matrix[9], matrix[10]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _to_pascal_point(
    point: tuple[float, float, float],
    mapping: PascalAxisMapping,
) -> tuple[float, float, float]:
    if mapping is PascalAxisMapping.Z_UP_RIGHT_HANDED:
        x, y, z = point
        return (x, z, -y)
    if mapping is PascalAxisMapping.Y_UP_RIGHT_HANDED:
        return point
    raise AssertionError(f"unhandled axis mapping: {mapping}")


def _box_vertices(
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    x, y, z = origin
    sx, sy, sz = size
    return (
        (x, y, z),
        (x + sx, y, z),
        (x + sx, y + sy, z),
        (x, y + sy, z),
        (x, y, z + sz),
        (x + sx, y, z + sz),
        (x + sx, y + sy, z + sz),
        (x, y + sy, z + sz),
    )


def _box_edges() -> list[dict[str, object]]:
    pairs = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (6, 7),
        (7, 4),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
    )
    return [
        {"id": f"e{index}", "vertexIds": [f"v{a}", f"v{b}"]}
        for index, (a, b) in enumerate(pairs)
    ]


def _box_faces(*, reverse: bool) -> list[dict[str, object]]:
    loops = (
        ("bottom", (0, 3, 2, 1)),
        ("top", (4, 5, 6, 7)),
        ("front", (0, 1, 5, 4)),
        ("right", (1, 2, 6, 5)),
        ("back", (2, 3, 7, 6)),
        ("left", (3, 0, 4, 7)),
    )
    return [
        {
            "id": f"f-{name}",
            "vertexIds": [
                f"v{index}" for index in (reversed(loop) if reverse else loop)
            ],
            "materialSlot": "body",
        }
        for name, loop in loops
    ]


def _parameter_vector(
    value_json: str,
    name: str,
    operation_id: str,
) -> tuple[float, float, float]:
    value = json.loads(value_json)
    if not isinstance(value, list) or len(value) != 3:
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_SOLID_PARAMETERS",
            f"{operation_id} parameter {name} must be a 3-vector",
        )
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise PascalBridgeError(
            "PASCAL_TRANSLATION_SOLID_PARAMETERS",
            f"{operation_id} parameter {name} must be finite",
        )
    return result  # type: ignore[return-value]


def _unit_to_meters(unit: LengthUnit) -> float:
    return {
        LengthUnit.METER: 1.0,
        LengthUnit.MILLIMETER: 0.001,
        LengthUnit.INCH: 0.0254,
        LengthUnit.FOOT: 0.3048,
    }[unit]


def _pascal_block_id(project_id: str, run_id: str, object_id: str) -> str:
    identity = f"{project_id}\0{run_id}\0{object_id}".encode("utf-8")
    return f"block_archflow_{hashlib.sha256(identity).hexdigest()[:24]}"


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") is True:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            code = structured.get("code", "PASCAL_MCP_TOOL_FAILED")
            message = structured.get("message", structured)
            raise McpClientError(f"{code}: {message}")
        raise McpClientError("Pascal MCP tool returned isError")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _clone_json(structured)
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return _clone_json(decoded)
    raise McpClientError("Pascal MCP tool returned no structured object")


def _target_to_dict(target: PascalSceneTarget) -> dict[str, object]:
    return {
        "scene_id": target.scene_id,
        "level_id": target.level_id,
        "branch_id": target.branch_id,
        "stage": target.stage,
        "base": _base_to_dict(target.base),
        "expected_scene_version": target.expected_scene_version,
        "expected_graph_sha256": target.expected_graph_sha256,
        "axis_mapping": target.axis_mapping.value,
        "allow_approximate_boolean_preview": (
            target.allow_approximate_boolean_preview
        ),
    }


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return digest


def _optional_positive_int(
    payload: dict[str, Any] | None,
    key: str,
) -> int | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_sha256(
    payload: dict[str, Any] | None,
    key: str,
) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    try:
        return _require_sha256(value, key)
    except (TypeError, ValueError):
        return None


def _bounded_messages(
    payload: dict[str, Any] | None,
    key: str,
) -> tuple[str, ...]:
    if payload is None:
        return ()
    values = payload.get(key)
    if not isinstance(values, list):
        return ()
    messages: list[str] = []
    for item in values[:128]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            parts = []
            for name in ("nodeId", "path", "message"):
                value = item.get(name)
                if value is not None and value != "":
                    parts.append(str(value))
            text = ": ".join(parts) if parts else json.dumps(item, sort_keys=True)
        else:
            text = str(item)
        messages.append(text[:500])
    return tuple(messages)


def _clone_json(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Pascal payload must contain finite JSON values") from exc


def _bounded_error(error: Exception, phase: str) -> str:
    text = f"{phase}: {error}"
    return text[:1000]


__all__ = [
    "PascalAxisMapping",
    "PascalBridgeError",
    "PascalExecutionReceipt",
    "PascalExecutionRequest",
    "PascalExecutionStatus",
    "PascalMcpAdapter",
    "PascalMcpConfig",
    "PascalPatchPlan",
    "PascalSceneTarget",
    "compile_pascal_block_patch",
]
