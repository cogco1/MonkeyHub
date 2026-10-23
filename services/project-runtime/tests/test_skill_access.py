"""A skill's declared access read against what a person allowed: within it, or exactly what it adds."""

from __future__ import annotations

import unittest

from project_runtime.application.skill_access import access_added, access_within, describe_added, describe_added_zh

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
        self.assertEqual(describe_added(added), "use the network; run git; write D:\\Projects; install numpy>=2.1")
        self.assertEqual(describe_added_zh(added), "联网；运行 git；写入 D:\\Projects；安装 numpy>=2.1")

    def test_folders_are_compared_as_folders_and_reading_is_not_writing(self) -> None:
        self.assertEqual(access_added({"read": ["D:\\Projects\\thesis-old"]}, GRANTED), {"read": ["D:\\Projects\\thesis-old"]},
                         "a sibling sharing a prefix is not inside")
        self.assertEqual(access_added({"read": ["D:\\Projects\\thesis\\out"]}, {"write": ["D:\\Projects\\thesis\\out"]}),
                         {"read": ["D:\\Projects\\thesis\\out"]})
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["/home/a"]}), {})
        self.assertEqual(access_added({"write": ["/home/a/out"]}, {"write": ["D:\\home\\a"]}), {"write": ["/home/a/out"]})

    def test_a_dot_dot_step_never_stays_within_the_folder_it_starts_from(self) -> None:
        for granted, requested in (("D:\\proj\\out", "D:\\proj\\out\\..\\..\\Windows"),
                                   ("D:\\proj\\out", "D:/proj/out/../../Windows"),
                                   ("/home/u/data", "/home/u/data/../../etc"),
                                   ("/home/u/data", "/home/u/data/sub/../more")):
            with self.subTest(requested=requested):
                self.assertEqual(access_added({"write": [requested]}, {"write": [granted]}), {"write": [requested]})
        self.assertEqual(access_added({"write": ["D:\\proj\\out\\.\\plans"]}, {"write": ["D:\\proj\\out"]}), {},
                         "a . step is the same folder")

    def test_folders_compare_as_the_runner_resolves_them(self) -> None:
        links = {"D:\\proj\\out\\link": "D:\\Windows"}
        resolve = lambda path: links.get(path, path)  # noqa: E731 - a link out of the allowed folder
        self.assertEqual(access_added({"write": ["D:\\proj\\out\\link"]}, {"write": ["D:\\proj\\out"]}), {})
        self.assertEqual(access_added({"write": ["D:\\proj\\out\\link"]}, {"write": ["D:\\proj\\out"]}, resolve=resolve),
                         {"write": ["D:\\proj\\out\\link"]})
        self.assertFalse(access_within({"write": ["D:\\proj\\out\\link"]}, {"write": ["D:\\proj\\out"]}, resolve=resolve))

    def test_anywhere_covers_every_folder_and_needs_anywhere(self) -> None:
        self.assertEqual(access_added({"read": ["C:\\", "/etc"]}, {"read": ["*"]}), {})
        self.assertEqual(access_added({"read": ["*"]}, GRANTED), {"read": ["*"]})
        self.assertEqual((describe_added({"read": ["*"]}), describe_added_zh({"read": ["*"]})), ("read *", "读取 *"))


if __name__ == "__main__":
    unittest.main()
