"""The authored State Record, bound to a run and projected for one screen.

Everything on this page is the kernel's answer. The record is parsed by
``StateRecord.from_dict`` and attached to its base by ``bound_to`` — never by a
hand-built base — the component tree comes from ``design_components_of``, the
edges from ``StateRecord.dependency_edges`` and the digest from the same
``developed_design_view`` the project runner uses, so a projection and a run
receipt name the same number or the difference is stated out loud.

Two identities travel, not three: ``record.digest`` is the record's content and
``state.state_digest`` is that content bound to a run. Neither is computed here.
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

from ..transport.errors import StudioError, error_sentence
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
    # ``None`` when the kernel refused to build the bound view. Only
    # ``GET /api/state`` is served such a projection; see ``project_state``.
    state: DevelopedDesignState | None
    matches_reference_receipt: bool | None
    components: tuple[DesignComponent, ...] | None
    component_tree_error: str | None
    elements: tuple[ProjectedElement, ...]
    parameters: tuple[Parameter, ...]
    edges: tuple[DependencyEdge, ...]
    honesty: tuple[str, ...]

    @property
    def record_digest(self) -> str:
        """Content identity: what the record says, invariant under binding."""

        return self.record.digest

    @property
    def state_digest(self) -> str | None:
        """Binding identity: the digest runner receipts carry.

        ``None`` when there is no bound view to take it from. A record the
        kernel would not view has no number a receipt could cite, and saying
        so is the only honest answer: any value here would be one the client
        could compare, and nothing produced it.
        """

        return None if self.state is None else self.state.state_digest

    @property
    def reference_receipt(self) -> Mapping[str, Any] | None:
        return self.reference.receipt

    @property
    def stage(self) -> StageBinding:
        return self.record.stage


def project_state(
    binding: ProjectBinding,
    run_id: str | None = None,
    *,
    require_view: bool = True,
) -> StateProjection:
    """Project the authored record against one run and the project's exact HEAD.

    Everything after the record is parsed is still the record's own fault when
    it fails: the kernel validates no per-schema entity fields, so a record can
    parse and then refuse to be read or to be viewed. Those failures arrive as
    ``422 STATE_RECORD_INVALID`` naming the file, never as a bare 500 about a
    record an operator authored.

    ``require_view`` is the one place the two kinds of caller differ.
    ``GET /api/state`` shows what the record *declares* and names what the
    kernel refused, so it asks with ``require_view=False`` and is given a
    projection with no bound view. Every other caller — a pick, a proposal,
    an impact, a candidate — is asking a question *about* that view, and
    answering it from a record that has none would be a guess; the default is
    therefore the refusal, so a caller added later inherits it instead of
    having to remember it.
    """

    reference = binding.reference_run(run_id)
    head = binding.head()
    authored = _load_authored_record(binding)
    # The one sanctioned binding: the record attaches itself to the run.
    run = RunRef(binding.project_id, reference.run.run_id, head)
    record = authored.bound_to(run)
    try:
        state, components, component_tree_error = _bound_view(
            record, run, require_view=require_view
        )
        edges = record.dependency_edges()
        elements = _elements(record)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        # A record that parsed and cannot be read is the operator's to fix,
        # and the sentence that says which field is the one worth repeating.
        raise _record_invalid(exc) from exc
    # Three states, not two: a receipt that names no digest leaves the
    # comparison unchecked, and unchecked is never reported as a mismatch. A
    # projection with no bound view has no digest to compare either.
    claimed = (
        None
        if reference.receipt is None
        else reference.receipt.get("design_state_digest")
    )
    matches = (
        state.state_digest == claimed
        if state is not None and isinstance(claimed, str)
        else None
    )
    return StateProjection(
        project_id=binding.project_id,
        head=head,
        run=run,
        reference=reference,
        record=record,
        state=state,
        matches_reference_receipt=matches,
        components=components,
        component_tree_error=component_tree_error,
        elements=elements,
        parameters=record.parameters,
        edges=edges,
        honesty=_honesty(
            record,
            edges=edges,
            reference=reference,
            matches=matches,
            run_id=run.run_id,
            component_tree_error=component_tree_error,
        ),
    )


def _record_invalid(exc: BaseException) -> StudioError:
    """The one refusal for a record this project holds and cannot use."""

    return StudioError(
        422,
        "STATE_RECORD_INVALID",
        f"{RUNNER_RECORD_PATH}: {error_sentence(exc)}",
    )


def _load_authored_record(binding: ProjectBinding) -> StateRecord:
    """The authored record, or a typed refusal naming what is wrong with it.

    A record that is absent and a record that is unreadable are different
    problems for whoever has to fix them, and neither is an API bug: the second
    must not arrive as a 500 that says nothing.
    """

    path = binding.repository.layout.resolve_relative(RUNNER_RECORD_PATH)
    if not path.is_file():
        raise StudioError(
            404,
            "STATE_RECORD_NOT_FOUND",
            f"{binding.project_id}: the bound project holds no authored state "
            f"record at {RUNNER_RECORD_PATH}",
        )
    try:
        return StateRecord.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (
        # ``UnicodeDecodeError`` is a ``ValueError``: a record written in
        # another encoding is undecodable, not absent, and ``OSError`` is a
        # file that is there and would not open. Both are the project's to
        # fix and neither is an API bug.
        OSError,
        json.JSONDecodeError,
        StateRecordError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise _record_invalid(exc) from exc


def _bound_view(
    record: StateRecord,
    run: RunRef,
    *,
    require_view: bool,
) -> tuple[
    DevelopedDesignState | None,
    tuple[DesignComponent, ...] | None,
    str | None,
]:
    """The kernel's bound view and its component tree, or the refusal's sentence.

    The two are taken together because they are one answer: the view builds
    the component tree itself, so a record the kernel will not view is one
    whose tree this projection has no business arranging on its own.

    When the caller can live without them the sentence is returned rather than
    raised. Failing to arrange a record's components is not a claim that they
    are absent, and the entities, parameters and edges are still the record's
    own answer — so the tree is reported as absent, with the kernel's reason
    beside it, and the digests that only the view could produce say ``null``.
    """

    try:
        state = developed_design_view(
            record,
            run=run,
            portfolio_id=PORTFOLIO_ID,
            branch_id=BRANCH_ID,
            selection_decision_ref=SELECTION_DECISION_REF,
        )
        return state, design_components_of(record), None
    except StateRecordError as exc:
        if require_view:
            raise _record_invalid(exc) from exc
        return None, None, str(exc)


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
    component_tree_error: str | None,
) -> tuple[str, ...]:
    """The lines the UI shows verbatim: what this projection cannot tell you."""

    lines: list[str] = []
    if component_tree_error is not None:
        # The kernel's sentence, in the one list a client always reads. A
        # panel that renders only the tree would otherwise be the sole place
        # this refusal appeared.
        lines.append(f"component tree unavailable: {component_tree_error}")
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
    if reference.skipped_runs:
        lines.append(
            f"{len(reference.skipped_runs)} run directories could not be read "
            "and were skipped by the reference-run rule: "
            + ", ".join(reference.skipped_runs)
        )
    if reference.workflow_unresolved:
        lines.append(
            "reference run's workflow record could not be loaded; harness "
            "status unknown"
        )
    if matches is False:
        lines.append(
            "projection digest differs from the reference receipt: the "
            f"authored record is not what run {run_id} executed"
        )
    return tuple(lines)
