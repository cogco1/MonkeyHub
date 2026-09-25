"""Working positions use P036; generation, acceptance and issue keep their owners.

The current position is also the project's **Working Head**: the latest valid
working state ordinary Modeling, Drawing and Render work follows (#271). It is
read here from the retained position, never guessed from the newest file.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_LOCAL_DRAFT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError, StaleWorkingDraft
from archflow.state.design_portfolio import DesignBranch

from .artifacts import FORMAT_3DM, ModelSource, list_artifacts, require_complete_model, require_model_source
from .binding import retained_sources
from .binding import ProjectBinding
from .projection import StateProjection, project_state
from ..transport.errors import StudioError
from ..transport.working_draft import LocalDraftDto, LocalDraftInputDto, LocalDraftSourceDto, WorkingDraftDto, WorkingDraftEntryDto


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(binding: ProjectBinding, value: dict, revision: str | None) -> None:
    try:
        binding.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
    except StaleWorkingDraft as exc:
        raise StudioError(409, "WORKING_DRAFT_STALE", str(exc)) from exc


def _entry(binding: ProjectBinding, run_id: str, *, branch_id: str | None = None) -> dict:
    projection = project_state(binding, run_id=run_id)
    if not projection.reference_state_exact or projection.state_digest is None:
        raise StudioError(409, "WORKING_DRAFT_UNAVAILABLE", "This run has no exact finished state to continue.")
    stage_ref = projection.source_stage_ref
    if stage_ref is not None:
        stage = binding.design_stage(stage_ref)
        if branch_id is not None and stage_ref not in {ref for ref, _ in binding.design_history(branch_id)}:
            raise StudioError(409, "WORKING_DRAFT_BRANCH_MISMATCH", "This source Stage does not belong to the selected branch.")
        branch_id = branch_id or stage.branch_id
    elif branch_id is not None:
        raise StudioError(409, "WORKING_DRAFT_BRANCH_MISMATCH", "This run has no confirmed Stage in the selected branch.")
    return {"updatedAt": _now(), "sourceStageRef": None if stage_ref is None else stage_ref.uri,
            "branchId": branch_id, "label": None, "automatic": False}


def _validate_local_source(binding: ProjectBinding, source: LocalDraftSourceDto) -> None:
    if source.projectId != binding.project_id:
        raise StudioError(409, "PROJECT_MISMATCH", "The local draft belongs to another project.")
    projection = project_state(binding, run_id=source.sourceRunId, source_stage_ref=source.sourceStageRef)
    if projection.state_digest != source.stateDigest:
        raise StudioError(409, "WORKING_DRAFT_SOURCE_CHANGED", "The local commands no longer match their exact retained source.")


@retained_sources
def read_working_draft(binding: ProjectBinding) -> WorkingDraftDto:
    value, revision = binding.repository.read_working_draft()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    entries = {run_id: WorkingDraftEntryDto(runId=run_id, **{key: item for key, item in row.items() if key != "automatic"})
               for run_id, row in value["runs"].items()}
    local = None
    if value["localDraftRef"] is not None:
        payload = binding.repository.load_json(ProjectRecordRef.from_dict(value["localDraftRef"]))
        if payload.get("schema") != "StudioLocalDraft@1" or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "WORKING_DRAFT_INVALID", "The retained local draft belongs to another project.")
        local = LocalDraftDto.model_validate(payload["draft"] | {"updatedAt": payload["updatedAt"]})
    return WorkingDraftDto(projectId=binding.project_id, revisionSha256=revision,
        current=entries.get(value["current"]),
        recovery=sorted((entries[key] for key, row in value["runs"].items()
                         if row["automatic"] and row["label"] is None and datetime.fromisoformat(row["updatedAt"]) >= cutoff),
                        key=lambda item: item.updatedAt, reverse=True),
        saved=sorted((entries[key] for key, row in value["runs"].items() if row["label"] is not None),
                     key=lambda item: item.updatedAt, reverse=True),
        managedRunIds=sorted(key for key, row in value["runs"].items() if row["automatic"]), localDraft=local)


@retained_sources
def select_working_draft(binding: ProjectBinding, run_id: str | None, revision: str | None,
                         branch_id: str | None = None) -> WorkingDraftDto:
    value, actual = binding.repository.read_working_draft()
    if actual != revision:
        raise StudioError(409, "WORKING_DRAFT_STALE", "The working position changed; read it before selecting another source.")
    if run_id is not None:
        row = _entry(binding, run_id, branch_id=branch_id)
        previous = value["runs"].get(run_id)
        if previous:
            row.update(label=previous["label"], automatic=previous["automatic"])
        value["runs"][run_id] = row
    value["current"] = run_id
    _write(binding, value, revision)
    return read_working_draft(binding)


@retained_sources
def save_working_draft(binding: ProjectBinding, run_id: str, revision: str | None, label: str | None) -> WorkingDraftDto:
    value, actual = binding.repository.read_working_draft()
    if actual != revision:
        raise StudioError(409, "WORKING_DRAFT_STALE", "The working draft changed; read it before saving a version.")
    previous = value["runs"].get(run_id)
    row = _entry(binding, run_id, branch_id=previous["branchId"] if previous else None)
    row.update(label=(label or "Saved version").strip() or "Saved version", automatic=previous["automatic"] if previous else False)
    value["runs"][run_id] = row
    _write(binding, value, revision)
    return read_working_draft(binding)


@retained_sources
def record_candidate_draft(binding: ProjectBinding, run_id: str, source_run_id: str | None) -> None:
    row = _entry(binding, run_id)
    row["automatic"] = True
    for _ in range(8):
        value, revision = binding.repository.read_working_draft()
        previous = value["runs"].get(run_id)
        if previous:
            row["label"] = previous["label"]
        value["runs"][run_id] = row
        if value["current"] is None or value["current"] == source_run_id:
            value["current"] = run_id
        try:
            binding.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
            return
        except StaleWorkingDraft:
            continue
    raise StudioError(409, "WORKING_DRAFT_STALE", "The candidate is retained, but its working position changed concurrently. Reopen its result.")


@retained_sources
def retain_local_draft(binding: ProjectBinding, draft: LocalDraftInputDto | None, revision: str | None,
                       expected_source: LocalDraftSourceDto | None = None) -> WorkingDraftDto:
    value, actual = binding.repository.read_working_draft()
    if actual != revision:
        raise StudioError(409, "WORKING_DRAFT_STALE", "The working draft changed before these local commands were saved.")
    if expected_source is not None and value["localDraftRef"] is not None:
        previous = binding.repository.load_json(ProjectRecordRef.from_dict(value["localDraftRef"]))
        if previous["draft"]["source"] != expected_source.model_dump():
            raise StudioError(409, "WORKING_DRAFT_SOURCE_CHANGED", "A different source now owns the local recovery; it has not been cleared.")
    if draft is None:
        value["localDraftRef"] = None
    else:
        _validate_local_source(binding, draft.source)
        if len(canonical_json(draft.model_dump()).encode("utf-8")) > 8 * 1024 * 1024:
            raise StudioError(413, "WORKING_DRAFT_TOO_LARGE", "A local recovery snapshot is limited to 8 MiB.")
        run = (binding.repository.load_run("studio-working-draft") if "studio-working-draft" in binding.run_ids()
               else binding.repository.create_run("studio-working-draft"))
        ref = binding.repository.put_json(run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECOVERY, run_id=run.run_id), record_kind=STUDIO_LOCAL_DRAFT,
            payload={"schema": "StudioLocalDraft@1", "projectId": binding.project_id, "updatedAt": _now(), "draft": draft.model_dump()})
        value["localDraftRef"] = ref.to_dict()
    _write(binding, value, revision)
    return read_working_draft(binding)


# ---- The Working Head (#271) -------------------------------------------------

WORKSPACES = ("modeling", "drawing", "render", "board")
LIVE = "live"
FROZEN = "frozen"
_LINEAGE_LIMIT = 64
_UNREADABLE = (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError)


@dataclass(frozen=True, slots=True)
class WorkingHead:
    """The project's current valid working state, as its retained facts state it."""

    run_id: str
    state_digest: str
    record_digest: str
    # The accepted Stage this state is, or the one it was continued from.
    source_stage_ref: str | None
    branch_id: str | None
    # True only when the head run is exactly that Stage's accepted model run.
    accepted: bool
    # working-position | branch-head | reference: which retained fact answered.
    origin: str
    label: str | None
    # The one complete viewable model of this exact state, when it has one.
    model_source: ModelSource | None
    # The head run first, then each exact retained parent it continued.
    lineage: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkingSource:
    """What one workspace should show and continue from, under one policy."""

    project_id: str
    workspace: str
    policy: str
    # The retained position's revision; it changes whenever the head moves.
    revision_sha256: str | None
    head: WorkingHead | None
    compatible: bool
    source: ModelSource | None
    # Set only when ``source`` is exactly an accepted Stage's pinned model.
    stage_ref: str | None
    reason: str | None
    warnings: tuple[str, ...]


def _detail(exc: Exception) -> str:
    return exc.detail if isinstance(exc, StudioError) else str(exc) or type(exc).__name__


def _lineage(binding: ProjectBinding, run_id: str) -> tuple[str, ...]:
    """The run and the exact retained sources it continued, newest first."""

    runs = [run_id]
    while len(runs) < _LINEAGE_LIMIT:
        try:
            delta = binding.candidate_delta(runs[-1])
            parent = None if delta is None else delta["source_run_ref"]["run_id"]
        except _UNREADABLE:
            break
        if parent is None or parent in runs:
            break
        runs.append(parent)
    return tuple(runs)


def _complete_model(binding: ProjectBinding, projection: StateProjection, stage) -> ModelSource | None:
    """The single complete model of this exact state; several are not chosen between."""

    run_id, digest = projection.run.run_id, projection.state_digest
    if stage is not None and stage.candidate_id == run_id:
        return ModelSource(run_id, digest, stage.model_sha256)
    rows = [row for row in list_artifacts(binding, run_id=run_id).artifacts
            if row.run_id == run_id and row.design_state_digest == digest and row.format == FORMAT_3DM
            and row.available and row.model_source is not None]
    composed = {row.model_source for row in rows if row.representation == "composed"}
    if composed:
        return next(iter(composed)) if len(composed) == 1 else None
    complete = set()
    for row in rows:
        try:
            require_complete_model(row, projection.reference.receipt or {})
        except StudioError:
            continue
        complete.add(row.model_source)
    return next(iter(complete)) if len(complete) == 1 else None


def _head_at(binding: ProjectBinding, run_id: str, *, branch_id: str | None, origin: str,
             label: str | None) -> WorkingHead:
    stage_ref = None
    if branch_id is not None and branch_id in binding.repository.read_design_branches():
        # A run accepted into several lines answers as the Stage of its own line.
        stage_ref = next((ref for ref, stage in binding.design_history(branch_id) if stage.candidate_id == run_id), None)
    projection = project_state(binding, run_id=run_id, source_stage_ref=stage_ref)
    if projection.run.run_id != run_id or not projection.reference_state_exact or projection.state_digest is None:
        raise StudioError(409, "WORKING_SOURCE_UNAVAILABLE", f"Run {run_id} has no exact finished state.")
    stage = None if projection.source_stage_ref is None else binding.design_stage(projection.source_stage_ref)
    accepted = stage is not None and stage.candidate_id == run_id
    return WorkingHead(
        run_id=run_id, state_digest=projection.state_digest, record_digest=projection.record_digest,
        source_stage_ref=None if projection.source_stage_ref is None else projection.source_stage_ref.uri,
        branch_id=stage.branch_id if stage is not None else branch_id, accepted=accepted, origin=origin,
        label=label or (stage.label if accepted else None),
        model_source=_complete_model(binding, projection, stage if accepted else None),
        lineage=_lineage(binding, run_id),
    )


def _fallback_head(binding: ProjectBinding, warnings: list[str]) -> WorkingHead | None:
    """Without a readable position: the main line's accepted head, then the reference run."""

    try:
        branches = binding.repository.read_design_branches()
        if branches:
            branch_id = "main" if "main" in branches else sorted(branches)[0]
            stage = binding.design_stage(DesignBranch.from_dict(branches[branch_id]).head_stage)
            return _head_at(binding, stage.candidate_id, branch_id=branch_id, origin="branch-head", label=None)
    except _UNREADABLE as exc:
        warnings.append(f"The accepted design head could not be read: {_detail(exc)}")
    try:
        reference = binding.reference_run()
        if reference.source == "none":
            return None
        return _head_at(binding, reference.run.run_id, branch_id=None, origin="reference", label=None)
    except _UNREADABLE as exc:
        warnings.append(f"The project's reference run could not be read: {_detail(exc)}")
    return None


def _stage_of_model(binding: ProjectBinding, model: ModelSource) -> str | None:
    matches = {ref.uri for branch_id in binding.repository.read_design_branches()
               for ref, stage in binding.design_history(branch_id)
               if stage.candidate_id == model.run_id and stage.model_sha256 == model.asset_sha256}
    return next(iter(matches)) if len(matches) == 1 else None


def _drawable(binding: ProjectBinding, head: WorkingHead) -> tuple[ModelSource | None, str | None, str | None]:
    """The head's first model the drawing owner can cut exactly, or why none can be."""

    from .drawings import _complete_source  # the drawing owner decides what it can draw

    stage_ref = record_ref_from_uri(head.source_stage_ref, binding.project_id) if head.accepted else None
    models = [] if head.model_source is None else [head.model_source]
    for row in list_artifacts(binding, run_id=head.run_id).artifacts:
        if (row.run_id == head.run_id and row.design_state_digest == head.state_digest and row.format == FORMAT_3DM
                and row.available and row.model_source is not None and row.model_source not in models):
            models.append(row.model_source)
    reason = "The current working version has no complete model to draw from yet."
    for model in models:
        pinned = stage_ref if stage_ref is not None and model == head.model_source else None
        try:
            _complete_source(binding, model, pinned)
        except _UNREADABLE as exc:
            reason = _detail(exc)
            continue
        return model, None if pinned is None else pinned.uri, None
    return None, None, reason


def _record_digest_of(binding: ProjectBinding, run_id: str, state_digest: str) -> str | None:
    try:
        newest = binding.newest_runner_receipt(run_id)
    except _UNREADABLE:
        return None
    if newest is None or newest[1].get("design_state_digest") != state_digest:
        return None
    digest = newest[1].get("state_record_digest")
    return digest if isinstance(digest, str) else None


def model_is_current(binding: ProjectBinding, run_id: str, state_digest: str, *,
                     head: WorkingHead | None = None) -> tuple[str, str | None]:
    """Whether an exact model state still is the Working Head's design content."""

    if head is None:
        head = resolve_working_source(binding).head
    if head is None:
        return "unavailable", "The project has no current working state to compare with."
    if (run_id, state_digest) == (head.run_id, head.state_digest):
        return "current", None
    if _record_digest_of(binding, run_id, state_digest) == head.record_digest:
        return "current", None
    return "outdated", "The project model has changed since this was made."


@retained_sources
def resolve_working_source(binding: ProjectBinding, workspace: str = "modeling", *, policy: str = LIVE,
                           pinned: ModelSource | None = None) -> WorkingSource:
    """Resolve the current working source for one workspace from retained facts only.

    LIVE answers the head's compatible exact source; FROZEN keeps an exact pinned
    model and says whether the head has moved past it. Nothing is written.
    """

    if workspace not in WORKSPACES:
        raise StudioError(422, "WORKSPACE_INVALID", f"Choose one of {', '.join(WORKSPACES)}.")
    if policy not in (LIVE, FROZEN) or (policy == FROZEN) != (pinned is not None):
        raise StudioError(422, "SOURCE_POLICY_INVALID", "Use live, or frozen with one exact pinned model.")
    value, revision = binding.repository.read_working_draft()
    warnings: list[str] = []
    head = None
    current = value["current"]
    if current is not None:
        row = value["runs"][current]
        try:
            head = _head_at(binding, current, branch_id=row["branchId"], origin="working-position", label=row["label"])
        except _UNREADABLE as exc:
            warnings.append(f"The saved working position {current} could not be read: {_detail(exc)}")
    if head is None:
        head = _fallback_head(binding, warnings)

    def answer(compatible, source, stage_ref, reason):
        return WorkingSource(binding.project_id, workspace, policy, revision, head, compatible, source, stage_ref,
                             reason, tuple(warnings))

    if policy == FROZEN:
        require_model_source(binding, pinned)
        state, reason = model_is_current(binding, pinned.run_id, pinned.state_digest, head=head)
        return answer(True, pinned, _stage_of_model(binding, pinned), None if state == "current" else reason)
    if head is None:
        return answer(False, None, None, "This project has no retained working state yet.")
    if workspace == "drawing":
        source, stage_ref, reason = _drawable(binding, head)
        return answer(source is not None, source, stage_ref, reason)
    source = head.model_source
    stage_ref = head.source_stage_ref if head.accepted and source is not None else None
    if workspace == "board":
        # Board follows its pages' own replacement links; any head is compatible.
        return answer(True, source, stage_ref, None)
    return answer(source is not None, source, stage_ref,
                  None if source is not None else "The current working version has no complete model to show yet.")
