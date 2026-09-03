"""The command line resolves one project root, and says which one won."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from archflow_studio_api.main import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    build_parser,
    settings_from_args,
)
from archflow_studio_api.settings import SettingsError


class CommandLineTests(unittest.TestCase):
    def test_project_dir_parses_to_a_path(self) -> None:
        args = build_parser().parse_args(["--project-dir", "x"])

        self.assertEqual(args.project_dir, Path("x"))
        self.assertEqual(args.host, DEFAULT_HOST)
        self.assertEqual(args.port, DEFAULT_PORT)

    def test_the_flag_beats_the_environment(self) -> None:
        args = build_parser().parse_args(["--project-dir", "from-the-flag"])

        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "from-the-environment"},
            clear=False,
        ):
            settings = settings_from_args(args)

        self.assertEqual(settings.project_dir, Path("from-the-flag"))

    def test_without_the_flag_the_environment_is_read(self) -> None:
        args = build_parser().parse_args([])

        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "from-the-environment"},
            clear=False,
        ):
            settings = settings_from_args(args)

        self.assertEqual(settings.project_dir, Path("from-the-environment"))

    def test_neither_source_means_no_project_at_all(self) -> None:
        args = build_parser().parse_args([])

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARCHFLOW_STUDIO_PROJECT_DIR", None)
            with self.assertRaises(SettingsError):
                settings_from_args(args)


if __name__ == "__main__":
    unittest.main()
