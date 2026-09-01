from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from archflow.adapters.three_dm_inspector import ThreeDmInspection
from tools.build_pantheon_progress_snapshot import (
    _closure_inventory,
    _execution_verification_summary,
    _model_alignment,
    _relation_control_summary,
)


def inspection(*, unit: str = "Meters") -> ThreeDmInspection:
    return ThreeDmInspection(
        file_sha256="a" * 64,
        file_bytes=1024,
        three_dm_version=8,
        archive_version=80,
        units={"name": unit, "code": 4 if unit == "Meters" else 8},
        layers=(),
        object_count=59,
        top_level_object_count=59,
        instance_definition_member_count=0,
        object_counts_by_type={"Brep": 59},
        object_counts_by_layer=(),
        instance_definitions=(),
        instance_references=(),
        document_user_strings=(),
        object_user_strings=(),
        aggregate_bbox={"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
        bbox_contributing_geometry_count=59,
    )


def receipt(*, program_digest: str = "b" * 64) -> dict[str, object]:
    return {
        "artifacts": {"model": {"sha256": "a" * 64}},
        "program_digest": program_digest,
        "verification": {"object_count": 59},
    }


def execution_receipt() -> dict[str, object]:
    return {
        "candidate_execution_verified": True,
        "disposition": "HOLD",
        "verification": {
            "strict": {
                "status": "diverged",
                "mismatches": [
                    {
                        "code": "bounds_deviation",
                        "object_id": "transition-block-object",
                        "deviation": 0.4374,
                    }
                ],
            },
            "contract": {"status": "equivalent", "mismatches": []},
            "semantics": {"status": "verified", "mismatches": []},
            "strict_difference_classification": "contract-equivalent",
            "max_abs_deviation": 0.4374,
        },
        "headless_model_gate": {
            "passed": True,
            "units": {"name": "Meters", "code": 4},
            "object_count": 59,
            "issues": [],
        },
    }


def relation_control(
    *,
    verification_status: str = "pass",
    promotion_status: str | None = "verified_promotion",
    source_status: str = "PASS",
    base_assembly_status: str = "pass",
) -> dict[str, object]:
    promoted = promotion_status is not None
    return {
        "schema": "P069StageRelationControlSummary@1",
        "status": source_status,
        "inventory_ref": "project://pantheon/run/stage/inventory.json",
        "inventory_digest": "1" * 64,
        "context_ref": "project://pantheon/run/stage/context.json",
        "context_digest": "2" * 64,
        "proposal_ref": "project://pantheon/run/stage/proposal.json",
        "proposal_digest": "3" * 64,
        "compilation_ref": "project://pantheon/run/stage/compilation.json",
        "compilation_status": "proposal_compiled",
        "assembly_profile_ref": "project://pantheon/run/stage/profile.json",
        "base_assembly_receipt_ref": (
            "project://pantheon/run/stage/base-assembly.json"
        ),
        "base_assembly_receipt_status": base_assembly_status,
        "program_topology_receipt_ref": (
            "project://pantheon/run/stage/program-topology.json"
        ),
        "program_topology_receipt_status": "pass",
        "walking_surface_profile_ref": (
            "project://pantheon/run/stage/walking-profile.json"
        ),
        "walking_surface_profile_digest": "5" * 64,
        "walking_surface_receipt_ref": (
            "project://pantheon/run/stage/walking-receipt.json"
        ),
        "walking_surface_receipt_digest": "6" * 64,
        "walking_surface_receipt_status": "pass",
        "relation_verification_base_receipt_ref": (
            "project://pantheon/run/stage/verification-base.json"
        ),
        "relation_verification_base_receipt_digest": "7" * 64,
        "relation_verification_base_receipt_status": "pass",
        "question_verifications": [
            {
                "question_ref": "relation-question:portico-support",
                "profile_ref": "project://pantheon/run/stage/verification-profile.json",
                "receipt_ref": "project://pantheon/run/stage/verification-receipt.json",
                "status": verification_status,
            }
        ],
        "promotion_ref": (
            "project://pantheon/run/stage/relation-promotion.json"
            if promoted
            else None
        ),
        "promotion_status": promotion_status,
        "effective_graph_ref": "architectural-relation-graph:" + "4" * 64,
        "effective_graph_digest": "4" * 64,
        "unresolved_reason_codes": (
            []
            if verification_status == "pass" and promoted
            else ["relation-independent-check-unknown"]
        ),
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


class PantheonProgressModelAlignmentTests(unittest.TestCase):
    def test_metric_current_model_is_current_but_never_accepts_stage(self) -> None:
        result = _model_alignment(
            inspection(),
            receipt(),
            current_program_digest="b" * 64,
        )

        self.assertEqual("CURRENT", result["status"])
        self.assertEqual([], result["reason_codes"])
        self.assertFalse(result["stage_acceptance_authority"])
        self.assertFalse(result["canonical_write_authority"])


class PantheonProgressRelationControlTests(unittest.TestCase):
    def test_legacy_review_is_not_recorded_and_never_promoted(self) -> None:
        result = _relation_control_summary(
            {"schema": "P069CandidateStageReview@1", "stage": 0}
        )

        self.assertEqual("NOT_RECORDED", result["recording_status"])
        self.assertEqual("NOT_RECORDED", result["status"])
        self.assertEqual("NOT_RECORDED", result["promotion"]["status"])
        self.assertIn(
            "relation-control-not-recorded",
            result["unresolved_reason_codes"],
        )
        self.assertFalse(result["stage_acceptance_authority"])

    def test_unknown_verification_and_absent_promotion_are_blocked(self) -> None:
        review = {
            "schema": "P069CandidateStageReview@1",
            "stage": 1,
            "relation_control": relation_control(
                verification_status="unknown",
                promotion_status=None,
                source_status="BLOCKED",
            ),
        }

        result = _relation_control_summary(review)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", result["question"]["status"])
        self.assertEqual("BLOCKED", result["verification"]["status"])
        self.assertEqual("BLOCKED", result["promotion"]["status"])
        self.assertIn(
            "relation-independent-check-unknown",
            result["unresolved_reason_codes"],
        )
        self.assertIn(
            "relation-promotion-not-recorded",
            result["unresolved_reason_codes"],
        )

    def test_complete_relation_records_still_have_no_stage_authority(self) -> None:
        result = _relation_control_summary(
            {
                "schema": "P069CandidateStageReview@1",
                "stage": 2,
                "relation_control": relation_control(),
            }
        )

        self.assertEqual("PASS", result["status"])
        self.assertEqual("PASS", result["verification"]["status"])
        self.assertEqual("PASS", result["promotion"]["status"])
        self.assertFalse(result["stage_acceptance_authority"])
        self.assertFalse(result["canonical_write_authority"])

    def test_exact_topology_pass_closes_conservative_generic_unknown(self) -> None:
        result = _relation_control_summary(
            {
                "schema": "P069CandidateStageReview@1",
                "stage": 3,
                "relation_control": relation_control(
                    base_assembly_status="unknown"
                ),
            }
        )

        self.assertEqual("PASS", result["status"])
        self.assertEqual("PASS", result["assembly"]["status"])
        self.assertTrue(
            result["assembly"]["exact_topology_supersedes_generic_unknown"]
        )
        self.assertEqual([], result["unresolved_reason_codes"])

    def test_record_inventory_completeness_is_not_formal_closure(self) -> None:
        names = (
            "branch-research-scope-a.json",
            "evidence-sufficiency-a.json",
            "stage-convergence-a.json",
            "stage-evidence-pack-a.json",
            "stage-subject-inventory-a.json",
            "relation-authoring-context-a.json",
            "relation-authoring-proposal-a.json",
            "relation-authoring-compilation-a.json",
            "base-assembly-receipt-a.json",
            "relation-verification-receipt-a.json",
            "relation-promotion-a.json",
        )
        with TemporaryDirectory() as temporary:
            branches = Path(temporary) / "branches"
            records = branches / "stage-0" / "records"
            records.mkdir(parents=True)
            for name in names:
                (records / name).touch()

            class Layout:
                def run(self, run_id: str) -> SimpleNamespace:
                    self.run_id = run_id
                    return SimpleNamespace(branches=branches)

            repository = SimpleNamespace(layout=Layout())
            run = SimpleNamespace(run_id="reconstruction-1")
            result = _closure_inventory(repository, run)

        counts = result["record_counts"]
        self.assertEqual(1, counts["stage_subject_inventory"])
        self.assertEqual(1, counts["relation_authoring_context"])
        self.assertEqual(1, counts["relation_authoring_proposal"])
        self.assertEqual(1, counts["relation_authoring_compilation"])
        self.assertEqual(1, counts["assembly_receipt"])
        self.assertEqual(1, counts["relation_verification"])
        self.assertEqual(1, counts["relation_promotion"])
        self.assertTrue(result["record_inventory_complete"])
        self.assertFalse(result["formal_closure_present"])
        self.assertEqual("NOT_EVALUATED", result["formal_closure_status"])
        self.assertFalse(result["stage_acceptance_authority"])


class PantheonProgressModelDriftTests(unittest.TestCase):
    def test_inches_and_old_program_are_independent_blockers(self) -> None:
        result = _model_alignment(
            inspection(unit="Inches"),
            receipt(program_digest="c" * 64),
            current_program_digest="b" * 64,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(
            ["model.unit_mismatch", "model.stale_program"],
            result["reason_codes"],
        )
        self.assertFalse(result["checks"]["unit_matches_stage_contract"])
        self.assertFalse(result["checks"]["program_matches_current_stage"])

    def test_digest_and_object_count_drift_fail_closed(self) -> None:
        drifted = replace(
            inspection(),
            file_sha256="d" * 64,
            object_count=58,
        )
        result = _model_alignment(
            drifted,
            receipt(),
            current_program_digest="b" * 64,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(
            ["model.digest_mismatch", "model.object_count_mismatch"],
            result["reason_codes"],
        )

    def test_execution_summary_keeps_strict_divergence_visible(self) -> None:
        result = _execution_verification_summary(execution_receipt())

        self.assertTrue(result["candidate_execution_verified"])
        self.assertEqual("diverged", result["strict"]["status"])
        self.assertEqual(0.4374, result["strict"]["mismatches"][0]["deviation"])
        self.assertEqual("equivalent", result["contract"]["status"])
        self.assertEqual("verified", result["semantics"]["status"])
        self.assertEqual("Meters", result["headless_model_gate"]["units"]["name"])
        self.assertFalse(result["stage_acceptance_authority"])
        self.assertFalse(result["canonical_write_authority"])


if __name__ == "__main__":
    unittest.main()
