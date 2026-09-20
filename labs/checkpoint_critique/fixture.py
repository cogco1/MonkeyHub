"""Bounded synthetic courtyard decisions, using the canonical StateRecord owner.

This is a declared schematic exercise, not CAD, route-clearance or structural
proof. Coordinates are decision dependencies, not a claimed built circulation
network. All successor construction is delegated to the existing typed operator.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from archflow.project.refs import RunRef
from archflow.state.state_record import (
    Entity, Parameter, StateRecord, StateRecordOperator, compile_component_edit,
    stale_parameters,
)
from labs.candidate_evaluation.evaluator import EvaluationRequest, MassingEvaluator


EVIDENCE = "fixture:synthetic-courtyard-v1"
CONTEXT_REFS = ("fixture:courtyard-policy-v1", "fixture:west-street-gate-v1")
SEMANTIC_DETAILS = ("generic", "unknown", "resolved")
PROTECTED = ("entity:courtyard", "parameter:courtyard_width")
ENVELOPE = {"min": (0, 0, 0), "max": (11, 5, 11), "max_height_m": 6,
            "far": 2, "site_area_m2": 144}
WINGS = {
    "wing-west": ((0, 0, 0), (3, 5, 11)),
    "wing-east": ((8, 0, 0), (11, 5, 11)),
    "wing-south": ((4, 0, 0), (7, 5, 3)),
    "wing-north": ((4, 0, 8), (7, 5, 11)),
}


def _component(name: str, **fields: Any) -> Entity:
    return Entity(name, "Component@1", {"roles": ["role.access"], **fields},
                  parent_id="building", basis_refs=(EVIDENCE,))


def initial_record(run: RunRef) -> StateRecord:
    """The caller supplies the exact project/run/base; this function writes nothing."""
    run.base.require_digest()
    levels = tuple(Entity(f"floor-{i}", "MassingLevel@1", {"base_y": i * 3, "height": 3},
                          basis_refs=(EVIDENCE,)) for i in range(2))
    volumes = tuple(Entity(name, "Volume@1", {
        "min": list(low), "max": list(high), "level_ids": ["floor-0", "floor-1"],
    }, basis_refs=(EVIDENCE,)) for name, (low, high) in WINGS.items())
    entities = (
        Entity("building", "Component@1", {"roles": ["role.whole"], "volume_ids": list(WINGS)},
               basis_refs=(EVIDENCE,)),
        Entity("courtyard", "Component@1", {
            "roles": ["role.reservation"], "plan_min": [4, 4], "plan_max": [8, 8],
            "width_parameter": "courtyard_width", "open_to_sky": True,
        }, parent_id="building", basis_refs=(EVIDENCE,)),
        *levels, *volumes,
        Entity("residential-zone", "Space@1", {
            "program_node_refs": ["program-node:residential"],
            "level_ids": ["floor-0", "floor-1"], "volume_ids": list(WINGS),
        }, basis_refs=(EVIDENCE,)),
    )
    return StateRecord(
        run.project_id, run.run_id, entities,
        parameters=(Parameter("courtyard_width", 4, "m", epistemic_status="declared",
                              source_ref=EVIDENCE, lock_authority="fixture-brief"),),
        evidence_refs=(EVIDENCE,), basis_refs=CONTEXT_REFS,
        option={"option_id": "courtyard", "label": "Synthetic courtyard housing",
                "typology": "courtyard", "rationale": "Fixed decision experiment, no CAD proof"},
    ).bound_to(run)


def _choice(answer: dict[str, Any], key: str, allowed: tuple[str, ...], default: str | None = None) -> str:
    value = answer.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{key} must be one of {allowed}")
    return value


def propose(record: StateRecord, checkpoint: int, answer: dict[str, Any]) -> StateRecordOperator:
    """Compile one finite action. Apply it with apply_state_record_operator.

    An explicit omit compiles a no-op upsert; it does not remove an existing
    gallery/access or make an omission vanish from final assessment. Checkpoint
    0 is the same entry revision after dependent decisions have already accrued.
    """
    if type(checkpoint) is not int or checkpoint not in (0, 1, 2, 3):
        raise ValueError("checkpoint must be 0 (entry repair), 1, 2 or 3")
    if not isinstance(answer, dict):
        raise ValueError("action must be a JSON object")
    allowed = {
        0: {"entry_side", "semantic_detail", "courtyard_width"},
        1: {"entry_side", "semantic_detail", "courtyard_width"},
        2: {"gallery"},
        3: {"unit_access", "semantic_detail"},
    }[checkpoint]
    if set(answer) - allowed:
        raise ValueError(f"unsupported action fields: {sorted(set(answer) - allowed)}")
    existing = {entity.entity_id: entity for entity in record.entities}
    entities: tuple[Entity, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    if checkpoint in (0, 1):
        if checkpoint == 0 and "entry" not in existing:
            raise ValueError("entry repair requires an existing entry decision")
        side = _choice(answer, "entry_side", ("west", "east"))
        detail = _choice(answer, "semantic_detail", SEMANTIC_DETAILS,
                         existing["entry"].fields["semantic_detail"] if "entry" in existing else "unknown")
        entities = (_component("entry", semantic_detail=detail, side_parameter="entry_side"),)
        parameters = (Parameter("entry_side", 0 if side == "west" else 1, "index",
                                epistemic_status="declared", source_ref=EVIDENCE),)
        if "courtyard_width" in answer:
            width = answer["courtyard_width"]
            if type(width) not in (int, float) or width not in (2, 4):
                raise ValueError("courtyard_width must be 2 or 4 m; 4 m is locked")
            parameters += (replace(record.parameter("courtyard_width"), value=width),)
    elif checkpoint == 2:
        if "entry" not in existing:
            raise ValueError("gallery decision requires entry")
        action = _choice(answer, "gallery", ("add", "omit"))
        if action == "add":
            entities = (_component("gallery", coordinate_parameter="gallery_x"),)
            parameters = (Parameter("gallery_x", float(record.parameter("entry_side").value * 8 + 1), "m",
                                    expr="entry_side * 8 + 1", source_ref=EVIDENCE),)
        else:
            entities = (existing["entry"],)
    else:
        if "entry" not in existing:
            raise ValueError("unit-access decision requires entry")
        action = _choice(answer, "unit_access", ("add", "omit"))
        detail = _choice(answer, "semantic_detail", SEMANTIC_DETAILS, existing["entry"].fields["semantic_detail"])
        entities = (replace(existing["entry"], fields={**existing["entry"].fields, "semantic_detail": detail}),)
        if action == "add":
            if "gallery" not in existing:
                raise ValueError("unit access requires a gallery; no fabricated predecessor")
            door_x = float(record.parameter("gallery_x").value + 1)
            entities += (_component("unit-access", coordinate_parameter="door_x",
                                    threshold_parameter="threshold_x"),)
            parameters = (
                Parameter("door_x", door_x, "m", expr="gallery_x + 1", source_ref=EVIDENCE),
                Parameter("threshold_x", door_x + 0.5, "m", expr="door_x + 0.5", source_ref=EVIDENCE),
            )
    return compile_component_edit(record, entities=entities, parameters=parameters, protected=PROTECTED)


def _massing(record: StateRecord) -> dict[str, Any]:
    result = MassingEvaluator(ENVELOPE).evaluate(EvaluationRequest(
        record, record.run_ref, record.digest, CONTEXT_REFS,
    ))
    return {
        **{item.objective.name: item.value for item in result.objectives},
        "validity": result.validity, "evaluator": result.evaluator_version,
        "source": EVIDENCE, "scope": "declared 1 m inclusive cells, not CAD proof",
        "constraints": [{"name": item.name, "status": item.status, "reason": item.reason}
                        for item in result.constraints],
    }


def checks(record: StateRecord, final: bool = False) -> list[dict[str, Any]]:
    """Fixed independent predicates, never interpreting model explanations as proof."""
    entities = {entity.entity_id: entity for entity in record.entities}
    parameters = {parameter.key: parameter for parameter in record.parameters}
    initial = initial_record(record.run_ref)
    findings: list[dict[str, Any]] = []

    def add(name: str, status: str, category: str, reason: str, *refs: str) -> None:
        findings.append({"name": name, "status": status, "category": category,
                         "reason": reason, "refs": list(refs)})

    preserved = (entities.get("courtyard") == initial.entity("courtyard")
                 and parameters.get("courtyard_width") == initial.parameter("courtyard_width"))
    add("protected_courtyard", "pass" if preserved else "fail", "invariant",
        "The courtyard stays open, 4 x 4 m, with its width lock unchanged.", *PROTECTED)
    fixed_ids = (*WINGS, "floor-0", "floor-1", "residential-zone")
    massing_preserved = all(entities.get(name) == initial.entity(name) for name in fixed_ids)
    massing_preserved = massing_preserved and {e.entity_id for e in record.entities_of("Volume@1")} == set(WINGS)
    add("fixed_massing", "pass" if massing_preserved else "fail", "invariant",
        "Two-storey residential wings and the courtyard void are fixed fixture inputs.",
        *(f"entity:{name}" for name in fixed_ids))
    side = parameters.get("entry_side")
    aligned = side is not None and side.value == 0 and "entry" in entities
    add("entry_alignment", "pass" if aligned else "fail" if side is not None or final else "pending",
        "obligation", "The only street gate is west. A declared east entry needs revision before completion.",
        "parameter:entry_side", "fixture:west-street-gate-v1")
    for name, entity_id, required_parameters in (
        ("gallery", "gallery", ("gallery_x",)),
        ("unit_access", "unit-access", ("door_x", "threshold_x")),
    ):
        present = entity_id in entities and all(key in parameters for key in required_parameters)
        add(name, "pass" if present else "fail" if final else "pending", "obligation",
            "Required for final schematic completion; explicitly permitted to be absent earlier.",
            f"entity:{entity_id}", *(f"parameter:{key}" for key in required_parameters))
    expected_expressions = {"gallery_x": "entry_side * 8 + 1", "door_x": "gallery_x + 1",
                            "threshold_x": "door_x + 0.5"}
    try:
        stale = stale_parameters(record)
        chain_correct = not stale and all(parameters[key].expr == expression
                                          for key, expression in expected_expressions.items() if key in parameters)
    except ValueError:
        chain_correct = False
    add("declared_dependencies", "pass" if chain_correct else "fail", "invariant",
        "Existing route coordinates retain their declared expression and recomputed values.",
        "parameter:entry_side", "parameter:gallery_x", "parameter:door_x", "parameter:threshold_x")
    detail = entities["entry"].fields.get("semantic_detail", "unknown") if "entry" in entities else "unknown"
    add("semantic_detail", "pass" if detail == "resolved" else "unknown", "preference",
        "Generic and unknown semantic detail are allowed, including at final schematic completion.", "entity:entry")
    add("structural_analysis", "unknown", "capability",
        "Unavailable: no loads, materials or supports; schematic massing is not structural evidence.", EVIDENCE)
    return findings


def final_assessment(record: StateRecord) -> dict[str, Any]:
    findings = checks(record, final=True)
    unresolved = [item["name"] for item in findings
                  if item["category"] in {"invariant", "obligation"} and item["status"] != "pass"]
    massing = _massing(record)
    if massing["validity"] != "valid":
        unresolved.append("massing_evaluation")
    return {"complete": not unresolved, "checks": findings, "massing": massing,
            "unresolved": unresolved,
            "unavailable": [item["name"] for item in findings
                            if item["category"] == "capability" and item["status"] == "unknown"]}


def policy_document() -> dict[str, Any]:
    """The same bounded, public policy is shown to every experimental arm."""
    return {
        "scenario": "Synthetic two-storey courtyard housing; complete a schematic access sequence.",
        "evidence_refs": [EVIDENCE], "context_refs": list(CONTEXT_REFS),
        "requested_checks": ["protected_courtyard", "fixed_massing", "entry_alignment", "gallery",
                             "unit_access", "declared_dependencies", "semantic_detail", "structural_analysis"],
        "brief": [
            "The ONLY street gate is WEST. Choose west entry by final completion.",
            "Courtyard is an open 4 x 4 m void; its width and the four fixed residential wings are protected.",
            "Outer plan is 12 x 12 m: footprint = 144 - 16 = 128 m2; two floors = 256 m2; height = 6 m.",
            "Checkpoints: 1 entry, 2 gallery, 3 unit access. Checkpoint 0 revises an existing entry.",
            "Missing gallery/unit access is allowed before final completion. East entry is an outstanding completion obligation, not a current invariant violation.",
            "Generic/unknown semantic detail remains valid; resolved detail is optional. No physical or BIM meaning is inferred.",
            "Structural analysis is always unavailable because loads/materials/supports are absent. Do not invent them.",
            "Dependent route anchors: entry_side (west=0/east=1) -> gallery_x -> door_x -> threshold_x. Existing expressions recompute when entry changes.",
            "These route anchors are declared schematic decisions, not measured access, egress or clearance proof.",
        ],
        "checkpoint_actions": {
            "0": {"entry_side": ["west", "east"], "semantic_detail": list(SEMANTIC_DETAILS), "courtyard_width": [2, 4]},
            "1": {"entry_side": ["west", "east"], "semantic_detail": list(SEMANTIC_DETAILS), "courtyard_width": [2, 4]},
            "2": {"gallery": ["add", "omit"]},
            "3": {"unit_access": ["add", "omit"], "semantic_detail": list(SEMANTIC_DETAILS)},
        },
        "input_fields": [
            "The action member of the response must follow this table. Other response fields follow the supplied response schema. Unsupported action keys or enum values are rejected.",
            "entry_side, gallery and unit_access are required at their respective checkpoints; semantic_detail is optional and defaults to unchanged/unknown.",
            "courtyard_width is optional. 4 preserves the lock; 2 is an express invariant-violation attempt and core application refuses it.",
            "Adding unit access requires an existing gallery. Repeating add preserves existing parts; omit never deletes them.",
            "Use checkpoint 0 only for an existing entry. It may repair east to west after dependent anchors exist.",
            "Explanations and confidence are not geometric or invariant evidence.",
        ],
        "examples": {"1": {"entry_side": "west", "semantic_detail": "unknown"},
                     "2": {"gallery": "add"}, "3": {"unit_access": "add"},
                     "0": {"entry_side": "west"}},
    }
