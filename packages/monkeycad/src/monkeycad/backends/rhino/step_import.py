"""Splitting an exported STEP into single-object files for an import-based Rhino work model, and checking the saved model.

An editable work model is not rebuilt: the host reads back the exact STEP
this program already exported, one named shape per file, and the reader
checks the saved document against those shapes. Each attempt gets a numbered
directory inside the caller's export workspace.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from archflow.state.geometry_program import require_sha256
from monkeycad.backends.occt.measure import measure_shape
from monkeycad.backends.occt.step import StepObject, read_step, write_step
from monkeycad.execution import (
    CadExecutionError,
    _sha256_bytes,
    _sha256_text,
    _strict_child,
    _strict_workspace,
    long_path,
)


# The ``export_path`` provenance of an export an architect asked for rather
# than one that realizes a seat. The runner's reuse and patch lookups read it
# to leave these alone: a work model is a delivery, never a seat's own output.
WORK_MODEL_EXPORT_PATH = "work-model"


@dataclass(frozen=True, slots=True)
class StepImportObject:
    """One named shape of an exported STEP, alone in a file of its own.

    The measures are the kernel's own reading of the shape that was written,
    taken after the single-object file was read back: what a host import has
    to arrive at, and what a silently healed or dropped hole would disagree
    with.
    """

    object_id: str
    file_name: str
    sha256: str
    solid_count: int
    closed: bool
    face_count: int
    volume: float | None
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "solid_count": self.solid_count,
            "closed": self.closed,
            "face_count": self.face_count,
            "volume": self.volume,
            "bbox": {"min": list(self.bbox_min), "max": list(self.bbox_max)},
        }


@dataclass(frozen=True, slots=True)
class StepImportSource:
    """The exported STEP an import-based export reads, split by named shape."""

    step_path: Path
    step_sha256: str
    objects: tuple[StepImportObject, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "step_file_name": self.step_path.name,
            "step_sha256": self.step_sha256,
            "objects": [item.to_dict() for item in self.objects],
        }


def work_model_workspace(speculative_root: Path, *, source_sha256: str) -> Path:
    """A fresh speculative directory for one export attempt, under a named root.

    The caller states the root - the run's own CAD export workspace, from the
    P036 layout - and this only makes one attempt directory inside it. No path
    is inferred from a file's neighbours, and no second workspace convention
    is introduced.

    Attempts are numbered rather than cleaned up: a previous failure keeps its
    script, marker and partial output where they can be read, and never stands
    in the way of the next attempt. Nothing is removed.
    """

    if not isinstance(speculative_root, Path):
        raise TypeError("speculative_root must be pathlib.Path")
    require_sha256(source_sha256, "source_sha256")
    root = _strict_workspace(speculative_root) / "work-model"
    long_path(root).mkdir(parents=True, exist_ok=True)
    stem = source_sha256[:12]
    attempt = 1
    while long_path(root / f"{stem}-{attempt}").exists():
        attempt += 1
    workspace = root / f"{stem}-{attempt}"
    long_path(workspace).mkdir()
    return workspace


def split_step_objects(
    step_path: Path, *, destination: Path, length_unit: str
) -> tuple[StepImportObject, ...]:
    """Write each named shape of one exported STEP into a file of its own.

    The shapes are the ones the kernel reads out of that STEP; nothing is
    recompiled, healed or tessellated, and the same writer that produced the
    source file writes each single-object file. Every file is then read back
    and measured: a shape whose solids, faces, closure, volume or bounds
    disagree with the shape it came from fails here, before any host sees it.

    Identity is the STEP's own shape name. A file whose shapes are unnamed or
    share a name is refused rather than guessed at by order or position.
    """

    if not isinstance(step_path, Path) or not isinstance(destination, Path):
        raise TypeError("step_path and destination must be pathlib.Path")
    if not destination.is_dir():
        raise CadExecutionError(f"the destination directory does not exist: {destination}")
    entries = read_step(long_path(step_path), length_unit=length_unit)
    if not entries:
        raise CadExecutionError(f"{step_path.name} holds no shape to import")
    unnamed = sum(1 for entry in entries if not entry.name)
    if unnamed:
        raise CadExecutionError(
            f"{step_path.name} has {unnamed} unnamed shape(s): an import binds each "
            "file to the object id its shape is named with, and never to import order"
        )
    names = [entry.name for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CadExecutionError(
            f"{step_path.name} names the same shape more than once: {', '.join(duplicates)}"
        )
    written: list[StepImportObject] = []
    for entry in entries:
        object_id = str(entry.name)
        file_name = f"{_import_file_stem(object_id)}.step"
        target = destination / file_name
        _strict_child(destination, target, require_exists=False)
        if long_path(target).exists():
            raise CadExecutionError(f"speculative import source already exists: {file_name}")
        layer = entry.layers[0] if entry.layers else ""
        write_step(
            long_path(target),
            (StepObject(object_id=object_id, shape=entry.shape, layer=layer, color=entry.color),),
            length_unit=length_unit,
        )
        source_measure = measure_shape(entry.shape)
        cold = read_step(long_path(target), length_unit=length_unit)
        if len(cold) != 1 or cold[0].name != object_id:
            raise CadExecutionError(
                f"{file_name} did not read back as the one shape named {object_id}"
            )
        saved_measure = measure_shape(cold[0].shape)
        _require_same_shape(object_id, source_measure, saved_measure)
        written.append(
            StepImportObject(
                object_id=object_id,
                file_name=file_name,
                sha256=_sha256_bytes(long_path(target).read_bytes()),
                solid_count=saved_measure.solid_count,
                closed=saved_measure.closed,
                face_count=saved_measure.face_count,
                volume=saved_measure.volume,
                bbox_min=saved_measure.bbox_min,
                bbox_max=saved_measure.bbox_max,
            )
        )
    return tuple(sorted(written, key=lambda item: item.object_id))


def verify_work_model_geometry(
    model_path: Path, source: StepImportSource
) -> tuple[dict[str, object], ...]:
    """Read the saved document and compare each object with the shape it came from.

    This is the reader's own second look, outside the host: per named object
    it checks that the geometry is a B-rep (never a mesh), that the solids and
    faces are the ones the STEP shape had - a healed-away opening shows up
    here as missing faces - and that a closed source stayed closed. Volume and
    exact placement are checked inside the host, where mass properties and
    trimmed-surface bounds are available; an openNURBS bounding box read here
    would include untrimmed control surfaces and could refuse a correct file.
    It returns one row per object; a disagreement raises.
    """

    try:
        rhino3dm = importlib.import_module("rhino3dm")
    except ImportError as exc:  # pragma: no cover - the export itself needs it
        raise CadExecutionError("rhino3dm is required to verify a saved work model") from exc
    if not isinstance(source, StepImportSource):
        raise TypeError("source must be StepImportSource")
    model = rhino3dm.File3dm.Read(str(long_path(model_path)))
    if model is None:
        raise CadExecutionError(f"the saved work model cannot be read: {model_path.name}")
    by_name: dict[str, list[object]] = {}
    for item in model.Objects:
        by_name.setdefault(item.Attributes.Name or "", []).append(item.Geometry)
    rows: list[dict[str, object]] = []
    for expected in source.objects:
        found = by_name.get(expected.object_id, [])
        if not found:
            raise CadExecutionError(
                f"{expected.object_id}: the saved work model has no object of that name"
            )
        solids = faces = 0
        for geometry in found:
            kind = type(geometry).__name__
            if kind == "Extrusion":
                brep = geometry.ToBrep(False)
            elif kind == "Brep":
                brep = geometry
            else:
                raise CadExecutionError(
                    f"{expected.object_id}: the saved work model holds {kind} geometry; an "
                    "editable work model carries B-rep surfaces, never a mesh"
                )
            if brep is None:
                raise CadExecutionError(f"{expected.object_id}: saved geometry has no B-rep form")
            faces += len(brep.Faces)
            solids += 1 if brep.IsSolid else 0
        if expected.closed and solids != expected.solid_count:
            raise CadExecutionError(
                f"{expected.object_id}: the exported shape is {expected.solid_count} closed "
                f"solid(s); the saved work model has {solids}"
            )
        if faces != expected.face_count:
            raise CadExecutionError(
                f"{expected.object_id}: the exported shape has {expected.face_count} faces; the "
                f"saved work model has {faces}. An opening or a face was not carried over"
            )
        rows.append({
            "object_id": expected.object_id,
            "objects": len(found),
            "solids": solids,
            "faces": faces,
            "closed": bool(expected.closed and solids == expected.solid_count),
        })
    return tuple(rows)


def _import_file_stem(object_id: str) -> str:
    """A file name that stays inside the workspace and keeps the id readable."""

    stem = "".join(char if (char.isalnum() or char in "-_.") else "-" for char in object_id)
    stem = stem.strip(".-") or "object"
    return f"{stem[:80]}@{_sha256_text(object_id)[:12]}"


def _require_same_shape(object_id: str, source, saved) -> None:
    """The single-object file holds the shape it was written from, or fails."""

    for field in ("solid_count", "face_count", "closed"):
        expected, actual = getattr(source, field), getattr(saved, field)
        if expected != actual:
            raise CadExecutionError(
                f"{object_id}: the single-object STEP has {field}={actual!r} where the "
                f"exported shape has {expected!r}"
            )
    if (source.volume is None) != (saved.volume is None):
        raise CadExecutionError(
            f"{object_id}: the single-object STEP {'has' if saved.volume is not None else 'has no'} "
            "volume where the exported shape does not"
        )
    if source.volume is not None and saved.volume is not None:
        scale = max(abs(source.volume), abs(saved.volume), 1e-9)
        if abs(source.volume - saved.volume) / scale > 1e-6:
            raise CadExecutionError(
                f"{object_id}: the single-object STEP encloses {saved.volume!r} where the "
                f"exported shape encloses {source.volume!r}"
            )
    for corner, expected, actual in (
        ("min", source.bbox_min, saved.bbox_min),
        ("max", source.bbox_max, saved.bbox_max),
    ):
        if any(abs(float(a) - float(b)) > 1e-7 for a, b in zip(expected, actual)):
            raise CadExecutionError(
                f"{object_id}: the single-object STEP bounds {corner} {actual} differ from "
                f"the exported shape's {expected}"
            )
