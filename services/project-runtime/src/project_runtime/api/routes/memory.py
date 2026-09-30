"""Save, read, revise and look up the bound project's memory (#252, ADR-009)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application import memory
from ...authentication import request_attribution
from ...binding import bound_project
from ...errors import StudioError
from ..dto.memory import (
    MemoryAboutDto,
    MemoryAboutRequestDto,
    MemoryDto,
    MemoryHistoryDto,
    MemoryKind,
    MemoryListDto,
    MemoryLocateDto,
    MemoryRequestDto,
    MemoryRevisionRequestDto,
    memory_dto,
    memory_match_dto,
)

router = APIRouter(tags=["memory"])


def _binding(request: Request, project_id: str):
    binding = bound_project(request.app.state)
    if project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The memory item names another project.")
    return binding


@router.get("/memory", response_model=MemoryListDto, response_model_by_alias=True)
def read_memory(request: Request, kind: MemoryKind | None = Query(default=None)) -> MemoryListDto:
    """Every memory item this project retains, at its current revision; one kind when asked."""

    binding = bound_project(request.app.state)
    return MemoryListDto(projectId=binding.project_id,
                         memory=[memory_dto(row) for row in memory.list_memory(binding, kind=kind)])


@router.get("/memory/locate", response_model=MemoryLocateDto, response_model_by_alias=True)
def locate(
    request: Request,
    q: str = Query(min_length=1, max_length=2000, description="the words asking where something is"),
    stage_ref: str | None = Query(alias="stageRef", default=None, min_length=1),
) -> MemoryLocateDto:
    """Where retained content is, by the user's words: each target re-read now, stale ones kept.

    The context read hands the same matches for a turn's utterance; this read
    needs no design source, so a project with only documents or a board can ask.
    """

    binding = bound_project(request.app.state)
    return MemoryLocateDto(projectId=binding.project_id, query=q,
                           locators=[memory_match_dto(row) for row in memory.locate(binding, q, stage_ref=stage_ref)])


@router.post("/memory/about", response_model=MemoryAboutDto, response_model_by_alias=True)
def about(request: Request, payload: MemoryAboutRequestDto) -> MemoryAboutDto:
    """The memory a turn's words are about, as ContextPack.memory selects it; it only reads.

    A turn with no design state has no context pack, yet memory is the
    project's: this hands the same selection from the words alone.
    """

    binding = _binding(request, payload.project_id)
    return MemoryAboutDto(projectId=binding.project_id, memory=[
        memory_match_dto(row)
        for row in memory.memory_for(binding, payload.utterance, stage_ref=payload.stage_ref, domain=payload.domain)])


@router.get("/memory/{memory_id}", response_model=MemoryHistoryDto, response_model_by_alias=True)
def read_memory_item(request: Request, memory_id: str) -> MemoryHistoryDto:
    """One memory item's whole chain: every revision as it was written."""

    binding = bound_project(request.app.state)
    chain = memory.memory_history(binding, memory_id)
    return MemoryHistoryDto(projectId=binding.project_id, memoryId=memory_id,
                            revisions=[memory_dto(row, status=None if row is chain[-1] else memory.SUPERSEDED)
                                       for row in chain])


@router.post("/memory", response_model=MemoryDto, response_model_by_alias=True, status_code=201)
def create_memory(request: Request, payload: MemoryRequestDto) -> MemoryDto:
    """Retain one memory item from the user's words; a locator's target must resolve now."""

    binding = _binding(request, payload.project_id)
    return memory_dto(memory.save_memory(binding, payload.model_dump(by_alias=True), request_attribution(request)))


@router.post("/memory/{memory_id}/revisions", response_model=MemoryDto,
             response_model_by_alias=True, status_code=201)
def revise_memory(request: Request, memory_id: str, payload: MemoryRevisionRequestDto) -> MemoryDto:
    """Revoke or supersede one memory item; its previous wording is kept as written."""

    binding = _binding(request, payload.project_id)
    return memory_dto(memory.revise_memory(
        binding, memory_id,
        expected_revision_ref=payload.expected_revision_ref,
        action=payload.action,
        reason=payload.reason,
        replacement=None if payload.replacement is None else payload.replacement.model_dump(by_alias=True),
        attribution=request_attribution(request),
        revision_message_source=(None if payload.revision_message_source is None
                                 else payload.revision_message_source.model_dump(by_alias=True)),
    ))
