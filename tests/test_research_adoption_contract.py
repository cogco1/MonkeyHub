from __future__ import annotations

import copy
import hashlib
import unittest
from types import SimpleNamespace

from archflow.capabilities.precedent import (
    PrecedentAdoption as CompatibilityPrecedentAdoption,
)
from archflow.capabilities.precedent import (
    PrecedentFact as CompatibilityPrecedentFact,
)
from archflow.research.adoption import (
    PrecedentAdoption,
    PrecedentError,
    PrecedentFact,
)
from archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)


TEXT = "The roof bears on the entablature."


def _fact() -> PrecedentFact:
    quote = "roof bears on the entablature"
    start = TEXT.index(quote)
    return PrecedentFact(
        fact_id="roof-support",
        statement="The roof requires a continuous support path.",
        quote=quote,
        quote_start=start,
        quote_end=start + len(quote),
        snapshot_ref="project://demo/runs/r/records/snapshot-a",
        snapshot_text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
        annotator="harness:test",
        annotator_is_harness=True,
        topic=ConstructabilityTopic.SUPPORT,
        strength=PolicyConstraintStrength.HARD,
        decision_refs=("decision:roof-support",),
    )


def _adoption() -> PrecedentAdoption:
    return PrecedentAdoption(
        adoption_id="adoption-roof-support",
        authority_id="authority.user",
        adopted_at="2026-08-30T13:00:00Z",
        facts=(_fact(),),
    )


class ResearchAdoptionReadbackTests(unittest.TestCase):
    def test_exact_round_trip(self) -> None:
        fact = _fact()
        adoption = _adoption()

        self.assertEqual(fact, PrecedentFact.from_dict(fact.to_dict()))
        self.assertEqual(
            adoption,
            PrecedentAdoption.from_dict(adoption.to_dict()),
        )
        self.assertIs(CompatibilityPrecedentFact, PrecedentFact)
        self.assertIs(
            CompatibilityPrecedentAdoption,
            PrecedentAdoption,
        )

    def test_legacy_v1_fact_without_decision_refs_remains_readable(self) -> None:
        payload = _fact().to_dict()
        payload.pop("decision_refs")

        decoded = PrecedentFact.from_dict(payload)

        self.assertEqual(decoded.decision_refs, ())
        self.assertIn("decision_refs", decoded.to_dict())

    def test_fact_rejects_schema_and_type_coercion(self) -> None:
        payload = _fact().to_dict()
        payload["unexpected"] = "value"
        with self.assertRaisesRegex(PrecedentError, "schema drifted"):
            PrecedentFact.from_dict(payload)

        for field, replacement in (
            ("quote_start", True),
            ("quote_end", "31"),
            ("annotator_is_harness", "false"),
            ("decision_refs", "decision:roof-support"),
        ):
            with self.subTest(field=field):
                payload = _fact().to_dict()
                payload[field] = replacement
                with self.assertRaisesRegex(
                    PrecedentError,
                    "field types drifted",
                ):
                    PrecedentFact.from_dict(payload)

        with self.assertRaisesRegex(PrecedentError, "valid range"):
            PrecedentFact(
                fact_id="roof-support",
                statement="The roof requires a continuous support path.",
                quote="r",
                quote_start=True,
                quote_end=2,
                snapshot_ref="project://demo/runs/r/records/snapshot-a",
                snapshot_text_sha256="a" * 64,
                annotator="harness:test",
                annotator_is_harness=True,
                topic=ConstructabilityTopic.SUPPORT,
                strength=PolicyConstraintStrength.HARD,
            )

    def test_adoption_rejects_extra_fields_and_authority_tampering(self) -> None:
        payload = _adoption().to_dict()
        payload["unexpected"] = "value"
        with self.assertRaisesRegex(PrecedentError, "schema drifted"):
            PrecedentAdoption.from_dict(payload)

        for field in (
            "retrieved_text_authority",
            "design_authority",
            "canonical_write_authority",
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(_adoption().to_dict())
                payload[field] = True
                with self.assertRaisesRegex(
                    PrecedentError,
                    "authority flags changed",
                ):
                    PrecedentAdoption.from_dict(payload)

    def test_adoption_rejects_noncanonical_fact_objects(self) -> None:
        impostor = SimpleNamespace(fact_id="roof-support")
        with self.assertRaisesRegex(PrecedentError, "canonical"):
            PrecedentAdoption(
                adoption_id="adoption-roof-support",
                authority_id="authority.user",
                adopted_at="2026-08-30T13:00:00Z",
                facts=(impostor,),
            )


if __name__ == "__main__":
    unittest.main()
