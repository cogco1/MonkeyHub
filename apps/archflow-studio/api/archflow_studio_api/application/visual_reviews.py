"""``POST /api/visual-reviews``: exact sources in, owner-rendered frames, one bounded look (GH-303).

The caller names exact sources and carries its loop's Harness allowance; it
never sends pixels. Each source is rendered here, in process, by its projection
owner: ``model_view`` draws each requested view of one retained model, and
``export_board_pages`` rasterizes one registered page. The owner's own answer
becomes the frame, so a frame can only show the source it was asked for, and
``observe_frames`` then binds, admits, looks once and validates.

Nothing is written. The frames are transient, the allowance goes back to the
caller with the answer, and the only trace is the ``visual_observation``
Monitor span the channel already keeps. Every refusal before the provider call
(the allowance, a source the project does not retain exactly, a frame outside
the channel's bounds) leaves the allowance as it was.
"""

from __future__ import annotations

from typing import Sequence

from ..transport.errors import StudioError
from .artifacts import ModelSource
from .binding import ProjectBinding
from .boards import BoardExportPage, export_board_pages
from .drawings import model_view
from .monitoring import StudioMonitor
from .visual_observation import (
    MODEL_VIEWS, EvidenceFrame, ObservationUsage, ReviewReason, SourceRef, StudioModelVisualProvider,
    VisualBudgetRefused, VisualObservationProvider, VisualProviderFailed, VisualReviewBudget, VisualReviewInvalid,
    VisualReviewRequest, VisualReviewResult, observe_frames, page_frame,
)

MODELING = "modeling"
MODEL_REPRESENTATION = "orthographic-line-projection"
# The edge Study and the V0 Board check already read pages at: legible at sheet
# scale, about 3 k input tokens, and inside the channel's 2048 px bound.
PAGE_MAX_EDGE = 1600
# Owner refusals meaning the named source is not exactly one the project
# retains (a stale state, a foreign asset, a missing run, revision or page):
# the source a review must not be answered about. Other owner refusals, such as
# a model with no complete STEP, keep their own code.
_NOT_RETAINED = {
    "model": frozenset({"RUN_NOT_FOUND", "MODEL_SOURCE_MISMATCH", "MODEL_SOURCE_UNREGISTERED"}),
    # BOARD_INVALID is how a one-page PNG export refuses a generated page named without its revisionRef.
    "page": frozenset({"RUN_NOT_FOUND", "DOCUMENT_NOT_FOUND", "DOCUMENT_PAGE_NOT_FOUND", "BOARD_INVALID"}),
}
_NOTHING_SENT = "Nothing was sent to the provider; the allowance is unchanged."


class VisualReviewRefused(StudioError):
    """A review that did not happen or failed, answered with what the caller needs to go on.

    ``budget`` is the loop's allowance as it now stands: unchanged when nothing
    was sent, one review spent when the provider was called. ``usage`` is what a
    failed call still cost; ``source`` is a named source its owner does not
    retain exactly.
    """

    def __init__(self, status: int, code: str, detail: str, *, source: SourceRef | None = None,
                 budget: VisualReviewBudget | None = None, usage: ObservationUsage | None = None) -> None:
        super().__init__(status, code, detail)
        self.source_ref = None if source is None else source.to_dict()
        self.budget_state = None if budget is None else budget.to_dict()
        self.usage = None if usage is None else usage.to_dict()

    def body(self) -> dict[str, object]:
        body = super().body()
        for key, value in (("sourceRef", self.source_ref), ("budgetState", self.budget_state), ("usage", self.usage)):
            if value is not None:
                body[key] = value
        return body


def visual_provider(compiler: object) -> VisualObservationProvider:
    """The project's configured Studio model transport as the vision provider; no fallback is chosen."""

    try:
        return StudioModelVisualProvider(compiler)
    except VisualReviewInvalid as exc:
        configured = getattr(compiler, "provider", None) or "none"
        raise VisualReviewRefused(
            409, "VISUAL_PROVIDER_UNAVAILABLE",
            "A visual review needs the project's configured Codex or Anthropic provider; this runtime's provider "
            f"is {configured}. {_NOTHING_SENT}",
        ) from exc


def planned_frames(request: VisualReviewRequest) -> tuple[tuple[SourceRef, str], ...]:
    """The owner view each frame will be, decided from the request alone before anything renders.

    A modeling review looks at one exact model in every view of its recipe. The
    other domains look at registered pages (drawing revisions, render results,
    Board pages), one frame each, named ``page-<pageIndex>``.
    """

    kinds = {source.kind for source in request.source_refs}
    if request.domain == MODELING:
        if kinds != {"model"} or len(request.source_refs) != 1:
            raise VisualReviewInvalid("a modeling review looks at exactly one model source; review another separately")
        unknown = [view for view in request.view_recipe if view not in MODEL_VIEWS]
        if unknown:
            raise VisualReviewInvalid(f"{', '.join(unknown)} is not a model view; use {', '.join(MODEL_VIEWS)}")
        return tuple((request.source_refs[0], view) for view in request.view_recipe)
    if kinds != {"page"}:
        raise VisualReviewInvalid(f"a {request.domain} review looks at registered pages, not models")
    frames = tuple((source, f"page-{source.page_index}") for source in request.source_refs)
    views = [view for _, view in frames]
    if len(set(views)) != len(views):
        raise VisualReviewInvalid("the pages of one review need distinct page indexes; review the others separately")
    if set(views) != set(request.view_recipe):
        raise VisualReviewInvalid(f"viewRecipe must name exactly this review's page frames: {', '.join(views)}")
    return frames


def render_frames(binding: ProjectBinding, planned: Sequence[tuple[SourceRef, str]]) -> tuple[EvidenceFrame, ...]:
    """Ask each source's owner for its frame; the owner verifies the exact source before it draws."""

    frames = []
    for source, view in planned:
        try:
            if source.kind == "model":
                png, width, height = model_view(
                    binding, model_source=ModelSource(source.run_id, source.state_digest, source.asset_sha256),
                    view=view)
                frames.append(EvidenceFrame(source, view, MODEL_REPRESENTATION, png, width, height))
            else:
                page = export_board_pages(
                    binding, [BoardExportPage(source.run_id, source.asset_sha256, source.revision_ref, source.page_index)],
                    "png", False, PAGE_MAX_EDGE)
                frames.append(page_frame(source, page.content, view_ref=view))
        except StudioError as exc:
            if exc.code not in _NOT_RETAINED[source.kind]:
                raise
            raise VisualReviewRefused(
                409, "VISUAL_SOURCE_MISMATCH",
                f"The project does not retain exactly this {source.kind} source ({exc.code}: {exc.detail}). "
                f"Read the current exact source and ask again. {_NOTHING_SENT}",
                source=source,
            ) from exc
    return tuple(frames)


def review_sources(binding: ProjectBinding, request: VisualReviewRequest, *, reason: ReviewReason | str,
                   budget: VisualReviewBudget, provider: VisualObservationProvider, addressed: Sequence[str] = (),
                   monitor: StudioMonitor | None = None) -> VisualReviewResult:
    """Admit, render through the owners, look once.

    ``budget`` is the caller's loop state (``VisualReviewBudget.resume``) and is
    updated in place, so the caller can be handed it back with the answer. An
    ``after_repair`` review names in ``addressed`` the findings of the last
    review that the repair answered.
    """

    try:
        reason = ReviewReason(reason)
        if addressed and reason is not ReviewReason.AFTER_REPAIR:
            raise VisualReviewInvalid("addressed findings belong to an after_repair review")
        planned = planned_frames(request)
        if reason is ReviewReason.AFTER_REPAIR:
            budget.note_repair(addressed)
        refused = budget.refusal(reason)
        if refused is not None:
            raise refused
    except VisualBudgetRefused as exc:
        raise VisualReviewRefused(409, exc.code, f"{exc}. {_NOTHING_SENT}", budget=budget) from exc
    except ValueError as exc:
        raise VisualReviewRefused(422, "VISUAL_REVIEW_INVALID", f"{exc}. {_NOTHING_SENT}") from exc
    try:
        frames = render_frames(binding, planned)
    except VisualReviewInvalid as exc:
        raise VisualReviewRefused(422, "VISUAL_REVIEW_INVALID", f"{exc}. {_NOTHING_SENT}") from exc
    used = budget.used
    try:
        return observe_frames(request, frames, provider=provider, budget=budget, reason=reason, monitor=monitor,
                              project_id=binding.project_id)
    except VisualBudgetRefused as exc:
        raise VisualReviewRefused(409, exc.code, f"{exc}. {_NOTHING_SENT}", budget=budget) from exc
    except VisualReviewInvalid as exc:
        raise VisualReviewRefused(422, "VISUAL_REVIEW_INVALID", f"{exc}. {_NOTHING_SENT}") from exc
    except VisualProviderFailed as exc:
        raise VisualReviewRefused(
            502, "VISUAL_PROVIDER_FAILED",
            f"The provider gave no usable observation, and this review is spent: {exc}",
            budget=budget, usage=exc.usage,
        ) from exc
    except StudioError as exc:
        if budget.used == used:
            raise
        raise VisualReviewRefused(
            502, "VISUAL_PROVIDER_FAILED",
            f"The provider call failed, and this review is spent ({exc.code}: {exc.detail})", budget=budget,
        ) from exc
