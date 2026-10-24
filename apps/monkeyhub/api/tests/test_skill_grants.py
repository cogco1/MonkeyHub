"""The Hub keeps what a person allowed skills to do in one file beside its settings (S-1).

One answer is one grant; an item that allows anything is allowed only for one
skill or one run; withdrawing keeps the record; a project's version grants from
before S-1 are carried over once, for that skill only; a Project Runtime reads
the same file as the Hub wrote it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from project_runtime.api.dto.skill_grants import SkillRefDto
from project_runtime.application import skill_access

from monkeyhub_api.models import HubFailure
from monkeyhub_api.settings import skill_grants

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
# The same skill as the Hub names it in a grant and as the Runtime checks a run of it.
OWN = SkillRefDto(name="blender-render", source="user")
BLENDER = skill_access.SkillRef("blender-render", "user")


def runtime_reads(root: Path) -> tuple[skill_access.Grant, ...]:
    """The grants as a Project Runtime reads them, at the path it is launched with."""

    return skill_access.read_grants(skill_grants.grants_path(root))


class SkillGrantStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_file_is_beside_the_application_settings_and_starts_empty(self) -> None:
        self.assertEqual(skill_grants.grants_path(self.root), self.root / "config" / "skill-grants.json")
        self.assertEqual(skill_grants.read(self.root).grants, [])
        self.assertFalse(skill_grants.grants_path(self.root).exists(), "reading writes nothing")

    def test_one_answer_allows_its_items_for_every_skill_and_a_runtime_reads_them(self) -> None:
        row = skill_grants.allow(self.root, {"programs": ["Blender.exe"], "ssh": ["renou-win"]}, method="card",
                                 ref="attention-1", now=NOW)
        self.assertEqual((row.access.programs, row.access.ssh, row.skill, row.method), (["blender"], ["renou-win"], None, "card"))
        saved = json.loads(skill_grants.grants_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], skill_access.GRANTS_SCHEMA)
        # What a Project Runtime reads at the path it is launched with.
        grants = runtime_reads(self.root)
        self.assertEqual(skill_access.needed({"programs": ["blender"], "ssh": ["renou-win"]}, grants,
                                             skill_access.SkillRef("anything", "bundled"), resolve=str), {})
        self.assertEqual([item.grant_id for item in runtime_reads(self.root)], [row.grant_id])

    def test_an_item_that_allows_anything_is_allowed_only_for_one_skill_or_one_run(self) -> None:
        for access in ({"programs": ["powershell"]}, {"programs": ["*"]}, {"write": ["*"]}, {"ssh": ["*"]},
                       {"packages": ["x @ https://example.com/x.whl"]}):
            with self.subTest(access=access), self.assertRaises(HubFailure) as refused:
                skill_grants.allow(self.root, access, method="settings")
            self.assertEqual(refused.exception.error.code, "SKILL_GRANT_TOO_BROAD")
        own = skill_grants.allow(self.root, {"programs": ["python"]}, method="card", skill=OWN)
        self.assertEqual((own.skill.name, own.skill.source), ("blender-render", "user"))
        with self.assertRaises(HubFailure) as nothing:
            skill_grants.allow(self.root, {"network": False, "read": ["C:\\Windows\\Fonts"]}, method="card")
        self.assertEqual(nothing.exception.error.code, "SKILL_GRANT_EMPTY", "reading is not something to allow")

    def test_a_one_run_answer_is_bound_and_expires(self) -> None:
        once = {"projectId": "housing-13-units", "codeSha256": "c" * 64}
        row = skill_grants.allow(self.root, {"ssh": ["renou-win"]}, method="card", skill=OWN, once=once, now=NOW)
        self.assertEqual(row.once["expiresAt"], (NOW + timedelta(minutes=skill_grants.ONCE_MINUTES)).isoformat())
        grants = runtime_reads(self.root)
        self.assertIsNotNone(skill_access.once_grant(
            grants, row.grant_id, BLENDER, project_id="housing-13-units", code_sha256="c" * 64,
            needs={"ssh": ["renou-win"]}, now=NOW, used=(), resolve=str))
        with self.assertRaises(HubFailure):
            skill_grants.allow(self.root, {"ssh": ["renou-win"]}, method="card", once=once)

    def test_withdrawing_keeps_the_record_and_allows_nothing_more(self) -> None:
        row = skill_grants.allow(self.root, {"network": True}, method="settings")
        withdrawn = skill_grants.revoke(self.root, row.grant_id, by="settings", now=NOW)
        self.assertEqual(withdrawn.revoked_at, NOW.isoformat())
        self.assertEqual(skill_grants.revoke(self.root, row.grant_id, by="settings").revoked_at, NOW.isoformat(),
                         "withdrawing twice answers the first")
        self.assertEqual(skill_access.needed({"network": True}, runtime_reads(self.root), BLENDER, resolve=str),
                         {"network": True})
        self.assertEqual(len(skill_grants.read(self.root).grants), 1)
        with self.assertRaises(HubFailure) as missing:
            skill_grants.revoke(self.root, "nobody", by="settings")
        self.assertEqual(missing.exception.error.code, "SKILL_GRANT_NOT_FOUND")

    def test_a_damaged_file_is_refused_and_kept(self) -> None:
        path = skill_grants.grants_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")
        for attempt in (lambda: skill_grants.read(self.root),
                        lambda: skill_grants.allow(self.root, {"network": True}, method="card")):
            with self.assertRaises(HubFailure) as refused:
                attempt()
            self.assertEqual(refused.exception.error.code, "SKILL_GRANTS_UNREADABLE")
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")

    def test_a_projects_version_grants_are_carried_over_once_for_that_skill_only(self) -> None:
        approvals = [
            {"approvalId": "a1", "skillId": "skill:blender-render", "version": 3, "scope": "version",
             "codeSha256": "d" * 64, "access": {"programs": ["blender"], "ssh": ["renou-win"], "read": ["D:\\Blender"]},
             "approvedAt": "2026-10-24T05:10:00+00:00", "approvedBy": {"actorId": "kaiwen"}},
            {"approvalId": "a2", "skillId": "skill:blender-render", "version": 3, "scope": "once",
             "access": {"network": True}},
            {"approvalId": "a3", "skillId": "skill:site-palette", "version": 1, "scope": "version", "access": None},
            {"approvalId": "a4", "skillId": "skill:from-a-team", "version": 1, "scope": "version",
             "access": {"network": True}},
        ]
        sources = {"blender-render": "user", "site-palette": "user", "from-a-team": "shared"}
        carried = skill_grants.migrate(self.root, "0925", approvals, source_of=sources.get, now=NOW)
        self.assertEqual([(row.ref, row.skill.name, row.skill.source, row.method) for row in carried],
                         [("a1", "blender-render", "user", "migrated")],
                         "not a once grant, not one that needed nothing, not onto a shared folder's skill")
        self.assertEqual(carried[0].migrated_from["projectId"], "0925")
        grants = runtime_reads(self.root)
        self.assertEqual(skill_access.needed({"programs": ["blender"], "ssh": ["renou-win"]}, grants, BLENDER,
                                             resolve=str), {})
        self.assertEqual(skill_access.needed({"programs": ["blender"]}, grants,
                                             skill_access.SkillRef("site-plan-map", "user"), resolve=str),
                         {"programs": ["blender"]}, "the person allowed that skill, not every skill")
        skill_grants.revoke(self.root, carried[0].grant_id, by="settings")
        self.assertEqual(skill_grants.migrate(self.root, "0925", approvals, source_of=sources.get), [],
                         "carried once, and not again after it was withdrawn")


if __name__ == "__main__":
    unittest.main()
