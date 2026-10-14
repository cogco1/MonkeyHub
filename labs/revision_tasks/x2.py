"""X2: a model decides a revision from one kind of material, in one call.

Every arm gets the same request, the same system prompt (``prompts/system_common.md``), the same
request template (``prompts/request.md``) and the same answer schema; only the materials differ.

| arm | materials |
|---|---|
| A | the history events (``history/events_v1.json``), written out as text |
| B | a readback of the model with its metadata stripped: object names and boxes |
| C | the C-task state (task rows of the record and the ``ContextPack@1``) without obligations, their limit parameters or bounding relations, as text |
| D | the C-task state, as text |
| F | the C-task state exactly as embedded in the 3DM (JSON) |

Batch 5 adds four control arms beside them (``BATCH5_ARMS``); A to F are built exactly as before:

| arm | materials |
|---|---|
| AG | A's text, the line ``AG_HEADING``, B's text |
| S | a summary the model wrote from A's text alone (``prompts/s_summary.md``), once per family before any task call, frozen |
| N | the C-task IFC as X1 writes it, without any ``ArchFlow_*`` property set, as text (``render_ifc``) |
| N+S | the same IFC as written (``ArchFlow_State`` and ``ArchFlow_Object`` kept), through the same renderer |

The C-task pack is read from a disposable project that holds the fixture as production does, with the history's
rejected proposals recorded as decisions (``x1.production_pack``). Text is written by one generic renderer
(``render``), the same for every text arm.

The answer's ``changes`` are mapped to the fixture's parameters (history names through
``x1.HISTORY_PARAMETERS``; B names no parameter), applied with ``apply_state_record_operator`` to the start,
realized, and judged by the oracle against the X4 task file of the same id when its start is the fixture's.
A batch-5 arm's answer is read as the counted arm whose names it carries (``READ_AS``).

Each call is ``claude -p`` with the flags in ``command``: one turn, no tools, no MCP servers, only project and
local settings (the trial directory holds no settings and no CLAUDE.md), bundled skills and slash commands off,
no session saved, and auto-memory off as MonkeyHub's own chat runs it. Claude Code's own system prompt cannot be
removed; it is the same for every arm.

Cost: each call is priced with ``monkeymonitor.pricing.quote`` at the manifest's API-equivalent rates, and the
larger of that and the CLI's own ``total_cost_usd`` goes into a shared append-only ledger. A part's cap or the
total cap stops the run before the call that could cross it.

The multi-turn G arm (MonkeyHub's construction tools) is not here.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archflow.state.state_record import StateRecordEditKind, StateRecordError, StateRecordOperator, apply_state_record_operator, evaluate_parameters

from labs.revision_tasks import carrier, fixtures, pipeline, reviser, taskspec, x1
from labs.revision_tasks import run as runner
from labs.revision_tasks.attacks import _rewrite, _strings

LAB = Path(__file__).resolve().parent
PROMPTS = LAB / "prompts"
ARMS = ("A", "B", "C", "D", "F")
# Batch 5's control arms. They run only when named (``--arms AG,S,N,N+S``); the default stays A to F.
BATCH5_ARMS = ("AG", "S", "N", "N+S")
# Whose names an arm's answer carries: AG and S the history's (A), N the objects' (B), N+S the state's (D).
READ_AS = {"AG": "A", "S": "A", "N": "B", "N+S": "D"}
AG_HEADING = "## Readback of the model file"
S_TEMPLATE = "s_summary.md"
S_STAND_IN = ("[DRY-RUN STAND-IN, NOT A SUMMARY: no model call was made. In a counted run this place holds the summary "
              "the model wrote from arm A's event material for family {family}, frozen before any task call.]")
MODEL = "claude-opus-5-5"
# Manifest v3.2 section 4: input $4 and output $20 per million tokens; cache reads and writes at the provider's
# standard multiples of input (0.1, 1.25 for five minutes, 2 for one hour).
RATES = {"input": "4", "cached_input": "0.4", "cache_write_input": "5", "cache_write_1h_input": "8", "output": "20"}
# Batch 3d: $35 of X2 single's unspent cap moves to X3 (its reruns), and X3 GH rises from 105 to 180 (total 865).
# Batch 3e: X3 rises to 640 and the total to 1070, so that X3's second wave runs to the end.
CAPS = {"pilot": 45.0, "x2_single": 65.0, "x2_g": 75.0, "x3": 640.0, "x3_gh": 180.0, "x8_i3": 15.0, "x9_agent": 20.0}
TOTAL_CAP = 1070.0
PER_CALL_USD = 1.0
VERDICTS = ("held", "violated", "refuse", "unknown", "flag_rejected")


class CapReached(RuntimeError):
    pass


# ---------------------------------------------------------------- materials

class Verbatim(str):
    """A stored value written exactly as it is stored (an IFC property's value), where the renderer would quote it.
    Only ``render_ifc`` makes one; no other arm's data contains one."""


def _scalar(value: Any) -> str:
    if isinstance(value, Verbatim) and "\n" not in value:
        return str(value)
    if isinstance(value, str) and value and value.strip() == value and ":" not in value:
        return value
    return json.dumps(value, ensure_ascii=False)


def _flat(value: Any) -> bool:
    return isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value)


def _render(value: Any, indent: int) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key in value:
            item = value[key]
            if item is None or item == [] or item == {} or item == "":
                continue
            if _flat(item):
                lines.append(f"{pad}{key}: [{', '.join(_scalar(v) for v in item)}]")
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_render(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
    else:
        for item in value:
            if isinstance(item, (dict, list)):
                inner = _render(item, indent + 1)
                if inner:
                    lines.append(f"{pad}- {inner[0].strip()}")
                    lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return lines


def render(value: Any) -> str:
    """Nested data as indented text: keys in their stored order, empty values left out, scalars as written."""

    return "\n".join(_render(value, 0))


def without_guards(state: dict[str, Any]) -> dict[str, Any]:
    """The state without its obligations, the limit parameters they compare against, and the relations that bound,
    clear or fix, wherever the pack repeats them."""

    state = json.loads(json.dumps(state))
    record = state["record"]
    obligations = record.get("obligations", [])
    removed = {o["obligation_id"] for o in obligations}
    removed |= {r["relation_id"] for r in record.get("relations", []) if (r.get("validator") or {}).get("check_kind") in x1.BOUNDING_CHECKS}
    # An obligation's second subject is its limit (``min_pier``, ``max_clear_spacing``): a parameter that only states the rule.
    removed |= {o["subject_refs"][1].removeprefix("parameter:") for o in obligations
                if len(o.get("subject_refs", [])) == 2 and str(o["subject_refs"][1]).startswith("parameter:")}
    record["obligations"] = []

    def keep(item: Any) -> bool:
        text = json.dumps(item, ensure_ascii=False)
        return not any(f'"{i}"' in text or f":{i}\"" in text for i in removed)

    def prune(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: prune(v) for k, v in node.items()}
        if isinstance(node, list):
            return [prune(v) for v in node if keep(v)]
        return node

    return prune(state)


def readback(path: Path) -> list[dict[str, Any]]:
    """Each named mesh object's box, from the 3DM (the harness's reading, not the oracle's)."""

    import rhino3dm

    rows = []
    for obj in rhino3dm.File3dm.Read(str(path)).Objects:
        mesh, name = obj.Geometry, obj.Attributes.Name
        if not isinstance(mesh, rhino3dm.Mesh) or not name:
            continue
        points = [(mesh.Vertices[i].X, mesh.Vertices[i].Y, mesh.Vertices[i].Z) for i in range(len(mesh.Vertices))]
        rows.append({"name": name, "min": [round(min(p[i] for p in points), 3) for i in range(3)],
                     "max": [round(max(p[i] for p in points), 3) for i in range(3)]})
    return sorted(rows, key=lambda r: r["name"])


def with_readback(history: str, objects: str) -> str:
    """AG's material: A's text, a blank line, ``AG_HEADING``, a blank line, B's text."""

    return f"{history}\n\n{AG_HEADING}\n\n{objects}"


# ---------------------------------------------------------------- IFC as text (N, N+S)

ARCHFLOW_PSET_PREFIX = "ArchFlow_"
# What a product keeps of ArchFlow_Object when its Name does not carry its object name: its identity alone.
IDENTITY_PROPERTIES = ("object_ref",)


def _ref(entity: Any) -> str:
    """An entity named in another's entry: class, Name and GlobalId."""

    name = getattr(entity, "Name", None)
    return f"{entity.is_a()} {name} ({entity.GlobalId})" if name else f"{entity.is_a()} ({entity.GlobalId})"


def _value(value: Any) -> Any:
    """An IFC value as stored: a wrapped value unwrapped, an entity named, text written as it is."""

    if value is None:
        return Verbatim("null")
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    if hasattr(value, "is_a"):
        if hasattr(value, "wrappedValue"):
            return _value(value.wrappedValue)
        return _ref(value) if hasattr(value, "GlobalId") else Verbatim(value.is_a())
    return Verbatim(value) if isinstance(value, str) else value


def _property(prop: Any) -> Any:
    if prop.is_a("IfcPropertySingleValue"):
        return _value(prop.NominalValue)
    if prop.is_a("IfcPropertyEnumeratedValue"):
        return _value(prop.EnumerationValues)
    if prop.is_a("IfcPropertyListValue"):
        return _value(prop.ListValues)
    if prop.is_a("IfcPropertyBoundedValue"):
        return {"lower": _value(prop.LowerBoundValue), "upper": _value(prop.UpperBoundValue)}
    if prop.is_a("IfcComplexProperty"):
        return {p.Name: _property(p) for p in sorted(prop.HasProperties, key=lambda p: p.Name)}
    if prop.is_a("IfcPhysicalSimpleQuantity"):
        return _value(next(v for k, v in prop.get_info(recursive=False).items() if k.endswith("Value")))
    if prop.is_a("IfcPropertyReferenceValue"):
        return _value(prop.PropertyReference)
    return Verbatim(prop.is_a())


def _property_sets(entity: Any) -> dict[str, dict[str, Any]]:
    """Every property and quantity set on ``entity``, by name, each with every property, by name."""

    found: dict[str, dict[str, Any]] = {}
    for rel in getattr(entity, "IsDefinedBy", None) or ():
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = rel.RelatingPropertyDefinition
        items = pset.HasProperties if pset.is_a("IfcPropertySet") else pset.Quantities if pset.is_a("IfcElementQuantity") else None
        if items is None:
            continue
        name = pset.Name if pset.Name not in found else f"{pset.Name} ({pset.GlobalId})"
        found[name] = {p.Name: _property(p) for p in sorted(items, key=lambda p: p.Name)}
    return dict(sorted(found.items()))


def _spatial(entity: Any) -> bool:
    return entity.is_a("IfcSpatialStructureElement") or entity.is_a("IfcSpatialElement")


def _relations(entity: Any, model: Any) -> list[dict[str, Any]]:
    """The aggregations and connections (containment included) ``entity`` takes part in: each one's class, GlobalId,
    the attribute that holds ``entity`` and every other attribute that names an entity."""

    rows = []
    for rel in model.get_inverse(entity):
        if not (rel.is_a("IfcRelAggregates") or rel.is_a("IfcRelConnects")):
            continue
        row: dict[str, Any] = {"class": rel.is_a(), "GlobalId": rel.GlobalId}
        for key, value in rel.get_info(recursive=False).items():
            if key in ("id", "type", "GlobalId", "OwnerHistory", "Name", "Description") or value is None:
                continue
            if value == entity or (isinstance(value, tuple) and entity in value):
                row["role"] = key
                continue
            row[key] = _value(value)
        rows.append(row)
    return sorted(rows, key=lambda r: (r["class"], r["GlobalId"]))


TESSELLATED = ("IfcTriangulatedFaceSet", "IfcPolygonalFaceSet")


def _box(points: Any) -> dict[str, list[float]]:
    return {"min": [round(float(v), 3) for v in points.min(axis=0)], "max": [round(float(v), 3) for v in points.max(axis=0)]}


def _boxes(model: Any, products: list[Any]) -> dict[str, dict[str, list[float]]]:
    """GlobalId -> the product's bounding box in metres, in world coordinates. A body made only of face sets is read from
    its mesh vertices under the product's placement; any other body from ifcopenshell.geom's shape."""

    import numpy
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    boxes, others = {}, []
    for product in products:
        if product.Representation is None:
            continue
        items = [i for r in product.Representation.Representations if r.RepresentationIdentifier == "Body" for i in r.Items]
        if not items or not all(i.is_a() in TESSELLATED for i in items):
            others.append(product)
            continue
        matrix = (ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement) if product.ObjectPlacement
                  else numpy.eye(4))
        points = numpy.vstack([numpy.asarray(i.Coordinates.CoordList, dtype=float) for i in items])
        boxes[product.GlobalId] = _box((points @ matrix[:3, :3].T + matrix[:3, 3]) * scale)
    if others:
        import ifcopenshell.geom

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        for product in others:
            try:
                shape = ifcopenshell.geom.create_shape(settings, product)   # kept: its geometry lives only as long as it does
            except RuntimeError:
                continue
            verts = numpy.asarray(list(shape.geometry.verts), dtype=float).reshape(-1, 3)
            if len(verts):
                boxes[product.GlobalId] = _box(verts)
    return boxes


def render_ifc(model: Any) -> str:
    """An IFC model as text: its spatial structure (project, site, building, storey, through IfcRelAggregates), then every
    other product sorted by Name and GlobalId, each with its class, Name, GlobalId, Tag, every property set with every
    property, its spatial container, the relations it takes part in, and its bounding box in metres. Property values
    are written as they are stored."""

    def node(entity: Any) -> dict[str, Any]:
        parts = [o for rel in getattr(entity, "IsDecomposedBy", None) or () if rel.is_a("IfcRelAggregates")
                 for o in rel.RelatedObjects if _spatial(o)]
        return {"class": entity.is_a(), "Name": entity.Name, "GlobalId": entity.GlobalId, "property_sets": _property_sets(entity),
                "parts": [node(p) for p in sorted(parts, key=lambda p: (p.Name or "", p.GlobalId))]}

    products = sorted((p for p in model.by_type("IfcProduct") if not _spatial(p)), key=lambda p: (p.Name or "", p.GlobalId))
    boxes = _boxes(model, products)
    rows = []
    for product in products:
        containers = [rel.RelatingStructure for rel in getattr(product, "ContainedInStructure", None) or ()]
        rows.append({"class": product.is_a(), "Name": product.Name, "GlobalId": product.GlobalId, "Tag": getattr(product, "Tag", None),
                     "property_sets": _property_sets(product),
                     "spatial_container": _ref(containers[0]) if len(containers) == 1 else [_ref(c) for c in containers],
                     "relations": _relations(product, model), "bounding_box": boxes.get(product.GlobalId)})
    projects = sorted(model.by_type("IfcProject"), key=lambda p: p.GlobalId)
    return render({"schema": model.schema, "spatial_structure": [node(p) for p in projects], "products": rows})


def strip_archflow(model: Any, names: set[str]) -> dict[str, Any]:
    """Remove every ``ArchFlow_*`` property set from ``model``, wherever it sits. A product whose Name is not one of
    ``names`` (the object names B's readback shows) keeps ArchFlow_Object's identity property alone. Returns what went."""

    removed: dict[str, dict[str, Any]] = {}
    kept = []
    for rel in list(model.by_type("IfcRelDefinesByProperties")):
        pset = rel.RelatingPropertyDefinition
        if not (pset.Name or "").startswith(ARCHFLOW_PSET_PREFIX):
            continue
        owners = list(rel.RelatedObjects)
        properties = list(getattr(pset, "HasProperties", None) or ())
        seen = removed.setdefault(pset.Name, {"on": {}, "properties": set()})
        for owner in owners:
            seen["on"][owner.is_a()] = seen["on"].get(owner.is_a(), 0) + 1
        seen["properties"] |= {p.Name for p in properties}
        unnamed = [o for o in owners if pset.Name == carrier.IFC_OBJECT_PSET and o.is_a("IfcProduct") and o.Name not in names]
        identity = [p for p in properties if p.Name in IDENTITY_PROPERTIES and p.is_a("IfcPropertySingleValue")]
        for owner in unnamed:
            if identity:
                values = [model.createIfcPropertySingleValue(p.Name, None, p.NominalValue, None) for p in identity]
                kept_pset = model.createIfcPropertySet(carrier._guid("pset-kept", pset.Name, owner.GlobalId), None, pset.Name, None, values)
                model.createIfcRelDefinesByProperties(carrier._guid("defines-kept", pset.Name, owner.GlobalId), None, None, None,
                                                      [owner], kept_pset)
                kept.append({"product": owner.Name, "GlobalId": owner.GlobalId, "kept": [p.Name for p in identity]})
        model.remove(rel)
        if not model.get_inverse(pset):
            model.remove(pset)
            for prop in properties:
                if not model.get_inverse(prop):
                    model.remove(prop)
    remaining = sorted({p.Name for p in model.by_type("IfcPropertySet") if (p.Name or "").startswith(ARCHFLOW_PSET_PREFIX)})
    return {"removed": {k: {"on": dict(sorted(v["on"].items())), "properties": sorted(v["properties"])} for k, v in sorted(removed.items())},
            "kept_identity": sorted(kept, key=lambda k: (k["product"] or "", k["GlobalId"])), "remaining": remaining}


def ifc_materials(path: Path, stripped: Path, names: list[str]) -> dict[str, Any]:
    """N and N+S from one IFC. N+S renders the file as written; N renders a copy (``stripped``) without its ``ArchFlow_*``
    property sets. ``names`` are B's object names: every product's Name is checked against them."""

    import ifcopenshell

    written = ifcopenshell.open(str(path))
    with_state = render_ifc(written)
    products = [p for p in written.by_type("IfcProduct") if not _spatial(p)]
    model = ifcopenshell.open(str(path))
    report = strip_archflow(model, set(names))
    model.write(str(stripped))
    native = render_ifc(ifcopenshell.open(str(stripped)))
    product_names = {p.Name for p in products}
    report["names"] = {"products": len(products), "readback_objects": len(names),
                       "match_readback": product_names == set(names),
                       "products_not_in_readback": sorted(n or "" for n in product_names - set(names)),
                       "readback_not_in_products": sorted(set(names) - product_names)}
    return {"N": native, "N+S": with_state, "report": report,
            "global_ids": {p.GlobalId: p.Name for p in products if p.Name}}


def family_inputs(family: str, root: Path, arms: tuple[str, ...] = ARMS) -> dict[str, Any]:
    """The materials of every arm for one family, and the start model the answers are executed against. AG, N and N+S
    are added when ``arms`` names them; S is added by ``run`` once the family's summary is frozen."""

    record = x1.FAMILY_FIXTURE[family]()
    fam = root / "inputs" / family
    fam.mkdir(parents=True)
    pack_read = x1.production_pack(family, record, fam / "production")
    full = reviser.write_state_file(record, arm="C-full", fmt="3dm", out_dir=fam, stem="c-full", parent_sha256=None,
                                    context_root=fam / "ctx-full", utterance="Current design state.")
    task = reviser.write_state_file(record, arm="C-task", fmt="3dm", out_dir=fam, stem="c-task", parent_sha256=None,
                                    context_root=fam / "ctx-task", utterance="Current design state.", context_pack=pack_read["pack"])
    embedded = _strings(task.path)["archflow:state"]
    state = json.loads(embedded)
    stripped = fam / "geometry-only.3dm"
    _rewrite(full.path, stripped, strings={k: None for k in _strings(full.path) if k.startswith("archflow:")})
    events = json.loads(x1.HISTORY.read_text(encoding="utf-8"))["families"][family]["events"]
    objects = readback(stripped)
    materials = {"A": render({"events": events}), "B": render({"objects": objects}),
                 "C": render(without_guards(state)), "D": render(state), "F": embedded}
    extra: dict[str, Any] = {}
    if "AG" in arms:
        materials["AG"] = with_readback(materials["A"], materials["B"])
    if "N" in arms or "N+S" in arms:
        # The C-task IFC as x1.run writes it: the same record, pack and focus.
        ifc = reviser.write_state_file(record, arm="C-task", fmt="ifc", out_dir=fam, stem="c-task-ifc", parent_sha256=None,
                                       context_root=fam / "ctx-task-ifc", utterance="Current design state.",
                                       focus=tuple(sorted(e.entity_id for e in record.entities_of("Element@1"))),
                                       context_pack=pack_read["pack"])
        native = ifc_materials(ifc.path, fam / "c-task-native.ifc", [o["name"] for o in objects])
        materials.update({arm: native[arm] for arm in ("N", "N+S")})
        extra = {"ifc": native["report"], "global_ids": native["global_ids"]}
        (fam / "ifc-report.json").write_text(json.dumps(native["report"], indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for arm, text in materials.items():
        (fam / f"materials-{arm}.txt").write_text(text, encoding="utf-8", newline="\n")
    return {"record": record, "materials": materials, "start_model": full.realization.preview_path,
            "objects_by_element": full.realization.objects_by_element, **extra}


# ---------------------------------------------------------------- S: the summary, frozen per family

def estimated_tokens(text: str) -> int:
    """The estimate stated for batch 5: characters (code points) over four, rounded up."""

    return math.ceil(len(text) / 4)


def cap_summary(text: str, cap: int) -> tuple[str, bool]:
    """``text`` cut to at most ``cap`` characters at a line boundary (the last whole line that fits), and whether it
    was cut. A first line longer than the cap is cut at the cap itself."""

    if len(text) <= cap:
        return text, False
    head = text[:cap]
    if text[cap] != "\n" and "\n" in head:
        head = head[:head.rfind("\n")]
    return head.rstrip(), True


class SummaryRefused(RuntimeError):
    pass


def summary_prompt(family: str, history: str, cap: int, prompts: Path | None = None) -> tuple[Path, str]:
    """S's generation prompt for one family: ``s_summary.md`` with ``{cap}`` filled, then A's text in ``{materials}``.

    The filled instance is written once beside the template (``s_summary_<family>.md``) so that it is hashed with the
    prompt files; a later run must find it unchanged."""

    prompts = prompts or PROMPTS
    instance = (prompts / S_TEMPLATE).read_text(encoding="utf-8").replace("{cap}", str(cap))
    path = prompts / f"s_summary_{family}.md"
    if path.exists():
        if path.read_text(encoding="utf-8") != instance:
            raise SummaryRefused(f"{path.name} differs from {S_TEMPLATE} filled with cap {cap}: the family's D material has changed")
    else:
        path.write_text(instance, encoding="utf-8", newline="\n")
    return path, instance.replace("{materials}", history)


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen_summary(family: str, *, history: str, cap: int, folder: Path, cli: str | None, config: Path, ledger: Ledger | None,
                   part: str, out: Path, reuse: Path | None = None, prompts: Path | None = None) -> dict[str, Any]:
    """S's material for one family, frozen as ``<folder>/<family>.md`` beside ``<family>.json`` (its SHA-256 and how it
    was made). A frozen summary already in ``folder`` or in ``reuse`` (an earlier run's folder) is used when its SHA-256
    and the prompt it was written from match; otherwise one ``claude -p`` call writes it: x2's command and fairness flags,
    one turn, no tools, no appended system prompt; the exact prompt is kept as ``<family>.prompt.md``. Its cost is booked to
    ``part``. The answer's surrounding whitespace is stripped; a summary longer than ``cap`` characters is then cut at a line
    boundary (``truncated``). Without ``cli`` a marked stand-in takes the summary's place and no call is made."""

    folder.mkdir(parents=True, exist_ok=True)
    prompt_path, prompt = summary_prompt(family, history, cap, prompts)
    for source in (folder, reuse):
        if source is None or not (source / f"{family}.json").exists():
            continue
        meta = json.loads((source / f"{family}.json").read_text(encoding="utf-8"))
        text = (source / f"{family}.md").read_bytes().decode("utf-8")
        if _sha_text(text) != meta.get("sha256"):
            raise SummaryRefused(f"{source / family}.md does not match its recorded SHA-256")
        if meta.get("prompt_sha256") != _sha_text(prompt):
            raise SummaryRefused(f"{source / family}.md was written from another prompt (A's text or the cap differ)")
        if meta.get("stand_in") and cli:
            raise SummaryRefused(f"{source / family}.md is a dry-run stand-in")
        if source != folder:
            meta = {**meta, "reused_from": str(source)}
            (folder / f"{family}.md").write_bytes(text.encode("utf-8"))
            (folder / f"{family}.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return {**meta, "text": text}
    meta: dict[str, Any] = {"family": family, "prompt_file": prompt_path.name, "prompt_file_sha256": taskspec.file_sha256(prompt_path),
                            "template_sha256": taskspec.file_sha256(prompt_path.parent / S_TEMPLATE),
                            "prompt_sha256": _sha_text(prompt), "prompt_chars": len(prompt), "cap_chars": cap,
                            "cap_rule": "the characters of the family's D material", "token_estimate": "characters / 4, rounded up",
                            "model": MODEL, "command": command(cli or "claude", config, system=None),
                            "appended_system_prompt": None, "environment": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}}
    (folder / f"{family}.prompt.md").write_bytes(prompt.encode("utf-8"))
    if cli is None:
        generated = S_STAND_IN.format(family=family)
        meta.update({"stand_in": True, "cost_usd": 0.0})
    else:
        ledger.check(part)
        call = call_model(prompt, folder / f"{family}-call", cli=cli, config=config, system=None)
        cost, priced = price(call["result"])
        ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X2", "part": part, "trial_id": f"X2-S-summary-{family}",
                    "out": str(out), "cost_usd": cost, **priced})
        generated = call["text"].strip()
        meta.update({"stand_in": False, "cost_usd": cost, "usage": priced, "exit_code": call["exit_code"], "seconds": round(call["seconds"], 1),
                     "init": call["init"], "stderr": call["stderr"]})
        if call["exit_code"] != 0 or not generated:
            (folder / f"{family}.failed.json").write_text(json.dumps(meta, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8",
                                                        newline="\n")
            raise SummaryRefused(f"the S summary call for {family} returned no text (exit {call['exit_code']})")
    text, truncated = cap_summary(generated, cap)
    meta.update({"generated_at": datetime.now(timezone.utc).isoformat(), "truncated": truncated,
                 "chars_generated": len(generated), "chars": len(text),
                 "estimated_tokens_generated": estimated_tokens(generated), "estimated_tokens": estimated_tokens(text),
                 "sha256": _sha_text(text)})
    (folder / f"{family}.md").write_bytes(text.encode("utf-8"))
    (folder / f"{family}.json").write_text(json.dumps(meta, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return {**meta, "text": text}


def prompt_for(arm: str, utterance: str, materials: str) -> str:
    intros = json.loads((PROMPTS / "materials.json").read_text(encoding="utf-8"))
    template = (PROMPTS / "request.md").read_text(encoding="utf-8")
    return template.replace("{utterance}", utterance).replace("{intro}", intros[arm]).replace("{materials}", materials)


def prompt_files_sha256() -> dict[str, str]:
    return {p.name: taskspec.file_sha256(p) for p in sorted(PROMPTS.iterdir()) if p.is_file()}


# ---------------------------------------------------------------- one call

def command(cli: str, config: Path, max_turns: int = 1, tools: str = "", system: Path | None = PROMPTS / "system_common.md") -> list[str]:
    """The call's argv. ``system=None`` appends no system prompt (S's summary call); every other flag stays."""

    appended = ["--append-system-prompt-file", str(system)] if system is not None else []
    return [cli, "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--setting-sources", "project,local", "--disable-slash-commands", "--max-turns", str(max_turns), "--tools", tools,
            "--disallowedTools", "WebSearch,WebFetch", "--strict-mcp-config", "--mcp-config", str(config / "mcp.json"),
            *appended, "--settings", str(config / "settings.json"),
            "--no-session-persistence", "--max-budget-usd", str(PER_CALL_USD)]


def write_config(config: Path) -> None:
    config.mkdir(parents=True, exist_ok=True)
    (config / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({"disableBundledSkills": True}), encoding="utf-8")


def price(result: dict[str, Any], cli_offset: float = 0.0) -> tuple[float, dict[str, Any]]:
    """API-equivalent cost of one call: MonkeyMonitor's quote at the manifest rates, or the CLI's own if larger.

    A resumed CLI session reports ``total_cost_usd`` for the whole session so far (its ``usage`` is the turn's own):
    ``cli_offset`` is that session's total before this turn, so the turn is booked by what it added."""

    from monkeymonitor.pricing import RateCard, quote
    from monkeymonitor.usage import TokenUsage

    usage = result.get("usage") or {}
    plain, write, read = (int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    hour = int((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
    tokens = TokenUsage(input_tokens=plain + write + read, cached_input_tokens=read, cache_write_input_tokens=write,
                        cache_write_1h_input_tokens=hour, output_tokens=int(usage.get("output_tokens") or 0))
    quoted = quote(tokens, RateCard(provider="claude", model=MODEL, label="manifest v3.2 section 4 API equivalent", **RATES))
    monitor = float(quoted["amount_usd"] or quoted["known_subtotal_usd"])
    cli_total = float(result.get("total_cost_usd") or 0.0)
    cli = max(0.0, cli_total - cli_offset)
    return max(monitor, cli), {"tokens": tokens.to_dict(), "monkeymonitor_usd": monitor, "cli_total_cost_usd": cli_total,
                               "cli_turn_cost_usd": cli, "cli_offset_usd": cli_offset}


class Ledger:
    """Append-only JSON lines shared by every run that spends from the same caps."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def spent(self, part: str | None = None) -> float:
        if not self.path.exists():
            return 0.0
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return sum(r["cost_usd"] for r in rows if part is None or r["part"] == part)

    def spent_by(self, out: Path) -> float:
        """What one run booked: the rows whose turn directory lies under ``out``. Runs that share a part, at the same time
        or not, do not count each other."""

        if not self.path.exists():
            return 0.0
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        root = str(Path(out)) + os.sep
        return sum(r["cost_usd"] for r in rows if str(r.get("out", "")).startswith(root))

    def check(self, part: str) -> None:
        if self.spent(part) + PER_CALL_USD > CAPS[part]:
            raise CapReached(f"part {part}: {self.spent(part):.2f} of {CAPS[part]:.2f} spent")
        if self.spent() + PER_CALL_USD > TOTAL_CAP:
            raise CapReached(f"total: {self.spent():.2f} of {TOTAL_CAP:.2f} spent")

    def add(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def call_model(prompt: str, trial: Path, *, cli: str, config: Path, system: Path | None = PROMPTS / "system_common.md") -> dict[str, Any]:
    trial.mkdir(parents=True)
    started = time.perf_counter()
    # As MonkeyHub's own chat runs the CLI: Claude Code's auto-memory off, so no memory section or memory file reaches the model.
    environment = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    done = subprocess.run(command(cli, config, system=system), input=prompt, cwd=trial, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, env=environment)
    seconds = time.perf_counter() - started
    (trial.parent / f"{trial.name}.stream.jsonl").write_text(done.stdout, encoding="utf-8", newline="\n")
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    return {"exit_code": done.returncode, "stderr": done.stderr[-2000:], "seconds": seconds, "result": result,
            "text": result.get("result") or "", "init": {k: init.get(k) for k in ("model", "tools", "mcp_servers", "permissionMode",
                                                                                     "slash_commands", "skills", "plugins", "claude_code_version")}}


def parse_answer(text: str) -> dict[str, Any] | None:
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        answer = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


# ---------------------------------------------------------------- execution and score

def map_changes(changes: dict[str, Any], arm: str, family: str, record) -> tuple[dict[str, float] | None, str | None]:
    evaluated = evaluate_parameters(record)
    values = {p.key: float(evaluated[p.key]) for p in record.parameters}
    history = x1.HISTORY_PARAMETERS[family]
    arm = READ_AS.get(arm, arm)
    mapped: dict[str, float] = {}
    for name, value in changes.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None, f"NOT_A_NUMBER: {name}"
        if arm == "A" and name in history:
            key, convert = history[name]
            mapped[key] = float(convert(value, values))
        elif arm != "B" and name.removeprefix("parameter:") in values:
            mapped[name.removeprefix("parameter:")] = float(value)
        else:
            return None, f"UNKNOWN_PARAMETER: {name}"
    return mapped, None


def execute(answer: dict[str, Any], arm: str, family: str, task: dict[str, Any], inputs: dict[str, Any], trial: Path) -> dict[str, Any]:
    changes = answer.get("changes")
    if answer.get("verdict") not in ("held", "violated") or not isinstance(changes, dict) or not changes:
        return {"executed": False, "why": "no changes to execute", "oracle_verdict": "unavailable"}
    mapped, error = map_changes(changes, arm, family, inputs["record"])
    if mapped is None:
        return {"executed": False, "why": error, "oracle_verdict": "unavailable"}
    record = fixtures.bound(inputs["record"])
    # A derived parameter in the answer is a statement about the result: it must equal what the inputs give.
    derived = {p.key for p in record.parameters if p.expr}
    try:
        for key, value in mapped.items():
            if key in derived:
                continue
            record = apply_state_record_operator(record, StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                target_ref=f"parameter:{key}", key=key, value=value))
        evaluated = evaluate_parameters(record)
        conflicts = sorted(k for k, v in mapped.items() if k in derived and abs(float(evaluated[k]) - v) > 1e-6)
        if conflicts:
            return {"executed": False, "why": f"DERIVED_CONFLICT: {', '.join(conflicts)}", "mapped": mapped, "oracle_verdict": "unavailable"}
        if all(k in derived for k in mapped):
            return {"executed": False, "why": "only derived values given", "mapped": mapped, "oracle_verdict": "unavailable"}
        work = trial.parent / f"{trial.name}.candidate"
        work.mkdir()
        candidate = pipeline.realize(record, work.resolve(), "candidate")
    except (StateRecordError, pipeline.RealizationError) as exc:
        return {"executed": False, "why": f"{type(exc).__name__}: {exc}"[:300], "mapped": mapped, "oracle_verdict": "unavailable"}
    spec_path = LAB / "tasks" / f"{task['task_id']}-01.json"
    if not spec_path.exists():
        return {"executed": True, "mapped": mapped, "oracle_verdict": "unavailable", "why": "no oracle task"}
    spec = taskspec.load(spec_path)
    if taskspec.start_record(spec).digest != inputs["record"].digest:
        return {"executed": True, "mapped": mapped, "oracle_verdict": "unavailable", "why": "the oracle task starts elsewhere"}
    report = runner.run_oracle(spec_path, candidate.preview_path, inputs["start_model"], trial.parent / f"{trial.name}.oracle.json")
    return {"executed": True, "mapped": mapped, "oracle_verdict": report.get("verdict", "unchecked"),
            "oracle_violated": [r.get("id") for r in report.get("relations", []) if r.get("status") == "violated"]}


def read_names(answer: dict[str, Any] | None, arm: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
    """N and N+S show each product's GlobalId beside its Name: an ``affected`` entry that is a product's GlobalId is
    read as that product's Name (the object name B shows) before x2's mapping. Every other arm's answer is unchanged."""

    if arm not in ("N", "N+S") or answer is None or not isinstance(answer.get("affected"), list):
        return answer
    names = inputs.get("global_ids") or {}
    return {**answer, "affected": [names.get(str(a), a) for a in answer["affected"]]}


def _element_ids(names: Any, inputs: dict[str, Any]) -> list[str]:
    if not isinstance(names, list):
        return []
    owner = runner.element_of(inputs["objects_by_element"])
    elements = set(inputs["objects_by_element"])
    out = set()
    for name in names:
        name = str(name).removeprefix("entity:")
        out.add(name if name in elements else owner.get(name, owner.get(name.removeprefix("obj-"), name.removeprefix("obj-"))))
    return sorted(out)


def score(answer: dict[str, Any] | None, task: dict[str, Any], family: str, execution: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    verdict = (answer or {}).get("verdict")
    gold = task["gold"]
    affected = _element_ids((answer or {}).get("affected"), inputs)
    precision, recall = x1._set_scores(affected, gold["affected"])
    declared = x1.HISTORY_DECLARATIONS[family]
    conditions = {declared.get(str(c), str(c)) or str(c) for c in ((answer or {}).get("conditions") or []) if isinstance(c, str)}
    _, recheck_recall = x1._set_scores(sorted(conditions), gold["recheck"])
    return {"parsed": answer is not None, "verdict": verdict, "verdict_ok": verdict in VERDICTS and x1._ok(verdict, task),
            "affected": affected, "affected_precision": precision, "affected_recall": recall, "recheck_recall": recheck_recall,
            "false_accept": verdict == "held" and ("held" not in gold["verdicts"] or execution.get("oracle_verdict") == "violated"),
            "usable_successor": bool(execution.get("executed")) and execution.get("oracle_verdict") == "held"}


# ---------------------------------------------------------------- the run

def batch5_inputs(out: Path, inputs: dict[str, dict[str, Any]], arms: tuple[str, ...], *, cli: str | None, config: Path,
                  ledger: Ledger | None, part: str, summaries: Path | None) -> tuple[dict[str, Any], str | None]:
    """S's frozen summaries (when S runs), and every family's material sizes and IFC report, into ``batch5_inputs.json``.
    Returns that report and why the run stops before its first task call, if it does."""

    report: dict[str, Any] = {"token_estimate": "characters / 4, rounded up", "families": {}}
    stopped = None
    for family, given in inputs.items():
        cell: dict[str, Any] = {"s_cap_chars": len(given["materials"]["D"])}
        if "S" in arms and stopped is None:
            try:
                frozen = frozen_summary(family, history=given["materials"]["A"], cap=len(given["materials"]["D"]),
                                        folder=out / "summaries", cli=cli, config=config, ledger=ledger, part=part, out=out,
                                        reuse=summaries)
            except (CapReached, SummaryRefused) as exc:
                stopped = f"S summary for {family}: {exc}"
            else:
                given["materials"]["S"] = frozen["text"]
                (out / "inputs" / family / "materials-S.txt").write_bytes(frozen["text"].encode("utf-8"))
                cell["s_summary"] = {k: v for k, v in frozen.items() if k != "text"}
        cell["materials"] = {arm: {"chars": len(text), "estimated_tokens": estimated_tokens(text)} for arm, text in given["materials"].items()}
        if "ifc" in given:
            cell["ifc"] = given["ifc"]
        report["families"][family] = cell
    truncated = [f for f, c in report["families"].items() if (c.get("s_summary") or {}).get("truncated")]
    report["s_truncated_families"] = {"count": len(truncated), "families": truncated}
    report["stopped"] = stopped
    (out / "batch5_inputs.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return report, stopped


def run(out: Path, *, families: tuple[str, ...], arms: tuple[str, ...], repeats: int, part: str, ledger: Ledger | None,
        cli: str | None, task_ids: tuple[str, ...] | None = None, summaries: Path | None = None) -> dict[str, Any]:
    """``summaries``: an earlier run's ``summaries`` folder, whose frozen S summaries this run reuses."""

    tasks = [t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]
             if t["family"] in families and (task_ids is None or t["task_id"] in task_ids)]
    config = out / "config"
    write_config(config)
    identity = {"harness": runner.harness_identity(), "prompt_files_sha256": prompt_files_sha256(), "model": MODEL,
                "command": command("claude", config)[1:], "rates": RATES, "per_call_usd": PER_CALL_USD, "part": part}
    if cli:
        identity["cli_version"] = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
    (out / "identity.json").write_text(json.dumps(identity, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    inputs = {family: family_inputs(family, out, arms) for family in families}
    rows: list[dict[str, Any]] = []
    stopped = None
    batch5 = None
    if any(arm in BATCH5_ARMS for arm in arms):
        batch5, stopped = batch5_inputs(out, inputs, arms, cli=cli, config=config, ledger=ledger, part=part, summaries=summaries)
    for index, task in enumerate(tasks):
        if stopped:
            break
        family = task["family"]
        for repeat in range(repeats):
            shift = (index + repeat) % len(arms)
            for slot, arm in enumerate(arms[shift:] + arms[:shift]):
                trial_id = f"X2-{arm}-{task['task_id']}-r{repeat}"
                prompt = prompt_for(arm, task["utterance"], inputs[family]["materials"][arm])
                trial = out / "trials" / trial_id
                row = {"trial_id": trial_id, "task_id": task["task_id"], "arm": arm, "repeat_idx": repeat, "order_slot": slot,
                       "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_chars": len(prompt)}
                if cli is None:
                    trial.parent.mkdir(parents=True, exist_ok=True)
                    (trial.parent / f"{trial_id}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
                    rows.append(row)
                    continue
                try:
                    ledger.check(part)
                except CapReached as exc:
                    stopped = str(exc)
                    break
                (out / "trials").mkdir(exist_ok=True)
                (trial.parent / f"{trial_id}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
                call = call_model(prompt, trial, cli=cli, config=config)
                cost, priced = price(call["result"])
                ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X2", "part": part, "trial_id": trial_id,
                            "out": str(out), "cost_usd": cost, **priced})
                answer = parse_answer(call["text"])
                execution = execute(answer, arm, family, task, inputs[family], trial) if answer else {"executed": False, "oracle_verdict": "unavailable"}
                scored = score(read_names(answer, arm, inputs[family]), task, family, execution, inputs[family])
                bundle = {"trial": row, "task": task, "call": {k: call[k] for k in ("exit_code", "seconds", "init", "text", "stderr")},
                          "usage": priced, "cost_usd": cost, "answer": answer, "execution": execution, "score": scored}
                (trial.parent / f"{trial_id}.bundle.json").write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
                row.update({**{k: scored[k] for k in ("parsed", "verdict", "verdict_ok", "affected_precision", "affected_recall",
                                                       "recheck_recall", "false_accept", "usable_successor")},
                            "executed": execution.get("executed"), "oracle_verdict": execution.get("oracle_verdict"),
                            "gold_verdicts": "|".join(task["gold"]["verdicts"]), "cost_usd": round(cost, 4),
                            "input_tokens": priced["tokens"]["input_tokens"], "output_tokens": priced["tokens"]["output_tokens"],
                            "seconds": round(call["seconds"], 1)})
                rows.append(row)
                print(f"{trial_id:22} {str(scored['verdict']):13} ok={scored['verdict_ok']!s:5} exec={execution.get('executed')!s:5} "
                      f"oracle={execution.get('oracle_verdict'):11} ${cost:.3f}", flush=True)
            if stopped:
                break
        if stopped:
            break
    if rows:
        fields = sorted({k for row in rows for k in row}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
        with (out / "x2.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    summary: dict[str, Any] = {"stopped": stopped, "spent_part_usd": ledger.spent(part) if ledger else 0.0,
                               "spent_total_usd": ledger.spent() if ledger else 0.0, "by_arm": {}}
    for row in rows:
        if "verdict_ok" not in row:
            continue
        cell = summary["by_arm"].setdefault(row["arm"], {"trials": 0, "verdict_ok": 0, "false_accept": 0, "usable_successor": 0,
                                                         "unparsed": 0, "cost_usd": 0.0, "input_tokens": []})
        cell["trials"] += 1
        cell["verdict_ok"] += int(row["verdict_ok"])
        cell["false_accept"] += int(row["false_accept"])
        cell["usable_successor"] += int(row["usable_successor"])
        cell["unparsed"] += int(not row["parsed"])
        cell["cost_usd"] = round(cell["cost_usd"] + row["cost_usd"], 4)
        cell["input_tokens"].append(row["input_tokens"])
    for cell in summary["by_arm"].values():
        tokens = sorted(cell.pop("input_tokens"))
        cell["median_input_tokens"] = tokens[len(tokens) // 2] if tokens else None
    if batch5 is not None:
        summary["s_truncated_families"] = batch5["s_truncated_families"]
    (out / "x2_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run X2 single-call arms into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1")
    parser.add_argument("--tasks", help="comma-separated task ids (default: every task of the families)")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--part", default="pilot", choices=sorted(CAPS))
    parser.add_argument("--ledger", type=Path, help="the shared cost ledger (required with --cli)")
    parser.add_argument("--cli", help="the claude executable; without it, only the prompts are written (S: a marked stand-in)")
    parser.add_argument("--summaries", type=Path, help="S: an earlier run's summaries folder, whose frozen summaries are reused")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    if args.cli and not args.ledger:
        parser.error("--ledger is required with --cli")
    arms = tuple(args.arms.split(","))
    unknown = [arm for arm in arms if arm not in ARMS + BATCH5_ARMS]
    if unknown:
        parser.error(f"unknown arms: {', '.join(unknown)}")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, families=tuple(args.families.split(",")), arms=arms, repeats=args.repeats,
                  part=args.part, ledger=Ledger(args.ledger.resolve()) if args.ledger else None, cli=args.cli,
                  task_ids=tuple(args.tasks.split(",")) if args.tasks else None,
                  summaries=args.summaries.resolve() if args.summaries else None)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
