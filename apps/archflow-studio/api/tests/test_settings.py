"""The project root is chosen by the operator, never defaulted in code."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from archflow_studio_api.settings import SettingsError, StudioSettings


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


if __name__ == "__main__":
    unittest.main()
