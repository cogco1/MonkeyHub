"""A project's layout fingerprint, kept current off every request path (ADR-008, #363).

``layout_fingerprint`` walks the whole project: about 340 stats and listings
for a real one, 25 ms on a quiet disk and more than a second on a busy one. No
reader may wait for that. One ``LayoutWatch`` per project root and process
keeps the latest fingerprint instead, on a thread of its own, and a reader
takes it with one attribute read (``LayoutLease.latest``). Every holder of a
lease on a root shares that one watch; the last release stops it.

How the watch learns that something moved:

- On Windows it opens the root once - sharing read, write and delete, so the
  folder can still be renamed, moved or deleted while it is watched - and keeps
  one recursive ``ReadDirectoryChangesW`` pending on it, overlapped, so that
  stopping cancels it at once. Shortly after changes arrive (``DEBOUNCE_S``)
  it stats only the directories they name, and lists one again only when an
  entry in it came, went or was renamed, or its time moved. A lost batch (0
  bytes, ``ERROR_NOTIFY_ENUM_DIR``) or a watch that fails costs one full walk.
  The root itself is stat'ed every ``POLL_S``: a root renamed or deleted under
  the watch closes it rather than holding the folder.
- Without notifications - another platform, a root that will not open, a
  watch that failed and has not been opened again yet - it stats every
  directory it knows once per ``POLL_S`` and lists only those whose time moved.
- This process's own writes arrive through ``add_write_observer`` and are read
  like a notification, so they wait for none.
- Either way the whole project is walked every ``SAFETY_S``: a network share,
  a synced folder or a restore that sets old times can change a project
  without a notification or a new time.

The fingerprint is the one ``layout_fingerprint`` gives for the same tree: the
same lines (``LayoutFingerprint.from_lines``), the same traversal
(``is_layout_directory``: no link and no junction is walked into) and the same
racy rule - stable only when its newest time is more than
``FINGERPRINT_SETTLED_NS`` older than the pass that took it. A pass that leaves
it unstable schedules another once that time has passed, which stats again
only what was too young to trust. A notification alone does not make it
unstable: a file rewritten in place moves nothing the fingerprint states, and
must not keep a project from ever settling.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import queue
import struct
import sys
import threading
import time
from typing import Callable
import weakref

from archflow.project.layout import (
    FINGERPRINT_POINTER_FILES,
    FINGERPRINT_SETTLED_NS,
    LayoutFingerprint,
    is_layout_directory,
    pointer_file_lines,
)
from archflow.project.memo import settled
from archflow.project.repository import (
    add_write_observer,
    project_path_key,
    project_root_key,
    write_serial,
)

_LOG = logging.getLogger(__name__)

# How often the root is checked while notifications feed the watch, and how
# often every known directory is stat'ed when none do.
POLL_S = 1.0
# Quiet time after a change before the tree is read again; a steady stream of
# changes is still read at least every ``DEBOUNCE_MAX_S``.
DEBOUNCE_S = 0.075
DEBOUNCE_MAX_S = 0.25
# The whole project is walked this often whatever the notifications say.
SAFETY_S = 120.0
# How long a watch that failed or could not open waits before opening again.
RETRY_S = 10.0
# How long the first reader waits for the first walk before it reads an
# unstable placeholder instead.
FIRST_WAIT_S = 30.0
# How long ``stop`` waits for the thread to finish.
STOP_WAIT_S = 5.0
# One notification buffer; 64 KiB is the most a network path accepts.
NOTIFY_BUFFER_BYTES = 64 * 1024

_WINDOWS = sys.platform == "win32"
# Bound once: a test that patches the time module's clocks for its own code
# must not stop the watch's deadlines, or skew the times it compares.
_monotonic = time.monotonic
_time_ns = time.time_ns


@dataclass(frozen=True, slots=True)
class WatchedLayout:
    """The fingerprint a watch published last, and what it was taken under.

    ``serial`` is ``write_serial(root)`` read before the pass began: a reader
    whose serial is past it has written something this fingerprint may not
    show yet. ``generation`` counts the watch's publications. ``notified``
    says whether file-system notifications were feeding the watch.
    """

    fingerprint: LayoutFingerprint
    serial: int
    generation: int
    notified: bool


class _Stopped(Exception):
    """Raised inside a pass when the watch is told to stop."""


# ---- the tree as last seen


@dataclass(slots=True)
class _Directory:
    """What the watch knows of one directory: its time, and its child directories as last listed."""

    mtime_ns: int | None = None
    stat_error: int | None = None
    stat_at_ns: int = 0
    # The time the directory had when it was listed; None until it was.
    listed_mtime_ns: int | None = None
    listed_at_ns: int = 0
    children: tuple[str, ...] = ()
    list_error: int | None = None


def _child(relative: str, name: str) -> str:
    return f"{relative}/{name}" if relative else name


def _fold(relative: str) -> str:
    """A relative path spelled as the file system compares it, with ``os.sep`` between parts."""

    return os.path.normcase(relative.replace("/", os.sep))


_POINTER_PARTS = tuple(tuple(_fold(pointer).split(os.sep)) for pointer in FINGERPRINT_POINTER_FILES)


class _Tree:
    """The directories below one root as last seen, and how to bring any of them up to date.

    Metadata only, like ``layout_fingerprint``: it stats and lists directories
    and stats the pointer files, and it opens, reads and writes no file. Its
    lines are that walk's lines, so its fingerprint is the walk's fingerprint
    of the same tree.
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.directories: dict[str, _Directory] = {}
        # ``_fold(relative)`` -> relative, to find the directory a notification names.
        self.folded: dict[str, str] = {}
        self.root_missing = False
        self.pointers: tuple[tuple[str, ...], int, bool] = ((), 0, False)
        self.pointers_at_ns = 0
        self._visited: set[str] = set()

    def path(self, relative: str) -> str:
        return os.path.join(self.root, *relative.split("/")) if relative else self.root

    def walk(self, stopping: Callable[[], bool]) -> None:
        """Forget everything and read the whole tree again, as ``layout_fingerprint`` does."""

        self.directories.clear()
        self.folded.clear()
        self.root_missing = False
        self._visited = set()
        self._visit("", True, stopping)
        self.read_pointers()

    def refresh(self, dirty: dict[str, bool], stopping: Callable[[], bool]) -> None:
        """Stat each named directory again, parents first; list it again when it moved.

        ``dirty`` maps a directory to whether it must be listed whatever its
        time says (an entry in it came, went or was renamed). A directory that
        appeared below one is walked whole; one that went is forgotten with
        everything below it.
        """

        self._visited = set()
        for relative in sorted(dirty, key=lambda item: (item.count("/") if item else -1, item)):
            if relative and relative not in self.directories:
                # Forgotten with a parent that went, earlier in this pass.
                continue
            self._visit(relative, dirty[relative], stopping)

    def refresh_all(self, stopping: Callable[[], bool]) -> None:
        """Stat every known directory and the pointer files; list only what moved or was too young."""

        self.refresh(dict.fromkeys(self.directories or ("",), False), stopping)
        self.read_pointers()

    def young(self) -> dict[str, bool]:
        """The directories whose time, or listing, was too close to its reading to prove anything yet."""

        return {
            relative: False for relative, entry in self.directories.items()
            if entry.mtime_ns is not None and (
                not settled(entry.mtime_ns, entry.stat_at_ns)
                or (entry.listed_mtime_ns is not None and not settled(entry.listed_mtime_ns, entry.listed_at_ns))
            )
        }

    def pointers_young(self) -> bool:
        newest = self.pointers[1]
        return newest > 0 and not settled(newest, self.pointers_at_ns)

    def read_pointers(self) -> None:
        self.pointers_at_ns = _time_ns()
        self.pointers = pointer_file_lines(self.root)

    def deepest(self, parts: list[str]) -> tuple[str, bool]:
        """The deepest known directory at or above folded ``parts``, and whether it is ``parts`` itself."""

        for end in range(len(parts), 0, -1):
            found = self.folded.get(os.sep.join(parts[:end]))
            if found is not None:
                return found, end == len(parts)
        return "", not parts

    def fingerprint(self, scanned_at_ns: int) -> tuple[LayoutFingerprint, bool]:
        """The fingerprint of the tree as it stands, and whether anything in it was unreadable."""

        lines: list[str] = []
        newest = 0
        unreadable = False
        if self.root_missing:
            lines.append("d . missing")
            unreadable = True
        for relative, entry in self.directories.items():
            name = relative or "."
            if entry.mtime_ns is None:
                lines.append(f"d {name} unreadable {entry.stat_error}")
                unreadable = True
                continue
            lines.append(f"d {name} {entry.mtime_ns}")
            newest = max(newest, entry.mtime_ns)
            if entry.list_error is not None:
                lines.append(f"l {name} unreadable {entry.list_error}")
                unreadable = True
        pointer_lines, pointer_newest, pointer_unreadable = self.pointers
        lines.extend(pointer_lines)
        unreadable = unreadable or pointer_unreadable
        return LayoutFingerprint.from_lines(
            lines, newest_mtime_ns=max(newest, pointer_newest), scanned_at_ns=scanned_at_ns, unreadable=unreadable,
        ), unreadable

    # ---- one directory at a time

    def _visit(self, relative: str, force_list: bool, stopping: Callable[[], bool]) -> None:
        pending = [(relative, force_list)]
        while pending:
            if stopping():
                raise _Stopped
            current, force = pending.pop()
            if current in self._visited:
                continue
            self._visited.add(current)
            pending.extend((child, True) for child in self._visit_one(current, force))

    def _visit_one(self, relative: str, force_list: bool) -> list[str]:
        """Stat one directory, list it if it has to be; the new child directories to walk."""

        path = self.path(relative)
        stat_at = _time_ns()
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            if relative:
                # Removed; its parent's time says so.
                self._forget(relative)
            else:
                self.directories.clear()
                self.folded.clear()
                self.root_missing = True
            return []
        except OSError as exc:
            self._forget(relative)
            if not relative:
                self.directories.clear()
                self.folded.clear()
                self.root_missing = False
            self._keep(relative, _Directory(stat_error=exc.errno, stat_at_ns=stat_at))
            return []
        if not relative:
            self.root_missing = False
        entry = self.directories.get(relative)
        if entry is None or entry.mtime_ns is None:
            entry = self._keep(relative, _Directory())
        entry.mtime_ns, entry.stat_error, entry.stat_at_ns = mtime, None, stat_at
        if not (
            force_list
            or entry.list_error is not None
            or entry.listed_mtime_ns != mtime
            or not settled(entry.listed_mtime_ns, entry.listed_at_ns)
        ):
            return []
        listed_at = _time_ns()
        try:
            with os.scandir(path) as entries:
                names = sorted(item.name for item in entries if is_layout_directory(item))
        except FileNotFoundError:
            # Gone between the stat and the listing: named, with nothing below it.
            names = []
        except OSError as exc:
            for name in entry.children:
                self._forget(_child(relative, name))
            entry.children, entry.listed_mtime_ns, entry.list_error = (), None, exc.errno
            return []
        present = set(names)
        for name in entry.children:
            if name not in present:
                self._forget(_child(relative, name))
        entry.children, entry.listed_mtime_ns, entry.listed_at_ns, entry.list_error = (
            tuple(names), mtime, listed_at, None,
        )
        return [_child(relative, name) for name in names if _child(relative, name) not in self.directories]

    def _keep(self, relative: str, entry: _Directory) -> _Directory:
        self.directories[relative] = entry
        if relative:
            self.folded[_fold(relative)] = relative
        return entry

    def _forget(self, relative: str) -> None:
        """Forget a directory and everything known below it."""

        pending = [relative]
        while pending:
            current = pending.pop()
            entry = self.directories.pop(current, None)
            if current:
                self.folded.pop(_fold(current), None)
            if entry is not None:
                pending.extend(_child(current, name) for name in entry.children)


# ---- Windows: one handle, one pending read

if _WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _ReadDirectoryChangesW = _kernel32.ReadDirectoryChangesW
    _ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(_OVERLAPPED), ctypes.c_void_p]
    _ReadDirectoryChangesW.restype = wintypes.BOOL
    _GetOverlappedResult = _kernel32.GetOverlappedResult
    _GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                     ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _GetOverlappedResult.restype = wintypes.BOOL
    _CancelIoEx = _kernel32.CancelIoEx
    _CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _CancelIoEx.restype = wintypes.BOOL
    _CreateEventW = _kernel32.CreateEventW
    _CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _CreateEventW.restype = wintypes.HANDLE
    _SetEvent = _kernel32.SetEvent
    _SetEvent.argtypes = [wintypes.HANDLE]
    _SetEvent.restype = wintypes.BOOL
    _ResetEvent = _kernel32.ResetEvent
    _ResetEvent.argtypes = [wintypes.HANDLE]
    _ResetEvent.restype = wintypes.BOOL
    _WaitForMultipleObjects = _kernel32.WaitForMultipleObjects
    _WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL,
                                        wintypes.DWORD]
    _WaitForMultipleObjects.restype = wintypes.DWORD
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _FILE_LIST_DIRECTORY = 0x0001
    # Read, write and delete: the watched folder stays renamable and removable.
    _FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OVERLAPPED = 0x40000000
    # FILE_NAME | DIR_NAME | SIZE | LAST_WRITE.
    _NOTIFY_FILTER = 0x0001 | 0x0002 | 0x0008 | 0x0010
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _ERROR_NOTIFY_ENUM_DIR = 1022

_FILE_ACTION_MODIFIED = 3


class _Notifier:
    """One handle on the root and one overlapped ``ReadDirectoryChangesW`` pending on it."""

    def __init__(self, root: str, buffer_bytes: int) -> None:
        handle = _CreateFileW(root, _FILE_LIST_DIRECTORY, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                              _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OVERLAPPED, None)
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        event = _CreateEventW(None, True, False, None)
        if not event:
            error = ctypes.get_last_error()
            _CloseHandle(handle)
            raise ctypes.WinError(error)
        self.handle = handle
        self.event = event
        self._size = buffer_bytes - buffer_bytes % 4
        self._buffer = (wintypes.DWORD * (self._size // 4))()
        self._overlapped = _OVERLAPPED()
        self._overlapped.hEvent = event
        self.pending = False

    def arm(self) -> None:
        """Ask for the next batch of changes below the root."""

        _ResetEvent(self.event)
        if not _ReadDirectoryChangesW(self.handle, self._buffer, self._size, True, _NOTIFY_FILTER, None,
                                      ctypes.byref(self._overlapped), None):
            raise ctypes.WinError(ctypes.get_last_error())
        self.pending = True

    def collect(self) -> list[tuple[int, str]] | None:
        """The finished batch as (action, path relative to the root); None when it was lost."""

        self.pending = False
        transferred = wintypes.DWORD()
        if not _GetOverlappedResult(self.handle, ctypes.byref(self._overlapped), ctypes.byref(transferred), False):
            error = ctypes.get_last_error()
            if error == _ERROR_NOTIFY_ENUM_DIR:
                return None
            raise ctypes.WinError(error)
        if not transferred.value:
            # More changed than the buffer holds: the batch is lost.
            return None
        return _changes(bytes(memoryview(self._buffer).cast("B")[: transferred.value]))

    def close(self) -> None:
        """Cancel the pending read, wait for it to end and close the handle: nothing is left open."""

        if self.handle is None:
            return
        if self.pending:
            _CancelIoEx(self.handle, ctypes.byref(self._overlapped))
            transferred = wintypes.DWORD()
            _GetOverlappedResult(self.handle, ctypes.byref(self._overlapped), ctypes.byref(transferred), True)
            self.pending = False
        _CloseHandle(self.handle)
        _CloseHandle(self.event)
        self.handle = self.event = None


def _changes(raw: bytes) -> list[tuple[int, str]]:
    """``FILE_NOTIFY_INFORMATION`` records: (action, name relative to the watched root)."""

    found: list[tuple[int, str]] = []
    offset = 0
    while offset + 12 <= len(raw):
        following, action, length = struct.unpack_from("<III", raw, offset)
        found.append((action, raw[offset + 12: offset + 12 + length].decode("utf-16-le", "surrogatepass")))
        if not following:
            break
        offset += following
    return found


class _Signal:
    """What wakes the watch's thread. On Windows a Win32 event, so one wait also covers the pending read."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._closed = False
        # Chosen once: a signal never changes kind under a thread waiting on it.
        self._win32 = _WINDOWS
        if self._win32:
            self.handle = _CreateEventW(None, False, False, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self._event = threading.Event()

    def set(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._win32:
                _SetEvent(self.handle)
            else:
                self._event.set()

    def wait(self, timeout_s: float, io_event=None) -> str:
        """Wait for ``set``, the read behind ``io_event`` or the timeout: "signal", "io" or "timeout"."""

        timeout_s = max(0.0, timeout_s)
        if not self._win32:
            fired = self._event.wait(timeout_s)
            self._event.clear()
            return "signal" if fired else "timeout"
        milliseconds = min(math.ceil(timeout_s * 1000), 0x7FFFFFFE)
        handles = (wintypes.HANDLE * 2)(self.handle, io_event) if io_event else (wintypes.HANDLE * 1)(self.handle)
        result = _WaitForMultipleObjects(len(handles), handles, False, milliseconds)
        if result == _WAIT_OBJECT_0:
            return "signal"
        if result == _WAIT_OBJECT_0 + 1:
            return "io"
        if result == _WAIT_TIMEOUT:
            return "timeout"
        raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._win32:
                _CloseHandle(self.handle)


# ---- the watch


class LayoutWatch:
    """The fingerprint of one project root, kept current on a thread of its own.

    Made and shared through ``watch_layout``; nobody constructs one to read.
    """

    def __init__(self, root: Path | str, key: str, *, notify: bool | None = None) -> None:
        self.root = os.fspath(root)
        self.key = key
        self.leases = 0
        # Notifications where the platform has them, unless a caller says not to.
        self.notify = _WINDOWS if notify is None else (notify and _WINDOWS)
        self._tree = _Tree(self.root)
        self._signal = _Signal()
        self._lock = threading.Lock()
        self._touched: list[str] = []
        self._stopping = False
        self._published_changed = threading.Condition()
        self._published: WatchedLayout | None = None
        self._generation = 0
        self._sync_wanted = 0
        self._sync_done = 0
        self._first = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped = False
        # Kept by the watch's own thread only.
        self._notifier: _Notifier | None = None
        self._identity: tuple[int, int] | None = None
        self._dirty: dict[str, bool] = {}
        self._pointers_dirty = False
        self._dirty_since: float | None = None
        self._debounce_at = 0.0
        self._full = True
        self._poll_at = 0.0
        self._safety_at = 0.0
        self._settle_at: float | None = None
        self._root_check_at = 0.0
        self._retry_at = 0.0
        # Attempts to watch the root refused in a row, and passes failed in a
        # row, so the log says each once rather than every second.
        self._refusals = 0
        self._failing = 0
        # What the watch has done, for tests and diagnostics: pass kinds and counts.
        self.passes: dict[str, int] = {"walk": 0, "poll": 0, "dirty": 0, "settle": 0}
        self.failures = 0
        self._prefix = key if key.endswith(os.sep) else key + os.sep

    # ---- readers

    def latest(self, *, wait: bool = True) -> WatchedLayout | None:
        """The fingerprint published last: one attribute read.

        Only before the first walk has published is there anything to wait
        for; ``wait=False`` answers None then, and a wait that runs out reads
        an unstable placeholder rather than holding a request.
        """

        published = self._published
        if published is not None or not wait:
            return published
        self._first.wait(FIRST_WAIT_S)
        published = self._published
        if published is not None:
            return published
        return WatchedLayout(LayoutFingerprint("pending", 0, _time_ns(), False), -1, 0, False)

    def sync(self, timeout: float = 30.0) -> WatchedLayout:
        """Walk the whole project now, on the watch's thread, and return what that walk published.

        For a caller that must not act on a layout older than now: a test,
        or a tool that just changed the project behind the watch's back.
        """

        with self._published_changed:
            self._sync_wanted += 1
            ticket = self._sync_wanted
        self._signal.set()
        deadline = _monotonic() + timeout
        with self._published_changed:
            while self._sync_done < ticket and not self._stopped:
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"the layout watch of {self.root} did not walk it within {timeout} s")
                self._published_changed.wait(remaining)
            return self._published  # type: ignore[return-value]

    @property
    def notified(self) -> bool:
        """Whether file-system notifications feed the watch right now."""

        return self._notifier is not None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def touched(self, path: Path | str) -> None:
        """A write this process made below the root (``add_write_observer``); read it like a notification.

        Runs on the writer's thread while it holds its locks: it only queues
        the path and wakes the watch.
        """

        with self._lock:
            if self._stopping:
                return
            self._touched.append(os.fspath(path))
        self._signal.set()

    # ---- life

    def start(self) -> None:
        name = os.path.basename(self.root.rstrip("\\/")) or self.root
        self._thread = threading.Thread(target=self._run, name=f"layout-watch:{name}", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = True) -> bool:
        """End the thread: cancel the pending read and close the handle. True once it has ended."""

        with self._lock:
            self._stopping = True
        self._signal.set()
        thread = self._thread
        if thread is None:
            return True
        if wait and thread is not threading.current_thread():
            thread.join(STOP_WAIT_S)
        return not thread.is_alive()

    def _is_stopping(self) -> bool:
        return self._stopping

    def _run(self) -> None:
        remove_observer = add_write_observer(self.root, self.touched)
        try:
            while not self._stopping:
                try:
                    self._watch()
                except _Stopped:
                    break
                except Exception:  # noqa: BLE001 - the watch must outlive any one failure
                    if self._stopping:
                        break
                    self.failures += 1
                    self._failing += 1
                    # The first failure in a row is reported whole; repeats are
                    # only counted, and each waits twice as long, up to a minute.
                    _LOG.log(logging.ERROR if self._failing == 1 else logging.DEBUG,
                             "layout watch of %s failed; walking the project instead", self.root, exc_info=True)
                    self._close_notifier()
                    self._full = True
                    self._retry_at = _monotonic() + RETRY_S
                    # Until a walk succeeds again, nothing may be kept under the last one.
                    self._publish_unsettled()
                    self._signal.wait(min(POLL_S * 2 ** (self._failing - 1), 60.0))
        finally:
            if not self._stopping:
                # Ended without being told to: nobody keeps it current any
                # more, so nothing may be kept or answered 304 under it.
                _LOG.error("layout watch of %s ended unexpectedly", self.root)
                self._publish_unsettled()
            remove_observer()
            self._close_notifier()
            self._signal.close()
            with self._published_changed:
                self._stopped = True
                self._published_changed.notify_all()
            self._first.set()

    def _watch(self) -> None:
        while not self._stopping:
            _release_collected()
            now = _monotonic()
            if self._notifier is None and self.notify and now >= self._retry_at and self._open_notifier():
                # Changes before the read was armed were never reported.
                self._full = True
            if self._notifier is not None and now >= self._root_check_at:
                self._check_root(now)
            self._take_touched(now)
            kind = self._due(now)
            if kind is not None:
                self._pass(kind)
                continue
            notifier = self._notifier
            woke = self._signal.wait(self._next_deadline(now) - now, notifier.event if notifier else None)
            if woke == "io" and self._notifier is notifier and notifier is not None:
                self._collect()

    def _due(self, now: float) -> str | None:
        with self._published_changed:
            wanted = self._sync_wanted > self._sync_done
        if self._full or wanted or now >= self._safety_at:
            return "walk"
        if self._notifier is None and now >= self._poll_at:
            return "poll"
        if (self._dirty or self._pointers_dirty) and now >= self._debounce_at:
            return "dirty"
        if self._settle_at is not None and now >= self._settle_at:
            return "settle"
        return None

    def _next_deadline(self, now: float) -> float:
        deadlines = [self._safety_at]
        if self._notifier is None:
            deadlines.append(self._poll_at)
            if self.notify:
                deadlines.append(self._retry_at)
        else:
            deadlines.append(self._root_check_at)
        if self._dirty or self._pointers_dirty:
            deadlines.append(self._debounce_at)
        if self._settle_at is not None:
            deadlines.append(self._settle_at)
        return max(now, min(deadlines))

    # ---- passes

    def _pass(self, kind: str) -> None:
        started = _monotonic()
        # Before anything is read: a write racing this pass then moves the
        # serial past the one its fingerprint is filed under.
        serial = write_serial(self.root)
        with self._published_changed:
            ticket = self._sync_wanted
        scanned_at_ns = _time_ns()
        tree = self._tree
        if kind == "walk":
            self._full = False
            self._clear_dirty()
            tree.walk(self._is_stopping)
            self._safety_at = _monotonic() + SAFETY_S
        elif kind == "poll":
            self._clear_dirty()
            tree.refresh_all(self._is_stopping)
        elif kind == "dirty":
            dirty, pointers = self._dirty, self._pointers_dirty
            self._clear_dirty()
            tree.refresh(dirty, self._is_stopping)
            if pointers:
                tree.read_pointers()
        else:
            tree.refresh(tree.young(), self._is_stopping)
            if tree.pointers_young():
                tree.read_pointers()
        fingerprint, unreadable = tree.fingerprint(scanned_at_ns)
        self.passes[kind] += 1
        self._failing = 0
        self._publish(fingerprint, serial, ticket if kind == "walk" else None)
        finished = _monotonic()
        if self._notifier is None:
            self._poll_at = max(started + POLL_S, finished + POLL_S / 4)
        if fingerprint.stable or unreadable:
            self._settle_at = None
        else:
            # Stable once its newest time is old enough; read again only then.
            wait_ns = fingerprint.newest_mtime_ns + FINGERPRINT_SETTLED_NS - _time_ns()
            self._settle_at = finished + min(max(wait_ns / 1e9, 0.0) + 0.05, SAFETY_S)

    def _clear_dirty(self) -> None:
        self._dirty = {}
        self._pointers_dirty = False
        self._dirty_since = None

    def _publish(self, fingerprint: LayoutFingerprint, serial: int, ticket: int | None) -> None:
        with self._published_changed:
            self._generation += 1
            self._published = WatchedLayout(fingerprint, serial, self._generation, self._notifier is not None)
            if ticket is not None:
                self._sync_done = max(self._sync_done, ticket)
            self._published_changed.notify_all()
        self._first.set()

    def _publish_unsettled(self) -> None:
        """Say the last fingerprint can no longer be trusted, keeping its digest.

        Before any walk succeeded there is none: readers waiting for the first
        one get an unstable placeholder instead, and read uncached until a
        walk succeeds.
        """

        with self._published_changed:
            last = self._published
            if last is not None and not last.fingerprint.stable:
                return
            self._generation += 1
            if last is None:
                unsettled = LayoutFingerprint("unwalked", 0, _time_ns(), False)
                self._published = WatchedLayout(unsettled, -1, self._generation, False)
            else:
                unsettled = LayoutFingerprint(last.fingerprint.digest, last.fingerprint.newest_mtime_ns,
                                              _time_ns(), False)
                self._published = WatchedLayout(unsettled, last.serial, self._generation, False)
            self._published_changed.notify_all()
        self._first.set()

    # ---- what moved

    def _mark(self, relative: str, force_list: bool, now: float) -> None:
        self._dirty[relative] = self._dirty.get(relative, False) or force_list
        self._arm_debounce(now)

    def _arm_debounce(self, now: float) -> None:
        if self._dirty_since is None:
            self._dirty_since = now
        self._debounce_at = min(self._dirty_since + DEBOUNCE_MAX_S, now + DEBOUNCE_S)

    def _note(self, folded: str, structural: bool, now: float) -> None:
        """One changed path, folded and relative to the root: mark what has to be read again.

        An entry that came, went or was renamed has its directory listed
        again; a directory that changed is stat'ed; a file that changed has its
        directory stat'ed. A path below nothing known marks the deepest known
        directory above it, to be listed. A pointer file, or a directory above
        one, is stat'ed again.
        """

        parts = [part for part in folded.split(os.sep) if part]
        if not parts:
            self._mark("", False, now)
            return
        if any(pointer[: len(parts)] == tuple(parts) for pointer in _POINTER_PARTS):
            self._pointers_dirty = True
            self._arm_debounce(now)
        own = self._tree.folded.get(os.sep.join(parts))
        parent, exact = self._tree.deepest(parts[:-1])
        if structural:
            self._mark(parent, True, now)
            if own is not None:
                self._mark(own, False, now)
        elif own is not None:
            self._mark(own, False, now)
        else:
            self._mark(parent, not exact, now)

    def _take_touched(self, now: float) -> None:
        with self._lock:
            touched, self._touched = self._touched, []
        for path in touched:
            location = project_path_key(path)
            if location == self.key:
                self._note("", True, now)
            elif location.startswith(self._prefix):
                self._note(location[len(self._prefix):], True, now)

    # ---- notifications

    def _open_notifier(self) -> bool:
        try:
            notifier = _Notifier(self.root, NOTIFY_BUFFER_BYTES)
        except OSError as exc:
            self._unwatchable(f"cannot open the root ({exc})")
            return False
        try:
            stat = os.stat(self.root)
            notifier.arm()
        except OSError as exc:
            notifier.close()
            self._unwatchable(f"cannot watch the root ({exc})")
            return False
        if self._refusals:
            _LOG.info("layout watch of %s is watching the root again", self.root)
        self._refusals = 0
        self._notifier = notifier
        self._identity = (stat.st_dev, stat.st_ino)
        self._root_check_at = _monotonic() + POLL_S
        return True

    def _unwatchable(self, reason: str) -> None:
        """The root would not be watched: scan until ``RETRY_S`` has passed, and say so once."""

        self._retry_at = _monotonic() + RETRY_S
        self._refusals += 1
        _LOG.log(logging.INFO if self._refusals == 1 else logging.DEBUG,
                 "layout watch of %s %s; scanning every %s s", self.root, reason, POLL_S)

    def _close_notifier(self) -> None:
        notifier, self._notifier = self._notifier, None
        if notifier is not None:
            notifier.close()

    def _lose_notifier(self, reason: str) -> None:
        _LOG.info("layout watch of %s: %s; walking it and scanning every %s s", self.root, reason, POLL_S)
        self._close_notifier()
        self._full = True
        self._retry_at = _monotonic() + RETRY_S

    def _collect(self) -> None:
        notifier = self._notifier
        assert notifier is not None
        try:
            changes = notifier.collect()
            notifier.arm()
        except OSError as exc:
            # A root deleted or renamed away refuses the next read.
            self._lose_notifier(f"the watch ended ({exc})")
            return
        if changes is None:
            # Lost changes: nothing short of a walk says what moved.
            self._full = True
            return
        now = _monotonic()
        for action, name in changes:
            self._note(os.path.normcase(name), action != _FILE_ACTION_MODIFIED, now)

    def _check_root(self, now: float) -> None:
        """The root is still the directory the handle is on; its own time moved without a notification."""

        self._root_check_at = now + POLL_S
        try:
            stat = os.stat(self.root)
        except OSError:
            stat = None
        if stat is None or (stat.st_dev, stat.st_ino) != self._identity:
            # Renamed, moved or deleted: let go of the folder and walk what is there now.
            self._lose_notifier("the root is gone or replaced")
            return
        known = self._tree.directories.get("")
        if known is None or known.mtime_ns != stat.st_mtime_ns:
            self._mark("", False, now)


# ---- one watch per root and process


_REGISTRY_GUARD = threading.RLock()
_WATCHES: dict[str, LayoutWatch] = {}
# Leases whose holder was collected without releasing them (``release_when_collected``).
_COLLECTED: queue.SimpleQueue = queue.SimpleQueue()


class LayoutLease:
    """One holder's share of a root's watch. ``release`` once; the last release stops the watch."""

    def __init__(self, watch: LayoutWatch) -> None:
        self.watch = watch
        self._released = False
        self._lock = threading.Lock()

    def latest(self, *, wait: bool = True) -> WatchedLayout | None:
        return self.watch.latest(wait=wait)

    def sync(self, timeout: float = 30.0) -> WatchedLayout:
        return self.watch.sync(timeout)

    @property
    def released(self) -> bool:
        return self._released

    def release(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        with _REGISTRY_GUARD:
            self.watch.leases -= 1
            last = self.watch.leases == 0
            if last and _WATCHES.get(self.watch.key) is self.watch:
                del _WATCHES[self.watch.key]
        if last:
            self.watch.stop(wait=wait)


def watch_layout(root: Path | str, *, notify: bool | None = None) -> LayoutLease:
    """A lease on the one watch of ``root`` in this process, started if none is running.

    ``notify=False`` asks a new watch to scan instead of listening for
    notifications; a watch already running keeps what it does.
    """

    _release_collected()
    key = project_root_key(root)
    with _REGISTRY_GUARD:
        watch = _WATCHES.get(key)
        if watch is None:
            watch = LayoutWatch(root, key, notify=notify)
            _WATCHES[key] = watch
            watch.start()
        watch.leases += 1
        return LayoutLease(watch)


def running_watches() -> dict[str, LayoutWatch]:
    """Every running watch by root key: what this process holds open."""

    with _REGISTRY_GUARD:
        return dict(_WATCHES)


def release_when_collected(holder: object, lease: LayoutLease) -> weakref.finalize:
    """Release ``lease`` once ``holder`` is garbage, for a holder that may never be closed.

    The finalizer only queues the lease: it can run on any thread at any
    point, holding any lock. The queue is emptied by the next lease taken and
    by every watch's thread.
    """

    finalizer = weakref.finalize(holder, _COLLECTED.put, lease)
    finalizer.atexit = False
    return finalizer


def _release_collected() -> None:
    while True:
        try:
            lease = _COLLECTED.get_nowait()
        except queue.Empty:
            return
        lease.release(wait=False)
