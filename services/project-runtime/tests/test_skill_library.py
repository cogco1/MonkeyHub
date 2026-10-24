"""Skill packages shipped with MonkeyHub and the user's own, registered into a project as its skills.

A package is a folder (skill.json, SKILL.md, text files). Importing one adds a
version only when its content differs from the project's current version, and
grants nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import skill_library
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "skill-library"
RUN = "import sys\nprint('ok')\n"


def write_package(folder: Path, *, script: str = RUN, access=None, frontmatter: bool = False) -> None:
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "skill.json").write_text(json.dumps({
        "name": folder.name, "description": f"{folder.name} from a package.", "entry": "scripts/run.py",
        "manifest": {"inputs": ["image"], "outputs": ["card.pdf"]}, "access": access,
    }), encoding="utf-8")
    body = "Run it on one image.\n"
    (folder / "SKILL.md").write_text(("---\nname: x\n---\n" if frontmatter else "") + body, encoding="utf-8")
    (folder / "scripts" / "run.py").write_text(script, encoding="utf-8")
    (folder / "params").mkdir(exist_ok=True)
    (folder / "params" / "default.json").write_text("{}\n", encoding="utf-8")
    (folder / "scripts" / "__pycache__").mkdir(exist_ok=True)
    (folder / "scripts" / "__pycache__" / "run.cpython-313.pyc").write_bytes(b"\x00\xff")


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill library ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.bundled, self.user = root / "bundled", root / "user"
        self.bundled.mkdir()
        self.user.mkdir()
        for value in (patch.object(skill_library, "BUNDLED", self.bundled),
                      patch.dict(os.environ, {"MONKEYHUB_SKILL_PACKAGES": str(self.user)})):
            value.start()
            self.addCleanup(value.stop)
        project = root / PROJECT_ID
        FilesystemProjectRepository.initialize(project, project_id=PROJECT_ID,
                                               initial_state={"project_id": PROJECT_ID, "version": 0})
        self.client = TestClient(create_app(StudioSettings(project_dir=project, cad_export="off")))
        self.addCleanup(self.client.close)

    def library(self) -> dict:
        response = self.client.get("/api/skills/library")
        self.assertEqual(response.status_code, 200, response.text)
        return {(row["source"], row["name"]): row for row in response.json()["packages"]}

    def import_(self, source: str, name: str, status: int = 200) -> dict:
        response = self.client.post("/api/skills/import", json={"projectId": PROJECT_ID, "source": source, "name": name})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_both_places_are_listed_with_what_the_project_holds(self):
        write_package(self.bundled / "site-palette", frontmatter=True)
        write_package(self.user / "rule-study", access={"read": ["C:\\Windows\\Fonts"]})
        (self.user / "broken").mkdir()
        (self.user / "broken" / "skill.json").write_text("{}", encoding="utf-8")
        rows = self.library()
        self.assertEqual(set(rows), {("bundled", "site-palette"), ("user", "rule-study"), ("user", "broken")})
        palette = rows[("bundled", "site-palette")]
        self.assertEqual((palette["skillId"], palette["projectVersion"], palette["upToDate"]),
                         ("skill:site-palette", None, False))
        self.assertEqual(palette["files"], ["params/default.json", "scripts/run.py"], "no bytecode, no frontmatter file")
        self.assertEqual(rows[("user", "rule-study")]["access"]["read"], ["C:\\Windows\\Fonts"])
        self.assertEqual(rows[("user", "broken")]["error"]["code"], "SKILL_PACKAGE_INVALID")

    def test_an_import_adds_a_version_only_when_the_package_changed(self):
        write_package(self.user / "rule-study")
        first = self.import_("user", "rule-study")
        self.assertEqual((first["imported"], first["skill"]["version"], first["skill"]["body"]),
                         (True, 1, "Run it on one image.\n"))
        self.assertFalse(first["skill"]["approved"], "importing grants nothing")
        again = self.import_("user", "rule-study")
        self.assertEqual((again["imported"], again["skill"]["version"]), (False, 1))
        self.assertTrue(self.library()[("user", "rule-study")]["upToDate"])
        write_package(self.user / "rule-study", script=RUN + "print('changed')\n")
        self.assertFalse(self.library()[("user", "rule-study")]["upToDate"])
        changed = self.import_("user", "rule-study")
        self.assertEqual((changed["imported"], changed["skill"]["version"], changed["skill"]["latestVersion"]),
                         (True, 2, 2))
        self.assertNotEqual(changed["skill"]["codeSha256"], first["skill"]["codeSha256"])

    def test_a_partial_access_declaration_is_compared_in_its_checked_form(self):
        write_package(self.bundled / "site-palette", access={"programs": ["blender"]})
        self.import_("bundled", "site-palette")
        self.assertEqual(self.import_("bundled", "site-palette")["imported"], False)

    def test_an_unknown_or_invalid_package_is_refused(self):
        self.assertEqual(self.import_("user", "missing", status=404)["code"], "SKILL_PACKAGE_NOT_FOUND")
        self.assertEqual(self.import_("user", "../x", status=422)["code"], "REQUEST_INVALID")
        write_package(self.user / "bad-name")
        (self.user / "bad-name" / "skill.json").write_text(json.dumps({"name": "Bad Name", "description": "x"}),
                                                           encoding="utf-8")
        self.assertEqual(self.import_("user", "bad-name", status=422)["code"], "SKILL_INVALID")
        (self.user / "binary").mkdir()
        write_package(self.user / "binary")
        (self.user / "binary" / "logo.png").write_bytes(b"\x89PNG\xff\xfe")
        self.assertEqual(self.import_("user", "binary", status=422)["code"], "SKILL_PACKAGE_INVALID")


if __name__ == "__main__":
    unittest.main()
