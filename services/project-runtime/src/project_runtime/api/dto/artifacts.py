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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application.artifacts import (
    artifact_model_source,
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
    run_id: str | None = Field(alias="runId", default=None, min_length=1)
    state_digest: str | None = Field(alias="stateDigest", default=None, pattern=r"^[0-9a-f]{64}$")
    file_name: str = Field(alias="fileName", min_length=1, max_length=240)
    content_base64: str = Field(alias="contentBase64", min_length=1)


class ModelSourceIndexDto(BaseModel):
    """Native metadata only; no geometry validation or semantic admission."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    model_source: ModelSourceDto = Field(alias="modelSource")
    file_name: str = Field(alias="fileName")
    archive_version: int = Field(alias="archiveVersion")
    units: dict[str, Any]
    object_count: int = Field(alias="objectCount")
    matched_count: int = Field(alias="matchedCount")
    objects: list[dict[str, Any]]
    offset: int
    next_offset: int | None = Field(alias="nextOffset")


class DocumentModelSourceRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1, description="The document's storage run; not its model source.")
    model_source: ModelSourceDto = Field(alias="modelSource", description="Explicitly declared correspondence, not a claim inferred from image pixels.")


class ModelImportConversionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    provider: str
    source_format: str = Field(alias="sourceFormat")
    target_format: str = Field(alias="targetFormat")
    representation: str
    warnings: list[str]


class ModelImportDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    source_artifact: dict[str, str] = Field(alias="sourceArtifact")
    source_file_name: str = Field(alias="sourceFileName")
    conversion: ModelImportConversionDto


class ProjectArtifactDto(BaseModel):
    """One exported model, as its receipt describes it and disk answers for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    artifact_id: str = Field(
        alias="artifactId",
        description="the file's sha256, or receipt:<sha> when it claims none",
    )
    run_id: str = Field(alias="runId")
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    source_import: ModelImportDto | None = Field(alias="sourceImport", default=None)
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
    """Which exact registered document is being made editable.

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
    """One registered document's editable file, and the document it answers for."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    asset_sha256: str = Field(alias="assetSha256")
    revision_ref: str | None = Field(alias="revisionRef", default=None)
    file_name: str = Field(alias="fileName")
    mime_type: Literal["image/png", "image/jpeg", "application/pdf"] = Field(
        alias="mimeType", description="the kind of file the copy itself holds: the origin's own",
    )
    relative_path: str = Field(
        alias="relativePath",
        description="where the editable copy lives, project-relative; display "
        "only — this machine's absolute path never crosses the boundary and no "
        "request may send a path back",
    )
    head_run_id: str = Field(alias="headRunId")
    head_asset_sha256: str = Field(alias="headAssetSha256")
    head_revision_ref: str | None = Field(alias="headRevisionRef", default=None)
    refusal: str | None = Field(
        default=None,
        description="why no one file can stand for this document right now, in "
        "the same words the refused request answers with; null when it is editable",
    )


def work_copy_dto(copy: DocumentWorkCopy) -> DocumentWorkCopyDto:
    return DocumentWorkCopyDto(
        project_id=copy.project_id, run_id=copy.run_id, asset_sha256=copy.asset_sha256,
        revision_ref=copy.revision_ref, file_name=copy.file_name,
        mime_type=copy.mime_type, relative_path=copy.relative_path, head_run_id=copy.head_run_id,
        head_asset_sha256=copy.head_asset_sha256, head_revision_ref=copy.head_revision_ref,
        refusal=copy.refusal,
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
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None,
        description="The exact retained model shown by the browser; validates identity, not correspondence inferred from pixels.")


class ViewportCaptureDto(BaseModel):
    """A non-canonical PNG retained below the named run workspace."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    relative_path: str = Field(alias="relativePath")
    sha256: str
    media_type: str = Field(alias="mediaType")
    size_bytes: int = Field(alias="sizeBytes")
    document: SourceDocumentDto | None = None


class TracingPaperCameraDto(BaseModel):
    """The exact camera the frozen review image was rendered from."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov: float = Field(gt=0, lt=180, allow_inf_nan=False)
    projection: Literal["perspective", "orthographic"]
    zoom: float = Field(gt=0, allow_inf_nan=False)


class TracingPaperReviewRequestDto(BaseModel):
    """One explicit promotion of a saved model-annotation revision to Board."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    model_source: ModelSourceDto = Field(alias="modelSource")
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    annotation_revision_sha256: str = Field(alias="annotationRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    camera: TracingPaperCameraDto
    screen_size: tuple[int, int] = Field(alias="screenSize")
    png_base64: str = Field(alias="pngBase64", min_length=1)

    @model_validator(mode="after")
    def _valid_screen_size(self) -> "TracingPaperReviewRequestDto":
        if any(value < 1 or value > 32768 for value in self.screen_size):
            raise ValueError("screenSize must contain two positive viewport dimensions")
        return self


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


class DocumentReplacementTargetDto(BaseModel):
    """The one registered document an upload replaces, whole.

    Naming the document instead of listing its pages is what makes a
    whole-file edit sayable: the server then knows a file that has gained or
    lost a page is wrong, rather than guessing from how many pages happened to
    be listed.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)


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
    replaces_document: DocumentReplacementTargetDto | None = Field(
        alias="replacesDocument", default=None,
        description="Replace one registered document whole, page for page. The "
        "upload must have the same media type and page count as the document it "
        "replaces. Mutually exclusive with replacesPages.",
    )

    @model_validator(mode="after")
    def _one_way_of_saying_what_is_replaced(self) -> "SourceDocumentRequestDto":
        if self.replaces_document is not None and self.replaces_pages:
            raise ValueError(
                "Name either the whole document this replaces or the individual pages, not both."
            )
        return self


class RevisionAttributionDto(BaseModel):
    """Who asked for a drawing revision, as the request boundary knew it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    actor_id: str = Field(alias="actorId")
    authenticated: bool
    origin: str = Field(description=(
        "hub: through a runtime the Hub manages (its Agent or its window); studio: a runtime no Hub manages."))


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
    previous_revision_ref: str | None = Field(alias="previousRevisionRef", default=None, description=(
        "Read only: the drawing revision this revision continued, from its receipt."))
    attribution: RevisionAttributionDto | None = Field(default=None, description=(
        "Read only: who asked for this drawing revision, from its receipt; null when it was not recorded."))
    reason: str | None = Field(default=None, description=(
        "Read only: why this drawing revision was asked for, from its receipt; null when none was given or recorded."))
    source_kind: Literal["human", "agent"] | None = Field(alias="sourceKind", default=None, description=(
        "Read only: whether a person's own edit (human) or an agent (agent) asked for this drawing revision, as its "
        "request said, from its receipt; null when the request did not say or the revision was retained before it could."))


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
        previous_revision_ref=document.previous_revision_ref,
        attribution=None if document.attribution is None else RevisionAttributionDto(
            actor_id=document.attribution.actor_id, authenticated=document.attribution.authenticated,
            origin=document.attribution.origin),
        reason=document.reason,
        source_kind=document.source_kind,
    )


def artifact_dto(record: ArtifactRecord) -> ProjectArtifactDto:
    """Shape one artifact for the wire; every value came off its receipt."""

    return ProjectArtifactDto(
        artifact_id=record.artifact_id,
        run_id=record.run_id,
        model_source=model_source_dto(artifact_model_source(record)),
        source_import=record.source_import,
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
        document=document_dto(capture.document) if capture.document is not None else None,
    )
