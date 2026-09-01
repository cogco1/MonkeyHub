"""Canonical branch basis index and explicit unscoped-v1 compatibility."""

import inspect
import unittest

from archflow.capabilities import basis_index as legacy_basis_index
from archflow.research import index as canonical_index
from archflow.research.compat import unscoped_v1
from archflow.research.compat.unscoped_v1 import (
    BasisIndex,
    BasisIndexError,
    build_basis_index,
    decision_slug,
)
from archflow.research.index import (
    BranchBasisIndex,
    BranchDecisionContext,
    BranchRAGProgress,
    compile_branch_decision_context,
    compile_branch_rag_progress,
)
from tests.test_branch_conditioned_rag import (
    _covered_branch_index,
    _index_ref,
    _p079_inputs,
    _selected_scope,
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


def _canonical_values():
    *_, scope = _selected_scope()
    index = _covered_branch_index(scope)
    universe, policy, closure, sufficiency, frontier = _p079_inputs(
        scope,
        ("declaration:column-order",),
    )
    context = compile_branch_decision_context(
        index,
        index_record=_index_ref(index),
        decision_refs=("declaration:column-order",),
        universe=universe,
        policy=policy,
        closure=closure,
        sufficiency=sufficiency,
        frontier=frontier,
    )
    progress = compile_branch_rag_progress(
        index,
        next_queries=(),
        universe=universe,
        policy=policy,
        closure=closure,
        sufficiency=sufficiency,
        frontier=frontier,
    )
    return index, context, progress


class BranchCanonicalIndexTests(unittest.TestCase):
    def test_capability_facade_preserves_canonical_and_compat_identity(self):
        canonical_names = {
            "BasisIndexError",
            "BranchBasisIndex",
            "BranchDecisionContext",
            "BranchRAGProgress",
            "BranchRAGProgressStatus",
            "build_branch_basis_index",
            "compile_branch_decision_context",
            "compile_branch_rag_progress",
            "compile_next_branch_queries",
            "require_branch_frontier_matches",
        }
        compat_names = {"BasisIndex", "build_basis_index", "decision_slug"}
        self.assertEqual(
            canonical_names | compat_names,
            set(legacy_basis_index.__all__),
        )
        for name in canonical_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_basis_index, name),
                    getattr(canonical_index, name),
                )
        for name in compat_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_basis_index, name),
                    getattr(unscoped_v1, name),
                )

    def test_canonical_source_contains_no_historical_project_schema(self):
        source = inspect.getsource(canonical_index)
        for schema in (
            "PrecedentQuery@1",
            "PrecedentAdoption@1",
            "P070DecisionCalibration@1",
            "WebEvidenceSnapshot@1",
        ):
            with self.subTest(schema=schema):
                self.assertNotIn(schema, source)

    def test_branch_index_schema_digest_and_roundtrip_remain_fixed(self):
        index, _, _ = _canonical_values()
        self.assertIsInstance(index, BranchBasisIndex)
        self.assertEqual("BranchBasisIndex@1", index.SCHEMA)
        self.assertEqual(
            "53fb0fb6055994709218c6109583a1f9853dbd82da7aeab2e43b9d169631943a",
            index.index_digest,
        )
        self.assertEqual(index, BranchBasisIndex.from_dict(index.to_dict()))
        self.assertFalse(index.to_dict()["evidence_authority"])
        self.assertFalse(index.to_dict()["canonical_write_authority"])

    def test_branch_context_schema_and_digest_remain_fixed(self):
        _, context, _ = _canonical_values()
        payload = context.to_dict()
        self.assertIsInstance(context, BranchDecisionContext)
        self.assertEqual("BranchDecisionContext@2", context.SCHEMA)
        self.assertEqual(
            "ec3bef604991e4865efa7fa2a45970b48cb1e27ea84307d3cd673f65decbf9a2",
            payload["context_digest"],
        )
        self.assertEqual(context, BranchDecisionContext.from_dict(payload))
        self.assertFalse(payload["selection_authority"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_branch_progress_schema_and_digest_remain_fixed(self):
        _, _, progress = _canonical_values()
        payload = progress.to_dict()
        self.assertIsInstance(progress, BranchRAGProgress)
        self.assertEqual("BranchRAGProgress@2", progress.SCHEMA)
        self.assertEqual(
            "3c06588dea9e92b2b6df3fa84b8ed9bbee3a788f30a0663dd956950b0228bee0",
            progress.progress_digest,
        )
        self.assertFalse(payload["selection_authority"])
        self.assertFalse(payload["canonical_write_authority"])


class UnscopedV1CompatibilityTests(unittest.TestCase):
    def test_output_is_explicitly_read_only_compat(self):
        index = build_basis_index(records())
        self.assertIsInstance(index, BasisIndex)
        self.assertTrue(unscoped_v1.READ_ONLY_COMPAT)
        self.assertTrue(index.READ_ONLY_COMPAT)
        self.assertFalse(unscoped_v1.NEW_WRITE_AUTHORITY)
        self.assertFalse(index.NEW_WRITE_AUTHORITY)
        self.assertFalse(unscoped_v1.CLOSURE_AUTHORITY)
        self.assertFalse(index.CLOSURE_AUTHORITY)

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
        with self.assertRaises(BasisIndexError):
            decision_slug("::")


if __name__ == "__main__":
    unittest.main()
