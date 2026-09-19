"""Overlay regeneration over a recording that was built by hand.

A recording is only frames plus a timeline, so the projections can be checked
without ever capturing a screen: two synthetic frames, three timeline lines,
and the rendered PNGs have to differ from what went in.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from monkeycontrol.overlay import PASSED, PROJECTIONS, render_overlays
from monkeycontrol.runtime import RuntimeRefusal
from monkeycontrol.store import ActionTraceStore

try:  # pragma: no cover - the import is the thing under test
    from PIL import Image

    PILLOW = True
except ImportError:  # pragma: no cover
    PILLOW = False

REASON = "install Pillow to render overlay projections"


def frame(color: tuple[int, int, int]) -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), color).save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(PILLOW, REASON)
class RecordingCase(unittest.TestCase):
    """One two-frame recording built by hand; no test of its own."""

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name) / "trace"
        self.store = ActionTraceStore(self.directory)
        self.recording = self.directory / "recordings" / "demo"
        self.frames = [frame((30, 30, 30)), frame((60, 60, 60))]
        for index, data in enumerate(self.frames, start=1):
            self.store.save_bytes(
                "recordings/demo/frames", data, ".png", name=f"{index:06d}.png"
            )
            self.store.append_json(
                "recordings/demo/frames.ndjson",
                {
                    "index": index,
                    "t": 0.0 + index * 0.25,
                    "path": f"frames/{index:06d}.png",
                    "sha256": ActionTraceStore.sha256(data),
                    "bytes": len(data),
                },
            )
        for event, detail, moment in (
            ("target_found", "40,40,180,90 Button 'Save' backend=windows-uia", 0.25),
            ("pointer_move", "110,65", 0.25),
            ("click", "110,65 left x1", 0.5),
            ("verify", "passed window Save As", 0.5),
        ):
            self.store.append_json(
                "recordings/demo/timeline.ndjson",
                {"t": moment, "event": event, "detail": detail, "step_id": "s-0001"},
            )
        self.store.write_json(
            "recordings/demo/manifest.json",
            {
                "name": "demo",
                "interval_ms": 250,
                "frame_count": 2,
                "frames": "frames.ndjson",
                "timeline": "timeline.ndjson",
                "actions": ["s-0001"],
            },
        )


class RenderTests(RecordingCase):
    def test_the_presentation_projection_draws_on_every_frame(self) -> None:
        answer = render_overlays(self.recording, "presentation")
        self.assertEqual(answer["projection"], "presentation")
        self.assertEqual(answer["frames"], 2)
        out = Path(answer["out_dir"])
        rendered = sorted(out.glob("*.png"))
        self.assertEqual([path.name for path in rendered], ["000001.png", "000002.png"])
        for path, original in zip(rendered, self.frames):
            self.assertNotEqual(path.read_bytes(), original)
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 200))

    def test_every_projection_renders_and_they_are_not_the_same_picture(self) -> None:
        drawn = {}
        for projection in PROJECTIONS:
            answer = render_overlays(
                self.recording,
                projection,
                out_dir=Path(self._temp.name) / "out" / projection,
            )
            self.assertEqual(answer["frames"], 2)
            drawn[projection] = (
                Path(answer["out_dir"]) / "000002.png"
            ).read_bytes()
        self.assertEqual(len(set(drawn.values())), len(PROJECTIONS))

    def test_an_unknown_projection_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            render_overlays(self.recording, "cinematic")

    def test_a_recording_without_frames_renders_nothing(self) -> None:
        empty = self.directory / "recordings" / "empty"
        empty.mkdir(parents=True)
        self.assertEqual(render_overlays(empty, "clean")["frames"], 0)


class PlacementTests(RecordingCase):
    """Where a projection puts what it draws, read back off the pixels."""

    def test_the_verdict_is_drawn_under_the_rectangle_it_is_about(self) -> None:
        answer = render_overlays(self.recording, "presentation")
        with Image.open(Path(answer["out_dir"]) / "000002.png") as image:
            drawn = image.convert("RGB")
        # The box is 40,40..180,90 and the passed verdict is green: it belongs
        # just under the box, not in the corner of the frame.
        self.assertTrue(self._has(drawn, PASSED, (30, 95, 320, 150)))
        self.assertFalse(self._has(drawn, PASSED, (0, 0, 320, 35)))

    def test_without_a_rectangle_the_verdict_falls_back_to_the_corner(self) -> None:
        # A frame whose only active event is the verdict has nothing to sit
        # under, so the chip goes back to the margin of the captured region.
        self.store.append_json(
            "recordings/demo/frames.ndjson",
            {
                "index": 3,
                "t": 4.0,
                "path": "frames/000003.png",
                "sha256": "0" * 64,
                "bytes": 1,
            },
        )
        self.store.save_bytes(
            "recordings/demo/frames", frame((10, 10, 10)), ".png", name="000003.png"
        )
        self.store.append_json(
            "recordings/demo/timeline.ndjson",
            {"t": 4.0, "event": "verify", "detail": "passed file demo.txt",
             "step_id": "s-0002"},
        )
        answer = render_overlays(self.recording, "presentation")
        with Image.open(Path(answer["out_dir"]) / "000003.png") as image:
            drawn = image.convert("RGB")
        self.assertTrue(self._has(drawn, PASSED, (0, 0, 320, 80)))

    @staticmethod
    def _has(image, colour: str, box) -> bool:
        wanted = tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5))
        crop = image.crop(box)
        pixels = (
            crop.get_flattened_data()
            if hasattr(crop, "get_flattened_data")
            else crop.getdata()
        )
        return any(
            abs(pixel[0] - wanted[0]) < 30
            and abs(pixel[1] - wanted[1]) < 30
            and abs(pixel[2] - wanted[2]) < 30
            for pixel in pixels
        )


class BackendTests(unittest.TestCase):
    def test_without_pillow_the_renderer_refuses_by_name(self) -> None:
        self.assertEqual(PROJECTIONS, ("clean", "presentation", "developer"))
        with TemporaryDirectory() as temporary:
            recording = Path(temporary) / "recordings" / "demo"
            (recording / "frames").mkdir(parents=True)
            missing = dict.fromkeys(
                ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont")
            )
            with mock.patch.dict(sys.modules, missing):
                with self.assertRaises(RuntimeRefusal) as caught:
                    render_overlays(recording, "presentation")
        self.assertEqual(caught.exception.code, "BACKEND_UNAVAILABLE")
        self.assertIn("Pillow", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
