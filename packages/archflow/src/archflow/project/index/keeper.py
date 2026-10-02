"""The one writer of a project's index: a thread fed by the project's known layout and this process's writes.

No request ever projects, stats or writes for the index (ADR-008 phase 1b,
#365). ``IndexKeeper`` owns a thread that loads the index, then applies every
change as it arrives:

- this process's own P036 writes, named the moment they land by the
  repository's write observer (``add_write_observer``), only where they wrote;
- every reading of the project's layout as this process knows it
  (``archflow.project.watch.KnownLayout``): the walk at open, the re-check
  once a reading or a write has settled, and a refresh (ADR-012). Each
  ``LayoutSighting`` carries the fingerprint's lines, and the lines that moved
  since the last one say which runs to project again. Nothing watches the
  project: a change made outside this process reaches the index at the next
  open or refresh.

``index.lock`` may still be held by a process that is exiting (a restart
that overlaps it): the keeper retries taking it with a bounded back-off and
says so in ``status`` meanwhile; every reader reads the project itself until
the index has loaded.

After each commit that moved the revision, and after the first load, the
keeper tells the listeners registered for its project (``add_commit_listener``)
where the index stands and which domains moved: a hint for clients to read the
changes, never the changes themselves (#366). The projection queue's commits
(a projection done, ``projections``) are announced the same way, from the
queue's thread, the moment they land (#367); every announcement names the
domains of all commits since the last one.

A reader asks ``state`` (one attribute read) whether the index answers yet,
and ``readable`` whether it has applied every write this process made; it
then reads a snapshot (``ProjectIndex.snapshot``), which never waits for the
writer. ``IndexState.digest`` names the layout the rows were projected under,
so a caller can tell whether they are as new as a fingerprint it holds.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time
from typing import Callable

from archflow.project.repository import add_write_observer, project_path_key, write_serial
from archflow.project.watch import KnownLayout, LayoutSighting

from .store import IndexCommit, IndexLocked, IndexToken, IndexUnavailable, ProjectIndex, path_area

_LOG = logging.getLogger(__name__)

# How long ``stop`` waits for the thread to finish.
STOP_WAIT_S = 10.0
# Written paths kept between two applies before they stop being told apart:
# past it, the next apply projects every run again.
WRITTEN_LIMIT = 4096
# Taking ``index.lock`` from another writer: the first wait, the longest one,
# and how long the keeper keeps trying before it gives the index up.
LOCK_RETRY_FIRST_S = 0.1
LOCK_RETRY_MAX_S = 5.0
LOCK_RETRY_TOTAL_S = 120.0


_LISTENERS_LOCK = threading.Lock()
_LISTENERS: dict[str, list[Callable[[IndexCommit], None]]] = {}


def add_commit_listener(root: Path | str, listener: Callable[[IndexCommit], None]) -> Callable[[], None]:
    """Hear of every commit of the index of the project at ``root``; returns the removal.

    Called on the keeper's thread after the commit is readable, with where the
    index stands and the domains it moved (``reset`` for a rebuild or a first
    load). It must return at once: publish, never read or write the project.
    """

    key = project_path_key(root).rstrip(os.sep)
    with _LISTENERS_LOCK:
        _LISTENERS.setdefault(key, []).append(listener)

    def remove() -> None:
        with _LISTENERS_LOCK:
            listeners = _LISTENERS.get(key, [])
            if listener in listeners:
                listeners.remove(listener)
            if not listeners:
                _LISTENERS.pop(key, None)

    return remove


def _announce(root: str, commit: IndexCommit) -> None:
    with _LISTENERS_LOCK:
        listeners = tuple(_LISTENERS.get(project_path_key(root).rstrip(os.sep), ()))
    for listener in listeners:
        try:
            listener(commit)
        except Exception:  # noqa: BLE001 - a hint that failed to go out is not a failed commit
            _LOG.exception("an index commit listener failed")


@dataclass(frozen=True, slots=True)
class IndexState:
    """What the index answers for after one commit.

    ``serial`` is the ``write_serial`` of the project up to which this
    process's writes are in the rows; ``digest`` the layout fingerprint of the
    last sighting applied and ``generation`` that sighting's.
    """

    token: IndexToken
    serial: int
    digest: str
    generation: int


class IndexKeeper:
    """Keeps one ``ProjectIndex`` current on a thread of its own; the index's only writer.

    ``layout`` is the project's layout as this process knows it: the keeper
    listens to its readings and opens it if nobody has. The layout belongs to
    whoever made it (the runtime's binding), which closes it; the keeper only
    stops listening.
    """

    def __init__(self, index: ProjectIndex, layout: KnownLayout, *, name: str = "") -> None:
        self.index = index
        self._layout = layout
        self._root = layout.root
        self._prefix = layout.key if layout.key.endswith(os.sep) else layout.key + os.sep
        self._name = name or os.path.basename(self._root.rstrip("\\/"))
        self._changed = threading.Condition()
        self._sighting: LayoutSighting | None = None
        self._written: set[str] = set()
        self._written_all = False
        self._noted_serial = 0
        self._stopping = False
        self._state: IndexState | None = None
        self._loaded = threading.Event()
        self._announced: IndexToken | None = None
        self._thread: threading.Thread | None = None
        self._remove_observer: Callable[[], None] | None = None
        self._remove_listener: Callable[[], None] | None = None
        # One announcement at a time, from the keeper's thread or the projection queue's.
        self._announcing = threading.Lock()
        index.projection_listener = self._projections_committed
        # What the keeper has done, for tests and diagnostics.
        self.applies = 0
        self.failure: str | None = None
        # Why the keeper is waiting for ``index.lock``, while it is.
        self.waiting: str | None = None
        self.lock_attempts = 0

    # ---- readers

    @property
    def state(self) -> IndexState | None:
        """The last commit's state, or None while the index is loading or after it failed."""

        return self._state

    def readable(self) -> IndexState | None:
        """The state when the rows hold every write this process made below the root, else None."""

        state = self._state
        if state is None or state.serial < write_serial(self._root):
            return None
        return state

    def wait_readable(self, timeout: float) -> IndexState | None:
        """``readable``, waiting up to ``timeout`` for a loaded index to apply this process's writes.

        An index that is not loaded yet is not waited for: the caller reads
        the project itself, as it does whenever this answers None.
        """

        state = self.readable()
        if state is not None or self._state is None or timeout <= 0:
            return state
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                state = self.readable()
                remaining = deadline - time.monotonic()
                if state is not None or self._state is None or self._stopping or remaining <= 0:
                    return state
                self._changed.wait(remaining)

    def wait_loaded(self, timeout: float | None = None) -> IndexState | None:
        """Wait for the first load to end; the state, or None when the index is not used."""

        self._loaded.wait(timeout)
        return self._state

    def wait_seen(self, generation: int, timeout: float) -> IndexState | None:
        """Wait until the rows hold the layout reading ``generation``, or a later one: the state, else None.

        A refresh waits for this, so that its answer and every read after it
        see what it read. An index that is loading, failed or stopped, or that
        has not applied the reading within ``timeout``, answers None; readers
        then read the project itself, as always.
        """

        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                state = self._state
                if state is not None and state.generation >= generation:
                    return state
                remaining = deadline - time.monotonic()
                if self._stopping or remaining <= 0 or not self.running:
                    return None
                self._changed.wait(remaining)

    @property
    def status(self) -> str:
        """One line on what the index is doing: ready, loading, waiting for its lock, failed or stopped."""

        if self._state is not None:
            return "ready"
        if self.failure is not None:
            return f"failed: {self.failure}"
        if not self.running:
            return "stopped"
        waiting = self.waiting
        if waiting is not None:
            return f"waiting for index.lock ({waiting}; attempt {self.lock_attempts})"
        return "loading"

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ---- life

    def start(self) -> None:
        # The observer before the serial: a write in between is both counted
        # in the load's serial and noted, which only projects it twice.
        self._remove_observer = add_write_observer(self._root, self._wrote)
        with self._changed:
            self._noted_serial = write_serial(self._root)
        self._remove_listener = self._layout.add_listener(self._saw)
        self._thread = threading.Thread(target=self._run, name=f"project-index:{self._name}", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = True) -> bool:
        """End the thread, close the index and stop listening to the layout. True once it has ended."""

        with self._changed:
            self._stopping = True
            self._changed.notify_all()
        thread = self._thread
        ended = True
        if thread is not None and thread is not threading.current_thread() and wait:
            thread.join(STOP_WAIT_S)
            ended = not thread.is_alive()
        elif thread is not None:
            ended = not thread.is_alive()
        if thread is None:
            self._finish()
        return ended

    def _finish(self) -> None:
        self._state = None
        for remove in (self._remove_listener, self._remove_observer):
            if remove is not None:
                remove()
        self._remove_listener = self._remove_observer = None
        self.index.close()
        self._loaded.set()
        with self._changed:
            self._changed.notify_all()

    # ---- what changed

    def _wrote(self, path: Path) -> None:
        """The write observer: note where this process wrote, and nothing more."""

        location = project_path_key(path)
        if not location.startswith(self._prefix) and location != self._prefix.rstrip(os.sep):
            return
        relative = location[len(self._prefix):].replace(os.sep, "/")
        serial = write_serial(self._root)
        with self._changed:
            if len(self._written) >= WRITTEN_LIMIT:
                self._written_all = True
            else:
                self._written.add(relative)
            self._noted_serial = max(self._noted_serial, serial)
            self._changed.notify_all()

    def _saw(self, sighting: LayoutSighting) -> None:
        """The layout listener: keep the newest sighting, and nothing more."""

        with self._changed:
            earlier = self._sighting
            if earlier is None or sighting.layout.generation > earlier.layout.generation:
                self._sighting = sighting
                self._changed.notify_all()

    def _take(self, *, wait: bool) -> tuple[LayoutSighting | None, set[str], bool, int] | None:
        """What changed since the last apply; None once stopping."""

        with self._changed:
            while not self._stopping and wait and self._sighting is None and not self._written \
                    and not self._written_all:
                self._changed.wait()
            if self._stopping:
                return None
            sighting, self._sighting = self._sighting, None
            written, self._written = self._written, set()
            everything, self._written_all = self._written_all, False
            return sighting, written, everything, self._noted_serial

    # ---- the thread

    def _run(self) -> None:
        try:
            if not self._lock():
                return
            # The open reading - one walk, unless a reader took it already -
            # is where the load starts.
            self._layout.latest()
            first = self._first_sighting()
            if first is None:
                return
            taken = self._take(wait=False)
            if taken is None:
                return
            newer, written, everything, serial = taken
            # A sighting that came in meanwhile is newer: the load starts from it.
            first = newer or first
            self.index.load(first.lines, first.scanned_at_ns)
            if written or everything:
                self._apply(None, written, everything)
            self._publish(serial, first)
            self._loaded.set()
            _LOG.info("project index of %s %s at revision %s", self._name, self.index.loaded,
                      self.index.token.revision)
            last = first
            while True:
                taken = self._take(wait=True)
                if taken is None:
                    return
                sighting, written, everything, serial = taken
                try:
                    self._apply(sighting, written, everything)
                except IndexUnavailable:
                    raise
                except Exception:  # noqa: BLE001 - a failed apply is repaired by one rebuild
                    _LOG.exception("project index of %s failed to apply a change; rebuilding it", self._name)
                    self._state = None
                    self._rebuild(sighting or last)
                last = sighting or last
                self._publish(serial, last)
        except IndexUnavailable as exc:
            self.failure = str(exc)
            _LOG.warning("project index of %s is not used: %s", self._name, exc)
        except Exception as exc:  # noqa: BLE001 - the index is derived; P036 still answers
            self.failure = f"{type(exc).__name__}: {exc}"
            _LOG.exception("project index of %s failed; every reader reads the project itself", self._name)
        finally:
            self._finish()

    def _lock(self) -> bool:
        """Take ``index.lock``, retrying with a bounded back-off; False once stopping.

        Raises ``IndexLocked`` when another writer still holds it after
        ``LOCK_RETRY_TOTAL_S``. Meanwhile the layout's readings and the write
        observer keep noting what changes, so the load that follows misses
        nothing.
        """

        delay = LOCK_RETRY_FIRST_S
        deadline = time.monotonic() + LOCK_RETRY_TOTAL_S
        while True:
            try:
                self.index.lock()
            except IndexLocked as exc:
                if time.monotonic() + delay > deadline:
                    self.waiting = None
                    raise
                self.lock_attempts += 1
                if self.waiting is None:
                    _LOG.info("project index of %s waits for its lock: %s", self._name, exc)
                self.waiting = str(exc)
                # Other notices (a sighting, a write) wake the wait early; only stopping ends it.
                retry_at = time.monotonic() + delay
                with self._changed:
                    while not self._stopping and (remaining := retry_at - time.monotonic()) > 0:
                        self._changed.wait(remaining)
                    if self._stopping:
                        return False
                delay = min(delay * 2, LOCK_RETRY_MAX_S)
                continue
            self.waiting = None
            return True

    def _first_sighting(self) -> LayoutSighting | None:
        with self._changed:
            while not self._stopping and self._sighting is None:
                self._changed.wait()
            if self._stopping:
                return None
            sighting, self._sighting = self._sighting, None
            return sighting

    def _apply(self, sighting: LayoutSighting | None, written: set[str], everything: bool) -> None:
        areas = {path_area(relative) for relative in written}
        if everything:
            areas |= {"runs", "branches", *(f"run:{run_id}" for run_id in self.index.run_ids())}
        if sighting is None:
            self.index.apply(None, 0, areas=areas)
        else:
            self.index.apply(sighting.lines, sighting.scanned_at_ns, areas=areas)
        self.applies += 1

    def _rebuild(self, sighting: LayoutSighting) -> None:
        self.index.rebuild(sighting.lines, sighting.scanned_at_ns)

    def _publish(self, serial: int, sighting: LayoutSighting) -> None:
        with self._announcing:
            token = self.index.token
            if token is None:
                raise IndexUnavailable("the project index closed")
            with self._changed:
                self._state = IndexState(token, serial, sighting.layout.fingerprint.digest, sighting.layout.generation)
                self._changed.notify_all()
            self._announce_locked(token)

    def _projections_committed(self) -> None:
        """The projection queue committed (a projection is done): announce it now, whatever the keeper is doing."""

        with self._announcing:
            token = self.index.token
            if token is None or self._announced is None:
                return  # not loaded yet: the first load announces everything
            with self._changed:
                state = self._state
                if state is not None and state.token != token:
                    self._state = IndexState(token, state.serial, state.digest, state.generation)
                    self._changed.notify_all()
            self._announce_locked(token)

    def _announce_locked(self, token: IndexToken) -> None:
        if token == self._announced:
            return
        # The first load is announced whatever it found: a client that
        # asked while the index was loading was refused, and reads now.
        domains = self.index.take_committed()
        if self._announced is None or not domains:
            domains = frozenset({"reset"})
        self._announced = token
        _announce(self._root, IndexCommit(token, domains))
