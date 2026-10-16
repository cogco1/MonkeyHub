"""The Studio's projector for the project index (ADR-008 phase 1b, #365).

``StudioProjector`` is the one derivation that fills ``archflow.project.index``:
for a rebuild, for a reopen that finds the project moved, and for every change
this process or another makes. It runs only on the index keeper's thread,
never on a request's. It derives nothing of its own. Each row is what
the readers the routes already use produce - ``record_refs``,
``_run_artifacts``, ``_run_documents``, ``_candidate_stage_source``,
``design_history``, the parts of each run's change the tree reads
(``RunChanges``, ``change_parts``), each run's part of the reference survey
(``survey_part``) and its newest runner receipt (``newest_runner_receipt``) -
with the same per-run caches (phase 1a) under them.

A run whose reading fails is not left out silently: its row says which part
failed, and a listing that needs that part reads the runs itself, so the
refusal it gives is the one the runs give.

``indexed_runs`` reads back, from one snapshot, what the design tree's
readers - the Worktree Graph, the Working Head and the design history - ask
of every run (#599).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, ContextManager, Mapping
import weakref

from archflow.contracts.canonical import canonical_digest
from archflow.project.index import (
    ArtifactRow,
    CandidateRow,
    DocumentRow,
    IndexKeeper,
    IndexUnavailable,
    ProjectIndex,
    RecordRow,
    RunRows,
    StageRow,
    TreeRows,
    manifest_stamp,
    path_area,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    AUDIT_EVENT,
    DELIBERATION_EPISODE,
    INTENT_COMPILATION,
    PROJECT_STAGE_WORKFLOW,
    STUDIO_BOARD_SCENE,
    STUDIO_DOCUMENT_ANNOTATIONS,
    STUDIO_MODEL_ANNOTATIONS,
    STUDIO_WORKING_COPY,
)
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError

from .errors import StudioError
from .application.artifacts import (
    SourceDocument,
    _candidate_stage_source,
    _document_from_body,
    _ordered_documents,
    _run_artifacts,
    _run_documents,
    artifact_row_body,
    document_row_body,
)
from .binding import KeptRuns, ProjectBinding, RunChanges, change_parts, record_kind

# Moves whenever a row's derivation changes: a kept index built by another
# version is rebuilt, never migrated. The source digest of the projector and
# of the readers it stores is part of it, so a change there rebuilds too.
PROJECTOR_NAME = "studio-projector@1"

# The records a run keeps beside what it shows (``RunRows.aside``): each save of a
# Board scene or of a page's or a model's annotations adds one, and none of them
# changes the run's result, its candidacy or its place in the tree. (Modeling's local
# recovery is no run record at all: saving it moves only the working pointer.)
ASIDE_KINDS = frozenset({STUDIO_BOARD_SCENE, STUDIO_DOCUMENT_ANNOTATIONS, STUDIO_MODEL_ANNOTATIONS})

# What makes one part of a run's projection fail without failing the others.
_UNREADABLE = (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError)


def _projector_version() -> str:
    from .application import artifacts
    from . import binding

    digest = hashlib.sha256()
    for module in (Path(__file__), Path(artifacts.__file__), Path(binding.__file__)):
        digest.update(module.read_bytes())
    return f"{PROJECTOR_NAME}:{digest.hexdigest()[:16]}"


class StudioProjector:
    """The rows of one project, read through one binding.

    The binding is held weakly: the keeper's thread holds this projector, and
    must not keep alive a binding nobody else holds (collecting it stops the
    keeper).
    """

    def __init__(self, binding: ProjectBinding) -> None:
        self._binding = weakref.ref(binding)
        self.version = _projector_version()

    @property
    def binding(self) -> ProjectBinding:
        binding = self._binding()
        if binding is None:
            raise IndexUnavailable("the project's binding is gone")
        return binding

    def run_ids(self) -> tuple[str, ...]:
        return self.binding.run_ids()

    def project_run(self, run_id: str) -> RunRows:
        binding = self.binding
        root = binding.repository.layout.root
        # This pass's one reader of the run's change: its candidate row and its kept parts.
        changes = RunChanges(binding)
        body: dict[str, Any] = {}
        cites: set[str] = set()
        records: tuple[RecordRow, ...] = ()
        aside: tuple[RecordRow, ...] = ()
        try:
            run = binding.load_run(run_id)
            body["base"] = run.base.to_dict()
        except _UNREADABLE:
            body["run_unreadable"] = True
        try:
            rows = [RecordRow(ref.uri, record_kind(ref), ref.sha256) for ref in binding.record_refs(run_id)]
            # Admissions and candidate reviews after the one that created their run
            # are kept in its review area, and each changes what the tree shows.
            rows += [RecordRow(ref.uri, record_kind(ref), ref.sha256) for ref in binding.repository.list_json(
                run=binding.load_run(run_id), destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id))]
            records = tuple(row for row in rows if row.kind not in ASIDE_KINDS)
            aside = tuple(row for row in rows if row.kind in ASIDE_KINDS)
        except _UNREADABLE:
            body["records_unreadable"] = True

        artifacts: tuple[ArtifactRow, ...] = ()
        try:
            rows, unreadable = _run_artifacts(binding, run_id)
            artifacts = tuple(
                ArtifactRow(row.sha256, row.format, row.representation, row.available,
                            artifact_row_body(binding, row))
                for row in rows
            )
            if unreadable:
                body["artifacts_unreadable"] = True
            cites.update(path_area(row.relative_path) for row in rows
                         if row.status == "registered" and row.relative_path is not None)
        except _UNREADABLE:
            artifacts = ()
            body["artifacts_error"] = True

        documents: tuple[DocumentRow, ...] = ()
        try:
            listed = _run_documents(binding, run_id)
            documents = tuple(DocumentRow(document.asset_sha256, document.revision_ref, document_row_body(document))
                              for document in listed)
            for document in listed:
                if document.revision_ref is not None:
                    cites.add(path_area(record_ref_from_uri(document.revision_ref, binding.project_id).relative_path))
        except _UNREADABLE:
            documents = ()
            body["documents_error"] = True

        candidate = None
        try:
            delta = changes(run_id)
        except _UNREADABLE:
            delta = None
            body["candidate_unreadable"] = True
        # Read even without a delta: that is how a listing asks too, and it
        # answers None there; it never raises.
        source = _candidate_stage_source(binding, run_id)
        if delta is not None or source is not None:
            stage_uri = None
            try:
                stage_uri = ProjectRecordRef.from_dict(delta["source_stage_ref"]).uri
            except (KeyError, TypeError, ValueError):
                stage_uri = None
            candidate = CandidateRow(stage_uri, {
                "source": None if source is None else list(source),
                "result_record_digest": delta.get("result_record_digest") if isinstance(delta, dict) else None,
                # What the tree's readers read of the change (#599); RunChanges answers it.
                "change": change_parts(delta) if isinstance(delta, dict) else None,
            })
            if stage_uri is not None:
                cites.add(path_area(record_ref_from_uri(stage_uri, binding.project_id).relative_path))
        if delta is not None or body.get("candidate_unreadable"):
            # A candidate's committed source is checked against the design
            # branches; HEAD and the working draft play no part in it.
            cites.add("branches")

        # The run's part of the reference survey and the receipt it answers
        # with (#599). A receipt's workflow may be retained in another run,
        # which the part then cites.
        survey, opened = binding.survey_part(run_id)
        body["survey"] = survey
        for path in opened:
            try:
                cites.add(path_area(path.relative_to(root).as_posix()))
            except ValueError:
                continue
        try:
            newest = binding.newest_runner_receipt(run_id)
            body["newest_receipt"] = None if newest is None else newest[0].uri
        except _UNREADABLE:
            body["newest_unreadable"] = True
        cites.discard(f"run:{run_id}")
        return RunRows(run_id, body, records, artifacts, documents, candidate, frozenset(cites), aside)

    def project_tree(self) -> TreeRows:
        binding = self.binding
        try:
            branches = binding.repository.read_design_branches()
        except _UNREADABLE:
            return TreeRows({"branches_unreadable": True})
        body: dict[str, Any] = {"branches": branches}
        stages: list[StageRow] = []
        cites: set[str] = set()
        for branch_id in sorted(branches):
            try:
                history = binding.design_history(branch_id)
            except _UNREADABLE as exc:
                body.setdefault("history_errors", {})[branch_id] = getattr(exc, "code", type(exc).__name__)
                continue
            for ref, stage in history:
                cites.add(path_area(ref.relative_path))
                stages.append(StageRow(branch_id, ref.uri, stage.candidate_id, {
                    "parent_stage": None if stage.parent_stage is None else stage.parent_stage.uri,
                    "record_ref": stage.record_ref.uri,
                    "runner_ref": stage.runner_ref.uri,
                    "model_ref": stage.model_ref.uri,
                    "model_sha256": stage.model_sha256,
                    "label": stage.label,
                    "accepted_by": stage.accepted_by,
                    "stage_branch_id": stage.branch_id,
                }))
        return TreeRows(body, tuple(stages), frozenset(cites))

    def project_working(self) -> dict[str, Any] | None:
        # The position every head reader reads (``resolve_working_source``, the Worktree Graph),
        # without ``localDraftRef``: saving Modeling's local recovery moves nothing a head shows (#366).
        # The retained runs' rows (labels, times) move the Worktree Graph too, but no client reads
        # them from here: they count by their digest alone, not sent in every snapshot and delta.
        try:
            value, _ = self.binding.repository.read_working_draft()
        except _UNREADABLE:
            return {"working_unreadable": True}
        result = {"current": value["current"], "active": value["active"], "runsDigest": canonical_digest(value["runs"])}
        if "positions" in value:
            # Peer moves invalidate the tree; private recovery-only writes do not.
            result["actorHeads"] = {actor: {"current": row["current"], "branchId": row["branchId"]}
                                    for actor, row in sorted(value["positions"].items())}
        return result


# What the tree's readers ask the index about before they read a run for it (#599):
# whether it holds a record of a kind in an area. A run holding none is not read
# for it: an Exploration or an accepted episode (legacy admission evidence), an
# intent compilation (the words a run was asked in), the project's frozen stage
# workflow and, beside the run, an audit event (a Continue onto it).
HELD_KINDS = (
    ("records", STUDIO_WORKING_COPY),
    ("records", DELIBERATION_EPISODE),
    ("records", INTENT_COMPILATION),
    ("records", PROJECT_STAGE_WORKFLOW),
    ("reviews", AUDIT_EVENT),
)
# A run projected with one of these flags was not listed whole: it may hold
# anything, so a reader that asks reads it.
_NOT_LISTED = ("run_unreadable", "records_unreadable")


@dataclass(frozen=True, slots=True)
class IndexedRuns:
    """What one snapshot of the project index says of every run, for one read of the design tree (#599).

    ``changes`` is what ``RunChanges`` answers from it: the kept change parts
    of each run whose change was read whole, None for a run without one; any
    other run is read. ``kept`` is what the binding's own readings answer from
    it (``ProjectBinding.indexed_reading``): the reference survey, each run's
    newest runner receipt and the runs that may hold the frozen workflow.
    ``holds`` says whether a run may hold a record of a kind of
    ``HELD_KINDS``: it does, or it was not listed whole, or the snapshot does
    not know it. ``documents`` is the project's document listing, when the
    reader asked for it and every run's documents were read whole; otherwise
    None, and the reader lists them from the runs.
    """

    runs: frozenset[str]
    changes: Mapping[str, Mapping[str, Any] | None]
    kept: KeptRuns
    held: Mapping[tuple[str, str], frozenset[str]]
    documents: tuple[SourceDocument, ...] | None = None

    def holding(self, kind: str, area: str = "records") -> frozenset[str]:
        """The runs of this snapshot that may hold a record of ``kind`` in ``area`` (``records`` or ``reviews``)."""

        return self.held[(area, kind)]

    def holds(self, run_id: str, kind: str, area: str = "records") -> bool:
        """Whether the run may hold a record of ``kind`` in ``area``; a run this snapshot does not know may."""

        return run_id not in self.runs or run_id in self.held[(area, kind)]

    def reading(self, binding: ProjectBinding) -> ContextManager[None]:
        """Read inside this block: the binding's own per-run readings answer from this snapshot, on this thread."""

        return binding.indexed_reading(self.kept)


def indexed_runs(binding: ProjectBinding, *, documents: bool = False) -> IndexedRuns | None:
    """What one snapshot of the index says of every run, or None when it cannot answer (#599).

    None, never an error, whenever the index cannot answer: not loaded,
    behind this process's own writes, rebuilding, SQLite refusing, or rows a
    projector before this one wrote. The reader then reads the runs, so every
    refusal stays the runs' own; so does a run whose change, newest receipt or
    records could not be read when it was projected, which is read from the
    project. ``documents`` also reads the project's documents from the same
    snapshot.
    """

    index = binding.index_reader()
    if index is None:
        return None
    try:
        with index.snapshot() as snapshot:
            runs = {row["run_id"]: row["body"] for row in snapshot.rows("run", limit=None)}
            candidates = {row["run_id"]: row["body"] for row in snapshot.rows("candidate", limit=None)}
            # A record is in a run's area when its name says so: ``project://<project>/runs/<run>/<area>/...``.
            held_rows = {(area, kind): [row["run_id"] for row in snapshot.rows("record", {"kind": kind}, limit=None)
                                        if f"/runs/{row['run_id']}/{area}/" in row["uri"]]
                         for area, kind in HELD_KINDS}
            document_rows = snapshot.rows("document", limit=None) if documents else None
    except (IndexUnavailable, sqlite3.Error, KeyError, TypeError, ValueError):
        return None
    try:
        changes: dict[str, Mapping[str, Any] | None] = {}
        survey: dict[str, tuple[tuple[float, str], ...] | None] = {}
        newest: dict[str, str | None] = {}
        for run_id, body in runs.items():
            candidate = candidates.get(run_id)
            if "survey" not in body or (candidate is not None and "change" not in candidate):
                return None  # rows of a projector before this one: never read as this one's
            if not body.get("candidate_unreadable"):
                changes[run_id] = None if candidate is None else candidate["change"]
            part = body["survey"]
            if part is not None:
                part = tuple((mtime, uri) for mtime, uri in part)
                if not all(isinstance(mtime, (int, float)) and isinstance(uri, str) for mtime, uri in part):
                    return None
            survey[run_id] = part
            if not body.get("newest_unreadable"):
                receipt = body["newest_receipt"]
                if receipt is not None and not isinstance(receipt, str):
                    return None
                newest[run_id] = receipt
        unlisted = frozenset(run_id for run_id, body in runs.items() if any(body.get(flag) for flag in _NOT_LISTED))
    except (KeyError, TypeError, ValueError):
        return None
    held = {key: unlisted | frozenset(found) for key, found in held_rows.items()}
    kept = KeptRuns(survey, newest, held[("records", PROJECT_STAGE_WORKFLOW)])
    return IndexedRuns(frozenset(runs), changes, kept, held, _snapshot_documents(runs, document_rows))


def _snapshot_documents(runs: Mapping[str, Mapping[str, Any]],
                        rows: list[dict[str, Any]] | None) -> tuple[SourceDocument, ...] | None:
    """``list_documents(binding)`` from the snapshot's rows, or None when one run's documents were not read whole."""

    if rows is None or any(body.get("documents_error") for body in runs.values()):
        return None
    try:
        rows = sorted((row for row in rows if row["run_id"] in runs), key=lambda row: (row["run_id"], row["position"]))
        return _ordered_documents(_document_from_body(row["body"]) for row in rows)
    except (KeyError, TypeError, ValueError):
        return None


def attach_project_index(binding: ProjectBinding, directory: Path) -> IndexKeeper:
    """Give ``binding`` its project index in ``directory``, loaded and kept current in the background."""

    projector = StudioProjector(binding)
    root = binding.repository.layout.root
    # Plain values: the stamp, like the projector, must not hold the binding.
    project_id, version = binding.project_id, projector.version
    index = ProjectIndex(
        directory,
        projector=projector,
        stamp=lambda: manifest_stamp(root, projector_version=version, project_id=project_id),
    )
    return binding.use_index(index)
