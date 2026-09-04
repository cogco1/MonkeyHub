"""The stage the studio's candidate runs under, and what it deliberately is not.

``run_project`` will not execute a seat without a ``StageExecutionGuard``: a
retained workflow, a retained envelope for the stage being opened, and — for
any stage after the first — the previous stage's exit binding and its compiled
closure receipt. That is the kernel refusing to let a run acquire a stage by
side effect, and it is not something to work around.

So the studio does not run inside the project's workflow at all. It declares
its own single-stage workflow, ``studio-candidate-harness``, opens stage zero
of it, and runs there. The stage's close obligation says so in one sentence
that travels into the run's own records: this closes nothing in the project's
own workflow. The reference-run rule in ``binding.py`` knows the same
``workflow_id`` and refuses to let a candidate run become the run the
projection answers for — the two halves of one promise, on purpose.
"""

from __future__ import annotations

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    STUDIO_CANDIDATE_ENVELOPE,
    STUDIO_CANDIDATE_WORKFLOW,
)
from archflow.project.refs import RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.runtime.project_runner import StageExecutionGuard
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import (
    ProjectStage,
    ProjectStageWorkflow,
    open_stage_run_envelope,
)

WORKFLOW_ID = "studio-candidate-harness"
STAGE_ID = "studio-candidate"
BRANCH_ID = "runner-v1"
BRANCH_EPOCH = 1
CLOSE_OBLIGATION_ID = "close-studio-candidate"
SUBJECT_REF = "state:developed-design-state"

# What the candidate readout says about itself on the wire, verbatim.
HARNESS_STATEMENT = "studio-candidate-harness (not a project stage advance)"


def harness_guard(
    repository: FilesystemProjectRepository,
    run: RunRef,
    state: DevelopedDesignState,
) -> StageExecutionGuard:
    """Retain the harness workflow and envelope, and guard the run with them.

    Both records are written into this run's own record area before the runner
    is called, because the guard checks that the payloads it was handed are
    exactly what P036 retained — equivalent-looking objects without their refs
    are intentionally not enough.

    The stage requires no check, and the harness closes nothing. A workflow
    may require only checks the spine can measure (ADR-007 rule 3), and the
    candidate's relations are reported per seat as ``seat-relation-check``
    rather than required by this stage: requiring one here would state a
    project stage requirement that the candidate is expressly not making.
    """

    workflow = ProjectStageWorkflow(
        project_id=run.project_id,
        workflow_id=WORKFLOW_ID,
        stages=(
            ProjectStage(
                stage_id=STAGE_ID,
                stage_index=0,
                phase=DesignPhase.DESIGN_DEVELOPMENT,
                required_roles=("geometry-program",),
                required_checks=(),
                close_obligation_id=CLOSE_OBLIGATION_ID,
            ),
        ),
        basis_refs=("decision:studio-candidate-harness",),
    )
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=run.run_id
    )
    workflow_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=STUDIO_CANDIDATE_WORKFLOW,
        payload=workflow.to_dict(),
    )
    envelope = open_stage_run_envelope(
        workflow,
        workflow_ref=workflow_ref.uri,
        run_id=run.run_id,
        base_version=run.base.version,
        base_state_sha256=run.base.require_digest(),
        branch_id=BRANCH_ID,
        branch_epoch=BRANCH_EPOCH,
        subject_ref=SUBJECT_REF,
        state_digest=state.state_digest,
        stage_index=0,
        close_obligation=DesignObligation(
            obligation_id=CLOSE_OBLIGATION_ID,
            statement=(
                "A studio candidate harness stage; it closes nothing in the "
                "project's own workflow."
            ),
            source_ref=f"workflow:{WORKFLOW_ID}/stage-0",
            subject_refs=(SUBJECT_REF,),
            validator_ref="validator:composite-stage-closure",
        ),
    )
    envelope_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=STUDIO_CANDIDATE_ENVELOPE,
        payload=envelope.to_dict(),
    )
    return StageExecutionGuard(
        workflow=workflow,
        workflow_record_ref=workflow_ref,
        envelope=envelope,
        envelope_record_ref=envelope_ref,
    )
