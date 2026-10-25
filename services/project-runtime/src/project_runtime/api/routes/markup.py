"""``POST /api/markup/resolve``: what each mark the architect drew lies on (#300 3-2)."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from ...binding import bound_project
from ...application import artifacts
from ...application.markup import MODEL_ZONE, PageMarks, hit_subjects_of, read_model_marks, read_page_marks
from ...application.model_parts import model_parts
from ...application.projection import project_state
from ...errors import StudioError
from ..dto.intent import document_gesture_from, gesture_from
from ..dto.markup import MarkupReadingsDto, MarkupResolveRequestDto, markup_readings_dto
from ..dto.proposal import ProposalDto
from .projections import ready_projections
from .proposals import _stale_base

router = APIRouter(tags=["markup"])


@router.post(
    "/markup/resolve",
    response_model=MarkupReadingsDto,
    response_model_by_alias=True,
)
def resolve(request: Request, body: MarkupResolveRequestDto) -> MarkupReadingsDto:
    """Read each mark into the components and elements it lies on, and say when one is unclear.

    Marks on a registered page are read against the page as it was drawn: a
    view drawing's or view sheet's object-named lines, or a camera pass's
    object-ID image, answered for by the record state the drawing was made
    from. Strokes on the model are read by the objects their samples fell on,
    answered for as a click is (``/api/pick/resolve``) against the state the
    model view was loaded with. Text is the sentence, not a mark. Nothing is
    written and no model is called; the same marks read the same every time.
    """

    binding = bound_project(request.app.state)
    if body.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The marks name another project.")
    if body.page is not None:
        page = body.page
        return markup_readings_dto(read_page_marks(
            binding, page.run_id, page.asset_sha256, page.page_index,
            tuple(document_gesture_from(mark) for mark in body.marks), revision_ref=page.drawing_revision_ref,
            parts=lambda sha: attached_parts(request, sha)))
    projection = project_state(binding, run_id=body.source_run_id)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the strokes", body.source_run_id)
    readings = read_model_marks([(stroke.id, gesture_from(stroke)) for stroke in body.strokes],
                                hit_subjects_of(projection))
    return markup_readings_dto(PageMarks(MODEL_ZONE, projection.state_digest, readings))


def attached_parts(request: Request, asset_sha256: str) -> dict:
    """What each object of a model attached from outside the project is (``model_parts``), kept per file."""

    binding = bound_project(request.app.state)
    _, data = artifacts.artifact_bytes(binding, asset_sha256, run_id=f"studio-model-{asset_sha256}")
    return model_parts(data, asset_sha256, projections=ready_projections(request.app.state),
                       compiler=request.app.state.intent_compiler)


@router.get("/model-assets/{asset_sha256}/parts")
def read_model_asset_parts(request: Request, asset_sha256: str = Path(pattern=r"^[0-9a-f]{64}$")) -> dict:
    """Each object of a model attached from outside the project (a Live Rhino snapshot), read as a building part.

    By its own name, its layer's name, then its shape; the groups still unread
    are named by the project's configured model, and are ``other`` without one.
    ``objects`` maps an object id to ``{part, name, by}``; ``parts`` counts
    each part with its labels; ``recognized`` is the share read as a part. The
    reading is kept by the file's bytes, so a model is asked about once.
    """

    return attached_parts(request, asset_sha256)


class AttachedModelDto(BaseModel):
    """One model attached from outside the project, as a design can be made from it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    asset_sha256: str = Field(alias="assetSha256")
    file_name: str = Field(alias="fileName")
    object_count: int | None = Field(alias="objectCount", default=None)
    attached_at: str = Field(alias="attachedAt", description="When its run was made, UTC ISO 8601.")


@router.get("/model-assets/attached", response_model=list[AttachedModelDto], response_model_by_alias=True)
def list_attached_models(request: Request) -> list[AttachedModelDto]:
    """The models attached from outside the project (Live Rhino snapshots, uploaded 3dm), the last attached first."""

    return _attached(bound_project(request.app.state))


def _attached(binding) -> list[AttachedModelDto]:
    """Each model file run's registration, newest run first."""

    import json
    from datetime import datetime, timezone

    layout = binding.repository.layout
    rows = []
    for run_id in binding.run_ids():
        if not run_id.startswith("studio-model-"):
            continue
        root = layout.run(run_id).root
        asset = next(iter(sorted((root / "records").glob("studio-model-asset-*.json"))), None)
        if asset is None:
            continue
        payload = json.loads(asset.read_bytes())
        if payload.get("representation") != "external":
            continue
        rows.append(((root / "run.json").stat().st_mtime, AttachedModelDto(
            asset_sha256=run_id.removeprefix("studio-model-"), file_name=str(payload.get("fileName") or ""),
            object_count=payload.get("objectCount"),
            attached_at=datetime.fromtimestamp((root / "run.json").stat().st_mtime, timezone.utc).isoformat())))
    return [row for _, row in sorted(rows, key=lambda item: -item[0])]


class DesignFromModelRequestDto(BaseModel):
    """Which attached model to make a design of; none names the one attached last."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    asset_sha256: str | None = Field(alias="assetSha256", default=None, pattern=r"^[0-9a-f]{64}$")


@router.post("/model-assets/design", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def propose_design_from_model_asset(request: Request, body: DesignFromModelRequestDto) -> ProposalDto:
    """The proposal that makes an attached model (a Live Rhino snapshot, an uploaded 3dm) a design that can be changed.

    Without ``assetSha256`` it is the model attached last (the newest file
    run), so one sentence or one command converts the snapshot just taken.

    One semantic edit at the file's own run, whose state is the project's
    authored start: each kind of part its objects are read as
    (``model_parts``) becomes a component under the root a seat builds, and
    each object, or each Rhino group of objects, an ``imported`` element
    naming the file and the objects (#300 item 22 B). Running it makes a
    candidate on a line of its own, whose composed model is the file's
    objects placed as the record says; the file and every other design stay
    as they are. Nothing is run here.
    """

    from monkeycad.formats.three_dm_compose import object_groups
    from monkeycad.formats.three_dm_inspector import inspect_three_dm_index

    from ...application.imported_design import conversion_edit
    from ...application.intent import component_edit_proposal
    from ...application.proposals import proposal_from
    from ...application.seats import load_seat_pack, seats_of
    from ..dto.proposal import to_dto

    binding = bound_project(request.app.state)
    asset_sha256 = body.asset_sha256 or _last_attached(binding)
    run_id = f"studio-model-{asset_sha256}"
    record, data = artifacts.artifact_bytes(binding, asset_sha256, run_id=run_id)
    if record.representation != "external":
        raise StudioError(409, "MODEL_ASSET_NOT_IMPORTED", "Only a model attached from outside the project is made a design.")
    projection = project_state(binding, run_id=run_id)
    entities = {entity.entity_id for entity in projection.record.entities}
    roots = [root for seat in seats_of(load_seat_pack(binding.repository)) if not seat.reviewer
             for root in seat.owned_component_ids if root in entities]
    if not roots:
        raise StudioError(409, "SEATS_NOT_FOUND", "No seat of this project builds a component a design could be made under.")
    groups = object_groups(data)
    index = inspect_three_dm_index(data, bounds_for=lambda ids: ids)
    edit = conversion_edit(
        index, attached_parts(request, asset_sha256), asset=asset_sha256, file_name=record.file_name,
        root_component_id=roots[0], source_ref=(projection.record.evidence_refs or (f"asset:{asset_sha256}",))[0],
        taken=entities, groups=groups)
    proposal = proposal_from(component_edit_proposal(projection, edit, utterance=edit["summary"],
                                                     component_id=None, keep_refs=()))
    proposal = replace(proposal, source_run_id=run_id, source_stage_ref=None, request=edit["summary"])
    return to_dto(request.app.state.proposals.put(proposal))


def _last_attached(binding) -> str:
    """The sha256 of the model attached last: the newest run holding a model file alone."""

    attached = _attached(binding)
    if not attached:
        raise StudioError(404, "MODEL_ASSET_NOT_FOUND", "No model has been attached to this project to make a design of.")
    return attached[0].asset_sha256
