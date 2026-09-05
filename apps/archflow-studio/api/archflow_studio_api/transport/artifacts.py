"""The wire form of the artifacts a project's receipts certify.

Every row carries three things together: which run and program produced the
file, which base that run stood on, and the digest its bytes must hash to. A
client that showed the file name alone could not tell a current export from a
stale one — so ``available`` and ``unavailableReason`` travel beside them, and
``available: false`` is a state the UI must render, never an empty list.
``format`` and ``representation`` say what the file is and what it claims to
be, so a client hands only a ``3dm`` to its viewer and never labels a preview
mesh as the exact model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.artifacts import (
    ArtifactListing,
    ArtifactRecord,
    ViewportCapture,
)
from .project import ProjectVersionDto


class ProjectArtifactDto(BaseModel):
    """One exported model, as its receipt describes it and disk answers for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    artifact_id: str = Field(
        alias="artifactId",
        description="the file's sha256, or receipt:<sha> when it claims none",
    )
    run_id: str = Field(alias="runId")
    stage_id: str | None = Field(alias="stageId")
    file_name: str = Field(alias="fileName")
    relative_path: str | None = Field(
        alias="relativePath",
        description="where the certified bytes were found, project-relative; "
        "display only — never send it back as a project path. A run's "
        "workspace may hold an export whose name is not a portable P036 "
        "segment, and resolve_relative rejects non-ASCII segments; the "
        "artifact is addressed by its sha256, never by this string",
    )
    sha256: str | None
    size_bytes: int | None = Field(alias="sizeBytes")
    object_count: int | None = Field(alias="objectCount")
    status: str | None
    readback_verified: bool | None = Field(alias="readbackVerified")
    available: bool
    unavailable_reason: str | None = Field(alias="unavailableReason")
    # The canonical version the producing run was created against.
    base: ProjectVersionDto | None
    branch_id: str | None = Field(alias="branchId")
    branch_epoch: int | None = Field(alias="branchEpoch")
    program_ref: str | None = Field(alias="programRef")
    program_digest: str | None = Field(alias="programDigest")
    design_state_digest: str | None = Field(alias="designStateDigest")
    length_unit: str | None = Field(alias="lengthUnit")
    up_axis: str | None = Field(alias="upAxis")
    receipt_ref: str = Field(
        alias="receiptRef",
        description="the retained export receipt this row was read from; an "
        "in-process (OCCT) receipt certifies two files and is two rows sharing "
        "this ref — one exact STEP, one 3dm preview — never two candidates",
    )
    format: str = Field(
        description="what a reader must know to open the file: 'step' (an "
        "exact B-rep, ISO 10303-21; download it, the viewer cannot load it) or "
        "'3dm' (what the viewer loads)",
    )
    representation: str = Field(
        description="what the receipt claims the geometry is: 'exact' for the "
        "delivered model (a STEP B-rep, or a Rhino export that was read back), "
        "'preview' for a render mesh tessellated from the exact model so it can "
        "be looked at — never a NURBS or B-rep delivery",
    )


class ArtifactListDto(BaseModel):
    """The wire form of ``GET /api/artifacts``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    artifacts: list[ProjectArtifactDto]
    skipped_runs: list[str] = Field(
        alias="skippedRuns",
        description="runs whose records could not be listed, named not hidden",
    )


class ViewportCaptureRequestDto(BaseModel):
    """The loaded run and the PNG bytes the browser captured."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId")
    png_base64: str = Field(alias="pngBase64")


class ViewportCaptureDto(BaseModel):
    """A non-canonical PNG retained below the named run workspace."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    relative_path: str = Field(alias="relativePath")
    sha256: str
    media_type: str = Field(alias="mediaType")
    size_bytes: int = Field(alias="sizeBytes")


def artifact_dto(record: ArtifactRecord) -> ProjectArtifactDto:
    """Shape one artifact for the wire; every value came off its receipt."""

    return ProjectArtifactDto(
        artifact_id=record.artifact_id,
        run_id=record.run_id,
        stage_id=record.stage_id,
        file_name=record.file_name,
        relative_path=record.relative_path,
        sha256=record.sha256,
        size_bytes=record.size_bytes,
        object_count=record.object_count,
        status=record.status,
        readback_verified=record.readback_verified,
        available=record.available,
        unavailable_reason=record.unavailable_reason,
        base=(
            None
            if record.base_version is None
            else ProjectVersionDto(
                version=record.base_version,
                state_sha256=record.base_state_sha256,
            )
        ),
        branch_id=record.branch_id,
        branch_epoch=record.branch_epoch,
        program_ref=record.program_ref,
        program_digest=record.program_digest,
        design_state_digest=record.design_state_digest,
        length_unit=record.length_unit,
        up_axis=record.up_axis,
        receipt_ref=record.receipt_ref,
        format=record.format,
        representation=record.representation,
    )


def to_dto(listing: ArtifactListing) -> ArtifactListDto:
    """Shape the whole listing, including the runs it could not read."""

    return ArtifactListDto(
        project_id=listing.project_id,
        artifacts=[artifact_dto(record) for record in listing.artifacts],
        skipped_runs=list(listing.skipped_runs),
    )


def capture_dto(capture: ViewportCapture) -> ViewportCaptureDto:
    return ViewportCaptureDto(
        project_id=capture.project_id,
        run_id=capture.run_id,
        relative_path=capture.relative_path,
        sha256=capture.sha256,
        media_type=capture.media_type,
        size_bytes=capture.size_bytes,
    )
