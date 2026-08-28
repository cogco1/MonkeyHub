"""P070: decision-scoped research loop contracts."""

import unittest

from archflow.capabilities.research import (
    PrecedentQuery,
    ResearchError,
    decode_research_json,
    extract_windows,
    parse_research_output,
    research_prompt,
)

SNAPSHOT = (
    "The portico is fronted by eight columns. "
    "Their height to width ratio is about 10:1. "
    "The ratio of total column height to column-shaft height is 6:5. "
    "A legend attributes the capital to Callimachus."
)
SNAPSHOT_SHA = "ab" * 32


def make_query(**overrides):
    values = {
        "query_id": "order-proportions",
        "question": "What proportions govern the columns?",
        "decision_refs": (
            "declaration:column-diameter-m",
            "declaration:column-shaft-m",
        ),
        "search_terms": ("ratio", "columns"),
        "jurisdiction": None,
        "domain_allowlist": (),
    }
    values.update(overrides)
    return PrecedentQuery(**values)


def make_candidate(**overrides):
    quote = "Their height to width ratio is about 10:1."
    start = SNAPSHOT.index(quote)
    values = {
        "fact_id": "slenderness-ratio",
        "statement": "Column slenderness is about 10:1.",
        "quote": quote,
        "quote_start": start,
        "quote_end": start + len(quote),
        "decision_refs": ["declaration:column-diameter-m"],
        "topic": "support",
        "strength": "soft",
    }
    values.update(overrides)
    return values


def parse(candidates, query=None):
    return parse_research_output(
        {
            "schema": "PrecedentResearchOutput@1",
            "query_id": "order-proportions",
            "candidates": candidates,
        },
        query=query or make_query(),
        snapshot_ref="project://p/runs/r/records/snapshot-1",
        snapshot_text=SNAPSHOT,
        snapshot_text_sha256=SNAPSHOT_SHA,
        annotator="model:test",
    )


class PrecedentQueryTest(unittest.TestCase):
    def test_query_requires_decision_refs(self):
        with self.assertRaises(ResearchError):
            make_query(decision_refs=())

    def test_query_requires_sorted_unique_decision_refs(self):
        with self.assertRaises(ResearchError):
            make_query(
                decision_refs=(
                    "declaration:column-shaft-m",
                    "declaration:column-diameter-m",
                )
            )

    def test_prompt_names_enums_and_denies_authority(self):
        query = make_query()
        windows = extract_windows(SNAPSHOT, query.search_terms)
        prompt = research_prompt(
            query,
            snapshot_ref="project://p/runs/r/records/snapshot-1",
            snapshot_text_sha256=SNAPSHOT_SHA,
            windows=windows,
        )
        contract = prompt["output_contract"]
        self.assertIn("support", contract["topic_values"])
        self.assertEqual(["hard", "soft"], contract["strength_values"])
        self.assertFalse(prompt["authority"]["adoption_authority"])
        self.assertFalse(prompt["authority"]["design_authority"])


class ExtractWindowsTest(unittest.TestCase):
    def test_windows_merge_and_carry_offsets(self):
        windows = extract_windows(
            SNAPSHOT, ("ratio",), window_chars=30
        )
        for window in windows:
            begin, end = window["window_start"], window["window_end"]
            self.assertEqual(SNAPSHOT[begin:end], window["text"])

    def test_no_match_is_typed_failure(self):
        with self.assertRaises(ResearchError):
            extract_windows(SNAPSHOT, ("zeppelin",))


class ParseResearchOutputTest(unittest.TestCase):
    def test_valid_candidate_becomes_fact(self):
        facts, rejections = parse([make_candidate()])
        self.assertEqual(1, len(facts))
        self.assertEqual((), rejections)
        self.assertEqual(
            ("declaration:column-diameter-m",), facts[0].decision_refs
        )

    def test_wrong_offsets_are_recomputed_by_harness(self):
        facts, rejections = parse(
            [make_candidate(quote_start=3, quote_end=9)]
        )
        self.assertEqual((), rejections)
        fact = facts[0]
        self.assertEqual(
            SNAPSHOT[fact.quote_start : fact.quote_end], fact.quote
        )

    def test_paraphrase_is_rejected(self):
        good = make_candidate()
        bad = make_candidate(
            fact_id="paraphrase",
            quote="Columns are roughly ten diameters tall.",
        )
        facts, rejections = parse([good, bad])
        self.assertEqual(1, len(facts))
        self.assertEqual(
            "quote_not_in_snapshot", rejections[0]["code"]
        )

    def test_decision_refs_outside_query_are_rejected(self):
        good = make_candidate()
        bad = make_candidate(
            fact_id="off-scope",
            decision_refs=["declaration:roof-pitch"],
        )
        facts, rejections = parse([good, bad])
        self.assertEqual(1, len(facts))
        self.assertEqual(
            "decision_refs_outside_query", rejections[0]["code"]
        )

    def test_empty_decision_refs_are_rejected(self):
        with self.assertRaises(ResearchError):
            parse([make_candidate(decision_refs=[])])

    def test_all_rejected_fails_closed(self):
        with self.assertRaises(ResearchError) as ctx:
            parse(
                [
                    make_candidate(
                        quote="Columns are roughly ten diameters tall."
                    )
                ]
            )
        self.assertIn("no candidate survived", str(ctx.exception))

    def test_schema_drift_fails_closed(self):
        with self.assertRaises(ResearchError):
            parse_research_output(
                {"schema": "SomethingElse@1", "candidates": []},
                query=make_query(),
                snapshot_ref="project://p/runs/r/records/snapshot-1",
                snapshot_text=SNAPSHOT,
                snapshot_text_sha256=SNAPSHOT_SHA,
                annotator="model:test",
            )

    def test_decode_tolerates_code_fences(self):
        decoded = decode_research_json(
            "```json\n{\"schema\": \"PrecedentResearchOutput@1\"}\n```"
        )
        self.assertEqual(
            "PrecedentResearchOutput@1", decoded["schema"]
        )


if __name__ == "__main__":
    unittest.main()
