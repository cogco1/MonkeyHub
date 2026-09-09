"""Compile a request into a scalar or semantic proposal against its exact base.

Pending intents retain clarification context. Scalar requests first resolve
known controls and scope; an agent may interpret an unfamiliar target against
the record sheet before the same checks run again. Component edits go to the
design compiler and are typed against StateRecord and producer contracts.
Both return a stored, reviewable proposal for the existing candidate path.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Query
from starlette.requests import Request
from archflow.project.refs import ProjectRecordRef

from dataclasses import replace

from ..application import clarification
from ..application.binding import bound_project
from ..application.artifacts import ModelSource, require_model_source
from ..application.catalog import catalog_of
from ..application.conventions import project_conventions
from ..application.clarification import PendingIntentStore, Resolution
from ..application.gestures import (
    DocumentAnnotationRef, GestureReading, list_document_comments, read_document_annotations, read_gestures,
    retain_document_comment, save_document_annotations,
    prepare_document_visuals,
    require_document_model_sources, require_document_comment_source,
    read_model_annotations, save_model_annotations,
)
from ..application.intent import (
    DeterministicIntentProvider,
    component_edit_proposal,
    merge_keep,
    parse_utterance,
)
from ..application.intent_agent import (
    AGENT_FAILED,
    DETERMINISTIC,
    DeterministicCompiler,
    Compilation,
    DocumentVisual,
    IntentCompiler,
    Selection,
    context_refs,
)
from ..application.projection import StateProjection, project_state, require_actionable
from ..application.proposals import proposal_from
from ..transport.errors import (
    BlockedNeedsHuman,
    MissingEditableControl,
    StudioError,
    UnsupportedRequest,
)
from ..transport.intent import (
    DocumentAnnotationsDto,
    DocumentAnnotationsRequestDto,
    DocumentCommentDto,
    DocumentCommentsDto,
    IntentBlockedDto,
    IntentDto,
    IntentRequestDto,
    IntentTimingsDto,
    ModelAnnotationsDto, ModelAnnotationsRequestDto, model_annotations_dto,
    agent_dto,
    document_annotation_ref_from,
    document_annotations_dto,
    document_gesture_from,
    draft_body,
    gesture_from,
    pending_body,
    pending_dto,
)
from ..transport.proposal import ProposalScopeDto, to_dto
from ..transport.artifacts import model_source_from, model_source_dto
from .proposals import _require_bound_project

router = APIRouter(tags=["intents"])


@router.get("/model-annotations", response_model=ModelAnnotationsDto, response_model_by_alias=True)
def read_saved_model_annotations(
    request: Request, run_id: str = Query(alias="runId"), state_digest: str = Query(alias="stateDigest"),
    asset_sha256: str = Query(alias="assetSha256"), revision_sha256: str | None = Query(default=None, alias="revisionSha256"),
) -> ModelAnnotationsDto:
    return model_annotations_dto(read_model_annotations(bound_project(request.app.state),
                                 ModelSource(run_id, state_digest, asset_sha256), revision_sha256))


@router.put("/model-annotations", response_model=ModelAnnotationsDto, response_model_by_alias=True)
def write_saved_model_annotations(request: Request, payload: ModelAnnotationsRequestDto) -> ModelAnnotationsDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return model_annotations_dto(save_model_annotations(binding, model_source_from(payload.model_source),
                                 payload.base_revision_sha256, [row.model_dump(mode="json", by_alias=True) for row in payload.annotations], payload.comment))


@router.get("/document-annotations", response_model=DocumentAnnotationsDto, response_model_by_alias=True)
def read_document_page_annotations(
    request: Request,
    run_id: str = Query(alias="runId", min_length=1),
    asset_sha256: str = Query(alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
    page_index: int = Query(alias="pageIndex", ge=0),
    revision_sha256: str | None = Query(alias="revisionSha256", default=None, pattern=r"^[0-9a-f]{64}$"),
    drawing_revision_ref: str | None = Query(alias="drawingRevisionRef", default=None),
) -> DocumentAnnotationsDto:
    return document_annotations_dto(read_document_annotations(
        bound_project(request.app.state), run_id, asset_sha256, page_index, revision_sha256, drawing_revision_ref,
    ))


@router.put("/document-annotations", response_model=DocumentAnnotationsDto, response_model_by_alias=True)
def write_document_page_annotations(request: Request, payload: DocumentAnnotationsRequestDto) -> DocumentAnnotationsDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return document_annotations_dto(save_document_annotations(
        binding, payload.run_id, payload.asset_sha256, payload.page_index,
        payload.base_revision_sha256, [document_gesture_from(row) for row in payload.annotations], payload.comment, payload.drawing_revision_ref,
    ))


@router.get("/document-comments", response_model=DocumentCommentsDto, response_model_by_alias=True)
def read_submitted_document_comments(request: Request, run_id: str = Query(alias="runId", min_length=1)) -> DocumentCommentsDto:
    return DocumentCommentsDto(comments=[
        DocumentCommentDto(comment_ref=ref.uri, **dict(payload))
        for ref, payload in list_document_comments(bound_project(request.app.state), run_id)
    ])


def _refused(
    store: PendingIntentStore, *, token: str | None, resolution: Resolution,
    document_comment_ref: ProjectRecordRef | None = None,
    model_source: ModelSource | None = None,
    document_visuals: tuple[DocumentVisual, ...] = (),
    source_stage_ref: ProjectRecordRef | None = None,
) -> StudioError:
    """One of the three refusing outcomes, as the error the route raises.

    The old token is closed whatever happens: a continuation is used once, so a
    reply cannot be replayed against a round that has already moved on. A
    non-terminal answer opens a new one; a terminal answer opens none, and its
    ``continuationToken`` is ``null`` — which is how the client knows to stop
    offering an input box.
    """

    resolution = replace(resolution, pending=replace(
        resolution.pending, document_comment_ref=document_comment_ref,
        model_source=model_source, document_visuals=document_visuals,
        source_stage_ref=source_stage_ref,
    ))
    store.close(token)
    if not resolution.terminal:
        store.open(resolution.pending)
    pending = pending_body(resolution.pending)
    if resolution.outcome == clarification.MISSING_EDITABLE_CONTROL:
        return MissingEditableControl(
            resolution.detail,
            pending=pending,
            draft=None if resolution.draft is None else draft_body(resolution.draft),
        )
    if resolution.outcome == clarification.UNSUPPORTED:
        return UnsupportedRequest(resolution.detail, pending=pending)
    return BlockedNeedsHuman(
        resolution.detail,
        question=resolution.question or "",
        accepted_forms=resolution.accepted_forms,
        pending=pending,
    )


def _semantic_answer(
    request: Request,
    body: IntentRequestDto,
    projection: StateProjection,
    reading: GestureReading,
    pending,
    compilation: Compilation,
    compile_ms: int,
    document_comment_ref: ProjectRecordRef | None = None,
) -> IntentDto:
    if compilation.status != "compiled" or compilation.utterance is not None or compilation.semantic_edit is None:
        raise StudioError(502, "INTENT_AGENT_FAILED", "the agent must compile one design edit or ask a question")
    typed_at = time.perf_counter()
    utterance = body.utterance if pending is None else (
        f"Original request: {pending.original_utterance}\nArchitect's clarification: {body.utterance}"
    )
    parts = component_edit_proposal(
        projection, compilation.semantic_edit, utterance=utterance,
        component_id=compilation.component_id, keep_refs=reading.keep_refs,
    )
    proposal = proposal_from(parts)
    resolution = clarification.semantic_resolution(
        projection, utterance=body.utterance,
        selection=Selection(proposal.component_id, proposal.element_id, reading.facts), pending=pending,
    )
    proposal = replace(
        proposal, pending=resolution.pending, source_run_id=body.source_run_id,
        source_stage_ref=projection.source_stage_ref,
        compilation_receipt=None if compilation.receipt is None else compilation.receipt.to_dict(),
        document_comment_ref=document_comment_ref,
        model_source=model_source_from(body.model_source) if body.model_source else None,
    )
    request.app.state.proposals.put(proposal)
    request.app.state.pending_intents.close(body.continuation_token)
    return IntentDto(
        outcome=clarification.COMPILED, agent=agent_dto(compilation), proposal=to_dto(proposal),
        timings=IntentTimingsDto(compile_ms=compile_ms, type_ms=int((time.perf_counter() - typed_at) * 1000)),
        gestures=list(reading.facts), pending_intent=pending_dto(resolution.pending),
        document_comment_ref=None if document_comment_ref is None else document_comment_ref.uri,
    )


@router.post(
    "/intents",
    response_model=IntentDto,
    response_model_by_alias=True,
    status_code=201,
    responses={422: {"model": IntentBlockedDto}},
)
def compile_intent(request: Request, body: IntentRequestDto) -> IntentDto:
    """Compile one request against the resolved target, then propose it."""

    binding = bound_project(request.app.state)
    _require_bound_project(binding, body.project_id)
    projection = project_state(binding, run_id=body.source_run_id, source_stage_ref=body.source_stage_ref)
    # Fail before resolution or model invocation: no compiler should explore
    # against a historical run whose exact state cannot base new work.
    require_actionable(projection)
    if body.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the request names state {body.state_digest}, but "
            f"{binding.project_id} is at {projection.state_digest}. Read "
            "/api/state again and ask against the state that answers now.",
        )
    if projection.reference_state_exact:
        body = body.model_copy(update={"source_run_id": projection.run.run_id})
    model_source = model_source_from(body.model_source) if body.model_source else None
    if model_source is not None:
        require_model_source(binding, model_source, projection)
    store: PendingIntentStore = request.app.state.pending_intents
    # A pending intent is bound to a stateDigest. One opened against a state
    # the project has left is void and is never applied to the state that
    # answers now — the client is told so rather than being answered about a
    # record it was not looking at.
    pending = (
        None
        if body.continuation_token is None
        else store.resume(body.continuation_token, projection.state_digest)
    )
    if pending is not None and pending.source_stage_ref != projection.source_stage_ref:
        raise StudioError(409, "DESIGN_STAGE_MISMATCH", "Continue this clarification from its original design stage.")
    if pending is not None and pending.model_source is not None:
        if model_source is not None and model_source != pending.model_source:
            raise StudioError(409, "MODEL_SOURCE_MISMATCH", "This clarification belongs to a different model source.")
        model_source = pending.model_source
        require_model_source(binding, model_source, projection)
        body = body.model_copy(update={"model_source": model_source_dto(model_source)})
    # What was drawn, read into the record's names before anyone reads the
    # words: a circle with no pick is the selection, a keep mark is a keep
    # clause. Both are the server's, and both are printed back as facts.
    reading = read_gestures(projection, [gesture_from(dto) for dto in body.gestures])
    document_references = [document_annotation_ref_from(dto) for dto in body.document_annotations]
    submitted_words = body.utterance
    if pending is not None and pending.document_comment_ref is not None:
        previous_comment = binding.repository.load_json(pending.document_comment_ref)
        require_document_comment_source(binding, previous_comment, projection)
        previous_references = [DocumentAnnotationRef(
            row["runId"], row["assetSha256"], row["pageIndex"], row["revisionSha256"],
            row.get("drawingRevisionRef"),
        ) for row in previous_comment["documentAnnotations"]]
        if "document_annotations" in body.model_fields_set and document_references != previous_references:
            raise StudioError(409, "DOCUMENT_CONTEXT_MISMATCH", "This clarification belongs to the originally submitted page revisions. Submit a new request to change them.")
        document_references = previous_references
        submitted_words = f"{previous_comment['utterance']}\nArchitect's clarification: {body.utterance}"
    if pending is not None and "document_visuals" not in body.model_fields_set:
        document_visuals = pending.document_visuals
        if document_references and not document_visuals:
            raise StudioError(422, "DOCUMENT_VISUALS_REQUIRED", "Submit a new drawing request with its page images.")
    else:
        if pending is not None and pending.document_visuals and not body.document_visuals:
            raise StudioError(409, "DOCUMENT_CONTEXT_MISMATCH", "This clarification must keep the originally submitted page images and references.")
        document_visuals = prepare_document_visuals(binding, document_references, [
            row.model_dump(mode="json", by_alias=True) for row in body.document_visuals
        ])
        if pending is not None and document_visuals != pending.document_visuals:
            raise StudioError(409, "DOCUMENT_CONTEXT_MISMATCH", "This clarification belongs to the originally submitted page images and references. Submit a new request to change them.")
    document_sources = require_document_model_sources(binding, document_references, projection, model_source)
    if model_source is None and document_sources:
        model_source = ModelSource.from_dict(document_sources[0]["modelSource"])
        body = body.model_copy(update={"model_source": model_source_dto(model_source)})
    document_facts, document_comment_ref = retain_document_comment(
        binding, document_references,
        utterance=submitted_words, state_digest=body.state_digest, source_run_id=body.source_run_id,
        document_sources=document_sources, document_visuals=document_visuals,
    )
    reading = replace(reading, facts=reading.facts + document_facts)
    selection = Selection(
        component_id=body.target_component_id,
        element_id=body.element_id,
        gestures=reading.facts,
        document_visuals=document_visuals,
    )
    compilation = None
    compile_ms = 0
    edit_request = clarification.component_edit_requested(body.utterance) or (
        pending is not None and pending.action_kind == clarification.EDIT_COMPONENTS
    )
    configured_compiler = request.app.state.intent_compiler
    if edit_request:
        agent_selection = replace(reading.target, gestures=reading.facts, document_visuals=document_visuals) if reading.target else selection
        if isinstance(configured_compiler, DeterministicCompiler):
            raise _refused(store, token=body.continuation_token, source_stage_ref=projection.source_stage_ref, model_source=model_source, document_visuals=document_visuals, document_comment_ref=document_comment_ref, resolution=clarification.semantic_resolution(
                projection, utterance=body.utterance, selection=agent_selection, pending=pending,
                unsupported=True,
                detail="Component changes need the configured design agent. This process currently accepts numeric edits only.",
            ))
        message = body.utterance
        if pending is not None:
            message = f"Original request: {pending.original_utterance}\nArchitect's clarification: {message}"
        started = time.perf_counter()
        compilation = configured_compiler.compile(
            message=message, selection=agent_selection, projection=projection,
        )
        compile_ms = int((time.perf_counter() - started) * 1000)
        if compilation.semantic_edit is not None:
            return _semantic_answer(request, body, projection, reading, pending, compilation, compile_ms, document_comment_ref)
        if edit_request:
            question = compilation.question if compilation.status == "question" else None
            raise _refused(store, token=body.continuation_token, source_stage_ref=projection.source_stage_ref, model_source=model_source, document_visuals=document_visuals, document_comment_ref=document_comment_ref, resolution=clarification.semantic_resolution(
                projection, utterance=body.utterance, selection=agent_selection, pending=pending,
                question=question, unsupported=question is None,
                detail=compilation.why or "The request changes building components; the agent did not supply a supported component edit.",
            ))
    # Known missing controls, derived values and unresolved scope are settled
    # before a model call. An unfamiliar natural-language name may still be
    # read by the agent against the sheet, then checked by this same resolver.
    # The project's conventions (PROJECT.md: names for people, the compass) and
    # the request's camera let a viewer word become a side; the catalog is the
    # one directory of editable elements and of what the model shows without a row.
    conventions = project_conventions(binding, run_id=body.source_run_id)
    conventions_are_current = conventions.state_digest == projection.state_digest
    camera = (
        body.camera.model_dump()
        if body.camera is not None
        else body.gestures[0].camera.model_dump()
        if body.gestures
        else None
    )
    catalog = catalog_of(binding, projection) if getattr(projection, "state", None) is not None else None
    resolution_context = dict(
        has_camera=bool(body.gestures) or camera is not None, pending=pending,
        camera=camera, compass=conventions.compass if conventions_are_current else None,
        aliases=conventions.aliases if conventions_are_current else {}, catalog=catalog, scope=body.scope,
    )
    resolution = clarification.resolve(
        projection, utterance=body.utterance, selection=selection,
        picked=reading.target, **resolution_context,
    )
    if (
        resolution.pending.reason_code == clarification.TARGET_UNRESOLVED
        and parse_utterance(body.utterance) is None
        and not isinstance(configured_compiler, DeterministicCompiler)
        and not clarification.declares_a_control(body.utterance)
    ):
        message = body.utterance if pending is None else (
            f"Original request: {pending.original_utterance}\nArchitect's clarification: {body.utterance}"
        )
        started = time.perf_counter()
        compilation = configured_compiler.compile(message=message, selection=selection, projection=projection)
        compile_ms = int((time.perf_counter() - started) * 1000)
        if compilation.semantic_edit is not None:
            return _semantic_answer(request, body, projection, reading, pending, compilation, compile_ms, document_comment_ref)
        checked = clarification.read_compilation(
            projection, compilation=compilation, resolution=resolution, pending=pending,
        )
        if checked.outcome != clarification.COMPILED:
            raise _refused(store, token=body.continuation_token, source_stage_ref=projection.source_stage_ref, model_source=model_source, document_visuals=document_visuals, resolution=checked, document_comment_ref=document_comment_ref)
        original_utterance = resolution.pending.original_utterance
        resolution = clarification.resolve(
            projection, utterance=compilation.utterance,
            selection=Selection(compilation.component_id, compilation.element_id, reading.facts),
            **resolution_context,
        )
        resolution = replace(resolution, pending=replace(resolution.pending, original_utterance=original_utterance))
    if resolution.outcome != clarification.COMPILED:
        raise _refused(store, token=body.continuation_token, source_stage_ref=projection.source_stage_ref, model_source=model_source, document_visuals=document_visuals, resolution=resolution, document_comment_ref=document_comment_ref)
    assert resolution.selection is not None
    # A sentence already in the grammar is not read by the agent: the grammar
    # is the truth about it, and the architect who typed an exact sentence gets
    # the same answer, in the same time, as before there was an agent at all.
    # An absolute delta against the one field the request resolved to ("提高 0.1m")
    # is read here, deterministically, into the grammar; a qualitative word is not.
    message = body.utterance
    delta_sentence = clarification.grammar_sentence_for(body.utterance, resolution=resolution, projection=projection)
    if delta_sentence is None and pending is not None:
        # A reply that settles a slot restates nothing. "整个叠层" answers how
        # far and says no number: the change this exchange is about is still
        # the sentence that opened it, which is the whole reason the pending
        # intent keeps ``originalUtterance``. The reading is the same
        # deterministic one — a number, a unit and a direction word, or nothing.
        delta_sentence = clarification.grammar_sentence_for(
            resolution.pending.original_utterance,
            resolution=resolution,
            projection=projection,
        )
    if delta_sentence is not None:
        message = delta_sentence
    compiler: IntentCompiler = (
        DeterministicCompiler()
        if parse_utterance(message) is not None
        else request.app.state.intent_compiler
    )
    if compilation is None:
        compiled_at = time.perf_counter()
        compilation = compiler.compile(
            message=message,
            selection=replace(resolution.selection, gestures=reading.facts, document_visuals=document_visuals),
            projection=projection,
        )
        compile_ms = int((time.perf_counter() - compiled_at) * 1000)
    if compilation.semantic_edit is not None:
        return _semantic_answer(request, body, projection, reading, pending, compilation, compile_ms, document_comment_ref)
    # What the compiler answered, in the same four outcomes. An agent that asks
    # still names a target, and that target is kept: losing it is what made the
    # next round start from nothing.
    resolution = clarification.read_compilation(
        projection, compilation=compilation, resolution=resolution, pending=pending
    )
    if resolution.outcome != clarification.COMPILED:
        raise _refused(store, token=body.continuation_token, source_stage_ref=projection.source_stage_ref, model_source=model_source, document_visuals=document_visuals, resolution=resolution, document_comment_ref=document_comment_ref)
    assert compilation.utterance is not None
    if reading.keep_refs:
        compilation = replace(
            compilation,
            utterance=merge_keep(compilation.utterance, reading.keep_refs),
        )
    typed_at = time.perf_counter()
    try:
        parts = DeterministicIntentProvider(projection).propose(
            session_ref=f"project:{binding.project_id}",
            message=compilation.utterance,
            context_refs=context_refs(
                body.state_digest, compilation, resolution.selection
            ),
        )
    except BlockedNeedsHuman as exc:
        if compilation.provider != DETERMINISTIC:
            store.close(body.continuation_token)
            raise StudioError(502, AGENT_FAILED, exc.detail) from exc
        # The grammar refuses for reasons only it knows — a locked parameter, a
        # unit it will not convert, an element of another component. Its
        # sentence is the record's and travels verbatim; what it was missing
        # was the exchange it belongs to and whether that is still advancing.
        raise _refused(
            store,
            token=body.continuation_token,
            document_comment_ref=document_comment_ref,
            source_stage_ref=projection.source_stage_ref,
            model_source=model_source,
            document_visuals=document_visuals,
            resolution=clarification.blocked(
                exc.detail,
                question=exc.question,
                resolution=resolution,
                pending=pending,
                accepted_forms=exc.accepted_forms,
            ),
        ) from exc
    proposal = proposal_from(parts)
    # What compiled the words travels with what they became. A sentence
    # already in the grammar was read by no model and carries no receipt.
    # The pending intent travels too: it is what was actually asked — the
    # request id, the slots the resolver filled — and a judgement made about
    # this proposal later has no other way of knowing it.
    proposal = replace(
        proposal,
        compilation_receipt=(
            None if compilation.receipt is None else compilation.receipt.to_dict()
        ),
        pending=resolution.pending,
        source_run_id=body.source_run_id,
        source_stage_ref=projection.source_stage_ref,
        document_comment_ref=document_comment_ref,
        model_source=model_source,
    )
    request.app.state.proposals.put(proposal)
    type_ms = int((time.perf_counter() - typed_at) * 1000)
    # The exchange is over: the token that got here is spent, and the pending
    # intent that comes back carries a null one.
    store.close(body.continuation_token)
    # How far the exchange settled on, travelling with what it became. The
    # operator moved one scalar whatever the scope says; the ids are what the
    # client shows revalidated, so the closure is not a surprise afterwards.
    settled = clarification.scope_of(resolution.pending)
    return IntentDto(
        outcome=clarification.COMPILED,
        agent=agent_dto(compilation),
        proposal=to_dto(
            proposal,
            scope=(
                None
                if settled is None
                else ProposalScopeDto(scope=settled[0], element_ids=list(settled[1]))
            ),
        ),
        timings=IntentTimingsDto(compile_ms=compile_ms, type_ms=type_ms),
        gestures=list(reading.facts),
        pending_intent=pending_dto(resolution.pending),
        document_comment_ref=None if document_comment_ref is None else document_comment_ref.uri,
    )
