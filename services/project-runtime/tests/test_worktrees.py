"""A read-only Worktree Graph: current head, its line, running work, other lines and conflicts."""

import base64
from unittest import mock

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import INTENT_COMPILATION
from archflow.project.refs import record_ref_from_uri
from archflow.state.state_record import StateRecordEditKind, StateRecordOperator

from project_runtime.application.artifacts import ModelSource, save_document
from fastapi.testclient import TestClient

from project_runtime.binding import ProjectBinding, bound_project
from project_runtime.application.candidate import run_operator
from project_runtime.jobs import Job
from project_runtime.application.projection import project_state
from project_runtime import status
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from project_runtime.status import worktree_graph
from project_runtime.application.working_draft import lineage_of

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate_admission import AdmissionFixture
from .test_construction_routes import ConstructionTestCase
from .test_rendering import Adapter, finished, png, request, submit
from .test_working_source import WorkingSourceFixture, adopt


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

    def test_a_generated_result_is_a_newer_line_until_it_is_continued(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], stage["candidateId"])
        [result] = self.lines(graph, "result")
        self.assertEqual((result["runId"], result["relation"], result["reconcile"]), (first, "ahead", "none"))
        self.adopt(first)
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        self.assertEqual(self.lines(graph, "result"), [])

    def test_sequential_work_is_one_current_line(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        self.adopt(second)
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
        self.adopt(first)
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
        self.adopt(first)
        second = self.candidate_from(stage, 2.8)
        before = (self.repository.read_working_draft(), self.repository.read_design_branches(), self.repository.read_head())
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        [result] = self.lines(graph, "result")
        self.assertEqual((result["runId"], result["reconcile"]), (second, "conflict"))
        self.assertIn("entity:portico-base", result["conflicts"])
        # Another attempt at what the head's line changed from the same Stage, never taken further (#575).
        self.assertEqual((result["relation"], result["supersededBy"]), ("superseded", first))
        self.assertEqual((self.repository.read_working_draft(), self.repository.read_design_branches(),
                          self.repository.read_head()), before)

    def test_lines_sharing_an_unaccepted_parent_compare_from_that_parent(self):
        stage = self.initialize()
        parent = self.candidate_from(stage, 2.2)
        head = self.continue_from(parent, height=2.4)
        self.adopt(head)
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
        self.adopt(first)
        self.fork(stage, "facade-b")
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        [branch] = self.lines(graph, "branch")
        self.assertEqual((branch["branchId"], branch["status"], branch["relation"]), ("facade-b", "accepted", "separate"))
        self.assertEqual(len(self.lines(graph, "head")), 1)

    def test_running_work_shows_base_scope_and_overlap(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        self.adopt(first)
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
        # A plan cut from the Stage's registered model reads that model's bytes.
        drawn = self.client.post("/api/drawings/plans", json={
            "projectId": PROJECT_ID, "sourceStageRef": stage["stageRef"], "drawingId": "stage-plan",
            "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50, "dimensions": []})
        self.assertEqual(drawn.status_code, 201, drawn.text)
        drawing = drawn.json()
        rows = {row["kind"]: row for row in self.graph()["representations"]}
        self.assertEqual((rows["drawing"]["itemId"], rows["drawing"]["state"], rows["drawing"]["sourceRunId"]),
                         ("stage-plan", "current", stage["candidateId"]))
        self.adopt(self.candidate_from(stage))
        rows = {row["kind"]: row for row in self.graph()["representations"]}
        self.assertEqual(rows["render"]["state"], "stale")
        # The continued result registers the same model bytes, so nothing the plan
        # reads changed: its row says what the Drawing tool says, although the
        # design state it would compare as a whole has moved.
        status = self.client.post("/api/drawings/plans/status", json={
            name: drawing[name] for name in ("runId", "assetSha256", "revisionRef")})
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual((rows["drawing"]["state"], status.json()["status"]), ("current", "current"))
        self.assertEqual(rows["drawing"]["detail"], status.json()["detail"])

    def test_a_combined_result_contains_the_work_it_combined(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        self.adopt(first)
        other = self.independent_from(None, stage=stage)
        response = self.client.post("/api/candidates/combine", json={"projectId": PROJECT_ID, "candidateIds": [first, other]})
        self.assertEqual(response.status_code, 202, response.text)
        combined = response.json()["candidateId"]
        self.assertEqual(self.finished(response.json()["jobId"])["status"], "succeeded")
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], first)
        results = {line["runId"]: line for line in self.lines(graph, "result")}
        self.assertEqual((results[combined]["relation"], results[combined]["reconcile"]), ("ahead", "none"))
        self.assertEqual(results[other]["reconcile"], "can-combine")

        position = self.client.get("/api/working-draft").json()
        moved = self.client.put("/api/working-draft", json={"projectId": PROJECT_ID, "runId": combined,
                                "baseRevisionSha256": position["revisionSha256"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        graph = self.graph()
        self.assertEqual(graph["head"]["runId"], combined)
        self.assertTrue({first, other} <= set(graph["head"]["lineage"]), graph["head"]["lineage"])
        # The inputs are the head's own history now: no conflicting lines remain.
        self.assertEqual(self.lines(graph, "result"), [])

    def test_the_head_history_is_walked_once_and_remembered(self):
        stage = self.initialize()
        run = self.candidate_from(stage)
        for height in (2.3, 2.4, 2.5):
            run = self.continue_from(run, height=height)
        self.adopt(run)
        binding = bound_project(self.app.state)
        with mock.patch("project_runtime.status.lineage_of", wraps=lineage_of) as walked:
            graph = worktree_graph(binding)
        self.assertEqual(graph.head.run_id, run)
        self.assertEqual([line.kind for line in graph.lines], ["head"])
        self.assertEqual(walked.call_count, 0, "results the head already contains are skipped before any walk")

        reads = []
        original = ProjectBinding.candidate_delta

        def counted(this, run_id):
            reads.append(run_id)
            return original(this, run_id)

        with mock.patch.object(ProjectBinding, "candidate_delta", counted):
            lineage = lineage_of(binding, run)
        self.assertEqual((len(lineage), lineage[0], lineage[-1]), (5, run, stage["candidateId"]))
        # Retained changes never change: only the root without one is read again.
        self.assertEqual(reads, [stage["candidateId"]])


class HeadLineTests(AdmissionFixture):
    """The Working Head's line and the drafts it superseded (#575), shaped like thi-hemp-study-01.

    Nothing is admitted and nothing is staged: outside agents made each run through the runtime API and
    the person continued them one after another, while two earlier attempts at the third step stayed
    retained beside the line.
    """

    def graph(self) -> dict:
        response = self.client.get("/api/worktrees")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def results(self, graph: dict) -> dict[str, dict]:
        return {line["runId"]: line for line in graph["lines"] if line["kind"] == "result"}

    def save(self, run_id: str, label: str) -> None:
        position = self.client.get("/api/working-draft").json()
        response = self.client.post("/api/working-draft/save", json={
            "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": position["revisionSha256"], "label": label})
        self.assertEqual(response.status_code, 200, response.text)

    def line_with_two_drafts(self) -> tuple[str, str, str, str, tuple[str, str]]:
        """A → B → C → D: two drafts were built on B before C, also built on B, was continued."""

        a = REFERENCE_RUN_ID
        b = self.result(height=2.2)
        self.adopt(b)
        drafts = (self.result(source=b, height=2.6), self.result(source=b, height=2.65))
        c = self.result(source=b, height=2.7)
        self.adopt(c)
        d = self.result(source=c, height=2.8)
        self.adopt(d)
        return a, b, c, d, drafts

    def test_drafts_the_line_moved_past_are_superseded_and_the_line_is_named(self):
        a, b, c, d, drafts = self.line_with_two_drafts()
        self.save(b, "V2")
        self.save(drafts[0], "V3 - closed wall and simple mono-pitch roof")
        # C's agent admitted it with the person's words, which come before the sentence its run was made from;
        # a saved name would still come first.
        self.admit({"runId": c, "outcome": "admitted", "label": "V3 - ribbed roof", "summary": "Ribs at 1.2 m"},
                   rawLanguage="Make the roof ribbed")
        before = (self.repository.read_working_draft(), self.repository.read_design_branches(), self.repository.read_head())
        with mock.patch.object(status, "_reconcile", wraps=status._reconcile) as compared:
            graph = self.graph()
        self.assertEqual(graph["head"]["runId"], d)
        line = graph["line"]
        self.assertEqual([step["runId"] for step in line], [a, b, c, d], "the line is A..D, oldest first")
        self.assertEqual([step["baseRunId"] for step in line], [None, a, b, c])
        self.assertEqual([step["label"] for step in line], [None, "V2", "V3 - ribbed roof", None])
        # Each run keeps the sentence its proposal was made from (#575); the project start was asked for by nobody.
        self.assertEqual([step["request"] for step in line], [None, "set height to 2.2", "Make the roof ribbed", "set height to 2.8"])
        self.assertEqual([step["summary"] for step in line], [None, None, "Ribs at 1.2 m", None])
        self.assertEqual([step["stageRef"] for step in line], [None] * 4)
        self.assertTrue(all(step["updatedAt"] for step in line[1:]), line)
        results = self.results(graph)
        self.assertEqual(set(results), set(drafts), "the drafts are listed, never dropped")
        for draft in drafts:
            row = results[draft]
            self.assertEqual((row["relation"], row["supersededBy"], row["baseRunId"], row["admission"]),
                             ("superseded", c, b, "none"))
            # The comparison that says so stays, for whoever asks; it is not the line's state.
            self.assertEqual(row["reconcile"], "conflict")
            self.assertIn("entity:portico-base", row["conflicts"])
        self.assertEqual(results[drafts[0]]["label"], "V3 - closed wall and simple mono-pitch roof")
        self.assertEqual(compared.call_count, 2)
        [head] = [row for row in graph["lines"] if row["kind"] == "head"]
        self.assertEqual((head["relation"], head["supersededBy"]), ("head", None))
        # Reading writes, moves and deletes nothing.
        self.assertEqual((self.repository.read_working_draft(), self.repository.read_design_branches(),
                          self.repository.read_head()), before)
        self.assertTrue(set(drafts) <= set(self.repository.run_ids()))

    def test_one_graph_reads_each_step_of_the_line_once_however_many_drafts_compare_with_it(self):
        _a, b, c, _d, drafts = self.line_with_two_drafts()
        drafts = (*drafts, self.result(source=b, height=2.62))
        binding = bound_project(self.app.state)
        before = worktree_graph(binding)
        reads = []
        original = ProjectBinding.candidate_delta

        def counted(this, run_id):
            reads.append(run_id)
            return original(this, run_id)

        with mock.patch.object(ProjectBinding, "candidate_delta", counted):
            graph = worktree_graph(binding)
        self.assertEqual(graph, before, "reading each change once changes no answer")
        self.assertEqual({line.run_id for line in graph.lines if line.relation == "superseded"}, set(drafts))
        # Each draft's comparison walks the head's line back to B. C's change is read once for all three,
        # and each draft's once, however many walks pass through them.
        self.assertEqual(reads.count(c), 1, reads)
        self.assertEqual({run: reads.count(run) for run in drafts}, dict.fromkeys(drafts, 1))

    def test_a_draft_somebody_took_further_stays_a_diverged_line(self):
        _a, b, _c, d, (continued, admitted) = self.line_with_two_drafts()
        built_on = self.result(source=b, height=2.55)
        further = self.result(source=built_on, height=2.58)
        # The person stood on one draft once, and on a result built on another, before returning to D.
        self.adopt(continued)
        self.adopt(further)
        self.adopt(d)
        self.admit({"runId": admitted, "outcome": "admitted", "label": "B"})
        results = self.results(self.graph())
        for run in (continued, admitted, built_on, further):
            self.assertEqual((results[run]["relation"], results[run]["supersededBy"], results[run]["reconcile"]),
                             ("diverged", None, "conflict"), run)

    def test_returning_to_an_earlier_step_shortens_the_line_and_supersedes_nothing(self):
        _a, b, c, d, drafts = self.line_with_two_drafts()
        self.adopt(b)
        graph = self.graph()
        self.assertEqual([step["runId"] for step in graph["line"]], [REFERENCE_RUN_ID, b])
        results = self.results(graph)
        # Everything built on B now continues the head; nothing is superseded, and the later steps stay retained.
        self.assertEqual({run: results[run]["relation"] for run in (*drafts, c, d)}, dict.fromkeys((*drafts, c, d), "ahead"))
        self.adopt(d)
        self.assertEqual({row["relation"] for row in self.results(self.graph()).values()}, {"superseded"})

    def test_a_run_from_before_requests_were_kept_is_named_by_the_sentence_a_model_compiled(self):
        binding = bound_project(self.app.state)
        projection = project_state(binding, run_id=REFERENCE_RUN_ID)
        # A change made the way runs were made before they kept their words: no request with its change.
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=projection.record.digest,
                                       base_state_digest=projection.record.state_digest,
                                       target_ref="parameter:module", key="module", value=1.5)
        run_operator(binding, self.settings, operator, "studio-cand-legacy", source_run_id=REFERENCE_RUN_ID)
        self.assertIsNone(binding.candidate_delta("studio-cand-legacy")["request"])
        self.adopt("studio-cand-legacy")
        self.assertEqual(self.graph()["line"][-1]["request"], None, "a change nobody asked for in words has none")
        self.repository.put_json(run=self.repository.load_run("studio-cand-legacy"), record_kind=INTENT_COMPILATION,
                                 destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="studio-cand-legacy"),
                                 payload={"schema": "IntentCompilation@1", "proposal_id": "proposal-legacy",
                                          "utterance": "Give the ribs their materials", "base_state_digest": "0" * 64,
                                          "receipt": {}})
        status._REQUESTS.clear()
        self.assertEqual(self.graph()["line"][-1]["request"], "Give the ribs their materials")

    def test_a_chat_admission_names_its_step_in_the_users_words(self):
        made = self.result(height=2.4)
        # The Hub binds the user's own words to the admission; the run itself kept the agent's sentence.
        self.admit({"runId": made, "outcome": "admitted", "label": "Materials"}, task="hub-chat", client=self.managed(),
                   messageSource={"sessionId": "chat-1", "messageId": "message-9"}, rawLanguage="给 238 个构件定材质")
        self.adopt(made)
        step = self.graph()["line"][-1]
        self.assertEqual((step["runId"], step["request"], step["label"]), (made, "给 238 个构件定材质", "Materials"))
        self.assertEqual(bound_project(self.app.state).candidate_delta(made)["request"], "set height to 2.4")

    def test_returning_to_an_earlier_step_lists_the_steps_it_left_as_a_later_line(self):
        a, b, c, d, drafts = self.line_with_two_drafts()
        self.save(c, "V3")
        self.adopt(b)
        graph = self.graph()
        self.assertEqual([step["runId"] for step in graph["line"]], [a, b])
        later = graph["later"]
        self.assertEqual([(step["runId"], step["baseRunId"]) for step in later], [(c, b), (d, c)],
                         "the line it left, oldest first, from the step made from the head")
        self.assertEqual([(step["label"], step["request"]) for step in later],
                         [("V3", "set height to 2.7"), (None, "set height to 2.8")])
        self.assertTrue(all(step["updatedAt"] for step in later))
        # The drafts built on B were never stood on: they are no part of it. Nothing about the results changed.
        results = self.results(graph)
        self.assertEqual({run: results[run]["relation"] for run in (*drafts, c, d)}, dict.fromkeys((*drafts, c, d), "ahead"))
        # Continued again, a later step is the head and the rest of the line still follows it.
        self.adopt(c)
        graph = self.graph()
        self.assertEqual(([step["runId"] for step in graph["line"]], [step["runId"] for step in graph["later"]]), ([a, b, c], [d]))
        self.adopt(d)
        self.assertEqual(self.graph()["later"], [], "on the line's last step nothing is left after it")
        # Back two steps at once: the whole line it left.
        self.adopt(b)
        self.adopt(a)
        self.assertEqual([step["runId"] for step in self.graph()["later"]], [b, c, d])
        # Reading the graph wrote nothing.
        before = (self.repository.read_working_draft(), self.repository.read_design_branches())
        self.graph()
        self.assertEqual((self.repository.read_working_draft(), self.repository.read_design_branches()), before)

    def test_where_the_line_left_forked_it_goes_on_through_the_branch_moved_onto_last(self):
        _a, b, c, d, _drafts = self.line_with_two_drafts()
        self.adopt(b)
        e = self.result(source=b, height=2.9)
        self.adopt(e)
        self.adopt(b)
        self.assertEqual([step["runId"] for step in self.graph()["later"]], [e], "E was stood on last")
        # Standing on D again and returning: that whole line is the one left, not E.
        self.adopt(d)
        self.adopt(b)
        self.assertEqual([step["runId"] for step in self.graph()["later"]], [c, d])
        # A result made from the head and never continued is a result, not a later step.
        fresh = self.result(source=b, height=3.0)
        graph = self.graph()
        self.assertEqual([step["runId"] for step in graph["later"]], [c, d])
        self.assertEqual(self.results(graph)[fresh]["relation"], "ahead")

    def test_a_stage_on_the_line_is_named_by_its_label(self):
        stage = self.stage("S0")
        first = self.result(stage, 2.2)
        self.adopt(first)
        line = self.graph()["line"]
        self.assertEqual([step["runId"] for step in line], [stage["candidateId"], first])
        self.assertEqual((line[0]["label"], line[0]["stageRef"]), ("S0", stage["stageRef"]))
        self.assertEqual((line[1]["label"], line[1]["stageRef"], line[1]["baseRunId"]), (None, None, stage["candidateId"]))


class RequestSentenceTests(ConstructionTestCase):
    """#575 rule 5: a run keeps the sentence an outside agent asked for it in, and its line step is named by it."""

    def graph(self, client: TestClient | None = None) -> dict:
        response = (client or self.client).get("/api/worktrees")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def continued(self, proposal: dict) -> str:
        run_id = self.run_candidate(proposal["proposalId"])
        adopt(self.client, run_id)
        return run_id

    def test_an_outside_agents_summary_names_its_run_and_survives_a_restart(self):
        facets = self.continued(self.facets([{"id": "portico", "set": {"material.name": "brick"}}], summary="给 238 个构件定材质"))
        delta = bound_project(self.client.app.state).candidate_delta(facets)
        self.assertEqual(delta["request"], "给 238 个构件定材质")
        self.assertEqual(self.graph()["line"][-1]["request"], "给 238 个构件定材质")
        built = self.continued(self.construct("mass = extrude(rect(0, 0, 6, 4), 3)", summary="Add a timber pavilion",
                                              sourceRunId=facets))
        line = self.graph()["line"]
        self.assertEqual([(step["runId"], step["request"]) for step in line[-2:]],
                         [(facets, "给 238 个构件定材质"), (built, "Add a timber pavilion")])
        # A restarted runtime holds no proposal and no memory of what it read: the runs say it themselves.
        restarted = TestClient(create_app(StudioSettings(cad_export=self.cad_export, project_dir=self.project)))
        self.addCleanup(restarted.close)
        with mock.patch.dict(status._REQUESTS, clear=True):
            self.assertEqual([step["request"] for step in self.graph(restarted)["line"][-2:]],
                             ["给 238 个构件定材质", "Add a timber pavilion"])
        self.assertEqual(restarted.app.state.proposals.for_state(delta["operator"]["base_state_digest"]), ())

    def test_a_change_left_to_describe_itself_keeps_no_request_and_a_chain_keeps_each_steps_words(self):
        described = self.continued(self.facets([{"id": "portico", "set": {"material.name": "brick"}}]))
        self.assertIsNone(bound_project(self.client.app.state).candidate_delta(described)["request"])
        self.assertIsNone(self.graph()["line"][-1]["request"], "the runtime's own description is no request")
        made = self.construct("mass = extrude(rect(0, 0, 6, 4), 3)", summary="Add a timber pavilion", sourceRunId=described)
        enriched = self.facets([{"id": "mass", "set": {"architectural.role": "wall"}}], summary="Make it a wall",
                               stateDigest=made["baseStateDigest"], sourceProposalId=made["proposalId"], sourceRunId=described)
        chained = self.continued(enriched)
        self.assertEqual(self.graph()["line"][-1]["request"], "Add a timber pavilion; Make it a wall")
        self.assertEqual(bound_project(self.client.app.state).candidate_delta(chained)["request"],
                         "Add a timber pavilion; Make it a wall")
