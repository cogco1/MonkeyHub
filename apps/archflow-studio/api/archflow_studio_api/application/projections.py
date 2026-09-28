"""The content-keyed projection cache and its one background renderer (ADR-008, #367).

A projection is a picture derived from one retained model: today the
axonometric line drawing of the model view, at a thumbnail size. It is
addressed by a key, not by a run or a time::

    key = sha256(input sha256, kind, sha256(recipe), renderer, SALT)

The input is the model asset's own sha256; the recipe holds every field a
request chooses (view, size, style). The renderer names everything else that
changes the pixels: the mesh renderer's version and a digest of the whole
pipeline (``drawings.model_view_pipeline``: the view frame, crop margin,
tessellation tolerance, the mesh renderer's settings and the OCP, numpy and
Pillow versions). Changing ``SALT`` or any of those gives every projection a
new key, so earlier outputs are simply never asked for again and the
collector removes them.

Where things live, all under the project's Hub cache directory (never the
project folder, never evidence; deleting it only means drawing again)::

    <cache dir>/projections/blobs/<sha256>.png    bytes named by their own digest
    <cache dir>/projections/tmp/                  written here, then renamed

Every PNG carries its input, kind, recipe and renderer version as text chunks.

One status row per key says pending, done or error. The rows live in the
project index (``archflow.project.index``, the ``projection`` table), so a
restart finds every picture it drew; ``StatusTable`` reads and writes them.
Inserting a row is the claim on a key (insert-ignore), and a per-key lock in
this process keeps two requests from racing past each other. A pending row
is a lease once the worker takes it: reclaimed at startup and when it
outlives its timeout, so a lost job never leaves a permanent placeholder. A
failure retries a bounded number of times with backoff; a cancel gives its
attempt back, and so does a source the render process refuses. A row that
becomes done moves the index's revision, so ``index.committed`` (domain
``projections``) tells clients, and their store holds the blob's digest.

Jobs are queued three ways, one priority each: the working position's model
when the index commits (``committed``), a reader's miss (what a client
shows), and every other model of the design tree after a commit. One
low-priority worker thread takes one job at a time, the same model's next
size first, and hands it to a long-lived render subprocess (``python -m``
this module), which checks the exact source, reads the model once for every
size it is asked to draw next, and draws. The subprocess makes the timeout
and the memory cap real: past either it is killed or exits, and the next job
starts a fresh one.

Every request checks its source (``check``), whether the key is new, done,
due for a retry or lost; so does an idle retry, which draws from the source
it last checked. A stale or unknown source is refused to its own requester
and never becomes a row that every other run with the same model asset
would inherit.

Drawings are projections too (#368): a retained elevation or section
perspective's SVG and PNG, a review sheet's PDF and DXF, and the raster of a
document page that Board, Publish and the export previews show. These kinds
are drawn on demand, in the request that needs them, through the same
generator the request would run anyway (``ProjectionQueue.on_demand``); the
background worker never draws them. Their row names the first file (the PNG
or PDF) as its blob and keeps every file's digest and what the receipt says
about the drawing in its body, so issuing the same recipe of the same content
again reads the bytes back instead of drawing. The cache is never evidence:
P036 keeps the issued files and their receipt, and a missing blob only means
drawing again.

The collector keeps what is reachable: a row whose input some artifact of
the index still names, or that was read within the grace window. An older
renderer's done row stays until the current one's replaces it, so pictures
change one at a time; blobs no row names go after the grace window. A done
row whose blob is gone (the cache directory was cleared) is drawn again when
that is noticed: at startup, on each commit's pass over the tree, when a
request finds it and when a client's blob read misses (``lost``).
"""

from __future__ import annotations

import base64
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from archflow.contracts.canonical import canonical_digest
from archflow.project.index import (
    PROJECTION_DONE as DONE, PROJECTION_ERROR as ERROR, PROJECTION_PENDING as PENDING, ProjectIndex, ProjectionRow,
)

from .artifacts import ModelSource

_log = logging.getLogger(__name__)

#: Bumped to invalidate every projection ever drawn, whatever its renderer.
SALT = "archflow-projection-1"
#: The axonometric line drawing of one complete model (``drawings.draw_model_view``).
MODEL_LINES = "model-line-view"
#: Each kind's recipe fields and the values each may take; the first is the default.
RECIPES: dict[str, dict[str, tuple[Any, ...]]] = {
    MODEL_LINES: {"view": ("axon",), "size": (512, 128, 256, 1024), "style": ("lines",)},
}
#: A retained model-axis elevation's SVG and PNG (``drawings.generate_elevation``).
ELEVATION = "drawing-elevation"
#: A retained section perspective's SVG and PNG (``drawings.generate_section_perspective``).
SECTION_PERSPECTIVE = "drawing-section-perspective"
#: A retained review sheet's PDF and DXF (``drawings.generate_sheet``).
SHEET = "drawing-sheet"
#: One page of a retained document as a bounded PNG: what Board, Publish and export previews show.
DOCUMENT_PAGE = "document-page"
#: The kinds drawn in the request that needs them (``ProjectionQueue.on_demand``), never by the worker.
ON_DEMAND = frozenset({ELEVATION, SECTION_PERSPECTIVE, SHEET, DOCUMENT_PAGE})
#: The file suffix each role of an on-demand projection's files is kept under.
FILE_SUFFIXES = {"png": ".png", "svg": ".svg", "pdf": ".pdf", "dxf": ".dxf"}
#: The recipe the design tree draws at: about twice its 80 x 56 px close cards.
TREE_RECIPE = {"size": 256}
PNG_MEDIA_TYPE = "image/png"
#: Queue priorities: the working position's model, what a reader shows, the rest of the tree.
CURRENT, VISIBLE, REST = 0, 1, 2

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MEMORY_CAP_MB = 3072
MAX_ATTEMPTS = 3
BACKOFF_S = (30.0, 300.0)
#: How long an unreferenced blob or temp file stays before the collector removes it.
GRACE_S = 7 * 24 * 3600.0
#: A read restarts a done row's grace window at most this often (a read does not write every time).
TOUCH_EVERY_S = 24 * 3600.0
_COLLECT_EVERY_S = 3600.0
_MAX_QUEUED = 256
#: How long a request waits for the project index to load before it is refused.
INDEX_WAIT_S = 10.0
#: Lines of the render process's stderr kept for a failure's message.
_STDERR_TAIL = 20


class ProjectionError(ValueError):
    """A projection request names an unknown kind or recipe value."""


def pipeline_of(kind: str, recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Everything besides the input and the recipe that changes this kind's bytes."""

    from .drawings import drawing_pipeline, model_view_pipeline

    if kind in ON_DEMAND:
        return drawing_pipeline(kind)
    if kind != MODEL_LINES:
        raise ProjectionError(f"unknown projection kind {kind!r}")
    return model_view_pipeline(recipe["view"])


@functools.cache
def _pipeline(kind: str, view: str | None) -> tuple[str, str]:
    # Once per process: the pipeline digests source and library versions, which
    # cannot change while the process runs, and reading them costs tens of ms.
    pipeline = pipeline_of(kind, {"view": view})
    if kind in ON_DEMAND:
        return json.dumps(pipeline, sort_keys=True), f"{kind}/{canonical_digest(pipeline)[:16]}"
    return json.dumps(pipeline, sort_keys=True), f"{pipeline['mesh']['renderer']}/{canonical_digest(pipeline)[:16]}"


def forget_renderer() -> None:
    """Compute the renderer version again on next use: only for tests that change a pipeline input in-process."""

    _pipeline.cache_clear()


def renderer_version(kind: str, recipe: Mapping[str, Any]) -> str:
    """The mesh renderer's own version and a digest of the whole pipeline it runs in (computed once per process)."""

    return _pipeline(kind, None if kind in ON_DEMAND else recipe["view"])[1]


def recipe_of(kind: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """The complete recipe: every field of the kind, defaults filled, unknown fields and values refused."""

    allowed = RECIPES.get(kind)
    if allowed is None:
        raise ProjectionError(f"unknown projection kind {kind!r}; use {', '.join(RECIPES)}")
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise ProjectionError(f"{kind} has no recipe field {', '.join(unknown)}")
    recipe = {}
    for name, values in allowed.items():
        value = fields.get(name, values[0])
        if value is None:
            value = values[0]
        if value not in values or isinstance(value, bool):
            raise ProjectionError(f"{kind} {name} must be one of {', '.join(map(str, values))}")
        recipe[name] = value
    return recipe


def projection_key(input_sha256: str, kind: str, recipe: Mapping[str, Any], *,
                   renderer: str, salt: str | None = None) -> str:
    """The cache address: no run, no time, nothing that does not change the output."""

    return canonical_digest({
        "input": input_sha256, "kind": kind, "recipe": canonical_digest(dict(recipe)),
        "renderer": renderer, "salt": SALT if salt is None else salt,
    })


@dataclass(frozen=True, slots=True)
class RecordedSource:
    """What an on-demand projection was drawn from, as its request named it: kept with the row, never resolved.

    The model, imported asset or document page the request had already
    verified through P036; ``assetSha256`` is the key's input.
    """

    fields: tuple[tuple[str, Any], ...]

    @classmethod
    def of(cls, value: Mapping[str, Any]) -> RecordedSource:
        if not isinstance(value.get("assetSha256"), str):
            raise ProjectionError("a projection's source names the content it is drawn from (assetSha256)")
        return cls(tuple(sorted(value.items())))

    @property
    def asset_sha256(self) -> str:
        return dict(self.fields)["assetSha256"]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.fields)


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """What one key draws, and the exact retained model (or, on demand, the verified content) it is drawn from."""

    key: str
    kind: str
    recipe: Mapping[str, Any]
    renderer: str
    source: ModelSource | RecordedSource

    @property
    def input_sha256(self) -> str:
        return self.source.asset_sha256

    @property
    def recipe_hash(self) -> str:
        return canonical_digest(dict(self.recipe))

    def png_text(self) -> dict[str, str]:
        return {"archflow:projection-key": self.key, "archflow:input-sha256": self.input_sha256,
                "archflow:kind": self.kind, "archflow:recipe": json.dumps(dict(self.recipe), sort_keys=True),
                "archflow:renderer": self.renderer,
                "archflow:pipeline": _pipeline(self.kind, self.recipe["view"])[0]}


def projection_spec(source: ModelSource, kind: str = MODEL_LINES, recipe: Mapping[str, Any] | None = None) -> ProjectionSpec:
    complete = recipe_of(kind, recipe or {})
    renderer = renderer_version(kind, complete)
    return ProjectionSpec(projection_key(source.asset_sha256, kind, complete, renderer=renderer),
                          kind, complete, renderer, source)


def on_demand_spec(kind: str, source: ModelSource | Mapping[str, Any], recipe: Mapping[str, Any]) -> ProjectionSpec:
    """The key of an on-demand kind: its input's own sha256, its complete recipe and its renderer; no run, no time.

    ``recipe`` holds every field that changes the bytes and is kept as given;
    the caller has resolved and verified ``source`` through P036.
    """

    if kind not in ON_DEMAND:
        raise ProjectionError(f"{kind!r} is not drawn on demand")
    if not isinstance(source, ModelSource):
        source = RecordedSource.of(source)
    recipe = json.loads(json.dumps(recipe, sort_keys=True))  # JSON values, as the row keeps them
    renderer = renderer_version(kind, recipe)
    return ProjectionSpec(projection_key(source.asset_sha256, kind, recipe, renderer=renderer),
                          kind, recipe, renderer, source)


# ---------------------------------------------------------------- blobs


class BlobStore:
    """PNG bytes named by their own sha256; written to a temp file, then renamed into place."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.tmp = root / "tmp"

    def path(self, sha256: str, suffix: str = ".png") -> Path:
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise ProjectionError("a blob is named by a lowercase sha256")
        if suffix not in FILE_SUFFIXES.values():
            raise ProjectionError(f"a blob is one of {', '.join(FILE_SUFFIXES.values())}")
        return self.blobs / f"{sha256}{suffix}"

    def put(self, data: bytes, suffix: str = ".png") -> str:
        sha256 = hashlib.sha256(data).hexdigest()
        target = self.path(sha256, suffix)
        if self.read(sha256, suffix) is not None:
            os.utime(target)  # seen again: the grace window starts over
            return sha256
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.tmp, prefix=sha256[:16], suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return sha256

    def exists(self, sha256: str, suffix: str = ".png") -> bool:
        """Whether the blob's file is there: a stat, not a read (a corrupt file is found when it is read)."""

        return self.path(sha256, suffix).is_file()

    def read(self, sha256: str, suffix: str = ".png") -> bytes | None:
        """The bytes, or None when missing or corrupt: either is a miss."""

        try:
            data = self.path(sha256, suffix).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == sha256 else None

    def collect(self, referenced: set[str], *, now: float, grace_s: float) -> tuple[str, ...]:
        """Remove blobs no row references and temp files, once older than the grace window."""

        removed = []
        blobs = tuple(f"*{suffix}" for suffix in FILE_SUFFIXES.values())
        for folder, patterns in ((self.blobs, blobs), (self.tmp, ("*.tmp",))):
            if not folder.is_dir():
                continue
            for path in sorted(path for pattern in patterns for path in folder.glob(pattern)):
                if folder is self.blobs and path.stem in referenced:
                    continue
                try:
                    if now - path.stat().st_mtime >= grace_s:
                        path.unlink()
                        removed.append(path.name)
                except OSError:
                    continue
        return tuple(removed)


# ---------------------------------------------------------------- status


@dataclass(frozen=True, slots=True)
class ProjectionStatus:
    """One key's row: pending (queued, or leased while ``claimed_at`` is set), done or error.

    ``spec.source`` is the source the row is drawn from: the last one checked.
    """

    spec: ProjectionSpec
    status: str
    attempts: int = 0
    claimed_at: float | None = None
    blob_sha256: str | None = None
    error: str | None = None
    next_attempt_at: float | None = None
    load_ms: int | None = None
    render_ms: int | None = None
    touched_at: float = 0.0
    #: An on-demand row's files by role (its blob is the first) and what was recorded about them.
    files: Mapping[str, str] | None = None
    facts: Mapping[str, Any] | None = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def png(self) -> bool:
        """Whether the row's blob is a PNG, which the blob route serves."""

        return self.files is None or next((role for role, sha256 in self.files.items()
                                           if sha256 == self.blob_sha256), "png") == "png"


def _status(row: ProjectionRow | None) -> ProjectionStatus | None:
    if row is None:
        return None
    source = row.body["source"]
    spec = ProjectionSpec(row.key, row.kind, dict(row.body["recipe"]), row.renderer_version,
                          RecordedSource.of(source) if row.kind in ON_DEMAND else ModelSource.from_dict(source))
    return ProjectionStatus(spec, row.status, row.attempts, row.claimed_at, row.blob_sha256, row.error,
                            row.next_attempt_at, row.load_ms, row.render_ms, row.touched_at,
                            row.body.get("files"), row.body.get("facts"))


def _body(spec: ProjectionSpec, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"recipe": dict(spec.recipe), "source": spec.source.to_dict(), **(extra or {})}


class StatusTable:
    """The projection status table in the project index; every method is one transaction.

    Raises ``IndexUnavailable`` when the index is closed.
    """

    def __init__(self, index: ProjectIndex | None) -> None:
        self._index = index

    @property
    def index(self) -> ProjectIndex:
        if self._index is None:
            from archflow.project.index import IndexUnavailable

            raise IndexUnavailable("this runtime keeps no project index")
        return self._index

    def get(self, key: str) -> ProjectionStatus | None:
        return _status(self.index.projection(key))

    def enqueue(self, spec: ProjectionSpec, *, now: float, body: Mapping[str, Any] | None = None) -> ProjectionStatus:
        """Insert a queued pending row unless one exists; return the row either way.

        ``body`` adds to what the row keeps: an on-demand row's files and facts.
        """

        return _status(self.index.enqueue_projection(ProjectionRow(
            spec.key, spec.input_sha256, spec.kind, spec.recipe_hash, spec.renderer, _body(spec, body)), now=now))

    def claim(self, key: str, *, now: float) -> ProjectionStatus | None:
        """Lease a queued row to the worker (attempts + 1); None when it is not queued."""

        return _status(self.index.claim_projection(key, now=now))

    def finish(self, key: str, *, blob_sha256: str, load_ms: int, render_ms: int, now: float) -> None:
        self.index.finish_projection(key, blob_sha256=blob_sha256, load_ms=load_ms, render_ms=render_ms, now=now)

    def fail(self, key: str, *, error: str, next_attempt_at: float | None, now: float) -> None:
        self.index.fail_projection(key, error=error, next_attempt_at=next_attempt_at, now=now)

    def release(self, key: str) -> None:
        """A cancel or a refused source: remove the row and give its attempt back; nothing is recorded as failed."""

        self.index.drop_projections({key})

    def retry(self, key: str, spec: ProjectionSpec, *, now: float) -> ProjectionStatus | None:
        """Queue an error row again, keeping its attempts, to be drawn from ``spec``'s source.

        None when the row is not an error (for instance, another request retried it first).
        """

        return _status(self.index.retry_projection(key, _body(spec), now=now))

    def reclaim(self, *, now: float, lease_s: float) -> tuple[str, ...]:
        """Turn leases older than ``lease_s`` (all of them at startup) back into queued rows."""

        return self.index.reclaim_projections(now=now, lease_s=lease_s)

    def rows(self, *, blob_sha256: str | None = None) -> tuple[ProjectionStatus, ...]:
        return tuple(_status(row) for row in self.index.projections(blob_sha256=blob_sha256))

    def redraw(self, keys: Sequence[str], *, now: float) -> tuple[ProjectionStatus, ...]:
        """Queue done rows whose blob is gone again, from the source each was drawn from; the rows now queued."""

        return tuple(_status(row) for row in self.index.redraw_projections(keys, now=now))

    def touch(self, key: str, *, now: float) -> None:
        """The row was read: its grace window starts over."""

        self.index.touch_projection(key, now=now)

    def drop(self, keys: Mapping[str, float]) -> None:
        """Remove rows not touched since the time given for each."""

        self.index.drop_projections(set(keys), touched=keys)

    def inputs(self) -> frozenset[str]:
        """The content hashes the project's artifacts still name: what a projection is reachable from."""

        return self.index.model_inputs()


# ---------------------------------------------------------------- rendering


class RenderFailed(RuntimeError):
    """The job ran and did not produce a projection; it counts as an attempt."""


class RenderCancelled(RuntimeError):
    """The job was stopped on purpose; its attempt is given back."""


class RenderRefused(RuntimeError):
    """The render process refused the source (it no longer names a retained model); its attempt is given back."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    png: bytes
    load_s: float
    render_s: float


class Renderer(Protocol):
    def render(self, spec: ProjectionSpec, *, timeout_s: float) -> RenderResult: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


class SubprocessRenderer:
    """One long-lived, low-priority render process; killed on timeout, cancel or close."""

    def __init__(self, project_dir: Path, *, memory_cap_mb: int = DEFAULT_MEMORY_CAP_MB) -> None:
        self.project_dir = Path(project_dir)
        self.memory_cap_mb = memory_cap_mb
        self._process: subprocess.Popen | None = None
        self._answers: queue.Queue = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=_STDERR_TAIL)
        #: Set once a render process has finished starting (for tests and diagnostics).
        self.ready = threading.Event()
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def _start(self) -> subprocess.Popen:
        api_root, repository_root = Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[5]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(api_root), str(repository_root), *filter(None, [environment.get("PYTHONPATH")])])
        flags = 0
        if os.name == "nt":
            flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            [sys.executable, "-m", "archflow_studio_api.application.projections",
             "--project-dir", str(self.project_dir), "--memory-cap-mb", str(self.memory_cap_mb)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, creationflags=flags,
        )
        answers: queue.Queue = queue.Queue()
        tail: deque[str] = deque(maxlen=_STDERR_TAIL)

        ready = self.ready
        ready.clear()

        def read() -> None:
            for line in process.stdout:
                if line.startswith(_READY):  # imports done; not an answer
                    ready.set()
                    continue
                answers.put(line)
            answers.put(None)

        def read_errors() -> None:
            for line in process.stderr:
                tail.append(line.decode("utf-8", "replace").rstrip()[:500])

        threading.Thread(target=read, daemon=True, name="projection-render-reader").start()
        self._stderr_reader = threading.Thread(target=read_errors, daemon=True, name="projection-render-stderr")
        self._stderr_reader.start()
        self._answers, self._stderr = answers, tail
        return process

    def stderr_tail(self) -> str:
        """The last lines the render process wrote to stderr, for diagnostics."""

        return "\n".join(self._stderr)

    def render(self, spec, *, timeout_s):
        with self._lock:
            self._cancelled = False
            if self._process is None or self._process.poll() is not None:
                self._process = self._start()
            process, answers = self._process, self._answers
        request = {"source": spec.source.to_dict(), "view": spec.recipe["view"], "size": spec.recipe["size"],
                   "text": spec.png_text()}
        try:
            process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            process.stdin.flush()
            line = answers.get(timeout=timeout_s)
        except queue.Empty:
            self._kill(process)
            raise RenderFailed(f"timed out after {timeout_s:g} s") from None
        except OSError:
            line = None
        if line is None:
            code = process.wait()
            with self._lock:
                if self._cancelled:
                    raise RenderCancelled("cancelled")
            if code == _MEMORY_EXIT:
                raise RenderFailed(f"memory cap of {self.memory_cap_mb} MB exceeded")
            self._stderr_reader.join(timeout=2)  # the process is gone: its stderr ends
            tail = self.stderr_tail()
            raise RenderFailed(f"render process exited with {code}" + (f": {tail[-1500:]}" if tail else ""))
        answer = json.loads(line)
        if answer.get("refused"):
            raise RenderRefused(answer["error"])
        if not answer["ok"]:
            raise RenderFailed(answer["error"])
        return RenderResult(base64.b64decode(answer["png"]), answer["loadS"], answer["renderS"])

    def _kill(self, process) -> None:
        process.kill()
        process.wait()

    def cancel(self):
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None and process.poll() is None:
            self._kill(process)

    def close(self):
        with self._lock:
            process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill(process)


# ---------------------------------------------------------------- the queue


def _backoff(attempts: int) -> float:
    return BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]


# What the worker is doing when it is not drawing (``_running``).
_TREE, _IDLE = "<tree>", "<idle>"


@dataclass
class ProjectionQueue:
    """One low-priority worker drawing one projection at a time.

    Enqueued on a reader's miss (``request``) and, after each commit of the
    index (``committed``), for every model ``tree_sources`` names: first the
    working position's, then the rest of the design tree. ``start`` reclaims
    every lease (nothing is running yet), queues the pending rows again and
    collects old outputs. ``check(spec)`` raises when ``spec``'s source cannot
    be drawn; it runs on every request and before every retry.
    """

    cache_root: Path
    renderer_factory: Callable[[], Renderer]
    store: StatusTable
    check: Callable[[ProjectionSpec], None] | None = None
    tree_sources: Callable[[], Sequence[tuple[int, ModelSource]]] | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_attempts: int = MAX_ATTEMPTS
    grace_s: float = GRACE_S
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.blobs = BlobStore(self.cache_root)
        self._queued: dict[int, deque[str]] = {CURRENT: deque(), VISIBLE: deque(), REST: deque()}
        self._priority: dict[str, int] = {}
        self._inputs: dict[str, str] = {}
        self._wake = threading.Condition()
        self._thread: threading.Thread | None = None
        self._renderer: Renderer | None = None
        self._running: str | None = None
        self._last_input: str | None = None
        self._stopping = False
        self._committed = False
        self._collected_at: float | None = None
        self._locks: dict[str, list] = {}
        self._locks_guard = threading.Lock()
        self._starting = threading.Lock()
        #: What was drawn, in order (by the worker or on demand), with load and render time (for tests and benchmarks).
        self.drawn: list[tuple[str, int, int]] = []

    @property
    def lease_s(self) -> float:
        # The worker kills a job at its timeout; a lease that outlives it was lost.
        return self.timeout_s + 30.0

    def start(self) -> None:
        # Startup reads the table without holding ``_wake``: a commit's listener
        # (``committed``) must never wait for it.
        with self._starting:
            with self._wake:
                if self._thread is not None or self._stopping:
                    return
            now = self.clock()
            self.store.reclaim(now=now, lease_s=0.0)
            rows = self.store.rows()
            queued = [row for row in rows if row.status == PENDING and _background(row) and _current(row)]
            # A done row whose blob is gone (the cache directory was cleared) is drawn again.
            queued += self._redraw([row for row in rows if row.status == DONE and not self._present(row)], now=now)
            with self._wake:
                if self._stopping:
                    return
                for row in queued:
                    self._push_locked(row.key, REST, row.spec.input_sha256)
                # The first pass over the tree: whatever was committed while no worker ran.
                self._committed = self.tree_sources is not None
                self._thread = threading.Thread(target=self._work, daemon=True, name="projection-worker")
                self._thread.start()
        self.collect()

    def _redraw(self, rows: Sequence[ProjectionStatus], *, now: float) -> list[ProjectionStatus]:
        """Done rows whose blob is gone: the current renderer's are queued again, an older one's dropped.

        Returns the rows now queued; the caller pushes them. The render process
        checks each one's source before it draws, and a refusal drops the row.
        An on-demand row is dropped too: the next request that needs it draws it.
        """

        if not rows:
            return []
        again = [row for row in rows if _background(row) and _current(row)]
        self.store.drop({row.key: row.touched_at for row in rows if row not in again})
        return list(self.store.redraw([row.key for row in again], now=now))

    def _present(self, row: ProjectionStatus, *, read: bool = False) -> bool:
        """Whether a done row's files are all there: a stat each, or with ``read`` a verified read."""

        digests = row.files if row.files is not None else {"png": row.blob_sha256}
        for role, sha256 in digests.items():
            suffix = FILE_SUFFIXES.get(role, ".png")
            if not (self.blobs.read(sha256, suffix) is not None if read else self.blobs.exists(sha256, suffix)):
                return False
        return True

    def lost(self, blob_sha256: str) -> int:
        """A client could not read this blob: every done row naming it is drawn again. Returns how many."""

        self.start()
        redrawn = self._redraw([row for row in self.store.rows(blob_sha256=blob_sha256)
                                if row.status == DONE and not self._present(row, read=True)], now=self.clock())
        for row in redrawn:
            self._push(row.key, VISIBLE, row.spec.input_sha256)
        return len(redrawn)

    def committed(self) -> None:
        """The index committed: queue the tree's models the worker has not drawn. Returns at once."""

        if self.tree_sources is None:
            return
        with self._wake:
            self._committed = True
            self._wake.notify_all()

    @contextmanager
    def _key(self, key: str):
        """The per-key lock of this process: one request at a time decides what a key needs."""

        with self._locks_guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._locks_guard:
                entry[1] -= 1
                if not entry[1]:
                    self._locks.pop(key, None)

    def request(self, spec: ProjectionSpec, *, priority: int = VISIBLE) -> ProjectionStatus:
        """The key's row; a miss, a vanished blob, a lost lease or a due retry queues the job.

        ``check(spec)`` runs first, whatever the row: a source that cannot be
        drawn is refused to its requester even when the key is done, and the
        row is left as it was. A due retry draws from this request's source.
        """

        self.start()
        if self.check is not None:
            self.check(spec)
        with self._key(spec.key):
            now = self.clock()
            row = self.store.get(spec.key)
            if row is not None and row.status == DONE and self.blobs.read(row.blob_sha256) is None:
                self.store.release(row.key)  # the cache directory was cleared: draw again
                row = None
            if row is not None and row.status == ERROR and self._due(row):
                retried = self.store.retry(row.key, spec, now=now)
                if retried is None:  # another request retried it first
                    return self.store.get(row.key) or row
                self._push(retried.key, priority, spec.input_sha256)
                return retried
            if (row is not None and row.status == PENDING and row.claimed_at is not None
                    and now - row.claimed_at >= self.lease_s and row.key != self._running):
                self.store.reclaim(now=now, lease_s=self.lease_s)  # a lost job: take the lease back
                row = self.store.get(row.key)
            if row is None:
                row = self.store.enqueue(spec, now=now)
            if row.status == PENDING and row.claimed_at is None:
                self._push(row.key, priority, spec.input_sha256)
            elif row.status == DONE and now - row.touched_at >= min(TOUCH_EVERY_S, self.grace_s / 2):
                self.store.touch(row.key, now=now)  # still read: its grace window starts over
            return row

    def read(self, key: str) -> ProjectionStatus | None:
        """A known key's row, requested again from the source it was last drawn from (checked again)."""

        row = self.store.get(key)
        if row is not None and row.spec.kind in ON_DEMAND:
            return row  # drawn again only by the request that needs it
        return None if row is None else self.request(row.spec)

    def on_demand(self, spec: ProjectionSpec, draw: Callable[[], tuple[Mapping[str, bytes], Mapping[str, Any]]],
              ) -> tuple[dict[str, bytes], dict[str, Any], bool]:
        """An on-demand projection's files and facts, and whether they came from the cache.

        A done row whose every file reads back is a hit: nothing is drawn. Any
        other row (its blobs gone, or left by a request that died) is dropped,
        and ``draw()`` runs once, under this key's lock, returning the files by
        role (the first becomes the row's blob) and what was recorded about
        them as JSON values. They are kept, then the row is inserted done in one
        step, so no worker ever sees it pending. ``draw`` raising leaves no row:
        a refused request is its caller's answer, not this key's failure.
        Raises ``IndexUnavailable`` when the index does not answer.
        """

        if spec.kind not in ON_DEMAND:
            raise ProjectionError(f"{spec.kind!r} is drawn by the worker, not on demand")
        with self._key(spec.key):
            now = self.clock()
            row = self.store.get(spec.key)
            if row is not None and row.status == DONE and row.files is not None:
                files = self._files(row.files)
                if files is not None:
                    if now - row.touched_at >= min(TOUCH_EVERY_S, self.grace_s / 2):
                        self.store.touch(row.key, now=now)  # still asked for: its grace window starts over
                    return files, dict(row.facts or {}), True
            if row is not None:
                self.store.drop({row.key: row.touched_at})
            started = time.perf_counter()
            files, facts = draw()
            render_ms = round((time.perf_counter() - started) * 1000)
            files = {role: bytes(data) for role, data in files.items()}
            facts = json.loads(json.dumps(facts, sort_keys=True))
            digests = {role: self.blobs.put(data, FILE_SUFFIXES[role]) for role, data in files.items()}
            self.store.enqueue(spec, now=now, body={"files": digests, "facts": facts})
            if self.store.claim(spec.key, now=now) is not None:
                self.store.finish(spec.key, blob_sha256=next(iter(digests.values())), load_ms=0,
                                  render_ms=render_ms, now=self.clock())
            self.drawn.append((spec.key, 0, render_ms))
            return files, facts, False

    def _files(self, digests: Mapping[str, str]) -> dict[str, bytes] | None:
        """Every file of an on-demand row, verified against its digest; None when any is missing or corrupt."""

        files = {}
        for role, sha256 in digests.items():
            data = self.blobs.read(sha256, FILE_SUFFIXES.get(role, ".png"))
            if data is None:
                return None
            files[role] = data
        return files

    def _due(self, row: ProjectionStatus) -> bool:
        return (row.attempts < self.max_attempts and row.next_attempt_at is not None
                and self.clock() >= row.next_attempt_at)

    def _push(self, key: str, priority: int, input_sha256: str) -> None:
        with self._wake:
            self._push_locked(key, priority, input_sha256)

    def _push_locked(self, key: str, priority: int, input_sha256: str) -> None:
        if key == self._running:
            return
        kept = self._priority.get(key)
        if kept is not None:
            if kept <= priority:
                return
            self._queued[kept].remove(key)  # asked for sooner: moved up
        elif sum(map(len, self._queued.values())) >= _MAX_QUEUED and priority == REST:
            return  # the row stays pending; an idle pass queues it later
        self._queued[priority].append(key)
        self._priority[key] = priority
        self._inputs[key] = input_sha256
        self._wake.notify_all()

    def _pop_locked(self) -> str | None:
        order = [key for priority in (CURRENT, VISIBLE, REST) for key in self._queued[priority]]
        if not order:
            return None
        # The same model's next size first: its shapes are still loaded in the render process.
        key = next((key for key in order if self._last_input is not None
                    and self._inputs.get(key) == self._last_input), order[0])
        self._queued[self._priority.pop(key)].remove(key)
        self._last_input = self._inputs.pop(key, None)
        return key

    def cancel(self, key: str) -> None:
        """Stop one job, queued or running; it is not a failure and keeps no attempt."""

        with self._wake:
            priority = self._priority.pop(key, None)
            if priority is not None:
                self._queued[priority].remove(key)
                self._inputs.pop(key, None)
                self.store.release(key)
                return
            running = key == self._running
        if running and self._renderer is not None:
            self._renderer.cancel()

    def shutdown(self) -> None:
        with self._wake:
            self._stopping = True
            running = self._running
            self._wake.notify_all()
        if running is not None and self._renderer is not None:
            self._renderer.cancel()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._renderer is not None:
            self._renderer.close()

    def collect(self) -> tuple[str, ...]:
        """Drop what is no longer reachable or superseded; remove blobs no row names after the grace window.

        Any row goes when its input is no artifact's any more and its grace
        window (restarted by reads, ``TOUCH_EVERY_S``) has passed. An older
        renderer's or salt's done row otherwise stays until the current one's
        done row for the same input and recipe exists, however old it is, so a
        renderer change replaces pictures one at a time; its rows not yet drawn
        go at once. A blob a remaining row names is never removed.
        """

        now = self.clock()
        rows = self.store.rows()
        inputs = self.store.inputs()
        replaced = {(row.spec.input_sha256, row.spec.kind, row.spec.recipe_hash)
                    for row in rows if row.status == DONE and _current(row)}
        drop: dict[str, float] = {}
        for row in rows:
            unreachable = now - row.touched_at >= self.grace_s and row.spec.input_sha256 not in inputs
            if unreachable or (not _current(row) and (row.status != DONE or (
                    row.spec.input_sha256, row.spec.kind, row.spec.recipe_hash) in replaced)):
                drop[row.key] = row.touched_at
        self.store.drop(drop)
        referenced = {sha256 for row in self.store.rows()
                      for sha256 in (row.blob_sha256, *(row.files or {}).values()) if sha256}
        self._collected_at = now
        return self.blobs.collect(referenced, now=now, grace_s=self.grace_s)

    def wait_idle(self, timeout_s: float = 60.0) -> bool:
        """For tests and benchmarks: True once nothing is queued or running and no commit awaits its pass."""

        deadline = time.monotonic() + timeout_s
        with self._wake:
            while any(self._queued.values()) or self._running is not None or self._committed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._wake.wait(remaining)
            return True

    def _next(self) -> str | None:
        with self._wake:
            while not self._stopping:
                if self._committed:
                    self._committed = False
                    self._running = _TREE
                    return _TREE
                key = self._pop_locked()
                if key is not None:
                    self._running = key
                    return key
                self._wake.notify_all()
                self._wake.wait(timeout=5.0)
                if not self._committed and not any(self._queued.values()) and not self._stopping:
                    self._running = _IDLE
                    return _IDLE
            return None

    def _work(self) -> None:
        while (job := self._next()) is not None:
            try:
                if job == _TREE:
                    self._queue_tree()
                elif job == _IDLE:
                    self._idle()
                else:
                    self._run(job)
            except Exception:  # a store error must not end the worker; the lease comes back later
                _log.exception("projection job %s could not be recorded", job)
            finally:
                with self._wake:
                    self._running = None
                    self._wake.notify_all()

    def _queue_tree(self) -> None:
        """Queue every model of the tree without a row: the working position's first, then the rest."""

        for priority, source in self.tree_sources():
            spec = projection_spec(source, MODEL_LINES, TREE_RECIPE)
            with self._key(spec.key):
                row = self.store.get(spec.key)
                if row is None:
                    if self.check is not None:
                        try:
                            self.check(spec)
                        except Exception:  # noqa: BLE001 - a model the tree cannot draw stays a placeholder
                            continue
                    row = self.store.enqueue(spec, now=self.clock())
                elif row.status == DONE and not self.blobs.exists(row.blob_sha256):
                    row = next(iter(self._redraw([row], now=self.clock())), row)  # the cache was cleared
            if row.status == PENDING and row.claimed_at is None:
                self._push(row.key, priority, spec.input_sha256)

    def _idle(self) -> None:
        # Nothing queued: take back lost leases, queue due retries (their source
        # checked again) and rows the full queue turned away, and collect now and then.
        now = self.clock()
        self.store.reclaim(now=now, lease_s=self.lease_s)
        for row in self.store.rows():
            if sum(map(len, self._queued.values())) >= _MAX_QUEUED:
                break
            if not _background(row) or not _current(row):
                continue
            if row.status == ERROR and self._due(row):
                try:
                    if self.check is not None:
                        self.check(row.spec)
                except Exception:  # noqa: BLE001 - a source no longer drawable is not retried
                    continue
                if self.store.retry(row.key, row.spec, now=now) is not None:
                    self._push(row.key, REST, row.spec.input_sha256)
            elif row.status == PENDING and row.claimed_at is None:
                self._push(row.key, REST, row.spec.input_sha256)
        if self._collected_at is None or now - self._collected_at >= _COLLECT_EVERY_S:
            self.collect()

    def _run(self, key: str) -> None:
        row = self.store.claim(key, now=self.clock())
        if row is None:
            return
        if not _background(row) or not _current(row):
            # Drawn now it would carry another renderer's key, or it is drawn only on demand.
            self.store.release(key)
            return
        try:
            if self._renderer is None:
                self._renderer = self.renderer_factory()
            result = self._renderer.render(row.spec, timeout_s=self.timeout_s)
            blob = self.blobs.put(result.png)
        except (RenderCancelled, RenderRefused):
            self.store.release(key)
            return
        except Exception as exc:  # a failed job is recorded, never raised into the worker
            detail = f"{type(exc).__name__}: {exc}" if not isinstance(exc, RenderFailed) else str(exc)
            retry_at = self.clock() + _backoff(row.attempts) if row.attempts < self.max_attempts else None
            self.store.fail(key, error=detail[-2000:], next_attempt_at=retry_at, now=self.clock())
            return
        load_ms, render_ms = round(result.load_s * 1000), round(result.render_s * 1000)
        self.drawn.append((key, load_ms, render_ms))
        self.store.finish(key, blob_sha256=blob, load_ms=load_ms, render_ms=render_ms, now=self.clock())


def _background(row: ProjectionStatus) -> bool:
    """Whether the worker draws this row's kind (on-demand kinds are drawn by their request)."""

    return row.spec.kind in RECIPES


def _current(row: ProjectionStatus) -> bool:
    """Whether the current renderer and salt would give this row's key."""

    spec = row.spec
    try:
        renderer = renderer_version(spec.kind, spec.recipe)
    except (ProjectionError, ValueError, KeyError, TypeError):
        return False
    return spec.renderer == renderer and spec.key == projection_key(
        spec.input_sha256, spec.kind, spec.recipe, renderer=renderer)


def tree_model_sources(index: ProjectIndex) -> list[tuple[int, ModelSource]]:
    """The models the design tree shows, from one snapshot of the index, in the order to draw them.

    The working position's run first (``CURRENT``); then each committed
    Stage's model, newest first, and each candidate run's models (``REST``).
    A model is a run's available 3dm artifact with its exact model source
    (``artifact_model_source``).
    """

    with index.snapshot() as snapshot:
        working = next((entity["body"] for entity in snapshot.entities(["working"])), None) or {}
        stages = snapshot.rows("stage", limit=None)
        candidates = snapshot.rows("candidate", limit=None)
        artifacts = snapshot.rows("artifact", {"format": "3dm"}, limit=None)
    models: dict[str, list[ModelSource]] = {}
    for row in artifacts:
        # As ``artifact_model_source`` reads a listed row: its declared source, else its run's exact state.
        body, source = row["body"], row["body"].get("model_source")
        if not row["available"]:
            continue
        if isinstance(source, Mapping):
            source = ModelSource(source["run_id"], source["state_digest"], source["asset_sha256"])
        elif body.get("design_state_digest") and row["sha256"]:
            source = ModelSource(row["run_id"], body["design_state_digest"], row["sha256"])
        else:
            continue
        models.setdefault(row["run_id"], []).append(source)
    order: list[tuple[int, ModelSource]] = []
    current = working.get("current")
    order += [(CURRENT, source) for source in models.get(current, ())] if isinstance(current, str) else []
    for stage in sorted(stages, key=lambda row: (-row["position"], row["branch_id"])):
        order += [(REST, source) for source in models.get(stage["candidate_id"], ())
                  if source.asset_sha256 == stage["body"].get("model_sha256")]
    for candidate in candidates:
        order += [(REST, source) for source in models.get(candidate["run_id"], ())]
    seen: set[ModelSource] = set()
    return [(priority, source) for priority, source in order if not (source in seen or seen.add(source))]


def projection_queue(settings, index: ProjectIndex | None = None, *,
                     check: Callable[[ProjectionSpec], None] | None = None) -> ProjectionQueue:
    """The runtime's queue: its blobs in the project's cache directory, its rows in ``index``, drawn in a subprocess.

    ``index`` is the project index the binding keeps (``open_projections``);
    without one the table refuses every call with ``IndexUnavailable``.
    """

    return ProjectionQueue(
        settings.project_cache_dir / "projections", lambda: SubprocessRenderer(settings.project_dir),
        StatusTable(index), check=check, tree_sources=None if index is None else (lambda: tree_model_sources(index)))


def open_projections(binding) -> ProjectionQueue:
    """The queue of ``binding``'s project, once its index has loaded; every request checks its source.

    Raises ``IndexUnavailable`` when the runtime keeps no index, or it has not loaded within ``INDEX_WAIT_S``.
    """

    from archflow.project.index import IndexUnavailable
    from .drawings import check_model_view_source

    keeper = binding.await_index(INDEX_WAIT_S)
    if keeper is None:
        raise IndexUnavailable(f"project index: {binding.index_status() or 'this process keeps none'}")
    return projection_queue(binding.settings, keeper.index,
                            check=lambda spec: check_model_view_source(binding, spec.source, spec.recipe["view"]))


# ---------------------------------------------------------------- the render process

_MEMORY_EXIT = 70
_READY = b'{"ready": true}'


def _resident_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        with open("/proc/self/statm", encoding="ascii") as stream:
            return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def _watch_memory(cap_bytes: int) -> None:
    while True:
        used = _resident_bytes()
        if used is not None and used > cap_bytes:
            os._exit(_MEMORY_EXIT)
        time.sleep(0.25)


def _serve(project_dir: Path, memory_cap_mb: int) -> None:
    # Answers go to a private copy of stdout; anything a native library prints
    # goes to the null device instead of into the protocol.
    answers = os.fdopen(os.dup(1), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    if hasattr(os, "nice"):
        os.nice(10)
    threading.Thread(target=_watch_memory, args=(memory_cap_mb * 1024 * 1024,), daemon=True).start()
    from .binding import ProjectBinding
    from .drawings import check_model_view_source, draw_loaded_view, load_model_view
    from ..settings import StudioSettings
    from ..transport.errors import StudioError

    answers.write(_READY.decode("ascii") + "\n")
    answers.flush()
    binding = None
    # The last model read: the next size of the same model is drawn without reading it again.
    loaded = None
    try:
        for line in sys.stdin:
            request = json.loads(line)
            source = ModelSource.from_dict(request["source"])
            try:
                if binding is None:
                    binding = ProjectBinding.open(StudioSettings(project_dir=project_dir, cad_export="off"))
                check_model_view_source(binding, source, request["view"])
            except StudioError as exc:
                # The source names no retained model (any more): not this projection's failure.
                answer = {"ok": False, "refused": True, "error": f"{exc.code}: {exc.detail}"}
            else:
                try:
                    reused = loaded is not None and loaded.model_source == source
                    if not reused:
                        loaded = None
                        loaded = load_model_view(binding, source)
                    drawn = draw_loaded_view(loaded, view=request["view"], size_px=request["size"],
                                             png_text=request["text"])
                    answer = {"ok": True, "png": base64.b64encode(drawn.png).decode("ascii"),
                              "loadS": 0.0 if reused else drawn.load_s, "renderS": drawn.render_s}
                except StudioError as exc:
                    answer = {"ok": False, "error": f"{exc.code}: {exc.detail}"}
                except Exception as exc:  # reported to the queue as this job's failure
                    answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            answers.write(json.dumps(answer) + "\n")
            answers.flush()
    finally:
        if binding is not None:
            binding.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render projections for one project; driven over stdin/stdout.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--memory-cap-mb", type=int, default=DEFAULT_MEMORY_CAP_MB)
    arguments = parser.parse_args()
    _serve(arguments.project_dir, arguments.memory_cap_mb)
