"""What a person allowed skills to do, read against what a run asks for (S-1).

Allowing is by access, not by code: an item allowed once holds for every skill
and version; a run is asked about only what nothing allowed yet; an item that
allows anything is never allowed for every skill.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from project_runtime.application.skill_access import (
    GRANTS_SCHEMA, GrantsUnreadable, SkillRef, access_added, access_within, asked, broad, describe_added,
    describe_added_zh, each_time, lasting, needed, once_grant, package_name, parse_grants, program_name, read_grants,
    tier,
)

# A machine where no declared folder is a link resolves each one as itself.
NO_LINKS = str
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
BLENDER = SkillRef("blender-render", "user")

GRANTED = {"network": False, "programs": ["rhino"], "ssh": [], "write": ["D:\\Projects\\thesis\\out"],
           "packages": ["numpy>=2"]}


def grant(grant_id: str, access: dict, *, skill: SkillRef | None = None, once: dict | None = None,
          revoked: bool = False) -> dict:
    row = {"grantId": grant_id, "access": access, "grantedAt": NOW.isoformat(), "method": "card"}
    if skill is not None:
        row["skill"] = {"name": skill.name, "source": skill.source}
    if once is not None:
        row["once"] = once
    if revoked:
        row["revokedAt"] = NOW.isoformat()
    return row


def document(*rows: dict) -> str:
    return json.dumps({"schema": GRANTS_SCHEMA, "grants": list(rows)})


class AccessTests(unittest.TestCase):
    def test_the_same_or_a_narrower_declaration_stays_within_the_grant(self) -> None:
        self.assertTrue(access_within(GRANTED, GRANTED, resolve=NO_LINKS))
        narrower = {"network": False, "programs": [], "write": ["D:\\Projects\\thesis\\out\\plans"], "packages": []}
        self.assertTrue(access_within(narrower, GRANTED, resolve=NO_LINKS), "below an allowed folder")
        self.assertTrue(access_within(None, GRANTED, resolve=NO_LINKS), "a version declaring nothing needs nothing")
        self.assertTrue(access_within({}, None, resolve=NO_LINKS))

    def test_a_wider_declaration_names_only_what_is_new(self) -> None:
        wider = {**GRANTED, "network": True, "programs": ["rhino", "git"],
                 "write": ["D:\\Projects\\thesis\\out", "D:\\Projects"], "packages": ["numpy>=2.1", "shapely"]}
        added = access_added(wider, GRANTED, resolve=NO_LINKS)
        self.assertEqual(added, {"network": True, "programs": ["git"], "write": ["D:\\Projects"], "packages": ["shapely"]})
        self.assertFalse(access_within(wider, GRANTED, resolve=NO_LINKS))
        self.assertEqual(describe_added(added), "use the network; run git; write to D:\\Projects; install shapely")
        self.assertEqual(describe_added_zh(added), "联网；运行 git；写入 D:\\Projects；安装 shapely")

    def test_reading_outside_the_project_is_asked_as_a_folder(self) -> None:
        # S23: the runner holds a script to what it reads, so reading a folder outside the project is asked about.
        declared = {"network": False, "read": ["C:\\Windows\\Fonts", "*"]}
        self.assertEqual(access_added(declared, None, resolve=NO_LINKS), {"read": ["C:\\Windows\\Fonts", "*"]})
        self.assertEqual(access_added({"read": ["d:/data/site/2026"]}, {"read": ["D:\\data\\site"]}, resolve=NO_LINKS), {},
                         "below an allowed folder")
        self.assertEqual(broad(declared), {"read": ["*"]}, "reading anything is allowed only for one skill or run")
        self.assertEqual(describe_added_zh({"read": ["D:\\data"]}), "读取 D:\\data")

    def test_three_tiers(self) -> None:
        # The user's rule of 2026-10-25: nothing declared runs; programs, packages and reading outside are asked
        # once; the network, ssh and writing outside are asked every time.
        self.assertEqual([tier(access) for access in (None, {}, {"network": False, "programs": []})], ["low"] * 3)
        for access in ({"programs": ["blender"]}, {"packages": ["shapely"]}, {"read": ["D:\\data"]}):
            with self.subTest(access=access):
                self.assertEqual((tier(access), each_time(access)), ("first", {}))
        for access, each in (({"network": True, "programs": ["blender"]}, {"network": True}),
                             ({"ssh": ["vast-gpu"]}, {"ssh": ["vast-gpu"]}),
                             ({"write": ["D:\\out"], "read": ["D:\\in"]}, {"write": ["D:\\out"]})):
            with self.subTest(access=access):
                self.assertEqual((tier(access), each_time(access)), ("each", each))

    def test_programs_compare_by_name_and_packages_by_distribution(self) -> None:
        self.assertEqual([program_name(item) for item in ("Blender.exe", "D:\\Blender\\blender.exe", "/usr/bin/git")],
                         ["blender", "blender", "git"])
        self.assertEqual([package_name(item) for item in ("numpy>=2", "NumPy==2.1", "ruamel.yaml", "pkg @ https://x/y.whl")],
                         ["numpy", "numpy", "ruamel-yaml", "pkg @ https://x/y.whl"])
        self.assertEqual(access_added({"programs": ["BLENDER.EXE"], "packages": ["numpy<3"]},
                                      {"programs": ["blender"], "packages": ["numpy>=2"]}, resolve=NO_LINKS), {},
                         "another version of an allowed package is the same package")

    def test_folders_are_compared_as_folders(self) -> None:
        self.assertEqual(access_added({"write": ["D:\\Projects\\thesis-old"]}, GRANTED, resolve=NO_LINKS),
                         {"write": ["D:\\Projects\\thesis-old"]}, "a sibling sharing a prefix is not inside")
        self.assertEqual(access_added({"write": ["d:/projects/thesis/out/site"]}, GRANTED, resolve=NO_LINKS), {},
                         "whatever the case or slashes")
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["/home/a"]}, resolve=NO_LINKS), {})
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["D:\\home\\a"]}, resolve=NO_LINKS),
                         {"write": ["/home/a/out"]})

    def test_a_dot_dot_step_never_stays_within_the_folder_it_starts_from(self) -> None:
        for granted, requested in (("D:\\proj\\out", "D:\\proj\\out\\..\\..\\Windows"),
                                   ("D:\\proj\\out", "D:/proj/out/../../Windows"),
                                   ("/home/u/data", "/home/u/data/../../etc"),
                                   ("/home/u/data", "/home/u/data/sub/../more")):
            with self.subTest(requested=requested):
                self.assertEqual(access_added({"write": [requested]}, {"write": [granted]}, resolve=NO_LINKS),
                                 {"write": [requested]})
        self.assertEqual(access_added({"write": ["D:\\proj\\out\\.\\plans"]}, {"write": ["D:\\proj\\out"]},
                                      resolve=NO_LINKS), {}, "a . step is the same folder")

    def test_deciding_not_to_ask_needs_to_know_how_folders_resolve(self) -> None:
        with self.assertRaises(TypeError):
            access_within(GRANTED, GRANTED, resolve=None)
        with self.assertRaises(TypeError):
            needed(GRANTED, (), BLENDER, resolve=None)
        links = {"D:\\proj\\out\\link": "D:\\Windows"}
        self.assertEqual(access_added({"write": ["D:\\proj\\out\\link"]}, {"write": ["D:\\proj\\out"]},
                                      resolve=lambda path: links.get(path, path)),
                         {"write": ["D:\\proj\\out\\link"]}, "a link out of the allowed folder is not inside it")

    def test_items_that_allow_anything(self) -> None:
        declared = {"programs": ["blender", "PowerShell.exe", "python", "*"], "ssh": ["renou-win", "*"],
                    "write": ["D:\\out", "*"], "packages": ["numpy", "evil @ https://x/evil.whl"]}
        self.assertEqual(broad(declared), {"programs": ["powershell", "python", "*"], "ssh": ["*"], "write": ["*"],
                                           "packages": ["evil @ https://x/evil.whl"]})
        self.assertEqual(broad({"programs": ["blender", "rhino"], "ssh": ["renou-win"]}), {})
        self.assertEqual(broad({"programs": ["python3.12", "/usr/bin/python3", "node20", "py"]})["programs"],
                         ["python3.12", "python3", "node20", "py"], "an interpreter under a version number")


class GrantTests(unittest.TestCase):
    def test_an_item_allowed_once_holds_for_every_skill_and_a_skill_grant_for_that_skill_only(self) -> None:
        grants = parse_grants(document(
            grant("all", {"programs": ["blender"]}),
            grant("own", {"read": ["D:\\assets"]}, skill=BLENDER),
            grant("gone", {"packages": ["shapely"]}, revoked=True)))
        blender_run = {"programs": ["blender"], "read": ["D:\\assets\\hdri"], "network": False}
        self.assertEqual(needed(blender_run, grants, BLENDER, resolve=NO_LINKS), {})
        self.assertEqual(needed(blender_run, grants, SkillRef("site-plan-map", "user"), resolve=NO_LINKS),
                         {"read": ["D:\\assets\\hdri"]}, "another skill is not allowed the folder")
        self.assertEqual(needed(blender_run, grants, SkillRef("blender-render", "shared"), resolve=NO_LINKS),
                         {"read": ["D:\\assets\\hdri"]}, "a skill of the same name from a shared folder is another skill")
        self.assertEqual(needed({"packages": ["shapely"]}, grants, BLENDER, resolve=NO_LINKS), {"packages": ["shapely"]},
                         "a withdrawn grant allows nothing")
        self.assertEqual(lasting(grants, BLENDER)["programs"], ["blender"])

    def test_the_network_ssh_and_writing_outside_are_never_allowed_for_long(self) -> None:
        # A grant from before S23 that allowed them for good, for every skill or for one, still allows its other items.
        grants = parse_grants(document(
            grant("old", {"network": True, "programs": ["blender"], "write": ["D:\\out"]}),
            grant("own", {"ssh": ["renou-win"]}, skill=BLENDER)))
        run = {"network": True, "programs": ["blender"], "ssh": ["renou-win"], "write": ["D:\\out"]}
        self.assertEqual(needed(run, grants, BLENDER, resolve=NO_LINKS),
                         {"network": True, "ssh": ["renou-win"], "write": ["D:\\out"]})
        self.assertEqual(lasting(grants, BLENDER), {"network": False, "programs": ["blender"], "ssh": [], "read": [],
                                                    "write": [], "packages": []})
        once = {"projectId": "housing", "codeSha256": "a" * 64, "expiresAt": (NOW + timedelta(minutes=30)).isoformat()}
        one_run = parse_grants(document(grant("run-1", {"network": True, "ssh": ["renou-win"], "write": ["D:\\out"]},
                                              skill=BLENDER, once=once)))
        self.assertIsNotNone(once_grant(one_run, "run-1", BLENDER, project_id="housing", code_sha256="a" * 64,
                                        needs=needed(run, grants, BLENDER, resolve=NO_LINKS), now=NOW, used=(),
                                        resolve=NO_LINKS), "a grant for one run allows them for that run")

    def test_an_item_that_allows_anything_counts_only_in_a_grant_for_that_skill(self) -> None:
        # The Hub refuses to write such a grant for every skill; one written into the file by hand allows nothing.
        grants = parse_grants(document(grant("hand", {"programs": ["powershell", "rhino"], "write": ["*"]}),
                                       grant("own", {"programs": ["python"]}, skill=BLENDER)))
        other = SkillRef("site-merge", "user")
        self.assertEqual(needed({"programs": ["powershell", "rhino"], "write": ["D:\\x"]}, grants, other, resolve=NO_LINKS),
                         {"programs": ["powershell"], "write": ["D:\\x"]})
        self.assertEqual(needed({"programs": ["python", "rhino"]}, grants, BLENDER, resolve=NO_LINKS), {})

    def test_a_one_run_grant_is_bound_to_its_project_skill_and_code(self) -> None:
        once = {"projectId": "housing", "codeSha256": "a" * 64, "expiresAt": (NOW + timedelta(minutes=30)).isoformat()}
        grants = parse_grants(document(grant("run-1", {"ssh": ["renou-win"]}, skill=BLENDER, once=once)))
        needs = {"ssh": ["renou-win"]}
        check = dict(project_id="housing", code_sha256="a" * 64, needs=needs, now=NOW, used=(), resolve=NO_LINKS)
        self.assertIsNotNone(once_grant(grants, "run-1", BLENDER, **check))
        self.assertEqual(needed(needs, grants, BLENDER, resolve=NO_LINKS), needs, "it is not a lasting grant")
        for change, why in (({"project_id": "0925"}, "another project"), ({"code_sha256": "b" * 64}, "other code"),
                            ({"used": ("run-1",)}, "a run already took it"),
                            ({"now": NOW + timedelta(minutes=31)}, "expired"),
                            ({"needs": {"ssh": ["renou-win"], "network": True}}, "the run needs more")):
            with self.subTest(why):
                self.assertIsNone(once_grant(grants, "run-1", BLENDER, **(check | change)))
        self.assertIsNone(once_grant(grants, "run-1", SkillRef("site-merge", "user"), **check), "another skill")

    def test_no_file_allows_nothing_and_a_damaged_one_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "skill-grants.json"
            self.assertEqual(read_grants(path), ())
            self.assertEqual(read_grants(None), ())
            for text in ("{", json.dumps({"schema": "Other@1", "grants": []}), json.dumps({"schema": GRANTS_SCHEMA}),
                         document({"access": {}})):
                with self.subTest(text=text[:40]):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(GrantsUnreadable):
                        read_grants(path)


if __name__ == "__main__":
    unittest.main()
