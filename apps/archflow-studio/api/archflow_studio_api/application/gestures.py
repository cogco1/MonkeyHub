"""Gestures on the model, read into facts the record can vouch for.

An architect circles an area, draws an arrow, marks something to keep. The
client records the stroke, the camera and the objects under it, and derives
nothing; this module turns those hits into the record's own names through the
pick resolver (element ids are resolved, never guessed) and writes what it
found as plain sentences. The sentences go on the record sheet the agent
reads, so the agent learns "an arrow on portico-base pointing up", not "there
is an arrow in the picture" - and the deterministic parts stay deterministic:
a keep mark becomes a keep clause, a circle becomes the selection.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import io
import math
import threading

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_DOCUMENT_ANNOTATIONS, STUDIO_DOCUMENT_COMMENT
from archflow.project.refs import ProjectRecordRef

from ..transport.errors import StudioError
from .artifacts import ModelSource, SourceDocument, document_bytes, require_model_source
from .binding import ProjectBinding, record_kind

from .intent_agent import DocumentVisual, Selection
from .pick import PickRequest, resolve_pick
from .projection import StateProjection
from archflow.project.record_kinds import STUDIO_MODEL_ANNOTATIONS

CIRCLE = "circle"
ARROW = "arrow"
KEEP = "keep"
REMOVE = "remove"
FREEHAND = "freehand"
LINE = "line"
RULER = "ruler"
ARC = "arc"
KINDS = (CIRCLE, ARROW, KEEP, REMOVE, FREEHAND, LINE, RULER, ARC)
TEXT = "text"
DOCUMENT_KINDS = (*KINDS, TEXT)

Vector = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class GestureHit:
    """One object a stroke sample fell on, as the viewer read it off the file."""

    object_name: str | None
    user_strings: Mapping[str, str]
    world: Vector


@dataclass(frozen=True, slots=True)
class Gesture:
    """One stroke, bound to the camera it was drawn in and the objects it hit."""

    kind: str
    hits: tuple[GestureHit, ...]
    world_direction: Vector | None = None
    length_model_units: float | None = None
    label: str | None = None
    screen: tuple[tuple[float, float], ...] = ()
    color: str | None = None
    line_width: int | None = None
    camera: Mapping[str, object] | None = None
    screen_size: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class GestureReading:
    """What the gestures say, in the record's names.

    ``facts`` are the sentences for the sheet, one or more per gesture, in
    order. ``keep_refs`` are the prefixed refs keep marks resolved to.
    ``target`` is the selection a circle implies (the component holding most
    of its hits, and the element when the circle held exactly one), or None.
    """

    facts: tuple[str, ...]
    keep_refs: tuple[str, ...]
    target: Selection | None


@dataclass(frozen=True, slots=True)
class DocumentGesture:
    """Ink in the visible page, with no model camera, hit or execution target."""

    id: str
    kind: str
    points: tuple[tuple[float, float], ...]
    color: str
    line_width: float
    label: str | None = None
    font_size: float | None = None

    def __post_init__(self) -> None:
        if not self.id or self.kind not in DOCUMENT_KINDS or not self.points:
            raise ValueError("a document annotation needs an id, a known tool and page points")
        if not all(math.isfinite(value) and 0 <= value <= 1 for point in self.points for value in point):
            raise ValueError("document points must lie in the normalized visible page")
        if not math.isfinite(self.line_width) or not 0 < self.line_width <= 1:
            raise ValueError("line width is a fraction of the visible page's shorter side")
        if self.kind == TEXT:
            if len(self.points) != 1:
                raise ValueError("page text needs one top-left anchor")
            if not self.label or not self.label.strip() or len(self.label) > 2000:
                raise ValueError("page text needs non-empty content of at most 2000 characters")
            if self.font_size is None or not math.isfinite(self.font_size) or not 0 < self.font_size <= 1:
                raise ValueError("page text needs fontSize as a fraction of the visible page's shorter side")
        elif self.font_size is not None:
            raise ValueError("fontSize belongs only to page text")
        elif self.label is not None and len(self.label) > 120:
            raise ValueError("a stroke label contains at most 120 characters")

    def to_dict(self) -> dict:
        result = {
            "id": self.id, "kind": self.kind, "points": [list(point) for point in self.points],
            "color": self.color, "lineWidth": self.line_width, "label": self.label,
        }
        # Preserve the serialized shape (and identity) of existing stroke data.
        if self.font_size is not None:
            result["fontSize"] = self.font_size
        return result


@dataclass(frozen=True, slots=True)
class DocumentAnnotationRef:
    run_id: str
    asset_sha256: str
    page_index: int
    revision_sha256: str
    drawing_revision_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "runId": self.run_id, "assetSha256": self.asset_sha256,
            "pageIndex": self.page_index, "revisionSha256": self.revision_sha256,
            **({"drawingRevisionRef": self.drawing_revision_ref} if self.drawing_revision_ref is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class DocumentAnnotationPage:
    project_id: str
    run_id: str
    asset_sha256: str
    page_index: int
    revision_sha256: str | None
    annotations: tuple[DocumentGesture, ...] = ()
    comment: str = ""
    drawing_revision_ref: str | None = None


# HTTP saves in this process must check and write a page revision together.
# Durable data still lives exclusively in P036; this lock holds no saved state.
_document_annotation_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class ModelAnnotationSnapshot:
    project_id: str
    model_source: ModelSource
    revision_sha256: str | None
    annotations: tuple[Mapping, ...] = ()
    comment: str = ""


def _model_annotation_revisions(binding: ProjectBinding, source: ModelSource) -> dict[str, Mapping]:
    revisions = {}
    for ref in binding.record_refs(source.run_id):
        if record_kind(ref) != STUDIO_MODEL_ANNOTATIONS:
            continue
        payload = binding.repository.load_json(ref)
        if payload.get("modelSource") != source.to_dict():
            continue
        if payload.get("schema") != "StudioModelAnnotations@1" or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "ANNOTATION_BINDING_MISMATCH", "The saved model annotations belong to another project.")
        revisions[ref.sha256] = payload
    return revisions


def read_model_annotations(binding: ProjectBinding, source: ModelSource, revision_sha256: str | None = None) -> ModelAnnotationSnapshot:
    require_model_source(binding, source)
    revisions = _model_annotation_revisions(binding, source)
    revision = revision_sha256 or _latest_document_revision(revisions)
    if revision is not None and revision not in revisions:
        raise StudioError(404, "ANNOTATION_REVISION_NOT_FOUND", "This exact model source has no such annotation revision.")
    row = revisions.get(revision)
    return ModelAnnotationSnapshot(binding.project_id, source, revision,
                                   tuple(row["annotations"]) if row else (), row.get("comment", "") if row else "")


def save_model_annotations(
    binding: ProjectBinding, source: ModelSource, base_revision_sha256: str | None,
    annotations: Sequence[Mapping], comment: str,
) -> ModelAnnotationSnapshot:
    require_model_source(binding, source)
    if len({row["id"] for row in annotations}) != len(annotations):
        raise StudioError(422, "ANNOTATION_INVALID", "Annotation ids must be distinct within this model source.")
    run = binding.load_run(source.run_id)
    with _document_annotation_lock:
        revisions = _model_annotation_revisions(binding, source)
        latest = _latest_document_revision(revisions)
        if base_revision_sha256 != latest:
            raise StudioError(409, "ANNOTATION_STALE", "This model was annotated from another saved revision. Read its latest annotations before retrying.")
        payload = {"schema": "StudioModelAnnotations@1", "projectId": binding.project_id,
                   "modelSource": source.to_dict(), "previousRevisionSha256": latest,
                   "annotations": [dict(row) for row in annotations], "comment": comment}
        ref = binding.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STUDIO_MODEL_ANNOTATIONS, payload=payload,
        )
    return ModelAnnotationSnapshot(binding.project_id, source, ref.sha256, tuple(annotations), comment)


def _document_page_source(binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int,
                          drawing_revision_ref: str | None = None, *, binding_ref: str | None = None) -> SourceDocument:
    document, _ = document_bytes(binding, run_id, asset_sha256, drawing_revision_ref, binding_ref=binding_ref)
    if not 0 <= page_index < len(document.pages):
        raise StudioError(422, "DOCUMENT_PAGE_NOT_FOUND", "The page does not exist in this source document version.")
    return document


def _document_page_revisions(binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int,
                             drawing_revision_ref: str | None = None) -> dict[str, Mapping]:
    revisions = {}
    for ref in binding.record_refs(run_id):
        if record_kind(ref) != STUDIO_DOCUMENT_ANNOTATIONS:
            continue
        payload = binding.repository.load_json(ref)
        if (payload.get("assetSha256"), payload.get("pageIndex"), payload.get("drawingRevisionRef")) != (asset_sha256, page_index, drawing_revision_ref):
            continue
        if payload.get("schema") != "StudioDocumentAnnotations@1" or (
            payload.get("projectId"), payload.get("runId")
        ) != (binding.project_id, run_id):
            raise StudioError(409, "ANNOTATION_BINDING_MISMATCH", "The saved annotations belong to another project or run.")
        revisions[ref.sha256] = payload
    return revisions


def _latest_document_revision(revisions: Mapping[str, Mapping]) -> str | None:
    parents = {payload.get("previousRevisionSha256") for payload in revisions.values()} - {None}
    tips = revisions.keys() - parents
    if (revisions and len(tips) != 1) or not parents.issubset(revisions):
        raise StudioError(409, "ANNOTATION_CONFLICT", "The saved page has competing or incomplete revisions. Its ink has been retained.")
    return next(iter(tips), None)


def _document_page_from(binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int, revision: str | None,
                        payload: Mapping | None, drawing_revision_ref: str | None = None) -> DocumentAnnotationPage:
    return DocumentAnnotationPage(
        binding.project_id, run_id, asset_sha256, page_index, revision,
        annotations=tuple(DocumentGesture(
            id=row["id"], kind=row["kind"], points=tuple(tuple(point) for point in row["points"]),
            color=row["color"], line_width=row["lineWidth"], label=row.get("label"),
            font_size=row.get("fontSize"),
        ) for row in payload["annotations"]) if payload is not None else (),
        comment=payload.get("comment", "") if payload is not None else "",
        drawing_revision_ref=drawing_revision_ref,
    )


def read_document_annotations(
    binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int,
    revision_sha256: str | None = None,
    drawing_revision_ref: str | None = None,
    *, binding_ref: str | None = None,
) -> DocumentAnnotationPage:
    _document_page_source(binding, run_id, asset_sha256, page_index, drawing_revision_ref, binding_ref=binding_ref)
    revisions = _document_page_revisions(binding, run_id, asset_sha256, page_index, drawing_revision_ref)
    revision = revision_sha256 or _latest_document_revision(revisions)
    if revision is not None and revision not in revisions:
        raise StudioError(404, "ANNOTATION_REVISION_NOT_FOUND", "This page has no saved annotation revision with that identity.")
    return _document_page_from(binding, run_id, asset_sha256, page_index, revision, revisions.get(revision), drawing_revision_ref)


def save_document_annotations(
    binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int,
    base_revision_sha256: str | None, annotations: Sequence[DocumentGesture], comment: str,
    drawing_revision_ref: str | None = None,
) -> DocumentAnnotationPage:
    _document_page_source(binding, run_id, asset_sha256, page_index, drawing_revision_ref)
    if len({annotation.id for annotation in annotations}) != len(annotations):
        raise StudioError(422, "ANNOTATION_INVALID", "Annotation ids must be distinct within a page.")
    run = binding.load_run(run_id)
    with _document_annotation_lock:
        revisions = _document_page_revisions(binding, run_id, asset_sha256, page_index, drawing_revision_ref)
        latest = _latest_document_revision(revisions)
        if base_revision_sha256 != latest:
            raise StudioError(409, "ANNOTATION_STALE", "This page was saved from another revision. Read its saved annotations before retrying.")
        payload = {
            "schema": "StudioDocumentAnnotations@1", "projectId": binding.project_id,
            "runId": run_id, "assetSha256": asset_sha256, "pageIndex": page_index,
            **({"drawingRevisionRef": drawing_revision_ref} if drawing_revision_ref is not None else {}),
            "previousRevisionSha256": latest,
            "annotations": [annotation.to_dict() for annotation in annotations], "comment": comment,
        }
        ref = binding.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=STUDIO_DOCUMENT_ANNOTATIONS, payload=payload,
        )
    return _document_page_from(binding, run_id, asset_sha256, page_index, ref.sha256, payload, drawing_revision_ref)


def _document_facts(document: SourceDocument, page: DocumentAnnotationPage) -> tuple[str, ...]:
    source = f"source document {document.file_name!r}, sha256 {page.asset_sha256}, run {page.run_id}, page {page.page_index + 1} (pageIndex {page.page_index}), annotation revision {page.revision_sha256}"
    facts = [f"{source}; coordinates are normalized to the visible page, not model geometry"]
    for annotation in page.annotations:
        xs, ys = zip(*annotation.points)
        if annotation.kind == TEXT:
            facts.append(
                f"document annotation {annotation.id}: text at ({xs[0]:.6g}, {ys[0]:.6g}); "
                f"color {annotation.color}; font size {annotation.font_size:.6g} of page short side; "
                f"text {annotation.label!r}"
            )
            continue
        facts.append(
            f"document annotation {annotation.id}: {annotation.kind}; "
            f"page bounds ({min(xs):.6g}, {min(ys):.6g}) to ({max(xs):.6g}, {max(ys):.6g}); "
            f"{len(annotation.points)} points; color {annotation.color}; width {annotation.line_width:.6g} of page short side"
            + (f"; label {annotation.label!r}" if annotation.label else "")
        )
    if page.comment:
        facts.append(f"Architect's saved page comment: {page.comment}")
    return tuple(facts)


def _document_visual_png(encoded: str) -> tuple[bytes, tuple[int, int]]:
    from PIL import Image

    try:
        png = base64.b64decode(encoded, validate=True)
        if not 0 < len(png) <= 4 * 1024 * 1024 or not png.startswith(b"\x89PNG\r\n\x1a\n") or not png.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ValueError("Expected a complete PNG of at most 4 MiB.")
        with Image.open(io.BytesIO(png)) as image:
            size = image.size
            if image.format != "PNG" or min(size) < 1 or max(size) > 2048:
                raise ValueError("The PNG longer side must be at most 2048 pixels.")
            image.verify()
        with Image.open(io.BytesIO(png)) as image:
            image.load()
    except (ValueError, OSError, SyntaxError, binascii.Error, Image.DecompressionBombError) as exc:
        raise StudioError(422, "DOCUMENT_VISUAL_INVALID", "The document visual must be a complete PNG, at most 4 MiB and 2048 pixels on its longer side.") from exc
    return png, size


def prepare_document_visuals(
    binding: ProjectBinding, references: Sequence[DocumentAnnotationRef], inputs: Sequence[Mapping],
) -> tuple[DocumentVisual, ...]:
    """Check registered page/revision bindings; pixels remain the client's render."""

    if not inputs:
        if references:
            raise StudioError(422, "DOCUMENT_VISUALS_REQUIRED", "Submit the document page image and its saved annotation overlay with this request.")
        return ()
    edits = [row for row in inputs if row["role"] == "edit"]
    if len(inputs) > 4 or len(references) != 1 or len(edits) != 1 or any(
        edits[0].get(key) != value for key, value in {
            **references[0].to_dict(), "drawingRevisionRef": references[0].drawing_revision_ref,
        }.items()
    ):
        raise StudioError(422, "DOCUMENT_VISUAL_MISMATCH", "One edit visual must match the exact submitted document page revision.")
    visuals = []
    total_bytes = 0
    for row in inputs:
        document = _document_page_source(binding, row["runId"], row["assetSha256"], row["pageIndex"], row.get("drawingRevisionRef"))
        page = None
        if row.get("revisionSha256") is not None:
            page = read_document_annotations(binding, row["runId"], row["assetSha256"], row["pageIndex"], row["revisionSha256"], row.get("drawingRevisionRef"))
        has_ink = page is not None and bool(page.annotations)
        if has_ink != (row.get("annotatedPngBase64") is not None):
            raise StudioError(422, "DOCUMENT_VISUAL_MISMATCH", "The selected saved revision needs its complete annotation overlay; pages without selected ink must omit it.")
        png, size = _document_visual_png(row["pagePngBase64"])
        visible = document.pages[row["pageIndex"]]
        scale = min(size[0] / visible.width, size[1] / visible.height)
        if abs(size[0] - visible.width * scale) > 1.01 or abs(size[1] - visible.height * scale) > 1.01:
            raise StudioError(422, "DOCUMENT_VISUAL_MISMATCH", "The image must preserve the registered visible page's aspect ratio.")
        overlay = None
        if has_ink:
            overlay, overlay_size = _document_visual_png(row["annotatedPngBase64"])
            if overlay_size != size:
                raise StudioError(422, "DOCUMENT_VISUAL_MISMATCH", "The page and annotated image must have identical pixel dimensions.")
        total_bytes += len(png) + (len(overlay) if overlay is not None else 0)
        if total_bytes > 16 * 1024 * 1024:
            raise StudioError(422, "DOCUMENT_VISUAL_INVALID", "Document visuals exceed the 16 MiB total decoded PNG limit.")
        summary = []
        for annotation in page.annotations if page else ():
            xs, ys = zip(*annotation.points)
            summary.append({"kind": annotation.kind, "label": annotation.label, "color": annotation.color,
                            "bounds": [min(xs), min(ys), max(xs), max(ys)], "pointCount": len(annotation.points)})
        visuals.append(DocumentVisual(context={
            "role": row["role"], "runId": row["runId"], "assetSha256": row["assetSha256"],
            "pageIndex": row["pageIndex"], "revisionSha256": row.get("revisionSha256"),
            **({"drawingRevisionRef": row["drawingRevisionRef"]} if row.get("drawingRevisionRef") is not None else {}),
            "fileName": document.file_name, "referenceNote": row.get("referenceNote"),
            "annotationSummary": summary,
        }, page_png=png, annotated_png=overlay))
    return tuple(visuals)


def retain_document_comment(
    binding: ProjectBinding, references: Sequence[DocumentAnnotationRef], *,
    utterance: str, state_digest: str, source_run_id: str | None,
    document_sources: Sequence[Mapping] = (),
    document_visuals: Sequence[DocumentVisual] = (),
) -> tuple[tuple[str, ...], ProjectRecordRef | None]:
    """Keep the submitted words and exact page revisions before an agent answers."""

    if not references:
        return (), None
    facts = []
    for reference in references:
        document = _document_page_source(binding, reference.run_id, reference.asset_sha256, reference.page_index, reference.drawing_revision_ref)
        page = read_document_annotations(
            binding, reference.run_id, reference.asset_sha256, reference.page_index, reference.revision_sha256, reference.drawing_revision_ref,
        )
        facts.extend(_document_facts(document, page))
    run = binding.load_run(source_run_id or references[0].run_id)
    ref = binding.repository.put_json(
        run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
        record_kind=STUDIO_DOCUMENT_COMMENT,
        payload={
            "schema": "StudioDocumentComment@1", "projectId": binding.project_id,
            "sourceRunId": source_run_id, "stateDigest": state_digest, "utterance": utterance,
            "documentAnnotations": [reference.to_dict() for reference in references],
            **({"documentSources": list(document_sources)} if document_sources else {}),
            **({"documentVisuals": [{**visual.context,
                "pagePngSha256": hashlib.sha256(visual.page_png).hexdigest(),
                "annotatedPngSha256": hashlib.sha256(visual.annotated_png).hexdigest() if visual.annotated_png is not None else None,
            } for visual in document_visuals]} if document_visuals else {}),
            "submittedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    return tuple(facts), ref


def require_document_model_sources(
    binding: ProjectBinding, references: Sequence[DocumentAnnotationRef], projection: StateProjection,
    model_source: ModelSource | None = None,
    *, pinned_sources: Sequence[Mapping] | None = None,
) -> tuple[Mapping, ...]:
    """Check declared correspondence before any model call; never infer it from storage."""

    if pinned_sources is not None and len(pinned_sources) != len(references):
        raise StudioError(409, "DOCUMENT_MODEL_SOURCE_MISMATCH", "The submitted request has incomplete source bindings.")
    sources = []
    for index, reference in enumerate(references):
        binding_ref = None if pinned_sources is None else pinned_sources[index].get("bindingRef")
        if pinned_sources is not None and not binding_ref:
            raise StudioError(409, "DOCUMENT_MODEL_SOURCE_UNKNOWN", "The submitted request has no exact model association.")
        document = _document_page_source(binding, reference.run_id, reference.asset_sha256, reference.page_index,
                                         reference.drawing_revision_ref, binding_ref=binding_ref)
        read_document_annotations(binding, reference.run_id, reference.asset_sha256, reference.page_index,
                                   reference.revision_sha256, reference.drawing_revision_ref, binding_ref=binding_ref)
        source = document.model_source
        if source is None:
            raise StudioError(409, "DOCUMENT_MODEL_SOURCE_UNKNOWN", "This drawing has no declared model source. Its annotations are saved; associate it with the model it describes before requesting a change.")
        if (source.run_id, source.state_digest) != (projection.run.run_id, projection.state_digest) or (model_source is not None and source != model_source):
            raise StudioError(409, "DOCUMENT_MODEL_SOURCE_MISMATCH", "The drawing describes a different model from the editing base. Its annotations remain saved; continue from the corresponding model.")
        require_model_source(binding, source, projection)
        if model_source is None:
            model_source = source
        sources.append({"runId": reference.run_id, "assetSha256": reference.asset_sha256,
                        **({"drawingRevisionRef": reference.drawing_revision_ref} if reference.drawing_revision_ref is not None else {}),
                        "modelSource": source.to_dict(), "bindingRef": document.model_source_binding_ref})
    return tuple(sources)


def require_document_comment_source(binding: ProjectBinding, comment: Mapping, projection: StateProjection) -> None:
    """An old comment cannot acquire a source association added after it was submitted."""

    pinned = comment.get("documentSources")
    if not pinned:
        raise StudioError(409, "DOCUMENT_MODEL_SOURCE_UNKNOWN", "This earlier request did not retain a model association. Submit a new request using the drawing's declared source.")
    if (comment.get("sourceRunId"), comment.get("stateDigest")) != (projection.run.run_id, projection.state_digest):
        raise StudioError(409, "DOCUMENT_MODEL_SOURCE_MISMATCH", "The submitted drawing request belongs to another editing base.")
    references = tuple(DocumentAnnotationRef(row["runId"], row["assetSha256"], row["pageIndex"], row["revisionSha256"],
                                             row.get("drawingRevisionRef")) for row in comment["documentAnnotations"])
    current = require_document_model_sources(binding, references, projection, pinned_sources=pinned)
    if list(current) != pinned:
        raise StudioError(409, "DOCUMENT_MODEL_SOURCE_MISMATCH", "The submitted request does not retain the drawing's exact declared model association.")


def list_document_comments(binding: ProjectBinding, run_id: str) -> tuple[tuple[ProjectRecordRef, Mapping], ...]:
    """Comments about documents in this storage run, plus legacy run-local copies."""

    binding.load_run(run_id)
    comments = {}
    for owner_run in binding.run_ids():
        for ref in binding.record_refs(owner_run):
            if record_kind(ref) != STUDIO_DOCUMENT_COMMENT:
                continue
            payload = binding.repository.load_json(ref)
            if payload.get("schema") != "StudioDocumentComment@1" or payload.get("projectId") != binding.project_id:
                raise StudioError(409, "ANNOTATION_BINDING_MISMATCH", "The retained document comment has a different project binding.")
            if owner_run != run_id and not any(row["runId"] == run_id for row in payload["documentAnnotations"]):
                continue
            if ref.sha256 not in comments or owner_run == payload.get("sourceRunId"):
                comments[ref.sha256] = (ref, payload)
    return tuple(sorted(comments.values(), key=lambda item: (item[1]["submittedAt"], item[0].sha256)))


@dataclass(frozen=True, slots=True)
class _Resolved:
    object_name: str | None
    status: str
    component_id: str | None
    element_id: str | None


def read_gestures(
    projection: StateProjection, gestures: Sequence[Gesture]
) -> GestureReading:
    facts: list[str] = []
    keep_refs: list[str] = []
    target: Selection | None = None
    for gesture in gestures:
        resolved = [_resolve(projection, hit) for hit in gesture.hits]
        if gesture.kind == CIRCLE:
            fact, implied = _circle(resolved)
            facts.append(fact)
            if target is None and implied is not None:
                target = implied
        elif gesture.kind == ARROW:
            facts.append(_arrow(resolved, gesture))
        elif gesture.kind == KEEP:
            fact, refs = _keep(resolved)
            facts.append(fact)
            keep_refs.extend(ref for ref in refs if ref not in keep_refs)
        elif gesture.kind == REMOVE:
            facts.append(_remove(resolved))
        elif gesture.kind in (FREEHAND, LINE, ARC):
            facts.append(_annotation(gesture.kind, resolved, gesture))
        elif gesture.kind == RULER:
            facts.append(_ruler(resolved, gesture))
        facts.extend(_unresolved(gesture.kind, resolved))
    return GestureReading(
        facts=tuple(facts), keep_refs=tuple(keep_refs), target=target
    )


def direction_words(direction: Vector | None) -> str:
    """A unit vector as the architect would say it: the dominant world axis.

    The export is Z-up (``archflow:up_axis``), so +Z is up and -Z is down; X
    and Y are named as axes because "left" and "right" depend on where the
    camera stands, and the camera is the client's, not the record's.
    """

    if direction is None:
        return "direction unknown"
    x, y, z = direction
    magnitude = max(abs(x), abs(y), abs(z))
    if magnitude == 0:
        return "direction unknown"
    axis, value = max((("X", x), ("Y", y), ("Z", z)), key=lambda item: abs(item[1]))
    sign = "+" if value > 0 else "-"
    word = {"+Z": " (up)", "-Z": " (down)"}.get(sign + axis, "")
    return f"world direction {sign}{axis}{word} ({x:.2f}, {y:.2f}, {z:.2f})"


def _resolve(projection: StateProjection, hit: GestureHit) -> _Resolved:
    resolution = resolve_pick(
        projection,
        PickRequest(
            state_digest=projection.state_digest,
            user_strings=hit.user_strings,
            document_user_strings=None,
            object_name=hit.object_name,
        ),
    )
    return _Resolved(
        object_name=hit.object_name,
        status=resolution.status,
        component_id=resolution.component_id,
        element_id=resolution.element_id,
    )


def _named(resolved: Sequence[_Resolved]) -> list[_Resolved]:
    return [row for row in resolved if row.component_id is not None]


def _subject(row: _Resolved) -> str:
    if row.element_id is not None:
        return f"{row.element_id} ({row.component_id})"
    return f"component {row.component_id}"


def _subjects(resolved: Sequence[_Resolved]) -> list[str]:
    """Distinct subjects, most-hit first, ties in first-seen order."""

    counts: Counter[str] = Counter()
    order: list[str] = []
    for row in _named(resolved):
        subject = _subject(row)
        if subject not in counts:
            order.append(subject)
        counts[subject] += 1
    return sorted(order, key=lambda subject: (-counts[subject], order.index(subject)))


def _circle(resolved: Sequence[_Resolved]) -> tuple[str, Selection | None]:
    named = _named(resolved)
    if not named:
        return "circle over nothing the record names", None
    by_component: Counter[str] = Counter(row.component_id for row in named)  # type: ignore[misc]
    parts: list[str] = []
    for component_id, count in by_component.most_common():
        elements = sorted(
            {
                row.element_id
                for row in named
                if row.component_id == component_id and row.element_id is not None
            }
        )
        if elements:
            noun = "element" if len(elements) == 1 else "elements"
            parts.append(
                f"{component_id} ({len(elements)} {noun}: {', '.join(elements)})"
            )
        else:
            parts.append(f"{component_id} ({count} objects, no element rows)")
    top, _ = by_component.most_common(1)[0]
    top_elements = sorted(
        {
            row.element_id
            for row in named
            if row.component_id == top and row.element_id is not None
        }
    )
    selection = Selection(
        component_id=top,
        element_id=top_elements[0] if len(top_elements) == 1 else None,
    )
    return "circle covering " + " · ".join(parts), selection


def _arrow(resolved: Sequence[_Resolved], gesture: Gesture) -> str:
    subjects = _subjects(resolved)
    where = (
        "arrow on " + ", ".join(subjects)
        if subjects
        else "arrow over nothing the record names"
    )
    words = direction_words(gesture.world_direction)
    length = (
        f"length ≈ {gesture.length_model_units:.3g} model units"
        if gesture.length_model_units is not None
        else "length unknown"
    )
    return f"{where} · {words} · {length}"


def _keep(resolved: Sequence[_Resolved]) -> tuple[str, tuple[str, ...]]:
    named = _named(resolved)
    if not named:
        return "keep mark on nothing the record names", ()
    elements = sorted({row.element_id for row in named if row.element_id is not None})
    if elements:
        refs = tuple(f"entity:{element_id}" for element_id in elements)
        return (
            "keep mark on " + ", ".join(elements) + " → keep " + ", ".join(refs),
            refs,
        )
    components = sorted({row.component_id for row in named})  # type: ignore[arg-type]
    return (
        "keep mark on component "
        + ", ".join(components)
        + " · no element resolved, so the grammar cannot protect it",
        (),
    )


def _remove(resolved: Sequence[_Resolved]) -> str:
    subjects = _subjects(resolved)
    where = ", ".join(subjects) if subjects else "nothing the record names"
    return (
        f"remove mark on {where} · the grammar has no form that removes; "
        "ask before proposing"
    )


def _screen_shape(gesture: Gesture) -> str:
    points = " → ".join(f"({x:.0f},{y:.0f})" for x, y in gesture.screen)
    colour = f" · color {gesture.color}" if gesture.color else ""
    width = f" · {gesture.line_width}px" if gesture.line_width else ""
    view = (
        f" · recorded {gesture.screen_size[0]}×{gesture.screen_size[1]} view"
        if gesture.screen_size is not None else " · recorded view" if gesture.camera is not None else ""
    )
    return f" · screen {points}{colour}{width}{view}" if points else f"{colour}{width}{view}"


def _annotation(kind: str, resolved: Sequence[_Resolved], gesture: Gesture) -> str:
    subjects = _subjects(resolved)
    noun = {FREEHAND: "freehand mark", LINE: "line annotation", ARC: "arc annotation"}[kind]
    where = f"on {', '.join(subjects)}" if subjects else "over nothing the record names"
    return f"{noun} {where}{_screen_shape(gesture)}"


def _ruler(resolved: Sequence[_Resolved], gesture: Gesture) -> str:
    subjects = _subjects(resolved)
    where = ", ".join(subjects) if subjects else "nothing the record names"
    label = f" · label: {gesture.label}" if gesture.label else ""
    return f"ruler annotation on {where}{label}{_screen_shape(gesture)}"


# How many unresolved object names one fact lists before it counts the rest:
# a circle over a hundred untagged objects is one sentence, not a hundred.
UNRESOLVED_NAMES_SHOWN = 3


def _unresolved(kind: str, resolved: Sequence[_Resolved]) -> list[str]:
    """One fact per resolution status for the hits the record cannot name."""

    misses = [row for row in resolved if row.component_id is None]
    if not misses:
        return []
    by_status: dict[str, list[str]] = {}
    for row in misses:
        by_status.setdefault(row.status, []).append(
            row.object_name or "an unnamed object"
        )
    facts: list[str] = []
    for status, names in by_status.items():
        if len(names) == 1:
            facts.append(f"{kind} hit on {names[0]} did not resolve ({status})")
            continue
        shown = ", ".join(names[:UNRESOLVED_NAMES_SHOWN])
        rest = len(names) - UNRESOLVED_NAMES_SHOWN
        tail = f", +{rest} more" if rest > 0 else ""
        facts.append(
            f"{kind}: {len(names)} hits did not resolve ({status}) · {shown}{tail}"
        )
    return facts
