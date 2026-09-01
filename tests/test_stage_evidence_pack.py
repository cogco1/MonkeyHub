from __future__ import annotations

import copy
import unittest

from archflow.capabilities import (
    stage_evidence_pack as legacy_stage_evidence_pack,
)
from archflow.evidence import stage_pack as canonical_stage_evidence_pack
from archflow.evidence.stage_pack import (
    CrossRunStagePackPredecessor,
    StageArtifactBinding,
    StageArtifactRole,
    StageClosureSummary,
    StageEvidenceBinding,
    StageEvidenceGap,
    StageEvidenceGapKind,
    StageEvidenceGapSeverity,
    StageEvidencePack,
    StageEvidencePackError,
    StageEvidenceRole,
    StagePackCompilationStatus,
    StagePackPredecessor,
    compile_stage_evidence_pack,
)
from archflow.project.refs import (
    BranchRef,
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)


_BASE_DIGEST = "a" * 64
_PROGRAM_DIGEST = "b" * 64


def record(
    name: str,
    *,
    area: str = "branches/historical/records",
    digest: str = "c" * 64,
    project_id: str = "pantheon-reconstruction",
    run_id: str = "stage-evidence-001",
) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=project_id,
        relative_path=f"runs/{run_id}/{area}/{name}-{digest}.json",
        sha256=digest,
    )


def artifact(
    name: str = "pantheon-candidate",
    *,
    digest: str = "d" * 64,
) -> ProjectArtifactRef:
    return ProjectArtifactRef(
        project_id="pantheon-reconstruction",
        artifact_id=name,
        relative_path=f"objects/sha256/{digest[:2]}/{digest}",
        sha256=digest,
        media_type="model/vnd.3dm",
    )


def run() -> RunRef:
    return RunRef(
        project_id="pantheon-reconstruction",
        run_id="stage-evidence-001",
        base=ProjectVersionRef(
            project_id="pantheon-reconstruction",
            version=0,
            state_sha256=_BASE_DIGEST,
        ),
    )


def branch() -> BranchRef:
    return BranchRef(run=run(), branch_id="historical", epoch=0)


def binding(role: StageEvidenceRole) -> StageEvidenceBinding:
    area = "branches/historical/records"
    if role is StageEvidenceRole.BRANCH_SELECTION:
        area = "records"
    elif role is StageEvidenceRole.REVIEW:
        area = "reviews"
    return StageEvidenceBinding(
        role=role,
        ref=record(role.value, area=area),
    )


def all_complete_bindings() -> tuple[StageEvidenceBinding, ...]:
    roles = (
        StageEvidenceRole.BRANCH_SELECTION,
        StageEvidenceRole.BRANCH_SCOPE,
        StageEvidenceRole.DECISION_UNIVERSE,
        StageEvidenceRole.BASIS_INDEX,
        StageEvidenceRole.DEPENDENCY_LEDGER,
        StageEvidenceRole.EVIDENCE_SUFFICIENCY,
        StageEvidenceRole.DESIGN_STATE,
        StageEvidenceRole.GEOMETRY_PROGRAM,
        StageEvidenceRole.MODEL_INSPECTION,
        StageEvidenceRole.STAGE_GATE,
        StageEvidenceRole.STAGE_CONVERGENCE,
        StageEvidenceRole.REVIEW,
    )
    return tuple(sorted((binding(role) for role in roles), key=lambda item: item.identity))


def closed() -> StageClosureSummary:
    return StageClosureSummary(
        evidence_sufficient=True,
        dependencies_closed=True,
        hard_gates_passed=True,
        stage_ready=True,
        model_artifact_current=True,
    )


def cross_run_predecessor(
    *,
    project_id: str = "pantheon-reconstruction",
    run_id: str = "reconstruction-004",
    base_version: int = 0,
    base_digest: str = _BASE_DIGEST,
    branch_id: str = "historical",
    pack_project_id: str | None = None,
    pack_run_id: str | None = None,
    pack_branch_id: str | None = None,
    stage_index: int = 3,
) -> CrossRunStagePackPredecessor:
    predecessor_run = RunRef(
        project_id=project_id,
        run_id=run_id,
        base=ProjectVersionRef(
            project_id=project_id,
            version=base_version,
            state_sha256=base_digest,
        ),
    )
    predecessor_branch = BranchRef(
        run=predecessor_run,
        branch_id=branch_id,
        epoch=0,
    )
    ref_project_id = pack_project_id or project_id
    ref_run_id = pack_run_id or run_id
    ref_branch_id = pack_branch_id or branch_id
    return CrossRunStagePackPredecessor(
        stage_id=f"stage-{stage_index}",
        stage_index=stage_index,
        branch=predecessor_branch,
        pack_ref=ProjectRecordRef(
            project_id=ref_project_id,
            relative_path=(
                f"runs/{ref_run_id}/branches/{ref_branch_id}/records/"
                f"stage-evidence-pack-stage-{stage_index}-{'e' * 64}.json"
            ),
            sha256="e" * 64,
        ),
        program_digest="f" * 64,
    )


def compile_cross_run_successor(
    predecessor: CrossRunStagePackPredecessor,
    *,
    stage_index: int = 4,
) -> StageEvidencePack:
    current_run = RunRef(
        project_id="pantheon-reconstruction",
        run_id="reconstruction-005",
        base=ProjectVersionRef(
            project_id="pantheon-reconstruction",
            version=0,
            state_sha256=_BASE_DIGEST,
        ),
    )
    current_branch = BranchRef(
        run=current_run,
        branch_id="historical",
        epoch=0,
    )
    return compile_stage_evidence_pack(
        project_id="pantheon-reconstruction",
        run=current_run,
        branch=current_branch,
        scope_ref=record("scope", run_id="reconstruction-005"),
        stage_id=f"stage-{stage_index}",
        stage_index=stage_index,
        revision=1,
        program_digest=_PROGRAM_DIGEST,
        contract_ref=record(
            "contract",
            area="records",
            run_id="reconstruction-005",
        ),
        bindings=(
            StageEvidenceBinding(
                role=StageEvidenceRole.BRANCH_SCOPE,
                ref=record("branch-scope", run_id="reconstruction-005"),
            ),
        ),
        artifacts=(),
        gaps=(),
        closure=StageClosureSummary(
            evidence_sufficient=False,
            dependencies_closed=False,
            hard_gates_passed=False,
            stage_ready=False,
            model_artifact_current=False,
        ),
        predecessor=predecessor,
    )


class StageEvidencePackContractTests(unittest.TestCase):
    def test_legacy_import_is_thin_canonical_facade(self) -> None:
        for name in legacy_stage_evidence_pack.__all__:
            self.assertIs(
                getattr(legacy_stage_evidence_pack, name),
                getattr(canonical_stage_evidence_pack, name),
                name,
            )

    def test_complete_pack_round_trips_without_acquiring_authority(self) -> None:
        pack = compile_stage_evidence_pack(
            project_id="pantheon-reconstruction",
            run=run(),
            branch=branch(),
            scope_ref=record("scope"),
            stage_id="stage-0",
            stage_index=0,
            revision=1,
            program_digest=_PROGRAM_DIGEST,
            contract_ref=record("contract", area="records"),
            bindings=all_complete_bindings(),
            artifacts=(
                StageArtifactBinding(
                    role=StageArtifactRole.CAD_MODEL,
                    ref=artifact(),
                    program_digest=_PROGRAM_DIGEST,
                ),
            ),
            gaps=(),
            closure=closed(),
        )

        self.assertIs(pack.compilation_status, StagePackCompilationStatus.COMPLETE)
        self.assertEqual(StageEvidencePack.from_dict(pack.to_dict()), pack)
        self.assertEqual(len(pack.pack_digest), 64)
        self.assertFalse(pack.to_dict()["selection_authority"])
        self.assertFalse(pack.to_dict()["evidence_authority"])
        self.assertFalse(pack.to_dict()["stage_acceptance_authority"])
        self.assertFalse(pack.to_dict()["canonical_write_authority"])
        self.assertEqual(
            "StageEvidencePack@1",
            pack.to_dict()["schema"],
        )
        self.assertEqual(
            "50a6f40e1a9b4aa48fc520e52e321373b911972087fda790f85b90881d9baa7e",
            pack.pack_digest,
        )

    def test_blocking_gap_keeps_pack_incomplete(self) -> None:
        pack = compile_stage_evidence_pack(
            project_id="pantheon-reconstruction",
            run=run(),
            branch=branch(),
            scope_ref=record("scope"),
            stage_id="stage-0",
            stage_index=0,
            revision=1,
            program_digest=_PROGRAM_DIGEST,
            contract_ref=record("contract", area="records"),
            bindings=(binding(StageEvidenceRole.BRANCH_SCOPE),),
            artifacts=(),
            gaps=(
                StageEvidenceGap(
                    gap_id="authority-pending",
                    kind=StageEvidenceGapKind.AUTHORITY_PENDING,
                    severity=StageEvidenceGapSeverity.BLOCKING,
                    description="No durable human authority receipt exists.",
                    decision_refs=("decision:wall-thickness",),
                    remediation="Retain a scoped approval receipt.",
                ),
            ),
            closure=StageClosureSummary(
                evidence_sufficient=False,
                dependencies_closed=False,
                hard_gates_passed=True,
                stage_ready=False,
                model_artifact_current=False,
            ),
        )

        self.assertIs(
            pack.compilation_status,
            StagePackCompilationStatus.INCOMPLETE,
        )
        self.assertEqual(pack.blocking_gap_ids, ("authority-pending",))

    def test_stage_successor_requires_exact_previous_pack(self) -> None:
        with self.assertRaisesRegex(
            StageEvidencePackError,
            "Stage N-1",
        ):
            compile_stage_evidence_pack(
                project_id="pantheon-reconstruction",
                run=run(),
                branch=branch(),
                scope_ref=record("scope"),
                stage_id="stage-1",
                stage_index=1,
                revision=1,
                program_digest=_PROGRAM_DIGEST,
                contract_ref=record("contract", area="records"),
                bindings=(binding(StageEvidenceRole.BRANCH_SCOPE),),
                artifacts=(),
                gaps=(),
                closure=StageClosureSummary(
                    evidence_sufficient=False,
                    dependencies_closed=False,
                    hard_gates_passed=False,
                    stage_ready=False,
                    model_artifact_current=False,
                ),
            )

        predecessor = StagePackPredecessor(
            stage_id="stage-0",
            stage_index=0,
            pack_ref=record("stage-evidence-pack-stage-0"),
            program_digest="e" * 64,
        )
        pack = compile_stage_evidence_pack(
            project_id="pantheon-reconstruction",
            run=run(),
            branch=branch(),
            scope_ref=record("scope"),
            stage_id="stage-1",
            stage_index=1,
            revision=1,
            program_digest=_PROGRAM_DIGEST,
            contract_ref=record("contract", area="records"),
            bindings=(binding(StageEvidenceRole.BRANCH_SCOPE),),
            artifacts=(),
            gaps=(),
            closure=StageClosureSummary(
                evidence_sufficient=False,
                dependencies_closed=False,
                hard_gates_passed=False,
                stage_ready=False,
                model_artifact_current=False,
            ),
            predecessor=predecessor,
        )
        self.assertEqual(pack.predecessor, predecessor)
        legacy_payload = predecessor.to_dict()
        self.assertEqual(
            legacy_payload,
            {
                "schema": "StagePackPredecessor@1",
                "stage_id": "stage-0",
                "stage_index": 0,
                "pack_ref": {
                    "project_id": "pantheon-reconstruction",
                    "relative_path": (
                        "runs/stage-evidence-001/branches/historical/records/"
                        f"stage-evidence-pack-stage-0-{'c' * 64}.json"
                    ),
                    "sha256": "c" * 64,
                    "media_type": "application/json",
                },
                "program_digest": "e" * 64,
            },
        )
        restored = StageEvidencePack.from_dict(pack.to_dict())
        self.assertEqual(restored, pack)
        self.assertIsInstance(restored.predecessor, StagePackPredecessor)

    def test_cross_run_predecessor_accepts_004_to_005_and_round_trips(self) -> None:
        predecessor = cross_run_predecessor()
        pack = compile_cross_run_successor(predecessor)

        self.assertEqual(pack.predecessor, predecessor)
        self.assertEqual(
            predecessor.to_dict()["schema"],
            "CrossRunStagePackPredecessor@1",
        )
        restored = StageEvidencePack.from_dict(pack.to_dict())
        self.assertEqual(restored, pack)
        self.assertIsInstance(
            restored.predecessor,
            CrossRunStagePackPredecessor,
        )
        self.assertEqual(
            "6f4e096528fd5e95cf9f42618e1984a068150fcd3bb6e81491d490dcceb71ae5",
            pack.pack_digest,
        )

    def test_cross_run_predecessor_rejects_wrong_canonical_base(self) -> None:
        cases = (
            ("digest", cross_run_predecessor(base_digest="9" * 64)),
            ("version", cross_run_predecessor(base_version=1)),
        )
        for label, predecessor in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    StageEvidencePackError,
                    "exact canonical base",
                ):
                    compile_cross_run_successor(predecessor)

    def test_cross_run_predecessor_rejects_wrong_branch(self) -> None:
        predecessor = cross_run_predecessor(branch_id="alternate")

        with self.assertRaisesRegex(StageEvidencePackError, "branch_id"):
            compile_cross_run_successor(predecessor)

    def test_cross_run_predecessor_rejects_wrong_project(self) -> None:
        cases = (
            (
                "predecessor branch project",
                cross_run_predecessor(project_id="other-project"),
            ),
            (
                "pack ref project",
                cross_run_predecessor(pack_project_id="other-project"),
            ),
        )
        for label, predecessor in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    StageEvidencePackError,
                    "another project",
                ):
                    compile_cross_run_successor(predecessor)

    def test_cross_run_predecessor_rejects_wrong_pack_path(self) -> None:
        cases = (
            (
                "wrong run",
                cross_run_predecessor(pack_run_id="reconstruction-003"),
            ),
            (
                "wrong branch",
                cross_run_predecessor(pack_branch_id="alternate"),
            ),
        )
        for label, predecessor in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    StageEvidencePackError,
                    "predecessor branch run path",
                ):
                    compile_cross_run_successor(predecessor)

    def test_cross_run_predecessor_rejects_same_run(self) -> None:
        predecessor = cross_run_predecessor(run_id="reconstruction-005")

        with self.assertRaisesRegex(StageEvidencePackError, "different run"):
            compile_cross_run_successor(predecessor)

    def test_cross_run_predecessor_rejects_nonadjacent_stage(self) -> None:
        predecessor = cross_run_predecessor(stage_index=2)

        with self.assertRaisesRegex(
            StageEvidencePackError,
            "immediately previous stage",
        ):
            compile_cross_run_successor(predecessor)

    def test_cross_branch_binding_and_model_program_drift_fail_closed(self) -> None:
        wrong_branch = StageEvidenceBinding(
            role=StageEvidenceRole.BASIS_INDEX,
            ref=record("basis", area="branches/gothic/records"),
        )
        with self.assertRaisesRegex(StageEvidencePackError, "outside its P036 area"):
            compile_stage_evidence_pack(
                project_id="pantheon-reconstruction",
                run=run(),
                branch=branch(),
                scope_ref=record("scope"),
                stage_id="stage-0",
                stage_index=0,
                revision=1,
                program_digest=_PROGRAM_DIGEST,
                contract_ref=record("contract", area="records"),
                bindings=(wrong_branch,),
                artifacts=(),
                gaps=(),
                closure=StageClosureSummary(
                    evidence_sufficient=False,
                    dependencies_closed=False,
                    hard_gates_passed=False,
                    stage_ready=False,
                    model_artifact_current=False,
                ),
            )

        with self.assertRaisesRegex(
            StageEvidencePackError,
            "current geometry program",
        ):
            compile_stage_evidence_pack(
                project_id="pantheon-reconstruction",
                run=run(),
                branch=branch(),
                scope_ref=record("scope"),
                stage_id="stage-0",
                stage_index=0,
                revision=1,
                program_digest=_PROGRAM_DIGEST,
                contract_ref=record("contract", area="records"),
                bindings=(binding(StageEvidenceRole.BRANCH_SCOPE),),
                artifacts=(
                    StageArtifactBinding(
                        role=StageArtifactRole.CAD_MODEL,
                        ref=artifact(),
                        program_digest="f" * 64,
                    ),
                ),
                gaps=(),
                closure=StageClosureSummary(
                    evidence_sufficient=False,
                    dependencies_closed=False,
                    hard_gates_passed=False,
                    stage_ready=False,
                    model_artifact_current=True,
                ),
            )

    def test_serialized_authority_flags_fail_closed(self) -> None:
        pack = compile_stage_evidence_pack(
            project_id="pantheon-reconstruction",
            run=run(),
            branch=branch(),
            scope_ref=record("scope"),
            stage_id="stage-0",
            stage_index=0,
            revision=1,
            program_digest=_PROGRAM_DIGEST,
            contract_ref=record("contract", area="records"),
            bindings=(binding(StageEvidenceRole.BRANCH_SCOPE),),
            artifacts=(),
            gaps=(),
            closure=StageClosureSummary(
                evidence_sufficient=False,
                dependencies_closed=False,
                hard_gates_passed=False,
                stage_ready=False,
                model_artifact_current=False,
            ),
        )
        payload = copy.deepcopy(pack.to_dict())
        payload["evidence_authority"] = True
        with self.assertRaisesRegex(StageEvidencePackError, "acquired authority"):
            StageEvidencePack.from_dict(payload)

        closure_authority = copy.deepcopy(pack.to_dict())
        closure_authority["closure_authority"] = True
        with self.assertRaisesRegex(StageEvidencePackError, "schema drifted"):
            StageEvidencePack.from_dict(closure_authority)


if __name__ == "__main__":
    unittest.main()
