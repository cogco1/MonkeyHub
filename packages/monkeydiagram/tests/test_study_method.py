"""The Study method on traces written in place: canonical evidence, derivations and a research snapshot (#519).

The Project Runtime keeps the retained ledger (exact source, revisions, cold
reads that replay a revision's own findings) and serves ``/api/studies``; its
suites cover that. Here the method's functions are read without a project.
"""

from __future__ import annotations

import unittest

from monkeydiagram.study import (
    CURRENT_DERIVATION_METHOD,
    RESEARCH_METHOD,
    Box,
    StudyEvidenceError,
    StudySource,
    box_of,
    canonical_evidence,
    check_research_links,
    composition_graph,
    confirmed_traces,
    derive,
    relation_rows,
    research_snapshot_of,
    rounded,
)

SOURCE = StudySource("drawing-run", "a" * 64, None, 0, 800.0, 600.0, "image/png",
                     "project://study-fixture/runs/drawing-run/records/source.json")


def _trace(evidence_id: str, kind: str, box: tuple[float, float, float, float], status: str = "confirmed") -> dict:
    x0, y0, x1, y1 = box
    return {"evidence_id": evidence_id, "kind": kind, "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            "status": status}


def _house() -> list[dict]:
    """An envelope around a dominant mass with a centred void, a small mass and a proposed trace."""

    return canonical_evidence(SOURCE, [
        _trace("envelope", "envelope", (0.1, 0.1, 0.9, 0.9)),
        _trace("hall", "mass", (0.2, 0.2, 0.7, 0.8)),
        _trace("court", "void", (0.4, 0.45, 0.5, 0.55)),
        _trace("porch", "mass", (0.75, 0.3, 0.85, 0.4)),
        _trace("guess", "mass", (0.12, 0.12, 0.18, 0.18), status="proposed"),
    ])


class EvidenceTests(unittest.TestCase):
    def test_rows_are_canonical_whatever_order_or_precision_they_arrive_in(self) -> None:
        rows = canonical_evidence(SOURCE, [
            {"evidence_id": "b", "kind": "mass", "points": [[0.1234567, 0.1], [0.3, 0.1], [0.3, 0.3]]},
            {"evidence_id": "a", "kind": "void", "origin": "machine",
             "geometry": {"points": [[0.5, 0.5], [0.6, 0.5], [0.6, 0.6]]}},
        ])
        self.assertEqual([row["evidence_id"] for row in rows], ["a", "b"])
        self.assertEqual(rows[1]["geometry"]["points"][0], [0.123457, 0.1])
        self.assertEqual((rows[0]["status"], rows[0]["confidence"], rows[1]["confidence"]), ("proposed", 0.5, 1.0))
        self.assertEqual(rows[0]["basis_refs"], [SOURCE.basis_ref])
        self.assertEqual(canonical_evidence(SOURCE, rows), rows)

    def test_a_trace_the_method_cannot_take_is_refused_by_name(self) -> None:
        good = _trace("hall", "mass", (0.2, 0.2, 0.7, 0.8))
        for rows, words in (
            ([good, good], "duplicated"),
            ([{**good, "kind": "garden"}], "unsupported kind"),
            ([{**good, "confidence": 1.5}], "confidence"),
            ([{**good, "points": [[0.1, 0.1], [0.2, 0.2]]}], "three polygon points"),
            ([{**good, "points": [[0.1, 0.1], [1.2, 0.1], [0.5, 0.5]]}], "normalized page coordinates"),
            ([{**good, "points": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]}], "no measurable area"),
            ([{**good, "evidence_id": "not an id"}], "stable identifiers"),
        ):
            with self.subTest(words=words), self.assertRaises(StudyEvidenceError) as raised:
                canonical_evidence(SOURCE, rows)
            self.assertEqual(raised.exception.code, "STUDY_EVIDENCE_INVALID")
            self.assertIn(words, str(raised.exception))


class DerivationTests(unittest.TestCase):
    def test_only_confirmed_traces_are_measured_related_and_explained(self) -> None:
        evidence = _house()
        measurements, relations, graph, hypotheses, counterfactuals = derive(evidence)
        confirmed = {row["evidence_id"] for row in confirmed_traces(evidence)}
        self.assertEqual(confirmed, {"envelope", "hall", "court", "porch"})
        self.assertEqual({row["evidence_id"] for row in measurements}, confirmed)
        self.assertEqual(relations, relation_rows(evidence))
        self.assertIn("contains:hall:court", {row["relation_id"] for row in relations})
        self.assertEqual(graph, composition_graph(evidence, relations))
        self.assertEqual({node["evidence_id"] for node in graph["nodes"]}, confirmed)
        rules = {row["rule"] for row in hypotheses}
        self.assertLessEqual({"dominant_mass", "void_nested_in_mass", "void_centrality"}, rules)
        for row in hypotheses:
            self.assertEqual(row["reasoning_receipt"]["graph_digest"], graph["graph_digest"])
        self.assertTrue(counterfactuals)
        self.assertEqual({row["method"] for row in counterfactuals}, {"composition-signature-jaccard@1"})

    def test_the_same_traces_derive_the_same_findings(self) -> None:
        self.assertEqual(derive(_house()), derive(_house()))
        self.assertEqual(CURRENT_DERIVATION_METHOD, "StudyDerivation@1")

    def test_a_relation_naming_an_unconfirmed_trace_forms_no_graph(self) -> None:
        evidence = _house()
        stray = {"relation_id": "contains:hall:guess", "kind": "contains", "subject_evidence_id": "hall",
                 "object_evidence_id": "guess", "method": "normalized-bounds-relation@1"}
        with self.assertRaises(ValueError):
            composition_graph(evidence, [stray])

    def test_bounds_are_read_off_the_points_and_retained_at_six_decimals(self) -> None:
        box = box_of([[0.1, 0.2], [0.4, 0.2], [0.4, 0.6]])
        self.assertEqual(box, Box(0.1, 0.2, 0.4, 0.6))
        self.assertEqual(box.to_dict()["area"], rounded(0.3 * 0.4))
        self.assertEqual(rounded(1 / 3), 0.333333)


class ResearchTests(unittest.TestCase):
    EMPTY = {"question": "", "historical_sources": [], "hypotheses": [], "gaps": [], "counterfactuals": [],
             "comparisons": [], "composition_pattern": None, "design_prior": None}

    def test_a_snapshot_names_its_method_source_and_what_it_still_lacks(self) -> None:
        snapshot = research_snapshot_of(SOURCE, _house(), self.EMPTY)
        self.assertEqual(snapshot["method"], RESEARCH_METHOD)
        self.assertEqual(snapshot["source_binding"], SOURCE.to_dict())
        self.assertEqual(snapshot["observations"]["method"], "aspect-correct-polygon@1")
        self.assertFalse(snapshot["completion"]["ready"])
        self.assertEqual(snapshot["completion"]["missing"], [
            "research-question", "two-competing-explanations", "evidence-gap",
            "three-to-five-computed-interventions", "composition-pattern", "conditional-design-prior",
            "changed-context-decision",
        ])

    def test_a_link_to_an_absent_trace_is_refused(self) -> None:
        research = {**self.EMPTY, "gaps": [{"gap_id": "gap-1", "description": "the roof", "evidence_ids": ["attic"]}]}
        with self.assertRaises(ValueError):
            check_research_links(research, _house())


if __name__ == "__main__":
    unittest.main()
