"""Small, real OCCT source for isolated collaboration integration tests.

Only authored input values come from ``support``. The runner itself produces
every execution record, STEP and 3DM; no CAD or runner receipt is fabricated.
The returned source can be passed to ``POST /api/design-stages/initialize``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, developed_design_view
from monkeyarch.runtime.project_runner import CAD_BACKEND_OCCT, RunOptions, run_project
from archflow_studio_api.adapters.harness import HARNESS_PHASE, STAGE_ID, harness_guard
from archflow_studio_api.adapters.seats import seats_of
from archflow_studio_api.application.artifacts import ModelSource, list_artifacts, require_complete_model
from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, RECORD_PAYLOAD, REFERENCE_RUN_ID, SEATS_PAYLOAD, VIEW_KWARGS


def seed_collaboration_project(project_dir: Path) -> dict:
    """Initialize a new caller-owned temporary project and compile two prisms.

    ``portico-base`` starts at 0.6 m and its supported ``portico-cornice`` at
    0.3 m. Either height can be revised through ordinary scalar proposals;
    the cornice follows the base's top datum. The root must not hold a project
    already. This function accepts no Stage and never advances canonical HEAD.
    """

    project_dir = Path(project_dir)
    record_payload = deepcopy(RECORD_PAYLOAD)
    seat_pack = deepcopy(SEATS_PAYLOAD)
    seat_pack.pop("provider_identity", None)
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
        authored_record=record_payload,
        seat_pack=seat_pack,
    )
    initial_head = repository.read_head()
    run = repository.create_run(REFERENCE_RUN_ID)
    record = StateRecord.from_dict(record_payload).bound_to(run)
    # The stage this seed run executes under is the studio harness, whose
    # envelope states HARNESS_PHASE; the runner refuses a state projected in
    # any other phase, so the projection is made in the stage's own phase.
    state = developed_design_view(record, run=run, phase=HARNESS_PHASE, **VIEW_KWARGS)
    guard = harness_guard(repository, run, state)
    seats = seats_of(seat_pack)
    workspace = repository.layout.run(run.run_id).root / "workspaces"
    for seat in seats:
        if not seat.reviewer:
            (workspace / f"cad-{STAGE_ID}-{seat.seat_id}").mkdir(parents=True, exist_ok=True)
    receipt = run_project(
        repository,
        run=run,
        stage_guard=guard,
        record=record,
        seats=seats,
        options=RunOptions(
            commitment_ref=seat_pack["commitment_ref"],
            export=True,
            cad_backend=CAD_BACKEND_OCCT,
            workspace_root=workspace,
        ),
    )
    assert receipt["seat_execution_complete"], receipt
    assert receipt["closure_status"] == "SATISFIED", receipt
    settings = StudioSettings(project_dir=project_dir, reference_run=run.run_id, cad_export="occt")
    binding = ProjectBinding(repository, project_id=PROJECT_ID, project_dir=project_dir, settings=settings)
    artifacts = list_artifacts(binding).artifacts
    models = [row for row in artifacts if row.run_id == run.run_id and row.format == "3dm" and row.available]
    steps = [row for row in artifacts if row.run_id == run.run_id and row.format == "step" and row.available]
    assert len(models) == 1 and len(steps) == 1, artifacts
    model = models[0]
    require_complete_model(model, receipt)
    assert model.object_count == 2, model
    assert repository.read_head() == initial_head
    return {
        "projectId": PROJECT_ID,
        "referenceRun": run.run_id,
        "modelSource": ModelSource(run.run_id, receipt["design_state_digest"], model.sha256).to_dict(),
        "runnerReceiptRef": receipt["receipt_ref"],
        "stepSha256": steps[0].sha256,
    }
