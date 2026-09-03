"""The authored State Record, bound to a run and projected for one screen.

Everything on this page is the kernel's answer. The record is parsed by
``StateRecord.from_dict`` and attached to its base by ``bound_to`` — never by a
hand-built base — the component tree comes from ``design_components_of``, the
edges from ``StateRecord.dependency_edges`` and the digest from the same
``developed_design_view`` the project runner uses, so a projection and a run
receipt name the same number or the difference is stated out loud.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.operational_state import DependencyEdge
from archflow.state.spatial import DesignComponent
from archflow.state.state_record import (
    Parameter,
    StageBinding,
    StateRecord,
    StateRecordError,
    design_components_of,
    developed_design_view,
)

from ..transport.errors import NotFound
from .binding import ProjectBinding, ReferenceRun

# Where a project keeps the record the runner executes. It is authored input,
# not a retained record, so it is read by path and never written by the API.
RUNNER_RECORD_PATH = "input/runner/state-record.json"

# The project runner's own view kwargs. Changing any of them turns
# ``stateDigest`` into a number no receipt carries.
PORTFOLIO_ID = "declared-schematic"
BRANCH_ID = "runner-v1"
SELECTION_DECISION_REF = "decision:declared-schematic-selection"


@dataclass(frozen=True, slots=True)
class ProjectedElement:
    """One ``Element@1`` row, with the scalars the intent grammar can target."""

    element_id: str
    component_id: str
    producer: str
    numeric_fields: Mapping[str, int | float]


@dataclass(frozen=True, slots=True)
class StateProjection:
    """One request's answer: the bound record and everything read off it."""

    project_id: str
    head: ProjectVersionRef
    run: RunRef
    reference: ReferenceRun
    record: StateRecord
    state: DevelopedDesignState
    authored_record_digest: str
    matches_reference_receipt: bool | None
    components: tuple[DesignComponent, ...] | None
    component_tree_error: str | None
    elements: tuple[ProjectedElement, ...]
    parameters: tuple[Parameter, ...]
    edges: tuple[DependencyEdge, ...]
    honesty: tuple[str, ...]

    @property
    def record_digest(self) -> str:
        """The bound record's content digest."""

        return self.record.digest

    @property
    def state_digest(self) -> str:
        """The developed-design digest a runner receipt carries."""

        return self.state.state_digest

    @property
    def reference_receipt(self) -> Mapping[str, Any] | None:
        return self.reference.receipt

    @property
    def stage(self) -> StageBinding:
        return self.record.stage


def project_state(
    binding: ProjectBinding,
    run_id: str | None = None,
) -> StateProjection:
    """Project the authored record against one run and the project's exact HEAD."""

    reference = binding.reference_run(run_id)
    head = binding.head()
    authored = _load_authored_record(binding)
    # The one sanctioned binding: the record attaches itself to the run.
    run = RunRef(binding.project_id, reference.run.run_id, head)
    record = authored.bound_to(run)
    state = developed_design_view(
        record,
        run=run,
        portfolio_id=PORTFOLIO_ID,
        branch_id=BRANCH_ID,
        selection_decision_ref=SELECTION_DECISION_REF,
    )
    components, component_tree_error = _component_tree(record)
    edges = record.dependency_edges()
    # Three states, not two: a receipt that names no digest leaves the
    # comparison unchecked, and unchecked is never reported as a mismatch.
    claimed = (
        None
        if reference.receipt is None
        else reference.receipt.get("design_state_digest")
    )
    matches = (
        state.state_digest == claimed if isinstance(claimed, str) else None
    )
    return StateProjection(
        project_id=binding.project_id,
        head=head,
        run=run,
        reference=reference,
        record=record,
        state=state,
        authored_record_digest=authored.digest,
        matches_reference_receipt=matches,
        components=components,
        component_tree_error=component_tree_error,
        elements=_elements(record),
        parameters=record.parameters,
        edges=edges,
        honesty=_honesty(
            record,
            edges=edges,
            reference=reference,
            matches=matches,
            run_id=run.run_id,
        ),
    )


def _load_authored_record(binding: ProjectBinding) -> StateRecord:
    path = binding.repository.layout.resolve_relative(RUNNER_RECORD_PATH)
    if not path.is_file():
        raise NotFound(
            "STATE_RECORD_NOT_FOUND",
            f"{binding.project_id}: no authored state record at "
            f"{RUNNER_RECORD_PATH} under {binding.project_dir}",
        )
    return StateRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _component_tree(
    record: StateRecord,
) -> tuple[tuple[DesignComponent, ...] | None, str | None]:
    """The kernel's component tree, or the reason it could not build one."""

    try:
        return design_components_of(record), None
    except StateRecordError as exc:
        return None, str(exc)


def _elements(record: StateRecord) -> tuple[ProjectedElement, ...]:
    return tuple(
        ProjectedElement(
            element_id=entity.entity_id,
            component_id=entity.fields["component_id"],
            producer=entity.fields["producer"],
            numeric_fields=_numeric_fields(entity.fields),
        )
        for entity in record.entities_of("Element@1")
    )


def _numeric_fields(fields: Mapping[str, Any]) -> dict[str, int | float]:
    """The scalar producer params; a profile is a list of points, not a number."""

    params = fields.get("params") or {}
    return {
        key: value
        for key, value in params.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _honesty(
    record: StateRecord,
    *,
    edges: tuple[DependencyEdge, ...],
    reference: ReferenceRun,
    matches: bool | None,
    run_id: str,
) -> tuple[str, ...]:
    """The lines the UI shows verbatim: what this projection cannot tell you."""

    lines: list[str] = []
    if not record.parameters:
        lines.append(
            "0 parameters declared: parameter intents will be "
            "BLOCKED_NEEDS_HUMAN"
        )
    if not record.relations:
        lines.append(
            "0 relations declared: relation checks are unchecked by "
            "construction"
        )
    if not edges:
        lines.append("0 dependency edges: impact closure is direct-only")
    stage = record.stage
    if (
        stage.workflow_ref is None
        and stage.envelope_ref is None
        and stage.stage_id is None
    ):
        lines.append("no stage binding on the authored record")
    if reference.source == "none":
        lines.append(
            "no eligible reference run: projection bound to the studio run "
            "id; its digests are not comparable to any receipt"
        )
    if matches is False:
        lines.append(
            "projection digest differs from the reference receipt: the "
            f"authored record is not what run {run_id} executed"
        )
    return tuple(lines)
