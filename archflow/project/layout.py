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
        return self.root / "HEAD"

    @property
    def inputs(self) -> Path:
        return self.root / "input"

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
