"""Working in the architect's open Rhino: instance choice, the script round trip, the routes.

RhinoCode itself is replaced by a fake that answers the way Rhino's script does
(it writes the result file), so these run without Rhino.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from project_runtime.api.dto.live_rhino import LiveRhinoWriteRequest
from project_runtime.application import live_rhino
from project_runtime.errors import StudioError
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, make_project

LISTED = json.dumps([
    {"pipeId": "rhinocode_remotepipe_11", "processId": 11, "processVersion": "8.28",
     "activeDoc": {"title": "a.3dm", "location": r"D:\work\a.3dm"}, "activeViewport": "Perspective"},
    {"pipeId": "rhinocode_remotepipe_22", "processId": 22, "activeDoc": {"title": "", "location": ""}},
])


class FakeRhinoCode:
    """Answers ``list`` with LISTED and runs nothing; a script's answer is what ``reply`` returns."""

    def __init__(self, reply=None, listed=LISTED):
        self.calls: list[list[str]] = []
        self.requests: list[dict] = []
        self.reply = reply or (lambda request: {"ok": True})
        self.listed = listed

    def __call__(self, args, **_):
        self.calls.append(list(args))
        if args[1] == "list":
            return subprocess.CompletedProcess(args, 0, self.listed, "")
        script = Path(args[-1].removeprefix('_-RunPythonScript "').removesuffix('"'))
        request_path, result_path = re.search(r"_main\('(.+?)', '(.+?)'\)\s*$", script.read_text(encoding="utf-8")).groups()
        request = json.loads(Path(request_path.replace("\\\\", "\\")).read_text(encoding="utf-8"))
        self.requests.append(request)
        Path(result_path.replace("\\\\", "\\")).write_text(json.dumps(self.reply(request)), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")


class LiveRhinoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        exe = patch.object(live_rhino, "rhinocode_executable", return_value=Path(r"C:\Rhino 8\System\RhinoCode.exe"))
        exe.start()
        self.addCleanup(exe.stop)

    def test_the_list_names_each_rhino_and_its_open_document(self) -> None:
        rows = live_rhino.list_instances(runner=FakeRhinoCode())
        self.assertEqual([row.pipe_id for row in rows], ["rhinocode_remotepipe_11", "rhinocode_remotepipe_22"])
        self.assertEqual(rows[0].document_path, r"D:\work\a.3dm")
        self.assertIsNone(rows[1].document_path, "an unsaved document has no path")

    def test_a_request_reaches_only_the_rhino_it_means(self) -> None:
        rows = live_rhino.parse_instances(LISTED)
        self.assertEqual(live_rhino.select_instance(rows, document_path=r"d:\WORK\a.3dm").process_id, 11)
        self.assertEqual(live_rhino.select_instance(rows, rhino_id="22").process_id, 22)
        for kwargs, code in (({}, "LIVE_RHINO_AMBIGUOUS"), ({"rhino_id": "33"}, "LIVE_RHINO_NOT_FOUND"),
                             ({"document_path": r"D:\work\b.3dm"}, "LIVE_RHINO_NOT_FOUND"),
                             ({"rhino_id": "11", "document_path": r"D:\work\b.3dm"}, "LIVE_RHINO_DOCUMENT_CHANGED")):
            with self.subTest(kwargs=kwargs), self.assertRaises(StudioError) as caught:
                live_rhino.select_instance(rows, **kwargs)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(StudioError) as caught:
            live_rhino.select_instance(())
        self.assertIn("StartScriptServer", caught.exception.detail)

    def test_a_write_is_one_rhino_command_and_a_read_is_a_plain_script(self) -> None:
        fake = FakeRhinoCode(lambda request: {"ok": True, "created": [], "document": {"path": request["documentPath"]}})
        answer = live_rhino.write_document(label="lot", operations=[{"op": "layer", "path": "HUB"}], project_id=PROJECT_ID,
                                           document_path=r"D:\work\a.3dm", runner=fake)
        self.assertEqual(fake.calls[-1][1:4], ["--rhino", "rhinocode_remotepipe_11", "command"])
        self.assertTrue(fake.calls[-1][4].startswith('_-RunPythonScript "'))
        request = fake.requests[-1]
        self.assertEqual((request["action"], request["label"], request["projectId"]), ("write", "lot", PROJECT_ID))
        self.assertFalse(request["allowUserObjects"])
        self.assertEqual(answer["rhino"]["rhinoId"], "rhinocode_remotepipe_11")

        live_rhino.read_document(rhino_id="11", layer_prefix="HUB", runner=fake)
        self.assertEqual(fake.calls[-1][3], "script")
        self.assertEqual(fake.requests[-1]["layerPrefix"], "HUB")

    def test_a_snapshot_registers_the_copy_rhino_wrote(self) -> None:
        def reply(request):
            Path(request["path"]).write_bytes(b"3D Geometry File Format snapshot")
            return {"ok": True, "document": {"path": request["documentPath"]}, "writtenObjects": 4,
                    "omitted": {"TextDot": 1}}

        fake = FakeRhinoCode(reply)
        with patch("project_runtime.application.artifacts.register_model_asset", return_value="record") as register,                 patch("monkeycad.backends.occt.native_models.drawable_report",
                      return_value={"checked": True, "drawable": 0, "objects": [{"id": "x", "reason": "trim"}]}):
            answer = live_rhino.snapshot_document("binding", rhino_id="11", drawable_only=True,
                                                  layer_prefixes=["SITE MODEL"], runner=fake)
        self.assertEqual(answer["undrawable"], {"checked": True, "drawable": 0, "objects": [{"id": "x", "reason": "trim"}]})
        request = fake.requests[-1]
        self.assertEqual((request["action"], request["drawableOnly"], request["layerPrefixes"]),
                         ("snapshot", True, ["SITE MODEL"]))
        self.assertEqual(fake.calls[-1][3], "script", "a snapshot is no command: nothing enters the undo list")
        args = register.call_args.args
        self.assertEqual((args[0], args[1], args[2], args[3]), ("binding", None, None, "a_live_snapshot.3dm"))
        self.assertEqual((answer["artifact"], answer["omitted"]), ("record", {"TextDot": 1}))

    def test_rhinos_refusal_keeps_its_code(self) -> None:
        fake = FakeRhinoCode(lambda request: {"ok": False, "code": "LIVE_RHINO_USER_OBJECT", "detail": "ask first"})
        with self.assertRaises(StudioError) as caught:
            live_rhino.write_document(label="move", operations=[{"op": "delete", "ids": ["x"]}], project_id=PROJECT_ID,
                                      rhino_id="11", runner=fake)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "LIVE_RHINO_USER_OBJECT"))

    def test_a_rhino_that_never_answers_times_out(self) -> None:
        class Silent(FakeRhinoCode):
            def __call__(self, args, **kwargs):
                if args[1] == "list":
                    return super().__call__(args, **kwargs)
                return subprocess.CompletedProcess(args, 0, "", "")

        instance = live_rhino.parse_instances(LISTED)[0]
        with self.assertRaises(StudioError) as caught:
            live_rhino.run_in_rhino(instance, {"action": "read"}, as_command=False, timeout_s=0.2, runner=Silent())
        self.assertEqual(caught.exception.code, "LIVE_RHINO_TIMEOUT")

    def test_the_rhino_script_is_valid_python(self) -> None:
        compile(live_rhino.RUNNER_SOURCE, "live_rhino_runner", "exec")


class OpeningRhinoTestCase(unittest.TestCase):
    """MonkeyHub opens Rhino with its script server on, so the person does not type StartScriptServer."""

    def setUp(self) -> None:
        self.listed = "[]"
        self.rhino_processes = ""
        self.calls: list[list[str]] = []
        self.launched: list[str] = []
        self.sleeps: list[float] = []
        for target in (patch.object(live_rhino, "rhinocode_executable", return_value=Path(r"C:\Rhino 8\System\RhinoCode.exe")),
                       patch.object(live_rhino, "rhino_executable", return_value=Path(r"C:\Rhino 8\System\Rhino.exe")),
                       patch.object(live_rhino, "_launch", side_effect=self.launch),
                       patch.object(live_rhino.time, "sleep", side_effect=self.sleeps.append)):
            target.start()
            self.addCleanup(target.stop)
        self.document = Path(tempfile.mkdtemp()) / "site.3dm"
        self.addCleanup(shutil.rmtree, self.document.parent, True)
        self.document.write_bytes(b"3dm")

    def run_command(self, args, **_):
        self.calls.append(list(args))
        if args[0] == "tasklist":
            return subprocess.CompletedProcess(args, 0, self.rhino_processes, "")
        return subprocess.CompletedProcess(args, 0, self.listed, "")

    launch_adds: dict = {"pipeId": "rhinocode_remotepipe_99", "processId": 99}

    def launch(self, command_line: str) -> None:
        self.launched.append(command_line)
        rows = json.loads(self.listed)
        self.listed = json.dumps(rows + [{**self.launch_adds, "activeDoc": {"title": self.document.name, "location": str(self.document)}}])

    def test_rhino_starts_with_the_script_server_command_and_the_document_and_is_waited_for(self) -> None:
        instance, opened = live_rhino.open_connected(document_path=str(self.document), runner=self.run_command)
        self.assertEqual((instance.pipe_id, opened), ("rhinocode_remotepipe_99", True))
        self.assertEqual(self.launched, [f'"C:\\Rhino 8\\System\\Rhino.exe" /nosplash /runscript="_StartScriptServer" "{self.document}"'])
        self.assertTrue(self.sleeps, "it waits between looks")

    def test_without_a_document_rhino_opens_as_it_usually_does(self) -> None:
        live_rhino.open_connected(runner=self.run_command)
        self.assertEqual(self.launched, ['"C:\\Rhino 8\\System\\Rhino.exe" /nosplash /runscript="_StartScriptServer"'])

    def test_a_rhino_that_already_answers_is_not_opened_again(self) -> None:
        self.listed = LISTED
        instance, opened = live_rhino.open_connected(document_path=None, runner=self.run_command)
        self.assertEqual((instance.pipe_id, opened, self.launched), ("rhinocode_remotepipe_11", False, []))
        held = Path(tempfile.mkdtemp()) / "a.3dm"
        self.addCleanup(shutil.rmtree, held.parent, True)
        held.write_bytes(b"3dm")
        self.listed = json.dumps([{"pipeId": "p1", "processId": 1, "activeDoc": {"title": "a.3dm", "location": str(held)}}])
        instance, opened = live_rhino.open_connected(document_path=str(held), runner=self.run_command)
        self.assertEqual((instance.pipe_id, opened, self.launched), ("p1", False, []), "the Rhino that holds the document is the one")

    def test_a_rhino_open_without_its_server_is_never_joined_by_a_second_one(self) -> None:
        self.rhino_processes = '"Rhino.exe","1234","Console","1","300,000 K"'
        with self.assertRaises(StudioError) as refused:
            live_rhino.open_connected(runner=self.run_command)
        self.assertEqual(refused.exception.code, "LIVE_RHINO_ALREADY_OPEN")
        self.assertIn("StartScriptServer", refused.exception.detail)
        self.assertEqual(self.launched, [])

    def test_a_connected_rhino_holding_another_file_does_not_stop_this_one_from_opening(self) -> None:
        # Rhino 11 answers RhinoCode with a.3dm open; the person asks for site.3dm. Rhino 11 is the only Rhino running.
        self.listed = json.dumps([{"pipeId": "rhinocode_remotepipe_11", "processId": 11,
                                   "activeDoc": {"title": "a.3dm", "location": r"D:\work\a.3dm"}}])
        self.rhino_processes = '"Rhino.exe","11","Console","1","300,000 K"'
        self.launch_adds = {"pipeId": "rhinocode_remotepipe_99", "processId": 99}
        instance, opened = live_rhino.open_connected(document_path=str(self.document), runner=self.run_command)
        self.assertEqual((instance.pipe_id, opened), ("rhinocode_remotepipe_99", True))
        self.assertEqual(len(self.launched), 1)
        # A second Rhino the person has open, which RhinoCode cannot reach, still stops it.
        self.launched.clear()
        self.rhino_processes += '\n"Rhino.exe","4321","Console","1","200,000 K"'
        other = self.document.with_name("other.3dm")
        other.write_bytes(b"3dm")
        with self.assertRaises(StudioError) as refused:
            live_rhino.open_connected(document_path=str(other), runner=self.run_command)
        self.assertEqual(refused.exception.code, "LIVE_RHINO_ALREADY_OPEN")
        self.assertEqual(self.launched, [])

    def test_a_rhino_that_never_answers_is_said_not_to_have_connected(self) -> None:
        with patch.object(live_rhino, "_launch"):
            with patch.object(live_rhino.time, "monotonic", side_effect=[0.0, 0.0, 5.0, 5.0, 10.0, 10.0]):
                with self.assertRaises(StudioError) as refused:
                    live_rhino.open_connected(runner=self.run_command, timeout_s=3.0)
        self.assertEqual((refused.exception.status, refused.exception.code), (504, "LIVE_RHINO_NOT_CONNECTED"))

    def test_only_an_existing_3dm_file_without_quotes_is_opened(self) -> None:
        for bad in (str(self.document.with_suffix(".txt")), str(self.document.parent / "missing.3dm"), f'{self.document}" /x'):
            with self.subTest(bad=bad):
                with self.assertRaises(StudioError) as refused:
                    live_rhino.open_connected(document_path=bad, runner=self.run_command)
                self.assertEqual(refused.exception.code, "LIVE_RHINO_DOCUMENT_INVALID")
        self.assertEqual(self.launched, [])

    def test_the_not_running_refusal_tells_what_to_ask(self) -> None:
        with self.assertRaises(StudioError) as refused:
            live_rhino.select_instance(())
        self.assertIn("POST /api/live-rhino/open", refused.exception.detail)
        self.assertIn("StartScriptServer", refused.exception.detail)


class FindingRhinoTestCase(unittest.TestCase):
    """RhinoCode.exe is found where Windows records Rhino, with no environment variable set."""

    def test_rhinocode_is_the_one_beside_a_discovered_rhino(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        system = root / "Rhino 8" / "System"
        system.mkdir(parents=True)
        for name in ("Rhino.exe", "RhinoCode.exe"):
            (system / name).write_bytes(b"")
        with patch.dict("os.environ", {}, clear=False), patch("monkeycad.backends.rhino.export.discover_rhino_executables",
                                                           return_value=(system / "Rhino.exe",)):
            os_environ = __import__("os").environ
            os_environ.pop(live_rhino.RHINOCODE_ENV, None)
            self.assertEqual(live_rhino.rhinocode_executable(), system / "RhinoCode.exe")
            self.assertEqual(live_rhino.rhino_executable(), system / "Rhino.exe")

    def test_no_rhino_is_said_without_leading_with_a_variable(self) -> None:
        with patch.dict("os.environ", {"ProgramFiles": r"Z:\nowhere"}, clear=False), patch(
                "monkeycad.backends.rhino.export.discover_rhino_executables", return_value=()):
            __import__("os").environ.pop(live_rhino.RHINOCODE_ENV, None)
            with self.assertRaises(StudioError) as refused:
                live_rhino.rhinocode_executable()
        self.assertEqual(refused.exception.code, "LIVE_RHINO_UNAVAILABLE")
        self.assertTrue(refused.exception.detail.startswith("Rhino 8 was not found on this computer"))


class LiveRhinoRequestTestCase(unittest.TestCase):
    def test_operations_are_checked_before_rhino_is_asked(self) -> None:
        request = LiveRhinoWriteRequest.model_validate({"label": "site", "operations": [
            {"op": "extrusion", "layer": "HUB::Site", "outline": [[0, 0], [51, 0], [51, 130]], "height": 1},
            {"op": "transform", "ids": ["a"], "rotate": {"angleDeg": 15}},
            {"op": "import", "layer": "HUB::Course", "path": r"D:\site.3dm", "scale": 1 / 12, "keepLayers": False},
        ]})
        body = request.model_dump(by_alias=True, exclude_none=True)["operations"]
        self.assertEqual(body[1]["rotate"], {"angleDeg": 15.0, "center": [0.0, 0.0, 0.0], "axis": [0.0, 0.0, 1.0]})
        self.assertFalse(body[2]["keepLayers"])
        for bad in ({"op": "box", "layer": "x", "min": [0, 0], "max": [1, 1, 1]},
                    {"op": "extrusion", "layer": "x", "outline": [[0, 0], [1, 0]], "height": 1},
                    {"op": "sphere", "layer": "x"},
                    {"op": "delete", "ids": []}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                LiveRhinoWriteRequest.model_validate({"label": "x", "operations": [bad]})


class LiveRhinoRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)))
        self.addCleanup(self.client.close)

    def test_the_write_route_names_the_bound_project(self) -> None:
        written = {"rhino": {"rhinoId": "rhinocode_remotepipe_7404", "processId": 7404, "version": "8.28",
                             "documentTitle": "a.3dm", "documentPath": r"D:\work\a.3dm", "activeViewport": "Top"},
                   "writeId": "w1", "label": "lot", "created": [{"operation": 0, "id": "b1", "op": "box"}],
                   "changed": [], "deleted": [], "imported": [],
                   "document": {"title": "a.3dm", "path": r"D:\work\a.3dm", "units": "Feet", "tolerance": 0.001,
                                "modified": True, "objectCount": 1}}
        with patch.object(live_rhino, "write_document", return_value=written) as write:
            answer = self.client.post("/api/live-rhino/write", json={
                "label": "lot", "documentPath": r"D:\work\a.3dm",
                "operations": [{"op": "box", "layer": "HUB", "min": [0, 0, 0], "max": [1, 1, 1]}]})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["created"], written["created"], "the answer is the documented shape")
        kwargs = write.call_args.kwargs
        self.assertEqual((kwargs["project_id"], kwargs["document_path"]), (PROJECT_ID, r"D:\work\a.3dm"))
        self.assertEqual(kwargs["operations"][0]["op"], "box")

    def test_a_refusal_is_answered_in_the_error_shape(self) -> None:
        refusal = StudioError(409, "LIVE_RHINO_NOT_RUNNING", "No running Rhino answers RhinoCode.")
        with patch.object(live_rhino, "list_instances", side_effect=refusal):
            answer = self.client.get("/api/live-rhino/document")
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(answer.json()["code"], "LIVE_RHINO_NOT_RUNNING")

    def test_only_a_local_runtime_reaches_its_machines_rhino(self) -> None:
        settings = self.client.app.state.settings
        object.__setattr__(settings, "mode", "remote")
        self.addCleanup(object.__setattr__, settings, "mode", "local")
        with patch.object(live_rhino, "list_instances", side_effect=AssertionError("Rhino was reached")):
            answer = self.client.get("/api/live-rhino/instances")
        self.assertEqual((answer.status_code, answer.json()["code"]), (403, "LIVE_RHINO_LOCAL_ONLY"))

    def test_opening_needs_the_persons_yes_and_names_the_rhino_that_answers(self) -> None:
        asked = self.client.post("/api/live-rhino/open", json={"confirmed": False})
        self.assertEqual((asked.status_code, asked.json()["code"]), (422, "LIVE_RHINO_NOT_CONFIRMED"))
        instance = live_rhino.LiveRhino("rhinocode_remotepipe_99", 99, "8.28", "site.3dm", r"D:\work\site.3dm", "Top")
        with patch.object(live_rhino, "open_connected", return_value=(instance, True)) as opened:
            answer = self.client.post("/api/live-rhino/open", json={"confirmed": True, "documentPath": r"D:\work\site.3dm"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json(), {"rhino": instance.to_dict(), "opened": True})
        self.assertEqual(opened.call_args.kwargs, {"document_path": r"D:\work\site.3dm"})

    def test_the_routes_are_in_the_runtime_schema(self) -> None:
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/live-rhino/instances", "/api/live-rhino/document", "/api/live-rhino/write", "/api/live-rhino/capture", "/api/live-rhino/open"):
            self.assertIn(path, paths)
        snapshot = paths["/api/live-rhino/snapshot"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        self.assertEqual(snapshot["$ref"], "#/components/schemas/LiveRhinoSnapshotDto", "the snapshot's answer is documented")


if __name__ == "__main__":
    unittest.main()
