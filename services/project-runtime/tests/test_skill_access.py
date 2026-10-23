"""A skill's declared access read against what a person allowed: within it, or exactly what it adds."""

from __future__ import annotations

import unittest

from project_runtime.application.skill_access import access_added, access_within, describe_added

GRANTED = {"network": False, "programs": ["rhino"], "ssh": [], "read": ["D:\\Projects\\thesis"],
           "write": ["D:\\Projects\\thesis\\out"], "packages": ["numpy>=2"]}


class AccessTests(unittest.TestCase):
    def test_the_same_or_a_narrower_declaration_stays_within_the_grant(self) -> None:
        self.assertTrue(access_within(GRANTED, GRANTED))
        narrower = {"network": False, "programs": [], "read": ["d:/projects/thesis/site"],
                    "write": ["D:\\Projects\\thesis\\out\\plans"], "packages": []}
        self.assertTrue(access_within(narrower, GRANTED), "below an allowed folder, whatever the case or slashes")
        self.assertTrue(access_within(None, GRANTED), "a version declaring nothing needs nothing")
        self.assertTrue(access_within({}, None))

    def test_a_wider_declaration_names_only_what_is_new(self) -> None:
        wider = {**GRANTED, "network": True, "programs": ["rhino", "git"],
                 "write": ["D:\\Projects\\thesis\\out", "D:\\Projects"], "packages": ["numpy>=2.1"]}
        added = access_added(wider, GRANTED)
        self.assertEqual(added, {"network": True, "programs": ["git"], "write": ["D:\\Projects"],
                                 "packages": ["numpy>=2.1"]})
        self.assertFalse(access_within(wider, GRANTED))
        self.assertEqual(describe_added(added), "联网；运行 git；写入 D:\\Projects；安装 numpy>=2.1")

    def test_folders_are_compared_as_folders_and_reading_is_not_writing(self) -> None:
        self.assertEqual(access_added({"read": ["D:\\Projects\\thesis-old"]}, GRANTED), {"read": ["D:\\Projects\\thesis-old"]},
                         "a sibling sharing a prefix is not inside")
        self.assertEqual(access_added({"read": ["D:\\Projects\\thesis\\out"]}, {"write": ["D:\\Projects\\thesis\\out"]}),
                         {"read": ["D:\\Projects\\thesis\\out"]})
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["/home/a"]}), {})
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["D:\\home\\a"]}), {"write": ["/home/a/out"]})

    def test_anywhere_covers_every_folder_and_needs_anywhere(self) -> None:
        self.assertEqual(access_added({"read": ["C:\\", "/etc"]}, {"read": ["*"]}), {})
        self.assertEqual(access_added({"read": ["*"]}, GRANTED), {"read": ["*"]})
        self.assertEqual(describe_added({"read": ["*"]}), "读取 *")


if __name__ == "__main__":
    unittest.main()
