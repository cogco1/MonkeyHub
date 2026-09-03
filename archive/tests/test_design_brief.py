from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

import archive.archflow.compilers.brief as canonical_brief
import archive.archflow.runtime.brief_compiler as legacy_brief
from archive.archflow.compilers.brief import (
    BriefIntentObservation,
    BriefObservation,
    compile_design_brief,
)
from archive.archflow.compilers.commitments import (
    IntentObservation,
    IntentOperator,
    IntentTerm,
)
from archflow.project.refs import ProjectVersionRef
from archive.archflow.state.design_brief import BriefClaimKind, BriefSlot, BriefSlotStatus, DesignBrief
from archflow.state.commitments import CommitmentStatus
from archflow.state.operational_state import FactEpistemicStatus
from archflow.state.geometry_program import digest_value


def _base(project_id: str = "case-a") -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256="a" * 64,
    )


def _request_ref(project_id: str = "case-a") -> str:
    return f"project://{project_id}/input/raw-request.json"


def _use_observation(
    project_id: str = "case-a",
    *,
    value: str = "public learning use",
) -> BriefObservation:
    return BriefObservation(
        observation_id="requested-use",
        slot=BriefSlot.USE,
        kind=BriefClaimKind.USER_FACT,
        key="requested-use",
        value=value,
        epistemic_status=FactEpistemicStatus.DECLARED,
        authority_id="authority.user",
        source_refs=(_request_ref(project_id),),
        resolves_slot=True,
    )


def _size_intent(project_id: str = "case-a") -> BriefIntentObservation:
    request_ref = _request_ref(project_id)
    return BriefIntentObservation(
        slot=BriefSlot.SIZE,
        observation=IntentObservation(
            observation_id="explicit-gross-area",
            raw_text="The user supplied an explicit gross area.",
            source_event_ref=f"project://{project_id}/events/request.json",
            authority_id="authority.user",
            scope_ref=f"project://{project_id}",
            interpretations=(
                IntentTerm(
                    parameter_key="building.gross_area",
                    operator=IntentOperator.EXACT,
                    value=2400,
                    unit="square_metres",
                ),
            ),
            evidence_refs=(request_ref,),
        ),
    )


class DesignBriefTests(unittest.TestCase):
    def test_legacy_facade_reexports_canonical_objects_by_identity(self) -> None:
        for name in legacy_brief.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(canonical_brief, name),
                    getattr(legacy_brief, name),
                )

    def test_reference_brief_preserves_schema_digests_and_behavior(self) -> None:
        result = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=_request_ref(),
            observations=(_use_observation(),),
            intent_observations=(_size_intent(),),
        )

        self.assertEqual("DesignBrief@1", result.brief.SCHEMA)
        self.assertEqual(
            "58ae6543da7d3e9fc319f87e5fc8e20e27988cb5cc4ebbf962e8a9fd42cd6c02",
            result.brief.brief_digest,
        )
        self.assertEqual("BriefCompilationReceipt@1", result.receipt.SCHEMA)
        self.assertEqual(
            "brief-8eeb35647765aeaeb589",
            result.receipt.compilation_id,
        )
        self.assertEqual(
            "c4de78fa3e8397aae050f8057ad448a3ac0d792f590e50d5fc05fa40cdb12a18",
            digest_value(result.receipt.to_dict()),
        )
        self.assertIs(result.receipt.to_dict()["generation_authority"], False)
        self.assertEqual(1, len(result.brief.constraint_proposals))
        self.assertEqual(1, len(result.brief.proposed_commitments))

    def test_minimal_use_only_prompt_keeps_other_slots_unknown(self) -> None:
        result = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=_request_ref(),
            observations=(_use_observation(),),
        )
        slots = {item.slot: item for item in result.brief.slots}

        self.assertIs(slots[BriefSlot.USE].status, BriefSlotStatus.SUPPORTED)
        for slot in (
            BriefSlot.SIZE,
            BriefSlot.OCCUPANCY,
            BriefSlot.SPACE_PROGRAM,
            BriefSlot.REGULATIONS,
        ):
            self.assertIs(slots[slot].status, BriefSlotStatus.UNKNOWN)
            self.assertEqual(len(slots[slot].obligation_ids), 1)
        self.assertEqual(result.brief.constraint_proposals, ())
        self.assertEqual(result.brief.proposed_commitments, ())
        self.assertFalse(result.brief.to_dict()["geometry_selected"])
        self.assertFalse(result.brief.to_dict()["generation_authority"])

    def test_explicit_size_becomes_external_proposal_not_active_fact(self) -> None:
        result = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=_request_ref(),
            observations=(_use_observation(),),
            intent_observations=(_size_intent(),),
        )
        slots = {item.slot: item for item in result.brief.slots}

        self.assertIs(slots[BriefSlot.SIZE].status, BriefSlotStatus.PROPOSED)
        self.assertEqual(len(result.brief.constraint_proposals), 1)
        constraint = result.brief.constraint_proposals[0]
        self.assertEqual(constraint.value.to_python(), 2400)
        self.assertEqual(constraint.unit, "square_metres")
        self.assertEqual(
            constraint.compiler_id,
            result.brief.compiler_id,
        )
        self.assertEqual(
            constraint.base_state_sha256,
            result.brief.base.require_digest(),
        )
        commitment = result.brief.proposed_commitments[0]
        self.assertIs(commitment.status, CommitmentStatus.PROPOSED)
        self.assertFalse(commitment.has_hard_gate_authority)
        self.assertEqual(
            constraint.commitment_id,
            commitment.commitment_id,
        )
        self.assertEqual(
            slots[BriefSlot.SIZE].obligation_ids,
            ("resolve-brief-size",),
        )
        self.assertEqual(
            DesignBrief.from_dict(result.brief.to_dict()),
            result.brief,
        )

    def test_claim_kinds_and_epistemics_remain_separate(self) -> None:
        request_ref = _request_ref()
        observations = (
            _use_observation(),
            BriefObservation(
                observation_id="retrieved-regulation-lead",
                slot=BriefSlot.REGULATIONS,
                kind=BriefClaimKind.RETRIEVED_EVIDENCE,
                key="regulation-source-lead",
                value={"source_available": True},
                epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                authority_id="authority.retrieval-provider",
                source_refs=(
                    "project://case-a/runs/brief-001/records/retrieval.json",
                ),
                resolves_slot=False,
            ),
            BriefObservation(
                observation_id="occupancy-hypothesis",
                slot=BriefSlot.OCCUPANCY,
                kind=BriefClaimKind.HYPOTHESIS,
                key="occupancy-hypothesis",
                value={"range": [100, 200]},
                epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                authority_id="agent.program",
                source_refs=(
                    "project://case-a/runs/brief-001/records/program.json",
                ),
                resolves_slot=False,
            ),
            BriefObservation(
                observation_id="daylight-preference",
                slot=BriefSlot.SPACE_PROGRAM,
                kind=BriefClaimKind.PREFERENCE,
                key="daylight-preference",
                value="prefer natural light",
                epistemic_status=FactEpistemicStatus.DECLARED,
                authority_id="authority.user",
                source_refs=(request_ref,),
                resolves_slot=False,
            ),
            BriefObservation(
                observation_id="basement-prohibition",
                slot=BriefSlot.SIZE,
                kind=BriefClaimKind.PROHIBITION,
                key="basement-prohibition",
                value="no basement",
                epistemic_status=FactEpistemicStatus.DECLARED,
                authority_id="authority.user",
                source_refs=(request_ref,),
                resolves_slot=False,
            ),
        )

        result = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=request_ref,
            observations=observations,
        )

        self.assertEqual(
            {item.kind for item in result.brief.claims},
            set(BriefClaimKind),
        )
        retrieved = next(
            item
            for item in result.brief.claims
            if item.kind is BriefClaimKind.RETRIEVED_EVIDENCE
        )
        self.assertIs(
            retrieved.fact.epistemic_status,
            FactEpistemicStatus.HYPOTHESIS,
        )
        self.assertNotIn(
            "hypothesis",
            str(retrieved.fact.python_value).lower(),
        )

    def test_ambiguous_intent_creates_alternatives_and_obligation(self) -> None:
        request_ref = _request_ref()
        ambiguous = BriefIntentObservation(
            slot=BriefSlot.OCCUPANCY,
            observation=IntentObservation(
                observation_id="ambiguous-occupancy",
                raw_text="About three hundred people.",
                source_event_ref="project://case-a/events/request.json",
                authority_id="authority.user",
                scope_ref="project://case-a",
                interpretations=tuple(
                    IntentTerm(
                        parameter_key="building.occupancy",
                        operator=operator,
                        value=300,
                        unit="people",
                    )
                    for operator in (
                        IntentOperator.TARGET,
                        IntentOperator.MINIMUM,
                        IntentOperator.MAXIMUM,
                    )
                ),
                evidence_refs=(request_ref,),
                explicit=False,
            ),
        )
        result = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=request_ref,
            intent_observations=(ambiguous,),
        )
        slot = next(
            item
            for item in result.brief.slots
            if item.slot is BriefSlot.OCCUPANCY
        )

        self.assertIs(slot.status, BriefSlotStatus.AMBIGUOUS)
        self.assertEqual(len(slot.constraint_proposal_ids), 3)
        self.assertEqual(
            slot.obligation_ids,
            ("resolve-brief-occupancy",),
        )
        self.assertEqual(
            result.receipt.ambiguous_slots,
            ("occupancy",),
        )

    def test_round_trip_exact_base_and_non_isomorphic_scope(self) -> None:
        first = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base("case-a"),
            raw_request_ref=_request_ref("case-a"),
            observations=(
                _use_observation("case-a", value="public learning use"),
            ),
        ).brief
        second = compile_design_brief(
            project_id="case-b",
            run_id="brief-001",
            base=_base("case-b"),
            raw_request_ref=_request_ref("case-b"),
            observations=(
                _use_observation("case-b", value="small workshop use"),
            ),
        ).brief

        self.assertEqual(DesignBrief.from_dict(first.to_dict()), first)
        self.assertNotEqual(first.brief_digest, second.brief_digest)
        self.assertTrue(
            all(
                item.base_state_sha256 == first.base.require_digest()
                for item in first.claims
            )
        )
        self.assertNotIn(
            "The user supplied",
            str(
                compile_design_brief(
                    project_id="case-a",
                    run_id="brief-001",
                    base=_base(),
                    raw_request_ref=_request_ref(),
                    intent_observations=(_size_intent(),),
                ).brief.to_dict()
            ),
        )

    def test_transcript_dump_and_cross_project_evidence_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "transcripts cannot enter",
        ):
            BriefObservation(
                observation_id="bad-transcript",
                slot=BriefSlot.USE,
                kind=BriefClaimKind.HYPOTHESIS,
                key="bad-transcript",
                value={"transcript": "entire conversation"},
                epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                authority_id="agent.interpreter",
                source_refs=(_request_ref(),),
                resolves_slot=False,
            )
        foreign = BriefObservation(
            observation_id="foreign-evidence",
            slot=BriefSlot.USE,
            kind=BriefClaimKind.HYPOTHESIS,
            key="foreign-evidence",
            value="unbound",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            authority_id="agent.interpreter",
            source_refs=(
                "project://case-b/runs/retrieval/records/evidence.json",
            ),
            resolves_slot=False,
        )
        with self.assertRaisesRegex(ValueError, "another project"):
            compile_design_brief(
                project_id="case-a",
                run_id="brief-001",
                base=_base(),
                raw_request_ref=_request_ref(),
                observations=(foreign,),
            )

    def test_brief_rejects_drifted_provenance_and_cross_slot_links(self) -> None:
        brief = compile_design_brief(
            project_id="case-a",
            run_id="brief-001",
            base=_base(),
            raw_request_ref=_request_ref(),
            observations=(_use_observation(),),
            intent_observations=(_size_intent(),),
        ).brief
        drifted_claim = replace(
            brief.claims[0],
            base_state_sha256="b" * 64,
        )
        with self.assertRaisesRegex(ValueError, "claim provenance"):
            replace(brief, claims=(drifted_claim,))

        slots = tuple(
            replace(
                slot,
                status=BriefSlotStatus.AMBIGUOUS,
                claim_refs=(brief.claims[0].ref,),
            )
            if slot.slot is BriefSlot.REGULATIONS
            else slot
            for slot in brief.slots
        )
        with self.assertRaisesRegex(ValueError, "another slot"):
            replace(brief, slots=slots)

    def test_similar_project_id_cannot_capture_foreign_intent_scope(self) -> None:
        foreign = BriefIntentObservation(
            slot=BriefSlot.SIZE,
            observation=replace(
                _size_intent("case-a2").observation,
                evidence_refs=(_request_ref("case-a"),),
            ),
        )
        with self.assertRaisesRegex(ValueError, "another project scope"):
            compile_design_brief(
                project_id="case-a",
                run_id="brief-001",
                base=_base(),
                raw_request_ref=_request_ref(),
                intent_observations=(foreign,),
            )

    def test_compiler_contains_no_instance_or_spatial_defaults(self) -> None:
        sources = (
            Path(__file__).parents[2]
            / "archive" / "archive" / "archflow"
            / "state"
            / "design_brief.py"
        ).read_text(encoding="utf-8") + (
            Path(__file__).parents[2]
            / "archive" / "archive" / "archflow"
            / "compilers"
            / "brief.py"
        ).read_text(encoding="utf-8")
        lowered = sources.lower()
        for forbidden in (
            "pantheon",
            "rotunda",
            "library",
            "16x12",
            "room list default",
            "expert order",
            "compose_building",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
