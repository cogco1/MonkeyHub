"""A library project keeps its skills as immutable versions (#252).

A skill is a procedure an agent may follow: a name, one line saying when to use
it, and a SKILL.md body. The index an agent lists carries no body; a version
once retained is never rewritten, and a superseded one stays readable.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.record_kinds import STUDIO_SKILL, require_registered
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application.skills import MAX_BODY_BYTES, SKILLS_RUN_ID
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

PROJECT_ID = "skill-library"
BODY = "When reviewing a plan's hatching:\n\n1. Read the drawing recipe.\n2. List every region hatched twice.\n"


def skill(name: str = "hatch-review", **fields) -> dict:
    return {"projectId": PROJECT_ID, "name": name,
            "description": "Review a plan's hatching against the project recipe.", "body": BODY, **fields}


class SkillRoutesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skills ")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(
            self.project, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.client = TestClient(create_app(StudioSettings(project_dir=self.project, cad_export="off")))
        self.addCleanup(self.client.close)

    def add(self, payload: dict, status: int = 201) -> dict:
        response = self.client.post("/api/skills", json=payload)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_an_empty_library_reads_as_no_skills_and_creates_no_run(self):
        response = self.client.get("/api/skills")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"projectId": PROJECT_ID, "skills": []})
        self.assertFalse((self.project / "runs" / SKILLS_RUN_ID).exists())
        self.assertEqual(self.client.get("/api/skills/skill:hatch-review").json()["code"], "SKILL_NOT_FOUND")

    def test_add_index_supersede_and_read_every_version(self):
        first = self.add(skill(manifest={"inputs": ["plan drawing"], "permissions": ["read drawings"]}))
        self.assertEqual((first["id"], first["version"], first["latestVersion"]), ("skill:hatch-review", 1, 1))
        self.assertEqual(first["body"], BODY)
        self.assertEqual(first["manifest"], {"inputs": ["plan drawing"], "outputs": [], "permissions": ["read drawings"]})
        self.assertEqual(require_registered(STUDIO_SKILL).schema, "StudioSkill@1")

        # The index is what an agent lists before it uses a skill: no body.
        index = self.client.get("/api/skills").json()
        self.assertEqual(index, {"projectId": PROJECT_ID, "skills": [{
            "id": "skill:hatch-review", "version": 1, "name": "hatch-review",
            "description": "Review a plan's hatching against the project recipe."}]})

        second = self.add(skill(description="Review hatching and poché against the recipe.",
                                body=BODY + "3. Check poché.\n", supersedesVersion=1))
        self.assertEqual((second["version"], second["latestVersion"]), (2, 2))
        self.assertEqual([row["version"] for row in self.client.get("/api/skills").json()["skills"]], [2])

        latest = self.client.get("/api/skills/skill:hatch-review").json()
        self.assertEqual((latest["version"], latest["body"]), (2, BODY + "3. Check poché.\n"))
        # The superseded version is still readable exactly as it was written,
        # and says which version is current now.
        old = self.client.get("/api/skills/skill:hatch-review", params={"version": 1}).json()
        self.assertEqual((old["version"], old["latestVersion"], old["body"]), (1, 2, BODY))
        self.assertEqual(old["description"], "Review a plan's hatching against the project recipe.")
        missing = self.client.get("/api/skills/skill:hatch-review", params={"version": 3})
        self.assertEqual((missing.status_code, missing.json()["code"]), (404, "SKILL_VERSION_NOT_FOUND"))

    def test_a_writer_who_did_not_read_the_current_version_replaces_nothing(self):
        self.add(skill())
        self.assertEqual(self.add(skill(), 409)["code"], "SKILL_EXISTS")
        self.add(skill(supersedesVersion=1))
        self.assertEqual(self.add(skill(supersedesVersion=1), 409)["code"], "SKILL_STALE")
        self.assertEqual(self.add(skill("section-sheet", supersedesVersion=1), 404)["code"], "SKILL_NOT_FOUND")
        self.assertEqual([row["version"] for row in self.client.get("/api/skills").json()["skills"]], [2])

    def test_refusals(self):
        self.assertEqual(self.add(skill("Hatch Review"), 422)["code"], "SKILL_INVALID")
        self.assertEqual(self.add(skill(description="two\nlines"), 422)["code"], "SKILL_INVALID")
        self.assertEqual(self.add(skill(body="---\nname: x\n---\nbody"), 422)["code"], "SKILL_INVALID")
        self.assertEqual(self.add(skill(body="x" * (MAX_BODY_BYTES + 1)), 413)["code"], "SKILL_TOO_LARGE")
        self.assertEqual(self.add(skill(projectId="another"), 403)["code"], "PROJECT_MISMATCH")
        self.assertEqual(self.client.get("/api/skills").json()["skills"], [])


if __name__ == "__main__":
    unittest.main()
