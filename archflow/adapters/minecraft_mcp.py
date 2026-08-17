"""ArchFlow boundary adapter for a structured Minecraft MCP backend."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archflow.adapters.mcp_stdio import McpClientError, StdioMcpClient
from archflow.state import ArtifactRef, CanonicalState
from archflow.workspace import WorkspaceRef


SESSION_TOOL = "minecraft_session"
PREVIEW_TOOL = "minecraft_preview_build_plan"
EXECUTE_TOOL = "minecraft_execute_build_plan"
CAPTURE_TOOL = "minecraft_capture_view"
UNDO_TOOL = "minecraft_undo_last_batch"


class MinecraftMcpFailure(RuntimeError):
    """A named adapter failure with an external receipt in the workspace."""

    def __init__(self, code: str, message: str, receipt_path: Path) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.receipt_path = receipt_path


@dataclass(frozen=True, slots=True)
class MinecraftMcpConfig:
    command: tuple[str, ...]
    timeout_seconds: float = 8.0
    framing: str = "content-length"
    allow_world_write: bool = False
    capture_after_build: bool = True
    allow_compensation: bool = False


@dataclass(frozen=True, slots=True)
class MinecraftExportRequest:
    """Typed downstream translation request bound to an accepted neutral package."""

    request_id: str
    source_artifact: ArtifactRef
    acceptance_receipt_ref: str
    payload_json: str

    SCHEMA = "MinecraftExportRequest@1"
    NEUTRAL_MEDIA_TYPE = "application/vnd.archflow.neutral-building+json"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.request_id, str)
            or not self.request_id.strip()
            or len(self.request_id) > 160
        ):
            raise ValueError("request_id must be bounded non-empty text")
        if not isinstance(self.source_artifact, ArtifactRef):
            raise TypeError("source_artifact must be ArtifactRef")
        if (
            self.source_artifact.media_type != self.NEUTRAL_MEDIA_TYPE
            or not self.source_artifact.uri.startswith("project://")
        ):
            raise ValueError(
                "source_artifact must be a project-owned neutral building package"
            )
        if (
            not isinstance(self.acceptance_receipt_ref, str)
            or not self.acceptance_receipt_ref.startswith("project://")
        ):
            raise ValueError(
                "acceptance_receipt_ref must be a project-owned record"
            )
        if not isinstance(self.payload_json, str):
            raise TypeError("payload_json must be text")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("payload_json must contain JSON") from exc
        if not isinstance(payload, dict) or not payload:
            raise ValueError("translated plan must be a non-empty object")
        if _canonical_json(payload).decode("utf-8") != self.payload_json:
            raise ValueError("translated plan must be canonical JSON")

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        source_artifact: ArtifactRef,
        acceptance_receipt_ref: str,
        translated_plan: Mapping[str, Any],
    ) -> "MinecraftExportRequest":
        if not isinstance(translated_plan, Mapping):
            raise TypeError("translated_plan must be a mapping")
        return cls(
            request_id=request_id,
            source_artifact=source_artifact,
            acceptance_receipt_ref=acceptance_receipt_ref,
            payload_json=_canonical_json(dict(translated_plan)).decode("utf-8"),
        )

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise AssertionError("validated translated plan stopped being an object")
        return value

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "source_artifact": {
                "artifact_id": self.source_artifact.artifact_id,
                "uri": self.source_artifact.uri,
                "media_type": self.source_artifact.media_type,
                "sha256": self.source_artifact.sha256,
            },
            "acceptance_receipt_ref": self.acceptance_receipt_ref,
            "payload_json": self.payload_json,
            "plan_sha256": self.plan_sha256,
            "design_authority": False,
            "validation_authority": False,
            "canonical_write_authority": False,
            "persistence_authority": False,
        }


class MinecraftMcpAdapter:
    """Turns a structured build plan into a workspace-owned evidence artifact.

    The Minecraft world is treated as speculative working state. This adapter
    never receives a canonical store or committer and therefore cannot advance
    ``CanonicalState``.
    """

    capability_id = "minecraft.voxel.build_plan"

    def __init__(self, config: MinecraftMcpConfig) -> None:
        self._config = config

    def preview_export(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        request: MinecraftExportRequest,
    ) -> ArtifactRef:
        """Preview only a typed neutral-package downstream request."""

        if not isinstance(request, MinecraftExportRequest):
            raise TypeError(
                "request must be MinecraftExportRequest; raw MCP payloads are compatibility-only"
            )
        return self.preview(state, workspace, request)

    def build_export(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        request: MinecraftExportRequest,
    ) -> ArtifactRef:
        """Execute only a typed neutral-package downstream request."""

        if not isinstance(request, MinecraftExportRequest):
            raise TypeError(
                "request must be MinecraftExportRequest; raw MCP payloads are compatibility-only"
            )
        return self.build(state, workspace, request)

    def preview(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        plan: dict[str, Any] | MinecraftExportRequest,
    ) -> ArtifactRef:
        """Compatibility surface; formal downstream routes use ``preview_export``."""
        export_request = (
            plan if isinstance(plan, MinecraftExportRequest) else None
        )
        self._check_base(state, workspace)
        frozen_plan = _freeze_json_object(
            plan.payload if export_request is not None else plan,
            "plan",
        )
        phase = "initialize"
        try:
            with self._client() as client:
                server = client.initialize()
                phase = "tools/list"
                tools = client.list_tools()
                self._require_tools(tools, (SESSION_TOOL, PREVIEW_TOOL))
                phase = SESSION_TOOL
                session = _tool_payload(client.call_tool(SESSION_TOOL, {}))
                phase = PREVIEW_TOOL
                preview = _tool_payload(client.call_tool(PREVIEW_TOOL, frozen_plan))
            artifact = self._write_artifact(
                state,
                workspace,
                plan=frozen_plan,
                export_request=export_request,
                server={
                    "name": server.name,
                    "version": server.version,
                    "protocol_version": server.protocol_version,
                },
                session=session,
                preview=preview,
                execution=None,
                capture=None,
            )
            if artifact is None:
                raise AssertionError("preview artifact write returned no artifact")
            return artifact
        except Exception as exc:
            self._fail(state, workspace, phase, exc, world_may_have_changed=False)

    def build(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        plan: dict[str, Any] | MinecraftExportRequest,
    ) -> ArtifactRef:
        """Compatibility surface; formal downstream routes use ``build_export``."""
        export_request = (
            plan if isinstance(plan, MinecraftExportRequest) else None
        )
        self._check_base(state, workspace)
        frozen_plan = _freeze_json_object(
            plan.payload if export_request is not None else plan,
            "plan",
        )
        if not self._config.allow_world_write:
            self._fail(
                state,
                workspace,
                "authorization",
                McpClientError(
                    "world mutation is disabled; set allow_world_write=True explicitly"
                ),
                world_may_have_changed=False,
                code="WORLD_WRITE_DISABLED",
            )

        phase = "initialize"
        execution_started = False
        execution_acknowledged = False
        journal: dict[str, Any] | None = None
        tools: tuple[str, ...] = ()
        session: dict[str, Any] | None = None
        preview: dict[str, Any] | None = None
        execution: dict[str, Any] | None = None
        capture: dict[str, Any] | None = None
        undo_token: str | None = None
        try:
            with self._client() as client:
                server = client.initialize()
                phase = "tools/list"
                tools = client.list_tools()
                required_tools = (
                    SESSION_TOOL,
                    PREVIEW_TOOL,
                    EXECUTE_TOOL,
                    *(
                        (CAPTURE_TOOL,)
                        if self._config.capture_after_build
                        else ()
                    ),
                )
                self._require_tools(tools, required_tools)
                phase = SESSION_TOOL
                session = _tool_payload(client.call_tool(SESSION_TOOL, {}))
                world_identity = _world_identity(session)
                phase = PREVIEW_TOOL
                preview = _tool_payload(client.call_tool(PREVIEW_TOOL, frozen_plan))
                plan_id = preview.get("planId") or preview.get("plan_id")
                if not isinstance(plan_id, str) or not plan_id.strip():
                    raise McpClientError(
                        "preview returned no planId; exact preview/execute parity "
                        "cannot be proven"
                    )
                journal = _new_mutation_journal(
                    state,
                    workspace,
                    frozen_plan,
                    server={
                        "name": server.name,
                        "version": server.version,
                        "protocol_version": server.protocol_version,
                    },
                    session=session,
                    world_identity=world_identity,
                    preview_plan_id=plan_id,
                )
                _append_mutation_phase(
                    self,
                    workspace,
                    journal,
                    phase="prepare",
                    outcome="prepared",
                    evidence={
                        "preview_plan_id": plan_id,
                        "issues": _bounded_value(preview.get("issues")),
                    },
                    status="prepared",
                )
                phase = EXECUTE_TOOL
                execution_started = True
                _append_mutation_phase(
                    self,
                    workspace,
                    journal,
                    phase="execute",
                    outcome="started",
                    evidence={"preview_plan_id": plan_id},
                    status="write_unknown",
                )
                execution = _tool_payload(
                    client.call_tool(EXECUTE_TOOL, {"executePlanId": plan_id})
                )
                execution_acknowledged = True
                _require_execution_plan(execution, plan_id)
                undo_token = _undo_token(execution)
                _append_mutation_phase(
                    self,
                    workspace,
                    journal,
                    phase="execute",
                    outcome="acknowledged",
                    evidence={
                        "execution": _bounded_value(execution),
                        "undo_token_available": undo_token is not None,
                    },
                    status="world_changed_unobserved",
                )
                if self._config.capture_after_build:
                    phase = CAPTURE_TOOL
                    _append_mutation_phase(
                        self,
                        workspace,
                        journal,
                        phase="observe",
                        outcome="started",
                        evidence={},
                        status="world_changed_unobserved",
                    )
                    capture = _tool_payload(client.call_tool(CAPTURE_TOOL, {}))
                    _append_mutation_phase(
                        self,
                        workspace,
                        journal,
                        phase="observe",
                        outcome="captured",
                        evidence={
                            "capture_sha256": _json_digest(capture),
                        },
                        status="world_change_observed",
                    )
                else:
                    _append_mutation_phase(
                        self,
                        workspace,
                        journal,
                        phase="observe",
                        outcome="not_requested",
                        evidence={},
                        status="world_changed_unobserved",
                    )
                phase = "validate"
                _append_mutation_phase(
                    self,
                    workspace,
                    journal,
                    phase="validate",
                    outcome="transport_evidence_bound",
                    evidence={
                        "hard_usability_evaluated": False,
                        "preview_execute_same_plan": True,
                    },
                    status=(
                        "transport_validated"
                        if capture is not None
                        else "world_changed_unobserved"
                    ),
                )
            artifact = self._write_artifact(
                state,
                workspace,
                plan=frozen_plan,
                export_request=export_request,
                server={
                    "name": server.name,
                    "version": server.version,
                    "protocol_version": server.protocol_version,
                },
                session=session,
                preview=preview,
                execution=execution,
                capture=capture,
                mutation_journal=journal,
            )
            if artifact is None:
                raise AssertionError("build artifact write returned no artifact")
            phase = "finalize"
            _append_mutation_phase(
                self,
                workspace,
                journal,
                phase="finalize",
                outcome=(
                    "candidate_evidence_ready"
                    if capture is not None
                    else "manual_observation_required"
                ),
                evidence={"candidate_artifact_id": artifact.artifact_id},
                status=(
                    "candidate_evidence_ready"
                    if capture is not None
                    else "world_changed_unobserved"
                ),
            )
            return artifact
        except Exception as exc:
            if journal is not None:
                status = (
                    "world_changed_unobserved"
                    if execution_acknowledged
                    else (
                        "write_unknown"
                        if execution_started
                        else "unchanged"
                    )
                )
                compensation = None
                if execution_acknowledged:
                    compensation = self._attempt_compensation(
                        workspace=workspace,
                        journal=journal,
                        expected_session=session,
                        undo_token=undo_token,
                        available_tools=tools,
                    )
                    if compensation == "succeeded":
                        status = "compensated"
                    elif compensation == "failed":
                        status = "compensation_failed"
                    else:
                        status = "manual_reconciliation_required"
                _append_mutation_phase(
                    self,
                    workspace,
                    journal,
                    phase="finalize",
                    outcome="failed",
                    evidence={
                        "failed_phase": phase,
                        "error": f"{type(exc).__name__}: {str(exc)[:2000]}",
                        "compensation": compensation,
                    },
                    status=status,
                )
            self._fail(
                state,
                workspace,
                phase,
                exc,
                world_may_have_changed=execution_started,
                mutation_journal=journal,
            )

    def _attempt_compensation(
        self,
        *,
        workspace: WorkspaceRef,
        journal: dict[str, Any],
        expected_session: dict[str, Any] | None,
        undo_token: str | None,
        available_tools: tuple[str, ...],
    ) -> str | None:
        """Try an exact-token undo without claiming atomic rollback."""

        if (
            not self._config.allow_compensation
            or expected_session is None
            or undo_token is None
            or UNDO_TOOL not in available_tools
        ):
            _append_mutation_phase(
                self,
                workspace,
                journal,
                phase="compensate",
                outcome="not_attempted",
                evidence={
                    "authorized": self._config.allow_compensation,
                    "exact_undo_token_available": undo_token is not None,
                    "undo_tool_available": UNDO_TOOL in available_tools,
                },
                status="manual_reconciliation_required",
            )
            return None
        try:
            with self._client() as client:
                client.initialize()
                tools = client.list_tools()
                self._require_tools(tools, (SESSION_TOOL, UNDO_TOOL))
                current_session = _tool_payload(
                    client.call_tool(SESSION_TOOL, {})
                )
                if _world_identity(current_session) != _world_identity(
                    expected_session
                ):
                    raise McpClientError(
                        "world identity changed before compensation"
                    )
                result = _tool_payload(
                    client.call_tool(UNDO_TOOL, {"undoToken": undo_token})
                )
                if not _compensation_acknowledged(result):
                    raise McpClientError(
                        "undo did not acknowledge compensation"
                    )
            _append_mutation_phase(
                self,
                workspace,
                journal,
                phase="compensate",
                outcome="acknowledged",
                evidence={
                    "undo_token_sha256": hashlib.sha256(
                        undo_token.encode("utf-8")
                    ).hexdigest(),
                    "compensation_session_sha256": _json_digest(
                        current_session
                    ),
                    "result": _bounded_value(result),
                    "atomic_rollback_claimed": False,
                },
                status="compensated",
            )
            return "succeeded"
        except Exception as exc:
            _append_mutation_phase(
                self,
                workspace,
                journal,
                phase="compensate",
                outcome="failed",
                evidence={
                    "error": f"{type(exc).__name__}: {str(exc)[:2000]}",
                    "atomic_rollback_claimed": False,
                },
                status="compensation_failed",
            )
            return "failed"

    def _client(self) -> StdioMcpClient:
        return StdioMcpClient(
            self._config.command,
            timeout_seconds=self._config.timeout_seconds,
            framing=self._config.framing,
        )

    @staticmethod
    def _check_base(state: CanonicalState, workspace: WorkspaceRef) -> None:
        if workspace.base != state.ref:
            raise ValueError("workspace was forked from another canonical state")

    @staticmethod
    def _require_tools(
        available: tuple[str, ...],
        required: tuple[str, ...],
    ) -> None:
        missing = sorted(set(required) - set(available))
        if missing:
            raise McpClientError(f"required MCP tools are missing: {missing}")

    def _write_artifact(
        self,
        state: CanonicalState | None,
        workspace: WorkspaceRef,
        *,
        plan: dict[str, Any] | None = None,
        export_request: MinecraftExportRequest | None = None,
        server: dict[str, str] | None = None,
        session: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        capture: dict[str, Any] | None = None,
        mutation_journal: dict[str, Any] | None = None,
        workspace_record: tuple[str, dict[str, Any]] | None = None,
    ) -> ArtifactRef | None:
        if workspace_record is not None:
            filename, record = workspace_record
            path = _workspace_path(workspace, filename)
            path.write_bytes(_pretty_json(record))
            return None
        if (
            state is None
            or plan is None
            or server is None
            or session is None
            or preview is None
        ):
            raise TypeError(
                "candidate artifact requires state plan server session preview"
            )
        screenshot = _extract_png(capture, workspace.root) if capture else None
        plan_encoded = _canonical_json(plan)
        mutation_ref = (
            _mutation_journal_ref(workspace, mutation_journal)
            if mutation_journal is not None
            else None
        )
        payload = {
            "schema": "MinecraftVoxelArtifact@1",
            "base_state": {
                "run_id": state.ref.run_id,
                "version": state.ref.version,
            },
            "exact_base_state": {
                "project_id": state.ref.project_id,
                "version": state.ref.version,
                "state_sha256": state.ref.state_sha256,
            },
            "workspace_id": workspace.workspace_id,
            "prompt": state.goal.prompt if state.goal is not None else None,
            "plan_sha256": hashlib.sha256(plan_encoded).hexdigest(),
            "build_plan": plan,
            "export_request": (
                None if export_request is None else export_request.to_dict()
            ),
            "mcp_server": server,
            "session": _bounded_value(session),
            "preview": _bounded_value(preview),
            "execution": _bounded_value(execution),
            "capture": _bounded_value(capture),
            "screenshot_uri": screenshot.as_uri() if screenshot else None,
            "voxel_summary": {
                "loadable": True,
                "previewed": True,
                "executed": execution is not None,
                "exact_preview_plan_id": preview.get("planId")
                or preview.get("plan_id"),
            },
            "world_mutation_receipt": mutation_ref,
            "architectural_usability_proven": False,
            "canonical_write_authority": False,
        }
        path = _workspace_path(workspace, "minecraft-voxel-artifact.json")
        encoded = _pretty_json(payload)
        path.write_bytes(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        return ArtifactRef(
            artifact_id=f"artifact-{digest[:20]}",
            uri=path.as_uri(),
            media_type="application/vnd.archflow.minecraft-voxel+json",
            sha256=digest,
        )

    def _fail(
        self,
        state: CanonicalState,
        workspace: WorkspaceRef,
        phase: str,
        error: Exception,
        *,
        world_may_have_changed: bool,
        code: str | None = None,
        mutation_journal: dict[str, Any] | None = None,
    ) -> None:
        failure_code = code or _error_code(error)
        message = str(error)[:2000]
        payload = {
            "schema": "MinecraftMcpFailureReceipt@1",
            "code": failure_code,
            "message": message,
            "phase": phase,
            "base_state": {
                "run_id": state.ref.run_id,
                "version": state.ref.version,
            },
            "exact_base_state": {
                "project_id": state.ref.project_id,
                "version": state.ref.version,
                "state_sha256": state.ref.state_sha256,
            },
            "workspace_id": workspace.workspace_id,
            "canonical_state_mutated": False,
            "world_may_have_changed": world_may_have_changed,
            "world_mutation_status": (
                mutation_journal.get("status")
                if mutation_journal is not None
                else ("write_unknown" if world_may_have_changed else "unchanged")
            ),
            "mutation_receipt": (
                _mutation_journal_ref(workspace, mutation_journal)
                if mutation_journal is not None
                else None
            ),
        }
        path = _workspace_path(workspace, "minecraft-mcp-failure.json")
        path.write_bytes(_pretty_json(payload))
        raise MinecraftMcpFailure(failure_code, message, path) from error


def _world_identity(session: dict[str, Any]) -> dict[str, str | None]:
    world_id = session.get("worldId") or session.get("world_id")
    if not isinstance(world_id, str) or not world_id.strip():
        raise McpClientError(
            "session returned no worldId; pre-write world identity is unknown"
        )
    dimension = session.get("dimensionId") or session.get("dimension_id")
    if dimension is not None and (
        not isinstance(dimension, str) or not dimension.strip()
    ):
        raise McpClientError("session dimension identity is malformed")
    return {
        "world_id": world_id,
        "dimension_id": dimension,
    }


def _require_execution_plan(
    execution: dict[str, Any],
    preview_plan_id: str,
) -> None:
    executed_plan_id = execution.get("planId") or execution.get("plan_id")
    if executed_plan_id != preview_plan_id:
        raise McpClientError(
            "execution acknowledgement does not match the previewed plan"
        )


def _undo_token(execution: dict[str, Any]) -> str | None:
    token = (
        execution.get("undoToken")
        or execution.get("undo_token")
        or execution.get("batchId")
        or execution.get("batch_id")
    )
    return token if isinstance(token, str) and token.strip() else None


def _compensation_acknowledged(result: dict[str, Any]) -> bool:
    return any(
        result.get(key) is True
        for key in ("undone", "compensated", "restored", "success")
    )


def _json_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _new_mutation_journal(
    state: CanonicalState,
    workspace: WorkspaceRef,
    plan: dict[str, Any],
    *,
    server: dict[str, str],
    session: dict[str, Any],
    world_identity: dict[str, str | None],
    preview_plan_id: str,
) -> dict[str, Any]:
    plan_sha256 = _json_digest(plan)
    seed = {
        "project_id": state.ref.project_id,
        "version": state.ref.version,
        "state_sha256": state.ref.state_sha256,
        "workspace_id": workspace.workspace_id,
        "plan_sha256": plan_sha256,
        "world_identity": world_identity,
        "preview_plan_id": preview_plan_id,
    }
    return {
        "schema": "MinecraftMutationReceipt@1",
        "mutation_id": f"mutation-{_json_digest(seed)[:20]}",
        "base_state": {
            "project_id": state.ref.project_id,
            "version": state.ref.version,
            "state_sha256": state.ref.state_sha256,
        },
        "workspace_id": workspace.workspace_id,
        "plan_sha256": plan_sha256,
        "server": server,
        "world_identity": world_identity,
        "session_sha256": _json_digest(session),
        "preview_plan_id": preview_plan_id,
        "status": "prepared",
        "phases": [],
        "hard_usability_evaluated": False,
        "candidate_accepted": False,
        "canonical_state_mutated": False,
        "cross_system_atomicity_claimed": False,
    }


def _append_mutation_phase(
    adapter: MinecraftMcpAdapter,
    workspace: WorkspaceRef,
    journal: dict[str, Any],
    *,
    phase: str,
    outcome: str,
    evidence: dict[str, Any],
    status: str,
) -> None:
    phases = journal.get("phases")
    if not isinstance(phases, list):
        raise ValueError("mutation journal phases are invalid")
    previous = (
        phases[-1]["phase_sha256"]
        if phases
        else None
    )
    body = {
        "sequence": len(phases),
        "phase": phase,
        "outcome": outcome,
        "plan_sha256": journal["plan_sha256"],
        "world_identity": journal["world_identity"],
        "session_sha256": journal["session_sha256"],
        "prior_phase_sha256": previous,
        "evidence": _bounded_value(evidence),
    }
    receipt = {
        **body,
        "phase_sha256": _json_digest(body),
    }
    phases.append(receipt)
    journal["status"] = status
    journal["phase_head_sha256"] = receipt["phase_sha256"]
    adapter._write_artifact(
        None,
        workspace,
        workspace_record=("minecraft-mutation-receipt.json", journal),
    )


def _mutation_journal_ref(
    workspace: WorkspaceRef,
    journal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mutation_id": journal["mutation_id"],
        "uri": _workspace_path(
            workspace,
            "minecraft-mutation-receipt.json",
        ).as_uri(),
        "phase_head_sha256": journal.get("phase_head_sha256"),
        "status": journal["status"],
    }


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") is True:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            code = structured.get("code", "MCP_TOOL_FAILED")
            message = structured.get("message", structured)
            raise McpClientError(f"{code}: {message}")
        raise McpClientError(f"MCP tool failed: {_bounded_value(result)}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
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
                return decoded
    raise McpClientError("MCP tool returned no structured object")


def _freeze_json_object(value: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty JSON object")
    try:
        frozen = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only finite JSON values") from exc
    if not isinstance(frozen, dict):
        raise ValueError(f"{name} must be a JSON object")
    return frozen


def _workspace_path(workspace: WorkspaceRef, filename: str) -> Path:
    root = workspace.root.resolve()
    path = (root / filename).resolve()
    path.relative_to(root)
    return path


def _extract_png(payload: dict[str, Any], workspace_root: Path) -> Path | None:
    candidates: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                data = item.get("data")
                if isinstance(data, str):
                    candidates.append(data)
    for key in ("imageBase64", "pngBase64", "base64"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for encoded in candidates:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            continue
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            continue
        path = (workspace_root.resolve() / "minecraft-view.png").resolve()
        path.relative_to(workspace_root.resolve())
        path.write_bytes(raw)
        return path
    return None


def _bounded_value(value: Any, *, max_string: int = 4000) -> Any:
    if isinstance(value, str):
        if len(value) <= max_string:
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"<omitted length={len(value)} sha256={digest}>"
    if isinstance(value, list):
        return [_bounded_value(item, max_string=max_string) for item in value[:200]]
    if isinstance(value, dict):
        return {
            str(key)[:200]: _bounded_value(item, max_string=max_string)
            for key, item in list(value.items())[:200]
        }
    return value


def _error_code(error: Exception) -> str:
    message = str(error)
    if "BRIDGE_UNAVAILABLE" in message:
        return "BRIDGE_UNAVAILABLE"
    if "missing" in message.lower() and "tool" in message.lower():
        return "MCP_CAPABILITY_MISSING"
    if "timed out" in message.lower():
        return "MCP_TIMEOUT"
    return "MCP_ADAPTER_FAILED"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
