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
from pathlib import Path
import re
import threading
from typing import Any, Mapping, NamedTuple

from PIL import Image, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from archflow.project.record_kinds import (
    SEAT_OCCT_EXECUTION, SEAT_RHINO_EXECUTION, STUDIO_SOURCE_DOCUMENT, STUDIO_MODEL_ASSET,
    STUDIO_DOCUMENT_MODEL_SOURCE,
)
from archflow.adapters.three_dm_inspector import inspect_three_dm_contents, ThreeDmInspectionError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import ProjectRepositoryError

from ..ports import StudioEventSink
from ..transport.errors import StudioError, error_sentence
from .binding import ProjectBinding, record_kind
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
PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"

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
DOCUMENT_MEDIA_TYPES = ("application/pdf", "image/png", "image/jpeg")


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Visible page size after crop/rotation; PDF points or oriented image pixels."""

    page_index: int
    width: float
    height: float
    rotation: int = 0


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """An imported reference, never an exported model or design state."""

    project_id: str
    run_id: str
    asset_sha256: str
    file_name: str
    mime_type: str
    size_bytes: int
    pages: tuple[DocumentPage, ...]
    model_source: ModelSource | None = None
    model_source_binding_ref: str | None = None


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


def list_documents(binding: ProjectBinding, run_id: str) -> tuple[SourceDocument, ...]:
    """Only registered source files in this run, separately from model exports."""

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
        )
        previous = documents.get(document.asset_sha256)
        if previous is None:
            documents[document.asset_sha256] = document
        elif document.model_source is not None:
            if previous.model_source is not None and previous.model_source != document.model_source:
                raise StudioError(409, "DOCUMENT_SOURCE_CONFLICT", "This document has competing model associations; its pages remain retained.")
            if previous.model_source is None:
                documents[document.asset_sha256] = replace(previous, model_source=document.model_source,
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
    return tuple(sorted(documents.values(), key=lambda doc: (doc.file_name, doc.asset_sha256)))


def document_bytes(
    binding: ProjectBinding, run_id: str, asset_sha256: str,
) -> tuple[SourceDocument, bytes]:
    """Serve the registered original only; the caller cannot supply a disk path."""

    if not SHA256_HEX.fullmatch(asset_sha256):
        raise StudioError(422, "DOCUMENT_INVALID", "A source document is addressed by its SHA-256.")
    document = next((row for row in list_documents(binding, run_id) if row.asset_sha256 == asset_sha256), None)
    if document is None:
        raise StudioError(404, "DOCUMENT_NOT_FOUND", f"Run {run_id} has no source document {asset_sha256}.")
    try:
        path = binding.repository.layout.resolve_relative(f"objects/sha256/{asset_sha256[:2]}/{asset_sha256}")
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise StudioError(409, "DOCUMENT_UNAVAILABLE", "The registered source document cannot be read.") from exc
    if hashlib.sha256(data).hexdigest() != asset_sha256:
        raise StudioError(409, "DOCUMENT_DIGEST_MISMATCH", "The source file no longer matches the version this document names.")
    return document, data


def save_document(
    binding: ProjectBinding, run_id: str, file_name: str, mime_type: str, content_base64: str,
    model_source: ModelSource | None = None,
) -> SourceDocument:
    """Retain original bytes via the object port and register them in the named run."""

    run = binding.load_run(run_id)
    if model_source is not None:
        require_model_source(binding, model_source)
    if not file_name.strip() or len(file_name) > 240 or any(char in file_name for char in "/\\\r\n\x00"):
        raise StudioError(422, "DOCUMENT_INVALID", "Provide a file name, not a server path.")
    if mime_type not in DOCUMENT_MEDIA_TYPES:
        raise StudioError(422, "DOCUMENT_INVALID", "Only PDF, PNG and JPEG source documents are supported.")
    if len(content_base64) > 4 * ((MAX_DOCUMENT_BYTES + 2) // 3):
        raise StudioError(413, "DOCUMENT_TOO_LARGE", "Source documents may contain at most 32 MiB.")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StudioError(422, "DOCUMENT_INVALID", "The source document is not valid base64 data.") from exc
    if len(data) > MAX_DOCUMENT_BYTES:
        raise StudioError(413, "DOCUMENT_TOO_LARGE", "Source documents may contain at most 32 MiB.")
    pages = _document_pages(data, mime_type)
    digest = hashlib.sha256(data).hexdigest()
    with _document_source_lock:
        existing = next((row for row in list_documents(binding, run_id) if row.asset_sha256 == digest), None)
        if existing is not None:
            if existing.model_source != model_source:
                raise StudioError(409, "DOCUMENT_SOURCE_IMMUTABLE", "This document's model source is already retained. Its saved pages cannot be rebound to another model.")
            document_bytes(binding, run_id, digest)
            return existing
        document = SourceDocument(binding.project_id, run_id, digest, file_name, mime_type, len(data), pages, model_source)
        try:
            binding.repository.ingest(
                run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                artifact_id=f"source-document-{digest}", media_type=mime_type, source=BytesIO(data),
            )
            binding.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
                record_kind=STUDIO_SOURCE_DOCUMENT,
                payload={"schema": "StudioSourceDocument@1", **{
                    key: value for key, value in asdict(document).items() if key not in ("model_source", "model_source_binding_ref")
                }, **({"modelSource": model_source.to_dict()} if model_source else {})},
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


def list_artifacts(binding: ProjectBinding) -> ArtifactListing:
    """Every artifact certified by a receipt in this project, run by run."""

    records: list[ArtifactRecord] = []
    skipped: list[str] = []
    for run_id in binding.run_ids():
        try:
            refs = _receipt_refs(binding, run_id)
            records.extend(_registered_model_assets(binding, run_id))
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
    records.sort(key=lambda item: (item.run_id, item.stage_id or "", item.file_name))
    return ArtifactListing(
        project_id=binding.project_id,
        artifacts=tuple(records),
        skipped_runs=tuple(skipped),
    )


def require_model_source(
    binding: ProjectBinding, source: ModelSource, projection: StateProjection | None = None,
) -> ArtifactRecord:
    """Resolve both identities; a matching state alone cannot identify model bytes."""

    actual = projection or project_state(binding, source.run_id)
    require_actionable(actual)
    if not actual.reference_state_exact or (actual.run.run_id, actual.state_digest) != (source.run_id, source.state_digest):
        raise StudioError(409, "MODEL_SOURCE_MISMATCH", "The model source does not match the exact retained editing state.")
    record = next((row for row in list_artifacts(binding).artifacts if (
        row.run_id, row.design_state_digest, row.sha256, row.format
    ) == (source.run_id, source.state_digest, source.asset_sha256, FORMAT_3DM)), None)
    if record is None:
        raise StudioError(409, "MODEL_SOURCE_UNREGISTERED", "This model has no retained artifact binding to the requested run state.")
    if not record.available:
        raise _unavailable(binding, record)
    return record


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


def _registered_model_assets(binding: ProjectBinding, run_id: str) -> tuple[ArtifactRecord, ...]:
    records = []
    for ref in binding.record_refs(run_id):
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
    binding: ProjectBinding, sha256: str
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
    listing = list_artifacts(binding)
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


def _receipt_refs(
    binding: ProjectBinding, run_id: str
) -> tuple[ProjectRecordRef, ...]:
    """The run's execution receipt records, digest-verified by the repository.

    Listing only. What each receipt *says* is read one at a time by the
    caller, so a record that will not load costs one row rather than a run.
    """

    return tuple(
        ref
        for ref in binding.record_refs(run_id)
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
        return (
            row(
                Path(_text(receipt.get("artifact_relative_path")) or "").name,
                _text(inspection.get("file_sha256")),
                format=FORMAT_3DM,
                representation=EXACT,
                size_bytes=_whole(inspection.get("file_bytes")),
                object_count=_whole(inspection.get("object_count")),
            ),
        )

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
