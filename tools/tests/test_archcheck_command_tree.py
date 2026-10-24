"""The command tree is a view of the registry's capabilities, and its commands are well formed."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.governance.archcheck import COMMAND_TREE, check_command_tree, render_command_tree


def _capability(capability_id, *commands, **ui):
    return {"capability_id": capability_id, "owner": "hub.shell", "kind": "shell", "status": "PRODUCTION", "host": "hub",
            "purpose": "Arrange the window.", "purpose_zh": "安排窗口。", "goals": ["arrange"],
            "ui": {"surface": "hub", "group": "shell", **ui, "commands": list(commands)}}


def _command(command_id, **fields):
    return {"id": command_id, "label": "Open", "label_zh": "打开", **fields}


class CommandTreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "governance").mkdir()

    def _write(self, *capabilities, render=True):
        registry = {"schema": "ArchFlowModuleRegistry@1", "modules": [], "capabilities": list(capabilities)}
        (self.root / "governance" / "module_registry.json").write_text(json.dumps(registry), encoding="utf-8")
        if render:
            target = self.root / COMMAND_TREE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_command_tree(registry), encoding="utf-8")
        return registry

    def _codes(self):
        return [finding.code for finding in check_command_tree(self.root)]

    def test_a_rendered_tree_of_well_formed_commands_passes(self):
        self._write(_capability("shell.pages", _command("view.settings.open"), _command("view.drawings", place="rail")))
        self.assertEqual(self._codes(), [])

    def test_the_view_carries_what_the_palette_reads_and_fills_defaults_from_the_group(self):
        registry = self._write(_capability("shell.pages", _command("view.settings.open", aliases=["preferences"]),
                                           channel="front", tier="advanced"))
        text = render_command_tree(registry)
        tree = json.loads(text[text.index("= ") + 2:text.rindex(";")])
        self.assertEqual(tree["capabilities"][0]["id"], "shell.pages")
        self.assertEqual(tree["capabilities"][0]["host"], "hub")
        command = tree["commands"][0]
        self.assertEqual((command["capability"], command["group"], command["channel"], command["tier"]),
                         ("shell.pages", "shell", "front", "advanced"))
        self.assertEqual(command["terms"], ["preferences"])
        self.assertEqual((command["place"], command["when"], command["args"]), (None, [], []))

    def test_a_registry_change_without_render_map_is_stale(self):
        self._write(_capability("shell.pages", _command("view.settings.open")))
        self._write(_capability("shell.pages", _command("view.settings.open"), _command("view.usage.open")), render=False)
        self.assertEqual(self._codes(), ["COMMAND_TREE_STALE"])

    def test_a_missing_view_is_stale_once_any_command_is_registered(self):
        self._write(_capability("shell.pages", _command("view.settings.open")), render=False)
        self.assertEqual(self._codes(), ["COMMAND_TREE_STALE"])

    def test_no_commands_need_no_view(self):
        self._write({"capability_id": "frame.set_level", "owner": "project_runtime.intent", "ui": {"preview": "MonkeyArch"}}, render=False)
        self.assertEqual(self._codes(), [])

    def test_an_id_is_snake_case_dotted_and_registered_once(self):
        self._write(_capability("shell.pages", _command("view.settings.open"), _command("View.Settings")),
                    _capability("shell.layout", _command("view.settings.open")))
        self.assertEqual(self._codes(), ["COMMAND_ID", "COMMAND_ID"])

    def test_labels_channel_and_tier_take_their_registered_values(self):
        self._write(_capability("shell.pages", _command("view.settings.open", label_zh="Open"),
                                _command("view.usage.open", channel="toolbar"), _command("view.render.open", tier="expert")))
        self.assertEqual(self._codes(), ["COMMAND_FIELD", "COMMAND_FIELD", "COMMAND_FIELD"])

    def test_a_command_has_at_most_one_standing_place(self):
        self._write(_capability("shell.pages", _command("view.settings.open", place=["menu", "rail"])))
        self.assertEqual(self._codes(), ["COMMAND_PLACE"])


if __name__ == "__main__":
    unittest.main()
