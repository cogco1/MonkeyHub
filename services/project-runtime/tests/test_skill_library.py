"""The skills installed on this machine: the ones shipped with MonkeyHub and the user's own (S-1).

A package is a folder (skill.json, SKILL.md, text files). Every project can use
it without importing it; the project retains a version only when a run uses one
whose content differs from its current version, and retaining allows nothing.
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
from project_runtime.application import skill_library, skills
from project_runtime.binding import bound_project
from project_runtime.authentication import ActorAttribution
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

    def retain(self, name: str):
        """What a run does first: the project's version of exactly the package the folders hold."""

        binding = bound_project(self.client.app.state)
        return skill_library.retain(binding, skill_library.installed().find(name),
                                    attribution=ActorAttribution("person", False, "local"))

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

    def test_a_version_is_retained_only_when_the_package_changed(self):
        write_package(self.user / "rule-study")
        first = self.retain("rule-study")
        self.assertEqual((first.version, first.payload["body"]), (1, "Run it on one image.\n"))
        self.assertEqual(self.retain("rule-study").ref, first.ref, "the same package is the same version")
        self.assertTrue(self.library()[("user", "rule-study")]["upToDate"])
        write_package(self.user / "rule-study", script=RUN + "print('changed')\n")
        self.assertFalse(self.library()[("user", "rule-study")]["upToDate"])
        changed = self.retain("rule-study")
        self.assertEqual(changed.version, 2)
        self.assertNotEqual(changed.code_sha256, first.code_sha256)

    def test_the_users_package_comes_before_a_shipped_one_of_the_same_name(self):
        write_package(self.bundled / "site-palette", access={"programs": ["blender"]})
        self.assertEqual(skill_library.installed().find("site-palette").source, "bundled")
        write_package(self.user / "site-palette")
        found = skill_library.installed()
        self.assertEqual(found.find("site-palette").source, "user")
        self.assertEqual([(row.source, row.name) for row in found.current()], [("user", "site-palette")])
        self.assertTrue(self.library()[("bundled", "site-palette")]["shadowed"])

    def test_what_a_run_would_need_is_read_without_running_or_retaining(self):
        write_package(self.user / "rule-study", access={"programs": ["Blender.exe"], "read": ["C:\\Windows\\Fonts"]})
        with patch.dict(os.environ, {"MONKEYHUB_SKILL_GRANTS": str(self.user.parent / "none.json")}):
            answer = self.client.get("/api/skills/skill:rule-study/needs")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual((body["skill"], body["needs"], body["broad"]),
                         ({"name": "rule-study", "source": "user"}, {"programs": ["blender"]}, {}))
        binding = bound_project(self.client.app.state)
        self.assertIsNone(skills.latest_version(binding, "skill:rule-study"), "nothing was retained")
        self.assertEqual(self.client.get("/api/skills/skill:missing/needs").json()["code"], "SKILL_NOT_INSTALLED")

    def test_a_package_that_would_only_fail_when_it_runs_is_caught_when_it_is_installed(self):
        # A script that does not compile, and a script SKILL.md sends the reader to that the package never carried.
        write_package(self.user / "typo-skill", script="def run(:\n    pass\n")
        write_package(self.user / "lost-script")
        (self.user / "lost-script" / "SKILL.md").write_text(
            "Run it, then check it with scripts/check.py and params/default.json.\n"
            "A per-style file such as params/<style>.json names no one file, and neither does scripts/*.py.\n",
            encoding="utf-8")
        write_package(self.user / "fine-skill")
        rows = self.library()
        syntax = rows[("user", "typo-skill")]["error"]
        self.assertEqual((syntax["kind"], syntax["file"], syntax["line"]), ("entry_syntax", "scripts/run.py", 1))
        self.assertIn("scripts/run.py does not compile", syntax["detail"])
        lost = rows[("user", "lost-script")]["error"]
        self.assertEqual((lost["kind"], lost["file"]), ("missing_file", "scripts/check.py"))
        self.assertNotIn("error", rows[("user", "fine-skill")], "a placeholder or a file that is there is no problem")
        # None of them is handed to a chat, and a run says why instead of starting.
        found = skill_library.installed()
        self.assertEqual([row.name for row in found.current() if row.content is not None], ["fine-skill"])
        for name, code in (("typo-skill", "SKILL_PACKAGE_INVALID"), ("lost-script", "SKILL_PACKAGE_INVALID")):
            response = self.client.post(f"/api/skills/skill:{name}/run", json={"projectId": PROJECT_ID})
            self.assertEqual((response.status_code, response.json()["code"]), (422, code))
        # Fixing the folder is enough: nothing is cached.
        write_package(self.user / "typo-skill")
        self.assertIsNotNone(skill_library.installed().find("typo-skill").content)

    def test_a_script_saved_with_a_byte_order_mark_is_a_usable_package_and_stays_as_it_was_written(self):
        # Windows editors write one at the start of a UTF-8 file; Python runs such a file, and so can the package.
        write_package(self.user / "bom-skill")
        script = self.user / "bom-skill" / "scripts" / "run.py"
        script.write_bytes(b"\xef\xbb\xbf" + RUN.encode("utf-8"))
        found = skill_library.installed().find("bom-skill")
        self.assertIsNone(found.error, "a byte order mark is not a syntax error")
        self.assertIsNotNone(found.content)
        self.assertTrue(found.content["files"]["scripts/run.py"].startswith("\ufeff"), "the file is kept exactly as written")
        # A mark does not hide a real mistake.
        script.write_bytes(b"\xef\xbb\xbfdef run(:\n    pass\n")
        broken = skill_library.installed().find("bom-skill")
        self.assertEqual((broken.error["kind"], broken.error["line"]), ("entry_syntax", 1))

    def test_an_invalid_package_is_listed_with_why_and_runs_nothing(self):
        write_package(self.user / "bad-name")
        (self.user / "bad-name" / "skill.json").write_text(json.dumps({"name": "Bad Name", "description": "x"}),
                                                           encoding="utf-8")
        (self.user / "binary").mkdir()
        write_package(self.user / "binary")
        (self.user / "binary" / "logo.png").write_bytes(b"\x89PNG\xff\xfe")
        rows = self.library()
        self.assertEqual(rows[("user", "bad-name")]["error"]["code"], "SKILL_INVALID")
        self.assertEqual(rows[("user", "binary")]["error"]["code"], "SKILL_PACKAGE_INVALID")
        for name, code in (("bad-name", "SKILL_INVALID"), ("binary", "SKILL_PACKAGE_INVALID")):
            response = self.client.post(f"/api/skills/skill:{name}/run", json={"projectId": PROJECT_ID})
            self.assertEqual((response.status_code, response.json()["code"]), (422, code))


if __name__ == "__main__":
    unittest.main()
