"""Receipt-certified model artifacts and non-canonical viewport captures.

A ``.3dm`` or ``.step`` file on disk is not an artifact: it is a file. What
makes it an artifact is a retained export receipt — a ``seat-occt-execution``
(the ordinary in-process export: one exact STEP file and one mesh ``.3dm``
preview of the same model) or a ``seat-rhino-execution`` (the Rhino host
export) — that says which run and which program produced it, against which
base, and what its bytes hash to. So this module never looks for files and then
asks what they are — it reads the receipts and then asks whether the file they
certify is still there. One OCCT receipt is two rows, because it certifies two
files; each row says which it is (``representation``: exact or preview) and
what it is (``format``: step or 3dm), and a preview is never labelled as a
B-rep.

That order is what lets the listing be honest about the four ways an artifact
can be absent: the receipt claimed no digest (a failed export), no file of that
name is in the run's workspaces, the copies that are there could not be read at
all, or they were read and none of them hash to the digest the receipt claims.
The last is the interesting one: it is what an edited-in-Rhino model looks like
from here, and it is never reported as success. The one before it is kept apart
from it on purpose — a file we could not open says nothing about its contents,
and calling that corruption would put a claim on the wire that nobody checked.

Resolution is content-addressed on purpose. Older runs did not put exports under
``cad-<stage_id>``, and two receipts in one run can name the same file. Matching
by digest inside that run's workspaces answers both without a convention.

A viewport capture is deliberately different: it is an inspection image, not
an exported model. The Studio validates its PNG bytes and asks P036 to retain
them below the existing run named by the request. No receipt is minted, the
capture is not returned by ``list_artifacts``, and canonical HEAD is untouched.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass, replace
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path, PurePosixPath
import re
import struct
import threading
import zlib
from typing import Any, Iterable, Mapping, NamedTuple

from PIL import Image, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from archflow.project.record_kinds import (
    SEAT_OCCT_EXECUTION, SEAT_RHINO_EXECUTION, STUDIO_SOURCE_DOCUMENT, STUDIO_MODEL_ASSET,
    STUDIO_DOCUMENT_MODEL_SOURCE,
)
from archflow.adapters.three_dm_inspector import inspect_three_dm_contents, ThreeDmInspectionError
from archflow.adapters.cad_program import ROOT_LAYER
from archflow.project.layout import cad_workspace_path
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError

from ..ports import StudioEventSink
from ..transport.errors import StudioError, error_sentence
from .binding import ProjectBinding, ReferenceRun, record_kind
from .projection import StateProjection, project_state, require_actionable

# A file digest, as it travels in a path parameter. Lowercase because that is
# what the kernel writes; anything else names no artifact here.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Why an artifact cannot be served. Four distinct facts, never collapsed —
# ``FILE_UNREADABLE`` especially: a copy that would not open has not been shown
# to be wrong, only to be unavailable, and saying otherwise would be a claim
# about contents nobody read.
NO_DIGEST = "no inspection digest"
FILE_MISSING = "file missing"
FILE_UNREADABLE = "file unreadable"
DIGEST_MISMATCH = "digest mismatch"
PNG_MEDIA_TYPE = "image/png"
JPEG_MEDIA_TYPE = "image/jpeg"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
DOCUMENT_UPLOAD_RUN_ID = "studio-documents"

# Where one registered document's editable copy lives, below the run that
# holds the registration it was made from. A speculative workspace file,
# never truth.
WORK_COPY_WORKSPACE = "studio-documents/work"

# The receipts that certify an exported file, and the schema that tells the
# two apart. Both are read; nothing is ever regenerated or run by reading them.
EXPORT_RECEIPT_KINDS = (SEAT_OCCT_EXECUTION, SEAT_RHINO_EXECUTION)
OCCT_RECEIPT_SCHEMA = "OcctExecutionReceipt@1"

# What a listed file is. ``format`` is the file format a reader has to know
# to open it: ``step`` (ISO 10303-21) or ``3dm`` (what the viewer loads).
# ``representation`` is the claim the receipt makes about its geometry:
# ``exact`` for the delivered model (a STEP B-rep, or a Rhino export that was
# read back), ``preview`` for a render mesh tessellated from the exact model
# so a viewer can show it — never a NURBS or B-rep delivery.
FORMAT_STEP = "step"
FORMAT_3DM = "3dm"
EXACT = "exact"
PREVIEW = "preview"


class _Resolution(NamedTuple):
    """Where the certified bytes are, or why they are not to be had."""

    path: Path | None
    reason: str | None
    # The OSError class that stopped the read, when that is the reason.
    error: str | None


@dataclass(frozen=True, slots=True)
class ModelSource:
    """The exact retained state and the complete model bytes being viewed."""

    run_id: str
    state_digest: str
    asset_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"runId": self.run_id, "stateDigest": self.state_digest, "assetSha256": self.asset_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelSource:
        return cls(value["runId"], value["stateDigest"], value["assetSha256"])


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One receipt, and what became of the file it certified."""

    artifact_id: str
    run_id: str
    stage_id: str | None
    file_name: str
    relative_path: str | None
    # The located file itself. Kept beside the rendered path because the
    # rendering is one-way: a run's workspace may hold an export whose name is
    # not a portable P036 path segment (an accent, a Chinese character), and
    # asking the kernel to parse our own display string back would refuse it.
    path: Path | None
    sha256: str | None
    size_bytes: int | None
    object_count: int | None
    status: str | None
    readback_verified: bool | None
    available: bool
    unavailable_reason: str | None
    # Which OSError stopped the read, when the reason is ``FILE_UNREADABLE``.
    # It belongs in the refusal's detail, not on the listing's wire form.
    unavailable_error: str | None
    base_version: int | None
    base_state_sha256: str | None
    branch_id: str | None
    branch_epoch: int | None
    program_ref: str | None
    program_digest: str | None
    design_state_digest: str | None
    length_unit: str | None
    up_axis: str | None
    receipt_ref: str
    # ``step`` or ``3dm``: what a reader must know to open the file.
    format: str
    # ``exact`` or ``preview``: what the receipt claims the geometry is. A
    # preview is a mesh for looking at; the exact file is the delivery.
    representation: str
    model_source: ModelSource | None = None
    # The committed source declared by this run's retained candidate delta.
    # Listing metadata only; continuing or accepting still verifies the state.
    source_stage_ref: str | None = None
    # For an editable work model: the digest of the exact STEP it was imported
    # from. It is what makes the pair readable - this file is that file, made
    # editable - and what stops another run's identical bytes from answering
    # for this one.
    source_step_sha256: str | None = None
    # And the export receipt it was made from. Not on the wire: it is how this
    # module recognizes a work model as this delivery's own, materials and all,
    # rather than one made from a different receipt of the same bytes.
    source_receipt_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactListing:
    """Every artifact this project can account for, and what it could not read."""

    project_id: str
    artifacts: tuple[ArtifactRecord, ...]
    # Run directories whose records could not be listed. One corrupt run must
    # not cost the client every other artifact, and the skip is never silent.
    skipped_runs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ViewportCapture:
    """One non-canonical inspection image retained in its named run."""

    project_id: str
    run_id: str
    relative_path: str
    sha256: str
    media_type: str
    size_bytes: int


MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
DOCUMENT_MEDIA_TYPES = ("application/pdf", PNG_MEDIA_TYPE, JPEG_MEDIA_TYPE)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Visible page size after crop/rotation; PDF points or oriented image pixels."""

    page_index: int
    width: float
    height: float
    rotation: int = 0


@dataclass(frozen=True, slots=True)
class DocumentReplacementTarget:
    """The one registered document an upload replaces, whole."""

    run_id: str
    asset_sha256: str
    revision_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentPageReplacement:
    """An exact registered old page and its corresponding uploaded page."""

    run_id: str
    asset_sha256: str
    revision_ref: str | None
    page_index: int
    new_page_index: int


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """An imported reference or a retained drawing, never a design state."""

    project_id: str
    run_id: str
    asset_sha256: str
    file_name: str
    mime_type: str
    size_bytes: int
    pages: tuple[DocumentPage, ...]
    model_source: ModelSource | None = None
    model_source_binding_ref: str | None = None
    drawing_id: str | None = None
    revision_ref: str | None = None
    source_stage_ref: str | None = None
    view_recipe: dict[str, Any] | None = None
    generated_at: str | None = None
    replaces_pages: tuple[DocumentPageReplacement, ...] = ()


def _document_pages(data: bytes, mime_type: str) -> tuple[DocumentPage, ...]:
    try:
        if mime_type == "application/pdf":
            if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
                raise ValueError("incomplete PDF")
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise StudioError(422, "DOCUMENT_ENCRYPTED", "Open an unencrypted PDF copy to annotate it.")
            if not 1 <= len(reader.pages) <= 1000:
                raise ValueError("PDF must contain between 1 and 1000 pages")
            pages = []
            for index, page in enumerate(reader.pages):
                crop, media = page.cropbox, page.mediabox
                left, bottom = max(crop.left, media.left), max(crop.bottom, media.bottom)
                right, top = min(crop.right, media.right), min(crop.top, media.top)
                unit = float(page.user_unit)
                width, height = float(right - left) * unit, float(top - bottom) * unit
                rotation = page.rotation % 360
                if rotation not in (0, 90, 180, 270) or not all(
                    math.isfinite(value) and value > 0 for value in (unit, width, height)
                ):
                    raise ValueError("invalid PDF page dimensions or rotation")
                if rotation in (90, 270):
                    width, height = height, width
                page.get_contents()
                pages.append(DocumentPage(index, width, height, rotation))
            return tuple(pages)
        image_format = {"image/png": "PNG", "image/jpeg": "JPEG"}[mime_type]
        if image_format == "PNG" and not data.endswith(PNG_END):
            raise ValueError("incomplete PNG")
        with Image.open(BytesIO(data), formats=[image_format]) as picture:
            picture.verify()
        with Image.open(BytesIO(data), formats=[image_format]) as picture:
            picture.load()
            oriented = ImageOps.exif_transpose(picture)
            return (DocumentPage(0, *oriented.size),)
    except (PdfReadError, OSError, SyntaxError, ValueError, TypeError, KeyError, Image.DecompressionBombError) as exc:
        raise StudioError(422, "DOCUMENT_INVALID", "The source is not a complete, readable PDF, PNG or JPEG.") from exc


def list_documents(binding: ProjectBinding, run_id: str | None = None) -> tuple[SourceDocument, ...]:
    """Registered documents in one run, or across the bound project."""

    if run_id is None:
        return _ordered_documents(
            document for source_run in binding.run_ids() for document in list_documents(binding, source_run)
        )

    documents: dict[str, SourceDocument] = {}
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != STUDIO_SOURCE_DOCUMENT:
            continue
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != "StudioSourceDocument@1" or (
            payload.get("project_id"), payload.get("run_id")
        ) != (binding.project_id, run_id):
            raise StudioError(409, "DOCUMENT_INVALID", "The retained source document has a different project or run binding.")
        document = SourceDocument(
            project_id=payload["project_id"], run_id=payload["run_id"],
            asset_sha256=payload["asset_sha256"], file_name=payload["file_name"],
            mime_type=payload["mime_type"], size_bytes=payload["size_bytes"],
            pages=tuple(DocumentPage(**page) for page in payload["pages"]),
            model_source=ModelSource.from_dict(payload["modelSource"]) if payload.get("modelSource") else None,
            model_source_binding_ref=ref.uri if payload.get("modelSource") else None,
            drawing_id=payload.get("drawingId"), revision_ref=payload.get("revisionRef"),
            source_stage_ref=payload.get("sourceStageRef"), view_recipe=payload.get("viewRecipe"),
            generated_at=payload.get("generatedAt"),
            replaces_pages=tuple(DocumentPageReplacement(**page) for page in payload.get("replaces_pages", ())),
        )
        key = document.revision_ref or document.asset_sha256
        previous = documents.get(key)
        if previous is None:
            documents[key] = document
        elif document.model_source is not None:
            if previous.model_source is not None and previous.model_source != document.model_source:
                raise StudioError(409, "DOCUMENT_SOURCE_CONFLICT", "This document has competing model associations; its pages remain retained.")
            if previous.model_source is None:
                documents[key] = replace(previous, model_source=document.model_source,
                                                           model_source_binding_ref=document.model_source_binding_ref)
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != STUDIO_DOCUMENT_MODEL_SOURCE:
            continue
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != "StudioDocumentModelSource@1" or (payload.get("projectId"), payload.get("runId")) != (binding.project_id, run_id):
            raise StudioError(409, "DOCUMENT_SOURCE_CONFLICT", "The saved model association belongs to another document run.")
        document = documents.get(payload["assetSha256"])
        source = ModelSource.from_dict(payload["modelSource"])
        if document is None or (document.model_source is not None and document.model_source != source):
            raise StudioError(409, "DOCUMENT_SOURCE_CONFLICT", "This document has competing model associations; its pages remain retained.")
        documents[document.asset_sha256] = replace(document, model_source=source, model_source_binding_ref=ref.uri)
    return _ordered_documents(documents.values())


def _ordered_documents(documents: Iterable[SourceDocument]) -> tuple[SourceDocument, ...]:
    documents = tuple(documents)
    generated = sorted((doc for doc in documents if doc.generated_at is not None),
                       key=lambda doc: (doc.generated_at, doc.revision_ref or doc.asset_sha256), reverse=True)
    undated = sorted((doc for doc in documents if doc.generated_at is None),
                     key=lambda doc: (doc.file_name, doc.run_id, doc.asset_sha256))
    return tuple(generated + undated)


def document_bytes(
    binding: ProjectBinding, run_id: str, asset_sha256: str,
    revision_ref: str | None = None,
    *, binding_ref: str | None = None,
) -> tuple[SourceDocument, bytes]:
    """Serve the registered original only; the caller cannot supply a disk path."""

    if not SHA256_HEX.fullmatch(asset_sha256):
        raise StudioError(422, "DOCUMENT_INVALID", "A source document is addressed by its SHA-256.")
    document = next((row for row in list_documents(binding, run_id) if row.asset_sha256 == asset_sha256
                     and (revision_ref is None or row.revision_ref == revision_ref)
                     and (binding_ref is None or row.model_source_binding_ref == binding_ref)), None)
    if document is None:
        raise StudioError(404, "DOCUMENT_NOT_FOUND", f"Run {run_id} has no source document {asset_sha256}.")
    return document, _registered_document_bytes(binding, document)


def _registered_document_bytes(binding: ProjectBinding, document: SourceDocument) -> bytes:
    run_id, asset_sha256 = document.run_id, document.asset_sha256
    if document.revision_ref is not None:
        from monkeydiagram.drawing_elevation import DrawingElevationError, read_model_axis_elevation

        try:
            drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(document.revision_ref, binding.project_id))
        except (DrawingElevationError, TypeError, ValueError) as exc:
            raise StudioError(409, "DOCUMENT_UNAVAILABLE", "The retained drawing revision cannot be read.") from exc
        if drawing.png_ref.sha256 != asset_sha256 or drawing.receipt["source"]["run_id"] != run_id or drawing.receipt["view"] != document.view_recipe:
            raise StudioError(409, "DOCUMENT_SOURCE_CONFLICT", "The drawing revision does not match its registered source and view.")
        return drawing.png
    try:
        path = binding.repository.layout.resolve_relative(f"objects/sha256/{asset_sha256[:2]}/{asset_sha256}")
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise StudioError(409, "DOCUMENT_UNAVAILABLE", "The registered source document cannot be read.") from exc
    if hashlib.sha256(data).hexdigest() != asset_sha256:
        raise StudioError(409, "DOCUMENT_DIGEST_MISMATCH", "The source file no longer matches the version this document names.")
    return data


def _validate_page_replacements(
    binding: ProjectBinding, run_id: str, digest: str, pages: tuple[DocumentPage, ...],
    replacements: tuple[DocumentPageReplacement, ...], documents: tuple[SourceDocument, ...],
) -> None:
    old_pages: set[tuple[str, str, str | None, int]] = set()
    new_pages: set[int] = set()
    # Reading a registered source verifies its bytes against its digest, which
    # for a 32 MiB original is not free. Every page of one document shares one
    # source, so verify each distinct registration once, not once per page.
    verified: set[tuple[str, str, str | None]] = set()
    for replacement in replacements:
        identity = (replacement.run_id, replacement.asset_sha256, replacement.revision_ref)
        old_page = (*identity, replacement.page_index)
        if old_page in old_pages:
            raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID", "Each old page may appear only once in a replacement upload.")
        old_pages.add(old_page)
        if identity == (run_id, digest, None):
            raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID", "A source document cannot replace itself.")
        previous = next((document for document in documents if (
            document.run_id, document.asset_sha256, document.revision_ref
        ) == identity), None)
        if previous is None:
            raise StudioError(404, "DOCUMENT_NOT_FOUND", "The page to replace does not name an exact registered source document.")
        old = next((page for page in previous.pages if page.page_index == replacement.page_index), None)
        new = next((page for page in pages if page.page_index == replacement.new_page_index), None)
        if old is None or new is None:
            raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID", "Both replacement page indices must exist in their documents.")
        # A page of this upload can answer for one old page only; otherwise two
        # different pages would resolve to the same image and one of them would
        # be lost inside an immutable record.
        if replacement.new_page_index in new_pages:
            raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID", "Each page of the upload may replace only one old page.")
        new_pages.add(replacement.new_page_index)
        if identity not in verified:
            _registered_document_bytes(binding, previous)
            verified.add(identity)
        if not math.isclose(old.width / old.height, new.width / new.height, rel_tol=0, abs_tol=0.001):
            raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID", "Replacement pages must have the same visible aspect ratio to preserve board marks.")
    for document in documents:
        if (document.run_id, document.asset_sha256, document.revision_ref) == (run_id, digest, None):
            continue
        if any((page.run_id, page.asset_sha256, page.revision_ref, page.page_index) in old_pages
               for page in document.replaces_pages):
            raise StudioError(409, "DOCUMENT_REPLACEMENT_CONFLICT", "An old page already has a registered replacement; replace that newer page instead.")


def _whole_document_replacement(
    target: DocumentReplacementTarget, mime_type: str, pages: tuple[DocumentPage, ...],
    documents: tuple[SourceDocument, ...],
) -> tuple[DocumentPageReplacement, ...]:
    """Page i of the named document, replaced by page i of this upload.

    The declaration is what makes the refusals possible: a file standing for a
    whole document has to be the same kind of file and have the same pages, so
    a page added in some editor is named here rather than registered into a
    record where nothing would ever show it.
    """

    previous = next((row for row in documents if (
        row.run_id, row.asset_sha256, row.revision_ref,
    ) == (target.run_id, target.asset_sha256, target.revision_ref)), None)
    if previous is None:
        raise StudioError(404, "DOCUMENT_NOT_FOUND",
                          f"Run {target.run_id} has no source document {target.asset_sha256}.")
    if mime_type != previous.mime_type:
        raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID",
                          "This is a different kind of file from the document it replaces.")
    if len(pages) != len(previous.pages):
        raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID",
                          f"This file has {_pages_phrase(len(pages))}; "
                          f"the document it replaces has {_pages_phrase(len(previous.pages))}.")
    return tuple(DocumentPageReplacement(
        run_id=previous.run_id, asset_sha256=previous.asset_sha256, revision_ref=previous.revision_ref,
        page_index=page.page_index, new_page_index=new.page_index,
    ) for page, new in zip(previous.pages, pages))



def _png_with_content_identity(data: bytes, identity: str) -> bytes:
    """Add a deterministic ancillary chunk without changing rendered pixels."""

    if not SHA256_HEX.fullmatch(identity) or not data.endswith(PNG_END):
        raise StudioError(422, "DOCUMENT_INVALID", "The review image identity is invalid.")
    chunk_type = b"tEXt"
    payload = b"MonkeyHub-Review\x00" + identity.encode("ascii")
    chunk = struct.pack(">I", len(payload)) + chunk_type + payload
    chunk += struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    return data[:-len(PNG_END)] + chunk + PNG_END


def save_document(
    binding: ProjectBinding, run_id: str | None, file_name: str, mime_type: str, content_base64: str,
    model_source: ModelSource | None = None,
    replaces_pages: tuple[DocumentPageReplacement, ...] = (),
    *, drawing_id: str | None = None, source_stage_ref: str | None = None,
    view_recipe: dict[str, Any] | None = None, generated_at: str | None = None,
    replaces_document: DocumentReplacementTarget | None = None, content_identity: str | None = None,
) -> SourceDocument:
    """Retain original bytes in a named run or the project's source-document run.

    ``replaces_document`` says the upload takes the place of one registered
    document whole — the shape a work copy has, one file for one document. It
    is not a shorthand for a page list: because the caller has declared the
    whole file, a file that has gained or lost a page, or changed kind, is
    wrong and is refused here, where the architect can see it, rather than
    registering with pages that answer for nothing and are placed on no board.
    """

    run = binding.load_run(run_id) if run_id is not None else None
    if model_source is not None:
        require_model_source(binding, model_source)
    if not file_name.strip() or len(file_name) > 240 or any(char in file_name for char in "/\\\r\n\x00"):
        raise StudioError(422, "DOCUMENT_INVALID", "Provide a file name, not a server path.")
    if mime_type not in DOCUMENT_MEDIA_TYPES:
        raise StudioError(422, "DOCUMENT_INVALID", "Only PDF, PNG and JPEG source documents are supported.")
    if replaces_document is not None and replaces_pages:
        raise StudioError(422, "DOCUMENT_REPLACEMENT_INVALID",
                          "Name either the whole document this replaces or the individual pages, not both.")
    if len(content_base64) > 4 * ((MAX_DOCUMENT_BYTES + 2) // 3):
        raise StudioError(413, "DOCUMENT_TOO_LARGE", "Source documents may contain at most 32 MiB.")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StudioError(422, "DOCUMENT_INVALID", "The source document is not valid base64 data.") from exc
    if len(data) > MAX_DOCUMENT_BYTES:
        raise StudioError(413, "DOCUMENT_TOO_LARGE", "Source documents may contain at most 32 MiB.")
    if content_identity is not None:
        if mime_type != PNG_MEDIA_TYPE:
            raise StudioError(422, "DOCUMENT_INVALID", "A review snapshot must be a PNG image.")
        data = _png_with_content_identity(data, content_identity)
    pages = _document_pages(data, mime_type)
    digest = hashlib.sha256(data).hexdigest()
    with _document_source_lock:
        target_run_id = run_id if run_id is not None else DOCUMENT_UPLOAD_RUN_ID
        documents = list_documents(binding) if (replaces_pages or replaces_document) else ()
        if replaces_document is not None:
            replaces_pages = _whole_document_replacement(replaces_document, mime_type, pages, documents)
        _validate_page_replacements(binding, target_run_id, digest, pages, replaces_pages, documents)
        existing_documents = documents if (replaces_pages or replaces_document) else (
            list_documents(binding, target_run_id) if target_run_id in binding.run_ids() else ()
        )
        existing = next((row for row in existing_documents
                         if row.run_id == target_run_id and row.asset_sha256 == digest), None)
        if existing is not None:
            if existing.model_source != model_source:
                raise StudioError(409, "DOCUMENT_SOURCE_IMMUTABLE", "This document's model source is already retained. Its saved pages cannot be rebound to another model.")
            if replaces_pages and set(existing.replaces_pages) != set(replaces_pages):
                raise StudioError(409, "DOCUMENT_SOURCE_IMMUTABLE", "This document's replacement pages are already retained and cannot be rebound.")
            _registered_document_bytes(binding, existing)
            return existing
        if run is None:
            # A general P036 run stores references without inventing a model
            # or a design Stage. Invalid uploads never create this envelope.
            try:
                run = (binding.load_run(DOCUMENT_UPLOAD_RUN_ID) if DOCUMENT_UPLOAD_RUN_ID in binding.run_ids()
                       else binding.repository.create_run(DOCUMENT_UPLOAD_RUN_ID))
            except (ProjectRepositoryError, OSError) as exc:
                raise StudioError(409, "DOCUMENT_WRITE_FAILED", "The source document run could not be retained in its project.") from exc
            run_id = run.run_id
        document = SourceDocument(binding.project_id, run_id, digest, file_name, mime_type, len(data), pages,
                                  model_source, drawing_id=drawing_id, source_stage_ref=source_stage_ref,
                                  view_recipe=view_recipe, generated_at=generated_at, replaces_pages=replaces_pages)
        try:
            binding.repository.ingest(
                run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                artifact_id=f"source-document-{digest}", media_type=mime_type, source=BytesIO(data),
            )
            binding.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
                record_kind=STUDIO_SOURCE_DOCUMENT,
                payload={"schema": "StudioSourceDocument@1", **{
                    key: value for key, value in asdict(document).items() if key not in (
                        "model_source", "model_source_binding_ref", "drawing_id", "revision_ref", "source_stage_ref", "view_recipe", "generated_at",
                    )
                }, **({"modelSource": model_source.to_dict()} if model_source else {}),
                **({"drawingId": drawing_id} if drawing_id is not None else {}),
                **({"sourceStageRef": source_stage_ref} if source_stage_ref is not None else {}),
                **({"viewRecipe": view_recipe} if view_recipe is not None else {}),
                **({"generatedAt": generated_at} if generated_at is not None else {})},
            )
        except (ProjectRepositoryError, OSError) as exc:
            raise StudioError(409, "DOCUMENT_WRITE_FAILED", "The source document could not be retained in its project.") from exc
        return next(row for row in list_documents(binding, run_id) if row.asset_sha256 == digest)


_document_source_lock = threading.RLock()


def bind_document_model_source(
    binding: ProjectBinding, run_id: str, asset_sha256: str, source: ModelSource,
) -> SourceDocument:
    """Record the user's declared association without rewriting old pages or comments."""

    require_model_source(binding, source)
    with _document_source_lock:
        document, _ = document_bytes(binding, run_id, asset_sha256)
        if document.model_source is not None:
            if document.model_source != source:
                raise StudioError(409, "DOCUMENT_SOURCE_IMMUTABLE", "This document already names a model source; it cannot be rebound.")
            return document
        run = binding.load_run(run_id)
        binding.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=STUDIO_DOCUMENT_MODEL_SOURCE,
            payload={"schema": "StudioDocumentModelSource@1", "projectId": binding.project_id,
                     "runId": run_id, "assetSha256": asset_sha256, "modelSource": source.to_dict()},
        )
        return next(row for row in list_documents(binding, run_id) if row.asset_sha256 == asset_sha256)


@dataclass(frozen=True, slots=True)
class DocumentWorkCopy:
    """An editable copy of one registered document, and the document it answers for.

    Two identities, as everywhere else here. The *origin* four fields are the
    copy's own identity: they never move, so the same document always resolves
    to the same file across restarts. The *head* four are the document the copy
    currently answers for — the newest registered replacement of the origin —
    and they move whenever that document is replaced, from this copy or from
    the Board's own upload.

    ``refusal`` is the whole editability answer, decided here where the row is
    derived, so the one caller that opens a copy and the one that watches the
    copies on disk can never disagree about which files are live. A row with a
    refusal is still a row: its file may well exist and hold someone's work.
    """

    project_id: str
    run_id: str
    asset_sha256: str
    revision_ref: str | None
    file_name: str
    mime_type: str              # the kind of file this copy holds: the origin's
    path: Path                  # the editable file itself
    relative_path: str          # project-relative POSIX path, for display
    head_run_id: str
    head_asset_sha256: str
    head_revision_ref: str | None
    # Every digest already registered in this page's replacement chain. Bytes
    # equal to one of them are that history, not evidence of a new revision.
    known_sha256: frozenset[str]
    # Why no one file can stand for this document right now, or None.
    refusal: str | None = None


# One page, as every replacement names it: which run and registered asset it
# belongs to, which retained drawing revision (if any), and which page of it.
_PageId = tuple[str, str, str | None, int]


def _page_replacements(documents: tuple[SourceDocument, ...]) -> dict[_PageId, _PageId]:
    """Old page -> the page that replaced it, from the documents' own records.

    ``_validate_page_replacements`` has already refused a second replacement of
    the same old page, so this mapping is single-valued.
    """

    links: dict[_PageId, _PageId] = {}
    for document in documents:
        for page in document.replaces_pages:
            links[(page.run_id, page.asset_sha256, page.revision_ref, page.page_index)] = (
                document.run_id, document.asset_sha256, document.revision_ref, page.new_page_index,
            )
    return links


def _chain_head(links: dict[_PageId, _PageId], origin: _PageId) -> tuple[_PageId, frozenset[str]]:
    """The newest registered page in this chain, and every digest on the way."""

    head, seen, known = origin, {origin}, {origin[1]}
    while True:
        following = links.get(head)
        # Retained records are immutable, so a cycle can only come from a
        # hand-written or corrupted chain; the newest page reached wins.
        if following is None or following in seen:
            return head, frozenset(known)
        seen.add(following)
        head = following
        known.add(head[1])


def _whole_document_head(
    links: dict[_PageId, _PageId], origin: SourceDocument,
) -> tuple[_PageId, frozenset[str], str | None]:
    """The one document every page of ``origin`` is currently answered for by.

    A work copy is a file, so it can only stand for pages that still travel
    together: page ``i`` of the origin must resolve to page ``i`` of one and the
    same registered document. Two different things break that, and they are
    worth telling apart because they look nothing alike to the person holding
    the file: the pages can end up in several documents, or they can all end up
    in one document at other page numbers — page 1 becoming page 2 of a longer
    PDF, which is what the Board's own replacement dialog does.

    Returns the head of page 0, every digest along the way, and the reason no
    single file can stand for the origin, or ``None`` when one still can.
    """

    heads = [_chain_head(links, (origin.run_id, origin.asset_sha256, origin.revision_ref, page.page_index))
             for page in origin.pages]
    first, known = heads[0]
    for _, digests in heads[1:]:
        known |= digests
    reason = None
    if any(head[:3] != first[:3] for head, _ in heads):
        reason = ("Pages of this document are answered for by different documents now, "
                  "so no single file can stand for it.")
    else:
        moved = next(((page, head) for page, (head, _) in zip(origin.pages, heads)
                      if head[3] != page.page_index), None)
        if moved is not None:
            page, head = moved
            reason = (f"Page {page.page_index + 1} of this document is now page {head[3] + 1} of another "
                      "document, so no single file can stand for it.")
    return first, frozenset(known), reason


# What P036 accepts as one workspace path segment (archflow/project/refs.py).
_WORK_COPY_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


_WORK_COPY_EXTENSIONS = {PNG_MEDIA_TYPE: ".png", JPEG_MEDIA_TYPE: ".jpg", "application/pdf": ".pdf"}


def _work_copy_segment(document: SourceDocument) -> str:
    """The copy's own file name on disk: the registered one when P036 can hold it.

    A registered document may be called ``研究图纸.png`` and a P036 workspace
    path segment must be portable ASCII. The copy is then named after the
    document it belongs to, deterministically, so the same registration always
    resolves to the same file and an accented name costs nobody their edits.
    """

    if _WORK_COPY_SEGMENT.fullmatch(document.file_name):
        return document.file_name
    # A media type this build has no editable copy for is refused on the row,
    # not raised: one hand-written record must not cost a whole project its
    # other work copies. The path stays derivable so nothing else has to care.
    return f"{document.asset_sha256[:32]}{_WORK_COPY_EXTENSIONS.get(document.mime_type, '')}"


def _pages_phrase(count: int) -> str:
    """``1 page``/``3 pages``: the counts appear in sentences people read."""

    return f"{count} page" if count == 1 else f"{count} pages"


def _work_copy_refusal(
    origin: SourceDocument, head_document: SourceDocument, split: str | None,
) -> str | None:
    """Why this document has no single editable file, in one accurate sentence.

    One predicate, one wording. The copy's name and path come from the origin
    and never move, so the file can only honestly hold bytes of the origin's
    own kind and shape; when what it answers for has become something else,
    that is what is said, rather than a file quietly holding the wrong thing
    under the right name.

    A registered document is read back from its own retained payload, so an
    unreadable media type is a refusal like any other. Nothing here may raise:
    one hand-written record must not cost a project every other work copy.
    """

    if not origin.pages:
        return "This registered document has no pages."
    if origin.mime_type not in _WORK_COPY_EXTENSIONS:
        return f"This document is registered as {origin.mime_type}, which has no editable copy."
    if split is not None:
        return split
    if len(head_document.pages) != len(origin.pages):
        return (f"The current replacement has {_pages_phrase(len(head_document.pages))}; "
                f"this document has {_pages_phrase(len(origin.pages))}.")
    if head_document.mime_type != origin.mime_type:
        return "The current replacement is a different kind of file from this document."
    return None


def _work_copy(
    binding: ProjectBinding, origin: SourceDocument, links: dict[_PageId, _PageId],
    documents: tuple[SourceDocument, ...],
) -> DocumentWorkCopy:
    """The deterministic copy row for one registered document, refusal and all.

    No IO: the path is derived from the origin's own identity, so the caller
    only has to ask whether that one file is there. Every reason this document
    cannot have one editable file is decided here, once, and carried on the
    row — a refused row is still returned, because its file may already exist
    and hold work nobody may lose sight of.
    """

    registration_path = origin.asset_sha256
    if origin.revision_ref is not None:
        revision = record_ref_from_uri(origin.revision_ref, binding.project_id)
        # Equal pixels can be registered by different drawing revisions. Keep
        # the existing record's complete path, including its run, so asking for
        # one registration never opts another into watching the same file.
        registration_path += f"/revisions/{revision.relative_path}"
    head, known, split = ((origin.run_id, origin.asset_sha256, origin.revision_ref, 0),
                          frozenset({origin.asset_sha256}), None)
    if origin.pages:
        head, known, split = _whole_document_head(links, origin)
    head_document = next((row for row in documents if (
        row.run_id, row.asset_sha256, row.revision_ref) == head[:3]), origin)
    refusal = _work_copy_refusal(origin, head_document, split)
    if refusal is not None:
        # Its own registration is the only thing a refused row can answer for.
        head, known = (origin.run_id, origin.asset_sha256, origin.revision_ref, 0), frozenset({origin.asset_sha256})
    workspace_relative = f"{WORK_COPY_WORKSPACE}/{registration_path}/{_work_copy_segment(origin)}"
    return DocumentWorkCopy(
        project_id=binding.project_id, run_id=origin.run_id, asset_sha256=origin.asset_sha256,
        revision_ref=origin.revision_ref,
        file_name=origin.file_name, mime_type=origin.mime_type,
        path=binding.repository.layout.run(origin.run_id).workspaces / Path(
            *PurePosixPath(workspace_relative).parts),
        relative_path=f"runs/{origin.run_id}/workspaces/{workspace_relative}",
        head_run_id=head[0], head_asset_sha256=head[1], head_revision_ref=head[2],
        known_sha256=known, refusal=refusal,
    )


def open_document_work_copy(
    binding: ProjectBinding, run_id: str, asset_sha256: str, *, revision_ref: str | None = None,
) -> DocumentWorkCopy:
    """Give this registered document an editable file, once, and say where.

    Explicitly requested: nothing materialises a copy by observing a project or
    a board. The three values name one exact registration — ``revision_ref``
    selects the registration that carries it, and ``None`` selects the one that
    carries none, so a plain upload and a retained drawing revision that happen
    to share a run and digest are never confused for one another.

    The bytes come from the document the copy has to answer for, through the
    registered reader, which already refuses an unreadable or mismatched
    registration. A copy that is already there is returned untouched: a
    returning architect must not lose their edits to a second click.
    """

    if not SHA256_HEX.fullmatch(asset_sha256):
        raise StudioError(422, "DOCUMENT_INVALID", "A source document is addressed by its SHA-256.")
    with _document_source_lock:
        documents = list_documents(binding)
        origin = next((row for row in documents if (
            row.run_id, row.asset_sha256, row.revision_ref) == (run_id, asset_sha256, revision_ref)), None)
        if origin is None:
            raise StudioError(404, "DOCUMENT_NOT_FOUND", f"Run {run_id} has no source document {asset_sha256}.")
        copy = _work_copy(binding, origin, _page_replacements(documents), documents)
        if copy.refusal is not None:
            # An existing file is someone's work. Never refuse without saying
            # where it is, or an edited copy is orphaned out of sight.
            detail = copy.refusal
            if copy.path.is_file():
                detail += f" The copy already made is still at {copy.relative_path}."
            raise StudioError(422, "DOCUMENT_NOT_EDITABLE", detail)
        head_document = next(row for row in documents if (
            row.run_id, row.asset_sha256, row.revision_ref
        ) == (copy.head_run_id, copy.head_asset_sha256, copy.head_revision_ref))
        if copy.path.is_file():
            return copy
        # document_bytes(..., revision_ref=None) is a legacy wildcard lookup.
        # Here None is an exact registration, already selected above; retain
        # the registered reader's source and byte checks without reselecting it.
        data = _registered_document_bytes(binding, head_document)
        run = binding.load_run(origin.run_id)
        try:
            binding.repository.put_workspace_file(
                run=run,
                destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id),
                artifact_id=f"document-work-copy-{origin.asset_sha256}",
                workspace_relative_path=copy.path.relative_to(
                    binding.repository.layout.run(origin.run_id).workspaces).as_posix(),
                media_type=copy.mime_type, source=BytesIO(data),
            )
        except (ProjectRepositoryError, OSError, ValueError) as exc:
            raise StudioError(409, "DOCUMENT_WORK_COPY_FAILED",
                              "The editable copy could not be written in its run's workspace.") from exc
        # No receipt and no digest of its own: nothing crossed into project
        # truth here. HEAD, the registered page and its pages are untouched.
        return copy


def list_document_work_copies(binding: ProjectBinding) -> tuple[DocumentWorkCopy, ...]:
    """Every registered document that currently has an editable file.

    Derived and restart-safe: each registered document names exactly one
    possible copy path, and the row exists only when that one file does. This
    is an existence check per registered document, not a walk of the workspace
    — nothing here discovers a file the project does not already account for,
    and no second store remembers which copies were made.

    A row whose ``refusal`` is set is listed like any other. A file on disk
    does not stop existing because the page it answered for moved, and a
    watcher that dropped it would silently discard every later save; the
    refusal travels with the row so it can be reported instead.
    """

    documents = list_documents(binding)
    links = _page_replacements(documents)
    copies = [_work_copy(binding, document, links, documents) for document in documents]
    return tuple(copy for copy in copies if copy.path.is_file())


def save_viewport_capture(
    binding: ProjectBinding,
    run_id: str,
    png_base64: str,
) -> ViewportCapture:
    """Retain one PNG below the explicitly named existing run through P036."""

    if not isinstance(png_base64, str) or not png_base64:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a PNG image",
        )
    try:
        png_bytes = base64.b64decode(png_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not valid base64 PNG data",
        ) from exc
    # Pillow tolerates a truncated IEND checksum; captures must be complete.
    if not png_bytes.endswith(PNG_END):
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a complete PNG image",
        )
    try:
        with Image.open(BytesIO(png_bytes), formats=["PNG"]) as capture:
            capture.verify()
        # verify checks the chunks, not the compressed pixel data.
        with Image.open(BytesIO(png_bytes), formats=["PNG"]) as capture:
            capture.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a readable PNG image",
        ) from exc
    run = binding.load_run(run_id)

    digest = hashlib.sha256(png_bytes).hexdigest()
    workspace_path = f"studio-captures/viewport-{digest}.png"
    try:
        ref = binding.repository.put_workspace_file(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_WORKSPACE,
                run_id=run.run_id,
            ),
            artifact_id=f"viewport-capture-{digest}",
            workspace_relative_path=workspace_path,
            media_type=PNG_MEDIA_TYPE,
            source=BytesIO(png_bytes),
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            409,
            "CAPTURE_WRITE_FAILED",
            f"{binding.project_id}: the viewport capture could not be retained "
            f"in run {run.run_id}: {error_sentence(exc)}",
        ) from exc
    return ViewportCapture(
        project_id=ref.project_id,
        run_id=run.run_id,
        relative_path=ref.relative_path,
        sha256=ref.sha256,
        media_type=ref.media_type,
        size_bytes=len(png_bytes),
    )


def list_artifacts(
    binding: ProjectBinding, *, run_id: str | None = None, include_candidate_sources: bool = False,
) -> ArtifactListing:
    """Certified artifacts in the requested run, or the whole project when omitted."""

    records: list[ArtifactRecord] = []
    skipped: list[str] = []
    for run_id in binding.run_ids() if run_id is None else (run_id,):
        try:
            record_refs = binding.record_refs(run_id)
            refs = _receipt_refs(record_refs)
            records.extend(_registered_model_assets(binding, run_id, record_refs))
        except (StudioError, ProjectRepositoryError, ValueError, OSError):
            skipped.append(run_id)
            continue
        if not refs:
            continue
        # One walk of the run's workspaces answers every receipt in it.
        index = _workspace_index(binding, run_id)
        for ref in refs:
            try:
                payload = binding.repository.load_json(ref)
            except (ProjectRepositoryError, ValueError, OSError):
                # One receipt that will not load costs its own row and no
                # more: the other receipts in this run still certify exactly
                # what they certified. The run is named as skipped, because a
                # listing that dropped a row silently would be a shorter
                # answer indistinguishable from a complete one.
                if run_id not in skipped:
                    skipped.append(run_id)
                continue
            records.extend(_artifacts(binding, run_id, ref, payload, index))
    if include_candidate_sources:
        sources: dict[str, tuple[str, str] | None] = {}
        for row_index, record in enumerate(records):
            if record.design_state_digest is None:
                continue
            if record.run_id not in sources:
                sources[record.run_id] = _candidate_stage_source(binding, record.run_id)
            source = sources[record.run_id]
            if source is not None and source[0] == record.design_state_digest:
                records[row_index] = replace(record, source_stage_ref=source[1])
    records.sort(key=lambda item: (item.run_id, item.stage_id or "", item.file_name))
    return ArtifactListing(
        project_id=binding.project_id,
        artifacts=tuple(records),
        skipped_runs=tuple(skipped),
    )


def _candidate_stage_source(binding: ProjectBinding, run_id: str) -> tuple[str, str] | None:
    """Read a candidate's retained state digest and committed source, without a view."""

    try:
        delta = binding.candidate_delta(run_id)
        if delta is None or delta.get("source_stage_ref") is None:
            return None
        newest = binding.newest_runner_receipt(run_id)
        if newest is None:
            return None
        # reference_run also surveys every project run for projection warnings.
        # Use its same receipt chooser here, without that unrelated survey.
        reference = ReferenceRun(binding.load_run(run_id), "query", newest[1])
        _, record = binding.exact_state_record(reference)
        state_digest = newest[1].get("design_state_digest")
        if (delta.get("result_record_digest") != record.digest or
                not isinstance(state_digest, str) or not SHA256_HEX.fullmatch(state_digest)):
            return None
        stage_ref = ProjectRecordRef.from_dict(delta["source_stage_ref"])
        binding.design_stage(stage_ref)
        return state_digest, stage_ref.uri
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError):
        # A missing or invalid candidate source must not hide certified bytes.
        return None


def require_model_source(
    binding: ProjectBinding, source: ModelSource, projection: StateProjection | None = None,
) -> ArtifactRecord:
    """Resolve both identities; a matching state alone cannot identify model bytes."""

    actual = projection or project_state(binding, source.run_id)
    require_actionable(actual)
    if not actual.reference_state_exact or (actual.run.run_id, actual.state_digest) != (source.run_id, source.state_digest):
        raise StudioError(409, "MODEL_SOURCE_MISMATCH", "The model source does not match the exact retained editing state.")
    record = next((row for row in list_artifacts(binding, run_id=source.run_id).artifacts if (
        row.run_id, row.design_state_digest, row.sha256, row.format
    ) == (source.run_id, source.state_digest, source.asset_sha256, FORMAT_3DM)), None)
    if record is None:
        raise StudioError(409, "MODEL_SOURCE_UNREGISTERED", "This model has no retained artifact binding to the requested run state.")
    if not record.available:
        raise _unavailable(binding, record)
    return record


def require_complete_model(record: ArtifactRecord, runner_receipt: Mapping[str, Any]) -> None:
    """A native seat export can represent the whole run only if it covers every producing seat."""

    if record.representation == "composed":
        return
    produced = [row for row in runner_receipt.get("seat_results", ()) if row.get("program_ref")]
    if not produced or any((row.get("cad") or {}).get("execution_ref") != record.receipt_ref for row in produced):
        raise StudioError(409, "MODEL_SOURCE_INCOMPLETE", "This native export does not establish coverage of every producing seat. Select a complete composed model or the run's complete native delivery.")


# How long a work export waits for the one Rhino this machine has, and how
# long the host itself is given once it starts.
WORK_MODEL_HOST_WAIT_S = 30.0
WORK_MODEL_TIMEOUT_S = 900.0

_work_model_lock = threading.RLock()


def export_rhino_work_model(
    binding: ProjectBinding, settings: Any, *, run_id: str, sha256: str,
) -> ArtifactRecord:
    """Import one run's exact STEP into Rhino and keep the editable ``.3dm``.

    The STEP is named by the run it belongs to *and* its digest: the same
    bytes can be exported by more than one run, and the work model belongs to
    the run whose receipt, program and base it was asked for.

    Nothing is recompiled. The shapes in that STEP are split into one file per
    named object, the supervised Rhino host reads those files back, and the
    saved document is verified against both the program's denominator and the
    shapes it came from. A work model that already exists for the same source
    is answered again rather than exported twice; a failed attempt leaves its
    own directory behind and never blocks the next one.
    """

    from archflow.adapters.cad_execution import (  # imported late: the kernel CAD adapter
        WORK_MODEL_EXPORT_PATH, CadExecutionError, CadProgramBinding, StepImportSource,
        discover_powershell, discover_rhino_executables, execute_rhino_three_dm_export,
        prepare_rhino_three_dm_export, split_step_objects, verify_work_model_geometry,
        work_model_workspace,
    )
    from archflow.adapters.cad_program import CadTranslationError
    from archflow.project.refs import BranchRef
    from monkeyarch.capabilities.geometry_proposal import load_compiled_geometry_program

    with _work_model_lock:
        listing = list_artifacts(binding, run_id=run_id)
        source_listing = listing
        if SHA256_HEX.match(sha256) is not None and not any(row.sha256 == sha256 for row in listing.artifacts):
            # A wrong-run request still names the runs that actually exported it.
            source_listing = list_artifacts(binding)
        source_record = _work_model_source(source_listing, run_id=run_id, sha256=sha256)
        existing = _existing_work_model(listing, source_record)
        if existing is not None:
            return existing
        if not source_record.available or source_record.path is None:
            raise _unavailable(binding, source_record)
        powershell = settings.powershell or discover_powershell()
        if powershell is None:
            raise StudioError(
                409, "RHINO_HOST_UNAVAILABLE",
                "An editable work model is supervised by a local PowerShell, and none was "
                "configured or found on this machine.",
            )
        if not discover_rhino_executables():
            raise StudioError(
                409, "RHINO_HOST_UNAVAILABLE",
                "An editable work model is written by this machine's Rhino, and no "
                "Rhino installation was found here. The exact STEP is available now.",
            )
        if source_record.program_ref is None:
            raise StudioError(
                409, "WORK_MODEL_SOURCE_UNBOUND",
                "This export's receipt names no compiled program, so there is nothing to bind "
                "an editable work model to.",
            )
        receipt = binding.repository.load_json(
            record_ref_from_uri(source_record.receipt_ref, binding.project_id)
        )
        receipt_binding = _mapping(_mapping(receipt.get("identity")).get("binding"))
        program_ref = record_ref_from_uri(source_record.program_ref, binding.project_id)
        program = load_compiled_geometry_program(binding.repository.load_json(program_ref))
        run = binding.load_run(run_id)
        cad_binding = CadProgramBinding(
            program_ref=program_ref,
            branch=BranchRef(run=run, branch_id=source_record.branch_id, epoch=source_record.branch_epoch),
            stage_id=source_record.stage_id,
            program_digest=source_record.program_digest,
            design_state_digest=source_record.design_state_digest,
            # The program this export realized may continue an earlier one.
            # bind_program checks the pair, so the predecessor is read off the
            # receipt that was written for it rather than assumed absent.
            predecessor_program_digest=_text(receipt_binding.get("predecessor_program_digest")),
        )
        # The export workspace is the run's own, from the P036 layout, and the
        # attempt directory is made inside it.
        workspace = work_model_workspace(
            cad_workspace_path(binding.repository.layout.run(run_id).workspaces, source_record.stage_id),
            source_sha256=sha256,
        )
        try:
            objects = split_step_objects(
                source_record.path, destination=workspace, length_unit=source_record.length_unit or "meter",
            )
            maps = _source_component_maps(receipt)
            plan = prepare_rhino_three_dm_export(
                program,
                binding=cad_binding,
                speculative_workspace=workspace,
                artifact_name=f"{Path(source_record.file_name).stem}.work.3dm",
                readback_tolerance=0.003,
                provenance={"export_path": WORK_MODEL_EXPORT_PATH, "source_step_sha256": sha256},
                # The layers and material declarations this model was exported
                # with, read off its own receipt, so an editable copy keeps them.
                layer_by_component=maps["layer_by_component"],
                material_by_component=maps["material_by_component"],
                material_colors=maps["material_colors"],
                step_import=StepImportSource(
                    step_path=source_record.path, step_sha256=sha256, objects=objects,
                ),
                # The material table this very export retained beside its mesh
                # preview: the work model wears the materials this model was
                # written with, not a second derivation of them.
                source_materials={
                    object_id: _mapping(value)
                    for object_id, value in _mapping(
                        _mapping(receipt.get("preview_artifact")).get("materials")
                    ).items()
                },
            )
        except (CadExecutionError, CadTranslationError) as exc:
            raise StudioError(409, "WORK_MODEL_NOT_EXPORTABLE", error_sentence(str(exc))) from exc
        _require_same_semantics(maps, plan.expected_semantics, plan.expected_layer_colors)
        execution = execute_rhino_three_dm_export(
            plan,
            powershell_executable=powershell,
            timeout_seconds=WORK_MODEL_TIMEOUT_S,
            host_wait_seconds=WORK_MODEL_HOST_WAIT_S,
        )
        payload = {
            **execution.to_dict(),
            "export_path": WORK_MODEL_EXPORT_PATH,
            "source_step_sha256": sha256,
            "source_receipt_ref": source_record.receipt_ref,
            "import_objects": [item.to_dict() for item in objects],
        }
        if execution.status.value == "succeeded":
            try:
                payload["work_model_objects"] = list(
                    verify_work_model_geometry(plan.model_path, StepImportSource(
                        step_path=source_record.path, step_sha256=sha256, objects=objects,
                    ))
                )
            except CadExecutionError as exc:
                raise StudioError(409, "WORK_MODEL_NOT_EXACT", error_sentence(str(exc))) from exc
        binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=SEAT_RHINO_EXECUTION,
            payload=payload,
        )
        if execution.status.value != "succeeded":
            raise StudioError(
                409, "WORK_MODEL_EXPORT_FAILED",
                error_sentence(_work_model_failure(execution)),
            )
        exported = _existing_work_model(list_artifacts(binding, run_id=run_id), source_record)
        if exported is None:  # pragma: no cover - the receipt was just retained
            raise StudioError(
                409, "WORK_MODEL_EXPORT_FAILED",
                "The export reported success but its work model is not in this run's artifacts.",
            )
        return exported


def _source_component_maps(receipt: Mapping[str, Any]) -> dict[str, dict]:
    """The layers and materials this export actually wrote, per component.

    Read off the receipt's own preview inspection - each object's recorded
    layer path and its ``archflow:component`` / ``archflow:material`` user
    text - and the preview's material table for the colours. They are the
    same three maps the export was made with, so rebuilding the document's
    semantics from them reproduces the layers and material declarations that
    model already has instead of resetting it to the program's defaults.

    An object whose component cannot be read, or whose component is recorded
    with two different layers or materials, contributes nothing rather than a
    guess; ``export_rhino_work_model`` then refuses if what it can rebuild
    does not match what the source recorded.
    """

    inspection = _mapping(receipt.get("preview_inspection"))
    layer_by_component: dict[str, str] = {}
    material_by_component: dict[str, str] = {}
    conflicting: set[str] = set()
    for row in inspection.get("object_user_strings") or ():
        item = _mapping(row)
        attributes = {
            _text(_mapping(entry).get("key")): _text(_mapping(entry).get("value"))
            for entry in item.get("attributes") or ()
        }
        component = attributes.get("archflow:component")
        layer = _text(item.get("layer_path"))
        if component is None or "+" in component or layer is None:
            continue
        # A layer path is ``<category>::<components>``; the export's scheme is
        # the category, and the historical root is the default the translator
        # already produces, so it is left unstated rather than restated.
        category = layer[: -len(f"::{component}")] if layer.endswith(f"::{component}") else None
        material = attributes.get("archflow:material")
        for table, value in (
            (layer_by_component, None if category in (None, ROOT_LAYER) else category),
            (material_by_component, material),
        ):
            if value is None:
                continue
            if table.setdefault(component, value) != value:
                conflicting.add(component)
    for component in conflicting:
        layer_by_component.pop(component, None)
        material_by_component.pop(component, None)
    colors: dict[str, tuple[int, int, int]] = {}
    materials = _mapping(_mapping(receipt.get("preview_artifact")).get("materials"))
    for value in materials.values():
        row = _mapping(value)
        name, diffuse = _text(row.get("name")), row.get("diffuse")
        if name is not None and isinstance(diffuse, list) and len(diffuse) == 3:
            colors.setdefault(name, tuple(int(channel) for channel in diffuse))
    layer_colors = {
        _text(_mapping(row).get("full_path")): tuple(
            int(channel) for channel in (_mapping(row).get("color_rgba") or ())[:3]
        )
        for row in inspection.get("layers") or ()
        if len(_mapping(row).get("color_rgba") or ()) >= 3
    }
    # A material's logical name (the ``archflow:material`` declaration) need
    # not be the name of the native material the preview wrote - a glazing
    # member carries the role's own. Where the table cannot answer for the
    # declared name, the colour that component's layer actually has in the
    # exported model does, so a layer keeps its colour instead of falling back
    # to the path-derived default.
    for component, material in material_by_component.items():
        if material in colors:
            continue
        layer_path = f"{layer_by_component.get(component, ROOT_LAYER)}::{component}"
        recorded = layer_colors.get(layer_path)
        if recorded is not None:
            colors[material] = recorded
    return {
        "layer_by_component": layer_by_component,
        "material_by_component": material_by_component,
        "material_colors": colors,
        "layer_colors": layer_colors,
        "object_layers": {
            _text(_mapping(row).get("name")): _text(_mapping(row).get("layer_path"))
            for row in inspection.get("object_user_strings") or ()
        },
    }


def _require_same_semantics(
    maps: Mapping[str, Any], semantics: Mapping[str, Any],
    layer_colors: Iterable[tuple[str, tuple[int, int, int]]] = (),
) -> None:
    """What the work model will carry must be what the source model carries."""

    for layer_path, color in layer_colors:
        recorded_color = maps.get("layer_colors", {}).get(layer_path)
        if recorded_color is not None and tuple(color) != tuple(recorded_color):
            raise StudioError(
                409, "WORK_MODEL_NOT_EXPORTABLE",
                error_sentence(
                    f"layer {layer_path!r} is {recorded_color} in the exported model, and an "
                    f"editable copy would make it {tuple(color)}. The export is refused rather "
                    "than recolouring it."
                ),
            )
    recorded = maps["object_layers"]
    for object_id, row in _mapping(semantics.get("objects")).items():
        expected = recorded.get(object_id)
        if expected is not None and _mapping(row).get("layer") != expected:
            raise StudioError(
                409, "WORK_MODEL_NOT_EXPORTABLE",
                error_sentence(
                    f"{object_id} is on layer {expected!r} in the exported model, and an editable "
                    f"copy would put it on {_mapping(row).get('layer')!r}. The export is refused "
                    "rather than moving it."
                ),
            )


def _work_model_source(listing: ArtifactListing, *, run_id: str, sha256: str) -> ArtifactRecord:
    """The exact STEP of that run and digest, or a refusal naming which is wrong."""

    if SHA256_HEX.match(sha256) is None:
        raise StudioError(
            404, "ARTIFACT_NOT_FOUND",
            f"{sha256!r} is not an artifact digest: artifacts are addressed by the 64 "
            "lowercase hex characters of their sha256.",
        )
    same_digest = [row for row in listing.artifacts if row.sha256 == sha256]
    candidates = [row for row in same_digest if row.run_id == run_id]
    if not candidates:
        runs = sorted({row.run_id for row in same_digest})
        detail = (
            f"No artifact {sha256} belongs to run {run_id}."
            + (f" That file is exported by {', '.join(runs)}." if runs else "")
            + _unsearched(listing.skipped_runs)
        )
        raise StudioError(404, "ARTIFACT_NOT_FOUND", detail)
    step = [row for row in candidates if row.format == FORMAT_STEP]
    if not step:
        raise StudioError(
            409, "WORK_MODEL_SOURCE_NOT_EXACT",
            "An editable work model is imported from the run's exact STEP; "
            f"{candidates[0].file_name} is a {candidates[0].format} "
            f"{candidates[0].representation} file.",
        )
    return step[0]


def _existing_work_model(listing: ArtifactListing, source: ArtifactRecord) -> ArtifactRecord | None:
    """The work model already exported from this exact source, if it is still there.

    Matched on the run and the source digest the receipt itself recorded, so
    another run's identical STEP never answers for this one, and a file that
    has since gone missing is re-exported rather than reported as present.
    """

    for row in listing.artifacts:
        if (
            row.format == FORMAT_3DM
            and row.representation == EXACT
            and row.status == "succeeded"
            and row.available
            and row.source_step_sha256 == source.sha256
            # Same run, same stage, same compiled program and the same exact
            # state that program was compiled against: a work model made from
            # another run's identical bytes, or from a different program or
            # state, is a different delivery and is not answered with here.
            and row.source_receipt_ref == source.receipt_ref
            and (row.run_id, row.stage_id, row.program_ref, row.program_digest, row.design_state_digest)
            == (source.run_id, source.stage_id, source.program_ref, source.program_digest, source.design_state_digest)
        ):
            return row
    return None


def _work_model_failure(execution: Any) -> str:
    """The host's own reason, not a summary of it."""

    for failure in execution.failures:
        detail = failure.get("detail") if isinstance(failure, Mapping) else None
        code = failure.get("code") if isinstance(failure, Mapping) else None
        if detail or code:
            return f"The Rhino work export did not finish: {code}: {detail}"
    return "The Rhino work export did not finish, and reported no reason."


_model_asset_lock = threading.RLock()


def register_model_asset(
    binding: ProjectBinding, run_id: str, state_digest: str, file_name: str, content_base64: str,
    *, event_sink: StudioEventSink | None = None,
) -> ArtifactRecord:
    """Retain an explicitly supplied composed model; never claim a native export."""

    projection = project_state(binding, run_id)
    require_actionable(projection)
    if not projection.reference_state_exact or projection.state_digest != state_digest:
        raise StudioError(409, "MODEL_SOURCE_MISMATCH", "Register the model against its exact retained run state.")
    if not file_name.lower().endswith(".3dm") or len(file_name) > 240 or any(char in file_name for char in "/\\\r\n\x00"):
        raise StudioError(422, "MODEL_ASSET_INVALID", "Provide a 3dm file name, not a server path.")
    if len(content_base64) > 4 * ((128 * 1024 * 1024 + 2) // 3):
        raise StudioError(413, "MODEL_ASSET_TOO_LARGE", "A model asset may contain at most 128 MiB.")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StudioError(422, "MODEL_ASSET_INVALID", "The model bytes are not valid base64.") from exc
    if not data.startswith(b"3D Geometry File Format"):
        raise StudioError(422, "MODEL_ASSET_INVALID", "The file is not a 3dm model.")
    digest = hashlib.sha256(data).hexdigest()
    source = ModelSource(run_id, state_digest, digest)
    with _model_asset_lock:
        existing = next((row for row in _registered_model_assets(binding, run_id) if row.model_source == source), None)
        if existing is not None:
            require_model_source(binding, source, projection)
            return existing
        try:
            inspected = inspect_three_dm_contents(data)
        except ThreeDmInspectionError as exc:
            raise StudioError(422, "MODEL_ASSET_INVALID", "The supplied 3dm cannot be read as a complete model.") from exc
        unit = {"Feet": "foot", "Inches": "inch", "Meters": "meter", "Millimeters": "millimeter"}.get(inspected.units["name"])
        if unit is None:
            raise StudioError(422, "MODEL_ASSET_INVALID", "The model must declare supported length units.")
        artifact = binding.repository.ingest(
            run=projection.run, destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=f"composed-model-{digest}", media_type="model/vnd.rhino", source=BytesIO(data),
        )
        binding.repository.put_json(
            run=projection.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=STUDIO_MODEL_ASSET,
            payload={
                "schema": "StudioModelAsset@1", "projectId": binding.project_id,
                "modelSource": source.to_dict(), "stateRecordRef": projection.record_source,
                "artifact": asdict(artifact), "fileName": file_name, "sizeBytes": len(data),
                "objectCount": inspected.object_count, "lengthUnit": unit,
            },
        )
        registered = require_model_source(binding, source, projection)
        if event_sink is not None:
            event_sink.publish(event={"type": "model_asset.registered", "run_id": run_id})
        return registered


def _registered_model_assets(
    binding: ProjectBinding, run_id: str, refs: tuple[ProjectRecordRef, ...] | None = None,
) -> tuple[ArtifactRecord, ...]:
    records = []
    for ref in binding.record_refs(run_id) if refs is None else refs:
        if record_kind(ref) != STUDIO_MODEL_ASSET:
            continue
        payload = binding.repository.load_json(ref)
        source = ModelSource.from_dict(payload["modelSource"])
        if payload.get("schema") != "StudioModelAsset@1" or payload.get("projectId") != binding.project_id or source.run_id != run_id:
            raise StudioError(409, "MODEL_SOURCE_MISMATCH", "The retained model asset has a different project or run binding.")
        path = binding.repository.layout.resolve_relative(payload["artifact"]["relative_path"])
        reason = FILE_MISSING
        try:
            if path.is_file():
                reason = None if _file_sha256(binding, path) == source.asset_sha256 else DIGEST_MISMATCH
        except OSError:
            reason = FILE_UNREADABLE
        run = binding.load_run(run_id)
        records.append(ArtifactRecord(
            artifact_id=source.asset_sha256, run_id=run_id, stage_id=None,
            file_name=payload["fileName"], relative_path=payload["artifact"]["relative_path"],
            path=path if reason is None else None, sha256=source.asset_sha256,
            size_bytes=payload["sizeBytes"], object_count=payload["objectCount"],
            status="registered", readback_verified=None, available=reason is None,
            unavailable_reason=reason, unavailable_error=None,
            base_version=run.base.version, base_state_sha256=run.base.state_sha256,
            branch_id=None, branch_epoch=None, program_ref=None, program_digest=None,
            design_state_digest=source.state_digest, length_unit=payload["lengthUnit"], up_axis="Z-up",
            receipt_ref=ref.uri, format=FORMAT_3DM, representation="composed", model_source=source,
        ))
    return tuple(records)


def artifact_bytes(
    binding: ProjectBinding, sha256: str, *, run_id: str | None = None,
) -> tuple[ArtifactRecord, bytes]:
    """The bytes of the artifact with that digest, re-verified as they are read.

    The listing already matched this file by content, but the check is made
    again here against the digest the caller asked for: between listing and
    reading, the file can change, and a download that no longer hashes to what
    was requested is a conflict, not a success.
    """

    if SHA256_HEX.match(sha256) is None:
        raise StudioError(
            404,
            "ARTIFACT_NOT_FOUND",
            f"{sha256!r} is not an artifact digest: artifacts are addressed by "
            "the 64 lowercase hex characters of their sha256.",
        )
    listing = list_artifacts(binding, run_id=run_id)
    claiming = [item for item in listing.artifacts if item.sha256 == sha256]
    if not claiming:
        raise StudioError(
            404,
            "ARTIFACT_NOT_FOUND",
            f"{binding.project_id}: no retained export receipt "
            f"({' or '.join(EXPORT_RECEIPT_KINDS)}) claims an artifact with "
            f"sha256 {sha256}"
            # "Not found" is only true of what was searched. Runs the listing
            # could not read were not searched, so they are named here rather
            # than letting the client read this as "no such artifact exists".
            + _unsearched(listing.skipped_runs),
        )
    # A digest exported twice is still one file; serve the copy that is there.
    record = next(
        (item for item in claiming if item.available), claiming[0]
    )
    if record.path is None:
        raise _unavailable(binding, record)
    try:
        data = record.path.read_bytes()
    except OSError as exc:
        raise StudioError(
            409,
            "ARTIFACT_UNREADABLE",
            f"{record.relative_path}: the file receipt {record.receipt_ref} "
            f"certifies could not be read ({error_sentence(exc)}). "
            "Nothing is claimed about its contents.",
        ) from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise StudioError(
            409,
            "ARTIFACT_DIGEST_MISMATCH",
            f"{record.relative_path} changed while it was being served: its "
            f"bytes hash to {actual}, not to the {sha256} its receipt "
            f"{record.receipt_ref} certifies.",
        )
    return record, data


def _unavailable(binding: ProjectBinding, record: ArtifactRecord) -> StudioError:
    """Why a receipt's digest cannot be served, in the receipt's own terms."""

    if record.unavailable_reason == DIGEST_MISMATCH:
        return StudioError(
            409,
            "ARTIFACT_DIGEST_MISMATCH",
            f"{binding.project_id}: run {record.run_id} holds a file named "
            f"{record.file_name}, but none of the copies in its workspaces "
            f"hashes to {record.sha256}, which receipt {record.receipt_ref} "
            "certifies. The file on disk is not the exported model.",
        )
    if record.unavailable_reason == FILE_UNREADABLE:
        # Deliberately not a mismatch: an unopened file has not disagreed with
        # anything. What failed is the read, and the read is what is reported.
        return StudioError(
            409,
            "ARTIFACT_UNREADABLE",
            f"{binding.project_id}: run {record.run_id} holds a file named "
            f"{record.file_name}, but the copies of it in that run's "
            f"workspaces could not be read ({record.unavailable_error}), so "
            f"whether any of them is the {record.sha256} that receipt "
            f"{record.receipt_ref} certifies is unknown.",
        )
    return StudioError(
        404,
        "ARTIFACT_NOT_FOUND",
        f"{binding.project_id}: receipt {record.receipt_ref} certifies "
        f"{record.file_name} with sha256 {record.sha256}, but no such file is "
        f"in the workspaces of run {record.run_id}.",
    )


def _unsearched(skipped_runs: tuple[str, ...]) -> str:
    """The runs a "not found" did not actually look in, named in its detail."""

    if not skipped_runs:
        return "."
    count = len(skipped_runs)
    noun = "directory" if count == 1 else "directories"
    return (
        f"; {count} run {noun} could not be read and may hold the claiming "
        f"receipt: {', '.join(skipped_runs)}."
    )


def _receipt_refs(refs: tuple[ProjectRecordRef, ...]) -> tuple[ProjectRecordRef, ...]:
    """The run's execution receipt records, digest-verified by the repository.

    Listing only. What each receipt *says* is read one at a time by the
    caller, so a record that will not load costs one row rather than a run.
    """

    return tuple(
        ref
        for ref in refs
        if record_kind(ref) in EXPORT_RECEIPT_KINDS
    )


def _workspace_index(
    binding: ProjectBinding, run_id: str
) -> dict[str, tuple[Path, ...]]:
    """Every file under one run's ``workspaces``, indexed by name.

    The walk is bounded by that directory: the search for an exported model
    never leaves the run that claims to have produced it, and never asks the
    project at large what ``.3dm`` files it happens to contain.
    """

    root = binding.repository.layout.run(run_id).workspaces
    if not root.is_dir():
        return {}
    found: dict[str, list[Path]] = {}
    for parent, directories, files in os.walk(root):
        parent_path = Path(parent)
        # Directories are indexed alongside files: something wearing the
        # artifact's name that cannot be opened is a different answer from
        # nothing being there, and the caller is entitled to the difference.
        for name in (*files, *directories):
            found.setdefault(name, []).append(parent_path / name)
    return {name: tuple(sorted(paths)) for name, paths in found.items()}


def _artifacts(
    binding: ProjectBinding,
    run_id: str,
    ref: ProjectRecordRef,
    receipt: Mapping[str, Any],
    index: Mapping[str, tuple[Path, ...]],
) -> tuple[ArtifactRecord, ...]:
    """One receipt read onto the wire's terms: one row per file it certifies.

    A Rhino receipt certifies one ``.3dm``. An OCCT receipt certifies the
    exact STEP file and the mesh preview tessellated from the same model, and
    is two rows sharing the receipt, the stage and the program binding — a
    client groups them by ``receipt_ref``; they are never two candidates.
    """

    identity = _mapping(receipt.get("identity"))
    program_binding = _mapping(identity.get("binding"))
    base = _mapping(program_binding.get("base"))
    bound = dict(
        run_id=run_id,
        stage_id=_text(program_binding.get("stage_id")),
        status=_text(receipt.get("status")),
        readback_verified=_flag(receipt.get("readback_verified")),
        base_version=_whole(base.get("version")),
        base_state_sha256=_text(base.get("state_sha256")),
        branch_id=_text(program_binding.get("branch_id")),
        branch_epoch=_whole(program_binding.get("branch_epoch")),
        program_ref=_text(_mapping(program_binding.get("program_ref")).get("uri")),
        program_digest=_text(program_binding.get("program_digest")),
        design_state_digest=_text(program_binding.get("design_state_digest")),
        length_unit=_text(identity.get("length_unit")),
        up_axis=_text(identity.get("up_axis")),
        receipt_ref=ref.uri,
    )

    def row(
        file_name: str,
        claimed: str | None,
        *,
        format: str,
        representation: str,
        size_bytes: int | None,
        object_count: int | None,
    ) -> ArtifactRecord:
        resolution = _resolve(binding, index, file_name, claimed)
        path = resolution.path
        return ArtifactRecord(
            # Content addresses the artifact; a receipt whose export claimed
            # no digest can only be addressed by the receipt itself, and says so.
            artifact_id=claimed if claimed is not None else f"receipt:{ref.sha256}",
            file_name=file_name,
            relative_path=(
                None
                if path is None
                else path.relative_to(binding.repository.layout.root).as_posix()
            ),
            path=path,
            sha256=claimed,
            size_bytes=size_bytes,
            object_count=object_count,
            available=path is not None,
            unavailable_reason=resolution.reason,
            unavailable_error=resolution.error,
            format=format,
            representation=representation,
            **bound,
        )

    if receipt.get("schema") != OCCT_RECEIPT_SCHEMA:
        inspection = _mapping(receipt.get("inspection"))
        native = row(
            Path(_text(receipt.get("artifact_relative_path")) or "").name,
            _text(inspection.get("file_sha256")),
            format=FORMAT_3DM,
            representation=EXACT,
            size_bytes=_whole(inspection.get("file_bytes")),
            object_count=_whole(inspection.get("object_count")),
        )
        # A work model says which exact STEP it was imported from; a seat's
        # own Rhino export says nothing there and stays as it was.
        return (replace(
            native,
            source_step_sha256=_text(receipt.get("source_step_sha256")),
            source_receipt_ref=_text(receipt.get("source_receipt_ref")),
        ),)

    physical = receipt.get("physical_object_ids")
    object_count = len(physical) if isinstance(physical, list) else None
    exact = receipt.get("exact_artifact")
    preview = receipt.get("preview_artifact")
    rows: list[ArtifactRecord] = []
    if not isinstance(exact, Mapping) and not isinstance(preview, Mapping):
        # Nothing was written (the build itself failed): one row, addressed by
        # the receipt, named by the stem the runner would have used, so the
        # failure is listed with its reason rather than vanishing.
        stage_id = _text(program_binding.get("stage_id")) or "export"
        digest = _text(program_binding.get("program_digest")) or ""
        rows.append(
            row(
                f"{stage_id}@{digest[:12]}.step" if digest else f"{stage_id}.step",
                None,
                format=FORMAT_STEP,
                representation=EXACT,
                size_bytes=None,
                object_count=object_count,
            )
        )
    if isinstance(exact, Mapping):
        rows.append(
            row(
                Path(_text(exact.get("relative_path")) or "").name,
                _text(exact.get("sha256")),
                format=FORMAT_STEP,
                representation=EXACT,
                size_bytes=None,
                object_count=object_count,
            )
        )
    if isinstance(preview, Mapping):
        inspected = _whole(_mapping(receipt.get("preview_inspection")).get("object_count"))
        rows.append(
            row(
                Path(_text(preview.get("relative_path")) or "").name,
                _text(preview.get("sha256")),
                format=FORMAT_3DM,
                representation=PREVIEW,
                size_bytes=None,
                object_count=inspected if inspected is not None else object_count,
            )
        )
    return tuple(rows)


def _resolve(
    binding: ProjectBinding,
    index: Mapping[str, tuple[Path, ...]],
    file_name: str,
    claimed: str | None,
) -> _Resolution:
    """The copy whose bytes are what the receipt certified, or why there is none.

    "None of them matched" and "none of them could be read" are separate
    answers. Only the first is evidence about content, so a candidate that
    refused to open is remembered as a failed read and never counted as a
    disagreeing one.
    """

    if claimed is None:
        return _Resolution(None, NO_DIGEST, None)
    candidates = index.get(file_name, ())
    if not candidates:
        return _Resolution(None, FILE_MISSING, None)
    read_one = False
    error: str | None = None
    for path in candidates:
        try:
            digest = _file_sha256(binding, path)
        except OSError as exc:
            error = error_sentence(exc)
            continue
        read_one = True
        if digest == claimed:
            return _Resolution(path, None, None)
    if read_one:
        return _Resolution(None, DIGEST_MISMATCH, None)
    return _Resolution(None, FILE_UNREADABLE, error)


def _file_sha256(binding: ProjectBinding, path: Path) -> str:
    """The file's own identity, hashed once per (path, size, mtime).

    Raises ``OSError`` when the file cannot be read: the caller decides what an
    unread candidate means, because here it means nothing at all.
    """

    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cache = binding.file_sha256_cache
    remembered = cache.get(key)
    if remembered is not None:
        return remembered
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    cache[key] = digest
    return digest


def _mapping(value: object) -> Mapping[str, Any]:
    """A nested receipt section, or an empty one when the receipt omits it."""

    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    """A receipt string that is actually a string, and not an empty one."""

    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
