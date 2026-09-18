"""The Hub is the only writer of a Studio child's environment.

The standalone launcher used to forward runtime.json and the saved preferences
into ARCHFLOW_STUDIO_* variables, and had tests for it. That launcher is gone
(#126); this is the same contract, owned by the Hub: project, CAD backend and
reference run from application settings, intent provider/model/timeout from the
user's saved preferences, and nothing inherited from the shell that started the
Hub.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api.applications import Applications
from monkeyhub_api.models import HubFailure


class StudioChildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 环境 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        self.appdata = self.root / "roaming"
        self.applications = Applications(self.root / "source", self.root / "runtime", hub_port=18790)
        # What a developer's shell may carry: the Hub must replace all of it.
        self.inherited = {
            "APPDATA": str(self.appdata),
            "ARCHFLOW_STUDIO_PROJECT_DIR": "somebody-elses-project",
            "ARCHFLOW_STUDIO_MODE": "remote",
            "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": "anthropic",
            "UNRELATED_SETTING": "kept",
        }

    def settings(self, **fields):
        return ApplicationSettingsDto.model_validate({"projectDir": str(self.project), **fields})

    def save_preferences(self, **preferences):
        (self.appdata / "MonkeyArch").mkdir(parents=True)
        (self.appdata / "MonkeyArch" / "settings.json").write_text(json.dumps(preferences), encoding="utf-8")

    def test_defaults_come_from_settings_and_nothing_from_the_shell(self):
        with patch.dict(os.environ, self.inherited, clear=True):
            command, environment = self.applications._command("studio", self.settings())
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]), (self.root / "source").resolve() / "apps/monkeyhub/run.py")
        self.assertEqual(command[2:], ["--service", "studio", "--host", "127.0.0.1"])
        studio = {key: value for key, value in environment.items() if key.startswith("ARCHFLOW_STUDIO_")}
        self.assertEqual(studio, {
            "ARCHFLOW_STUDIO_MODE": "local",
            "ARCHFLOW_STUDIO_PROJECT_DIR": str(self.project),
            "ARCHFLOW_STUDIO_CAD_EXPORT": "occt",
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": "deterministic",
        })
        self.assertEqual(
            environment["MONKEYMONITOR_DATA_DIR"],
            str((self.root / "runtime").resolve() / "diagnostics" / "monkeymonitor"),
        )
        self.assertEqual(environment["UNRELATED_SETTING"], "kept")

    def test_saved_preferences_and_settings_reach_the_child(self):
        self.save_preferences(intentProvider="codex", intentModel="gpt-5", intentTimeoutS=45.5)
        with patch.dict(os.environ, self.inherited, clear=True):
            _, environment = self.applications._command(
                "studio", self.settings(cadExport="off", referenceRun="run-003"),
            )
        self.assertEqual(environment["ARCHFLOW_STUDIO_CAD_EXPORT"], "off")
        self.assertEqual(environment["ARCHFLOW_STUDIO_REFERENCE_RUN"], "run-003")
        self.assertEqual(environment["ARCHFLOW_STUDIO_INTENT_PROVIDER"], "codex")
        self.assertEqual(environment["ARCHFLOW_STUDIO_INTENT_MODEL"], "gpt-5")
        self.assertEqual(float(environment["ARCHFLOW_STUDIO_INTENT_TIMEOUT_S"]), 45.5)

    def test_a_folder_without_a_manifest_is_refused_but_no_web_build_is_required(self):
        (self.project / "project.json").unlink()
        with patch.dict(os.environ, self.inherited, clear=True), self.assertRaises(HubFailure) as refused:
            self.applications._command("studio", self.settings())
        self.assertEqual(refused.exception.error.code, "PROJECT_REQUIRED")
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, self.inherited, clear=True):
            command, _ = self.applications._command("studio", self.settings())
        self.assertNotIn("--web-dir", command)


if __name__ == "__main__":
    unittest.main()
