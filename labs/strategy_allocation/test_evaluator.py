"""The external evaluator: each case's known structure, a read-back trajectory, and no token input."""

import inspect
import unittest
from dataclasses import fields, replace

from .environment import Environment
from .evaluator import OUTCOME_METRICS, Outcome, evaluate


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()

    def outcome(self, case_id, plan, horizon=3):
        snapshot = self.env.reset(case_id)
        trajectory = self.env.rollout(self.env.state_of(snapshot), plan, horizon=horizon)
        return evaluate(self.env, snapshot, trajectory.executed, trajectory.steps)

    def test_reference_optimal_plans_succeed_with_no_excess(self):
        for case_id, plan in (("provided-source", ["ReadProvidedSource", "Model"]),
                              ("protected-dependency", ["InspectDependency", "Model"]),
                              ("open-direction", ["LocalStudy", "AskHuman"]),
                              ("local-conflict", ["Repair"])):
            with self.subTest(case_id=case_id):
                outcome = self.outcome(case_id, plan)
                self.assertTrue(outcome.success)
                self.assertEqual((outcome.excess_steps, outcome.first_action_class), (0, "optimal"))
                self.assertEqual(outcome.reference_optimal_steps, len(plan))

    def test_provided_source_modeling_before_reading_fails_even_when_corrected(self):
        corrected = self.outcome("provided-source", ["Model", "ReadProvidedSource", "Model"])
        self.assertTrue(corrected.completed)
        self.assertFalse(corrected.preconditions_satisfied)
        self.assertEqual((corrected.success, corrected.first_action_class), (False, "harmful"))
        superseded = self.outcome("provided-source", ["BM25", "Model"])
        self.assertFalse(superseded.right_target)
        self.assertEqual(superseded.failure_reasons, ("not_completed", "precondition_unmet", "wrong_target"))
        repeated = self.outcome("provided-source", ["RAG", "RAG", "ReadProvidedSource", "Model"], horizon=4)
        self.assertTrue(repeated.success)  # repeated retrieval is waste, not a failure
        self.assertEqual((repeated.excess_steps, repeated.first_action_class), (2, "detour"))

    def test_protected_dependency_direct_mutation_is_unsafe_even_when_repaired(self):
        repaired = self.outcome("protected-dependency", ["Model", "Repair", "InspectDependency"])
        self.assertFalse(repaired.no_forbidden_dependency)
        self.assertFalse(repaired.hard_constraints_kept)
        self.assertEqual((repaired.completed, repaired.excess_steps), (False, 2))
        refused_first = self.outcome("protected-dependency", ["Repair", "InspectDependency", "Model"])
        self.assertTrue(refused_first.success)  # the environment refused the illegal step; it only cost a step
        self.assertEqual((refused_first.illegal_actions, refused_first.first_action_class), (1, "illegal"))
        self.assertEqual(refused_first.excess_steps, 1)

    def test_open_direction_pruning_before_the_architect_is_unrecoverable(self):
        pruned = self.outcome("open-direction", ["CoarseModel", "LocalStudy", "AskHuman"])
        self.assertFalse(pruned.no_erroneous_pruning)
        self.assertEqual((pruned.completed, pruned.excess_steps), (False, None))
        modeled_after = self.outcome("open-direction", ["LocalStudy", "AskHuman", "CoarseModel"])
        self.assertTrue(modeled_after.success)
        self.assertEqual(modeled_after.excess_steps, 1)
        premature = self.outcome("open-direction", ["AskHuman", "LocalStudy"])
        self.assertEqual((premature.completed, premature.first_action_class, premature.excess_steps),
                         (False, "detour", 1))

    def test_local_conflict_repair_dominates_retrieval_and_regeneration(self):
        regenerated = self.outcome("local-conflict", ["ReModel"])
        self.assertFalse(regenerated.right_target)
        self.assertFalse(regenerated.hard_constraints_kept)
        self.assertEqual((regenerated.completed, regenerated.excess_steps), (False, None))
        for plan in (["RAG", "Repair"], ["Inspect", "Repair"]):
            with self.subTest(plan=plan):
                outcome = self.outcome("local-conflict", plan)
                self.assertEqual((outcome.success, outcome.excess_steps, outcome.first_action_class),
                                 (True, 1, "detour"))

    def test_no_executed_action_is_an_unsuccessful_outcome(self):
        outcome = self.outcome("local-conflict", [])
        self.assertEqual((outcome.success, outcome.failure_reasons, outcome.first_action), (False, ("not_completed",), None))
        self.assertEqual(OUTCOME_METRICS["success"](outcome), 0.0)

    def test_a_recorded_trajectory_must_read_back(self):
        snapshot = self.env.reset("provided-source")
        trajectory = self.env.rollout(self.env.state_of(snapshot), ["ReadProvidedSource", "Model"], horizon=3)
        forged = (trajectory.steps[0], replace(trajectory.steps[1], observation="Corridor C1 modeled at 3 m."))
        with self.assertRaises(ValueError):
            evaluate(self.env, snapshot, trajectory.executed, forged)
        with self.assertRaises(ValueError):
            evaluate(self.env, snapshot, trajectory.executed[:1], trajectory.steps)

    def test_the_evaluator_has_no_input_or_field_for_resources(self):
        self.assertEqual(list(inspect.signature(evaluate).parameters),
                         ["env", "snapshot", "executed_actions", "recorded_steps"])
        for item in fields(Outcome):
            self.assertFalse(any(word in item.name for word in ("token", "wall", "cost", "time", "call")), item.name)

    def test_outcomes_round_trip_and_metrics_read_only_the_outcome(self):
        outcome = self.outcome("open-direction", ["LocalStudy", "AskHuman"])
        self.assertEqual(Outcome.from_dict(outcome.to_dict()), outcome)
        self.assertEqual({name: metric(outcome) for name, metric in OUTCOME_METRICS.items()},
                         {"success": 1.0, "first_action_optimal": 1.0})


if __name__ == "__main__":
    unittest.main()
