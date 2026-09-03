"""The committed OpenAPI snapshot is the contract the web client reads."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from tools_openapi import SNAPSHOT_PATH


class OpenApiSnapshotTests(unittest.TestCase):
    def test_committed_snapshot_matches_the_live_schema(self) -> None:
        app = create_app(
            StudioSettings(project_dir=Path("unbound-placeholder"))
        )
        committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            committed,
            app.openapi(),
            "apps/archflow-studio/api/openapi.json is stale. Re-run "
            "py -3.12 apps/archflow-studio/api/tools_openapi.py "
            "and commit the regenerated snapshot.",
        )


if __name__ == "__main__":
    unittest.main()
