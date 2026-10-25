"""An imported model made into a design that can be changed (#300 item 22 B).

A Live Rhino snapshot or an uploaded 3dm has no design record: its objects
can be marked and named (``model_parts``), not changed. Made into a design,
each kind of part becomes one component and each object (or each Rhino group
of objects) one ``imported`` element naming the file and the object. The
seats build nothing for them; the run's composed model copies them from the
file and places them as the record says (``place_imported_objects``), so a
removed element is gone, a moved or turned one moves, and a material or a
note on its component is worn and read like any other. Their shape stays the
file's. The file itself is never changed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from monkeyarch.authoring.element_producers import IMPORTED, ElementRow, imported_placement
from monkeycad.formats.three_dm_compose import ImportedPlacement
from archflow.state.state_record import StateRecord

from ..binding import ProjectBinding
from ..errors import StudioError
from .model_parts import _METRES, LABELS, OTHER

#: The run a registered model file is kept under (``artifacts.MODEL_UPLOAD_RUN_PREFIX``).
_FILE_RUN_PREFIX = "studio-model-"


def imported_placements(record: StateRecord) -> tuple[ImportedPlacement, ...]:
    """Every imported element of the record, as the composed model places it."""

    placements = []
    # Read straight off the imported rows: no other element's bindings are resolved to find them.
    for entity in record.entities_of("Element@1"):
        if entity.fields.get("producer") != IMPORTED:
            continue
        row = ElementRow(entity.entity_id, str(entity.fields.get("component_id") or entity.parent_id), IMPORTED,
                         entity.fields.get("references") or {}, entity.fields.get("params") or {})
        asset, objects, move, turn = imported_placement(row)
        placements.append(ImportedPlacement(row.element_id, row.component_id, asset, objects, move, turn))
    return tuple(placements)


def imported_files(binding: ProjectBinding, placements: Sequence[ImportedPlacement]) -> dict[str, bytes]:
    """The bytes of each model file the placements copy from, re-verified as they are read."""

    from .artifacts import artifact_bytes

    files: dict[str, bytes] = {}
    for asset in sorted({placement.asset for placement in placements}):
        try:
            _, files[asset] = artifact_bytes(binding, asset, run_id=f"{_FILE_RUN_PREFIX}{asset}")
        except StudioError as exc:
            raise ValueError(f"The imported model {asset[:12]} this design copies from cannot be read: {exc}") from exc
    return files


def imported_shapes(binding: ProjectBinding, run_id: str) -> tuple:
    """The imported objects the run's composed model places, as the catalog joins exported objects to elements.

    Each top-level object labelled imported (``IMPORTED_KEY``) with its name,
    component and box in metres, CAD Z-up. Empty when the run registered no
    composed model.
    """

    from monkeycad.formats.three_dm_compose import IMPORTED_KEY
    from monkeycad.formats.three_dm_inspector import inspect_three_dm_index

    from .artifacts import artifact_bytes, list_artifacts
    from .compare import Shape

    rows = [row for row in list_artifacts(binding, run_id=run_id).artifacts
            if row.run_id == run_id and row.representation == "composed" and row.available and row.sha256]
    if len(rows) != 1:
        return ()
    index = inspect_three_dm_index(artifact_bytes(binding, rows[0].sha256, run_id=run_id)[1], bounds_for=lambda ids: ids)
    metres = _METRES.get((index.get("units") or {}).get("name"), 1.0)
    shapes = []
    for row in index.get("objects") or ():
        strings = {pair["key"]: pair["value"] for pair in row.get("attributes") or ()}
        if row.get("is_instance_definition_object") or not strings.get(IMPORTED_KEY):
            continue
        bounds = row.get("bounds") if isinstance(row.get("bounds"), Mapping) else {}
        shapes.append(Shape(
            name=row.get("name") or "", seat_id="imported", component_id=strings.get("archflow:component") or None,
            producer_op=None, geometry_sha256=None,
            bbox_min=tuple(value * metres for value in bounds["min"]) if bounds.get("min") else None,
            bbox_max=tuple(value * metres for value in bounds["max"]) if bounds.get("max") else None))
    return tuple(shapes)


def conversion_edit(
    index: Mapping[str, Any], parts: Mapping[str, Any], *, asset: str, file_name: str, root_component_id: str,
    source_ref: str, taken: set[str], groups: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The one semantic edit that makes a model file's objects a design: a component per part, an element per object.

    ``index`` is the file's object index (``inspect_three_dm_index``) and
    ``parts`` its reading (``model_parts``). Every top-level object is
    placed: objects in one Rhino group (``groups``, the first group of each
    object) form one element, every other object is its own. A part with nothing read is ``other``. Ids are new to the
    record (``taken``); components hang under ``root_component_id``, the
    root a seat builds.
    """

    rows = [row for row in index.get("objects") or () if not row.get("is_instance_definition_object")]
    if not rows:
        raise StudioError(422, "IMPORTED_MODEL_EMPTY", "This model has no objects to make into a design.")
    read = parts.get("objects") or {}
    by_part: dict[str, list[list[Mapping[str, Any]]]] = {}
    grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        part = (read.get(row["object_id"]) or {}).get("part") or OTHER
        if part not in LABELS:
            part = OTHER
        group = (groups or {}).get(row["object_id"])
        if group is not None:
            key = (part, group)
            if key not in grouped:
                grouped[key] = []
                by_part.setdefault(part, []).append(grouped[key])
            grouped[key].append(row)
        else:
            by_part.setdefault(part, []).append([row])
    used = set(taken)
    metres = _METRES.get((index.get("units") or {}).get("name"), 1.0)

    def fresh(stem: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "imported"
        candidate, number = stem, 1
        while candidate in used:
            number += 1
            candidate = f"{stem}-{number}"
        used.add(candidate)
        return candidate

    entities: list[dict[str, Any]] = []
    for part, members in sorted(by_part.items(), key=lambda item: -sum(len(objects) for objects in item[1])):
        label = LABELS[part][0]
        component_id = fresh(f"imported-{part}")
        entities.append({
            "entity_id": component_id, "schema": "Component@1", "parent_id": root_component_id,
            "basis_refs": [source_ref],
            "fields": {"intent": f"{label}（导入的 {file_name}）", "source_refs": [source_ref],
                       "facets": {"description.note": f"导入的{label}，来自 {file_name}"[:120]}},
        })
        for number, objects in enumerate(members, start=1):
            element_id = fresh(f"{component_id}-{number}")
            params: dict[str, Any] = {"asset": asset, "objects": [row["object_id"] for row in objects]}
            extent = _extent(objects, metres)
            if extent is not None:
                params["extent"] = extent
            entities.append({
                "entity_id": element_id, "schema": "Element@1", "parent_id": component_id,
                "basis_refs": [source_ref],
                "fields": {"component_id": component_id, "producer": IMPORTED, "references": {}, "params": params},
            })
    return {
        "summary": f"把导入的模型 {file_name} 转成能改的设计：{len(by_part)} 类构件、{len(rows)} 个对象",
        "entities": entities, "parameters": [], "relations": [], "removeEntityIds": [],
        "removeParameterKeys": [], "removeRelationIds": [], "protected": [], "kept": [],
    }


def _extent(rows: Sequence[Mapping[str, Any]], metres: float) -> dict[str, list[float]] | None:
    """The box the objects fill in their file, in record metres (x, y-up, z): CAD (X, Y, Z) is record (X, Z, Y)."""

    boxes = [row["bounds"] for row in rows if isinstance(row.get("bounds"), Mapping)
             and row["bounds"].get("min") and row["bounds"].get("max")]
    if not boxes:
        return None
    low = [min(box["min"][axis] for box in boxes) * metres for axis in range(3)]
    high = [max(box["max"][axis] for box in boxes) * metres for axis in range(3)]
    return {"min": [round(low[0], 6), round(low[2], 6), round(low[1], 6)],
            "max": [round(high[0], 6), round(high[2], 6), round(high[1], 6)]}


__all__ = ["conversion_edit", "imported_files", "imported_placements"]
