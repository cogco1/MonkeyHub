"""Round-1 settings: the project root is chosen, never defaulted in code."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import SettingsError, StudioSettings


class SettingsFromEnvironmentTests(unittest.TestCase):
    def test_missing_project_dir_names_the_variable_it_wants(self) -> None:
        with patch.dict(
            os.environ,
            {},
            clear=False,
        ):
            os.environ.pop("ARCHFLOW_STUDIO_PROJECT_DIR", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()

        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", str(raised.exception))

    def test_project_dir_comes_from_the_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"},
            clear=False,
        ):
            os.environ.pop("ARCHFLOW_STUDIO_RHINO_EXPORT", None)
            os.environ.pop("ARCHFLOW_STUDIO_POWERSHELL", None)
            settings = StudioSettings.from_env()

        self.assertEqual(settings.project_dir, Path("some/project"))
        self.assertFalse(settings.rhino_export)
        self.assertIsNone(settings.powershell)

    def test_rhino_export_is_on_only_for_the_exact_string_one(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_RHINO_EXPORT": "1",
            },
            clear=False,
        ):
            enabled = StudioSettings.from_env()
        self.assertTrue(enabled.rhino_export)

        for value in ("0", "true", "TRUE", "yes", ""):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {
                        "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                        "ARCHFLOW_STUDIO_RHINO_EXPORT": value,
                    },
                    clear=False,
                ):
                    settings = StudioSettings.from_env()
                self.assertFalse(settings.rhino_export)

    def test_powershell_is_an_optional_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_POWERSHELL": "C:/pwsh/pwsh.exe",
            },
            clear=False,
        ):
            settings = StudioSettings.from_env()

        self.assertEqual(settings.powershell, Path("C:/pwsh/pwsh.exe"))


class AppFactoryTests(unittest.TestCase):
    def test_create_app_does_not_touch_the_project_directory(self) -> None:
        settings = StudioSettings(
            project_dir=Path("no-such-directory-anywhere-on-this-machine")
        )
        self.assertFalse(settings.project_dir.exists())

        app = create_app(settings)

        self.assertIs(app.state.settings, settings)
        self.assertFalse(settings.project_dir.exists())


if __name__ == "__main__":
    unittest.main()
