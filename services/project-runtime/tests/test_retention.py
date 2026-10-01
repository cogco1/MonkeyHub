"""What the line moved past goes to the project trash, restorable for 30 days (#575).

Kaiwen's rule (2026-10-01): only superseded drafts and failed attempts are
cleaned, only when they are on no kept line, nobody continued or admitted them
and nothing refers to them. These tests drive real candidate runs through the
HTTP boundary, shaped like the case that asked for it: a line A -> B -> C -> D
and two earlier attempts at C left on B.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    AUDIT_EVENT,
    INTENT_COMPILATION,
    STUDIO_CANDIDATE_DELTA,
    STUDIO_MODEL_ANNOTATIONS,
    STUDIO_RENDER_JOB,
    STUDIO_WORKING_COPY,
)
from archflow.project.repository import RunNotTrashed
from project_runtime.application.artifacts import ModelSource, save_document
from project_runtime.application.boards import BOARD_RUN_ID
from project_runtime.application.retention import (
    DESIGN_CLEANED,
    DESIGN_RESTORED,
    RETENTION_RUN_ID,
    RULE_FAILED,
    RULE_REPLACED,
    RULE_SUPERSEDED,
    RetentionSweeps,
    clean_superseded,
    plan_cleaning,
)
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.protocol import BASE_CAPABILITIES
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate_admission import AdmissionFixture
from .test_rendering import png

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def records(run_id: str, area: PersistenceArea = PersistenceArea.RUN_RECORD) -> PersistenceDestination:
    return PersistenceDestination(area, run_id=run_id)


class RetentionFixture(AdmissionFixture):
    def line(self, *, attempts: int = 2) -> tuple[str, str, str, str, tuple[str, ...]]:
        """A -> B -> C -> D: ``attempts`` drafts were built on B before C, also built on B, was continued."""

        a = REFERENCE_RUN_ID
        b = self.result(height=2.2)
        self.adopt(b)
        drafts = tuple(self.result(source=b, height=round(2.6 + index / 1000, 3)) for index in range(attempts))
        c = self.result(source=b, height=2.7)
        self.adopt(c)
        d = self.result(source=c, height=2.8)
        self.adopt(d)
        return a, b, c, d, drafts

    def sweep(self, now: datetime = NOW):
        return clean_superseded(bound_project(self.app.state), now=now, jobs=self.app.state.jobs)

    def trash(self, client: TestClient | None = None) -> dict:
        response = (client or self.client).get("/api/trash")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def restore(self, run_id: str, expect: int = 200) -> dict:
        response = self.client.post("/api/trash/restore", json={"projectId": PROJECT_ID, "runId": run_id})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def graph(self, client: TestClient | None = None) -> dict:
        response = (client or self.client).get("/api/worktrees")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def retained_events(self, action: str) -> list[dict]:
        if RETENTION_RUN_ID not in self.repository.run_ids():
            return []
        run = self.repository.load_run(RETENTION_RUN_ID)
        payloads = [self.repository.load_json(ref) for ref in self.repository.list_json(
            run=run, destination=records(RETENTION_RUN_ID, PersistenceArea.RUN_REVIEW), record_kind=AUDIT_EVENT)]
        return [payload for payload in payloads if payload["action"] == action]

    def row(self, run_id: str) -> dict | None:
        return self.repository.read_working_draft()[0]["runs"].get(run_id)


class CleaningTests(RetentionFixture):
    def test_the_drafts_the_line_moved_past_go_to_the_trash_and_come_back(self) -> None:
        a, b, c, d, drafts = self.line()
        states = {draft: self.state_of(draft)["stateDigest"] for draft in drafts}
        rows = {draft: self.row(draft) for draft in drafts}
        # The words that asked for the first draft, as an intent model compiled them into its run.
        self.repository.put_json(run=self.repository.load_run(drafts[0]), record_kind=INTENT_COMPILATION,
                                 destination=records(drafts[0]),
                                 payload={"schema": "IntentCompilation@1", "proposal_id": "proposal-x",
                                          "utterance": "Close the north wall", "base_state_digest": "0" * 64, "receipt": {}})
        self.assertEqual({row["runId"]: row["relation"] for row in self.graph()["lines"] if row["kind"] == "result"},
                         dict.fromkeys(drafts, "superseded"))
        before = (self.repository.read_head(), self.repository.read_design_branches())
        self.assertIn("project-trash", BASE_CAPABILITIES)

        sweep = self.sweep()
        self.assertEqual(sorted(entry.run_id for entry in sweep.cleaned), sorted(drafts))
        for draft in drafts:
            self.assertNotIn(draft, self.repository.run_ids())
            self.assertTrue(self.repository.layout.trashed_run(draft).is_dir())
            self.assertIsNone(self.row(draft))
        trash = self.trash()
        self.assertEqual((trash["projectId"], trash["retentionDays"]), (PROJECT_ID, 30))
        entries = {entry["runId"]: entry for entry in trash["entries"]}
        self.assertEqual(set(entries), set(drafts))
        for draft in drafts:
            entry = entries[draft]
            self.assertEqual((entry["rule"], entry["supersededBy"], entry["baseRunId"], entry["stateDigest"]),
                             (RULE_SUPERSEDED, c, b, states[draft]))
            self.assertEqual((entry["trashedAt"], entry["expiresAt"]), (NOW.isoformat(), (NOW + timedelta(days=30)).isoformat()))
            self.assertEqual(entry["label"], "Close the north wall" if draft == drafts[0] else None)
            self.assertIn(c, entry["reason"])
        # The line, its head, the design branches and the issued version are as they were.
        graph = self.graph()
        self.assertEqual([step["runId"] for step in graph["line"]], [a, b, c, d])
        self.assertEqual(graph["head"]["runId"], d)
        self.assertEqual([row for row in graph["lines"] if row["kind"] == "result"], [])
        self.assertEqual((self.repository.read_head(), self.repository.read_design_branches()), before)
        self.assertTrue({b, c, d} <= set(self.repository.run_ids()))
        [cleaned] = self.retained_events(DESIGN_CLEANED)
        self.assertEqual(sorted(row["runId"] for row in cleaned["runs"]), sorted(drafts))
        self.assertEqual((cleaned["actorId"], cleaned["origin"], cleaned["trigger"]), ("studio:retention-rule", "runtime", "open"))

        answer = self.restore(drafts[0])
        self.assertEqual(answer["restored"], [drafts[0]])
        self.assertEqual([entry["runId"] for entry in answer["trash"]["entries"]], [drafts[1]])
        self.assertIn(drafts[0], self.repository.run_ids())
        self.assertEqual(self.row(drafts[0]), rows[drafts[0]])
        self.assertEqual({row["runId"]: (row["relation"], row["supersededBy"]) for row in self.graph()["lines"]
                          if row["kind"] == "result"}, {drafts[0]: ("superseded", c)})
        [restored] = self.retained_events(DESIGN_RESTORED)
        self.assertEqual(restored["runs"][0]["runId"], drafts[0])
        # What a person restored stays: the events that name it keep it from the next sweep.
        again = self.sweep(NOW + timedelta(hours=1))
        self.assertEqual(again.cleaned, ())
        self.assertIn(RETENTION_RUN_ID, again.kept[drafts[0]])
        self.assertEqual(self.restore("studio-cand-nothing", expect=404)["code"], "TRASH_ENTRY_NOT_FOUND")

    def test_the_line_its_admitted_results_and_its_stages_are_never_cleaned(self) -> None:
        stage = self.stage("S0")
        b = self.result(stage, 2.2)
        self.adopt(b)
        draft = self.result(source=b, height=2.6)
        admitted = self.result(source=b, height=2.65)
        self.admit({"runId": admitted, "outcome": "admitted", "label": "Option B"})
        c = self.result(source=b, height=2.7)
        self.adopt(c)
        d = self.result(source=c, height=2.8)
        self.adopt(d)
        plan = plan_cleaning(bound_project(self.app.state), jobs=self.app.state.jobs)
        self.assertEqual([run.run_id for run in plan.runs], [draft])
        sweep = self.sweep()
        self.assertEqual([entry.run_id for entry in sweep.cleaned], [draft])
        for kept in (stage["candidateId"], b, c, d, admitted):
            self.assertIn(kept, self.repository.run_ids())
        # The repository itself refuses every step of the line, the admitted result's Continue-less run aside.
        for step in (b, c, d):
            with self.assertRaises(RunNotTrashed):
                self.repository.trash_run(step, now=NOW.isoformat(), rule=RULE_SUPERSEDED, reason="no")
        with self.assertRaises(RunNotTrashed):
            self.repository.trash_run(stage["candidateId"], now=NOW.isoformat(), rule=RULE_SUPERSEDED, reason="no")

    def test_each_reference_keeps_a_draft(self) -> None:
        binding = bound_project(self.app.state)
        a, b, c, d, drafts = self.line(attempts=12)
        (free, saved, board, page, pinned, render, annotated, copied, rejected, studied, parent, decided) = drafts
        # A saved version.
        position = self.client.get("/api/working-draft").json()
        response = self.client.post("/api/working-draft/save", json={
            "projectId": PROJECT_ID, "runId": saved, "baseRevisionSha256": position["revisionSha256"], "label": "V3"})
        self.assertEqual(response.status_code, 200, response.text)
        # A Board component-info card that applies to it.
        card = {"id": "component-info:materials", "type": "rectangle", "x": 0, "y": 0, "width": 120, "height": 60,
                "customData": {"componentInfo": {"schema": "MonkeyHubComponentInfo@1", "id": "materials", "title": "Materials",
                               "appliesTo": {"projectId": PROJECT_ID, "runId": board,
                                             "stateDigest": self.state_of(board)["stateDigest"]}}}}
        response = self.client.put("/api/board", json={"projectId": PROJECT_ID, "baseRevisionSha256": None,
                                                       "title": "MonkeyBoard", "elements": [card], "seenDocuments": []})
        self.assertEqual(response.status_code, 200, response.text)
        # A drawing page registered in its own run, and one elsewhere pinned to its model.
        image = base64.b64encode(png()).decode()
        save_document(binding, page, "plan.png", "image/png", image)
        save_document(binding, None, "view.png", "image/png", image, model_source=ModelSource.from_dict(self.models[pinned]))
        # A render attempt that read its model, annotations on its model, a working copy based on it.
        self.repository.create_run("studio-render-attempts")
        self.repository.put_json(run=self.repository.load_run("studio-render-attempts"), record_kind=STUDIO_RENDER_JOB,
                                 destination=records("studio-render-attempts"),
                                 payload={"schema": "StudioRenderJob@2", "source": {"modelSource": self.models[render]}})
        self.repository.put_json(run=self.repository.load_run(annotated), record_kind=STUDIO_MODEL_ANNOTATIONS,
                                 destination=records(annotated), payload={"schema": "StudioModelAnnotations@1", "marks": []})
        self.repository.put_json(run=self.repository.load_run(copied), record_kind=STUDIO_WORKING_COPY,
                                 destination=records(copied), payload={"schema": "StudioWorkingCopy@1", "options": []})
        # A person's rejection, a Study built on it, a decision that names it.
        self.admit({"runId": rejected, "outcome": "rejected", "reason": "too heavy"}, rawLanguage="No, too heavy")
        built = self.result(source=studied, height=2.9)
        self.admit({"runId": built, "outcome": "admitted"}, study={"id": "roof", "label": "Roof", "baseRunId": studied})
        child = self.result(source=parent, height=2.95)
        position = self.client.get("/api/working-draft").json()
        response = self.client.post("/api/working-draft/save", json={
            "projectId": PROJECT_ID, "runId": child, "baseRevisionSha256": position["revisionSha256"], "label": "Kept child"})
        self.assertEqual(response.status_code, 200, response.text)
        self.repository.create_run("studio-decisions-test")
        self.repository.put_json(run=self.repository.load_run("studio-decisions-test"), record_kind=AUDIT_EVENT,
                                 destination=records("studio-decisions-test", PersistenceArea.RUN_REVIEW),
                                 payload={"schema": "AuditEvent@1", "action": "decision.noted", "subjectRunId": decided})

        sweep = self.sweep()
        self.assertEqual([entry.run_id for entry in sweep.cleaned], [free])
        expected = {
            saved: "saved version", board: f"{BOARD_RUN_ID} names it", page: "studio-source-document",
            pinned: "studio-documents names it", render: "studio-render-attempts names it",
            annotated: STUDIO_MODEL_ANNOTATIONS, copied: STUDIO_WORKING_COPY, rejected: "rejected",
            parent: f"{child} names it", decided: "studio-decisions-test names it", child: "saved version",
        }
        for run_id, words in expected.items():
            self.assertIn(words, sweep.kept.get(run_id, ""), f"{run_id} should stay because of {words}")
            self.assertIn(run_id, self.repository.run_ids())
        # A Study built on a draft took it further: the line does not supersede it, and it is never considered.
        self.assertNotIn(studied, sweep.kept)
        self.assertEqual({row["runId"]: row["relation"] for row in self.graph()["lines"] if row["runId"] in (studied, built)},
                         {studied: "diverged", built: "diverged"})
        self.assertTrue({studied, built} <= set(self.repository.run_ids()))
        self.assertNotIn(free, self.repository.run_ids())


class AttemptTests(RetentionFixture):
    def test_failed_and_replaced_attempts_go_and_an_attempt_a_result_grew_from_stays(self) -> None:
        a, b, c, d, _ = self.line(attempts=0)
        # A design change whose run never finished: its change is retained, its receipt never was.
        failed = "studio-cand-failed"
        run = self.repository.create_run(failed)
        delta = self.repository.load_json(next(ref for ref in bound_project(self.app.state).record_refs(d)
                                                if ref.record_kind == STUDIO_CANDIDATE_DELTA))
        self.repository.put_json(run=run, record_kind=STUDIO_CANDIDATE_DELTA, destination=records(failed),
                                 payload={**delta, "run_id": failed})
        # Two attempts from the head: the second replaced the first, and was admitted.
        replaced = self.result(source=d, height=3.0)
        result = self.result(source=d, height=3.1)
        # Another loop repaired its attempt: the admitted repair grew from it.
        grown_from = self.result(source=d, height=3.2)
        repair = self.result(source=grown_from, height=3.3)
        self.admit({"runId": result, "outcome": "admitted", "supersedes": [replaced], "label": "Taller"},
                   {"runId": repair, "outcome": "admitted", "supersedes": [grown_from], "label": "Repaired"})
        sweep = self.sweep()
        entries = {entry.run_id: entry for entry in sweep.cleaned}
        self.assertEqual(set(entries), {failed, replaced})
        self.assertEqual((entries[failed].rule, entries[failed].superseded_by, entries[failed].base_run_id),
                         (RULE_FAILED, None, delta["source_run_ref"]["run_id"]))
        self.assertIn("never finished", entries[failed].reason)
        self.assertEqual((entries[replaced].rule, entries[replaced].superseded_by), (RULE_REPLACED, result))
        self.assertIn("kept line", sweep.kept[grown_from])
        for kept in (result, repair, grown_from, d):
            self.assertIn(kept, self.repository.run_ids())


class WhenItRunsTests(RetentionFixture):
    def test_open_cleans_once_the_index_has_loaded_and_purges_after_thirty_days(self) -> None:
        a, b, c, d, drafts = self.line()
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        clock = {"now": NOW}
        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off",
                                        cache_dir=cache / "projects" / "runtime-retention"))
        app.state.retention = RetentionSweeps(app.state, clock=lambda: clock["now"])
        client = TestClient(app)
        self.addCleanup(client.close)
        self.addCleanup(app.state.stop_index_events)
        with client:
            binding = bound_project(app.state)
            self.addCleanup(binding.close)
            self.assertTrue(app.state.retention.wait(120), "the sweep at open ends")
            self.assertIsNotNone(binding.await_index(30), "the index loaded")
            self.assertEqual(sorted(entry["runId"] for entry in self.trash(client)["entries"]), sorted(drafts))
            reader = binding.index_reader(wait=10)
            self.assertIsNotNone(reader)
            indexed = {row["run_id"] for row in reader.query("run")}
            self.assertFalse(set(drafts) & indexed, "the index forgot the trashed runs")
            self.assertTrue({b, c, d} <= indexed)
            self.assertEqual(app.state.retention.last.trigger, "open")

            # Restored, a run is seen again; thirty days on, the rest is purged at the next open.
            restored = client.post("/api/trash/restore", json={"projectId": PROJECT_ID, "runId": drafts[0]})
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertIn(drafts[0], {row["run_id"] for row in binding.index_reader(wait=10).query("run")})
            clock["now"] = NOW + timedelta(days=30)
            app.state.retention.at_open()
            self.assertTrue(app.state.retention.wait(60))
            self.assertEqual(app.state.retention.last.purged, ())
            clock["now"] = NOW + timedelta(days=30, seconds=1)
            app.state.retention.at_open()
            self.assertTrue(app.state.retention.wait(60))
            self.assertEqual(app.state.retention.last.purged, (drafts[1],))
            self.assertEqual(self.trash(client)["entries"], [])
            self.assertNotIn(drafts[1], self.repository.run_ids())
            self.assertFalse(self.repository.layout.trashed_run(drafts[1]).exists())
            self.assertIn(drafts[0], self.repository.run_ids(), "what was restored stays")

    def test_a_continue_cleans_what_the_line_now_moves_past(self) -> None:
        self.app.state.retention = RetentionSweeps(self.app.state, clock=lambda: NOW)
        b = self.result(height=2.2)
        self.adopt(b)
        self.assertTrue(self.app.state.retention.wait(60))
        drafts = (self.result(source=b, height=2.6), self.result(source=b, height=2.65))
        c = self.result(source=b, height=2.7)
        self.assertEqual(self.trash()["entries"], [], "nothing is superseded until the line moves on")
        self.adopt(c)
        self.assertTrue(self.app.state.retention.wait(60))
        self.assertEqual(self.app.state.retention.last.trigger, "continue")
        self.assertEqual(sorted(entry["runId"] for entry in self.trash()["entries"]), sorted(drafts))
        [cleaned] = self.retained_events(DESIGN_CLEANED)
        self.assertEqual(cleaned["trigger"], "continue")
