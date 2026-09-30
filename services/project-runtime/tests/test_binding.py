"""Binding one project: which project, which HEAD, which run answers for it."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import PROJECT_STAGE_WORKFLOW, STATE_RECORD
from archflow.project.refs import record_ref_from_uri
from archflow.state.stage_workflow import DesignPhase

from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import (
    HARNESS_RUN_ID,
    PROJECT_ID,
    REFERENCE_RUN_ID,
    add_harness_run,
    freeze_workflow,
    make_empty_project,
    make_project,
)


class UnboundProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.settings = StudioSettings(cad_export="off", project_dir=self.root / "no-project")

    def test_open_refuses_a_directory_that_is_not_a_project(self) -> None:
        with self.assertRaises(StudioError) as raised:
            ProjectBinding.open(self.settings)

        self.assertEqual(raised.exception.code, "PROJECT_NOT_BOUND")
        self.assertEqual(raised.exception.status, 503)
        # The kernel's reason, and the knob that names the project. Not the
        # directory: a refusal never publishes the server's own layout.
        self.assertIn("no local project", raised.exception.detail)
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", raised.exception.detail)
        self.assertNotIn(str(self.root), raised.exception.detail)

    def test_project_route_answers_503_in_the_error_shape(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.get("/api/project")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["code"], "PROJECT_NOT_BOUND")
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", payload["detail"])
        self.assertNotIn(str(self.root), payload["detail"])


class BoundProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)

    def test_project_route_reports_the_exact_issue_and_reference_run(
        self,
    ) -> None:
        head = self.repository.read_head()

        response = self.client.get("/api/project")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["projectId"], PROJECT_ID)
        self.assertEqual(payload["projectDir"], str(self.root / PROJECT_ID))
        self.assertEqual(payload["published"]["version"], 0)
        self.assertEqual(payload["published"]["stateSha256"], head.state_sha256)
        self.assertEqual(payload["referenceRun"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(payload["referenceRun"]["baseVersion"], 0)
        self.assertEqual(
            payload["referenceRun"]["baseSha256"], head.state_sha256
        )

    def test_modeling_entry_preserves_an_existing_design_and_refuses_another_project(self) -> None:
        before = self.repository.layout.authored_record.read_bytes()
        state = self.client.get("/api/state").json()
        self.assertEqual(self.client.post("/api/project/modeling", json={"projectId": PROJECT_ID}).json(),
                         {"projectId": PROJECT_ID, "initialized": False})
        self.assertEqual(self.repository.layout.authored_record.read_bytes(), before)
        self.assertEqual(self.client.get("/api/state").json(), state)
        response = self.client.post("/api/project/modeling", json={"projectId": "another-project"})
        self.assertEqual(response.status_code, 404, response.text)

    def test_initial_preparation_does_not_read_stale_authored_inputs_over_a_retained_model(self) -> None:
        state = self.client.get("/api/state").json()
        self.repository.layout.authored_record.write_bytes(b"unfinished authored edit")
        response = self.client.post("/api/project/modeling", json={"projectId": PROJECT_ID})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["initialized"])
        self.assertEqual(self.client.get("/api/state").json(), state)
        self.assertEqual(self.repository.layout.authored_record.read_bytes(), b"unfinished authored edit")

    def test_the_binding_is_opened_once_and_kept(self) -> None:
        first = self.client.get("/api/project")
        second = self.client.get("/api/project")

        self.assertEqual(first.json(), second.json())
        binding = self.client.app.state.binding
        self.assertIsInstance(binding, ProjectBinding)
        self.assertEqual(binding.project_id, PROJECT_ID)

    def test_the_rule_skips_a_newer_harness_run(self) -> None:
        add_harness_run(self.repository)

        response = self.client.get("/api/project")

        self.assertEqual(
            response.json()["referenceRun"]["runId"], REFERENCE_RUN_ID
        )

    def test_the_rule_names_its_source_and_receipt(self) -> None:
        binding = ProjectBinding.open(self.settings)

        reference = binding.reference_run()

        self.assertEqual(reference.source, "rule")
        self.assertEqual(reference.run.run_id, REFERENCE_RUN_ID)
        self.assertIsNotNone(reference.receipt)
        # The raw kernel record, so this is the receipt's own schema field.
        self.assertEqual(reference.receipt["schema"], "RunnerRunReceipt@3")

    def test_reference_selection_reads_receipts_then_verifies_only_the_selected_state(self) -> None:
        add_harness_run(self.repository)
        binding = ProjectBinding.open(self.settings)
        with patch.object(binding.repository, "load_json", wraps=binding.repository.load_json) as load:
            reference = binding.reference_run()
        self.assertEqual(reference.run.run_id, REFERENCE_RUN_ID)
        self.assertNotIn(STATE_RECORD, [call.args[0].record_kind for call in load.call_args_list])
        ref, _ = binding.exact_state_record(reference)
        self.assertEqual(ref, record_ref_from_uri(reference.receipt["state_record_ref"], PROJECT_ID))
        binding.repository.layout.resolve_record(ref).write_bytes(b"broken selected state")
        with self.assertRaises(StudioError) as broken:
            binding.exact_state_record(reference)
        self.assertEqual(broken.exception.code, "REFERENCE_STATE_NOT_EXACT")

    def test_frozen_phase_reads_only_project_workflows_and_preserves_stage_order_and_ambiguity(self) -> None:
        add_harness_run(self.repository)
        uri = freeze_workflow(self.repository, phase=DesignPhase.SCHEMATIC_DESIGN)
        ref = record_ref_from_uri(uri, PROJECT_ID)
        payload = self.repository.load_json(ref)
        first = payload["stages"][0]
        self.repository.put_json(run=self.repository.load_run("workflow-001"),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="workflow-001"),
            record_kind=PROJECT_STAGE_WORKFLOW,
            payload={**payload, "stages": [first, {**first, "stage_id": "stage-1", "stage_index": 1,
                                                   "phase": DesignPhase.DESIGN_DEVELOPMENT.value}]})
        binding = ProjectBinding.open(self.settings)
        with patch.object(binding.repository, "load_json", wraps=binding.repository.load_json) as load:
            self.assertEqual(binding.frozen_workflow_first_phase(), DesignPhase.SCHEMATIC_DESIGN.value)
        self.assertEqual({call.args[0].record_kind for call in load.call_args_list}, {PROJECT_STAGE_WORKFLOW})
        freeze_workflow(self.repository, phase=DesignPhase.SCHEMATIC_DESIGN, run_id="a-same-phase")
        self.assertEqual(binding.frozen_workflow_first_phase(), DesignPhase.SCHEMATIC_DESIGN.value)
        freeze_workflow(self.repository, phase=DesignPhase.DESIGN_DEVELOPMENT, run_id="z-competing-phase")
        self.assertIsNone(binding.frozen_workflow_first_phase(), "neither run order nor recency chooses between different ladders")

    def test_frozen_phase_verifies_selected_payload_and_recovers_after_restoration(self) -> None:
        uri = freeze_workflow(self.repository, phase=DesignPhase.SCHEMATIC_DESIGN)
        path = self.repository.layout.resolve_record(record_ref_from_uri(uri, PROJECT_ID))
        original = path.read_bytes()
        binding = ProjectBinding.open(self.settings)
        self.assertEqual(binding.frozen_workflow_first_phase(), DesignPhase.SCHEMATIC_DESIGN.value)
        path.write_bytes(original.replace(DesignPhase.SCHEMATIC_DESIGN.value.encode(), DesignPhase.DESIGN_DEVELOPMENT.value.encode()))
        self.assertIsNone(binding.frozen_workflow_first_phase(), "a readable JSON payload still needs the retained digest")
        path.write_bytes(original)
        self.assertEqual(binding.frozen_workflow_first_phase(), DesignPhase.SCHEMATIC_DESIGN.value)

    def test_the_configured_run_beats_the_rule(self) -> None:
        add_harness_run(self.repository)
        binding = ProjectBinding.open(
            StudioSettings(
                cad_export="off", project_dir=self.root / PROJECT_ID,
                reference_run=HARNESS_RUN_ID,
            )
        )

        reference = binding.reference_run()

        self.assertEqual(reference.source, "config")
        self.assertEqual(reference.run.run_id, HARNESS_RUN_ID)

    def test_a_configured_run_that_does_not_exist_is_named_in_the_404(
        self,
    ) -> None:
        settings = StudioSettings(
            cad_export="off", project_dir=self.root / PROJECT_ID,
            reference_run="run-nowhere",
        )
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/project")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["code"], "RUN_NOT_FOUND")
        self.assertIn("run-nowhere", payload["detail"])

    def test_an_unknown_run_id_is_a_run_not_found(self) -> None:
        binding = ProjectBinding.open(self.settings)

        with self.assertRaises(StudioError) as raised:
            binding.load_run("run-nowhere")

        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "RUN_NOT_FOUND")
        self.assertIn("run-nowhere", raised.exception.detail)

    def test_the_binding_lists_the_runs_on_disk(self) -> None:
        add_harness_run(self.repository)
        binding = ProjectBinding.open(self.settings)

        self.assertEqual(
            binding.run_ids(), (REFERENCE_RUN_ID, HARNESS_RUN_ID)
        )


class ProjectWithoutRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository = make_empty_project(self.root)
        self.settings = StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)

    def test_a_project_with_no_run_still_states_its_binding(self) -> None:
        head = self.repository.read_head()

        response = self.client.get("/api/project")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["published"]["version"], 0)
        self.assertEqual(
            payload["referenceRun"]["runId"], "studio-projection"
        )
        self.assertEqual(
            payload["referenceRun"]["baseSha256"], head.state_sha256
        )

    def test_no_eligible_run_leaves_the_reference_unsourced(self) -> None:
        binding = ProjectBinding.open(self.settings)

        reference = binding.reference_run()

        self.assertEqual(reference.source, "none")
        self.assertEqual(reference.run.run_id, "studio-projection")
        self.assertIsNone(reference.receipt)


if __name__ == "__main__":
    unittest.main()
