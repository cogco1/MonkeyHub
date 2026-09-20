"""Public massing options made by the existing MonkeyHub option path.

This is authored experiment input, not an occupied building or arbitrary CAD.
The 24 fixed options use production transforms and P036 retention/readback.
There is no solver execution, candidate acceptance or automatic search here.
The caller supplies an empty project destination, normally TemporaryDirectory.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import Entity, StateRecord

from .evaluator import EvaluationRequest, EvaluationResult, MassingEvaluator
from .retained import evaluate_retained, load_retained_request


PROJECT_ID = "public-massing-evaluation"
EVIDENCE = "fixture:public-massing-evaluation-v1"
CONTEXT = ("fixture:public-massing-envelope-v1",)
ENVELOPE = {"min": (-10, 0, -10), "max": (20, 20, 20),
            "max_height_m": 12, "site_area_m2": 200, "far": 2}


@dataclass(frozen=True)
class RetainedCandidate:
    name: str
    record_ref: ProjectRecordRef
    request: EvaluationRequest
    result: EvaluationResult
    transforms: tuple[dict[str, Any], ...]


def public_massing_record() -> StateRecord:
    """A declared 6 x 4 cell, one-storey public study with one semantic owner."""

    return StateRecord(
        PROJECT_ID, "authored", (
            Entity("building", "Component@1", {
                "semantic_kind": "building", "intent": "Public massing allocation experiment",
                "volume_ids": ["block"], "source_refs": [EVIDENCE],
            }),
            Entity("ground", "MassingLevel@1", {"base_y": 0, "height": 3}, basis_refs=(EVIDENCE,)),
            Entity("block", "Volume@1", {"min": [0, 0, 0], "max": [5, 2, 3],
                                         "level_ids": ["ground"]}, basis_refs=(EVIDENCE,)),
            Entity("room", "Space@1", {"program_node_refs": ["program-node:public-study"],
                "level_ids": ["ground"], "volume_ids": ["block"]}, basis_refs=(EVIDENCE,)),
        ), evidence_refs=(EVIDENCE,), option={
            "option_id": "public-study", "label": "Public fixed massing study",
            "typology": "declared massing", "rationale": "public experiment input",
            "footprint_cells": [[x, z] for x in range(6) for z in range(4)],
        },
    )


def create_public_massing_fixture(project_root: Path) -> tuple[RetainedCandidate, ...]:
    """Create, retain and reopen 24 fixed options; never promote project HEAD.

    Widths 4..7, depths 3..5, and 1/2 storeys are authored choices. Geometry
    arithmetic stays in ``studio.options.make_option``. The returned requests
    use run/content bindings captured before reopening, not inferred on read.
    The source-tree import uses the existing Studio package without copying its
    transforms or adding that application to the core package's dependencies.
    """

    options = import_module("apps.archflow-studio.api.archflow_studio_api.application.options")
    binding_module = import_module("apps.archflow-studio.api.archflow_studio_api.application.binding")
    settings_module = import_module("apps.archflow-studio.api.archflow_studio_api.settings")
    repository = FilesystemProjectRepository.initialize(
        project_root, project_id=PROJECT_ID, initial_state={"fixture": EVIDENCE},
        authored_record=public_massing_record().to_dict(),
    )
    head = repository.read_head()
    source = public_massing_record().bound_to(repository.create_run("public-source"))
    binding = binding_module.ProjectBinding(
        repository, project_id=PROJECT_ID, project_dir=project_root,
        settings=settings_module.StudioSettings(project_dir=project_root, cad_export="off"),
    )
    store = options.OptionStore()
    saved = []
    for width in range(4, 8):
        for depth in range(3, 6):
            scale = {"transform": options.SCALE_VOLUME,
                     "parameters": {"volume_id": "block", "sx": width / 6, "sz": depth / 4}}
            scaled = options.make_option(binding, store, source, state_digest=source.state_digest, **scale)
            for floors in (1, 2):
                transforms = (scale,)
                option = scaled
                if floors == 2:
                    add_floor = {"transform": options.ADD_FLOOR, "parameters": {}}
                    option = options.make_option(binding, store, scaled.record,
                                                 state_digest=scaled.record.state_digest, **add_floor)
                    transforms = (scale, add_floor)
                run = repository.load_run(option.run_id)
                record = option.record.bound_to(run)
                # Capture the external expectations before retention and readback.
                expected = EvaluationRequest(record, run, record.digest, CONTEXT)
                ref = repository.put_json(
                    run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                    record_kind=STATE_RECORD, payload=record.to_dict(),
                )
                saved.append((f"w{width}-d{depth}-f{floors}", ref, expected, transforms))

    reopened = FilesystemProjectRepository.open(project_root)
    if reopened.read_head() != head:
        raise RuntimeError("public fixture unexpectedly changed canonical HEAD")
    evaluator = MassingEvaluator(ENVELOPE)
    candidates = []
    for name, ref, expected, transforms in saved:
        kwargs = dict(expected_run=expected.expected_run, expected_content_digest=expected.expected_content_digest,
                      context_refs=expected.context_refs)
        request = load_retained_request(reopened, ref, **kwargs)
        result = evaluate_retained(reopened, ref, evaluator=evaluator, **kwargs)
        candidates.append(RetainedCandidate(name, ref, request, result, transforms))
    return tuple(candidates)
