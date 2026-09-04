"""Open one stage of a project's frozen workflow as a run (ADR-007 rule 2).

    python tools/open_stage_run.py --project <project root> \
      --workflow-ref project://... --stage-index 0 --run stage-0-001

    python tools/open_stage_run.py --project <project root> \
      --workflow-ref project://... --stage-index 1 --run stage-1-001 \
      --predecessor-run stage-0-001

A stage is a property of a run, stated by the ``StageRunEnvelope@1`` retained
in that run. This is the one command that creates such a run: it takes the
workflow already frozen in the project, creates the run against canonical
HEAD, computes the developed state the run will execute from the project's own
work-in-progress record, and retains the envelope. ``tools/run_project.py``
then runs the stage and writes its closure; nothing here runs, closes or
accepts anything.

Stage N>0 needs ``--predecessor-run``: the run that already closed stage N-1.
Its retained ``stage-exit-binding`` is embedded in the new envelope, so the
successor names the exact predecessor envelope and the exact SATISFIED closure
that binding points at. Without one the command refuses, which is the rule the
runner's ``StageExecutionGuard`` enforces again before its first write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.inputs import load_authored_record  # noqa: E402
from archflow.project.ports import PersistenceArea, PersistenceDestination  # noqa: E402
from archflow.project.record_kinds import (  # noqa: E402
    PROJECT_STAGE_WORKFLOW,
    STAGE_EXIT_BINDING,
    STAGE_RUN_ENVELOPE,
)
from archflow.project.refs import parse_record_file_name, record_ref_from_uri  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository  # noqa: E402
from archflow.runtime.project_runner import RunOptions  # noqa: E402
from archflow.state.operational_state import DesignObligation  # noqa: E402
from archflow.state.stage_workflow import (  # noqa: E402
    HARNESS_WORKFLOW_IDS,
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
    open_stage_run_envelope,
    require_measurable,
)
from archflow.state.state_record import StateRecord, developed_design_view  # noqa: E402

# What the envelope binds its state digest to. The runner computes the same
# projection from the same record with the same three constants, and its guard
# refuses the run if the two digests differ, so they are read off RunOptions
# rather than restated here.
SUBJECT_REF = "state:developed-design-state"
VALIDATOR_REF = "validator:composite-stage-closure"


class StageRunError(ValueError):
    """A stage cannot be opened as asked, and the message says what is missing."""


def _retained_exit_binding(
    repository: FilesystemProjectRepository,
    run_id: str,
) -> tuple[StageExitBinding, str]:
    """The one ``stage-exit-binding`` a predecessor run retained, with its uri.

    A run closes one stage, so it retains one exit binding. None means the run
    did not close its stage; more than one means two different closes are on
    disk and the caller has to say which, rather than this picking.
    """

    records = Path(repository.layout.run(run_id).records)
    names = []
    for path in sorted(records.glob(f"{STAGE_EXIT_BINDING}-*.json")):
        try:
            kind, _ = parse_record_file_name(path.name)
        except ValueError:
            continue
        if kind == STAGE_EXIT_BINDING:
            names.append(path.name)
    if not names:
        raise StageRunError(
            f"run {run_id!r} retains no {STAGE_EXIT_BINDING}: it did not "
            "close its stage, so no successor stage may open against it"
        )
    if len(names) > 1:
        raise StageRunError(
            f"run {run_id!r} retains {len(names)} exit bindings "
            f"({', '.join(names)}); a run closes one stage"
        )
    uri = f"project://{repository.load_manifest().project_id}/runs/{run_id}/records/{names[0]}"
    ref = record_ref_from_uri(uri, repository.load_manifest().project_id)
    return StageExitBinding.from_dict(repository.load_json(ref)), ref.uri


def open_stage_run(
    *,
    project_root: Path,
    workflow_uri: str,
    stage_index: int,
    run_id: str,
    predecessor_run_id: str | None = None,
    record_ref: str | None = None,
) -> dict[str, object]:
    """Create the run and retain the envelope that says which stage it is."""

    repository = FilesystemProjectRepository.open(project_root)
    project_id = repository.load_manifest().project_id
    workflow_ref = record_ref_from_uri(workflow_uri, project_id)
    workflow = ProjectStageWorkflow.from_dict(repository.load_json(workflow_ref))
    require_measurable(workflow)
    if workflow.project_id != project_id:
        raise StageRunError("workflow belongs to another project")
    if workflow.workflow_id in HARNESS_WORKFLOW_IDS:
        raise StageRunError(
            f"{workflow.workflow_id!r} is a harness workflow: it is a shared "
            "container for coordination and never a project stage (ADR-007 "
            "rule 4)"
        )
    stage = workflow.stage_at(stage_index)
    if stage_index > 0 and predecessor_run_id is None:
        raise StageRunError(
            f"stage {stage.stage_id!r} is stage {stage_index}; it opens only "
            "against the retained exit binding of the run that closed stage "
            f"{stage_index - 1}. Pass --predecessor-run"
        )
    if stage_index == 0 and predecessor_run_id is not None:
        raise StageRunError("stage 0 has no predecessor")

    predecessor = predecessor_exit = predecessor_ref = predecessor_exit_ref = None
    if predecessor_run_id is not None:
        predecessor_exit, predecessor_exit_ref = _retained_exit_binding(
            repository, predecessor_run_id
        )
        predecessor_ref = predecessor_exit.envelope_ref
        predecessor = StageRunEnvelope.from_dict(
            repository.load_json(record_ref_from_uri(predecessor_ref, project_id))
        )

    run = repository.create_run(run_id, base=repository.read_head())
    options = RunOptions(commitment_ref=f"commitment:{workflow.workflow_id}")
    if record_ref:
        # a candidate held in a run (a re-indexed successor): the envelope binds *its* developed state
        record = StateRecord.from_dict(repository.load_json(record_ref_from_uri(record_ref, repository.read_head().project_id)))
    else:
        record = load_authored_record(repository).record
    state = developed_design_view(
        record,
        run=run,
        portfolio_id=options.portfolio_id,
        branch_id=options.branch_id,
        selection_decision_ref=options.selection_decision_ref,
    )
    envelope = open_stage_run_envelope(
        workflow,
        workflow_ref=workflow_ref.uri,
        run_id=run.run_id,
        base_version=run.base.version,
        base_state_sha256=run.base.require_digest(),
        branch_id=options.branch_id,
        branch_epoch=options.branch_epoch,
        subject_ref=SUBJECT_REF,
        state_digest=state.state_digest,
        stage_index=stage_index,
        close_obligation=DesignObligation(
            obligation_id=stage.close_obligation_id,
            statement=(
                f"Stage {stage.stage_id} closes only through the closure the "
                "runner writes from its own checks; opening it accepts nothing."
            ),
            source_ref=f"workflow:{workflow.workflow_id}/stage-{stage_index}",
            subject_refs=(SUBJECT_REF,),
            validator_ref=VALIDATOR_REF,
        ),
        predecessor=predecessor,
        predecessor_ref=predecessor_ref,
        predecessor_exit=predecessor_exit,
        predecessor_exit_ref=predecessor_exit_ref,
    )
    envelope_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run.run_id
        ),
        record_kind=STAGE_RUN_ENVELOPE,
        payload=envelope.to_dict(),
    )
    return {
        "project_id": project_id,
        "run_id": run.run_id,
        "workflow_ref": workflow_ref.uri,
        "workflow_digest": workflow.workflow_digest,
        "stage_id": stage.stage_id,
        "stage_index": stage_index,
        "phase": stage.phase.value,
        "required_checks": list(stage.required_checks),
        "stage_envelope_ref": envelope_ref.uri,
        "stage_envelope_digest": envelope.envelope_digest,
        "state_digest": state.state_digest,
        "state_record_digest": record.digest,
        "predecessor_run_id": predecessor_run_id,
        "predecessor_envelope_ref": predecessor_ref,
        "predecessor_exit_binding_ref": predecessor_exit_ref,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a run and retain the StageRunEnvelope@1 that binds "
                    "it to one stage of the project's frozen workflow. Stage N>0 "
                    "requires the predecessor run's retained exit binding.",
    )
    parser.add_argument("--project", required=True, type=Path, help="project root; its input/runner/ holds the record")
    parser.add_argument("--workflow-ref", required=True, help=f"project:// uri of the retained {PROJECT_STAGE_WORKFLOW} record")
    parser.add_argument("--stage-index", required=True, type=int)
    parser.add_argument("--run", required=True, help="the run id to create")
    parser.add_argument("--predecessor-run", default=None, help="the run that closed stage N-1 (required for stage N>0)")
    parser.add_argument("--record-ref", default=None, help="bind the envelope to this state-record record (a candidate held in a run) instead of the authored record")
    args = parser.parse_args()
    result = open_stage_run(
        project_root=args.project.resolve(),
        workflow_uri=args.workflow_ref,
        stage_index=args.stage_index,
        run_id=args.run,
        predecessor_run_id=args.predecessor_run,
        record_ref=args.record_ref,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    print(
        "next: python tools/run_project.py --project <root> "
        f"--run {result['run_id']} --workflow-ref {result['workflow_ref']} "
        f"--stage-envelope-ref {result['stage_envelope_ref']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
