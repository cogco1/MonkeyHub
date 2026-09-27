"""The content-keyed projection cache and its one background renderer (ADR-008, #367 part A).

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

One status row per key says pending, done or error. ``StatusStore`` is that
table's interface; ``InMemoryStatusStore`` is the part-A stand-in that part B
replaces with the table inside the project index before this issue merges. A
pending row is a lease: claimed when the worker starts the job and reclaimed
when it outlives its timeout or the process restarts, so a lost job never
leaves a permanent placeholder. A failure retries a bounded number of times
with backoff; a cancel gives its attempt back.

One low-priority worker thread takes one job at a time and hands it to a
long-lived render subprocess (``python -m`` this module), which opens the
project read-only, verifies the exact model and draws. The subprocess makes
the timeout and the memory cap real: past either it is killed or exits, and
the next job starts a fresh one.

A miss or a due retry first checks the requesting source (the route passes
``check``), so a stale or unknown source is refused to its own requester and
never becomes a row that every other run with the same model asset would
inherit; a retry draws from the source of the request that asked for it, not
from the row's first requester.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field, replace
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
from typing import Any, Callable, Mapping, Protocol

from archflow.contracts.canonical import canonical_digest

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
PNG_MEDIA_TYPE = "image/png"
PENDING, DONE, ERROR = "pending", "done", "error"

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MEMORY_CAP_MB = 3072
MAX_ATTEMPTS = 3
BACKOFF_S = (30.0, 300.0)
#: How long an unreferenced blob or temp file stays before the collector removes it.
GRACE_S = 7 * 24 * 3600.0
_COLLECT_EVERY_S = 3600.0
_MAX_QUEUED = 256
#: Lines of the render process's stderr kept for a failure's message.
_STDERR_TAIL = 20


class ProjectionError(ValueError):
    """A projection request names an unknown kind or recipe value."""


def pipeline_of(kind: str, recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Everything besides the input and the recipe that changes this kind's pixels."""

    from .drawings import model_view_pipeline

    if kind != MODEL_LINES:
        raise ProjectionError(f"unknown projection kind {kind!r}")
    return model_view_pipeline(recipe["view"])


def renderer_version(kind: str, recipe: Mapping[str, Any]) -> str:
    """The mesh renderer's own version and a digest of the whole pipeline it runs in."""

    pipeline = pipeline_of(kind, recipe)
    return f"{pipeline['mesh']['renderer']}/{canonical_digest(pipeline)[:16]}"


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
class ProjectionSpec:
    """What one key draws, and the exact retained model it is drawn from."""

    key: str
    kind: str
    recipe: Mapping[str, Any]
    renderer: str
    source: ModelSource

    @property
    def input_sha256(self) -> str:
        return self.source.asset_sha256

    def png_text(self) -> dict[str, str]:
        return {"archflow:projection-key": self.key, "archflow:input-sha256": self.input_sha256,
                "archflow:kind": self.kind, "archflow:recipe": json.dumps(dict(self.recipe), sort_keys=True),
                "archflow:renderer": self.renderer,
                "archflow:pipeline": json.dumps(pipeline_of(self.kind, self.recipe), sort_keys=True)}


def projection_spec(source: ModelSource, kind: str = MODEL_LINES, recipe: Mapping[str, Any] | None = None) -> ProjectionSpec:
    complete = recipe_of(kind, recipe or {})
    renderer = renderer_version(kind, complete)
    return ProjectionSpec(projection_key(source.asset_sha256, kind, complete, renderer=renderer),
                          kind, complete, renderer, source)


# ---------------------------------------------------------------- blobs


class BlobStore:
    """PNG bytes named by their own sha256; written to a temp file, then renamed into place."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.tmp = root / "tmp"

    def path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise ProjectionError("a blob is named by a lowercase sha256")
        return self.blobs / f"{sha256}.png"

    def put(self, data: bytes) -> str:
        sha256 = hashlib.sha256(data).hexdigest()
        target = self.path(sha256)
        if self.read(sha256) is not None:
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

    def read(self, sha256: str) -> bytes | None:
        """The bytes, or None when missing or corrupt: either is a miss."""

        try:
            data = self.path(sha256).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == sha256 else None

    def collect(self, referenced: set[str], *, now: float, grace_s: float) -> tuple[str, ...]:
        """Remove blobs no row references and temp files, once older than the grace window."""

        removed = []
        for folder, pattern in ((self.blobs, "*.png"), (self.tmp, "*.tmp")):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob(pattern)):
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
    """One key's row: pending (queued, or leased while ``claimed_at`` is set), done or error."""

    spec: ProjectionSpec
    status: str
    attempts: int = 0
    claimed_at: float | None = None
    blob_sha256: str | None = None
    error: str | None = None
    next_attempt_at: float | None = None
    load_ms: int | None = None
    render_ms: int | None = None

    @property
    def key(self) -> str:
        return self.spec.key


class StatusStore(Protocol):
    """The projection status table. Every method is atomic on its key."""

    def get(self, key: str) -> ProjectionStatus | None: ...

    def enqueue(self, spec: ProjectionSpec) -> ProjectionStatus:
        """Insert a queued pending row unless one exists; return the row either way."""

    def claim(self, key: str, *, now: float) -> ProjectionStatus | None:
        """Lease a queued row to the worker (attempts + 1); None when it is not queued."""

    def finish(self, key: str, *, blob_sha256: str, load_ms: int, render_ms: int) -> None: ...

    def fail(self, key: str, *, error: str, next_attempt_at: float | None) -> None: ...

    def release(self, key: str) -> None:
        """A cancel: remove the row and give its attempt back; nothing is recorded as failed."""

    def retry(self, key: str, spec: ProjectionSpec) -> ProjectionStatus | None:
        """Queue an error row again, keeping its attempts, to be drawn from ``spec``'s source.

        None when the row is not an error (for instance, another request retried it first).
        """

    def reclaim(self, *, now: float, lease_s: float) -> tuple[str, ...]:
        """Turn leases older than ``lease_s`` (all of them at startup) back into queued rows."""

    def rows(self) -> tuple[ProjectionStatus, ...]: ...

    def drop(self, keys: set[str]) -> None: ...


class InMemoryStatusStore:
    """STAND-IN for part A of #367: part B replaces it with the status table in index.sqlite.

    Rows live in this process only, so a restart forgets them; a blob already
    on disk is found again by its digest when the same projection is drawn.
    """

    def __init__(self) -> None:
        self._rows: dict[str, ProjectionStatus] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._rows.get(key)

    def enqueue(self, spec):
        with self._lock:
            return self._rows.setdefault(spec.key, ProjectionStatus(spec, PENDING))

    def claim(self, key, *, now):
        with self._lock:
            row = self._rows.get(key)
            if row is None or row.status != PENDING or row.claimed_at is not None:
                return None
            row = self._rows[key] = replace(row, claimed_at=now, attempts=row.attempts + 1)
            return row

    def finish(self, key, *, blob_sha256, load_ms, render_ms):
        with self._lock:
            row = self._rows[key]
            self._rows[key] = replace(row, status=DONE, claimed_at=None, blob_sha256=blob_sha256, error=None,
                                      next_attempt_at=None, load_ms=load_ms, render_ms=render_ms)

    def fail(self, key, *, error, next_attempt_at):
        with self._lock:
            row = self._rows[key]
            self._rows[key] = replace(row, status=ERROR, claimed_at=None, error=error, next_attempt_at=next_attempt_at)

    def release(self, key):
        with self._lock:
            self._rows.pop(key, None)

    def retry(self, key, spec):
        with self._lock:
            row = self._rows.get(key)
            if row is None or row.status != ERROR:
                return None
            row = self._rows[key] = replace(row, spec=spec, status=PENDING, claimed_at=None, next_attempt_at=None)
            return row

    def reclaim(self, *, now, lease_s):
        with self._lock:
            stale = [key for key, row in self._rows.items()
                     if row.status == PENDING and row.claimed_at is not None and now - row.claimed_at >= lease_s]
            for key in stale:
                self._rows[key] = replace(self._rows[key], claimed_at=None)
            return tuple(sorted(stale))

    def rows(self):
        with self._lock:
            return tuple(self._rows[key] for key in sorted(self._rows))

    def drop(self, keys):
        with self._lock:
            for key in keys:
                self._rows.pop(key, None)


# ---------------------------------------------------------------- rendering


class RenderFailed(RuntimeError):
    """The job ran and did not produce a projection; it counts as an attempt."""


class RenderCancelled(RuntimeError):
    """The job was stopped on purpose; its attempt is given back."""


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


@dataclass
class ProjectionQueue:
    """One low-priority worker drawing one projection at a time, enqueued on a read miss.

    ``start`` reclaims every lease (nothing is running yet), queues the pending
    rows again and collects old outputs; the worker starts on the first request.
    """

    cache_root: Path
    renderer_factory: Callable[[], Renderer]
    store: StatusStore = field(default_factory=InMemoryStatusStore)
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_attempts: int = MAX_ATTEMPTS
    grace_s: float = GRACE_S
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.blobs = BlobStore(self.cache_root)
        self._queued: deque[str] = deque()
        self._wake = threading.Condition()
        self._thread: threading.Thread | None = None
        self._renderer: Renderer | None = None
        self._running: str | None = None
        self._stopping = False
        self._collected_at: float | None = None

    @property
    def lease_s(self) -> float:
        # The worker kills a job at its timeout; a lease that outlives it was lost.
        return self.timeout_s + 30.0

    def start(self) -> None:
        with self._wake:
            if self._thread is not None or self._stopping:
                return
            self.store.reclaim(now=self.clock(), lease_s=0.0)
            for row in self.store.rows():
                if row.status == PENDING:
                    self._queued.append(row.key)
            self._thread = threading.Thread(target=self._work, daemon=True, name="projection-worker")
            self._thread.start()
        self.collect()

    def request(self, spec: ProjectionSpec, check: Callable[[ModelSource], None] | None = None) -> ProjectionStatus:
        """The key's row; a miss, a vanished blob or a due retry queues the job.

        ``check(spec.source)`` runs before anything is queued and raises when
        the requester's source cannot be drawn; the row is then left as it
        was. A due retry draws from the source of the request that asks for it.
        """

        self.start()
        row = self.store.get(spec.key)
        if row is not None and row.status == DONE and self.blobs.read(row.blob_sha256) is None:
            self.store.release(row.key)  # the cache directory was cleared: draw again
            row = None
        if row is not None and row.status == ERROR and self._due(row):
            if check is not None:
                check(spec.source)
            retried = self.store.retry(row.key, spec)
            if retried is None:  # another request retried it first
                return self.store.get(row.key) or row
            self._push(retried.key)
            return retried
        if (row is not None and row.status == PENDING and row.claimed_at is not None
                and self.clock() - row.claimed_at >= self.lease_s and row.key != self._running):
            self.store.reclaim(now=self.clock(), lease_s=self.lease_s)  # a lost job: take the lease back
            row = self.store.get(row.key)
            if row is not None:
                self._push(row.key)
        if row is None:
            if check is not None:
                check(spec.source)
            row = self.store.enqueue(spec)
            if row.status == PENDING and row.claimed_at is None:
                self._push(row.key)
        return row

    def _due(self, row: ProjectionStatus) -> bool:
        return (row.attempts < self.max_attempts and row.next_attempt_at is not None
                and self.clock() >= row.next_attempt_at)

    def _push(self, key: str) -> None:
        with self._wake:
            if key not in self._queued and key != self._running and len(self._queued) < _MAX_QUEUED:
                self._queued.append(key)
                self._wake.notify()

    def cancel(self, key: str) -> None:
        """Stop one job, queued or running; it is not a failure and keeps no attempt."""

        with self._wake:
            if key in self._queued:
                self._queued.remove(key)
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
        """Drop rows drawn by other renderer versions or salts; remove blobs nothing reaches after the grace window."""

        now = self.clock()
        stale = {row.key for row in self.store.rows()
                 if row.spec.renderer != renderer_version(row.spec.kind, row.spec.recipe)
                 or row.key != projection_key(row.spec.input_sha256, row.spec.kind, row.spec.recipe,
                                              renderer=row.spec.renderer)}
        self.store.drop(stale)
        referenced = {row.blob_sha256 for row in self.store.rows() if row.status == DONE and row.blob_sha256}
        self._collected_at = now
        return self.blobs.collect(referenced, now=now, grace_s=self.grace_s)

    def wait_idle(self, timeout_s: float = 60.0) -> bool:
        """For tests and benchmarks: True once nothing is queued or running."""

        deadline = time.monotonic() + timeout_s
        with self._wake:
            while self._queued or self._running is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._wake.wait(remaining)
            return True

    def _next(self) -> str | None:
        with self._wake:
            while not self._stopping:
                if self._queued:
                    self._running = self._queued.popleft()
                    return self._running
                self._wake.notify_all()
                self._wake.wait(timeout=5.0)
                if not self._queued:
                    self._idle()
            return None

    def _idle(self) -> None:
        # Called with the condition held and nothing queued: take back lost
        # leases, queue due retries and rows the full queue turned away, and
        # collect now and then.
        now = self.clock()
        self.store.reclaim(now=now, lease_s=self.lease_s)
        for row in self.store.rows():
            if len(self._queued) >= _MAX_QUEUED:
                break
            if row.status == ERROR and self._due(row) and self.store.retry(row.key, row.spec) is not None:
                self._queued.append(row.key)
            elif row.status == PENDING and row.claimed_at is None and row.key not in self._queued:
                self._queued.append(row.key)
        if self._collected_at is None or now - self._collected_at >= _COLLECT_EVERY_S:
            self._collected_at = now
            threading.Thread(target=self.collect, daemon=True, name="projection-collect").start()

    def _work(self) -> None:
        while (key := self._next()) is not None:
            try:
                self._run(key)
            except Exception:  # a store error must not end the worker; the lease comes back later
                _log.exception("projection job %s could not be recorded", key)
            finally:
                with self._wake:
                    self._running = None
                    self._wake.notify_all()

    def _run(self, key: str) -> None:
        row = self.store.claim(key, now=self.clock())
        if row is None:
            return
        try:
            if self._renderer is None:
                self._renderer = self.renderer_factory()
            result = self._renderer.render(row.spec, timeout_s=self.timeout_s)
            blob = self.blobs.put(result.png)
        except RenderCancelled:
            self.store.release(key)
            return
        except Exception as exc:  # a failed job is recorded, never raised into the worker
            detail = f"{type(exc).__name__}: {exc}" if not isinstance(exc, RenderFailed) else str(exc)
            retry_at = self.clock() + _backoff(row.attempts) if row.attempts < self.max_attempts else None
            self.store.fail(key, error=detail[-2000:], next_attempt_at=retry_at)
            return
        self.store.finish(key, blob_sha256=blob, load_ms=round(result.load_s * 1000),
                          render_ms=round(result.render_s * 1000))


def projection_queue(settings) -> ProjectionQueue:
    """The runtime's queue: its cache under the project's cache directory, rendering in a subprocess."""

    return ProjectionQueue(settings.project_cache_dir / "projections",
                           lambda: SubprocessRenderer(settings.project_dir))


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
    from .drawings import draw_model_view
    from ..settings import StudioSettings
    from ..transport.errors import StudioError

    answers.write(_READY.decode("ascii") + "\n")
    answers.flush()
    binding = None
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                if binding is None:
                    binding = ProjectBinding.open(StudioSettings(project_dir=project_dir, cad_export="off"))
                drawn = draw_model_view(binding, model_source=ModelSource.from_dict(request["source"]),
                                        view=request["view"], size_px=request["size"], png_text=request["text"])
                answer = {"ok": True, "png": base64.b64encode(drawn.png).decode("ascii"),
                          "loadS": drawn.load_s, "renderS": drawn.render_s}
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
