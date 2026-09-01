from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archflow.control.stage_artifacts import (
    ArtifactShaBinding,
    RecordDigestBinding,
    StageArtifactClaim,
    StageArtifactClaimError,
    StageArtifactStatus,
    StageArtifactVerificationDenominator,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.design_maturity import (
    DesignPhase,
    DeliverableRole,
    PhaseGateReceipt,
    StageEntryProof,
)
from archflow.state.model import ArtifactRef


def _sha(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _predecessor_branch(
    *,
    project_id: str = "project-a",
    run_id: str = "run-a",
    branch_id: str = "option-a",
    epoch: int = 4,
) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=run_id,
            base=ProjectVersionRef(
                project_id=project_id,
                version=2,
                state_sha256=_sha(f"{project_id}-base"),
            ),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def _proof() -> StageEntryProof:
    predecessor = _predecessor_branch()
    successor = replace(predecessor, epoch=predecessor.epoch + 1)
    gate = PhaseGateReceipt(
        receipt_id="research-exit",
        request_id="enter-programming",
        branch=predecessor,
        base_state_digest=_sha("research-state"),
        from_phase=DesignPhase.RESEARCH_BRIEF,
        to_phase=DesignPhase.PROGRAMMING,
        required_roles=(DeliverableRole.RESEARCH_BRIEF,),
        accepted_deliverable_refs=("deliverable:research-brief",),
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


def _record(branch: BranchRef, name: str) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/"
            f"records/{name}.json"
        ),
        sha256=_sha(f"record:{name}"),
    )


def _binding(branch: BranchRef, name: str) -> RecordDigestBinding:
    return RecordDigestBinding(
        record_ref=_record(branch, name),
        content_digest=_sha(f"content:{name}"),
    )


def _artifact(branch: BranchRef, name: str) -> ArtifactShaBinding:
    digest = _sha(f"artifact:{name}")
    ref = ArtifactRef(
        artifact_id=name,
        uri=(
            f"project://{branch.run.project_id}/runs/{branch.run.run_id}/"
            f"branches/{branch.branch_id}/artifacts/{name}.3dm"
        ),
        media_type="model/vnd.rhino",
        sha256=digest,
    )
    return ArtifactShaBinding(ref, digest)


def _entered_kwargs() -> dict[str, object]:
    proof = _proof()
    branch = proof.successor_branch
    proof_record = RecordDigestBinding(
        record_ref=_record(branch, "stage-entry-proof"),
        content_digest=proof.proof_digest,
    )
    return {
        "claim_id": "stage-artifact-a",
        "status": StageArtifactStatus.STAGE_ENTERED_CANDIDATE,
        "branch": branch,
        "stage_id": DesignPhase.PROGRAMMING.value,
        "stage_entry_proof": proof,
        "stage_entry_proof_record": proof_record,
        "artifact": _artifact(branch, "candidate-model"),
        "geometry_program": _binding(branch, "geometry-program"),
        "component_index": _binding(branch, "component-index"),
        "function_ledger": _binding(branch, "function-ledger"),
    }


class StageArtifactClaimTests(unittest.TestCase):
    def test_exploratory_claim_allows_proposal_and_preview_only(self) -> None:
        branch = _predecessor_branch()
        claim = StageArtifactClaim(
            claim_id="exploration-a",
            status=StageArtifactStatus.EXPLORATORY_PRE_STAGE,
            branch=branch,
            stage_id=DesignPhase.RESEARCH_BRIEF.value,
            proposal=_binding(branch, "proposal"),
            preview=_artifact(branch, "preview"),
            viewer_refs=("viewer:axon",),
            diagnostic_refs=("diagnostic:open-functions",),
        )

        self.assertEqual(StageArtifactClaim.from_dict(claim.to_dict()), claim)
        payload = claim.to_dict()
        for field in (
            "stage_acceptance_authority",
            "persistence_authority",
            "canonical_write_authority",
            "materialization_authority",
            "readback_authority",
        ):
            self.assertIs(payload[field], False)

    def test_missing_proof_cannot_claim_stage_entry(self) -> None:
        branch = _predecessor_branch()
        with self.assertRaisesRegex(StageArtifactClaimError, "proof"):
            StageArtifactClaim(
                claim_id="unproved",
                status=StageArtifactStatus.STAGE_ENTERED_CANDIDATE,
                branch=branch,
                stage_id=DesignPhase.RESEARCH_BRIEF.value,
                artifact=_artifact(branch, "candidate"),
            )

    def test_viewer_and_diagnostic_refs_never_upgrade_status(self) -> None:
        branch = _predecessor_branch()
        with self.assertRaisesRegex(StageArtifactClaimError, "status"):
            StageArtifactClaim(
                claim_id="display-only",
                status=StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE,
                branch=branch,
                stage_id=DesignPhase.RESEARCH_BRIEF.value,
                viewer_refs=("viewer:looks-complete",),
                diagnostic_refs=("diagnostic:all-green",),
            )

    def test_stage_entered_claim_is_exact_and_deterministic(self) -> None:
        claim = StageArtifactClaim(**_entered_kwargs())
        reloaded = StageArtifactClaim.from_dict(claim.to_dict())

        self.assertEqual(reloaded, claim)
        self.assertEqual(reloaded.claim_digest, claim.claim_digest)
        self.assertEqual(claim.to_dict(), reloaded.to_dict())
        self.assertEqual(
            claim.stage_entry_proof_record.content_digest,
            claim.stage_entry_proof.proof_digest,
        )

    def test_stage3_verified_requires_exact_verification_denominator(self) -> None:
        values = _entered_kwargs()
        branch = values["branch"]
        topology = (_binding(branch, "relation-topology"),)
        realization = (_binding(branch, "relation-realization"),)
        receipts = (_binding(branch, "functional-verification"),)
        function_requirements = _binding(branch, "function-requirements")
        stage_requirement_profile = _binding(
            branch,
            "stage-requirement-profile",
        )
        stage_checks = (_binding(branch, "stage-check-01"),)
        baseline_sources = _binding(branch, "baseline-sources")
        baseline_coverage = _binding(branch, "baseline-coverage")
        stage_closure = _binding(branch, "stage-closure")
        stage_subject_inventory = _binding(
            branch,
            "stage-subject-inventory",
        )
        readback = _binding(branch, "cad-readback")
        denominator = StageArtifactVerificationDenominator(
            stage_subject_inventory_digest=(
                stage_subject_inventory.content_digest
            ),
            component_index_digest=values["component_index"].content_digest,
            stage_requirement_profile_digest=(
                stage_requirement_profile.content_digest
            ),
            baseline_source_set_digest=baseline_sources.content_digest,
            baseline_coverage_digest=baseline_coverage.content_digest,
            stage_closure_digest=stage_closure.content_digest,
            function_ledger_digest=values["function_ledger"].content_digest,
            function_relation_requirement_digest=(
                function_requirements.content_digest
            ),
            program_digest=values["geometry_program"].content_digest,
            readback_digest=readback.content_digest,
            topology_source_digests=tuple(
                item.content_digest for item in topology
            ),
            topology_graph_digests=(_sha("topology-graph"),),
            realization_source_digests=tuple(
                item.content_digest for item in realization
            ),
            realization_receipt_digests=tuple(
                item.content_digest for item in receipts
            ),
            stage_check_receipt_digests=tuple(
                item.content_digest for item in stage_checks
            ),
        )
        values.update(
            status=StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE,
            stage_subject_inventory=stage_subject_inventory,
            function_relation_requirements=function_requirements,
            stage_requirement_profile=stage_requirement_profile,
            baseline_sources=baseline_sources,
            baseline_coverage=baseline_coverage,
            stage_closure=stage_closure,
            stage_checks=stage_checks,
            relation_topology=topology,
            cad_readback=readback,
            relation_realization=realization,
            functional_verification=receipts,
            verification_denominator=denominator,
        )
        claim = StageArtifactClaim(**values)

        self.assertEqual(
            claim.status,
            StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE,
        )
        self.assertEqual(StageArtifactClaim.from_dict(claim.to_dict()), claim)
        for missing in (
            "verification_denominator",
            "stage_subject_inventory",
            "function_relation_requirements",
            "stage_requirement_profile",
            "baseline_sources",
            "baseline_coverage",
            "stage_closure",
            "stage_checks",
            "relation_topology",
            "cad_readback",
            "relation_realization",
            "functional_verification",
        ):
            incomplete = dict(values)
            incomplete[missing] = (
                ()
                if missing
                in {
                    "relation_topology",
                    "relation_realization",
                    "functional_verification",
                    "stage_checks",
                }
                else None
            )
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(
                    StageArtifactClaimError,
                    "verification evidence is incomplete",
                ):
                    StageArtifactClaim(**incomplete)

        stale = dict(values)
        stale["baseline_coverage"] = _binding(
            branch,
            "stale-baseline-coverage",
        )
        with self.assertRaisesRegex(
            StageArtifactClaimError,
            "exact denominator",
        ):
            StageArtifactClaim(**stale)

    def test_exact_lineage_rejects_stale_cross_scope_and_wrong_stage(self) -> None:
        base = _entered_kwargs()
        proof = base["stage_entry_proof"]
        assert isinstance(proof, StageEntryProof)
        cases: list[tuple[str, dict[str, object], str]] = []
        cases.append((
            "stale epoch",
            {"branch": replace(base["branch"], epoch=base["branch"].epoch + 1)},
            "stale",
        ))
        cases.append((
            "cross branch",
            {"branch": replace(base["branch"], branch_id="option-b")},
            "branch",
        ))
        foreign_run = _predecessor_branch(run_id="run-b", epoch=5)
        cases.append(("cross run", {"branch": foreign_run}, "run or branch"))
        foreign_project = _predecessor_branch(project_id="project-b", epoch=5)
        cases.append(("cross project", {"branch": foreign_project}, "projects"))
        cases.append((
            "wrong stage",
            {"stage_id": DesignPhase.SCHEMATIC_DESIGN.value},
            "stage disagree",
        ))

        for label, replacement, message in cases:
            values = dict(base)
            values.update(replacement)
            with self.subTest(case=label):
                with self.assertRaisesRegex(Exception, message):
                    StageArtifactClaim(**values)

    def test_every_bound_record_and_artifact_is_exact_scope(self) -> None:
        base = _entered_kwargs()
        branch = base["branch"]
        assert isinstance(branch, BranchRef)
        cross_branch = replace(branch, branch_id="option-b")
        for field in (
            "stage_entry_proof_record",
            "geometry_program",
            "component_index",
            "function_ledger",
        ):
            values = dict(base)
            values[field] = _binding(cross_branch, field)
            with self.subTest(field=field):
                with self.assertRaisesRegex(StageArtifactClaimError, "branch"):
                    StageArtifactClaim(**values)

        values = dict(base)
        values["artifact"] = _artifact(cross_branch, "candidate-model")
        with self.assertRaisesRegex(StageArtifactClaimError, "branch"):
            StageArtifactClaim(**values)

    def test_proof_record_digest_and_checkpoint_cannot_form_fixed_point(self) -> None:
        values = _entered_kwargs()
        proof = values["stage_entry_proof"]
        assert isinstance(proof, StageEntryProof)
        values["stage_entry_proof_record"] = RecordDigestBinding(
            _record(proof.successor_branch, "wrong-proof"),
            _sha("not-the-proof"),
        )
        with self.assertRaisesRegex(StageArtifactClaimError, "wrong proof"):
            StageArtifactClaim(**values)

        values = _entered_kwargs()
        values["stage_entry_proof_record"] = RecordDigestBinding(
            proof.stage_exit_checkpoint_ref,
            proof.proof_digest,
        )
        with self.assertRaisesRegex(StageArtifactClaimError, "fixed point"):
            StageArtifactClaim(**values)

        self.assertNotIn("claim_digest", proof.to_dict())

    def test_artifact_ref_and_explicit_sha_must_match(self) -> None:
        artifact = _artifact(_proof().successor_branch, "candidate")
        with self.assertRaisesRegex(StageArtifactClaimError, "disagree"):
            ArtifactShaBinding(artifact.artifact_ref, _sha("other"))

    def test_strict_roundtrip_rejects_tamper_and_schema_drift(self) -> None:
        claim = StageArtifactClaim(**_entered_kwargs())

        authority = copy.deepcopy(claim.to_dict())
        authority["canonical_write_authority"] = True
        with self.assertRaisesRegex(StageArtifactClaimError, "authority"):
            StageArtifactClaim.from_dict(authority)

        extra = copy.deepcopy(claim.to_dict())
        extra["viewer_approved"] = True
        with self.assertRaisesRegex(StageArtifactClaimError, "schema drifted"):
            StageArtifactClaim.from_dict(extra)

        tampered = copy.deepcopy(claim.to_dict())
        tampered["artifact"]["artifact_ref"]["sha256"] = _sha("tampered")
        tampered["artifact"]["artifact_sha256"] = _sha("tampered")
        with self.assertRaisesRegex(StageArtifactClaimError, "digest changed"):
            StageArtifactClaim.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
