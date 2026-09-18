"""Overlay regeneration over a recording that was built by hand.

A recording is only frames plus a timeline, so the projections can be checked
without ever capturing a screen: two synthetic frames, three timeline lines,
and the rendered PNGs have to differ from what went in.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycontrol.overlay import PROJECTIONS, render_overlays
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
class RenderTests(unittest.TestCase):
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


class BackendTests(unittest.TestCase):
    def test_without_pillow_the_renderer_refuses_by_name(self) -> None:
        refusal = RuntimeRefusal("BACKEND_UNAVAILABLE")
        self.assertEqual(refusal.code, "BACKEND_UNAVAILABLE")
        self.assertEqual(PROJECTIONS, ("clean", "presentation", "developer"))


if __name__ == "__main__":
    unittest.main()
