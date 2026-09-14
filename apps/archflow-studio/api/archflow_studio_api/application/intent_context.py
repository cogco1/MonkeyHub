"""Compile a request's read context; the record still owns change propagation.

Known numeric requests use actions; explicitly located design work uses a
dependency slice with the existing semantic output contract. Unresolved or
global work retains the complete sheet. More reads never enlarge permitted edits.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from archflow.state.state_record import StateRecord


@dataclass(frozen=True, slots=True)
class IntentContext:
    tier: str
    sheet: Mapping[str, Any]
    target_ids: tuple[str, ...] = ()
    producer_ids: tuple[str, ...] = ()
    editable_fields: tuple[str, ...] = ()
    included_refs: tuple[str, ...] = ()
    escalation: tuple[str, ...] = ()
    expansion_count: int = 0
    supplemental_refs: tuple[str, ...] = ()
    design_sheet: Mapping[str, Any] | None = None


def _complete_sheet(sheet: Mapping[str, Any], record: StateRecord | None) -> dict[str, Any]:
    result = deepcopy(dict(sheet))
    if record is None:
        return result
    entities = {item.entity_id: item for item in record.entities}
    for row in result.get("components", ()):
        entity = entities.get(row["componentId"])
        if entity is not None:
            row["parentId"] = entity.parent_id
            extra_fields = {key: deepcopy(value) for key, value in entity.fields.items() if key not in {"semantic_kind", "intent"}}
            if extra_fields:
                row["authoredContext"] = extra_fields
    for row in result.get("elements", ()):
        entity = entities.get(row["elementId"])
        if entity is None:
            continue
        row["parentId"] = entity.parent_id
        # Flat reference forms and authored arrays are as material as the
        # projected numbers. Preserve them rather than guessing dependencies.
        for key in ("base_level", "top_level", "sill_level", "host"):
            if key in entity.fields:
                row[key] = deepcopy(entity.fields[key])
        row["params"] = deepcopy(entity.fields.get("params", {}))
        row["references"] = deepcopy(entity.fields.get("references", {}))
        row["typeRef"] = entity.fields.get("type_ref")
        represented_fields = {"component_id", "producer", "params", "references", "type_ref", "base_level", "top_level", "sill_level", "host"}
        extra_fields = {key: deepcopy(value) for key, value in entity.fields.items() if key not in represented_fields}
        if extra_fields:
            row["authoredContext"] = extra_fields
    # Keep expression inputs and lock provenance even for a caller with an
    # older sheet projection. Parameter.reads() is the expression owner.
    parameters = {item.key: item for item in record.parameters}
    for row in result.get("parameters", ()):
        parameter = parameters.get(row["key"])
        if parameter is not None:
            row.update(expr=parameter.expr, inputs=list(parameter.reads()), lockAuthority=parameter.lock_authority,
                       sourceRef=parameter.source_ref, epistemicStatus=parameter.epistemic_status)
    result["obligations"] = [item.to_dict() for item in record.obligations]
    represented = {"Component@1", "Element@1", "Level@1", "GridAxis@1", "Type@1", "Reading@1"}
    extra = [item.to_dict() for item in record.entities if item.schema not in represented]
    if extra:
        result["contextEntities"] = extra
    return result


def _rows(sheet: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result = {}
    for name, key, prefix in (
        ("components", "componentId", "entity:"), ("elements", "elementId", "entity:"),
        ("parameters", "key", "parameter:"), ("frame", "entity_id", "entity:"),
        ("types", "entity_id", "entity:"), ("readings", "entity_id", "entity:"),
        ("contextEntities", "entity_id", "entity:"), ("relationships", "relation_id", "relation:"),
        ("obligations", "obligation_id", "obligation:"),
    ):
        for row in sheet.get(name, ()):
            if isinstance(row, Mapping) and isinstance(row.get(key), str):
                result[prefix + row[key]] = (name, row)
    return result


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _named_refs(value: Any, known: Mapping[str, Any]) -> set[str]:
    """Exact references only; never infer identity from a similar name."""
    refs = set()
    for text in _strings(value):
        candidates = [text, "entity:" + text, "parameter:" + text.removeprefix("@")]
        if text.endswith("-top"):
            candidates.append("entity:" + text[:-4])
        refs.update(item for item in candidates if item in known)
        refs.update(item for item in re.findall(r"(?:entity|parameter|relation|obligation):[\w.-]+", text) if item in known)
    return refs


def _has_id(message: str, identifier: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(identifier)}(?![\w-])", message, re.IGNORECASE) is not None


def _grid_roles(value: Any) -> set[str]:
    """Frame references use role labels, not entity ids, in the record."""
    if not isinstance(value, Mapping):
        return set()
    result = set()
    for key, item in value.items():
        if key == "grid":
            result.update(_strings(item))
        elif key == "axis_point" and isinstance(item, Mapping) and isinstance(item.get("axis"), str):
            result.add(item["axis"])
        elif isinstance(item, Mapping):
            result.update(_grid_roles(item))
    return result


def _scope(message: str, sheet: Mapping[str, Any]):
    # Import at call time: clarification consumes the intent-agent seam.
    from .clarification import component_edit_requested, declares_a_control, kinds_in, property_in, scope_in

    elements = list(sheet.get("elements", ()))
    selected = sheet.get("selection", {})
    scope, _ = scope_in(message)
    if scope in {"stack", "datum"} or component_edit_requested(message) or declares_a_control(message):
        return (), (), "architectural_or_extended_scope"
    if sheet.get("documentVisuals") or sheet.get("gestures"):
        return (), (), "visual_scope_requires_full_context"
    if re.search(r"\b(all|every|entire|whole|both|reorganize|reconfigure|redesign)\b|全部|所有|整体|重新组织|重组|整层|同时保持", message, re.I):
        return (), (), "architectural_or_extended_scope"
    named = [item for item in elements if _has_id(message, item["elementId"])]
    if len(named) > 1:
        return (), (), "multiple_explicit_targets"
    target = named[0] if named else next((item for item in elements if item["elementId"] == selected.get("elementId")), None)
    if target is None:
        return (), (), "target_not_unambiguously_resolved"
    named_kinds = kinds_in(message)
    target_kinds = kinds_in(" ".join(str(target.get(key, "")) for key in ("elementId", "componentId", "producer")))
    component = next((item for item in sheet.get("components", ()) if item["componentId"] == target.get("componentId")), {})
    if not target_kinds:
        target_kinds = kinds_in(str(component.get("semanticKind", "")))
    if named_kinds and not named_kinds.issubset(target_kinds):
        return (), (), "request_kind_disagrees_with_selection"
    fields = set()
    numeric = target.get("numericFields", {})
    for word in re.findall(r"[A-Za-z_]+|[\u4e00-\u9fff]+", message):
        for token in re.split(r"和|并且|以及|、", word):
            field = token if token in numeric else property_in(token)
            if field is not None and field not in numeric:
                return (), (), "requested_control_not_available"
            if field in numeric:
                fields.add(field)
    if not fields or not re.search(r"\d", message):
        return (), (), "numeric_control_or_value_unresolved"
    # A closed, small grammar decides only whether reduced context is safe;
    # interpreting values/units remains the existing model + validator's job.
    # Unrecognised words (including a second design request) use the full tier.
    remainder = message.lower()
    for identifier in (target["elementId"],):
        if identifier:
            remainder = re.sub(rf"(?<![\w-]){re.escape(identifier.lower())}(?![\w-])", " ", remainder)
    if re.search(r"[\u4e00-\u9fff]", remainder):
        for word in sorted(("请把", "请将", "选中的", "选中", "这个", "这扇", "这面", "修改为", "改成", "设置为", "调整为", "毫米", "厘米", "宽度", "高度", "厚度", "长度", "深度", "半径", "直径", "标高", "窗户", "墙体", "楼梯", "柱子", "以及", "并且", "和", "的", "把", "将", "窗", "墙", "门", "米"), key=len, reverse=True):
            remainder = remainder.replace(word, " ")
    accepted = {"please", "set", "change", "make", "adjust", "increase", "decrease", "the", "this", "selected", "selection", "its", "to", "by", "from", "and", "with", "a", "of", "s", "mm", "cm", "m", "meters", "metres", "millimeters", "millimetres", "feet", "ft", "inches", "inch", "in"}
    for word in re.findall(r"[a-z_]+", remainder):
        if word not in accepted and word not in numeric and not property_in(word) and not kinds_in(word):
            return (), (), "request_exceeds_known_numeric_edit"
    if re.search(r"[\u4e00-\u9fff]", remainder):
        return (), (), "request_exceeds_known_numeric_edit"
    return (target["elementId"],), tuple(sorted(fields)), None


def _design_targets(message: str, sheet: Mapping[str, Any]) -> tuple[str, ...]:
    """Only explicit local anchors can bound a semantic design request."""
    if sheet.get("documentVisuals") or sheet.get("gestures"):
        return ()
    if re.search(r"\b(all|every|entire|whole|building|circulation|stack|datum)\b|全部|所有|整体|整层|整栋|流线|交通组织", message, re.I):
        return ()
    if not re.search(r"\b(add|remove|replace|change|set|adjust|rework|reconfigure|redesign|reorganize|widen|narrow)\b|增加|添加|增设|补齐|安装|移除|删除|替换|调整|改成|修改|重组", message, re.I):
        return ()
    # Keep clauses are read requirements, not additional change targets.
    action = re.split(r"\b(?:keep|keeping|preserve|preserving|retain|retaining)\b|保持|保留|不改变", message, maxsplit=1, flags=re.I)[0]
    elements = list(sheet.get("elements", ()))
    targets = {row["elementId"] for row in elements if _has_id(action, row["elementId"])}
    components = {row["componentId"] for row in sheet.get("components", ()) if _has_id(action, row["componentId"])}
    # An explicitly named component includes its authored descendants.
    while True:
        nested = components | {row["componentId"] for row in sheet.get("components", ()) if row.get("parentId") in components}
        if nested == components:
            break
        components = nested
    targets.update(row["elementId"] for row in elements if row.get("componentId") in components)
    if not targets and re.search(r"\b(this|selected)\b|这个|这扇|这面|选中", action, re.I):
        selected = sheet.get("selection", {}).get("elementId")
        target = next((row for row in elements if row["elementId"] == selected), None)
        if target is not None:
            from .clarification import kinds_in
            # The words next to the deictic must agree with the selection;
            # an added object elsewhere in the sentence may be a new kind.
            anchor = re.search(r"\b(?:this|selected)\s+([\w-]+)|(?:这个|这扇|这面|选中的?)([^，。\s]{1,4})", action, re.I)
            kinds = kinds_in(anchor.group(0)) if anchor else set()
            target_kinds = kinds_in(" ".join(str(target.get(key, "")) for key in ("elementId", "componentId", "producer")))
            if not kinds or kinds.issubset(target_kinds):
                targets.add(selected)
    return tuple(sorted(targets))


def _slice(sheet: Mapping[str, Any], seeds: set[str], record: StateRecord, *, changed: set[str], include_global_locks: bool = True) -> tuple[dict[str, Any], tuple[str, ...]]:
    rows = _rows(sheet)
    included = set(seeds)
    included.update(record.closure(tuple(changed)))
    upstream: dict[str, set[str]] = {}
    for edge in record.dependency_edges():
        upstream.setdefault(edge.downstream_ref, set()).add(edge.upstream_ref)
    axes_by_role: dict[str, set[str]] = {}
    for axis in record.entities_of("GridAxis@1"):
        axes_by_role.setdefault(axis.fields.get("role"), set()).add(axis.ref)
    for entity in record.entities:
        if entity.parent_id:
            upstream.setdefault(entity.ref, set()).add("entity:" + entity.parent_id)
        # Grid-role lookups are validated by StateRecord but do not produce
        # invalidation edges; they still belong in the model's read context.
        roles = _grid_roles(entity.fields.get("references", {}))
        for role in roles:
            upstream.setdefault(entity.ref, set()).update(axes_by_role.get(role, ()))
    # All declared locks remain visible. Applicable duties/readings are kept,
    # and unscoped ones remain global because exclusion would be a guess.
    globals_: list[tuple[str, Mapping[str, Any]]] = []
    for name in ("obligations", "preferences", "constraints"):
        globals_.extend((name, row) for row in sheet.get(name, ()) if isinstance(row, Mapping))
    for row in sheet.get("parameters", ()):
        if include_global_locks and row.get("lockAuthority"):
            included.add("parameter:" + row["key"])
    retained_global: dict[str, list[Any]] = {
        name: [deepcopy(row) for row in sheet[name] if not isinstance(row, Mapping)]
        for name in ("obligations", "preferences", "constraints") if name in sheet
    }
    # What each row names is fixed for this slice: the rows are the caller's
    # own and the closure below only grows a set of ids, never rewrites a row.
    # So each row is read for its references once instead of once per round as
    # the set widens — same rows, same references, same result. Relations,
    # readings and duties are offered to every round, so reading those up front
    # costs nothing extra; the rest is read when the closure first reaches it,
    # which keeps a small slice of a large record small work.
    row_refs: dict[str, set[str]] = {}

    def refs_of(ref: str) -> set[str]:
        found = row_refs.get(ref)
        if found is None:
            found = row_refs[ref] = _named_refs(rows[ref][1], rows)
        return found

    linked = [(ref, name, refs_of(ref) - {ref}) for ref, (name, _) in rows.items()
              if name in {"relationships", "readings"}]
    global_refs = []
    for name, row in globals_:
        own_ref = "obligation:" + row["obligation_id"] if name == "obligations" else None
        refs = refs_of(own_ref) if own_ref in rows else _named_refs(row, rows)
        global_refs.append((name, row, refs - {own_ref}, own_ref))
    while True:
        previous = set(included)
        for ref in tuple(included):
            included.update(upstream.get(ref, ()))
            if ref in rows:
                # Canonical edges supply references/bindings; these additional
                # links retain reading targets, semantic keep refs and lineage.
                included.update(refs_of(ref))
        for ref, name, refs in linked:
            if refs.intersection(included) or (name == "readings" and not refs):
                included.add(ref)
                included.update(refs)
        for name, row, refs, own_ref in global_refs:
            if not refs or refs.intersection(included) or own_ref in included:
                if row not in retained_global[name]:
                    retained_global[name].append(row)
                if own_ref is not None:
                    included.add(own_ref)
                included.update(refs)
        if included == previous:
            break
    result = deepcopy(dict(sheet))
    for name in {value[0] for value in rows.values()}:
        result[name] = [deepcopy(row) for ref, (group, row) in rows.items() if group == name and ref in included]
    result.update(deepcopy(retained_global))
    producers = {row["producer"] for row in result.get("elements", ())}
    producers.update(row.get("fields", {}).get("producer") for row in result.get("types", ()))
    result["producerSignatures"] = {key: deepcopy(value) for key, value in sheet.get("producerSignatures", {}).items() if key in producers}
    # Broad semantic vocabulary is needed for authoring new components, not
    # for changing the declared numeric controls of one current element.
    if include_global_locks:
        result.pop("semanticIds", None)
    return result, tuple(sorted(included.intersection(rows)))


def compile_context(message: str, sheet: Mapping[str, Any], *, record: StateRecord | None = None) -> IntentContext:
    full = _complete_sheet(sheet, record)
    targets, fields, reason = _scope(message, full)
    if reason is None and record is None:
        reason = "dependency_record_unavailable"
    if reason is None and len(fields) > 1:
        target = next(row for row in full["elements"] if row["elementId"] == targets[0])
        signature = full.get("producerSignatures", {}).get(target["producer"])
        if signature is None:
            reason = "component_signature_unavailable"
        elif not set(fields).issubset(signature.get("parameters", {}).get("properties", {})):
            reason = "component_fields_not_advertised"
    if reason is not None:
        design_targets = _design_targets(message, full) if record is not None else ()
        if design_targets:
            seeds = {"entity:" + target for target in design_targets}
            # Exact references in keep clauses also seed mandatory read context.
            seeds.update(ref for ref in _rows(full) if _has_id(message, ref.split(":", 1)[1]))
            narrowed, refs = _slice(full, seeds, record,
                                   changed={"entity:" + target for target in design_targets},
                                   include_global_locks=False)
            # The complete source remains private for scope/validation checks.
            return IntentContext("design", full, design_targets,
                                 tuple(sorted(full.get("producerSignatures", {}))),
                                 included_refs=refs, escalation=(reason,), design_sheet=narrowed)
        return IntentContext("design", full, producer_ids=tuple(sorted(full.get("producerSignatures", {}))), included_refs=tuple(sorted(_rows(full))), escalation=(reason,))
    target = next(row for row in full["elements"] if row["elementId"] == targets[0])
    changed = {"entity:" + item for item in targets}
    for field in fields:
        binding = target.get("parameterBindings", {}).get(field)
        if binding:
            changed.add("parameter:" + binding.removeprefix("@"))
    narrowed, refs = _slice(full, set(changed), record, changed=changed)
    return IntentContext("scalar" if len(fields) == 1 else "component", narrowed, targets, (target["producer"],), fields, refs)


def expand_context(context: IntentContext, sheet: Mapping[str, Any], requested_refs: Sequence[str], *, record: StateRecord | None = None) -> IntentContext:
    """Add exact known read references with bounded retries and no scope grant."""
    if (context.tier == "design" and context.design_sheet is None) or record is None:
        raise ValueError("full context cannot expand")
    if context.expansion_count >= 2:
        raise ValueError("context expansion limit reached")
    if isinstance(requested_refs, (str, bytes)) or not 1 <= len(requested_refs) <= 16 or any(not isinstance(ref, str) for ref in requested_refs):
        raise ValueError("context expansion requires one to sixteen exact references")
    full = _complete_sheet(sheet, record)
    known = _rows(full)
    if any(ref not in known for ref in requested_refs):
        raise ValueError("context expansion requested an unknown reference")
    added = set(requested_refs) - set(context.included_refs)
    if not added:
        raise ValueError("context expansion made no progress")
    narrowed, refs = _slice(full, set(context.included_refs) | added, record, changed=set(),
                           include_global_locks=context.tier != "design")
    return replace(context, sheet=full if context.tier == "design" else narrowed,
                   design_sheet=narrowed if context.tier == "design" else None,
                   included_refs=refs, expansion_count=context.expansion_count + 1,
                   supplemental_refs=tuple(sorted(set(context.supplemental_refs) | added)),
                   escalation=(*context.escalation, "expanded_context"))


def control_unit(context: IntentContext, row: Mapping[str, Any], field: str) -> str | None:
    """Return a declared unit, never infer it from a field's spelling or value."""
    binding = row.get("parameterBindings", {}).get(field)
    if isinstance(binding, str):
        parameter = next((item for item in context.sheet.get("parameters", ()) if item["key"] == binding.removeprefix("@")), None)
        unit = parameter.get("unit") if parameter is not None else None
        return unit if isinstance(unit, str) and unit else None
    # produce_wall declares dimensions in metres and builds its geometry with
    # _M (monkeyarch.capabilities.element_producers). Other producers/fields
    # have no unit metadata in the queried signature; do not guess theirs.
    if row.get("producer") == "wall" and field in {"height", "thickness"}:
        return "m"
    return None


def _display_name(row: Mapping[str, Any]) -> str:
    authored = row.get("authoredContext", row.get("fields", {}))
    for key in ("label", "name"):
        if isinstance(authored.get(key), str) and authored[key].strip():
            return authored[key]
    return str(row.get("elementId", row.get("componentId", row.get("entity_id", row.get("key", "Selected object")))))


def _affected_parameters(context: IntentContext, parameter_key: str) -> set[str]:
    affected = {parameter_key}
    while True:
        expanded = affected | {
            row["key"] for row in context.sheet.get("parameters", ())
            if affected.intersection(row.get("inputs", ()))
        }
        if expanded == affected:
            break
        affected = expanded
    return affected


def _shared_consumers(context: IntentContext, parameter_key: str) -> list[Mapping[str, Any]]:
    affected = _affected_parameters(context, parameter_key)
    types = {row["entity_id"]: row.get("fields", {}) for row in context.sheet.get("types", ())}
    consumers = []
    for row in context.sheet.get("elements", ()):
        bindings = {value.removeprefix("@") for value in row.get("parameterBindings", {}).values() if isinstance(value, str)}
        type_ref = row.get("typeRef")
        defaults = types.get(type_ref.removeprefix("entity:"), {}) if isinstance(type_ref, str) else {}
        for group in ("params", "references"):
            effective = {**defaults.get(group, {}), **row.get(group, {})}
            bindings.update(value[1:] for value in _strings(effective) if value.startswith("@"))
        if bindings.intersection(affected):
            consumers.append(row)
    return consumers


_PRIVATE_FACT_KEYS = frozenset({
    "schema", "entity_id", "relation_id", "obligation_id", "parent_id", "parentId",
    "component_id", "componentId", "elementId", "producer", "lineage", "provenance",
    "expr", "inputs", "lockAuthority", "lock_authority", "parameterBindings",
    "params", "references", "typeRef", "status", "validator", "propagation",
})


def _design_value(value: Any) -> Any:
    """Keep authored design facts while leaving identifiers and control data private."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in _PRIVATE_FACT_KEYS or key.lower().endswith(("ref", "refs")):
                continue
            cleaned = _design_value(item)
            if cleaned not in (None, {}, []):
                result[key] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [item for child in value if (item := _design_value(child)) not in (None, {}, [])]
    return deepcopy(value)


def model_context(context: IntentContext) -> dict[str, Any]:
    """Facts for proposing a numeric action, separate from the validation closure.

    The complete sheet stays private on IntentContext. This projection never
    grants edits, changes references or evaluates a candidate's constraints.
    """
    if context.tier == "design":
        result = deepcopy(dict(context.design_sheet if context.design_sheet is not None else context.sheet))
        result.pop("producerSignatures", None)  # The response schema supplies authoring vocabulary.
        if context.design_sheet is not None:
            result["editTargets"] = list(context.target_ids)
        return result
    rows = _rows(context.sheet)
    elements = {row["elementId"]: row for row in context.sheet.get("elements", ())}
    parameters = {row["key"]: row for row in context.sheet.get("parameters", ())}
    targets = []
    relevant = {"entity:" + identifier for identifier in context.target_ids} | set(context.supplemental_refs)
    for identifier in context.target_ids:
        row = elements[identifier]
        controls = []
        for field in context.editable_fields:
            binding = row.get("parameterBindings", {}).get(field)
            parameter = parameters.get(binding.removeprefix("@"), {}) if isinstance(binding, str) else {}
            type_ref = row.get("typeRef")
            type_row = rows.get("entity:" + type_ref.removeprefix("entity:"), (None, {}))[1] if isinstance(type_ref, str) else {}
            effective_refs = {**type_row.get("fields", {}).get("references", {}), **row.get("references", {})}
            reason = None
            if parameter.get("lockAuthority"):
                reason = "This value is locked by an existing design decision."
            elif parameter.get("expr"):
                reason = "This value is derived from other dimensions and cannot be changed directly."
            elif field == "height" and (effective_refs.get("top") or row.get("top_level")):
                reason = "This height is determined by its top reference and cannot be changed directly."
            control = {"field": field, "currentValue": row.get("numericFields", {}).get(field),
                       "unit": control_unit(context, row, field), "editable": reason is None}
            if reason:
                control["reason"] = reason
            if isinstance(binding, str):
                relevant.add("parameter:" + binding.removeprefix("@"))
                consumers = _shared_consumers(context, binding.removeprefix("@"))
                affected_parameters = _affected_parameters(context, binding.removeprefix("@"))
                coupled_fields = [key for key, value in row.get("parameterBindings", {}).items()
                                  if key != field and isinstance(value, str) and value.removeprefix("@") in affected_parameters]
                if len(consumers) > 1 or coupled_fields:
                    if len(consumers) > 1 or any(key not in context.editable_fields for key in coupled_fields):
                        control["editable"] = False
                        control.setdefault("reason", "This dimension is shared; decide which objects and dimensions should change before editing it.")
                    names = sorted(_display_name(item) for item in consumers)
                    control["sharedImpact"] = {
                        "affectedCount": len(consumers),
                        "meaning": "Changing this shared dimension also changes the other named objects.",
                    }
                    if len(names) <= 8:
                        control["sharedImpact"]["affectedObjects"] = names
                    else:
                        # Complete impact remains private. A large set is an
                        # explicit count and examples, not an unbounded id list.
                        control["sharedImpact"].update(exampleAffectedObjects=names[:4], unnamedCount=len(names) - 4)
                    if coupled_fields:
                        control["sharedImpact"]["coupledFieldsOnThisObject"] = sorted(coupled_fields)
                        control["sharedImpact"]["meaning"] = "Changing this dimension also changes the listed coupled dimensions."
                    relevant.update("entity:" + item["elementId"] for item in consumers)
            controls.append(control)
        targets.append({"name": _display_name(row), "controls": controls})
    # Follow only the facts the selected objects reference, plus explicitly
    # requested supplements. An unrelated global lock may occur in the private
    # validation sheet but cannot seed additional model facts here.
    while True:
        before = set(relevant)
        for ref in tuple(relevant):
            if ref not in rows:
                continue
            group, row = rows[ref]
            if group == "elements":
                links = {key: row.get(key) for key in ("references", "typeRef", "host", "base_level", "top_level", "sill_level", "parentId", "componentId")}
                relevant.update(_named_refs(links, rows))
            elif group == "components":
                relevant.update(_named_refs(row.get("parentId"), rows))
            elif group == "parameters":
                relevant.update("parameter:" + key for key in row.get("inputs", ()))
            elif group == "types":
                relevant.update(_named_refs(row.get("fields", {}).get("references", {}), rows))
            elif group == "obligations":
                relevant.update(_named_refs(row, rows))
        for row in context.sheet.get("obligations", ()):
            ref = "obligation:" + row["obligation_id"]
            refs = _named_refs(row, rows) - {ref}
            if refs.intersection(relevant):
                relevant.add(ref)
                relevant.update(refs)
        if before == relevant:
            break
    facts = []
    for group in ("readings", "obligations", "preferences", "constraints"):
        for row in context.sheet.get(group, ()):
            if not isinstance(row, Mapping):
                if isinstance(row, str):
                    facts.append({"kind": group, "statement": row})
                continue
            own = {prefix + row[key] for prefix, key in (("entity:", "entity_id"), ("obligation:", "obligation_id")) if key in row}
            refs = _named_refs(row, rows) - own
            if refs and not refs.intersection(relevant) and not own.intersection(relevant):
                continue
            fact = _design_value(row.get("fields", row))
            if fact:
                facts.append({"kind": group, "fact": fact})
    for row in context.sheet.get("relationships", ()):
        endpoints = {"entity:" + row["subject"], "entity:" + row["object"]}
        if not endpoints.intersection(relevant):
            continue
        fact = {"kind": row["kind"], "between": [
            _display_name(rows[ref][1]) if ref in rows else ref.removeprefix("entity:")
            for ref in ("entity:" + row["subject"], "entity:" + row["object"])
        ]}
        validator = row.get("validator") or {}
        if validator.get("check_kind") == "clearance_interval":
            fact["requiredClearance"] = {"interval": deepcopy(validator.get("interval_m")), "unit": "m"}
        elif validator.get("check_kind") == "support_contact":
            fact["requirement"] = "Maintain the declared support contact."
        elif validator.get("check_kind") == "aperture_exists":
            fact["requirement"] = "Retain the declared opening."
        elif validator.get("check_kind") == "solid_nonpenetration":
            fact["requirement"] = "The named objects must not penetrate each other."
        elif validator:
            fact["requirement"] = "An existing geometric constraint must remain satisfied."
        facts.append(fact)
    dependencies = []
    for ref in sorted(relevant):
        if ref not in rows:
            continue
        group, row = rows[ref]
        if group == "frame":
            fields = row.get("fields", {})
            fact = {"name": _display_name(row)}
            if "elevation" in fields:
                fact["elevation"] = deepcopy(fields["elevation"])
                fact["unit"] = fields.get("unit")  # No implicit units on Level@1.
            if "role" in fields:
                fact["description"] = fields["role"]
            dependencies.append(fact)
        elif ref in context.supplemental_refs and group in {"elements", "parameters", "types"}:
            fact = {"name": _display_name(row)}
            if group == "elements":
                fact["dimensions"] = [{"field": field, "value": value, "unit": control_unit(context, row, field)}
                                      for field, value in row.get("numericFields", {}).items()]
            elif group == "parameters":
                fact.update(value=row.get("value"), unit=row.get("unit"))
            else:
                fields = row.get("fields", {})
                fact["description"] = _design_value(fields)
                fact["dimensions"] = []
                for field, value in fields.get("params", {}).items():
                    parameter = parameters.get(value.removeprefix("@"), {}) if isinstance(value, str) and value.startswith("@") else {}
                    if type(value) not in (int, float) and not parameter:
                        continue
                    fact["dimensions"].append({"field": field, "value": parameter.get("value", value),
                                               "unit": parameter.get("unit") if parameter else control_unit(context, fields, field)})
            dependencies.append(fact)
    return {"targets": targets, "designFacts": facts, "dependencyFacts": dependencies}
