"""Immutable, decision-relevant canonical state.

Raw reasoning transcripts, discarded drafts, and tool traffic are intentionally
absent.  Canonical state may keep stable references to evidence owned elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.refs import ProjectRecordRef, ProjectVersionRef
from archflow.state.commitments import Commitment
from archflow.state.program import BuildingProgram


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _require_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} contains duplicates")


# Compatibility import name.  The identity itself is now project-version
# scoped; runs and branches use archflow.project.RunRef / BranchRef.
StateRef = ProjectVersionRef


@dataclass(frozen=True, slots=True)
class GoalContract:
    """Compatibility/test-only walking-skeleton clauses.

    Production hard authority belongs to authorized typed commitments.  This
    object is retained only so the early fake adapter fixtures stay executable.
    """

    prompt: str
    must: tuple[str, ...]
    prefer: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.prompt, "prompt")
        for name, values in (
            ("must", self.must),
            ("prefer", self.prefer),
            ("forbid", self.forbid),
        ):
            _require_tuple(values, name)
            for value in values:
                _require_text(value, f"{name} item")
            _require_unique(values, name)
        overlap = (set(self.must) & set(self.forbid)) | (
            set(self.prefer) & set(self.forbid)
        )
        if overlap:
            raise ValueError(f"goal clauses conflict: {sorted(overlap)}")


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    value: str
    source_ref: str

    def __post_init__(self) -> None:
        _require_text(self.key, "fact key")
        _require_text(self.value, "fact value")
        _require_text(self.source_ref, "fact source_ref")


@dataclass(frozen=True, slots=True)
class Obligation:
    obligation_id: str
    statement: str
    source_ref: str

    def __post_init__(self) -> None:
        _require_text(self.obligation_id, "obligation_id")
        _require_text(self.statement, "obligation statement")
        _require_text(self.source_ref, "obligation source_ref")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    uri: str
    media_type: str
    sha256: str

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id")
        _require_text(self.uri, "artifact uri")
        _require_text(self.media_type, "artifact media_type")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("artifact sha256 must be a 64-character hex digest")


@dataclass(frozen=True, slots=True)
class CanonicalState:
    ref: ProjectVersionRef
    goal: GoalContract | None = None
    design_program_ref: ProjectRecordRef | None = None
    legacy_program_view: BuildingProgram | None = None
    facts: tuple[Fact, ...] = ()
    commitments: tuple[Commitment, ...] = ()
    open_obligations: tuple[Obligation, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    evaluation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ProjectVersionRef):
            raise TypeError("ref must be a ProjectVersionRef")
        if self.goal is not None and not isinstance(self.goal, GoalContract):
            raise TypeError("goal must be a compatibility GoalContract or None")
        if (
            self.design_program_ref is not None
            and not isinstance(self.design_program_ref, ProjectRecordRef)
        ):
            raise TypeError("design_program_ref must be a ProjectRecordRef or None")
        if (
            self.design_program_ref is not None
            and self.design_program_ref.project_id != self.ref.project_id
        ):
            raise ValueError("design program belongs to another project")
        if (
            self.legacy_program_view is not None
            and not isinstance(self.legacy_program_view, BuildingProgram)
        ):
            raise TypeError(
                "legacy_program_view must be a BuildingProgram compatibility view"
            )
        for name, values in (
            ("facts", self.facts),
            ("commitments", self.commitments),
            ("open_obligations", self.open_obligations),
            ("artifacts", self.artifacts),
            ("evaluation_refs", self.evaluation_refs),
        ):
            _require_tuple(values, name)
        _require_unique(tuple(fact.key for fact in self.facts), "fact keys")
        if any(
            not isinstance(item, Commitment) for item in self.commitments
        ):
            raise TypeError("commitments must contain Commitment values")
        _require_unique(
            tuple(item.commitment_id for item in self.commitments),
            "commitment ids",
        )
        _require_unique(
            tuple(item.obligation_id for item in self.open_obligations),
            "obligation ids",
        )
        _require_unique(
            tuple(item.artifact_id for item in self.artifacts), "artifact ids"
        )
        _require_unique(self.evaluation_refs, "evaluation_refs")


def initialize_canonical_project(
    project_id: str,
    *,
    commitments: tuple[Commitment, ...] = (),
    design_program_ref: ProjectRecordRef | None = None,
) -> CanonicalState:
    """Create production canonical state without fixture goals or design answers."""

    return CanonicalState(
        ref=ProjectVersionRef(project_id=project_id, version=0),
        design_program_ref=design_program_ref,
        commitments=commitments,
    )
