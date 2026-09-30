from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archflow.contracts.canonical import canonical_json

from monkeycontrol.store import ActionTraceStore

FIRST = {"schema": "ComputerActionReceipt@1", "step_id": "s-0001", "status": "succeeded"}
SECOND = {"schema": "ComputerActionReceipt@1", "step_id": "s-0002", "status": "refused"}


class ActionTraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name) / "diagnostics" / "monkeycontrol"
        self.store = ActionTraceStore(self.directory)

    def test_the_constructor_touches_no_disk(self) -> None:
        self.assertEqual(self.store.directory, self.directory)
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.store.read_receipts(), [])
        self.assertFalse(self.directory.exists())

    def test_append_writes_one_canonical_line_per_receipt(self) -> None:
        self.store.append(FIRST)
        self.store.append(SECOND)
        lines = (self.directory / "actions.ndjson").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(lines, [canonical_json(FIRST), canonical_json(SECOND)])
        self.assertEqual(self.store.read_receipts(), [FIRST, SECOND])

    def test_save_bytes_is_content_addressed(self) -> None:
        data = b"\x89PNG fake frame"
        digest = hashlib.sha256(data).hexdigest()
        relative = self.store.save_bytes("shots", data, ".png")
        self.assertEqual(relative, f"shots/{digest}.png")
        self.assertEqual(ActionTraceStore.sha256(data), digest)
        self.assertEqual((self.directory / relative).read_bytes(), data)
        self.assertEqual(
            [path.name for path in (self.directory / "shots").iterdir()],
            [f"{digest}.png"],
        )

    def test_save_bytes_honours_an_explicit_frame_name(self) -> None:
        relative = self.store.save_bytes(
            "recordings/demo/frames", b"frame", ".png", name="000001.png"
        )
        self.assertEqual(relative, "recordings/demo/frames/000001.png")
        self.assertEqual((self.directory / relative).read_bytes(), b"frame")

    def test_write_json_is_canonical_and_replaces_in_place(self) -> None:
        self.store.write_json("recordings/demo/manifest.json", {"frames": 1, "a": 2})
        self.store.write_json("recordings/demo/manifest.json", {"frames": 2, "a": 2})
        path = self.directory / "recordings" / "demo" / "manifest.json"
        self.assertEqual(path.read_text(encoding="utf-8"), canonical_json({"a": 2, "frames": 2}))
        self.assertEqual(
            [entry.name for entry in path.parent.iterdir()], ["manifest.json"]
        )

    def test_nothing_is_written_outside_the_trace_directory(self) -> None:
        with self.assertRaises(ValueError):
            self.store.save_bytes("../escape", b"x", ".png")
        with self.assertRaises(ValueError):
            self.store.write_json("/tmp/escape.json", {})
        with self.assertRaises(ValueError):
            self.store.save_bytes("shots", b"x", ".png", name="../escape.png")


if __name__ == "__main__":
    unittest.main()
