"""A read-only Worktree Graph: current head, running work, other lines and conflicts."""

import base64

from archflow.project.refs import record_ref_from_uri
from archflow.state.state_record import StateRecordEditKind, StateRecordOperator

from archflow_studio_api.application.artifacts import ModelSource, save_document
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.candidate import run_operator
from archflow_studio_api.application.jobs import Job
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.application.runtime import worktree_graph

from .support import PROJECT_ID
from .test_rendering import Adapter, finished, png, request, submit
from .test_working_source import WorkingSourceFixture


class _Jobs:
    def __init__(self, *jobs: Job) -> None:
        self.jobs = jobs

    def list(self) -> tuple[Job, ...]:
        return self.jobs


class WorktreeGraphTests(WorkingSourceFixture):
    def graph(self) -> dict:
        response = self.client.get("/api/worktrees")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def lines(self, graph: dict, kind: str) -> list[dict]:
        return [line for line in graph["lines"] if line["kind"] == kind]

    def independent_from(self, run_id: str | None, *, stage: dict | None = None, name: str = "studio-cand-module") -> str:
        """Change only the module parameter from an exact retained source, as a separate result."""

        binding = bound_project(self.app.state)
        projection = (project_state(binding, run_id=run_id) if run_id is not None
                      else project_state(binding, source_stage_ref=stage["stageRef"]))
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR,
                                       base_record_digest=projection.record.digest,
                                       base_state_digest=projection.record.state_digest,
                                       target_ref="parameter:module", key="module", value=1.5)
        run_operator(binding, self.settings, operator, name, source_run_id=run_id,
                     source_stage_ref=None if stage is None else record_ref_from_uri(stage["stageRef"], PROJECT_ID))
        return name

    def test_sequential_work_is_one_current_line(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        graph = self.graph()
        self.assertEqual(graph["projectId"], PROJECT_ID)
        self.assertEqual(graph["head"]["runId"], second)
        head_lines = self.lines(graph, "head")
        self.assertEqual(len(head_lines), 1)
        self.assertEqual((head_lines[0]["runId"], head_lines[0]["relation"], head_lines[0]["baseRunId"]),
                         (second, "head", first))
        self.assertEqual(self.lines(graph, "result"), [])
        self.assertEqual(self.lines(graph, "branch"), [])

    def test_independent_work_from_the_same_base_can_be_combined(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        other = self.independent_from(None, stage=stage)
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        [result] = self.lines(graph, "result")
        self.assertEqual((result["runId"], result["relation"], result["baseRunId"]), (other, "diverged", stage["candidateId"]))
        self.assertEqual((result["reconcile"], result["conflicts"]), ("can-combine", []))
        self.assertIn("parameter:module", result["writes"])
        self.assertEqual(result["status"], "ready")

    def test_overlapping_work_is_a_visible_conflict_and_nothing_merges(self):
        stage = self.initialize()
        first = self.candidate_from(stage, 2.2)
        second = self.candidate_from(stage, 2.8)
        before = (self.repository.read_working_draft(), self.repository.read_design_branches(), self.repository.read_head())
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        [result] = self.lines(graph, "result")
        self.assertEqual((result["runId"], result["reconcile"]), (second, "conflict"))
        self.assertIn("entity:portico-base", result["conflicts"])
        self.assertEqual((self.repository.read_working_draft(), self.repository.read_design_branches(),
                          self.repository.read_head()), before)

    def test_lines_sharing_an_unaccepted_parent_compare_from_that_parent(self):
        stage = self.initialize()
        parent = self.candidate_from(stage, 2.2)
        head = self.continue_from(parent, height=2.4)
        sibling = self.independent_from(parent, name="studio-cand-sibling")
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], head)
        [result] = self.lines(graph, "result")
        self.assertEqual((result["runId"], result["baseRunId"], result["relation"]), (sibling, parent, "diverged"))
        # The parent's own portico-base change is shared history, not a conflict.
        self.assertEqual((result["reconcile"], result["conflicts"]), ("can-combine", []))
        self.assertNotIn("entity:portico-base", result["writes"])

    def test_an_explicit_fork_is_its_own_line_and_the_head_stays_unambiguous(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        self.fork(stage, "facade-b")
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        [branch] = self.lines(graph, "branch")
        self.assertEqual((branch["branchId"], branch["status"], branch["relation"]), ("facade-b", "accepted", "separate"))
        self.assertEqual(len(self.lines(graph, "head")), 1)

    def test_running_work_shows_base_scope_and_overlap(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        self.repository.protect_working_run("studio-cand-running-a", first)
        self.repository.protect_working_run("studio-cand-running-b", stage["candidateId"])
        self.repository.protect_working_run("studio-cand-interrupted", first)
        jobs = _Jobs(
            Job("job-a", "running", "studio-cand-running-a", "proposal-a", "2026-09-24T00:00:00+00:00",
                read_refs=frozenset({"entity:portico"}), write_refs=frozenset({"entity:portico-base"})),
            Job("job-b", "queued", "studio-cand-running-b", "proposal-b", "2026-09-24T00:00:01+00:00",
                write_refs=frozenset({"entity:portico-base", "parameter:module"})),
        )
        graph = worktree_graph(bound_project(self.app.state), jobs=jobs)
        running = {line.run_id: line for line in graph.lines if line.kind == "running"}
        self.assertEqual(set(running), {"studio-cand-running-a", "studio-cand-running-b", "studio-cand-interrupted"})
        a, b = running["studio-cand-running-a"], running["studio-cand-running-b"]
        self.assertEqual((a.status, a.base_run_id, a.relation, a.reads, a.writes),
                         ("running", first, "ahead", ("entity:portico",), ("entity:portico-base",)))
        self.assertEqual((b.status, b.base_run_id, b.relation), ("queued", stage["candidateId"], "behind"))
        self.assertEqual((a.reconcile, a.conflicts), ("conflict", ("entity:portico-base",)))
        self.assertEqual((b.reconcile, b.conflicts), ("conflict", ("entity:portico-base",)))
        self.assertEqual(running["studio-cand-interrupted"].status, "interrupted")

    def test_representations_start_empty_and_a_render_goes_stale_with_the_head(self):
        self.assertEqual(self.graph()["representations"], [])
        stage = self.initialize()
        self.app.state.render_jobs.adapter = Adapter()
        self.addCleanup(self.app.state.render_jobs.shutdown)
        source = save_document(
            bound_project(self.app.state), stage["candidateId"], "exact-view.png", "image/png",
            base64.b64encode(png()).decode(), model_source=ModelSource.from_dict(stage["modelSource"]),
            source_stage_ref=stage["stageRef"], view_recipe={"camera": {"projection": "orthographic"}},
        )
        page = {"runId": source.run_id, "assetSha256": source.asset_sha256, "revisionRef": None, "pageIndex": 0}
        job = finished(self.client, submit(self.client, request(page)))["jobId"]
        [render] = self.graph()["representations"]
        self.assertEqual((render["kind"], render["itemId"], render["state"]), ("render", job, "current"))
        self.candidate_from(stage)
        [render] = self.graph()["representations"]
        self.assertEqual(render["state"], "stale")
