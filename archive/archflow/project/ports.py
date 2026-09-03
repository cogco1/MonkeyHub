"""Persistence ports only archived lanes use; the spine keeps the ones it uses in archflow/project/ports.py."""

from __future__ import annotations

from typing import Protocol

from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.ports import PersistenceDestination


class ArtifactSink(Protocol):
    def ingest(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef: ...


class CanonicalRepository(Protocol):
    def read_head(self) -> ProjectVersionRef: ...

    def compare_and_swap(
        self,
        *,
        expected: ProjectVersionRef,
        event: ProjectRecordRef,
        replacement: ProjectRecordRef,
    ) -> ProjectVersionRef: ...


class ProjectLoader(Protocol):
    def load_manifest(self) -> ProjectManifest: ...
