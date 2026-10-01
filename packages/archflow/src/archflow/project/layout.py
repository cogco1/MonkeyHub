"""Pure project path mapping with no directory creation or file writes.

``layout_fingerprint`` is the one reader here: it states the project's shape
from directory metadata alone, and it too creates, opens and writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import time
from typing import Iterable

from archflow.project.refs import (
    ProjectArtifactRef,
    ProjectRecordRef,
    require_identifier,
    require_project_relative_path,
)

# The files the designer authors: the work-in-progress container (ADR-007).
# They are named here because the layout owns a project's on-disk names, and
# read in exactly one place, ``archflow.project.inputs``.
AUTHORED_RECORD_PATH = "input/runner/state-record.json"
SEAT_PACK_PATH = "input/runner/seats.json"
# The architect's program: departments, spaces and the adjacencies they
# demand. Work in progress like the other two — it is the brief being written,
# not a product of a run — and the only one of the three the studio may write.
PROGRAM_SHEET_PATH = "input/runner/program-sheet.json"


def cad_workspace_path(workspace_root: Path | None, stage_id: str) -> Path:
    """Map an export stage to its caller-owned workspace without creating it."""

    if workspace_root is None or not workspace_root.is_absolute():
        raise ValueError("CAD export requires an explicit absolute workspace_root")
    name = require_project_relative_path(f"cad-{stage_id}")
    if len(PurePosixPath(name).parts) != 1:
        raise ValueError("CAD export workspace must be one directory name")
    return workspace_root / name


@dataclass(frozen=True, slots=True)
class RunLayout:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "run.json"

    @property
    def records(self) -> Path:
        return self.root / "records"

    @property
    def branches(self) -> Path:
        return self.root / "branches"

    @property
    def candidates(self) -> Path:
        return self.root / "candidates"

    @property
    def reviews(self) -> Path:
        return self.root / "reviews"

    @property
    def workspaces(self) -> Path:
        return self.root / "workspaces"

    @property
    def recovery(self) -> Path:
        return self.root / "recovery"


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    root: Path
    project_id: str

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        object.__setattr__(self, "root", self.root.resolve(strict=False))

    @property
    def manifest(self) -> Path:
        return self.root / "project.json"

    @property
    def head(self) -> Path:
        # The published container is called an *issue* everywhere a person
        # reads it (ADR-007), but this file keeps the name ``HEAD``: the
        # repository format owns it, every retained project on disk has one,
        # and renaming it would strand them. "Do not rename the HEAD file."
        return self.root / "HEAD"

    @property
    def inputs(self) -> Path:
        return self.root / "input"

    @property
    def authored_record(self) -> Path:
        """The designer's authored ``StateRecord@1``: work in progress."""

        return self.resolve_relative(AUTHORED_RECORD_PATH)

    @property
    def seat_pack(self) -> Path:
        """The seats the designer authored, beside the record."""

        return self.resolve_relative(SEAT_PACK_PATH)

    @property
    def program_sheet(self) -> Path:
        """The architect's ``ProgramSheet@1``, beside the record."""

        return self.resolve_relative(PROGRAM_SHEET_PATH)

    @property
    def objects(self) -> Path:
        return self.root / "objects" / "sha256"

    @property
    def events(self) -> Path:
        return self.root / "events"

    @property
    def canonical(self) -> Path:
        return self.root / "canonical"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def design_branches(self) -> Path:
        """Working design history positions; independent of issued HEAD."""
        return self.root / "design" / "branches.json"

    @property
    def working_draft(self) -> Path:
        """Current working position and retention choices, independent of issued HEAD."""
        return self.root / "design" / "working.json"

    @property
    def trash(self) -> Path:
        """The project trash (#575): runs moved out of ``runs/`` whole, restorable until they are purged.

        ``trash/runs/<run_id>/`` is a run's own directory, renamed there
        unchanged; ``trash/entries/<run_id>.json`` is the manifest saying when
        it moved, why, under which rule, and the working-draft row it took.
        """
        return self.root / "trash"

    def trashed_run(self, run_id: str) -> Path:
        """Where a run's whole directory stands while it is in the trash."""
        require_identifier(run_id, "run_id")
        return self.trash / "runs" / run_id

    def trash_manifest(self, run_id: str) -> Path:
        """The manifest of a run in the trash, kept apart from the runs so no run id can collide with it."""
        require_identifier(run_id, "run_id")
        return self.trash / "entries" / f"{run_id}.json"

    def run(self, run_id: str) -> RunLayout:
        require_identifier(run_id, "run_id")
        return RunLayout(self.runs / run_id)

    def resolve_relative(self, relative_path: str) -> Path:
        normalized = require_project_relative_path(relative_path)
        portable = PurePosixPath(normalized)
        target = (self.root / Path(*portable.parts)).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("project-relative path escapes project root") from exc
        return target

    def resolve_record(
        self, ref: ProjectRecordRef | ProjectArtifactRef
    ) -> Path:
        if ref.project_id != self.project_id:
            raise ValueError("reference belongs to another project")
        return self.resolve_relative(ref.relative_path)


# The mutable pointer documents a project keeps at fixed names, the ones
# ``ProjectLayout`` calls manifest, head, design_branches and working_draft.
# Replacing one in place moves nothing in a directory listing, so the
# fingerprint states their size and time itself.
FINGERPRINT_POINTER_FILES = (
    "project.json",
    "HEAD",
    "design/branches.json",
    "design/working.json",
)
# Git's racy rule: a timestamp this close to the scan may be shared by a write
# the scan did not see, because file-system clocks tick coarser than writes.
FINGERPRINT_SETTLED_NS = 2_000_000_000


@dataclass(frozen=True, slots=True)
class LayoutFingerprint:
    """What a project's directories and pointer files looked like at one scan.

    ``digest`` changes when a directory appears, disappears or gains, loses
    or renames an entry, and when a pointer file is replaced. ``stable`` is
    true only when the newest time it saw is more than two seconds older than
    the scan: before then an equal digest is not evidence of no change.
    ``scanned_at_ns`` is wall-clock time taken when the scan began.
    """

    digest: str
    newest_mtime_ns: int
    scanned_at_ns: int
    stable: bool

    @classmethod
    def from_lines(
        cls, lines: Iterable[str], *, newest_mtime_ns: int, scanned_at_ns: int, unreadable: bool,
    ) -> LayoutFingerprint:
        """The fingerprint of one scan's lines: sha256 over them sorted, and the racy rule.

        The one place both readers of a layout - ``layout_fingerprint`` and the
        watch that keeps it current (``archflow.project.watch``) - turn what they
        saw into a digest, so equal sightings give equal digests.
        """

        text = "\n".join(sorted(lines))
        return cls(
            digest=hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            newest_mtime_ns=newest_mtime_ns,
            scanned_at_ns=scanned_at_ns,
            stable=not unreadable and scanned_at_ns - newest_mtime_ns > FINGERPRINT_SETTLED_NS,
        )


def is_layout_directory(entry: os.DirEntry) -> bool:
    """Whether the fingerprint walks into a listed entry: a directory, never a link or a junction."""

    try:
        return entry.is_dir(follow_symlinks=False) and not entry.is_junction()
    except OSError:
        return False


def pointer_file_lines(root: Path | str) -> tuple[tuple[str, ...], int, bool]:
    """Each pointer file's line, the newest time among them and whether one was unreadable.

    A pointer file contributes its size and ``st_mtime_ns``, or that it is
    missing; anything else that stops the ``stat`` is named and unreadable.
    """

    base = os.fspath(root)
    lines: list[str] = []
    newest = 0
    unreadable = False
    for relative in FINGERPRINT_POINTER_FILES:
        try:
            stat = os.stat(os.path.join(base, *relative.split("/")))
        except FileNotFoundError:
            lines.append(f"f {relative} missing")
            continue
        except OSError as exc:
            lines.append(f"f {relative} unreadable {exc.errno}")
            unreadable = True
            continue
        lines.append(f"f {relative} {stat.st_size} {stat.st_mtime_ns}")
        newest = max(newest, stat.st_mtime_ns)
    return tuple(lines), newest, unreadable


def layout_fingerprint(root: Path | str) -> LayoutFingerprint:
    """Fingerprint a project from metadata only: no file is opened or read.

    Every directory under ``root``, the root included, contributes its
    relative path and ``st_mtime_ns``; each pointer file its size and
    ``st_mtime_ns``, or that it is missing. The digest is sha256 over the
    sorted lines. A directory's own ``stat`` is used, never the copy of its
    times in its parent's listing: Windows updates that copy lazily. Anything
    that cannot be read is named in a line and makes the result unstable.

    This walks the whole project, every time. Nothing on a request path calls
    it: the project's layout watch (``archflow.project.watch``) keeps the same
    fingerprint current in the background and is what a reader asks.
    """

    base = os.fspath(root)
    scanned_at_ns = time.time_ns()
    lines: list[str] = []
    newest = 0
    unreadable = False
    pending = [("", base)]
    while pending:
        relative, path = pending.pop()
        name = relative or "."
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            if relative:
                # Removed during the scan; its parent's time says so.
                continue
            lines.append("d . missing")
            unreadable = True
            continue
        except OSError as exc:
            lines.append(f"d {name} unreadable {exc.errno}")
            unreadable = True
            continue
        lines.append(f"d {name} {mtime}")
        newest = max(newest, mtime)
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if is_layout_directory(entry):
                        pending.append(
                            (f"{relative}/{entry.name}" if relative else entry.name, entry.path)
                        )
        except FileNotFoundError:
            continue
        except OSError as exc:
            lines.append(f"l {name} unreadable {exc.errno}")
            unreadable = True
    pointers, pointer_newest, pointer_unreadable = pointer_file_lines(base)
    lines.extend(pointers)
    return LayoutFingerprint.from_lines(
        lines,
        newest_mtime_ns=max(newest, pointer_newest),
        scanned_at_ns=scanned_at_ns,
        unreadable=unreadable or pointer_unreadable,
    )
