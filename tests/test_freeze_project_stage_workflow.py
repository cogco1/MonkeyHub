from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project import FilesystemProjectRepository, ProjectIntegrityError
from archflow.state.design_maturity import DesignPhase
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow
from tools.freeze_project_stage_workflow import freeze_workflow


def _workflow(project_id: str = "demo") -> ProjectStageWorkflow:
    return ProjectStageWorkflow(
        project_id=project_id,
        workflow_id="building-stages-v1",
        stages=(
            ProjectStage(
                stage_id="stage-0-research",
                stage_index=0,
                phase=DesignPhase.RESEARCH_BRIEF,
                required_roles=("branch-policy", "evidence-denominator"),
                required_checks=("evidence-coverage",),
                close_obligation_id="close-stage-0",
            ),
        ),
        basis_refs=("decision:workflow-freeze",),
    )


class FreezeProjectStageWorkflowTests(unittest.TestCase):
    def test_freeze_retains_exact_workflow_without_moving_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="demo",
                initial_state={"schema": "TestState@1"},
            )
            before = repository.read_head()
            source = Path(temporary) / "workflow.json"
            source.write_text(
                __import__("json").dumps(_workflow().to_dict()),
                encoding="utf-8",
            )

            result = freeze_workflow(
                project_root=root,
                run_id="workflow-001",
                workflow_path=source,
                create_run=True,
            )

            reopened = FilesystemProjectRepository.open(root)
            self.assertEqual(reopened.read_head(), before)
            self.assertEqual(result["stage_status"], "NOT_STARTED")
            self.assertFalse(result["legacy_basis_is_acceptance"])
            self.assertFalse(result["stage_acceptance_authority"])
            self.assertTrue(result["workflow_ref"].startswith("project://demo/"))

            repeated = freeze_workflow(
                project_root=root,
                run_id="workflow-001",
                workflow_path=source,
                create_run=False,
            )
            self.assertEqual(repeated["workflow_ref"], result["workflow_ref"])
            self.assertEqual(reopened.read_head(), before)

    def test_missing_run_or_cross_project_workflow_fails_typed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            FilesystemProjectRepository.initialize(
                root,
                project_id="demo",
                initial_state={"schema": "TestState@1"},
            )
            source = Path(temporary) / "workflow.json"
            source.write_text(
                __import__("json").dumps(_workflow().to_dict()),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not exist"):
                freeze_workflow(
                    project_root=root,
                    run_id="workflow-001",
                    workflow_path=source,
                    create_run=False,
                )

            source.write_text(
                __import__("json").dumps(_workflow("other").to_dict()),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "another project"):
                freeze_workflow(
                    project_root=root,
                    run_id="workflow-001",
                    workflow_path=source,
                    create_run=True,
                )

    def test_corrupt_existing_run_is_not_reclassified_or_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "demo"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="demo",
                initial_state={"schema": "TestState@1"},
            )
            source = Path(temporary) / "workflow.json"
            source.write_text(
                __import__("json").dumps(_workflow().to_dict()),
                encoding="utf-8",
            )
            repository.create_run("workflow-001", base=repository.read_head())
            repository.layout.run("workflow-001").manifest.write_text(
                "not-json",
                encoding="utf-8",
            )

            with self.assertRaises(ProjectIntegrityError):
                freeze_workflow(
                    project_root=root,
                    run_id="workflow-001",
                    workflow_path=source,
                    create_run=True,
                )


if __name__ == "__main__":
    unittest.main()
