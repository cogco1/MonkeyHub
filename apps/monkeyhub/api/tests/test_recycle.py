"""The Hub never deletes for good: a folder is set aside in quarantine, then recycled only when the bin keeps it."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402

source_roots.put_first(ROOT)

from monkeyhub_api import recycle  # noqa: E402


class RecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub recycle ")
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name) / "runtime"
        self.folder = self.runtime / "chats" / "one" / "scratch"
        (self.folder / "deep").mkdir(parents=True)
        (self.folder / "deep" / "kept.txt").write_text("still here", encoding="utf-8")

    def test_set_aside_renames_into_the_days_quarantine_and_loses_nothing(self):
        aside = recycle.set_aside(self.folder, self.runtime, "chat-one-scratch")
        self.assertFalse(self.folder.exists())
        self.assertEqual(aside.parent, recycle.quarantine_dir(self.runtime))
        self.assertTrue(aside.name.startswith("chat-one-scratch-"))
        self.assertEqual((aside / "deep" / "kept.txt").read_text(encoding="utf-8"), "still here")

    def test_nothing_there_is_nothing_to_set_aside(self):
        self.assertIsNone(recycle.set_aside(self.runtime / "missing", self.runtime, "x"))

    def test_a_folder_that_cannot_be_renamed_stays_where_it_is(self):
        with patch.object(recycle.os, "replace", side_effect=PermissionError("in use")):
            self.assertIsNone(recycle.set_aside(self.folder, self.runtime, "x"))
        self.assertTrue((self.folder / "deep" / "kept.txt").is_file())

    def test_a_throwaway_runtime_keeps_its_quarantine_out_of_the_bin(self):
        aside = recycle.set_aside(self.folder, self.runtime, "x")
        with patch.object(recycle, "_shell_recycle") as shell:
            self.assertFalse(recycle.recycle(aside))
        shell.assert_not_called()
        self.assertTrue(aside.is_dir())

    @unittest.skipUnless(os.name == "nt", "the Recycle Bin is Windows'")
    def test_a_path_too_long_for_the_bin_is_never_recycled(self):
        aside = recycle.set_aside(self.folder, self.runtime, "x")
        long = aside / ("n" * 120) / ("m" * 120)
        long.mkdir(parents=True)
        (long / "f.txt").write_text("x", encoding="utf-8")
        with patch.object(recycle, "_under_temp", return_value=False), \
                patch.object(recycle, "_bin_capacity", return_value=10 ** 12), \
                patch.object(recycle, "_shell_recycle") as shell:
            self.assertFalse(recycle.recycle(aside))
        shell.assert_not_called()
        self.assertTrue((long / "f.txt").is_file())

    @unittest.skipUnless(os.name == "nt", "the Recycle Bin is Windows'")
    def test_a_bin_that_is_off_or_unknown_keeps_it_in_quarantine(self):
        aside = recycle.set_aside(self.folder, self.runtime, "x")
        with patch.object(recycle, "_under_temp", return_value=False), \
                patch.object(recycle, "_bin_capacity", return_value=None), \
                patch.object(recycle, "_shell_recycle") as shell:
            self.assertFalse(recycle.recycle(aside))
        shell.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "the Recycle Bin is Windows'")
    def test_a_folder_the_bin_keeps_is_handed_to_the_shell(self):
        aside = recycle.set_aside(self.folder, self.runtime, "x")
        with patch.object(recycle, "_under_temp", return_value=False), \
                patch.object(recycle, "_bin_capacity", return_value=10 ** 12), \
                patch.object(recycle, "_shell_recycle", return_value=True) as shell:
            self.assertTrue(recycle.recycle(aside))
        shell.assert_called_once_with(aside)


if __name__ == "__main__":
    unittest.main()
