"""A 3DM that carries a design state and its seal.

Owns the ``archflow:state`` and ``archflow:binding`` document strings: writing them
into a copy of a model exported elsewhere, reading them back, and naming every
difference between the file as found and its seal (out-of-band edits). The record
and decisions are caller data; only their rows are read here. Object rows come from
``three_dm_inspector``: named top-level objects with saved visible bounds. The seal also keeps the record and
decisions it vouches for, so a reader can build from what was sealed rather than from what the file says now,
where each mesh object's vertices sit inside its own box (their mean and spread), which tells a turn, mirror or
reshape inside an unchanged box from a mere re-encoding, and how many top-level objects have no name.

Does not own the design record's meaning, choosing paths, receipts, or repairing a
file; reading never writes.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import CanonicalValueError, canonical_digest, canonical_json

from monkeycad.formats.three_dm_inspector import ThreeDmInspection, ThreeDmInspectionError, inspect_three_dm


SCHEMA = "ArchFlowStateFile@1"
BINDING_SCHEMA = "ArchFlowStateBinding@1"
STATE_KEY = "archflow:state"
BINDING_KEY = "archflow:binding"
BOX_TOLERANCE_M = 0.002
# Vertices that sit where they sat, to this, keep their shape: a re-encoded mesh keeps them, a turned one does not.
SHAPE_TOLERANCE_M = 0.005
_SEAL_TOLERANCE_M = 1e-9
_HEADER_REF = "record:header"
_ROW_LISTS = (
    ("entities", "entity", "entity_id"),
    ("parameters", "parameter", "key"),
    ("relations", "relation", "relation_id"),
    ("obligations", "obligation", "obligation_id"),
)


class StateFileError(ValueError):
    """The file cannot be read or written as a state file; ``code`` names why."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ObjectRow:
    name: str
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    geometry_sha256: str | None
    # Where a mesh's vertices sit inside its own box: their mean and spread on x, y and z, from the box's low corner.
    shape: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class OutOfBand:
    """One difference between the file as found and its seal."""

    kind: str
    ref: str
    detail: dict


@dataclass(frozen=True, slots=True)
class StateFile:
    record: dict
    decisions: tuple[dict, ...]
    binding: dict | None
    file_sha256: str
    objects: tuple[ObjectRow, ...]
    out_of_band: tuple[OutOfBand, ...]
    reencoded: tuple[str, ...]
    # What the seal vouches for, when it keeps a copy: the record and decisions as MonkeyHub wrote them.
    sealed_record: dict | None = None
    sealed_decisions: tuple[dict, ...] | None = None


def row_digests(record: dict) -> dict[str, str]:
    """Digest per row: entities, parameters, relations, obligations by id, the rest as ``record:header``."""

    if not isinstance(record, Mapping):
        raise StateFileError("STATE_CORRUPT", "the record is not a JSON object")
    lists = {field for field, _, _ in _ROW_LISTS}
    digests = {_HEADER_REF: _digest({key: value for key, value in record.items() if key not in lists})}
    for field, prefix, id_key in _ROW_LISTS:
        rows = record.get(field) or ()
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise StateFileError("STATE_CORRUPT", f"record.{field} is not a list")
        for row in rows:
            row_id = row.get(id_key) if isinstance(row, Mapping) else None
            if not isinstance(row_id, str) or not row_id:
                raise StateFileError("STATE_CORRUPT", f"a row of record.{field} has no {id_key}")
            ref = f"{prefix}:{row_id}"
            if ref in digests:
                raise StateFileError("STATE_CORRUPT", f"{ref} appears twice")
            digests[ref] = _digest(row)
    return dict(sorted(digests.items()))


def object_rows(path: Path) -> tuple[ObjectRow, ...]:
    """Named top-level objects of a 3DM with saved visible bounds, encoded-geometry and shape digests."""

    shapes, _ = _shapes(Path(path).read_bytes())
    return _object_rows(_inspect(path), shapes)


def binding_for(
    record: dict,
    decisions: Sequence[dict],
    objects: Sequence[ObjectRow],
    parent_file_sha256: str | None,
    unnamed: int = 0,
) -> dict:
    """The seal of a state over these objects; a repeated name binds its first row (see ``compare``). It keeps the
    record and decisions themselves, which its digests then also vouch for."""

    return {
        "schema": BINDING_SCHEMA,
        "record_digest": _digest(record),
        "decisions_digest": _digest(list(decisions)),
        "row_digests": row_digests(record),
        "objects": {
            name: {"min": list(row.min), "max": list(row.max), "geometry_sha256": row.geometry_sha256,
                   "shape": None if row.shape is None else list(row.shape)}
            for name, row in _first_by_name(objects).items()
        },
        "parent_file_sha256": parent_file_sha256,
        "record": record,
        "decisions": list(decisions),
        "unnamed": unnamed,
    }


def compare(
    binding: dict,
    record: dict,
    decisions: Sequence[dict],
    objects: Sequence[ObjectRow],
    unnamed: int | None = None,
) -> tuple[tuple[OutOfBand, ...], tuple[str, ...]]:
    """(out-of-band differences, re-encoded object names) of a state and objects against a seal.

    A box that moved by one offset is ``object_moved``; one that changed otherwise (turned, mirrored, resized) is
    ``object_transformed``. An object whose box stays within ``BOX_TOLERANCE_M`` but whose vertices sit elsewhere
    inside it by more than ``SHAPE_TOLERANCE_M`` was turned, mirrored or reshaped (``object_reshaped``); one whose
    geometry digest alone
    changed is re-encoded, not edited. A name carried by several objects is reported as ``object_duplicate`` and
    compared through its first row in (min, max, digest) order. A changed count of unnamed objects is
    ``object_unnamed``.
    """

    sealed = _checked_binding(binding)
    found: list[OutOfBand] = []
    current_rows, sealed_rows = row_digests(record), sealed["row_digests"]
    for ref in sorted(current_rows.keys() | sealed_rows.keys()):
        if ref not in sealed_rows:
            found.append(OutOfBand("row_added", ref, {}))
        elif ref not in current_rows:
            found.append(OutOfBand("row_removed", ref, {}))
        elif current_rows[ref] != sealed_rows[ref]:
            found.append(OutOfBand("row_changed", ref, {}))
    if _digest(list(decisions)) != sealed["decisions_digest"]:
        detail: dict = {}
        if "decisions" in sealed:
            kept = {_digest(row) for row in sealed["decisions"]}
            now = {_digest(row) for row in decisions}
            detail = {"added": [row for row in decisions if _digest(row) not in kept],
                      "removed": [row for row in sealed["decisions"] if _digest(row) not in now]}
        found.append(OutOfBand("decisions_changed", "decisions", detail))

    counts = Counter(row.name for row in objects)
    current_objects, sealed_objects = _first_by_name(objects), sealed["objects"]
    reencoded: list[str] = []
    for name in sorted(current_objects.keys() | sealed_objects.keys()):
        ref = f"object:{name}"
        if counts[name] > 1:
            found.append(OutOfBand("object_duplicate", ref, {"count": counts[name]}))
        before, row = sealed_objects.get(name), current_objects.get(name)
        if row is None:
            found.append(OutOfBand("object_removed", ref, {"before": _box(before["min"], before["max"])}))
            continue
        after = _box(row.min, row.max)
        if before is None:
            found.append(OutOfBand("object_added", ref, {"after": after}))
            continue
        moved = max(abs(a - b) for a, b in zip((*row.min, *row.max), (*before["min"], *before["max"])))
        if moved > BOX_TOLERANCE_M:
            low = [a - b for a, b in zip(row.min, before["min"])]
            high = [a - b for a, b in zip(row.max, before["max"])]
            detail = {"before": _box(before["min"], before["max"]), "after": after, "moved_m": moved}
            kind = "object_moved" if max(abs(a - b) for a, b in zip(low, high)) <= BOX_TOLERANCE_M else "object_transformed"
            found.append(OutOfBand(kind, ref, detail))
        elif None not in (row.shape, before.get("shape")) and len(row.shape) == len(before["shape"]) \
                and max(abs(a - b) for a, b in zip(row.shape, before["shape"])) > SHAPE_TOLERANCE_M:
            found.append(OutOfBand("object_reshaped", ref, {"box": after}))
        elif None not in (row.geometry_sha256, before["geometry_sha256"]) \
                and row.geometry_sha256 != before["geometry_sha256"]:
            reencoded.append(name)
    if unnamed is not None and isinstance(sealed.get("unnamed"), int) and unnamed != sealed["unnamed"]:
        found.append(OutOfBand("object_unnamed", "objects:unnamed", {"before": sealed["unnamed"], "after": unnamed}))
    return tuple(sorted(found, key=lambda item: (item.ref, item.kind))), tuple(reencoded)


def read_state_file(path: Path) -> StateFile:
    """The state, seal and out-of-band differences of one 3DM; an unsealed file reports ``unsealed``."""

    inspection = _inspect(path)
    shapes, unnamed = _shapes(Path(path).read_bytes())
    strings = _strings(inspection)
    if STATE_KEY not in strings:
        raise StateFileError("STATE_MISSING", f"no {STATE_KEY} string in the file")
    state = _parse(strings[STATE_KEY], "STATE_CORRUPT")
    if state.get("schema") != SCHEMA:
        raise StateFileError("SCHEMA_UNSUPPORTED", f"{STATE_KEY} schema is {state.get('schema')!r}")
    record, decisions = state.get("record"), state.get("decisions")
    if not isinstance(record, dict) or not isinstance(decisions, list) \
            or not all(isinstance(item, dict) for item in decisions):
        raise StateFileError("STATE_CORRUPT", f"{STATE_KEY} needs a record object and a list of decision objects")
    objects = _object_rows(inspection, shapes)
    binding = _checked_binding(_parse(strings[BINDING_KEY], "BINDING_CORRUPT")) if BINDING_KEY in strings else None
    if binding is None:
        out_of_band, reencoded = (OutOfBand("unsealed", "file", {}),), ()
    else:
        out_of_band, reencoded = compare(binding, record, decisions, objects, unnamed)
    sealed_record = None if binding is None else binding.get("record")
    sealed_decisions = None if binding is None or "decisions" not in binding else tuple(binding["decisions"])
    return StateFile(record, tuple(decisions), binding, inspection.file_sha256, objects, out_of_band, reencoded,
                     sealed_record, sealed_decisions)


def seal_state_file(
    source: Path,
    destination: Path,
    *,
    record: dict,
    decisions: Sequence[dict],
    parent_file_sha256: str | None,
) -> StateFile:
    """Write a copy of ``source`` at a new ``destination`` carrying the state and its seal.

    Other document strings, ``archflow:*`` ones included, are kept. The written file is read
    back and its object rows must equal the sealed ones, else ``WRITE_FAILED`` (the bytes
    already written stay at ``destination``).
    """

    if Path(source).resolve() == Path(destination).resolve():
        raise StateFileError("WRITE_FAILED", "the destination is the source file")
    try:
        data = Path(source).read_bytes()
    except OSError as exc:
        raise StateFileError("FILE_UNREADABLE", f"{Path(source).name}: {exc}") from exc
    inspection = _inspect(source)
    if inspection.file_sha256 != hashlib.sha256(data).hexdigest():
        raise StateFileError("FILE_UNREADABLE", f"{Path(source).name} changed while it was read")
    shapes, unnamed = _shapes(data)
    sealed_objects = _object_rows(inspection, shapes)
    binding = binding_for(record, decisions, sealed_objects, parent_file_sha256, unnamed)
    state = {"schema": SCHEMA, "record": record, "decisions": list(decisions)}
    state_text, binding_text = _json(state), _json(binding)

    try:
        rhino3dm = importlib.import_module("rhino3dm")
    except ImportError as exc:
        raise StateFileError("WRITE_FAILED", "rhino3dm is unavailable") from exc
    model = rhino3dm.File3dm.FromByteArray(data)
    if model is None:
        raise StateFileError("FILE_UNREADABLE", f"rhino3dm could not decode {Path(source).name}")
    model.Strings[STATE_KEY] = state_text
    model.Strings[BINDING_KEY] = binding_text
    if Path(destination).exists():
        raise StateFileError("WRITE_FAILED", f"{Path(destination).name} already exists")
    if not model.Write(str(destination), 8):
        raise StateFileError("WRITE_FAILED", f"rhino3dm could not write {Path(destination).name}")

    try:
        written = read_state_file(Path(destination))
    except StateFileError as exc:
        raise StateFileError("WRITE_FAILED", f"{Path(destination).name} does not read back: {exc}") from exc
    if written.binding != binding or not _same_rows(written.objects, sealed_objects):
        raise StateFileError("WRITE_FAILED", f"{Path(destination).name} does not read back as sealed")
    return written


def _inspect(path: Path) -> ThreeDmInspection:
    try:
        return inspect_three_dm(Path(path))
    except ThreeDmInspectionError as exc:
        raise StateFileError("FILE_UNREADABLE", f"{exc.code.value}: {exc.message}") from exc


def _object_rows(inspection: ThreeDmInspection, shapes: Mapping[str, str] | None = None) -> tuple[ObjectRow, ...]:
    digests = {str(item["object_id"]): item["geometry_sha256"] for item in inspection.object_geometry_sha256}
    rows = []
    for item in inspection.named_object_bboxes:
        digest = digests.get(str(item["object_id"]))
        rows.append(ObjectRow(
            str(item["name"]),
            _triple(item["bbox"]["min"]),
            _triple(item["bbox"]["max"]),
            None if digest is None else str(digest),
            (shapes or {}).get(str(item["object_id"]).lower()),
        ))
    return tuple(sorted(rows, key=lambda row: (row.name, row.min, row.max, row.geometry_sha256 or "")))


def _shapes(data: bytes) -> tuple[dict[str, tuple[float, ...]], int | None]:
    """Where each named mesh object's vertices sit inside its own box, by object id, and how many top-level objects
    have no name; ({}, None) when the file cannot be read for them. A re-encoded mesh, or one moved whole, keeps
    them; a turned, mirrored or reshaped one does not."""

    try:
        rhino3dm = importlib.import_module("rhino3dm")
        model = rhino3dm.File3dm.FromByteArray(data)
    except Exception:  # noqa: BLE001 - the fingerprints are only a further reading; their absence compares nothing
        return {}, None
    if model is None:
        return {}, None
    shapes: dict[str, tuple[float, ...]] = {}
    unnamed = 0
    for item in model.Objects:
        attributes = item.Attributes
        if getattr(attributes, "IsInstanceDefinitionObject", False):
            continue
        if not attributes.Name:
            unnamed += 1
            continue
        geometry = item.Geometry
        if type(geometry).__name__ != "Mesh":
            continue
        vertices = geometry.Vertices
        points = {(float(p.X), float(p.Y), float(p.Z)) for p in (vertices[index] for index in range(len(vertices)))}
        if not points:
            continue
        low = [min(point[axis] for point in points) for axis in range(3)]
        mean = [sum(point[axis] for point in points) / len(points) for axis in range(3)]
        spread = [(sum((point[axis] - mean[axis]) ** 2 for point in points) / len(points)) ** 0.5 for axis in range(3)]
        shapes[str(attributes.Id).lower()] = tuple(round(value, 6) + 0.0 for value in
                                                   (*(m - l for m, l in zip(mean, low)), *spread))
    return shapes, unnamed


def _first_by_name(objects: Sequence[ObjectRow]) -> dict[str, ObjectRow]:
    first: dict[str, ObjectRow] = {}
    for row in sorted(objects, key=lambda row: (row.name, row.min, row.max, row.geometry_sha256 or "")):
        first.setdefault(row.name, row)
    return first


def _same_rows(found: Sequence[ObjectRow], sealed: Sequence[ObjectRow]) -> bool:
    return len(found) == len(sealed) and all(
        a.name == b.name
        and a.geometry_sha256 == b.geometry_sha256
        and a.shape == b.shape
        and all(abs(x - y) <= _SEAL_TOLERANCE_M for x, y in zip((*a.min, *a.max), (*b.min, *b.max)))
        for a, b in zip(found, sealed)
    )


def _strings(inspection: ThreeDmInspection) -> dict[str, str]:
    strings: dict[str, str] = {}
    for item in inspection.document_user_strings:
        key = str(item["key"])
        if key in strings and key in (STATE_KEY, BINDING_KEY):
            raise StateFileError("STATE_CORRUPT" if key == STATE_KEY else "BINDING_CORRUPT", f"{key} appears twice")
        strings[key] = str(item["value"])
    return strings


def _parse(text: str, code: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise StateFileError(code, str(exc)) from exc
    if not isinstance(value, dict):
        raise StateFileError(code, "not a JSON object")
    return value


def _checked_binding(value: Any) -> dict:
    if not isinstance(value, Mapping):
        raise StateFileError("BINDING_CORRUPT", "the binding is not a JSON object")
    if value.get("schema") != BINDING_SCHEMA:
        raise StateFileError("SCHEMA_UNSUPPORTED", f"{BINDING_KEY} schema is {value.get('schema')!r}")
    rows, objects = value.get("row_digests"), value.get("objects")
    well_formed = (
        isinstance(value.get("record_digest"), str)
        and isinstance(value.get("decisions_digest"), str)
        and isinstance(value.get("parent_file_sha256"), (str, type(None)))
        and isinstance(rows, Mapping)
        and all(isinstance(digest, str) for digest in rows.values())
        and isinstance(objects, Mapping)
        and all(_is_bound_object(item) for item in objects.values())
    )
    if not well_formed:
        raise StateFileError("BINDING_CORRUPT", f"{BINDING_KEY} lacks a field or has one of the wrong type")
    # The copy it keeps must be the one its digests vouch for, or the seal does not match itself.
    if "record" in value and (not isinstance(value["record"], Mapping) or _digest(value["record"]) != value["record_digest"]):
        raise StateFileError("BINDING_CORRUPT", f"{BINDING_KEY} keeps a record its own digest does not match")
    if "decisions" in value and (not isinstance(value["decisions"], list)
                                 or _digest(list(value["decisions"])) != value["decisions_digest"]):
        raise StateFileError("BINDING_CORRUPT", f"{BINDING_KEY} keeps decisions its own digest does not match")
    return dict(value)


def _is_bound_object(item: Any) -> bool:
    return (
        isinstance(item, Mapping)
        and all(
            isinstance(item.get(end), list) and len(item[end]) == 3
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in item[end])
            for end in ("min", "max")
        )
        and isinstance(item.get("geometry_sha256"), (str, type(None)))
        and (item.get("shape") is None or (isinstance(item["shape"], list) and len(item["shape"]) == 6 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in item["shape"])))
    )


def _triple(values: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = (float(value) for value in values)
    return (x, y, z)


def _box(minimum: Sequence[float], maximum: Sequence[float]) -> dict[str, list[float]]:
    return {"min": [float(x) for x in minimum], "max": [float(x) for x in maximum]}


def _digest(value: object) -> str:
    try:
        return canonical_digest(value)
    except CanonicalValueError as exc:
        raise StateFileError("STATE_CORRUPT", str(exc)) from exc


def _json(value: object) -> str:
    try:
        return canonical_json(value)
    except CanonicalValueError as exc:
        raise StateFileError("STATE_CORRUPT", str(exc)) from exc
