from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.adapters.three_dm_inspector import ThreeDmInspection
from tools.build_pantheon_progress_snapshot import (
    _execution_verification_summary,
    _model_alignment,
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
