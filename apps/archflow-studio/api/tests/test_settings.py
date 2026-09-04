"""The project root is chosen by the operator, never defaulted in code."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from archflow_studio_api.settings import (
    LOCAL_MODE,
    REMOTE_MODE,
    SettingsError,
    StudioSettings,
)


class SettingsTests(unittest.TestCase):
    def test_missing_project_dir_refuses_and_names_the_variable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARCHFLOW_STUDIO_PROJECT_DIR", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", str(raised.exception))

    def test_project_dir_comes_from_the_environment(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            self.assertEqual(StudioSettings.from_env().project_dir, Path("some/project"))

    def test_the_reference_run_is_configured_and_otherwise_unset(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            os.environ.pop("ARCHFLOW_STUDIO_REFERENCE_RUN", None)
            self.assertIsNone(StudioSettings.from_env().reference_run)
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_REFERENCE_RUN": " run-002 ",
            },
            clear=False,
        ):
            self.assertEqual(StudioSettings.from_env().reference_run, "run-002")

    def test_the_mode_bind_and_token_are_read_from_the_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"},
            clear=False,
        ):
            for name in (
                "ARCHFLOW_STUDIO_MODE",
                "ARCHFLOW_STUDIO_BIND",
                "ARCHFLOW_STUDIO_TOKEN",
                "ARCHFLOW_STUDIO_ORIGINS",
            ):
                os.environ.pop(name, None)
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, LOCAL_MODE)
        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertIsNone(settings.api_token)
        self.assertEqual(settings.origins, ())
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": " remote ",
                "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
                "ARCHFLOW_STUDIO_TOKEN": " s3cret ",
                "ARCHFLOW_STUDIO_ORIGINS": "https://a.example, https://b.example ,",
            },
            clear=False,
        ):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, REMOTE_MODE)
        self.assertEqual(settings.bind_host, "0.0.0.0")
        self.assertEqual(settings.api_token, "s3cret")
        self.assertEqual(
            settings.origins, ("https://a.example", "https://b.example")
        )

    def test_remote_mode_from_the_environment_refuses_without_a_token(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": "remote",
            },
            clear=False,
        ):
            os.environ.pop("ARCHFLOW_STUDIO_TOKEN", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_TOKEN", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
