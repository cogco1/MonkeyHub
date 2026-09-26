"""One read-only status for a representation page, asked of the owner that holds it (#223).

A drawing, an AI render, a captured model view and an uploaded page each keep
their content with their own owner: the cut-plan recipe and its receipt
(studio.artifacts), the render request and its source snapshots
(studio.render), the registered page and its explicit replacements
(studio.artifacts). What a page was made from, and whether that still holds,
is derived here on every read from those retained facts. Nothing here writes,
stores a status or copies a dependency edge: this is the project-level
dependency as a projection, not a second state beside the Design State.

One vocabulary answers every consumer:

- ``current``: the page still shows what its inputs say now;
- ``outdated``: a newer registered page replaces it, or an input it read has
  changed; the page stays readable and is never rebound;
- ``frozen``: a person keeps it on a chosen version, either a drawing recipe's
  ``follow: frozen`` or a consumer's own keep such as Publish's freeze;
- ``unavailable``: the page, or an exact input it was made from, can no longer
  be read or verified.

The owners keep their read sets: ``drawing_plans.plan_status`` for a cut plan,
``rendering._freshness`` for an AI render's retained request, and
``working_draft.model_is_current`` for a page bound to one model state. Each
consumer keeps its wire words: the Worktree Graph says ``stale`` for
``outdated``; Publish says ``stale`` for ``outdated`` and ``missing`` when the
page's own bytes cannot be read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError
from ..transport.rendering import RenderRequestDto
from .artifacts import SourceDocument, _page_replacements, document_bytes, list_documents, require_model_source
from .binding import ProjectBinding
from .drawing_plans import plan_status
from .rendering import _freshness
from .working_draft import WorkingSources, model_is_current

CURRENT = "current"
OUTDATED = "outdated"
FROZEN = "frozen"
UNAVAILABLE = "unavailable"

# One page as every replacement names it: run, registered asset, drawing revision if any, page.
PageId = tuple[str, str, str | None, int]

_UNVERIFIABLE = (StudioError, ProjectRepositoryError, OSError, KeyError, TypeError, ValueError)


class ReplacementCycle(ValueError):
    """The retained replacements of a page loop back, so no newest page can be named."""


@dataclass(frozen=True, slots=True)
class RepresentationStatus:
    """Whether one exact page still represents what it was made from."""

    # current | outdated | frozen | unavailable
    state: str
    reason: str | None
    # The exact refs this page was made from, as its owner retained them: its
    # direct inputs only (an AI render's source and references, a drawing's
    # model and Stage), never their inputs.
    upstream: tuple[Mapping[str, Any], ...]
    # The newest registered page that replaces this one, if any.
    replacement: PageId | None
    # False only when this page's own registration or bytes cannot be read.
    page_available: bool = True


class RepresentationReads:
    """One request's shared reads: one Working Head and one document listing for every page it judges."""

    def __init__(self, binding: ProjectBinding, working: WorkingSources | None = None) -> None:
        self.binding = binding
        self.working = working or WorkingSources(binding)
        self._documents: tuple[SourceDocument, ...] | None = None
        self._links: dict[PageId, PageId] | None = None

    @property
    def documents(self) -> tuple[SourceDocument, ...]:
        if self._documents is None:
            self._documents = list_documents(self.binding)
        return self._documents

    def newest(self, page: PageId) -> PageId | None:
        """The newest registered page that replaces ``page``, following each explicit replacement."""

        if self._links is None:
            self._links = _page_replacements(self.documents)
        newest, seen = self._links.get(page), {page}
        while newest is not None and newest in self._links:
            if newest in seen:
                raise ReplacementCycle("Source replacements form a cycle.")
            seen.add(newest)
            newest = self._links[newest]
        return newest


def representation_status(binding: ProjectBinding, page: PageId, *, frozen: bool = False,
                          reads: RepresentationReads | None = None) -> RepresentationStatus:
    """Read one exact registered page's status from the owner that holds its dependencies.

    ``frozen`` is a consumer's own keep, such as Publish's freeze: the page is
    then read, never compared. A page's replacement answers before what it
    depicts. Raises ``ReplacementCycle`` when this page's replacements loop.
    """

    reads = reads or RepresentationReads(binding)
    replacement = reads.newest(page)
    run_id, asset_sha256, revision_ref, page_index = page
    try:
        document, _ = document_bytes(binding, run_id, asset_sha256, revision_ref)
    except StudioError as exc:
        return RepresentationStatus(UNAVAILABLE, exc.detail, (), replacement, page_available=False)
    except _UNVERIFIABLE:
        return RepresentationStatus(UNAVAILABLE, "The registered source document cannot be read.", (), replacement,
                                    page_available=False)
    if document.revision_ref != revision_ref or not 0 <= page_index < len(document.pages):
        return RepresentationStatus(UNAVAILABLE, "The exact source page is unavailable.", (), replacement,
                                    page_available=False)
    upstream = _upstream(document)
    if frozen:
        return RepresentationStatus(FROZEN, None, upstream, replacement)
    if replacement is not None:
        return RepresentationStatus(OUTDATED, "A newer registered page replaces this one.", upstream, replacement)
    state, reason = _owner_status(binding, document, reads.working)
    return RepresentationStatus(state, reason, upstream, None)


def _owner_status(binding: ProjectBinding, document: SourceDocument, working: WorkingSources) -> tuple[str, str | None]:
    """Ask the owner that holds this page's dependencies; never guess from a newer file."""

    recipe = document.view_recipe or {}
    try:
        if recipe.get("kind") == "mesh-orthographic":
            from .mesh_drawings import drawing_status
            return drawing_status(binding, recipe)
        if recipe.get("kind") == "cycles-render":
            from .render_scene import latest_scene
            from .geometry_sources import current_geometry
            scene,geometry=latest_scene(binding),current_geometry(binding)
            if not scene or not geometry:
                return UNAVAILABLE,"The scene or geometry is unavailable."
            if recipe['sceneRevision']!=scene['sceneRevision'] or recipe['geometryRevision']!=geometry['geometryRevision']:
                return OUTDATED,"Geometry or scene changed after this Cycles render."
            return CURRENT,None
        if recipe.get("kind") == "cut-plan":
            # Drawing owns its read set, anchors and dimensions: a change outside
            # the crop leaves the drawing current, against the Working Head or
            # against the version it was kept on.
            status = plan_status(binding, run_id=document.run_id, asset_sha256=document.asset_sha256,
                                 revision_ref=document.revision_ref, working=working)
            if status["status"] == "current":
                return (FROZEN if recipe.get("follow") == "frozen" else CURRENT), status["detail"]
            return (OUTDATED if status["status"] == "outdated" else UNAVAILABLE), status["detail"]
        if recipe.get("kind") == "ai-render":
            # Render follows its retained request through every source and reference.
            request = RenderRequestDto.model_validate(recipe["request"])
            if request.project_id != binding.project_id or recipe["jobId"] != document.run_id:
                raise ValueError("The render recipe belongs to another binding.")
            return _freshness(binding, request, recipe["sourceSnapshots"], working)
        if document.model_source is not None:
            # A captured view or a model-bound page shows one exact state: it is
            # current while that state is the Working Head's design content.
            require_model_source(binding, document.model_source)
            return model_is_current(binding, document.model_source.run_id, document.model_source.state_digest,
                                    head=working.head)
        # A page bound to nothing is current until a newer page replaces it.
        return CURRENT, None
    except _UNVERIFIABLE:
        return UNAVAILABLE, "The exact inputs of this page can no longer be verified."


def _upstream(document: SourceDocument) -> tuple[dict[str, Any], ...]:
    """The exact retained refs this page was made from, in the owner's own field names."""

    recipe = document.view_recipe or {}
    if recipe.get('kind') in ('mesh-orthographic','cycles-render'):
        return tuple({key:recipe[key]} for key in ('geometry','geometryRevision','sceneRevision') if key in recipe)
    if recipe.get("kind") == "ai-render":
        request = recipe.get("request")
        if not isinstance(request, Mapping):
            return ()
        pages = [("source", request.get("source")), *(("reference", ref) for ref in request.get("references") or ())]
        return tuple({role: dict(ref)} for role, ref in pages if isinstance(ref, Mapping))
    refs: list[dict[str, Any]] = []
    if recipe.get("sourceAsset"):
        refs.append({"sourceAsset": dict(recipe["sourceAsset"])})
    if document.model_source is not None:
        refs.append({"modelSource": document.model_source.to_dict()})
    if document.source_stage_ref is not None:
        refs.append({"sourceStageRef": document.source_stage_ref})
    return tuple(refs)
