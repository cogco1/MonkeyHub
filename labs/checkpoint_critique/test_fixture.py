"""Observable binding, incompleteness and propagation behavior for GH-173."""

from dataclasses import replace
import json
import unittest

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.state_record import (
    StateRecord, StateRecordError, apply_state_record_operator, compile_component_edit,
)
from .fixture import PROTECTED, checks, final_assessment, initial_record, policy_document, propose


def record():
    return initial_record(RunRef("critique-fixture", "trial", ProjectVersionRef("critique-fixture", 0, "a" * 64)))


def advance(source, checkpoint, answer):
    return apply_state_record_operator(source, propose(source, checkpoint, answer))


def complete(side="west", detail="unknown"):
    source = advance(record(), 1, {"entry_side": side, "semantic_detail": detail})
    source = advance(source, 2, {"gallery": "add"})
    return advance(source, 3, {"unit_access": "add"})


class FixtureTests(unittest.TestCase):
    def test_initial_record_has_a_real_supplied_run_and_synthetic_evidence(self):
        source = record()
        self.assertEqual(source.run_ref.base.version, 0)
        self.assertEqual(StateRecord.from_dict(source.to_dict()), source)
        self.assertEqual(source.evidence_refs, ("fixture:synthetic-courtyard-v1",))
        self.assertEqual(len(source.state_digest), 64)

    def test_generic_and_unknown_are_allowed_early_and_at_completion(self):
        for detail in ("generic", "unknown", "resolved"):
            with self.subTest(detail=detail):
                source = advance(record(), 1, {"entry_side": "west", "semantic_detail": detail})
                finding = {item["name"]: item for item in checks(source)}
                self.assertEqual(finding["gallery"]["status"], "pending")
                self.assertEqual(finding["unit_access"]["status"], "pending")
                self.assertFalse([item for item in finding.values()
                                  if item["category"] == "invariant" and item["status"] != "pass"])
                result = final_assessment(complete(detail=detail))
                self.assertTrue(result["complete"])
                self.assertEqual(result["unresolved"], [])
                self.assertEqual(result["unavailable"], ["structural_analysis"])

    def test_completion_does_not_silently_supply_omitted_work(self):
        source = advance(record(), 1, {"entry_side": "west"})
        source = advance(source, 2, {"gallery": "omit"})
        source = advance(source, 3, {"unit_access": "omit"})
        result = final_assessment(source)
        self.assertFalse(result["complete"])
        self.assertEqual(result["unresolved"], ["gallery", "unit_access"])
        self.assertNotIn("gallery_x", {parameter.key for parameter in source.parameters})

    def test_east_entry_is_visible_early_but_not_a_current_invariant_violation(self):
        source = advance(record(), 1, {"entry_side": "east"})
        finding = {item["name"]: item for item in checks(source)}
        self.assertEqual((finding["entry_alignment"]["category"], finding["entry_alignment"]["status"]),
                         ("obligation", "fail"))
        self.assertFalse([item for item in finding.values()
                          if item["category"] == "invariant" and item["status"] == "fail"])
        self.assertEqual(final_assessment(complete("east"))["unresolved"], ["entry_alignment"])

    def test_width_attempt_is_refused_by_the_actual_core_protection(self):
        source = record()
        before = source.to_dict()
        operator = propose(source, 1, {"entry_side": "west", "courtyard_width": 2})
        self.assertEqual(operator.protected, PROTECTED)
        with self.assertRaisesRegex(StateRecordError, "protected refs"):
            apply_state_record_operator(source, operator)
        self.assertEqual(source.to_dict(), before)
        # Removing the lab's protected list still cannot circumvent the core lock.
        with self.assertRaisesRegex(StateRecordError, "locked parameters"):
            apply_state_record_operator(source, replace(operator, protected=()))
        preserved = advance(source, 1, {"entry_side": "west", "courtyard_width": 4})
        self.assertEqual(preserved.parameter("courtyard_width"), source.parameter("courtyard_width"))

    def test_protected_courtyard_entity_and_independent_detection(self):
        source = record()
        courtyard = source.entity("courtyard")
        altered = replace(courtyard, fields={**courtyard.fields, "open_to_sky": False})
        operator = compile_component_edit(source, entities=(altered,), protected=PROTECTED)
        with self.assertRaisesRegex(StateRecordError, "protected refs: entity:courtyard"):
            apply_state_record_operator(source, operator)
        # An externally supplied malformed candidate also fails the independent check.
        tampered = replace(source, entities=tuple(altered if item == courtyard else item for item in source.entities))
        self.assertIn("protected_courtyard", final_assessment(tampered)["unresolved"])

    def test_exact_content_change_and_binding_change_both_refuse_stale_operator(self):
        source = advance(record(), 1, {"entry_side": "east"})
        old_operator = propose(source, 0, {"entry_side": "west"})
        successor = advance(source, 2, {"gallery": "add"})
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(successor, old_operator)
        rebound = source.bound_to(RunRef(source.project_id, "other-trial",
                                        ProjectVersionRef(source.project_id, 1, "b" * 64)))
        self.assertEqual(rebound.digest, source.digest)
        self.assertNotEqual(rebound.state_digest, source.state_digest)
        with self.assertRaisesRegex(StateRecordError, "exact base is stale"):
            apply_state_record_operator(rebound, old_operator)

    def test_declared_chain_grows_only_when_decisions_are_made_and_recomputes(self):
        source = advance(record(), 1, {"entry_side": "east"})
        self.assertEqual(source.closure(("parameter:entry_side",)), ("parameter:entry_side",))
        source = advance(source, 2, {"gallery": "add"})
        self.assertEqual(set(source.closure(("parameter:entry_side",))),
                         {"parameter:entry_side", "parameter:gallery_x"})
        source = advance(source, 3, {"unit_access": "add"})
        edges = {(edge.upstream_ref, edge.downstream_ref) for edge in source.dependency_edges()}
        self.assertTrue({("parameter:entry_side", "parameter:gallery_x"),
                         ("parameter:gallery_x", "parameter:door_x"),
                         ("parameter:door_x", "parameter:threshold_x")}.issubset(edges))
        self.assertEqual(set(source.closure(("parameter:entry_side",))),
                         {"parameter:entry_side", "parameter:gallery_x", "parameter:door_x", "parameter:threshold_x"})
        successor = advance(source, 0, {"entry_side": "west"})
        changed = {parameter.key for parameter in source.parameters
                   if parameter.value != successor.parameter(parameter.key).value}
        self.assertEqual(changed, {"entry_side", "gallery_x", "door_x", "threshold_x"})
        self.assertEqual([successor.parameter(key).value for key in ("gallery_x", "door_x", "threshold_x")],
                         [1, 2, 2.5])
        self.assertEqual(source.entities, successor.entities)
        self.assertTrue(final_assessment(successor)["complete"])

    def test_early_repair_changes_only_existing_parameter_not_hypothetical_future_work(self):
        source = advance(record(), 1, {"entry_side": "east"})
        successor = advance(source, 0, {"entry_side": "west"})
        changed = {parameter.key for parameter in source.parameters
                   if parameter.value != successor.parameter(parameter.key).value}
        self.assertEqual(changed, {"entry_side"})
        self.assertEqual(set(successor.closure(("parameter:entry_side",))), {"parameter:entry_side"})

    def test_two_storey_ring_matches_hand_calculation_and_keeps_void_empty(self):
        source = complete()
        result = final_assessment(source)
        massing = result["massing"]
        self.assertEqual([massing[key] for key in ("footprint_m2", "gross_floor_area_m2", "floor_count", "height_m")],
                         [12 * 12 - 4 * 4, (12 * 12 - 4 * 4) * 2, 2, 6])
        self.assertEqual(massing["validity"], "valid")
        cells = set()
        for volume in source.entities_of("Volume@1"):
            low, high = volume.fields["min"], volume.fields["max"]
            cells.update((x, z) for x in range(low[0], high[0] + 1) for z in range(low[2], high[2] + 1))
        self.assertEqual(len(cells), 128)
        self.assertTrue(all((x, z) not in cells for x in range(4, 8) for z in range(4, 8)))

    def test_repeated_add_and_omit_do_not_destroy_existing_details(self):
        source = complete()
        for checkpoint, action in ((2, {"gallery": "add"}), (2, {"gallery": "omit"}),
                                   (3, {"unit_access": "add"}), (3, {"unit_access": "omit"})):
            with self.subTest(action=action):
                successor = advance(source, checkpoint, action)
                self.assertEqual(successor.digest, source.digest)
                self.assertTrue(final_assessment(successor)["complete"])

    def test_strict_action_fields_and_stage_prerequisites(self):
        source = record()
        for checkpoint, answer in ((1, {"entry_side": "north"}), (1, {"entry_side": "west", "unlock": True}),
                                   (1, {"entry_side": "west", "courtyard_width": True}),
                                   (1, {"entry_side": "west", "courtyard_width": float("nan")}),
                                   (1, {"entry_side": "west", "semantic_detail": "fabricated"}),
                                   (True, {"entry_side": "west"}), (1, {}), (0, {"entry_side": "west"}),
                                   (2, {"gallery": "add"}), (3, {"unit_access": "add"})):
            with self.subTest(checkpoint=checkpoint, answer=answer):
                with self.assertRaises(ValueError):
                    propose(source, checkpoint, answer)
        source = advance(source, 1, {"entry_side": "west"})
        with self.assertRaisesRegex(ValueError, "requires a gallery"):
            propose(source, 3, {"unit_access": "add"})

    def test_reproducible_final_judgment_is_separate_from_model_claims(self):
        source = complete("east")
        before = source.to_dict()
        first = final_assessment(source)
        self.assertEqual(first, final_assessment(StateRecord.from_dict(before)))
        self.assertEqual(source.to_dict(), before)
        self.assertFalse(first["complete"])
        self.assertIn("entry_alignment", first["unresolved"])
        json.dumps(first, allow_nan=False)
        with self.assertRaisesRegex(ValueError, "unsupported action fields"):
            propose(source, 0, {"entry_side": "east", "claim_complete": True})

    def test_final_check_catches_detached_or_stale_declared_dependencies(self):
        source = complete()
        for changed in (replace(source.parameter("threshold_x"), expr=None),
                        replace(source.parameter("threshold_x"), value=9)):
            tampered = replace(source, parameters=tuple(changed if item.key == changed.key else item
                                                       for item in source.parameters))
            self.assertIn("declared_dependencies", final_assessment(tampered)["unresolved"])

    def test_policy_exposes_the_same_finite_actions_evidence_and_unknown_rule(self):
        policy = policy_document()
        self.assertEqual(set(policy["checkpoint_actions"]), {"0", "1", "2", "3"})
        source = record()
        for checkpoint in (1, 2, 3, 0):
            source = advance(source, checkpoint, policy["examples"][str(checkpoint)])
        self.assertTrue(final_assessment(source)["complete"])
        self.assertIn("unknown", policy["checkpoint_actions"]["1"]["semantic_detail"])
        self.assertTrue(policy["evidence_refs"])
        self.assertTrue(policy["context_refs"])
        json.dumps(policy, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
