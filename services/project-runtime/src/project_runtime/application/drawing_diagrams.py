"""Diagrams drawn from data (gap 38): a network, a section schematic or a timeline from a spec, with no model.

MonkeyDiagram draws the spec on one sheet; it is registered as a sheet is, a PDF in the documents list with its SVG,
DXF and PNG retained beside it, but in the project's document run, since there is no model run to keep it in. A
drawing id names a diagram's revisions: a changed spec under the same id is a new document, the earlier one kept;
the same request again is the same document. It does not reach the Board by itself (``board: "on-request"``).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path
from typing import Any, Mapping

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from monkeydiagram import diagrams

from ..binding import ProjectBinding
from ..errors import StudioError
from .artifacts import DOCUMENT_UPLOAD_RUN_ID, SourceDocument, _document_source_lock, list_documents, save_document
from .drawings import DRAWING_KINDS, SHEET_FILES, _sheet_files, _sheet_fonts, refuse_other_kind

DIAGRAM_KIND = "diagram"


def _fonts(needs_cjk: bool) -> dict[str, Path]:
    """Fonts that draw the diagram's text: one with Chinese, Japanese and Korean when the text has them."""

    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for normal, bold in (
        (windows / "msyh.ttc", windows / "msyhbd.ttc"),
        (Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"), Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")),
    ):
        if normal.is_file() and bold.is_file():
            return {"normal": normal, "bold": bold}
    if needs_cjk:
        raise StudioError(503, "DRAWING_FONT_UNAVAILABLE", "This diagram has Chinese, Japanese or Korean text: install "
                          "Microsoft YaHei, WenQuanYi Micro Hei or Noto Sans CJK to draw it.")
    return _sheet_fonts()


def generate_diagram(binding: ProjectBinding, *, kind: str, spec: Mapping[str, Any], drawing_id: str,
                     style_id: str, title: str, subtitle: str = "") -> SourceDocument:
    """Draw and register one diagram; a request that repeats the last registered recipe returns that document."""

    try:
        checked = diagrams.check(kind, spec)
    except diagrams.DiagramError as exc:
        raise StudioError(422, "DIAGRAM_INVALID", str(exc)) from exc
    recipe: dict[str, Any] = {"kind": DIAGRAM_KIND, "diagramKind": kind, "styleId": style_id, "title": title,
                              "subtitle": subtitle, "board": "on-request", "spec": checked}
    recipe = _plain(recipe)
    recipe["specSha256"] = hashlib.sha256(canonical_json(recipe["spec"]).encode("utf-8")).hexdigest()
    for document in list_documents(binding, None):
        if document.drawing_id == drawing_id and _without(document.view_recipe, "warnings") == recipe:
            return document
    try:
        drawn = diagrams.draw(kind, spec, style_id=style_id, title=title, subtitle=subtitle,
                              font_mapping=_fonts(diagrams.needs_cjk_font(spec, title, subtitle)))
    except diagrams.DiagramError as exc:
        raise StudioError(422, "DIAGRAM_INVALID", str(exc)) from exc
    recipe["warnings"] = list(drawn.warnings)
    files = _sheet_files(drawn.canvas, canonical_json(recipe))
    digest = hashlib.sha256(files["pdf"]).hexdigest()
    with _document_source_lock:
        refuse_other_kind(binding, drawing_id, DIAGRAM_KIND, DRAWING_KINDS[DIAGRAM_KIND])
        run = (binding.load_run(DOCUMENT_UPLOAD_RUN_ID) if DOCUMENT_UPLOAD_RUN_ID in binding.run_ids()
               else binding.repository.create_run(DOCUMENT_UPLOAD_RUN_ID))
        destination = PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id)
        for role, media_type in SHEET_FILES:
            binding.repository.put_workspace_file(
                run=run, destination=destination, artifact_id=f"drawing-{'sheet' if role == 'pdf' else role}-{digest}",
                workspace_relative_path=f"documentation/{digest}/sheet.{role}", media_type=media_type,
                source=BytesIO(files[role]),
            )
        return save_document(binding, run.run_id, f"{drawing_id}.pdf", "application/pdf",
                             base64.b64encode(files["pdf"]).decode("ascii"), drawing_id=drawing_id, view_recipe=recipe,
                             generated_at=datetime.now(timezone.utc).isoformat())


def _plain(value: Any) -> Any:
    """Tuples as lists, as the recipe reads back from JSON."""

    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _without(recipe: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    return {k: v for k, v in (recipe or {}).items() if k != key}
