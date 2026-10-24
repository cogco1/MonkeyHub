"""Clickable controls name their command, the unnamed ones only become fewer, and places stay within budget."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.governance.archcheck import (
    COMMAND_TREE,
    ArchitecturePolicyError,
    check_changed_ui_baseline,
    check_command_tree,
    check_ui_entries,
    render_command_tree,
    unregistered_ui_entries,
)

SOURCE = "apps/monkeyhub/web/src"


def _policy(baseline=None, budgets=None, **fields):
    return {"ui_entries": {"source_roots": [SOURCE], "wrappers": ["MenuCommand"], "view_rows": [],
                           "baseline": baseline or {}, "budgets": budgets or {}, **fields}}


def _command(command_id, place=None, surface="board"):
    return {"id": command_id, "label": "Open", "label_zh": "打开", "surface": surface, "place": place}


def _ui_command(command_id, **fields):
    return {"id": command_id, "label": "Open", "label_zh": "打开", **fields}


class UnregisteredEntryTests(unittest.TestCase):
    def test_a_clicked_button_without_a_command_is_reported_by_line(self):
        text = '<div>\n  <button type="button" onClick={() => go()}>Go</button>\n</div>'
        self.assertEqual(unregistered_ui_entries(text), [2])

    def test_a_named_button_a_plain_button_and_a_submit_button_pass(self):
        text = ('<button data-command="board.export.run" onClick={run}>Export</button>'
                '<button type="button">Inert</button><button type="submit">Send</button>')
        self.assertEqual(unregistered_ui_entries(text), [])

    def test_wrappers_and_menu_items_count_like_buttons(self):
        text = ('<MenuCommand onClick={open}>More</MenuCommand>\n'
                '<li role="menuitem" tabIndex={0}>Rename</li>\n'
                '<li role={"menuitemcheckbox"} data-command="view.menu.toggle">Pin</li>\n'
                '<Panel onClick={close} />')
        self.assertEqual(unregistered_ui_entries(text, ["MenuCommand"]), [1, 2])

    def test_elements_with_a_control_role_and_a_click_count(self):
        text = ('<li role="treeitem" onClick={pick}>Row</li>\n'
                '<div role="option" onClick={run} data-command="view.menu.toggle">Run</div>\n'
                '<div role="button" tabIndex={0}>Inert</div>\n'
                '<div role="region" onClick={focus}>Area</div>')
        self.assertEqual(unregistered_ui_entries(text), [1])

    def test_arrows_comparisons_and_quoted_angles_do_not_end_an_element_early(self):
        text = ('<button title="a > b" onClick={() => setZoom((value) => value > 1 ? value / 2 : value)}\n'
                '  data-command="view.canvas.zoom_out">−</button>')
        self.assertEqual(unregistered_ui_entries(text), [])

    def test_type_parameters_and_comparisons_are_not_controls(self):
        text = "const [x, setX] = useState<number>(0); if (a <b) run(); function f<T extends string>(v: T) {}"
        self.assertEqual(unregistered_ui_entries(text), [])

    def test_comments_strings_templates_and_regular_expressions_hold_no_controls(self):
        text = ('// a <button onClick={x}> used to live here\n'
                '/** e.g. <button onClick={open}>More</button> */\n'
                'const doc = "<button onClick={x}>", other = \'<button onClick={y}>\';\n'
                'const html = `<button onClick=${run}>`;\n'
                'const path = value.replace(/^"(.*)"$/, "$1");\n'
                'const view = <div>{/* <button onClick={old}> */}<button onClick={go}>Go</button></div>;')
        self.assertEqual(unregistered_ui_entries(text), [6])

    def test_jsx_text_with_apostrophes_or_addresses_hides_nothing(self):
        text = ("const view = <p>Don't stop · http://example.test</p>;\n"
                "const next = <section><b>it's</b> fine <button onClick={go}>Won't</button></section>;")
        self.assertEqual(unregistered_ui_entries(text), [2])

    def test_a_generic_arrow_function_is_not_an_element(self):
        text = ("const queue = useCallback(<T,>(write: () => Promise<T>): Promise<T> => write(), []);\n"
                "const view = <><button onClick={go}>Go</button></>;")
        self.assertEqual(unregistered_ui_entries(text), [2])


class BaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / SOURCE).mkdir(parents=True)

    def _source(self, name, unnamed, named=0):
        body = "\n".join(["<button onClick={a}>A</button>"] * unnamed + ['<button data-command="x.y" onClick={a}>B</button>'] * named)
        path = self.root / SOURCE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def _findings(self, policy):
        return [(finding.code, finding.message) for finding in check_ui_entries(self.root, policy)]

    def test_files_within_their_baseline_pass(self):
        self._source("Shell.tsx", 3)
        self._source("features/Named.tsx", 0, named=4)
        self.assertEqual(self._findings(_policy({f"{SOURCE}/Shell.tsx": 3})), [])

    def test_a_new_unnamed_control_is_reported(self):
        self._source("Shell.tsx", 4)
        self._source("New.tsx", 1)
        codes = [code for code, _ in self._findings(_policy({f"{SOURCE}/Shell.tsx": 3}))]
        self.assertEqual(codes, ["UI_ENTRY_UNREGISTERED", "UI_ENTRY_UNREGISTERED"])

    def test_a_baseline_above_the_file_must_be_lowered(self):
        self._source("Shell.tsx", 1)
        findings = self._findings(_policy({f"{SOURCE}/Shell.tsx": 3}))
        self.assertEqual(findings, [("UI_ENTRY_BASELINE", f"ui_entries.baseline allows {SOURCE}/Shell.tsx 3 unnamed controls, it has 1; lower the baseline to 1")])

    def test_a_baseline_for_a_removed_file_is_reported(self):
        codes = [code for code, _ in self._findings(_policy({f"{SOURCE}/Gone.tsx": 2}))]
        self.assertEqual(codes, ["UI_ENTRY_BASELINE"])

    def test_command_tree_rows_count_only_outside_the_view_rows(self):
        row = '<div role="option" data-command-view onClick={run}>Run</div>'
        for name in ("features/commands/CommandPalette.tsx", "Shell.tsx"):
            path = self.root / SOURCE / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row, encoding="utf-8")
        findings = list(check_ui_entries(self.root, _policy(view_rows=[f"{SOURCE}/features/commands/"])))
        self.assertEqual([(finding.code, finding.path) for finding in findings], [("UI_ENTRY_UNREGISTERED", f"{SOURCE}/Shell.tsx")])

    def test_installed_packages_are_not_scanned(self):
        self._source("node_modules/lib/Widget.tsx", 5)
        self.assertEqual(self._findings(_policy()), [])

    def test_a_malformed_block_fails_rather_than_passing(self):
        with self.assertRaises(ArchitecturePolicyError):
            list(check_ui_entries(self.root, _policy(baseline={f"{SOURCE}/Shell.tsx": -1})))
        with self.assertRaises(ArchitecturePolicyError):
            list(check_ui_entries(self.root, {"ui_entries": {"source_roots": SOURCE}}))

    def test_a_policy_without_the_block_checks_nothing(self):
        self._source("Shell.tsx", 2)
        self.assertEqual(self._findings({}), [])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / SOURCE).mkdir(parents=True)
        (self.root / "governance").mkdir()

    def _registry(self, *commands):
        capability = {"capability_id": "board.organize", "owner": "hub.shell", "ui": {"commands": list(commands)}}
        registry = {"schema": "ArchFlowModuleRegistry@1", "modules": [], "capabilities": [capability]}
        (self.root / "governance" / "module_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    def _codes(self, budgets):
        return [finding.code for finding in check_ui_entries(self.root, _policy(budgets=budgets))]

    def test_places_are_counted_per_surface_and_menu(self):
        self._registry(_command("a.b.one", "menu:board_more"), _command("a.b.two", "menu:board_more"),
                       _command("a.b.three", "menu:export"), _command("a.b.four", "menu:board_more", surface="modeling"),
                       _command("a.b.five"))
        self.assertEqual(self._codes({"menu": 2}), [])

    def test_a_place_over_its_budget_is_reported(self):
        self._registry(*(_command(f"a.b.c{index}", "project_bar") for index in range(5)))
        self.assertEqual(self._codes({"project_bar": 4}), ["UI_ENTRY_BUDGET"])

    def test_a_place_kind_without_a_budget_is_reported(self):
        self._registry(_command("a.b.one", "toolbar"))
        self.assertEqual(self._codes({"project_bar": 4}), ["UI_ENTRY_BUDGET"])


class ChangedBaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        (self.root / "governance").mkdir()

    def _git(self, *args):
        subprocess.run(("git", *args), cwd=self.root, check=True, capture_output=True)

    def _commit(self, baseline, message):
        (self.root / "governance" / "architecture_policy.json").write_text(json.dumps(_policy(baseline)), encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def _codes(self):
        return [finding.code for finding in check_changed_ui_baseline(self.root, "main")]

    def test_lowering_the_baseline_passes(self):
        self._commit({"a.tsx": 3}, "base")
        self._git("checkout", "-q", "-b", "lane")
        self._commit({"a.tsx": 1}, "fewer")
        self.assertEqual(self._codes(), [])

    def test_raising_it_or_adding_a_file_is_reported_even_after_a_lower_step(self):
        self._commit({"a.tsx": 3}, "base")
        self._git("checkout", "-q", "-b", "lane")
        self._commit({"a.tsx": 1}, "fewer")
        self._commit({"a.tsx": 4, "b.tsx": 1}, "more")
        self.assertEqual(self._codes(), ["UI_ENTRY_BASELINE", "UI_ENTRY_BASELINE"])



class CommandUiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "governance").mkdir()

    def _codes(self, *commands, **ui):
        capability = {"capability_id": "board.organize", "owner": "hub.shell", "purpose": "Arrange.", "purpose_zh": "整理。",
                      "ui": {"surface": "board", "group": "board", **ui, "commands": list(commands)}}
        registry = {"schema": "ArchFlowModuleRegistry@1", "modules": [], "capabilities": [capability]}
        (self.root / "governance" / "module_registry.json").write_text(json.dumps(registry), encoding="utf-8")
        target = self.root / COMMAND_TREE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_command_tree(registry), encoding="utf-8")
        return [finding.code for finding in check_command_tree(self.root)]

    def test_registered_fields_pass(self):
        self.assertEqual(self._codes(_ui_command("board.export.run", entries=["板15"], args=["format"], opens=False,
                                              aliases=["export"], shortcut=None)), [])

    def test_an_unknown_field_is_reported(self):
        self.assertEqual(self._codes(_ui_command("board.export.run", entry=["板15"])), ["COMMAND_UI"])
        self.assertEqual(self._codes(_ui_command("board.export.run"), colour="red"), ["COMMAND_UI"])

    def test_entries_name_audit_rows_and_a_set_of_options_shares_one(self):
        self.assertEqual(self._codes(_ui_command("board.export.run", entries=["B15"])), ["COMMAND_UI"])
        self.assertEqual(self._codes(_ui_command("view.model_camera.top", entries=["模10"]),
                                     _ui_command("view.model_camera.front", entries=["模10"])), [])

    def test_arguments_are_camel_case_and_opens_is_a_flag(self):
        self.assertEqual(self._codes(_ui_command("board.export.run", args=["stage_ref"], opens="yes")), ["COMMAND_UI", "COMMAND_UI"])

    def test_an_in_place_command_is_a_flag_with_no_standing_place_or_shortcut(self):
        self.assertEqual(self._codes(_ui_command("chat.composer.stop", in_place=True),
                                     _ui_command("chat.composer.send", in_place=False, place="composer")), [])
        self.assertEqual(self._codes(_ui_command("chat.composer.stop", in_place="yes")), ["COMMAND_UI"])
        self.assertEqual(self._codes(_ui_command("chat.composer.stop", in_place=True, place="composer")), ["COMMAND_UI"])
        self.assertEqual(self._codes(_ui_command("chat.composer.stop", in_place=True, shortcut="Esc")), ["COMMAND_UI"])

if __name__ == "__main__":
    unittest.main()
