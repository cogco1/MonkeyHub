from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archflow.adapters.cad_execution import (
    CadExecutionError,
    CadExecutionStatus,
    RhinoCadProgramBinding,
)
from archflow.adapters.stage_cad_execution import (
    StageBoundRhinoCadExecutionReceipt,
    StageCadExportError,
    StageCadExportGuardReceipt,
    execute_stage_bound_rhino_three_dm_export,
    prepare_stage_bound_rhino_three_dm_export,
    require_stage_cad_binding,
)
from archflow.control.stage_artifacts import (
    ArtifactShaBinding,
    RecordDigestBinding,
    StageArtifactClaim,
    StageArtifactStatus,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.state.design_maturity import (
    DeliverableRole,
    DesignPhase,
    PhaseGateReceipt,
    StageEntryProof,
)
from archflow.state.model import ArtifactRef
from tests.test_cad_execution import (
    _binding,
    _inspection,
    _program,
    _verified_readback,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _record(branch: BranchRef, name: str) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/"
            f"records/{name}.json"
        ),
        sha256=_sha(f"record:{name}"),
    )


def _record_binding(branch: BranchRef, name: str) -> RecordDigestBinding:
    return RecordDigestBinding(
        record_ref=_record(branch, name),
        content_digest=_sha(f"content:{name}"),
    )


def _artifact_binding(
    branch: BranchRef,
    artifact_name: str,
) -> ArtifactShaBinding:
    digest = _sha(f"artifact:{artifact_name}")
    return ArtifactShaBinding(
        artifact_ref=ArtifactRef(
            artifact_id=Path(artifact_name).stem,
            uri=(
                f"project://{branch.run.project_id}/runs/{branch.run.run_id}/"
                f"branches/{branch.branch_id}/artifacts/{artifact_name}"
            ),
            media_type="model/vnd.rhino",
            sha256=digest,
        ),
        artifact_sha256=digest,
    )


def _entry_proof(binding: RhinoCadProgramBinding) -> StageEntryProof:
    successor = binding.branch
    predecessor = replace(successor, epoch=successor.epoch - 1)
    gate = PhaseGateReceipt(
        receipt_id="site-exit",
        request_id="enter-schematic",
        branch=predecessor,
        base_state_digest=_sha("site-state"),
        from_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
        to_phase=DesignPhase.SCHEMATIC_DESIGN,
        required_roles=(
            DeliverableRole.SITE_CONTEXT,
            DeliverableRole.BUILD_POLICY,
        ),
        accepted_deliverable_refs=(
            "deliverable:site-context",
            "deliverable:build-policy",
        ),
    )
    return StageEntryProof(
        phase_gate=gate,
        stage_exit_checkpoint_ref=_record(
            successor,
            "stage-exit-checkpoint",
        ),
        stage_exit_proof_digest=_sha("stage-exit-proof"),
        predecessor_checkpoint_digest=_sha("predecessor-checkpoint"),
        successor_checkpoint_digest=_sha("successor-checkpoint"),
        successor_branch=successor,
    )


def _formal_claim(
    program,
    binding: RhinoCadProgramBinding,
    *,
    artifact_name: str = "candidate.3dm",
) -> StageArtifactClaim:
    proof = _entry_proof(binding)
    return StageArtifactClaim(
        claim_id="formal-candidate",
        status=StageArtifactStatus.STAGE_ENTERED_CANDIDATE,
        branch=binding.branch,
        stage_id=binding.stage_id,
        stage_entry_proof=proof,
        stage_entry_proof_record=RecordDigestBinding(
            record_ref=_record(binding.branch, "stage-entry-proof"),
            content_digest=proof.proof_digest,
        ),
        artifact=_artifact_binding(binding.branch, artifact_name),
        geometry_program=RecordDigestBinding(
            record_ref=binding.program_ref,
            content_digest=program.program_digest,
        ),
        component_index=_record_binding(binding.branch, "component-index"),
        function_ledger=_record_binding(binding.branch, "function-ledger"),
    )


def _exploratory_claim(
    binding: RhinoCadProgramBinding,
    *,
    artifact_name: str = "candidate.3dm",
) -> StageArtifactClaim:
    return StageArtifactClaim(
        claim_id="exploratory-candidate",
        status=StageArtifactStatus.EXPLORATORY_PRE_STAGE,
        branch=binding.branch,
        stage_id=binding.stage_id,
        proposal=_record_binding(binding.branch, "proposal"),
        preview=_artifact_binding(binding.branch, artifact_name),
    )


def _successful_raw_receipt(stage_plan):  # type: ignore[no-untyped-def]
    expected_sha256 = stage_plan.guard.expected_artifact_sha256
    inspection = _inspection(stage_plan.export_plan)
    if expected_sha256 is not None:
        inspection = replace(inspection, file_sha256=expected_sha256)
    return _verified_readback(stage_plan.export_plan, inspection)


class StageCadExecutionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = _program()
        self.binding = _binding(
            self.program,
            stage_id=DesignPhase.SCHEMATIC_DESIGN.value,
        )

    def test_exploratory_export_remains_explicitly_pre_stage(self) -> None:
        claim = _exploratory_claim(self.binding)
        with tempfile.TemporaryDirectory() as raw:
            result = prepare_stage_bound_rhino_three_dm_export(
                self.program,
                claim=claim,
                binding=self.binding,
                speculative_workspace=Path(raw),
                artifact_name="candidate.3dm",
                readback_tolerance=0.001,
            )

            self.assertIs(
                result.status,
                StageArtifactStatus.EXPLORATORY_PRE_STAGE,
            )
            self.assertFalse(result.formal_stage_bound)
            self.assertTrue(result.export_plan.script_path.is_file())
            provenance = dict(result.export_plan.expected_document_user_text)
            self.assertEqual(
                provenance["archflow:stage_claim_status"],
                StageArtifactStatus.EXPLORATORY_PRE_STAGE.value,
            )
            self.assertEqual(provenance["archflow:stage_formal_binding"], "false")

    def test_entered_claim_is_derived_as_formal(self) -> None:
        claim = _formal_claim(self.program, self.binding)
        guard = require_stage_cad_binding(
            claim,
            self.program,
            binding=self.binding,
            artifact_name="candidate.3dm",
        )
        self.assertIs(
            guard.status,
            StageArtifactStatus.STAGE_ENTERED_CANDIDATE,
        )
        self.assertTrue(guard.formal_stage_bound)
        self.assertEqual(
            guard.expected_artifact_sha256,
            claim.artifact.artifact_sha256,
        )

        with tempfile.TemporaryDirectory() as raw:
            claim = _formal_claim(self.program, self.binding)
            result = prepare_stage_bound_rhino_three_dm_export(
                self.program,
                claim=claim,
                binding=self.binding,
                speculative_workspace=Path(raw),
                artifact_name="candidate.3dm",
                readback_tolerance=0.001,
            )
            self.assertTrue(result.formal_stage_bound)
            self.assertEqual(result.export_plan.identity.binding, self.binding)

    def test_stale_program_cross_branch_artifact_and_fake_claim_fail_closed(self) -> None:
        stale_digest = _sha("stale-program")
        stale_binding = RhinoCadProgramBinding(
            program_ref=self.binding.program_ref,
            branch=self.binding.branch,
            stage_id=self.binding.stage_id,
            program_digest=stale_digest,
            design_state_digest=self.binding.design_state_digest,
            predecessor_program_digest=self.binding.predecessor_program_digest,
        )
        stale_claim = _formal_claim(self.program, stale_binding)
        with self.assertRaisesRegex(CadExecutionError, "digest differs"):
            require_stage_cad_binding(
                stale_claim,
                self.program,
                binding=stale_binding,
                artifact_name="candidate.3dm",
            )

        other_binding = _binding(
            self.program,
            branch_id="candidate-b",
            stage_id=DesignPhase.SCHEMATIC_DESIGN.value,
        )
        other_claim = _formal_claim(self.program, other_binding)
        with self.assertRaisesRegex(StageCadExportError, "branch or epoch"):
            require_stage_cad_binding(
                other_claim,
                self.program,
                binding=self.binding,
                artifact_name="candidate.3dm",
            )

        wrong_artifact = _formal_claim(
            self.program,
            self.binding,
            artifact_name="other.3dm",
        )
        with self.assertRaisesRegex(StageCadExportError, "requested CAD artifact"):
            require_stage_cad_binding(
                wrong_artifact,
                self.program,
                binding=self.binding,
                artifact_name="candidate.3dm",
            )

        claim = _formal_claim(self.program, self.binding)
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            with self.assertRaisesRegex(TypeError, "StageArtifactClaim"):
                prepare_stage_bound_rhino_three_dm_export(
                    self.program,
                    claim=claim.to_dict(),
                    binding=self.binding,
                    speculative_workspace=workspace,
                    artifact_name="candidate.3dm",
                    readback_tolerance=0.001,
                )
            self.assertEqual(tuple(workspace.iterdir()), ())

    def test_guard_roundtrip_is_strict_and_has_no_authority(self) -> None:
        claim = _formal_claim(self.program, self.binding)
        guard = require_stage_cad_binding(
            claim,
            self.program,
            binding=self.binding,
            artifact_name="candidate.3dm",
        )
        payload = guard.to_dict()
        self.assertEqual(StageCadExportGuardReceipt.from_dict(payload), guard)
        for field in (
            "stage_acceptance_authority",
            "persistence_authority",
            "canonical_write_authority",
            "materialization_authority",
            "readback_authority",
        ):
            self.assertIs(payload[field], False)

        forged = copy.deepcopy(payload)
        forged["formal_stage_bound"] = False
        with self.assertRaisesRegex(StageCadExportError, "digest changed"):
            StageCadExportGuardReceipt.from_dict(forged)

        authority = copy.deepcopy(payload)
        authority["canonical_write_authority"] = True
        with self.assertRaisesRegex(StageCadExportError, "authority"):
            StageCadExportGuardReceipt.from_dict(authority)

    def test_execute_retains_exploratory_and_formal_claims_exactly(self) -> None:
        for formal in (False, True):
            with self.subTest(formal=formal), tempfile.TemporaryDirectory() as raw:
                claim = (
                    _formal_claim(self.program, self.binding)
                    if formal
                    else _exploratory_claim(self.binding)
                )
                plan = prepare_stage_bound_rhino_three_dm_export(
                    self.program,
                    claim=claim,
                    binding=self.binding,
                    speculative_workspace=Path(raw),
                    artifact_name="candidate.3dm",
                    readback_tolerance=0.001,
                )
                raw_receipt = _successful_raw_receipt(plan)
                executable = Path(raw) / "powershell.exe"
                with patch(
                    "archflow.adapters.stage_cad_execution."
                    "execute_rhino_three_dm_export",
                    return_value=raw_receipt,
                ) as executor:
                    receipt = execute_stage_bound_rhino_three_dm_export(
                        plan,
                        powershell_executable=executable,
                    )

                self.assertIsInstance(
                    receipt,
                    StageBoundRhinoCadExecutionReceipt,
                )
                self.assertIs(receipt.status, CadExecutionStatus.SUCCEEDED)
                self.assertTrue(receipt.readback_verified)
                self.assertEqual(receipt.formal_stage_bound, formal)
                self.assertEqual(receipt.claim_digest, claim.claim_digest)
                self.assertEqual(receipt.stage_plan_digest, plan.plan_digest)
                self.assertIs(receipt.raw_execution_receipt, raw_receipt)
                executor.assert_called_once_with(
                    plan.export_plan,
                    powershell_executable=executable,
                    timeout_seconds=300.0,
                    runner=None,
                    cleanup_runner=None,
                    monotonic=None,
                    sleeper=None,
                )
                payload = receipt.to_dict()
                for field in (
                    "stage_acceptance_authority",
                    "persistence_authority",
                    "canonical_write_authority",
                    "materialization_authority",
                    "readback_authority",
                ):
                    self.assertIs(payload[field], False)

    def test_execute_rejects_guard_tamper_before_raw_execution_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            plan = prepare_stage_bound_rhino_three_dm_export(
                self.program,
                claim=_formal_claim(self.program, self.binding),
                binding=self.binding,
                speculative_workspace=workspace,
                artifact_name="candidate.3dm",
                readback_tolerance=0.001,
            )
            object.__setattr__(plan.guard, "artifact_name", "other.3dm")
            with patch(
                "archflow.adapters.stage_cad_execution."
                "execute_rhino_three_dm_export"
            ) as executor:
                with self.assertRaisesRegex(
                    StageCadExportError,
                    "requested CAD artifact",
                ):
                    execute_stage_bound_rhino_three_dm_export(
                        plan,
                        powershell_executable=workspace / "powershell.exe",
                    )
                executor.assert_not_called()
            self.assertFalse(plan.export_plan.model_path.exists())
            self.assertFalse(plan.export_plan.completion_marker_path.exists())
            self.assertFalse(plan.export_plan.host_witness_path.exists())

            with self.assertRaisesRegex(
                TypeError,
                "StageBoundRhinoCadExportPlan",
            ):
                execute_stage_bound_rhino_three_dm_export(
                    plan.export_plan,
                    powershell_executable=workspace / "powershell.exe",
                )

    def test_execute_rejects_mismatched_raw_result_and_artifact_sha(self) -> None:
        for mismatch in ("plan", "artifact"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as raw:
                workspace = Path(raw)
                plan = prepare_stage_bound_rhino_three_dm_export(
                    self.program,
                    claim=_formal_claim(self.program, self.binding),
                    binding=self.binding,
                    speculative_workspace=workspace,
                    artifact_name="candidate.3dm",
                    readback_tolerance=0.001,
                )
                raw_receipt = _successful_raw_receipt(plan)
                if mismatch == "plan":
                    raw_receipt = replace(
                        raw_receipt,
                        plan_digest=_sha("different-raw-plan"),
                    )
                    message = "exact stage plan"
                else:
                    assert raw_receipt.inspection is not None
                    raw_receipt = replace(
                        raw_receipt,
                        inspection={
                            **raw_receipt.inspection,
                            "file_sha256": _sha("different-artifact"),
                        },
                    )
                    message = "artifact SHA"
                with patch(
                    "archflow.adapters.stage_cad_execution."
                    "execute_rhino_three_dm_export",
                    return_value=raw_receipt,
                ):
                    with self.assertRaisesRegex(StageCadExportError, message):
                        execute_stage_bound_rhino_three_dm_export(
                            plan,
                            powershell_executable=workspace / "powershell.exe",
                        )


if __name__ == "__main__":
    unittest.main()
