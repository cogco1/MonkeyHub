"""A recording: sequential frames, a timeline of what happened, a manifest.

The recorder captures the raw screen and nothing else. What the demo drew over
it is deliberately absent from these frames -- the overlay window excludes
itself from capture -- so the same recording can be replayed under any overlay
projection later, or under none. Every byte it keeps goes through
:class:`~monkeycontrol.store.ActionTraceStore`; the one exception is the
encoded video, which ffmpeg writes itself into the recording directory the
store handed out, and the manifest says so.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from .store import ActionTraceStore

FRAMES = "frames"
FRAME_INDEX = "frames.ndjson"
TIMELINE = "timeline.ndjson"
MANIFEST = "manifest.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: How many failed captures in a row end a recording rather than fill a log.
GIVE_UP_AFTER = 3
#: A GIF is a fallback, not an archive: it is sampled and scaled to stay usable.
GIF_MAX_FRAMES = 240
GIF_MAX_WIDTH = 800
GIF_COLORS = 128
MAX_FPS = 30
ENCODE_TIMEOUT_S = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class Recorder:
    """One capture thread, one timeline and the manifest that ties them."""

    def __init__(
        self,
        store: ActionTraceStore,
        presentation,
        name: str,
        *,
        interval_ms: int = 250,
        clock=time.monotonic,
    ) -> None:
        if not NAME.match(str(name)):
            raise ValueError(
                f"{name!r} must be a plain recording name: letters, digits, - and _"
            )
        if interval_ms <= 0:
            raise ValueError("a recording interval must be a positive number of ms")
        self._store = store
        self._presentation = presentation
        self._name = str(name)
        self._interval_ms = int(interval_ms)
        self._clock = clock
        self._origin = clock()
        self._started_at = _now()
        self._stopping = threading.Event()
        self._notes = threading.Lock()
        self._thread: threading.Thread | None = None
        self._index = 0
        self._steps: list[str] = []
        self._failures: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def relative_dir(self) -> str:
        return f"recordings/{self._name}"

    @property
    def started_at(self) -> str:
        return self._started_at

    @property
    def frame_count(self) -> int:
        return self._index

    def start(self) -> None:
        """Begin capturing; capturing twice from one recorder is a mistake."""

        if self._thread is not None:
            raise RuntimeError(f"{self._name!r} is already capturing")
        self._thread = threading.Thread(
            target=self._capture_loop, name=f"monkeycontrol-record-{self._name}",
            daemon=True,
        )
        self._thread.start()

    def note(self, event: str, detail: str, *, step_id: str) -> None:
        """Record one thing that happened, at its own moment on the timeline."""

        with self._notes:
            if step_id not in self._steps:
                self._steps.append(step_id)
            self._store.append_json(
                f"{self.relative_dir}/{TIMELINE}",
                {
                    "t": round(self._clock() - self._origin, 3),
                    "event": str(event),
                    "detail": str(detail),
                    "step_id": str(step_id),
                },
            )

    def stop(self) -> dict:
        """End the capture, encode what can be encoded, write the manifest."""

        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=30)
        directory = self._store.directory / "recordings" / self._name
        manifest = {
            "name": self._name,
            "started_at": self._started_at,
            "stopped_at": _now(),
            "interval_ms": self._interval_ms,
            "frame_count": self._index,
            "frames": FRAME_INDEX,
            "timeline": TIMELINE,
            "actions": list(self._steps),
            "video": encode_video(directory, self.fps),
        }
        if self._failures:
            manifest["capture_errors"] = list(self._failures)
        self._store.write_json(f"{self.relative_dir}/{MANIFEST}", manifest)
        return manifest

    @property
    def fps(self) -> int:
        return min(MAX_FPS, max(1, round(1000 / self._interval_ms)))

    def _capture_loop(self) -> None:
        missed = 0
        while True:
            try:
                self._frame()
                missed = 0
            except Exception as exc:  # a lost frame must not end the session
                missed += 1
                self._failures.append(f"{type(exc).__name__}: {exc}")
                if missed >= GIVE_UP_AFTER:
                    return
            if self._stopping.wait(self._interval_ms / 1000):
                return

    def _frame(self) -> None:
        shot = self._presentation.screenshot()
        png = shot["png"]
        index = self._index + 1
        name = f"{index:06d}.png"
        self._store.save_bytes(f"{self.relative_dir}/{FRAMES}", png, ".png", name=name)
        self._store.append_json(
            f"{self.relative_dir}/{FRAME_INDEX}",
            {
                "index": index,
                "t": round(self._clock() - self._origin, 3),
                "path": f"{FRAMES}/{name}",
                "sha256": ActionTraceStore.sha256(png),
                "bytes": len(png),
                # Where this frame's top-left sits on a multi-monitor desktop,
                # so a replayed overlay can put a screen point on it.
                "bounds": [int(item) for item in shot.get("bounds") or (0, 0, 0, 0)],
            },
        )
        self._index = index


def ffmpeg_path() -> str | None:
    """ffmpeg, from ``MONKEYCONTROL_FFMPEG`` or the PATH, or ``None``."""

    named = os.environ.get("MONKEYCONTROL_FFMPEG")
    if named:
        return named if Path(named).is_file() else shutil.which(named)
    return shutil.which("ffmpeg")


def encode_video(recording_dir: Path, fps: int) -> dict:
    """Turn a frame sequence into one file, or say honestly that nothing could.

    ffmpeg is preferred and writes ``raw.mp4`` itself, into the directory the
    store created for these frames; Pillow's GIF goes back through the store
    like every other byte this package keeps.
    """

    directory = Path(recording_dir)
    frames = sorted((directory / FRAMES).glob("*.png"))
    if not frames:
        return {"format": None, "reason": "the recording kept no frame to encode"}
    tool = ffmpeg_path()
    if tool:
        encoded = _encode_mp4(tool, directory, len(frames), fps)
        if encoded is not None:
            return encoded
    return _encode_gif(directory, frames, fps)


def _encode_mp4(tool: str, directory: Path, count: int, fps: int) -> dict | None:
    """``raw.mp4`` beside the frames, or ``None`` when ffmpeg would not."""

    destination = directory / "raw.mp4"
    command = [
        tool, "-y", "-loglevel", "error",
        "-framerate", str(fps), "-start_number", "1",
        "-i", str(directory / FRAMES / "%06d.png"),
        # H.264 needs even dimensions and a browser-friendly pixel format.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(destination),
    ]
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True, timeout=ENCODE_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0 or not destination.is_file():
        return None
    return {
        "format": "mp4",
        "path": destination.name,
        "encoder": "ffmpeg",
        "frames": count,
        "writer": "ffmpeg, into the recording directory monkeycontrol.store created",
    }


def _encode_gif(directory: Path, frames: list[Path], fps: int) -> dict:
    """``raw.gif`` through the store, sampled and scaled to stay openable."""

    try:
        from PIL import Image
    except ImportError:
        return {
            "format": None,
            "reason": "ffmpeg not found and Pillow not importable",
        }
    chosen = _sampled(frames, GIF_MAX_FRAMES)
    pictures = []
    buffer = BytesIO()
    try:
        for path in chosen:
            with Image.open(path) as opened:
                picture = opened.convert("RGB")
            picture.thumbnail((GIF_MAX_WIDTH, GIF_MAX_WIDTH))
            pictures.append(picture.quantize(colors=GIF_COLORS))
        pictures[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=pictures[1:],
            duration=max(20, round(1000 / max(1, fps))),
            loop=0,
            optimize=True,
        )
    except (OSError, ValueError) as exc:
        # A frame this machine cannot open is a reason, not a lost recording:
        # the frames themselves are still on disk and still replayable.
        return {"format": None, "reason": f"Pillow could not encode the frames: {exc}"}
    store = ActionTraceStore(directory)
    store.save_bytes(".", buffer.getvalue(), ".gif", name="raw.gif")
    return {
        "format": "gif",
        "path": "raw.gif",
        "encoder": "pillow",
        "frames": len(pictures),
        "writer": "monkeycontrol.store",
    }


def _sampled(frames: list[Path], most: int) -> list[Path]:
    """Every frame, or an evenly spread subset when there are too many."""

    if len(frames) <= most:
        return frames
    step = len(frames) / most
    return [frames[min(len(frames) - 1, int(index * step))] for index in range(most)]
