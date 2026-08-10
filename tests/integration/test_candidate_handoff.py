from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.runtime.candidate_assembly import (
    CandidateDerivationArchive,
    CandidateDisposition,
    assemble_candidate,
    load_candidate_archive,
    persist_candidate_archive,
)
from tests.test_candidate_assembly import (
    _plan_bindings,
    _policies,
)
from tests.test_design_development import EVIDENCE, _coordinated_state


class CandidateHandoffIntegrationTests(unittest.TestCase):
    def test_rejected_candidate_keeps_full_derivation_without_head_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = FilesystemProjectRepository.initialize(
                Path(temporary) / "portfolio-project",
                project_id="portfolio-project",
                initial_state={
                    "schema": "CanonicalProjectState@1",
                    "phase": "design_development",
                    "authoritative_record_refs": [],
                    "derived_record_refs": [],
                },
            )
            run = repository.create_run("run-001")
            _, _, _, state = _coordinated_state(run)
            assembly = assemble_candidate(
                state,
                workspace_id="candidate-workspace",
                plan_id="candidate-plan",
                plan_payload={
                    "operations": [
                        {
                            "tool": "project-authored-operation",
                            "component": "primary-support",
                            "material": "primary-surface",
                            "origin": [0, 0, 0],
                        }
                    ]
                },
                plan_bindings=_plan_bindings(),
                policies=_policies(),
                evidence_refs=(EVIDENCE,),
            )
            archive = CandidateDerivationArchive(
                assembly=assembly,
                disposition=CandidateDisposition.REJECTED,
                execution=None,
                predecessor_candidate_ref=None,
                review_refs=(
                    "project://portfolio-project/runs/run-001/reviews/"
                    "hard-gate-rejection.json",
                ),
                evidence_refs=(EVIDENCE,),
                rationale=(
                    "The hard-gate rejection is retained for project repair."
                ),
            )
            before = repository.read_head()
            record = persist_candidate_archive(
                repository,
                run=run,
                archive=archive,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_CANDIDATE,
                    run_id=run.run_id,
                ),
            )
            reopened = FilesystemProjectRepository.open(repository.layout.root)
            loaded = load_candidate_archive(reopened, record)

            self.assertEqual(loaded, archive)
            self.assertEqual(reopened.read_head(), before)
            self.assertEqual(
                loaded.assembly.design_state.state_digest,
                state.state_digest,
            )
            self.assertEqual(
                loaded.assembly.plan.plan_digest,
                assembly.plan.plan_digest,
            )
            self.assertTrue(loaded.review_refs)
            self.assertFalse(
                loaded.to_dict()["canonical_write_authority"]
            )
            reopened.verify()


if __name__ == "__main__":
    unittest.main()
