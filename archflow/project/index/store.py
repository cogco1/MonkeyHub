"""The project index: one SQLite file of rows derived from a project (ADR-008 phase 1b).

P036 stays the only source of truth. The index holds what one projector
derives from it, per run and for the design tree, so that a reader can ask
"which run, which artifact, which stage" without reading the records again.
It can be deleted at any time; the next load builds it again.

- One file per project, in a cache directory the caller names (the Hub's
  ``cache/projects/<runtime_id>/index``), never in the project folder.
- One writer: the thread of the ``IndexKeeper`` (``keeper.py``) of the process
  that holds ``index.lock`` beside the file. Any other process that tries gets
  ``IndexLocked`` (its keeper retries for a while: the holder may be exiting)
  and meanwhile reads P036 itself. ``load`` and ``apply`` are that
  thread's alone; readers take ``snapshot`` from any thread and never wait for
  the writer (WAL).
- ``meta`` states the stamp (schema version, projector version, project id and
  the digest of ``project.json``), the ``epoch``, the monotonic ``revision``
  and when the layout lines were read. A stamp that differs means rebuild,
  never migrate: a new file is written, then renamed into place, under a new
  epoch. A file SQLite cannot read is moved aside and rebuilt once.
- ``place`` keeps every layout line (``archflow.project.watch.LayoutSighting``),
  so a kept index reopened after a restart re-projects only what moved while
  nobody watched.
- One projector (Fossil's single crosslink) fills the rows for a rebuild and
  for every change. It runs before the write transaction opens, never inside
  it; the revision moves once per commit that changed a row.
- ``change`` is the bounded change log (#366): for each entity a client reads
  (``run:<id>``, ``aside:<id>``, ``tree``, ``working``, ``area:<name>``), the revision that
  last changed or deleted it. ``IndexSnapshot.changes`` answers ``since=<revision>`` from it;
  a revision below ``meta.floor`` is older than the log and answers nothing,
  so the caller sends a whole snapshot instead.
- ``projection`` is the projection cache's status table (#367): one row per
  projection key, pending, done or error. It is no projection of P036 but the
  state of a cache beside it, so a rebuild carries it into the new file and
  its rows are written by the projection queue (``*_projection`` methods)
  rather than the keeper, under the same write lock. A row that becomes done,
  or a done row that goes, is an entity of its own (``projections:<key>``)
  and moves the revision like any other change.

Index rows and keys are never evidence: each row names the P036 record it was
read from, and that record is the evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from archflow.project.layout import FINGERPRINT_POINTER_FILES, FINGERPRINT_SETTLED_NS

SCHEMA_VERSION = 6
# How many revisions the change log keeps: a client further behind than this
# is sent a whole snapshot instead of the changes.
CHANGE_LOG_REVISIONS = 512
# The areas whose rows the index already keeps: a change there is a run's or
# the tree's. Every other area is an entity of its own (``area:<name>``), so
# that saving a draft or issuing a version moves the revision a client follows.
_ROW_AREAS = ("runs", "branches")
INDEX_FILE = "index.sqlite"
LOCK_FILE = "index.lock"
# How long a rebuild waits for the readers of the old file to finish before
# it renames the new one over it (Windows refuses to replace an open file).
SWAP_WAIT_S = 5.0

_LOG = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE place (key TEXT PRIMARY KEY, line TEXT NOT NULL, mtime_ns INTEGER NOT NULL);
CREATE TABLE run (
    run_id TEXT PRIMARY KEY, rev INTEGER NOT NULL, digest TEXT NOT NULL,
    cites TEXT NOT NULL, body TEXT NOT NULL, aside_rev INTEGER NOT NULL, aside_digest TEXT NOT NULL
);
CREATE TABLE record (
    run_id TEXT NOT NULL, uri TEXT NOT NULL, kind TEXT, sha256 TEXT NOT NULL, rev INTEGER NOT NULL,
    aside INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, uri)
);
CREATE INDEX record_kind ON record (kind);
CREATE INDEX record_sha256 ON record (sha256);
CREATE TABLE artifact (
    run_id TEXT NOT NULL, position INTEGER NOT NULL, sha256 TEXT, format TEXT NOT NULL,
    representation TEXT NOT NULL, available INTEGER NOT NULL, rev INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);
CREATE INDEX artifact_sha256 ON artifact (sha256);
CREATE TABLE document (
    run_id TEXT NOT NULL, position INTEGER NOT NULL, asset_sha256 TEXT NOT NULL, revision_ref TEXT,
    rev INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);
CREATE INDEX document_asset_sha256 ON document (asset_sha256);
CREATE TABLE candidate (
    run_id TEXT PRIMARY KEY, source_stage_ref TEXT, rev INTEGER NOT NULL, body TEXT NOT NULL
);
CREATE TABLE stage (
    branch_id TEXT NOT NULL, position INTEGER NOT NULL, stage_ref TEXT NOT NULL,
    candidate_id TEXT NOT NULL, rev INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (branch_id, position)
);
CREATE TABLE change (id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
CREATE INDEX change_revision ON change (revision);
CREATE TABLE projection (
    key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, kind TEXT NOT NULL, recipe_hash TEXT NOT NULL,
    renderer_version TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
    claimed_at REAL, blob_sha256 TEXT, error TEXT, next_attempt_at REAL, load_ms INTEGER, render_ms INTEGER,
    touched_at REAL NOT NULL, rev INTEGER NOT NULL
);
CREATE INDEX projection_input ON projection (input_hash, kind, recipe_hash, renderer_version);
CREATE INDEX projection_blob ON projection (blob_sha256);
"""
_PROJECTION_COLUMNS = ("key", "input_hash", "kind", "recipe_hash", "renderer_version", "body", "status", "attempts",
                       "claimed_at", "blob_sha256", "error", "next_attempt_at", "load_ms", "render_ms", "touched_at",
                       "rev")
# A projection row's status: queued (a lease once claimed_at is set), drawn, or failed.
PROJECTION_PENDING = "pending"
PROJECTION_DONE = "done"
PROJECTION_ERROR = "error"

# What an agent may filter each table by, and the order its rows come in.
QUERYABLE: dict[str, tuple[tuple[str, ...], str]] = {
    "run": (("run_id",), "run_id"),
    "record": (("run_id", "uri", "kind", "sha256"), "run_id, uri"),
    "artifact": (("run_id", "sha256", "format", "representation"), "run_id, position"),
    "document": (("run_id", "asset_sha256", "revision_ref"), "run_id, position"),
    "candidate": (("run_id", "source_stage_ref"), "run_id"),
    "stage": (("branch_id", "stage_ref", "candidate_id"), "branch_id, position"),
}
_COLUMNS: dict[str, tuple[str, ...]] = {
    "run": ("run_id", "rev", "body"),
    "record": ("run_id", "uri", "kind", "sha256", "rev"),
    "artifact": ("run_id", "position", "sha256", "format", "representation", "available", "rev", "body"),
    "document": ("run_id", "position", "asset_sha256", "revision_ref", "rev", "body"),
    "candidate": ("run_id", "source_stage_ref", "rev", "body"),
    "stage": ("branch_id", "position", "stage_ref", "candidate_id", "rev", "body"),
}
QUERY_LIMIT = 1000
# The aside digest of a run that keeps nothing aside.
_NO_ASIDE = ""

# The area each pointer file belongs to (``place_area``). HEAD and the working
# draft are areas of their own that no row cites: saving a draft or issuing a
# version projects nothing again.
_POINTER_AREAS = {
    "project.json": "manifest",
    "HEAD": "head",
    "design/branches.json": "branches",
    "design/working.json": "working",
}
assert set(_POINTER_AREAS) == set(FINGERPRINT_POINTER_FILES)


class IndexUnavailable(RuntimeError):
    """The index cannot be used here; the caller reads P036 itself."""


class IndexLocked(IndexUnavailable):
    """Another writer holds ``index.lock``; it may give it up soon (a process that is exiting)."""


@dataclass(frozen=True, slots=True)
class IndexStamp:
    """What a kept index must have been built for; any difference means rebuild."""

    projector_version: str
    project_id: str
    manifest_sha256: str
    schema_version: int = SCHEMA_VERSION

    def items(self) -> dict[str, str]:
        return {
            "schema_version": str(self.schema_version),
            "projector_version": self.projector_version,
            "project_id": self.project_id,
            "manifest_sha256": self.manifest_sha256,
        }


def manifest_stamp(root: Path, *, projector_version: str, project_id: str) -> IndexStamp:
    """The stamp of the project at ``root``: its manifest's bytes, digested."""

    try:
        digest = hashlib.sha256((Path(root) / "project.json").read_bytes()).hexdigest()
    except OSError:
        digest = "unreadable"
    return IndexStamp(projector_version, project_id, digest)


@dataclass(frozen=True, slots=True)
class IndexToken:
    """Where the index stands: a new epoch per build, one revision per commit that changed a row."""

    epoch: str
    revision: int


@dataclass(frozen=True, slots=True)
class IndexCommit:
    """What one commit changed: where the index stands after it and the domains it touched.

    ``domains`` are the entity kinds (``run``, ``aside``, ``tree``, ``working``, ``area``); a rebuild
    names ``reset``, since a client of another epoch reads everything again.
    """

    token: IndexToken
    domains: frozenset[str]


@dataclass(frozen=True, slots=True)
class RecordRow:
    uri: str
    kind: str | None
    sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    sha256: str | None
    format: str
    representation: str
    available: bool
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DocumentRow:
    asset_sha256: str
    revision_ref: str | None
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CandidateRow:
    source_stage_ref: str | None
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StageRow:
    branch_id: str
    stage_ref: str
    candidate_id: str
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RunRows:
    """Everything the projector derives from one run.

    ``cites`` names the areas outside the run the rows were read from, as
    ``place_area`` spells them: a change there projects this run again.
    ``aside`` are the records the run keeps beside what it shows (a Board's
    scene revisions, a page's annotations): they are
    records like the others, but an entity of their own (``aside:<id>``), so
    saving one moves nothing a reader of ``run:<id>`` shows (#366).
    """

    run_id: str
    body: Mapping[str, Any]
    records: tuple[RecordRow, ...] = ()
    artifacts: tuple[ArtifactRow, ...] = ()
    documents: tuple[DocumentRow, ...] = ()
    candidate: CandidateRow | None = None
    cites: frozenset[str] = field(default_factory=frozenset)
    aside: tuple[RecordRow, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeRows:
    """What the projector derives from the design branches: the committed stages.

    ``cites`` names the areas the stages were read from; ``branches`` is always cited.
    """

    body: Mapping[str, Any]
    stages: tuple[StageRow, ...] = ()
    cites: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ProjectionRow:
    """One projection key's status (#367): pending (queued, or leased while ``claimed_at`` is set), done or error.

    ``key`` digests the four identity columns and the cache's salt: the input's
    content hash, the kind, the recipe's hash and the renderer version. ``body``
    is the caller's own (the complete recipe, the source to draw from). Times
    are the caller's clock, in seconds.
    """

    key: str
    input_hash: str
    kind: str
    recipe_hash: str
    renderer_version: str
    body: Mapping[str, Any]
    status: str = PROJECTION_PENDING
    attempts: int = 0
    claimed_at: float | None = None
    blob_sha256: str | None = None
    error: str | None = None
    next_attempt_at: float | None = None
    load_ms: int | None = None
    render_ms: int | None = None
    touched_at: float = 0.0
    rev: int = 0

    def entity(self) -> dict[str, Any]:
        """What a client keeps of a done row: enough to find it by input and recipe, and its blob."""

        return {"id": f"projections:{self.key}", "domain": "projections", "rev": self.rev, "body": {
            "key": self.key, "inputSha256": self.input_hash, "kind": self.kind,
            "recipe": self.body.get("recipe"), "renderer": self.renderer_version, "blobSha256": self.blob_sha256}}


def _projection_row(values: tuple) -> ProjectionRow:
    item = dict(zip(_PROJECTION_COLUMNS, values))
    item["body"] = json.loads(item["body"])
    return ProjectionRow(**item)


class Projector(Protocol):
    """The one derivation that fills the index, for a rebuild and for every change."""

    version: str

    def run_ids(self) -> tuple[str, ...]: ...

    def project_run(self, run_id: str) -> RunRows: ...

    def project_tree(self) -> TreeRows: ...

    def project_working(self) -> Mapping[str, Any] | None:
        """The working position a head is read from, without the local recovery it may name.

        None when it cannot be read. Its own entity (``working``): saving a
        local recovery rewrites the same pointer file, and moves only
        ``area:working``, so a reader of the head can tell the two apart.
        """
        ...


def place_area(key: str) -> str:
    """The part of a project a layout line's key belongs to.

    A key is a line's kind and name (``d runs/r1/records``, ``f HEAD``,
    ``l design``). ``run:<id>`` for anything in one run, ``runs`` for the run
    directory's own listing, a pointer file's own area (``manifest``, ``head``,
    ``branches``, ``working``), ``root`` for the project directory itself and
    ``area:<name>`` for every other top-level directory.
    """

    kind, _, name = key.partition(" ")
    if kind == "f":
        return _POINTER_AREAS.get(name, f"area:{name}")
    return path_area(name)


def path_area(relative_path: str) -> str:
    """The area of a project-relative POSIX path: a pointer file's, a run's or a top-level directory's."""

    if relative_path in _POINTER_AREAS:
        return _POINTER_AREAS[relative_path]
    if relative_path in ("", "."):
        return "root"
    parts = relative_path.split("/")
    if parts[0] == "runs":
        return "runs" if len(parts) == 1 else f"run:{parts[1]}"
    return f"area:{parts[0]}"


def line_places(lines: Iterable[str]) -> dict[str, tuple[str, int]]:
    """Layout lines by key (kind and name), each with the time it states (0 when none)."""

    places: dict[str, tuple[str, int]] = {}
    for line in lines:
        kind, _, rest = line.partition(" ")
        if kind == "f":
            # "f <pointer> <size> <mtime>" or "f <pointer> missing|unreadable ..."
            name = next((pointer for pointer in FINGERPRINT_POINTER_FILES if rest.startswith(pointer + " ")), rest)
        else:
            # "d <name> <mtime>", "d . missing", "d|l <name> unreadable <errno>"
            name = rest.rpartition(" ")[0]
            if name.endswith(" unreadable"):
                name = name[: -len(" unreadable")]
        tail = line.rsplit(" ", 1)[-1]
        mtime = int(tail) if tail.isdigit() and "unreadable" not in line else 0
        places[f"{kind} {name}"] = (line, mtime)
    return places


def entity_area(area: str) -> str | None:
    """The ``area:<name>`` entity an area is, or None when its rows are a run's or the tree's."""

    if area in _ROW_AREAS or area.startswith("run:"):
        return None
    return area if area.startswith("area:") else f"area:{area}"


def _racy(places: Mapping[str, tuple[str, int]], scanned_at_ns: int) -> set[str]:
    """Git's racy rule: the places whose time was too close to the scan to prove anything."""

    return {key for key, (_, mtime) in places.items()
            if mtime and scanned_at_ns - mtime <= FINGERPRINT_SETTLED_NS}


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class _WriterLease:
    """``index.lock`` held exclusively for as long as this process writes the index."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # The file stays: deleting it would let another process lock a
        # replacement while this one still holds the old one.
        self._handle = (directory / LOCK_FILE).open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            raise IndexLocked(f"another process writes the index in {directory}") from exc

    def release(self) -> None:
        if self._handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class IndexSnapshot:
    """One read transaction: every row it answers comes from the same commit."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        meta = dict(connection.execute("SELECT key, value FROM meta WHERE key IN ('epoch', 'revision')"))
        self.token = IndexToken(meta["epoch"], int(meta["revision"]))

    def rows(self, table: str, filters: Mapping[str, str] | None = None, *,
             limit: int | None = QUERY_LIMIT) -> list[dict[str, Any]]:
        """Rows of one table, filtered by exact values of its queryable columns.

        ``body`` comes back decoded; ``limit=None`` returns every row. Refuses
        a table or a column it does not list rather than guessing.
        """

        if table not in QUERYABLE:
            raise ValueError(f"the index has no table {table!r}; it has {', '.join(QUERYABLE)}")
        allowed, order = QUERYABLE[table]
        filters = dict(filters or {})
        unknown = sorted(set(filters) - set(allowed))
        if unknown:
            raise ValueError(f"{table} rows are filtered by {', '.join(allowed)}, not {', '.join(unknown)}")
        columns = _COLUMNS[table]
        where = " AND ".join(f"{name} = ?" for name in filters)
        sql = (f"SELECT {', '.join(columns)} FROM {table}" + (f" WHERE {where}" if where else "")
               + f" ORDER BY {order} LIMIT ?")
        found = self._connection.execute(
            sql, (*filters.values(), -1 if limit is None else max(0, int(limit))),
        ).fetchall()
        decoded = []
        for row in found:
            item = dict(zip(columns, row))
            if "body" in item:
                item["body"] = json.loads(item["body"])
            if "available" in item:
                item["available"] = bool(item["available"])
            decoded.append(item)
        return decoded

    def tree(self) -> Any:
        """The design tree's own body, as the projector stated it."""

        kept = self._connection.execute("SELECT value FROM meta WHERE key = 'tree'").fetchone()
        return None if kept is None else json.loads(kept[0])

    def meta(self) -> dict[str, str]:
        """The stamp, epoch, revision and scan time the index stands at."""

        return {key: value for key, value in self._connection.execute("SELECT key, value FROM meta")
                if key != "tree"}

    @property
    def floor(self) -> int:
        """The oldest revision the change log can answer ``changes`` from."""

        kept = self._connection.execute("SELECT value FROM meta WHERE key = 'floor'").fetchone()
        return self.token.revision if kept is None else int(kept[0])

    def changes(self, since: int) -> tuple[list[dict[str, Any]], list[str]] | None:
        """The entities changed after revision ``since`` and the ids deleted since; None past the log.

        None when ``since`` is below the log's floor or ahead of this snapshot:
        the caller then sends a whole snapshot (``entities``).
        """

        if since < self.floor or since > self.token.revision:
            return None
        ids = [row[0] for row in self._connection.execute(
            "SELECT id FROM change WHERE revision > ? ORDER BY id", (since,))]
        if not ids:
            return [], []
        present = self.entities(ids)
        found = {entity["id"] for entity in present}
        return present, [entity_id for entity_id in ids if entity_id not in found]

    def entities(self, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """What a client keeps of the index, one entity per run, the tree, the working position, each other area and done projection.

        ``{id, domain, rev, body}``: a run's body holds its own body, candidate,
        artifacts, documents and how many records it has (the records
        themselves stay in ``GET /api/index/record``); a run that keeps records
        aside has one more entity, ``aside:<id>``, whose body counts them; the tree's is its body
        and stages; the working position's is what ``project_working`` read;
        an area's the layout lines it holds; a done projection's
        (``projections:<key>``) its input, kind, recipe, renderer and blob. ``rev`` is the
        revision that last changed the entity. ``ids`` limits the answer to
        those entities; an id that names nothing is left out.
        """

        wanted = None if ids is None else set(ids)
        logged = dict(self._connection.execute("SELECT id, revision FROM change"))
        found: list[dict[str, Any]] = []
        run_ids = None if wanted is None else sorted(entity[4:] for entity in wanted if entity.startswith("run:"))
        if run_ids is None or run_ids:
            found.extend(self._runs(run_ids))
        aside_ids = None if wanted is None else sorted(entity[6:] for entity in wanted if entity.startswith("aside:"))
        if aside_ids is None or aside_ids:
            found.extend(self._asides(aside_ids))
        if wanted is None or "tree" in wanted:
            tree = self.tree()
            if tree is not None:
                stages = self.rows("stage", limit=None)
                kept = self._connection.execute("SELECT value FROM meta WHERE key = 'tree_rev'").fetchone()
                found.append({"id": "tree", "domain": "tree", "rev": int(kept[0]) if kept else 1,
                              "body": {"tree": tree, "stages": stages}})
        if wanted is None or "working" in wanted:
            kept = dict(self._connection.execute(
                "SELECT key, value FROM meta WHERE key IN ('working', 'working_rev')"))
            if "working" in kept:
                found.append({"id": "working", "domain": "working", "rev": int(kept.get("working_rev", 1)),
                              "body": json.loads(kept["working"])})
        projection_keys = None if wanted is None else sorted(
            entity[len("projections:"):] for entity in wanted if entity.startswith("projections:"))
        if projection_keys is None or projection_keys:
            where = "" if projection_keys is None else f" AND key IN ({', '.join('?' * len(projection_keys))})"
            found.extend(_projection_row(row).entity() for row in self._connection.execute(
                f"SELECT {', '.join(_PROJECTION_COLUMNS)} FROM projection WHERE status = ?{where} ORDER BY key",
                (PROJECTION_DONE, *(projection_keys or ()))))
        if wanted is None or any(entity.startswith("area:") for entity in wanted):
            areas: dict[str, list[str]] = {}
            for key, line in self._connection.execute("SELECT key, line FROM place ORDER BY key"):
                entity = entity_area(place_area(key))
                if entity is not None and (wanted is None or entity in wanted):
                    areas.setdefault(entity, []).append(line)
            for entity, lines in sorted(areas.items()):
                found.append({"id": entity, "domain": "area", "rev": logged.get(entity, 1),
                              "body": {"area": entity[5:], "lines": lines}})
        return found

    def _runs(self, run_ids: list[str] | None) -> list[dict[str, Any]]:
        where = "" if run_ids is None else f" WHERE run_id IN ({', '.join('?' * len(run_ids))})"
        params = () if run_ids is None else tuple(run_ids)
        runs = {run_id: {"id": f"run:{run_id}", "domain": "run", "rev": rev, "body": {
                    "runId": run_id, "run": json.loads(body), "candidate": None,
                    "artifacts": [], "documents": [], "records": 0}}
                for run_id, rev, body in self._connection.execute(
                    f"SELECT run_id, rev, body FROM run{where} ORDER BY run_id", params)}
        for run_id, count in self._connection.execute(
                f"SELECT run_id, COUNT(*) FROM record{where}{' AND' if where else ' WHERE'} aside = 0 GROUP BY run_id",
                params):
            if run_id in runs:
                runs[run_id]["body"]["records"] = count
        for table in ("artifact", "document", "candidate"):
            columns = [name for name in _COLUMNS[table] if name not in ("rev",)]
            order = QUERYABLE[table][1]
            for row in self._connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table}{where} ORDER BY {order}", params):
                item = dict(zip(columns, row))
                item["body"] = json.loads(item["body"])
                if "available" in item:
                    item["available"] = bool(item["available"])
                run = runs.get(item.pop("run_id"))
                if run is None:
                    continue
                if table == "candidate":
                    run["body"]["candidate"] = item
                else:
                    run["body"][f"{table}s"].append(item)
        return list(runs.values())

    def _asides(self, run_ids: list[str] | None) -> list[dict[str, Any]]:
        where = "" if run_ids is None else f" AND run.run_id IN ({', '.join('?' * len(run_ids))})"
        params = () if run_ids is None else tuple(run_ids)
        return [{"id": f"aside:{run_id}", "domain": "aside", "rev": rev, "body": {"runId": run_id, "records": count}}
                for run_id, rev, count in self._connection.execute(
                    "SELECT run.run_id, run.aside_rev, COUNT(*) FROM run JOIN record ON record.run_id = run.run_id"
                    f" WHERE record.aside = 1{where} GROUP BY run.run_id ORDER BY run.run_id", params)]


class ProjectIndex:
    """One project's index file, written by one thread of this process alone.

    ``load`` opens, reconciles or rebuilds it and ``apply`` brings it up to
    date; both belong to the writer (``IndexKeeper``). ``snapshot`` and
    ``query`` read it from any thread and never wait for the writer.
    """

    def __init__(
        self,
        directory: Path,
        *,
        projector: Projector,
        stamp: Callable[[], IndexStamp],
    ) -> None:
        self.directory = Path(directory)
        self.path = self.directory / INDEX_FILE
        self._projector = projector
        self._stamp = stamp
        self._lease: _WriterLease | None = None
        self._writer: sqlite3.Connection | None = None
        self._token: IndexToken | None = None
        # The writer's view of the file, kept in memory: each run's cites and
        # row digest, the tree's cites and digest, every place and the racy ones.
        self._runs: dict[str, tuple[frozenset[str], str, str]] = {}
        self._tree: tuple[frozenset[str], str] | None = None
        self._working: str | None = None
        self._places: dict[str, tuple[str, int]] = {}
        self._racy: set[str] = set()
        # Readers: pooled connections, how many are reading, and whether the
        # file is being swapped under them.
        self._gate = threading.Condition()
        self._pool: list[sqlite3.Connection] = []
        self._reading = 0
        self._swapping = False
        self._open = False
        # How the last load went: "reused", "reconciled" or "rebuilt".
        self.loaded: str | None = None
        # The last commit that moved the revision (or the last rebuild), for the keeper to announce.
        self.last_commit: IndexCommit | None = None
        # Every write to the file: the keeper's commits, the projection queue's and a rebuild's swap.
        self._write_lock = threading.RLock()
        # The domains committed since the keeper last announced (``take_committed``).
        self._committed: set[str] = set()
        # Told after each projection commit, outside the write lock (the keeper announces it).
        self.projection_listener: Callable[[], None] | None = None

    # ---- the writer

    def load(self, lines: Iterable[str], scanned_at_ns: int) -> IndexToken:
        """Open the kept file, or rebuild it; never migrate one.

        ``lines`` are the layout's lines now. A kept file whose stamp matches
        is reconciled with them: only the places whose line moved since it was
        written, or whose time was then too new to be trusted, are projected
        again. Raises ``IndexUnavailable`` when another process holds the
        index or when a rebuild itself fails.
        """

        self.lock()
        places = line_places(lines)
        stamp = self._stamp()
        kept = self._open_kept(stamp)
        if kept is None:
            self._rebuild(stamp, places, scanned_at_ns)
            self.loaded = "rebuilt"
            return self._token
        stored, stored_scan = kept
        changed = {key for key in stored.keys() | places.keys()
                   if stored.get(key, (None,))[0] != places.get(key, (None,))[0]}
        # Git's racy rule, across a restart: a line read too close to its
        # time may hide a later write with the same time.
        changed |= _racy(stored, stored_scan)
        self._places = stored
        self._racy = set()
        self._apply(changed, set(), places, scanned_at_ns)
        self.loaded = "reconciled" if changed else "reused"
        self._publish_open()
        return self._token

    def lock(self) -> None:
        """Take ``index.lock`` for this writer, unless it holds it already.

        Raises ``IndexLocked`` while another writer holds it; ``load`` takes it too.
        """

        if self._lease is None:
            self._lease = _WriterLease(self.directory)

    def apply(self, lines: Iterable[str] | None, scanned_at_ns: int, *,
              areas: Iterable[str] = (), reread: Iterable[str] = ()) -> IndexToken:
        """Project again what moved, as one commit; the revision moves only when a row changed.

        ``lines`` are the layout's lines now (None: unchanged since the last
        call); ``areas`` names areas known to have changed besides (this
        process's writes), ``reread`` project-relative directories whose files
        may have changed in place.
        """

        if self._writer is None:
            raise IndexUnavailable("the project index is not open")
        places = self._places if lines is None else line_places(lines)
        changed = {key for key in self._places.keys() | places.keys()
                   if self._places.get(key, (None,))[0] != places.get(key, (None,))[0]}
        if lines is not None:
            # A place read racily before is read again once it has settled.
            changed |= {key for key in self._racy
                        if key not in places or scanned_at_ns - places[key][1] > FINGERPRINT_SETTLED_NS}
        extra = set(areas) | {path_area(relative) for relative in reread}
        self._apply(changed, extra, places, scanned_at_ns if lines is not None else None)
        return self._token

    def rebuild(self, lines: Iterable[str], scanned_at_ns: int) -> IndexToken:
        """Project everything again into a new file under a new epoch (the writer's repair)."""

        if self._lease is None:
            raise IndexUnavailable("the project index is not open")
        self._rebuild(self._stamp(), line_places(lines), scanned_at_ns)
        self.loaded = "rebuilt"
        return self._token

    def run_ids(self) -> tuple[str, ...]:
        """The runs the index holds rows for."""

        return tuple(sorted(self._runs))

    def close(self) -> None:
        """Close the writer and every reader, and give up the lease."""

        with self._gate:
            self._open = False
            pool, self._pool = self._pool, []
        for connection in pool:
            connection.close()
        with self._write_lock:
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            self._token = None

    @property
    def token(self) -> IndexToken | None:
        return self._token

    def _open_kept(self, stamp: IndexStamp) -> tuple[dict[str, tuple[str, int]], int] | None:
        """The kept file's places and scan time, or None when it must be rebuilt."""

        if not self.path.exists():
            return None
        connection = None
        try:
            connection = self._connect(self.path)
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("quick_check failed")
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            if any(meta.get(key) != value for key, value in stamp.items().items()):
                connection.close()
                return None
            if not meta["epoch"] or int(meta["revision"]) < 1:
                raise ValueError("the index states no epoch or revision")
            stored = {key: (line, mtime) for key, line, mtime in
                      connection.execute("SELECT key, line, mtime_ns FROM place")}
            self._writer = connection
            self._read_state()
            return stored, int(meta["scanned_at_ns"])
        except (sqlite3.DatabaseError, KeyError, ValueError, json.JSONDecodeError) as exc:
            if connection is not None:
                connection.close()
            self._writer = None
            self._quarantine(exc)
            return None

    def _quarantine(self, reason: BaseException) -> None:
        """Move a file SQLite cannot read aside, with its journal; it is rebuilt once.

        Only the latest such file is kept: it is there to be looked at, and a
        cache directory must not grow with every failure.
        """

        for earlier in self.directory.glob(f"{INDEX_FILE}*.corrupt-*"):
            earlier.unlink()
        suffix = f".corrupt-{time.time_ns()}"
        for name in (INDEX_FILE, f"{INDEX_FILE}-wal", f"{INDEX_FILE}-shm"):
            source = self.directory / name
            if source.exists():
                os.replace(source, self.directory / (name + suffix))
        _LOG.warning("project index %s could not be read (%s); moved aside as %s and rebuilt",
                     self.path, reason, INDEX_FILE + suffix)

    @staticmethod
    def _connect(path: Path, *, reader: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(os.fspath(path), check_same_thread=False, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=2000")
            if reader:
                # The file is already WAL (persistent); a reader never writes.
                connection.execute("PRAGMA query_only=1")
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
        except BaseException:
            # A file SQLite cannot read fails here, before the caller holds the
            # connection: close it, or Windows keeps the file from being moved aside.
            connection.close()
            raise
        return connection

    def _rebuild(self, stamp: IndexStamp, places: Mapping[str, tuple[str, int]], scanned_at_ns: int) -> None:
        """Project everything into a new file, then rename it over the old one."""

        # Projected before anything is written: a failing projector leaves the old file as it was.
        tree = self._projector.project_tree()
        working = self._projector.project_working()
        runs = [self._projector.project_run(run_id) for run_id in self._projector.run_ids()]
        fresh = self.directory / f"{INDEX_FILE}.new"
        # Held from carrying the projection rows over until the new file is in
        # place: a projection written meanwhile would land in the old file.
        self._write_lock.acquire()
        try:
            for stale in (fresh, Path(f"{fresh}-journal")):
                if stale.exists():
                    stale.unlink()
            building = sqlite3.connect(os.fspath(fresh), isolation_level=None)
            try:
                building.executescript(_SCHEMA)
                self._carry_projections(building)
                building.execute("BEGIN")
                for key, value in stamp.items().items():
                    building.execute("INSERT INTO meta VALUES (?, ?)", (key, value))
                building.execute("INSERT INTO meta VALUES ('epoch', ?)", (uuid4().hex,))
                building.execute("INSERT INTO meta VALUES ('revision', '1')")
                # The change log starts empty: a client of another epoch reads everything.
                building.execute("INSERT INTO meta VALUES ('floor', '1')")
                building.execute("INSERT INTO meta VALUES ('scanned_at_ns', ?)", (str(scanned_at_ns),))
                self._write_tree(building, tree, 1, None)
                self._working = None
                if working is not None:
                    self._write_working(building, working, 1)
                for rows in runs:
                    self._write_run(building, rows, 1, None)
                building.executemany("INSERT INTO place VALUES (?, ?, ?)",
                                     [(key, line, mtime) for key, (line, mtime) in places.items()])
                building.execute("COMMIT")
            finally:
                building.close()
            self._swap(fresh)
            self._places = dict(places)
            self._racy = _racy(places, scanned_at_ns)
            self._read_state()
            self._note_commit(IndexCommit(self._token, frozenset({"reset"})))
        except (sqlite3.Error, OSError) as exc:
            raise IndexUnavailable(f"the project index could not be rebuilt: {exc}") from exc
        finally:
            self._write_lock.release()
        self._publish_open()

    def _carry_projections(self, building: sqlite3.Connection) -> None:
        """Copy the projection status rows of the file being replaced, if it has readable ones.

        They are a cache's state, not rows of P036: a new projector or a
        repaired file must not make every picture be drawn again. Their
        revisions start again with the new epoch. A file whose table differs
        (another schema) or cannot be read carries nothing.
        """

        if not self.path.exists():
            return
        try:
            building.execute("ATTACH DATABASE ? AS kept", (os.fspath(self.path),))
        except sqlite3.Error:
            return
        try:
            columns = ", ".join(_PROJECTION_COLUMNS[:-1])
            building.execute(f"INSERT INTO projection ({columns}, rev) SELECT {columns}, 1 FROM kept.projection")
        except sqlite3.Error:
            building.execute("DELETE FROM projection")
        finally:
            try:
                building.execute("DETACH DATABASE kept")
            except sqlite3.Error:
                pass

    def _swap(self, fresh: Path) -> None:
        """Rename ``fresh`` over the index once no reader has the old file open."""

        with self._gate:
            self._swapping = True
            deadline = time.monotonic() + SWAP_WAIT_S
            while self._reading and time.monotonic() < deadline:
                self._gate.wait(deadline - time.monotonic())
            pool, self._pool = self._pool, []
        try:
            for connection in pool:
                connection.close()
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            # The old file's journal must not be replayed into the new one.
            for name in (f"{INDEX_FILE}-wal", f"{INDEX_FILE}-shm"):
                journal = self.directory / name
                if journal.exists():
                    journal.unlink()
            os.replace(fresh, self.path)
            self._writer = self._connect(self.path)
        finally:
            with self._gate:
                self._swapping = False
                self._gate.notify_all()

    def _publish_open(self) -> None:
        with self._gate:
            self._open = True

    def _read_state(self) -> None:
        connection = self._writer
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        self._token = IndexToken(meta["epoch"], int(meta["revision"]))
        self._runs = {run_id: (frozenset(json.loads(cites)), digest, aside_digest)
                      for run_id, cites, digest, aside_digest in connection.execute(
                          "SELECT run_id, cites, digest, aside_digest FROM run")}
        tree_cites = json.loads(meta.get("tree_cites", "[]"))
        self._tree = (frozenset(tree_cites), meta["tree_digest"]) if "tree_digest" in meta else None
        self._working = meta.get("working_digest")

    def _apply(self, changed: set[str], extra: set[str], places: Mapping[str, tuple[str, int]],
               scanned_at_ns: int | None) -> None:
        areas = {place_area(key) for key in changed} | extra
        # The areas without rows whose lines moved, or that this process wrote:
        # each is an entity of its own. A line only read again (racy) moved nothing.
        moved_areas = {entity_area(place_area(key)) for key in changed
                       if self._places.get(key, (None,))[0] != places.get(key, (None,))[0]}
        moved_areas |= {entity_area(area) for area in extra}
        moved_areas.discard(None)
        if "manifest" in areas:
            stamp = self._stamp()
            with self._write_lock:
                meta = dict(self._writer.execute("SELECT key, value FROM meta"))
            if any(meta.get(key) != value for key, value in stamp.items().items()):
                # A different project, or one of another format: never migrated.
                self._rebuild(stamp, places, scanned_at_ns if scanned_at_ns is not None else time.time_ns())
                return
        if areas:
            current = self._projector.run_ids()
            dirty = {area[4:] for area in areas if area.startswith("run:")}
            dirty |= set(current) ^ self._runs.keys()
            dirty |= {run_id for run_id, (cites, *_) in self._runs.items() if cites & areas}
            tree_due = self._tree is None or "branches" in areas or bool(self._tree[0] & areas)
            # Projected outside the transaction: readers and the next commit never wait for it.
            tree = self._projector.project_tree() if tree_due else None
            working = self._projector.project_working() if "working" in areas else None
            present = set(current)
            projected = {run_id: self._projector.project_run(run_id) if run_id in present else None
                         for run_id in sorted(dirty)}
        else:
            tree, projected, working = None, {}, None
        if not changed and not projected and tree is None and working is None and not moved_areas:
            self._places = dict(places)
            if scanned_at_ns is not None:
                self._racy = _racy(places, scanned_at_ns)
            return
        with self._write_lock:
            self._commit_apply(changed, places, scanned_at_ns, moved_areas, tree, working, projected)

    def _commit_apply(self, changed, places, scanned_at_ns, moved_areas, tree, working, projected) -> None:
        connection = self._writer
        if connection is None:
            raise IndexUnavailable("the project index is not open")
        revision = self._token.revision + 1
        connection.execute("BEGIN IMMEDIATE")
        try:
            logged: set[str] = set(moved_areas)
            if tree is not None and self._write_tree(connection, tree, revision, self._tree):
                logged.add("tree")
            if working is not None and self._write_working(connection, working, revision):
                logged.add("working")
            for run_id, rows in projected.items():
                if rows is None:
                    if run_id in self._runs:
                        self._delete_run(connection, run_id)
                        logged.add(f"run:{run_id}")
                        if self._runs[run_id][2] != _NO_ASIDE:
                            logged.add(f"aside:{run_id}")
                else:
                    logged |= self._write_run(connection, rows, revision, self._runs.get(run_id))
            moved = bool(logged)
            for key in changed:
                if key in places:
                    line, mtime = places[key]
                    connection.execute("INSERT OR REPLACE INTO place VALUES (?, ?, ?)", (key, line, mtime))
                else:
                    connection.execute("DELETE FROM place WHERE key = ?", (key,))
            if scanned_at_ns is not None:
                connection.execute("INSERT OR REPLACE INTO meta VALUES ('scanned_at_ns', ?)", (str(scanned_at_ns),))
            if moved:
                self._log_changes(connection, revision, logged)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            self._read_state()
            raise
        self._places = dict(places)
        if scanned_at_ns is not None:
            self._racy = _racy(places, scanned_at_ns)
        self._read_state()
        if moved:
            self._note_commit(IndexCommit(self._token, frozenset(
                "area" if entity.startswith("area:") else entity.partition(":")[0] for entity in logged)))

    @staticmethod
    def _log_changes(connection: sqlite3.Connection, revision: int, logged: Iterable[str]) -> None:
        """Move the revision to ``revision`` and log the entities that moved at it."""

        connection.execute("UPDATE meta SET value = ? WHERE key = 'revision'", (str(revision),))
        connection.executemany("INSERT OR REPLACE INTO change VALUES (?, ?)",
                               [(entity, revision) for entity in sorted(logged)])
        floor = revision - CHANGE_LOG_REVISIONS
        if floor > 1:
            # Past the log's reach: a client that far behind reads a whole snapshot.
            connection.execute("DELETE FROM change WHERE revision <= ?", (floor,))
            connection.execute("UPDATE meta SET value = ? WHERE key = 'floor' AND CAST(value AS INTEGER) < ?",
                               (str(floor), floor))

    def _note_commit(self, commit: IndexCommit) -> None:
        self.last_commit = commit
        with self._gate:
            self._committed |= commit.domains

    def take_committed(self) -> frozenset[str]:
        """The domains committed since the last call: what the keeper's next announcement names."""

        with self._gate:
            domains, self._committed = frozenset(self._committed), set()
        return domains

    # ---- the projection status table (#367)

    @contextmanager
    def _projection_write(self) -> Iterator[tuple[sqlite3.Connection, set[str]]]:
        """One transaction on the projection table; the entities it names in the set move the revision."""

        moved: set[str] = set()
        with self._write_lock:
            connection = self._writer
            if connection is None or self._token is None:
                raise IndexUnavailable("the project index is not open")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection, moved
                if moved:
                    revision = self._token.revision + 1
                    connection.executemany("UPDATE projection SET rev = ? WHERE key = ?",
                                           [(revision, entity.partition(":")[2]) for entity in moved])
                    self._log_changes(connection, revision, moved)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            if moved:
                self._token = IndexToken(self._token.epoch, revision)
                self._note_commit(IndexCommit(self._token, frozenset({"projections"})))
        listener = self.projection_listener
        if moved and listener is not None:
            listener()

    def _projection_read(self, sql: str, params: tuple = ()) -> list[ProjectionRow]:
        with self._write_lock:
            connection = self._writer
            if connection is None:
                raise IndexUnavailable("the project index is not open")
            return [_projection_row(row) for row in connection.execute(
                f"SELECT {', '.join(_PROJECTION_COLUMNS)} FROM projection{sql}", params)]

    @staticmethod
    def _get_projection(connection: sqlite3.Connection, key: str) -> ProjectionRow | None:
        found = connection.execute(f"SELECT {', '.join(_PROJECTION_COLUMNS)} FROM projection WHERE key = ?",
                                   (key,)).fetchone()
        return None if found is None else _projection_row(found)

    def projection(self, key: str) -> ProjectionRow | None:
        found = self._projection_read(" WHERE key = ?", (key,))
        return found[0] if found else None

    def projections(self) -> tuple[ProjectionRow, ...]:
        """Every status row, by key."""

        return tuple(self._projection_read(" ORDER BY key"))

    def enqueue_projection(self, row: ProjectionRow, *, now: float) -> ProjectionRow:
        """Insert ``row`` as queued unless its key has a row (insert-ignore: the insert is the claim on the key)."""

        with self._projection_write() as (connection, _):
            connection.execute(
                f"INSERT OR IGNORE INTO projection ({', '.join(_PROJECTION_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_PROJECTION_COLUMNS))})",
                (row.key, row.input_hash, row.kind, row.recipe_hash, row.renderer_version, _text(row.body),
                 PROJECTION_PENDING, 0, None, None, None, None, None, None, now, 0))
            return self._get_projection(connection, row.key)

    def claim_projection(self, key: str, *, now: float) -> ProjectionRow | None:
        """Lease a queued row (attempts + 1); None when it is not queued."""

        with self._projection_write() as (connection, _):
            claimed = connection.execute(
                "UPDATE projection SET claimed_at = ?, attempts = attempts + 1, touched_at = ? "
                "WHERE key = ? AND status = ? AND claimed_at IS NULL", (now, now, key, PROJECTION_PENDING)).rowcount
            return self._get_projection(connection, key) if claimed else None

    def finish_projection(self, key: str, *, blob_sha256: str, load_ms: int, render_ms: int, now: float) -> None:
        """A leased row is done: it becomes an entity (``projections:<key>``) and the revision moves."""

        with self._projection_write() as (connection, moved):
            if connection.execute(
                    "UPDATE projection SET status = ?, claimed_at = NULL, blob_sha256 = ?, error = NULL, "
                    "next_attempt_at = NULL, load_ms = ?, render_ms = ?, touched_at = ? WHERE key = ?",
                    (PROJECTION_DONE, blob_sha256, load_ms, render_ms, now, key)).rowcount:
                moved.add(f"projections:{key}")

    def fail_projection(self, key: str, *, error: str, next_attempt_at: float | None, now: float) -> None:
        with self._projection_write() as (connection, _):
            connection.execute(
                "UPDATE projection SET status = ?, claimed_at = NULL, error = ?, next_attempt_at = ?, touched_at = ? "
                "WHERE key = ?", (PROJECTION_ERROR, error, next_attempt_at, now, key))

    def retry_projection(self, key: str, body: Mapping[str, Any], *, now: float) -> ProjectionRow | None:
        """Queue an error row again, keeping its attempts, with ``body``; None when it is not an error."""

        with self._projection_write() as (connection, _):
            if not connection.execute(
                    "UPDATE projection SET status = ?, claimed_at = NULL, next_attempt_at = NULL, body = ?, "
                    "touched_at = ? WHERE key = ? AND status = ?",
                    (PROJECTION_PENDING, _text(body), now, key, PROJECTION_ERROR)).rowcount:
                return None
            return self._get_projection(connection, key)

    def reclaim_projections(self, *, now: float, lease_s: float) -> tuple[str, ...]:
        """Turn leases older than ``lease_s`` (all of them at startup) back into queued rows."""

        with self._projection_write() as (connection, _):
            stale = [key for (key,) in connection.execute(
                "SELECT key FROM projection WHERE status = ? AND claimed_at IS NOT NULL AND ? - claimed_at >= ? "
                "ORDER BY key", (PROJECTION_PENDING, now, lease_s))]
            connection.executemany("UPDATE projection SET claimed_at = NULL WHERE key = ?", [(key,) for key in stale])
            return tuple(stale)

    def touch_projection(self, key: str, *, now: float) -> None:
        """A row was asked for: its grace window starts over."""

        with self._projection_write() as (connection, _):
            connection.execute("UPDATE projection SET touched_at = ? WHERE key = ?", (now, key))

    def drop_projections(self, keys: Iterable[str], *, touched: Mapping[str, float] | None = None) -> None:
        """Remove rows (a cancel gives its attempt back this way); a done row that goes moves the revision.

        With ``touched``, a row is removed only while its ``touched_at`` is
        still the one given: a row asked for again meanwhile stays.
        """

        keys = sorted(set(keys))
        if not keys:
            return
        with self._projection_write() as (connection, moved):
            for key in keys:
                row = self._get_projection(connection, key)
                if row is None or (touched is not None and key in touched and row.touched_at != touched[key]):
                    continue
                connection.execute("DELETE FROM projection WHERE key = ?", (key,))
                if row.status == PROJECTION_DONE:
                    moved.add(f"projections:{key}")

    def model_inputs(self) -> frozenset[str]:
        """Every content hash an artifact row of the index names: what a projection's input is reachable from."""

        with self._write_lock:
            connection = self._writer
            if connection is None:
                raise IndexUnavailable("the project index is not open")
            return frozenset(sha for (sha,) in connection.execute(
                "SELECT DISTINCT sha256 FROM artifact WHERE sha256 IS NOT NULL"))

    def _write_tree(self, connection: sqlite3.Connection, tree: TreeRows, revision: int,
                    kept: tuple[frozenset[str], str] | None) -> bool:
        texts = [_text(tree.body), *(_text([row.branch_id, row.stage_ref, row.candidate_id, row.body])
                                     for row in tree.stages)]
        cites = sorted(tree.cites | {"branches"})
        digest = hashlib.sha256("\n".join([*texts, _text(cites)]).encode("utf-8")).hexdigest()
        if kept is not None and kept[1] == digest:
            return False
        connection.execute("DELETE FROM stage")
        positions: dict[str, int] = {}
        for row in tree.stages:
            position = positions.get(row.branch_id, 0)
            positions[row.branch_id] = position + 1
            connection.execute("INSERT INTO stage VALUES (?, ?, ?, ?, ?, ?)",
                               (row.branch_id, position, row.stage_ref, row.candidate_id, revision, _text(row.body)))
        for key, value in (("tree_digest", digest), ("tree", texts[0]), ("tree_cites", _text(cites)),
                           ("tree_rev", str(revision))):
            connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        return True

    def _write_working(self, connection: sqlite3.Connection, working: Mapping[str, Any], revision: int) -> bool:
        """Keep the working position when it moved; False when it reads as it did."""

        text = _text(working)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest == self._working:
            return False
        for key, value in (("working", text), ("working_digest", digest), ("working_rev", str(revision))):
            connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        return True

    def _write_run(self, connection: sqlite3.Connection, rows: RunRows, revision: int,
                   kept: tuple[frozenset[str], str, str] | None) -> set[str]:
        """Keep one run's rows; the entities that moved (``run:<id>``, ``aside:<id>``), none when it reads as it did.

        The run and what it keeps aside have a digest each: a new scene or
        annotation rewrites the aside records and moves ``aside:<id>`` alone.
        """

        run_id = rows.run_id
        aside = [(row.uri, row.kind, row.sha256) for row in rows.aside]
        aside_digest = _NO_ASIDE if not aside else hashlib.sha256(_text(aside).encode("utf-8")).hexdigest()
        records = [(row.uri, row.kind, row.sha256) for row in rows.records]
        artifacts = [(row.sha256, row.format, row.representation, int(row.available), _text(row.body))
                     for row in rows.artifacts]
        documents = [(row.asset_sha256, row.revision_ref, _text(row.body)) for row in rows.documents]
        candidate = (None if rows.candidate is None
                     else (rows.candidate.source_stage_ref, _text(rows.candidate.body)))
        cites = _text(sorted(rows.cites))
        body = _text(rows.body)
        digest = hashlib.sha256(_text([body, cites, records, artifacts, documents, candidate]).encode("utf-8"))
        digest = digest.hexdigest()
        moved = set()
        if kept is None or kept[1] != digest:
            moved.add(f"run:{run_id}")
        if (_NO_ASIDE if kept is None else kept[2]) != aside_digest:
            moved.add(f"aside:{run_id}")
        if not moved:
            return moved
        aside_rows = [(run_id, *row, revision, 1) for row in aside]
        if f"run:{run_id}" not in moved:
            connection.execute("DELETE FROM record WHERE run_id = ? AND aside = 1", (run_id,))
            connection.executemany("INSERT OR REPLACE INTO record VALUES (?, ?, ?, ?, ?, ?)", aside_rows)
            connection.execute("UPDATE run SET aside_rev = ?, aside_digest = ? WHERE run_id = ?",
                               (revision, aside_digest, run_id))
            return moved
        kept_aside = connection.execute("SELECT aside_rev FROM run WHERE run_id = ?", (run_id,)).fetchone()
        aside_rev = revision if f"aside:{run_id}" in moved or kept_aside is None else kept_aside[0]
        self._delete_run(connection, run_id)
        connection.execute("INSERT INTO run VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (run_id, revision, digest, cites, body, aside_rev, aside_digest))
        connection.executemany("INSERT OR REPLACE INTO record VALUES (?, ?, ?, ?, ?, ?)",
                               [(run_id, *row, revision, 0) for row in records] + aside_rows)
        connection.executemany("INSERT INTO artifact VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               [(run_id, position, *row[:4], revision, row[4])
                                for position, row in enumerate(artifacts)])
        connection.executemany("INSERT INTO document VALUES (?, ?, ?, ?, ?, ?)",
                               [(run_id, position, *row[:2], revision, row[2])
                                for position, row in enumerate(documents)])
        if candidate is not None:
            connection.execute("INSERT INTO candidate VALUES (?, ?, ?, ?)",
                               (run_id, candidate[0], revision, candidate[1]))
        return moved

    @staticmethod
    def _delete_run(connection: sqlite3.Connection, run_id: str) -> None:
        for table in ("run", "record", "artifact", "document", "candidate"):
            connection.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))

    # ---- readers

    @contextmanager
    def snapshot(self) -> Iterator[IndexSnapshot]:
        """One read transaction on a connection of its own; raises ``IndexUnavailable`` when none can be had.

        It never waits for the writer: WAL gives every reader the last commit.
        While a rebuild renames a new file into place it does not wait
        either; it refuses, and the caller reads the project itself.
        """

        with self._gate:
            if not self._open or self._swapping:
                raise IndexUnavailable("the project index is not open")
            self._reading += 1
            connection = self._pool.pop() if self._pool else None
        clean = False
        try:
            try:
                if connection is None:
                    connection = self._connect(self.path, reader=True)
                connection.execute("BEGIN")
                snapshot = IndexSnapshot(connection)
            except (sqlite3.Error, KeyError, ValueError) as exc:
                raise IndexUnavailable(f"the project index could not be read: {exc}") from exc
            try:
                yield snapshot
            except sqlite3.Error as exc:
                raise IndexUnavailable(f"the project index could not be read: {exc}") from exc
            finally:
                try:
                    connection.execute("COMMIT")
                    clean = True
                except sqlite3.Error:
                    clean = False
        finally:
            with self._gate:
                self._reading -= 1
                if connection is not None:
                    if clean and self._open and not self._swapping:
                        self._pool.append(connection)
                    else:
                        connection.close()
                self._gate.notify_all()

    def query(self, table: str, filters: Mapping[str, str] | None = None, *,
              limit: int | None = QUERY_LIMIT) -> list[dict[str, Any]]:
        """``IndexSnapshot.rows`` in a snapshot of its own."""

        with self.snapshot() as snapshot:
            return snapshot.rows(table, filters, limit=limit)

    def meta(self) -> dict[str, str]:
        with self.snapshot() as snapshot:
            return snapshot.meta()
