"""The API answers health without a project and refuses unknown routes in
the error shape."""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow_studio_api.main import _source_revision, _watch_managed_stdin, create_app, main
from archflow_studio_api.protocol import SERVER_VERSION
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_project


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=Path("unbound-placeholder")))
        )
        self.addCleanup(self.client.close)

    def test_health_is_up_and_unbound(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "archflow-studio-api", "projectBound": False},
        )

    def test_unknown_route_answers_in_the_error_shape(self) -> None:
        response = self.client.get("/api/nonsense")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "NOT_FOUND")


class BoundHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_health_reports_the_binding_it_opened(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["projectBound"], True)


class ManagedStudioTests(unittest.TestCase):
    def test_stop_command_and_owner_pipe_eof_close_job_admission(self) -> None:
        for owner_input in ("ignored\nstop\n", ""):
            with self.subTest(owner_input=owner_input):
                app = create_app(StudioSettings(cad_export="off", project_dir=Path("unbound-placeholder")))
                self.addCleanup(app.state.jobs.shutdown)
                server = SimpleNamespace(should_exit=False)
                _watch_managed_stdin(server, app.state.jobs, io.StringIO(owner_input))
                self.assertTrue(server.should_exit)
                self.assertFalse(app.state.jobs.accepting)

    def test_packaged_source_identity_precedes_git_and_refuses_invalid_stamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "b" * 40
            (root / "source-version.txt").write_text(revision + "\n", encoding="utf-8")
            with patch("archflow_studio_api.main.subprocess.run") as git:
                self.assertEqual(_source_revision(root), revision)
                git.assert_not_called()
            (root / "source-version.txt").write_text("0.1.0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "full source commit SHA"):
                _source_revision(root)

    def test_a_source_copy_without_a_version_stamp_or_git_reports_no_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(_source_revision(Path(directory)))

    def test_cli_serves_prebuilt_web_and_retains_api_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            web = Path(directory)
            (web / "index.html").write_text("<html>Studio fixture</html>", encoding="utf-8")
            environment = {"ARCHFLOW_STUDIO_PROJECT_DIR": str(web / "missing-project")}
            with patch.dict(os.environ, environment, clear=True), patch(
                "archflow_studio_api.main.uvicorn.run"
            ) as serve, patch("archflow_studio_api.main._source_revision", return_value="b" * 40):
                main(["--web-dir", str(web)])
            app = serve.call_args.args[0]
            with TestClient(app) as client:
                self.assertEqual(client.get("/?view=documents").text, "<html>Studio fixture</html>")
                health = client.get("/api/health").json()
                self.assertEqual(health["serverVersion"], SERVER_VERSION)
                self.assertEqual(health["processId"], os.getpid())
                self.assertEqual(health["parentProcessId"], os.getppid())
                self.assertEqual(health["sourceRevision"], "b" * 40)
                self.assertNotIn("managedInstanceId", health)
                self.assertEqual(client.get("/api/nonsense").json()["code"], "NOT_FOUND")

    def test_managed_cli_reports_the_actual_instance_and_waits_for_normal_stop(self) -> None:
        environment = {"ARCHFLOW_STUDIO_PROJECT_DIR": "unbound-placeholder"}
        with patch.dict(os.environ, environment, clear=True), patch(
            "archflow_studio_api.main.uvicorn.Server"
        ) as server_type, patch(
            "archflow_studio_api.main.sys.stdin", io.StringIO("stop\n")
        ), patch("archflow_studio_api.main._source_revision", return_value="c" * 40):
            main(["--managed-stdin", "--managed-instance-id", "hub-launch-123"])
        server_type.return_value.run.assert_called_once_with()
        app = server_type.call_args.args[0].app
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            self.assertEqual(health["managedInstanceId"], "hub-launch-123")
            self.assertEqual(health["processId"], os.getpid())
            self.assertEqual(health["parentProcessId"], os.getppid())
            self.assertEqual(health["sourceRevision"], "c" * 40)
        self.assertFalse(app.state.jobs.accepting)

    def test_managed_arguments_must_be_paired(self) -> None:
        for arguments in (["--managed-stdin"], ["--managed-instance-id", "orphan"]):
            with self.subTest(arguments=arguments), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                main(arguments)


if __name__ == "__main__":
    unittest.main()
