"""A bounded, thread-safe memo for values derived from retained project files.

ADR-008 phase 1a keeps what a reader already derived from the P036 files in
memory, keyed by what it was derived from: a record's content digest, or the
stat stamp of the directory or file it was read out of. ``ContentMemo`` is the
one container every such cache uses. It is a least-recently-used map from a
tuple key to a value, bounded by its entry count and optionally by a total
size the caller states for each entry.

A memo is never a source of truth. What it answers is what reading the files
again would answer, or the key is wrong; so an entry may be dropped at any
time, and a miss only costs the read. Whoever keeps a value here must never
change it afterwards, and must hand out only values nobody can change:
immutable ones, or a fresh copy per caller. A stat stamp is a key only once
it is ``settled``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
import os
import threading
import time
from typing import Any

from archflow.project.layout import FINGERPRINT_SETTLED_NS


def settled(stamp_ns: int, now_ns: int) -> bool:
    """Whether a modification time is old enough for an unchanged one to mean no change.

    Git's racy rule, the one the layout fingerprint applies: a time this close
    to ``now_ns`` may be shared by a later change the stamp cannot tell apart,
    because file-system clocks tick coarser than writes. A value read under
    such a stamp may be used once, and is not kept.
    """

    return now_ns - stamp_ns > FINGERPRINT_SETTLED_NS


class PathStamps:
    """The stat stamps of the paths a value is read from, taken now.

    A file states its size and modification time, a directory its own
    modification time (never the copy in its parent's listing), and a path
    that is not there states that. ``value`` is the stamp: equal stamps mean
    the paths did not change in between, once every time in them is settled.
    Take the stamp before reading what it covers, so that nothing kept under
    it is older than it.
    """

    def __init__(self) -> None:
        self.scanned_at_ns = time.time_ns()
        self._parts: list[tuple] = []
        self._settled = True

    def file(self, path: str | os.PathLike[str]) -> None:
        self._take(path, directory=False)

    def files(self, paths: Iterable[str | os.PathLike[str]]) -> None:
        for path in paths:
            self._take(path, directory=False)

    def directory(self, path: str | os.PathLike[str]) -> None:
        self._take(path, directory=True)

    def tree(self, root: str | os.PathLike[str]) -> None:
        """``root`` and every directory below it, and every file in them.

        The walk descends where ``os.walk`` descends by default: into every
        directory that is not a symbolic link. A link is stamped, not entered.
        """

        pending = [os.fspath(root)]
        while pending:
            directory = pending.pop()
            self._take(directory, directory=True)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            descend = entry.is_dir() and not entry.is_symlink()
                        except OSError:
                            descend = False
                        if descend:
                            pending.append(entry.path)
                        else:
                            self._take(entry.path, directory=False)
            except FileNotFoundError:
                continue
            except OSError:
                self._settled = False

    def value(self) -> tuple | None:
        """The stamp; None when a time in it is not settled or a path could not be stat'ed."""

        return tuple(self._parts) if self._settled else None

    def _take(self, path: str | os.PathLike[str], *, directory: bool) -> None:
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            self._parts.append((os.fspath(path), None))
            return
        except OSError:
            self._settled = False
            return
        self._parts.append((os.fspath(path), None if directory else stat.st_size, stat.st_mtime_ns))
        if not settled(stat.st_mtime_ns, self.scanned_at_ns):
            self._settled = False


class ContentMemo:
    """Least recently used out, bounded by entries and, optionally, by size.

    ``put`` states each value's size (in bytes, or whatever unit the owner
    bounds by); a value above ``max_entry_size`` is not kept at all, and the
    oldest entries leave until both bounds hold again.
    """

    def __init__(
        self,
        name: str,
        *,
        max_entries: int,
        max_size: int | None = None,
        max_entry_size: int | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("a memo keeps at least one entry")
        self.name = name
        self.max_entries = max_entries
        self.max_size = max_size
        self.max_entry_size = max_entry_size
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple, tuple[Any, int]] = OrderedDict()
        self._size = 0

    def get(self, key: tuple, default: Any = None) -> Any:
        """The value kept under ``key``, now the most recently used; else ``default``."""

        with self._lock:
            found = self._entries.get(key)
            if found is None:
                return default
            self._entries.move_to_end(key)
            return found[0]

    def put(self, key: tuple, value: Any, *, size: int = 0) -> bool:
        """Keep ``value`` under ``key``; False when it is too large to keep."""

        if size < 0:
            raise ValueError("an entry's size is not negative")
        if (self.max_entry_size is not None and size > self.max_entry_size) or (
            self.max_size is not None and size > self.max_size
        ):
            self.discard(key)
            return False
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size -= previous[1]
            self._entries[key] = (value, size)
            self._size += size
            while len(self._entries) > self.max_entries or (
                self.max_size is not None and self._size > self.max_size
            ):
                _, (_, dropped) = self._entries.popitem(last=False)
                self._size -= dropped
        return True

    def discard(self, key: tuple) -> None:
        """Forget ``key`` if it is kept."""

        with self._lock:
            found = self._entries.pop(key, None)
            if found is not None:
                self._size -= found[1]

    def discard_where(self, predicate: Callable[[tuple], bool]) -> int:
        """Forget every entry whose key ``predicate`` accepts; how many were forgotten."""

        with self._lock:
            doomed = [key for key in self._entries if predicate(key)]
            for key in doomed:
                self._size -= self._entries.pop(key)[1]
        return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._entries

    @property
    def size(self) -> int:
        """The total size stated for the entries kept now."""

        with self._lock:
            return self._size
