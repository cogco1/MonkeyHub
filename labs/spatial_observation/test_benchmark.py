import unittest

from labs.spatial_observation.benchmark import gold, score, task_set


class ScoringTests(unittest.TestCase):
    def test_abstention_missing_and_duplicate_are_not_success(self):
        truth = {"twin_gap": 3.0}
        self.assertFalse(score({"answers": [{"id": "twin_gap", "status": "unknown", "value": 3}]}, truth)[0]["correct"])
        self.assertFalse(score(None, truth)[0]["correct"])
        row = {"id": "twin_gap", "status": "known", "value": 3}
        self.assertFalse(score({"answers": [row, row]}, truth)[0]["correct"])
        self.assertFalse(score({"answers": [{**row, "value": True}]}, truth)[0]["correct"])
        self.assertFalse(score({"answers": [{**row, "id": []}]}, truth)[0]["correct"])
        self.assertFalse(score({"answers": [{"id": "declared_impact", "status": "known", "value": [{}]}]},
                               {"declared_impact": ["screen"]})[0]["correct"])

    def test_unknown_role_and_stale_are_distinct_from_numeric_abstention(self):
        rows = [{"id": "unknown_role", "status": "unknown", "value": None},
                {"id": "stale", "status": "stale", "value": None}]
        self.assertTrue(all(row["correct"] for row in score({"answers": rows}, {"unknown_role": None, "stale": None})))

    def test_fixed_tasks_keep_gold_out_and_distance_volume_scale_differently(self):
        self.assertEqual({task["id"] for task in task_set(1)}, set(gold("base", 1)))
        self.assertEqual(gold("base", 2)["twin_gap"], 6)
        self.assertEqual(gold("base", 2)["clash_overlap"], 2)
        self.assertEqual(gold("heldout", 1)["shell_gap"], .25)


if __name__ == "__main__":
    unittest.main()
