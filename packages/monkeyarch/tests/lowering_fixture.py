"""The record ``initialize_modeling`` seeds, and a construction script lowered and applied to it.

``_record`` is the modelling root ``model`` with a level ``ground`` at zero;
``_compile`` lowers a script against it and ``_apply`` merges the result the
way the semantic-edit path does. The lowering tests (test_construction_lowering.py)
and the wall-realization tests (test_wall_realization.py) build on these.
"""

from __future__ import annotations

from archflow.project.refs import ProjectVersionRef
from archflow.state.state_record import (
    Entity,
    Parameter,
    StateRecord,
    apply_state_record_operator,
    compile_component_edit,
)
from monkeyarch.authoring.element_producers import validate_element_contract
from monkeyarch.authoring.construction.lowering import compile_construction_script

EVIDENCE = "input:monkeyarch-modeling-setup"
ROOT = Entity("model", "Component@1", {"intent": "Root for candidate modeling", "source_refs": [EVIDENCE]})


def _level(entity_id: str, elevation: float, role: str | None = None) -> Entity:
    return Entity(entity_id, "Level@1", {"role": role or entity_id, "elevation": elevation}, basis_refs=(EVIDENCE,))


def _record(*extra: Entity, parameters: tuple[Parameter, ...] = (), ground: float | None = 0.0) -> StateRecord:
    levels = (_level("ground", ground),) if ground is not None else ()
    return StateRecord(
        project_id="demo", run_id="authored", entities=(ROOT, *levels, *extra),
        parameters=parameters, evidence_refs=(EVIDENCE,), option={"option_id": "modeling"},
        base=ProjectVersionRef("demo", 0, "0" * 64),
    )


def _compile(script: str, record: StateRecord | None = None):
    return compile_construction_script(script, record if record is not None else _record(), root_component_id="model")


def _apply(record: StateRecord, result) -> StateRecord:
    """The successor, exactly as the semantic-edit path derives it: rows merged over existing fields."""

    existing = {entity.entity_id: entity for entity in record.entities}
    entities = []
    for row in result.entities:
        previous = existing.get(row["entity_id"])
        value = dict(row)
        if previous is not None:
            value = {**previous.to_dict(), **value, "fields": {**previous.fields, **row["fields"]}}
        entities.append(Entity.from_dict(value))
    operator = compile_component_edit(record, entities=tuple(entities), remove_entity_ids=tuple(result.remove_entity_ids))
    successor = apply_state_record_operator(record, operator)
    validate_element_contract(successor, tuple(row["entity_id"] for row in result.entities if row["schema"] == "Element@1"))
    return successor
