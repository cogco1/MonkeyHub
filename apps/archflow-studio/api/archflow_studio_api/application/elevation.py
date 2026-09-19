"""Stage 1 elevation edits expressed as existing entity/parameter proposals."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..transport.errors import StudioError
from .intent import component_edit_proposal
from .projection import drawing_context, elevation_reference, reference_value


def elevation_proposal(projection, *, action: str, element_id: str | None = None,
                       value: float | None = None, reference: Mapping[str, Any] | None = None,
                       level_id: str | None = None, name: str | None = None, keep_refs=()):
    record = projection.record
    parameters: list[dict[str, Any]] = []
    try:
        if action == "set-datum":
            existing = next((e for e in record.entities if e.entity_id == level_id), None)
            if existing is not None and existing.schema != "Level@1":
                raise ValueError(f"{level_id} already names a different entity")
            fields = dict(existing.fields) if existing else {"role": level_id}
            fields["elevation"] = value
            if name is not None:
                fields["name"] = name
            entity = {"entity_id": level_id, "schema": "Level@1", "parent_id": existing.parent_id if existing else None,
                      "basis_refs": list(existing.basis_refs) if existing else ["studio:intent"], "fields": fields}
            component_id = None
        else:
            entity_row = next((e for e in record.entities_of("Element@1") if e.entity_id == element_id), None)
            if entity_row is None:
                raise ValueError(f"unknown element {element_id}")
            rows, context, placements = drawing_context(record)
            row = rows[element_id]
            placement = placements.get(element_id, "Base/Top/Height controls require a drawn prism")
            if isinstance(placement, str):
                raise ValueError(placement)
            if (not placement["horizontal"]
                    or (row.producer != "prism" and not (row.producer == "planar-surface" and action == "detach-base"))):
                raise ValueError("Base/Top/Height controls require a horizontal upward prism")
            fields = deepcopy(dict(entity_row.fields))
            # Include type defaults without materializing parameter bindings as numbers.
            from archflow.state.state_record import _element_fields

            authored = _element_fields(record, entity_row)
            params = deepcopy(dict(authored.get("params", {})))
            refs = deepcopy(dict(authored.get("references", {})))
            inherited_type = next((item for item in record.entities_of("Type@1")
                                   if item.entity_id == str(fields.get("type_ref", "")).removeprefix("entity:")), None)
            base, top, height = placement["base"], placement["top"], placement["height"]

            def assign_param(key: str, number: float):
                old = params.get(key)
                if isinstance(old, str) and old.startswith("@"):
                    parameter = next(p for p in record.parameters if p.key == old[1:])
                    if parameter.expr is not None:
                        raise ValueError(f"{key} is derived from {parameter.expr}; edit its existing inputs")
                    parameters.append({"key": parameter.key, "value": round(number, 9)})
                else:
                    params[key] = round(number, 9)

            def set_base(number: float):
                assign_param("elevation", float(row.params.get("elevation", 0)) + number - base)
                if "top" in refs and "height" in params:
                    assign_param("height", top - number)

            def set_top(number: float):
                if "top" in refs:
                    if any(path.startswith("references.top") for path, _ in _bindings(entity_row, record)):
                        raise ValueError("the top reference is parameter-bound; edit its existing control")
                    bound = elevation_reference(row.references["top"], number, context)
                    refs["top"] = _reference(bound)
                if "top" not in refs or "height" in params:
                    assign_param("height", number - base)

            if action == "set-base":
                set_base(value)
            elif action == "set-top":
                set_top(value)
            elif action == "set-height":
                set_top(base + value)
            elif action in {"bind-base", "bind-top"}:
                target = reference_value(reference, context) + reference["offset"]
                if reference["kind"] == "element-top" and reference["id"] == element_id:
                    raise ValueError("an element cannot bind its own top")
                if action == "bind-base":
                    # The reference's offset describes the physical bottom;
                    # retain the drawing plane and its parameterized inputs.
                    local_bottom = base - placement["referenceElevation"]
                    refs["base"] = _reference({**reference, "offset": reference["offset"] - local_bottom})
                    if "top" in refs and "height" in params:
                        assign_param("height", top - target)
                else:
                    if isinstance(params.get("height"), str):
                        raise ValueError("height is parameter-bound; detach that control explicitly before binding the top")
                    if inherited_type is not None and "height" in inherited_type.fields.get("params", {}):
                        raise ValueError("height is declared by the element type; edit the type before binding its top")
                    refs["top"] = _reference(reference)
                    params.pop("height", None)
            elif action == "detach-base":
                local_bottom = base - placement["referenceElevation"]
                refs["base"] = {"elevation": round(base - local_bottom, 9)}
            elif action == "detach-top":
                if inherited_type is not None and "top" in inherited_type.fields.get("references", {}):
                    raise ValueError("top is declared by the element type; edit that type before detaching it")
                refs.pop("top", None)
                assign_param("height", height)
            else:
                raise ValueError(f"unknown elevation action {action}")
            for field_name, edited in (("params", params), ("references", refs)):
                # Leave unchanged type defaults inherited, so a later type
                # edit still reaches this instance's profile and controls.
                original = dict(entity_row.fields.get(field_name, {}))
                effective = authored.get(field_name, {})
                for key in set(effective) | set(edited):
                    if key in effective and key in edited and effective[key] == edited[key]:
                        continue
                    if key in edited:
                        original[key] = edited[key]
                    else:
                        original.pop(key, None)
                fields[field_name] = original
            entity = {"entity_id": entity_row.entity_id, "schema": entity_row.schema,
                      "parent_id": entity_row.parent_id, "basis_refs": list(entity_row.basis_refs), "fields": fields}
            component_id = row.component_id
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise StudioError(422, "ELEVATION_EDIT_INVALID", str(exc)) from exc
    summary = f"{action} {level_id or element_id}"
    return component_edit_proposal(projection, {
        "summary": summary, "entities": [entity], "parameters": parameters, "relations": [],
        "removeEntityIds": [], "removeParameterKeys": [], "removeRelationIds": [], "protected": [], "kept": [],
    }, utterance=summary, component_id=component_id, keep_refs=keep_refs)


def _bindings(entity, record):
    from archflow.state.state_record import parameter_bindings_of

    return parameter_bindings_of(entity, record)


def _reference(reference):
    if reference["kind"] == "level":
        return {"offset_from": {"level": reference["id"], "offset": round(reference["offset"], 9)}}
    return {"datum": reference["id"] + "-top", "offset": round(reference["offset"], 9)}
