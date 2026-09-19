"""The recorder, driven by a presentation provider that returns tiny PNGs.

Frames, the timeline and the manifest are all files in the trace directory, so
every assertion here reads them back rather than the recorder's own state. The
video is whatever this machine can encode: mp4, gif, or an honest reason.
"""

from __future__ import annotations

import base64
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycontrol import record as record_module
from monkeycontrol.record import Recorder, encode_video
from monkeycontrol.store import ActionTraceStore

#: A real 4x4 PNG, so a Pillow encode has something it can actually open.
#: Enough of an mp4 header for a test to tell one blob from another.
MP4_BYTES = bytes([0, 0, 0, 24]) + b"ftypmp42 a fake container"
FRAME = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGM8ocHFAANMcBZ"
    "eDgAylgECe1IpCwAAAABJRU5ErkJggg=="
)


class FakePresentation:
    """One grabber that answers the same PNG every time, over any region."""

    def __init__(self, *, pause: float = 0.0) -> None:
        self.shots = 0
        self.regions: list = []
        self._pause = pause

    def screenshot(self, *, bounds=None):
        self.shots += 1
        self.regions.append(tuple(bounds) if bounds else None)
        if self._pause:
            time.sleep(self._pause)
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


class RegionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = ActionTraceStore(Path(self._temp.name) / "trace")
        self.presentation = FakePresentation()

    def test_a_recorder_captures_the_whole_screen_until_it_is_told_otherwise(
        self,
    ) -> None:
        recorder = Recorder(self.store, self.presentation, "demo", interval_ms=10000)
        self.assertIsNone(recorder.region)
        recorder.start()
        recorder.stop()
        self.assertEqual(self.presentation.regions[0], None)

    def test_a_region_is_a_rectangle_and_is_kept(self) -> None:
        recorder = Recorder(self.store, self.presentation, "demo", interval_ms=10000)
        recorder.set_region((1920, 0, 3840, 1080))
        self.assertEqual(recorder.region, (1920, 0, 3840, 1080))
        recorder.start()
        recorder.stop()
        self.assertEqual(self.presentation.regions[0], (1920, 0, 3840, 1080))
        with self.assertRaises(ValueError):
            recorder.set_region((10, 10, 5, 5))

    def test_the_manifest_names_the_region_policy(self) -> None:
        recorder = Recorder(
            self.store, self.presentation, "demo", interval_ms=10000,
            region="window-monitor",
        )
        recorder.start()
        self.assertEqual(recorder.stop()["region"], "window-monitor")

    def test_a_capture_thread_that_will_not_stop_is_said_so(self) -> None:
        slow = FakePresentation(pause=0.5)
        recorder = Recorder(self.store, slow, "demo", interval_ms=10)
        original = record_module.JOIN_TIMEOUT_S
        record_module.JOIN_TIMEOUT_S = 0.01
        self.addCleanup(setattr, record_module, "JOIN_TIMEOUT_S", original)
        recorder.start()
        manifest = recorder.stop()
        self.assertTrue(
            any("still running" in note for note in manifest["capture_errors"]),
            manifest.get("capture_errors"),
        )


class EncodeVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.recording = Path(self._temp.name) / "recordings" / "demo"
        (self.recording / "frames").mkdir(parents=True)
        for index in (1, 2):
            (self.recording / "frames" / f"{index:06d}.png").write_bytes(FRAME)

    def pretend_ffmpeg(self) -> None:
        """Say ffmpeg is installed, so the pipe is exercised without one."""

        original = record_module.ffmpeg_path
        record_module.ffmpeg_path = lambda: "ffmpeg"
        self.addCleanup(setattr, record_module, "ffmpeg_path", original)

    def test_an_encode_names_its_format_its_path_and_its_encoder(self) -> None:
        answer = encode_video(self.recording, 4)
        if answer["format"] is None:
            self.assertIn("reason", answer)
        else:
            self.assertEqual(answer["path"], f"raw.{answer['format']}")
            self.assertTrue((self.recording / answer["path"]).is_file())
            self.assertIn(answer["encoder"], ("ffmpeg", "pillow"))

    def test_ffmpeg_writes_through_the_store_and_never_to_a_path(self) -> None:
        asked = {}

        class Finished:
            returncode = 0
            stdout = MP4_BYTES

        def runner(command, **kwargs):
            asked["command"] = command
            return Finished()

        self.pretend_ffmpeg()
        answer = encode_video(self.recording, 4, runner=runner)
        self.assertEqual(answer["format"], "mp4")
        self.assertEqual(answer["path"], "raw.mp4")
        self.assertEqual(answer["encoder"], "ffmpeg")
        self.assertEqual(answer["writer"], "monkeycontrol.store")
        self.assertEqual(
            (self.recording / "raw.mp4").read_bytes(), MP4_BYTES
        )
        self.assertIn("pipe:1", asked["command"])
        self.assertTrue(
            all(not str(part).endswith("raw.mp4") for part in asked["command"]),
            asked["command"],
        )

    def test_an_ffmpeg_that_fails_falls_back_rather_than_lying(self) -> None:
        class Finished:
            returncode = 1
            stdout = b""

        self.pretend_ffmpeg()
        answer = encode_video(self.recording, 4, runner=lambda *a, **k: Finished())
        self.assertIn(answer["format"], ("gif", None))
        self.assertFalse((self.recording / "raw.mp4").exists())

    def test_no_frames_is_a_reason_rather_than_a_crash(self) -> None:
        empty = Path(self._temp.name) / "recordings" / "empty"
        (empty / "frames").mkdir(parents=True)
        answer = encode_video(empty, 4)
        self.assertIsNone(answer["format"])
        self.assertIn("reason", answer)


if __name__ == "__main__":
    unittest.main()
