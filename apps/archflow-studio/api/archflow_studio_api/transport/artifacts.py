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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.artifacts import (
    ArtifactListing,
    ArtifactRecord,
    DocumentWorkCopy,
    ModelSource,
    SourceDocument,
    ViewportCapture,
)
from .project import ProjectVersionDto


class ModelSourceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    state_digest: str = Field(alias="stateDigest", pattern=r"^[0-9a-f]{64}$")
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")


def model_source_from(dto: ModelSourceDto) -> ModelSource:
    return ModelSource(dto.run_id, dto.state_digest, dto.asset_sha256)


def model_source_dto(source: ModelSource | None) -> ModelSourceDto | None:
    return None if source is None else ModelSourceDto(**source.to_dict())


class ModelAssetRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    state_digest: str = Field(alias="stateDigest", pattern=r"^[0-9a-f]{64}$")
    file_name: str = Field(alias="fileName", min_length=1, max_length=240)
    content_base64: str = Field(alias="contentBase64", min_length=1)


class DocumentModelSourceRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1, description="The document's storage run; not its model source.")
    model_source: ModelSourceDto = Field(alias="modelSource", description="Explicitly declared correspondence, not a claim inferred from image pixels.")


class ProjectArtifactDto(BaseModel):
    """One exported model, as its receipt describes it and disk answers for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    artifact_id: str = Field(
        alias="artifactId",
        description="the file's sha256, or receipt:<sha> when it claims none",
    )
    run_id: str = Field(alias="runId")
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    source_stage_ref: str | None = Field(
        alias="sourceStageRef", default=None,
        description="The committed source Stage declared by this run's retained candidate delta, "
        "matched to the artifact's run state. Listing metadata; editing and acceptance verify the full state.",
    )
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
    source_step_sha256: str | None = Field(
        alias="sourceStepSha256", default=None,
        description="for an editable work model: the sha256 of the exact STEP its "
        "geometry was imported from, so a client can show the pair as one delivery "
        "and never load the same geometry twice",
    )


class RhinoWorkExportRequestDto(BaseModel):
    """Which run's export is being made editable.

    The digest in the path names the bytes; this names the run they belong to.
    The same STEP can be exported by more than one run, and a work model is
    bound to the run, program and base it was asked for.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId")


class DocumentWorkCopyRequestDto(BaseModel):
    """Which exact registered page is being made editable.

    The digest in the path names the bytes; these name the one registration
    they belong to. ``revisionRef`` is part of that identity, not a filter: a
    null selects the registration that carries none, so a plain upload and a
    retained drawing revision sharing a run and digest are never confused. No
    server path is accepted here or anywhere.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)


class DocumentWorkCopyDto(BaseModel):
    """One registered document's editable file, and the pages it answers for."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    asset_sha256: str = Field(alias="assetSha256")
    revision_ref: str | None = Field(alias="revisionRef", default=None)
    page_index: int = Field(alias="pageIndex", ge=0)
    page_count: int = Field(alias="pageCount", ge=1)
    file_name: str = Field(alias="fileName")
    mime_type: Literal["image/png", "image/jpeg", "application/pdf"] = Field(alias="mimeType")
    relative_path: str = Field(
        alias="relativePath",
        description="where the editable copy lives, project-relative; display "
        "only — this machine's absolute path never crosses the boundary and no "
        "request may send a path back",
    )
    head_run_id: str = Field(alias="headRunId")
    head_asset_sha256: str = Field(alias="headAssetSha256")
    head_revision_ref: str | None = Field(alias="headRevisionRef", default=None)
    head_page_index: int = Field(alias="headPageIndex", ge=0)


def work_copy_dto(copy: DocumentWorkCopy) -> DocumentWorkCopyDto:
    return DocumentWorkCopyDto(
        project_id=copy.project_id, run_id=copy.run_id, asset_sha256=copy.asset_sha256,
        revision_ref=copy.revision_ref, page_index=copy.page_index, page_count=copy.page_count,
        file_name=copy.file_name,
        mime_type=copy.mime_type, relative_path=copy.relative_path, head_run_id=copy.head_run_id,
        head_asset_sha256=copy.head_asset_sha256, head_revision_ref=copy.head_revision_ref,
        head_page_index=copy.head_page_index,
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


class DocumentPageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    page_index: int = Field(alias="pageIndex", ge=0, description="Zero-based page index; images have page 0 only.")
    width: float = Field(gt=0, description="Visible width after PDF CropBox/rotation in points, or EXIF-oriented image pixels.")
    height: float = Field(gt=0, description="Visible height after PDF CropBox/rotation in points, or EXIF-oriented image pixels.")
    rotation: int = Field(description="PDF page rotation applied to these dimensions; 0 for oriented images.")


class DocumentPageReplacementDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)
    page_index: int = Field(alias="pageIndex", ge=0, strict=True)
    new_page_index: int = Field(alias="newPageIndex", ge=0, strict=True)


class SourceDocumentRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str | None = Field(alias="runId", default=None, min_length=1,
                              description="Existing storage run, or omit to use the project's source-document run without a model or Stage association.")
    file_name: str = Field(alias="fileName", min_length=1, max_length=240)
    mime_type: Literal["application/pdf", "image/png", "image/jpeg"] = Field(alias="mimeType")
    content_base64: str = Field(alias="contentBase64", min_length=1, description="Original file bytes; maximum decoded size 32 MiB. No server path is accepted.")
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    replaces_pages: list[DocumentPageReplacementDto] = Field(alias="replacesPages", default_factory=list)


class SourceDocumentDto(BaseModel):
    """An imported reference document, separate from certified model artifacts."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    asset_sha256: str = Field(alias="assetSha256")
    file_name: str = Field(alias="fileName")
    mime_type: Literal["application/pdf", "image/png", "image/jpeg"] = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    page_count: int = Field(alias="pageCount")
    pages: list[DocumentPageDto]
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    model_source_binding_ref: str | None = Field(alias="modelSourceBindingRef", default=None)
    drawing_id: str | None = Field(alias="drawingId", default=None)
    revision_ref: str | None = Field(alias="revisionRef", default=None)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    view_recipe: dict[str, Any] | None = Field(alias="viewRecipe", default=None)
    generated_at: str | None = Field(alias="generatedAt", default=None)
    replaces_pages: list[DocumentPageReplacementDto] = Field(alias="replacesPages", default_factory=list)


class SourceDocumentListDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str | None = Field(alias="runId", description="Requested storage run; null when listing every registered project document.")
    documents: list[SourceDocumentDto]


def document_dto(document: SourceDocument) -> SourceDocumentDto:
    return SourceDocumentDto(
        project_id=document.project_id, run_id=document.run_id,
        asset_sha256=document.asset_sha256, file_name=document.file_name,
        mime_type=document.mime_type, size_bytes=document.size_bytes,
        page_count=len(document.pages),
        model_source=model_source_dto(document.model_source),
        model_source_binding_ref=document.model_source_binding_ref,
        drawing_id=document.drawing_id, revision_ref=document.revision_ref,
        source_stage_ref=document.source_stage_ref, view_recipe=document.view_recipe,
        generated_at=document.generated_at,
        replaces_pages=[DocumentPageReplacementDto(
            run_id=page.run_id, asset_sha256=page.asset_sha256, revision_ref=page.revision_ref,
            page_index=page.page_index, new_page_index=page.new_page_index,
        ) for page in document.replaces_pages],
        pages=[DocumentPageDto(page_index=page.page_index, width=page.width, height=page.height, rotation=page.rotation) for page in document.pages],
    )


def artifact_dto(record: ArtifactRecord) -> ProjectArtifactDto:
    """Shape one artifact for the wire; every value came off its receipt."""

    return ProjectArtifactDto(
        artifact_id=record.artifact_id,
        run_id=record.run_id,
        model_source=model_source_dto(record.model_source or (
            ModelSource(record.run_id, record.design_state_digest, record.sha256)
            if record.design_state_digest and record.sha256 and record.format == "3dm" and record.available else None
        )),
        source_stage_ref=record.source_stage_ref,
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
        source_step_sha256=record.source_step_sha256,
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
