"""P102: the canonical State Record.

Entities of typed schemas, parameters with lineage, relations with datum
roles / propagation / validator bindings, obligations kept apart; the
record yields kernel dependency edges and a closure; the legacy
DevelopedDesignState is only a forwarded view.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.operational_state import DependencyEffect, DesignObligation, ObligationStatus
from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.stage_workflow import DesignPhase
from archflow.state.state_record import (
    CHECK_KINDS,
    RECORD_BINDING_PHASE,
    Entity,
    Lineage,
    Parameter,
    Relation,
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    ValidatorBinding,
    apply_state_record_operator,
    compile_component_edit,
    combine_component_changes,
    developed_design_view,
    parameter_bindings_of,
    resolve_element_bindings,
)


def _record() -> StateRecord:
    entities = (
        Entity("building", "Component@1", {"semantic_kind": "whole-building", "intent": "villa", "typology": "centralized villa"}),
        Entity("portico-west", "Component@1", {"semantic_kind": "arrival-and-buttress", "intent": "west portico"}, parent_id="building"),
        Entity("portico-columns", "Component@1", {"semantic_kind": "vertical-support", "intent": "six columns"}, parent_id="portico-west"),
        Entity("portico-entablature", "Component@1", {"semantic_kind": "horizontal-load-transfer", "intent": "entablature"}, parent_id="portico-west"),
        Entity("level-piano-nobile", "Level@1", {"role": "piano-nobile", "elevation": 3.57}, basis_refs=("reading:plan",)),
        Entity("axis-1", "GridAxis@1", {"role": "1", "origin": [-10.71, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}),
        Entity("columns-west", "Element@1", {"component_id": "portico-columns", "producer": "column-array", "base_level": "level-piano-nobile"}, lineage=Lineage(introduced_at="stage-2")),
        Entity("entablature-west", "Element@1", {"component_id": "portico-entablature", "producer": "beam", "host": "columns-west"}, lineage=Lineage(introduced_at="stage-2")),
    )
    parameters = (
        Parameter("column_diameter", 0.714, "m", epistemic_status="declared", source_ref="reading:plan"),
        Parameter("column_height", 6.426, "m", expr="9 * column_diameter", inputs=("column_diameter",), source_ref="rule:ionic-nine-diameters"),
    )
    relations = (
        Relation("columns-support-entablature", "support", "columns-west", "entablature-west", datum_role="columns-west-top", propagation="revalidate",
                 validator=ValidatorBinding("support_contact", tolerance=0.001), basis_refs=("reading:plan",)),
    )
    obligations = (DesignObligation(obligation_id="continuous-load-path", statement="a column grid was chosen: complete the load path to the foundation",
                                    source_ref="relation:columns-support-entablature", status=ObligationStatus.OPEN, subject_refs=("entity:columns-west",)),)
    return StateRecord("demo", "run-1", entities, parameters, relations, obligations, evidence_refs=("reading:plan",), decision_ref="decision:declared")


class StateRecordTests(unittest.TestCase):
    def test_independent_changes_combine_on_original_base_with_protection_checks(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        wall = apply_state_record_operator(record, self._wall_edit(record))
        scalar = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest,
                                     base_state_digest=record.state_digest, target_ref="parameter:column_diameter",
                                     key="column_diameter", value=0.8)
        columns = apply_state_record_operator(record, scalar)
        combined = combine_component_changes(record, (wall, columns))
        result = apply_state_record_operator(record, combined)
        self.assertEqual(result.entity("wall-new"), wall.entity("wall-new"))
        self.assertEqual(result.parameter("column_diameter").value, 0.8)
        self.assertEqual(result.parameter("column_height").value, 7.2)
        self.assertEqual(combined.base_record_digest, record.digest)
        self.assertEqual(scalar.base_state_digest, record.state_digest)
        with self.assertRaisesRegex(StateRecordError, "protected"):
            combine_component_changes(record, (wall, columns), protected=("parameter:column_diameter",))

    def test_combination_refuses_shared_objects_and_declared_dependencies(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        source = record.entity("columns-west")
        dependent = record.entity("entablature-west")
        left = apply_state_record_operator(record, compile_component_edit(record,
            entities=(replace(source, fields={**source.fields, "label": "changed"}),)))
        right = apply_state_record_operator(record, compile_component_edit(record,
            entities=(replace(dependent, fields={**dependent.fields, "label": "changed"}),)))
        for pair in ((left, right), (left, left)):
            with self.assertRaisesRegex(StateRecordError, "overlap or depend"):
                combine_component_changes(record, pair)

    def test_combination_refuses_two_parameters_rebuilding_the_same_element(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        column = record.entity("columns-west")
        record = apply_state_record_operator(record, compile_component_edit(record,
            parameters=(Parameter("width", 1, "m"), Parameter("depth", 1, "m")),
            entities=(replace(column, fields={**column.fields, "params": {"width": "@width", "depth": "@depth"}}),)))
        results = tuple(apply_state_record_operator(record, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest,
            base_state_digest=record.state_digest, target_ref=f"parameter:{key}", key=key, value=2,
        )) for key in ("width", "depth"))
        with self.assertRaisesRegex(StateRecordError, "entity:columns-west"):
            combine_component_changes(record, results)

    def test_combination_checks_dependencies_introduced_by_the_other_candidate(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        added = apply_state_record_operator(record, compile_component_edit(record, entities=(
            Entity("cabinet-new", "Element@1", {"component_id": "building", "producer": "prism",
                    "params": {"height": "@column_diameter"}}, parent_id="building"),)))
        edited = apply_state_record_operator(record, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest,
            base_state_digest=record.state_digest, target_ref="parameter:column_diameter", key="column_diameter", value=0.8,
        ))
        for pair in ((added, edited), (edited, added)):
            with self.assertRaisesRegex(StateRecordError, "entity:cabinet-new"):
                combine_component_changes(record, pair)

    def test_retained_operator_replays_components_and_scalars_on_exact_parent(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        edits = (
            self._wall_edit(record),
            StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR,
                                base_record_digest=record.digest, base_state_digest=record.state_digest,
                                target_ref="parameter:column_diameter", key="column_diameter", value=0.8),
        )
        for edit in edits:
            with self.subTest(kind=edit.kind):
                retained = StateRecordOperator.from_dict(edit.to_dict())
                expected = apply_state_record_operator(record, edit)
                self.assertEqual(apply_state_record_operator(record, retained).digest, expected.digest)
                with self.assertRaises(StateRecordError):
                    apply_state_record_operator(expected, retained)

    def _wall_edit(self, record: StateRecord, **extra) -> StateRecordOperator:
        return compile_component_edit(
            record,
            entities=(
                Entity("opening-source", "Reading@1", {"note": "declared arch profile"}, basis_refs=("reading:plan",)),
                Entity("arch-type", "Type@1", {"producer": "opening", "params": {
                    "profile": "semicircular", "width": "@opening_width", "rise": 0.6,
                }}, basis_refs=("reading:plan",)),
                Entity("wall-new", "Element@1", {
                    "component_id": "building", "producer": "wall", "references": {},
                    "params": {"height": "@wall_height", "thickness": 0.3},
                }, parent_id="building", basis_refs=("reading:plan",)),
                Entity("arch-new", "Element@1", {
                    "component_id": "building", "producer": "opening", "type_ref": "arch-type",
                    "references": {"host": {"host": {"element": "wall-new"}}},
                    "params": {"width": "@opening_width", "height": "@opening_height"},
                }, parent_id="building", basis_refs=("reading:plan",)),
            ),
            parameters=(
                Parameter("wall_height", 3, "m", epistemic_status="declared"),
                Parameter("opening_width", 1.2, "m", epistemic_status="declared"),
                Parameter("opening_height", 0, "m", expr="opening_width * 2"),
            ),
            relations=(Relation("wall-hosts-arch", "hosts_void", "wall-new", "arch-new"),),
            **extra,
        )

    def test_component_edit_adds_named_elements_bindings_and_relations_atomically(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        operator = self._wall_edit(record)
        successor = apply_state_record_operator(record, operator)

        self.assertEqual(successor.entities[:len(record.entities)], record.entities)
        self.assertEqual(successor.parameter("opening_height").value, 2.4)
        self.assertEqual(resolve_element_bindings(successor)["arch-new"]["params"]["height"], 2.4)
        self.assertEqual(successor.entity("arch-new").fields["type_ref"], "arch-type")
        self.assertIn("entity:arch-new", successor.closure(("entity:arch-type",)))
        self.assertIn("entity:arch-new", successor.closure(("entity:wall-new",)))
        self.assertEqual(successor.digest, apply_state_record_operator(record, operator).digest)

        reloaded = StateRecord.from_dict(successor.to_dict())
        continued = apply_state_record_operator(reloaded, compile_component_edit(
            reloaded, parameters=(replace(reloaded.parameter("opening_width"), value=1.5),),
        ))
        self.assertEqual(continued.parameter("opening_height").value, 3)
        self.assertEqual(continued.entity("arch-new"), reloaded.entity("arch-new"))
        scalar = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=continued.digest, base_state_digest=continued.state_digest,
            target_ref="parameter:opening_width", key="opening_width", value=2,
        )
        self.assertEqual(apply_state_record_operator(continued, scalar).parameter("opening_height").value, 4)

    def test_component_delete_removes_relations_and_can_remove_related_parameters_together(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        record = apply_state_record_operator(record, self._wall_edit(record))
        successor = apply_state_record_operator(record, compile_component_edit(
            record, remove_entity_ids=("arch-new",), remove_parameter_keys=("opening_height",),
        ))

        self.assertNotIn("wall-hosts-arch", {relation.relation_id for relation in successor.relations})
        self.assertNotIn("entity:arch-new", successor.closure(("entity:wall-new",)))
        self.assertEqual(successor.entity("wall-new"), record.entity("wall-new"))
        self.assertNotIn("opening_height", {parameter.key for parameter in successor.parameters})
        self.assertEqual(len(successor.entities), len(record.entities) - 1)

    def test_type_defaults_share_one_parameter_projection_and_instance_overrides(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        record = apply_state_record_operator(record, self._wall_edit(record))
        declared_type = replace(record.entity("arch-type"), fields={
            **record.entity("arch-type").fields,
            "params": {**record.entity("arch-type").fields["params"], "openings": [{"width": 1}]},
            "references": {"base": {"level": "level-piano-nobile"}},
        })
        instance = replace(record.entity("arch-new"), fields={
            **record.entity("arch-new").fields, "type_ref": "entity:arch-type",
            "params": {"height": "@opening_height", "openings": [{"width": 2}]},
        })
        record = apply_state_record_operator(record, compile_component_edit(record, entities=(declared_type, instance)))
        resolved = resolve_element_bindings(record)["arch-new"]
        self.assertEqual(resolved["type_ref"], "arch-type")
        self.assertEqual(resolved["params"], {"profile": "semicircular", "width": 1.2, "rise": 0.6, "height": 2.4, "openings": [{"width": 2}]})
        self.assertEqual(resolved["references"]["base"], {"level": "level-piano-nobile"})
        self.assertEqual(resolved["references"]["host"], instance.fields["references"]["host"])
        self.assertIn(("params.width", "opening_width"), parameter_bindings_of(instance, record))
        self.assertEqual(record.entity("arch-new"), instance)
        self.assertIn("entity:arch-type", record.closure(("parameter:opening_width",)))
        self.assertIn("entity:arch-new", record.closure(("parameter:opening_width",)))

        scalar = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
            target_ref="entity:arch-new", key="rise", value=0.8,
        )
        successor = apply_state_record_operator(record, scalar)
        self.assertEqual(successor.entity("arch-new").fields["params"], {**instance.fields["params"], "rise": 0.8})
        self.assertEqual(successor.entity("arch-type"), declared_type)
        with self.assertRaisesRegex(StateRecordError, "bound to parameter opening_width"):
            apply_state_record_operator(record, replace(scalar, key="width", value=2))
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: entity:arch-new"):
            apply_state_record_operator(record, compile_component_edit(record,
                parameters=(replace(record.parameter("opening_width"), value=2),), protected=("entity:arch-new",),
            ))

    def test_type_instance_requires_matching_producer_and_known_type_and_parameters(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        record = apply_state_record_operator(record, self._wall_edit(record))
        instance = record.entity("arch-new")
        for fields, error in (
            ({"type_ref": "wall-new"}, "names no Type"),
            ({"type_ref": "missing"}, "names no Type"),
            ({"producer": "beam"}, "producer must match type"),
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(StateRecordError, error):
                apply_state_record_operator(record, compile_component_edit(record, entities=(replace(instance, fields={**instance.fields, **fields}),)))
        with self.assertRaisesRegex(StateRecordError, "binds @missing"):
            apply_state_record_operator(record, compile_component_edit(record, entities=(replace(record.entity("arch-type"), fields={
                "producer": "opening", "params": {"width": "@missing"},
            }),)))

    def test_component_delete_refuses_dangling_host_type_parent_member_and_parameter(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        record = apply_state_record_operator(record, self._wall_edit(record))
        cases = (
            ({"remove_entity_ids": ("wall-new",)}, "reference host names no entity"),
            ({"remove_entity_ids": ("arch-type",)}, "type_ref names no Type"),
            ({"remove_entity_ids": ("building",)}, "unknown parent"),
            ({"remove_entity_ids": ("portico-columns",)}, "component_id names no Component"),
            ({"remove_parameter_keys": ("wall_height",)}, "names no parameter"),
            ({"remove_parameter_keys": ("opening_width",), "entities": (replace(record.entity("arch-new"), fields={
                **record.entity("arch-new").fields, "params": {"height": "@opening_height"},
            }),)}, "opening_width"),
        )
        for edit, error in cases:
            with self.subTest(edit=edit), self.assertRaisesRegex(StateRecordError, error):
                apply_state_record_operator(record, compile_component_edit(record, **edit))

    def test_component_relation_replacement_and_removal_update_dependencies(self) -> None:
        record = replace(_record(), base=ProjectVersionRef("demo", 0, "0" * 64))
        record = apply_state_record_operator(record, self._wall_edit(record))
        old_relation = record.relations[-1]
        replacement = replace(old_relation, object="entablature-west")
        successor = apply_state_record_operator(record, compile_component_edit(record, relations=(replacement,)))
        self.assertEqual(successor.relations[-1], replacement)
        self.assertIn("entity:entablature-west", successor.closure(("entity:wall-new",)))
        deleted = apply_state_record_operator(successor, compile_component_edit(successor, remove_relation_ids=(replacement.relation_id,)))
        self.assertNotIn("entity:entablature-west", deleted.closure(("entity:wall-new",)))

    def test_component_edit_checks_new_and_previous_protected_dependencies_and_locks(self) -> None:
        record = self._bound_record()
        new_relation = Relation("columns-reach-building", "adjacent", "columns-west", "building")
        operator = compile_component_edit(record, relations=(new_relation,), protected=("entity:building",))
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: entity:building"):
            apply_state_record_operator(record, operator)
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: entity:entablature-west"):
            apply_state_record_operator(record, compile_component_edit(record, remove_relation_ids=(record.relations[0].relation_id,), protected=("entity:entablature-west",)))

        locked = replace(record, parameters=(replace(record.parameters[0], lock_authority="architect"), record.parameters[1]))
        for edited in (replace(locked.parameter("source"), value=5), replace(locked.parameter("source"), lock_authority=None)):
            with self.subTest(edited=edited), self.assertRaisesRegex(StateRecordError, "locked parameters"):
                apply_state_record_operator(locked, compile_component_edit(locked, parameters=(edited,)))

    def test_component_edit_refuses_both_stale_identities(self) -> None:
        record = self._bound_record()
        operator = compile_component_edit(record, parameters=(replace(record.parameter("source"), value=4),))
        for field_name in ("base_record_digest", "base_state_digest"):
            with self.subTest(field=field_name), self.assertRaisesRegex(StateRecordError, "exact base is stale"):
                apply_state_record_operator(record, replace(operator, **{field_name: "f" * 64}))

    def test_component_edit_refuses_empty_unknown_ambiguous_and_wrong_schema_edits(self) -> None:
        record = self._bound_record()
        with self.assertRaisesRegex(StateRecordError, "at least one edit"):
            compile_component_edit(record)
        for remove_field in ("remove_entity_ids", "remove_parameter_keys", "remove_relation_ids"):
            with self.subTest(remove=remove_field), self.assertRaisesRegex(StateRecordError, "removes unknown"):
                apply_state_record_operator(record, compile_component_edit(record, **{remove_field: ("missing",)}))
        with self.assertRaisesRegex(StateRecordError, "both edits and removes"):
            apply_state_record_operator(record, compile_component_edit(record, entities=(record.entity("columns-west"),), remove_entity_ids=("columns-west",)))
        with self.assertRaisesRegex(StateRecordError, "ids must be unique"):
            apply_state_record_operator(record, compile_component_edit(record, parameters=(record.parameter("source"), record.parameter("source"))))
        with self.assertRaisesRegex(StateRecordError, "cannot change schema"):
            apply_state_record_operator(record, compile_component_edit(record, entities=(Entity("columns-west", "Type@1", {}),)))
        with self.assertRaisesRegex(StateRecordError, "cannot edit entity schema"):
            apply_state_record_operator(record, compile_component_edit(record, entities=(record.entity("level-piano-nobile"),)))
        with self.assertRaisesRegex(StateRecordError, "only edit_components"):
            replace(compile_component_edit(record, remove_entity_ids=("columns-west",)), kind=StateRecordEditKind.REINDEX)

    def test_typed_operator_changes_one_value_deterministically(self) -> None:
        record = replace(
            _record(), base=ProjectVersionRef("demo", 0, "0" * 64)
        )
        operator = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR,
            base_record_digest=record.digest,
            base_state_digest=record.state_digest,
            target_ref="parameter:column_diameter",
            key="column_diameter",
            value=0.72,
        )

        first = apply_state_record_operator(record, operator)
        second = apply_state_record_operator(record, operator)

        self.assertEqual(first.digest, second.digest)
        self.assertNotEqual(first.digest, record.digest)
        self.assertEqual(first.entities, record.entities)
        self.assertEqual(first.parameter("column_diameter").value, 0.72)
        # the declared dependency is followed: column_height = 9 * column_diameter is re-evaluated,
        # and nothing about the declaration itself (expr, inputs, basis) moves
        self.assertAlmostEqual(first.parameter("column_height").value, 6.48)
        self.assertEqual(replace(first.parameter("column_height"), value=record.parameter("column_height").value), record.parameter("column_height"))

    def test_typed_operator_refuses_an_old_complete_record_base(self) -> None:
        source = _record()
        columns = source.entity("columns-west")
        record = replace(
            source,
            entities=tuple(
                replace(
                    entity,
                    fields={**entity.fields, "params": {"height": 6.426}},
                )
                if entity.entity_id == columns.entity_id
                else replace(
                    entity,
                    fields={**entity.fields, "volume_ids": ["volume-main"]},
                )
                if entity.entity_id == "building"
                else entity
                for entity in source.entities
            )
            + (
                Entity(
                    "massing-ground",
                    "MassingLevel@1",
                    {"base_y": 0, "height": 4},
                ),
                Entity(
                    "volume-main",
                    "Volume@1",
                    {
                        "min": [0, 0, 0],
                        "max": [4, 4, 4],
                        "level_ids": ["massing-ground"],
                    },
                ),
                Entity(
                    "space-main",
                    "Space@1",
                    {
                        "program_node_refs": ["program:public/main"],
                        "level_ids": ["massing-ground"],
                        "volume_ids": ["volume-main"],
                    },
                ),
            ),
            option={"option_id": "complete-record-base"},
            base=ProjectVersionRef("demo", 0, "0" * 64),
        )
        first_edit = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR,
            base_record_digest=record.digest,
            base_state_digest=record.state_digest,
            target_ref="entity:columns-west",
            key="height",
            value=6.5,
        )
        stale_edit = replace(first_edit, value=6.6)
        changed = apply_state_record_operator(record, first_edit)
        self.assertEqual(changed.state_digest, record.state_digest)

        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(changed, stale_edit)

        rebound = replace(
            record,
            run_id="run-2",
            base=ProjectVersionRef("demo", 1, "1" * 64),
        )
        self.assertEqual(rebound.digest, record.digest)
        self.assertNotEqual(rebound.state_digest, record.state_digest)
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(rebound, first_edit)

    def test_program_operator_cannot_rewrite_existing_space_identity(self) -> None:
        record = replace(
            _record(),
            entities=_record().entities
            + (
                Entity(
                    "existing-space",
                    "Space@1",
                    {
                        "program_node_refs": ["program:public/existing"],
                        "level_ids": [],
                        "volume_ids": [],
                    },
                    parent_id="building",
                    basis_refs=("reading:plan",),
                    lineage=Lineage(introduced_at="stage-2"),
                ),
            ),
            base=ProjectVersionRef("demo", 0, "0" * 64),
        )
        previous = record.entity("existing-space")
        rewritten = replace(
            previous,
            parent_id="portico-west",
            fields={
                **previous.fields,
                "program_node_refs": [
                    "program:public/existing",
                    "program:public/new",
                ],
            },
        )
        operator = StateRecordOperator(
            kind=StateRecordEditKind.APPLY_PROGRAM,
            base_record_digest=record.digest,
            base_state_digest=record.state_digest,
            entities=(rewritten,),
        )

        with self.assertRaisesRegex(StateRecordError, "may only append"):
            apply_state_record_operator(record, operator)

    def test_reindex_operator_accepts_only_index_entity_schemas(self) -> None:
        record = replace(
            _record(), base=ProjectVersionRef("demo", 0, "0" * 64)
        )
        operator = StateRecordOperator(
            kind=StateRecordEditKind.REINDEX,
            base_record_digest=record.digest,
            base_state_digest=record.state_digest,
            entities=(
                Entity(
                    "invented-component",
                    "Component@1",
                    {"semantic_kind": "whole-building"},
                ),
            ),
        )

        with self.assertRaisesRegex(StateRecordError, "cannot add entity schemas"):
            apply_state_record_operator(record, operator)

    def test_typed_operator_refuses_stale_base_and_protected_closure(self) -> None:
        record = replace(
            _record(), base=ProjectVersionRef("demo", 0, "0" * 64)
        )
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(
                record,
                StateRecordOperator(
                    kind=StateRecordEditKind.SET_SCALAR,
                    base_record_digest="f" * 64,
                    base_state_digest=record.state_digest,
                    target_ref="parameter:column_diameter",
                    key="column_diameter",
                    value=0.72,
                ),
            )
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs"):
            apply_state_record_operator(
                record,
                StateRecordOperator(
                    kind=StateRecordEditKind.SET_SCALAR,
                    base_record_digest=record.digest,
                    base_state_digest=record.state_digest,
                    protected=("parameter:column_height",),
                    target_ref="parameter:column_diameter",
                    key="column_diameter",
                    value=0.72,
                ),
            )

    def test_typed_operator_refuses_a_locked_parameter_in_the_closure(self) -> None:
        authored = _record()
        locked = replace(
            authored,
            parameters=(
                authored.parameters[0],
                replace(authored.parameters[1], lock_authority="architect"),
            ),
            base=ProjectVersionRef("demo", 0, "0" * 64),
        )
        operator = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR,
            base_record_digest=locked.digest,
            base_state_digest=locked.state_digest,
            target_ref="parameter:column_diameter",
            key="column_diameter",
            value=0.72,
        )

        with self.assertRaisesRegex(StateRecordError, "locked parameters"):
            apply_state_record_operator(locked, operator)

    def test_record_round_trips_and_digests(self) -> None:
        record = _record()
        self.assertEqual(StateRecord.from_dict(record.to_dict()).digest, record.digest)
        self.assertEqual([e.entity_id for e in record.entities_of("Level@1")], ["level-piano-nobile"])
        self.assertEqual(record.parameter("column_height").inputs, ("column_diameter",))
        self.assertEqual(record.entity("columns-west").lineage.introduced_at, "stage-2")

    def test_relations_and_obligations_stay_apart(self) -> None:
        record = _record()
        relation = record.relations[0]
        self.assertEqual(relation.validator.check_kind, "support_contact")
        self.assertEqual(relation.datum_role, "columns-west-top")
        obligation = record.obligations[0]
        self.assertIs(obligation.status, ObligationStatus.OPEN)
        self.assertEqual(obligation.source_ref, "relation:columns-support-entablature")     # created by the relation, not the relation

    def test_edges_and_closure_follow_references(self) -> None:
        record = _record()
        edges = record.dependency_edges()
        kinds = {(e.upstream_ref, e.downstream_ref, e.effect) for e in edges}
        self.assertIn(("entity:columns-west", "entity:entablature-west", DependencyEffect.REQUIRES_REVALIDATION), kinds)     # the relation
        self.assertIn(("parameter:column_diameter", "parameter:column_height", DependencyEffect.REQUIRES_REVALIDATION), kinds)
        self.assertIn(("entity:level-piano-nobile", "entity:columns-west", DependencyEffect.REQUIRES_REVALIDATION), kinds)   # base_level field
        self.assertIn(("entity:columns-west", "entity:entablature-west", DependencyEffect.INVALIDATES), kinds)                # host field
        closure = record.closure(("entity:level-piano-nobile",))
        self.assertEqual(closure, ("entity:columns-west", "entity:entablature-west", "entity:level-piano-nobile"))
        self.assertEqual(record.closure(("entity:axis-1",)), ("entity:axis-1",))                                             # nothing references the axis yet

    def test_batched_closures_remain_independent_through_cycles_and_unchanged_relations(self) -> None:
        record = _record()
        record = replace(record, relations=(*record.relations,
            Relation("return-to-columns", "dependency", "entablature-west", "columns-west", propagation="invalidate"),
            Relation("axis-unaffected", "dependency", "entablature-west", "axis-1", propagation="unchanged"),
        ))
        groups = ((), ("entity:axis-1",), ("entity:level-piano-nobile",),
                  ("parameter:column_diameter",), ("entity:entablature-west", "entity:entablature-west"))
        expected = ((), ("entity:axis-1",),
                    ("entity:columns-west", "entity:entablature-west", "entity:level-piano-nobile"),
                    ("parameter:column_diameter", "parameter:column_height"),
                    ("entity:columns-west", "entity:entablature-west"))
        self.assertEqual(record.closures(groups), expected)
        self.assertEqual(tuple(record.closure(group) for group in groups), expected)
        self.assertEqual(record.closures(()), ())
        changed = replace(record, relations=(*record.relations[:-1],
            replace(record.relations[-1], propagation="revalidate"),
        ))
        self.assertEqual(changed.closures((("entity:entablature-west",),)),
                         (("entity:axis-1", "entity:columns-west", "entity:entablature-west"),))

    def test_gaps_fail_typed(self) -> None:
        with self.assertRaises(StateRecordError):
            Entity("x", "Widget@1", {})
        with self.assertRaises(StateRecordError):
            Relation("r", "support", "a", "a")
        with self.assertRaises(StateRecordError):
            Relation("r", "sits_on", "a", "b")                                    # not in the kernel vocabulary
        with self.assertRaises(StateRecordError):
            ValidatorBinding("magic")
        record = _record()
        with self.assertRaises(StateRecordError):
            StateRecord("demo", "run-1", record.entities, (Parameter("h", 1.0, "m", inputs=("missing",)),))
        with self.assertRaises(StateRecordError):
            StateRecord("demo", "run-1", record.entities, relations=(Relation("r", "support", "columns-west", "nowhere"),))

    def test_a_validator_names_a_check_the_spine_can_measure(self) -> None:
        self.assertEqual(sorted(CHECK_KINDS), ["aperture_exists", "clearance_interval", "lintel_minimum_bearing", "solid_nonpenetration", "support_contact"])
        for kind in ("alignment", "meets", "engagement_interval", "separation_interval", "magic"):
            with self.assertRaises(StateRecordError) as raised:                       # a kind nothing measures is refused here, not reported unchecked forever
                ValidatorBinding(kind)
            for registered in CHECK_KINDS:
                self.assertIn(registered, str(raised.exception))

    def test_a_validator_carries_the_field_its_check_measures(self) -> None:
        self.assertEqual(ValidatorBinding("clearance_interval", interval_m=(0.9, 1.5)).interval_m, (0.9, 1.5))
        self.assertEqual(ValidatorBinding("aperture_exists", tolerance=0.01).tolerance, 0.01)
        for wrong in (lambda: ValidatorBinding("clearance_interval", tolerance=0.01),                # an interval check takes no tolerance
                      lambda: ValidatorBinding("clearance_interval"),                                # and needs its interval
                      lambda: ValidatorBinding("clearance_interval", interval_m=(1.5, 0.9)),         # low above high
                      lambda: ValidatorBinding("support_contact", interval_m=(0.0, 1.0)),            # a tolerance check takes no interval
                      lambda: ValidatorBinding("aperture_exists", tolerance=-0.01)):
            with self.assertRaises(StateRecordError):
                wrong()

    def test_solid_pair_relations_require_final_pairs_and_can_check_one_hosts_distinct_outputs(self) -> None:
        validator = ValidatorBinding("solid_nonpenetration", tolerance=0)
        relation = Relation("jamb-head", "clearance", "columns-west", "columns-west", validator=validator,
                            parameters={"object_pairs": [["final-jamb", "final-head"]]})
        self.assertEqual(Relation.from_dict(relation.to_dict()), relation)
        record = replace(_record(), relations=(relation,))
        self.assertEqual(StateRecord.from_dict(record.to_dict()).digest, record.digest)
        self.assertIsNone(relation.dependency_edge())
        with self.assertRaises(StateRecordError):
            ValidatorBinding("solid_nonpenetration", tolerance=0.001)
        with self.assertRaises(StateRecordError):
            Relation("self-support", "support", "columns-west", "columns-west")
        for pairs in (None, [], [["final-jamb"]], [["final-jamb", "final-jamb"]]):
            with self.subTest(pairs=pairs), self.assertRaises(StateRecordError):
                Relation("bad-solid-pair", "clearance", "columns-west", "columns-west", validator=validator,
                         parameters={"object_pairs": pairs})

    def test_production_entry_accepts_the_record(self) -> None:
        from monkeyarch.capabilities.geometry_proposal import GeometryProposalProductionError, _as_developed_state
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = _as_developed_state(_record(), run=run, phase=DesignPhase.DESIGN_DEVELOPMENT)
            self.assertEqual(state.selected_schematic.option.option_id, "declared")
            self.assertIs(_as_developed_state(state, run=run, phase=None), state)          # legacy input passes through untouched, and states its own phase
            bare = StateRecord("demo", "run-1", _record().entities, decision_ref="decision:declared")
            with self.assertRaises(GeometryProposalProductionError):
                _as_developed_state(bare, run=run, phase=DesignPhase.DESIGN_DEVELOPMENT)   # no evidence: typed refusal
            with self.assertRaises(GeometryProposalProductionError):
                _as_developed_state(_record(), run=run, phase=None)                         # a record states no phase: the run's must be passed

    def test_view_without_massing_keeps_every_evidence_source(self) -> None:
        # A record with several evidence refs and no massing entities: every component carries all of them
        # (design_components_of), so the block-view proposal must declare the same set, not just the first.
        base = _record()
        record = replace(base, evidence_refs=("reading:manufacturer-board", "reading:plan", "reading:section"))
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            # The record projects in its own binding phase, as the round-trip test above does.
            state = developed_design_view(record, run=run, evidence_ref="reading:detail-review",
                                          phase=RECORD_BINDING_PHASE)
        proposal = state.selected_schematic.option.proposal
        self.assertEqual(proposal.evidence_refs, ("reading:detail-review", "reading:manufacturer-board", "reading:plan", "reading:section"))
        self.assertTrue(proposal.components)
        for component in proposal.components:
            self.assertEqual(set(component.source_refs), set(proposal.evidence_refs))    # nothing dropped, nothing invented

    def test_record_without_massing_preserves_all_drawing_and_material_sources(self) -> None:
        manufacturer = "reading:manufacturer-board"
        review = "reading:detail-review"
        original = _record()
        record = replace(original, evidence_refs=tuple(sorted((*original.evidence_refs, manufacturer))), entities=original.entities + (
            Entity("manufacturer-board", "Reading@1", {"source_ref": "source:manufacturer-board.pdf", "thickness_m": 0.012},
                   basis_refs=(manufacturer,)),
        ))
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = developed_design_view(record, run=run, evidence_ref=review, phase=RECORD_BINDING_PHASE)
        proposal = state.selected_schematic.option.proposal
        self.assertEqual(set(proposal.evidence_refs), {"reading:plan", manufacturer, review})
        self.assertTrue(proposal.components)
        for component in proposal.components:
            self.assertTrue(set(record.evidence_refs) <= set(component.source_refs))
            self.assertTrue(set(component.source_refs) <= set(proposal.evidence_refs))
        self.assertEqual(record.entity("manufacturer-board").fields["source_ref"], "source:manufacturer-board.pdf")

    def test_massing_entities_rebuild_the_spatial_option_exactly(self) -> None:
        from monkeyarch.runtime.project_runner import SchematicPack, bootstrap_developed_state
        from tests.test_project_runner import EVIDENCE, _component
        pack = SchematicPack.from_dict({
            "schema": "SchematicPack@1", "project_id": "demo", "option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico",
            "rationale": "declared from the survey record", "evidence_refs": [EVIDENCE],
            "levels": [{"level_id": "ground", "base_y": 0, "height": 12}], "volumes": [{"volume_id": "block", "min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}],
            "zones": [{"zone_id": "hall", "program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]}], "connections": [],
            "components": [_component("building", None, "whole-building", "one block", ("block",)), _component("portico", "building", "arrival-and-buttress", "front portico")],
            "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"]})
        entities = [Entity(c.component_id, "Component@1", {k: v for k, v in c.to_dict().items() if k not in ("component_id", "parent_component_id")}, parent_id=c.parent_component_id)
                    for c in pack.components]
        entities += [Entity(l["level_id"], "MassingLevel@1", {"base_y": l["base_y"], "height": l["height"]}) for l in pack.levels]
        entities += [Entity(v["volume_id"], "Volume@1", {"min": v["min"], "max": v["max"], "level_ids": v["level_ids"]}) for v in pack.volumes]
        entities += [Entity(z["zone_id"], "Space@1", {"program_node_refs": z["program_node_refs"], "level_ids": z["level_ids"], "volume_ids": z["volume_ids"]}) for z in pack.zones]
        entities += [Entity(c["connection_id"], "Connection@1", {k: v for k, v in c.items() if k != "connection_id"}) for c in pack.connections]
        record = StateRecord(pack.project_id, "run-1", tuple(entities), evidence_refs=pack.evidence_refs,
                             option={"option_id": pack.option_id, "label": pack.label, "typology": pack.typology, "rationale": pack.rationale,
                                     "footprint_cells": [list(c) for c in pack.footprint_cells], "assumption_refs": list(pack.assumption_refs)})
        self.assertEqual(StateRecord.from_dict(record.to_dict()).digest, record.digest)
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / pack.project_id, project_id=pack.project_id, initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            via_record = developed_design_view(record, run=run, branch_id="runner-v1", portfolio_id="declared", selection_decision_ref="decision:declared",
                                               phase=DesignPhase.DESIGN_DEVELOPMENT)
            via_pack = bootstrap_developed_state(pack, run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared",
                                                 phase=DesignPhase.DESIGN_DEVELOPMENT)
            self.assertEqual(via_record.state_digest, via_pack.state_digest)                    # one state, whichever door it came through
        with self.assertRaises(StateRecordError):
            StateRecord(pack.project_id, "run-1", tuple(entities), option={"label": "no id"})
        with self.assertRaises(StateRecordError):
            StateRecord(pack.project_id, "run-1", tuple(entities) + (Entity("z2", "Space@1", {"program_node_refs": [], "level_ids": [], "volume_ids": ["nowhere"]}),))

    def test_an_authored_record_is_bound_before_it_can_name_its_state(self) -> None:
        """A portable record has no base; bound_to is the one sanctioned way to give it one."""

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            authored = _record()
            self.assertIsNone(authored.base)
            with self.assertRaises(StateRecordError):
                authored.state_digest                                                   # cannot name its own run
            bound = authored.bound_to(run)
            self.assertEqual(bound.base, run.base)
            self.assertEqual(bound.run_ref, run)
            self.assertEqual(len(bound.state_digest), 64)
            self.assertEqual(authored.digest, StateRecord.from_dict(authored.to_dict()).digest)   # the authored record is untouched
            other = FilesystemProjectRepository.initialize(Path(tmp) / "other", project_id="other", initial_state={"schema": "TestState@1"}).create_run("run-1")
            with self.assertRaises(StateRecordError):
                authored.bound_to(other)                                                # another project's run

    def test_the_compiler_binds_a_program_to_the_record_itself(self) -> None:
        """P102 last step: the record answers the four identity questions, so the compiler takes it directly."""

        from monkeyarch.compilers.geometry import compile_geometry_program
        from tests.test_geometry_compiler import COMMITMENT, _proposal, _state

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            record = replace(_record(), run_id=run.run_id, base=run.base)
            self.assertEqual(record.run_ref, run)
            view = developed_design_view(record, run=run, phase=RECORD_BINDING_PHASE)
            self.assertEqual(record.state_digest, view.state_digest)                     # the record cites its own projection
            self.assertEqual(StateRecord.from_dict(record.to_dict()).base, run.base)     # base survives the round trip

            legacy = _state()                                                            # the compiler fixture's own state
            proposal = _proposal(legacy)
            by_view = compile_geometry_program(legacy, proposal, active_commitment_refs=(COMMITMENT,))
            self.assertIsNotNone(by_view.program)
            bound = replace(record, project_id=legacy.project_id, run_id=legacy.run_id, base=legacy.base)
            peer = replace(proposal, project_id=bound.project_id, run_id=bound.run_id, base=bound.base, design_state_digest=bound.state_digest)
            self.assertNotEqual(bound.state_digest, legacy.state_digest)                 # different states, same contract
            by_record = compile_geometry_program(bound, peer, active_commitment_refs=(COMMITMENT,))
            self.assertIsNotNone(by_record.program, [(i.code.value, i.detail) for i in by_record.receipt.issues])
            self.assertEqual([o.object_id for o in by_record.program.objects], [o.object_id for o in by_view.program.objects])

            with self.assertRaises(TypeError):
                compile_geometry_program(object(), peer, active_commitment_refs=(COMMITMENT,))
            stale = replace(peer, design_state_digest="0" * 64)
            self.assertIsNone(compile_geometry_program(bound, stale, active_commitment_refs=(COMMITMENT,)).program)   # not exact-base peers
            with self.assertRaises(StateRecordError):
                _record().state_digest                                                    # a record with no base cannot name its run

    def test_developed_design_view_forwards_to_the_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = developed_design_view(_record(), run=run, option_id="declared", evidence_ref="reading:plan", phase=DesignPhase.DESIGN_DEVELOPMENT)
            ids = [c.component_id for c in state.selected_schematic.option.proposal.components]
            self.assertEqual(ids, ["building", "portico-columns", "portico-entablature", "portico-west"])
            self.assertEqual(len(state.state_digest), 64)

    # ---- parameters: declared expressions, explicit bindings, one evaluator (B3)
    def _bound_record(self, *, derived_value: float = 6.0, height_binding="@derived", inputs=("source",), expr="source * 2") -> StateRecord:
        """source=3 declared; derived = source * 2; the beam's height binds @derived, the column's height is the literal 2."""

        base = _record()
        entities = tuple(
            replace(e, fields={**e.fields, "params": {"height": height_binding, "depth": 0.4}}) if e.entity_id == "entablature-west"
            else replace(e, fields={**e.fields, "params": {"height": 2, "radius": 0.3}}) if e.entity_id == "columns-west"
            else e for e in base.entities)
        parameters = (
            Parameter("source", 3.0, "m", epistemic_status="declared", source_ref="reading:plan"),
            Parameter("derived", derived_value, "m", expr=expr, inputs=inputs),
        )
        return replace(base, entities=entities, parameters=parameters, base=ProjectVersionRef("demo", 0, "0" * 64))

    def _edit(self, record: StateRecord, key: str, value: float, **extra) -> StateRecordOperator:
        return StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                                   target_ref=f"parameter:{key}", key=key, value=value, **extra)

    def test_a_source_edit_recomputes_the_derived_parameter_and_the_row_bound_to_it(self) -> None:
        from archflow.state.state_record import evaluate_parameters, resolve_element_bindings, stale_parameters

        record = self._bound_record()
        self.assertEqual(stale_parameters(record), ())
        self.assertEqual(resolve_element_bindings(record)["entablature-west"]["params"], {"height": 6.0, "depth": 0.4})
        self.assertEqual(resolve_element_bindings(record)["columns-west"]["params"], {"height": 2, "radius": 0.3})        # a literal is a literal

        successor = apply_state_record_operator(record, self._edit(record, "source", 10))
        self.assertEqual(successor.parameter("source").value, 10)
        self.assertEqual(successor.parameter("derived").value, 20.0)                                                      # declared downstream recomputed
        self.assertEqual(evaluate_parameters(successor)["derived"], 20.0)
        self.assertEqual(resolve_element_bindings(successor)["entablature-west"]["params"]["height"], 20.0)               # the bound row follows
        self.assertEqual(successor.entity("entablature-west").fields["params"]["height"], "@derived")                       # the binding itself is kept, not baked
        self.assertEqual(resolve_element_bindings(successor)["columns-west"]["params"]["height"], 2)                      # the unrelated literal stands
        self.assertEqual(successor.entity("columns-west"), record.entity("columns-west"))
        # the binding is a dependency edge: the change reaches the row, so protecting the row refuses the edit
        edges = {(e.upstream_ref, e.downstream_ref, e.relation, e.effect) for e in record.dependency_edges()}
        self.assertIn(("parameter:derived", "entity:entablature-west", "binds", DependencyEffect.INVALIDATES), edges)
        self.assertEqual(record.closure(("parameter:source",)), ("entity:entablature-west", "parameter:derived", "parameter:source"))
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: entity:entablature-west"):
            apply_state_record_operator(record, self._edit(record, "source", 10, protected=("entity:entablature-west",)))

    def test_recomputation_is_deterministic_across_save_reload_and_continuation(self) -> None:
        from archflow.state.state_record import resolve_element_bindings, stale_parameters

        record = self._bound_record()
        first = apply_state_record_operator(record, self._edit(record, "source", 10))
        again = apply_state_record_operator(record, self._edit(record, "source", 10))
        self.assertEqual(first.digest, again.digest)
        reloaded = StateRecord.from_dict(first.to_dict())                                    # saved and read back: the same record
        self.assertEqual(reloaded.digest, first.digest)
        self.assertEqual(stale_parameters(reloaded), ())
        self.assertEqual(resolve_element_bindings(reloaded)["entablature-west"]["params"]["height"], 20.0)
        continued = apply_state_record_operator(reloaded, self._edit(reloaded, "source", 4))     # continuing from the reloaded successor
        self.assertEqual(continued.parameter("derived").value, 8.0)
        self.assertEqual(StateRecord.from_dict(continued.to_dict()).digest, continued.digest)

    def test_a_stale_serialized_derived_value_is_refused_at_the_binding_and_repaired_by_an_edit(self) -> None:
        from archflow.state.state_record import evaluate_parameters, resolve_element_bindings, stale_parameters

        stale = self._bound_record(derived_value=99.0)                                          # source=3, derived says 99, expr says 6
        self.assertEqual(stale.parameter("derived").value, 99.0)                                 # readable: no migration of what was written
        self.assertEqual(evaluate_parameters(stale)["derived"], 6.0)
        self.assertEqual(stale_parameters(stale), ("derived",))
        with self.assertRaisesRegex(StateRecordError, r"element entablature-west: params.height binds @derived: stored value 99.0 of derived parameter derived disagrees with its expression 'source \* 2' = 6.0"):
            resolve_element_bindings(stale)                                                      # neither 99 nor 6 is read behind the author's back
        repaired = apply_state_record_operator(stale, self._edit(stale, "source", 10))
        self.assertEqual(repaired.parameter("derived").value, 20.0)
        self.assertEqual(stale_parameters(repaired), ())
        self.assertEqual(resolve_element_bindings(repaired)["entablature-west"]["params"]["height"], 20.0)
        # a stale derived value no row binds does not stop the rows that bind nothing
        unbound = self._bound_record(derived_value=99.0, height_binding=1.5)
        self.assertEqual(resolve_element_bindings(unbound)["entablature-west"]["params"]["height"], 1.5)

    def test_cycles_missing_names_and_conflicting_declarations_are_located(self) -> None:
        from archflow.state.state_record import evaluate_parameters

        entities = _record().entities
        with self.assertRaisesRegex(StateRecordError, r"cycle among parameters: a -> b -> a"):
            evaluate_parameters(StateRecord("demo", "run-1", entities, (Parameter("a", 1.0, "m", expr="b * 2"), Parameter("b", 1.0, "m", expr="a / 2"))))
        with self.assertRaisesRegex(StateRecordError, r"parameter a reads unknown name 'missing'"):
            evaluate_parameters(StateRecord("demo", "run-1", entities, (Parameter("a", 1.0, "m", expr="missing * 2"),)))
        with self.assertRaisesRegex(StateRecordError, r"parameter b: declared inputs \['c'\] disagree with its expression 'a \* 2', which reads \['a'\]"):
            evaluate_parameters(StateRecord("demo", "run-1", entities, (Parameter("a", 1.0, "m"), Parameter("c", 1.0, "m"), Parameter("b", 2.0, "m", expr="a * 2", inputs=("c",)))))
        with self.assertRaisesRegex(StateRecordError, r"entity entablature-west: params.height binds @nothing, which names no parameter \(parameters: column_diameter, column_height\)"):
            replace(_record(), entities=tuple(replace(e, fields={**e.fields, "params": {"height": "@nothing"}}) if e.entity_id == "entablature-west" else e for e in _record().entities))

    def test_a_derived_parameter_and_a_bound_row_value_are_edited_through_their_source(self) -> None:
        record = self._bound_record()
        with self.assertRaisesRegex(StateRecordError, r"parameter derived is derived by 'source \* 2' from \['source'\]: edit its inputs, or re-declare it without an expression"):
            apply_state_record_operator(record, self._edit(record, "derived", 7))
        with self.assertRaisesRegex(StateRecordError, r"element entablature-west: params.height is bound to parameter derived; edit that parameter"):
            apply_state_record_operator(record, StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                                                                    target_ref="entity:entablature-west", key="height", value=7))
        # a locked derived parameter downstream still refuses the source edit before anything is recomputed
        locked = replace(record, parameters=(record.parameters[0], replace(record.parameters[1], lock_authority="architect")))
        with self.assertRaisesRegex(StateRecordError, "locked parameters: parameter:derived"):
            apply_state_record_operator(locked, self._edit(locked, "source", 10))

    def test_an_expression_declares_its_dependencies_even_when_inputs_is_empty(self) -> None:
        """The expression is the declaration: closure, protection, locks and recomputation follow it with ``inputs=()``."""

        from archflow.state.state_record import evaluate_parameters, resolve_element_bindings, stale_parameters

        record = self._bound_record(inputs=())
        self.assertEqual(record.parameter("derived").inputs, ())                                                       # the authored array is not rewritten
        self.assertEqual(record.parameter("derived").reads(), ("source",))
        edges = {(e.upstream_ref, e.downstream_ref, e.relation) for e in record.dependency_edges()}
        self.assertIn(("parameter:source", "parameter:derived", "derives"), edges)                                     # used to be absent with inputs=()
        self.assertEqual(record.closure(("parameter:source",)), ("entity:entablature-west", "parameter:derived", "parameter:source"))

        successor = apply_state_record_operator(record, self._edit(record, "source", 10))
        self.assertEqual(successor.parameter("derived").value, 20.0)                                                   # used to keep 6 while the expression said 20
        self.assertEqual(evaluate_parameters(successor)["derived"], 20.0)
        self.assertEqual(stale_parameters(successor), ())
        self.assertEqual(resolve_element_bindings(successor)["entablature-west"]["params"]["height"], 20.0)            # the producer reads the recomputed value
        self.assertEqual(resolve_element_bindings(successor)["columns-west"]["params"]["height"], 2)                   # the literal stands
        self.assertEqual(successor.parameter("derived").inputs, ())
        reloaded = StateRecord.from_dict(successor.to_dict())                                                          # persisted and continued
        self.assertEqual(reloaded.digest, successor.digest)
        continued = apply_state_record_operator(reloaded, self._edit(reloaded, "source", 4))
        self.assertEqual(continued.parameter("derived").value, 8.0)
        self.assertEqual(resolve_element_bindings(continued)["entablature-west"]["params"]["height"], 8.0)
        # protection and locks read the same declaration: both refuse the source edit before anything moves
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: entity:entablature-west"):
            apply_state_record_operator(record, self._edit(record, "source", 10, protected=("entity:entablature-west",)))
        with self.assertRaisesRegex(StateRecordError, "reaches protected refs: parameter:derived"):
            apply_state_record_operator(record, self._edit(record, "source", 10, protected=("parameter:derived",)))
        locked = replace(record, parameters=(record.parameters[0], replace(record.parameters[1], lock_authority="architect")))
        with self.assertRaisesRegex(StateRecordError, "locked parameters: parameter:derived"):
            apply_state_record_operator(locked, self._edit(locked, "source", 10))
        # a stale stored derived value is still refused at the binding, and the direct edit of the derived value is still a conflict
        with self.assertRaisesRegex(StateRecordError, r"binds @derived: stored value 99.0 of derived parameter derived disagrees"):
            resolve_element_bindings(self._bound_record(inputs=(), derived_value=99.0))
        with self.assertRaisesRegex(StateRecordError, r"parameter derived is derived by 'source \* 2' from \['source'\]"):
            apply_state_record_operator(record, self._edit(record, "derived", 7))

    def test_a_dependency_question_never_evaluates_the_expression(self) -> None:
        """``source / (source - 1)`` with source=3 is 1.5 and depends on source; the division is only judged with the real reading."""

        from archflow.state.state_record import evaluate_parameters, resolve_element_bindings, stale_parameters

        record = self._bound_record(inputs=(), expr="source / (source - 1)", derived_value=1.5)
        self.assertEqual(record.parameter("derived").reads(), ("source",))                                              # used to raise division by zero here
        self.assertEqual(stale_parameters(record), ())
        self.assertEqual(evaluate_parameters(record)["derived"], 1.5)
        self.assertEqual(resolve_element_bindings(record)["entablature-west"]["params"]["height"], 1.5)
        self.assertIn(("parameter:source", "parameter:derived"), {(e.upstream_ref, e.downstream_ref) for e in record.dependency_edges()})
        self.assertEqual(apply_state_record_operator(record, self._edit(record, "source", 5)).parameter("derived").value, 1.25)
        with self.assertRaisesRegex(StateRecordError, "parameters: parameter derived: division by zero"):
            apply_state_record_operator(record, self._edit(record, "source", 1))                                       # the real evaluation still refuses
        with self.assertRaisesRegex(StateRecordError, "parameters: parameter broken"):
            Parameter("broken", 1.0, "m", expr="source +").reads()                                                     # a malformed expression is a typed error, not an empty answer

    def test_the_view_carries_the_callers_phase_into_the_binding_identity(self) -> None:
        """The record states no phase; the caller (a run's envelope) does, and it binds the state digest."""

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            record = _record()
            with self.assertRaises(TypeError):
                developed_design_view(record, run=run)                                           # no default: a caller says which run's phase it projects
            developed = developed_design_view(record, run=run, phase=DesignPhase.DESIGN_DEVELOPMENT)
            schematic = developed_design_view(record, run=run, phase=DesignPhase.SCHEMATIC_DESIGN)
            self.assertIs(developed.active_phase, DesignPhase.DESIGN_DEVELOPMENT)               # the historical reading, unchanged
            self.assertIs(schematic.active_phase, DesignPhase.SCHEMATIC_DESIGN)
            self.assertNotEqual(developed.state_digest, schematic.state_digest)
            self.assertEqual(developed.selected_schematic.option.option_digest, schematic.selected_schematic.option.option_digest)   # same content
            self.assertIs(RECORD_BINDING_PHASE, DesignPhase.DESIGN_DEVELOPMENT)                 # the record's own binding identity names its phase
            self.assertEqual(record.bound_to(run).state_digest, developed.state_digest)
            with self.assertRaises(StateRecordError):
                developed_design_view(record, run=run, phase="schematic_design")                 # a phase is a DesignPhase, not text

    def test_binding_changes_state_digest_but_not_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            record = _record()
            bound_a = record.bound_to(RunRef("demo", "run-a", repository.read_head()))
            bound_b = record.bound_to(RunRef("demo", "run-b", repository.read_head()))
            self.assertEqual(bound_a.digest, record.digest)
            self.assertEqual(bound_b.digest, record.digest)
            self.assertNotEqual(bound_a.state_digest, bound_b.state_digest)


if __name__ == "__main__":
    unittest.main()
