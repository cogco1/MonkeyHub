"""Hub integration status is local discovery, never host or project execution."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient
from monkeycad.integration_packs import IntegrationPackManager
from monkeycad.discovery import Discovery, Installation
from monkeyhub_api.main import HubSettings, create_app


class IntegrationApiTests(unittest.TestCase):
    def test_status_is_lazy_cached_path_free_and_rescans_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, project = root / "source", root / "project"
            source.mkdir()
            project.mkdir()
            (source / "source-version.txt").write_text("a" * 40, encoding="utf-8")
            keep = project / "project.json"
            keep.write_text('{"identity":"keep-project"}', encoding="utf-8")
            discovery_calls = []

            class DiscoveryProvider:
                def discover(self, product):
                    discovery_calls.append(product)
                    return Discovery("Windows", (Installation(
                        product, root / "private-installations" / product / f"{product}.exe",
                        "8" if product == "rhino" else None, "standard-install-directory"),))

            manager = IntegrationPackManager(discovery=DiscoveryProvider())
            with patch.dict(os.environ, {"APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local")}):
                app = create_app(HubSettings(runtime_root=root / "runtime", port=9125), source_root=source)
                app.state.integrations = manager
                self.assertEqual(discovery_calls, [])
                # No context manager: lifespan would intentionally start Monitor.
                client = TestClient(app, base_url="http://127.0.0.1:9125")
                self.addCleanup(client.close)
                self.addCleanup(app.state.updates.shutdown)
                before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                with patch.object(subprocess, "Popen", side_effect=AssertionError("status launched a process")), \
                     patch.object(subprocess, "run", side_effect=AssertionError("status ran a process")), \
                     patch.object(ctypes, "CDLL", side_effect=AssertionError("status loaded vendor DLL")), \
                     patch.object(socket, "create_connection", side_effect=AssertionError("status used network")), \
                     patch.object(Path, "write_text", side_effect=AssertionError("status wrote a file")), \
                     patch.object(Path, "write_bytes", side_effect=AssertionError("status wrote a file")):
                    response = client.get("/api/integrations")
                    self.assertEqual(response.status_code, 200)
                    first = response.json()
                    self.assertEqual(len(discovery_calls), 3)
                    self.assertEqual(client.get("/api/integrations").json(), first)
                    self.assertEqual(client.get("/api/integrations?rescan=false").json(), first)
                    self.assertEqual(len(discovery_calls), 3)
                    self.assertEqual(client.get("/api/integrations?rescan=true").json(), first)
                    self.assertEqual(len(discovery_calls), 6)
                for pack in first["packs"]:
                    self.assertTrue(pack["software"]["detected"])
                    self.assertFalse(any(row["status"] == "ready" for row in pack["capabilities"]))
                    self.assertFalse(any(row["available"] for row in pack["workflows"]))
                self.assertNotIn(root.name, json.dumps(first))
                self.assertEqual({path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
                self.assertEqual(keep.read_text(encoding="utf-8"), '{"identity":"keep-project"}')


if __name__ == "__main__":
    unittest.main()
