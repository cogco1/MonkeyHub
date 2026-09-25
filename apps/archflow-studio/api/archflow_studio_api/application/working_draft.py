"""Working positions use P036; generation, acceptance and issue keep their owners."""

from datetime import datetime, timedelta, timezone

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_LOCAL_DRAFT
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import StaleWorkingDraft

from .binding import retained_sources
from .binding import ProjectBinding
from .projection import project_state
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
    """List a finished candidate for recovery; never move the working position.

    ``current`` is the architect's own editing base and only
    ``select_working_draft`` moves it (Continue, Return to default, a Versions
    choice, an adopted Sync). Recording a generated candidate -- Hub agent, Arch
    proposal, options, program or combine -- leaves it alone, even when the
    candidate was generated from that base (``source_run_id``).
    """
    row = _entry(binding, run_id)
    row["automatic"] = True
    for _ in range(8):
        value, revision = binding.repository.read_working_draft()
        previous = value["runs"].get(run_id)
        if previous:
            row["label"] = previous["label"]
        value["runs"][run_id] = row
        try:
            binding.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
            return
        except StaleWorkingDraft:
            continue
    raise StudioError(409, "WORKING_DRAFT_STALE",
                      "The candidate is retained, but the working draft changed concurrently before it was listed. Reopen its result.")


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
