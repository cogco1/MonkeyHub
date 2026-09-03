"""Binding one project: which project, which HEAD, which run answers for it."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import NotBound, NotFound

from .support import (
    HARNESS_RUN_ID,
    PROJECT_ID,
    REFERENCE_RUN_ID,
    add_harness_run,
    make_empty_project,
    make_project,
)


class UnboundProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, self.root)
        self.settings = StudioSettings(project_dir=self.root / "no-project")

    def test_open_refuses_a_directory_that_is_not_a_project(self) -> None:
        with self.assertRaises(NotBound) as raised:
            ProjectBinding.open(self.settings)

        self.assertEqual(raised.exception.code, "PROJECT_NOT_BOUND")
        self.assertEqual(raised.exception.status, 503)
        self.assertIn(str(self.settings.project_dir), raised.exception.detail)

    def test_project_route_answers_503_in_the_studio_error_shape(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.get("/api/project")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioError@1")
        self.assertEqual(payload["code"], "PROJECT_NOT_BOUND")
        self.assertIn(str(self.settings.project_dir), payload["detail"])

    def test_health_reports_the_binding_it_could_not_open(self) -> None:
        with TestClient(create_app(self.settings)) as client:
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["projectBound"], False)


class BoundProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, self.root)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)

    def test_project_route_reports_the_exact_head_and_reference_run(
        self,
    ) -> None:
        head = self.repository.read_head()

        response = self.client.get("/api/project")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "ProjectBinding@1")
        self.assertEqual(payload["projectId"], PROJECT_ID)
        self.assertEqual(
            payload["projectDir"], str(self.root / PROJECT_ID)
        )
        self.assertEqual(payload["head"]["version"], 0)
        self.assertEqual(payload["head"]["stateSha256"], head.state_sha256)
        self.assertEqual(
            payload["referenceRun"]["runId"], REFERENCE_RUN_ID
        )
        self.assertEqual(payload["referenceRun"]["baseVersion"], 0)
        self.assertEqual(
            payload["referenceRun"]["baseSha256"], head.state_sha256
        )
        self.assertIs(payload["canonicalWriteAuthority"], False)

    def test_health_reports_the_binding_it_opened(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["projectBound"], True)

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
        self.assertEqual(
            reference.receipt["schema"], "RunnerRunReceipt@3"
        )

    def test_the_configured_run_beats_the_rule(self) -> None:
        add_harness_run(self.repository)
        binding = ProjectBinding.open(
            StudioSettings(
                project_dir=self.root / PROJECT_ID,
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
            project_dir=self.root / PROJECT_ID,
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

        with self.assertRaises(NotFound) as raised:
            binding.load_run("run-nowhere")

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
        self.addCleanup(_remove_tree, self.root)
        self.repository = make_empty_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)

    def test_a_project_with_no_run_still_states_its_binding(self) -> None:
        head = self.repository.read_head()

        response = self.client.get("/api/project")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["head"]["version"], 0)
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


def _remove_tree(root: Path) -> None:
    import shutil

    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
