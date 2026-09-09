"""One judgement of the studio, kept: what was asked, what was on the table,
what was decided about each option and why.

The proposal store beside this one answers "what would that sentence do"; the
run records answer "what did the runner build". Neither answers the question an
architect actually asks a week later — *why is it this and not the other one* —
because until now the judgement itself was the one thing nothing retained. An
accepted proposal left a run; a rejected proposal left nothing at all, and the
option that was considered and turned down disappeared with the chat.

A ``DeliberationEpisode`` is that judgement as a value: the intent it answered,
every proposal that was on the table with the decision made on it and the
architect's own sentence for it, what the request asked to keep, the evidence
and validation receipts read before deciding, and the run the accepted one
produced. It is written into that run through ``repository.put_json`` under the
``deliberation-episode`` kind, and it states facts only — it claims no
authority, mints no digest of itself, and is never read back as a substitute
for the records the runner wrote.

**Two lives, and the DTO says which.** A judgement made *before* any run exists
— a reject, a modification — has no run to be written into. It is held in this
process, exactly like the proposal it is about, and it says so: ``persistence``
reads ``in-memory (not version history)``. When this process next runs a
candidate against the same ``stateDigest``, those held episodes are flushed
into that run, and ``persistence`` becomes ``run:<id>``. An episode that never
meets a run is lost on restart, and the store never pretends otherwise.

**Running a candidate is not accepting it.** A candidate is a reversible
preview the architect asked to look at; the studio makes no judgement on the
proposal for having run it, and closes none of the other proposals against the
same base. ``accept`` is the architect's explicit act and is called only when a
person accepts: ``POST /api/proposals/{id}/decision`` with ``accepted`` and the
``candidateId`` of the finished run being chosen. No candidate run calls it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
from typing import Any, Mapping, Sequence
from uuid import uuid4

from archflow.project.ports import (
    PersistenceArea,
    PersistenceDestination,
    RecordSink,
)
from archflow.project.record_kinds import DELIBERATION_EPISODE, STUDIO_WORKING_COPY
from archflow.project.refs import ProjectRecordRef, RunRef

from ..ports import StudioEventSink
from ..transport.errors import StudioError
from .clarification import property_in
from .proposals import PERSISTENCE, Proposal, write_refs_of
from .artifacts import ModelSource, require_model_source
from .binding import ProjectBinding, record_kind
from .projection import project_state
from .pick import COMPONENT_KEY, OBJECT_REF_KEY, OBJECT_REF_PREFIX, element_of_object
from archflow.adapters.three_dm_inspector import inspect_three_dm_contents

SCHEMA = "DeliberationEpisode@1"


@dataclass(frozen=True, slots=True)
class WorkingCopyOption:
    id: str
    label: str
    model_source: ModelSource

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "modelSource": self.model_source.to_dict()}


@dataclass(frozen=True, slots=True)
class WorkingCopy:
    project_id: str
    group_id: str
    label: str
    stage_id: str
    common_base: ModelSource
    scope: tuple[str, ...]
    options: tuple[WorkingCopyOption, ...]
    selected_option_id: str | None
    revision_sha256: str
    base_stage_ref: str | None = None


_working_copy_lock = threading.RLock()


def _working_copy_revisions(binding: ProjectBinding, group_id: str | None = None) -> dict[str, dict[str, Mapping]]:
    groups: dict[str, dict[str, Mapping]] = {}
    for run_id in binding.run_ids():
        for ref in binding.record_refs(run_id):
            if record_kind(ref) != STUDIO_WORKING_COPY:
                continue
            payload = binding.repository.load_json(ref)
            if group_id is not None and payload.get("groupId") != group_id:
                continue
            if payload.get("schema") != "StudioWorkingCopy@1" or payload.get("projectId") != binding.project_id or payload["commonBase"]["runId"] != run_id:
                raise StudioError(409, "WORKING_COPY_CONFLICT", "The work item has an inconsistent common-base binding.")
            groups.setdefault(payload["groupId"], {})[ref.sha256] = payload
    return groups


def _working_copy_from(revisions: Mapping[str, Mapping], revision_sha256: str | None = None) -> WorkingCopy:
    parents = {row.get("previousRevisionSha256") for row in revisions.values()} - {None}
    tips = revisions.keys() - parents
    if not parents.issubset(revisions) or len(tips) != 1:
        raise StudioError(409, "WORKING_COPY_CONFLICT", "The work item has competing or incomplete saved choices.")
    revision = revision_sha256 or next(iter(tips))
    if revision not in revisions:
        raise StudioError(404, "WORKING_COPY_REVISION_NOT_FOUND", "This work item has no such saved revision.")
    row = revisions[revision]
    return WorkingCopy(row["projectId"], row["groupId"], row["label"], row["stageId"],
                       ModelSource.from_dict(row["commonBase"]), tuple(row["scope"]),
                       tuple(WorkingCopyOption(item["id"], item["label"], ModelSource.from_dict(item["modelSource"])) for item in row["options"]),
                       row["selectedOptionId"], revision, row.get("baseStageRef"))


def list_working_copies(binding: ProjectBinding) -> tuple[WorkingCopy, ...]:
    return tuple(_working_copy_from(revisions) for _, revisions in sorted(_working_copy_revisions(binding).items()))


def read_working_copy(binding: ProjectBinding, group_id: str, revision_sha256: str | None = None) -> WorkingCopy:
    revisions = _working_copy_revisions(binding, group_id).get(group_id)
    if not revisions:
        raise StudioError(404, "WORKING_COPY_NOT_FOUND", "No retained work item has that id.")
    return _working_copy_from(revisions, revision_sha256)


def _working_scope(binding: ProjectBinding, base: ModelSource, scope: Sequence[str]) -> tuple[set[str], set[str]]:
    record = project_state(binding, base.run_id).record
    entities = {row.entity_id: row for row in record.entities}
    parameters = {row.key for row in record.parameters}
    entity_ids: set[str] = set()
    parameter_keys: set[str] = set()
    for ref in scope:
        kind, _, value = ref.partition(":")
        if kind in ("entity", "element", "component") and value in entities:
            if kind == "element" and entities[value].schema != "Element@1":
                raise StudioError(422, "WORKING_COPY_SCOPE_INVALID", "The scope element does not name an Element.")
            if kind == "component" and entities[value].schema != "Component@1":
                raise StudioError(422, "WORKING_COPY_SCOPE_INVALID", "The scope component does not name a Component.")
            entity_ids.add(value)
            if entities[value].schema == "Component@1":
                descendants = {value}
                while True:
                    expanded = descendants | {row.entity_id for row in entities.values() if row.parent_id in descendants}
                    if expanded == descendants:
                        break
                    descendants = expanded
                entity_ids.update(descendants)
        elif kind == "parameter" and value in parameters:
            parameter_keys.add(value)
        else:
            raise StudioError(422, "WORKING_COPY_SCOPE_INVALID", "The local scope must name existing record entities, components or parameters.")
    return entity_ids, parameter_keys


def _require_working_option(binding: ProjectBinding, base: ModelSource, scope: Sequence[str], option: WorkingCopyOption) -> None:
    require_model_source(binding, base)
    require_model_source(binding, option.model_source)
    before = project_state(binding, base.run_id).record
    after = project_state(binding, option.model_source.run_id).record
    entity_ids, parameter_keys = _working_scope(binding, base, scope)
    if before.base != after.base:
        raise StudioError(409, "WORKING_COPY_BASE_MISMATCH", "Both options must belong to the same canonical project base.")
    kept_before = {row.entity_id: row for row in before.entities if row.entity_id not in entity_ids}
    kept_after = {row.entity_id: row for row in after.entities if row.entity_id not in entity_ids}
    if kept_before != kept_after or (
        {row.key: row for row in before.parameters if row.key not in parameter_keys} !=
        {row.key: row for row in after.parameters if row.key not in parameter_keys}
    ) or before.relations != after.relations:
        raise StudioError(409, "WORKING_COPY_SCOPE_MISMATCH", "This option changes state outside the declared local scope or its existing relations.")
    _require_scoped_model_assets(binding, base, option.model_source, entity_ids)


def _require_scoped_model_assets(binding: ProjectBinding, base: ModelSource, option: ModelSource, entity_ids: set[str]) -> None:
    before, after = require_model_source(binding, base), require_model_source(binding, option)
    if base.asset_sha256 == option.asset_sha256:
        return
    if before.representation != "composed" or after.representation != "composed":
        raise StudioError(409, "WORKING_COPY_MODEL_INCOMPLETE", "A composed-model work item needs a complete composed option; a native preview cannot replace shared assets.")
    assert before.path is not None and after.path is not None
    inspections = [inspect_three_dm_contents(row.path.read_bytes()) for row in (before, after)]
    if tuple(row.file_sha256 for row in inspections) != (base.asset_sha256, option.asset_sha256):
        raise StudioError(409, "MODEL_SOURCE_MISMATCH", "The model bytes changed during comparison.")
    kept = []
    for source, inspection in zip((base, option), inspections):
        projection = project_state(binding, source.run_id)
        edited = set()
        for row in inspection.object_user_strings:
            strings = {value["key"]: value["value"] for value in row["attributes"]}
            component = strings.get(COMPONENT_KEY)
            object_ref = strings.get(OBJECT_REF_KEY, "")
            name = object_ref[len(OBJECT_REF_PREFIX):] if object_ref.startswith(OBJECT_REF_PREFIX) else row["name"]
            if component and element_of_object(projection, name, component) in entity_ids:
                edited.add(row["object_id"])
        kept.append((
            tuple(row for row in inspection.object_geometry_sha256 if row["object_id"] not in edited),
            tuple(row for row in inspection.object_user_strings if row["object_id"] not in edited),
            inspection.units,
            tuple({key: value for key, value in layer.items() if key != "object_count"} for layer in inspection.layers),
            inspection.instance_definitions, inspection.instance_references, inspection.materials,
            tuple(row for row in inspection.object_material_bindings if row["object_id"] not in edited),
        ))
    if kept[0] != kept[1]:
        raise StudioError(409, "WORKING_COPY_SHARED_MODEL_CHANGED", "This option changes model objects or shared definitions outside the declared local scope.")


def _retain_working_copy(binding: ProjectBinding, item: WorkingCopy, previous: str | None) -> WorkingCopy:
    run = binding.load_run(item.common_base.run_id)
    ref = binding.repository.put_json(
        run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
        record_kind=STUDIO_WORKING_COPY,
        payload={"schema": "StudioWorkingCopy@1", "projectId": item.project_id, "groupId": item.group_id,
                 "label": item.label, "stageId": item.stage_id, "commonBase": item.common_base.to_dict(),
                 "baseStageRef": item.base_stage_ref,
                 "scope": list(item.scope), "options": [option.to_dict() for option in item.options],
                 "selectedOptionId": item.selected_option_id, "previousRevisionSha256": previous},
    )
    return replace(item, revision_sha256=ref.sha256)


def create_working_copy(
    binding: ProjectBinding, group_id: str, label: str, stage_id: str, common_base: ModelSource,
    scope: Sequence[str], options: Sequence[WorkingCopyOption],
) -> WorkingCopy:
    if len({option.id for option in options}) != len(options) or not any(option.model_source == common_base for option in options):
        raise StudioError(422, "WORKING_COPY_OPTIONS_INVALID", "The options need distinct ids and must include the common base unchanged.")
    for option in options:
        _require_working_option(binding, common_base, scope, option)
    with _working_copy_lock:
        if group_id in _working_copy_revisions(binding, group_id):
            raise StudioError(409, "WORKING_COPY_EXISTS", "This work item is already retained.")
        base_stage = project_state(binding, common_base.run_id).source_stage_ref
        return _retain_working_copy(binding, WorkingCopy(binding.project_id, group_id, label, stage_id, common_base,
                                                        tuple(scope), tuple(options), None, "",
                                                        None if base_stage is None else base_stage.uri), None)


def select_working_copy_option(binding: ProjectBinding, group_id: str, base_revision: str, option_id: str) -> WorkingCopy:
    with _working_copy_lock:
        item = read_working_copy(binding, group_id)
        if item.revision_sha256 != base_revision:
            raise StudioError(409, "WORKING_COPY_STALE", "The work item changed. Read its saved options before choosing again.")
        option = next((row for row in item.options if row.id == option_id), None)
        if option is None:
            raise StudioError(404, "WORKING_COPY_OPTION_NOT_FOUND", "This option does not belong to the work item.")
        require_model_source(binding, option.model_source)
        if item.selected_option_id == option_id:
            return item
        return _retain_working_copy(binding, replace(item, selected_option_id=option_id), item.revision_sha256)


def add_working_copy_option(
    binding: ProjectBinding, group_id: str, base_revision: str, option: WorkingCopyOption,
    *, event_sink: StudioEventSink | None = None,
) -> WorkingCopy:
    with _working_copy_lock:
        item = read_working_copy(binding, group_id)
        if item.revision_sha256 != base_revision:
            raise StudioError(409, "WORKING_COPY_STALE", "The work item changed. Read its saved options before adding another.")
        if any(row.id == option.id for row in item.options):
            raise StudioError(409, "WORKING_COPY_OPTION_EXISTS", "An existing option cannot be overwritten.")
        _require_working_option(binding, item.common_base, item.scope, option)
        updated = _retain_working_copy(binding, replace(item, options=(*item.options, option)), item.revision_sha256)
        if event_sink is not None:
            event_sink.publish(event={"type": "working_copy.option_added", "run_id": option.model_source.run_id})
        return updated

# The three decisions a judgement can be. They are the studio's own words for
# what the architect did with one option, and they are closed: a fourth would
# be a second vocabulary for the same act.
ACCEPTED = "accepted"
REJECTED = "rejected"
MODIFIED = "modified"
DECISIONS = (ACCEPTED, REJECTED, MODIFIED)

# The slot a request carries when it says how far a change reaches. It is read
# from the pending intent's ``knownSlots``; a request that carried no scope word
# leaves ``chosenScope`` null rather than being given a default nobody chose.
SCOPE_SLOT = "scope"
SCOPES = ("element", "stack", "datum")



@dataclass(frozen=True, slots=True)
class EpisodeIntent:
    """What was asked, as the studio resolved it — not the chat log.

    ``request_id`` is the pending intent's, when the request came through a
    clarification chain; a sentence already in the grammar opened no pending
    intent and names none.
    """

    utterance: str
    target_component_id: str
    element_id: str | None
    requested_property: str | None
    known_slots: Mapping[str, str]
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance": self.utterance,
            "targetComponentId": self.target_component_id,
            "elementId": self.element_id,
            "requestedProperty": self.requested_property,
            "knownSlots": dict(self.known_slots),
            "requestId": self.request_id,
        }


def intent_of(proposal: Proposal) -> EpisodeIntent:
    """What one proposal was asked for, from the proposal and its pending intent.

    Nothing is re-resolved. When the request came through ``POST /api/intents``
    the pending intent already says which property was named and which slots
    were filled, and those are used verbatim; a sentence sent straight to
    ``POST /api/proposals`` opened no pending intent, so the property is read
    from the utterance with the same table the resolver reads it with, and the
    slots are empty rather than invented.
    """

    pending = proposal.pending
    if pending is None:
        return EpisodeIntent(
            utterance=proposal.utterance,
            target_component_id=proposal.component_id,
            element_id=proposal.element_id,
            requested_property=property_in(proposal.utterance),
            known_slots={},
            request_id=None,
        )
    return EpisodeIntent(
        utterance=pending.original_utterance,
        target_component_id=proposal.component_id,
        element_id=proposal.element_id,
        requested_property=pending.requested_semantic_property,
        known_slots=dict(pending.known_slots),
        request_id=pending.request_id,
    )


@dataclass(frozen=True, slots=True)
class EpisodeChange:
    """The number as the record had it, and the number the option proposed."""

    key: str | None
    old: int | float | None
    new: int | float | None
    semantic_edit: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.semantic_edit is not None:
            return {"kind": "edit_components", **self.semantic_edit}
        return {"key": self.key, "old": self.old, "new": self.new}


@dataclass(frozen=True, slots=True)
class EpisodeProposal:
    """One option that was on the table, and what became of it.

    ``closure`` is the proposal's own — every ref the change invalidates, as
    ``write_refs_of`` computes it. It travels with the decision because what an
    option *would have touched* is half of why it was or was not chosen, and
    the proposal it came from is lost on restart.
    """

    proposal_id: str
    target: str
    change: EpisodeChange
    closure: tuple[str, ...]
    decision: str
    reason: str | None = None
    modified_to: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(
                f"decision must be one of {', '.join(DECISIONS)}, not "
                f"{self.decision!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposalId": self.proposal_id,
            "target": self.target,
            "change": self.change.to_dict(),
            "closure": list(self.closure),
            "decision": self.decision,
            "reason": self.reason,
            "modifiedTo": (
                None if self.modified_to is None else dict(self.modified_to)
            ),
        }


def decided(
    proposal: Proposal,
    decision: str,
    *,
    reason: str | None = None,
    modified_to: Mapping[str, Any] | None = None,
) -> EpisodeProposal:
    """One proposal as the episode holds it, with the decision made on it.

    The closure is taken from the proposal itself rather than recomputed: there
    is one definition of what a change reaches and it is ``write_refs_of``.
    """

    return EpisodeProposal(
        proposal_id=proposal.proposal_id,
        target=proposal.target_ref,
        change=EpisodeChange(
            key=proposal.key, old=proposal.old, new=proposal.new,
            semantic_edit=proposal.semantic_edit,
        ),
        closure=tuple(sorted(write_refs_of(proposal))),
        decision=decision,
        reason=reason,
        modified_to=modified_to,
    )


@dataclass(frozen=True, slots=True)
class DeliberationEpisode:
    """One judgement: the intent, the options, the decisions, and the run.

    ``produced_run`` is the run the accepted proposal made, and it is the only
    thing that separates an episode the project can account for from one this
    process is merely holding. Nothing here is recomputed on read.
    """

    episode_id: str
    project_id: str
    state_digest: str
    intent: EpisodeIntent
    proposals: tuple[EpisodeProposal, ...]
    protected: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    validation_refs: tuple[str, ...]
    created_at: str
    produced_run: str | None = None
    chosen_scope: str | None = None

    @property
    def persistence(self) -> str:
        """Where this episode lives, in the words the wire carries."""

        if self.produced_run is None:
            return PERSISTENCE
        return f"run:{self.produced_run}"

    def to_dict(self) -> dict[str, Any]:
        """The ``DeliberationEpisode@1`` payload, camelCase, facts only."""

        return {
            "schema": SCHEMA,
            "episodeId": self.episode_id,
            "projectId": self.project_id,
            "stateDigest": self.state_digest,
            "intent": self.intent.to_dict(),
            "proposals": [item.to_dict() for item in self.proposals],
            "protected": list(self.protected),
            "evidenceRefs": list(self.evidence_refs),
            "validationRefs": list(self.validation_refs),
            "producedRun": self.produced_run,
            "chosenScope": self.chosen_scope,
            "createdAt": self.created_at,
        }


def episode_id() -> str:
    """``ep-`` and twelve hex: one judgement, named once and never reused."""

    return f"ep-{uuid4().hex[:12]}"


def scope_of(known_slots: Mapping[str, str] | None) -> str | None:
    """The scope word the request carried, or ``None``.

    Read from the slots the resolver already filled; nothing here parses an
    utterance. A slot holding anything but one of the three scope words is not
    a scope, and the episode says nothing rather than saying the wrong thing.
    """

    if not known_slots:
        return None
    value = known_slots.get(SCOPE_SLOT)
    return value if value in SCOPES else None


def open_episode(
    *,
    project_id: str,
    state_digest: str,
    intent: EpisodeIntent,
    proposals: Sequence[EpisodeProposal],
    protected: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    validation_refs: Sequence[str] = (),
    produced_run: str | None = None,
) -> DeliberationEpisode:
    """One judgement, stamped now. The scope comes from the intent's slots."""

    return DeliberationEpisode(
        episode_id=episode_id(),
        project_id=project_id,
        state_digest=state_digest,
        intent=intent,
        proposals=tuple(proposals),
        protected=tuple(protected),
        evidence_refs=tuple(evidence_refs),
        validation_refs=tuple(validation_refs),
        created_at=datetime.now(timezone.utc).isoformat(),
        produced_run=produced_run,
        chosen_scope=scope_of(intent.known_slots),
    )


class EpisodeStore:
    """Every judgement this process has made, in the order it made them.

    A list rather than a dict because order is part of the answer: two
    judgements about one state were made one after the other, and which came
    first is the difference between a reconsideration and a first thought.

    Locked, because held judgements are flushed into a run on a candidate
    worker thread while ``GET /api/episodes`` is answered on the event loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._episodes: list[DeliberationEpisode] = []

    def open(self, episode: DeliberationEpisode) -> DeliberationEpisode:
        """Hold one judgement. Nothing is written; ``retain`` writes."""

        with self._lock:
            self._episodes.append(episode)
        return episode

    def all(self) -> tuple[DeliberationEpisode, ...]:
        with self._lock:
            return tuple(self._episodes)

    def for_state(self, state_digest: str | None) -> tuple[DeliberationEpisode, ...]:
        """Every judgement made against one state, or all of them."""

        if state_digest is None:
            return self.all()
        return tuple(
            episode
            for episode in self.all()
            if episode.state_digest == state_digest
        )

    def decided_proposals(self) -> frozenset[str]:
        """Every proposal this process has already made a judgement about.

        A proposal that was rejected is no longer on the table, and running a
        different one does not reject it a second time.
        """

        return frozenset(
            item.proposal_id
            for episode in self.all()
            for item in episode.proposals
        )

    def get(self, episode_id_: str) -> DeliberationEpisode:
        """One judgement, or a 404 that says where judgements do not survive."""

        for episode in self.all():
            if episode.episode_id == episode_id_:
                return episode
        raise StudioError(
            404,
            "EPISODE_NOT_FOUND",
            f"no deliberation episode {episode_id_} in this process. An "
            f"episode that met no run is held {PERSISTENCE}: one made before "
            "a restart, or by another process, is gone rather than hidden. An "
            "episode that met a run is in that run's records.",
        )

    def retain(
        self,
        repository: RecordSink,
        run: RunRef,
        episode: DeliberationEpisode,
    ) -> ProjectRecordRef:
        """Write one judgement into a run, and remember it as retained.

        The episode is bound to the run it is written into: ``produced_run``
        is set here if it was not already, so what the store hands back and
        what the run holds are the same statement rather than two.
        """

        bound = (
            episode
            if episode.produced_run == run.run_id
            else replace(episode, produced_run=run.run_id)
        )
        ref = repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run.run_id
            ),
            record_kind=DELIBERATION_EPISODE,
            payload=bound.to_dict(),
        )
        with self._lock:
            for index, held in enumerate(self._episodes):
                if held.episode_id == bound.episode_id:
                    self._episodes[index] = bound
                    break
            else:
                self._episodes.append(bound)
        return ref

    def flush(
        self,
        repository: RecordSink,
        run: RunRef,
        state_digest: str,
    ) -> tuple[DeliberationEpisode, ...]:
        """Retain every held judgement about one state into this run.

        A reject and a modification are made before any run exists. They are
        about the same state the accepted proposal ran against, and they are
        the reasons that proposal is the one that ran — so they belong in the
        run that answers for it, not in a process's memory.
        """

        waiting = [
            episode
            for episode in self.all()
            if episode.produced_run is None
            and episode.state_digest == state_digest
        ]
        for episode in waiting:
            self.retain(repository, run, episode)
        return tuple(
            replace(episode, produced_run=run.run_id) for episode in waiting
        )


# ---- the three judgements ---------------------------------------------------


def reject(
    store: EpisodeStore,
    *,
    project_id: str,
    proposal: Proposal,
    reason: str | None = None,
    evidence_refs: Sequence[str] = (),
    validation_refs: Sequence[str] = (),
) -> DeliberationEpisode:
    """The architect turned this option down. Held; no run exists to hold it."""

    return store.open(
        open_episode(
            project_id=project_id,
            state_digest=proposal.base_state_digest,
            intent=intent_of(proposal),
            proposals=(decided(proposal, REJECTED, reason=reason),),
            protected=proposal.protected,
            evidence_refs=evidence_refs,
            validation_refs=validation_refs,
        )
    )


def modify(
    store: EpisodeStore,
    *,
    project_id: str,
    proposal: Proposal,
    replacement: Proposal,
    reason: str | None = None,
    evidence_refs: Sequence[str] = (),
    validation_refs: Sequence[str] = (),
) -> DeliberationEpisode:
    """The architect said something else instead, and it became a proposal.

    ``modified_to`` names the sentence *and* the proposal it compiled to, so
    the judgement is a link between two options rather than a note about one.
    """

    return store.open(
        open_episode(
            project_id=project_id,
            state_digest=proposal.base_state_digest,
            intent=intent_of(proposal),
            proposals=(
                decided(
                    proposal,
                    MODIFIED,
                    reason=reason,
                    modified_to={
                        "utterance": replacement.utterance,
                        "proposalId": replacement.proposal_id,
                    },
                ),
            ),
            protected=proposal.protected,
            evidence_refs=evidence_refs,
            validation_refs=validation_refs,
        )
    )


def accept(
    store: EpisodeStore,
    repository: RecordSink,
    run: RunRef,
    *,
    project_id: str,
    proposal: Proposal,
    reason: str | None = None,
    evidence_refs: Sequence[str] = (),
    validation_refs: Sequence[str] = (),
) -> DeliberationEpisode:
    """Retain the explicit choice; other explorations and alternatives stay open."""

    store.flush(repository, run, proposal.base_state_digest)
    episode = open_episode(
        project_id=project_id,
        state_digest=proposal.base_state_digest,
        intent=intent_of(proposal),
        proposals=(decided(proposal, ACCEPTED, reason=reason),),
        protected=proposal.protected,
        evidence_refs=evidence_refs,
        validation_refs=validation_refs,
        produced_run=run.run_id,
    )
    store.open(episode)
    store.retain(repository, run, episode)
    return episode


def validation_refs_read(
    jobs: Any, validations: Any, proposals: Sequence[Proposal]
) -> tuple[str, ...]:
    """Every validation receipt this process holds for these options.

    A verdict is read before a judgement is made, not after: the architect who
    ran a candidate, saw it violate a relation and then rejected it decided
    *because of* that receipt, and the episode names it. Usually there is none
    — a proposal is often accepted or dropped before anything ran — and an
    empty tuple is that fact, not a missing field.

    ``jobs`` and ``validations`` are the process's registries, read through
    their own accessors. Nothing is computed here and no verdict is triggered.
    """

    found: list[str] = []
    for proposal in proposals:
        for candidate_id in jobs.candidates_of(proposal.proposal_id):
            found.extend(validations.receipt_ids(candidate_id))
    return tuple(sorted(set(found)))


def still_open(
    store: EpisodeStore,
    proposals: Sequence[Proposal],
    *,
    without: str,
) -> tuple[Proposal, ...]:
    """The options this process still holds undecided, minus the one named.

    "Still open" is the whole of the rule: a proposal already rejected is not
    rejected again, and a proposal already superseded by an earlier acceptance
    is not superseded twice by a later one. Running a candidate decides
    nothing, so a proposal that has been run is still open.
    """

    decided_ids = store.decided_proposals()
    return tuple(
        proposal
        for proposal in proposals
        if proposal.proposal_id != without
        and proposal.proposal_id not in decided_ids
    )
