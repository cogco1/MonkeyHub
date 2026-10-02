"""A project's layout as this process last read it; nothing watches the project (ADR-012, #599).

``layout_fingerprint`` walks the whole project: about 340 stats and listings
for a real one, 25 ms on a quiet disk and more than a second on a busy one. A
``KnownLayout`` keeps the fingerprint that walk gives, and its lines, so a
reader takes it with one attribute read (``latest``). It holds no thread and
no handle on the folder, and it moves at exactly three points:

- **open**: the first reader walks the whole project once (``open``);
- **this process's writes**: the repository's write observer
  (``add_write_observer``) names each path this process wrote. Nothing is read
  on the writer's thread: the path is queued, and the re-check below reads it;
- **refresh**: someone asks to read the project again (``refresh``). This
  process's own writes are taken in first, then the whole project is walked
  once, and every place that moved besides is reported as moved outside this
  process (``LayoutRefresh``).

The racy rule is the fingerprint's own: a reading is stable only when its
newest time is more than ``FINGERPRINT_SETTLED_NS`` older than the reading. A
reading that is not, and every write queued since the last reading, gets one
re-check on a one-shot timer once that time has passed. The re-check stats
only the directories that were written or too young to trust, lists one again
only when its time moved, and publishes. A runtime creates ``writer.lock`` as
it opens a project (ADR-012), which moves the root's time: that open reading
settles the same way. A reading that could only settle more than
``SETTLE_LONGEST_S`` from now (a time in the future) schedules nothing: no
reading is ever repeated on a clock.

A change made by anyone else - a hand edit, a sync, a restore, an agent's
command - is therefore read at open and on refresh, not by itself (ADR-012's
known limit), unless it lands in a directory this process wrote, or one too
young to trust, before that directory's re-check.

Whoever keeps something derived from the tree beside the digest - the project
index (``archflow.project.index``) - asks for every publication with
``add_listener``: a ``LayoutSighting`` carries the fingerprint's own lines, so
the listener can tell which directories moved.

The fingerprint is the one ``layout_fingerprint`` gives for the same tree: the
same lines (``LayoutFingerprint.from_lines``), the same traversal
(``is_layout_directory``: no link and no junction is walked into) and the same
racy rule.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time

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

# A re-check runs this long after the newest time it waits for has settled:
# file-system clocks tick coarser than writes.
SETTLE_MARGIN_S = 0.05
# A reading that could only settle later than this (a time in the future)
# schedules no re-check; the next reading may settle it.
SETTLE_LONGEST_S = 120.0
# Paths this process wrote, kept between two readings before they stop being
# told apart: past it, the next reading walks the whole project.
TOUCHED_LIMIT = 4096

# Bound once: a test that patches the time module's clocks for its own code
# must not stop the re-check's deadlines, or skew the times it compares.
_monotonic = time.monotonic
_time_ns = time.time_ns


@dataclass(frozen=True, slots=True)
class LayoutReading:
    """One published reading of a project's layout.

    ``serial`` is ``write_serial(root)`` read before the reading began: a
    reader whose serial is past it has written something this fingerprint may
    not show yet. ``generation`` counts the publications of one
    ``KnownLayout``.
    """

    fingerprint: LayoutFingerprint
    serial: int
    generation: int


@dataclass(frozen=True, slots=True)
class LayoutSighting:
    """One publication, as a listener (``KnownLayout.add_listener``) is told it.

    ``lines`` are the fingerprint's own lines (``LayoutFingerprint.from_lines``):
    one per directory, per unreadable listing and per pointer file, so two
    sightings differ in exactly the lines of what moved between them.
    """

    layout: LayoutReading
    lines: tuple[str, ...]
    scanned_at_ns: int


@dataclass(frozen=True, slots=True)
class LayoutRefresh:
    """What reading the project again found (``KnownLayout.refresh``).

    ``moved`` names each directory or pointer file whose line moved although
    this process wrote nothing there, as a project-relative POSIX path ("."
    for the project folder itself): the folder changed outside this process.
    """

    layout: LayoutReading
    moved: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.moved)


class _Stopped(Exception):
    """Raised inside a reading when its layout is closed."""


# ---- the tree as last seen


@dataclass(slots=True)
class _Directory:
    """What the layout knows of one directory: its time, and its child directories as last listed."""

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
        # ``_fold(relative)`` -> relative, to find the directory a written path names.
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

    def refresh(self, dirty: Mapping[str, bool], stopping: Callable[[], bool]) -> None:
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

    def places(self) -> tuple[dict[str, str], int, bool]:
        """Each line of the tree as it stands by its place (kind and name), the newest time, and whether anything was unreadable."""

        places: dict[str, str] = {}
        newest = 0
        unreadable = False
        if self.root_missing:
            places["d ."] = "d . missing"
            unreadable = True
        for relative, entry in self.directories.items():
            name = relative or "."
            if entry.mtime_ns is None:
                places[f"d {name}"] = f"d {name} unreadable {entry.stat_error}"
                unreadable = True
                continue
            places[f"d {name}"] = f"d {name} {entry.mtime_ns}"
            newest = max(newest, entry.mtime_ns)
            if entry.list_error is not None:
                places[f"l {name}"] = f"l {name} unreadable {entry.list_error}"
                unreadable = True
        pointer_lines, pointer_newest, pointer_unreadable = self.pointers
        for pointer, line in zip(FINGERPRINT_POINTER_FILES, pointer_lines):
            places[f"f {pointer}"] = line
        return places, max(newest, pointer_newest), unreadable or pointer_unreadable

    def fingerprint(self, scanned_at_ns: int) -> tuple[LayoutFingerprint, bool, tuple[str, ...]]:
        """The fingerprint of the tree as it stands, whether anything in it was unreadable, and its lines."""

        places, newest, unreadable = self.places()
        lines = tuple(places.values())
        return LayoutFingerprint.from_lines(
            lines, newest_mtime_ns=newest, scanned_at_ns=scanned_at_ns, unreadable=unreadable,
        ), unreadable, lines

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


def _moved(before: Mapping[str, str], after: Mapping[str, str], own: Collection[str]) -> tuple[str, ...]:
    """The places whose line differs, by name, less any place a path in ``own`` can have moved.

    ``own`` holds folded paths this process wrote while the walk ran: each can
    have moved its own line, every line below it and the line of every
    directory above it, so none of those is said to have moved outside.
    """

    names = sorted({place.partition(" ")[2] for place in before.keys() | after.keys()
                    if before.get(place) != after.get(place)})
    if not own:
        return tuple(names)

    def related(name: str) -> bool:
        if name == ".":
            return True
        folded = _fold(name)
        return any(folded == path or folded.startswith(path + os.sep) or path.startswith(folded + os.sep)
                   or not path for path in own)

    return tuple(name for name in names if not related(name))


# ---- the layout


class KnownLayout:
    """The layout of one project root as this process last read it: no thread, no handle (ADR-012).

    Made by whoever answers for the project in this process - the runtime's
    binding (``project_runtime.binding``) - and closed by it. Readers take
    ``latest``; the first one opens it (one walk).
    """

    def __init__(self, root: Path | str) -> None:
        self.root = os.fspath(root)
        self.key = project_root_key(root)
        self.name = os.path.basename(self.root.rstrip("\\/")) or self.root
        self._prefix = self.key if self.key.endswith(os.sep) else self.key + os.sep
        self._tree = _Tree(self.root)
        # One reading at a time: the open walk, a re-check, a refresh.
        self._reading = threading.Lock()
        # Publications, in order, and the listeners told each of them.
        self._publishing = threading.Lock()
        self._published: LayoutReading | None = None
        self._sighting: LayoutSighting | None = None
        self._generation = 0
        self._listeners: tuple[Callable[[LayoutSighting], object], ...] = ()
        # What this process wrote below the root since the last reading
        # (folded, relative to the root), the one-shot re-check and when it is
        # due (monotonic), and the write observer. Kept under ``_lock``, which
        # a writer's thread takes and never waits long for.
        self._lock = threading.Lock()
        self._touched: set[str] = set()
        self._touched_all = False
        self._recheck_at: float | None = None
        self._timer: threading.Timer | None = None
        self._remove_observer: Callable[[], None] | None = None
        self._closed = False
        # Until a reading succeeds, the next one walks the whole project.
        self._full = True
        # What it has read, for tests and diagnostics.
        self.readings: dict[str, int] = {"open": 0, "recheck": 0, "refresh": 0}

    # ---- readers

    def latest(self, *, wait: bool = True) -> LayoutReading | None:
        """The reading published last: one attribute read.

        Before the first reading there is none: ``wait=False`` answers None,
        and ``wait=True`` opens the layout (one walk) and answers that.
        """

        published = self._published
        if published is not None or not wait:
            return published
        return self.open()

    def open(self) -> LayoutReading:
        """Walk the whole project once, unless a reading was published already; the reading published last."""

        with self._reading:
            return self._opened()

    def refresh(self) -> LayoutRefresh:
        """Read the project again now: this process's writes first, then one walk of the whole project.

        Every place whose line moved besides was changed outside this process
        and is named in the answer. A layout that was never read is opened
        instead, and nothing is said to have moved.
        """

        with self._reading:
            if self._published is None:
                return LayoutRefresh(self._opened(), ())
            self._start()
            return self._read_again()

    def _opened(self) -> LayoutReading:
        """The reading published last, walking the whole project first if there is none (under ``_reading``)."""

        if self._published is None:
            self._start()
            self._read("open")
        published = self._published
        if published is None:
            # Closed while it walked.
            raise RuntimeError(f"the layout of {self.root} is closed")
        return published

    @property
    def pending(self) -> bool:
        """Whether a re-check is scheduled."""

        return self._timer is not None

    def add_listener(self, callback: Callable[[LayoutSighting], object]) -> Callable[[], None]:
        """Tell ``callback`` every publication from now on, and the last one at once if there is one.

        It runs on whichever thread published - a reader's, a refresh's or the
        re-check's - after the publication is visible to readers, so it must be
        quick: queue and return. One that raises is logged and never stops a
        reading. Returns the function that removes it.
        """

        with self._publishing:
            self._listeners = (*self._listeners, callback)
            sighting = self._sighting
            if sighting is not None:
                self._tell((callback,), sighting)

        def remove() -> None:
            with self._publishing:
                self._listeners = tuple(item for item in self._listeners if item is not callback)

        return remove

    def close(self) -> None:
        """Cancel a pending re-check and stop hearing this process's writes; a reading under way stops."""

        with self._lock:
            self._closed = True
            self._touched.clear()
            self._touched_all = False
            timer, self._timer = self._timer, None
            self._recheck_at = None
            remove, self._remove_observer = self._remove_observer, None
        if timer is not None:
            timer.cancel()
        if remove is not None:
            remove()

    # ---- this process's writes

    def _start(self) -> None:
        """Hear this process's writes from now on (before any walk, so none is missed)."""

        with self._lock:
            if self._closed:
                raise RuntimeError(f"the layout of {self.root} is closed")
            if self._remove_observer is None:
                self._remove_observer = add_write_observer(self.root, self._wrote)

    def _wrote(self, path: Path) -> None:
        """The write observer: queue the path and ask for a re-check once it has settled.

        Runs on the writer's thread while it holds its locks: nothing is read here.
        """

        location = project_path_key(path)
        if location == self.key:
            relative = ""
        elif location.startswith(self._prefix):
            relative = location[len(self._prefix):]
        else:
            return
        with self._lock:
            if self._closed:
                return
            if len(self._touched) >= TOUCHED_LIMIT:
                self._touched_all = True
            else:
                self._touched.add(relative)
        self._recheck_in(FINGERPRINT_SETTLED_NS / 1e9 + SETTLE_MARGIN_S)

    def _take_touched(self) -> tuple[set[str], bool]:
        with self._lock:
            touched, self._touched = self._touched, set()
            everything, self._touched_all = self._touched_all, False
        return touched, everything

    def _dirty(self, touched: Iterable[str]) -> tuple[dict[str, bool], bool]:
        """What the written paths make the tree read again, and whether a pointer file is among them.

        A written path's directory is listed again (an entry in it came, went
        or was renamed), and the path itself is stat'ed when it is a directory
        the tree knows. A path below nothing known lists the deepest known
        directory above it. A pointer file, or a directory above one, has the
        pointer files stat'ed again.
        """

        dirty: dict[str, bool] = {}
        pointers = False

        def mark(relative: str, force_list: bool) -> None:
            dirty[relative] = dirty.get(relative, False) or force_list

        for folded in touched:
            parts = [part for part in folded.split(os.sep) if part]
            if not parts:
                mark("", False)
                continue
            if any(pointer[: len(parts)] == tuple(parts) for pointer in _POINTER_PARTS):
                pointers = True
            parent, _ = self._tree.deepest(parts[:-1])
            mark(parent, True)
            own = self._tree.folded.get(os.sep.join(parts))
            if own is not None:
                mark(own, False)
        return dirty, pointers

    # ---- readings (each under ``_reading``)

    def _is_closed(self) -> bool:
        return self._closed

    def _read(self, kind: str) -> LayoutSighting | None:
        """One reading: a whole walk at open or after a failure, else a re-check of what was written or young."""

        # Before anything is read: a write racing this reading then moves the
        # serial past the one its fingerprint is filed under. Asked by the
        # root's key, so a root given in another spelling (an 8.3 short name)
        # is not resolved again at every reading.
        serial = write_serial(self.key)
        touched, everything = self._take_touched()
        scanned_at_ns = _time_ns()
        tree = self._tree
        try:
            if kind != "recheck" or everything or self._full:
                tree.walk(self._is_closed)
            else:
                dirty, pointers = self._dirty(touched)
                for relative, force_list in tree.young().items():
                    dirty.setdefault(relative, force_list)
                tree.refresh(dirty, self._is_closed)
                if pointers or tree.pointers_young():
                    tree.read_pointers()
        except _Stopped:
            return None
        except Exception:  # noqa: BLE001 - a reading that fails leaves nothing stable behind
            _LOG.exception("reading the layout of %s failed", self.root)
            self._full = True
            self._publish_unsettled()
            return None
        self._full = False
        self.readings[kind] += 1
        fingerprint, unreadable, lines = tree.fingerprint(scanned_at_ns)
        sighting = self._publish(fingerprint, serial, lines, scanned_at_ns)
        if not fingerprint.stable and not unreadable:
            self._settle(fingerprint)
        return sighting

    def _read_again(self) -> LayoutRefresh:
        """A refresh: take in this process's writes, walk everything, and name what moved besides."""

        tree = self._tree
        try:
            touched, everything = self._take_touched()
            if everything or self._full:
                tree.walk(self._is_closed)
            elif touched:
                dirty, pointers = self._dirty(touched)
                tree.refresh(dirty, self._is_closed)
                if pointers:
                    tree.read_pointers()
            before, _, _ = tree.places()
            serial = write_serial(self.key)
            scanned_at_ns = _time_ns()
            tree.walk(self._is_closed)
            # This process's writes that landed during the walk are read again,
            # and nothing they can have moved is said to have moved outside.
            during, during_all = self._take_touched()
            if during_all:
                tree.walk(self._is_closed)
            elif during:
                dirty, pointers = self._dirty(during)
                tree.refresh(dirty, self._is_closed)
                if pointers:
                    tree.read_pointers()
        except _Stopped:
            raise RuntimeError(f"the layout of {self.root} is closed") from None
        except Exception:  # noqa: BLE001 - a reading that fails leaves nothing stable behind
            _LOG.exception("reading the layout of %s again failed", self.root)
            self._full = True
            self._publish_unsettled()
            published = self._published
            assert published is not None  # a refresh follows a published reading
            return LayoutRefresh(published, ())
        self._full = False
        self.readings["refresh"] += 1
        after, _, _ = tree.places()
        moved = () if during_all else _moved(before, after, during)
        fingerprint, unreadable, lines = tree.fingerprint(scanned_at_ns)
        sighting = self._publish(fingerprint, serial, lines, scanned_at_ns)
        if not fingerprint.stable and not unreadable:
            self._settle(fingerprint)
        return LayoutRefresh(sighting.layout, moved)

    # ---- publications

    def _publish(self, fingerprint: LayoutFingerprint, serial: int, lines: tuple[str, ...],
                 scanned_at_ns: int) -> LayoutSighting:
        with self._publishing:
            self._generation += 1
            reading = LayoutReading(fingerprint, serial, self._generation)
            sighting = LayoutSighting(reading, lines, scanned_at_ns)
            self._published, self._sighting = reading, sighting
            self._tell(self._listeners, sighting)
        return sighting

    def _publish_unsettled(self) -> None:
        """Say the last fingerprint can no longer be trusted, keeping its digest.

        Before any reading succeeded there is none: readers get an unstable
        placeholder instead, and read uncached until a reading succeeds.
        """

        with self._publishing:
            last = self._published
            if last is not None and not last.fingerprint.stable:
                return
            self._generation += 1
            if last is None:
                self._published = LayoutReading(LayoutFingerprint("unwalked", 0, _time_ns(), False), -1,
                                                 self._generation)
            else:
                unsettled = LayoutFingerprint(last.fingerprint.digest, last.fingerprint.newest_mtime_ns,
                                              _time_ns(), False)
                self._published = LayoutReading(unsettled, last.serial, self._generation)

    def _tell(self, listeners: Iterable[Callable[[LayoutSighting], object]], sighting: LayoutSighting) -> None:
        for listener in listeners:
            try:
                listener(sighting)
            except Exception:  # noqa: BLE001 - a listener never stops a reading
                _LOG.exception("a listener of the layout of %s failed", self.root)

    # ---- the one-shot re-check

    def _settle(self, fingerprint: LayoutFingerprint) -> None:
        """A reading too young to trust: read it once more when its newest time has settled."""

        wait_s = (fingerprint.newest_mtime_ns + FINGERPRINT_SETTLED_NS - _time_ns()) / 1e9
        if wait_s > SETTLE_LONGEST_S:
            _LOG.info("the layout of %s holds a time %.0f s ahead; it is not read again until it is refreshed",
                      self.root, wait_s)
            return
        self._recheck_in(max(wait_s, 0.0) + SETTLE_MARGIN_S)

    def _recheck_in(self, delay_s: float) -> None:
        """Have the one re-check run ``delay_s`` from now, or later if it is already due later."""

        with self._lock:
            if self._closed:
                return
            due = _monotonic() + delay_s
            if self._recheck_at is None or due > self._recheck_at:
                self._recheck_at = due
            if self._timer is None:
                self._arm(self._recheck_at - _monotonic())

    def _arm(self, delay_s: float) -> None:
        timer = threading.Timer(max(delay_s, 0.0), self._recheck)
        timer.name = f"layout-recheck:{self.name}"
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _recheck(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or self._recheck_at is None:
                return
            remaining = self._recheck_at - _monotonic()
            if remaining > 0.001:
                # Pushed later meanwhile by a later write: wait for that, reading nothing now.
                self._arm(remaining)
                return
            self._recheck_at = None
        with self._reading:
            if not self._closed and self._published is not None:
                self._read("recheck")
