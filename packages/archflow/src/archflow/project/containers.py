"""The four container states of ADR-007, named over the P036 layout.

ISO 19650 calls a set of information a *container* and gives it a state: work
in progress, shared, published, archived. ADR-007 fixed each of those on one
place in the project layout, and this module is that table as code. It reads
what the repository already retains and names it; it creates nothing, writes
nothing, and adds no pointer beside ``HEAD``.

The states, and where each one lives:

* work in progress - the designer's authored files under ``input/``. Loose
  files: nothing about them is retained until a run uses them.
* shared - one run directory, ``runs/<run_id>/``. Every record the runner put
  there is retained and digest-verified; the run is *shared for coordination*
  (S1) until its stage closes, and *suitable for stage approval* (S4) after.
* published - ``HEAD``, the one compare-and-swap position. Moving a run there
  is an **issue** (出图).
* archived - every canonical snapshot ``HEAD`` has left behind. Nobody deletes
  one; the chain is the archive.

Neither a shared run nor a work-in-progress file is "the current design". Only
the published container is, and only until the next issue.

The seam this module reserves is parallel development. ``work_in_progress``
takes an author because tomorrow there are several authored slots and one
published position; ``shared`` takes a branch because tomorrow a run says which
branch it ran on. Both parameters resolve against paths and records that exist
today, so the second author and the second branch arrive without a second
store.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote

from archflow.project.layout import (
    AUTHORED_RECORD_PATH,
    SEAT_PACK_PATH,
    ProjectLayout,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, STAGE_CLOSURE
from archflow.project.refs import (
    ProjectRecordRef,
    RunRef,
    require_identifier,
)
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectRepositoryError,
)


SATISFIED = "SATISFIED"

_DEFAULT_AUTHOR = "runner"
_AUTHORED_RECORD_NAME = PurePosixPath(AUTHORED_RECORD_PATH).name
_SEAT_PACK_NAME = PurePosixPath(SEAT_PACK_PATH).name
_SNAPSHOT_NAME = re.compile(r"^state-v(\d{6})-([0-9a-f]{64})\.json$")
_UNREADABLE = (ProjectRepositoryError, OSError, TypeError, ValueError)


class ContainerError(RuntimeError):
    """The project this module was asked to describe cannot be read.

    Raised only for the containers a project always has: its published
    position and the archive behind it. A single unreadable run is not this
    error - it is reported as the shared container it is, with a note saying
    so, because one broken run must not cost a caller every other answer.
    """


class ContainerState(StrEnum):
    """The four states ISO 19650 names, and ADR-007 places."""

    WORK_IN_PROGRESS = "work_in_progress"
    SHARED = "shared"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class StatusCode:
    """The ISO 19650 suitability codes this project uses, as plain text.

    These are not an enum beside ``ContainerState``. A suitability code is a
    label read off a container and shown to a person; the standard defines
    many more (S2, S3, B1..Bn, A1..An) and a project adopts the ones it can
    honestly award. Four are earned here today, and a closed enum would invite
    branching on the code where the state is the thing to branch on.
    """

    WORK_IN_PROGRESS = "S0"
    SHARED_FOR_COORDINATION = "S1"
    SHARED_FOR_STAGE_APPROVAL = "S4"
    PUBLISHED = "A"


@dataclass(frozen=True, slots=True)
class Container:
    """One container: what state it is in, and what to open to see it.

    ``ref`` is a ``project://`` URI for everything the repository retains and
    a filesystem path for work in progress, because a work-in-progress file is
    not retained and has no URI to name it by. ``note`` is one line for a
    person: what this container is and what it is still missing.
    """

    state: ContainerState
    status_code: str
    project_id: str
    run_id: str | None
    ref: str
    author: str | None
    branch_id: str | None
    note: str


def work_in_progress(
    repository: FilesystemProjectRepository,
    author: str | None = None,
) -> tuple[Container, ...]:
    """The authored slots: today the runner's, tomorrow one per author.

    With no ``author`` this is the single slot ADR-007 fixed,
    ``input/runner/state-record.json`` beside ``input/runner/seats.json`` -
    the two paths ``project.layout`` names and ``project.inputs`` reads. With
    an ``author`` the slot is ``input/<author>/state-record.json``: the
    reserved seam for parallel development, many work in progress and one
    published. Nothing here creates that directory, and an absent slot is an
    empty tuple, not a refusal - an author who has not started work is not an
    error.

    Only the file's existence and the digest of its bytes are read. Parsing an
    authored record is ``project.inputs``'s job and stays there; this module
    would learn nothing from the parse that it reports.
    """

    layout = repository.layout
    if author is None:
        record_path = layout.authored_record
        seats_path = layout.seat_pack
    else:
        require_identifier(author, "author")
        record_path = layout.resolve_relative(
            f"input/{author}/{_AUTHORED_RECORD_NAME}"
        )
        seats_path = layout.resolve_relative(
            f"input/{author}/{_SEAT_PACK_NAME}"
        )
    if not record_path.is_file():
        return ()
    seats = "seats beside it" if seats_path.is_file() else "no seat pack"
    return (
        Container(
            state=ContainerState.WORK_IN_PROGRESS,
            status_code=StatusCode.WORK_IN_PROGRESS,
            project_id=layout.project_id,
            run_id=None,
            ref=str(record_path),
            author=author,
            branch_id=None,
            note=(
                f"{author or _DEFAULT_AUTHOR} · authored "
                f"{_digest_of(record_path)[:12]} · {seats}"
            ),
        ),
    )


def shared(
    repository: FilesystemProjectRepository,
    *,
    branch_id: str | None = None,
) -> tuple[Container, ...]:
    """Every run, in run-id order, with the suitability it has earned.

    A run is S4 - *shared, suitable for stage approval* - when it retains a
    ``runner-run-receipt`` whose seats all executed **and** a ``stage-closure``
    record that says ``SATISFIED``. Nothing on the spine writes a stage closure
    yet: the kind is reserved for Wave C, so today every run comes back S1,
    *shared for coordination*, and its note says which half is missing.

    ``branch_id`` keeps only the runs whose receipt claims that branch. No
    receipt carries a ``branch_id`` today either, so filtering by one is
    honest and empty rather than wrong: a run that cannot say which branch it
    ran on is not evidence that it ran on this one.

    Records are read through the repository, which verifies every digest. The
    only thing read by path is the list of run directories, which is the
    layout's own name for them.
    """

    layout = repository.layout
    if branch_id is not None:
        require_identifier(branch_id, "branch_id")
    containers: list[Container] = []
    for run_id in _run_ids(layout):
        containers.extend(_shared_run(repository, run_id, branch_id))
    return tuple(containers)


def published(repository: FilesystemProjectRepository) -> Container:
    """``HEAD``: the one published container, and the issue it carries.

    There is always exactly one. A project whose ``HEAD`` or whose snapshot
    cannot be read has no published position to report, which is a broken
    project, not an empty answer.
    """

    layout = repository.layout
    version, snapshots = _issued_snapshots(repository)
    for snapshot_version, ref in snapshots:
        if snapshot_version == version:
            return Container(
                state=ContainerState.PUBLISHED,
                status_code=StatusCode.PUBLISHED,
                project_id=layout.project_id,
                run_id=None,
                ref=ref.uri,
                author=None,
                branch_id=None,
                note=f"issue {version}",
            )
    raise ContainerError(
        f"{layout.project_id}: no canonical snapshot for published "
        f"version {version}"
    )


def archived(
    repository: FilesystemProjectRepository,
) -> tuple[Container, ...]:
    """Every snapshot ``HEAD`` has left behind, oldest issue first.

    An archived container keeps the code it was issued under: each of these
    was the published position once. A project at its first issue has nothing
    behind it, so this is empty.
    """

    layout = repository.layout
    version, snapshots = _issued_snapshots(repository)
    return tuple(
        Container(
            state=ContainerState.ARCHIVED,
            status_code=StatusCode.PUBLISHED,
            project_id=layout.project_id,
            run_id=None,
            ref=ref.uri,
            author=None,
            branch_id=None,
            note=f"issue {snapshot_version} · superseded",
        )
        for snapshot_version, ref in snapshots
        if snapshot_version < version
    )


def _shared_run(
    repository: FilesystemProjectRepository,
    run_id: str,
    branch_id: str | None,
) -> tuple[Container, ...]:
    """One run as a container, including when the run cannot be read."""

    layout = repository.layout
    ref = _area_uri(layout.project_id, f"runs/{run_id}")
    try:
        run = repository.load_run(run_id)
    except _UNREADABLE:
        return (
            _unreadable(layout.project_id, run_id, ref, "run manifest unreadable"),
        )
    try:
        records = _run_records(repository, run)
    except _UNREADABLE:
        return (
            _unreadable(layout.project_id, run_id, ref, "run records unreadable"),
        )
    receipts = tuple(
        payload for kind, payload in records if kind == RUNNER_RUN_RECEIPT
    )
    if branch_id is not None:
        receipts = tuple(
            payload
            for payload in receipts
            if payload.get("branch_id") == branch_id
        )
        if not receipts:
            return ()
    closures = tuple(
        payload for kind, payload in records if kind == STAGE_CLOSURE
    )
    seats_done = any(
        bool(payload.get("seat_execution_complete")) for payload in receipts
    )
    closed = any(payload.get("status") == SATISFIED for payload in closures)
    claimed_branch = next(
        (
            payload["branch_id"]
            for payload in receipts
            if isinstance(payload.get("branch_id"), str)
        ),
        None,
    )
    return (
        Container(
            state=ContainerState.SHARED,
            status_code=(
                StatusCode.SHARED_FOR_STAGE_APPROVAL
                if seats_done and closed
                else StatusCode.SHARED_FOR_COORDINATION
            ),
            project_id=layout.project_id,
            run_id=run_id,
            ref=ref,
            author=None,
            branch_id=claimed_branch,
            note=(
                f"{run_id} · {_seat_phrase(receipts, seats_done)} · "
                f"{_closure_phrase(closures, closed)}"
            ),
        ),
    )


def _unreadable(
    project_id: str,
    run_id: str,
    ref: str,
    note: str,
) -> Container:
    """A run the repository refuses is still a container, and says so."""

    return Container(
        state=ContainerState.SHARED,
        status_code=StatusCode.SHARED_FOR_COORDINATION,
        project_id=project_id,
        run_id=run_id,
        ref=ref,
        author=None,
        branch_id=None,
        note=note,
    )


def _seat_phrase(receipts: tuple[Mapping[str, Any], ...], done: bool) -> str:
    if done:
        return "seats complete"
    return "seats incomplete" if receipts else "no run receipt"


def _closure_phrase(
    closures: tuple[Mapping[str, Any], ...],
    closed: bool,
) -> str:
    if closed:
        return "closure satisfied"
    return "closure open" if closures else "no closure retained"


def _run_records(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> tuple[tuple[str | None, Mapping[str, Any]], ...]:
    """Every retained record of one run as (record kind, payload).

    The kind is the name the writer gave ``put_json``: the repository puts it
    in front of the content digest in the file name, and that name is the only
    place a record's kind survives. A file whose name is not that shape has no
    kind, and comes back as ``None`` rather than as a guess.
    """

    refs = repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run.run_id
        ),
    )
    return tuple(
        (_record_kind(ref), repository.load_json(ref)) for ref in refs
    )


def _record_kind(ref: ProjectRecordRef) -> str | None:
    """This record's kind, or None for a file that is not a P036 record."""

    try:
        return ref.record_kind
    except ValueError:
        return None


def _run_ids(layout: ProjectLayout) -> tuple[str, ...]:
    """The run directories the layout owns, in run-id order."""

    try:
        if not layout.runs.is_dir():
            return ()
        return tuple(
            sorted(item.name for item in layout.runs.iterdir() if item.is_dir())
        )
    except OSError as exc:
        raise ContainerError(
            f"{layout.project_id}: cannot list runs: {exc}"
        ) from exc


def _issued_snapshots(
    repository: FilesystemProjectRepository,
) -> tuple[int, tuple[tuple[int, ProjectRecordRef], ...]]:
    """Use one verified HEAD chain, excluding prepared but unissued snapshots."""

    try:
        report = repository.verify()
    except _UNREADABLE as exc:
        raise ContainerError(
            f"{repository.layout.project_id}: cannot read issued snapshots: {exc}"
        ) from exc
    found: list[tuple[int, ProjectRecordRef]] = []
    for relative_path in report.reachable_paths:
        path = PurePosixPath(relative_path)
        if path.parent != PurePosixPath("canonical"):
            continue
        matched = _SNAPSHOT_NAME.fullmatch(path.name)
        if matched is None:
            continue
        found.append(
            (
                int(matched.group(1)),
                ProjectRecordRef(
                    project_id=repository.layout.project_id,
                    relative_path=relative_path,
                    sha256=matched.group(2),
                ),
            )
        )
    return report.head.version, tuple(sorted(found, key=lambda item: item[0]))


def _digest_of(path: Path) -> str:
    """The sha-256 of a work-in-progress file's bytes, and nothing more."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ContainerError(f"{path.as_posix()}: cannot be read: {exc}") from exc


def _area_uri(project_id: str, relative_path: str) -> str:
    """A ``project://`` URI for a project area that is not one record.

    A run is a directory, not a record, so ``ProjectRecordRef`` cannot name
    it; the shape is the one that type produces so both read the same way.
    """

    return (
        f"project://{quote(project_id, safe='')}/"
        f"{quote(relative_path, safe='/._-')}"
    )
