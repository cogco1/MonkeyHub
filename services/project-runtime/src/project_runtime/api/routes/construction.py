"""The construction contract (#419, spec §3.2): how an agent makes, reads and enriches geometry.

``GET /api/construction`` is the language, ``GET /api/construction/model`` the
model in the same words, ``POST /api/proposals/construction`` one script as one
proposal and ``POST /api/proposals/facets`` meaning added to existing geometry.
``POST /api/proposals/hosted-opening`` is the capability that meaning unlocks
(Stage C, spec §3.5): a door or a window on geometry whose facets say
``architectural.role = wall``. Every proposal is an ordinary one: made against
one exact state, continued with ``sourceProposalId`` and run by the candidate
route that already exists, with the base, chaining and stale checks of the
drawing routes (reused from ``routes/proposals.py``, never copied).
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Query
from starlette.requests import Request

from monkeyarch.construction import vocabulary

from ...binding import bound_project
from ...application.construction import (
    construction_model,
    construction_proposal,
    facets_proposal,
    hosted_opening_proposal,
    kept_refs,
)
from ...application.projection import StateProjection, project_state
from ...application.proposals import Proposal
from ..dto.construction import (
    ConstructionModelDto,
    ConstructionOutcomeDto,
    ConstructionProposalDto,
    ConstructionRefusalDto,
    ConstructionRequestDto,
    ConstructionVocabularyDto,
    EnrichmentRequiredDto,
    FacetsRequestDto,
    HostedOpeningRequestDto,
    construction_model_dto,
)
from ...errors import StudioError
from ..dto.proposal import ProposalDto
from .proposals import _proposal_source, _remember_proposal, _stale_base

router = APIRouter(tags=["construction"])

_SCRIPT_REFUSED = {422: {"model": ConstructionRefusalDto,
                         "description": "Refused; a refused script (CONSTRUCTION_INVALID) names its line"}}
_FACETS_REFUSED = {422: {"model": ConstructionRefusalDto,
                         "description": "Refused with a code and one sentence (FACETS_INVALID, FACETS_TARGET_INVALID)"}}
_OPENING_REFUSED = {
    409: {"model": EnrichmentRequiredDto,
          "description": "ENRICHMENT_REQUIRED names the facet to add first; STALE_BASE a request made against another state"},
    422: {"model": ConstructionRefusalDto,
          "description": "Refused with a code and one sentence: HOST_INVALID; a block that cannot take one as it is "
                         "drawn, with the reason; FAMILY_INVALID; INTERFACE_UNKNOWN; OPENING_INVALID"},
}


@router.get("/construction", response_model=ConstructionVocabularyDto, response_model_by_alias=True)
def read_construction_vocabulary() -> ConstructionVocabularyDto:
    """The construction language: conventions, verbs, limits and one example. Nothing needs to be discovered first."""

    return ConstructionVocabularyDto.model_validate(vocabulary())


@router.get("/construction/model", response_model=ConstructionModelDto, response_model_by_alias=True)
def read_construction_model(
    request: Request,
    run: str | None = Query(
        default=None, min_length=1,
        description="Answer for this run instead of the one the rule chooses. The run must exist in the bound project.",
    ),
    source_stage_ref: str | None = Query(default=None, alias="sourceStageRef", min_length=1),
) -> ConstructionModelDto:
    """The model as construction reads it: one entry per geometry id with its form, bounds, cuts, facets and
    the capabilities its facets unlock, plus the levels and parameters a script can name, the stateDigest a
    script is sent against, and which run and issued design it was read from (as GET /api/state says them)."""

    projection = project_state(bound_project(request.app.state), run, require_view=False,
                               source_stage_ref=source_stage_ref)
    return construction_model_dto(projection, construction_model(projection))


@router.post("/proposals/construction", response_model=ConstructionProposalDto, response_model_by_alias=True,
             status_code=201, responses=_SCRIPT_REFUSED)
def create_construction_proposal(request: Request, body: ConstructionRequestDto) -> ConstructionProposalDto:
    """One construction script becomes one proposal, run by the candidate route like any other.

    The script is interpreted, never executed. A refused script saves nothing and answers 422
    CONSTRUCTION_INVALID with the line, column and source line that caused it, including when the record
    refuses what the script made.
    """

    binding, base, projection, previous, body = _proposal_source(request, body)
    _require_state(binding, body, projection, "script")
    made = construction_proposal(
        binding, projection, body.script,
        parameters=[parameter.model_dump(exclude_unset=True) for parameter in body.parameters],
        summary=body.summary, keep_refs=kept_refs(projection.record, body.keep),
    )
    proposal = _remember(request, made.proposal, base, previous, body)
    return ConstructionProposalDto(**dict(proposal), construction=ConstructionOutcomeDto(
        report=list(made.result.report), log=list(made.result.log)))


@router.post("/proposals/facets", response_model=ProposalDto, response_model_by_alias=True, status_code=201,
             responses=_FACETS_REFUSED)
def create_facets_proposal(request: Request, body: FacetsRequestDto) -> ProposalDto:
    """Meaning set on, or taken from, existing geometry by its id; its form, cuts and objects stay exactly as
    they are. Unknown keys and values are refused with the nearest key or the allowed values."""

    binding, base, projection, previous, body = _proposal_source(request, body)
    _require_state(binding, body, projection, "facet change")
    proposal = facets_proposal(projection, [target.model_dump() for target in body.targets],
                               summary=body.summary, keep_refs=kept_refs(projection.record, body.keep))
    return _remember(request, proposal, base, previous, body)


@router.post("/proposals/hosted-opening", response_model=ProposalDto, response_model_by_alias=True, status_code=201,
             responses=_OPENING_REFUSED)
def create_hosted_opening_proposal(request: Request, body: HostedOpeningRequestDto) -> ProposalDto:
    """A door or a window on geometry whose meaning is architectural.role = wall, as one proposal.

    The model view lists hosted-opening among an entity's capabilities once its facets say so; asked of
    anything else, the answer is 409 ENRICHMENT_REQUIRED naming the facet to add. The geometry keeps its id,
    its delivered object and whatever stands on it; a block that cannot take one as it is drawn (not a
    rectangle, lifted off its base) is refused with the reason. along is measured on the model view's alongLine.
    """

    binding, base, projection, previous, body = _proposal_source(request, body)
    _require_state(binding, body, projection, "opening")
    proposal = hosted_opening_proposal(
        projection, body.host, kind=body.kind, along=body.along, width=body.width, sill=body.sill, head=body.head,
        shape=body.shape, spring_height=body.spring_height, family=body.family, interface_ref=body.interface_ref,
        summary=body.summary, keep_refs=kept_refs(projection.record, body.keep),
    )
    return _remember(request, proposal, base, previous, body)


def _require_state(binding, body, projection: StateProjection, what: str) -> None:
    """Made against one exact state, like every other proposal; a refusal names the run that holds the sent state."""

    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, f"the {what}", body.source_run_id)


def _remember(request: Request, proposal: Proposal, base: StateProjection, previous: Proposal | None,
              body: ConstructionRequestDto | FacetsRequestDto | HostedOpeningRequestDto) -> ProposalDto:
    """Place the proposal on its exact source, as the drawing routes do, then keep or continue it."""

    proposal = replace(proposal,
                       source_run_id=body.source_run_id or (base.run.run_id if base.reference_state_exact else None),
                       source_stage_ref=base.source_stage_ref)
    return _remember_proposal(request, proposal, base, previous)
