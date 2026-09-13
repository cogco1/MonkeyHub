"""The exact retained State Record of a run, projected for one screen.

Everything on this page is the kernel's answer. The record is parsed by
``StateRecord.from_dict`` and verified against the run manifest and receipt;
only a project with no eligible run reads the authored WIP and attaches it via
``bound_to``. The component tree comes from ``design_components_of``, the
edges from ``StateRecord.dependency_edges`` and the digest from the same
``developed_design_view`` the project runner uses, so a projection and a run
receipt name the same number or the difference is stated out loud.

Two identities travel, not three: ``record.digest`` is the record's content and
``state.state_digest`` is that content bound to a run. Neither is computed here.

The phase that view is taken in is the run's, never this module's: it is read
from the reference run's receipt, then from the stage envelope that run
retained, then - for a projection of authored WIP in a project that holds no
run - from stage zero of the project's frozen ``project-stage-workflow``.
``UNSTATED_PHASE`` is the last step and applies only when the project states
no phase anywhere; ``_projected_phase`` is the whole rule and says why each
step exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from archflow.project.inputs import (
    AuthoredRecordInvalid,
    AuthoredRecordMissing,
    load_authored_record,
)
from archflow.project.layout import AUTHORED_RECORD_PATH
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef, record_ref_from_uri
from archflow.state.developed_design import (
    DEVELOPED_PHASES,
    DevelopedDesignError,
    DevelopedDesignState,
)
from archflow.state.design_portfolio import DesignStage
from archflow.state.operational_state import DependencyEdge
from archflow.state.spatial import DesignComponent
from archflow.state.stage_workflow import DesignPhase
from archflow.state.state_record import (
    Parameter,
    StateRecord,
    StateRecordError,
    design_components_of,
    developed_design_view,
    parameter_bindings_of,
    resolve_element_bindings,
)

from ..transport.errors import StudioError, error_sentence
from .binding import ProjectBinding, ReferenceRun, STUDIO_RUN_ID

# The project runner's own view kwargs. Changing any of them turns
# ``stateDigest`` into a number no receipt carries.
PORTFOLIO_ID = "declared-schematic"
BRANCH_ID = "runner-v1"
SELECTION_DECISION_REF = "decision:declared-schematic-selection"

# The last step of ``_projected_phase`` and the only place this module names
# a phase: a project that holds no run and has frozen no stage ladder states
# none anywhere. It is the phase every such projection has had (P112), and
# the phase in which a receipt older than ``stage.phase`` computed its own
# digest, so a comparison against one still compares like with like. It is
# not a default for a run: a run states its phase in the envelope it
# retained, and steps 1-3 of the rule read it there.
UNSTATED_PHASE = DesignPhase.DESIGN_DEVELOPMENT


@dataclass(frozen=True, slots=True)
class ProjectedElement:
    """One ``Element@1`` row, with the scalars the intent grammar can target.

    ``numeric_fields`` are the values the producers read: a literal as
    authored, a ``"@key"`` binding as the kernel evaluates it
    (``resolve_element_bindings``). ``bindings`` says which of them are
    bound and to which parameter, so a change to a bound field is routed to
    that parameter instead of being proposed against a number the row does
    not own.
    """

    element_id: str
    component_id: str
    producer: str
    numeric_fields: Mapping[str, int | float]
    bindings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StateProjection:
    """One request's answer: the bound record and everything read off it."""

    project_id: str
    head: ProjectVersionRef
    run: RunRef
    reference: ReferenceRun
    record: StateRecord
    record_source: str
    reference_state_exact: bool
    reference_state_error: str | None
    # The phase the record was projected in, by ``_projected_phase``: the
    # reference run's own stage phase when its receipt or retained envelope
    # states one, else the project's frozen stage ladder, else
    # ``UNSTATED_PHASE``. It enters ``state_digest``.
    phase: DesignPhase
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
    source_stage_ref: ProjectRecordRef | None = None

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


def project_state(
    binding: ProjectBinding,
    run_id: str | None = None,
    *,
    require_view: bool = True,
    source_stage_ref: ProjectRecordRef | str | None = None,
) -> StateProjection:
    """Project one run's retained record, or authored WIP when no run exists.

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

    if isinstance(source_stage_ref, str):
        try:
            source_stage_ref = record_ref_from_uri(source_stage_ref, binding.project_id)
        except ValueError as exc:
            raise StudioError(422, "DESIGN_STAGE_REF_INVALID", error_sentence(exc)) from exc
    # Existing sourceRunId callers can continue a committed model. An exact
    # Stage selection takes precedence when a run belongs to multiple lines.
    selected_stage: DesignStage | None = None
    if source_stage_ref is None:
        branches = binding.repository.read_design_branches()
        main_head = None if "main" not in branches else ProjectRecordRef.from_dict(branches["main"]["head_stage"])
        if run_id is None and main_head is not None:
            source_stage_ref = main_head
        elif run_id is not None and branches:
            matches: dict[ProjectRecordRef, DesignStage] = {}
            for branch_id in branches:
                for ref, stage in binding.design_history(branch_id):
                    if stage.candidate_id == run_id:
                        matches[ref] = stage
            if main_head in matches:
                source_stage_ref = main_head
                selected_stage = matches[main_head]
            elif len(matches) == 1:
                source_stage_ref, selected_stage = next(iter(matches.items()))
            elif not matches:
                delta = binding.candidate_delta(run_id)
                if delta is not None and delta.get("source_stage_ref") is not None:
                    source_stage_ref = ProjectRecordRef.from_dict(delta["source_stage_ref"])
    if source_stage_ref is not None and selected_stage is None:
        selected_stage = binding.design_stage(source_stage_ref)
    if selected_stage is not None and (run_id is None or run_id == selected_stage.candidate_id):
        reference = ReferenceRun(binding.load_run(selected_stage.candidate_id), "query",
                                 binding.repository.load_json(selected_stage.runner_ref))
    else:
        reference = binding.reference_run(run_id)
        if selected_stage is not None:
            delta = binding.candidate_delta(reference.run.run_id)
            if delta is None or delta.get("source_stage_ref") != source_stage_ref.to_dict():
                raise StudioError(409, "DESIGN_STAGE_MISMATCH", "The candidate is not derived from the selected stage.")
    head = binding.head()
    record_source = AUTHORED_RECORD_PATH
    reference_state_exact = False
    reference_state_error: str | None = None
    if reference.source == "none":
        authored = _load_authored_record(binding)
        run = reference.run
        try:
            record = authored.bound_to(run)
        except (StateRecordError, KeyError, TypeError, ValueError) as exc:
            raise _record_invalid(exc) from exc
    else:
        try:
            retained_ref, record = binding.exact_state_record(reference)
            run = reference.run
            record_source = retained_ref.uri
            reference_state_exact = True
            if selected_stage is not None and reference.run.run_id == selected_stage.candidate_id and retained_ref != selected_stage.record_ref:
                raise StudioError(409, "DESIGN_STAGE_MISMATCH", "The stage's runner and StateRecord references disagree.")
        except StudioError as exc:
            if exc.code != "REFERENCE_STATE_NOT_EXACT":
                raise
            # Existing historical runs remain inspectable even when their
            # receipt cannot establish an exact State Record.  The fallback is
            # explicitly a current WIP projection under a synthetic run id;
            # it is never rebound to the historical run it did not come from.
            authored = _load_authored_record(binding)
            run = RunRef(binding.project_id, STUDIO_RUN_ID, head)
            try:
                record = authored.bound_to(run)
            except (StateRecordError, KeyError, TypeError, ValueError) as exc:
                raise _record_invalid(exc) from exc
            reference_state_error = exc.detail
    # The phase is the run's, stated by the envelope it retained and copied
    # on to its receipt (ADR-007 rule 1); ``_projected_phase`` carries the
    # whole rule, including what a project with no run at all is read in.
    # The receipt's own phase is read only for the exact retained record of
    # that run: a WIP fallback is a current projection under the studio run
    # id and is never rebound to the phase of a run it did not come from.
    phase, phase_error = _projected_phase(
        binding, reference, exact=reference_state_exact
    )
    try:
        state, components, component_tree_error = _bound_view(
            record,
            run,
            phase=phase,
            require_view=require_view,
            record_source=record_source,
        )
        edges = record.dependency_edges()
        elements, binding_error = _elements(record)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        # A record that parsed and cannot be read is the operator's to fix,
        # and the sentence that says which field is the one worth repeating.
        raise _record_invalid(exc) from exc
    # Three states, not two: a receipt that names no digest leaves the
    # comparison unchecked, and unchecked is never reported as a mismatch. A
    # projection with no bound view has no digest to compare either, and a
    # receipt whose phase this projection cannot carry was not projected in
    # the phase it names, so its digest is not compared to a number computed
    # under another one.
    claimed = (
        None
        if reference.receipt is None
        else reference.receipt.get("design_state_digest")
    )
    matches = (
        False
        if reference.source != "none" and not reference_state_exact
        else None
        if phase_error is not None
        else state.state_digest == claimed
        if state is not None and isinstance(claimed, str)
        else None
    )
    return StateProjection(
        project_id=binding.project_id,
        head=head,
        run=run,
        reference=reference,
        record=record,
        record_source=record_source,
        reference_state_exact=reference_state_exact,
        reference_state_error=reference_state_error,
        phase=phase,
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
            component_tree_error=component_tree_error,
            reference_state_exact=reference_state_exact,
            reference_state_error=reference_state_error,
            head=head,
            phase_error=phase_error,
            binding_error=binding_error,
        ),
        source_stage_ref=source_stage_ref,
    )


def project_proposed_record(base: StateProjection, record: StateRecord) -> StateProjection:
    """Read an in-memory successor for the next edit, without making a run."""

    state, components, tree_error = _bound_view(
        record, base.run, phase=base.phase, require_view=True,
        record_source="in-memory proposal",
    )
    elements, binding_error = _elements(record)
    if binding_error is not None:
        raise StudioError(422, "STATE_RECORD_INVALID", binding_error)
    return replace(
        base, record=record, state=state, components=components,
        component_tree_error=tree_error, elements=elements,
        parameters=record.parameters, edges=record.dependency_edges(),
        record_source="in-memory proposal", matches_reference_receipt=None,
    )


def _carryable(value: object) -> DesignPhase | None:
    """One written phase, if it is a design phase the developed view can carry."""

    if not isinstance(value, str):
        return None
    try:
        phase = DesignPhase(value)
    except ValueError:
        return None
    return phase if phase in DEVELOPED_PHASES else None


def _projected_phase(
    binding: ProjectBinding, reference: ReferenceRun, *, exact: bool
) -> tuple[DesignPhase, str | None]:
    """The phase this projection reads the record in, and why the run's own was not used.

    A stage belongs to the run (ADR-007 rule 1), so the rule asks runs first
    and names a phase itself only when the project states none anywhere:

    1. the reference run's receipt, when the record on screen is that run's
       exact retained record and ``RunnerRunReceipt@3`` states ``stage.phase``;
    2. the stage envelope that run retained - the record the receipt copied its
       phase from - for a receipt written before that key existed, or for a WIP
       fallback bound to a run that still states its own stage;
    3. stage zero of the project's own frozen ``project-stage-workflow``, for a
       projection of authored WIP in a project with no run to answer for it;
    4. ``UNSTATED_PHASE``, when the project holds neither a run that states a
       phase nor a frozen ladder.

    A phase the developed-design projection cannot carry is never mapped to one
    it can: the rule continues past it and the returned sentence says so, so the
    digest comparison stays unmade rather than reporting a mismatch about a
    number nobody computed.
    """

    named: str | None = None
    if exact and reference.receipt is not None:
        stage = reference.receipt.get("stage")
        value = stage.get("phase") if isinstance(stage, Mapping) else None
        phase = _carryable(value)
        if phase is not None:
            return phase, None
        if isinstance(value, str):
            named = value
    resolved: DesignPhase | None = None
    if reference.source != "none":
        resolved = _carryable(binding.retained_stage_phase(reference.run.run_id))
    if resolved is None:
        resolved = _carryable(binding.frozen_workflow_first_phase())
    if resolved is None:
        resolved = UNSTATED_PHASE
    if named is None:
        return resolved, None
    try:
        DesignPhase(named)
    except ValueError:
        return resolved, (
            f"reference run {reference.run.run_id} names stage phase "
            f"{named!r}, which is not a design phase; projected in "
            f"{resolved.value}, so its digest is not compared to the receipt"
        )
    return resolved, (
        f"reference run {reference.run.run_id} ran its stage in phase "
        f"{named}, which the developed-design projection cannot "
        f"carry; projected in {resolved.value}, so its digest is not "
        "compared to the receipt"
    )


def require_actionable(projection: StateProjection) -> None:
    """Require the exact current reference before creating design work.

    A project with no eligible run deliberately starts from authored WIP.  Once
    a run exists, proposal and candidate work must stand on that run's retained
    record, its receipt digest and the same canonical base as current HEAD.
    """

    if projection.reference.source == "none":
        return
    if not projection.reference_state_exact:
        raise StudioError(
            409,
            "REFERENCE_STATE_NOT_EXACT",
            projection.reference_state_error
            or "the reference run has no verified retained State Record",
        )
    if projection.reference.run.base != projection.head and projection.source_stage_ref is None:
        raise StudioError(
            409,
            "REFERENCE_BASE_STALE",
            f"reference run {projection.reference.run.run_id!r} is based on "
            f"canonical version {projection.reference.run.base.version}, but "
            f"HEAD is version {projection.head.version}. It remains available "
            "for inspection; choose or create a run on current HEAD before "
            "proposing or running a candidate.",
        )
    if projection.matches_reference_receipt is not True:
        raise StudioError(
            409,
            "REFERENCE_STATE_MISMATCH",
            f"reference run {projection.reference.run.run_id!r} does not carry "
            "a design_state_digest matching its verified State Record. It "
            "remains available for inspection but cannot base a proposal or "
            "candidate.",
        )


def _record_invalid(
    exc: BaseException, *, record_source: str = AUTHORED_RECORD_PATH
) -> StudioError:
    """The one refusal for a record this project holds and cannot use."""

    return StudioError(
        422,
        "STATE_RECORD_INVALID",
        f"{record_source}: {error_sentence(exc)}",
    )


def _load_authored_record(binding: ProjectBinding) -> StateRecord:
    """The kernel's one reader of the authored record, answered on the wire.

    ``archflow.project.inputs`` owns the reading and the two refusals; this
    turns them into the API's own bodies. A record that is absent and a record
    that is unreadable are different problems for whoever has to fix them, and
    neither is an API bug: the second must not arrive as a 500 that says
    nothing. The sentence quoted is the underlying reason, because the reader
    names the file with its whole path and a client asking about a project is
    not entitled to learn where the process keeps it.
    """

    try:
        return load_authored_record(binding.repository).record
    except AuthoredRecordMissing as exc:
        raise StudioError(
            404,
            "STATE_RECORD_NOT_FOUND",
            f"{binding.project_id}: the bound project holds no authored state "
            f"record at {AUTHORED_RECORD_PATH}",
        ) from exc
    except AuthoredRecordInvalid as exc:
        raise _record_invalid(exc.__cause__ or exc) from exc


def _bound_view(
    record: StateRecord,
    run: RunRef,
    *,
    phase: DesignPhase,
    require_view: bool,
    record_source: str,
) -> tuple[
    DevelopedDesignState | None,
    tuple[DesignComponent, ...] | None,
    str | None,
]:
    """The kernel's bound view and its component tree, or the refusal's sentence.

    The two are taken together because they are one answer: the view builds
    the component tree itself, so a record the kernel will not view is one
    whose tree this projection has no business arranging on its own.

    ``phase`` is the run's (``_projected_phase``): the same view kwargs the
    runner uses, in the same phase its envelope stated, or the digest is a
    number no receipt carries.

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
            phase=phase,
        )
        return state, design_components_of(record), None
    except (StateRecordError, DevelopedDesignError) as exc:
        if require_view:
            raise _record_invalid(exc, record_source=record_source) from exc
        return None, None, str(exc)


def _elements(
    record: StateRecord,
) -> tuple[tuple[ProjectedElement, ...], str | None]:
    """Every ``Element@1`` row with the numbers the producers would read, and the kernel's refusal if it would read none.

    The values are the kernel's own input projection
    (``resolve_element_bindings``): a literal as authored and a ``"@key"``
    binding as the evaluated parameter, with no evaluator of this module's.
    When the kernel refuses the projection — a bound derived value whose
    stored number disagrees with its expression, a cycle — the rows are still
    listed with their literals, the bound fields are left out rather than
    shown as either number, and the sentence travels in ``honesty``.
    """

    entities = record.entities_of("Element@1")
    bindings = {
        entity.entity_id: {
            path[len("params."):]: key
            for path, key in parameter_bindings_of(entity, record)
            if path.startswith("params.") and "[" not in path[len("params."):] and "." not in path[len("params."):]
        }
        for entity in entities
    }
    error: str | None = None
    try:
        resolved = resolve_element_bindings(record)
    except StateRecordError as exc:
        error = str(exc)
        resolved = {entity.entity_id: dict(entity.fields) for entity in entities}
    return tuple(
        ProjectedElement(
            element_id=entity.entity_id,
            component_id=entity.fields["component_id"],
            producer=entity.fields["producer"],
            numeric_fields=_numeric_fields(resolved[entity.entity_id]),
            bindings=bindings[entity.entity_id],
        )
        for entity in entities
    ), error


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
    component_tree_error: str | None,
    reference_state_exact: bool,
    reference_state_error: str | None,
    head: ProjectVersionRef,
    phase_error: str | None = None,
    binding_error: str | None = None,
) -> tuple[str, ...]:
    """The lines the UI shows verbatim: what this projection cannot tell you."""

    lines: list[str] = []
    if component_tree_error is not None:
        # The kernel's sentence, in the one list a client always reads. A
        # panel that renders only the tree would otherwise be the sole place
        # this refusal appeared.
        lines.append(f"component tree unavailable: {component_tree_error}")
    if phase_error is not None:
        lines.append(phase_error)
    if binding_error is not None:
        # The kernel refused to resolve the rows' parameter bindings; the bound
        # fields are absent from ``elements`` rather than shown as a number
        # the producers would refuse to read.
        lines.append(f"bound element values unavailable: {binding_error}")
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
    if reference.source == "none":
        lines.append(
            "no eligible reference run: projection bound to the studio run "
            "id; its digests are not comparable to any receipt"
        )
    elif not reference_state_exact:
        lines.append(
            (reference_state_error or "reference State Record is not exact")
            + "; showing current authored WIP for inspection only"
        )
    elif reference.run.base != head:
        lines.append(
            f"reference run {reference.run.run_id} is based on canonical "
            f"version {reference.run.base.version}, while HEAD is version "
            f"{head.version}: inspection is allowed but new proposals and "
            "candidates are blocked"
        )
    if reference.skipped_runs:
        # Shown verbatim, so it has to read as a sentence: one skipped run
        # directory is not "1 run directories".
        count = len(reference.skipped_runs)
        noun, verb = (
            ("directory", "was") if count == 1 else ("directories", "were")
        )
        lines.append(
            f"{count} run {noun} could not be read and {verb} skipped by the "
            "reference-run rule: " + ", ".join(reference.skipped_runs)
        )
    if reference.workflow_unresolved:
        lines.append(
            "reference run's workflow record could not be loaded; harness "
            "status unknown"
        )
    if matches is False:
        lines.append(
            "projection digest differs from the reference receipt: the "
            f"projected record is not what run {reference.run.run_id} executed"
        )
    return tuple(lines)
