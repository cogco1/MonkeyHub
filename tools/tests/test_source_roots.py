"""Each checkout imports its own code: its Python source roots go first (#488)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.dev import source_roots

REPO = Path(__file__).resolve().parents[2]


class SourceRootsTests(unittest.TestCase):
    def directory(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def checkout(self, *listed: str) -> Path:
        root = self.directory()
        (root / "governance").mkdir()
        (root / "governance/architecture_policy.json").write_text(
            json.dumps({"python_source_roots": list(listed)}), encoding="utf-8")
        return root

    def test_the_policy_names_the_roots_relative_to_the_checkout(self) -> None:
        root = self.checkout(".", "apps/hub/api", "packages/drawing/src")
        self.assertEqual(source_roots.roots(root),
                         [str(root), str(root / "apps/hub/api"), str(root / "packages/drawing/src")])

    def test_a_missing_root_goes_in_front_and_a_listed_one_keeps_its_place(self) -> None:
        root = self.checkout(".", "apps/hub/api", "packages/drawing/src")
        elsewhere = str(root.parent / "another-checkout")
        with patch.object(sys, "path", [elsewhere, str(root / "apps/hub/api")]):
            source_roots.put_first(root)
            expected = [str(root), str(root / "packages/drawing/src"), elsewhere, str(root / "apps/hub/api")]
            self.assertEqual(sys.path, expected)
            source_roots.put_first(root)
            self.assertEqual(sys.path, expected)

    def test_an_installed_bundle_keeps_the_path_its_pth_file_gave_it(self) -> None:
        bundle = self.directory()
        with patch.object(sys, "path", ["bundle-root", "bundle-root/apps/monkeyhub/api"]):
            source_roots.put_first(bundle)
            self.assertEqual(sys.path, ["bundle-root", "bundle-root/apps/monkeyhub/api"])
        self.assertEqual(source_roots.roots(bundle), [])

    def test_a_process_started_elsewhere_imports_this_checkouts_packages(self) -> None:
        # Neither PYTHONPATH nor a package installed from another checkout decides.
        script = ("import sys; from pathlib import Path; checkout = Path(sys.argv[1]); "
                  "sys.path.insert(0, str(checkout)); from tools.dev import source_roots; "
                  "source_roots.put_first(checkout); import archflow, monkeydiagram; "
                  "print(archflow.__file__); print(monkeydiagram.__file__)")
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        finished = subprocess.run([sys.executable, "-c", script, str(REPO)], cwd=self.directory(), env=environment,
                                  capture_output=True, text=True, check=True)
        locations = finished.stdout.splitlines()
        self.assertEqual(len(locations), 2, finished.stdout)
        for location in locations:
            self.assertTrue(Path(location).resolve().is_relative_to(REPO), location)


if __name__ == "__main__":
    unittest.main()
