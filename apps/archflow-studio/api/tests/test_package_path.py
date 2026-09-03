"""Importing the API package makes the archflow kernel importable, anywhere.

The API's whole job is to delegate to ``archflow``; if that import depended on
the working directory the service would only run from one place.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from archflow_studio_api import REPOSITORY_ROOT


class KernelImportPathTests(unittest.TestCase):
    def test_repository_root_is_on_the_import_path(self) -> None:
        self.assertIn(str(REPOSITORY_ROOT), sys.path)
        self.assertTrue((REPOSITORY_ROOT / "archflow" / "__init__.py").is_file())

    def test_kernel_imports_from_an_unrelated_working_directory(self) -> None:
        api_root = REPOSITORY_ROOT / "apps" / "archflow-studio" / "api"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(api_root)

        with tempfile.TemporaryDirectory() as elsewhere:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import archflow_studio_api\n"
                    "import archflow\n"
                    "print(archflow.__file__)\n",
                ],
                cwd=elsewhere,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(completed.stdout.strip()).resolve().parent,
            REPOSITORY_ROOT / "archflow",
        )


if __name__ == "__main__":
    unittest.main()
