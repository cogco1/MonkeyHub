"""Single durable filesystem owner for one ArchFlow project document."""

from __future__ import annotations

import base64
import errno
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from pathlib import PurePath
from pathlib import PurePosixPath
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISLNK
from typing import Any, BinaryIO
from uuid import uuid4
from urllib.parse import unquote, urlsplit

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised only on POSIX hosts
    import fcntl

from archflow.project.digests import project_state_sha256
from archflow.project.layout import AUTHORED_RECORD_PATH, ProjectLayout, RunLayout
from archflow.project.manifest import ProjectManifest, ProjectManifestError
from archflow.project.memo import ContentMemo, settled
from archflow.project.ports import PersistenceArea, PersistenceDestination, TrashEntry
from archflow.project.record_kinds import (
    DESIGN_STAGE,
    PROJECT_FORMAT_MIGRATION,
    STUDIO_LOCAL_DRAFT,
    is_registered,
    require_registered,
)
from archflow.project.version_refs import (
    replace_digests as _replace_digests,
    content_digest_of as _content_digest_of,
    locations as _declared_locations,
    covered_pointers as _covered_pointers,
    derived_fields as _derived_fields,
    closure_check as _closure_check,
    file_reference_child as _file_reference_child,
    read_back as _read_back,
    recompute as _recompute_derived,
    register as _register_version_refs,
    register_derived as _register_derived_fields,
    restate as _restate_declared,
)
from archflow.project.refs import (
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    record_file_name,
    require_identifier,
    require_project_relative_path,
    parse_record_file_name,
)


class ProjectRepositoryError(RuntimeError):
    """Base error for durable project corruption or invalid transitions."""


class ProjectAlreadyExists(ProjectRepositoryError):
    pass


class ProjectIntegrityError(ProjectRepositoryError):
    pass


class StaleProjectHead(ProjectRepositoryError):
    pass


class StaleDesignBranch(ProjectRepositoryError):
    """The design branch advanced since this change was prepared."""


class StaleWorkingDraft(ProjectRepositoryError):
    """The working position changed since this view was read."""


class PromotionAuthorityError(ProjectRepositoryError):
    pass


class RunNotTrashed(ProjectRepositoryError):
    """The run stays where it is: something still holds it, or it could not be moved whole (#575)."""


class TrashEntryNotFound(ProjectRepositoryError):
    """The project trash holds no whole entry for this run."""


class RunNotRestored(ProjectRepositoryError):
    """The trashed run stays in the trash: its place in ``runs/`` is taken, or it could not be moved whole."""


def _transfer_path(value: str) -> str:
    """Retained native export names may contain @; never accept host paths.

    ``exports`` is a project-owned root a retained reference may name, so the
    closure can follow a typed edge into it. It is never walked: only a file
    some retained record actually references travels.
    """
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: expected a project-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in ("", ".", "..") or part.endswith((".", " "))
        or any(ord(char) < 32 for char in part) for part in path.parts
    ):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: unsafe project path")
    allowed = value in ("project.json", "HEAD", "design/branches.json", "design/working.json",
                        "input/runner/state-record.json", "input/runner/seats.json",
                        "input/runner/program-sheet.json")
    if not allowed and path.parts[0] not in (
        "canonical", "events", "exports", "objects", "runs",
    ):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: unassigned project area")
    if any(part.endswith(".lock") for part in path.parts):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: locks are not project content")
    return value


@dataclass(frozen=True, slots=True)
class PreparedTransition:
    expected: ProjectVersionRef
    event: ProjectRecordRef
    replacement: ProjectRecordRef


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    head: ProjectVersionRef
    reachable_paths: tuple[str, ...]
    orphan_paths: tuple[str, ...]


LEGACY_FORMAT_VERSION = 1
CURRENT_FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS: tuple[int, ...] = (
    LEGACY_FORMAT_VERSION,
    CURRENT_FORMAT_VERSION,
)

# What ``project.json`` says this build can do with a directory. There is no
# ``upgradeable`` status: a supported legacy format 1 is readable in place and
# is upgraded only through ``migrate_project_format``, which writes a new
# format-2 directory and never rewrites the source, so upgradeability is a
# property of that operation rather than a fourth thing a directory declares.
PROJECT_FORMAT_CURRENT = "current"
PROJECT_FORMAT_SUPPORTED_LEGACY = "supported_legacy"
PROJECT_FORMAT_TOO_NEW = "too_new"
PROJECT_FORMAT_INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ProjectFormatInspection:
    """What one directory's ``project.json`` declares, read and nothing more.

    ``project.json`` is the only authority here. This reads no HEAD, opens no
    project and writes nothing, so a too-new or malformed project can be named
    instead of only raising on open.
    """

    root: Path
    status: str
    format_version: int | None
    project_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class RetainedFormatEntry:
    """One row of the retained closure grouped by what governs its format.

    ``category`` says which authority owns the file: ``envelope`` for the
    project-format structures ``project.json`` versions, ``record`` for the
    content-addressed run records the record-kind table names, ``run_manifest``
    and ``design`` for the stable project structures, ``authored_input`` for
    work in progress, and ``artifact`` for digest-identified bytes. ``schema``
    is the literal the payload declares, and ``registered`` says whether the
    record-kind table still holds this kind, or is ``None`` where record-kind
    registration does not apply. Nothing here interprets a payload's contents.
    """

    category: str
    kind: str
    schema: str | None
    registered: bool | None
    count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class ProjectMigrationPlan:
    """An ephemeral dry run: what a migration would have to touch, and why not.

    This is a plain value, not a second retained manifest. ``planned`` says the
    retained closure was read and inventoried; when it is false the plan is a
    refusal and ``blockers`` says why. A caller may act only when
    ``migration_required`` is true and ``blockers`` is empty.
    """

    inspection: ProjectFormatInspection
    target_format_version: int
    planned: bool
    migration_required: bool
    project_id: str | None
    head: ProjectVersionRef | None
    run_ids: tuple[str, ...]
    inventory: tuple[RetainedFormatEntry, ...]
    retained_files: int
    retained_bytes: int
    orphan_paths: tuple[str, ...]
    required_transformations: tuple[str, ...]
    preserved: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectFormatMigration:
    """What one completed forward migration wrote, and what it did not restate.

    ``versions`` is the exact identity map the migration used: one row per
    published version, its legacy snapshot-file digest and the semantic digest
    that names the same state in the target. ``rewritten`` names the few
    project-relative files whose owner restated a base; every other retained
    file was copied byte for byte. ``embedded_legacy_references`` is always
    empty in a receipt that was returned: the migration refuses rather than
    hand back a project whose retained records still name a legacy version, so
    the field is the proof that the scan ran and found nothing rather than a
    list of what was left behind.
    ``unscanned_binaries`` names the opaque files this did not open and
    ``orphan_legacy_references`` the identities inside records the published
    chain does not reach, which are carried over exactly as they are: the
    receipt states the limits of its own account rather than reading its own
    silence as absence.
    """

    source_root: Path
    target_root: Path
    project_id: str
    source_format_version: int
    target_format_version: int
    source_head_sha256: str
    versions: tuple[tuple[int, str, str], ...]
    rewritten: tuple[str, ...]
    preserved_files: int
    orphans: tuple[str, ...]
    orphan_legacy_references: tuple[tuple[str, str, str, int, str], ...]
    unscanned_binaries: tuple[str, ...]
    embedded_legacy_references: tuple[tuple[str, str, str, int, str], ...]
    receipt: ProjectRecordRef | None

    def to_dict(self) -> dict[str, Any]:
        """The retained ``ProjectFormatMigration@1`` payload this becomes."""

        semantic = {version: new for version, _, new in self.versions}
        return {
            "schema": "ProjectFormatMigration@1",
            "project_id": self.project_id,
            "source_format_version": self.source_format_version,
            "target_format_version": self.target_format_version,
            "source_head_sha256": self.source_head_sha256,
            "versions": [
                {"version": version, "legacy_state_sha256": legacy, "state_sha256": new}
                for version, legacy, new in self.versions
            ],
            "rewritten": list(self.rewritten),
            "preserved_files": self.preserved_files,
            "orphans": list(self.orphans),
            "orphan_legacy_references": [
                {"path": path, "pointer": pointer, "shape": shape,
                 "version": version, "legacy_state_sha256": legacy}
                for path, pointer, shape, version, legacy in self.orphan_legacy_references
            ],
            "unscanned_binaries": list(self.unscanned_binaries),
            "embedded_legacy_references": [
                {
                    "path": path,
                    "pointer": pointer,
                    "shape": shape,
                    "version": version,
                    "legacy_state_sha256": legacy,
                    "state_sha256": semantic.get(version),
                }
                for path, pointer, shape, version, legacy in self.embedded_legacy_references
            ],
        }


@dataclass(slots=True)
class _CopiedClosure:
    """What the byte-for-byte pass did with everything the envelope does not own."""

    rewritten: list[str]
    preserved_files: int
    orphans: list[str]
    orphan_legacy: list[tuple[str, str, str, int, str]]
    unscanned_binaries: list[str]
    embedded: list[tuple[str, str, str, int, str]]

_LOCK_INDEX_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class _WrittenRoot:
    """What this process has written below one project root, as a counter."""

    serial: int = 0
    observers: tuple[Callable[[Path], object], ...] = ()


# Every project root this process has opened a repository on, or asked about,
# keyed by its normalized resolved path. Process-wide because a root may be
# opened by several repository objects; the serial belongs to the directory.
_WRITTEN_ROOTS_GUARD = threading.Lock()
_WRITTEN_ROOTS: dict[str, _WrittenRoot] = {}


def _plain_spelling(text: str) -> str:
    """``text`` without a Win32 extended-length prefix: ``\\\\?\\C:\\p`` is ``C:\\p``.

    The Hub hands out project paths carrying the prefix, and a directory must
    have one key however it was spelled. A UNC path keeps its two slashes.
    """

    if text[:8].lower() == "\\\\?\\unc\\":
        return "\\\\" + text[8:]
    if text[:4] == "\\\\?\\" and text[5:7] == ":\\":
        return text[4:]
    return text


def project_root_key(root: Path | str) -> str:
    """The one key this process files a project root under, however it is spelled.

    Normalized case, absolute, resolved and without an extended-length
    prefix. ``write_serial``, ``add_write_observer`` and the known layout
    (``archflow.project.watch``) key by it, so two spellings of one directory
    share one serial and one set of observers.
    """

    text = _plain_spelling(os.path.normcase(os.fspath(root)))
    with _WRITTEN_ROOTS_GUARD:
        if text in _WRITTEN_ROOTS:
            # Already a registered spelling: resolving again costs a syscall.
            return text
    resolved = os.path.abspath(os.fspath(Path(root).resolve(strict=False)))
    return _plain_spelling(os.path.normcase(resolved))


def project_path_key(path: Path | str) -> str:
    """A path below a project root, spelled the way ``project_root_key`` spells the root.

    Normalized case, absolute and without an extended-length prefix, but not
    resolved, so it costs no file-system call: a written path is spelled so to
    find the roots that contain it.
    """

    return _plain_spelling(os.path.normcase(os.path.abspath(os.fspath(path))))


def _written_root(root: Path | str) -> _WrittenRoot:
    key = project_root_key(root)
    with _WRITTEN_ROOTS_GUARD:
        return _WRITTEN_ROOTS.setdefault(key, _WrittenRoot())


def write_serial(root: Path | str) -> int:
    """How many writes this process has made below ``root`` since it started.

    Counts only this process: another process's writes are visible to a
    reader through the project's layout fingerprint
    (``archflow.project.layout``) once the project is read again (at open or
    on refresh, ``archflow.project.watch``), never here.
    """

    entry = _written_root(root)
    with _WRITTEN_ROOTS_GUARD:
        return entry.serial


def add_write_observer(
    root: Path | str, callback: Callable[[Path], object],
) -> Callable[[], None]:
    """Call ``callback(path)`` after every write this process makes below ``root``.

    The callback runs on the writer's thread, after the write is on disk and
    while the writer still holds its locks, so it must be quick and must not
    write the project. One that raises is logged and never fails the write.
    Returns the function that removes this registration again.
    """

    entry = _written_root(root)
    with _WRITTEN_ROOTS_GUARD:
        entry.observers = (*entry.observers, callback)

    def remove() -> None:
        with _WRITTEN_ROOTS_GUARD:
            observers = list(entry.observers)
            if callback in observers:
                observers.remove(callback)
            entry.observers = tuple(observers)

    return remove


# ---- remembered reads (ADR-008 phase 1a)
#
# Process-wide memos stand between the readers below and the disk. None is a
# source of truth: each answers what reading the files again would answer.
#
# A record is verified when its bytes hash to its ref's digest and parse as a
# JSON object. ``_VERIFIED_RECORDS`` remembers that a record was, and
# ``_RECORD_BYTES`` also keeps the bytes themselves, both keyed by the project
# root, the record's path and that digest. The key names the content, so an
# entry cannot go stale; it is used only while the file is still there with
# the size and modification time it was read with, and only once that time was
# settled when it was read (``archflow.project.memo``). Every ``load_json``
# still parses a payload of its own for its caller.
#
# ``_LISTINGS`` keeps what was read out of a place that can change - a record
# directory's listing, a run manifest, the design branches - under that
# place's stat stamp, once the stamp is settled. A write this process makes
# below a root forgets the kept places it can have changed at once
# (``_forget_listings``); another process's write moves a stamp. The verified
# records stay: their keys name their content.
#
# Methods marked ``_reads_fresh`` (``verify``) bypass all of them.
RECORD_BYTES_MAX_SIZE = 64 * 1024 * 1024
RECORD_BYTES_MAX_ENTRY = 4 * 1024 * 1024
_RECORD_BYTES = ContentMemo(
    "project.record-bytes",
    max_entries=16_384,
    max_size=RECORD_BYTES_MAX_SIZE,
    max_entry_size=RECORD_BYTES_MAX_ENTRY,
)
_VERIFIED_RECORDS = ContentMemo("project.verified-records", max_entries=32_768)
_LISTINGS = ContentMemo("project.listings", max_entries=8_192)
_FRESH_READS = threading.local()


def _reads_fresh(method):
    """Run ``method`` reading every file itself, past every memo (it still fills them)."""

    @functools.wraps(method)
    def fresh(*args, **kwargs):
        _FRESH_READS.depth = getattr(_FRESH_READS, "depth", 0) + 1
        try:
            return method(*args, **kwargs)
        finally:
            _FRESH_READS.depth -= 1

    return fresh


def _reading_fresh() -> bool:
    return getattr(_FRESH_READS, "depth", 0) > 0


def _stamp(path: Path) -> os.stat_result | None:
    """The path's stat, or None when it cannot be taken; never raises."""

    try:
        return os.stat(path)
    except OSError:
        return None


def _unchanged(path: Path, size: int, mtime_ns: int) -> bool:
    stat = _stamp(path)
    return stat is not None and stat.st_size == size and stat.st_mtime_ns == mtime_ns


def _plain(own: os.stat_result) -> bool:
    """Whether a path's own stat, taken without following it, shows no link, junction or other reparse point."""

    return not S_ISLNK(own.st_mode) and not getattr(own, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT


def _plain_entry(entry: os.DirEntry) -> bool:
    """``_plain`` for a listed entry. On Windows the entry's own stat comes with the listing, so this reads nothing."""

    try:
        if entry.is_symlink() or entry.is_junction():
            return False
        return os.name != "nt" or _plain(entry.stat(follow_symlinks=False))
    except OSError:
        return False


def _remembered_bytes(key: tuple) -> tuple[Path, bytes] | None:
    """The kept verified bytes and resolved path, while that file is unchanged."""

    if _reading_fresh():
        return None
    kept = _RECORD_BYTES.get(key)
    if kept is None:
        return None
    path, size, mtime_ns, data = kept
    if not _unchanged(path, size, mtime_ns):
        _RECORD_BYTES.discard(key)
        return None
    return path, data


def _verified(key: tuple) -> tuple[ProjectRecordRef | None, int] | None:
    """A record verified before, as its listed ref (if kept) and size, while its file is unchanged."""

    if _reading_fresh():
        return None
    kept = _VERIFIED_RECORDS.get(key)
    if kept is None:
        return None
    path, size, mtime_ns, ref = kept
    if not _unchanged(path, size, mtime_ns):
        _VERIFIED_RECORDS.discard(key)
        return None
    return ref, size


def _remember_verified(
    key: tuple, ref: ProjectRecordRef | None, path: Path, read: _Stamped, *, keep_bytes: bool,
) -> None:
    """Remember a verified record under the stat it was read with, once that stat is settled.

    ``keep_bytes`` keeps the bytes as well: ``load_json`` does, so its next
    reader skips the disk; a listing that only verified them does not.
    """

    stat = read.stat
    if stat is not None and stat.st_size == len(read.data) and settled(stat.st_mtime_ns, read.scanned_at_ns):
        _VERIFIED_RECORDS.put(key, (path, stat.st_size, stat.st_mtime_ns, ref))
        if keep_bytes:
            _RECORD_BYTES.put(key, (path, stat.st_size, stat.st_mtime_ns, read.data), size=len(read.data))


def _remembered_listing(key: tuple, stamp: object) -> Any:
    """What ``_keep_listing`` kept under exactly this key and stamp, else None."""

    if _reading_fresh():
        return None
    kept = _LISTINGS.get(key)
    if kept is None or kept[0] != stamp:
        return None
    return kept[1]


def _keep_listing(key: tuple, stamp: object, mtime_ns: int, scanned_at_ns: int, value: object) -> None:
    """Keep ``value`` under a stamp that is settled at ``scanned_at_ns``; else nothing."""

    if settled(mtime_ns, scanned_at_ns):
        _LISTINGS.put(key, (stamp, value))


def _listing_place(relative: str) -> str:
    """A project-relative POSIX place, spelled as ``_note_write`` spells a written one."""

    return os.path.normcase(relative.replace("/", os.sep))


def _forget_listings(root: str, written: str) -> None:
    """Forget what ``_LISTINGS`` keeps for ``root`` that a write at ``written`` can have changed.

    Every key is ``(root, place, ...)``. A write changes the written place
    itself, whatever lies below it (a directory made or removed) and the
    listing of the directory that holds it; no other kept place moves.
    """

    relative = os.path.relpath(written, root)
    if relative == os.curdir:
        _LISTINGS.discard_where(lambda key: key[0] == root)
        return
    parent = os.path.dirname(relative)
    below = relative + os.sep
    _LISTINGS.discard_where(
        lambda key: key[0] == root and (key[1] == relative or key[1] == parent or key[1].startswith(below))
    )


def _copy_branches(branches: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """A design-branch table nobody else holds: its rows and their refs copied."""

    return {
        branch_id: {field: dict(value) if isinstance(value, Mapping) else value for field, value in row.items()}
        for branch_id, row in branches.items()
    }


def _note_write(path: Path | str) -> None:
    """Count one completed on-disk mutation and tell its root's observers.

    Every registered root containing ``path`` counts it and forgets what it
    kept in ``_LISTINGS`` that the write can have changed. Called after each
    successful write in this module, never before: a reader that sees the new
    serial must also be able to see what was written.
    """

    location = project_path_key(path)
    notified: list[tuple[Callable[[Path], object], ...]] = []
    roots: list[str] = []
    with _WRITTEN_ROOTS_GUARD:
        if not _WRITTEN_ROOTS:
            return
        current = location
        while True:
            entry = _WRITTEN_ROOTS.get(current)
            if entry is not None:
                entry.serial += 1
                roots.append(current)
                if entry.observers:
                    notified.append(entry.observers)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    for root in roots:
        _forget_listings(root, location)
    if not notified:
        return
    written = Path(path)
    for observers in notified:
        for observer in observers:
            try:
                observer(written)
            except Exception:  # noqa: BLE001 - an observer never fails a write
                _LOG.exception("project write observer failed for %s", written)


def _make_directory(path: Path) -> None:
    """``mkdir -p`` that counts the write only when it created ``path``.

    An existing directory is accepted exactly as ``exist_ok=True`` accepts it.
    """

    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError:
        if not path.is_dir():
            raise
        return
    _note_write(path)


_HEAD_LOCK_TIMEOUT_SECONDS = 10.0
_HEAD_LOCK_RETRY_SECONDS = 0.05

_HEAD_SHARE_RETRY_ATTEMPTS = 200
_HEAD_SHARE_RETRY_SECONDS = 0.005


def _project_lock(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root.resolve(strict=False)))
    with _LOCK_INDEX_GUARD:
        return _PROJECT_LOCKS.setdefault(key, threading.RLock())


class ProjectHeadLocked(ProjectRepositoryError):
    """Another process holds the project head lock."""


class _HeadFileLock:
    """OS-level advisory lock serializing HEAD mutation across processes.

    The per-project ``threading.RLock`` provides in-process ordering, so this
    lock must only be acquired while that lock is held; then at most one
    thread per process ever touches the underlying file handle.  The lock
    file lives beside ``HEAD`` and deliberately carries no ``.json`` suffix
    so integrity scans never treat it as a project record.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None
        self._depth = 0

    @property
    def path(self) -> Path:
        """Where this lock lives. ``__enter__`` creates it if it is absent."""

        return self._path

    def _try_acquire(self, handle: BinaryIO) -> bool:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised only on POSIX hosts
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def __enter__(self) -> None:
        if self._depth:
            self._depth += 1
            return
        _make_directory(self._path.parent)
        # Opening an existing lock changes nothing on disk; creating one adds
        # a directory entry, and that alone is counted as a write.
        created = not self._path.exists()
        handle = self._path.open("a+b")
        if created:
            _note_write(self._path)
        deadline = time.monotonic() + _HEAD_LOCK_TIMEOUT_SECONDS
        try:
            while not self._try_acquire(handle):
                if time.monotonic() >= deadline:
                    raise ProjectHeadLocked(
                        "another process holds the project head lock: "
                        f"{self._path}"
                    )
                time.sleep(_HEAD_LOCK_RETRY_SECONDS)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        self._depth = 1

    def __exit__(self, *exc_info: object) -> None:
        self._depth -= 1
        if self._depth:
            return
        handle = self._handle
        self._handle = None
        if handle is None:  # pragma: no cover - defensive
            return
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised only on POSIX hosts
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


# ---- the writer lease (ADR-012, ``archflow.project.writer_lease``)
#
# While a project is open, its Project Runtime holds an OS lock on this file in
# the project folder. Every write below takes that lease before anything else,
# and a process that does not hold it while another does is refused; reads
# never take it. The thread lock, ``HEAD.lock`` and ``design/branches.lock``
# keep their jobs inside it, taken in that order after it.
WRITER_LOCK = "writer.lock"


class ProjectWriterBusy(ProjectRepositoryError):
    """Another process holds the project's writer lease: nothing was written (ADR-012).

    The project is open in its Project Runtime, which writes it, or another
    tool is writing it right now. Write through the runtime that has it open.
    """

    code = "PROJECT_WRITER_BUSY"


def _open_lock_file(path: Path) -> BinaryIO:
    """Open a lock file of a project, making it and its folder when absent.

    Opening an existing one changes nothing on disk; creating one adds a
    directory entry, and that alone is counted as a write. The file is never
    removed: another process could lock a replacement while one still held it.
    """

    _make_directory(path.parent)
    created = not path.exists()
    handle = path.open("a+b")
    if created:
        _note_write(path)
    return handle


def _writing(root: Path):
    """The project's writer lease for the length of one write, taken before any other lock."""

    # The lease builds on this module, so it is imported where it is used.
    from archflow.project.writer_lease import writing

    return writing(root)


def _writes(method):
    """Run a write method under the project's writer lease; refused, it has touched nothing."""

    @functools.wraps(method)
    def write(self, *args, **kwargs):
        with _writing(self.layout.root):
            return method(self, *args, **kwargs)

    return write


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise TypeError("JSON record payload must be a mapping")
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be finite JSON data") from exc
    return (encoded + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _position_revision(value: Mapping[str, Any]) -> str:
    """The working position's revision: the whole document but its ``active`` execution ledger.

    A candidate's start and end rewrite ``active`` in the same document; they
    never move the position, so they never refuse a position writer (GH-293).
    """
    return _sha256(_json_bytes({key: item for key, item in value.items() if key != "active"}))


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ProjectIntegrityError(f"cannot read project record: {path.name}") from exc


@dataclass(frozen=True, slots=True)
class _Stamped:
    """Bytes read from a file, the file's stat taken just before, and when."""

    data: bytes
    stat: os.stat_result | None
    scanned_at_ns: int


def _read_bytes_stamped(path: Path) -> _Stamped:
    """``_read_bytes``, stamped. The stat comes first: bytes kept under it are never older than it."""

    scanned_at_ns = time.time_ns()
    stat = _stamp(path)
    return _Stamped(_read_bytes(path), stat, scanned_at_ns)


def _read_json(path: Path) -> dict[str, Any]:
    return _parse_json_document(_read_bytes(path), path.name)


def _parse_json_document(data: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectIntegrityError(f"invalid JSON record: {name}") from exc
    if not isinstance(value, dict):
        raise ProjectIntegrityError(f"JSON record is not an object: {name}")
    return value


def _retry_windows_sharing(operation, *, subject: str):
    """Absorb transient Windows sharing violations around the HEAD swap.

    On Windows, ``os.replace`` onto a file a concurrent reader holds open and
    opening the file while a replace is in flight both surface as
    ``PermissionError``.  A brief bounded retry keeps readers from
    misreporting a healthy project as corrupt and keeps the compare-and-swap
    writer from leaking a bare ``PermissionError``; exhaustion fails with the
    typed ``ProjectHeadLocked``.  POSIX behavior is unchanged: the retry only
    engages on Windows.
    """

    last_error: PermissionError | None = None
    for _ in range(_HEAD_SHARE_RETRY_ATTEMPTS):
        try:
            return operation()
        except PermissionError as exc:
            if os.name != "nt":
                raise
            last_error = exc
            time.sleep(_HEAD_SHARE_RETRY_SECONDS)
    raise ProjectHeadLocked(
        f"concurrent HEAD access kept the project head busy: {subject}"
    ) from last_error


def _read_shared_json(path: Path) -> dict[str, Any]:
    """Read a JSON document that a concurrent ``os.replace`` may be swapping."""

    def read() -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            if os.name == "nt" and isinstance(exc, PermissionError):
                raise
            raise ProjectIntegrityError(
                f"cannot read project record: {path.name}"
            ) from exc

    return _parse_json_document(
        _retry_windows_sharing(read, subject=path.name),
        path.name,
    )


def _write_immutable(path: Path, data: bytes) -> None:
    """Install immutable content without ever replacing an existing target."""

    _make_directory(path.parent)
    if path.exists():
        if _read_bytes(path) != data:
            raise ProjectIntegrityError(
                f"immutable project path already contains different bytes: {path.name}"
            )
        return

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_bytes(path) != data:
                raise ProjectIntegrityError(
                    f"concurrent immutable write disagreed: {path.name}"
                )
    finally:
        temporary.unlink(missing_ok=True)
    _note_write(path)


def _replace_atomic(path: Path, data: bytes) -> None:
    _make_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # On Windows a concurrent HEAD reader briefly blocks the replace
        # with a sharing violation; retry within a bound instead of leaking
        # a bare PermissionError from a healthy race.
        _retry_windows_sharing(
            lambda: os.replace(temporary, path),
            subject=path.name,
        )
        # No post-replace fsync: raising after the swap is already visible
        # would break the caller's "exception means no promotion" contract,
        # which matters more than flushing the rename's directory metadata.
    finally:
        temporary.unlink(missing_ok=True)
    _note_write(path)


# ---- publishing a run whole (#327)
#
# A reader lists a project's runs by their directories and then loads each
# one's ``run.json``. So a new run's directory must never be visible before
# that file and the run's areas are in it. Everything a new run starts with -
# its manifest and record areas, or the files a transfer brings for it - is
# staged in a directory beside the runs named ``.<uuid>.tmp``, and that
# directory is renamed to ``runs/<run_id>`` in one step. No run id begins with
# a dot, so a staging directory, still in flight or left behind by an
# interrupted writer, is never listed as a run (``run_ids``). Only a run that
# is not there yet is published so; adding to an existing run stays one
# immutable file at a time. A run whose staging directory something outside
# this process keeps busy past every retry is completed in place, as every run
# was before, rather than refused.
_PUBLISH_RETRY_ATTEMPTS = 400
_PUBLISH_RETRY_SECONDS = 0.005


class _DirectoryBusy(ProjectRepositoryError):
    """A handle below a directory outlasted every retry of its rename."""


def _run_manifest_bytes(run: RunRef) -> bytes:
    """The bytes of a run's ``run.json``: its identity and exact base."""

    return _json_bytes({
        "schema": "ProjectRun@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": run.base.to_dict(),
    })


def _run_areas(run_layout: RunLayout) -> tuple[Path, ...]:
    """The record areas every run is created with."""

    return (
        run_layout.records,
        run_layout.branches,
        run_layout.candidates,
        run_layout.reviews,
        run_layout.workspaces,
        run_layout.recovery,
    )


def _complete_run_in_place(run_layout: RunLayout, manifest: bytes) -> None:
    """Install a run's manifest and areas where it stands, as every run was created before #327."""

    _write_immutable(run_layout.manifest, manifest)
    for directory in _run_areas(run_layout):
        _make_directory(directory)


def _stage_directory(
    parent: Path,
    files: Iterable[tuple[str, bytes]],
    directories: Iterable[str] = (),
) -> Path:
    """A new dot-named directory in ``parent`` holding ``files`` and ``directories``.

    ``files`` are POSIX paths below the new directory with their bytes. A
    failure removes what was staged before it propagates.
    """

    _make_directory(parent)
    staging = parent / f".{uuid4().hex}.tmp"
    staging.mkdir()
    _note_write(staging)
    try:
        for relative, data in files:
            _write_immutable(staging.joinpath(*PurePosixPath(relative).parts), data)
        for name in directories:
            _make_directory(staging / name)
    except BaseException:
        _discard_staging(staging)
        raise
    return staging


def _rename_directory(source: Path, target: Path) -> bool:
    """Rename ``source`` to ``target`` in one step; False when ``target`` exists.

    The rename never replaces anything: Windows refuses an existing target, and
    POSIX, which would replace an empty directory, has the target looked for
    first, under the writer's locks. On Windows a handle open below ``source``
    (a scanner reading it, such as an indexer, a sync client or antivirus)
    refuses the rename for a moment; that is retried within a bound, as the
    HEAD swap's sharing violations are.
    """

    last_error: OSError | None = None
    for _ in range(_PUBLISH_RETRY_ATTEMPTS):
        if os.path.lexists(target):
            return False
        try:
            os.rename(source, target)
        except FileExistsError:
            return False
        except PermissionError as exc:
            if os.name != "nt":
                raise
            last_error = exc
            time.sleep(_PUBLISH_RETRY_SECONDS)
            continue
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                return False
            raise
        _note_write(source)
        _note_write(target)
        return True
    raise _DirectoryBusy(
        f"the directory stayed busy and was not renamed: {source.name} -> {target.name}"
    ) from last_error


def _remove_directory(path: Path) -> str | None:
    """Remove a directory this module staged or took back; what stopped it, if anything."""

    if not os.path.lexists(path):
        return None
    try:
        shutil.rmtree(path)
    except OSError as exc:
        if os.path.lexists(path):
            _note_write(path)
            return f"{path}: {exc}"
    _note_write(path)
    return None


def _discard_staging(staging: Path) -> None:
    """Remove a staging directory. One that stays is never read as a run, so it is only logged."""

    failure = _remove_directory(staging)
    if failure is not None:
        _LOG.warning("a staging directory was left behind: %s", failure)


def _unpublish_directory(root: Path) -> str | None:
    """Take a directory just published back in one step, then remove it; what stopped it, if anything.

    It is renamed to a dot name first, so a reader sees it whole until it is
    gone. When even that is refused, it is removed where it is.
    """

    hidden = root.with_name(f".{uuid4().hex}.tmp")
    try:
        renamed = _rename_directory(root, hidden)
    except (ProjectRepositoryError, OSError):
        renamed = False
    if not renamed:
        return _remove_directory(root)
    _discard_staging(hidden)
    return None


# ---- who saved a working row's name (#575)
#
# A row of ``design/working.json`` may carry its run's ``label``. ``labelSavedBy``
# says that the person saved that label in the Hub (the history panel's save), and
# a row carries it only then: a label an agent or any other caller saved, and every
# row written before the key existed, records no saver. Every name is shown; only a
# name a person saved keeps its run out of the project trash.

_WORKING_ROW_FIELDS = frozenset({"updatedAt", "sourceStageRef", "branchId", "label", "automatic"})
LABEL_SAVED_BY = "labelSavedBy"
SAVED_BY_PERSON = "person"


def saved_by_person(row: Mapping[str, Any]) -> bool:
    """Whether the person saved this working row's label in the Hub: only such a name keeps its run (#575)."""

    return row.get("label") is not None and row.get(LABEL_SAVED_BY) == SAVED_BY_PERSON


# ---- the project trash's names (#575; ``FilesystemProjectRepository.trash_run``)

TRASH_ENTRY_SCHEMA = "ProjectTrashEntry@1"
# How long a trashed run can be restored before ``purge_trash`` deletes it.
TRASH_RETENTION = timedelta(days=30)
_TRASH_ENTRY_FIELDS = frozenset({
    "schema", "projectId", "runId", "trashedAt", "rule", "reason", "stateDigest", "supersededBy", "baseRunId",
    "label", "workingRow",
})
# A trashed run being purged is renamed to a dot name ending so: never an entry, and removed by the next purge.
_PURGING = ".purge"
_IDENTIFIER_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def names_run(data: bytes, run_id: str) -> bool:
    """Whether ``data`` names ``run_id`` whole: no identifier character right before it or right after it.

    ``"<id>"`` and ``runs/<id>/`` name it; ``<id>-2`` and ``cand-<id>`` do not.
    """

    needle = run_id.encode("ascii")
    start = data.find(needle)
    while start >= 0:
        end = start + len(needle)
        if ((start == 0 or data[start - 1] not in _IDENTIFIER_BYTES)
                and (end == len(data) or data[end] not in _IDENTIFIER_BYTES)):
            return True
        start = data.find(needle, start + 1)
    return False


def _version_from_dict(value: object, *, field: str) -> ProjectVersionRef:
    if not isinstance(value, dict) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise ProjectIntegrityError(f"{field} is not an exact project-version ref")
    try:
        return ProjectVersionRef(
            project_id=value["project_id"],
            version=value["version"],
            state_sha256=value["state_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(f"{field} is invalid") from exc


def _semantic_state_sha256(
    state: object,
    *,
    field: str,
) -> str:
    if not isinstance(state, Mapping):
        raise ProjectIntegrityError(f"{field} must be an object")
    try:
        return project_state_sha256(state)
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(
            f"{field} semantic digest is invalid"
        ) from exc


def _require_semantic_state_identity(
    state: Mapping[str, Any],
    *,
    project_id: str,
    version: int,
    field: str,
) -> None:
    if state.get("schema") != "CanonicalState@1":
        return
    if (
        state.get("project_id") != project_id
        or state.get("version") != version
    ):
        raise ProjectIntegrityError(
            f"{field} canonical identity disagrees with its project version"
        )


def _record_dict(ref: ProjectRecordRef) -> dict[str, Any]:
    return {
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(
    value: object,
    *,
    project_id: str,
    field: str,
) -> ProjectRecordRef:
    if not isinstance(value, dict) or set(value) != {
        "relative_path",
        "sha256",
        "media_type",
    }:
        raise ProjectIntegrityError(f"{field} is not an exact record ref")
    try:
        return ProjectRecordRef(
            project_id=project_id,
            relative_path=value["relative_path"],
            sha256=value["sha256"],
            media_type=value["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(f"{field} is invalid") from exc


_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})
_VERSION_DIGEST_KEYS = frozenset({"version", "state_sha256"})
_HEX_DIGITS = frozenset("0123456789abcdef")

# The three shapes a retained payload uses to name a published project version.
# ``version_ref`` is the exact ``ProjectVersionRef`` mapping; ``version_digest``
# is the two-field form a design branch retains (archflow.state.spatial); ``digest_field``
# is a flat string field such as a CAD receipt's ``base_state_sha256``. Every
# reader of retained payloads uses this one walker, so the dry run and the
# migration receipt cannot disagree about what a project still embeds.
VERSION_REF = "version_ref"
VERSION_DIGEST = "version_digest"
DIGEST_FIELD = "digest_field"


@dataclass(frozen=True, slots=True)
class EmbeddedVersionIdentity:
    """One location in a retained payload that names a project version.

    ``detail`` is ``None`` when every field read; otherwise it says what did
    not, and the readable parts are still carried. A location is reported, not
    interpreted: whether it must be restated is its owner's question.
    """

    json_pointer: str
    shape: str
    project_id: str | None
    version: int | None
    state_sha256: str | None
    detail: str | None


def _digest_or_none(value: object) -> str | None:
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value.lower())
    ):
        return value
    return None


def _version_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strings_in(payload: object, pointer: str = "") -> list[tuple[str, str]]:
    """(pointer, value) for every string anywhere in a payload."""

    found: list[tuple[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(_strings_in(value, f"{pointer}/{escaped}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_strings_in(value, f"{pointer}/{index}"))
    elif isinstance(payload, str):
        found.append((pointer or "/", payload))
    return found


def _identity_row(
    payload: Mapping[str, Any], pointer: str, shape: str,
) -> EmbeddedVersionIdentity:
    raw_project = payload.get("project_id") if shape == VERSION_REF else None
    project_id = raw_project if isinstance(raw_project, str) and raw_project else None
    version = _version_or_none(payload.get("version"))
    digest = _digest_or_none(payload.get("state_sha256"))
    problems: list[str] = []
    if shape == VERSION_REF and project_id is None:
        problems.append("project_id is not a non-empty string")
    if version is None:
        problems.append("version is not a non-negative integer")
    if digest is None:
        problems.append("state_sha256 is not a 64-character lowercase sha-256")
    return EmbeddedVersionIdentity(
        json_pointer=pointer or "/",
        shape=shape,
        project_id=project_id,
        version=version,
        state_sha256=digest,
        detail="; ".join(problems) or None,
    )


def embedded_version_identities(
    payload: object, pointer: str = "",
) -> list[EmbeddedVersionIdentity]:
    """Every place ``payload`` names a published project version, in order.

    Matched mappings are not descended, so a reference's own fields are never
    reported twice. A string field whose name contains ``state_sha256`` is
    reported as a flat digest: CAD receipts and stage envelopes retain a base
    that way, and a scan that only knew the mapping shape would call a project
    fully surveyed while those digests sat unlisted.
    """

    found: list[EmbeddedVersionIdentity] = []
    if isinstance(payload, Mapping):
        keys = set(payload)
        if keys == _VERSION_REF_KEYS:
            return [_identity_row(payload, pointer, VERSION_REF)]
        if keys == _VERSION_DIGEST_KEYS:
            return [_identity_row(payload, pointer, VERSION_DIGEST)]
        for key, value in payload.items():
            name = str(key)
            escaped = name.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            if "state_sha256" in name and isinstance(value, str):
                found.append(EmbeddedVersionIdentity(
                    json_pointer=child,
                    shape=DIGEST_FIELD,
                    project_id=None,
                    version=_version_or_none(payload.get("base_version"))
                    if name == "base_state_sha256" else None,
                    state_sha256=_digest_or_none(value),
                    detail=None if _digest_or_none(value)
                    else "state_sha256 is not a 64-character lowercase sha-256",
                ))
                continue
            found.extend(embedded_version_identities(value, child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(embedded_version_identities(value, f"{pointer}/{index}"))
    return found


def legacy_version_identities(
    payload: object, legacy_digests: Mapping[str, int],
) -> list[EmbeddedVersionIdentity]:
    """Every place ``payload`` names a known legacy version, in any spelling at all.

    The typed shapes come first, and then every remaining string in the payload
    that *contains* one of these digests, case-insensitively, wherever it sits
    and whatever else that string holds. A CAD document's user
    strings keep the canonical base as the value of a ``{key, value}`` row - no
    field is named ``state_sha256`` anywhere near it - and a migration that
    only knew the named shapes would copy that row forward unchanged and call
    the project migrated. ``legacy_digests`` maps a legacy snapshot-file digest
    to its version; a row is selected by its digest alone, so a reference whose
    declared version disagrees with its digest is still reported.
    """

    found = [
        row for row in embedded_version_identities(payload)
        if row.state_sha256 is not None and row.state_sha256 in legacy_digests
    ]
    named = {row.json_pointer for row in found}

    def inside_a_named_location(pointer: str) -> bool:
        # The digest inside a reference this already named is that reference,
        # not a second location: reporting both would refuse every project.
        return any(
            pointer == other or pointer.startswith(f"{other}/") for other in named
        )

    for pointer, value in _strings_in(payload):
        if inside_a_named_location(pointer) or len(value) < 64:
            continue
        lowered = value.lower()
        for digest, version in legacy_digests.items():
            # Not only a field that *is* the digest: a retained string may wrap
            # it - ``record:<sha>``, ``artifact:sha256:<sha>``, a snapshot file
            # name, a ``project://`` URI - and a CAD document may have
            # upper-cased it. Each of those still names that version.
            if digest.lower() in lowered:
                found.append(EmbeddedVersionIdentity(
                    json_pointer=pointer, shape=DIGEST_FIELD, project_id=None,
                    version=version, state_sha256=digest, detail=None,
                ))
                break
    return found


# The run the migration files its own receipt in, and the event keys a
# format-2 event states for itself: everything else an older event carried is
# carried through rather than dropped.
MIGRATION_RUN_ID = "format-migration"
# The planner groups undeclared identities by kind so a project carrying many
# reads as a list of contracts to declare; the migration repeats the same
# detection per record, because the person adding a declaration needs the
# record and the pointer. This sentence is how one recognises the other's.
UNDECLARED_IDENTITY_BLOCKER = "migration needs a handler"
# The run manifest is this module's own retained record, and it names the
# canonical version the run is based on in one place.
VERSION_REF_POINTERS = {"ProjectRun@1": ("/base",)}
_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("ProjectRun@1", ())
_MIGRATED_EVENT_KEYS = frozenset({
    "schema", "project_id", "event_type", "decision", "run_id",
    "from", "from_snapshot", "to", "to_snapshot", "previous_event",
    "decision_receipt",
})
# ``_write_immutable``/``_replace_atomic`` leave these behind if a writer dies.
_TEMPORARY_NAME = re.compile(r"^\..+\.[0-9a-f]{32}\.tmp$")


def _migration_source(root: Path) -> Path:
    """The source project directory, or a typed refusal naming it."""

    try:
        return Path(root).resolve(strict=True)
    except OSError as exc:
        raise ProjectIntegrityError(
            f"MIGRATION_NOT_APPLICABLE: the source directory cannot be read: {exc}"
        ) from exc


def _contains_path(outer: Path, inner: Path) -> bool:
    """Whether ``inner`` is ``outer`` or lies inside it, comparably on Windows."""

    def parts(path: Path) -> list[str]:
        text = str(path)
        for prefix in ("\\\\?\\UNC\\", "\\\\?\\"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return [os.path.normcase(part) for part in PurePath(text).parts]

    outer_parts, inner_parts = parts(outer), parts(inner)
    return inner_parts[: len(outer_parts)] == outer_parts

_RECORD_REF_KEYS = frozenset({"relative_path", "sha256", "media_type"})
_RECORD_AREAS = ("records", "reviews", "candidates", "branches")
_CASCADE_PASSES = 64


@dataclass(slots=True)
class _RecordCascade:
    """Retained records restated to a fixed point, and what moved with them."""

    payloads: dict[str, dict[str, Any]]
    renames: dict[str, str]
    moved_digests: dict[str, str]
    restated: list[str]
    blockers: list[str]


def _load_version_ref_owners() -> None:
    """Load every owner's declaration explicitly.

    A declaration registers when its owner module is imported, so a reader that
    trusted an incidental import would see a partial table and refuse a project
    for a contract this build does state. Imported here rather than at module
    scope because one of the owners is this module's own caller.
    """

    import archflow.project.version_ref_owners  # noqa: F401


def undeclared_version_identities(
    payload: object, legacy_digests: Mapping[str, int],
) -> list[EmbeddedVersionIdentity]:
    """Every legacy identity in ``payload`` that no owner declares how to restate.

    The one rule. ``plan_project_migration`` and the migration both ask this,
    so the dry run can never refuse what the migration would accept, nor the
    other way round.
    """

    covered = _covered_pointers(payload)
    return [
        row for row in legacy_version_identities(payload, legacy_digests)
        if row.json_pointer not in covered
    ]


def _is_record_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if len(parts) < 4 or parts[0] != "runs" or parts[2] not in _RECORD_AREAS:
        return False
    try:
        parse_record_file_name(parts[-1])
    except ValueError:
        return False
    return True


def _rewrite_references(
    payload: Any,
    project_id: str,
    renames: Mapping[str, str],
    digests: Mapping[str, str],
) -> Any:
    """Move every reference that names a record which moved.

    A record is named several ways: as one of the file-reference shapes its
    owner declares - which carry the path, the digest and sometimes the uri
    together - as a ``project://`` URI, and, where an owner states one, as a
    bare content digest. A content digest may also be wrapped in a longer
    string, exactly as a version digest may be. Every spelling moves together
    or the migrated project names a file that is not there.
    """

    if isinstance(payload, Mapping):
        moved = _moved_file_reference(payload, renames)
        if moved is not None:
            return {
                **{
                    key: _rewrite_references(value, project_id, renames, digests)
                    for key, value in payload.items()
                },
                **moved,
            }
        return {
            key: _rewrite_references(value, project_id, renames, digests)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_rewrite_references(item, project_id, renames, digests) for item in payload]
    if isinstance(payload, str):
        prefix = f"project://{project_id}/"
        if payload.startswith(prefix):
            relative = payload[len(prefix):]
            if relative in renames:
                return prefix + renames[relative]
        # A content digest is cited bare, but also wrapped: ``record:<sha>``,
        # ``artifact:sha256:<sha>``, a file name. It moves in every one of them
        # or the citation dangles in silence.
        return _replace_digests(payload, digests)
    return payload


def _moved_file_reference(
    payload: Mapping[str, Any], renames: Mapping[str, str],
) -> dict[str, Any] | None:
    """The fields of a declared file reference that move, if the file it names did.

    Keyed off the shapes ``register_file_reference`` declares, so the two-field
    form and the one that also carries a ``uri`` move like the exact record
    reference does rather than being called covered and left behind.
    """

    if _file_reference_child(payload) is None:
        return None
    relative = payload.get("relative_path")
    if not isinstance(relative, str) or relative not in renames:
        return None
    moved = renames[relative]
    fields: dict[str, Any] = {"relative_path": moved}
    try:
        _, digest = parse_record_file_name(PurePosixPath(moved).name)
    except ValueError:  # pragma: no cover - every renamed file is content-addressed
        return fields
    fields["sha256"] = digest
    uri = payload.get("uri")
    if isinstance(uri, str) and relative in uri:
        fields["uri"] = uri.replace(relative, moved)
    return fields


def _canonical_snapshot_moves(
    lineage: list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]],
    semantic: list[str],
    project_id: str,
) -> dict[str, str]:
    """Where each published snapshot file is going, before any of it is written.

    A retained record may cite the canonical snapshot by path - as a record
    reference or a ``project://`` URI - and that file does not survive a format
    change: its schema, its contents and therefore its name all move. The new
    names follow from the states, which are already validated by this point, so
    a record citing one moves in the same pass as everything else. If this ever
    disagreed with what the writers produce, the migration would name a file
    that is not there and refuse.
    """

    moves: dict[str, str] = {}
    previous: ProjectVersionRef | None = None
    for (legacy_ref, _, state), digest in zip(lineage, semantic):
        version = legacy_ref.version
        payload = {
            "schema": "CanonicalSnapshot@2",
            "project_id": project_id,
            "version": version,
            "state_sha256": digest,
            "parent": None if previous is None else previous.to_dict(),
            "state": state,
        }
        kind = f"state-v{version:06d}"
        moves[f"canonical/{record_file_name(kind, legacy_ref.require_digest())}"] = (
            f"canonical/{record_file_name(kind, _sha256(_json_bytes(payload)))}"
        )
        previous = ProjectVersionRef(project_id, version, digest)
    return moves


def _cascade_records(
    payloads: Mapping[str, dict[str, Any]],
    *,
    project_id: str,
    mapping: Mapping[str, str],
    legacy_digests: Mapping[str, int],
    moved: Mapping[str, str] | None = None,
) -> _RecordCascade:
    """Restate every declared version identity and follow the rename to a fixed point.

    Restating a content-addressed record changes its bytes, so its file name
    changes and everything naming it has to move too; moving those changes
    them in turn. The record graph is acyclic because a record can only name
    one that already existed, so repeating the pass converges - but it is
    capped, and a project that does not settle is refused rather than written.

    A record carrying a known legacy identity at a location no owner declares
    is a blocker: this build cannot say what restating it would mean.
    """

    current = {path: dict(payload) for path, payload in payloads.items()}
    blockers: list[str] = []
    for path in sorted(current):
        payload = current[path]
        schema = payload.get("schema")
        for row in undeclared_version_identities(payload, legacy_digests):
            blockers.append(
                f"record {path} ({schema if isinstance(schema, str) else 'undeclared schema'}) "
                f"carries a project-version identity at {row.json_pointer} that no owner "
                f"in this build restates; {UNDECLARED_IDENTITY_BLOCKER}"
            )
    if blockers:
        return _RecordCascade(current, {}, {}, [], blockers)

    # The envelope's own files are not records, but a record may cite one and
    # they move too: seeding the rename map with them lets a single pass move
    # every citation of anything at all.
    renames: dict[str, str] = dict(moved or {})
    # A record whose owner states a content digest is cited by that digest
    # elsewhere; restating the record moves the digest, and every citation of
    # it has to move too or the migrated project cites a value nothing has.
    digests: dict[str, str] = {}
    original_digests = {
        path: _content_digest_of(payload) for path, payload in current.items()
    }
    restated: set[str] = set()
    for _ in range(_CASCADE_PASSES):
        changed = False
        for path in sorted(current):
            # Always transform the payload as it arrived. The rename map is
            # keyed by the name a record came in under, so a record that moves
            # twice must be resolved from that name both times; rewriting the
            # already-rewritten copy would leave every citation one step behind.
            # Rewriting the references changes the record's contents too, so
            # the owners get a second pass to rebuild whatever they derive from
            # those contents; an empty mapping restates nothing and rebuilds
            # everything.
            updated = _restate_declared(
                _rewrite_references(
                    _restate_declared(payloads[path], mapping),
                    project_id, renames, digests,
                ),
                {},
            )
            if updated == current[path]:
                continue
            # ``current`` stays keyed by the path the record came from, so
            # one entry per record records where it has moved to so far.
            changed = True
            current[path] = updated
            restated.add(path)
            previous_digest = original_digests.get(path)
            if previous_digest is not None:
                moved_digest = _content_digest_of(updated)
                if moved_digest is not None and moved_digest != previous_digest:
                    digests[previous_digest] = moved_digest
            if _is_record_path(path):
                kind, previous_sha = parse_record_file_name(PurePosixPath(path).name)
                moved_sha = _sha256(_json_bytes(updated))
                renames[path] = (
                    PurePosixPath(path).parent / record_file_name(kind, moved_sha)
                ).as_posix()
                # A record is also cited by its file digest alone - a stage
                # envelope states the digest of the record it executes. That
                # digest moves with the record like its name does.
                if moved_sha != previous_sha:
                    digests[previous_sha] = moved_sha
        if not changed:
            return _RecordCascade(
                {renames.get(path, path): payload for path, payload in current.items()},
                # The seeded envelope moves are not this pass's to report.
                {path: name for path, name in renames.items() if path in current},
                dict(digests),
                sorted(restated),
                [],
            )
    return _RecordCascade(
        current, renames, dict(digests), sorted(restated),
        [
            f"restating this project's records did not settle within "
            f"{_CASCADE_PASSES} passes; its retained references may name each other "
            f"in a cycle this build cannot resolve"
        ],
    )


def _copy_retained_closure(
    staging: Path,
    target_root: Path,
    legacy: FilesystemProjectRepository,
    mapping: Mapping[str, str],
    legacy_digests: Mapping[str, int],
    cascade: _RecordCascade,
) -> _CopiedClosure:
    """Write everything the envelope does not own into the target.

    The cascade has already restated every declared version identity and moved
    every reference that names a record which changed; this writes the result,
    copies the opaque bytes unchanged, and records what it could not survey.
    Retained ``canonical``/``events`` records the published chain does not
    reach - what a promotion interrupted before its HEAD swap leaves behind -
    are content-addressed, so they are carried over and named in the receipt
    rather than silently dropped.
    """

    report = _CopiedClosure([], 0, [], [], [], [])
    orphans = set(legacy.verify().orphan_paths)
    # Lock files are never project content, the writer lease's included.
    locks = {path.relative_to(staging).as_posix() for path in legacy.lock_paths()} | {WRITER_LOCK}
    written = {
        path: _json_bytes(payload) for path, payload in cascade.payloads.items()
    }
    moved = set(cascade.renames)
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        if relative in ("project.json", "HEAD") or relative in locks:
            continue
        if _TEMPORARY_NAME.match(PurePosixPath(relative).name):
            continue
        data = _read_bytes(path)
        category, _, _ = _retained_category(relative)
        if relative.startswith(("canonical/", "events/")):
            if relative in orphans:
                # Content-addressed and unreachable, so it is carried over as
                # it is - but it is still read, because the snapshot an
                # interrupted promotion left names the version it was reaching.
                for row in legacy_version_identities(
                    _parse_json_document(data, relative), legacy_digests,
                ):
                    report.orphan_legacy.append((
                        relative, row.json_pointer, row.shape,
                        legacy_digests[row.state_sha256], row.state_sha256,
                    ))
                _write_immutable(target_root / relative, data)
                report.orphans.append(relative)
            continue
        if category == "artifact":
            # Digest-identified bytes are opaque. A legacy digest written
            # inside one is neither read nor rewritten; the file is named so
            # the receipt does not read its silence as absence.
            if _legacy_digest_in_bytes(data, legacy_digests):
                report.unscanned_binaries.append(relative)
            _write_immutable(target_root / relative, data)
            report.preserved_files += 1
            continue
        destination = cascade.renames.get(relative, relative)
        payload = cascade.payloads.get(destination)
        if payload is None:
            # Not a document the cascade read: its decodability was already
            # settled by the plan's own read of this closure.
            _write_immutable(target_root / relative, data)
            report.preserved_files += 1
            continue
        for row in legacy_version_identities(payload, legacy_digests):
            report.embedded.append((
                destination, row.json_pointer, row.shape,
                legacy_digests[row.state_sha256], row.state_sha256,
            ))
        _write_immutable(target_root / destination, written[destination])
        if written[destination] == data and relative == destination:
            report.preserved_files += 1
        else:
            report.rewritten.append(
                destination if relative not in moved else f"{relative} -> {destination}"
            )
    return report


def _require_consistent_migration(
    migrated: FilesystemProjectRepository,
    closure: Mapping[str, Any],
    mapping: Mapping[str, str],
    moved_digests: Mapping[str, str],
) -> None:
    """Every retained record reads, resolves and still digests to what it says.

    ``verify()`` proves the published chain and the design history; this proves
    the rest of the closure: that no record names a legacy version in any
    spelling, that every record reference resolves to a file whose bytes match,
    that a record whose owner derives a digest from its own contents still
    agrees with that owner, that no citation kept a digest the cascade moved,
    and finally that every owner's own rule about the records it names still
    holds when those records are resolved across the migrated closure.
    """

    # Every version the migration replaced. A migrated record naming one of
    # these anywhere - in a typed reference or as the value of a CAD user
    # string - is a location the cascade did not reach.
    legacy = {digest: 0 for digest in mapping}
    # Every place the closure still names a digest some record had before this
    # migration: a citation that did not move with the record it names is where
    # the next stage to check a binding would fail instead.
    cited: dict[str, set[str]] = {}
    documents: dict[str, Mapping[str, Any]] = {}
    for entry in closure["files"]:
        path = entry["path"]
        if _retained_category(path)[0] == "artifact":
            continue
        payload = _parse_json_document(
            migrated.read_transfer_file(path, entry["sha256"]), path,
        )
        for row in legacy_version_identities(payload, legacy):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path}{row.json_pointer} still names a legacy version"
            )
        for reference in _record_references_in(payload, migrated.layout.project_id):
            resolved = migrated.layout.root / reference
            if not resolved.is_file():
                raise ProjectIntegrityError(
                    f"MIGRATION_VERIFY: {path} names {reference}, which is not there"
                )
            try:
                _, named = parse_record_file_name(PurePosixPath(reference).name)
            except ValueError:
                continue
            if _sha256(_read_bytes(resolved)) != named:
                raise ProjectIntegrityError(
                    f"MIGRATION_VERIFY: {reference} does not hold the bytes its name claims"
                )
        if _retained_category(path)[0] != "envelope":
            # The envelope was written by this build's own writers rather than
            # cascaded, so there is no earlier derivation of it to go stale.
            _require_self_derived_fields_agree(path, payload)
        for _, value in _strings_in(payload):
            # Wrapped or upper-cased, a citation is still a citation: indexing
            # only bare 64-character strings is how one goes unnoticed.
            for digest in moved_digests:
                if digest.lower() in value.lower():
                    cited.setdefault(digest.lower(), set()).add(path)
        documents[path] = payload
    _require_citations_resolve(cited, moved_digests)
    _require_cross_record_rules(migrated.layout.project_id, documents)


def _require_cross_record_rules(
    project_id: str, documents: Mapping[str, Mapping[str, Any]],
) -> None:
    """Let each owner check what it says about the records it names.

    Nothing inside one record can tell whether the digest it cites for another
    is still right. The owner is handed a resolver over the migrated closure
    and applies its own rule - the same rule the next stage will apply, and
    computed independently of whatever this record's digest registration says.
    """

    prefix = f"project://{project_id}/"

    def resolve(reference: object) -> Mapping[str, Any] | None:
        if isinstance(reference, Mapping):
            reference = reference.get("relative_path")
        if not isinstance(reference, str):
            return None
        if reference.startswith(prefix):
            reference = reference[len(prefix):]
        return documents.get(reference)

    for path, payload in sorted(documents.items()):
        try:
            _closure_check(payload, resolve)
        except ProjectRepositoryError:
            raise
        except Exception as exc:
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} breaks a rule its own owner states "
                f"about the records it names: {exc}"
            ) from exc


def _require_citations_resolve(
    cited: Mapping[str, set[str]], moved_digests: Mapping[str, str],
) -> None:
    """No citation still names a record by a digest that record no longer has.

    A record restated by the cascade digests differently afterwards, and the
    cascade moves every citation of the old value with it. Anything that kept
    the old value points at a version of that record which does not exist -
    ``require_stage_exit_binding`` would call it stale or cross-scoped, long
    after the migration reported success. What a record claims about *itself*
    is checked by its owner's reader and by the owners of the records citing
    it, not here.
    """

    for old, new in sorted(moved_digests.items()):
        for path in sorted(cited.get(old.lower(), ())):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} still cites {old[:12]}, the digest a "
                f"record had before this migration; it is now {new[:12]}"
            )


def _require_self_derived_fields_agree(path: str, payload: Mapping[str, Any]) -> None:
    """A record still says about itself what its owner says about it.

    A record that serialises a digest of its own contents is invalidated by
    any restatement inside it. Its owner declares those fields and how to
    rebuild them; here the rebuild is run and compared. A kind whose schema
    never declared what it derives is refused rather than trusted: that is the
    state in which a migration writes a record its own reader will reject.
    """

    schema = payload.get("schema")
    if not isinstance(schema, str):
        return
    fields = _derived_fields(schema)
    if fields is None:
        # A record that only names files by their digest derives nothing from a
        # canonical base; the question is whether it carries a version identity.
        if _declared_locations(payload):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} is a {schema} carrying a project-version "
                f"identity whose owner never said what it derives from one"
            )
        return
    # The owner's reader first, and for every record whose owner lends one: a
    # record with nothing derived can still be left inconsistent by a moved
    # reference, and the reader is what every later stage will use.
    try:
        _read_back(payload)
    except ProjectRepositoryError:
        raise
    except Exception as exc:
        raise ProjectIntegrityError(
            f"MIGRATION_VERIFY: {path} is a {schema} its own reader rejects "
            f"after migration: {exc}"
        ) from exc
    if not fields:
        return
    # The rebuild cannot vouch for itself - comparing it with itself proves
    # only that it is a function - but it does say which fields to look at.
    rebuilt = _recompute_derived(payload)
    for field in fields:
        if payload.get(field) != rebuilt.get(field):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} keeps a {field!r} its own owner no longer "
                f"derives from its contents"
            )




def _record_references_in(payload: object, project_id: str) -> list[str]:
    """Every project-relative path this payload names as a retained record."""

    found: list[str] = []
    if isinstance(payload, Mapping):
        if _RECORD_REF_KEYS <= set(payload) <= (_RECORD_REF_KEYS | {"project_id"}):
            relative = payload.get("relative_path")
            if isinstance(relative, str) and relative.startswith("runs/"):
                found.append(relative)
        for value in payload.values():
            found.extend(_record_references_in(value, project_id))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_record_references_in(value, project_id))
    elif isinstance(payload, str):
        prefix = f"project://{project_id}/"
        if payload.startswith(prefix) and payload[len(prefix):].startswith("runs/"):
            found.append(payload[len(prefix):])
    return found


def _legacy_digest_in_bytes(data: bytes, legacy_digests: Mapping[str, int]) -> bool:
    """Whether opaque bytes contain a known legacy digest as plain ASCII."""

    return any(digest.encode("ascii") in data for digest in legacy_digests)


class FilesystemProjectRepository:
    """Content-addressed records, issued HEAD and atomic design positions."""

    def __init__(self, layout: ProjectLayout, manifest: ProjectManifest) -> None:
        self.layout = layout
        self._manifest = manifest
        self._lock = _project_lock(layout.root)
        self._head_lock = _HeadFileLock(layout.root / "HEAD.lock")
        self._design_lock = _HeadFileLock(layout.design_branches.with_suffix(".lock"))
        # Registered so every write below this root counts (``write_serial``).
        _written_root(layout.root)
        # The root's registered spelling: the first part of every key this
        # repository keeps in the read memos, and what ``_note_write`` names.
        self._root_key = project_root_key(layout.root)

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        project_id: str,
        initial_state: Mapping[str, Any],
        authored_record: Mapping[str, Any] | None = None,
        seat_pack: Mapping[str, Any] | None = None,
    ) -> FilesystemProjectRepository:
        """Create a project, optionally installing its caller-authored WIP inputs.

        Input meaning belongs to the caller. These files are installed only at
        creation; they create no run and are not published design content.
        """

        layout = ProjectLayout(Path(root), project_id)
        manifest = ProjectManifest(
            project_id=project_id,
            format_version=CURRENT_FORMAT_VERSION,
        )
        state = dict(initial_state)
        _require_semantic_state_identity(
            state, project_id=project_id, version=0, field="initial state"
        )
        state_sha256 = _semantic_state_sha256(state, field="initial state")
        authored_files = tuple(
            (path, _json_bytes(dict(payload)))
            for path, payload in (
                (layout.authored_record, authored_record),
                (layout.seat_pack, seat_pack),
            )
            if payload is not None
        )
        # The existence check and the HEAD write must sit inside the same
        # OS-level lock compare_and_swap uses, or a stalled duplicate
        # initialize from another process can reset a promoted HEAD to v0.
        head_lock = _HeadFileLock(layout.root / "HEAD.lock")
        with _writing(layout.root), _project_lock(layout.root), head_lock:
            if layout.manifest.exists() or layout.head.exists():
                raise ProjectAlreadyExists(f"project already exists: {project_id}")
            for path, _ in authored_files:
                if path.exists():
                    raise ProjectAlreadyExists(f"authored input already exists: {path}")
            _make_directory(layout.root)
            for directory in (
                layout.inputs,
                layout.objects,
                layout.events,
                layout.canonical,
                layout.runs,
                layout.exports,
            ):
                _make_directory(directory)
            _write_immutable(layout.manifest, _json_bytes(manifest.to_dict()))
            repository = cls(layout, manifest)
            snapshot = repository._put_internal_json(
                layout.canonical,
                "state-v000000",
                {
                    "schema": "CanonicalSnapshot@2",
                    "project_id": project_id,
                    "version": 0,
                    "state_sha256": state_sha256,
                    "parent": None,
                    "state": state,
                },
            )
            head_ref = ProjectVersionRef(project_id, 0, state_sha256)
            event = repository._put_internal_json(
                layout.events,
                "event-v000000",
                {
                    "schema": "ProjectEvent@2",
                    "project_id": project_id,
                    "event_type": "project.initialized",
                    "decision": "accepted",
                    "run_id": None,
                    "from": None,
                    "from_snapshot": None,
                    "to": head_ref.to_dict(),
                    "to_snapshot": _record_dict(snapshot),
                    "previous_event": None,
                    "decision_receipt": None,
                },
            )
            for path, data in authored_files:
                _write_immutable(path, data)
            _replace_atomic(
                layout.head,
                _json_bytes(repository._head_payload(head_ref, snapshot, event)),
            )
            repository.verify()
            return repository

    @classmethod
    def open(cls, root: Path) -> FilesystemProjectRepository:
        root = Path(root).resolve(strict=False)
        manifest_path = root / "project.json"
        try:
            manifest = ProjectManifest.from_dict(_read_json(manifest_path))
        except (ProjectManifestError, TypeError, ValueError) as exc:
            raise ProjectIntegrityError("project manifest is invalid") from exc
        if manifest.format_version not in {
            LEGACY_FORMAT_VERSION,
            CURRENT_FORMAT_VERSION,
        }:
            raise ProjectIntegrityError(
                f"unsupported project format version: "
                f"{manifest.format_version}"
            )
        repository = cls(ProjectLayout(root, manifest.project_id), manifest)
        repository.verify()
        return repository

    @_writes
    def initialize_authored_inputs(
        self,
        *,
        expected_head: ProjectVersionRef,
        expected_record: Mapping[str, Any] | None,
        authored_record: Mapping[str, Any],
        seat_pack: Mapping[str, Any],
    ) -> bool:
        """Install caller-prepared initial WIP without replacing other inputs.

        The caller decides whether the record is empty and what modeling means.
        Compare the exact previous mapping and HEAD under the project lock. Seats
        are installed first; an interrupted call can retry with the same payload
        before the actionable authored record becomes visible. No run or issue.
        """

        with self._lock, self._head_lock:
            if self.read_head() != expected_head or expected_head.version != 0:
                raise StaleProjectHead("initial authored inputs require the unchanged initial HEAD")
            path = self.layout.authored_record
            current = _read_json(path) if path.exists() else None
            desired = dict(authored_record)
            if current != expected_record and current != desired:
                raise ProjectAlreadyExists("authored input changed before initialization")
            seats = self.layout.seat_pack
            if seats.exists() and _read_json(seats) != dict(seat_pack):
                raise ProjectAlreadyExists("project already declares different modeling seats")
            if current == desired and seats.exists():
                return False
            if not seats.exists():
                _write_immutable(seats, _json_bytes(dict(seat_pack)))
            _replace_atomic(path, _json_bytes(desired))
            return True

    def load_manifest(self) -> ProjectManifest:
        current = ProjectManifest.from_dict(_read_json(self.layout.manifest))
        if current != self._manifest:
            raise ProjectIntegrityError("immutable project manifest changed")
        return current

    def read_head(self) -> ProjectVersionRef:
        # The version this project publishes right now. ``read_head`` keeps
        # its name after ADR-007 because it reads the file called ``HEAD``
        # and the format owns that name; every caller that shows the answer
        # to a person calls it the published design, or issue N.
        return self._read_head_document()[0]

    def load_current_state(self) -> dict[str, Any]:
        _, snapshot_ref, _ = self._read_head_document()
        snapshot = self.load_json(snapshot_ref)
        state = snapshot.get("state")
        if not isinstance(state, dict):
            raise ProjectIntegrityError("canonical snapshot state is not an object")
        return state

    def load_version_state(self, version: ProjectVersionRef) -> dict[str, Any]:
        """Read an exact published ancestor, following retained event references."""
        self._require_project_version(version, durable=True)
        current, snapshot_ref, event_ref = self._read_head_document()
        while current != version:
            if current.version <= version.version:
                raise ProjectIntegrityError("requested version is not in published history")
            event = self.load_json(event_ref)
            parent = _version_from_dict(event.get("from"), field="event from")
            snapshot_ref = (
                _record_from_dict(event.get("from_snapshot"), project_id=self._manifest.project_id,
                                  field="event from_snapshot")
                if self._manifest.format_version == CURRENT_FORMAT_VERSION
                else self._canonical_ref_for_version(parent)
            )
            event_ref = _record_from_dict(event.get("previous_event"), project_id=self._manifest.project_id,
                                          field="previous_event")
            self._verify_snapshot(snapshot_ref, parent)
            current = parent
        snapshot = self._verify_snapshot(snapshot_ref, version)
        state = snapshot.get("state")
        if not isinstance(state, dict):
            raise ProjectIntegrityError("canonical snapshot state is not an object")
        return state

    @_writes
    def create_run(
        self,
        run_id: str,
        *,
        base: ProjectVersionRef | None = None,
    ) -> RunRef:
        require_identifier(run_id, "run_id")
        chosen_base = base or self.read_head()
        self._require_project_version(chosen_base, durable=True)
        run = RunRef(self._manifest.project_id, run_id, chosen_base)
        run_layout = self.layout.run(run_id)
        manifest = _run_manifest_bytes(run)
        with self.working_draft_guard():
            if not self._publish_run(
                run_layout.root,
                ((run_layout.manifest.name, manifest),),
                tuple(area.name for area in _run_areas(run_layout)),
            ):
                # Already there - a run created before, or a directory an
                # interrupted writer left without its manifest - or held
                # busy: completed in place, and never replaced.
                _complete_run_in_place(run_layout, manifest)
        return run

    @_writes
    def create_run_batch(
        self,
        run_ids: tuple[str, ...],
        *,
        base: ProjectVersionRef | None = None,
        require_current_base: bool = False,
    ) -> tuple[RunRef, ...]:
        """Create an exact run set or leave no member of the set behind.

        This is intentionally narrower than a general record transaction: it
        atomically guards fixed sibling-run bootstrap under the repository's
        process and HEAD locks, and rolls back only roots proven absent before
        this call if filesystem creation fails. Every sibling is staged before
        the first is published; each is published whole (``create_run``).
        """

        if not isinstance(run_ids, tuple) or not run_ids:
            raise TypeError("run_ids must be a non-empty tuple")
        for run_id in run_ids:
            require_identifier(run_id, "run_id")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_ids must be unique")
        if not isinstance(require_current_base, bool):
            raise TypeError("require_current_base must be bool")
        chosen_base = base or self.read_head()
        self._require_project_version(chosen_base, durable=True)
        runs = tuple(
            RunRef(self._manifest.project_id, run_id, chosen_base)
            for run_id in run_ids
        )
        layouts = tuple(self.layout.run(run.run_id) for run in runs)
        with self._lock, self._head_lock:
            if require_current_base:
                current, _, _ = self._read_head_document()
                if current != chosen_base:
                    raise StaleProjectHead(
                        "fixed run batch no longer shares the current canonical base"
                    )
            existing = [
                layout.root
                for layout in layouts
                if layout.root.exists() or layout.manifest.exists()
            ]
            if existing:
                raise ProjectAlreadyExists(
                    "run batch target already exists: "
                    + ", ".join(str(path) for path in existing)
                )
            resolved_runs = self.layout.runs.resolve(strict=False)
            roots: list[Path] = []
            for run_layout in layouts:
                resolved_root = run_layout.root.resolve(strict=False)
                if (
                    not resolved_root.is_relative_to(resolved_runs)
                    or resolved_root.parent != resolved_runs
                ):
                    raise ProjectIntegrityError(
                        "run batch target escaped the assigned runs root"
                    )
                roots.append(resolved_root)
            staged: list[Path] = []
            published: list[Path] = []
            try:
                for run, run_layout in zip(runs, layouts, strict=True):
                    staged.append(_stage_directory(
                        resolved_runs,
                        ((run_layout.manifest.name, _run_manifest_bytes(run)),),
                        tuple(area.name for area in _run_areas(run_layout)),
                    ))
                for run, run_layout, staging, root in zip(runs, layouts, staged, roots, strict=True):
                    try:
                        renamed = _rename_directory(staging, root)
                    except _DirectoryBusy as busy:
                        # As in ``_publish_run``: completed in place rather
                        # than refused, and taken back with the rest on failure.
                        _LOG.warning("run %s is completed in place instead of published whole: %s", root.name, busy)
                        _discard_staging(staging)
                        published.append(root)
                        _complete_run_in_place(run_layout, _run_manifest_bytes(run))
                        continue
                    if not renamed:
                        raise ProjectAlreadyExists(
                            f"run batch target already exists: {root}"
                        )
                    published.append(root)
            except BaseException as exc:
                cleanup_failures = [
                    failure
                    for failure in (_unpublish_directory(root) for root in reversed(published))
                    if failure is not None
                ]
                for staging in staged:
                    _discard_staging(staging)
                if cleanup_failures:
                    raise ProjectIntegrityError(
                        "run batch failed and rollback was incomplete: "
                        + "; ".join(cleanup_failures)
                    ) from exc
                raise
        return runs

    def _publish_run(
        self,
        root: Path,
        files: Iterable[tuple[str, bytes]],
        directories: Iterable[str] = (),
    ) -> bool:
        """Publish a run that is not here yet whole; False when the caller must complete it in place.

        Its ``files`` and ``directories`` are staged beside the runs and the
        staged directory is renamed to ``root`` in one step, so a reader lists
        the run complete or not at all. False, with nothing staged left behind,
        when ``root`` already exists, or when a handle below the staging
        directory outlasts every retry of the rename (a slow scanner, a sync
        client): the caller then completes the run in place, as every run was
        before #327, rather than refusing the write. The caller holds the
        project and HEAD locks.
        """

        if os.path.lexists(root):
            return False
        staging = _stage_directory(root.parent, files, directories)
        try:
            published = _rename_directory(staging, root)
        except _DirectoryBusy as exc:
            _LOG.warning("run %s is completed in place instead of published whole: %s", root.name, exc)
            published = False
        except BaseException:
            _discard_staging(staging)
            raise
        if not published:
            _discard_staging(staging)
        return published

    def run_ids(self) -> tuple[str, ...]:
        """The project's run directories by name, in name order.

        A run published whole (``create_run``) appears here complete, never
        before its ``run.json`` and record areas. A dot-named entry is never a
        run - no run id begins with a dot - so a run still being staged, or a
        staging directory an interrupted writer left behind, is not listed.
        Every other directory is, whether or not its ``run.json`` can be read:
        ``load_run`` refuses a damaged one, so a survey can name it.
        """

        runs = self.layout.runs
        if not runs.is_dir():
            return ()
        return tuple(sorted(
            item.name for item in runs.iterdir()
            if not item.name.startswith(".") and item.is_dir()
        ))

    def load_run(self, run_id: str) -> RunRef:
        require_identifier(run_id, "run_id")
        payload = self._run_manifest(run_id)
        if set(payload) != {"schema", "project_id", "run_id", "base"}:
            raise ProjectIntegrityError("run manifest schema drifted")
        if (
            payload.get("schema") != "ProjectRun@1"
            or payload.get("project_id") != self._manifest.project_id
            or payload.get("run_id") != run_id
        ):
            raise ProjectIntegrityError("run manifest identity changed")
        run = RunRef(
            project_id=self._manifest.project_id,
            run_id=run_id,
            base=_version_from_dict(payload["base"], field="run base"),
        )
        self._validate_run(run, payload)
        return run

    @_writes
    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef:
        self._validate_run(run)
        require_identifier(record_kind, "record_kind")
        # The kind is the only word a reader has for what a retained file is,
        # so it comes from one table. Reads stay unrestricted: retained runs
        # from the archived lanes carry kinds the spine never writes.
        try:
            require_registered(record_kind)
        except ValueError as exc:
            raise ProjectRepositoryError(str(exc)) from exc
        directory = self._destination_directory(run, destination)
        if destination.area in {
            PersistenceArea.EVENT,
            PersistenceArea.CANONICAL,
            PersistenceArea.OBJECT,
        }:
            raise PromotionAuthorityError(
                f"{destination.area.value} writes are repository-internal"
            )
        data = _json_bytes(payload)
        digest = _sha256(data)
        path = directory / record_file_name(record_kind, digest)
        with self.working_draft_guard():
            self.load_run(run.run_id)
            _write_immutable(path, data)
        return self._record_ref(path, digest, "application/json")

    @_writes
    def ingest(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef:
        self._validate_run(run)
        if destination.area is not PersistenceArea.OBJECT:
            raise ValueError("binary artifacts require the object destination")
        require_identifier(artifact_id, "artifact_id")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type must be non-empty text")
        data = source.read()
        if not isinstance(data, bytes):
            raise TypeError("artifact source must be opened in binary mode")
        digest = _sha256(data)
        path = self.layout.objects / digest[:2] / digest
        with self._lock:
            _write_immutable(path, data)
        relative = path.relative_to(self.layout.root).as_posix()
        return ProjectArtifactRef(
            project_id=self._manifest.project_id,
            artifact_id=artifact_id,
            relative_path=relative,
            sha256=digest,
            media_type=media_type,
        )

    @_writes
    def put_workspace_file(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        workspace_relative_path: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef:
        """Install one immutable binary below an assigned run workspace."""

        self._validate_run(run)
        if destination.area is not PersistenceArea.RUN_WORKSPACE:
            raise ValueError(
                "workspace files require the run-workspace destination"
            )
        require_identifier(artifact_id, "artifact_id")
        relative_in_workspace = require_project_relative_path(
            workspace_relative_path
        )
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type must be non-empty text")
        data = source.read()
        if not isinstance(data, bytes):
            raise TypeError("workspace source must be opened in binary mode")

        workspace = self._destination_directory(run, destination).resolve(
            strict=False
        )
        portable = PurePosixPath(relative_in_workspace)
        path = (workspace / Path(*portable.parts)).resolve(strict=False)
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("workspace path escapes the assigned run") from exc
        digest = _sha256(data)
        with self._lock:
            _write_immutable(path, data)
        relative = path.relative_to(self.layout.root).as_posix()
        return ProjectArtifactRef(
            project_id=self._manifest.project_id,
            artifact_id=artifact_id,
            relative_path=relative,
            sha256=digest,
            media_type=media_type,
        )

    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]:
        """The record's payload, verified against its digest; a fresh object per call.

        Bytes already verified are taken from ``_RECORD_BYTES`` while the file
        is unchanged; they are parsed again for every caller, so no caller can
        change what another one reads.
        """

        path, data, payload = self._read_record(ref)
        return _parse_json_document(data, path.name) if payload is None else payload

    def require_json(self, ref: ProjectRecordRef) -> int:
        """Check exactly what ``load_json`` checks, without a payload nobody reads.

        Raises as ``load_json`` would; returns the record's size in bytes. A
        record verified before whose file is unchanged is not read again.
        """

        self._require_record(ref)
        verified = _verified((self._root_key, ref.relative_path, ref.sha256))
        return len(self._read_record(ref)[1]) if verified is None else verified[1]

    def record_stat(self, ref: ProjectRecordRef) -> os.stat_result:
        """The stat, taken now, of the file ``load_json`` reads for ``ref``.

        The path is resolved as ``load_json`` resolves it; a record verified
        before stats the path it was resolved to then, instead of resolving
        it again. Raises ``OSError`` when the file cannot be stat'ed.
        """

        self._require_record(ref)
        kept = None if _reading_fresh() else _VERIFIED_RECORDS.get((self._root_key, ref.relative_path, ref.sha256))
        return os.stat(self._record_path(ref) if kept is None else kept[0])

    def _read_record(
        self, ref: ProjectRecordRef, plain: Path | None = None,
    ) -> tuple[Path, bytes, dict[str, Any] | None]:
        """The record's resolved path and verified bytes, with their payload when they were just parsed.

        Kept bytes (``_RECORD_BYTES``) are answered, unparsed, while their file
        keeps the stat they were read with. ``plain`` is the record's path when
        the listing reading it has just found it plain on a plain way from the
        project root (``_plain_names``): resolving it would lead nowhere else.
        """

        self._require_record(ref)
        key = (self._root_key, ref.relative_path, ref.sha256)
        kept = _remembered_bytes(key)
        if kept is not None:
            return kept[0], kept[1], None
        path = self._record_path(ref) if plain is None else plain
        read = _read_bytes_stamped(path)
        if _sha256(read.data) != ref.sha256:
            raise ProjectIntegrityError(f"record digest mismatch: {ref.relative_path}")
        payload = _parse_json_document(read.data, path.name)
        # A listing hands the kept ref back as its own, so only a ref exactly
        # like the ones it builds (``_record_ref``) is kept for it.
        listed = ref if ref.media_type == "application/json" else None
        _remember_verified(key, listed, path, read, keep_bytes=True)
        return path, read.data, payload

    def _record_path(self, ref: ProjectRecordRef) -> Path:
        self._require_record(ref)
        return self.layout.resolve_record(ref)

    def _require_design_stage(self, ref: ProjectRecordRef) -> dict[str, Any]:
        self._require_record(ref)
        parts = PurePosixPath(ref.relative_path).parts
        if len(parts) != 4 or parts[0] != "runs" or parts[2] != "reviews" or ref.record_kind != DESIGN_STAGE:
            raise ProjectIntegrityError("design branch must reference a retained design stage review")
        self.load_run(parts[1])
        return self.load_json(ref)

    def _design_branch_payload(self, branch_id: str, value: object) -> dict[str, Any]:
        require_identifier(branch_id, "branch_id")
        if not isinstance(value, Mapping) or set(value) != {"branch_id", "parent_branch", "fork_stage", "head_stage"}:
            raise ProjectIntegrityError("design branch reference schema drifted")
        if value["branch_id"] != branch_id:
            raise ProjectIntegrityError("design branch id disagrees with its key")
        parent = value["parent_branch"]
        if parent is not None:
            require_identifier(parent, "parent_branch")
            if parent == branch_id:
                raise ProjectIntegrityError("design branch cannot fork from itself")
        result: dict[str, Any] = {"branch_id": branch_id, "parent_branch": parent}
        for field in ("fork_stage", "head_stage"):
            ref = ProjectRecordRef.from_dict(value[field], field)
            self._require_design_stage(ref)
            result[field] = ref.to_dict()
        return result

    @contextmanager
    def working_draft_guard(self):
        """Keep exact source reads and retaining their references atomic with cleanup.

        Cleanup removes no run; it takes these locks too, so a guarded read of
        the current local recovery snapshot never races that snapshot's expiry.

        HEAD already exists for every project. Do not create a design lock merely
        to validate a first request that may be refused without a project write.
        Nested position writes acquire the design lock after this HEAD lock.
        The guard is a write's: it takes the writer lease first (ADR-012).
        """
        with _writing(self.layout.root), self._lock, self._head_lock:
            yield

    def read_working_draft(self) -> tuple[dict[str, Any], str | None]:
        """Read the project's working position; reading an old project writes nothing."""
        path = self.layout.working_draft
        if not path.exists():
            return {"schema": "ProjectWorkingDraft@1", "projectId": self.layout.project_id,
                    "current": None, "runs": {}, "active": {}, "localDraftRef": None}, None
        value = _read_shared_json(path)
        self._require_working_draft(value)
        return value, _position_revision(value)

    def _require_working_draft(self, value: Mapping[str, Any]) -> None:
        if (not isinstance(value, Mapping) or set(value) != {
                "schema", "projectId", "current", "runs", "active", "localDraftRef"}
                or value.get("schema") != "ProjectWorkingDraft@1"
                or value.get("projectId") != self.layout.project_id
                or not isinstance(value.get("runs"), dict) or not isinstance(value.get("active"), dict)):
            raise ProjectIntegrityError("working draft metadata is invalid")
        if value["current"] is not None and (not isinstance(value["current"], str) or value["current"] not in value["runs"]):
            raise ProjectIntegrityError("working draft current position is not retained")
        for run_id, row in value["runs"].items():
            require_identifier(run_id, "working run_id")
            # ``labelSavedBy`` is optional: a row from before it, or one no person named, has none (#575).
            if (not isinstance(row, dict) or set(row) - {LABEL_SAVED_BY} != _WORKING_ROW_FIELDS
                    or type(row["automatic"]) is not bool
                    or any(row[key] is not None and not isinstance(row[key], str)
                           for key in ("sourceStageRef", "branchId", "label"))
                    or (LABEL_SAVED_BY in row and (row[LABEL_SAVED_BY] != SAVED_BY_PERSON or row["label"] is None))):
                raise ProjectIntegrityError("working run retention metadata is invalid")
            self._working_time(row["updatedAt"])
        for run_id, sources in value["active"].items():
            require_identifier(run_id, "active run_id")
            if not isinstance(sources, list):
                raise ProjectIntegrityError("active sources must be retained run ids")
            for source in sources:
                require_identifier(source, "active source run_id")
        if value["localDraftRef"] is not None:
            ref = ProjectRecordRef.from_dict(value["localDraftRef"])
            if ref.project_id != self.layout.project_id or ref.record_kind != STUDIO_LOCAL_DRAFT:
                raise ProjectIntegrityError("working recovery must name this project's local draft")

    @staticmethod
    def _working_time(value: str) -> datetime:
        try:
            result = datetime.fromisoformat(value)
            if result.tzinfo is None:
                raise ValueError("timestamp must include its timezone")
            return result
        except (TypeError, ValueError) as exc:
            raise ProjectIntegrityError("working draft timestamp is invalid") from exc

    @_writes
    def compare_and_swap_working_draft(
        self, *, expected_revision: str | None, value: Mapping[str, Any], ledger: bool = False,
    ) -> tuple[dict[str, Any], str]:
        """Replace the document while its position is still ``expected_revision``.

        A position writer never carries the ``active`` ledger: the retained one
        is kept, so an execution recorded after its read is never dropped. Only
        ``protect_working_run`` and ``release_working_run`` write the ledger, and
        they read it under this same lock.
        """
        with self._lock, self._design_lock:
            current, actual = self.read_working_draft()
            if actual != expected_revision:
                raise StaleWorkingDraft("the working draft changed; read it before updating this position")
            if not ledger:
                value = {**value, "active": current["active"]}
            self._require_working_draft(value)
            data = _json_bytes(value)
            _replace_atomic(self.layout.working_draft, data)
            written = _parse_json_document(data, "working draft")
            return written, _position_revision(written)

    @_writes
    def protect_working_run(self, run_id: str, source_run_id: str | None, *, dependencies: tuple[str, ...] = ()) -> None:
        """Record a candidate's execution and exact inputs before they are read, without serializing workers."""
        require_identifier(run_id, "active run_id")
        with self._lock, self._design_lock:
            sources = sorted(set(dependencies) | ({source_run_id} if source_run_id is not None else set()))
            for source in sources:
                self.load_run(source)
            value, revision = self.read_working_draft()
            if run_id in value["active"]:
                raise ProjectRepositoryError("this candidate already has an active or interrupted execution")
            value["active"][run_id] = sources
            self.compare_and_swap_working_draft(expected_revision=revision, value=value, ledger=True)

    @_writes
    def release_working_run(self, run_id: str) -> None:
        with self._lock, self._design_lock:
            value, revision = self.read_working_draft()
            if run_id in value["active"]:
                del value["active"][run_id]
                self.compare_and_swap_working_draft(expected_revision=revision, value=value, ledger=True)

    @_writes
    def prune_working_draft(self, *, now: str) -> tuple[str, ...]:
        """Expire superseded local recovery snapshots; never remove a run.

        Every run stays here, including an automatic candidate that nothing
        refers to: a run leaves ``runs/`` only through the project trash
        (``trash_run``, #575), whole and restorable, under the rules its caller
        owns, never on this timer.
        A snapshot is a crash-recovery copy of unsynced local commands and only
        the one ``localDraftRef`` names is ever read back, so that one is kept
        however old it is; any other snapshot expires 24 hours after its
        ``updatedAt``. Returns the removed snapshots' project-relative paths.
        """
        cutoff = self._working_time(now) - timedelta(hours=24)
        with self._lock, self._head_lock, self._design_lock:
            value, _ = self.read_working_draft()
            current = None if value["localDraftRef"] is None else value["localDraftRef"]["relative_path"]
            expired: list[Path] = []
            for path in sorted(self.layout.runs.glob(f"*/recovery/{STUDIO_LOCAL_DRAFT}-*.json")):
                payload = _parse_json_document(_read_bytes(path), path.name)
                if payload.get("schema") != "StudioLocalDraft@1" or payload.get("projectId") != self.layout.project_id:
                    raise ProjectIntegrityError("local recovery has an inconsistent project binding")
                if path.relative_to(self.layout.root).as_posix() != current and self._working_time(payload["updatedAt"]) < cutoff:
                    expired.append(path)
            runs = self.layout.runs.resolve()
            for path in expired:
                if not path.resolve().is_relative_to(runs) or path.is_symlink():
                    raise ProjectIntegrityError("recovery cleanup target escaped the project")
            for path in expired:
                path.unlink(missing_ok=True)
                _note_write(path)
            return tuple(path.relative_to(self.layout.root).as_posix() for path in expired)

    # ---- the project trash (#575)
    #
    # A run its caller's retention rules may clean leaves ``runs/`` whole: its
    # directory is renamed to ``trash/runs/<run_id>`` in one step, unchanged,
    # beside the manifest ``trash/entries/<run_id>.json``. Restoring renames it
    # back; purging deletes an entry once it is older than the retention.
    # Nothing is ever copied, and a run that cannot be renamed whole stays where
    # it is, so a run is never in two places or in part of one.
    #
    # The working position names its runs, and reopening refuses a row whose
    # run is gone (``verify``), so a run's row travels with it. Trashing writes
    # the manifest, then drops the row, then renames the run; restoring renames
    # the run back, then puts the row back, then removes the manifest. Every
    # interruption therefore leaves a manifest whose run is in ``runs/`` whole
    # (or already purged): ``_recover_trash`` puts a missing row back and
    # removes that manifest before the next trash, restore or purge.

    @_writes
    def trash_run(
        self, run_id: str, *, now: str, rule: str, reason: str, state_digest: str | None = None,
        superseded_by: str | None = None, base_run_id: str | None = None, label: str | None = None,
    ) -> TrashEntry:
        """Move one whole run into the project trash, or refuse and leave it where it is.

        The caller decides which run may go and says why; this refuses a run
        that anything the repository owns still holds: the Working Head, an
        execution's ledger (as run or source), a working row a person chose or
        whose name a person saved (``saved_by_person``), the local recovery, a
        review, Stage or attributed act retained in the run, a design branch,
        or the published history. Its automatic row, if it has one, moves with
        it, with any name no person saved. ``RunNotTrashed`` says why a run
        stayed.
        """

        require_identifier(run_id, "run_id")
        self._working_time(now)
        require_identifier(rule, "rule")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("reason must be one sentence of at most 2000 characters")
        for field, value in (("state_digest", state_digest), ("label", label)):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
                raise ValueError(f"{field} must be non-empty text or None")
        for field, value in (("superseded_by", superseded_by), ("base_run_id", base_run_id)):
            if value is not None:
                require_identifier(value, field)
        with self._lock, self._head_lock, self._design_lock:
            self._recover_trash()
            source, target = self.layout.run(run_id).root, self.layout.trashed_run(run_id)
            manifest = self.layout.trash_manifest(run_id)
            try:
                self.load_run(run_id)
            except (ProjectRepositoryError, OSError, ValueError) as exc:
                raise RunNotTrashed(f"{run_id} is not a whole run of this project") from exc
            if os.path.lexists(target) or os.path.lexists(manifest):
                raise RunNotTrashed(f"the trash already holds a run named {run_id}")
            working, revision = self.read_working_draft()
            held = self._trash_holds(run_id, working)
            if held:
                raise RunNotTrashed(f"{run_id} stays: {held}")
            row = working["runs"].get(run_id)
            payload = {
                "schema": TRASH_ENTRY_SCHEMA, "projectId": self.layout.project_id, "runId": run_id,
                "trashedAt": now, "rule": rule, "reason": reason.strip(), "stateDigest": state_digest,
                "supersededBy": superseded_by, "baseRunId": base_run_id, "label": label,
                "workingRow": None if row is None else dict(row),
            }
            _write_immutable(manifest, _json_bytes(payload))
            if row is not None:
                remaining = {**working, "runs": {key: value for key, value in working["runs"].items() if key != run_id}}
                self.compare_and_swap_working_draft(expected_revision=revision, value=remaining)
            _make_directory(target.parent)
            failure: BaseException | None = None
            try:
                moved = _rename_directory(source, target)
            except (_DirectoryBusy, OSError) as exc:
                moved, failure = False, exc
            if not moved:
                # Still whole in runs/: its row goes back and the manifest goes.
                self._settle_trash_entry(run_id, payload)
                raise RunNotTrashed(
                    f"{run_id} could not be moved whole and was left where it is"
                    + (f": {failure}" if failure is not None else "")
                ) from failure
            return self._trash_entry_of(payload)

    def trash_entries(self) -> tuple[TrashEntry, ...]:
        """Every whole entry of the project trash, oldest first; reading writes nothing.

        An entry is whole when its manifest and its run are both there. A
        manifest an interrupted move left without its run is not an entry: the
        next trash, restore or purge settles it. One that cannot be read is
        left out and logged, and is never purged or restored.
        """

        directory = self.layout.trash / "entries"
        if not directory.is_dir():
            return ()
        entries: list[TrashEntry] = []
        for path in sorted(directory.glob("*.json")):
            run_id = path.name[: -len(".json")]
            try:
                require_identifier(run_id, "run_id")
                if not self.layout.trashed_run(run_id).is_dir():
                    continue
                entries.append(self._trash_entry_of(self._trash_payload(path, run_id)))
            except (ProjectRepositoryError, OSError, ValueError) as exc:
                _LOG.warning("a project trash manifest could not be read: %s: %s", path.name, exc)
        return tuple(sorted(entries, key=lambda entry: (self._working_time(entry.trashed_at), entry.run_id)))

    @_writes
    def restore_trashed_run(self, run_id: str) -> TrashEntry:
        """Move a trashed run back into ``runs/`` exactly as it left, with its working row.

        ``TrashEntryNotFound`` when the trash holds no whole entry for it;
        ``RunNotRestored`` when ``runs/<run_id>`` is taken or the run cannot be
        moved back whole, and then it stays in the trash.
        """

        require_identifier(run_id, "run_id")
        with self._lock, self._head_lock, self._design_lock:
            self._recover_trash()
            source, manifest = self.layout.trashed_run(run_id), self.layout.trash_manifest(run_id)
            if not manifest.is_file() or not source.is_dir():
                raise TrashEntryNotFound(f"the project trash holds no run named {run_id}")
            payload = self._trash_payload(manifest, run_id)
            target = self.layout.run(run_id).root
            if os.path.lexists(target):
                raise RunNotRestored(f"{run_id} stays in the trash: the project already has a run named {run_id}")
            _make_directory(target.parent)
            try:
                moved = _rename_directory(source, target)
            except (_DirectoryBusy, OSError) as exc:
                raise RunNotRestored(f"{run_id} could not be moved back whole and stays in the trash: {exc}") from exc
            if not moved:
                raise RunNotRestored(f"{run_id} stays in the trash: the project already has a run named {run_id}")
            self._settle_trash_entry(run_id, payload)
            return self._trash_entry_of(payload)

    def purge_trash(self, *, now: str, retention: timedelta | None = None) -> tuple[str, ...]:
        """Delete every trash entry older than the retention (30 days); the purged run ids.

        An entry is taken out of the trash's listing in one step (renamed to
        a dot name) before its manifest and its files are deleted; files that
        will not go yet are tried again by the next purge.
        """

        cutoff = self._working_time(now) - (TRASH_RETENTION if retention is None else retention)
        purged: list[str] = []
        if not self._trash_holds_anything():
            # Nothing to purge or recover: take no lock, so a project opened with an empty trash
            # is left exactly as it was, without even a lock file (#575). Nor the writer lease.
            return ()
        with _writing(self.layout.root), self._lock, self._head_lock, self._design_lock:
            self._recover_trash()
            for entry in self.trash_entries():
                if self._working_time(entry.trashed_at) >= cutoff:
                    continue
                trashed = self.layout.trashed_run(entry.run_id)
                hidden = trashed.with_name(f".{uuid4().hex}{_PURGING}")
                try:
                    moved = _rename_directory(trashed, hidden)
                except (_DirectoryBusy, OSError) as exc:
                    _LOG.warning("trashed run %s stays until the next purge: %s", entry.run_id, exc)
                    continue
                if not moved:
                    continue
                manifest = self.layout.trash_manifest(entry.run_id)
                manifest.unlink(missing_ok=True)
                _note_write(manifest)
                failure = _remove_directory(hidden)
                if failure is not None:
                    _LOG.warning("a purged run's files are removed by the next purge: %s", failure)
                purged.append(entry.run_id)
        return tuple(purged)

    def _trash_holds_anything(self) -> bool:
        """Whether the trash has a manifest or a run directory, a purge's leftovers included; reads only."""

        for directory in (self.layout.trash / "entries", self.layout.trash / "runs"):
            try:
                if any(directory.iterdir()):
                    return True
            except FileNotFoundError:
                continue
        return False

    def run_mentions(self, run_ids: Iterable[str]) -> dict[str, frozenset[str]]:
        """Where the project's retained JSON names each run, outside that run's own directory.

        A byte search, not a parse: a run id counts wherever it stands whole,
        between characters no identifier contains, as in ``"<id>"`` or
        ``runs/<id>/``. Each place is ``run:<id>`` for a file in another run's
        directory, else the file's project-relative path. The trash, exports
        (shared copies, never project state), shared objects and staging or
        temporary files are not searched. Reading writes nothing and takes no
        lock: a caller checks again as it acts.
        """

        wanted = {require_identifier(run_id, "run_id"): None for run_id in dict.fromkeys(run_ids)}
        if not wanted:
            return {}
        found: dict[str, set[str]] = {run_id: set() for run_id in wanted}
        root = os.fspath(self.layout.root)
        skipped = {"trash", "exports", "objects"}
        for directory, directories, files in os.walk(root):
            relative = os.path.relpath(directory, root)
            parts = [] if relative == os.curdir else relative.split(os.sep)
            if not parts:
                directories[:] = [name for name in directories if name not in skipped and not name.startswith(".")]
            else:
                directories[:] = [name for name in directories if not name.startswith(".")]
            owner = parts[1] if len(parts) > 1 and parts[0] == "runs" else None
            for name in files:
                if not name.endswith(".json") or name.startswith("."):
                    continue
                try:
                    with open(os.path.join(directory, name), "rb") as handle:
                        data = handle.read()
                except OSError:
                    continue
                for run_id in wanted:
                    if run_id == owner or not names_run(data, run_id):
                        continue
                    found[run_id].add(f"run:{owner}" if owner is not None else "/".join((*parts, name)))
        return {run_id: frozenset(places) for run_id, places in found.items()}

    def _trash_holds(self, run_id: str, working: Mapping[str, Any]) -> str | None:
        """What keeps a run out of the trash, in words, or None. The caller holds every lock."""

        if working["current"] == run_id:
            return "it is the Working Head"
        if run_id in working["active"] or any(run_id in sources for sources in working["active"].values()):
            return "an execution is using it"
        row = working["runs"].get(run_id)
        if row is not None and saved_by_person(row):
            return "a person saved it as a version"
        if row is not None and not row["automatic"]:
            return "a person chose it as the working position"
        local = working["localDraftRef"]
        if local is not None:
            if PurePosixPath(local["relative_path"]).parts[:2] == ("runs", run_id):
                return "it holds the local recovery"
            draft = self.load_json(ProjectRecordRef.from_dict(local))
            source = (draft.get("draft") or {}).get("source") or {}
            if source.get("sourceRunId") == run_id:
                return "the local recovery was made from it"
        run_layout = self.layout.run(run_id)
        for area, words in ((run_layout.reviews, "it keeps a review, Stage or attributed act"),
                            (run_layout.recovery, "it keeps local recovery")):
            if any(path.is_file() for path in area.glob("*.json")):
                return words
        for branch in self.read_design_branches().values():
            for field in ("fork_stage", "head_stage"):
                if PurePosixPath(branch[field]["relative_path"]).parts[:2] == ("runs", run_id):
                    return "a design branch stands on it"
        if run_id in self._published_runs():
            return "the published history names it"
        return None

    def _published_runs(self) -> set[str]:
        """The runs the published event chain names, as its run or as where its decision is kept."""

        runs: set[str] = set()
        _, _, event_ref = self._read_head_document()
        seen: set[str] = set()
        current: ProjectRecordRef | None = event_ref
        while current is not None and current.relative_path not in seen:
            seen.add(current.relative_path)
            event = self.load_json(current)
            if isinstance(event.get("run_id"), str):
                runs.add(event["run_id"])
            receipt = event.get("decision_receipt")
            if isinstance(receipt, Mapping) and isinstance(receipt.get("relative_path"), str):
                parts = PurePosixPath(receipt["relative_path"]).parts
                if len(parts) > 1 and parts[0] == "runs":
                    runs.add(parts[1])
            previous = event.get("previous_event")
            current = None if previous is None else _record_from_dict(
                previous, project_id=self._manifest.project_id, field="previous_event")
        return runs

    def _trash_payload(self, path: Path, run_id: str) -> dict[str, Any]:
        payload = _read_json(path)
        if (set(payload) != _TRASH_ENTRY_FIELDS or payload["schema"] != TRASH_ENTRY_SCHEMA
                or payload["projectId"] != self.layout.project_id or payload["runId"] != run_id
                or not all(isinstance(payload[key], str) for key in ("trashedAt", "rule", "reason"))
                or not all(payload[key] is None or isinstance(payload[key], str)
                           for key in ("stateDigest", "supersededBy", "baseRunId", "label"))
                or not (payload["workingRow"] is None or isinstance(payload["workingRow"], dict))):
            raise ProjectIntegrityError(f"project trash manifest is invalid: {path.name}")
        self._working_time(payload["trashedAt"])
        return payload

    @staticmethod
    def _trash_entry_of(payload: Mapping[str, Any]) -> TrashEntry:
        return TrashEntry(
            run_id=payload["runId"], trashed_at=payload["trashedAt"], rule=payload["rule"], reason=payload["reason"],
            state_digest=payload["stateDigest"], superseded_by=payload["supersededBy"],
            base_run_id=payload["baseRunId"], label=payload["label"],
            working_row=None if payload["workingRow"] is None else dict(payload["workingRow"]),
        )

    def _settle_trash_entry(self, run_id: str, payload: Mapping[str, Any]) -> None:
        """A manifest whose run is not in the trash: the run's row back if it is in ``runs/``, then no manifest."""

        row = payload["workingRow"]
        if row is not None and self.layout.run(run_id).manifest.is_file():
            working, revision = self.read_working_draft()
            if run_id not in working["runs"]:
                working["runs"][run_id] = dict(row)
                self.compare_and_swap_working_draft(expected_revision=revision, value=working)
        manifest = self.layout.trash_manifest(run_id)
        manifest.unlink(missing_ok=True)
        _note_write(manifest)

    def _recover_trash(self) -> None:
        """Settle what an interrupted trash, restore or purge left; the caller holds every lock."""

        entries = self.layout.trash / "entries"
        if entries.is_dir():
            for path in sorted(entries.glob("*.json")):
                run_id = path.name[: -len(".json")]
                try:
                    require_identifier(run_id, "run_id")
                    if self.layout.trashed_run(run_id).is_dir():
                        continue
                    payload = self._trash_payload(path, run_id)
                except (ProjectRepositoryError, OSError, ValueError) as exc:
                    _LOG.warning("a project trash manifest is left as it is: %s: %s", path.name, exc)
                    continue
                self._settle_trash_entry(run_id, payload)
        runs = self.layout.trash / "runs"
        if runs.is_dir():
            for item in runs.iterdir():
                if item.name.startswith(".") and item.name.endswith(_PURGING):
                    failure = _remove_directory(item)
                    if failure is not None:
                        _LOG.warning("a purged run's files are removed by the next purge: %s", failure)

    def read_design_branches(self) -> dict[str, dict[str, Any]]:
        """Read verified design references, including projects predating them.

        The verified table is kept under the stat stamp of ``branches.json``
        (``_LISTINGS``); every caller gets its own copy to change.
        """
        path = self.layout.design_branches
        scanned_at = time.time_ns()
        stat = _stamp(path)
        if stat is None and not path.exists():
            return {}
        key = (self._root_key, _listing_place("design/branches.json"), "design-branches")
        stamp = None if stat is None else (stat.st_size, stat.st_mtime_ns)
        if stamp is not None:
            kept = _remembered_listing(key, stamp)
            if kept is not None:
                return _copy_branches(kept)
        payload = _read_shared_json(path)
        if set(payload) != {"schema", "project_id", "branches"} or payload["schema"] != "DesignBranches@1" or payload["project_id"] != self._manifest.project_id:
            raise ProjectIntegrityError("design branches belong to another project or schema")
        if not isinstance(payload["branches"], Mapping):
            raise ProjectIntegrityError("design branches must be a mapping")
        branches = {branch_id: self._design_branch_payload(branch_id, value) for branch_id, value in payload["branches"].items()}
        if stat is not None:
            _keep_listing(key, stamp, stat.st_mtime_ns, scanned_at, _copy_branches(branches))
        return branches

    @_writes
    def compare_and_swap_design_branch(
        self,
        *,
        branch_id: str,
        expected_head: ProjectRecordRef | None,
        branch: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish a retained design position without moving canonical HEAD.

        The application owns acceptance and lineage rules. The repository
        verifies durable references and serializes creation or head movement.
        """
        replacement = self._design_branch_payload(branch_id, branch)
        if expected_head is not None:
            self._require_design_stage(expected_head)
        with self._lock, self._design_lock:
            branches = self.read_design_branches()
            previous = branches.get(branch_id)
            actual = None if previous is None else ProjectRecordRef.from_dict(previous["head_stage"])
            if actual != expected_head:
                raise StaleDesignBranch(f"design branch {branch_id!r} changed; reload its current stage")
            if previous is not None and any(previous[key] != replacement[key] for key in ("parent_branch", "fork_stage")):
                raise ProjectIntegrityError("an existing design branch's fork identity is immutable")
            branches[branch_id] = replacement
            _replace_atomic(self.layout.design_branches, _json_bytes({
                "schema": "DesignBranches@1", "project_id": self._manifest.project_id,
                "branches": branches,
            }))
        return replacement

    def list_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str | None = None,
    ) -> tuple[ProjectRecordRef, ...]:
        """Discover verified records, optionally selecting one exact kind first.

        Kind selection uses the retained filename, not payload contents. Reads
        still accept historical kinds and verify every selected record's bytes.
        The verified listing is kept under the directory's stat stamp
        (``_LISTINGS``) and answers again while that stamp is unchanged; a
        record's bytes are checked again whenever ``load_json`` reads them.
        """

        self._validate_run(run)
        if record_kind is not None:
            require_identifier(record_kind, "record_kind")
        directory = self._destination_directory(run, destination)
        if destination.area in {
            PersistenceArea.OBJECT,
            PersistenceArea.EVENT,
            PersistenceArea.CANONICAL,
        }:
            raise ValueError("use typed repository loaders for this internal area")
        # The directory's own stat stamps the listing: adding, removing or
        # renaming a record moves it. Taken before the directory is read, so
        # what is kept is never older than its stamp.
        scanned_at = time.time_ns()
        stat = _stamp(directory)
        if stat is None and not directory.exists():
            return ()
        relative_directory = directory.relative_to(self.layout.root).as_posix()
        key = (self._root_key, _listing_place(relative_directory), "list_json", relative_directory, record_kind)
        if stat is not None:
            kept = _remembered_listing(key, stat.st_mtime_ns)
            if kept is not None:
                return kept
        refs = []
        pattern = "*.json" if record_kind is None else f"{record_kind}-*.json"
        plain_names: frozenset[str] | None = None

        def plain_path(path: Path) -> Path | None:
            # Looked at once per listing, and only when a listed record has to be read.
            nonlocal plain_names
            if plain_names is None:
                plain_names = self._plain_names(directory)
            return path if path.name in plain_names else None

        for path in sorted(directory.glob(pattern)):
            # ``glob`` joins each name onto ``directory``, so this is the
            # record's project-relative path ``_record_ref`` would compute.
            relative = f"{relative_directory}/{path.name}"
            if record_kind is None:
                ref = self._listed_record(path, relative, plain_path)
            else:
                try:
                    kind, digest = parse_record_file_name(path.name)
                except ValueError as exc:
                    raise ProjectIntegrityError(str(exc)) from exc
                if kind != record_kind:
                    continue
                verified = _verified((self._root_key, relative, digest))
                ref = None if verified is None else verified[0]
                if ref is None:
                    ref = self._record_ref(path, digest, "application/json")
                    self._read_record(ref, plain_path(path))
            refs.append(ref)
        listed = tuple(refs)
        if stat is not None:
            _keep_listing(key, stat.st_mtime_ns, stat.st_mtime_ns, scanned_at, listed)
        return listed

    def _plain_names(self, directory: Path) -> frozenset[str]:
        """The names in ``directory`` that are where they say: plain entries on a plain way from the project root.

        Resolving a path follows links, junctions and other reparse points. A
        path below the already resolved project root that passes none of them
        resolves to itself, so a record named here stays in the project
        without a ``Path.resolve()`` per record (#575). A link, junction or
        reparse point on the way from the root, or as the entry itself, leaves
        names out, and those records are resolved and checked as any read
        checks them, ``project-relative path escapes project root`` included.
        Nothing is kept.
        """

        try:
            parts = directory.relative_to(self.layout.root).parts
        except ValueError:
            return frozenset()
        current = self.layout.root
        try:
            for part in parts:
                current = current / part
                if not _plain(os.lstat(current)):
                    return frozenset()
            with os.scandir(directory) as entries:
                return frozenset(entry.name for entry in entries if _plain_entry(entry))
        except OSError:
            return frozenset()

    def _listed_record(
        self, path: Path, relative: str, plain_path: Callable[[Path], Path | None] | None = None,
    ) -> ProjectRecordRef:
        """The ref of one listed record, its digest taken from its own bytes.

        The digest is the one these bytes have, so they are parsed as read
        instead of read again to be checked against it (#314). A record
        verified before under the digest its name claims answers instead,
        while its file is unchanged: its bytes hashed to that digest. A
        record its listing found plain (``_plain_names``) is not resolved:
        resolving it would lead nowhere else.
        """

        try:
            claimed = parse_record_file_name(path.name)[1]
        except ValueError:
            claimed = None
        if claimed is not None:
            verified = _verified((self._root_key, relative, claimed))
            if verified is not None:
                return verified[0] or self._record_ref(path, claimed, "application/json")
        read = _read_bytes_stamped(path)
        ref = self._record_ref(path, _sha256(read.data), "application/json")
        resolved = (None if plain_path is None else plain_path(path)) or self._record_path(ref)
        _parse_json_document(read.data, resolved.name)
        _remember_verified((self._root_key, ref.relative_path, ref.sha256), ref, resolved, read, keep_bytes=False)
        return ref

    @_writes
    def prepare_transition(
        self,
        *,
        run: RunRef,
        expected: ProjectVersionRef,
        replacement_state: Mapping[str, Any],
        decision_receipt: ProjectRecordRef,
    ) -> PreparedTransition:
        # ``prepare_transition``, ``compare_and_swap`` and
        # ``PromotionDecision@1`` keep their names: the decision receipt's key
        # set is compared literally below and retained receipts were written
        # against it (ADR-004). The act these two perform is called an
        # *issue* (ADR-007), and ``archflow.project.issue`` is its one caller.
        self._validate_run(run)
        self._require_project_version(expected, durable=True)
        if run.base != expected:
            raise StaleProjectHead("run is not based on the proposed canonical version")
        receipt = self.load_json(decision_receipt)
        expected_receipt = {
            "schema",
            "status",
            "project_id",
            "run_id",
            "checked_state",
            "candidate_ref",
        }
        if set(receipt) != expected_receipt:
            raise PromotionAuthorityError("promotion decision receipt schema drifted")
        if (
            receipt["schema"] != "PromotionDecision@1"
            or receipt["status"] != "accepted"
            or receipt["project_id"] != run.project_id
            or receipt["run_id"] != run.run_id
            or _version_from_dict(
                receipt["checked_state"],
                field="decision checked_state",
            )
            != expected
        ):
            raise PromotionAuthorityError(
                "only an accepted exact-base decision may prepare canonical promotion"
            )

        current, current_snapshot, previous_event = (
            self._read_head_document()
        )
        if current != expected:
            raise StaleProjectHead(
                f"expected {expected!r}; canonical is {current!r}"
            )
        next_version = expected.version + 1
        replacement_payload = dict(replacement_state)
        if self._manifest.format_version == LEGACY_FORMAT_VERSION:
            snapshot = self._put_internal_json(
                self.layout.canonical,
                f"state-v{next_version:06d}",
                {
                    "schema": "CanonicalSnapshot@1",
                    "project_id": run.project_id,
                    "version": next_version,
                    "parent": expected.to_dict(),
                    "state": replacement_payload,
                },
            )
            replacement = ProjectVersionRef(
                run.project_id,
                next_version,
                snapshot.sha256,
            )
            event_payload = {
                "schema": "ProjectEvent@1",
                "project_id": run.project_id,
                "event_type": "candidate.promoted",
                "decision": "accepted",
                "run_id": run.run_id,
                "from": expected.to_dict(),
                "to": replacement.to_dict(),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": _record_dict(decision_receipt),
            }
        else:
            return self._write_transition(
                previous=expected,
                previous_snapshot=current_snapshot,
                previous_event=previous_event,
                state=replacement_payload,
                event_type="candidate.promoted",
                decision="accepted",
                run_id=run.run_id,
                decision_receipt=_record_dict(decision_receipt),
            )
        event = self._put_internal_json(
            self.layout.events,
            f"event-v{next_version:06d}",
            event_payload,
        )
        return PreparedTransition(expected, event, snapshot)

    def _write_transition(
        self,
        *,
        previous: ProjectVersionRef,
        previous_snapshot: ProjectRecordRef,
        previous_event: ProjectRecordRef,
        state: Mapping[str, Any],
        event_type: Any,
        decision: Any,
        run_id: Any,
        decision_receipt: Any,
        carried: Mapping[str, Any] | None = None,
    ) -> PreparedTransition:
        """Write one format-2 snapshot and the event that reaches it. HEAD is untouched.

        The only place a ``CanonicalSnapshot@2``/``ProjectEvent@2`` pair is
        composed, so a promotion and a format migration cannot drift into two
        spellings of the same transition. ``carried`` seeds the event with keys
        an older event already held; the keys this format owns are written over
        them, so a foreign field survives a migration instead of being dropped.
        """

        version = previous.version + 1
        payload = dict(state)
        _require_semantic_state_identity(
            payload,
            project_id=self._manifest.project_id,
            version=version,
            field=f"state v{version}",
        )
        state_sha256 = _semantic_state_sha256(payload, field=f"state v{version}")
        snapshot = self._put_internal_json(
            self.layout.canonical,
            f"state-v{version:06d}",
            {
                "schema": "CanonicalSnapshot@2",
                "project_id": self._manifest.project_id,
                "version": version,
                "state_sha256": state_sha256,
                "parent": previous.to_dict(),
                "state": payload,
            },
        )
        replacement = ProjectVersionRef(
            self._manifest.project_id, version, state_sha256,
        )
        event = self._put_internal_json(
            self.layout.events,
            f"event-v{version:06d}",
            {
                **(dict(carried) if carried else {}),
                "schema": "ProjectEvent@2",
                "project_id": self._manifest.project_id,
                "event_type": event_type,
                "decision": decision,
                "run_id": run_id,
                "from": previous.to_dict(),
                "from_snapshot": _record_dict(previous_snapshot),
                "to": replacement.to_dict(),
                "to_snapshot": _record_dict(snapshot),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": decision_receipt,
            },
        )
        return PreparedTransition(previous, event, snapshot)

    @_writes
    def compare_and_swap(
        self,
        *,
        expected: ProjectVersionRef,
        event: ProjectRecordRef,
        replacement: ProjectRecordRef,
    ) -> ProjectVersionRef:
        self._require_project_version(expected, durable=True)
        # Thread lock first, then the OS file lock: the read-validate-replace
        # sequence must be exclusive across processes, not just threads.
        with self._lock, self._head_lock:
            current, current_snapshot, current_event = (
                self._read_head_document()
            )
            if current != expected:
                raise StaleProjectHead(
                    f"expected {expected!r}; canonical is {current!r}"
                )
            self._require_area(event, "events/")
            self._require_area(replacement, "canonical/")
            snapshot = self.load_json(replacement)
            event_payload = self.load_json(event)
            if self._manifest.format_version == LEGACY_FORMAT_VERSION:
                next_ref = ProjectVersionRef(
                    self._manifest.project_id,
                    expected.version + 1,
                    replacement.sha256,
                )
                snapshot_valid = (
                    snapshot.get("schema") == "CanonicalSnapshot@1"
                    and snapshot.get("project_id")
                    == self._manifest.project_id
                    and snapshot.get("version") == next_ref.version
                    and _version_from_dict(
                        snapshot.get("parent"),
                        field="snapshot parent",
                    )
                    == expected
                )
                event_valid = (
                    event_payload.get("schema") == "ProjectEvent@1"
                    and event_payload.get("decision") == "accepted"
                    and _version_from_dict(
                        event_payload.get("from"),
                        field="event from",
                    )
                    == expected
                    and _version_from_dict(
                        event_payload.get("to"),
                        field="event to",
                    )
                    == next_ref
                    and _record_from_dict(
                        event_payload.get("previous_event"),
                        project_id=self._manifest.project_id,
                        field="previous_event",
                    )
                    == current_event
                )
            else:
                state_sha256 = _semantic_state_sha256(
                    snapshot.get("state"),
                    field="replacement snapshot state",
                )
                next_ref = ProjectVersionRef(
                    self._manifest.project_id,
                    expected.version + 1,
                    state_sha256,
                )
                snapshot_valid = (
                    snapshot.get("schema") == "CanonicalSnapshot@2"
                    and snapshot.get("project_id")
                    == self._manifest.project_id
                    and snapshot.get("version") == next_ref.version
                    and snapshot.get("state_sha256") == state_sha256
                    and _version_from_dict(
                        snapshot.get("parent"),
                        field="snapshot parent",
                    )
                    == expected
                )
                event_valid = (
                    event_payload.get("schema") == "ProjectEvent@2"
                    and event_payload.get("decision") == "accepted"
                    and _version_from_dict(
                        event_payload.get("from"),
                        field="event from",
                    )
                    == expected
                    and _record_from_dict(
                        event_payload.get("from_snapshot"),
                        project_id=self._manifest.project_id,
                        field="event from_snapshot",
                    )
                    == current_snapshot
                    and _version_from_dict(
                        event_payload.get("to"),
                        field="event to",
                    )
                    == next_ref
                    and _record_from_dict(
                        event_payload.get("to_snapshot"),
                        project_id=self._manifest.project_id,
                        field="event to_snapshot",
                    )
                    == replacement
                    and _record_from_dict(
                        event_payload.get("previous_event"),
                        project_id=self._manifest.project_id,
                        field="previous_event",
                    )
                    == current_event
                )
            if not snapshot_valid:
                raise ProjectIntegrityError(
                    "replacement snapshot is not exact-base"
                )
            if not event_valid:
                raise PromotionAuthorityError(
                    "event does not prove an accepted exact-base transition"
                )
            _replace_atomic(
                self.layout.head,
                _json_bytes(self._head_payload(next_ref, replacement, event)),
            )
            return next_ref

    def lock_paths(self) -> tuple[Path, ...]:
        """The advisory lock files this repository's guarded reads acquire.

        ``export_transfer`` reads the closure under the HEAD and design locks,
        and acquiring one creates its file if it is absent. A caller that must
        not change the project at all can check these first and refuse.
        """

        return (self._head_lock.path, self._design_lock.path)

    def export_transfer(
        self, *, run_id: str | None = None,
        known_files: Mapping[str, str] | None = None,
        include_contents: bool = True,
        include_all_runs: bool = False,
    ) -> dict[str, Any]:
        """Read a retained design snapshot or one candidate and its dependencies.

        This is a transport value, not a new project format. All identities and
        file bytes are the existing P036 ones. Only receipt-named workspace
        artifacts travel; speculative scripts and runtime logs do not. Full
        archives also retain the current position and local command recovery.
        Archives may include all retained runs, including project documents,
        boards and unaccepted candidates; ordinary synchronization stays scoped.
        """
        if include_all_runs and run_id is not None:
            raise ValueError("all retained runs require a project snapshot")
        with self._lock, self._head_lock, self._design_lock:
            report = self.verify()
            branches = self.read_design_branches() if run_id is None else {}
            files: dict[str, tuple[str, int]] = {}
            contents: dict[str, str] = {}
            known = known_files or {}
            pending: list[tuple[str, str | None]] = []
            runs: set[str] = set()

            def add(path: str, digest: str | None = None) -> None:
                pending.append((_transfer_path(path), digest))

            def add_run(value: str) -> None:
                require_identifier(value, "transfer run_id")
                if value in runs:
                    return
                try:
                    run = self.load_run(value)
                except ProjectRepositoryError as exc:
                    raise ProjectIntegrityError(
                        f"TRANSFER_RUN_INVALID: runs/{value}/run.json: {exc}"
                    ) from exc
                self.load_version_state(run.base)
                runs.add(value)
                add(f"runs/{value}/run.json")
                for area in ("records", "reviews", "candidates", "branches"):
                    directory = self.layout.run(value).root / area
                    if not directory.exists():
                        continue
                    for path in sorted(directory.rglob("*.json")):
                        try:
                            _, digest = parse_record_file_name(path.name)
                        except ValueError:
                            continue
                        add(path.relative_to(self.layout.root).as_posix(), digest)

            def artifact(path: str, digest: str, current_run: str | None) -> None:
                # Native CAD receipts name a workspace-local export. P036
                # artifact refs instead carry a complete project-relative path.
                if path.startswith(
                    ("runs/", "objects/", "canonical/", "events/", "exports/")
                ):
                    add(path, digest)
                    return
                if current_run is None:
                    raise ProjectIntegrityError("TRANSFER_DEPENDENCY_MISSING: artifact has no run")
                name = PurePosixPath(path.replace("\\", "/")).name
                matches = [p for p in self.layout.run(current_run).workspaces.rglob(name)
                           if p.is_file() and _sha256(_read_bytes(p)) == digest]
                if not matches:
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {current_run}/{name}")
                add(sorted(matches)[0].relative_to(self.layout.root).as_posix(), digest)

            def inline_projection_source(value: Mapping[str, Any], current_run: str | None) -> bool:
                # Legacy Studio deltas retain an authored source inline, bound
                # to a synthetic RunRef. Only these two matching bindings name
                # no stored run; their nested evidence still has to travel.
                if (value.get("schema") != "StudioCandidateDelta@1"
                        or value.get("project_id") != self._manifest.project_id
                        or current_run is None or value.get("run_id") != current_run
                        or any(value.get(key) is not None for key in (
                            "source_stage_ref", "source_record_ref", "source_runner_ref", "source_model"))):
                    return False
                source, record = value.get("source_run_ref"), value.get("source_record")
                if (not isinstance(source, Mapping) or not isinstance(record, Mapping)
                        or source.get("project_id") != self._manifest.project_id
                        or source.get("run_id") != "studio-projection"
                        or record.get("schema") != "StateRecord@1"
                        or any(record.get(key) != source.get(key) for key in ("project_id", "run_id", "base"))):
                    return False
                root = self.layout.run("studio-projection").root
                if root.exists() or root.is_symlink():
                    return False
                try:
                    run = RunRef.from_dict(source)
                except (TypeError, ValueError) as exc:
                    raise ProjectIntegrityError("TRANSFER_RUN_INVALID: invalid inline projection binding") from exc
                # The synthetic run never existed, but its exact published
                # base must exist in this project's retained history.
                self.load_version_state(run.base)
                return True

            def references(value: Any, current_run: str | None, *, inline_projection: bool = False) -> None:
                if isinstance(value, Mapping):
                    # Uploaded source documents name their object by digest.
                    # Generated drawings instead link a receipt via revisionRef;
                    # the recursive project URI traversal retains its artifacts.
                    if value.get("schema") == "StudioSourceDocument@1":
                        if value.get("project_id") != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign document")
                        if value.get("revisionRef") is None:
                            digest = value["asset_sha256"]
                            artifact(f"objects/sha256/{digest[:2]}/{digest}", digest, current_run)
                    if "relative_path" in value and "sha256" in value:
                        if value.get("project_id", self._manifest.project_id) != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign artifact")
                        artifact(value["relative_path"], value["sha256"], current_run)
                    # An authored record can name a run without being bound to
                    # one. A retained RunRef always has a non-null base.
                    if {"run_id", "project_id", "base"}.issubset(value):
                        if value["project_id"] != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign run")
                        if value["base"] is not None and not inline_projection:
                            add_run(value["run_id"])
                    native = value.get("artifact_relative_path")
                    inspection = value.get("inspection")
                    if native and isinstance(inspection, Mapping) and inspection.get("file_sha256"):
                        artifact(native, inspection["file_sha256"], current_run)
                    inline_source = inline_projection_source(value, current_run)
                    for key, item in value.items():
                        evidence_list = key == "archflow:evidence" or (
                            key == "value" and value.get("key") == "archflow:evidence"
                        )
                        if evidence_list and isinstance(item, str):
                            # The CAD metadata contract serializes evidence as
                            # a comma-separated list in this specific field.
                            # Every member still takes the strict URI path check.
                            for source in item.split(","):
                                references(source, current_run)
                        else:
                            references(item, current_run, inline_projection=(
                                inline_source and key in ("source_run_ref", "source_record")
                            ))
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        references(item, current_run)
                elif isinstance(value, str) and value.startswith("project://"):
                    uri = urlsplit(value)
                    if uri.netloc != self._manifest.project_id or uri.query or uri.fragment:
                        raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign project reference")
                    path = _transfer_path(unquote(uri.path.lstrip("/")))
                    parts = PurePosixPath(path).parts
                    if parts[0] == "runs" and len(parts) >= 2:
                        add_run(parts[1])
                        if len(parts) == 2:
                            return
                    add(path)

            add("project.json")
            add("HEAD")
            for path in report.reachable_paths:
                if path.startswith(("canonical/", "events/")):
                    add(path)
            if run_id is None:
                if self.layout.design_branches.exists():
                    add("design/branches.json")
                references(branches, None)
                for path in ("input/runner/state-record.json", "input/runner/seats.json",
                             "input/runner/program-sheet.json"):
                    if (self.layout.root / path).is_file():
                        add(path)
                if include_all_runs:
                    if self.layout.working_draft.exists():
                        add("design/working.json")
                    for local in self.layout.runs.glob(f"*/recovery/{STUDIO_LOCAL_DRAFT}-*.json"):
                        _, digest = parse_record_file_name(local.name)
                        add(local.relative_to(self.layout.root).as_posix(), digest)
                    for listed in self.run_ids():
                        if (self.layout.runs / listed / "run.json").exists():
                            add_run(listed)
            else:
                add_run(run_id)
            while pending:
                path, expected_digest = pending.pop()
                if path in files:
                    if expected_digest is not None and files[path][0] != expected_digest:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
                    continue
                target = (self.layout.root / path).resolve()
                if not target.is_relative_to(self.layout.root) or not target.is_file():
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
                is_json = path.endswith(".json") or path == "HEAD"
                data = _read_bytes(target) if is_json else None
                if data is None:
                    with target.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    size = target.stat().st_size
                else:
                    digest, size = _sha256(data), len(data)
                if expected_digest is not None and digest != expected_digest:
                    raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
                files[path] = (digest, size)
                if include_contents and known.get(path) != digest:
                    if data is None:
                        data = _read_bytes(target)
                    if _sha256(data) != digest or len(data) != size:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path} changed during export")
                    contents[path] = base64.b64encode(data).decode("ascii")
                parts = PurePosixPath(path).parts
                current_run = parts[1] if parts[0] == "runs" else None
                if current_run is not None:
                    add_run(current_run)
                if is_json:
                    assert data is not None
                    references(_parse_json_document(data, path), current_run)
            return {
                "project_id": self._manifest.project_id,
                "format_version": self._manifest.format_version,
                "mode": "snapshot" if run_id is None else "candidate",
                "root_run_id": run_id, "head": report.head.to_dict(),
                "branches": branches, "run_ids": sorted(runs),
                "files": [{"path": path, "sha256": digest, "size": size}
                          for path, (digest, size) in sorted(files.items())],
                "contents": contents,
            }

    def read_transfer_file(self, path: str, sha256: str) -> bytes:
        """Read one retained file by identity without rescanning other binaries."""
        path = _transfer_path(path)
        target = (self.layout.root / path).resolve()
        if not target.is_relative_to(self.layout.root):
            raise ProjectIntegrityError("TRANSFER_PATH_INVALID: target escapes project root")
        parts = PurePosixPath(path).parts
        metadata = path in ("project.json", "HEAD", "design/branches.json", "design/working.json",
                            "input/runner/state-record.json", "input/runner/seats.json",
                            "input/runner/program-sheet.json")
        run_id = parts[1] if parts[0] == "runs" and len(parts) >= 3 else None
        run_manifest = run_id is not None and len(parts) == 3 and parts[2] == "run.json"
        record_area = (parts[0] in ("canonical", "events") and len(parts) == 2) or (
            run_id is not None and (
                (len(parts) == 4 and (parts[2] in ("records", "reviews", "candidates")
                                     or (parts[2] == "recovery" and parts[-1].startswith(f"{STUDIO_LOCAL_DRAFT}-"))))
                or (len(parts) == 6 and parts[2] == "branches" and parts[4] == "records")
            )
        )
        if run_manifest:
            self.load_run(run_id)
        elif record_area:
            try:
                _, named_digest = parse_record_file_name(parts[-1])
            except ValueError as exc:
                raise ProjectIntegrityError("TRANSFER_FILE_UNAVAILABLE: not a retained record") from exc
            if named_digest != sha256:
                raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: record filename")
        elif not metadata:
            workspace = run_id is not None and len(parts) >= 4 and parts[2] == "workspaces"
            object_file = parts == ("objects", "sha256", sha256[:2], sha256)
            # A project-owned export is served exactly like a run's workspace
            # file: only where a retained record names it, and only by digest.
            # Refusing it here while the closure lists it would break the very
            # archive the migration tells an operator to take first.
            exported = parts[0] == "exports" and len(parts) >= 2
            if not (workspace or object_file or exported) or not self._transfer_artifact_referenced(path, sha256, run_id):
                raise ProjectIntegrityError("TRANSFER_FILE_UNAVAILABLE: no retained artifact reference")
        data = _read_bytes(target)
        if _sha256(data) != sha256:
            raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: shared file changed")
        return data

    def _transfer_artifact_referenced(self, path: str, digest: str, run_id: str | None) -> bool:
        """Find an object's ref, or a retained receipt naming this artifact.

        A native CAD receipt names only a workspace-local export, so that loose
        filename match stays inside the run owning the bytes. A complete
        project-relative reference is already unambiguous and export follows it
        across runs, so one retained run may hold an artifact that only another
        retained run's receipt names, and a reference may point into the
        project's own ``exports`` root. Both forms still require the recorded
        digest, this project, and an already admissible workspace, export or
        object path; the owning run is searched first so the common read stops
        there.
        """
        def matches(value: Any, local: bool) -> bool:
            if isinstance(value, Mapping):
                if (value.get("schema") == "StudioSourceDocument@1"
                        and value.get("revisionRef") is None
                        and value.get("project_id") == self._manifest.project_id
                        and value.get("asset_sha256") == digest
                        and path == f"objects/sha256/{digest[:2]}/{digest}"):
                    return True
                relative = value.get("relative_path")
                if value.get("sha256") == digest and isinstance(relative, str):
                    if relative == path:
                        return value.get("project_id", self._manifest.project_id) == self._manifest.project_id
                    if local and not relative.startswith(("runs/", "objects/")):
                        if PurePosixPath(relative.replace("\\", "/")).name == PurePosixPath(path).name:
                            return True
                inspection = value.get("inspection")
                native = value.get("artifact_relative_path")
                if local and isinstance(inspection, Mapping) and isinstance(native, str):
                    if inspection.get("file_sha256") == digest and PurePosixPath(native.replace("\\", "/")).name == PurePosixPath(path).name:
                        return True
                return any(matches(item, local) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(matches(item, local) for item in value)
            return value == f"project://{self._manifest.project_id}/{path}"

        owner = self.layout.run(run_id).root if run_id else None
        roots = ([owner] if owner is not None else []) + [
            root for root in sorted(self.layout.runs.iterdir()) if root != owner
        ]
        for root in roots:
            for area in ("records", "reviews", "candidates", "branches"):
                if not (root / area).is_dir():
                    continue
                for record in (root / area).rglob("*.json"):
                    try:
                        _, record_digest = parse_record_file_name(record.name)
                    except ValueError:
                        continue
                    data = _read_bytes(record)
                    if _sha256(data) != record_digest:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {record.name}")
                    if matches(_parse_json_document(data, record.name), root == owner):
                        return True
        return False

    @classmethod
    def _validate_transfer(
        cls, transfer: Mapping[str, Any], *, expected_project_id: str,
        existing_root: Path | None = None,
    ) -> dict[str, bytes]:
        """Verify the complete received closure before any destination write."""
        keys = {"project_id", "format_version", "mode", "root_run_id", "head",
                "branches", "run_ids", "files", "contents"}
        if not isinstance(transfer, Mapping) or set(transfer) != keys:
            raise ProjectIntegrityError("TRANSFER_INVALID: invalid transport fields")
        if transfer["project_id"] != expected_project_id:
            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: transfer names another project")
        if transfer["mode"] not in ("snapshot", "candidate") or (
            (transfer["root_run_id"] is None) != (transfer["mode"] == "snapshot")
        ):
            raise ProjectIntegrityError("TRANSFER_INVALID: invalid transfer root")
        if not isinstance(transfer["contents"], Mapping) or not isinstance(transfer["files"], list):
            raise ProjectIntegrityError("TRANSFER_INVALID: expected files and contents")
        files: dict[str, bytes] = {}
        for row in transfer["files"]:
            if not isinstance(row, Mapping) or set(row) != {"path", "sha256", "size"}:
                raise ProjectIntegrityError("TRANSFER_INVALID: invalid file manifest")
            path = _transfer_path(row["path"])
            if path in files:
                raise ProjectIntegrityError("TRANSFER_INVALID: duplicate path")
            if path in transfer["contents"]:
                try:
                    data = base64.b64decode(transfer["contents"][path], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ProjectIntegrityError("TRANSFER_INVALID: invalid file bytes") from exc
            elif existing_root is not None:
                target = (existing_root / path).resolve()
                if not target.is_relative_to(existing_root.resolve()) or not target.is_file():
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
                data = _read_bytes(target)
            else:
                raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
            if type(row["size"]) is not int or len(data) != row["size"] or _sha256(data) != row["sha256"]:
                raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
            parts = PurePosixPath(path).parts
            # A run's workspace and the project's exports hold native exports,
            # and some of those are themselves JSON. They are artifacts, not
            # records: the sender only admits one a retained receipt names, and
            # ``read_transfer_file`` already serves it by area and digest. The
            # content-addressed record filename is required of records,
            # canonical state and events alone, so applying it here too would
            # refuse an archive this code wrote.
            workspace = parts[0] == "exports" or (
                parts[0] == "runs" and len(parts) >= 4 and parts[2] == "workspaces"
            )
            if path.endswith(".json") and not workspace and path not in (
                "project.json", "design/branches.json", "design/working.json", "input/runner/state-record.json",
                "input/runner/seats.json", "input/runner/program-sheet.json",
            ) and not (parts[0] == "runs" and len(parts) == 3 and parts[2] == "run.json"):
                try:
                    _, named_digest = parse_record_file_name(parts[-1])
                except ValueError as exc:
                    raise ProjectIntegrityError("TRANSFER_INVALID: non-record JSON") from exc
                if named_digest != row["sha256"]:
                    raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: record filename")
            if parts[0] == "objects" and parts != ("objects", "sha256", row["sha256"][:2], row["sha256"]):
                raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: object path")
            files[path] = data
        if set(transfer["contents"]) - set(files):
            raise ProjectIntegrityError("TRANSFER_INVALID: unlisted contents")
        # This disposable repository is owned by P036 too. Reopening it tests
        # the original format, event chain, Stage ancestry and all references;
        # no transport parser is allowed to replace those existing readers.
        with tempfile.TemporaryDirectory(prefix="archflow-transfer-") as temporary:
            root = Path(temporary)
            for path, data in files.items():
                _write_immutable(root / path, data)
            staged = cls.open(root)
            if staged.load_manifest().project_id != expected_project_id:
                raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: manifest")
            checked = staged.export_transfer(
                run_id=transfer["root_run_id"], include_contents=False,
                include_all_runs=transfer["mode"] == "snapshot",
            )
            for key in keys - {"contents", "files"}:
                if checked[key] != transfer[key]:
                    raise ProjectIntegrityError(f"TRANSFER_INVALID: {key} differs from retained content")
            if checked["files"] != sorted(transfer["files"], key=lambda row: row["path"]):
                raise ProjectIntegrityError("TRANSFER_DEPENDENCY_MISSING: transfer is not the complete retained closure")
        return files

    @classmethod
    def bootstrap_transfer(
        cls, root: Path, transfer: Mapping[str, Any], *, expected_project_id: str,
    ) -> FilesystemProjectRepository:
        """Install a trusted snapshot; retry only matching unfinished bytes."""
        if transfer.get("mode") != "snapshot":
            raise ProjectIntegrityError("TRANSFER_INVALID: bootstrap requires a snapshot")
        files = cls._validate_transfer(transfer, expected_project_id=expected_project_id)
        root = Path(root).resolve()
        directories = {parent.as_posix() for path in files
                       for parent in PurePosixPath(path).parents if parent != PurePosixPath(".")}
        project_directories = {"objects", "objects/sha256", "events", "canonical", "runs", "exports", "input"}
        directories.update(project_directories)

        def require_resume_target() -> None:
            if not root.exists():
                return
            if not root.is_dir():
                raise ProjectAlreadyExists("bootstrap destination is not a directory")
            if (root / "HEAD").exists():
                raise ProjectAlreadyExists("bootstrap destination already has a published position")
            for path in root.rglob("*"):
                relative = path.relative_to(root).as_posix()
                if relative in ("HEAD.lock", WRITER_LOCK):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ProjectAlreadyExists("bootstrap destination contains a redirected path")
                if path.is_dir() and relative in directories:
                    continue
                if not path.is_file() or relative not in files or _read_bytes(path) != files[relative]:
                    raise ProjectAlreadyExists("bootstrap destination contains unrelated or conflicting content")

        require_resume_target()
        with _writing(root), _project_lock(root), _HeadFileLock(root / "HEAD.lock"):
            require_resume_target()
            for directory in project_directories:
                _make_directory(root / directory)
            for path, data in files.items():
                if path != "HEAD":
                    _write_immutable(root / path, data)
            _write_immutable(root / "HEAD", files["HEAD"])
        return cls.open(root)

    @classmethod
    def migrate_project_format(
        cls,
        source_root: Path,
        target_root: Path,
    ) -> ProjectFormatMigration:
        """Write a format-2 copy of a format-1 project into an empty directory.

        The source is never written, not even its advisory lock files: it is
        copied into a disposable staging directory beside the target, and that
        copy is what is opened, planned and read. Every version's identity is
        computed before the first target byte is written; the envelope is then
        written by this build's own transition writer and each HEAD move goes
        through ``compare_and_swap``. Run manifests and the authored state
        record have their base restated by their owners; everything else is
        copied byte for byte and the project-version identities it still
        embeds are listed, not rewritten. If anything fails, the directory this
        created is removed, so a retry starts from the same empty target.
        """

        _load_version_ref_owners()
        source_root = _migration_source(source_root)
        target_root = Path(target_root).resolve(strict=False)
        inspection = inspect_project_format(source_root)
        if (
            inspection.status != PROJECT_FORMAT_SUPPORTED_LEGACY
            or inspection.format_version != LEGACY_FORMAT_VERSION
        ):
            raise ProjectIntegrityError(
                f"MIGRATION_NOT_APPLICABLE: {inspection.status}: {inspection.detail}"
            )
        project_id = inspection.project_id
        if project_id is None or target_root.name != project_id:
            raise ProjectIntegrityError(
                f"MIGRATION_TARGET_NAME: the target directory must be named {project_id!r}"
            )
        if target_root.exists() and (not target_root.is_dir() or any(target_root.iterdir())):
            raise ProjectIntegrityError(
                "MIGRATION_TARGET_USED: the target directory already contains files"
            )
        if _contains_path(source_root, target_root) or _contains_path(target_root, source_root):
            raise ProjectIntegrityError(
                "MIGRATION_TARGET_NESTED: source and target may not contain each other"
            )
        # A junction is not a symlink to ``Path.is_symlink``, and ``copytree``
        # would inline whatever it points at. This is the same pre-check the
        # archive restore makes about its own destination.
        for path in source_root.rglob("*"):
            if (
                path.is_symlink()
                or path.is_junction()
                or not path.resolve().is_relative_to(source_root)
            ):
                raise ProjectIntegrityError(
                    f"MIGRATION_SOURCE_REDIRECTED: "
                    f"{path.relative_to(source_root).as_posix()}"
                )

        # Finding 7: an empty directory the caller made is kept, but every
        # file this writes into it is removed again if the migration fails.
        target_existed = target_root.exists()
        created: Path | None = None
        # Staging lives beside the target, on the target's volume: the copy is
        # the migration's working set, not something to leave in the system
        # temporary directory, and a hard link in the target cannot cross a
        # volume boundary that staging silently introduced.
        _make_directory(target_root.parent)
        try:
            # ``lease`` holds the target's writer lease from its first byte until
            # this ends, and gives it back before a failure empties the target.
            with tempfile.TemporaryDirectory(
                prefix=".archflow-migrate-", dir=target_root.parent,
            ) as temporary, ExitStack() as lease:
                staging = Path(temporary) / project_id
                shutil.copytree(
                    source_root, staging, symlinks=False,
                    ignore=shutil.ignore_patterns("*.tmp"),
                )
                _note_write(staging)
                if _read_bytes(source_root / "HEAD") != _read_bytes(staging / "HEAD"):
                    raise ProjectIntegrityError(
                        "MIGRATION_SOURCE_MOVED: the source published a new version "
                        "while it was being copied; nothing was written"
                    )
                source_head_sha256 = _sha256(_read_bytes(staging / "HEAD"))
                legacy = cls.open(staging)
                for lock in legacy.lock_paths():
                    _make_directory(lock.parent)
                    lock.touch(exist_ok=True)
                    _note_write(lock)
                plan = plan_project_migration(staging)
                # An undeclared identity is re-detected below, per record and
                # per pointer; every other refusal is final here.
                refusals = [
                    blocker for blocker in plan.blockers
                    if UNDECLARED_IDENTITY_BLOCKER not in blocker
                ]
                if not plan.planned or refusals:
                    raise ProjectIntegrityError(
                        "MIGRATION_BLOCKED: " + "; ".join(refusals or plan.blockers)
                    )
                if not plan.migration_required:
                    raise ProjectIntegrityError(
                        "MIGRATION_NOT_REQUIRED: the project is already at the target format"
                    )
                if MIGRATION_RUN_ID in plan.run_ids:
                    raise ProjectIntegrityError(
                        f"MIGRATION_RECEIPT_RUN_TAKEN: the project already retains a run "
                        f"named {MIGRATION_RUN_ID!r}, which is where the migration files "
                        f"its own receipt"
                    )

                lineage = legacy._legacy_lineage()
                project_files = {
                    item.relative_to(staging).as_posix(): item
                    for item in staging.rglob("*") if item.is_file()
                }
                # Everything the envelope needs is computed before the target
                # exists: a state that cannot be digested must not leave a
                # half-written directory that inspects as current at v0.
                semantic: list[str] = []
                for ref, _, state in lineage:
                    _require_semantic_state_identity(
                        state, project_id=project_id, version=ref.version,
                        field=f"migrated state v{ref.version}",
                    )
                    semantic.append(_semantic_state_sha256(
                        state, field=f"migrated state v{ref.version}",
                    ))

                mapping = {
                    ref.require_digest(): digest
                    for (ref, _, _), digest in zip(lineage, semantic)
                }
                legacy_digests = {
                    ref.require_digest(): ref.version for ref, _, _ in lineage
                }
                # Records are restated and moved to a fixed point before a
                # single target byte exists: an event names a decision receipt,
                # so the envelope cannot be written until the receipt's final
                # name is known, and an undeclared identity must refuse here.
                payloads: dict[str, dict[str, Any]] = {}
                for relative, item in sorted(project_files.items()):
                    if (
                        relative in ("project.json", "HEAD")
                        or relative.startswith(("canonical/", "events/"))
                        or not relative.endswith(".json")
                        or _retained_category(relative)[0] == "artifact"
                        or _TEMPORARY_NAME.match(PurePosixPath(relative).name)
                    ):
                        continue
                    # A retained document that does not decode has already
                    # failed the plan's own read of this closure above.
                    payloads[relative] = _parse_json_document(
                        _read_bytes(item), relative,
                    )
                cascade = _cascade_records(
                    payloads, project_id=project_id,
                    mapping=mapping, legacy_digests=legacy_digests,
                    moved=_canonical_snapshot_moves(lineage, semantic, project_id),
                )
                if cascade.blockers:
                    raise ProjectIntegrityError(
                        "MIGRATION_BLOCKED: " + "; ".join(cascade.blockers)
                    )
                authored = payloads.get(AUTHORED_RECORD_PATH)
                if authored is not None:
                    from archflow.project.version_refs import locations as _at
                    stranded = [
                        digest for _, _, digest in _at(authored)
                        if digest not in mapping and digest not in set(mapping.values())
                    ]
                    if stranded:
                        raise ProjectIntegrityError(
                            "MIGRATION_AUTHORED_BASE_UNKNOWN: the authored state record "
                            "is based on a version this project does not publish, so no "
                            "owner can restate it"
                        )

                # ``initialize`` writes the manifest, the first snapshot and
                # HEAD before it verifies its own work, so the target is this
                # migration's from before that call, not after it returns.
                created = target_root
                lease.enter_context(_writing(target_root))
                target = cls.initialize(
                    target_root, project_id=project_id, initial_state=lineage[0][2],
                )
                versions: list[tuple[int, str, str]] = [
                    (0, lineage[0][0].require_digest(), target.read_head().require_digest())
                ]
                with target._lock, target._head_lock:
                    for (legacy_ref, event, state), digest in zip(lineage[1:], semantic[1:]):
                        previous, previous_snapshot, previous_event = (
                            target._read_head_document()
                        )
                        carried = {
                            key: value for key, value in event.items()
                            if key not in _MIGRATED_EVENT_KEYS
                        }
                        prepared = target._write_transition(
                            previous=previous,
                            previous_snapshot=previous_snapshot,
                            previous_event=previous_event,
                            state=state,
                            event_type=event.get("event_type"),
                            decision=event.get("decision"),
                            run_id=event.get("run_id"),
                            decision_receipt=_rewrite_references(
                                event.get("decision_receipt"), project_id,
                                cascade.renames, {},
                            ),
                            carried=carried,
                        )
                        published = target.compare_and_swap(
                            expected=previous,
                            event=prepared.event,
                            replacement=prepared.replacement,
                        )
                        if published.require_digest() != digest:
                            raise ProjectIntegrityError(
                                f"MIGRATION_VERIFY: published v{published.version} does "
                                f"not carry the digest its state was validated with"
                            )
                        versions.append(
                            (legacy_ref.version, legacy_ref.require_digest(), digest)
                        )

                report = _copy_retained_closure(
                    staging, target_root, legacy, mapping, legacy_digests, cascade,
                )

                migrated = cls.open(target_root)
                migrated.verify()
                closure = migrated.export_transfer(
                    include_contents=False, include_all_runs=True,
                )
                if closure["format_version"] != CURRENT_FORMAT_VERSION:
                    raise ProjectIntegrityError(
                        "MIGRATION_VERIFY: the migrated closure does not report the target format"
                    )
                if plan_project_migration(target_root).migration_required:
                    raise ProjectIntegrityError(
                        "MIGRATION_VERIFY: the migrated project still requires migration"
                    )
                # The scan is not decoration: a legacy identity anywhere in the
                # migrated closure means the cascade missed a location.
                if report.embedded:
                    path, pointer, _, _, _ = report.embedded[0]
                    raise ProjectIntegrityError(
                        f"MIGRATION_VERIFY: {len(report.embedded)} retained location(s) "
                        f"still name a legacy version, the first at {path}{pointer}"
                    )
                _require_consistent_migration(
                    migrated, closure, mapping, cascade.moved_digests,
                )
                result = ProjectFormatMigration(
                    source_root=source_root,
                    target_root=target_root,
                    project_id=project_id,
                    source_format_version=LEGACY_FORMAT_VERSION,
                    target_format_version=CURRENT_FORMAT_VERSION,
                    source_head_sha256=source_head_sha256,
                    versions=tuple(versions),
                    rewritten=tuple(report.rewritten),
                    preserved_files=report.preserved_files,
                    orphans=tuple(report.orphans),
                    orphan_legacy_references=tuple(report.orphan_legacy),
                    unscanned_binaries=tuple(report.unscanned_binaries),
                    embedded_legacy_references=tuple(report.embedded),
                    receipt=None,
                )
                # Last, and only once the migrated project verifies: the
                # migration files its own account as a retained record, through
                # the repository's own writer, inside the closure it produced.
                return replace(result, receipt=migrated._file_migration_receipt(result))
        except BaseException as exc:
            if created is not None and created.exists():
                if target_existed:
                    # The directory was the caller's; only its contents are ours.
                    for item in created.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                        _note_write(item)
                else:
                    shutil.rmtree(created, ignore_errors=True)
                    _note_write(created)
            if isinstance(exc, ProjectRepositoryError):
                raise
            if isinstance(exc, (OSError, ValueError, KeyError, TypeError)):
                raise ProjectIntegrityError(f"MIGRATION_FAILED: {exc}") from exc
            raise

    def _legacy_lineage(self) -> list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]]:
        """Published history oldest-first: (version, its event, its state).

        One backward walk reads each snapshot exactly once, so a long history
        costs one pass rather than one full replay per version.
        """

        current, snapshot_ref, event_ref = self._read_head_document()
        walked: list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]] = []
        while True:
            event = self.load_json(event_ref)
            snapshot = self._verify_snapshot(snapshot_ref, current)
            state = snapshot.get("state")
            if not isinstance(state, dict):
                raise ProjectIntegrityError("canonical snapshot state is not an object")
            walked.append((current, event, state))
            parent = event.get("from")
            if parent is None:
                break
            current = _version_from_dict(parent, field="event from")
            snapshot_ref = self._canonical_ref_for_version(current)
            event_ref = _record_from_dict(
                event.get("previous_event"),
                project_id=self._manifest.project_id,
                field="previous_event",
            )
        walked.reverse()
        if [ref.version for ref, _, _ in walked] != list(range(len(walked))):
            raise ProjectIntegrityError(
                "MIGRATION_LINEAGE: published versions are not contiguous from 0"
            )
        return walked

    def _file_migration_receipt(self, result: ProjectFormatMigration) -> ProjectRecordRef:
        """Retain the migration's own account inside the project it produced."""

        run = self.create_run(MIGRATION_RUN_ID)
        return self.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=MIGRATION_RUN_ID,
            ),
            record_kind=PROJECT_FORMAT_MIGRATION,
            payload=result.to_dict(),
        )

    @_writes
    def import_candidate_transfer(self, transfer: Mapping[str, Any]) -> None:
        """Import immutable candidate evidence; never accept, branch or issue."""
        if transfer.get("mode") != "candidate":
            raise ProjectIntegrityError("TRANSFER_INVALID: candidate upload requires a candidate transfer")
        files = self._validate_transfer(transfer, expected_project_id=self._manifest.project_id,
                                        existing_root=self.layout.root)
        with self._lock, self._head_lock, self._design_lock:
            if self.read_head().to_dict() != transfer["head"]:
                raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: synchronize the published base before upload")
            self._install_transfer_files(files)

    @_writes
    def pull_transfer(
        self, transfer: Mapping[str, Any], *, expected_head: ProjectVersionRef,
        expected_branches: Mapping[str, Any],
    ) -> None:
        """Adopt shared Stage positions, preserving local runs and authored WIP.

        Published HEAD migration is deliberately not this operation. A changed
        published base requires an explicit later synchronization capability.
        """
        if transfer.get("mode") != "snapshot":
            raise ProjectIntegrityError("TRANSFER_INVALID: pull requires a shared snapshot")
        files = self._validate_transfer(transfer, expected_project_id=self._manifest.project_id,
                                        existing_root=self.layout.root)
        with self._lock, self._head_lock, self._design_lock:
            if self.read_head() != expected_head or transfer["head"] != expected_head.to_dict():
                raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: published base synchronization is not supported by Stage pull")
            if self.read_design_branches() != expected_branches:
                raise StaleDesignBranch("local design branches changed while the shared snapshot was downloaded")
            for branch_id, previous in expected_branches.items():
                replacement = transfer["branches"].get(branch_id)
                if replacement is None or any(previous[key] != replacement[key] for key in ("fork_stage", "parent_branch")):
                    raise StaleDesignBranch("shared snapshot cannot remove or refork existing local history")
                stage = replacement["head_stage"]
                while stage != previous["head_stage"]:
                    payload = _parse_json_document(files[stage["relative_path"]], "pulled design Stage")
                    stage = payload.get("parent_stage")
                    if stage is None:
                        raise StaleDesignBranch("shared snapshot does not continue the current local design head")
            self._install_transfer_files(files)
            if transfer["branches"]:
                _replace_atomic(self.layout.design_branches, files["design/branches.json"])

    def _install_transfer_files(self, files: Mapping[str, bytes]) -> None:
        writes: list[tuple[str, Path, bytes]] = []
        for path, data in files.items():
            if path in ("design/branches.json", "design/working.json") or path.startswith("input/"):
                continue
            target = (self.layout.root / path).resolve()
            if not target.is_relative_to(self.layout.root):
                raise ProjectIntegrityError("TRANSFER_PATH_INVALID: destination escapes project root")
            if target.exists() and _read_bytes(target) != data:
                raise ProjectIntegrityError(f"TRANSFER_CONTENT_CONFLICT: {path}")
            if path in ("HEAD", "project.json") or path.startswith(("canonical/", "events/")):
                if not target.exists():
                    raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: retained published history differs")
                continue
            if not target.exists():
                writes.append((path, target, data))
        # A run the transfer brings that is not here yet arrives whole
        # (``_publish_run``), at its place in the order; a run already here
        # gains its new files one immutable file at a time.
        arriving: dict[str, list[tuple[str, bytes]]] = {}
        for path, _, data in writes:
            parts = PurePosixPath(path).parts
            if parts[0] == "runs" and len(parts) > 2 and (
                parts[1] in arriving or not os.path.lexists(self.layout.runs / parts[1])
            ):
                arriving.setdefault(parts[1], []).append(("/".join(parts[2:]), data))
        installed: set[str] = set()
        for path, target, data in writes:
            parts = PurePosixPath(path).parts
            run_id = parts[1] if parts[0] == "runs" and len(parts) > 2 else None
            if run_id not in arriving:
                _write_immutable(target, data)
                continue
            if run_id in installed:
                continue
            installed.add(run_id)
            root = self.layout.runs / run_id
            if not self._publish_run(root, arriving[run_id]):
                for relative, content in arriving[run_id]:
                    _write_immutable(root.joinpath(*PurePosixPath(relative).parts), content)

    @_reads_fresh
    def verify(self) -> RecoveryReport:
        self.load_manifest()
        head, snapshot, event = self._read_head_document()
        reachable = {
            "project.json",
            "HEAD",
            snapshot.relative_path,
            event.relative_path,
        }
        expected = head
        expected_snapshot = snapshot
        current_event = event
        while True:
            snapshot_payload = self._verify_snapshot(
                expected_snapshot,
                expected,
            )
            event_payload = self.load_json(current_event)
            to_ref = _version_from_dict(event_payload.get("to"), field="event to")
            if to_ref != expected:
                raise ProjectIntegrityError("event chain does not reach HEAD")
            if self._manifest.format_version == CURRENT_FORMAT_VERSION:
                if event_payload.get("schema") != "ProjectEvent@2":
                    raise ProjectIntegrityError(
                        "format-version-2 event schema drifted"
                    )
                to_snapshot = _record_from_dict(
                    event_payload.get("to_snapshot"),
                    project_id=self._manifest.project_id,
                    field="event to_snapshot",
                )
                if to_snapshot != expected_snapshot:
                    raise ProjectIntegrityError(
                        "event does not name its resulting snapshot"
                    )
            elif event_payload.get("schema") != "ProjectEvent@1":
                raise ProjectIntegrityError(
                    "format-version-1 event schema drifted"
                )
            previous = event_payload.get("previous_event")
            parent = event_payload.get("from")
            if previous is None:
                from_snapshot = event_payload.get("from_snapshot")
                if (
                    parent is not None
                    or expected.version != 0
                    or (
                        self._manifest.format_version
                        == CURRENT_FORMAT_VERSION
                        and from_snapshot is not None
                    )
                    or snapshot_payload.get("parent") is not None
                ):
                    raise ProjectIntegrityError("event chain terminates incorrectly")
                break
            previous_ref = _record_from_dict(
                previous,
                project_id=self._manifest.project_id,
                field="previous_event",
            )
            parent_ref = _version_from_dict(parent, field="event from")
            reachable.add(previous_ref.relative_path)
            if self._manifest.format_version == CURRENT_FORMAT_VERSION:
                parent_snapshot = _record_from_dict(
                    event_payload.get("from_snapshot"),
                    project_id=self._manifest.project_id,
                    field="event from_snapshot",
                )
                if _version_from_dict(
                    snapshot_payload.get("parent"),
                    field="snapshot parent",
                ) != parent_ref:
                    raise ProjectIntegrityError(
                        "snapshot parent disagrees with event"
                    )
            else:
                parent_snapshot = self._canonical_ref_for_version(
                    parent_ref
                )
            self._verify_snapshot(parent_snapshot, parent_ref)
            reachable.add(parent_snapshot.relative_path)
            expected = parent_ref
            expected_snapshot = parent_snapshot
            current_event = previous_ref

        design_branches = self.read_design_branches()
        if design_branches:
            reachable.add(self.layout.design_branches.relative_to(self.layout.root).as_posix())
        for branch in design_branches.values():
            for field in ("fork_stage", "head_stage"):
                stage_ref: ProjectRecordRef | None = ProjectRecordRef.from_dict(branch[field])
                seen: set[str] = set()
                while stage_ref is not None:
                    if stage_ref.relative_path in seen:
                        raise ProjectIntegrityError("design history contains a cycle")
                    seen.add(stage_ref.relative_path)
                    stage = self._require_design_stage(stage_ref)
                    reachable.add(stage_ref.relative_path)
                    parent = stage.get("parent_stage")
                    stage_ref = None if parent is None else ProjectRecordRef.from_dict(parent, "parent_stage")
        working, working_revision = self.read_working_draft()
        if working_revision is not None:
            reachable.add("design/working.json")
            for run_id in working["runs"]:
                self.load_run(run_id)
                reachable.add(f"runs/{run_id}/run.json")
            if working["localDraftRef"] is not None:
                ref = ProjectRecordRef.from_dict(working["localDraftRef"])
                local = self.load_json(ref)
                if local.get("schema") != "StudioLocalDraft@1" or local.get("projectId") != self.layout.project_id:
                    raise ProjectIntegrityError("working recovery binding is invalid")
                self._working_time(local["updatedAt"])
                source = local["draft"]["source"]
                if source.get("projectId") != self.layout.project_id:
                    raise ProjectIntegrityError("working recovery source belongs to another project")
                if source.get("sourceRunId") is not None:
                    self.load_run(source["sourceRunId"])
                reachable.add(ref.relative_path)
        retained = {
            path.relative_to(self.layout.root).as_posix()
            for base in (self.layout.events, self.layout.canonical)
            if base.exists()
            for path in base.rglob("*.json")
            if path.is_file()
        }
        retained.update(
            path.relative_to(self.layout.root).as_posix()
            for path in self.layout.runs.glob(f"*/reviews/{DESIGN_STAGE}-*.json")
            if path.is_file()
        )
        return RecoveryReport(
            head=head,
            reachable_paths=tuple(sorted(reachable)),
            orphan_paths=tuple(sorted(retained - reachable)),
        )

    def _read_head_document(
        self,
    ) -> tuple[ProjectVersionRef, ProjectRecordRef, ProjectRecordRef]:
        # HEAD is the one mutable document: a concurrent compare_and_swap may
        # be replacing it right now, so the read must tolerate the transient
        # Windows sharing violation instead of reporting corruption.
        payload = _read_shared_json(self.layout.head)
        if set(payload) != {
            "schema",
            "project_id",
            "current",
            "snapshot",
            "event",
        }:
            raise ProjectIntegrityError("HEAD schema drifted")
        expected_schema = (
            "ProjectHead@1"
            if self._manifest.format_version == LEGACY_FORMAT_VERSION
            else "ProjectHead@2"
        )
        if (
            payload.get("schema") != expected_schema
            or payload.get("project_id") != self._manifest.project_id
        ):
            raise ProjectIntegrityError("HEAD belongs to another project or schema")
        current = _version_from_dict(payload["current"], field="HEAD current")
        snapshot = _record_from_dict(
            payload["snapshot"],
            project_id=self._manifest.project_id,
            field="HEAD snapshot",
        )
        event = _record_from_dict(
            payload["event"],
            project_id=self._manifest.project_id,
            field="HEAD event",
        )
        self._require_area(snapshot, "canonical/")
        self._require_area(event, "events/")
        self._verify_snapshot(snapshot, current)
        self.load_json(event)
        return current, snapshot, event

    def _head_payload(
        self,
        current: ProjectVersionRef,
        snapshot: ProjectRecordRef,
        event: ProjectRecordRef,
    ) -> dict[str, Any]:
        # ``ProjectHead@1``/``@2`` keep their names: every retained project
        # document on disk declares one of them and readers match the literal
        # (ADR-004). The design this points at is the *published* one, and
        # putting it there is an *issue* (ADR-007); the schema string is not
        # part of that vocabulary.
        return {
            "schema": (
                "ProjectHead@1"
                if self._manifest.format_version == LEGACY_FORMAT_VERSION
                else "ProjectHead@2"
            ),
            "project_id": self._manifest.project_id,
            "current": current.to_dict(),
            "snapshot": _record_dict(snapshot),
            "event": _record_dict(event),
        }

    def _verify_snapshot(
        self,
        snapshot: ProjectRecordRef,
        expected: ProjectVersionRef,
    ) -> dict[str, Any]:
        self._require_area(snapshot, "canonical/")
        payload = self.load_json(snapshot)
        if (
            payload.get("project_id") != self._manifest.project_id
            or payload.get("version") != expected.version
            or not isinstance(payload.get("state"), Mapping)
        ):
            raise ProjectIntegrityError("snapshot metadata disagrees")
        if self._manifest.format_version == LEGACY_FORMAT_VERSION:
            if (
                payload.get("schema") != "CanonicalSnapshot@1"
                or expected.require_digest() != snapshot.sha256
            ):
                raise ProjectIntegrityError(
                    "legacy snapshot identity disagrees"
                )
            return payload
        if payload.get("schema") != "CanonicalSnapshot@2":
            raise ProjectIntegrityError(
                "format-version-2 snapshot schema drifted"
            )
        state_sha256 = _semantic_state_sha256(
            payload["state"],
            field="snapshot state",
        )
        _require_semantic_state_identity(
            payload["state"],
            project_id=self._manifest.project_id,
            version=expected.version,
            field="snapshot state",
        )
        if (
            payload.get("state_sha256") != state_sha256
            or expected.require_digest() != state_sha256
        ):
            raise ProjectIntegrityError(
                "snapshot semantic state digest disagrees"
            )
        return payload

    def _destination_directory(
        self,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> Path:
        if not isinstance(destination, PersistenceDestination):
            raise TypeError("destination must be a PersistenceDestination")
        if destination.run_id is not None and destination.run_id != run.run_id:
            raise ValueError("destination belongs to another run")
        run_layout = self.layout.run(run.run_id)
        areas = {
            PersistenceArea.INPUT: self.layout.inputs,
            PersistenceArea.OBJECT: self.layout.objects,
            PersistenceArea.EVENT: self.layout.events,
            PersistenceArea.CANONICAL: self.layout.canonical,
            PersistenceArea.RUN_RECORD: run_layout.records,
            PersistenceArea.RUN_CANDIDATE: run_layout.candidates,
            PersistenceArea.RUN_REVIEW: run_layout.reviews,
            PersistenceArea.RUN_WORKSPACE: run_layout.workspaces,
            PersistenceArea.RUN_RECOVERY: run_layout.recovery,
            PersistenceArea.EXPORT: self.layout.exports,
        }
        if destination.area is PersistenceArea.RUN_BRANCH:
            if destination.branch_id is None:
                raise ValueError("run branch destination lacks branch_id")
            return run_layout.branches / destination.branch_id / "records"
        return areas[destination.area]

    def _put_internal_json(
        self,
        directory: Path,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef:
        require_identifier(record_kind, "record_kind")
        data = _json_bytes(payload)
        digest = _sha256(data)
        path = directory / record_file_name(record_kind, digest)
        _write_immutable(path, data)
        return self._record_ref(path, digest, "application/json")

    def _record_ref(
        self,
        path: Path,
        digest: str,
        media_type: str,
    ) -> ProjectRecordRef:
        relative = path.relative_to(self.layout.root).as_posix()
        return ProjectRecordRef(
            project_id=self._manifest.project_id,
            relative_path=relative,
            sha256=digest,
            media_type=media_type,
        )

    def _canonical_ref_for_version(
        self,
        ref: ProjectVersionRef,
    ) -> ProjectRecordRef:
        if self._manifest.format_version != LEGACY_FORMAT_VERSION:
            raise ProjectIntegrityError(
                "current snapshots require explicit record references"
            )
        digest = ref.require_digest()
        return ProjectRecordRef(
            project_id=self._manifest.project_id,
            relative_path=(
                f"canonical/state-v{ref.version:06d}-{digest}.json"
            ),
            sha256=digest,
        )

    def _require_record(self, ref: ProjectRecordRef) -> None:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be a ProjectRecordRef")
        if ref.project_id != self._manifest.project_id:
            raise ValueError("record belongs to another project")

    def _require_area(self, ref: ProjectRecordRef, prefix: str) -> None:
        self._require_record(ref)
        if not ref.relative_path.startswith(prefix):
            raise ProjectIntegrityError(
                f"record must belong to project area {prefix.rstrip('/')}"
            )

    def _require_project_version(
        self,
        ref: ProjectVersionRef,
        *,
        durable: bool,
    ) -> None:
        if not isinstance(ref, ProjectVersionRef):
            raise TypeError("ref must be a ProjectVersionRef")
        if ref.project_id != self._manifest.project_id:
            raise ValueError("version belongs to another project")
        if durable:
            ref.require_digest()

    def _run_manifest(self, run_id: str) -> dict[str, Any]:
        """One run's ``run.json`` payload, read again only when the file changed.

        The payload may be the one ``_LISTINGS`` keeps: it is only read here,
        never handed out or changed.
        """

        path = self.layout.run(run_id).manifest
        key = (self._root_key, _listing_place(f"runs/{run_id}/run.json"), "run.json", run_id)
        scanned_at = time.time_ns()
        stat = _stamp(path)
        stamp = None if stat is None else (stat.st_size, stat.st_mtime_ns)
        if stamp is not None:
            kept = _remembered_listing(key, stamp)
            if kept is not None:
                return kept
        payload = _read_json(path)
        if stat is not None:
            _keep_listing(key, stamp, stat.st_mtime_ns, scanned_at, payload)
        return payload

    def _validate_run(self, run: RunRef, payload: Mapping[str, Any] | None = None) -> None:
        """Require ``run`` to be exactly what its manifest says; ``payload`` is that manifest when just read."""

        if not isinstance(run, RunRef):
            raise TypeError("run must be a RunRef")
        if run.project_id != self._manifest.project_id:
            raise ValueError("run belongs to another project")
        if payload is None:
            payload = self._run_manifest(run.run_id)
        expected = {
            "schema": "ProjectRun@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": run.base.to_dict(),
        }
        if payload != expected:
            raise ProjectIntegrityError("run manifest changed or base drifted")


# ---- read-only project-format inspection and dry-run migration planning
#
# ``project.json.format_version`` is the one global project-format authority;
# each retained record keeps its own ``schema`` where that record is (ADR-004).
# Both entry points are read-only and neither interprets a record payload
# beyond the ``schema`` literal it declares: what a migration must do with a
# record's references is that record's typed owner's answer, not a parser's.


def inspect_project_format(root: Path) -> ProjectFormatInspection:
    """Classify a directory's declared project format without opening it.

    ``current`` and ``supported_legacy`` are the formats this build reads;
    ``too_new`` fails closed with what this build supports, and ``invalid``
    names a directory whose ``project.json`` is missing or drifted. Only
    ``project.json`` is read, and no file is created or changed.
    """

    resolved = Path(root).resolve(strict=False)

    def answer(
        status: str,
        version: int | None,
        project_id: str | None,
        detail: str,
    ) -> ProjectFormatInspection:
        return ProjectFormatInspection(
            root=resolved,
            status=status,
            format_version=version,
            project_id=project_id,
            detail=detail,
        )

    try:
        payload = _read_json(resolved / "project.json")
    except ProjectIntegrityError as exc:
        return answer(
            PROJECT_FORMAT_INVALID, None, None,
            f"project.json is missing or unreadable: {exc}",
        )
    try:
        manifest = ProjectManifest.from_dict(payload)
    except (ProjectManifestError, TypeError, ValueError) as exc:
        return answer(
            PROJECT_FORMAT_INVALID, None, None,
            f"project.json is not a valid project manifest: {exc}",
        )
    supported = ", ".join(str(version) for version in SUPPORTED_FORMAT_VERSIONS)
    version = manifest.format_version
    if version == CURRENT_FORMAT_VERSION:
        return answer(
            PROJECT_FORMAT_CURRENT, version, manifest.project_id,
            f"project format {version} is current",
        )
    if version in SUPPORTED_FORMAT_VERSIONS:
        return answer(
            PROJECT_FORMAT_SUPPORTED_LEGACY, version, manifest.project_id,
            f"project format {version} is supported legacy; this build reads it "
            f"unchanged and never rewrites it in place",
        )
    if version > CURRENT_FORMAT_VERSION:
        return answer(
            PROJECT_FORMAT_TOO_NEW, version, manifest.project_id,
            f"project format {version} was written by a newer build; this build "
            f"supports {supported}. Open it with the build that wrote it; a "
            f"project format is never downgraded",
        )
    return answer(
        PROJECT_FORMAT_INVALID, version, manifest.project_id,
        f"project format {version} is not one of {supported}",
    )


def _retained_category(path: str) -> tuple[str, str, bool | None]:
    """Which authority governs one retained path, and its kind and registration.

    Artifact bytes are identified by digest and carry no project schema, so the
    planner never opens them; every other retained file is JSON whose declared
    ``schema`` the inventory reports.
    """

    if path in ("project.json", "HEAD"):
        return "envelope", path, None
    if path in ("design/branches.json", "design/working.json"):
        return "design", path, None
    if path.startswith("input/"):
        return "authored_input", path, None
    parts = PurePosixPath(path).parts
    if parts[0] == "canonical":
        return "envelope", "canonical/snapshot", None
    if parts[0] == "events":
        return "envelope", "events/event", None
    if parts[0] == "objects":
        return "artifact", "objects/sha256", None
    if parts[0] == "runs" and len(parts) >= 3:
        if len(parts) == 3 and parts[2] == "run.json":
            return "run_manifest", "runs/run.json", None
        if parts[2] == "workspaces":
            return "artifact", "runs/workspaces", None
        try:
            kind, _ = parse_record_file_name(parts[-1])
        except ValueError:
            return "artifact", f"runs/{parts[2]}", None
        return "record", kind, is_registered(kind)
    return "artifact", parts[0], None


def plan_project_migration(
    root: Path,
    *,
    target_format_version: int = CURRENT_FORMAT_VERSION,
) -> ProjectMigrationPlan:
    """Report what migrating one retained project would require. Writes nothing.

    The project is opened and verified with the reader its own format already
    has, and the inventory is the complete retained closure the existing
    transfer export walks, including every run, authored input and source
    artifact. The inventory reports each retained kind, its declared schema and
    whether the record-kind table still holds it; it does not parse payloads.
    Records the migration does not own are preserved and listed; blockers
    remain only for a closure this build cannot read completely.

    Reading that closure goes through the repository's own guarded export, which
    acquires the advisory HEAD and design locks; acquiring one creates its file.
    A project missing those lock files therefore cannot be inventoried without
    changing it, and this refuses instead, naming the exact paths.
    """

    _load_version_ref_owners()
    supported = ", ".join(str(version) for version in SUPPORTED_FORMAT_VERSIONS)
    if target_format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(
            f"target project format {target_format_version} is not one of {supported}"
        )
    inspection = inspect_project_format(root)

    def refused(*blockers: str) -> ProjectMigrationPlan:
        return ProjectMigrationPlan(
            inspection=inspection,
            target_format_version=target_format_version,
            planned=False,
            migration_required=False,
            project_id=inspection.project_id,
            head=None,
            run_ids=(),
            inventory=(),
            retained_files=0,
            retained_bytes=0,
            orphan_paths=(),
            required_transformations=(),
            preserved=(),
            blockers=blockers,
        )

    if inspection.status in (PROJECT_FORMAT_INVALID, PROJECT_FORMAT_TOO_NEW):
        return refused(inspection.detail)
    source = inspection.format_version
    if source is None:  # pragma: no cover - a supported status carries its version
        return refused(inspection.detail)
    if source > target_format_version:
        return refused(
            f"a project format migration is forward-only; this project is format "
            f"{source} and the requested target is {target_format_version}"
        )

    try:
        repository = FilesystemProjectRepository.open(inspection.root)
    except ProjectRepositoryError as exc:
        return refused(
            f"the project does not open and verify through its own format-{source} "
            f"reader, so its retained closure cannot be planned: {exc}"
        )
    absent = [path for path in repository.lock_paths() if not path.exists()]
    if absent:
        return refused(
            "the retained closure is read through the repository's guarded export, "
            "which acquires the advisory lock(s) "
            + ", ".join(
                path.relative_to(inspection.root).as_posix() for path in absent
            )
            + "; this project does not have them yet and acquiring a lock creates "
            "its file, so inventorying this project would change it. The format "
            "above was read without opening the project; inventory a project whose "
            "own owner has already opened it for writing"
        )

    grouped: dict[tuple[str, str, str | None, bool | None], list[int]] = {}
    unknown: dict[tuple[str, str | None], int] = {}
    # kind -> (record count, one example "<path><pointer>")
    undeclared: dict[str, tuple[int, str]] = {}
    unreadable: list[str] = []
    # The same legacy identities the migration would remap, so the dry run and
    # the migration cannot disagree about what blocks.
    try:
        legacy_digests = (
            {ref.require_digest(): ref.version for ref, _, _ in repository._legacy_lineage()}
            if source != target_format_version else {}
        )
    except ProjectRepositoryError as exc:
        return refused(
            f"the published history could not be read completely, so this project "
            f"cannot be planned: {exc}"
        )
    try:
        transfer = repository.export_transfer(
            include_contents=False, include_all_runs=True,
        )
        report = repository.verify()
        for row in transfer["files"]:
            path, digest, size = row["path"], row["sha256"], row["size"]
            category, kind, registered = _retained_category(path)
            schema: str | None = None
            if category != "artifact":
                payload = _parse_json_document(
                    repository.read_transfer_file(path, digest), path,
                )
                declared = payload.get("schema")
                schema = declared if isinstance(declared, str) else None
                if registered is False:
                    unknown[(kind, schema)] = unknown.get((kind, schema), 0) + 1
                if category in ("record", "run_manifest", "authored_input", "design"):
                    # The migration asks each owner for this record's content
                    # digest; if the owner cannot read its own record the
                    # migration stops, so the dry run has to say so too.
                    try:
                        _content_digest_of(payload)
                    except ValueError as exc:
                        unreadable.append(f"{path}: {exc}")
                    for found in undeclared_version_identities(payload, legacy_digests):
                        count, example = undeclared.get(kind, (0, ""))
                        undeclared[kind] = (
                            count + 1, example or f"{path}{found.json_pointer}",
                        )
            totals = grouped.setdefault((category, kind, schema, registered), [0, 0])
            totals[0] += 1
            totals[1] += size
    except ProjectRepositoryError as exc:
        # A record or manifest that changed mid-scan leaves a partial reading.
        # Report that the project cannot be planned rather than presenting an
        # incomplete inventory as the complete retained closure.
        return refused(
            f"the retained closure could not be read completely, so no inventory "
            f"of this project is reported: {exc}"
        )

    inventory = tuple(
        RetainedFormatEntry(
            category=category,
            kind=kind,
            schema=schema,
            registered=registered,
            count=count,
            bytes=size,
        )
        for (category, kind, schema, registered), (count, size) in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
        )
    )
    counts = {
        name: sum(entry.count for entry in inventory if match(entry))
        for name, match in (
            ("artifact", lambda entry: entry.category == "artifact"),
            ("record", lambda entry: entry.category == "record"),
            ("snapshot", lambda entry: entry.kind == "canonical/snapshot"),
            ("event", lambda entry: entry.kind == "events/event"),
        )
    }
    common = {
        "inspection": inspection,
        "target_format_version": target_format_version,
        "planned": True,
        "project_id": repository.load_manifest().project_id,
        "head": report.head,
        "run_ids": tuple(transfer["run_ids"]),
        "inventory": inventory,
        "retained_files": len(transfer["files"]),
        "retained_bytes": sum(entry.bytes for entry in inventory),
        "orphan_paths": report.orphan_paths,
    }
    if source == target_format_version:
        return ProjectMigrationPlan(
            migration_required=False, required_transformations=(),
            preserved=(), blockers=(),
            **common,
        )

    transformations = [
        f"project.json format_version {source} -> {target_format_version}",
        f"HEAD ProjectHead@{source} -> ProjectHead@{target_format_version}",
    ]
    if counts["snapshot"]:
        transformations.append(
            f"{counts['snapshot']} canonical snapshot(s) CanonicalSnapshot@{source} -> "
            f"@{target_format_version}, whose state_sha256 becomes the semantic state "
            f"digest instead of the snapshot-file digest"
        )
    if counts["event"]:
        transformations.append(
            f"{counts['event']} retained event(s) ProjectEvent@{source} -> "
            f"@{target_format_version}, which must name from_snapshot/to_snapshot"
        )
    if counts["record"]:
        transformations.append(
            f"each of the {counts['record']} retained record(s) listed above is "
            f"restated at the project-version pointers its schema's owner declares, "
            f"re-hashed when its payload changes, and every retained reference that "
            f"names it is moved with it; a record carrying a legacy identity no owner "
            f"declares blocks the migration instead of being guessed at"
        )
    if counts["artifact"]:
        transformations.append(
            f"preserve {counts['artifact']} retained binary/source artifact(s) "
            f"byte-for-byte"
        )

    # A location no owner declares is where a migration would have to guess.
    # Grouped by kind with a count and one example, so a project carrying many
    # of them reads as a list of contracts to declare, not a wall of paths.
    blockers = [
        f"a retained record its own owner cannot read, so no owner can restate "
        f"it: {detail}"
        for detail in sorted(unreadable)
    ] + [
        f"retained kind {kind!r}: {count} record(s) carry a project-version "
        f"identity at a location no owner in this build restates "
        f"(for example {example}); {UNDECLARED_IDENTITY_BLOCKER}"
        for kind, (count, example) in sorted(undeclared.items())
    ]
    preserved = [
        f"retained record kind {kind!r} ({count} record(s), schema "
        f"{schema or 'undeclared'}) is not in the current record-kind table; the "
        f"migration preserves it byte for byte and lists any project-version "
        f"identity it embeds in the migration receipt"
        for (kind, schema), count in sorted(
            unknown.items(), key=lambda item: (item[0][0], item[0][1] or ""),
        )
        if kind not in undeclared
    ]

    return ProjectMigrationPlan(
        migration_required=True,
        required_transformations=tuple(transformations),
        preserved=tuple(preserved),
        blockers=tuple(blockers),
        **common,
    )
