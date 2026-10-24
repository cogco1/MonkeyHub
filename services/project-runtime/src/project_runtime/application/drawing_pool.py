"""Drawings solved beside the Project Runtime's process (#300): a pool of drawing workers.

Solving a view reads the source's shapes and runs OCCT's hidden-line solve,
which holds the interpreter while it works. In the Runtime's own process that
stopped every other request, a status or list read included, until the
drawing was done, and drawings solved one after another. Here each solve runs
in a worker process (``monkeydiagram.drawing_runs.run_draw_task``), as many at
once as there are workers; the Runtime keeps retaining and registering each
result, and answers other requests meanwhile.

Only the serving process starts the pool (``start``, from ``main``). A process
that never does, a test for one, solves in its own process as before.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from monkeydiagram.drawing_runs import DrawResult, DrawTask, run_draw_task
from monkeydiagram.sources import VerifiedElevationSource

from ..errors import StudioError

#: How many drawings solve at once; 0 solves in the Runtime's own process.
WORKERS_ENV = "MONKEY_DRAWING_WORKERS"
# Verified sources kept for the cache to answer before a solve (``DrawingPool.verified``).
_KEPT_SOURCES = 64

_LOG = logging.getLogger(__name__)


def pool_size() -> int:
    """``MONKEY_DRAWING_WORKERS`` when set; otherwise half this machine's processors, from one to four."""

    configured = os.environ.get(WORKERS_ENV, "").strip()
    if configured:
        try:
            return max(0, int(configured))
        except ValueError:
            _LOG.warning("%s=%r is not a number; drawing workers follow the processors", WORKERS_ENV, configured)
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _initialize_worker() -> None:
    from monkeydiagram.documentation.styles import initialize_drawing_runtime

    initialize_drawing_runtime()


class DrawingPool:
    """Worker processes solving ``DrawTask``s; callable as the ``drawer`` the ``freeze_*`` functions take."""

    def __init__(self, workers: int) -> None:
        self.workers = workers
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None
        self._sources: OrderedDict[object, VerifiedElevationSource] = OrderedDict()

    def __call__(self, task: DrawTask) -> DrawResult:
        executor = self._running()
        try:
            result = executor.submit(run_draw_task, task).result()
        except BrokenProcessPool as exc:
            self._replace(executor)
            raise StudioError(503, "DRAWING_WORKER_LOST", "The drawing worker stopped before the drawing was done; "
                                                          "nothing was retained. Ask for the drawing again.") from exc
        self._keep(task.source, result.verified)
        return result

    def verified(self, source) -> VerifiedElevationSource | None:
        """The source as a worker verified it before (without its shapes), so a cache can answer for it."""

        try:
            with self._lock:
                found = self._sources.get(source)
                if found is not None:
                    self._sources.move_to_end(source)
                return found
        except TypeError:  # a source that cannot be a key is verified again each time
            return None

    def close(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _running(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers, mp_context=multiprocessing.get_context("spawn"),
                    initializer=_initialize_worker)
            return self._executor

    def _replace(self, broken: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._executor is broken:
                self._executor = None
        broken.shutdown(wait=False, cancel_futures=True)
        _LOG.warning("A drawing worker stopped; the next drawing starts a new pool")

    def _keep(self, source, verified: VerifiedElevationSource) -> None:
        try:
            with self._lock:
                self._sources[source] = verified
                self._sources.move_to_end(source)
                while len(self._sources) > _KEPT_SOURCES:
                    self._sources.popitem(last=False)
        except TypeError:
            pass


_pool: DrawingPool | None = None


def start(workers: int | None = None) -> DrawingPool | None:
    """Start this process's drawing workers; none when the size is 0. Workers start at the first drawing."""

    global _pool
    size = pool_size() if workers is None else workers
    if size > 0 and _pool is None:
        _pool = DrawingPool(size)
    return _pool


def drawer() -> DrawingPool | None:
    """This process's pool, or None when drawings solve in this process."""

    return _pool


def stop() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        pool.close()
