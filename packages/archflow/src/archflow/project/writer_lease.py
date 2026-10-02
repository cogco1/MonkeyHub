"""The project's writer lease (ADR-012): one process writes a project at a time.

While a project is open, its Project Runtime holds the project's writer lease
for as long as it runs (``hold_writer_lease``): an OS lock on ``writer.lock``
in the project folder, which the operating system ends with the process that
holds it. The file is never the lock. One left behind by a process that ended
holds nothing, and it is never removed: another process could lock a
replacement while one still held the old file.

Every write of the repository (``archflow.project.repository``) takes the
lease before it touches anything (``writing``). In a process that holds it,
that is a count. A process that does not hold it - a tool, a test, the Hub
creating a project nobody has open - takes it for the length of that one write
and gives it back. When another process holds it, the write waits up to
``WRITE_WAIT_S`` for a writer that may be about to finish, as the HEAD lock
does, and is then refused with ``ProjectWriterBusy`` (``PROJECT_WRITER_BUSY``),
having written nothing. Reads never take it and never wait for it.

A hold retries for a bounded time (``HOLD_WAIT_S``), as the index keeper does
for ``index.lock``: the holder may be a runtime of the same project that is
still exiting.

The repository's other locks keep their jobs inside the lease. The
process-wide thread lock, ``HEAD.lock`` and ``design/branches.lock`` still
order this process's threads, a read that takes them (``export_transfer``) and
a build older than the lease; the project trash takes the HEAD and design
locks as before. Every writer takes the lease first, then the thread lock,
then ``HEAD.lock``, then ``design/branches.lock``.

On Windows the lock is one byte far past the end of the empty file, not its
first byte: a byte-range lock refuses another process's read of the range it
covers, and a lock on byte 0 left the file unreadable to whatever copies the
project folder. POSIX ``flock`` locks the whole file and refuses no read.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Iterator

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised only on POSIX hosts
    import fcntl

from archflow.project.repository import WRITER_LOCK, ProjectWriterBusy, _open_lock_file, project_root_key

_LOG = logging.getLogger(__name__)

# How long a write in a process that does not hold the lease waits for another writer, as the HEAD lock does.
WRITE_WAIT_S = 10.0
# How long a hold waits for a holder that may be exiting: below the 30 s the Hub gives a runtime to start.
HOLD_WAIT_S = 20.0
# Retrying: the first wait and the longest one.
RETRY_FIRST_S = 0.05
RETRY_MAX_S = 2.0
# Where the Windows lock sits: far past the end of the empty file, and of any read of it.
_LOCKED_OFFSET = 1 << 31

_BUSY = ("Another process holds this project's writer lease (writer.lock): the project is open in a MonkeyHub "
         "Project Runtime, or another tool is writing it. Nothing was written; write through the runtime that "
         "has the project open.")


@dataclass(eq=False)
class _Lease:
    """This process's share of one project's lease: one OS lock, counted."""

    path: Path
    # Taken while the OS lock is taken or given back, and while a count moves.
    turn: threading.Lock = field(default_factory=threading.Lock)
    handle: BinaryIO | None = None
    holds: int = 0
    writes: int = 0


_GUARD = threading.Lock()
# Keyed by ``project_root_key``: every spelling of one root shares one lease.
_LEASES: dict[str, _Lease] = {}


def _lease_of(root: Path | str) -> _Lease:
    key = project_root_key(root)
    with _GUARD:
        lease = _LEASES.get(key)
        if lease is None:
            lease = _LEASES[key] = _Lease(Path(root) / WRITER_LOCK)
        return lease


def _try_lock(handle: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            handle.seek(_LOCKED_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised only on POSIX hosts
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            handle.seek(_LOCKED_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised only on POSIX hosts
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        _LOG.debug("the writer lease was given back by closing its file", exc_info=True)
    finally:
        handle.close()


def _take(lease: _Lease, wait_s: float, *, holding: bool) -> None:
    """Take the OS lock, retrying with a bounded back-off; the caller holds ``lease.turn``."""

    deadline = time.monotonic() + wait_s
    delay = RETRY_FIRST_S
    handle: BinaryIO | None = None
    unopened: OSError | None = None
    try:
        while True:
            if handle is None:
                try:
                    handle = _open_lock_file(lease.path)
                    unopened = None
                except PermissionError as exc:
                    if os.name != "nt":
                        raise
                    # A scanner or an indexer keeps the file for a moment: tried again below.
                    unopened = exc
            if handle is not None and _try_lock(handle):
                lease.handle, handle = handle, None
                return
            if time.monotonic() + delay > deadline:
                if unopened is not None:
                    raise unopened
                raise ProjectWriterBusy(_BUSY)
            if holding and delay == RETRY_FIRST_S:
                _LOG.info("another process holds the writer lease of %s; waiting up to %.0f s", lease.path.parent, wait_s)
            time.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)
    finally:
        if handle is not None:
            handle.close()


def _enter(root: Path | str, wait_s: float, *, holding: bool) -> _Lease:
    lease = _lease_of(root)
    with lease.turn:
        if lease.handle is None:
            _take(lease, wait_s, holding=holding)
        if holding:
            lease.holds += 1
        else:
            lease.writes += 1
    return lease


def _leave(lease: _Lease, *, holding: bool) -> None:
    with lease.turn:
        if holding:
            lease.holds -= 1
        else:
            lease.writes -= 1
        if lease.holds or lease.writes or lease.handle is None:
            return
        handle, lease.handle = lease.handle, None
        _unlock(handle)


class WriterLease:
    """One hold of a project's writer lease, given back by ``release`` (once) or at the end of a ``with``."""

    def __init__(self, lease: _Lease, root: Path) -> None:
        self.root = root
        self._lease = lease
        self._released = False
        self._guard = threading.Lock()

    def release(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
        _leave(self._lease, holding=True)

    def __enter__(self) -> WriterLease:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def hold_writer_lease(root: Path | str, *, wait_s: float | None = None) -> WriterLease:
    """Hold the project's writer lease until ``release``: the open project's Project Runtime does (ADR-012).

    Waits up to ``wait_s`` (``HOLD_WAIT_S``) for another process to give it
    up, since that may be a runtime of the same project that is exiting, and
    raises ``ProjectWriterBusy`` after. The holds of one process share one OS
    lock, which goes when the last of them is released or the process ends.
    """

    root = Path(root)
    return WriterLease(_enter(root, HOLD_WAIT_S if wait_s is None else wait_s, holding=True), root)


def holds_writer_lease(root: Path | str) -> bool:
    """Whether this process holds the project's writer lease right now, held or taken for a write."""

    with _GUARD:
        lease = _LEASES.get(project_root_key(root))
    return lease is not None and lease.handle is not None


@contextmanager
def writing(root: Path | str, *, wait_s: float | None = None) -> Iterator[None]:
    """The lease for the length of one write: counted where it is held, else taken and given back.

    Waits up to ``wait_s`` (``WRITE_WAIT_S``) for another process's write to
    end, then raises ``ProjectWriterBusy`` before anything was written.
    """

    lease = _enter(root, WRITE_WAIT_S if wait_s is None else wait_s, holding=False)
    try:
        yield
    finally:
        _leave(lease, holding=False)
