"""P077: relation coverage ledger contracts."""

import unittest

import archive.archflow.capabilities.relation_coverage as legacy_relation_discovery
import archive.archflow.evidence.relation_discovery as canonical_relation_discovery
from archive.archflow.evidence.relation_discovery import (
    RelationCoverageError,
    detect_program_edges,
    enumerate_candidate_relations,
    relation_coverage_ledger,
)
from archflow.contracts.canonical import canonical_digest


def obj(object_id, minimum, maximum, binding_id, physical=True):
    return {
        "object_id": object_id,
        "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
        "semantic_binding_ids": [binding_id],
        "physical": physical,
    }


BINDINGS = [
    {"binding_id": "hall-b", "component_id": "hall",
     "object_ids": ["hall-object"]},
    {"binding_id": "porch-b", "component_id": "porch",
     "object_ids": ["porch-object"]},
    {"binding_id": "tower-b", "component_id": "tower",
     "object_ids": ["tower-object"]},
    {"binding_id": "door-b", "component_id": "door",
     "object_ids": ["door-tool-object"]},
]

SCENE = [
    obj("hall-object", (0, 0, 0), (20, 10, 20), "hall-b"),
    obj("porch-object", (20, 0, 5), (26, 6, 15), "porch-b"),
    obj("tower-object", (40, 0, 0), (44, 20, 4), "tower-b"),
    obj("door-tool-object", (19, 0, 8), (21, 4, 12), "door-b",
        physical=False),
]


class EnumerationTest(unittest.TestCase):
    def test_touching_components_are_candidates(self):
        candidates = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=0.0
        )
        pairs = {c.components for c in candidates}
        self.assertIn(("hall", "porch"), pairs)
        self.assertNotIn(("hall", "tower"), pairs)
        self.assertNotIn(("porch", "tower"), pairs)

    def test_tolerance_extends_reach(self):
        candidates = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=20.0
        )
        pairs = {c.components for c in candidates}
        self.assertIn(("hall", "tower"), pairs)

    def test_contact_kind_and_gap(self):
        candidates = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=0.0
        )
        row = next(
            c for c in candidates if c.components == ("hall", "porch")
        )
        self.assertEqual("overlap", row.contact)
        self.assertEqual(0.0, row.min_gap)

    def test_non_physical_objects_do_not_enumerate(self):
        candidates = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=0.0
        )
        pairs = {c.components for c in candidates}
        self.assertFalse(any("door" in p for pair in pairs for p in pair))

    def test_determinism(self):
        first = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=5.0
        )
        second = enumerate_candidate_relations(
            list(reversed(SCENE)), BINDINGS, tolerance=5.0
        )
        self.assertEqual(
            [c.to_dict() for c in first], [c.to_dict() for c in second]
        )


class DetectionTest(unittest.TestCase):
    OPS = [
        {"op_id": "hall-wall", "semantic_binding_ids": ["hall-b"],
         "input_object_ids": ["door-tool-object"],
         "responds_to_binding_ids": []},
        {"op_id": "porch-row", "semantic_binding_ids": ["porch-b"],
         "input_object_ids": [],
         "responds_to_binding_ids": ["hall-b"]},
    ]

    def test_consumption_and_response_become_derived_edges(self):
        edges = detect_program_edges(self.OPS, BINDINGS)
        self.assertIn(("door", "hall"), edges)
        self.assertIn(("hall", "porch"), edges)
        via = edges[("door", "hall")]["detected_from"][0]["via"]
        self.assertTrue(via.startswith("consumes:"))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.candidates = enumerate_candidate_relations(
            SCENE, BINDINGS, tolerance=20.0
        )

    def test_legacy_facade_reexports_canonical_objects_by_identity(self):
        for name in legacy_relation_discovery.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(canonical_relation_discovery, name),
                    getattr(legacy_relation_discovery, name),
                )

    def test_reference_ledger_preserves_digest_and_authority_boundary(self):
        candidates = enumerate_candidate_relations(
            SCENE,
            BINDINGS,
            tolerance=0.0,
        )
        ledger = relation_coverage_ledger(
            candidates,
            detected={},
            declared=(),
        )

        self.assertEqual("RelationCoverageLedger@1", ledger["schema"])
        self.assertEqual(
            "b9a65b2c03e1abd37da2193ff5f6698a713c32141cd93afb6c5ffaa337bb9e3d",
            canonical_digest(ledger),
        )
        self.assertIs(ledger["authority"], False)
        self.assertNotIn("relationship_requirement_authority", ledger)
        self.assertNotIn("stage_closure_authority", ledger)
        self.assertEqual(
            "uncovered_relation",
            ledger["candidates"][0]["resolution"]["status"],
        )

    def test_uncovered_pairs_are_typed_not_silent(self):
        ledger = relation_coverage_ledger(
            self.candidates, detected={}, declared=()
        )
        self.assertEqual(0, ledger["resolved_count"])
        self.assertEqual(
            ledger["candidate_count"], len(ledger["uncovered"])
        )
        row = ledger["candidates"][0]
        self.assertEqual(
            "uncovered_relation", row["resolution"]["status"]
        )

    def test_detected_and_declared_resolutions_join(self):
        detected = detect_program_edges(DetectionTest.OPS, BINDINGS)
        declared = [
            {
                "components": ["tower", "hall"],
                "kind": "constraint",
                "refs": ["commitment:primary-axis-center"],
            }
        ]
        ledger = relation_coverage_ledger(
            self.candidates, detected=detected, declared=declared
        )
        by_pair = {
            tuple(row["components"]): row["resolution"]
            for row in ledger["candidates"]
        }
        self.assertEqual("program", by_pair[("hall", "porch")]["source"])
        self.assertEqual(
            "declaration", by_pair[("hall", "tower")]["source"]
        )
        self.assertLess(0, ledger["coverage_ratio"])

    def test_declaration_validation_fails_closed(self):
        with self.assertRaises(RelationCoverageError):
            relation_coverage_ledger(
                self.candidates,
                detected={},
                declared=[{"components": ["a", "b"], "kind": "vibes",
                           "refs": ["x"]}],
            )
        with self.assertRaises(RelationCoverageError):
            relation_coverage_ledger(
                self.candidates,
                detected={},
                declared=[{"components": ["a", "b"], "kind": "derived",
                           "refs": []}],
            )


if __name__ == "__main__":
    unittest.main()
