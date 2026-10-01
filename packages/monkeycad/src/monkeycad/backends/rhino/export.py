"""Fail-closed Rhino externalization for exact neutral geometry programs.

The adapter writes only a translated script into a caller-supplied speculative
workspace.  It has no project-repository or canonical-write authority.  A
successful process exit is never sufficient: the saved ``.3dm`` must be read
back independently and match the exact P036 program binding, complete physical
denominator, native semantics, instance counts, and expected bounds.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping

from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    CompiledGeometryProgram,
    delivered_object_ids,
    expected_object_bounds,
    require_sha256,
)
from monkeycad.backends.occt.export import _preview_materials
from monkeycad.backends.rhino.script import (
    _completion_marker_payload,
    _export_script,
    build_patch_prelude,
    translate_step_import_to_rhino_python,
    translate_to_rhino_python,
)
from monkeycad.backends.rhino.step_import import StepImportSource
from monkeycad.discovery import SoftwareDiscoveryRegistry
from monkeycad.execution import (
    CadExecutionError,
    CadExecutionStatus,
    RhinoCadProgramBinding,
    _MAX_PROCESS_TEXT,
    _UNIT_TO_RHINO,
    _aggregate_bounds,
    _bbox_close,
    _bounds_to_rhino,
    _failure,
    _json_copy,
    _portable_relative_path,
    _positive_finite,
    _provenance,
    _require_file_digest,
    _sha256_bytes,
    _sha256_text,
    _strict_child,
    _strict_workspace,
    long_path,
)
from monkeycad.formats.three_dm_inspector import ThreeDmInspection, ThreeDmInspectionError, inspect_three_dm
from monkeycad.patch import CadPatchError, select_patch_operations
from monkeycad.program import _resolved_layer_colors, expected_object_semantics


@dataclass(frozen=True, slots=True)
class RhinoCadExportIdentity:
    """Stable execution identity derived only from the exact program binding."""

    binding: RhinoCadProgramBinding
    length_unit: str
    up_axis: str = "Z-up"

    SCHEMA = "RhinoCadExportIdentity@2"

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RhinoCadProgramBinding):
            raise TypeError("binding must be RhinoCadProgramBinding")
        if self.length_unit not in _UNIT_TO_RHINO:
            raise CadExecutionError(
                "length_unit must be millimeter, meter, inch, or foot"
            )
        if self.up_axis != "Z-up":
            raise CadExecutionError("Rhino CAD externalization requires Z-up")

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
        }


@dataclass(frozen=True, slots=True)
class RhinoCadExportPlan:
    """Prepared script and complete deterministic readback denominator."""

    identity: RhinoCadExportIdentity
    workspace: Path
    script_path: Path
    model_path: Path
    completion_marker_path: Path
    host_witness_path: Path
    script_sha256: str
    translation_sha256: str
    validation_denominator_sha256: str
    completion_token: str
    completion_witness_sha256: str
    expected_layer_colors: tuple[tuple[str, tuple[int, int, int]], ...]
    physical_object_ids: tuple[str, ...]
    expected_document_user_text: tuple[tuple[str, str], ...]
    expected_semantics: dict[str, object]
    expected_bounds: dict[str, dict[str, object]]
    expected_object_counts: tuple[tuple[str, int], ...]
    readback_tolerance: float
    patch: dict[str, object] | None = None

    SCHEMA = "RhinoCadExportPlan@4"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RhinoCadExportIdentity):
            raise TypeError("identity must be RhinoCadExportIdentity")
        for field in (
            "workspace",
            "script_path",
            "model_path",
            "completion_marker_path",
            "host_witness_path",
        ):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"{field} must be pathlib.Path")
        _validate_plan_paths(self, require_script=True)
        object.__setattr__(
            self,
            "script_sha256",
            require_sha256(self.script_sha256, "script_sha256"),
        )
        object.__setattr__(
            self,
            "translation_sha256",
            require_sha256(self.translation_sha256, "translation_sha256"),
        )
        object.__setattr__(
            self,
            "validation_denominator_sha256",
            require_sha256(
                self.validation_denominator_sha256,
                "validation_denominator_sha256",
            ),
        )
        object.__setattr__(
            self,
            "completion_token",
            require_sha256(self.completion_token, "completion_token"),
        )
        object.__setattr__(
            self,
            "completion_witness_sha256",
            require_sha256(
                self.completion_witness_sha256,
                "completion_witness_sha256",
            ),
        )
        if self.physical_object_ids != tuple(sorted(set(self.physical_object_ids))):
            raise CadExecutionError("physical_object_ids must be sorted and unique")
        if self.expected_layer_colors != tuple(
            sorted(set(self.expected_layer_colors))
        ):
            raise CadExecutionError("expected_layer_colors must be sorted and unique")
        for layer_path, color in self.expected_layer_colors:
            if not isinstance(layer_path, str) or not layer_path:
                raise CadExecutionError("expected layer paths must be non-empty text")
            if (
                not isinstance(color, tuple)
                or len(color) != 3
                or any(
                    isinstance(channel, bool)
                    or not isinstance(channel, int)
                    or channel < 0
                    or channel > 255
                    for channel in color
                )
            ):
                raise CadExecutionError("expected layer colors must be RGB bytes")
        for value in self.physical_object_ids:
            require_identifier(value, "physical_object_ids")
        if self.expected_document_user_text != tuple(
            sorted(set(self.expected_document_user_text))
        ):
            raise CadExecutionError(
                "expected_document_user_text must be sorted and unique"
            )
        for key, value in self.expected_document_user_text:
            if not isinstance(key, str) or not key.startswith("archflow:"):
                raise CadExecutionError("document keys must use archflow namespace")
            if not isinstance(value, str) or not value:
                raise CadExecutionError("document values must be non-empty text")
        if not isinstance(self.expected_semantics, dict) or set(
            self.expected_semantics
        ) != {"objects", "blocks"}:
            raise CadExecutionError("expected_semantics schema drifted")
        if set(self.expected_semantics["objects"]) != set(
            self.physical_object_ids
        ):
            raise CadExecutionError("semantic and physical denominators differ")
        semantic_layers = {
            row["layer"] for row in self.expected_semantics["objects"].values()
        }
        if {path for path, _ in self.expected_layer_colors} != {
            "archflow",
            *semantic_layers,
        }:
            raise CadExecutionError("layer and semantic denominators differ")
        if not isinstance(self.expected_bounds, dict) or set(
            self.expected_bounds
        ) != set(self.physical_object_ids):
            raise CadExecutionError("bounds and physical denominators differ")
        if self.expected_object_counts != tuple(
            sorted(set(self.expected_object_counts))
        ):
            raise CadExecutionError("expected_object_counts must be sorted and unique")
        if {key for key, _ in self.expected_object_counts} != set(
            self.physical_object_ids
        ) or any(count <= 0 for _, count in self.expected_object_counts):
            raise CadExecutionError("expected_object_counts denominator is invalid")
        tolerance = _positive_finite(self.readback_tolerance, "readback_tolerance")
        object.__setattr__(self, "readback_tolerance", tolerance)
        _validate_plan_denominator(self)
        expected_token = _completion_token(
            identity=self.identity,
            artifact_relative_path=self.artifact_relative_path,
            translation_sha256=self.translation_sha256,
            validation_denominator_sha256=self.validation_denominator_sha256,
        )
        if self.completion_token != expected_token:
            raise CadExecutionError("completion token does not bind the exact plan")
        expected_witness = _sha256_text(
            _marker_text(
                _completion_marker_payload(
                    artifact_relative_path=self.artifact_relative_path,
                    completion_token=self.completion_token,
                    status="succeeded",
                )
            )
        )
        if self.completion_witness_sha256 != expected_witness:
            raise CadExecutionError("completion witness digest does not match the plan")

    @property
    def script_relative_path(self) -> str:
        return self.script_path.relative_to(self.workspace).as_posix()

    @property
    def artifact_relative_path(self) -> str:
        return self.model_path.relative_to(self.workspace).as_posix()

    @property
    def completion_marker_relative_path(self) -> str:
        return self.completion_marker_path.relative_to(self.workspace).as_posix()

    @property
    def host_witness_relative_path(self) -> str:
        return self.host_witness_path.relative_to(self.workspace).as_posix()

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Serialize without the machine-local workspace root."""

        return _json_copy(
            {
                "schema": self.SCHEMA,
                "identity": self.identity.to_dict(),
                "script_relative_path": self.script_relative_path,
                "artifact_relative_path": self.artifact_relative_path,
                "completion_marker_relative_path": (
                    self.completion_marker_relative_path
                ),
                "host_witness_relative_path": self.host_witness_relative_path,
                "script_sha256": self.script_sha256,
                "translation_sha256": self.translation_sha256,
                "validation_denominator_sha256": (
                    self.validation_denominator_sha256
                ),
                "completion_token": self.completion_token,
                "completion_witness_sha256": self.completion_witness_sha256,
                "expected_layer_colors": [
                    {"full_path": path, "rgb": list(color)}
                    for path, color in self.expected_layer_colors
                ],
                "physical_object_ids": list(self.physical_object_ids),
                "expected_document_user_text": [
                    {"key": key, "value": value}
                    for key, value in self.expected_document_user_text
                ],
                "expected_semantics": self.expected_semantics,
                "expected_bounds": self.expected_bounds,
                "expected_object_counts": [
                    {"object_id": key, "count": count}
                    for key, count in self.expected_object_counts
                ],
                "readback_tolerance": self.readback_tolerance,
                "patch": _json_copy(self.patch) if self.patch else None,
                "canonical_write_authority": False,
            }
        )


@dataclass(frozen=True, slots=True)
class RhinoCadExecutionReceipt:
    """Process evidence plus independent saved-file readback."""

    status: CadExecutionStatus
    identity: RhinoCadExportIdentity
    adapter_id: str
    executable_name: str
    plan_digest: str
    script_sha256: str
    artifact_relative_path: str
    completion_marker_relative_path: str
    host_witness_relative_path: str
    completion_token: str
    completion_witness_sha256: str
    completion_marker_sha256: str | None
    validation_denominator_sha256: str
    host_witness_sha256: str | None
    cleanup_witness_sha256: str | None
    cleanup_status: str
    process_exit_code: int | None
    stdout_sha256: str | None
    stderr_sha256: str | None
    inspection: dict[str, object] | None
    failures: tuple[dict[str, str], ...]

    SCHEMA = "RhinoCadExecutionReceipt@4"

    def __post_init__(self) -> None:
        if not isinstance(self.status, CadExecutionStatus):
            raise TypeError("status must be CadExecutionStatus")
        if not isinstance(self.identity, RhinoCadExportIdentity):
            raise TypeError("identity must be RhinoCadExportIdentity")
        require_identifier(self.adapter_id, "adapter_id")
        if not isinstance(self.executable_name, str) or not self.executable_name:
            raise CadExecutionError("executable_name must be non-empty text")
        for field in (
            "plan_digest",
            "script_sha256",
            "completion_token",
            "completion_witness_sha256",
            "validation_denominator_sha256",
        ):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        _portable_relative_path(self.artifact_relative_path)
        _portable_relative_path(self.completion_marker_relative_path)
        _portable_relative_path(self.host_witness_relative_path)
        if self.completion_marker_sha256 is not None:
            object.__setattr__(
                self,
                "completion_marker_sha256",
                require_sha256(
                    self.completion_marker_sha256,
                    "completion_marker_sha256",
                ),
            )
        for field in ("host_witness_sha256", "cleanup_witness_sha256"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self,
                    field,
                    require_sha256(value, field),
                )
        if self.cleanup_status not in (
            "confirmed",
            "cleanup_unverified",
            "not_performed",
        ):
            raise CadExecutionError("cleanup_status is invalid")
        if self.process_exit_code is not None and not isinstance(
            self.process_exit_code, int
        ):
            raise TypeError("process_exit_code must be int or None")
        for field in ("stdout_sha256", "stderr_sha256"):
            value = getattr(self, field)
            if value is not None:
                require_sha256(value, field)
        if self.inspection is not None and not isinstance(self.inspection, dict):
            raise TypeError("inspection must be dict or None")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(item, dict) for item in self.failures
        ):
            raise TypeError("failures must be tuple[dict, ...]")
        succeeded = self.status is CadExecutionStatus.SUCCEEDED
        if succeeded != (not self.failures and self.inspection is not None):
            raise CadExecutionError(
                "successful execution requires verified inspection and no failures"
            )
        if succeeded and self.completion_marker_sha256 != self.completion_witness_sha256:
            raise CadExecutionError(
                "successful execution requires the exact completion marker witness"
            )
        if succeeded and (
            self.host_witness_sha256 is None
            or self.cleanup_witness_sha256 is None
            or self.cleanup_status != "confirmed"
        ):
            raise CadExecutionError(
                "successful execution requires exact host and cleanup witnesses"
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
                "executable_name": self.executable_name,
                "plan_digest": self.plan_digest,
                "script_sha256": self.script_sha256,
                "artifact_relative_path": self.artifact_relative_path,
                "completion_marker_relative_path": (
                    self.completion_marker_relative_path
                ),
                "host_witness_relative_path": self.host_witness_relative_path,
                "completion_token": self.completion_token,
                "completion_witness_sha256": self.completion_witness_sha256,
                "completion_marker_sha256": self.completion_marker_sha256,
                "validation_denominator_sha256": (
                    self.validation_denominator_sha256
                ),
                "host_witness_sha256": self.host_witness_sha256,
                "cleanup_witness_sha256": self.cleanup_witness_sha256,
                "cleanup_status": self.cleanup_status,
                "process_exit_code": self.process_exit_code,
                "stdout_sha256": self.stdout_sha256,
                "stderr_sha256": self.stderr_sha256,
                "inspection": self.inspection,
                "failures": list(self.failures),
                "readback_verified": self.readback_verified,
                "canonical_write_authority": False,
            }
        )


@dataclass(frozen=True)
class RhinoPatchBase:
    """The prior saved document and the program it realized (P103)."""

    prior_model_path: Path
    prior_program: CompiledGeometryProgram

    def __post_init__(self) -> None:
        if not isinstance(self.prior_model_path, Path):
            raise TypeError("prior_model_path must be pathlib.Path")
        if not isinstance(self.prior_program, CompiledGeometryProgram):
            raise TypeError("prior_program must be CompiledGeometryProgram")


def prepare_rhino_three_dm_export(
    program: CompiledGeometryProgram,
    *,
    binding: RhinoCadProgramBinding,
    speculative_workspace: Path,
    artifact_name: str,
    readback_tolerance: float,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    patch: RhinoPatchBase | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    step_import: StepImportSource | None = None,
    source_materials: Mapping[str, Mapping[str, object]] | None = None,
    material_by_part: Mapping[str, Mapping[str, str | None]] | None = None,
) -> RhinoCadExportPlan:
    """Prepare one immutable export plan in an existing explicit workspace.

    With ``patch`` the plan rebuilds only the selected operations on top of
    the prior document's kept objects; the readback denominator is still
    the whole program, so a patch is verified exactly like a rebuild.

    With ``step_import`` the host builds nothing: it reads back the exact
    STEP this program already exported, one named shape per file, and the
    document is the same geometry rather than a second realization of it.
    The denominator is still the whole program, so an imported document is
    verified exactly like a rebuilt one. ``source_materials`` is that
    delivery's own retained material table, per object id, and is what the
    imported objects end up wearing.
    """

    if not isinstance(binding, RhinoCadProgramBinding):
        raise TypeError("binding must be RhinoCadProgramBinding")
    binding.bind_program(program)
    unit = program.proposal.length_unit.value
    identity = RhinoCadExportIdentity(binding=binding, length_unit=unit)
    tolerance = _positive_finite(readback_tolerance, "readback_tolerance")
    workspace = _strict_workspace(speculative_workspace)
    artifact_name = _artifact_name(artifact_name)
    model_path = workspace / artifact_name
    script_path = workspace / f"{Path(artifact_name).stem}.archflow.py"
    completion_marker_path = (
        workspace / f"{Path(artifact_name).stem}.archflow-completion.json"
    )
    host_witness_path = (
        workspace / f"{Path(artifact_name).stem}.archflow-host.json"
    )
    for target in (
        model_path,
        script_path,
        completion_marker_path,
        host_witness_path,
    ):
        _strict_child(workspace, target, require_exists=False)
        if target.exists():
            raise CadExecutionError(f"speculative output already exists: {target.name}")
    supplied = _provenance(identity, provenance, export_schema=RhinoCadExecutionReceipt.SCHEMA)
    selection = None
    patch_prelude = None
    patch_record: dict[str, object] | None = None
    if patch is not None:
        if not isinstance(patch, RhinoPatchBase):
            raise TypeError("patch must be RhinoPatchBase")
        prior_model = patch.prior_model_path.resolve()
        if not prior_model.is_file():
            raise CadExecutionError(f"patch base model does not exist: {prior_model}")
        try:
            selection = select_patch_operations(program, patch.prior_program)
        except CadPatchError as exc:
            raise CadExecutionError(f"patch not expressible: {exc}") from exc
        # an empty selection means the geometry is already in the prior document; the patch then only
        # carries every object over and re-stamps its semantics (bindings, evidence, commitments) — a "restamp"
        patch_prelude = build_patch_prelude(
            selection,
            prior_model_path=prior_model,
            semantics=expected_object_semantics(program, material_by_component=material_by_component,
                                                material_by_part=material_by_part,
                                                layer_by_component=layer_by_component)["objects"],
        )
        patch_record = {
            **selection.to_dict(),
            "mode": "restamp" if selection.empty else "patch",
            "prior_model_path": str(prior_model),
            "prior_model_sha256": _sha256_bytes(prior_model.read_bytes()),
        }
    if step_import is not None:
        if patch is not None:
            raise CadExecutionError("an imported document has no patch base to build on")
        if not isinstance(step_import, StepImportSource):
            raise TypeError("step_import must be StepImportSource")
        # The digests are what binds this plan to bytes rather than to file
        # names: a source STEP or a single-object file swapped between the
        # split and the export is a different document, and is refused here
        # rather than imported and then described as the one that was asked for.
        _require_file_digest(step_import.step_path, step_import.step_sha256, "the exported STEP")
        for item in step_import.objects:
            source_file = workspace / item.file_name
            _strict_child(workspace, source_file, require_exists=True)
            _require_file_digest(source_file, item.sha256, f"the import source for {item.object_id}")
        # The materials this delivery already wears. The caller passes the
        # table its own export receipt retained, so an imported document keeps
        # the materials that model was actually written with - glass included,
        # with its transparency. Only a receipt that recorded none falls back
        # to deriving them from the program the same way the preview did.
        import_semantics = _json_copy(
            expected_object_semantics(
                program,
                material_by_component=material_by_component,
                material_by_part=material_by_part,
                layer_by_component=layer_by_component,
            )
        )
        import_layer_colors = dict(
            _resolved_layer_colors(
                {row["layer"] for row in import_semantics["objects"].values()},
                material_by_component=material_by_component,
                material_colors=material_colors,
            )
        )
        translation = translate_step_import_to_rhino_python(
            program,
            step_file_by_object={item.object_id: item.file_name for item in step_import.objects},
            step_sha256_by_object={item.object_id: item.sha256 for item in step_import.objects},
            object_materials=dict(source_materials) if source_materials else {
                object_id: material.to_dict()
                for object_id, material in _preview_materials(
                    program,
                    physical=tuple(sorted(import_semantics["objects"])),
                    semantics=import_semantics,
                    layer_colors=import_layer_colors,
                    material_colors=material_colors,
                ).items()
            },
            provenance=supplied,
            material_by_component=material_by_component,
            material_by_part=material_by_part,
            material_colors=material_colors,
            layer_by_component=layer_by_component,
        )
    else:
        translation = translate_to_rhino_python(
            program,
            provenance=supplied,
            material_by_component=material_by_component,
            material_by_part=material_by_part,
            material_colors=material_colors,
            operation_subset=None if selection is None else selection.rebuilt_op_ids,
            layer_by_component=layer_by_component,
        )
    if translation.losses:
        raise CadExecutionError(
            "CAD translation has typed losses and cannot be exported strictly: "
            + json.dumps(translation.losses, sort_keys=True)
        )
    semantics = _json_copy(
        expected_object_semantics(
            program,
            material_by_component=material_by_component,
            material_by_part=material_by_part,
            layer_by_component=layer_by_component,
        )
    )
    if step_import is not None:
        # A rebuilt document repeats an array as a block definition and its
        # instances; an imported one holds the solids the STEP already carries,
        # one named object per copy. The denominator states the representation
        # this export actually delivers rather than the other one's: the
        # objects, their names, layers, semantics and counts are all still
        # verified, and this is where the count of each is stated.
        semantics["blocks"] = {}
    raw_bounds = expected_object_bounds(program)
    bounds = {
        object_id: _bounds_to_rhino(value)
        for object_id, value in sorted(raw_bounds.items())
    }
    counts = tuple(
        sorted(
            (object_id, int(value["brep_count"]))
            for object_id, value in raw_bounds.items()
        )
    )
    translation_sha256 = _sha256_text(translation.script)
    # a patch names only the rebuilt objects in its script; the denominator stays the whole document
    physical_object_ids = tuple(sorted(translation.physical_object_ids if selection is None else delivered_object_ids(program.proposal)))
    expected_document_user_text = tuple(
        sorted((f"archflow:{key}", value) for key, value in supplied.items())
    )
    validation_denominator_sha256 = _validation_denominator_digest(
        identity=identity,
        translation_sha256=translation_sha256,
        expected_layer_colors=translation.layer_colors,
        physical_object_ids=physical_object_ids,
        expected_document_user_text=expected_document_user_text,
        expected_semantics=semantics,
        expected_bounds=bounds,
        expected_object_counts=counts,
        readback_tolerance=tolerance,
    )
    completion_token = _completion_token(
        identity=identity,
        artifact_relative_path=artifact_name,
        translation_sha256=translation_sha256,
        validation_denominator_sha256=validation_denominator_sha256,
    )
    success_marker = _completion_marker_payload(
        artifact_relative_path=artifact_name,
        completion_token=completion_token,
        status="succeeded",
    )
    completion_witness_sha256 = _sha256_text(_marker_text(success_marker))
    script = _export_script(
        translation.script,
        artifact_name=artifact_name,
        completion_marker_name=completion_marker_path.name,
        completion_token=completion_token,
        length_unit=unit,
        readback_tolerance=tolerance,
        patch_prelude=patch_prelude,
        source_measures=None if step_import is None else {
            item.object_id: {
                "solid_count": item.solid_count,
                "face_count": item.face_count,
                "closed": item.closed,
                "volume": item.volume,
            }
            for item in step_import.objects
        },
    )
    try:
        with long_path(script_path).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(script)
    except FileExistsError as exc:
        raise CadExecutionError(
            "speculative script appeared before exclusive creation"
        ) from exc
    return RhinoCadExportPlan(
        identity=identity,
        workspace=workspace,
        script_path=script_path,
        model_path=model_path,
        completion_marker_path=completion_marker_path,
        host_witness_path=host_witness_path,
        script_sha256=_sha256_text(script),
        translation_sha256=translation_sha256,
        validation_denominator_sha256=validation_denominator_sha256,
        completion_token=completion_token,
        completion_witness_sha256=completion_witness_sha256,
        expected_layer_colors=translation.layer_colors,
        physical_object_ids=physical_object_ids,
        expected_document_user_text=expected_document_user_text,
        expected_semantics=semantics,
        expected_bounds=bounds,
        expected_object_counts=counts,
        readback_tolerance=tolerance,
        patch=patch_record,
    )


def discover_rhino_executables() -> tuple[Path, ...]:
    """Read-only discovery; never launches or attaches to Rhino."""

    discovery = SoftwareDiscoveryRegistry().discover("rhino")
    # The supervised host is Rhino's Windows COM server; a Mac bundle is not one.
    if discovery.system != "Windows":
        return ()
    return tuple(row.executable for row in discovery.installations)


def discover_powershell() -> Path | None:
    """The shell that supervises the host, when the caller named none.

    Read-only and ordinary: the two places Windows keeps its own PowerShell,
    in the order an operator would expect. A caller's explicit executable
    always wins; this only answers the case where nothing was configured, so
    a machine that has Rhino and PowerShell can export without being asked to
    fill in a path it did not choose. Nothing is launched here.
    """

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for candidate in (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe",
    ):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    return None


def build_rhino_com_powershell_source(
    plan: RhinoCadExportPlan,
    *,
    timeout_seconds: float,
) -> str:
    """Build the STA worker that records its Rhino host before blocking COM."""

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    timeout = _positive_finite(timeout_seconds, "timeout_seconds")
    _validate_plan_paths(plan, require_script=True)
    script_path = str(plan.script_path)
    host_witness_path = str(plan.host_witness_path)
    if any(character in script_path for character in "\x00\r\n()"):
        raise CadExecutionError(
            "Rhino COM script path contains unsafe macro characters"
        )
    if any(character in host_witness_path for character in "\x00\r\n"):
        raise CadExecutionError(
            "Rhino host witness path contains unsafe characters"
        )
    script_literal = _powershell_literal(script_path)
    host_literal = _powershell_literal(host_witness_path)
    token_literal = _powershell_literal(plan.completion_token)
    timeout_literal = format(timeout, ".17g")
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$scriptPath = {script_literal}
$hostWitnessPath = {host_literal}
$completionToken = {token_literal}
$timeoutSeconds = [double]{timeout_literal}
$initDeadline = [DateTime]::UtcNow.AddSeconds([Math]::Min(60.0, $timeoutSeconds))
function Write-ExclusiveUtf8Json([string]$Path, [object]$Payload) {{
  $text = $Payload | ConvertTo-Json -Compress -Depth 4
  $encoding = New-Object System.Text.UTF8Encoding($false)
  $stream = [IO.File]::Open(
    $Path,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::None
  )
  try {{
    $bytes = $encoding.GetBytes($text)
    $stream.Write($bytes, 0, $bytes.Length)
  }} finally {{
    $stream.Dispose()
  }}
}}
$baselinePids = @(
  Get-Process -Name 'Rhino' -ErrorAction SilentlyContinue |
    ForEach-Object {{ [int]$_.Id }}
)
$rhino = $null
$result = [ordered]@{{
  schema = 'RhinoComPowerShellWorkerResult@4'
  prog_id = 'Rhino.Application.8'
  initialized = $false
  ownership_status = 'not_observed'
  error_code = $null
  error_detail = $null
}}
try {{
  $type = [Type]::GetTypeFromProgID('Rhino.Application.8', $true)
  $rhino = [Activator]::CreateInstance($type)
  $rhino.Visible = 0
  while ([int]$rhino.IsInitialized() -eq 0) {{
    if ([DateTime]::UtcNow -ge $initDeadline) {{ throw 'RHINO_INIT_TIMEOUT' }}
    Start-Sleep -Milliseconds 100
  }}
  $result.initialized = $true
  $currentPids = @(
    Get-Process -Name 'Rhino' -ErrorAction SilentlyContinue |
      ForEach-Object {{ [int]$_.Id }}
  )
  $newPids = @($currentPids | Where-Object {{ $baselinePids -notcontains $_ }})
  $hostWitness = [ordered]@{{
    schema = 'RhinoCadHostWitness@1'
    completion_token = $completionToken
    ownership_status = 'ambiguous'
    new_pid_count = $newPids.Count
    pid = $null
    executable_path = $null
    start_time_utc_ticks = $null
  }}
  if ($newPids.Count -eq 1) {{
    try {{
      $owned = Get-Process -Id ([int]$newPids[0]) -ErrorAction Stop
      $hostWitness.pid = [int]$owned.Id
      $hostWitness.executable_path = [string]$owned.Path
      $hostWitness.start_time_utc_ticks = [int64]$owned.StartTime.ToUniversalTime().Ticks
      if (-not [string]::IsNullOrWhiteSpace($hostWitness.executable_path) -and
          $hostWitness.start_time_utc_ticks -gt 0) {{
        $hostWitness.ownership_status = 'exact'
      }}
    }} catch {{}}
  }}
  Write-ExclusiveUtf8Json $hostWitnessPath $hostWitness
  $result.ownership_status = $hostWitness.ownership_status
  if ($hostWitness.ownership_status -ne 'exact') {{
    throw 'RHINO_PID_OWNERSHIP_AMBIGUOUS'
  }}
  $macro = '-_RunPythonScript (' + $scriptPath + ')'
  $null = $rhino.RunScript($macro, 0)
}} catch {{
  $result.error_code = 'cad_execution.com_bridge_failed'
  $detail = [string]$_.Exception.Message
  if ($detail.Length -gt 2000) {{ $detail = $detail.Substring(0, 2000) }}
  $result.error_detail = $detail
}} finally {{
  if ($null -ne $rhino) {{
    try {{
      $null = [Runtime.InteropServices.Marshal]::FinalReleaseComObject($rhino)
    }} catch {{}}
    $rhino = $null
  }}
  $result | ConvertTo-Json -Compress -Depth 4
}}
if ($null -ne $result.error_code) {{ exit 1 }}
exit 0
"""


def build_rhino_com_powershell_command(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float,
) -> tuple[str, ...]:
    """Encode the COM bridge so no shell interpolation or launcher file exists."""

    executable = _powershell_executable(powershell_executable)
    source = build_rhino_com_powershell_source(
        plan,
        timeout_seconds=timeout_seconds,
    )
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return (
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-EncodedCommand",
        encoded,
    )


def _build_rhino_cleanup_powershell_source(
    host_witness: Mapping[str, object],
) -> str:
    pid = int(host_witness["pid"])
    executable_path = str(host_witness["executable_path"])
    start_ticks = int(host_witness["start_time_utc_ticks"])
    token = str(host_witness["completion_token"])
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$expectedPid = [int]{pid}
$expectedPath = {_powershell_literal(executable_path)}
$expectedTicks = [int64]{start_ticks}
$completionToken = {_powershell_literal(token)}
$result = [ordered]@{{
  schema = 'RhinoCadCleanupWitness@1'
  completion_token = $completionToken
  pid = $expectedPid
  status = 'cleanup_unverified'
  identity_matched = $false
  stop_requested = $false
  cleanup_confirmed = $false
  error_detail = $null
}}
try {{
  $process = Get-Process -Id $expectedPid -ErrorAction SilentlyContinue
  if ($null -eq $process) {{
    $result.status = 'already_exited'
    $result.cleanup_confirmed = $true
  }} else {{
    $actualPath = [string]$process.Path
    $actualTicks = [int64]$process.StartTime.ToUniversalTime().Ticks
    if ($actualPath -ieq $expectedPath -and $actualTicks -eq $expectedTicks) {{
      $result.identity_matched = $true
      $result.stop_requested = $true
      Stop-Process -Id $expectedPid -Force -ErrorAction Stop
      $deadline = [DateTime]::UtcNow.AddSeconds(3.0)
      while ((Get-Process -Id $expectedPid -ErrorAction SilentlyContinue) -and
             [DateTime]::UtcNow -lt $deadline) {{
        Start-Sleep -Milliseconds 50
      }}
      if (-not (Get-Process -Id $expectedPid -ErrorAction SilentlyContinue)) {{
        $result.status = 'stopped'
        $result.cleanup_confirmed = $true
      }}
    }} else {{
      $result.status = 'identity_mismatch'
    }}
  }}
}} catch {{
  $detail = [string]$_.Exception.Message
  if ($detail.Length -gt 1000) {{ $detail = $detail.Substring(0, 1000) }}
  $result.error_detail = $detail
}}
$result | ConvertTo-Json -Compress -Depth 3
if ($result.cleanup_confirmed) {{ exit 0 }}
exit 1
"""


def _build_rhino_cleanup_powershell_command(
    *,
    powershell_executable: Path,
    host_witness: Mapping[str, object],
) -> tuple[str, ...]:
    executable = _powershell_executable(powershell_executable)
    source = _build_rhino_cleanup_powershell_source(host_witness)
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return (
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-EncodedCommand",
        encoded,
    )


#: One Rhino host at a time in this process. A candidate's export and an
#: explicitly requested export are the same machine resource, so they take
#: the same lock here rather than each trusting its own caller's queue: a
#: queue orders the callers it knows about, and this orders every caller.
_RHINO_HOST_LOCK = threading.Lock()


def execute_rhino_three_dm_export(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., object] | None = None,
    cleanup_runner: Callable[..., object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    host_wait_seconds: float = 0.0,
) -> RhinoCadExecutionReceipt:
    """Supervise a blocking COM worker, then independently clean its Rhino.

    Only one export drives the host at a time. ``host_wait_seconds`` is how
    long this call waits for the one already running; the default waits not
    at all and answers ``cad_execution.host_busy`` rather than queueing
    invisibly behind work the caller cannot see.
    """

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    wait = _positive_finite(host_wait_seconds, "host_wait_seconds") if host_wait_seconds else 0.0
    acquired = (
        _RHINO_HOST_LOCK.acquire(timeout=wait) if wait else _RHINO_HOST_LOCK.acquire(blocking=False)
    )
    if not acquired:
        return _failure_receipt(
            plan,
            _powershell_executable(powershell_executable).name,
            "cad_execution.host_busy",
            "another export is driving this machine's Rhino; this one was not started",
        )
    try:
        return _execute_rhino_three_dm_export(
            plan,
            powershell_executable=powershell_executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
            cleanup_runner=cleanup_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
    finally:
        _RHINO_HOST_LOCK.release()


def _execute_rhino_three_dm_export(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., object] | None = None,
    cleanup_runner: Callable[..., object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> RhinoCadExecutionReceipt:
    """The supervision itself, with the host already held by the caller."""

    timeout = _positive_finite(timeout_seconds, "timeout_seconds")
    executable = _powershell_executable(powershell_executable)
    try:
        _validate_plan_denominator(plan)
    except CadExecutionError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.validation_denominator_tampered",
            str(exc),
        )
    try:
        _validate_plan_paths(plan, require_script=True)
    except (CadExecutionError, OSError) as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.path_boundary_failed",
            str(exc),
        )
    try:
        current_script_sha = _sha256_bytes(long_path(plan.script_path).read_bytes())
    except OSError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.script_unreadable",
            str(exc),
        )
    if current_script_sha != plan.script_sha256:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.script_tampered",
            "prepared script SHA-256 changed before execution",
        )
    if plan.model_path.exists() or plan.model_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.output_exists",
            "3dm target must not exist before execution",
        )
    if plan.completion_marker_path.exists() or plan.completion_marker_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.completion_marker_exists",
            "completion marker must not exist before execution",
        )
    if plan.host_witness_path.exists() or plan.host_witness_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_witness_exists",
            "host witness must not exist before execution",
        )
    command = build_rhino_com_powershell_command(
        plan,
        powershell_executable=executable,
        timeout_seconds=timeout,
    )
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW
    factory = subprocess.Popen if runner is None else runner
    try:
        worker = factory(
            command,
            cwd=plan.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except OSError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.process_error",
            str(exc),
        )
    clock = time.monotonic if monotonic is None else monotonic
    pause = time.sleep if sleeper is None else sleeper
    deadline = clock() + timeout
    timed_out = False
    completion_observed = False
    marker_failure: dict[str, str] | None = None
    host_error: dict[str, str] | None = None
    host_witness: dict[str, object] | None = None
    host_witness_sha256: str | None = None
    while True:
        worker_exit = worker.poll()
        if plan.host_witness_path.exists():
            host_witness, host_witness_sha256, host_error = _read_host_witness(plan)
            if host_error is not None and (
                host_error["code"]
                in (
                    "cad_execution.host_witness_unreadable",
                    "cad_execution.host_witness_malformed",
                )
                and worker_exit is None
            ):
                host_error = None
            elif host_error is not None or (
                host_witness is not None
                and host_witness["ownership_status"] != "exact"
            ):
                break
        if plan.completion_marker_path.exists():
            marker_failure = _read_completion_marker(plan)
            if marker_failure is None or marker_failure["code"] == (
                "cad_execution.rhino_script_failed"
            ):
                completion_observed = True
                break
            if not (
                marker_failure["code"]
                in (
                    "cad_execution.completion_marker_unreadable",
                    "cad_execution.completion_marker_malformed",
                )
                and worker_exit is None
            ):
                break
            marker_failure = None
        if worker_exit is not None:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            timed_out = True
            break
        pause(min(0.1, remaining))
    _terminate_spawned_worker(worker)
    stdout, stderr = _collect_worker_output(worker)
    exit_code = getattr(worker, "returncode", None)
    if exit_code is not None:
        exit_code = int(exit_code)
    if host_witness is None and host_error is None:
        host_witness, host_witness_sha256, host_error = _read_host_witness(plan)
    cleanup_status = "cleanup_unverified"
    cleanup_witness_sha256 = None
    cleanup_detail = "host ownership was not exact"
    if (
        host_error is None
        and host_witness is not None
        and host_witness["ownership_status"] == "exact"
    ):
        (
            cleanup_status,
            cleanup_witness_sha256,
            cleanup_detail,
        ) = _run_exact_rhino_cleanup(
            powershell_executable=executable,
            host_witness=host_witness,
            runner=cleanup_runner,
        )
    common_receipt = {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "host_witness_sha256": host_witness_sha256,
        "cleanup_witness_sha256": cleanup_witness_sha256,
        "cleanup_status": cleanup_status,
    }
    if host_error is not None:
        return _failure_receipt(
            plan,
            executable.name,
            host_error["code"],
            host_error["detail"] + "; cleanup=" + cleanup_detail,
            **common_receipt,
        )
    if host_witness is None:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_witness_missing",
            "PowerShell worker did not produce an exact host witness",
            **common_receipt,
        )
    if host_witness["ownership_status"] != "exact":
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_ownership_ambiguous",
            "Rhino PID set difference was not exactly one; geometry was not run",
            **common_receipt,
        )
    if timed_out and not completion_observed:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.timeout",
            "Rhino geometry did not produce a completion marker before deadline; "
            "cleanup=" + cleanup_detail,
            **common_receipt,
        )
    if marker_failure is None and not completion_observed:
        marker_failure = _read_completion_marker(plan)
    if marker_failure is not None:
        return _failure_receipt(
            plan,
            executable.name,
            marker_failure["code"],
            _with_com_diagnostics(marker_failure["detail"], stdout),
            completion_marker_sha256=marker_failure.get("marker_sha256"),
            **common_receipt,
        )
    if cleanup_status != "confirmed":
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.cleanup_unverified",
            cleanup_detail,
            completion_marker_sha256=plan.completion_witness_sha256,
            **common_receipt,
        )
    try:
        _strict_child(plan.workspace, plan.model_path, require_exists=True)
        inspection = inspect_three_dm(plan.model_path)
    except (CadExecutionError, ThreeDmInspectionError, OSError) as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.readback_failed",
            _with_com_diagnostics(str(exc), stdout),
            completion_marker_sha256=plan.completion_witness_sha256,
            **common_receipt,
        )
    return verify_rhino_export_readback(
        plan,
        inspection,
        executable_name=executable.name,
        process_exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        completion_marker_sha256=plan.completion_witness_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
    )


def _rhino_semantic_failures(semantics, inspection, expected_counts) -> list[dict[str, str]]:
    """Compare actual Rhino object/instance witnesses with the requested semantics."""
    failures = []
    expected_total = sum(expected_counts.values())
    semantic_objects = semantics["objects"]
    expected_by_op: dict[str, tuple[str, dict[str, str]]] = {}
    for object_id, row in semantic_objects.items():
        user_text = dict(row["user_text"])
        producer = user_text["archflow:producer_op"]
        if producer in expected_by_op:
            failures.append(
                _failure(
                    "cad_execution.ambiguous_producer",
                    f"producer {producer} names more than one physical object",
                )
            )
        expected_by_op[producer] = (object_id, user_text)

    definition_members = {
        object_id
        for definition in inspection.instance_definitions
        for object_id in definition["object_ids"]
    }
    observed_by_op: dict[str, list[dict[str, object]]] = {}
    tagged_top_level = 0
    for row in inspection.object_user_strings:
        if row["object_id"] in definition_members:
            continue
        attribute_pairs = tuple(
            sorted(
                (pair["key"], pair["value"])
                for pair in row.get("attributes", ())
                if pair["key"].startswith("archflow:")
            )
        )
        attributes = dict(attribute_pairs)
        producer = attributes.get("archflow:producer_op")
        if producer is None:
            continue
        tagged_top_level += 1
        geometry_archflow = tuple(
            sorted(
                (pair["key"], pair["value"])
                for pair in row.get("geometry", ())
                if pair["key"].startswith("archflow:")
            )
        )
        observed_by_op.setdefault(producer, []).append(
            {
                "name": row.get("name"),
                "layer_path": row.get("layer_path"),
                "attribute_pairs": attribute_pairs,
                "geometry_archflow": geometry_archflow,
            }
        )
    if tagged_top_level != expected_total:
        failures.append(
            _failure(
                "cad_execution.semantic_witness_count_mismatch",
                "not every top-level object has one semantic witness",
            )
        )
    if set(observed_by_op) != set(expected_by_op):
        failures.append(
            _failure(
                "cad_execution.physical_identity_mismatch",
                "producer operation set differs from physical denominator",
            )
        )
    for producer, (object_id, expected_text) in expected_by_op.items():
        rows = observed_by_op.get(producer, [])
        expected_semantic = semantic_objects[object_id]
        if len(rows) != expected_counts[object_id]:
            failures.append(
                _failure(
                    "cad_execution.physical_multiplicity_mismatch",
                    f"object {object_id} has wrong persisted multiplicity",
                )
            )
        expected_pairs = tuple(sorted(expected_text.items()))
        if any(
            row["attribute_pairs"] != expected_pairs
            or row["geometry_archflow"]
            for row in rows
        ):
            failures.append(
                _failure(
                    "cad_execution.semantic_user_text_mismatch",
                    f"object {object_id} semantic user text differs",
                )
            )
        if any(row["name"] != expected_semantic["name"] for row in rows):
            failures.append(
                _failure(
                    "cad_execution.object_name_mismatch",
                    f"object {object_id} CAD name differs from physical id",
                )
            )
        if any(row["layer_path"] != expected_semantic["layer"] for row in rows):
            failures.append(
                _failure(
                    "cad_execution.object_layer_mismatch",
                    f"object {object_id} layer differs from semantic contract",
                )
            )

    expected_blocks = dict(semantics["blocks"])
    actual_blocks = {
        item["name"]: int(item["reference_count"])
        for item in inspection.instance_definitions
    }
    if actual_blocks != expected_blocks:
        failures.append(
            _failure(
                "cad_execution.block_mismatch",
                "block definitions or reference counts differ",
            )
        )
    expected_instance_total = sum(expected_blocks.values())
    if len(inspection.instance_references) != expected_instance_total:
        failures.append(
            _failure(
                "cad_execution.block_reference_mismatch",
                "instance reference denominator differs",
            )
        )
    if inspection.object_count != (
        inspection.top_level_object_count
        + inspection.instance_definition_member_count
    ):
        failures.append(
            _failure(
                "cad_execution.inspection_count_inconsistent",
                "3dm object accounting is internally inconsistent",
            )
        )

    return failures


def verify_rhino_export_readback(
    plan: RhinoCadExportPlan,
    inspection: ThreeDmInspection,
    *,
    executable_name: str,
    process_exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    completion_marker_sha256: str | None = None,
    host_witness_sha256: str | None = None,
    cleanup_witness_sha256: str | None = None,
    cleanup_status: str = "not_performed",
) -> RhinoCadExecutionReceipt:
    """Validate the full persisted physical, semantic, and bounds denominator."""

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    if not isinstance(inspection, ThreeDmInspection):
        raise TypeError("inspection must be ThreeDmInspection")
    try:
        _validate_plan_denominator(plan)
    except CadExecutionError as exc:
        return _failure_receipt(
            plan,
            executable_name,
            "cad_execution.validation_denominator_tampered",
            str(exc),
            exit_code=process_exit_code,
            stdout=stdout,
            stderr=stderr,
            completion_marker_sha256=completion_marker_sha256,
            host_witness_sha256=host_witness_sha256,
            cleanup_witness_sha256=cleanup_witness_sha256,
            cleanup_status=cleanup_status,
        )
    failures: list[dict[str, str]] = []
    if completion_marker_sha256 != plan.completion_witness_sha256:
        failures.append(
            _failure(
                "cad_execution.completion_witness_mismatch",
                "exact successful completion marker was not witnessed",
            )
        )
    if host_witness_sha256 is None:
        failures.append(
            _failure(
                "cad_execution.host_witness_missing",
                "exact Rhino host witness was not supplied",
            )
        )
    if cleanup_witness_sha256 is None or cleanup_status != "confirmed":
        failures.append(
            _failure(
                "cad_execution.cleanup_unverified",
                "independent exact-host cleanup was not confirmed",
            )
        )
    if not inspection.read_only or inspection.rhino_process_started:
        failures.append(
            _failure(
                "cad_execution.inspector_not_read_only",
                "verification must be independent and read-only",
            )
        )
    actual_archflow_document = tuple(
        sorted(
            (row["key"], row["value"])
            for row in inspection.document_user_strings
            if row["key"].startswith("archflow:")
        )
    )
    if actual_archflow_document != plan.expected_document_user_text:
        failures.append(
            _failure(
                "cad_execution.provenance_mismatch",
                "archflow document user-text key/value set differs from plan",
            )
        )
    expected_unit = _UNIT_TO_RHINO[plan.identity.length_unit][1]
    if inspection.units.get("name") != expected_unit:
        failures.append(
            _failure("cad_execution.unit_mismatch", "saved model units differ")
        )
    expected_layers = dict(plan.expected_layer_colors)
    actual_layers: dict[str, list[dict[str, object]]] = {}
    for row in inspection.layers:
        full_path = row.get("full_path")
        if isinstance(full_path, str) and (
            full_path == "archflow" or full_path.startswith("archflow::")
        ):
            actual_layers.setdefault(full_path, []).append(row)
    if set(actual_layers) != set(expected_layers) or any(
        len(rows) != 1 for rows in actual_layers.values()
    ):
        failures.append(
            _failure(
                "cad_execution.layer_set_mismatch",
                "archflow layer full-path set has missing, duplicate, or extra layers",
            )
        )
    for full_path in sorted(set(expected_layers) & set(actual_layers)):
        if len(actual_layers[full_path]) != 1:
            continue
        actual_rgba = actual_layers[full_path][0].get("color_rgba")
        expected_rgba = [*expected_layers[full_path], 255]
        if actual_rgba != expected_rgba:
            failures.append(
                _failure(
                    "cad_execution.layer_color_mismatch",
                    f"layer {full_path} RGBA differs from translation contract",
                )
            )

    expected_counts = dict(plan.expected_object_counts)
    expected_total = sum(expected_counts.values())
    if inspection.top_level_object_count != expected_total:
        failures.append(
            _failure(
                "cad_execution.object_count_mismatch",
                "top-level object count differs from physical denominator",
            )
        )
    failures.extend(_rhino_semantic_failures(plan.expected_semantics, inspection, expected_counts))

    visible_witnesses = tuple(inspection.visible_bounds_witnesses)
    expected_leaf_witness_count = (
        inspection.top_level_object_count
        - len(inspection.instance_references)
        + inspection.instance_definition_member_count
    )
    witness_ids = [str(row.get("object_id", "")) for row in visible_witnesses]
    if (
        len(visible_witnesses) != expected_leaf_witness_count
        or len(set(witness_ids)) != len(witness_ids)
    ):
        failures.append(
            _failure(
                "cad_execution.visible_bounds_witness_count_mismatch",
                "saved leaf geometry does not have one unique visible-bounds witness",
            )
        )
    for row in visible_witnesses:
        geometry_type = row.get("type")
        source = row.get("source")
        if geometry_type == "Brep" and (
            source
            not in {
                "brep_face_render_mesh_vertices",
                "explicit_trimmed_render_mesh_witnesses",
            }
            or not isinstance(row.get("mesh_face_count"), int)
            or int(row["mesh_face_count"]) <= 0
            or not isinstance(row.get("mesh_vertex_count"), int)
            or int(row["mesh_vertex_count"]) <= 0
        ):
            failures.append(
                _failure(
                    "cad_execution.visible_bounds_witness_invalid",
                    "Brep bounds are not backed by retained face render-mesh vertices",
                )
            )
        if geometry_type == "Extrusion" and source not in {
            "extrusion_render_mesh_vertices",
            "explicit_trimmed_render_mesh_witnesses",
        }:
            failures.append(
                _failure(
                    "cad_execution.visible_bounds_witness_invalid",
                    "Extrusion bounds are not backed by a retained render mesh",
                )
            )

    expected_blocks = dict(plan.expected_semantics["blocks"])
    array_ops = {
        name.removeprefix("archflow-family-") for name in expected_blocks
    }
    direct_ids = {
        object_id
        for object_id, row in plan.expected_semantics["objects"].items()
        if row["user_text"]["archflow:producer_op"] not in array_ops
    }
    named_rows: dict[str, list[dict[str, object]]] = {}
    for row in inspection.named_object_bboxes:
        named_rows.setdefault(str(row["name"]), []).append(row)
    expected_named_counts = dict(plan.expected_object_counts)
    if set(named_rows) != direct_ids or any(
        len(rows) != expected_named_counts.get(object_id, 1)
        for object_id, rows in named_rows.items()
    ):
        failures.append(
            _failure(
                "cad_execution.named_object_mismatch",
                "named direct-object set has missing, duplicate, or extra identities",
            )
        )
    for object_id in sorted(direct_ids & set(named_rows)):
        named_row = named_rows[object_id][0]
        if named_row.get("type") == "Brep" and named_row.get(
            "bbox_source"
        ) not in {
            "brep_face_render_mesh_vertices",
            "explicit_trimmed_render_mesh_witnesses",
        }:
            failures.append(
                _failure(
                    "cad_execution.named_bounds_witness_invalid",
                    f"object {object_id} bounds do not use retained trimmed mesh vertices",
                )
            )
        actual_bbox = named_row["bbox"]
        expected_bbox = plan.expected_bounds[object_id]
        if not _bbox_close(
            actual_bbox,
            expected_bbox,
            plan.readback_tolerance,
        ):
            failures.append(
                _failure(
                    "cad_execution.named_bounds_mismatch",
                    f"object {object_id} bounds differ",
                )
            )
    expected_aggregate = _aggregate_bounds(plan.expected_bounds.values())
    if inspection.aggregate_bbox is None or not _bbox_close(
        inspection.aggregate_bbox,
        expected_aggregate,
        plan.readback_tolerance,
    ):
        failures.append(
            _failure(
                "cad_execution.aggregate_bounds_mismatch",
                "aggregate saved-model bounds differ",
            )
        )

    return RhinoCadExecutionReceipt(
        status=CadExecutionStatus.FAILED if failures else CadExecutionStatus.SUCCEEDED,
        identity=plan.identity,
        adapter_id="rhino-com-powershell",
        executable_name=executable_name,
        plan_digest=plan.plan_digest,
        script_sha256=plan.script_sha256,
        artifact_relative_path=plan.artifact_relative_path,
        completion_marker_relative_path=plan.completion_marker_relative_path,
        host_witness_relative_path=plan.host_witness_relative_path,
        completion_token=plan.completion_token,
        completion_witness_sha256=plan.completion_witness_sha256,
        completion_marker_sha256=completion_marker_sha256,
        validation_denominator_sha256=plan.validation_denominator_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
        process_exit_code=process_exit_code,
        stdout_sha256=_sha256_text(stdout),
        stderr_sha256=_sha256_text(stderr),
        inspection=inspection.to_dict(),
        failures=tuple(failures),
    )


def _completion_token(
    *,
    identity: RhinoCadExportIdentity,
    artifact_relative_path: str,
    translation_sha256: str,
    validation_denominator_sha256: str,
) -> str:
    return canonical_digest(
        {
            "schema": "RhinoCadCompletionToken@1",
            "identity": identity.to_dict(),
            "artifact_relative_path": artifact_relative_path,
            "translation_sha256": translation_sha256,
            "validation_denominator_sha256": validation_denominator_sha256,
        }
    )


def _validation_denominator_digest(
    *,
    identity: RhinoCadExportIdentity,
    translation_sha256: str,
    expected_layer_colors: tuple[tuple[str, tuple[int, int, int]], ...],
    physical_object_ids: tuple[str, ...],
    expected_document_user_text: tuple[tuple[str, str], ...],
    expected_semantics: Mapping[str, object],
    expected_bounds: Mapping[str, object],
    expected_object_counts: tuple[tuple[str, int], ...],
    readback_tolerance: float,
) -> str:
    return canonical_digest(
        {
            "schema": "RhinoCadValidationDenominator@1",
            "identity": identity.to_dict(),
            "translation_sha256": translation_sha256,
            "expected_layer_colors": [
                {"full_path": path, "rgb": list(color)}
                for path, color in expected_layer_colors
            ],
            "physical_object_ids": list(physical_object_ids),
            "expected_document_user_text": [
                {"key": key, "value": value}
                for key, value in expected_document_user_text
            ],
            "expected_semantics": expected_semantics,
            "expected_bounds": expected_bounds,
            "expected_object_counts": [
                {"object_id": object_id, "count": count}
                for object_id, count in expected_object_counts
            ],
            "readback_tolerance": readback_tolerance,
        }
    )


def _validate_plan_denominator(plan: RhinoCadExportPlan) -> None:
    actual = _validation_denominator_digest(
        identity=plan.identity,
        translation_sha256=plan.translation_sha256,
        expected_layer_colors=plan.expected_layer_colors,
        physical_object_ids=plan.physical_object_ids,
        expected_document_user_text=plan.expected_document_user_text,
        expected_semantics=plan.expected_semantics,
        expected_bounds=plan.expected_bounds,
        expected_object_counts=plan.expected_object_counts,
        readback_tolerance=plan.readback_tolerance,
    )
    if actual != plan.validation_denominator_sha256:
        raise CadExecutionError(
            "canonical validation denominator changed after plan preparation"
        )


def _marker_text(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_completion_marker(
    plan: RhinoCadExportPlan,
) -> dict[str, str] | None:
    """Return ``None`` only for the exact successful marker witness."""

    if not long_path(plan.completion_marker_path).exists():
        return _failure(
            "cad_execution.completion_marker_missing",
            "Rhino COM command returned without a completion marker",
        )
    try:
        _strict_child(
            plan.workspace,
            plan.completion_marker_path,
            require_exists=True,
        )
        marker_bytes = long_path(plan.completion_marker_path).read_bytes()
    except (CadExecutionError, OSError) as exc:
        return _failure("cad_execution.completion_marker_unreadable", str(exc))
    marker_sha256 = _sha256_bytes(marker_bytes)
    if len(marker_bytes) > 8_192:
        result = _failure(
            "cad_execution.completion_marker_malformed",
            "completion marker exceeds bounded size",
        )
        result["marker_sha256"] = marker_sha256
        return result
    try:
        payload = json.loads(marker_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result = _failure("cad_execution.completion_marker_malformed", str(exc))
        result["marker_sha256"] = marker_sha256
        return result
    if not isinstance(payload, dict):
        result = _failure(
            "cad_execution.completion_marker_malformed",
            "completion marker must be a JSON object",
        )
        result["marker_sha256"] = marker_sha256
        return result
    shared = {
        "schema": "RhinoCadCompletionMarker@1",
        "artifact_relative_path": plan.artifact_relative_path,
        "completion_token": plan.completion_token,
    }
    if any(payload.get(key) != value for key, value in shared.items()):
        result = _failure(
            "cad_execution.completion_marker_mismatch",
            "completion marker does not bind the exact export plan",
        )
        result["marker_sha256"] = marker_sha256
        return result
    if payload.get("status") == "failed":
        allowed = {*shared, "status", "error_type", "error_detail"}
        if set(payload) != allowed or any(
            not isinstance(payload.get(key), str)
            for key in ("error_type", "error_detail")
        ):
            result = _failure(
                "cad_execution.completion_marker_malformed",
                "failure marker schema drifted",
            )
        else:
            result = _failure(
                "cad_execution.rhino_script_failed",
                f"{str(payload['error_type'])[:128]}: "
                f"{str(payload['error_detail'])[:1000]}",
            )
        result["marker_sha256"] = marker_sha256
        return result
    expected = _completion_marker_payload(
        artifact_relative_path=plan.artifact_relative_path,
        completion_token=plan.completion_token,
        status="succeeded",
    )
    if payload != expected or marker_sha256 != plan.completion_witness_sha256:
        result = _failure(
            "cad_execution.completion_marker_mismatch",
            "successful marker differs from the exact digest witness",
        )
        result["marker_sha256"] = marker_sha256
        return result
    return None


def _read_host_witness(
    plan: RhinoCadExportPlan,
) -> tuple[dict[str, object] | None, str | None, dict[str, str] | None]:
    if not long_path(plan.host_witness_path).exists():
        return (
            None,
            None,
            _failure(
                "cad_execution.host_witness_missing",
                "PowerShell worker did not create a host witness",
            ),
        )
    try:
        _strict_child(plan.workspace, plan.host_witness_path, require_exists=True)
        witness_bytes = long_path(plan.host_witness_path).read_bytes()
    except (CadExecutionError, OSError) as exc:
        return (
            None,
            None,
            _failure("cad_execution.host_witness_unreadable", str(exc)),
        )
    witness_sha256 = _sha256_bytes(witness_bytes)
    if len(witness_bytes) > 8_192:
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "host witness exceeds bounded size",
            ),
        )
    try:
        payload = json.loads(witness_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return (
            None,
            witness_sha256,
            _failure("cad_execution.host_witness_malformed", str(exc)),
        )
    expected_keys = {
        "schema",
        "completion_token",
        "ownership_status",
        "new_pid_count",
        "pid",
        "executable_path",
        "start_time_utc_ticks",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "host witness schema drifted",
            ),
        )
    if (
        payload["schema"] != "RhinoCadHostWitness@1"
        or payload["completion_token"] != plan.completion_token
        or payload["ownership_status"] not in ("exact", "ambiguous")
        or not isinstance(payload["new_pid_count"], int)
        or isinstance(payload["new_pid_count"], bool)
        or payload["new_pid_count"] < 0
    ):
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_mismatch",
                "host witness does not bind the exact execution plan",
            ),
        )
    if payload["ownership_status"] == "ambiguous":
        if any(
            payload[field] is not None
            for field in ("pid", "executable_path", "start_time_utc_ticks")
        ):
            return (
                None,
                witness_sha256,
                _failure(
                    "cad_execution.host_witness_malformed",
                    "ambiguous witness cannot assert a Rhino identity",
                ),
            )
        return _json_copy(payload), witness_sha256, None
    executable_path = payload["executable_path"]
    if (
        payload["new_pid_count"] != 1
        or not isinstance(payload["pid"], int)
        or isinstance(payload["pid"], bool)
        or payload["pid"] <= 0
        or not isinstance(executable_path, str)
        or any(character in executable_path for character in "\x00\r\n")
        or not PureWindowsPath(executable_path).is_absolute()
        or PureWindowsPath(executable_path).name.lower() != "rhino.exe"
        or not isinstance(payload["start_time_utc_ticks"], int)
        or isinstance(payload["start_time_utc_ticks"], bool)
        or payload["start_time_utc_ticks"] <= 0
    ):
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "exact host witness lacks PID/path/start-time identity",
            ),
        )
    return _json_copy(payload), witness_sha256, None


def _terminate_spawned_worker(worker: object) -> None:
    try:
        if worker.poll() is not None:
            return
        worker.terminate()
        try:
            worker.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=2.0)
    except (OSError, ProcessLookupError, AttributeError):
        return


def _collect_worker_output(worker: object) -> tuple[str, str]:
    try:
        stdout, stderr = worker.communicate(timeout=1.0)
    except (subprocess.TimeoutExpired, AttributeError):
        stdout = getattr(worker, "stdout_text", "")
        stderr = getattr(worker, "stderr_text", "")
    return str(stdout or "")[:8_192], str(stderr or "")[:8_192]


def _run_exact_rhino_cleanup(
    *,
    powershell_executable: Path,
    host_witness: Mapping[str, object],
    runner: Callable[..., object] | None,
) -> tuple[str, str | None, str]:
    command = _build_rhino_cleanup_powershell_command(
        powershell_executable=powershell_executable,
        host_witness=host_witness,
    )
    execute = subprocess.run if runner is None else runner
    try:
        completed = execute(
            command,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "cleanup_unverified", None, str(exc)[:1_000]
    stdout = str(getattr(completed, "stdout", "") or "")
    if len(stdout.encode("utf-8", errors="replace")) > 8_192:
        return "cleanup_unverified", None, "cleanup JSON exceeds bounded size"
    try:
        payload = json.loads(stdout.strip())
    except (json.JSONDecodeError, UnicodeError) as exc:
        return "cleanup_unverified", None, ("invalid cleanup JSON: " + str(exc))[:1_000]
    expected_keys = {
        "schema",
        "completion_token",
        "pid",
        "status",
        "identity_matched",
        "stop_requested",
        "cleanup_confirmed",
        "error_detail",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return "cleanup_unverified", None, "cleanup witness schema drifted"
    witness_sha256 = _sha256_text(_marker_text(payload))
    valid = (
        payload["schema"] == "RhinoCadCleanupWitness@1"
        and payload["completion_token"] == host_witness["completion_token"]
        and payload["pid"] == host_witness["pid"]
        and payload["status"] in (
            "already_exited",
            "stopped",
            "identity_mismatch",
            "cleanup_unverified",
        )
        and isinstance(payload["identity_matched"], bool)
        and isinstance(payload["stop_requested"], bool)
        and isinstance(payload["cleanup_confirmed"], bool)
        and (payload["error_detail"] is None or isinstance(payload["error_detail"], str))
    )
    if not valid:
        return "cleanup_unverified", witness_sha256, "cleanup witness values drifted"
    returncode = int(getattr(completed, "returncode", 1))
    if (
        returncode == 0
        and payload["cleanup_confirmed"] is True
        and payload["status"] in ("already_exited", "stopped")
        and (
            payload["status"] == "already_exited"
            or (
                payload["identity_matched"] is True
                and payload["stop_requested"] is True
            )
        )
    ):
        return "confirmed", witness_sha256, str(payload["status"])
    detail = payload["error_detail"] or str(payload["status"])
    return "cleanup_unverified", witness_sha256, str(detail)[:1_000]


def _validate_plan_paths(plan: RhinoCadExportPlan, *, require_script: bool) -> None:
    root = _strict_workspace(plan.workspace)
    _strict_child(root, plan.script_path, require_exists=require_script)
    _strict_child(root, plan.model_path, require_exists=False)
    _strict_child(root, plan.completion_marker_path, require_exists=False)
    _strict_child(root, plan.host_witness_path, require_exists=False)
    if plan.script_path.name != f"{plan.model_path.stem}.archflow.py":
        raise CadExecutionError("script/model plan names are inconsistent")
    if plan.completion_marker_path.name != (
        f"{plan.model_path.stem}.archflow-completion.json"
    ):
        raise CadExecutionError("completion marker/model plan names are inconsistent")
    if plan.host_witness_path.name != f"{plan.model_path.stem}.archflow-host.json":
        raise CadExecutionError("host witness/model plan names are inconsistent")
    _artifact_name(plan.model_path.name)


def _artifact_name(value: str) -> str:
    _portable_relative_path(value)
    path = PurePosixPath(value)
    if len(path.parts) != 1 or path.suffix.lower() != ".3dm":
        raise CadExecutionError("artifact_name must be one portable .3dm filename")
    return value


def _powershell_executable(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("powershell_executable must be pathlib.Path")
    if value.is_symlink():
        raise CadExecutionError("powershell_executable cannot be a symlink")
    resolved = value.resolve(strict=True)
    if not resolved.is_file() or resolved.name.lower() not in (
        "powershell.exe",
        "pwsh.exe",
    ):
        raise CadExecutionError(
            "powershell_executable must name an existing PowerShell executable"
        )
    return resolved


def _powershell_literal(value: str) -> str:
    if not isinstance(value, str) or any(character in value for character in "\x00\r\n"):
        raise CadExecutionError("PowerShell literal contains unsafe characters")
    return "'" + value.replace("'", "''") + "'"


def _with_com_diagnostics(detail: str, stdout: str) -> str:
    """Append only bounded, allow-listed fields from the bridge JSON result."""

    if not isinstance(stdout, str) or not stdout:
        return detail
    payload = None
    for line in reversed(stdout[-8_192:].splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, dict)
            and candidate.get("schema") == "RhinoComPowerShellWorkerResult@4"
        ):
            payload = candidate
            break
    if payload is None:
        return detail
    error_detail = payload.get("error_detail")
    ownership_status = payload.get("ownership_status")
    parts: list[str] = []
    if isinstance(error_detail, str) and error_detail:
        parts.append("com_error=" + error_detail[:1_000])
    if ownership_status in ("not_observed", "exact", "ambiguous"):
        parts.append("ownership=" + str(ownership_status))
    if not parts:
        return detail
    return (detail + "; " + "; ".join(parts))[:_MAX_PROCESS_TEXT]


def _failure_receipt(
    plan: RhinoCadExportPlan,
    executable_name: str,
    code: str,
    detail: str,
    *,
    exit_code: int | None = None,
    stdout: object = "",
    stderr: object = "",
    completion_marker_sha256: str | None = None,
    host_witness_sha256: str | None = None,
    cleanup_witness_sha256: str | None = None,
    cleanup_status: str = "not_performed",
) -> RhinoCadExecutionReceipt:
    return RhinoCadExecutionReceipt(
        status=CadExecutionStatus.FAILED,
        identity=plan.identity,
        adapter_id="rhino-com-powershell",
        executable_name=executable_name,
        plan_digest=plan.plan_digest,
        script_sha256=plan.script_sha256,
        artifact_relative_path=plan.artifact_relative_path,
        completion_marker_relative_path=plan.completion_marker_relative_path,
        host_witness_relative_path=plan.host_witness_relative_path,
        completion_token=plan.completion_token,
        completion_witness_sha256=plan.completion_witness_sha256,
        completion_marker_sha256=completion_marker_sha256,
        validation_denominator_sha256=plan.validation_denominator_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
        process_exit_code=exit_code,
        stdout_sha256=_sha256_text("" if stdout is None else str(stdout)),
        stderr_sha256=_sha256_text("" if stderr is None else str(stderr)),
        inspection=None,
        failures=(_failure(code, detail),),
    )
