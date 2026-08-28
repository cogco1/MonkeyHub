"""P076: decision-keyed basis index contracts."""

import unittest

from archflow.capabilities.basis_index import (
    build_basis_index,
    decision_slug,
)

SNAP_SHA = "aa" * 32


def records():
    query = (
        "precedent-query-q1-abc.json",
        {
            "schema": "PrecedentQuery@1",
            "query_id": "q1",
            "question": "governing rules?",
            "decision_refs": [
                "declaration:bay-spacing",
                "declaration:axis",
                "declaration:material",
            ],
        },
    )
    snapshot = (
        "web-evidence-snapshot-def.json",
        {
            "schema": "WebEvidenceSnapshot@1",
            "url": "https://example.org/source",
            "retrieved_at": "2026-08-29",
            "text_sha256": SNAP_SHA,
        },
    )
    adoption = (
        "precedent-adoption-q1-ghi.json",
        {
            "schema": "PrecedentAdoption@1",
            "adoption_id": "q1-adoption",
            "authority_id": "authority.user",
            "facts": [
                {
                    "fact_id": "symmetry-rule",
                    "statement": "fronts are symmetric",
                    "quote": "symmetry",
                    "quote_start": 10,
                    "quote_end": 18,
                    "strength": "hard",
                    "topic": "support",
                    "snapshot_ref": "project://p/runs/r/records/snap",
                    "snapshot_text_sha256": SNAP_SHA,
                    "decision_refs": [
                        "declaration:bay-spacing",
                        "declaration:axis",
                    ],
                }
            ],
        },
    )
    calibration = (
        "decision-calibration-q1-jkl.json",
        {
            "schema": "P070DecisionCalibration@1",
            "query_id": "q1",
            "adoption_ref": "project://p/runs/r/records/adoption",
            "calibrations": [
                {"decision_ref": "declaration:bay-spacing",
                 "facts": ["symmetry-rule"]},
                {"decision_ref": "declaration:axis",
                 "facts": ["symmetry-rule"]},
                {"decision_ref": "declaration:material", "facts": []},
            ],
        },
    )
    unrelated = (
        "sandbox-scene-xyz.json",
        {"schema": "HybridSandboxScene@1", "objects": []},
    )
    return [query, snapshot, adoption, calibration, unrelated]


class BasisIndexTest(unittest.TestCase):
    def test_shards_group_facts_by_decision(self):
        index = build_basis_index(records())
        shard = index.decisions["declaration:bay-spacing"]
        self.assertEqual("covered", shard["status"])
        self.assertEqual(1, len(shard["facts"]))
        fact = shard["facts"][0]
        self.assertEqual("symmetry-rule", fact["fact_id"])
        self.assertEqual("q1-adoption", fact["adoption_id"])
        self.assertEqual(SNAP_SHA, fact["snapshot_text_sha256"])

    def test_uncovered_decision_is_typed_not_silent(self):
        index = build_basis_index(records())
        self.assertEqual(("declaration:material",), index.uncovered)
        shard = index.decisions["declaration:material"]
        self.assertEqual("uncovered", shard["status"])
        self.assertEqual([], shard["facts"])
        self.assertEqual(
            ["q1"], [item["query_id"] for item in shard["queries"]]
        )

    def test_reverse_source_shard_names_reopen_set(self):
        index = build_basis_index(records())
        source = index.sources[SNAP_SHA]
        self.assertEqual(["symmetry-rule"], source["facts"])
        self.assertEqual(
            ["declaration:axis", "declaration:bay-spacing"],
            source["decision_refs"],
        )
        self.assertEqual("https://example.org/source", source["url"])

    def test_derivation_is_deterministic_and_names_its_records(self):
        first = build_basis_index(records())
        second = build_basis_index(reversed(records()))
        self.assertEqual(first.decisions, second.decisions)
        self.assertEqual(first.sources, second.sources)
        self.assertIn("precedent-adoption-q1-ghi.json", first.derived_from)
        self.assertNotIn("sandbox-scene-xyz.json", first.derived_from)

    def test_summary_counts(self):
        summary = build_basis_index(records()).summary()
        self.assertEqual(3, summary["decision_count"])
        self.assertEqual(
            ["declaration:axis", "declaration:bay-spacing"],
            summary["covered"],
        )
        self.assertEqual(["declaration:material"], summary["uncovered"])

    def test_decision_slug_is_filesystem_safe(self):
        self.assertEqual(
            "declaration-bay-spacing",
            decision_slug("declaration:bay-spacing"),
        )
        with self.assertRaises(Exception):
            decision_slug("::")


if __name__ == "__main__":
    unittest.main()
