"""The project's single board, retained as scene revisions through P036.

The scene holds arrangement and discussion. Images name registered document
pages; their bytes and any model association remain with the document owner.
Saving a board creates neither a design Stage nor a canonical transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import PurePath
from typing import Any, Literal, Mapping, Sequence
import threading
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageOps
# pypdf, PyMuPDF and python-pptx load where a document is read or exported: about 0.2 s of a worker's start otherwise (#449).
# PyMuPDF as ``pymupdf``: importing it as ``fitz`` prints a deprecation line on stdout, which an agent's output stream carries.

from archflow.contracts.canonical import CanonicalValueError, canonical_json_bytes
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
from archflow.project.repository import ProjectRepositoryError

from ..errors import StudioError
from .artifacts import SourceDocument, document_bytes
from ..binding import retained_sources
from ..binding import ProjectBinding, record_kind
from .projections import DOCUMENT_PAGE, ProjectionQueue, ProjectionSpec, on_demand_spec

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
    # The board's own run, asked for by name. Listing every run in the project
    # to discover whether this one exists makes an exact read cost the whole
    # project. Whether the directory is there is one bounded read-only
    # question about this one known run; a run that exists is then loaded, so
    # a damaged manifest still refuses instead of reading as an empty board.
    if not binding.repository.layout.run(BOARD_RUN_ID).root.is_dir():
        return {}
    binding.load_run(BOARD_RUN_ID)
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


def read_board(binding: ProjectBinding, revision_sha256: str | None = None) -> BoardScene:
    """The board as it stands, or one exact retained revision of it.

    A named revision is read out of the same validated chain the latest one
    comes from, so historic evidence cannot be answered from a record that
    belongs to another project or to a broken chain. Reading an older
    revision changes nothing: the board's latest revision stays where it is.
    """

    with _board_lock:
        revisions = _revisions(binding)
        latest = _latest(revisions)
        if revision_sha256 is None:
            return _scene(binding, latest, revisions.get(latest))
        payload = revisions.get(revision_sha256)
        if payload is None:
            raise StudioError(404, "BOARD_REVISION_NOT_FOUND",
                              "That board revision is not retained in this project.")
        return _scene(binding, revision_sha256, payload)


def _export_name(index: int, file_name: str, suffix: str) -> str:
    stem = PurePath(file_name).stem.strip() or "drawing"
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem).strip("-") or "drawing"
    return f"{index:03d}-{clean}.{suffix}"


def _page_pdf(data: bytes, mime_type: str, page_index: int) -> bytes:
    from pypdf import PdfReader, PdfWriter
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
    import pymupdf as fitz
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


def page_export_png(data: bytes, mime_type: str, page_index: int) -> bytes:
    """One page as the PNG a Board PNG export writes at its full size: a PDF page at 144 dpi over white.

    A view sheet's retained PNG is this export of its PDF, so both answer with
    the same pixels. Deterministic: the same bytes give the same PNG.
    """

    return _page_raster(data, mime_type, page_index, "png", None)


#: The longest edge of a page raster, in pixels.
PAGE_RASTER_EDGE = 2048


@dataclass(frozen=True, slots=True)
class PageRaster:
    png: bytes
    width: int
    height: int


def _board_size(size: tuple[float, float], scale: float) -> tuple[int, int]:
    """``size`` scaled and rounded as Board's own page renderer sized it (JavaScript's ``Math.round``)."""

    return tuple(max(1, min(PAGE_RASTER_EDGE, math.floor(side * scale + 0.5))) for side in size)


def page_raster(data: bytes, mime_type: str, page_index: int) -> PageRaster:
    """One page of a retained document as the PNG every surface previews: at most 2048 px, transparency kept.

    A PDF page is drawn by MuPDF, its CropBox and /Rotate applied, over
    transparent pixels, exactly 2048 px on its long edge and the short edge
    rounded as Board always drew it: a saved Board crop is in these pixels
    (Excalidraw keeps ``crop`` in source pixels), so the size must not move.
    A PNG within the bound and without an EXIF orientation is its own raster,
    byte for byte; any other image is oriented and reduced the same way, never enlarged.
    Deterministic: the same bytes give the same PNG.
    """
    import pymupdf as fitz

    if mime_type == "application/pdf":
        document = fitz.open(stream=data, filetype="pdf")
        try:
            page = document.load_page(page_index)
            width, height = page.rect.width, page.rect.height
            size = _board_size((width, height), PAGE_RASTER_EDGE / max(width, height))
            # MuPDF pixels use premultiplied alpha; PNG encoding unpremultiplies them.
            pixmap = page.get_pixmap(matrix=fitz.Matrix(size[0] / width, size[1] / height), alpha=True)
            return PageRaster(pixmap.tobytes("png"), pixmap.width, pixmap.height)
        finally:
            document.close()
    with Image.open(BytesIO(data)) as source:
        if source.format == "PNG" and source.getexif().get(0x0112, 1) == 1 and max(source.size) <= PAGE_RASTER_EDGE:
            return PageRaster(data, *source.size)
        image = ImageOps.exif_transpose(source)
        if image.mode not in {"1", "L", "LA", "RGB", "RGBA"}:
            image = image.convert("RGBA")
        scale = min(1, PAGE_RASTER_EDGE / max(image.size))
        if scale < 1:
            image = image.resize(_board_size(image.size, scale), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="PNG")
        return PageRaster(output.getvalue(), *image.size)


def page_spec(document: SourceDocument, page_index: int) -> ProjectionSpec:
    """The projection key of one document page's raster: the document's own bytes, the page and the bound."""

    return on_demand_spec(DOCUMENT_PAGE, {
        "runId": document.run_id, "assetSha256": document.asset_sha256, "revisionRef": document.revision_ref,
        "pageIndex": page_index,
    }, {"page": page_index, "maxEdge": PAGE_RASTER_EDGE, "mimeType": document.mime_type})


def cached_page(projections: ProjectionQueue | None, document: SourceDocument, data: bytes, page_index: int) -> PageRaster:
    """A retained document page's raster: from the projection cache when one answers, else drawn (and kept there).

    ``document`` and ``data`` come from ``document_bytes``, which verified them
    against P036; the raster only supplies pixels, the document stays the
    source. Without a cache, or with one that fails, the page is drawn as
    before; its size is read from the PNG itself.
    """

    if not 0 <= page_index < len(document.pages):
        raise StudioError(422, "DOCUMENT_PAGE_NOT_FOUND", "The requested page no longer exists in its registered source.")
    if projections is None:
        return page_raster(data, document.mime_type, page_index)
    files, _, _ = projections.on_demand(
        page_spec(document, page_index), lambda: ({"png": page_raster(data, document.mime_type, page_index).png}, {}))
    with Image.open(BytesIO(files["png"])) as image:
        return PageRaster(files["png"], *image.size)


def export_board_pages(
    binding: ProjectBinding,
    pages: Sequence[BoardExportPage],
    format: Literal["merged-pdf", "page-pdfs", "png", "jpeg"],
    zip_output: bool,
    max_edge: int | None = None,
) -> BoardExport:
    """Build transient clean-source output in caller order without retaining a new project artifact."""
    from pypdf import PdfReader, PdfWriter

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


# ---- One region of the board, as it is drawn, for an agent to read (gap 28) ----
#
# A page read (export_board_pages) gives a registered page alone. What a
# person drew over it on the Board — strokes, shapes, text, a tracing-paper
# snapshot — lives only in the scene. view_board draws a frame or a region of
# the scene with its pages under those marks, and lists every mark with where
# it sits on which page. It approximates Excalidraw's own drawing (no
# hand-drawn roughness, plain fonts) and writes nothing.

BOARD_VIEW_EDGE = 2048
BOARD_VIEW_MAX_BYTES = 4 * 1024 * 1024
MAX_VIEW_ANNOTATIONS = 2000
_VIEW_FRAME_TYPES = frozenset({"frame", "magicframe"})
_VIEW_LINEAR_TYPES = frozenset({"freedraw", "line", "arrow"})
TRACING_PAPER_REVIEW = "tracing-paper-review"


@dataclass(frozen=True, slots=True)
class BoardView:
    revision_sha256: str | None
    region: tuple[float, float, float, float]
    frame: Mapping[str, Any] | None
    frames: tuple[Mapping[str, Any], ...]
    scale: float
    png: bytes
    width: int
    height: int
    pages: tuple[Mapping[str, Any], ...]
    annotations: tuple[Mapping[str, Any], ...]
    annotation_count: int


def _view_number(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default


def _view_points(element: Mapping[str, Any]) -> list[tuple[float, float]]:
    """A linear element's points in scene coordinates, its rotation about its own centre applied."""

    x, y = _view_number(element.get("x")), _view_number(element.get("y"))
    raw = element.get("points") if isinstance(element.get("points"), list) else []
    points = [(x + _view_number(row[0]), y + _view_number(row[1])) for row in raw
              if isinstance(row, (list, tuple)) and len(row) >= 2]
    angle = _view_number(element.get("angle"))
    if not angle or not points:
        return points
    cx = x + _view_number(element.get("width")) / 2
    cy = y + _view_number(element.get("height")) / 2
    cos, sin = math.cos(angle), math.sin(angle)
    return [(cx + (px - cx) * cos - (py - cy) * sin, cy + (px - cx) * sin + (py - cy) * cos) for px, py in points]


def _view_corners(element: Mapping[str, Any]) -> list[tuple[float, float]]:
    x, y = _view_number(element.get("x")), _view_number(element.get("y"))
    w, h = _view_number(element.get("width")), _view_number(element.get("height"))
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    angle = _view_number(element.get("angle"))
    if not angle:
        return corners
    cx, cy = x + w / 2, y + h / 2
    cos, sin = math.cos(angle), math.sin(angle)
    return [(cx + (px - cx) * cos - (py - cy) * sin, cy + (px - cx) * sin + (py - cy) * cos) for px, py in corners]


def _view_bounds(element: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """The axis-aligned box an element covers in scene coordinates, rotation included."""

    points = _view_points(element) if element.get("type") in _VIEW_LINEAR_TYPES else []
    points = points or _view_corners(element)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _view_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None


def _view_rgb(value: Any) -> tuple[int, int, int] | None:
    from PIL import ImageColor

    if not isinstance(value, str) or not value or value == "transparent":
        return None
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError:
        return (30, 30, 30)
    return rgb[:3]


def _view_font(size: int):
    import os
    from pathlib import Path
    from PIL import ImageFont

    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for candidate in (windows / "msyh.ttc", windows / "arial.ttf",
                      Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
                      Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _view_box(box: tuple[float, float, float, float]) -> dict:
    return {"x": round(box[0], 2), "y": round(box[1], 2), "width": round(box[2] - box[0], 2),
            "height": round(box[3] - box[1], 2)}


def _view_time(element: Mapping[str, Any]) -> str | None:
    from datetime import datetime, timezone

    updated = element.get("updated")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool) or not 0 < updated < 1e14:
        return None
    return datetime.fromtimestamp(updated / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _view_page_image(raster: PageRaster, element: Mapping[str, Any], canvas_box: tuple[int, int, int, int],
                     origin: tuple[float, float], scale: float):
    """The part of one page raster an unrotated image element shows inside the canvas, at canvas scale."""

    x, y = _view_number(element.get("x")), _view_number(element.get("y"))
    w, h = _view_number(element.get("width")), _view_number(element.get("height"))
    with Image.open(BytesIO(raster.png)) as opened:
        source = opened.convert("RGBA")
    crop = element.get("crop")
    if isinstance(crop, Mapping):
        natural_w = _view_number(crop.get("naturalWidth"), source.width) or source.width
        natural_h = _view_number(crop.get("naturalHeight"), source.height) or source.height
        kx, ky = source.width / natural_w, source.height / natural_h
        cx0, cy0 = _view_number(crop.get("x")) * kx, _view_number(crop.get("y")) * ky
        cw, ch = _view_number(crop.get("width"), natural_w) * kx, _view_number(crop.get("height"), natural_h) * ky
        source = source.crop((round(cx0), round(cy0), round(cx0 + cw), round(cy0 + ch)))
    flip = element.get("scale") if isinstance(element.get("scale"), list) and len(element["scale"]) == 2 else [1, 1]
    if _view_number(flip[0], 1) < 0:
        source = ImageOps.mirror(source)
    if _view_number(flip[1], 1) < 0:
        source = ImageOps.flip(source)
    dest = ((x - origin[0]) * scale, (y - origin[1]) * scale, (x + w - origin[0]) * scale, (y + h - origin[1]) * scale)
    visible = _view_overlap(dest, canvas_box)
    if visible is None or dest[2] - dest[0] <= 0 or dest[3] - dest[1] <= 0:
        return None
    # Only the visible part is resized: a small region of a large page stays small.
    sx, sy = source.width / (dest[2] - dest[0]), source.height / (dest[3] - dest[1])
    part = source.crop((round((visible[0] - dest[0]) * sx), round((visible[1] - dest[1]) * sy),
                        max(round((visible[0] - dest[0]) * sx) + 1, round((visible[2] - dest[0]) * sx)),
                        max(round((visible[1] - dest[1]) * sy) + 1, round((visible[3] - dest[1]) * sy))))
    size = (max(1, round(visible[2] - visible[0])), max(1, round(visible[3] - visible[1])))
    return part.resize(size, Image.Resampling.LANCZOS), (round(visible[0]), round(visible[1]))


def _view_draw(canvas, element: Mapping[str, Any], origin: tuple[float, float], scale: float) -> None:
    """One mark drawn onto ``canvas`` (RGBA) at its opacity."""
    from PIL import ImageDraw

    kind = element.get("type")
    stroke = _view_rgb(element.get("strokeColor")) or (30, 30, 30)
    fill = _view_rgb(element.get("backgroundColor"))
    alpha = max(0, min(255, round(_view_number(element.get("opacity"), 100) * 2.55)))
    width = max(1, round(_view_number(element.get("strokeWidth"), 1) * scale * (1.5 if kind == "freedraw" else 1)))
    turned = kind == "text" and bool(_view_number(element.get("angle")))
    # Most marks are opaque: drawn straight onto the canvas. A translucent or
    # turned text mark is drawn on its own layer first.
    layer = canvas if alpha == 255 and not turned else Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def at(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] - origin[0]) * scale, (point[1] - origin[1]) * scale

    if kind in _VIEW_LINEAR_TYPES:
        points = [at(point) for point in _view_points(element)]
        if len(points) == 1:
            r = width / 2
            draw.ellipse((points[0][0] - r, points[0][1] - r, points[0][0] + r, points[0][1] + r), fill=stroke)
        elif points:
            if kind != "freedraw" and fill is not None and len(points) > 2 and points[0] == points[-1]:
                draw.polygon(points, fill=fill)
            draw.line(points, fill=stroke, width=width, joint="curve")
            if kind == "arrow":
                for head, tip, back in (("endArrowhead", points[-1], points[-2]), ("startArrowhead", points[0], points[1])):
                    if element.get(head):
                        heading = math.atan2(tip[1] - back[1], tip[0] - back[0])
                        length = max(6.0, 12 * scale)
                        for turn in (math.pi * 5 / 6, -math.pi * 5 / 6):
                            draw.line([tip, (tip[0] + length * math.cos(heading + turn),
                                             tip[1] + length * math.sin(heading + turn))], fill=stroke, width=width)
    elif kind == "text":
        text = element.get("text") if isinstance(element.get("text"), str) else ""
        size = max(6, round(_view_number(element.get("fontSize"), 20) * scale))
        font = _view_font(size)
        line_height = _view_number(element.get("lineHeight"), 1.25) * size
        x, y = at((_view_number(element.get("x")), _view_number(element.get("y"))))
        box_width = _view_number(element.get("width")) * scale
        align = element.get("textAlign")
        for index, line in enumerate(text.split("\n")):
            line_width = draw.textlength(line, font=font)
            offset = (box_width - line_width) / 2 if align == "center" else box_width - line_width if align == "right" else 0
            draw.text((x + offset, y + index * line_height), line, fill=stroke, font=font)
        angle = _view_number(element.get("angle"))
        if angle:
            cx, cy = at((_view_number(element.get("x")) + _view_number(element.get("width")) / 2,
                         _view_number(element.get("y")) + _view_number(element.get("height")) / 2))
            layer = layer.rotate(-math.degrees(angle), center=(cx, cy), resample=Image.Resampling.BICUBIC)
    elif kind == "ellipse":
        corners = _view_corners(element)
        cx = sum(p[0] for p in corners) / 4
        cy = sum(p[1] for p in corners) / 4
        rx, ry = _view_number(element.get("width")) / 2, _view_number(element.get("height")) / 2
        angle = _view_number(element.get("angle"))
        ring = [at((cx + rx * math.cos(t) * math.cos(angle) - ry * math.sin(t) * math.sin(angle),
                    cy + rx * math.cos(t) * math.sin(angle) + ry * math.sin(t) * math.cos(angle)))
                for t in (2 * math.pi * i / 64 for i in range(64))]
        draw.polygon(ring, fill=fill, outline=stroke, width=width)
    elif kind == "diamond":
        corners = _view_corners(element)
        ring = [at(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) for a, b in zip(corners, corners[1:] + corners[:1])]
        draw.polygon(ring, fill=fill, outline=stroke, width=width)
    else:
        # A rectangle, and the outline of any kind this reader does not draw.
        ring = [at(point) for point in _view_corners(element)]
        draw.polygon(ring, fill=fill if kind == "rectangle" else None,
                     outline=stroke if kind == "rectangle" else (150, 150, 150), width=width)
    if layer is canvas:
        return
    if alpha < 255:
        layer.putalpha(layer.getchannel("A").point(lambda value: value * alpha // 255))
    canvas.alpha_composite(layer)


def _view_png(canvas) -> tuple[bytes, int, int]:
    """The canvas as an RGB PNG of at most 4 MiB, reduced until it fits."""

    image = canvas.convert("RGB")
    for _ in range(4):
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        data = output.getvalue()
        if len(data) <= BOARD_VIEW_MAX_BYTES:
            return data, image.width, image.height
        factor = math.sqrt(BOARD_VIEW_MAX_BYTES / len(data)) * 0.9
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))),
                             Image.Resampling.LANCZOS)
    raise StudioError(413, "BOARD_VIEW_TOO_LARGE", "This region does not fit a 4 MiB PNG; ask for a smaller region or maxEdge.")


def view_board(
    binding: ProjectBinding,
    projections: ProjectionQueue | None = None,
    *,
    frame_id: str | None = None,
    region: tuple[float, float, float, float] | None = None,
    max_edge: int = BOARD_VIEW_EDGE,
    revision_sha256: str | None = None,
) -> BoardView:
    """One frame or region of the board drawn with its pages and marks, and the marks listed (gap 28).

    A frame is named by its element id; a region is ``(x, y, width, height)``
    in scene coordinates; neither is the whole scene. Every element whose box
    meets the region is drawn in scene order; image elements are their exact
    registered pages (a tracing-paper snapshot is one), and every other kind
    is a mark: listed with its colour, last change, box and, for each page it
    lies over, where on that page in page fractions. Reads only.
    """

    if frame_id is not None and region is not None:
        raise _invalid("Name a frame or a region, not both.")
    if type(max_edge) is not int or not 64 <= max_edge <= BOARD_VIEW_EDGE:
        raise _invalid("maxEdge must be an integer from 64 to 2048.")
    scene = read_board(binding, revision_sha256)
    elements = [row for row in scene.elements if isinstance(row, Mapping) and not row.get("isDeleted")]
    frames = [row for row in elements if row.get("type") in _VIEW_FRAME_TYPES]
    frame_rows = tuple({"id": row.get("id"), "name": row.get("name") if isinstance(row.get("name"), str) else None,
                        "box": _view_box(_view_bounds(row))} for row in frames)
    frame = None
    if frame_id is not None:
        frame = next((row for row in frames if row.get("id") == frame_id), None)
        if frame is None:
            raise StudioError(404, "BOARD_FRAME_NOT_FOUND", "The board has no frame with this id; this answer's frames list them.")
        bounds = _view_bounds(frame)
    elif region is not None:
        if len(region) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in region):
            raise _invalid("A region is four finite numbers: x, y, width and height in scene coordinates.")
        x, y, w, h = (float(v) for v in region)
        if not (0 < w <= 1e6 and 0 < h <= 1e6):
            raise _invalid("A region's width and height must be positive and at most 1,000,000.")
        bounds = (x, y, x + w, y + h)
    else:
        boxes = [_view_bounds(row) for row in elements if row.get("type") not in _VIEW_FRAME_TYPES]
        if not boxes:
            raise StudioError(422, "BOARD_EMPTY", "The board has nothing on it to read.")
        bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    span_w, span_h = max(bounds[2] - bounds[0], 1e-6), max(bounds[3] - bounds[1], 1e-6)
    scale = max_edge / max(span_w, span_h)
    size = (max(1, round(span_w * scale)), max(1, round(span_h * scale)))
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    canvas_box = (0, 0, size[0], size[1])
    origin = (bounds[0], bounds[1])

    shown = [row for row in elements if row.get("type") not in _VIEW_FRAME_TYPES
             and _view_overlap(_view_bounds(row), bounds) is not None]
    documents: dict[tuple, tuple[SourceDocument, bytes]] = {}
    rasters: dict[tuple, PageRaster] = {}
    pages: list[dict] = []
    marks: list[tuple[Mapping[str, Any], tuple[float, float, float, float]]] = []
    for element in shown:
        box = _view_bounds(element)
        if element.get("type") != "image":
            marks.append((element, box))
            _view_draw(canvas, element, origin, scale)
            continue
        source = (element.get("customData") or {}).get("sourceDocument") if isinstance(element.get("customData"), Mapping) else None
        if not isinstance(source, Mapping):
            continue
        key = (source.get("runId"), source.get("assetSha256"), source.get("revisionRef"))
        if key not in documents:
            documents[key] = document_bytes(binding, *key)
        document, data = documents[key]
        page_index = source.get("pageIndex")
        raster_key = (*key, page_index)
        if raster_key not in rasters:
            rasters[raster_key] = cached_page(projections, document, data, page_index)
        tracing = isinstance(document.view_recipe, Mapping) and document.view_recipe.get("kind") == TRACING_PAPER_REVIEW
        pages.append({"elementId": element.get("id"), "frameId": element.get("frameId"), "fileName": document.file_name,
                      "tracingPaper": tracing, "box": _view_box(box),
                      "sourceDocument": {"runId": key[0], "assetSha256": key[1], "revisionRef": key[2], "pageIndex": page_index},
                      "_bounds": box})
        if _view_number(element.get("angle")):
            # A turned page is drawn as its outline: the marks over it still say where they fall.
            _view_draw(canvas, {**element, "type": "image-outline"}, origin, scale)
            continue
        placed = _view_page_image(rasters[raster_key], element, canvas_box, origin, scale)
        if placed is not None:
            picture, position = placed
            alpha = max(0, min(255, round(_view_number(element.get("opacity"), 100) * 2.55)))
            if alpha < 255:
                picture.putalpha(picture.getchannel("A").point(lambda value: value * alpha // 255))
            canvas.paste(picture, position, picture)

    annotations = []
    for element, box in marks[:MAX_VIEW_ANNOTATIONS]:
        on_pages = []
        area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
        for page in pages:
            shared = _view_overlap(box, page["_bounds"])
            if shared is None:
                continue
            px0, py0, px1, py1 = page["_bounds"]
            pw, ph = max(px1 - px0, 1e-9), max(py1 - py0, 1e-9)
            on_pages.append({
                "elementId": page["elementId"], "fileName": page["fileName"], "tracingPaper": page["tracingPaper"],
                "at": {"x0": round((shared[0] - px0) / pw, 4), "y0": round((shared[1] - py0) / ph, 4),
                       "x1": round((shared[2] - px0) / pw, 4), "y1": round((shared[3] - py0) / ph, 4)},
                "overlap": round(min(1.0, (shared[2] - shared[0]) * (shared[3] - shared[1]) / area), 4)
                if box[2] > box[0] and box[3] > box[1] else 1.0,
            })
        row = {"id": element.get("id"), "type": element.get("type"), "strokeColor": element.get("strokeColor"),
               "backgroundColor": element.get("backgroundColor"), "strokeWidth": element.get("strokeWidth"),
               "opacity": element.get("opacity"), "box": _view_box(box), "updatedAt": _view_time(element),
               "frameId": element.get("frameId"), "groupIds": list(element.get("groupIds") or []), "onPages": on_pages}
        if element.get("type") == "text":
            row["text"] = (element.get("text") or "")[:4000]
        if element.get("type") in _VIEW_LINEAR_TYPES:
            row["pointCount"] = len(element.get("points") or [])
        annotations.append(row)
    png, width, height = _view_png(canvas)
    return BoardView(
        revision_sha256=scene.revision_sha256, region=(bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]),
        frame=None if frame is None else {"id": frame.get("id"), "name": frame.get("name") if isinstance(frame.get("name"), str) else None},
        frames=frame_rows, scale=width / span_w, png=png, width=width, height=height,
        pages=tuple({key: value for key, value in page.items() if key != "_bounds"} for page in pages),
        annotations=tuple(annotations), annotation_count=len(marks),
    )
