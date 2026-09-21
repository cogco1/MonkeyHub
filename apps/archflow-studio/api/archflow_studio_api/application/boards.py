"""The project's single board, retained as scene revisions through P036.

The scene holds arrangement and discussion. Images name registered document
pages; their bytes and any model association remain with the document owner.
Saving a board creates neither a design Stage nor a canonical transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Any, Literal, Mapping, Sequence
import threading
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter

from archflow.contracts.canonical import CanonicalValueError, canonical_json_bytes
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError
from .artifacts import document_bytes
from .binding import retained_sources
from .binding import ProjectBinding, record_kind

BOARD_RUN_ID = "studio-board"
MAX_BOARD_BYTES = 8 * 1024 * 1024
MAX_BOARD_ELEMENTS = 10_000
MAX_SEEN_DOCUMENTS = 10_000

# Reads and check-and-save share this single Studio process lock so readers
# cannot observe a partially created run. Cold reads use only P036 records.
_board_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class BoardScene:
    project_id: str
    title: str = "MonkeyBoard"
    elements: tuple[Mapping[str, Any], ...] = ()
    seen_documents: tuple[str, ...] = ()
    revision_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BoardExportPage:
    run_id: str
    asset_sha256: str
    revision_ref: str | None
    page_index: int


@dataclass(frozen=True, slots=True)
class BoardExport:
    file_name: str
    media_type: str
    content: bytes


def _revisions(binding: ProjectBinding) -> dict[str, Mapping]:
    if BOARD_RUN_ID not in binding.run_ids():
        return {}
    revisions = {}
    for ref in binding.record_refs(BOARD_RUN_ID):
        if record_kind(ref) != STUDIO_BOARD_SCENE:
            continue
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != "StudioBoardScene@1" or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "BOARD_BINDING_MISMATCH", "The saved board belongs to another project.")
        revisions[ref.sha256] = payload
    return revisions


def _latest(revisions: Mapping[str, Mapping]) -> str | None:
    parents = {payload.get("previousRevisionSha256") for payload in revisions.values()} - {None}
    tips = revisions.keys() - parents
    if (revisions and len(tips) != 1) or not parents.issubset(revisions):
        raise StudioError(409, "BOARD_CONFLICT", "The saved board has competing or incomplete revisions. Its content has been retained.")
    return next(iter(tips), None)


def _scene(binding: ProjectBinding, revision: str | None, payload: Mapping | None) -> BoardScene:
    if payload is None:
        return BoardScene(binding.project_id)
    return BoardScene(binding.project_id, payload["title"], tuple(payload["elements"]),
                      tuple(payload["seenDocuments"]), revision)


def read_board(binding: ProjectBinding) -> BoardScene:
    with _board_lock:
        revisions = _revisions(binding)
        latest = _latest(revisions)
        return _scene(binding, latest, revisions.get(latest))


def _export_name(index: int, file_name: str, suffix: str) -> str:
    stem = PurePath(file_name).stem.strip() or "drawing"
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem).strip("-") or "drawing"
    return f"{index:03d}-{clean}.{suffix}"


def _page_pdf(data: bytes, mime_type: str, page_index: int) -> bytes:
    writer = PdfWriter()
    if mime_type == "application/pdf":
        reader = PdfReader(BytesIO(data))
        writer.add_page(reader.pages[page_index])
    else:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            dpi = image.info.get("dpi", (72, 72))
            output = BytesIO()
            image.save(output, format="PDF", resolution=float(dpi[0]))
        reader = PdfReader(BytesIO(output.getvalue()))
        writer.add_page(reader.pages[0])
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def _page_raster(
    data: bytes, mime_type: str, page_index: int, format: Literal["png", "jpeg"], max_edge: int | None,
) -> bytes:
    if mime_type == "application/pdf":
        document = fitz.open(stream=data, filetype="pdf")
        try:
            # Keep 144 dpi exports; bounded previews scale before allocating pixels.
            page = document.load_page(page_index)
            scale = min(2, max_edge / max(page.rect.width, page.rect.height)) if max_edge else 2
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            document.close()
    else:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    if max_edge:
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    output = BytesIO()
    if format == "png":
        image.save(output, format="PNG", optimize=True)
    else:
        image.save(output, format="JPEG", quality=95, subsampling=0, optimize=True)
    return output.getvalue()


def export_board_pages(
    binding: ProjectBinding,
    pages: Sequence[BoardExportPage],
    format: Literal["merged-pdf", "page-pdfs", "png", "jpeg"],
    zip_output: bool,
    max_edge: int | None = None,
) -> BoardExport:
    """Build transient clean-source output in caller order without retaining a new project artifact."""

    if not pages:
        raise _invalid("Choose at least one drawing page to export.")
    if len(pages) > 100:
        raise _invalid("An export can contain at most 100 drawing pages.")
    if max_edge is not None and (type(max_edge) is not int or not 1 <= max_edge <= 2048 or format not in {"png", "jpeg"}):
        raise _invalid("maxEdge bounds PNG/JPEG exports only and must be an integer from 1 to 2048.")
    entries: list[tuple[str, bytes]] = []
    exported: set[tuple[str, str, str | None, int]] = set()
    for reference in pages:
        identity = (reference.run_id, reference.asset_sha256, reference.revision_ref, reference.page_index)
        if identity in exported:
            continue
        exported.add(identity)
        index = len(entries) + 1
        document, data = document_bytes(binding, reference.run_id, reference.asset_sha256, reference.revision_ref)
        if document.revision_ref != reference.revision_ref:
            raise _invalid("Generated drawings must name their exact registered revisionRef.")
        if type(reference.page_index) is not int or not 0 <= reference.page_index < len(document.pages):
            raise StudioError(422, "DOCUMENT_PAGE_NOT_FOUND", "The requested board page no longer exists in its registered source.")
        if format in {"merged-pdf", "page-pdfs"}:
            entries.append((_export_name(index, document.file_name, "pdf"), _page_pdf(data, document.mime_type, reference.page_index)))
        else:
            entries.append((_export_name(index, document.file_name, format), _page_raster(data, document.mime_type, reference.page_index, format, max_edge)))

    if format == "merged-pdf":
        writer = PdfWriter()
        for _, data in entries:
            writer.append(PdfReader(BytesIO(data)))
        output = BytesIO(); writer.write(output)
        merged = output.getvalue()
        if not zip_output:
            return BoardExport("monkeyboard-print.pdf", "application/pdf", merged)
        entries = [("monkeyboard-print.pdf", merged), *entries]

    if not zip_output and len(entries) == 1:
        name, data = entries[0]
        return BoardExport(name, "application/pdf" if format in {"merged-pdf", "page-pdfs"} else f"image/{format}", data)

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return BoardExport("monkeyboard-export.zip", "application/zip", output.getvalue())


def _invalid(message: str) -> StudioError:
    return StudioError(422, "BOARD_INVALID", message)


def _validate_scene(
    binding: ProjectBinding, title: str, elements: Sequence[Mapping[str, Any]], seen_documents: Sequence[str],
) -> dict:
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise _invalid("A board title needs 1 to 200 characters.")
    if len(elements) > MAX_BOARD_ELEMENTS or len(seen_documents) > MAX_SEEN_DOCUMENTS:
        raise StudioError(413, "BOARD_TOO_LARGE", "A board supports at most 10,000 elements and 10,000 received document ids.")
    content = {"title": title, "elements": list(elements), "seenDocuments": list(seen_documents)}
    try:
        size = len(canonical_json_bytes(content, ascii=False))
    except (CanonicalValueError, RecursionError) as exc:
        raise _invalid("The board must contain finite JSON values.") from exc
    if size > MAX_BOARD_BYTES:
        raise StudioError(413, "BOARD_TOO_LARGE", "The board scene may contain at most 8 MiB; upload image bytes as documents.")
    if any(not isinstance(value, str) or not value or len(value) > 2048 for value in seen_documents):
        raise _invalid("Received document ids must be non-empty strings of at most 2048 characters.")
    if len(set(seen_documents)) != len(seen_documents):
        raise _invalid("Received document ids must be distinct.")

    ids: set[str] = set()
    checked_documents: dict[tuple, int] = {}
    for element in elements:
        if not isinstance(element, Mapping):
            raise _invalid("Each board element must be a JSON object.")
        identifier = element.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256 or identifier in ids:
            raise _invalid("Board elements need distinct ids of at most 256 characters.")
        ids.add(identifier)
        if not isinstance(element.get("type"), str) or not element["type"]:
            raise _invalid("Each board element needs its serialized type.")
        # Excalidraw appState and BinaryFiles are transient client data. Scene
        # elements remain extensible without admitting pixel payloads or claims
        # that could be mistaken for a registered model source.
        pending = [element]
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                if value.keys() & {"files", "dataURL", "contentBase64", "appState", "modelSource", "sourceStageRef"}:
                    raise _invalid("Board elements store document references, not files, app state or client-declared model sources.")
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
        custom = element.get("customData")
        if custom is not None and not isinstance(custom, Mapping):
            raise _invalid("Element customData must be a JSON object.")
        source = custom.get("sourceDocument") if custom else None
        if element["type"] != "image":
            if source is not None:
                raise _invalid("A sourceDocument reference belongs to an image element.")
            continue
        if not isinstance(source, Mapping) or set(source) - {"runId", "assetSha256", "revisionRef", "pageIndex"}:
            raise _invalid("Each board image must name its registered source document and exact page.")
        run_id, sha, revision, page = (source.get(key) for key in ("runId", "assetSha256", "revisionRef", "pageIndex"))
        if (not isinstance(run_id, str) or not run_id or not isinstance(sha, str)
                or (revision is not None and not isinstance(revision, str)) or type(page) is not int):
            raise _invalid("A sourceDocument needs runId, assetSha256, integer pageIndex and optional revisionRef.")
        file_id = element.get("fileId")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 256:
            raise _invalid("Each image needs a client fileId of at most 256 characters.")
        key = (run_id, sha, revision)
        if key not in checked_documents:
            document, _ = document_bytes(binding, run_id, sha, revision)
            if document.revision_ref != revision:
                raise _invalid("Generated drawings must name their exact registered revisionRef.")
            checked_documents[key] = len(document.pages)
        if not 0 <= page < checked_documents[key]:
            raise StudioError(422, "DOCUMENT_PAGE_NOT_FOUND", "The page does not exist in this source document version.")
    return content


@retained_sources
def save_board(
    binding: ProjectBinding, base_revision_sha256: str | None, title: str,
    elements: Sequence[Mapping[str, Any]], seen_documents: Sequence[str],
) -> BoardScene:
    content = _validate_scene(binding, title, elements, seen_documents)
    with _board_lock:
        revisions = _revisions(binding)
        latest = _latest(revisions)
        if base_revision_sha256 != latest:
            raise StudioError(409, "BOARD_STALE", "The board has a newer saved revision. Read it before saving again.")
        previous = revisions.get(latest)
        if previous is not None and all(previous[key] == value for key, value in content.items()):
            return _scene(binding, latest, previous)
        payload = {"schema": "StudioBoardScene@1", "projectId": binding.project_id,
                   "previousRevisionSha256": latest, **content}
        try:
            run = (binding.load_run(BOARD_RUN_ID) if BOARD_RUN_ID in binding.run_ids()
                   else binding.repository.create_run(BOARD_RUN_ID))
            ref = binding.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=BOARD_RUN_ID),
                record_kind=STUDIO_BOARD_SCENE, payload=payload,
            )
        except (ProjectRepositoryError, OSError) as exc:
            raise StudioError(409, "BOARD_WRITE_FAILED", "The board could not be retained in its project.") from exc
        return _scene(binding, ref.sha256, payload)
