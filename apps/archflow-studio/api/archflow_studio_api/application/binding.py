"""The one project this process answers for, and the run that answers for it.

Binding is a kernel question: ``open_located_project`` finds the repository and
``read_head`` states the exact canonical version. Choosing the *reference run*
is the only judgement here, and it is made in the open: the request may name a
run, the operator may configure one, and otherwise the newest run that actually
finished design work wins. The choice and its source both travel on the wire so
no client has to guess which run a number belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from starlette.datastructures import State

from archflow.project.location import open_located_project
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectRepositoryError,
)

from ..settings import PROJECT_DIR_ENV, REFERENCE_RUN_ENV, StudioSettings
from ..transport.errors import StudioError, error_sentence

# ``<kind>-<64 hex>.json``. The kind is compared by equality: a prefix test
# would let ``runner-run-receipt-summary`` answer as a run receipt.
RECORD_NAME = re.compile(r"^(?P<kind>.+)-(?P<sha>[0-9a-f]{64})\.json$")

RUNNER_RECEIPT_KIND = "runner-run-receipt"
STAGE_WORKFLOW_SCHEMA = "ProjectStageWorkflow@1"
RUNNER_RECEIPT_V3 = "RunnerRunReceipt@3"

# Runs whose workflow says they exist to compare or to answer the Studio, not
# to carry the design forward. They may be the newest complete runs in the
# project and they must still never become its reference.
HARNESS_WORKFLOW_IDS = frozenset(
    {"equivalence-harness", "studio-candidate-harness"}
)

# The run id a projection is bound to when the project holds no run that can
# answer for it. It names no run on disk, and the projection says so.
STUDIO_RUN_ID = "studio-projection"


@dataclass(frozen=True, slots=True)
class ReferenceRun:
    """The run a projection answers for, and how it came to be chosen."""

    run: RunRef
    source: str
    receipt: Mapping[str, Any] | None
    # Run directories the survey could not read. One corrupt run must not cost
    # the client every answer, but the skip is never silent.
    skipped_runs: tuple[str, ...] = ()
    # The chosen receipt names a workflow that would not load, so whether the
    # run was a harness is unknown rather than settled.
    workflow_unresolved: bool = False


def record_kind(ref: ProjectRecordRef) -> str | None:
    """The record kind in a P036 record name, or None if it is not one."""

    match = RECORD_NAME.match(ref.relative_path.rsplit("/", 1)[-1])
    return None if match is None else match.group("kind")


class ProjectBinding:
    """One opened P036 project. Read-only: it never writes and never promotes."""

    def __init__(
        self,
        repository: FilesystemProjectRepository,
        *,
        project_id: str,
        project_dir: Path,
        settings: StudioSettings,
    ) -> None:
        self.repository = repository
        self.project_id = project_id
        self.project_dir = project_dir
        # Kept so ``reference_run`` can honour the configured run without the
        # routes having to pass settings back in on every request.
        self.settings = settings
        # File identity, memoized per opened project. The key carries size and
        # modification time, so a file that changed is hashed again rather than
        # remembered wrongly.
        self.file_sha256_cache: dict[tuple[str, int, int], str] = {}

    @classmethod
    def open(cls, settings: StudioSettings) -> ProjectBinding:
        """Open the configured project, or refuse and say what went wrong."""

        project_dir = Path(settings.project_dir)
        try:
            location, repository = open_located_project(
                project_dir.name,
                local_projects_root=project_dir.parent,
            )
        except Exception as exc:  # the reason belongs on the wire, not in a log
            raise StudioError(
                503,
                "PROJECT_NOT_BOUND",
                "the configured project could not be opened: "
                f"{error_sentence(exc)}. The Studio API binds the project "
                f"named by {PROJECT_DIR_ENV} or --project-dir; it never "
                "guesses one.",
            ) from exc
        return cls(
            repository,
            project_id=location.project_id,
            project_dir=location.root,
            settings=settings,
        )

    def head(self) -> ProjectVersionRef:
        """The exact canonical version this project is at right now."""

        return self.repository.read_head()

    def run_ids(self) -> tuple[str, ...]:
        """Every run directory in the project, in name order."""

        runs = self.repository.layout.runs
        if not runs.is_dir():
            return ()
        return tuple(sorted(item.name for item in runs.iterdir() if item.is_dir()))

    def load_run(self, run_id: str) -> RunRef:
        """The named run, or a 404 that repeats the name it was given.

        The detail names the project and the run and stops there. Where this
        service keeps the project on disk is an operator's question, answered
        by ``projectDir`` on ``GET /api/project``; it is no part of an answer
        about a run, and a refusal is read by whoever ran into it.
        """

        try:
            return self.repository.load_run(run_id)
        except Exception as exc:
            raise StudioError(
                404,
                "RUN_NOT_FOUND",
                f"{self.project_id}: run {run_id!r} does not exist in the "
                f"bound project: {error_sentence(exc)}",
            ) from exc

    def record_refs(self, run_id: str) -> tuple[ProjectRecordRef, ...]:
        """Every retained JSON record of one run, digest-verified by P036."""

        return self.repository.list_json(
            run=self.load_run(run_id),
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run_id
            ),
        )

    def latest_runner_receipt(
        self,
    ) -> tuple[str, ProjectRecordRef, Mapping[str, Any]] | None:
        """The newest complete, non-harness runner receipt in the project."""

        return self._survey()[0]

    def newest_runner_receipt(
        self, run_id: str
    ) -> tuple[ProjectRecordRef, Mapping[str, Any]] | None:
        """One run's newest ``runner-run-receipt``, with the record it is in.

        The ref travels beside the payload because the receipt does not name
        itself on disk: the runner writes its own ``receipt_ref`` into the
        mapping it returns and not into the record it retains, so this ref is
        the only thing a caller can name the evidence with.

        This is the one place a run's receipt is chosen. Everything that asks
        "which receipt does this run answer with" asks here, so a second reader
        cannot start preferring a different one.
        """

        newest: tuple[float, ProjectRecordRef, Mapping[str, Any]] | None = None
        for ref, payload in self._receipts_of(run_id):
            try:
                mtime = self._mtime(ref)
            except OSError:
                # Listed and then gone. A record nobody can stat cannot be
                # this run's newest receipt, and a file disappearing between
                # two reads is not a bug in the reader.
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, ref, payload)
        return None if newest is None else (newest[1], newest[2])

    def _mtime(self, ref: ProjectRecordRef) -> float:
        """When the record was last written; the only ordering P036 offers.

        The receipt carries no authored timestamp, so "newest" can only mean
        newest on disk — a kernel card, not a choice made here.
        """

        return self.repository.layout.resolve_record(ref).stat().st_mtime

    def _survey(
        self,
    ) -> tuple[
        tuple[str, ProjectRecordRef, Mapping[str, Any]] | None,
        tuple[str, ...],
    ]:
        """Survey the runs for a reference, reporting what could not be read.

        Choosing a reference run is a survey, not a transaction: one run
        directory without a manifest, or with a record the repository refuses,
        must not cost the client every other answer in the project. Such a run
        is skipped and named, and the projection says how many were skipped.
        """

        newest: tuple[float, str, ProjectRecordRef, Mapping[str, Any]] | None = None
        skipped: list[str] = []
        for run_id in self.run_ids():
            # The whole of one run's reading is inside the tolerance, not just
            # its listing: a record that vanishes between being listed and
            # being stat'ed is the same kind of accident as a run with no
            # manifest, and both are skipped and named rather than fatal.
            try:
                for ref, payload in self._receipts_of(run_id):
                    if not _is_complete(payload):
                        continue
                    if self._is_harness(payload):
                        continue
                    mtime = self._mtime(ref)
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, run_id, ref, payload)
            except (StudioError, ProjectRepositoryError, ValueError, OSError):
                skipped.append(run_id)
                continue
        chosen = None if newest is None else (newest[1], newest[2], newest[3])
        return chosen, tuple(skipped)

    def reference_run(self, run_id: str | None = None) -> ReferenceRun:
        """Resolve which run answers: the request, the operator, then the rule."""

        if run_id is not None:
            # An explicitly named run must exist; the survey's tolerance is for
            # runs nobody asked about.
            return self._chosen(self.load_run(run_id), "query", run_id)
        configured = self.settings.reference_run
        if configured is not None:
            try:
                run = self.load_run(configured)
            except StudioError as exc:
                raise StudioError(
                    404,
                    "RUN_NOT_FOUND",
                    f"{REFERENCE_RUN_ENV} names run {configured!r}, which does "
                    f"not exist in the bound project: {exc.detail}",
                ) from exc
            return self._chosen(run, "config", configured)
        chosen, skipped = self._survey()
        if chosen is None:
            # No run has finished design work here. Rather than refuse to
            # describe the project, bind to a run id that claims nothing.
            return ReferenceRun(
                RunRef(self.project_id, STUDIO_RUN_ID, self.head()),
                "none",
                None,
                skipped_runs=skipped,
            )
        return ReferenceRun(
            self.load_run(chosen[0]),
            "rule",
            chosen[2],
            skipped_runs=skipped,
            workflow_unresolved=self._workflow_unresolved(chosen[2]),
        )

    def _chosen(self, run: RunRef, source: str, run_id: str) -> ReferenceRun:
        """A run the caller named, with whatever receipt it happens to hold.

        A run that loads and whose records the repository then refuses is a
        fact about that run, not a bug: it arrives as the 404 that names it
        rather than as an uncaught repository error. A run that simply holds
        no receipt yet is not refused — it exists, which is all a named run
        was ever asked to be — and answers with no receipt.

        The survey still runs, for its confession alone: a projection that
        skipped a run directory says so whether the run it answers for was
        named in the request or chosen by the rule.
        """

        try:
            receipt = self._newest_receipt_of(run_id)
        except (ProjectRepositoryError, ValueError, OSError) as exc:
            raise StudioError(
                404,
                "RUN_NOT_FOUND",
                f"{self.project_id}: run {run_id!r} exists in the bound "
                f"project and its records could not be read: "
                f"{error_sentence(exc)}",
            ) from exc
        return ReferenceRun(
            run,
            source,
            receipt,
            skipped_runs=self._survey()[1],
            workflow_unresolved=(
                False if receipt is None else self._workflow_unresolved(receipt)
            ),
        )

    # ---- the rule's own reading of the run records

    def _receipts_of(
        self, run_id: str
    ) -> tuple[tuple[ProjectRecordRef, Mapping[str, Any]], ...]:
        return tuple(
            (ref, self.repository.load_json(ref))
            for ref in self.record_refs(run_id)
            if record_kind(ref) == RUNNER_RECEIPT_KIND
        )

    def _newest_receipt_of(self, run_id: str) -> Mapping[str, Any] | None:
        newest = self.newest_runner_receipt(run_id)
        return None if newest is None else newest[1]

    def _load_workflow(
        self, receipt: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """The stage workflow the receipt names, or None if it does not resolve."""

        workflow_ref = receipt.get("workflow_ref")
        if workflow_ref is None:
            return None
        workflow = self._load_uri(workflow_ref)
        if workflow is None or workflow.get("schema") != STAGE_WORKFLOW_SCHEMA:
            return None
        return workflow

    def _is_harness(self, receipt: Mapping[str, Any]) -> bool:
        """Whether the receipt's workflow says, positively, that it is a harness.

        Harness runs are defined by what their workflow *is*, never by what it
        might be: a receipt naming no workflow (``RunnerRunReceipt@1``), or one
        whose workflow will not load, stays eligible. Excluding the unknown
        would let one unreadable record silently disqualify a real design run.
        """

        workflow = self._load_workflow(receipt)
        return (
            workflow is not None
            and workflow.get("workflow_id") in HARNESS_WORKFLOW_IDS
        )

    def _workflow_unresolved(self, receipt: Mapping[str, Any]) -> bool:
        """The receipt names a workflow the project cannot produce."""

        return (
            receipt.get("workflow_ref") is not None
            and self._load_workflow(receipt) is None
        )

    def _load_uri(self, uri: object) -> Mapping[str, Any] | None:
        """Load a ``project://`` record reference, or None if it does not resolve."""

        if not isinstance(uri, str):
            return None
        parsed = urlparse(uri)
        if parsed.scheme != "project" or unquote(parsed.netloc) != self.project_id:
            return None
        relative = unquote(parsed.path).lstrip("/")
        match = RECORD_NAME.match(relative.rsplit("/", 1)[-1])
        if match is None:
            return None
        try:
            return self.repository.load_json(
                ProjectRecordRef(
                    project_id=self.project_id,
                    relative_path=relative,
                    sha256=match.group("sha"),
                )
            )
        except Exception:
            return None


def bound_project(state: State) -> ProjectBinding:
    """The process's one binding, opened on first use and kept.

    A failed open is not remembered: a project that appears after the service
    started binds on the next request instead of needing a restart.
    """

    binding = getattr(state, "binding", None)
    if binding is None:
        binding = ProjectBinding.open(state.settings)
        state.binding = binding
    return binding


def _is_complete(receipt: Mapping[str, Any]) -> bool:
    """Whether the run this receipt describes actually finished its seats."""

    if receipt.get("schema") == RUNNER_RECEIPT_V3:
        return bool(receipt.get("seat_execution_complete"))
    return bool(receipt.get("accepted"))
