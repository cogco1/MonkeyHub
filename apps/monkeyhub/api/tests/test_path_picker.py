"""A person chooses a folder or a file in Windows' own dialog instead of typing its path.

The dialog is a PowerShell child that is told what to ask through its environment; here it is replaced by a
runner that answers the way the child does (the chosen path on stdout, nothing when cancelled).
"""

from __future__ import annotations

import base64
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from monkeyhub_api import path_picker  # noqa: E402
from monkeyhub_api.app.composition import HubSettings, create_app  # noqa: E402
from monkeyhub_api.models import HubFailure  # noqa: E402


class FakeDialog:
    def __init__(self, answer: str = "", returncode: int = 0):
        self.answer, self.returncode = answer, returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, self.returncode, self.answer, "")


class PathPickerTests(unittest.TestCase):
    def test_the_dialog_is_asked_through_the_environment_and_the_chosen_path_comes_back(self):
        dialog = FakeDialog("D:\\projects\\site\n")
        self.assertEqual(path_picker.pick("folder", "Choose the project folder", runner=dialog), "D:\\projects\\site")
        args, kwargs = dialog.calls[0]
        self.assertEqual(args[:5], ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand"])
        self.assertEqual((kwargs["env"]["MH_PICK_KIND"], kwargs["env"]["MH_PICK_TITLE"]), ("folder", "Choose the project folder"))
        script = base64.b64decode(args[5]).decode("utf-16-le")
        self.assertNotIn("Choose the project folder", script, "what is asked is never part of the script")
        self.assertIn("FolderBrowserDialog", script)

    def test_the_child_is_given_no_stdin(self):
        with patch.object(path_picker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            path_picker.pick("folder", "x", runner=None) if sys.platform == "win32" else None
        if sys.platform == "win32":
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL, "the child never waits on the pipe the desktop holds")

    def test_a_cancelled_dialog_chooses_nothing(self):
        self.assertIsNone(path_picker.pick("file", "Choose the archive", file_filter="archive", runner=FakeDialog("")))

    def test_a_file_dialog_offers_the_named_kind_of_file_only(self):
        dialog = FakeDialog("D:\\a.zip")
        path_picker.pick("file", "Choose the archive", file_filter="archive", runner=dialog)
        self.assertEqual(dialog.calls[0][1]["env"]["MH_PICK_FILTER"], path_picker.FILTERS["archive"])
        self.assertIn("OpenFileDialog", base64.b64decode(dialog.calls[0][0][5]).decode("utf-16-le"))

    def test_a_dialog_that_fails_or_cannot_start_says_to_type_the_path(self):
        with self.assertRaises(HubFailure) as failed:
            path_picker.pick("folder", "x", runner=FakeDialog("", returncode=1))
        self.assertEqual((failed.exception.status, failed.exception.error.code), (502, "PATH_PICKER_FAILED"))

        def missing(*_args, **_kwargs):
            raise FileNotFoundError("powershell.exe")

        with self.assertRaises(HubFailure) as unavailable:
            path_picker.pick("folder", "x", runner=missing)
        self.assertEqual(unavailable.exception.error.code, "PATH_PICKER_UNAVAILABLE")
        self.assertIn("Type the path", unavailable.exception.error.detail)

    def test_another_system_has_no_such_dialog(self):
        with patch.object(path_picker.sys, "platform", "linux"), self.assertRaises(HubFailure) as unavailable:
            path_picker.pick("folder", "x")
        self.assertEqual((unavailable.exception.status, unavailable.exception.error.code), (501, "PATH_PICKER_UNAVAILABLE"))


class PathPickerRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub picker ")
        self.addCleanup(temporary.cleanup)
        self.app = create_app(HubSettings(runtime_root=Path(temporary.name) / "runtime", port=18792), source_root=ROOT)

    def test_a_person_at_this_computer_gets_the_dialog(self):
        client = TestClient(self.app, base_url="http://127.0.0.1:18792", client=("127.0.0.1", 50000))
        with patch.object(path_picker, "pick", return_value="D:\\projects\\site") as pick:
            answer = client.post("/api/chat/pick-path", json={"kind": "folder", "title": "Choose the project folder"})
        self.assertEqual((answer.status_code, answer.json()), (200, {"path": "D:\\projects\\site"}))
        pick.assert_called_once_with("folder", "Choose the project folder", file_filter="any")

    def test_closing_the_dialog_answers_null(self):
        client = TestClient(self.app, base_url="http://127.0.0.1:18792", client=("127.0.0.1", 50000))
        with patch.object(path_picker, "pick", return_value=None):
            answer = client.post("/api/chat/pick-path", json={"kind": "file", "title": "Choose the archive", "filter": "archive"})
        self.assertEqual(answer.json(), {"path": None})

    def test_nobody_else_can_open_a_dialog_on_this_screen(self):
        client = TestClient(self.app, base_url="http://127.0.0.1:18792", client=("203.0.113.9", 50000))
        with patch.object(path_picker, "pick", side_effect=AssertionError("a dialog was opened")):
            answer = client.post("/api/chat/pick-path", json={"kind": "folder", "title": "x"})
        self.assertEqual((answer.status_code, answer.json()["code"]), (403, "LOCAL_PICKER_REQUIRED"))

    def test_the_request_is_checked(self):
        client = TestClient(self.app, base_url="http://127.0.0.1:18792", client=("127.0.0.1", 50000))
        for body in ({"kind": "drive", "title": "x"}, {"kind": "folder", "title": ""}, {"kind": "file", "title": "x", "filter": "*.*"},
                     {"kind": "folder", "title": "x", "start": "C:\\"}):
            with self.subTest(body=body):
                self.assertEqual(client.post("/api/chat/pick-path", json=body).status_code, 422)


if __name__ == "__main__":
    unittest.main()
