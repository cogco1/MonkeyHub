"""An invented P036 project shaped like a real one, for the projection check (GH-376).

Not a test module: ``tools/benchmarks/projection_check.py`` and ``test_synthetic_project``
call :func:`build_synthetic_project`. Every record is written the way the
product writes it - the ``support`` helpers for what a runner or an OCCT seat
leaves behind, the Studio API for what an architect does - so the routes the
check compares read the same kinds of records a real project holds. The
content is invented: one portico, its module and some sketch pages.

Run ids, candidate ids, stage labels, branch names and bytes are fixed, and so
is what the product would take from the machine: the Studio stamps reviews,
admissions, Stages and the working draft with the wall clock and random event
ids, and the runner records how long each round took. While the scenario plays,
those modules read a clock that starts at a fixed instant, ids from a seeded
generator and a timer that advances a fixed step per reading, so two
generations are identical byte for byte. The check still generates once and
copies that project for every process it starts.
"""

from __future__ import annotations

import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import itertools
from pathlib import Path
import random
import time
from typing import Any, Iterator
from unittest import mock
import uuid

from fastapi.testclient import TestClient
from PIL import Image

from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecordEditKind, StateRecordOperator
from project_runtime.application import design_history, working_draft
from project_runtime.binding import bound_project
from project_runtime.application.candidate import run_operator
from project_runtime.application.projection import project_state
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from monkeyarch.application import project_runner

from . import support

FIXTURES = Path(__file__).parent / "fixtures"
MODEL_FILES = ("model-source-a.3dm", "model-source-b.3dm", "model-source-composed.3dm", "native-source-index.3dm")
DOCUMENT_COLOURS = ("blue", "green", "orange", "purple", "gray", "teal", "navy")
REFERENCE_RUN_ID = support.REFERENCE_RUN_ID
FORK_BRANCH = "alternative"
# The runs the scenario always writes beside its candidates and document runs.
STUDIO_RUNS = (REFERENCE_RUN_ID, "studio-admissions", "studio-candidate-reviews", "studio-board")
# Three Stages on main and a fork with its own need six candidates; with the
# four runs above and two document runs that is twelve.
MIN_RUNS = 12
# What the product stamps from the machine, fixed: the Studio's wall clock starts at
# EPOCH and ticks a second per reading, its random event ids come from a generator
# seeded with ID_SEED, and the runner's timer advances TIMER_STEP per reading, so a
# measured duration counts readings instead of the machine's speed.
EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
ID_SEED = 376
TIMER_STEP = 0.05
# The modules that stamp an architect's acts with the wall clock and a random event id.
STAMPING_MODULES = (design_history, working_draft)


@contextlib.contextmanager
def _project_id(project_id: str) -> Iterator[None]:
    """Let the ``support`` helpers stamp this project's id instead of their fixture's."""

    previous = support.PROJECT_ID
    support.PROJECT_ID = project_id
    try:
        yield
    finally:
        support.PROJECT_ID = previous


class _RunnerTime:
    """The runner's ``time``: ``perf_counter`` advances ``TIMER_STEP`` per reading, the rest is real."""

    def __init__(self) -> None:
        self._readings = itertools.count()

    def perf_counter(self) -> float:
        return next(self._readings) * TIMER_STEP

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@contextlib.contextmanager
def _fixed_stamps() -> Iterator[None]:
    """Give the modules that stamp records the fixed clock, event ids and runner timer."""

    ticks = itertools.count()
    ids = random.Random(ID_SEED)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = EPOCH + timedelta(seconds=next(ticks))
            return moment.replace(tzinfo=None) if tz is None else moment.astimezone(tz)

    def uuid4() -> uuid.UUID:
        return uuid.UUID(int=ids.getrandbits(128), version=4)

    with contextlib.ExitStack() as stack:
        for module in STAMPING_MODULES:
            stack.enter_context(mock.patch.object(module, "datetime", Clock))
            stack.enter_context(mock.patch.object(module, "uuid4", uuid4))
        stack.enter_context(mock.patch.object(project_runner, "time", _RunnerTime()))
        yield


def _payloads(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = copy.deepcopy(support.RECORD_PAYLOAD)
    record["project_id"] = project_id
    return record, copy.deepcopy(support.SEATS_PAYLOAD)


def _png(index: int) -> bytes:
    """A small sketch page; its colour and width make every page's bytes distinct."""

    output = BytesIO()
    colour = DOCUMENT_COLOURS[index % len(DOCUMENT_COLOURS)]
    Image.new("RGB", (96 + index, 64), colour).save(output, format="PNG")
    return output.getvalue()


class _Scenario:
    """The architect's session, replayed through the API with fixed names."""

    def __init__(self, repository: FilesystemProjectRepository, project_id: str) -> None:
        self.repository = repository
        self.project_id = project_id
        self.settings = StudioSettings(project_dir=repository.layout.root, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.models = [(FIXTURES / name).read_bytes() for name in MODEL_FILES]
        self.candidates: list[str] = []

    def close(self) -> None:
        self.client.close()

    def _ok(self, response, status: int = 200) -> dict:
        if response.status_code != status:
            raise RuntimeError(f"{response.request.method} {response.request.url.path}: "
                               f"{response.status_code} {response.text}")
        return response.json()

    def state_digest(self, run_id: str) -> str:
        return self._ok(self.client.get("/api/state", params={"run": run_id}))["stateDigest"]

    def register_model(self, run_id: str, index: int) -> dict:
        data = self.models[index % len(self.models)]
        return self._ok(self.client.post("/api/model-assets", json={
            "projectId": self.project_id, "runId": run_id, "stateDigest": self.state_digest(run_id),
            "fileName": "complete.3dm", "contentBase64": base64.b64encode(data).decode("ascii"),
        }), 201)["modelSource"]

    def retain_occt(self, run_id: str, index: int) -> None:
        data = self.models[index % len(self.models)]
        support.retain_occt_receipt(
            self.repository, self.repository.load_run(run_id), stage_id=f"stage-{index:03d}",
            step_bytes=b"ISO-10303-21; synthetic " + run_id.encode("ascii") + b"\n",
            preview_bytes=data,
        )

    def candidate(self, index: int, *, source_run_id: str | None = None, stage: dict | None = None,
                  occt: bool = True) -> str:
        """One retained candidate with a fixed id, changing one declared height."""

        run_id = f"studio-cand-{index:03d}"
        binding = bound_project(self.app.state)
        stage_ref = None if stage is None else record_ref_from_uri(stage["stageRef"], self.project_id)
        projection = project_state(binding, run_id=source_run_id, source_stage_ref=stage_ref)
        operator = StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=projection.record.digest,
            base_state_digest=projection.record.state_digest, target_ref="parameter:module",
            key="module", value=round(1.2 + 0.05 * index, 2),
        )
        run_operator(binding, self.settings, operator, run_id, source_run_id=source_run_id,
                     source_stage_ref=stage_ref)
        self.register_model(run_id, index)
        if occt:
            self.retain_occt(run_id, index)
        self.candidates.append(run_id)
        return run_id

    def initialize(self, model: dict) -> dict:
        return self._ok(self.client.post("/api/design-stages/initialize", json={
            "projectId": self.project_id, "modelSource": model}), 201)

    def accept(self, candidate_id: str, stage: dict, branch_id: str = "main") -> dict:
        return self._ok(self.client.post(f"/api/candidates/{candidate_id}/accept", json={
            "projectId": self.project_id, "branchId": branch_id, "expectedHeadStageRef": stage["stageRef"]}))

    def fork(self, stage: dict, branch_id: str) -> dict:
        return self._ok(self.client.post("/api/design-branches", json={
            "projectId": self.project_id, "branchId": branch_id, "parentBranch": "main",
            "stageRef": stage["stageRef"]}), 201)

    def admit(self, run_ids: list[str], *, rejected: frozenset[str] = frozenset()) -> dict:
        results = [{"runId": run_id, "outcome": "rejected" if run_id in rejected else "admitted",
                    **({} if run_id in rejected else {"label": f"Option {run_id[-3:]}"})} for run_id in run_ids]
        return self._ok(self.client.post("/api/admissions", json={
            "projectId": self.project_id, "task": {"kind": "ui"}, "results": results}), 201)

    def review(self, kind: str, ref: str, action: str, reason: str | None = None) -> dict:
        return self._ok(self.client.post("/api/candidate-reviews", json={
            "projectId": self.project_id, "subjectKind": kind, "subjectRef": ref,
            "action": action, "reason": reason}), 201)

    def document(self, run_id: str, index: int) -> dict:
        return self._ok(self.client.post("/api/documents", json={
            "projectId": self.project_id, "runId": run_id, "fileName": f"sketch-{index:02d}.png",
            "mimeType": "image/png", "contentBase64": base64.b64encode(_png(index)).decode("ascii"),
        }), 201)

    def board(self, title: str) -> dict:
        before = self._ok(self.client.get("/api/board"))
        return self._ok(self.client.put("/api/board", json={
            "projectId": self.project_id, "baseRevisionSha256": before.get("revisionSha256"),
            "title": title, "elements": [], "seenDocuments": []}))

    def adopt(self, run_id: str, branch_id: str | None = None) -> dict:
        position = self._ok(self.client.get("/api/working-draft"))
        return self._ok(self.client.put("/api/working-draft", json={
            "projectId": self.project_id, "runId": run_id, "branchId": branch_id,
            "baseRevisionSha256": position["revisionSha256"]}))


def plan(runs: int) -> tuple[int, int]:
    """How many candidate runs and document runs a project of ``runs`` runs holds."""

    if runs < MIN_RUNS:
        raise ValueError(f"the scenario needs at least {MIN_RUNS} runs, not {runs}")
    documents = max(1, min(6, runs // 5))
    return runs - len(STUDIO_RUNS) - documents, documents


def build_synthetic_project(parent: Path, project_id: str = "synthetic-bench", *, runs: int = 30) -> Path:
    """Write the synthetic project to ``parent / project_id`` and answer that directory.

    ``runs`` is the number of run directories the project ends with: the
    reference run, the Studio's admission, review and board runs, a few
    document runs and candidates for the rest. At 30 runs that is 20
    candidates and 457 retained records.
    """

    candidates, documents = plan(runs)
    project_dir = Path(parent) / project_id
    record, seats = _payloads(project_id)
    with _project_id(project_id), _fixed_stamps():
        repository = FilesystemProjectRepository.initialize(
            project_dir, project_id=project_id,
            initial_state={"project_id": project_id, "version": 0},
        )
        run = repository.create_run(REFERENCE_RUN_ID)
        support.write_runner_record(repository, record)
        support.write_runner_seats(repository, seats)
        support.retain_runner_receipt(
            repository, run, record_payload=record,
            design_state_digest=support.runner_state_digest(repository, REFERENCE_RUN_ID, record),
        )
        scenario = _Scenario(repository, project_id)
        try:
            _play(scenario, candidates=candidates, document_runs=documents)
        finally:
            scenario.close()
    return project_dir


def _play(scenario: _Scenario, *, candidates: int, document_runs: int) -> None:
    """Three Stages on main, a fork with its own, reviews, documents, a board and a draft."""

    main_stages = [scenario.initialize(scenario.register_model(REFERENCE_RUN_ID, 0))]
    # Candidates fall into four stretches. Each of the first three ends in a
    # Stage on main; the fork is taken from the second Stage and its last
    # candidate becomes the fork's own Stage. Within a stretch each candidate
    # continues the one before it, as ordinary work does.
    fork_at = candidates - max(2, candidates // 5)
    per_stretch = max(1, fork_at // 3)
    previous: str | None = None
    fork_stage: dict | None = None
    for index in range(1, candidates + 1):
        if index == fork_at + 1:
            scenario.fork(main_stages[1], FORK_BRANCH)
            fork_stage = main_stages[1]
            previous = None
        base_stage = fork_stage if index > fork_at else main_stages[-1]
        candidate = scenario.candidate(index, source_run_id=previous,
                                       stage=base_stage if previous is None else None,
                                       occt=index % 7 != 0)
        previous = candidate
        if index <= fork_at and index % per_stretch == 0 and len(main_stages) < 4:
            main_stages.append(scenario.accept(candidate, main_stages[-1]))
            previous = None
        elif index == candidates and fork_stage is not None:
            fork_stage = scenario.accept(candidate, fork_stage, FORK_BRANCH)
    # Admission batches of three, as a loop closes: two admitted, one rejected.
    staged = {stage["candidateId"] for stage in (*main_stages, fork_stage) if stage is not None}
    open_candidates = [run_id for run_id in scenario.candidates if run_id not in staged]
    admitted: list[str] = []
    for start in range(0, len(open_candidates), 3):
        batch = open_candidates[start:start + 3]
        rejected = frozenset(batch[2:])
        scenario.admit(batch, rejected=rejected)
        admitted.extend(run_id for run_id in batch if run_id not in rejected)
    for number, candidate in enumerate(admitted):
        if number % 2 == 0:
            action = ("endorse", "reject", "archive")[(number // 2) % 3]
            scenario.review("candidate", candidate, action, f"synthetic review {number}")
    scenario.review("stage", main_stages[-1]["stageRef"], "endorse", "develop this stage")
    for number in range(document_runs):
        run_id = f"survey-{number + 1:02d}"
        scenario.repository.create_run(run_id)
        for page in range(2):
            scenario.document(run_id, 2 * number + page)
    scenario.board("synthetic board")
    scenario.adopt(scenario.candidates[-2])
