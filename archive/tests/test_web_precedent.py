"""P067 contract tests: snapshots, quoted facts, adoption, constraints."""

import hashlib
import unittest

from archive.archflow.adapters.web_evidence import extract_text
from archive.archflow.research.adoption import (
    PrecedentAdoption,
    PrecedentError,
    PrecedentFact,
    compile_precedent_constraints,
)
from archive.archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)

TEXT = "The portico is crowned by a triangular pediment above the columns."


def _fact(
    *,
    quote: str = "crowned by a triangular pediment",
    start: int | None = None,
) -> PrecedentFact:
    begin = TEXT.find(quote) if start is None else start
    return PrecedentFact(
        fact_id="portico-pediment",
        statement="The portico must carry a triangular pediment.",
        quote=quote,
        quote_start=begin,
        quote_end=begin + len(quote),
        snapshot_ref="project://demo/runs/r/records/web-evidence-snapshot-x",
        snapshot_text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
        annotator="harness:test",
        annotator_is_harness=True,
        topic=ConstructabilityTopic.SUPPORT,
        strength=PolicyConstraintStrength.HARD,
    )


class WebPrecedentContractTests(unittest.TestCase):
    def test_extract_text_strips_active_content(self):
        text = extract_text(
            "<p>keep this</p><script>alert('drop this')</script>"
        )
        self.assertIn("keep this", text)
        self.assertNotIn("drop this", text)

    def test_quote_must_sit_exactly_at_its_claimed_span(self):
        fact = _fact()
        fact.require_quote_in(TEXT)
        drifted = _fact(start=0)
        with self.assertRaises(PrecedentError):
            drifted.require_quote_in(TEXT)

    def test_constraints_chain_provenance_and_leak_no_page_text(self):
        adoption = PrecedentAdoption(
            adoption_id="adoption-1",
            authority_id="authority.user",
            adopted_at="2026-08-28T21:05:00+08:00",
            facts=(_fact(),),
        )
        self.assertEqual("PrecedentAdoption@1", adoption.to_dict()["schema"])
        self.assertEqual(
            "e0a54ff963e5b817766aea8f60d8bb8dc66e68752fae4fc1b4fc1934b127c756",
            adoption.adoption_digest,
        )
        self.assertFalse(adoption.to_dict()["retrieved_text_authority"])
        self.assertFalse(adoption.to_dict()["design_authority"])
        self.assertFalse(adoption.to_dict()["canonical_write_authority"])
        constraints = compile_precedent_constraints(
            adoption,
            adoption_ref="project://demo/runs/r/records/precedent-adoption-x",
            compiler_id="archflow.resource-compiler",
            base_state_sha256="b" * 64,
        )
        self.assertEqual(1, len(constraints))
        constraint = constraints[0]
        self.assertEqual(
            "precedent-portico-pediment", constraint.constraint_id
        )
        self.assertEqual(
            adoption.facts[0].statement, constraint.statement
        )
        self.assertEqual(
            (adoption.facts[0].snapshot_ref,), constraint.subject_refs
        )
        self.assertEqual(
            ("project://demo/runs/r/records/precedent-adoption-x",),
            constraint.provenance.source_refs,
        )
        self.assertNotIn(TEXT, constraint.statement)


if __name__ == "__main__":
    unittest.main()
