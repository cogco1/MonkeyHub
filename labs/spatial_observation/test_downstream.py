import unittest

from labs.spatial_observation.downstream import score_refs


class SupportedReferenceTests(unittest.TestCase):
    def test_correct_guess_outside_context_is_not_supported(self):
        row = {"gold": {"relevant_entities": ["a", "b"], "relevant_edges": ["a-b"]},
               "result": {"entity_refs": ["a"], "edge_refs": []}}
        result = score_refs({"entity_refs": ["a", "b"], "edge_refs": ["a-b"]}, row)
        self.assertTrue(result["complete_entity_answer"])
        self.assertFalse(result["supported_complete_entity_answer"])
        self.assertFalse(result["supported_complete_edge_answer"])

    def test_no_gold_edges_are_not_a_successful_recall_sample(self):
        row = {"gold": {"relevant_entities": ["a"]}, "result": {"entity_refs": ["a"], "edge_refs": []}}
        self.assertIsNone(score_refs(None, row)["edge_recall"])
        self.assertFalse(score_refs(None, row)["valid"])


if __name__ == "__main__":
    unittest.main()
