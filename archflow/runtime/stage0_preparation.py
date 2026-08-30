"""Compile and durably prepare one data-driven Stage 0 controller branch.

The declaration is deliberately building-agnostic.  It accepts only explicit
typed state members and exact P036 JSON record references; it does not infer a
brief, select a design, or advance canonical ``HEAD``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol

from archflow.project.digests import canonical_json_sha256
from archflow.project.manifest import ProjectManifest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.runtime.design_controller import (
    ControllerStatus,
    DesignControllerCheckpoint,
    ProjectControllerArchiveAdapter,
)
from archflow.runtime.event_log import DesignEvent, EventDecision
from archflow.state.commitments import Commitment
from archflow.state.design_maturity import DesignMaturityState, DesignPhase
from archflow.state.design_state import (
    DesignStateLayer,
    DesignStateNode,
    DesignStateTree,
    StatePath,
    StatePathSegment,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DesignObligation,
    OperationalMarkovState,
    ParameterBinding,
    StateFact,
    StateLock,
    require_local_id,
    require_logical_ref,
)


STAGE0_DECLARATION_SCHEMA = "Stage0Declaration@1"
STAGE0_INITIALIZATION_SCHEMA = "Stage0Initialization@1"
STAGE0_COMPILER_VERSION = "stage0-declaration-compiler-1"
_MAX_ITEMS = 4096


class Stage0PreparationError(ValueError):
    """A declaration cannot initialize the selected exact P036 branch."""


class Stage0Repository(Protocol):
    """P036 surface required by the Stage 0 preparation orchestrator."""

    def load_manifest(self) -> ProjectManifest: ...

    def load_run(self, run_id: str) -> RunRef: ...

    def read_head(self) -> ProjectVersionRef: ...

    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef: ...

    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...

    def list_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> tuple[ProjectRecordRef, ...]: ...


def _exact_mapping(
    value: object,
    expected: set[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise Stage0PreparationError(f"{field} schema drifted")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if len(value) > _MAX_ITEMS:
        raise Stage0PreparationError(f"{field} exceeds bounded item count")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must be a string list")
    return tuple(values)


def _version_to_dict(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.require_digest(),
    }


def _version_from_dict(value: object, field: str) -> ProjectVersionRef:
    payload = _exact_mapping(
        value,
        {"project_id", "version", "state_sha256"},
        field,
    )
    try:
        return ProjectVersionRef(
            project_id=payload["project_id"],
            version=payload["version"],
            state_sha256=payload["state_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise Stage0PreparationError(f"{field} is invalid") from exc


def _branch_to_dict(value: BranchRef) -> dict[str, object]:
    return {
        "project_id": value.run.project_id,
        "run_id": value.run.run_id,
        "branch_id": value.branch_id,
        "epoch": value.epoch,
        "base": _version_to_dict(value.run.base),
    }


def _branch_from_dict(value: object) -> BranchRef:
    payload = _exact_mapping(
        value,
        {"project_id", "run_id", "branch_id", "epoch", "base"},
        "stage0 branch",
    )
    base = _version_from_dict(payload["base"], "stage0 branch base")
    if base.project_id != payload["project_id"]:
        raise Stage0PreparationError(
            "stage0 branch and base belong to different projects"
        )
    try:
        return BranchRef(
            run=RunRef(
                project_id=payload["project_id"],
                run_id=payload["run_id"],
                base=base,
            ),
            branch_id=payload["branch_id"],
            epoch=payload["epoch"],
        )
    except (TypeError, ValueError) as exc:
        raise Stage0PreparationError("stage0 branch is invalid") from exc


def _record_to_dict(value: ProjectRecordRef) -> dict[str, str]:
    return {
        "project_id": value.project_id,
        "relative_path": value.relative_path,
        "sha256": value.sha256,
        "media_type": value.media_type,
    }


def _record_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    try:
        return ProjectRecordRef(
            project_id=payload["project_id"],
            relative_path=payload["relative_path"],
            sha256=payload["sha256"],
            media_type=payload["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise Stage0PreparationError(f"{field} is invalid") from exc


@dataclass(frozen=True, slots=True)
class Stage0Declaration:
    """Explicit Stage 0 state declaration bound to one exact branch epoch."""

    branch: BranchRef
    phase: str
    root_node_id: str
    allowed_authority_ids: tuple[str, ...]
    facts: tuple[StateFact, ...]
    bindings: tuple[ParameterBinding, ...]
    locks: tuple[StateLock, ...]
    commitments: tuple[Commitment, ...]
    obligations: tuple[DesignObligation, ...]
    dependencies: tuple[DependencyEdge, ...]
    invalidated_refs: tuple[str, ...]
    evidence_refs: tuple[ProjectRecordRef, ...]
    actor_id: str
    authority_id: str | None
    max_iterations: int

    SCHEMA = STAGE0_DECLARATION_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        if self.branch.epoch != 0:
            raise Stage0PreparationError(
                "Stage 0 requires branch epoch zero"
            )
        if self.phase != DesignPhase.RESEARCH_BRIEF.value:
            raise Stage0PreparationError(
                "Stage 0 phase must be research_brief"
            )
        require_local_id(self.root_node_id, "root_node_id")
        if not isinstance(self.allowed_authority_ids, tuple):
            raise TypeError("allowed_authority_ids must be a tuple")
        if not self.allowed_authority_ids:
            raise Stage0PreparationError(
                "Stage 0 root requires at least one mutation authority"
            )
        for authority in self.allowed_authority_ids:
            require_local_id(authority, "allowed_authority_id")
        if len(self.allowed_authority_ids) != len(
            set(self.allowed_authority_ids)
        ):
            raise Stage0PreparationError(
                "allowed_authority_ids contains duplicates"
            )
        object.__setattr__(
            self,
            "allowed_authority_ids",
            tuple(sorted(self.allowed_authority_ids)),
        )

        typed_collections = (
            ("facts", self.facts, StateFact),
            ("bindings", self.bindings, ParameterBinding),
            ("locks", self.locks, StateLock),
            ("commitments", self.commitments, Commitment),
            ("obligations", self.obligations, DesignObligation),
            ("dependencies", self.dependencies, DependencyEdge),
        )
        for field, values, item_type in typed_collections:
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(
                    f"{field} must contain {item_type.__name__} values"
                )
        if not isinstance(self.invalidated_refs, tuple):
            raise TypeError("invalidated_refs must be a tuple")
        for ref in self.invalidated_refs:
            require_logical_ref(ref, "invalidated_ref")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        if not self.evidence_refs:
            raise Stage0PreparationError(
                "Stage 0 declaration requires exact P036 evidence refs"
            )
        if any(
            not isinstance(item, ProjectRecordRef)
            for item in self.evidence_refs
        ):
            raise TypeError(
                "evidence_refs must contain ProjectRecordRef values"
            )
        identities = tuple(
            (item.uri, item.sha256, item.media_type)
            for item in self.evidence_refs
        )
        if len(identities) != len(set(identities)):
            raise Stage0PreparationError("evidence_refs contains duplicates")
        locations = tuple(
            (item.project_id, item.relative_path)
            for item in self.evidence_refs
        )
        if len(locations) != len(set(locations)):
            raise Stage0PreparationError(
                "evidence_refs contains conflicting digests for one path"
            )
        if any(
            item.project_id != self.branch.run.project_id
            for item in self.evidence_refs
        ):
            raise Stage0PreparationError(
                "evidence ref belongs to another project"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                sorted(
                    self.evidence_refs,
                    key=lambda item: (
                        item.uri,
                        item.sha256,
                        item.media_type,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "facts",
            tuple(sorted(self.facts, key=lambda item: item.ref)),
        )
        object.__setattr__(
            self,
            "bindings",
            tuple(sorted(self.bindings, key=lambda item: item.ref)),
        )
        object.__setattr__(
            self,
            "locks",
            tuple(sorted(self.locks, key=lambda item: item.target_ref)),
        )
        object.__setattr__(
            self,
            "commitments",
            tuple(
                sorted(
                    self.commitments,
                    key=lambda item: item.commitment_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "obligations",
            tuple(
                sorted(
                    self.obligations,
                    key=lambda item: item.obligation_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    self.dependencies,
                    key=lambda item: item.identity,
                )
            ),
        )
        object.__setattr__(
            self,
            "invalidated_refs",
            tuple(sorted(self.invalidated_refs)),
        )

        require_identifier(self.actor_id, "actor_id")
        if self.authority_id is not None:
            require_identifier(self.authority_id, "authority_id")
            if self.authority_id not in self.allowed_authority_ids:
                raise Stage0PreparationError(
                    "event authority is absent from root mutation authorities"
                )
        if (
            not isinstance(self.max_iterations, int)
            or isinstance(self.max_iterations, bool)
            or self.max_iterations < 1
        ):
            raise Stage0PreparationError(
                "max_iterations must be a positive integer"
            )

        evidence_uris = {item.uri for item in self.evidence_refs}
        declared_sources = {
            *(item.source_ref for item in self.facts),
            *(item.source_ref for item in self.bindings),
            *(item.source_ref for item in self.locks),
            *(item.source_ref for item in self.obligations),
            *(item.source_ref for item in self.dependencies),
            *(item.source_event_ref for item in self.commitments),
            *(
                ref
                for item in self.commitments
                for ref in item.evidence_refs
            ),
        }
        unknown_sources = declared_sources - evidence_uris
        if unknown_sources:
            raise Stage0PreparationError(
                "state sources lack exact P036 evidence refs: "
                f"{sorted(unknown_sources)}"
            )

        # Construct once during declaration validation so collection-level
        # invariants (duplicates, blockers, cycles, readiness) fail before IO.
        self.compile_operational_state()

    @property
    def declaration_digest(self) -> str:
        return canonical_json_sha256(self.to_dict())

    def compile_operational_state(
        self,
        *,
        declaration_ref: ProjectRecordRef | None = None,
    ) -> OperationalMarkovState:
        evidence_refs = [item.uri for item in self.evidence_refs]
        if declaration_ref is not None:
            if declaration_ref.project_id != self.branch.run.project_id:
                raise Stage0PreparationError(
                    "declaration record belongs to another project"
                )
            evidence_refs.append(declaration_ref.uri)
        return OperationalMarkovState(
            branch=self.branch,
            compiler_version=STAGE0_COMPILER_VERSION,
            phase=self.phase,
            facts=self.facts,
            bindings=self.bindings,
            locks=self.locks,
            commitments=self.commitments,
            obligations=self.obligations,
            dependencies=self.dependencies,
            invalidated_refs=self.invalidated_refs,
            evidence_refs=tuple(sorted(set(evidence_refs))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch": _branch_to_dict(self.branch),
            "phase": self.phase,
            "root_node_id": self.root_node_id,
            "allowed_authority_ids": list(self.allowed_authority_ids),
            "facts": [item.to_dict() for item in self.facts],
            "bindings": [item.to_dict() for item in self.bindings],
            "locks": [item.to_dict() for item in self.locks],
            "commitments": [item.to_dict() for item in self.commitments],
            "obligations": [item.to_dict() for item in self.obligations],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "invalidated_refs": list(self.invalidated_refs),
            "evidence_refs": [
                _record_to_dict(item) for item in self.evidence_refs
            ],
            "actor_id": self.actor_id,
            "authority_id": self.authority_id,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, value: object) -> "Stage0Declaration":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "branch",
                "phase",
                "root_node_id",
                "allowed_authority_ids",
                "facts",
                "bindings",
                "locks",
                "commitments",
                "obligations",
                "dependencies",
                "invalidated_refs",
                "evidence_refs",
                "actor_id",
                "authority_id",
                "max_iterations",
            },
            "stage0 declaration",
        )
        if payload["schema"] != cls.SCHEMA:
            raise Stage0PreparationError(
                "unsupported Stage 0 declaration schema"
            )
        return cls(
            branch=_branch_from_dict(payload["branch"]),
            phase=payload["phase"],
            root_node_id=payload["root_node_id"],
            allowed_authority_ids=_strings(
                payload["allowed_authority_ids"],
                "allowed_authority_ids",
            ),
            facts=tuple(
                StateFact.from_dict(item)
                for item in _list(payload["facts"], "facts")
            ),
            bindings=tuple(
                ParameterBinding.from_dict(item)
                for item in _list(payload["bindings"], "bindings")
            ),
            locks=tuple(
                StateLock.from_dict(item)
                for item in _list(payload["locks"], "locks")
            ),
            commitments=tuple(
                Commitment.from_dict(item)
                for item in _list(payload["commitments"], "commitments")
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(payload["obligations"], "obligations")
            ),
            dependencies=tuple(
                DependencyEdge.from_dict(item)
                for item in _list(payload["dependencies"], "dependencies")
            ),
            invalidated_refs=_strings(
                payload["invalidated_refs"],
                "invalidated_refs",
            ),
            evidence_refs=tuple(
                _record_from_dict(item, "evidence_ref")
                for item in _list(payload["evidence_refs"], "evidence_refs")
            ),
            actor_id=payload["actor_id"],
            authority_id=payload["authority_id"],
            max_iterations=payload["max_iterations"],
        )


@dataclass(frozen=True, slots=True)
class Stage0Compilation:
    operational_state: OperationalMarkovState
    tree: DesignStateTree
    event: DesignEvent
    checkpoint: DesignControllerCheckpoint


@dataclass(frozen=True, slots=True)
class Stage0PreparationResult:
    declaration_ref: ProjectRecordRef
    event_ref: ProjectRecordRef
    checkpoint_ref: ProjectRecordRef
    compilation: Stage0Compilation
    canonical_head: ProjectVersionRef

    SCHEMA = "Stage0PreparationResult@1"

    def to_dict(self) -> dict[str, object]:
        branch = self.compilation.tree.branch
        return {
            "schema": self.SCHEMA,
            "project_id": branch.run.project_id,
            "run_id": branch.run.run_id,
            "branch_id": branch.branch_id,
            "epoch": branch.epoch,
            "phase": self.compilation.operational_state.phase,
            "operational_state_digest": (
                self.compilation.operational_state.state_digest
            ),
            "tree_digest": self.compilation.tree.tree_digest,
            "event_id": self.compilation.event.event_id,
            "checkpoint_digest": (
                self.compilation.checkpoint.checkpoint_digest
            ),
            "declaration_ref": _record_to_dict(self.declaration_ref),
            "event_ref": _record_to_dict(self.event_ref),
            "checkpoint_ref": _record_to_dict(self.checkpoint_ref),
            "canonical_head": _version_to_dict(self.canonical_head),
            "canonical_head_unchanged": True,
        }


def compile_stage0_declaration(
    declaration: Stage0Declaration,
    *,
    declaration_ref: ProjectRecordRef,
) -> Stage0Compilation:
    """Purely compile one explicit declaration into Stage 0 controller state."""

    if not isinstance(declaration, Stage0Declaration):
        raise TypeError("declaration must be a Stage0Declaration")
    if not isinstance(declaration_ref, ProjectRecordRef):
        raise TypeError("declaration_ref must be a ProjectRecordRef")
    state = declaration.compile_operational_state(
        declaration_ref=declaration_ref
    )
    path = StatePath(
        (
            StatePathSegment(
                DesignStateLayer.GLOBAL_CONCEPT,
                declaration.root_node_id,
            ),
        )
    )
    root = DesignStateNode(
        path=path,
        operational_state=state,
        allowed_authority_ids=declaration.allowed_authority_ids,
    )
    tree = DesignStateTree(
        branch=declaration.branch,
        nodes=(root,),
    )
    evidence_refs = tuple(
        sorted(
            {
                declaration_ref.uri,
                *(item.uri for item in declaration.evidence_refs),
            }
        )
    )
    event = DesignEvent.create(
        sequence=0,
        project_id=declaration.branch.run.project_id,
        event_type="stage0.initialized",
        decision=EventDecision.OBSERVED,
        actor_id=declaration.actor_id,
        authority_id=declaration.authority_id,
        prior_event_sha256=None,
        prior_state=None,
        proposed_delta={
            "schema": STAGE0_INITIALIZATION_SCHEMA,
            "declaration_digest": declaration.declaration_digest,
            "declaration_ref": _record_to_dict(declaration_ref),
            "branch": _branch_to_dict(declaration.branch),
            "operational_state_digest": state.state_digest,
            "tree_digest": tree.tree_digest,
            "target_node_ref": root.ref,
        },
        evidence_refs=evidence_refs,
        validation_receipt_refs=(),
        commit_receipt_ref=None,
        artifact_refs=(),
        reducer_version=STAGE0_COMPILER_VERSION,
        resulting_state=declaration.branch.run.base,
    )
    checkpoint = DesignControllerCheckpoint(
        tree=tree,
        target_node_ref=root.ref,
        maturity=DesignMaturityState.from_operational_state(state),
        status=ControllerStatus.READY,
        iteration=0,
        max_iterations=declaration.max_iterations,
        history_event_refs=(event.event_id,),
        decision_context_refs=evidence_refs,
    )
    return Stage0Compilation(
        operational_state=state,
        tree=tree,
        event=event,
        checkpoint=checkpoint,
    )


def _require_exact_evidence_record(
    repository: Stage0Repository,
    ref: ProjectRecordRef,
    *,
    branch: BranchRef,
) -> None:
    if ref.project_id != branch.run.project_id:
        raise Stage0PreparationError(
            "evidence record belongs to another project"
        )
    if ref.media_type != "application/json":
        raise Stage0PreparationError(
            "Stage 0 evidence must be an exact P036 JSON record"
        )
    parts = PurePosixPath(ref.relative_path).parts
    run_prefix = ("runs", branch.run.run_id)
    branch_prefix = (
        "runs",
        branch.run.run_id,
        "branches",
        branch.branch_id,
        "records",
    )
    allowed = (
        parts[:1] == ("input",)
        or parts[:3] == (*run_prefix, "records")
        or parts[:5] == branch_prefix
    )
    if not allowed:
        if parts[:1] == ("runs",):
            raise Stage0PreparationError(
                "evidence record crosses the selected run or branch"
            )
        raise Stage0PreparationError(
            "evidence record is outside project input/current run/current branch"
        )
    payload = repository.load_json(ref)
    if (
        "project_id" in payload
        and payload["project_id"] != branch.run.project_id
    ):
        raise Stage0PreparationError(
            "evidence payload belongs to another project"
        )
    # Project-global inputs may omit run identity.  If they declare one, it is
    # still authoritative content and must match the selected exact run/base.
    if "run_id" in payload and payload["run_id"] != branch.run.run_id:
        raise Stage0PreparationError(
            "evidence payload belongs to another run"
        )
    if (
        "run_base" in payload
        and _version_from_dict(
            payload["run_base"],
            "evidence payload run_base",
        ) != branch.run.base
    ):
        raise Stage0PreparationError(
            "evidence payload is bound to another or historical run base"
        )
    branch_scoped_path = parts[:5] == branch_prefix
    declares_branch = (
        "branch_id" in payload or "branch_epoch" in payload
    )
    if branch_scoped_path or declares_branch:
        if "branch_id" not in payload or "branch_epoch" not in payload:
            raise Stage0PreparationError(
                "branch-scoped evidence payload requires branch_id and branch_epoch"
            )
    # A run-level or project-input record may still carry a branch identity.
    # Its wider storage location must not let that identity bypass the selected
    # branch, while branch records must prove the otherwise path-opaque epoch.
    if (
        "branch_id" in payload
        and payload["branch_id"] != branch.branch_id
    ):
        raise Stage0PreparationError(
            "evidence payload belongs to another branch"
        )
    if (
        "branch_epoch" in payload
    ):
        branch_epoch = payload["branch_epoch"]
        if (
            not isinstance(branch_epoch, int)
            or isinstance(branch_epoch, bool)
            or branch_epoch < 0
        ):
            raise Stage0PreparationError(
                "evidence payload branch epoch is invalid"
            )
        if branch_epoch != branch.epoch:
            raise Stage0PreparationError(
                "evidence payload belongs to another branch epoch"
            )


def _find_exact_record(
    repository: Stage0Repository,
    *,
    branch: BranchRef,
    payload: Mapping[str, Any],
) -> ProjectRecordRef:
    destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=branch.run.run_id,
        branch_id=branch.branch_id,
    )
    matches = tuple(
        ref
        for ref in repository.list_json(
            run=branch.run,
            destination=destination,
        )
        if repository.load_json(ref) == dict(payload)
    )
    if len(matches) != 1:
        raise Stage0PreparationError(
            "branch archive does not contain exactly one expected record"
        )
    return matches[0]


def prepare_stage0_declaration(
    repository: Stage0Repository,
    declaration: Stage0Declaration,
) -> Stage0PreparationResult:
    """Persist an initial event/checkpoint without mutating canonical HEAD.

    Replaying the identical declaration returns the same content-addressed
    references.  Any pre-existing different event, evolved history, stale run
    base, or cross-scope evidence fails before declaration persistence.
    """

    if not isinstance(declaration, Stage0Declaration):
        raise TypeError("declaration must be a Stage0Declaration")
    branch = declaration.branch
    manifest = repository.load_manifest()
    if manifest.project_id != branch.run.project_id:
        raise Stage0PreparationError(
            "repository belongs to another project"
        )
    durable_run = repository.load_run(branch.run.run_id)
    if durable_run != branch.run:
        raise Stage0PreparationError(
            "declaration does not match the durable run exact base"
        )
    head_before = repository.read_head()
    if head_before != branch.run.base:
        raise Stage0PreparationError(
            "historical run base cannot initialize Stage 0"
        )
    for ref in declaration.evidence_refs:
        _require_exact_evidence_record(
            repository,
            ref,
            branch=branch,
        )

    adapter = ProjectControllerArchiveAdapter(
        repository,
        branch=branch,
    )
    existing_events = adapter.event_log.records()
    if len(existing_events) > 1:
        raise Stage0PreparationError(
            "Stage 0 branch already has evolved event history"
        )
    if existing_events:
        prior = existing_events[0].proposed_delta
        if (
            prior.get("schema") != STAGE0_INITIALIZATION_SCHEMA
            or prior.get("declaration_digest")
            != declaration.declaration_digest
            or prior.get("branch") != _branch_to_dict(branch)
        ):
            raise Stage0PreparationError(
                "Stage 0 branch already contains another initialization"
            )

    destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=branch.run.run_id,
        branch_id=branch.branch_id,
    )
    declaration_ref = repository.put_json(
        run=branch.run,
        destination=destination,
        record_kind="stage0-declaration",
        payload=declaration.to_dict(),
    )
    if repository.load_json(declaration_ref) != declaration.to_dict():
        raise Stage0PreparationError(
            "retained Stage 0 declaration changed during persistence"
        )
    compilation = compile_stage0_declaration(
        declaration,
        declaration_ref=declaration_ref,
    )
    if existing_events:
        if existing_events[0] != compilation.event:
            raise Stage0PreparationError(
                "retained Stage 0 event differs from deterministic replay"
            )
    else:
        adapter.event_log.append(compilation.event)

    checkpoint_ref = adapter.save_checkpoint(compilation.checkpoint)
    resumed = adapter.load_latest_checkpoint()
    if (
        resumed.record_ref != checkpoint_ref
        or resumed.checkpoint != compilation.checkpoint
        or resumed.event_chain != (compilation.event,)
    ):
        raise Stage0PreparationError(
            "retained Stage 0 checkpoint failed exact latest readback"
        )
    event_ref = _find_exact_record(
        repository,
        branch=branch,
        payload=compilation.event.to_dict(),
    )
    if repository.read_head() != head_before:
        raise Stage0PreparationError(
            "canonical HEAD changed during Stage 0 preparation"
        )
    return Stage0PreparationResult(
        declaration_ref=declaration_ref,
        event_ref=event_ref,
        checkpoint_ref=checkpoint_ref,
        compilation=compilation,
        canonical_head=head_before,
    )


__all__ = [
    "STAGE0_COMPILER_VERSION",
    "STAGE0_DECLARATION_SCHEMA",
    "Stage0Compilation",
    "Stage0Declaration",
    "Stage0PreparationError",
    "Stage0PreparationResult",
    "compile_stage0_declaration",
    "prepare_stage0_declaration",
]
