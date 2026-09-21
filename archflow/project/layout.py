"""Pure project path mapping with no directory creation or file writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
