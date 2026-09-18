"""The recorder, driven by a presentation provider that returns tiny PNGs.

Frames, the timeline and the manifest are all files in the trace directory, so
every assertion here reads them back rather than the recorder's own state. The
video is whatever this machine can encode: mp4, gif, or an honest reason.
"""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycontrol.record import Recorder, encode_video
from monkeycontrol.store import ActionTraceStore

#: A real 4x4 PNG, so a Pillow encode has something it can actually open.
FRAME = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGM8ocHFAANMcBZ"
    "eDgAylgECe1IpCwAAAABJRU5ErkJggg=="
)


class FakePresentation:
    """One grabber that answers a slightly different PNG every time."""

    def __init__(self) -> None:
        self.shots = 0

    def screenshot(self, *, bounds=None):
        self.shots += 1
        return {
            "png": FRAME,
            "sha256": ActionTraceStore.sha256(FRAME),
            "bounds": list(bounds or (0, 0, 4, 4)),
            "bytes": len(FRAME),
        }


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name) / "trace"
        self.store = ActionTraceStore(self.directory)
        self.presentation = FakePresentation()

    def recorder(self, **overrides) -> Recorder:
        return Recorder(
            self.store,
            self.presentation,
            overrides.pop("name", "demo"),
            interval_ms=overrides.pop("interval_ms", 10),
        )

    def lines(self, relative: str) -> list[dict]:
        path = self.directory / relative
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_a_recording_leaves_frames_a_timeline_and_a_manifest(self) -> None:
        recorder = self.recorder()
        recorder.start()
        recorder.note("click", "120,240 left x1", step_id="s-0001")
        manifest = recorder.stop()
        frames = self.lines("recordings/demo/frames.ndjson")
        self.assertGreaterEqual(len(frames), 1)
        self.assertEqual(frames[0]["index"], 1)
        self.assertEqual(frames[0]["path"], "frames/000001.png")
        self.assertEqual(frames[0]["sha256"], ActionTraceStore.sha256(FRAME))
        self.assertTrue(
            (self.directory / "recordings/demo/frames/000001.png").is_file()
        )
        timeline = self.lines("recordings/demo/timeline.ndjson")
        self.assertEqual(timeline[0]["event"], "click")
        self.assertEqual(timeline[0]["detail"], "120,240 left x1")
        self.assertEqual(timeline[0]["step_id"], "s-0001")
        self.assertGreaterEqual(timeline[0]["t"], 0.0)
        self.assertEqual(manifest["name"], "demo")
        self.assertEqual(manifest["interval_ms"], 10)
        self.assertGreaterEqual(manifest["frame_count"], 1)
        self.assertEqual(manifest["frames"], "frames.ndjson")
        self.assertEqual(manifest["timeline"], "timeline.ndjson")
        self.assertEqual(manifest["actions"], ["s-0001"])
        self.assertTrue(manifest["started_at"].endswith("Z"))
        self.assertTrue(manifest["stopped_at"].endswith("Z"))
        stored = json.loads(
            (self.directory / "recordings/demo/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored, manifest)

    def test_the_manifest_says_what_encoded_the_video_or_why_nothing_did(
        self,
    ) -> None:
        recorder = self.recorder()
        recorder.start()
        recorder.note("screenshot", "shots/aa.png", step_id="s-0001")
        video = recorder.stop()["video"]
        if video["format"] is None:
            self.assertIn("ffmpeg", video["reason"])
        else:
            self.assertIn(video["format"], ("gif", "mp4"))
            self.assertIn(video["encoder"], ("ffmpeg", "pillow"))
            self.assertEqual(video["path"], f"raw.{video['format']}")
            self.assertTrue(
                (self.directory / "recordings/demo" / video["path"]).is_file()
            )

    def test_the_manifest_lists_every_step_once_in_order(self) -> None:
        recorder = self.recorder()
        recorder.start()
        recorder.note("click", "1,2", step_id="s-0002")
        recorder.note("verify", "passed window Notepad", step_id="s-0002")
        recorder.note("keypress", "ctrl+s", step_id="s-0003")
        self.assertEqual(recorder.stop()["actions"], ["s-0002", "s-0003"])

    def test_a_note_before_the_start_still_lands_on_the_timeline(self) -> None:
        recorder = self.recorder()
        recorder.note("launch", "notepad.exe", step_id="s-0001")
        recorder.start()
        recorder.stop()
        self.assertEqual(
            [entry["event"] for entry in self.lines("recordings/demo/timeline.ndjson")],
            ["launch"],
        )


class EncodeVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.recording = Path(self._temp.name) / "recordings" / "demo"
        (self.recording / "frames").mkdir(parents=True)
        for index in (1, 2):
            (self.recording / "frames" / f"{index:06d}.png").write_bytes(FRAME)

    def test_an_encode_names_its_format_its_path_and_its_encoder(self) -> None:
        answer = encode_video(self.recording, 4)
        if answer["format"] is None:
            self.assertIn("reason", answer)
        else:
            self.assertEqual(answer["path"], f"raw.{answer['format']}")
            self.assertTrue((self.recording / answer["path"]).is_file())
            self.assertIn(answer["encoder"], ("ffmpeg", "pillow"))

    def test_no_frames_is_a_reason_rather_than_a_crash(self) -> None:
        empty = Path(self._temp.name) / "recordings" / "empty"
        (empty / "frames").mkdir(parents=True)
        answer = encode_video(empty, 4)
        self.assertIsNone(answer["format"])
        self.assertIn("reason", answer)


if __name__ == "__main__":
    unittest.main()
