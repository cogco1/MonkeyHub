"""Hub-to-CLI behavior; optional real runs use a caller-selected test interpreter."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from monkeyhub_api.fabrication import ACCESS_CODE_ENV
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import FabSendRequest


PROFILE = {
    "key": "fixture", "label": "Fixture printer",
    "nominal_volume_mm": [100.0, 100.0, 100.0],
    "usable_origin_mm": [0.0, 0.0, 0.0],
    "usable_volume_mm": [90.0, 90.0, 90.0],
    "notes": "CLI-owned fixture", "sources": ["https://example.invalid/profile"],
}


class _FabCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyFab Hub 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        with patch("monkeyhub_api.applications.source_revision", return_value="a" * 40):
            self.app = create_app(HubSettings(runtime_root=self.runtime), source_root=ROOT)
        self.client = self.enterContext(TestClient(self.app, base_url="http://127.0.0.1:8790"))
        self.source = self.root / "closed model.obj"
        self.output = self.root / "explicit output"
        self.job = self.root / "sliced job.gcode.3mf"

    def prepare_request(self, **changes):
        return {
            "source": str(self.source), "outputDir": str(self.output),
            "printer": "fixture", "inputUnit": "mm", **changes,
        }

    def send_request(self, **changes):
        return {"source": str(self.job), "host": "192.0.2.1", "dryRun": True, **changes}

    def send_result(self, **changes):
        return {
            "file": str(self.job), "remote_path": "/sliced job.gcode.3mf",
            "bytes": 42, "plates": [1], "host": "192.0.2.1",
            "status": "validated", "print_started": False, **changes,
        }

    @contextmanager
    def command(self, stdout="", stderr="", code=0, **options):
        result = subprocess.CompletedProcess([], code, stdout, stderr)
        with patch("monkeyhub_api.fabrication.available", return_value=True), patch(
            "monkeyhub_api.fabrication.subprocess.run", return_value=result, **options,
        ) as run:
            yield run


class FabApiTests(_FabCase):
    def test_fab_is_hosted_here_without_creating_or_stopping_a_child(self):
        with patch("monkeyhub_api.fabrication.available", return_value=True), patch(
            "monkeyhub_api.applications.subprocess.Popen",
        ) as spawn:
            rows = {row["appId"]: row for row in self.client.get("/api/apps").json()}
            self.assertEqual(rows["monkeyfab"]["state"], "running")
            self.assertEqual(rows["monkeyfab"]["serviceId"], "hub")
            self.assertEqual(rows["monkeyfab"]["processId"], os.getpid())
            self.assertEqual(rows["monkeyfab"]["url"], "http://127.0.0.1:8790/?view=fab")
            self.assertEqual(self.client.post("/api/apps/monkeyfab/start").json(), rows["monkeyfab"])
            response = self.client.post("/api/apps/monkeyfab/stop")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "APP_HOSTED_BY_HUB")
            self.assertEqual(self.client.get("/api/health").status_code, 200)
            spawn.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_missing_package_has_explicit_status_and_api_error(self):
        with patch("monkeyhub_api.fabrication.available", return_value=False), patch(
            "monkeyhub_api.fabrication.subprocess.run",
        ) as run:
            rows = self.client.get("/api/apps").json()
            status = next(row for row in rows if row["appId"] == "monkeyfab")
            self.assertFalse(status["available"])
            self.assertEqual(status["state"], "unavailable")
            self.assertIsNone(status["url"])
            self.assertIsNone(status["processId"])
            self.assertEqual(status["error"]["code"], "FAB_UNAVAILABLE")
            response = self.client.get("/api/fab/profiles")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "FAB_UNAVAILABLE")
            run.assert_not_called()

    def test_profiles_reuse_the_cli_json(self):
        with self.command(json.dumps({"fixture": PROFILE})) as run:
            response = self.client.get("/api/fab/profiles")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"fixture": PROFILE})
            self.assertEqual(run.call_args.args[0], [sys.executable, "-m", "monkeyfab", "profiles", "--json"])

    def test_prepare_preserves_paths_options_and_cli_stdout_without_hub_writes(self):
        stdout = "Prepared 1 closed part.\nOutput: caller-owned directory.\n"
        with self.command(stdout) as run:
            response = self.client.post("/api/fab/prepare", json=self.prepare_request(
                scale="1:100", xyMarginMm=3.5, zClearanceMm=2.0,
            ))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"stdout": stdout, "outputDir": str(self.output)})
            args, kwargs = run.call_args.args[0], run.call_args.kwargs
            self.assertEqual(args[:5], [sys.executable, "-m", "monkeyfab", "prepare", str(self.source)])
            self.assertEqual(args[args.index("--output") + 1], str(self.output))
            self.assertIn("--scale=1:100", args)
            self.assertIn("--xy-margin=3.5", args)
            self.assertIn("--z-clearance=2.0", args)
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["cwd"], ROOT)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.runtime.exists())

    def test_send_secret_exists_only_in_that_child_environment(self):
        secret = "test-access-code"
        with patch.dict(os.environ, {"BAMBU_ACCESS_CODE": "inherited-secret", ACCESS_CODE_ENV: "old-secret"}), self.command(
            json.dumps(self.send_result(status="uploaded")),
        ) as run:
            before = dict(os.environ)
            response = self.client.post("/api/fab/send", json=self.send_request(
                dryRun=False, accessCode=secret, remoteName="requested.gcode.3mf", timeout=45.0,
            ))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "uploaded")
            self.assertFalse(response.json()["print_started"])
            args, kwargs = run.call_args.args[0], run.call_args.kwargs
            self.assertNotIn(secret, " ".join(args))
            self.assertEqual(kwargs["env"][ACCESS_CODE_ENV], secret)
            self.assertNotIn("BAMBU_ACCESS_CODE", kwargs["env"])
            self.assertEqual(args[args.index("--access-code-env") + 1], ACCESS_CODE_ENV)
            self.assertIn("--remote-name=requested.gcode.3mf", args)
            self.assertIn("--timeout=45.0", args)
            self.assertEqual(dict(os.environ), before)
            self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, repr(FabSendRequest.model_validate(self.send_request(accessCode=secret))))
        self.assertFalse(self.runtime.exists())

    def test_dry_run_passes_no_access_code_and_needs_none(self):
        with patch.dict(os.environ, {"BAMBU_ACCESS_CODE": "inherited-secret", ACCESS_CODE_ENV: "old-secret"}), self.command(
            json.dumps(self.send_result()),
        ) as run:
            response = self.client.post("/api/fab/send", json=self.send_request(accessCode="ignored-secret"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), self.send_result())
            self.assertIn("--dry-run", run.call_args.args[0])
            self.assertNotIn(ACCESS_CODE_ENV, run.call_args.kwargs["env"])
            self.assertNotIn("BAMBU_ACCESS_CODE", run.call_args.kwargs["env"])

    def test_required_code_and_strict_validation_never_echo_request_secret(self):
        secret = "private-request-value"
        with self.command() as run:
            for body in (
                self.send_request(dryRun=False),
                self.send_request(dryRun=False, accessCode=" "),
                self.send_request(accessCode=secret, dryRun="true"),
                self.send_request(accessCode={"secret": secret}),
                self.send_request(accessCode=secret, extra=secret),
                self.send_request(accessCode=secret, source="relative.gcode.3mf"),
                self.send_request(accessCode=secret, timeout=True),
            ):
                with self.subTest(body_keys=tuple(body)):
                    response = self.client.post("/api/fab/send", json=body)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn(secret, response.text)
                    self.assertEqual(set(response.json()), {"code", "detail"})
            for body in (
                self.prepare_request(outputDir="relative-output"),
                self.prepare_request(xyMarginMm=-1),
                self.prepare_request(scale=0.01),
                self.prepare_request(inputUnit="ft"),
            ):
                self.assertEqual(self.client.post("/api/fab/prepare", json=body).status_code, 422)
            run.assert_not_called()

    def test_child_errors_are_reported_without_secret_or_false_success(self):
        secret = "private-child-secret"
        with self.command(stderr=f"monkeyfab: {secret}: file already exists", code=2):
            response = self.client.post("/api/fab/send", json=self.send_request(dryRun=False, accessCode=secret))
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "FAB_COMMAND_FAILED")
            self.assertIn("file already exists", response.json()["detail"])
            self.assertNotIn(secret, response.text)
        with self.command(stderr=f"Traceback: {secret}", code=1):
            response = self.client.post("/api/fab/send", json=self.send_request(dryRun=False, accessCode=secret))
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(secret, response.text)
            self.assertNotIn("Traceback", response.text)
        with self.command(stdout="not JSON"):
            self.assertEqual(self.client.get("/api/fab/profiles").status_code, 502)
        with self.command(stdout=json.dumps(self.send_result(print_started=True))):
            response = self.client.post("/api/fab/send", json=self.send_request())
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["code"], "FAB_OUTPUT_INVALID")

    def test_origin_and_post_boundaries_prevent_cli_execution(self):
        with self.command() as run:
            self.assertEqual(self.client.get("/api/fab/prepare").status_code, 405)
            self.assertEqual(self.client.get("/api/fab/send").status_code, 405)
            self.assertEqual(self.client.post("/api/fab/prepare", json=self.prepare_request(), headers={
                "Origin": "https://external.invalid",
            }).status_code, 403)
            self.assertEqual(self.client.post("/api/fab/send", json=self.send_request(), headers={
                "Host": "external.invalid:8790",
            }).status_code, 403)
            run.assert_not_called()

    def test_request_waits_for_cli_while_health_remains_available(self):
        entered, release = threading.Event(), threading.Event()
        responses = []

        def blocking_command(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("The test did not release the CLI operation")
            return subprocess.CompletedProcess([], 0, "Prepared fixture.\n", "")

        with self.command(side_effect=blocking_command):
            thread = threading.Thread(target=lambda: responses.append(
                self.client.post("/api/fab/prepare", json=self.prepare_request()),
            ))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(responses, [])
                self.assertEqual(self.client.get("/api/health").status_code, 200)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(responses[0].status_code, 200)


class FabRealCliTests(_FabCase):
    """Opt in with MONKEYFAB_TEST_PYTHON; these tests never send to a printer."""

    def setUp(self):
        interpreter = os.environ.get("MONKEYFAB_TEST_PYTHON")
        if not interpreter:
            self.skipTest("Set MONKEYFAB_TEST_PYTHON to an interpreter with the real Fab package")
        self.interpreter = Path(interpreter)
        if not self.interpreter.is_absolute() or not self.interpreter.is_file():
            self.fail("MONKEYFAB_TEST_PYTHON must be an existing absolute interpreter path")
        super().setUp()

    def test_real_profiles_prepare_refuse_overwrite_and_dry_run(self):
        self.source.write_text(
            "v 0 0 0\nv 10 0 0\nv 0 10 0\nv 0 0 10\n"
            "f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n", encoding="utf-8",
        )
        with ZipFile(self.job, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "; disposable local validation fixture\nG28\n")
        with patch("monkeyhub_api.fabrication.available", return_value=True), patch(
            "monkeyhub_api.fabrication.sys.executable", str(self.interpreter),
        ):
            profiles = self.client.get("/api/fab/profiles")
            self.assertEqual(profiles.status_code, 200, profiles.text)
            self.assertIn("h2s", profiles.json())
            body = self.prepare_request(printer="h2s", scale="1:1")
            prepared = self.client.post("/api/fab/prepare", json=body)
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertEqual(prepared.json()["outputDir"], str(self.output))
            outputs = {path.name: path.read_bytes() for path in self.output.iterdir()}
            self.assertIn("parts.json", outputs)
            self.assertTrue(any(name.endswith(".stl") for name in outputs))
            metadata = json.loads(outputs["parts.json"])
            self.assertEqual(metadata["output_unit"], "mm")
            self.assertEqual(len(metadata["parts"]), 1)
            duplicate = self.client.post("/api/fab/prepare", json=body)
            self.assertEqual(duplicate.status_code, 422, duplicate.text)
            self.assertIn("new or empty directory", duplicate.json()["detail"])
            self.assertEqual({path.name: path.read_bytes() for path in self.output.iterdir()}, outputs)
            dry = self.client.post("/api/fab/send", json=self.send_request())
            self.assertEqual(dry.status_code, 200, dry.text)
            self.assertEqual(dry.json()["status"], "validated")
            self.assertFalse(dry.json()["print_started"])
            self.assertEqual(dry.json()["bytes"], self.job.stat().st_size)
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()
