"""Every row of the entry audit says where it went, and the command tree agrees.

docs/audits/hub-entries.md lists the 711 entries the web interface showed on 2026-10-21. Its last two
columns say which capability group and which command of governance/module_registry.json now answers
for each row, or why none does. A command's `entries` name the same rows, so a person looking for a
button finds its command, and a command names the buttons it replaced (plan 1.5, ADR-014 B1).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.governance.archcheck import command_tree

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs" / "audits" / "hub-entries.md"
REGISTRY = ROOT / "governance" / "module_registry.json"
SURFACE_CODE = {"Hub 外壳": "壳", "Hub 弹窗": "窗", "项目栏（各工作面共用）": "栏", "Modeling": "模", "Board": "板", "Layout": "排",
                "文档页": "文", "Drawings": "图", "Render": "渲", "Design tree": "树", "Fabrication": "制", "Usage": "量"}
# Why a row has no command: done by the machine, retired, drawn by Excalidraw, a feature still to come,
# or a shell row the shell lane has yet to register.
MARKERS = {"后台", "退役", "Excalidraw", "新功能", "待登记"}
CODE = re.compile(r"^(壳|窗|栏|模|板|排|文|图|渲|树|制|量)([1-9]\d*)$")


def audit_rows() -> list[dict[str, str]]:
    lines = AUDIT.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("| 工作面 | # | 位置 |"))
    header = [cell.strip() for cell in lines[start].strip("|").split("|")]
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("| "):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        row = dict(zip(header, cells))
        row["代码"] = f'{SURFACE_CODE[row["工作面"]]}{row["#"]}'
        rows.append(row)
    return rows


def commands_of(row: dict[str, str]) -> list[str]:
    """The command ids a row names: one, or several for a row that counted a set of options."""
    cell = row.get("命令", "")
    return [] if cell in MARKERS else [command for command in cell.split("、") if command]


class HubEntriesCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = audit_rows()
        cls.tree = command_tree(json.loads(REGISTRY.read_text(encoding="utf-8")))
        cls.commands = {command["id"]: command for command in cls.tree["commands"]}

    def test_the_audit_keeps_its_711_rows_and_their_codes(self) -> None:
        self.assertEqual(len(self.rows), 711)
        self.assertEqual(len({row["代码"] for row in self.rows}), 711)

    def test_every_row_names_its_commands_or_why_it_has_none(self) -> None:
        for row in self.rows:
            with self.subTest(row=row["代码"]):
                cell = row.get("命令", "")
                if cell in MARKERS:
                    continue
                unknown = [command for command in commands_of(row) if command not in self.commands]
                self.assertTrue(cell and not unknown, f"{cell!r}: {unknown or 'empty'} is neither a command nor a marker")

    def test_only_shell_rows_wait_for_registration(self) -> None:
        waiting = [row["代码"] for row in self.rows if row.get("命令") == "待登记" and row["代码"][0] not in "壳窗栏"]
        self.assertEqual(waiting, [])

    def test_a_row_and_its_commands_point_at_each_other(self) -> None:
        for row in self.rows:
            commands = [self.commands[command] for command in commands_of(row) if command in self.commands]
            if not commands:
                continue
            with self.subTest(row=row["代码"]):
                for command in commands:
                    self.assertIn(row["代码"], command["entries"], command["id"])
                self.assertEqual(set(row.get("能力组", "").split("、")), {command["capability"] for command in commands})

    def test_every_entry_a_command_names_is_an_audit_row_that_names_it(self) -> None:
        by_code = {row["代码"]: row for row in self.rows}
        for command in self.tree["commands"]:
            for code in command["entries"]:
                with self.subTest(command=command["id"], entry=code):
                    self.assertRegex(code, CODE)
                    self.assertIn(code, by_code)
                    self.assertIn(command["id"], commands_of(by_code[code]))


if __name__ == "__main__":
    unittest.main()
