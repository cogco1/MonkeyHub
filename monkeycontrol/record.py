"""A recording: sequential frames, a timeline of what happened, a manifest.

The recorder captures the raw screen and nothing else. What the demo drew over
it is deliberately absent from these frames -- the overlay window excludes
itself from capture -- so the same recording can be replayed under any overlay
projection later, or under none. Every byte it keeps goes through
:class:`~monkeycontrol.store.ActionTraceStore`, the encoded video included:
ffmpeg is given the frames to read and a pipe to write to, so where a file
lands stays this package's decision and not a subprocess argument.
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
#: How long stop() waits for the capture thread before it says it is stuck.
JOIN_TIMEOUT_S = 30
ENCODE_TIMEOUT_S = 600
#: save_bytes writes into the store's own directory with this relative dir.
HERE = "."


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
        region: str = "virtual",
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
        self._region_name = str(region)
        # Read by the capture thread and replaced by the runtime between
        # frames; a tuple is swapped in one assignment, so no frame ever sees
        # half a rectangle.
        self._region: tuple[int, int, int, int] | None = None

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

    @property
    def region(self) -> tuple[int, int, int, int] | None:
        """The rectangle being captured; ``None`` is the whole virtual desktop."""

        return self._region

    def set_region(self, bounds) -> None:
        """Capture this rectangle from the next frame on.

        A recording that followed the whole desktop would keep whatever is open
        on the other monitor, which is both somebody's business and several
        megabytes a frame.
        """

        if bounds is None:
            self._region = None
            return
        rectangle = tuple(int(item) for item in bounds)
        wide = len(rectangle) == 4 and rectangle[2] > rectangle[0]
        if not wide or rectangle[3] <= rectangle[1]:
            raise ValueError("a capture region must be [left, top, right, bottom]")
        self._region = rectangle

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
        thread = self._thread
        if thread is not None:
            thread.join(timeout=JOIN_TIMEOUT_S)
            if thread.is_alive():
                # The manifest is written anyway, but a frame written after it
                # would not be in the count, so the count says it may be short.
                self._failures.append(
                    f"the capture thread was still running after {JOIN_TIMEOUT_S}s"
                )
        self._thread = None
        directory = self._store.directory / "recordings" / self._name
        manifest = {
            "name": self._name,
            "started_at": self._started_at,
            "stopped_at": _now(),
            "interval_ms": self._interval_ms,
            "frame_count": self._index,
            "frames": FRAME_INDEX,
            "timeline": TIMELINE,
            "region": self._region_name,
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
        shot = self._presentation.screenshot(bounds=self._region)
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


def encode_video(recording_dir: Path, fps: int, *, runner=subprocess.run) -> dict:
    """Turn a frame sequence into one file, or say honestly that nothing could.

    ffmpeg is preferred and Pillow's GIF is the fallback, and neither of them
    is allowed to choose where the bytes land: ffmpeg reads the frames and
    writes the container to its stdout, and what comes back goes through
    :class:`~monkeycontrol.store.ActionTraceStore` like every other byte this
    package keeps. ``runner`` is the seam a test replaces to stand in for a
    machine that has ffmpeg, or one whose ffmpeg fails.
    """

    directory = Path(recording_dir)
    frames = sorted((directory / FRAMES).glob("*.png"))
    if not frames:
        return {"format": None, "reason": "the recording kept no frame to encode"}
    tool = ffmpeg_path()
    if tool:
        encoded = _encode_mp4(tool, directory, len(frames), fps, runner)
        if encoded is not None:
            return encoded
    return _encode_gif(directory, frames, fps)


def _encode_mp4(
    tool: str, directory: Path, count: int, fps: int, runner
) -> dict | None:
    """``raw.mp4`` through the store, or ``None`` when ffmpeg would not."""

    command = [
        tool, "-y", "-loglevel", "error",
        "-framerate", str(fps), "-start_number", "1",
        "-i", str(directory / FRAMES / "%06d.png"),
        # H.264 needs even dimensions and a browser-friendly pixel format.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        # A pipe cannot be seeked, so the moov atom has to travel in front of
        # the media rather than be written back over the head of the file.
        "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]
    try:
        finished = runner(command, capture_output=True, timeout=ENCODE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    data = getattr(finished, "stdout", None)
    failed = getattr(finished, "returncode", 1) != 0
    if failed or not isinstance(data, bytes) or not data:
        return None
    ActionTraceStore(directory).save_bytes(HERE, data, ".mp4", name="raw.mp4")
    return {
        "format": "mp4",
        "path": "raw.mp4",
        "encoder": "ffmpeg",
        "frames": count,
        "writer": "monkeycontrol.store",
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
    store.save_bytes(HERE, buffer.getvalue(), ".gif", name="raw.gif")
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
