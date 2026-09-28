"""The Studio's projector for the project index (ADR-008 phase 1b, #365).

``StudioProjector`` is the one derivation that fills ``archflow.project.index``:
for a rebuild, for a reopen that finds the project moved, and for every change
this process or another makes. It runs only on the index keeper's thread,
never on a request's. It derives nothing of its own. Each row is what
the readers the routes already use produce - ``record_refs``,
``_run_artifacts``, ``_run_documents``, ``_candidate_stage_source`` and
``design_history`` - with the same per-run caches (phase 1a) under them.

A run whose reading fails is not left out silently: its row says which part
failed, and a listing that needs that part reads the runs itself, so the
refusal it gives is the one the runs give.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
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
from archflow.project.record_kinds import STUDIO_BOARD_SCENE, STUDIO_DOCUMENT_ANNOTATIONS, STUDIO_MODEL_ANNOTATIONS
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError
from .artifacts import (
    _candidate_stage_source,
    _run_artifacts,
    _run_documents,
    artifact_row_body,
    document_row_body,
)
from .binding import ProjectBinding, record_kind

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
    from . import artifacts, binding

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
            delta = binding.candidate_delta(run_id)
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
            })
            if stage_uri is not None:
                cites.add(path_area(record_ref_from_uri(stage_uri, binding.project_id).relative_path))
        if delta is not None or body.get("candidate_unreadable"):
            # A candidate's committed source is checked against the design
            # branches; HEAD and the working draft play no part in it.
            cites.add("branches")
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
        return {"current": value["current"], "active": value["active"], "runsDigest": canonical_digest(value["runs"])}


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
