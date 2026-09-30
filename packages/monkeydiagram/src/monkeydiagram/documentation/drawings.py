"""Validate caller-supplied drawing data without writing or choosing project paths.

Coordinates on paper are millimetres from the bottom left. Rectangles use
``[xmin, ymin, xmax, ymax]``. Dimension endpoints and values share the source
model's millimetre datum. A scale is its denominator, not a fit-to-page factor.
The adjacent JSON Schema describes structure only; the checks here express
cross-record drawing rules. Neither check establishes source-model correctness.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from math import isclose, isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class DrawingFinding:
    severity: str
    code: str
    subject: str
    detail: str


@dataclass(frozen=True, slots=True)
class DrawingPlan:
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.data)


_COLLECTIONS = (
    "sheets", "views", "dimensions", "dimension_chains", "references",
    "components", "materials", "schedules", "notes", "interfaces", "revisions",
)
_SYSTEMS = (
    "SheetSystem", "ViewSystem", "GraphicSystem", "AnnotationSystem",
    "InformationSystem", "StatusSystem", "CoordinationSystem", "RevisionSystem",
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _texts(value: Any) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def _rect(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 4 and all(_number(v) for v in value)


def _source(value: Any) -> bool:
    return _text(value) or isinstance(value, dict) and bool(value)


def _structure(state: Any) -> list[DrawingFinding]:
    """Check the input shapes used below, without imposing closed object schemas."""
    findings: list[DrawingFinding] = []

    def check(valid: bool, subject: str, expected: str) -> None:
        if not valid:
            findings.append(DrawingFinding("error", "INVALID_SHAPE", subject, expected))

    if not isinstance(state, dict):
        check(False, "state", "DrawingState must be an object")
        return findings
    for field in ("source", "project"):
        check(isinstance(state.get(field), dict) and bool(state[field]), field, "Expected a nonempty object")
    text_fields = {
        "sheets": ("number", "title", "purpose"),
        "views": ("id", "sheet", "number", "name", "drawing_type", "purpose"),
        "dimensions": ("id", "view_id", "axis", "datum", "role", "certainty", "representation", "use"),
        "dimension_chains": ("id", "overall_dimension_id"),
        "references": ("source_view_id", "target_view_id"),
        "components": ("object_id", "tag", "material_tag", "certainty", "representation"),
        "materials": ("tag", "name", "certainty"),
        "schedules": ("object_id", "component_tag", "material_tag"),
        "notes": ("id", "text"),
        "interfaces": ("id", "status"),
        "revisions": ("id", "date", "description", "issue_status"),
    }
    list_fields = {
        "sheets": ("view_ids",), "views": ("object_ids",),
        "dimensions": ("actions",), "dimension_chains": ("dimension_ids",),
        "components": ("actions",), "notes": ("view_ids",),
        "interfaces": ("object_ids",), "revisions": ("changed_view_ids",),
    }
    for name in _COLLECTIONS:
        entries = state.get(name)
        check(isinstance(entries, list), name, "Expected a list, possibly empty")
        if not isinstance(entries, list):
            continue
        for index, item in enumerate(entries):
            subject = f"{name}[{index}]"
            check(isinstance(item, dict), subject, "Expected an object")
            if not isinstance(item, dict):
                continue
            for field in text_fields[name]:
                check(_text(item.get(field)), f"{subject}.{field}", "Expected nonempty text")
            for field in list_fields.get(name, ()):
                check(_texts(item.get(field)), f"{subject}.{field}", "Expected a list of nonempty strings")
            if name == "sheets":
                size = item.get("size_mm")
                check(isinstance(size, list) and len(size) == 2 and all(_number(v) for v in size),
                      f"{subject}.size_mm", "Expected [width, height] in paper mm")
                check(_rect(item.get("printable_rect_mm")), f"{subject}.printable_rect_mm", "Expected [xmin, ymin, xmax, ymax]")
                if "primitive_bounds_mm" in item:
                    bounds = item["primitive_bounds_mm"]
                    check(isinstance(bounds, list) and all(_rect(v) for v in bounds),
                          f"{subject}.primitive_bounds_mm", "Expected a list of paper rectangles")
            elif name == "views":
                check("scale" in item and (item["scale"] is None or _number(item["scale"])),
                      f"{subject}.scale", "Expected a numeric denominator or null for an unscaled schedule")
                check(_rect(item.get("bounds_mm")), f"{subject}.bounds_mm", "Expected a paper rectangle")
                if "scale_group" in item:
                    check(item["scale_group"] is None or _text(item["scale_group"]), f"{subject}.scale_group", "Expected text or null")
                if "scale_exception_reason" in item:
                    check(_text(item["scale_exception_reason"]), f"{subject}.scale_exception_reason", "Expected a reason")
                if "information" in item:
                    check(_texts(item["information"]), f"{subject}.information", "Expected information keys")
            elif name == "dimensions":
                for field in ("start", "end", "value"):
                    check(_number(item.get(field)), f"{subject}.{field}", "Expected a finite model-mm coordinate/value")
                check(_source(item.get("source")), f"{subject}.source", "Expected a source reference or object")
                if isinstance(item.get("source"), dict):
                    for field in ("dimension_ids", "object_ids"):
                        if field in item["source"]:
                            check(_texts(item["source"][field]), f"{subject}.source.{field}", "Expected source ids")
            elif name == "materials" and "thickness_mm" in item:
                check(_number(item["thickness_mm"]), f"{subject}.thickness_mm", "Expected a finite thickness")
            elif name == "components" and "construction_scope" in item:
                check(_text(item["construction_scope"]), f"{subject}.construction_scope", "Expected a construction scope")
            elif name == "schedules" and "view_id" in item:
                check(_text(item["view_id"]), f"{subject}.view_id", "Expected a view id")
            elif name == "notes":
                check(isinstance(item.get("repeat_allowed"), bool), f"{subject}.repeat_allowed", "Expected an explicit boolean")
            elif name == "interfaces":
                for field in ("design_provides", "field_verify", "vendor_provides"):
                    check(field in item and (_text(item[field]) or _texts(item[field])),
                          f"{subject}.{field}", "Expected text or a list; an empty list explicitly means no item")
                if "view_ids" in item:
                    check(_texts(item["view_ids"]), f"{subject}.view_ids", "Expected view ids")
            elif name == "revisions":
                check("issued_by" in item and (item["issued_by"] is None or _text(item["issued_by"])),
                      f"{subject}.issued_by", "Expected a named issuer or explicit null for draft")
                if "changed_object_ids" in item:
                    check(_texts(item["changed_object_ids"]), f"{subject}.changed_object_ids", "Expected object ids")
                if "changes" in item:
                    check(isinstance(item["changes"], list), f"{subject}.changes", "Expected a list of described changes")
    return findings


def validate_drawing_state(state: dict[str, Any], standard: dict[str, Any]) -> tuple[DrawingFinding, ...]:
    """Return structural and drawing-rule findings; do not infer missing facts.

    The caller loads the standard. Optional ``view.information`` keys declare
    intended content; this checks that declaration, not the rendered geometry.
    ``sheet.primitive_bounds_mm`` must come from the renderer's actual text and
    stroke extents for the page-boundary check to cover those marks.
    """
    findings = _structure(state)

    def error(code: str, subject: str, detail: str) -> None:
        findings.append(DrawingFinding("error", code, subject, detail))

    if not isinstance(standard, dict) or any(not isinstance(standard.get(key), dict) for key in _SYSTEMS):
        error("INVALID_STANDARD", "standard", "Expected the eight Drawing Standard systems")
        return tuple(findings)
    try:
        preferred = standard["ViewSystem"]["preferred_scale_denominators"]
        tolerance = standard["AnnotationSystem"]["dimension_tolerance_mm"]
        roles = standard["AnnotationSystem"]["dimension_roles"]
        drawing_types = standard["InformationSystem"]["drawing_types"]
        status = standard["StatusSystem"]
        for key in ("certainty", "representation", "actions", "dimension_use"):
            if not _texts(status[key]):
                raise ValueError(key)
        if not isinstance(preferred, list) or not preferred or not all(_number(v) and v > 0 for v in preferred):
            raise ValueError("preferred scales")
        if not _number(tolerance) or tolerance <= 0 or not _texts(roles) or not isinstance(drawing_types, dict):
            raise ValueError("annotation rules or drawing types")
        for rule in drawing_types.values():
            if not isinstance(rule, dict) or any(not _texts(rule[key]) for key in ("required", "optional", "forbidden")):
                raise ValueError("information rules")
    except (KeyError, TypeError, ValueError) as exc:
        error("INVALID_STANDARD", "standard", f"Missing or invalid rule: {exc}")
        return tuple(findings)
    # A configurable style cannot grant stronger factual certainty or issuance.
    if status.get("control_requires_certainty") != "CONFIRMED":
        error("INVALID_STANDARD", "StatusSystem.control_requires_certainty", "Control dimensions require CONFIRMED source facts")
    if set(status["certainty"]) != {"CONFIRMED", "ASSUMED", "UNRESOLVED"}:
        error("INVALID_STANDARD", "StatusSystem.certainty", "Certainty has the three separate factual states")
    if findings:
        return tuple(findings)

    def close(a: float, b: float) -> bool:
        return isclose(a, b, rel_tol=0.0, abs_tol=tolerance)

    def index_by(collection: str, field: str) -> dict[str, dict[str, Any]]:
        result = {}
        for item in state[collection]:
            key = item[field]
            if key in result:
                error("DUPLICATE_ID", f"{collection}:{key}", f"Duplicate {field}")
            result[key] = item
        return result

    sheets = index_by("sheets", "number")
    views = index_by("views", "id")
    dimensions = index_by("dimensions", "id")
    components = index_by("components", "object_id")
    materials = index_by("materials", "tag")
    index_by("dimension_chains", "id")
    index_by("interfaces", "id")
    index_by("revisions", "id")
    if not sheets:
        error("EMPTY_DRAWING", "sheets", "At least one sheet is required")

    def reference(value: str, records: dict, subject: str) -> bool:
        if value not in records:
            error("INVALID_REFERENCE", subject, f"Unknown target {value!r}")
            return False
        return True

    def contained(rect: list, outer: list, subject: str) -> None:
        if rect[0] > rect[2] or rect[1] > rect[3]:
            error("INVALID_BOUNDS", subject, "Rectangle minima must not exceed maxima")
        elif any((rect[0] < outer[0] - tolerance, rect[1] < outer[1] - tolerance,
                  rect[2] > outer[2] + tolerance, rect[3] > outer[3] + tolerance)):
            error("OUTSIDE_PRINTABLE_AREA", subject, "Paper bounds extend outside the printable rectangle")

    view_numbers: set[tuple[str, str]] = set()
    scales: dict[str, float] = {}
    for sheet in state["sheets"]:
        subject = f"sheet:{sheet['number']}"
        width, height = sheet["size_mm"]
        if width <= 0 or height <= 0:
            error("INVALID_SHEET_SIZE", subject, "Paper dimensions must be positive")
        contained(sheet["printable_rect_mm"], [0, 0, width, height], subject)
        if sheet["printable_rect_mm"][0] == sheet["printable_rect_mm"][2] or sheet["printable_rect_mm"][1] == sheet["printable_rect_mm"][3]:
            error("INVALID_BOUNDS", subject, "Printable rectangle must have positive area")
        for view_id, count in Counter(sheet["view_ids"]).items():
            if count > 1:
                error("DUPLICATE_VIEW_PLACEMENT", subject, f"View {view_id} is listed more than once")
            if reference(view_id, views, subject) and views[view_id]["sheet"] != sheet["number"]:
                error("VIEW_SHEET_MISMATCH", subject, f"View {view_id} belongs to {views[view_id]['sheet']}")
        for i, bounds in enumerate(sheet.get("primitive_bounds_mm", [])):
            contained(bounds, sheet["printable_rect_mm"], f"{subject}.primitive[{i}]")
    for view in state["views"]:
        subject = f"view:{view['id']}"
        number_key = (view["sheet"], view["number"])
        if number_key in view_numbers:
            error("DUPLICATE_VIEW_NUMBER", subject, "View number is already used on this sheet")
        view_numbers.add(number_key)
        if reference(view["sheet"], sheets, subject):
            sheet = sheets[view["sheet"]]
            if view["id"] not in sheet["view_ids"]:
                error("VIEW_SHEET_MISMATCH", subject, "Sheet does not list this view")
            contained(view["bounds_mm"], sheet["printable_rect_mm"], subject)
        rule = drawing_types.get(view["drawing_type"])
        if rule is None:
            error("INVALID_DRAWING_TYPE", subject, f"Unknown drawing type {view['drawing_type']}")
            continue
        scale = view["scale"]
        if scale is None:
            if view["drawing_type"] != "SCHEDULE_RESPONSIBILITY":
                error("SCALE_REQUIRED", subject, "A graphical view requires an explicit scale")
        elif scale <= 0 or scale not in preferred and not _text(view.get("scale_exception_reason")):
            error("INVALID_SCALE", subject, "Use a preferred denominator or provide a project exception reason")
        group = view.get("scale_group")
        if group and scale is not None:
            if group in scales and scales[group] != scale:
                error("SCALE_GROUP_MISMATCH", subject, f"Group {group} already uses 1:{scales[group]}")
            scales[group] = scale
        if "information" in view:
            information = set(view["information"])
            missing = set(rule["required"]) - information
            forbidden = set(rule["forbidden"]) & information
            if missing:
                error("MISSING_INFORMATION", subject, f"Missing declared content: {', '.join(sorted(missing))}")
            if forbidden:
                error("FORBIDDEN_INFORMATION", subject, f"Inappropriate declared content: {', '.join(sorted(forbidden))}")
    for ref in state["references"]:
        reference(ref["source_view_id"], views, "reference.source_view_id")
        reference(ref["target_view_id"], views, "reference.target_view_id")

    def check_status(item: dict, subject: str, *, representation: bool = True) -> None:
        if item["certainty"] not in status["certainty"]:
            error("INVALID_CERTAINTY", subject, f"Unknown certainty {item['certainty']}")
        if representation:
            if item["representation"] not in status["representation"]:
                error("INVALID_REPRESENTATION", subject, f"Unknown representation {item['representation']}")
            if any(action not in status["actions"] for action in item["actions"]):
                error("INVALID_ACTION", subject, "Actions must be declared separately from certainty and representation")

    endpoint_groups: dict[tuple[str, str, str], list[dict]] = {}
    for dimension in state["dimensions"]:
        subject = f"dimension:{dimension['id']}"
        reference(dimension["view_id"], views, subject)
        check_status(dimension, subject)
        if dimension["role"] not in roles:
            error("INVALID_DIMENSION_ROLE", subject, "Use overall, setting_out or component")
        if dimension["use"] not in status["dimension_use"]:
            error("INVALID_DIMENSION_USE", subject, "Unknown dimensional use")
        if dimension["certainty"] != "CONFIRMED" and dimension["use"] == "control":
            error("UNCONFIRMED_CONTROL_DIMENSION", subject, "An unconfirmed value cannot control construction")
        if dimension["end"] <= dimension["start"] or not close(dimension["value"], dimension["end"] - dimension["start"]):
            error("DIMENSION_VALUE_MISMATCH", subject, "Value must equal end - start with ordered, distinct endpoints")
        key = (dimension["view_id"], dimension["axis"], dimension["datum"])
        for previous in endpoint_groups.setdefault(key, []):
            if close(previous["start"], dimension["start"]) and close(previous["end"], dimension["end"]):
                code = "DUPLICATE_DIMENSION" if close(previous["value"], dimension["value"]) else "CONFLICTING_DIMENSION"
                error(code, subject, f"Same endpoints as {previous['id']}")
        endpoint_groups[key].append(dimension)
        source = dimension["source"]
        if isinstance(source, dict):
            dependencies = []
            for field, records in (("dimension_ids", dimensions), ("object_ids", components)):
                for source_id in source.get(field, []):
                    if reference(source_id, records, subject):
                        dependencies.append(records[source_id])
            if dimension["certainty"] == "CONFIRMED" and any(item["certainty"] != "CONFIRMED" for item in dependencies):
                error("SOURCE_CERTAINTY_UPGRADE", subject, "A derived value cannot confirm an unconfirmed input")
    for chain in state["dimension_chains"]:
        subject = f"chain:{chain['id']}"
        ids = chain["dimension_ids"]
        if not ids or len(set(ids)) != len(ids) or chain["overall_dimension_id"] in ids:
            error("INVALID_DIMENSION_CHAIN", subject, "Use distinct segment dimensions and a separate overall dimension")
        valid_ids = [reference(value, dimensions, subject) for value in ids + [chain["overall_dimension_id"]]]
        if not ids or not all(valid_ids):
            continue
        segments = [dimensions[value] for value in ids]
        overall = dimensions[chain["overall_dimension_id"]]
        expected_datum = tuple(overall[field] for field in ("view_id", "axis", "datum"))
        if any(tuple(item[field] for field in ("view_id", "axis", "datum")) != expected_datum for item in segments):
            error("CHAIN_DATUM_MISMATCH", subject, "Segments and overall must share view, axis and datum")
        if not close(sum(item["value"] for item in segments), overall["value"]):
            error("CHAIN_SUM_MISMATCH", subject, "Segment values do not sum to the overall value")
        if (not close(segments[0]["start"], overall["start"]) or
                not close(segments[-1]["end"], overall["end"]) or
                any(not close(left["end"], right["start"]) for left, right in zip(segments, segments[1:]))):
            error("CHAIN_ENDPOINT_MISMATCH", subject, "Segments must meet in order and span the overall endpoints")

    tags = {}
    for component in state["components"]:
        subject = f"component:{component['object_id']}"
        check_status(component, subject)
        if ("construction_scope" in component and
                component["construction_scope"] not in standard["GraphicSystem"]["construction_scope"]):
            error("INVALID_CONSTRUCTION_SCOPE", subject, "Use a construction scope separately from certainty or actions")
        reference(component["material_tag"], materials, subject)
        if component["tag"] in tags:
            error("DUPLICATE_COMPONENT_TAG", subject, f"Tag {component['tag']} already names {tags[component['tag']]}")
        tags[component["tag"]] = component["object_id"]
    for material in state["materials"]:
        check_status(material, f"material:{material['tag']}", representation=False)
        if "thickness_mm" in material and material["thickness_mm"] <= 0:
            error("INVALID_MATERIAL_THICKNESS", f"material:{material['tag']}", "Declared thickness must be positive")
    scheduled = Counter()
    for row in state["schedules"]:
        subject = f"schedule:{row['object_id']}"
        scheduled[row["object_id"]] += 1
        if reference(row["object_id"], components, subject):
            component = components[row["object_id"]]
            if row["component_tag"] != component["tag"] or row["material_tag"] != component["material_tag"]:
                error("SCHEDULE_MAPPING_MISMATCH", subject, "Schedule tags/material must match the named component")
        reference(row["material_tag"], materials, subject)
        if "view_id" in row:
            reference(row["view_id"], views, subject)
    for object_id in components:
        if scheduled[object_id] != 1:
            error("SCHEDULE_MAPPING_COUNT", f"component:{object_id}", "Each component needs exactly one schedule row")

    notes: dict[str, dict] = {}
    placements: Counter = Counter()
    notes_by_text: dict[str, list[dict]] = {}
    for note in state["notes"]:
        subject = f"note:{note['id']}"
        if note["id"] in notes:
            previous = notes[note["id"]]
            if note["text"] != previous["text"] or note["repeat_allowed"] != previous["repeat_allowed"]:
                error("NOTE_DEFINITION_CONFLICT", subject, "The same note id has incompatible definitions")
        notes[note["id"]] = note
        placements[note["id"]] += len(note["view_ids"])
        notes_by_text.setdefault(" ".join(note["text"].split()).casefold(), []).append(note)
        for view_id in note["view_ids"]:
            reference(view_id, views, subject)
    for note_id, count in placements.items():
        if count > 1 and not notes[note_id]["repeat_allowed"]:
            error("REPEATED_NOTE", f"note:{note_id}", "Multiple placements require explicit repeat_allowed")
    for definitions in notes_by_text.values():
        placed = [note for note in definitions if note["view_ids"]]
        if (len({note["id"] for note in placed}) > 1 and
                sum(len(note["view_ids"]) for note in placed) > 1 and
                not all(note["repeat_allowed"] for note in placed)):
            error("REPEATED_NOTE_TEXT", "notes", "The same note text is placed under different ids without repeat permission")

    for interface in state["interfaces"]:
        subject = f"interface:{interface['id']}"
        if interface["status"] not in status["certainty"]:
            error("INVALID_INTERFACE_STATUS", subject, "Interface confirmation status must be explicit")
        if not interface["object_ids"]:
            error("INTERFACE_OBJECT_REQUIRED", subject, "An interface must identify its related objects")
        for object_id in interface["object_ids"]:
            reference(object_id, components, subject)
        for view_id in interface.get("view_ids", []):
            reference(view_id, views, subject)
    for revision in state["revisions"]:
        subject = f"revision:{revision['id']}"
        try:
            parsed_date = date.fromisoformat(revision["date"])
            if parsed_date.isoformat() != revision["date"]:
                raise ValueError("not YYYY-MM-DD")
        except ValueError:
            error("INVALID_REVISION_DATE", subject, "Use an actual YYYY-MM-DD date supplied by the caller")
        if revision["issue_status"] not in ("draft", "issued"):
            error("INVALID_ISSUE_STATUS", subject, "Issue status must be draft or issued")
        if revision["issue_status"] != "draft" and revision["issued_by"] is None:
            error("ISSUER_REQUIRED", subject, "Only a draft may have an unknown issuer")
        if not revision["changed_view_ids"] or len(set(revision["changed_view_ids"])) != len(revision["changed_view_ids"]):
            error("INVALID_REVISION_VIEWS", subject, "Identify distinct changed views")
        for view_id in revision["changed_view_ids"]:
            reference(view_id, views, subject)
        for object_id in revision.get("changed_object_ids", []):
            reference(object_id, components, subject)
    return tuple(findings)


def compile_drawing_state(state: dict[str, Any], standard: dict[str, Any]) -> DrawingPlan:
    """Validate and resolve view titles and cross-sheet reference labels in memory."""
    findings = validate_drawing_state(state, standard)
    if any(finding.severity == "error" for finding in findings):
        raise ValueError("; ".join(f"{finding.code} [{finding.subject}]: {finding.detail}" for finding in findings))
    data = deepcopy(state)
    views = {view["id"]: view for view in data["views"]}
    for view in views.values():
        scale = view["scale"]
        view["scale_label"] = "NTS" if scale is None else f"1:{scale:g}"
        view["title"] = f"{view['number']}  {view['name']}  {view['scale_label']}"
    for reference in data["references"]:
        target = views[reference["target_view_id"]]
        reference["label"] = f"{target['number']} / {target['sheet']}"
    return DrawingPlan(data)
