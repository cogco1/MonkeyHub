"""Project-owned communication pages, independent of design and Board review.

CREATE: Board owns an infinite review canvas, documents own source bytes, and
Drawing owns projection. None owns ordered editable publication pages or their
format compilers. P036 remains the only writer; exports are transient bytes.
"""
from __future__ import annotations

from copy import deepcopy
import threading
from uuid import NAMESPACE_URL, uuid5

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_PUBLICATION
from ..transport.errors import StudioError
from .binding import ProjectBinding, record_kind, retained_sources
from . import artifacts, boards

RUN_ID = "studio-publication"
_lock = threading.RLock()
DEFAULT_SPEC = {"width": 960, "height": 540, "template": "hero"}


def _records(binding):
    if not binding.repository.layout.run(RUN_ID).root.is_dir():
        return {}
    binding.load_run(RUN_ID)
    result = {}
    for ref in binding.record_refs(RUN_ID):
        if record_kind(ref) != STUDIO_PUBLICATION:
            continue
        row = binding.repository.load_json(ref)
        if row.get("schema") != "PublicationDocument@1" or row.get("projectId") != binding.project_id:
            raise StudioError(409, "PUBLICATION_BINDING", "The publication belongs to another project.")
        result[ref.sha256] = row
    return result


def _latest(records):
    parents = {row["previousRevisionSha256"] for row in records.values()} - {None}
    tips = records.keys() - parents
    if (records and len(tips) != 1) or not parents.issubset(records):
        raise StudioError(409, "PUBLICATION_CONFLICT", "Publication revisions conflict. Existing pages are retained.")
    return next(iter(tips), None)


def source_statuses(binding, pages):
    from .drawing_plans import plan_status
    from .rendering import _freshness
    from ..transport.rendering import RenderRequestDto

    documents = artifacts.list_documents(binding)
    replacements = artifacts._page_replacements(documents)
    statuses = []
    for page in pages:
        for element in page["elements"]:
            source = element.get("source")
            if not source:
                continue
            key = (source["runId"], source["assetSha256"], source.get("revisionRef"), source["pageIndex"])
            replacement = replacements.get(key)
            visited = {key}
            while replacement and replacement in replacements:
                if replacement in visited:
                    raise StudioError(409, "PUBLICATION_SOURCE_CONFLICT", "Source replacements form a cycle.")
                visited.add(replacement)
                replacement = replacements[replacement]
            row = {"elementId": element["id"], "status": "frozen" if element.get("frozen") else "current", "detail": "", "replacement": None}
            try:
                document, _ = artifacts.document_bytes(binding, *key[:3])
                if document.revision_ref != key[2] or not 0 <= key[3] < len(document.pages):
                    raise StudioError(409, "PUBLICATION_SOURCE_MISSING", "The exact source page is unavailable.")
            except StudioError as exc:
                row.update(status="missing", detail=exc.detail)
            else:
                if replacement:
                    row["replacement"] = dict(zip(("runId", "assetSha256", "revisionRef", "pageIndex"), replacement))
                    if not element.get("frozen"):
                        row.update(status="stale", detail="A replacement page exists. Update explicitly or keep this source frozen.")
                elif not element.get("frozen"):
                    # Read domain-owned dependencies, not a guessed latest file.
                    # The retained old image remains usable if explicitly frozen;
                    # an unavailable current host is not missing historical bytes.
                    recipe = document.view_recipe or {}
                    try:
                        if recipe.get("kind") == "cut-plan":
                            target = {} if document.source_stage_ref else {"target_model_source": document.model_source}
                            status = plan_status(binding, run_id=document.run_id, asset_sha256=document.asset_sha256,
                                                 revision_ref=document.revision_ref, **target)
                            if status["status"] != "current":
                                row.update(status="stale", detail=status["detail"])
                        elif recipe.get("kind") == "ai-render":
                            request = RenderRequestDto.model_validate(recipe["request"])
                            if request.project_id != binding.project_id or recipe["jobId"] != document.run_id:
                                raise ValueError("Render source binding differs")
                            status, detail = _freshness(binding, request, recipe["sourceSnapshots"])
                            if status != "current":
                                row.update(status="stale", detail=detail)
                    except (StudioError, KeyError, TypeError, ValueError):
                        row.update(status="stale", detail="The current source dependencies cannot be verified. The retained image can be kept frozen.")
            statuses.append(row)
    return statuses


def read_publication(binding: ProjectBinding, revision: str | None = None):
    with _lock:
        records = _records(binding)
        latest = _latest(records)
        if revision and revision not in records:
            raise StudioError(404, "PUBLICATION_NOT_FOUND", "This publication revision is not retained in the project.")
        revision = revision or latest
        content = records.get(revision, {"title": "Untitled", "spec": DEFAULT_SPEC, "pages": []})
        return {"projectId": binding.project_id, "revisionSha256": revision, "title": content["title"],
                "spec": content["spec"], "pages": content["pages"], "sources": source_statuses(binding, content["pages"])}


@retained_sources
def save_publication(binding: ProjectBinding, base: str | None, title: str, spec: dict, pages: list):
    identifiers = [page["id"] for page in pages] + [item["id"] for page in pages for item in page["elements"]]
    if len(identifiers) != len(set(identifiers)):
        raise StudioError(422, "PUBLICATION_INVALID", "Page and element identifiers must be distinct.")
    for page in pages:
        for item in page["elements"]:
            if item["x"] + item["width"] > spec["width"] + 0.01 or item["y"] + item["height"] > spec["height"] + 0.01:
                raise StudioError(422, "PUBLICATION_BOUNDS", "Keep each element within the page.")
    with _lock:
        records = _records(binding)
        latest = _latest(records)
        if base != latest:
            raise StudioError(409, "PUBLICATION_STALE", "Another saved publication exists. Reload before saving; this draft has not replaced it.")
        # Preserve unavailable already-retained refs so a text edit can still be
        # saved. A new or substituted image must be resolved exactly first.
        previous = records.get(latest, {})
        old_sources = [item.get("source") for page in previous.get("pages", []) for item in page["elements"]]
        statuses = source_statuses(binding, pages)
        by_id = {row["elementId"]: row for row in statuses}
        for page in pages:
            for item in page["elements"]:
                if item.get("source") and item["source"] not in old_sources and by_id[item["id"]]["status"] == "missing":
                    raise StudioError(422, "PUBLICATION_SOURCE_MISSING", by_id[item["id"]]["detail"])
        content = {"title": title, "spec": spec, "pages": pages}
        if previous and all(previous[key] == value for key, value in content.items()):
            return read_publication(binding)
        run = binding.load_run(RUN_ID) if records else (binding.load_run(RUN_ID) if RUN_ID in binding.run_ids() else binding.repository.create_run(RUN_ID))
        ref = binding.repository.put_json(run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=RUN_ID), record_kind=STUDIO_PUBLICATION,
            payload={"schema": "PublicationDocument@1", "projectId": binding.project_id, "previousRevisionSha256": latest, **content})
        return read_publication(binding, ref.sha256)


def hero_page(identifier: str, source: dict, title: str, spec: dict):
    """One repeatable rule. Coordinates are computed once and then editable."""
    width, height = spec["width"], spec["height"]
    return {"id": identifier, "elements": [
        {"id": identifier + "-title", "kind": "text", "x": width * .05, "y": height * .05,
         "width": width * .9, "height": height * .13, "text": title, "fontSize": min(28, height * .1), "source": None, "frozen": False, "crop": [0, 0, 0, 0]},
        {"id": identifier + "-image", "kind": "image", "x": width * .05, "y": height * .21,
         "width": width * .9, "height": height * .74, "text": "", "fontSize": 24, "source": source, "frozen": False, "crop": [0, 0, 0, 0]},
    ]}


def append_board_selection(binding, base, board_revision, ids):
    board = boards.read_board(binding, board_revision)
    selected = set(ids)
    visible = [row for row in board.elements if not row.get("isDeleted")]
    if not selected.issubset({row["id"] for row in visible}):
        raise StudioError(409, "PUBLICATION_BOARD_SELECTION", "The selected Board objects are not in this saved revision.")
    rows = [row for row in visible if row["id"] in selected or row.get("frameId") in selected]
    rows = sorted(rows, key=lambda row: (row.get("y", 0), row.get("x", 0), row["id"]))
    current = read_publication(binding)
    if current["revisionSha256"] != base:
        raise StudioError(409, "PUBLICATION_STALE", "Reload the publication before appending pages.")
    pages = deepcopy(current["pages"])
    found = False
    for row in rows:
        source = row.get("customData", {}).get("sourceDocument")
        if row.get("type") != "image" or not source:
            continue
        found = True
        document, _ = artifacts.document_bytes(binding, source["runId"], source["assetSha256"], source.get("revisionRef"))
        # Retry of the same retained selection cannot append duplicate pages.
        identifier = "board-" + uuid5(NAMESPACE_URL, f"{board_revision}:{row['id']}").hex
        if any(page["id"] == identifier for page in pages):
            continue
        pages.append(hero_page(identifier, source, document.file_name, current["spec"]))
    if not found:
        raise StudioError(422, "PUBLICATION_BOARD_SELECTION", "Select at least one registered image or drawing page on Board.")
    if len(pages) > 60:
        raise StudioError(422, "PUBLICATION_LIMIT", "A publication may contain at most 60 pages.")
    return save_publication(binding, base, current["title"], current["spec"], pages)
