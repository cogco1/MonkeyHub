"""The in-process OCCT executor (P107): realize a bound program, write STEP and a mesh preview, cold-read both.

A second executor of the same CompiledGeometryProgram as the Rhino export.
It shares the neutral P036 CadProgramBinding (the historical Rhino name
remains a compatibility alias), the analytic predictor (expected_object_bounds), the
semantic denominator (expected_object_semantics) and the workspace rules;
it does not share the Rhino plan, its completion token, host witness or
cleanup receipt, because no host process exists.  Evidence tier is
``self_measured_cold_read``: the STEP file is re-read from disk by a fresh
reader and measured; the Rhino gate remains the independent instrument.
"""

from __future__ import annotations

import hashlib
import math
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    AssemblyRole,
    CompiledGeometryProgram,
    GeometryBoundsError,
    delivered_object_ids,
    expected_object_bounds,
    lift_to_base_level,
    operation_parameters,
    require_sha256,
)
from monkeycad.backends.occt.boolean import DIFFERENCE_STRATEGIES
from monkeycad.backends.occt.build import (
    CLOSED_SOLID,
    CURVE,
    OPEN_SURFACE,
    _polyline_geometry,
    build_program_shapes,
    declared_delivery,
)
from monkeycad.backends.occt.errors import OcctBackendError, OcctCapabilityError, OcctUnavailableError
from monkeycad.backends.occt.kernel import _observe_operation as _observe_occt_operation, backend_identity
from monkeycad.backends.occt.measure import measure_shape
from monkeycad.backends.occt.preview import PreviewMaterial, PreviewObject, write_preview_three_dm
from monkeycad.backends.occt.step import StepObject, read_step, write_step
from monkeycad.execution import (
    CadCapabilityError,
    CadExecutionError,
    CadExecutionStatus,
    RhinoCadProgramBinding,
    _UNIT_TO_RHINO,
    _aggregate_bounds,
    _artifact_stem,
    _bbox_close,
    _bounds_to_rhino,
    _failure,
    _json_copy,
    _portable_relative_path,
    _positive_finite,
    _provenance,
    _sha256_bytes,
    _strict_child,
    _strict_workspace,
    long_path,
)
from monkeycad.formats.three_dm_inspector import ThreeDmInspection, ThreeDmInspectionError, inspect_three_dm
from monkeycad.program import CadTranslationError, _resolved_layer_colors, expected_object_semantics


OCCT_ADAPTER_ID = "occt-in-process"
OCCT_EVIDENCE_TIER = "self_measured_cold_read"
_STEP_FORMAT = "STEP AP214 (ISO 10303-21)"
_PREVIEW_FORMAT = "3dm render-mesh preview"
_PREVIEW_ANGULAR_DEFLECTION = 0.5

# Preview materials for an assembly's members, by role (P107 lane C).  A
# FRAME member wears the material its component declares (name and display
# colour from the caller's assignment), or, with no declaration, an opaque
# material named after the role in the layer colour darkened by this factor
# so the frame reads against its wall.  GLAZING has no declared vocabulary
# yet: it always takes the documented glass fallback, a light tint with
# non-zero openNURBS transparency.  Objects outside those roles keep the
# layer's display colour, as before.
_FRAME_FALLBACK_SHADE = 0.55
_GLAZING_FALLBACK = PreviewMaterial(name=AssemblyRole.GLAZING.value, diffuse=(150, 200, 225), transparency=0.6)
# openNURBS stores transparency as a double; the readback compares the
# inspector's native value with the declared one within this tolerance.
_PREVIEW_TRANSPARENCY_TOLERANCE = 1.0e-6


def _preview_materials(
    program: CompiledGeometryProgram,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    layer_colors: Mapping[str, tuple[int, int, int]],
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> dict[str, PreviewMaterial]:
    """The declared native material each delivered object wears, keyed by object id.

    Every declared component material is carried into the preview. Roles
    come from ``program.proposal.assemblies`` alone, never from an object's
    name: GLAZING keeps its glass fallback and an undeclared FRAME is shaded.
    """

    objects = semantics["objects"]
    materials: dict[str, PreviewMaterial] = {}
    for object_id in physical:
        row = objects[object_id]
        declared = row["user_text"].get("archflow:material")
        if declared:
            layer_color = layer_colors.get(row["layer"], (0, 0, 0))
            color = (material_colors or {}).get(declared, layer_color)
            materials[object_id] = PreviewMaterial(name=declared, diffuse=tuple(int(c) for c in color))
    for assembly in program.proposal.assemblies:
        for object_id in assembly.objects_for(AssemblyRole.GLAZING):
            if object_id in physical:
                materials[object_id] = _GLAZING_FALLBACK
        for object_id in assembly.objects_for(AssemblyRole.FRAME):
            if object_id not in physical or object_id in materials:
                continue
            row = objects[object_id]
            layer_color = layer_colors.get(row["layer"], (0, 0, 0))
            shaded = tuple(int(round(channel * _FRAME_FALLBACK_SHADE)) for channel in layer_color)
            materials[object_id] = PreviewMaterial(name=AssemblyRole.FRAME.value, diffuse=shaded)
    return materials


@dataclass(frozen=True, slots=True)
class OcctCadExportIdentity:
    """Execution identity of one in-process export: the binding, the unit, the frame."""

    binding: RhinoCadProgramBinding
    length_unit: str
    up_axis: str = "Z-up"

    SCHEMA = "OcctCadExportIdentity@1"
    COORDINATE_FRAME = (
        "program (x, y-up, z-plan) written as CAD (x, z, y): the one Z-up "
        "conversion the Rhino translation applies, applied once here"
    )

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RhinoCadProgramBinding):
            raise TypeError("binding must be RhinoCadProgramBinding")
        if self.length_unit not in _UNIT_TO_RHINO:
            raise CadExecutionError(
                "length_unit must be millimeter, meter, inch, or foot"
            )
        if self.up_axis != "Z-up":
            raise CadExecutionError("OCCT CAD externalization requires Z-up")

    @property
    def project_id(self) -> str:
        return self.binding.project_id

    @property
    def run_id(self) -> str:
        return self.binding.run_id

    @property
    def program_digest(self) -> str:
        return self.binding.program_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding": self.binding.to_dict(),
            "length_unit": self.length_unit,
            "up_axis": self.up_axis,
            "coordinate_frame": self.COORDINATE_FRAME,
        }


@dataclass(frozen=True, slots=True)
class OcctExecutionReceipt:
    """What crossed the file boundary and what the cold read found there.

    ``exact_artifact`` names the STEP file (exact B-rep, object names and
    layers); ``preview_artifact`` names the render-mesh/curve ``.3dm`` from
    the same model with viewer semantics. ``readback``
    is the per-object measurement of the STEP file re-read from disk.
    """

    status: CadExecutionStatus
    identity: OcctCadExportIdentity
    adapter_id: str
    backend: dict[str, object]
    evidence_tier: str
    exact_artifact: dict[str, object] | None
    preview_artifact: dict[str, object] | None
    physical_object_ids: tuple[str, ...]
    expected_semantics: dict[str, object]
    expected_bounds: dict[str, dict[str, object]]
    readback: dict[str, dict[str, object]] | None
    preview_inspection: dict[str, object] | None
    readback_tolerance: float
    timings: dict[str, float]
    failures: tuple[dict[str, str], ...]
    reused_object_ids: tuple[str, ...] = ()
    lowering: tuple[tuple[str, str], ...] = ()

    SCHEMA = "OcctExecutionReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.status, CadExecutionStatus):
            raise TypeError("status must be CadExecutionStatus")
        if not isinstance(self.identity, OcctCadExportIdentity):
            raise TypeError("identity must be OcctCadExportIdentity")
        require_identifier(self.adapter_id, "adapter_id")
        if not isinstance(self.backend, dict):
            raise TypeError("backend must be dict")
        if not isinstance(self.reused_object_ids, tuple) or set(self.reused_object_ids) - set(self.physical_object_ids):
            raise CadExecutionError("reused objects must belong to the exported physical denominator")
        if not isinstance(self.lowering, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 or item[1] != "profile_with_holes"
            for item in self.lowering
        ):
            raise CadExecutionError("lowering names only operations realized as a profile with holes")
        lowering_ids = tuple(item[0] for item in self.lowering)
        if lowering_ids != tuple(sorted(set(lowering_ids))):
            raise CadExecutionError("lowering op ids must be sorted and unique")
        for op_id in lowering_ids:
            require_identifier(op_id, "lowering")
        if not isinstance(self.evidence_tier, str) or not self.evidence_tier:
            raise CadExecutionError("evidence_tier must be non-empty text")
        for field in ("exact_artifact", "preview_artifact"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, dict):
                    raise TypeError(f"{field} must be dict or None")
                _portable_relative_path(str(value.get("relative_path")))
                require_sha256(str(value.get("sha256")), f"{field} sha256")
        if self.physical_object_ids != tuple(sorted(set(self.physical_object_ids))):
            raise CadExecutionError("physical_object_ids must be sorted and unique")
        for value in self.physical_object_ids:
            require_identifier(value, "physical_object_ids")
        if not isinstance(self.expected_semantics, dict) or set(
            self.expected_semantics
        ) != {"objects", "blocks"}:
            raise CadExecutionError("expected_semantics schema drifted")
        if not isinstance(self.expected_bounds, dict) or set(
            self.expected_bounds
        ) != set(self.physical_object_ids):
            raise CadExecutionError("bounds and physical denominators differ")
        if self.readback is not None and not isinstance(self.readback, dict):
            raise TypeError("readback must be dict or None")
        if self.preview_inspection is not None and not isinstance(
            self.preview_inspection, dict
        ):
            raise TypeError("preview_inspection must be dict or None")
        object.__setattr__(
            self,
            "readback_tolerance",
            _positive_finite(self.readback_tolerance, "readback_tolerance"),
        )
        if not isinstance(self.timings, dict) or any(
            not isinstance(value, float) for value in self.timings.values()
        ):
            raise TypeError("timings must be dict[str, float]")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(item, dict) for item in self.failures
        ):
            raise TypeError("failures must be tuple[dict, ...]")
        succeeded = self.status is CadExecutionStatus.SUCCEEDED
        if succeeded != (
            not self.failures
            and self.exact_artifact is not None
            and self.readback is not None
            and set(self.readback) == set(self.physical_object_ids)
        ):
            raise CadExecutionError(
                "successful execution requires a written STEP file, a complete cold readback and no failures"
            )

    @property
    def readback_verified(self) -> bool:
        return self.status is CadExecutionStatus.SUCCEEDED

    def to_dict(self) -> dict[str, object]:
        return _json_copy(
            {
                "schema": self.SCHEMA,
                "status": self.status.value,
                "identity": self.identity.to_dict(),
                "adapter_id": self.adapter_id,
                "backend": self.backend,
                "evidence_tier": self.evidence_tier,
                "exact_artifact": self.exact_artifact,
                "preview_artifact": self.preview_artifact,
                "physical_object_ids": list(self.physical_object_ids),
                "expected_semantics": self.expected_semantics,
                "expected_bounds": self.expected_bounds,
                "readback": self.readback,
                "preview_inspection": self.preview_inspection,
                "readback_tolerance": self.readback_tolerance,
                "timings": self.timings,
                "failures": list(self.failures),
                "readback_verified": self.readback_verified,
                **({"reused_object_ids": list(self.reused_object_ids)} if self.reused_object_ids else {}),
                **({"lowering": dict(self.lowering)} if self.lowering else {}),
            }
        )


@contextmanager
def _occt_step(observer, phase: str, *, parent_event_id, timings, timing_key: str, details):
    """Publish the same measured interval retained by the execution receipt."""

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    outcome = {"status": "succeeded", "details": details}
    try:
        yield outcome
    except BaseException:
        outcome["status"] = "failed"
        raise
    finally:
        elapsed = time.perf_counter() - started
        timings[timing_key] = elapsed
        if outcome["status"] != "succeeded":
            for field in ("emitted_object_ids", "output_refs"):
                if field in details:
                    details[field] = []
        _observe_occt_operation(observer, phase=phase, status=outcome["status"],
            started_at=started_at, ended_at=datetime.now(timezone.utc), duration_ms=round(elapsed * 1000),
            parent_event_id=parent_event_id, details=deepcopy(details))


def execute_occt_export(
    program: CompiledGeometryProgram,
    *,
    binding: RhinoCadProgramBinding,
    speculative_workspace: Path,
    artifact_stem: str,
    readback_tolerance: float = 0.003,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    preview: bool = True,
    prior_program: CompiledGeometryProgram | None = None,
    prior_step: Path | None = None,
    prior_step_sha256: str | None = None,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    observation_parent_id: str | None = None,
    difference_strategy: str = "auto",
) -> OcctExecutionReceipt:
    """Realize the bound program, write STEP and a mesh/curve preview, cold-read both.

    Writes ``<artifact_stem>.step`` (exact B-rep, one named shape per
    physical object) and ``<artifact_stem>.preview.3dm`` (render meshes and native curves of
    the same model with the viewer's names, layers, colours and
    ``archflow:*`` user text) into the caller-supplied workspace; neither may
    exist beforehand.  The STEP file is then re-read by a fresh reader and
    every physical object is checked against what its producer operation
    declared: present exactly once under its id, valid, bounds within
    ``readback_tolerance`` of the analytic predictor, on its semantic layer,
    and either exactly the expected number of closed solids (every
    solid operation) or an open surface - no solid, an
    actual open boundary, no volume claimed) or a polyline with its original
    vertices, endpoints and length. The preview is read back
    through ``inspect_three_dm`` and checked against the same denominator.
    No process is started.

    An explicitly supplied prior program and verified STEP may contribute
    unchanged shapes. Only changed geometry is built; the complete current
    model is written and independently read back under the current binding.

    ``difference_strategy`` is handed to ``build_program_shapes``; a
    difference realized as a profile with holes is named in the receipt's
    ``lowering``.

    Raises ``CadCapabilityError`` (a ``CadExecutionError``) before writing
    when the program uses an operation this executor does not realize, and
    ``CadExecutionError`` when the binding, workspace or backend is unusable.
    Build, write and readback failures come back as a FAILED receipt.

    Integration recipe (runtime.project_runner._export, once released):

        program_ref = repository.put_json(run=run, destination=branch_destination,
                                          record_kind=stage_geometry_program(stage_id),
                                          payload=program.to_dict())          # P036 first, as today
        binding = CadProgramBinding(program_ref=program_ref, branch=branch, stage_id=stage_id,
                                    program_digest=program.program_digest,
                                    design_state_digest=program.proposal.design_state_digest,
                                    predecessor_program_digest=None)
        receipt = execute_occt_export(program, binding=binding, speculative_workspace=workspace,
                                      artifact_stem=f"{stage_id}@{program.program_digest[:12]}",
                                      readback_tolerance=0.003, provenance={**provenance, "export_path": "occt"})
        repository.put_json(run=run, destination=destination, record_kind=<new occt execution kind>,
                            payload=receipt.to_dict())
        # receipt.preview_artifact["relative_path"] is what the viewer loads;
        # receipt.exact_artifact["relative_path"] is the STEP delivery.
        # The Rhino path stays opt-in behind its own flag and is never
        # launched by create/save/reopen/preview.
    """

    if difference_strategy not in DIFFERENCE_STRATEGIES:
        raise CadExecutionError(f"unknown difference strategy {difference_strategy!r}")
    if not isinstance(binding, RhinoCadProgramBinding):
        raise TypeError("binding must be RhinoCadProgramBinding")
    binding.bind_program(program)
    unit = program.proposal.length_unit.value
    identity = OcctCadExportIdentity(binding=binding, length_unit=unit)
    if operation_observer is not None:
        caller_observer = operation_observer

        def observe_program_operation(event):
            caller_observer({**event, "source_ref": event.get("source_ref") or binding.program_ref.uri})

        operation_observer = observe_program_operation
    tolerance = _positive_finite(readback_tolerance, "readback_tolerance")
    workspace = _strict_workspace(speculative_workspace)
    stem = _artifact_stem(artifact_stem)
    step_path = workspace / f"{stem}.step"
    preview_path = workspace / f"{stem}.preview.3dm"
    for target in (step_path, preview_path):
        _strict_child(workspace, target, require_exists=False)
        if target.exists() or target.is_symlink():
            raise CadExecutionError(f"speculative output already exists: {target.name}")
    try:
        semantics = _json_copy(
            expected_object_semantics(
                program,
                material_by_component=material_by_component,
                layer_by_component=layer_by_component,
            )
        )
        raw_bounds = expected_object_bounds(program)
    except (CadTranslationError, GeometryBoundsError) as exc:
        raise CadExecutionError(f"program denominator is not analytically determined: {exc}") from exc
    physical = tuple(sorted(semantics["objects"]))
    if set(raw_bounds) != set(physical):
        raise CadExecutionError("analytic bounds and physical denominators differ")
    bounds = {object_id: _bounds_to_rhino(raw_bounds[object_id]) for object_id in physical}
    counts = {object_id: int(raw_bounds[object_id]["brep_count"]) for object_id in physical}
    deliveries = _declared_deliveries(program, physical)
    curves = {
        operation.output_object_ids[0]: [[x, z, y] for x, y, z in lift_to_base_level(
            operation_parameters(operation)["points"], operation_parameters(operation), operation.op_id)]
        for operation in program.proposal.operations
        if operation.kind.value == "curve" and operation.output_object_ids[0] in physical
    }
    layer_colors = dict(
        _resolved_layer_colors(
            {row["layer"] for row in semantics["objects"].values()},
            material_by_component=material_by_component,
            material_colors=material_colors,
        )
    )
    supplied = _provenance(identity, provenance, export_schema=OcctExecutionReceipt.SCHEMA)
    document_user_text = {f"archflow:{key}": value for key, value in supplied.items()}
    timings: dict[str, float] = {}
    started = time.perf_counter()

    def failure_receipt(code: str, detail: str, **artifacts) -> OcctExecutionReceipt:
        timings["total_seconds"] = time.perf_counter() - started
        return OcctExecutionReceipt(
            status=CadExecutionStatus.FAILED,
            identity=identity,
            adapter_id=OCCT_ADAPTER_ID,
            backend=backend,
            evidence_tier=OCCT_EVIDENCE_TIER,
            exact_artifact=artifacts.get("exact_artifact"),
            preview_artifact=artifacts.get("preview_artifact"),
            physical_object_ids=physical,
            expected_semantics=semantics,
            expected_bounds=bounds,
            readback=artifacts.get("readback"),
            preview_inspection=artifacts.get("preview_inspection"),
            readback_tolerance=tolerance,
            timings=dict(timings),
            failures=(_failure(code, detail),),
        )

    try:
        with _occt_step(operation_observer, "occt_initialization", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="initialization_seconds",
                        details={"execution_path": "occt", "scope": "kernel_initialization"}):
            backend = backend_identity()
    except OcctUnavailableError as exc:
        raise CadExecutionError(str(exc)) from exc
    try:
        reuse_details = {"input_identity": {"program_digest": program.program_digest,
                         **({"source_program_digest": prior_program.program_digest} if prior_program is not None else {}),
                         **({"source_step_sha256": prior_step_sha256} if prior_step_sha256 is not None else {})},
                         "execution_path": "occt", "scope": "verified_source_shapes"}
        with _occt_step(operation_observer, "occt_reuse_check", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="reuse_check_seconds", details=reuse_details):
            reused_shapes = _reusable_occt_shapes(program, prior_program, prior_step, prior_step_sha256,
                                                  diagnostics=reuse_details)
        build = build_program_shapes(program, **({"reusable_shapes": reused_shapes} if reused_shapes else {}),
                                     operation_observer=operation_observer, observation_parent_id=observation_parent_id,
                                     difference_strategy=difference_strategy)
    except OcctCapabilityError as exc:
        raise CadCapabilityError(
            f"OCCT executor cannot realize {exc.op_id} ({exc.kind}): {exc.reason}",
            op_id=exc.op_id,
            kind=exc.kind,
        ) from exc
    except OcctBackendError as exc:
        return failure_receipt("cad_execution.occt_build_failed", str(exc))
    timings["build_seconds"] = build.elapsed_seconds
    if set(build.physical_object_ids) != set(physical):
        return failure_receipt(
            "cad_execution.physical_identity_mismatch",
            "built physical objects differ from the semantic denominator",
        )

    step_objects = tuple(
        StepObject(
            object_id=object_id,
            shape=build.objects[object_id].shape,
            layer=semantics["objects"][object_id]["layer"],
            color=layer_colors.get(semantics["objects"][object_id]["layer"]),
            # A hidden inspection witness is invisible in the exact STEP as in the preview.
            visible=not build.objects[object_id].hidden,
        )
        for object_id in physical
    )
    try:
        with _occt_step(operation_observer, "step_write", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="step_write_seconds",
                        details={"input_identity": {"program_digest": program.program_digest},
                                 "input_object_ids": list(physical), "emitted_object_ids": list(physical),
                                 "execution_path": "occt", "scope": "step_write_and_hash",
                                 "executed_stages": ["write_step"], "output_refs": [step_path.name]}):
            write_step(step_path, step_objects, length_unit=unit)
            exact_artifact = _exact_artifact(step_path, workspace, deliveries)
    except (OcctBackendError, OSError) as exc:
        return failure_receipt("cad_execution.step_write_failed", str(exc))

    read_error = None
    with _occt_step(operation_observer, "step_readback", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="step_read_seconds",
                        details={"input_identity": {"step_sha256": exact_artifact["sha256"]},
                                 "input_object_ids": list(physical), "execution_path": "occt",
                                 "scope": "cold_read_and_verification", "executed_stages": ["read_step", "verify_step"],
                                 "comparison_refs": [step_path.name]}) as read_span:
        try:
            entries = read_step(step_path, length_unit=unit)
        except (OcctBackendError, OSError) as exc:
            read_error = exc
            read_span["status"] = "failed"
        else:
            readback, failures = _verify_step_readback(
                entries, physical=physical, semantics=semantics, expected_bounds=bounds,
                expected_counts=counts, expected_deliveries=deliveries, expected_curves=curves,
                layer_colors=layer_colors, tolerance=tolerance,
            )
            if failures:
                read_span["status"] = "failed"
    if read_error is not None:
        return failure_receipt(
            "cad_execution.step_readback_failed", str(read_error), exact_artifact=exact_artifact
        )

    preview_artifact = None
    preview_inspection = None
    if preview:
        linear_deflection = tolerance / 4.0
        preview_materials = _preview_materials(
            program,
            physical=physical,
            semantics=semantics,
            layer_colors=layer_colors,
            material_colors=material_colors,
        )
        preview_objects = tuple(
            PreviewObject(
                object_id=object_id,
                shape=build.objects[object_id].shape,
                layer=semantics["objects"][object_id]["layer"],
                user_text=semantics["objects"][object_id]["user_text"],
                visible=semantics["objects"][object_id].get("visible", True) is not False,
                material=preview_materials.get(object_id),
                delivery=deliveries[object_id],
            )
            for object_id in physical
        )
        phase = time.perf_counter()
        try:
            mesh_counts = write_preview_three_dm(
                preview_path,
                preview_objects,
                layer_colors=layer_colors,
                document_user_text=document_user_text,
                length_unit=unit,
                linear_deflection=linear_deflection,
                angular_deflection=_PREVIEW_ANGULAR_DEFLECTION,
                operation_observer=operation_observer,
                observation_parent_id=observation_parent_id,
            )
            preview_artifact = _preview_artifact(
                preview_path, workspace, linear_deflection, mesh_counts, preview_materials
            )
        except (OcctBackendError, OSError) as exc:
            failures.append(_failure("cad_execution.preview_write_failed", str(exc)))
        timings["preview_write_seconds"] = time.perf_counter() - phase
        if preview_artifact is not None:
            with _occt_step(operation_observer, "preview_readback", parent_event_id=observation_parent_id,
                                timings=timings, timing_key="preview_read_seconds",
                                details={"input_identity": {"asset_sha256": preview_artifact["sha256"]},
                                         "input_object_ids": list(physical), "execution_path": "occt",
                                         "scope": "cold_read_and_verification", "executed_stages": ["inspect_three_dm", "verify_preview"],
                                         "comparison_refs": [preview_path.name]}) as preview_span:
                try:
                    _strict_child(workspace, preview_path, require_exists=True)
                    inspection = inspect_three_dm(preview_path)
                except (CadExecutionError, ThreeDmInspectionError, OSError) as exc:
                    failures.append(_failure("cad_execution.preview_readback_failed", str(exc)))
                    preview_span["status"] = "failed"
                else:
                    preview_inspection = inspection.to_dict()
                    preview_failures = _verify_preview_readback(
                        inspection, physical=physical, semantics=semantics,
                        readback_bounds={object_id: row["bbox"] for object_id, row in readback.items() if "bbox" in row},
                        layer_colors=layer_colors, expected_document_user_text=document_user_text,
                        expected_materials=preview_materials, expected_curves=curves, length_unit=unit, tolerance=tolerance,
                    )
                    failures.extend(preview_failures)
                    if preview_failures:
                        preview_span["status"] = "failed"
    timings["total_seconds"] = time.perf_counter() - started
    return OcctExecutionReceipt(
        status=CadExecutionStatus.FAILED if failures else CadExecutionStatus.SUCCEEDED,
        identity=identity,
        adapter_id=OCCT_ADAPTER_ID,
        backend=backend,
        evidence_tier=OCCT_EVIDENCE_TIER,
        exact_artifact=exact_artifact,
        preview_artifact=preview_artifact,
        physical_object_ids=physical,
        expected_semantics=semantics,
        expected_bounds=bounds,
        readback=readback,
        preview_inspection=preview_inspection,
        readback_tolerance=tolerance,
        timings=timings,
        failures=tuple(failures),
        reused_object_ids=tuple(sorted(reused_shapes)),
        lowering=tuple(sorted(build.lowering.items())),
    )


def _reusable_occt_shapes(program, prior_program, prior_step, prior_step_sha256, *, diagnostics=None) -> dict[str, Any]:
    """Load unchanged physical inputs from the exact source the caller chose."""

    details = diagnostics if diagnostics is not None else {}
    details.update(cache_status="miss", cache_reason="source_not_selected", cache_checks={})
    if prior_program is None and prior_step is None and prior_step_sha256 is None:
        return {}
    if prior_program is None or prior_step is None or prior_step_sha256 is None:
        details.update(cache_status="refused", cache_reason="source_inputs_incomplete", cache_checks={"source_inputs": "missing"})
        raise CadExecutionError("OCCT reuse requires the prior program, STEP and certified digest")
    if program.proposal.length_unit != prior_program.proposal.length_unit:
        details.update(cache_reason="length_unit_changed", cache_checks={"length_unit": "changed"})
        return {}
    source = Path(prior_step)
    if source.is_symlink():
        details.update(cache_status="refused", cache_reason="source_symlink_refused", cache_checks={"artifact": "changed"})
        raise CadExecutionError("OCCT reuse source cannot be a symlink")
    details.update(cache_status="refused", cache_reason="source_artifact_unreadable")
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != prior_step_sha256:
            details.update(cache_reason="source_artifact_changed", cache_checks={"artifact": "changed"})
            raise CadExecutionError("OCCT reuse source differs from its certified STEP")
    details.update(cache_reason="source_step_readback_failed", cache_checks={"artifact": "same"})
    entries = read_step(source, length_unit=program.proposal.length_unit.value)
    by_name = {entry.name: entry.shape for entry in entries}
    if len(by_name) != len(entries) or set(by_name) != set(delivered_object_ids(prior_program.proposal)):
        details.update(cache_reason="source_object_identity_changed", cache_checks={"artifact": "same", "object_names": "changed"})
        raise CadExecutionError("OCCT reuse source has missing or ambiguous physical objects")
    from monkeycad.patch import select_patch_operations

    selection = select_patch_operations(program, prior_program)
    # The Rhino patch's kept set excludes the entire connected input closure.
    # OCCT can keep an unchanged final shape even when a changed sibling needs
    # their missing shared intermediate rebuilt from the program.
    unchanged = (set(by_name) & set(delivered_object_ids(program.proposal))) - set(selection.changed_object_ids)
    details.update(
        cache_status="hit" if unchanged and unchanged == set(delivered_object_ids(program.proposal)) else "partial" if unchanged else "miss",
        cache_reason="geometry_changed" if selection.changed_object_ids else "objects_added_or_retired"
            if selection.added_object_ids or selection.retired_object_ids else "unchanged_geometry",
        input_equivalent=selection.empty,
        input_object_ids=sorted(by_name), reused_object_ids=sorted(unchanged),
        cache_checks={"length_unit": "same", "source_artifact": "same", "source_object_names": "same",
                      **{f"{name}.{reason}": "changed" for name, reason in selection.reasons.items()},
                      **{f"{name}.geometry": "same" for name in unchanged}},
        comparison_refs=[source.name],
    )
    return {name: by_name[name] for name in unchanged}


def _declared_deliveries(program: CompiledGeometryProgram, physical: tuple[str, ...]) -> dict[str, str]:
    """Per physical object, what its producer operation declared: ``closed_solid`` or ``open_surface``.

    A plain value read off the compiled program's operations; the verifier
    takes it as the denominator for closure, next to the predictor's bounds
    and B-rep counts.
    """

    producers = {
        object_id: operation
        for operation in program.proposal.operations
        for object_id in operation.output_object_ids
    }
    return {object_id: declared_delivery(producers[object_id]) for object_id in physical}


def _exact_artifact(path: Path, workspace: Path, deliveries: Mapping[str, str]) -> dict[str, object]:
    solids = sum(1 for delivery in deliveries.values() if delivery == CLOSED_SOLID)
    surfaces = sum(1 for delivery in deliveries.values() if delivery == OPEN_SURFACE)
    curves = sum(1 for delivery in deliveries.values() if delivery == CURVE)
    geometry = f"exact B-rep in the CAD frame and the program unit: {solids} closed solid object(s)"
    if surfaces:
        geometry += f", {surfaces} open surface object(s) from planar faces or uncapped lofts"
    if curves:
        geometry += f", {curves} polyline curve object(s) without faces or thickness"
    return {
        "format": _STEP_FORMAT,
        "relative_path": path.relative_to(workspace).as_posix(),
        "sha256": _sha256_bytes(long_path(path).read_bytes()),
        "exact_brep": True,
        "carries": [
            "one named shape per physical object (name = object id)",
            "the semantic layer path and its colour per object",
            geometry,
        ],
        "deliveries": {object_id: deliveries[object_id] for object_id in sorted(deliveries)},
        "does_not_carry": ["archflow:* object user text", "archflow:* document user text"],
    }


def _preview_artifact(
    path: Path,
    workspace: Path,
    linear_deflection: float,
    mesh_counts: Mapping[str, Mapping[str, int]],
    materials: Mapping[str, PreviewMaterial],
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "format": _PREVIEW_FORMAT,
        "relative_path": path.relative_to(workspace).as_posix(),
        "sha256": _sha256_bytes(long_path(path).read_bytes()),
        "exact_brep": False,
        "geometry": "render mesh tessellated from the same OCCT model as the STEP file",
        "tessellator": "BRepMesh_IncrementalMesh",
        "linear_deflection": linear_deflection,
        "angular_deflection": _PREVIEW_ANGULAR_DEFLECTION,
        "mesh_counts": {key: dict(value) for key, value in sorted(mesh_counts.items()) if "mesh_face_count" in value},
        "carries": [
            "object names, nested layer paths and layer colours",
            "archflow:* object user text",
            "archflow:* document user text",
        ],
        "note": "not a NURBS/B-rep delivery; the STEP file is the exact geometry",
    }
    curves = {key: dict(value) for key, value in sorted(mesh_counts.items()) if "curve_point_count" in value}
    if curves:
        artifact.update(format="3dm render-mesh and curve preview", curve_counts=curves,
                        geometry="render meshes and native polylines from the same OCCT model as the STEP file",
                        note="Surfaces are render meshes; polylines are native curves. STEP retains the exact model.")
    if materials:
        artifact["carries"].append("native object materials for assembly frame and glazing members")
        artifact["materials"] = {
            object_id: material.to_dict() for object_id, material in sorted(materials.items())
        }
    return artifact


def _verify_step_readback(
    entries,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    expected_bounds: Mapping[str, Mapping[str, object]],
    expected_counts: Mapping[str, int],
    layer_colors: Mapping[str, tuple[int, int, int]],
    tolerance: float,
    expected_deliveries: Mapping[str, str] | None = None,
    expected_curves: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    """Measure every named entry of the cold read against the denominator.

    ``expected_deliveries`` says per object whether its producer declared a
    closed solid (the default for every object it does not name) or an open
    surface.  A closed solid must hold exactly ``expected_counts`` solids,
    all closed.  An open surface must hold no solid, at least one face and an
    actual open boundary (free edges); its volume is not a measurement and
    is reported as ``None``. Curves must contain only the declared path's edges,
    checked against its full vertex sequence, endpoints and length.
    """

    failures: list[dict[str, str]] = []
    deliveries = dict(expected_deliveries or {})
    for object_id in physical:
        delivery = deliveries.setdefault(object_id, CLOSED_SOLID)
        if delivery not in (CLOSED_SOLID, OPEN_SURFACE, CURVE):
            raise CadExecutionError(f"{object_id}: unknown declared delivery {delivery!r}")
    by_name: dict[str, list] = {}
    unnamed = 0
    for entry in entries:
        if entry.name is None:
            unnamed += 1
            continue
        by_name.setdefault(entry.name, []).append(entry)
    if unnamed:
        failures.append(
            _failure("cad_execution.step_object_unnamed", f"{unnamed} shape(s) carry no object name")
        )
    missing = sorted(set(physical) - set(by_name))
    extra = sorted(set(by_name) - set(physical))
    if missing or extra:
        failures.append(
            _failure(
                "cad_execution.step_object_set_mismatch",
                f"missing={missing} extra={extra}",
            )
        )
    readback: dict[str, dict[str, object]] = {}
    for object_id in physical:
        rows = by_name.get(object_id, [])
        if len(rows) != 1:
            if rows:
                failures.append(
                    _failure("cad_execution.step_object_duplicate", f"{object_id} appears {len(rows)} times")
                )
            continue
        entry = rows[0]
        try:
            measure = measure_shape(entry.shape)
        except OcctBackendError as exc:
            failures.append(_failure("cad_execution.step_shape_invalid", f"{object_id}: {exc}"))
            continue
        row = measure.to_dict()
        row["name"] = entry.name
        row["layers"] = list(entry.layers)
        row["color"] = list(entry.color) if entry.color is not None else None
        row["declared_delivery"] = deliveries[object_id]
        readback[object_id] = row
        if not measure.valid:
            failures.append(_failure("cad_execution.step_shape_invalid", f"{object_id} is not a valid shape"))
        if deliveries[object_id] == CURVE:
            try:
                if measure.solid_count or measure.face_count:
                    raise OcctBackendError("curve contains a surface or solid")
                row.update(_polyline_geometry(entry.shape))
                if not _curve_matches(row, (expected_curves or {}).get(object_id, ()), tolerance):
                    raise OcctBackendError("curve vertices, endpoints or length differ from the authored path")
            except OcctBackendError as exc:
                failures.append(_failure("cad_execution.step_curve_mismatch", f"{object_id}: {exc}"))
        elif deliveries[object_id] == OPEN_SURFACE:
            if measure.solid_count or measure.closed or measure.free_edge_count == 0 or measure.face_count == 0:
                failures.append(
                    _failure(
                        "cad_execution.step_not_open_surface",
                        f"{object_id} was declared an open surface but holds {measure.solid_count} solid(s), "
                        f"{measure.face_count} face(s) and {measure.free_edge_count} free edge(s)",
                    )
                )
            if measure.volume is not None:
                failures.append(_failure("cad_execution.step_not_open_surface", f"{object_id} reports a volume for an open surface"))
        else:
            if measure.solid_count != expected_counts[object_id]:
                failures.append(
                    _failure(
                        "cad_execution.step_solid_count_mismatch",
                        f"{object_id} has {measure.solid_count} solid(s), expected {expected_counts[object_id]}",
                    )
                )
            if not measure.closed:
                failures.append(_failure("cad_execution.step_not_closed_solid", f"{object_id} is not a closed solid"))
        if not _bbox_close(row["bbox"], expected_bounds[object_id], tolerance):
            failures.append(_failure("cad_execution.named_bounds_mismatch", f"object {object_id} bounds differ"))
        expected_layer = semantics["objects"][object_id]["layer"]
        if tuple(entry.layers) != (expected_layer,):
            failures.append(
                _failure("cad_execution.object_layer_mismatch", f"object {object_id} layer differs from semantic contract")
            )
        expected_color = layer_colors.get(expected_layer)
        if expected_color is not None and (
            entry.color is None
            or any(abs(int(a) - int(b)) > 1 for a, b in zip(entry.color, expected_color))
        ):
            failures.append(
                _failure("cad_execution.layer_color_mismatch", f"object {object_id} colour differs from layer contract")
            )
    if set(readback) == set(physical) and physical:
        aggregate = _aggregate_bounds(readback[object_id]["bbox"] for object_id in physical)
        if not _bbox_close(aggregate, _aggregate_bounds(expected_bounds.values()), tolerance):
            failures.append(
                _failure("cad_execution.aggregate_bounds_mismatch", "aggregate STEP bounds differ")
            )
    return readback, failures


def _curve_matches(actual: Mapping[str, object], expected: Sequence[Sequence[float]], tolerance: float) -> bool:
    points = actual.get("curve_points", ())
    length = actual.get("curve_length")
    if not expected or len(points) != len(expected) or not isinstance(length, (int, float)):
        return False
    expected_length = sum(math.dist(a, b) for a, b in zip(expected, expected[1:]))
    return abs(length - expected_length) <= tolerance and any(
        all(math.dist(a, b) <= tolerance for a, b in zip(points, ordered))
        for ordered in (expected, tuple(reversed(expected)))
    )


def _verify_preview_readback(
    inspection: ThreeDmInspection,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    readback_bounds: Mapping[str, Mapping[str, object]],
    layer_colors: Mapping[str, tuple[int, int, int]],
    expected_document_user_text: Mapping[str, str],
    length_unit: str,
    tolerance: float,
    expected_materials: Mapping[str, PreviewMaterial] | None = None,
    expected_curves: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> list[dict[str, str]]:
    """The preview carries the same identities and mesh/curve geometry as the STEP."""

    failures: list[dict[str, str]] = []
    if not inspection.read_only or inspection.rhino_process_started:
        failures.append(
            _failure("cad_execution.inspector_not_read_only", "verification must be independent and read-only")
        )
    if inspection.units.get("name") != _UNIT_TO_RHINO[length_unit][1]:
        failures.append(_failure("cad_execution.unit_mismatch", "preview units differ"))
    actual_document = {
        row["key"]: row["value"]
        for row in inspection.document_user_strings
        if str(row["key"]).startswith("archflow:")
    }
    if actual_document != dict(expected_document_user_text):
        failures.append(
            _failure("cad_execution.provenance_mismatch", "archflow document user-text key/value set differs")
        )
    actual_layers: dict[str, list] = {}
    for row in inspection.layers:
        actual_layers.setdefault(str(row.get("full_path")), []).append(row)
    for full_path, color in sorted(layer_colors.items()):
        rows = actual_layers.get(full_path, [])
        if len(rows) != 1:
            failures.append(_failure("cad_execution.layer_set_mismatch", f"layer {full_path} is missing or duplicated"))
            continue
        if rows[0].get("color_rgba") != [*color, 255]:
            failures.append(_failure("cad_execution.layer_color_mismatch", f"layer {full_path} RGBA differs"))
    if inspection.top_level_object_count != len(physical):
        failures.append(
            _failure("cad_execution.object_count_mismatch", "preview object count differs from physical denominator")
        )
    named: dict[str, list] = {}
    for row in inspection.named_object_bboxes:
        named.setdefault(str(row["name"]), []).append(row)
    if set(named) != set(physical) or any(len(rows) != 1 for rows in named.values()):
        failures.append(
            _failure("cad_execution.named_object_mismatch", "preview named-object set has missing, duplicate, or extra identities")
        )
    for object_id in sorted(set(named) & set(physical)):
        row = named[object_id][0]
        if len(named[object_id]) != 1:
            continue
        curve = (expected_curves or {}).get(object_id)
        if curve is not None:
            analysis = [item for item in inspection.object_geometry_analysis if item["name"] == object_id]
            if row.get("type") != "Curve" or len(analysis) != 1 or not _curve_matches(analysis[0], curve, tolerance):
                failures.append(_failure("cad_execution.preview_curve_mismatch", f"object {object_id} curve vertices, endpoints or length differ"))
        elif row.get("type") != "Mesh":
            failures.append(_failure("cad_execution.preview_not_mesh", f"object {object_id} is not a preview mesh"))
        expected = readback_bounds.get(object_id)
        if expected is None or not _bbox_close(row["bbox"], expected, tolerance):
            failures.append(_failure("cad_execution.named_bounds_mismatch", f"preview object {object_id} bounds differ from STEP"))
    observed: dict[str, list] = {}
    for row in inspection.object_user_strings:
        if row.get("is_instance_definition_object"):
            continue
        observed.setdefault(str(row.get("name")), []).append(row)
    for object_id in physical:
        expected_semantic = semantics["objects"][object_id]
        rows = observed.get(object_id, [])
        if len(rows) != 1:
            failures.append(_failure("cad_execution.semantic_witness_count_mismatch", f"object {object_id} has {len(rows)} semantic witnesses"))
            continue
        pairs = {
            pair["key"]: pair["value"]
            for pair in rows[0].get("attributes", ())
            if str(pair["key"]).startswith("archflow:")
        }
        if pairs != dict(expected_semantic["user_text"]):
            failures.append(_failure("cad_execution.semantic_user_text_mismatch", f"object {object_id} semantic user text differs"))
        if rows[0].get("layer_path") != expected_semantic["layer"]:
            failures.append(_failure("cad_execution.object_layer_mismatch", f"preview object {object_id} layer differs"))
    bindings: dict[str, list] = {}
    for row in inspection.object_material_bindings:
        if row.get("is_instance_definition_object"):
            continue
        bindings.setdefault(str(row.get("name")), []).append(row)
    for object_id, material in sorted((expected_materials or {}).items()):
        rows = bindings.get(object_id, [])
        transparency = rows[0].get("material_transparency") if len(rows) == 1 else None
        bound = (
            len(rows) == 1
            and rows[0].get("material_source") == "MaterialFromObject"
            and rows[0].get("material_name") == material.name
            and rows[0].get("archflow_material_id") == material.name
            and rows[0].get("material_diffuse_color_rgba") == [*material.diffuse, 255]
            and isinstance(transparency, (int, float))
            and not isinstance(transparency, bool)
            and abs(float(transparency) - material.transparency) <= _PREVIEW_TRANSPARENCY_TOLERANCE
        )
        if not bound:
            failures.append(
                _failure("cad_execution.preview_material_mismatch", f"preview object {object_id} does not wear material {material.name}")
            )
    return failures
