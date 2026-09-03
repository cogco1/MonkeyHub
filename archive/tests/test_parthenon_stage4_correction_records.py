from __future__ import annotations

import copy
import unittest

from archive.tools import parthenon_stage4_correction_records as correction
from archive.tools import run_parthenon_reconstruction as stage3
from archive.tools import run_parthenon_stage4_reconstruction as stage4


AUTHORIZATION_REF = (
    "decision:human-authorized-stage4-principal-door-closed-double-leaf"
)
DIAGNOSIS_REF = (
    "project://parthenon-reconstruction/runs/research-007/records/"
    "principal-door-dependency-diagnosis-test.json"
)


def _partition() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    sources = {
        decision.decision_ref: (
            f"evidence:{decision.decision_ref.split(':')[-1]}",
        )
        for decision in stage3.DECISIONS
    }
    predecessor = stage3.build_stage_operations(3, sources)
    _, lineage = stage4.compile_stage4_operations(
        predecessor,
        column_source_refs=("evidence:penrose-column",),
        entablature_source_refs=("evidence:perseus-entablature",),
        opening_source_refs=("evidence:bsa-windows",),
        visual_manifest_ref="evidence:selected-visual-regions",
    )
    roots = tuple(str(item["operation_id"]) for item in predecessor)
    refined = tuple(str(item) for item in lineage["replacement_allowlist"])
    copied = tuple(sorted(set(roots) - set(refined)))
    return roots, refined, copied


def _experience() -> dict[str, object]:
    roots, refined, copied = _partition()
    return correction.compile_incomplete_stage4_experience_record(
        stage3_root_operation_ids=roots,
        refined_stage3_root_ids=refined,
        copied_stage3_root_ids=copied,
        failed_attempt_evidence_refs=(
            *correction.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS,
            "project://parthenon-reconstruction/runs/reconstruction-005/"
            "branches/idealized-periclean-original/records/"
            "stage-4-detail-validation-test.json",
        ),
    )


def _experience_ref(record: dict[str, object]) -> str:
    return (
        "project://parthenon-reconstruction/runs/research-007/records/"
        f"stage4-incomplete-attempt-experience-{record['record_digest']}.json"
    )


def _decision(experience: dict[str, object] | None = None) -> dict[str, object]:
    retained = experience or _experience()
    return correction.compile_authorized_door_decision_record(
        authorization_ref=AUTHORIZATION_REF,
        failed_attempt_experience_ref=_experience_ref(retained),
        door_dependency_diagnosis_ref=DIAGNOSIS_REF,
        selected_visual_manifest_ref=correction.VISUAL_MANIFEST_REF,
    )


def _reseal(record: dict[str, object]) -> None:
    record["record_digest"] = correction.compute_correction_record_digest(record)


class ParthenonStage4CorrectionRecordTests(unittest.TestCase):
    def test_experience_record_is_stable_and_retains_exact_failure_counts(self) -> None:
        roots, refined, copied = _partition()
        first = correction.compile_incomplete_stage4_experience_record(
            stage3_root_operation_ids=roots,
            refined_stage3_root_ids=refined,
            copied_stage3_root_ids=copied,
            failed_attempt_evidence_refs=(
                *correction.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS,
                "project://parthenon-reconstruction/runs/reconstruction-005/"
                "records/detail-gate.json",
            ),
        )
        second = correction.compile_incomplete_stage4_experience_record(
            stage3_root_operation_ids=tuple(reversed(roots)),
            refined_stage3_root_ids=tuple(reversed(refined)),
            copied_stage3_root_ids=tuple(reversed(copied)),
            failed_attempt_evidence_refs=tuple(
                reversed(
                    (
                        *correction.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS,
                        "project://parthenon-reconstruction/runs/"
                        "reconstruction-005/records/detail-gate.json",
                    )
                )
            ),
        )

        self.assertEqual(first, second)
        validation = correction.validate_incomplete_stage4_experience_record(first)
        self.assertTrue(validation["passed"], validation["failures"])
        self.assertEqual("reconstruction-004", validation["only_stage_predecessor_run_id"])
        self.assertEqual(271, first["coverage"]["stage3_root_operation_count"])
        self.assertEqual(110, first["coverage"]["refined_stage3_root_count"])
        self.assertEqual(161, first["coverage"]["copied_stage3_root_count"])
        findings = first["failure_findings"]
        self.assertEqual(23, findings["inner_colonnade"]["direct_lower_to_upper_shaft_contact_count"])
        self.assertEqual(2, findings["east_windows"]["obstruction_count"])
        self.assertEqual(0, findings["materials"]["effective_render_material_object_count"])
        self.assertEqual(4.92, findings["principal_doors"]["stone_opening_width_m"])
        self.assertEqual(4.20, findings["principal_doors"]["old_leaf_width_m"])
        self.assertEqual(
            ["main-gabled-roof", "pediment-east", "pediment-west"],
            findings["roof_pediment"]["unrefined_stage3_root_ids"],
        )

    def test_content_tamper_breaks_stable_digest(self) -> None:
        record = _experience()
        record["failure_findings"]["inner_colonnade"][
            "direct_lower_to_upper_shaft_contact_count"
        ] = 22

        result = correction.validate_incomplete_stage4_experience_record(record)

        self.assertFalse(result["passed"])
        self.assertTrue(any("record_digest" in item for item in result["failures"]))

    def test_resealed_summary_tamper_still_fails_semantics(self) -> None:
        record = _experience()
        record["coverage"]["refined_stage3_root_count"] = 109
        _reseal(record)

        result = correction.validate_incomplete_stage4_experience_record(record)

        self.assertFalse(result["passed"])
        self.assertIn("coverage summary counts drifted", result["failures"])

    def test_reconstruction_005_cannot_become_stage_predecessor(self) -> None:
        record = _experience()
        record["only_stage_predecessor"]["run_id"] = "reconstruction-005"
        _reseal(record)

        result = correction.validate_incomplete_stage4_experience_record(record)

        self.assertFalse(result["passed"])
        self.assertTrue(
            any("only_stage_predecessor" in item for item in result["failures"])
        )

    def test_failed_attempt_policy_cannot_be_promoted_or_used_as_exact(self) -> None:
        record = _experience()
        record["failed_attempt_policy"]["eligible_as_exact_stage_predecessor"] = True
        record["failed_attempt_policy"]["eligible_for_canonical_promotion"] = True
        _reseal(record)

        result = correction.validate_incomplete_stage4_experience_record(record)

        self.assertFalse(result["passed"])
        self.assertTrue(any("failed-attempt policy" in item for item in result["failures"]))

    def test_experience_compiler_requires_exact_failed_attempt_artifacts(self) -> None:
        roots, refined, copied = _partition()
        with self.assertRaises(correction.ParthenonStage4CorrectionRecordError):
            correction.compile_incomplete_stage4_experience_record(
                stage3_root_operation_ids=roots,
                refined_stage3_root_ids=refined,
                copied_stage3_root_ids=copied,
                failed_attempt_evidence_refs=(
                    correction.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS[0],
                ),
            )

    def test_human_door_decision_is_candidate_only_with_complete_dependencies(self) -> None:
        experience = _experience()
        record = _decision(experience)

        result = correction.validate_authorized_door_decision_record(record)

        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual("reconstruction-004", result["only_stage_predecessor_run_id"])
        self.assertEqual("reconstruction-005", result["failed_attempt_run_id"])
        self.assertEqual(AUTHORIZATION_REF, result["authorization_ref"])
        self.assertEqual("AUTHORIZED_CLOSED_DOUBLE_LEAF", record["authorization_scope"]["strategy"])
        self.assertFalse(record["authorization_scope"]["historical_truth_authorized"])
        self.assertFalse(
            record["authorization_scope"]["candidate_parameters"]["metric_authority"]
        )
        self.assertEqual(5, len(record["dependency_chain"]))
        self.assertEqual(
            sorted(correction.REQUIRED_INVALIDATES_ON),
            [item["condition_id"] for item in record["invalidates_on"]],
        )

    def test_missing_or_nonhuman_authorization_fails_closed(self) -> None:
        experience = _experience()
        for authorization in ("", "decision:assistant-selected-double-leaf"):
            with self.subTest(authorization=authorization):
                with self.assertRaises((TypeError, ValueError)):
                    correction.compile_authorized_door_decision_record(
                        authorization_ref=authorization,
                        failed_attempt_experience_ref=_experience_ref(experience),
                        door_dependency_diagnosis_ref=DIAGNOSIS_REF,
                        selected_visual_manifest_ref=correction.VISUAL_MANIFEST_REF,
                    )

    def test_wrong_predecessor_in_human_decision_fails_after_reseal(self) -> None:
        record = _decision()
        record["only_stage_predecessor"]["run_id"] = "reconstruction-005"
        record["only_stage_predecessor"]["stage_index"] = 4
        _reseal(record)

        result = correction.validate_authorized_door_decision_record(record)

        self.assertFalse(result["passed"])
        self.assertTrue(
            any("only_stage_predecessor" in item for item in result["failures"])
        )

    def test_missing_invalidator_fails_at_compile_and_after_reseal(self) -> None:
        experience = _experience()
        incomplete = correction.REQUIRED_INVALIDATES_ON[:-1]
        with self.assertRaises(correction.ParthenonStage4CorrectionRecordError):
            correction.compile_authorized_door_decision_record(
                authorization_ref=AUTHORIZATION_REF,
                failed_attempt_experience_ref=_experience_ref(experience),
                door_dependency_diagnosis_ref=DIAGNOSIS_REF,
                selected_visual_manifest_ref=correction.VISUAL_MANIFEST_REF,
                invalidates_on=incomplete,
            )

        record = _decision(experience)
        record["invalidates_on"].pop()
        _reseal(record)
        result = correction.validate_authorized_door_decision_record(record)
        self.assertFalse(result["passed"])
        self.assertTrue(any("invalidates_on" in item for item in result["failures"]))

    def test_dependency_authority_cannot_upgrade_failed_attempt_or_visual_roi(self) -> None:
        record = _decision()
        record["dependency_chain"][1]["authority"] = "EXACT_GEOMETRY_PREDECESSOR"
        record["dependency_chain"][3]["authority"] = "EXACT_DIMENSION"
        _reseal(record)

        result = correction.validate_authorized_door_decision_record(record)

        self.assertFalse(result["passed"])
        self.assertTrue(any("dependency chain" in item for item in result["failures"]))

    def test_human_readable_report_covers_full_correction_boundary(self) -> None:
        experience = _experience()
        decision = _decision(experience)

        report = correction.render_stage4_correction_report(experience, decision)

        for required in (
            "唯一 Stage 前驱是 `reconstruction-004`",
            "`reconstruction-005` 仅作 `FAILED_ATTEMPT_EVIDENCE_ONLY`",
            "271 = 110 refined + 161 copied",
            "main-gabled-roof",
            "pediment-east",
            "pediment-west",
            "23 处上下柱身直接接触",
            "2 处 `projected_side_aisle_or_view_overlap`",
            "石门洞 4.92 × 9.84 m",
            "旧门扇只有 4.20 × 7.00 m",
            "左右各空 0.36 m、顶部空 2.84 m",
            "有效渲染材质覆盖为 0 / 509",
            "旧 closure 的自证问题",
            "research-007",
            "reconstruction-006",
            "AUTHORIZED_CLOSED_DOUBLE_LEAF",
            "SOFT、闭合、左右对称双扇门候选",
            "它不是历史真值",
            "metric_authority = false",
            "本次修正不采用任何外部网格资产",
            "research-006/workspaces/asset-rag/_外部资产来源清单.md",
            "canonical HEAD 不变",
            correction.CANONICAL_STATE_SHA256,
        ):
            with self.subTest(required=required):
                self.assertIn(required, report)
        for condition in correction.REQUIRED_INVALIDATES_ON:
            self.assertIn(f"`{condition}`", report)
        self.assertTrue(report.endswith("\n"))

    def test_report_refuses_tampered_experience_or_door_decision(self) -> None:
        experience = _experience()
        decision = _decision(experience)
        tampered_experience = copy.deepcopy(experience)
        tampered_experience["failure_findings"]["east_windows"][
            "obstruction_count"
        ] = 1
        with self.assertRaises(correction.ParthenonStage4CorrectionRecordError):
            correction.render_stage4_correction_report(
                tampered_experience,
                decision,
            )

        tampered_decision = copy.deepcopy(decision)
        tampered_decision["authority_boundary"]["historical_truth_authorized"] = True
        with self.assertRaises(correction.ParthenonStage4CorrectionRecordError):
            correction.render_stage4_correction_report(
                experience,
                tampered_decision,
            )

    def test_report_refuses_individually_valid_but_unrelated_records(self) -> None:
        experience = _experience()
        unrelated_ref = (
            "project://parthenon-reconstruction/runs/research-007/records/"
            f"stage4-incomplete-attempt-experience-{'0' * 64}.json"
        )
        decision = correction.compile_authorized_door_decision_record(
            authorization_ref=AUTHORIZATION_REF,
            failed_attempt_experience_ref=unrelated_ref,
            door_dependency_diagnosis_ref=DIAGNOSIS_REF,
            selected_visual_manifest_ref=correction.VISUAL_MANIFEST_REF,
        )
        self.assertTrue(
            correction.validate_authorized_door_decision_record(decision)["passed"]
        )

        with self.assertRaises(correction.ParthenonStage4CorrectionRecordError):
            correction.render_stage4_correction_report(experience, decision)

    def test_dispatcher_rejects_unknown_schema(self) -> None:
        result = correction.validate_stage4_correction_record(
            {"schema": "UnknownCorrection@1", "record_digest": "0" * 64}
        )
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
