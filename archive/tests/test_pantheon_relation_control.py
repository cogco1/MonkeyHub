"""Pantheon Stage 0--3 project relation-control tests."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from archflow.project.refs import ProjectVersionRef
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef
from archflow.relations.contracts import RelationEpistemicStatus
from archflow.state.geometry_program import GeometryParameter
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archive.archflow.validation.relation_verification import RelationVerificationError
from archive.tools import run_pantheon_reconstruction as P
from archive.tools.pantheon_relation_control import (
    PantheonRelationControlError,
    compile_pantheon_program_topology_receipt,
    compile_pantheon_relation_control,
    pantheon_subject_record_payloads,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
EVIDENCE_REFS = ("evidence:pantheon-relation-declaration",)
AUTHORITY_REFS = ("authority:pantheon-relation-declaration",)


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        project_id=P.PROJECT_ID,
        run_id="relation-control-test",
        base=ProjectVersionRef(P.PROJECT_ID, 0, SHA_A),
        state_digest=SHA_A,
        selected_schematic=SimpleNamespace(
            option=SimpleNamespace(
                proposal=SimpleNamespace(
                    evidence_refs=("evidence:pantheon-geometry-program",)
                )
            )
        ),
    )


def _branch() -> BranchRef:
    state = _state()
    return BranchRef(
        run=RunRef(
            project_id=state.project_id,
            run_id=state.run_id,
            base=state.base,
        ),
        branch_id="candidate",
        epoch=0,
    )


def _program(stage: int):
    return P._pantheon_geometry(_state(), stage=stage)


def _record_ref(stage: int, name: str, digest: str) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=P.PROJECT_ID,
        relative_path=(
            "runs/relation-control-test/branches/candidate/records/"
            f"{name}-stage-{stage}.json"
        ),
        sha256=digest,
    )


def _compile(stage: int, program=None, *, verification_base_receipt=None):
    if program is None:
        program = _program(stage)
    branch = _branch()
    payloads = pantheon_subject_record_payloads(stage, program, branch)
    return compile_pantheon_relation_control(
        stage,
        program,
        branch,
        scope_digest=SHA_B,
        stage_subject_ref=f"stage-subject:pantheon-stage-{stage}",
        stage_subject_digest=SHA_C,
        component_proposal_ref=_record_ref(stage, "component-proposal", SHA_D),
        component_proposal_digest=payloads.component_proposal_digest,
        component_index_ref=_record_ref(stage, "component-index", SHA_E),
        component_index_digest=payloads.component_index_digest,
        evidence_refs=EVIDENCE_REFS,
        authority_refs=AUTHORITY_REFS,
        verification_base_receipt=verification_base_receipt,
    )


def _unknown_receipt(receipt: CheckReceiptEnvelope) -> CheckReceiptEnvelope:
    return CheckReceiptEnvelope(
        check_id=receipt.check_id,
        checker_id=receipt.checker_id,
        checker_version=receipt.checker_version,
        branch=receipt.branch,
        scope_digest=receipt.scope_digest,
        subject_refs=receipt.subject_refs,
        subject_digest=receipt.subject_digest,
        status=CheckStatus.UNKNOWN,
        source_refs=receipt.source_refs,
        authority_refs=receipt.authority_refs,
        findings=(
            CheckFinding(
                code="pantheon-independent-check-unknown",
                severity=FindingSeverity.UNKNOWN,
                message="Independent geometry verification is not conclusive.",
                subject_refs=(receipt.subject_refs[0],),
                evidence_refs=(receipt.source_refs[0],),
            ),
        ),
        coverage_denominator=receipt.coverage_denominator,
        covered_refs=(),
    )


def _replace_operation_parameter(program, op_id: str, name: str, update):
    operations = []
    for operation in program.operations:
        if operation.op_id != op_id:
            operations.append(operation)
            continue
        parameters = []
        for parameter in operation.parameters:
            if parameter.name != name:
                parameters.append(parameter)
                continue
            parameters.append(
                GeometryParameter.create(
                    name=parameter.name,
                    kind=parameter.kind,
                    value=update(json.loads(parameter.value_json)),
                    unit=parameter.unit,
                )
            )
        operations.append(
            replace(
                operation,
                parameters=tuple(sorted(parameters, key=lambda item: item.name)),
            )
        )
    return replace(
        program,
        operations=tuple(sorted(operations, key=lambda item: item.op_id)),
    )


class PantheonRelationControlTests(unittest.TestCase):
    def test_finished_floor_objects_are_in_exact_semantic_bindings(self) -> None:
        program = _program(1)
        expected = {
            "rotunda-binding": "rotunda-floor-object",
            "transition-binding": "transition-floor-object",
        }
        supplied = {
            binding.binding_id: set(binding.object_ids)
            for binding in program.semantic_bindings
        }

        for binding_id, object_id in expected.items():
            self.assertIn(object_id, supplied[binding_id])
            altered = replace(
                program,
                semantic_bindings=tuple(
                    replace(
                        binding,
                        object_ids=tuple(
                            item for item in binding.object_ids if item != object_id
                        ),
                    )
                    if binding.binding_id == binding_id
                    else binding
                    for binding in program.semantic_bindings
                ),
            )
            with self.assertRaisesRegex(
                PantheonRelationControlError,
                "explicit object mapping|denominator",
            ):
                pantheon_subject_record_payloads(1, altered, _branch())

    def test_stage_1_binds_complete_soft_candidate_walking_path(self) -> None:
        result = _compile(1)
        profile = result.walking_surface_profile
        receipt = result.walking_surface_receipt
        assert profile is not None
        assert receipt is not None

        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertEqual(1, len(profile.paths))
        self.assertEqual(10, len(profile.paths[0].node_refs))
        self.assertEqual(
            "walking-surface:pantheon-exterior-grade",
            profile.paths[0].node_refs[0],
        )
        self.assertEqual(
            "geometry-object:rotunda-floor-object",
            profile.paths[0].node_refs[-1],
        )
        self.assertTrue(
            set(profile.checker_requirement_refs)
            <= set(result.relation_verification_base_receipt.coverage_denominator)
        )
        self.assertIn(
            "candidate:pantheon-front-steps-soft",
            receipt.source_refs,
        )
        access = next(
            item
            for item in result.question_verifications
            if item.profile.question.question_id == "pantheon-main-entry-clear"
        )
        self.assertTrue(
            all(
                set(profile.checker_requirement_refs)
                <= set(binding.checker_requirement_refs)
                for binding in access.profile.bindings
            )
        )
        self.assertTrue(
            all(
                "geometry-object:portico-floor-object"
                in binding.checker_subject_refs
                and "geometry-object:transition-floor-object"
                in binding.checker_subject_refs
                and "geometry-object:rotunda-floor-object"
                in binding.checker_subject_refs
                for binding in access.profile.bindings
            )
        )
        front_step_relations = tuple(
            relation
            for relation in result.proposal_graph.relations
            if any(
                participant.node_ref == "design-component:front-steps"
                for participant in relation.participants
            )
        )
        self.assertEqual(1, len(front_step_relations))

    def test_walking_surface_datum_break_and_threshold_misalignment_fail_closed(self) -> None:
        base = _program(1)

        def taller_step(value):
            value[1] += 0.25
            return value

        def raised_threshold(value):
            value[1] += 0.05
            return value

        cases = {
            "step-datum-break": _replace_operation_parameter(
                base,
                "front-step-2",
                "size",
                taller_step,
            ),
            "threshold-misalignment": _replace_operation_parameter(
                base,
                "door-tool",
                "origin",
                raised_threshold,
            ),
        }
        for label, program in cases.items():
            with self.subTest(label=label):
                result = _compile(1, program)
                receipt = result.walking_surface_receipt
                assert receipt is not None
                self.assertIs(CheckStatus.FAIL, receipt.status)
                self.assertEqual(
                    receipt.coverage_denominator,
                    receipt.covered_refs,
                )
                self.assertIn(
                    "walking-surface-limit-exceeded",
                    {item.code for item in receipt.findings},
                )
                self.assertIsNone(result.promotion)

    def test_stage_payloads_explicitly_enumerate_columns_and_coffers(self) -> None:
        branch = _branch()
        payloads = pantheon_subject_record_payloads(3, _program(3), branch)
        entries = payloads.component_index_payload["entries"]
        component_ids = {item["component_id"] for item in entries}

        self.assertEqual(
            16,
            len(
                {
                    component_id
                    for component_id in component_ids
                    if component_id.startswith("portico-column-r")
                }
            ),
        )
        self.assertEqual(
            140,
            len(
                {
                    component_id
                    for component_id in component_ids
                    if component_id.startswith("coffer-r")
                }
            ),
        )
        self.assertIn("foundation", component_ids)
        encoded = json.dumps(payloads.to_dict(), sort_keys=True)
        self.assertNotIn("relative_path", encoded)
        self.assertNotIn("/records/", encoded)

    def test_all_stages_compile_hypotheses_and_retain_conservative_assembly_unknown(self) -> None:
        relation_counts = []
        for stage in range(4):
            result = _compile(stage)
            relation_counts.append(len(result.proposal_graph.relations))

            self.assertIs(CheckStatus.UNKNOWN, result.base_assembly_receipt.status)
            self.assertIs(CheckStatus.PASS, result.program_topology_receipt.status)
            self.assertEqual(
                {RelationEpistemicStatus.HYPOTHESIS},
                {
                    relation.epistemic_status
                    for relation in result.proposal_graph.relations
                },
            )
            self.assertTrue(
                all(
                    item.receipt.status is CheckStatus.PASS
                    for item in result.question_verifications
                )
            )
            self.assertIsNotNone(result.promotion)
            self.assertEqual(
                {RelationEpistemicStatus.DERIVED},
                {
                    relation.epistemic_status
                    for relation in result.effective_graph.relations
                },
            )
            self.assertTrue(
                set(EVIDENCE_REFS) <= set(result.base_assembly_receipt.source_refs)
            )
            self.assertTrue(
                set(AUTHORITY_REFS)
                <= set(result.base_assembly_receipt.authority_refs)
            )

        self.assertEqual(relation_counts, sorted(relation_counts))
        self.assertEqual(140, relation_counts[3] - relation_counts[2])

    def test_missing_foundation_is_rejected_before_relation_authoring(self) -> None:
        program = _program(0)
        bindings = tuple(
            replace(
                binding,
                object_ids=tuple(
                    item for item in binding.object_ids if item != "plinth-object"
                ),
            )
            if binding.binding_id == "rotunda-binding"
            else binding
            for binding in program.semantic_bindings
        )
        operations = tuple(
            operation for operation in program.operations if operation.op_id != "plinth"
        )
        without_foundation = replace(
            program,
            semantic_bindings=bindings,
            operations=operations,
        )

        with self.assertRaisesRegex(
            PantheonRelationControlError,
            "explicit object mapping|denominator",
        ):
            pantheon_subject_record_payloads(0, without_foundation, _branch())

    def test_obstructed_main_entry_fails_and_cannot_promote(self) -> None:
        program = _program(1)
        operations = []
        for operation in program.operations:
            if operation.op_id != "column-shaft-r0-c0":
                operations.append(operation)
                continue
            parameters = []
            for parameter in operation.parameters:
                if parameter.name == "axis_start":
                    parameters.append(
                        GeometryParameter.create(
                            name=parameter.name,
                            kind=parameter.kind,
                            value=[28.0, 2.0, 23.0],
                            unit=parameter.unit,
                        )
                    )
                elif parameter.name == "axis_end":
                    parameters.append(
                        GeometryParameter.create(
                            name=parameter.name,
                            kind=parameter.kind,
                            value=[28.0, 13.9, 23.0],
                            unit=parameter.unit,
                        )
                    )
                else:
                    parameters.append(parameter)
            operations.append(
                replace(
                    operation,
                    parameters=tuple(sorted(parameters, key=lambda item: item.name)),
                )
            )
        obstructed = replace(
            program,
            operations=tuple(sorted(operations, key=lambda item: item.op_id)),
        )

        result = _compile(1, obstructed)

        self.assertIs(CheckStatus.FAIL, result.program_topology_receipt.status)
        self.assertIs(CheckStatus.FAIL, result.status)
        self.assertIsNone(result.promotion)
        self.assertIs(result.effective_graph, result.proposal_graph)

    def test_unmapped_relation_is_rejected_by_exact_profile_denominator(self) -> None:
        result = _compile(0)
        profile = result.question_verifications[0].profile

        with self.assertRaisesRegex(
            RelationVerificationError,
            "question denominator",
        ):
            replace(profile, bindings=profile.bindings[:-1])

    def test_checker_unknown_keeps_hypothesis_and_skips_promotion(self) -> None:
        first = _compile(0)
        unknown = _unknown_receipt(first.program_topology_receipt)

        result = _compile(0, verification_base_receipt=unknown)

        self.assertIs(CheckStatus.UNKNOWN, result.status)
        self.assertTrue(
            all(
                item.receipt.status is CheckStatus.UNKNOWN
                for item in result.question_verifications
            )
        )
        self.assertIsNone(result.promotion)
        self.assertEqual(result.proposal_graph, result.effective_graph)
        self.assertEqual(
            {RelationEpistemicStatus.HYPOTHESIS},
            {
                relation.epistemic_status
                for relation in result.effective_graph.relations
            },
        )

    def test_topology_checker_exposes_exact_stage_3_boolean_denominator(self) -> None:
        receipt = compile_pantheon_program_topology_receipt(
            3,
            _program(3),
            _branch(),
            SHA_B,
            SHA_C,
        )

        coffer_refs = tuple(
            item
            for item in receipt.coverage_denominator
            if "dome-boolean-input:coffer-cutter" in item
        )
        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertEqual(140, len(coffer_refs))


if __name__ == "__main__":
    unittest.main()
