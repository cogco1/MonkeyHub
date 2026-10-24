"""The capability table is the one list a person's Ctrl+K and an agent's index read.

Every entry names an owner the module registry knows, says honestly what it is, and
points only at routes that exist: a Runtime route in the Runtime's schema, a Hub route in
the Hub's. An interface-only group (``kind: "shell"``) has commands and no routes. The
web view of the commands is checked by apps/monkeyhub/web/test/commandTree.test.ts.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for source in ("services/project-runtime/src", "apps/monkeyhub/api"):
    if str(ROOT / source) not in sys.path:
        sys.path.insert(0, str(ROOT / source))

REGISTRY = ROOT / "governance" / "module_registry.json"
KINDS = {"authoring", "output", "manage", "shell"}
STATUSES = {"PRODUCTION", "PARTIAL", "MISSING"}
HOSTS = {"runtime", "hub"}
CHANNELS = {"front", "side", "background"}
COMMAND_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
ENTRYPOINT = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")


def _routes(app) -> set[tuple[str, str]]:
    return {(method.upper(), path) for path, operations in app.openapi()["paths"].items() for method in operations}


class CapabilityRegistryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.capabilities = data["capabilities"]
        cls.modules = {module["module_id"] for module in data["modules"]}
        from monkeyhub_api.app.composition import HubSettings, create_app as hub_app
        from project_runtime.main import create_app as runtime_app
        from project_runtime.settings import StudioSettings

        with tempfile.TemporaryDirectory(prefix="capability-routes-") as directory:
            cls.routes = {
                "runtime": _routes(runtime_app(StudioSettings(project_dir=Path(directory) / "unbound", cad_export="off"))),
                "hub": _routes(hub_app(HubSettings(runtime_root=Path(directory)))),
            }

    def test_each_entry_says_what_it_is_and_who_owns_it(self) -> None:
        seen = set()
        for entry in self.capabilities:
            capability_id = entry["capability_id"]
            with self.subTest(capability=capability_id):
                self.assertNotIn(capability_id, seen, "registered twice")
                seen.add(capability_id)
                self.assertIn(entry["owner"], self.modules)
                self.assertIn(entry["execution_owner"], self.modules)
                self.assertIn(entry["kind"], KINDS)
                self.assertIn(entry["status"], STATUSES)
                self.assertIn(entry.get("host", "runtime"), HOSTS)
                for field in ("purpose", "purpose_zh"):
                    self.assertTrue(str(entry.get(field, "")).strip(), field)
                self.assertTrue(entry.get("goals"), "a capability nobody can search for is not in the tree")

    def test_every_entrypoint_is_a_route_its_host_serves(self) -> None:
        for entry in self.capabilities:
            host = entry.get("host", "runtime")
            for point in entry.get("entrypoints", ()):
                with self.subTest(capability=entry["capability_id"], entrypoint=point):
                    match = ENTRYPOINT.match(point)
                    self.assertIsNotNone(match, "write it as 'METHOD /path'")
                    self.assertIn((match[1], match[2]), self.routes[host], f"not a {host} route")

    def test_an_interface_group_lives_in_the_hub_and_has_commands_instead_of_routes(self) -> None:
        for entry in self.capabilities:
            if entry["kind"] != "shell":
                continue
            with self.subTest(capability=entry["capability_id"]):
                self.assertEqual(entry.get("host"), "hub")
                self.assertEqual(entry.get("entrypoints", []), [])
                self.assertTrue(entry.get("ui", {}).get("commands"))

    def test_commands_have_one_id_each_and_both_labels(self) -> None:
        seen: dict[str, str] = {}
        for entry in self.capabilities:
            ui = entry.get("ui", {})
            for command in ui.get("commands", ()):
                command_id = command.get("id", "")
                with self.subTest(command=command_id):
                    self.assertRegex(command_id, COMMAND_ID)
                    self.assertNotIn(command_id, seen, f"also in {seen.get(command_id)}")
                    seen[command_id] = entry["capability_id"]
                    self.assertTrue(command.get("label", "").strip() and command.get("label_zh", "").strip())
                    self.assertIn(command.get("channel", ui.get("channel", "side")), CHANNELS)

    def test_the_runtime_index_answers_only_with_what_it_performs(self) -> None:
        from project_runtime.application.capability import capability_index

        served = {entry["capability_id"] for entry in capability_index()}
        for entry in self.capabilities:
            with self.subTest(capability=entry["capability_id"]):
                self.assertEqual(entry["capability_id"] in served, entry.get("host", "runtime") == "runtime")


if __name__ == "__main__":
    unittest.main()
