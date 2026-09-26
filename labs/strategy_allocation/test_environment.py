"""Public dynamics: the four action sets, legality, determinism and what a preview shows."""

import unittest
from dataclasses import fields

from .environment import MAX_PREVIEW_DEPTH, Environment
from .state_snapshot import StateSnapshot


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()

    def test_four_cases_carry_the_issue_action_sets(self):
        self.assertEqual({case_id: self.env.case(case_id).action_ids for case_id in self.env.case_ids}, {
            "provided-source": ("ReadProvidedSource", "BM25", "RAG", "Model", "AskHuman"),
            "protected-dependency": ("InspectDependency", "Model", "Repair", "RAG"),
            "open-direction": ("LocalStudy", "CoarseModel", "AskHuman", "Inspect"),
            "local-conflict": ("Repair", "ReModel", "RAG", "Inspect"),
        })

    def test_an_illegal_action_is_refused_and_keeps_the_state(self):
        state = self.env.initial_state("protected-dependency")
        step = self.env.step(state, "Repair")
        self.assertFalse(step.legal)
        self.assertEqual(step.state_after, state)
        self.assertEqual(step.refusal, "there is no reported violation to repair")
        self.assertNotIn("Repair", self.env.legal_actions(state))
        violated = self.env.step(state, "Model").state_after  # the public check reports the break
        self.assertIn("Repair", self.env.legal_actions(violated))
        resolved = self.env.step(self.env.initial_state("local-conflict"), "Repair").state_after
        self.assertEqual(self.env.refusal(resolved, "Repair"), "there is no open clash to repair")

    def test_a_rollout_attempts_at_most_the_horizon_and_refusals_use_a_step(self):
        trajectory = self.env.rollout(self.env.initial_state("local-conflict"), ["Repair", "Repair", "Inspect", "RAG"],
                                      horizon=3)
        self.assertEqual(trajectory.executed, ("Repair", "Repair", "Inspect"))
        self.assertTrue(trajectory.truncated)
        self.assertEqual([step.legal for step in trajectory.steps], [True, False, True])
        self.assertEqual(trajectory.final["clash"], "resolved")
        with self.assertRaises(ValueError):
            self.env.rollout(trajectory.initial, ["Repair"], horizon=0)

    def test_dynamics_are_deterministic_and_never_mutate_a_state(self):
        for case_id in self.env.case_ids:
            state = self.env.initial_state(case_id)
            actions = self.env.case(case_id).action_ids
            plan = actions + actions[::-1]
            first = self.env.rollout(state, plan, horizon=len(plan))
            self.assertEqual(first, Environment().rollout(state, plan, horizon=len(plan)))
            self.assertEqual(state, self.env.initial_state(case_id))

    def test_an_action_outside_the_case_set_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.env.step(self.env.initial_state("open-direction"), "Repair")

    def test_state_variables_keep_their_names_and_types(self):
        state = self.env.initial_state("open-direction")
        with self.assertRaises(ValueError):
            state.replace({"studied": "yes"})
        with self.assertRaises(ValueError):
            state.replace({"unknown": True})

    def test_a_preview_reports_public_consequences_and_executes_nothing(self):
        state = self.env.initial_state("protected-dependency")
        previews = {entry["action"]: entry for entry in self.env.preview(state)}
        self.assertEqual(previews["Repair"], {"action": "Repair", "legal": False,
                                              "refusal": "there is no reported violation to repair"})
        self.assertIn("R-7 check: violated", previews["Model"]["observation"])
        self.assertEqual(state, self.env.initial_state("protected-dependency"))
        deep = self.env.preview(state, depth=2)
        self.assertTrue(all("then" in entry for entry in deep if entry["legal"]))
        with self.assertRaises(ValueError):
            self.env.preview(state, depth=MAX_PREVIEW_DEPTH + 1)

    def test_a_snapshot_names_its_state_and_a_forged_one_is_refused(self):
        snapshot = self.env.reset("provided-source")
        self.assertEqual(self.env.state_of(snapshot), self.env.initial_state("provided-source"))
        later = self.env.step(self.env.state_of(snapshot), "ReadProvidedSource").state_after
        self.assertEqual(self.env.state_of(self.env.snapshot(later)), later)
        values = {item.name: getattr(snapshot, item.name) for item in fields(snapshot) if item.name != "digest"}
        forged = StateSnapshot.create(**{**values, "state": later.values})  # valid digest, stale facts
        with self.assertRaises(ValueError):
            self.env.state_of(forged)


if __name__ == "__main__":
    unittest.main()
