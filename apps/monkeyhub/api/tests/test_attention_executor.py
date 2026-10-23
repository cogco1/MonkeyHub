"""A card's answer runs as the person's own request, once, and can be taken back while the project still stands there."""

from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from monkeyhub_api.attention.actions import COMMANDS, AttentionExecutor, action_router
from monkeyhub_api.attention.models import COMMAND_ID, AttentionDraft
from monkeyhub_api.attention.store import AttentionStore
from monkeyhub_api.models import HubFailure

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class FakeStudio:
    """A project's working position and candidate reviews, as the Hub forward answers them."""

    def __init__(self):
        self.current = {"runId": "run-v16", "branchId": "main"}
        self.revision = "rev-0"
        self.local_draft = None
        self.reviews: dict[str, str] = {}
        self.missing: set[str] = set()
        self.stale_once = False
        self.calls: list[tuple] = []

    def __call__(self, project_id, project_dir, method, path, body=None, *, operation_id=None):
        self.calls.append((method, path, dict(body or {}), operation_id))
        if path == "/api/working-draft" and method == "GET":
            return {"projectId": project_id, "revisionSha256": self.revision,
                    "current": dict(self.current) if self.current else None, "localDraft": self.local_draft}
        if path == "/api/working-draft" and method == "PUT":
            if self.stale_once:
                self.stale_once, self.revision = False, "rev-moved"
                raise HubFailure(409, "WORKING_DRAFT_STALE", "The working position changed.")
            if body["baseRevisionSha256"] != self.revision:
                raise HubFailure(409, "WORKING_DRAFT_STALE", "The working position changed.")
            self.current = {"runId": body["runId"], "branchId": body["branchId"]}
            self.revision = f"rev-{len(self.calls)}"
            return {"projectId": project_id, "revisionSha256": self.revision, "current": self.current}
        if path == "/api/candidate-reviews":
            if body["subjectRef"] in self.missing:
                raise HubFailure(404, "CANDIDATE_NOT_FOUND", "The reviewed Candidate is not retained in this project.")
            self.reviews[body["subjectRef"]] = "archived" if body["action"] == "archive" else "unreviewed"
            return {"disposition": self.reviews[body["subjectRef"]]}
        if path == "/api/candidates/combine":
            return {"candidateId": "run-combined"}
        raise AssertionError(f"unexpected request {method} {path}")

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class FakeChats:
    def __init__(self):
        self.permissions, self.posts = [], []

    def resolve_permission(self, session_id, permission_id, request):
        self.permissions.append((session_id, permission_id, request.optionId))

    def post(self, session_id, request):
        self.posts.append((session_id, request))


class FakeHub:
    def __init__(self):
        self.acknowledged, self.applied = [], 0

    def acknowledge_operation(self, project_id, project_dir, operation_id):
        self.acknowledged.append(operation_id)

    def apply_update(self):
        self.applied += 1


def option(option_id, label, command, *, undoable=False, recommended=False, **args):
    return {"id": option_id, "label": label, "consequence": label + "，看后果", "undoable": undoable,
            "recommended": recommended, "action": {"commandId": command, "args": args}}


def ab_card(study="s1", recommend=None):
    options = [option("a", "留 A", "design.go_to_step", undoable=True, recommended=recommend == "a", runId="run-v16"),
               option("b", "换成 B", "design.go_to_step", undoable=True, recommended=recommend == "b",
                      runId=f"run-b-{study}", branchId=None),
               option("archive", "都不要", "candidate.review.archive", undoable=True, runIds=["run-a", "run-b"])]
    card = {"channel": "decide", "kind": "ab", "title": "V16 和斜撑版，留哪个", "dedupeKey": f"result:study:{study}",
            "source": {"studyId": study}, "options": options}
    if recommend:
        card["recommendation"] = {"optionId": recommend, "reason": "检查相同，构件少 4 个"}
    return AttentionDraft.model_validate(card)


class AttentionExecutorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_dir = str(Path(temporary.name) / "thi-hemp")
        Path(self.project_dir).mkdir()
        self.store = AttentionStore(Path(temporary.name) / "attention")
        self.studio, self.chats, self.hub = FakeStudio(), FakeChats(), FakeHub()
        self.executor = AttentionExecutor(self.store, studio=self.studio, chats=self.chats, hub=self.hub)

    def card(self, draft):
        return self.store.upsert("thi-hemp", self.project_dir, draft).id

    def test_adopting_b_is_a_continue_and_undo_returns_to_v16(self):
        card = self.card(ab_card())
        done = self.executor.resolve(card, "b").item
        self.assertEqual(self.studio.current["runId"], "run-b-s1")
        self.assertEqual(done.state, "done")
        self.assertEqual(done.resolution.receipt, {"runId": "run-b-s1", "previousRunId": "run-v16"})
        put = self.studio.writes()[-1]
        self.assertEqual(put[:2], ("PUT", "/api/working-draft"))
        self.assertEqual(put[2], {"projectId": "thi-hemp", "runId": "run-b-s1", "baseRevisionSha256": "rev-0",
                                  "branchId": None})
        self.assertRegex(put[3], UUID, "the operation journal admits only a UUID key")

        back = self.executor.undo(card)
        self.assertIsNone(back.followUp)
        self.assertEqual(back.item.state, "open")
        self.assertEqual(self.studio.current, {"runId": "run-v16", "branchId": "main"})
        with self.assertRaises(HubFailure) as nothing:
            self.executor.undo(card)
        self.assertEqual(nothing.exception.error.code, "ATTENTION_NOT_UNDOABLE")

    def test_undo_after_the_project_moved_on_asks_instead(self):
        card = self.card(ab_card())
        self.executor.resolve(card, "b")
        self.studio.current = {"runId": "run-c", "branchId": "main"}
        writes = len(self.studio.writes())
        asked = self.executor.undo(card)
        self.assertEqual(len(self.studio.writes()), writes, "nothing is forced back")
        self.assertEqual(asked.item.state, "done")
        self.assertEqual(asked.followUp.dedupeKey, f"undo:{card}")
        self.assertEqual([row.id for row in asked.followUp.options], ["back", "keep"])
        self.executor.resolve(asked.followUp.id, "back")
        self.assertEqual(self.studio.current["runId"], "run-v16")

    def test_unrecorded_edits_hold_a_continue_back(self):
        card = self.card(ab_card())
        self.store.later(card)
        self.studio.local_draft = {"updatedAt": "now"}
        with self.assertRaises(HubFailure) as held:
            self.executor.resolve(card, "b")
        self.assertEqual(held.exception.error.code, "DESIGN_TREE_UNSYNCED_EDITS")
        after = self.store.get(card)
        self.assertEqual(after.state, "later", "the card goes back where it was")
        self.assertTrue(after.history[-1].note.startswith("DESIGN_TREE_UNSYNCED_EDITS"))
        self.assertEqual(self.studio.writes(), [])

    def test_a_continue_that_lost_a_race_reads_again(self):
        self.studio.stale_once = True
        card = self.card(ab_card())
        self.executor.resolve(card, "b")
        puts = [call for call in self.studio.writes() if call[1] == "/api/working-draft"]
        self.assertEqual([call[2]["baseRevisionSha256"] for call in puts], ["rev-0", "rev-moved"])
        self.assertNotEqual(puts[0][3], puts[1][3], "each write is its own request")
        self.assertEqual(self.studio.current["runId"], "run-b-s1")

    def test_archive_is_all_or_none_and_can_be_taken_back(self):
        card = self.card(ab_card())
        self.studio.missing = {"run-b"}
        with self.assertRaises(HubFailure):
            self.executor.resolve(card, "archive")
        self.assertEqual(self.studio.reviews, {"run-a": "unreviewed"}, "the first one was put back")
        self.assertEqual(self.store.get(card).state, "open")

        self.studio.missing = set()
        self.executor.resolve(card, "archive")
        self.assertEqual(self.studio.reviews, {"run-a": "archived", "run-b": "archived"})
        self.executor.undo(card)
        self.assertEqual(self.studio.reviews, {"run-a": "unreviewed", "run-b": "unreviewed"})

    def test_conversation_answers_go_to_the_chat(self):
        permission = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "「改楼板」请求：写入 runs/", "dedupeKey": "perm:c1:p1",
            "options": [option("o0", "允许这一次", "chat.answer_permission", sessionId="c1", permissionId="p1",
                               optionId="allow")]}))
        self.executor.resolve(permission, "o0")
        self.assertEqual(self.chats.permissions, [("c1", "p1", "allow")])
        with self.assertRaises(HubFailure):
            self.executor.undo(permission)

        guess = self.card(AttentionDraft.model_validate({
            "channel": "guess", "kind": "suggestion", "title": "出一套期中评图的图", "dedupeKey": "sug:m1",
            "options": [option("go", "就这样做", "attention.answer_guess", sessionId="c1",
                               messageId="0b8f2b49-6f4e-4d6a-9d36-3c1b5a1e2f10", revision=2)]}))
        self.executor.resolve(guess, "go")
        session, request = self.chats.posts[-1]
        self.assertEqual((session, request.suggestionSelection.revision, request.content), ("c1", 2, ""))

        question = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "西门廊要保留原来的柱距吗", "dedupeKey": "dlq:c1:m2",
            "options": [option("answer", "回答", "chat.send_message", sessionId="c1")]}))
        with self.assertRaises(HubFailure) as empty:
            self.executor.resolve(question, "answer")
        self.assertEqual(empty.exception.error.code, "ATTENTION_ACTION_INVALID")
        self.assertEqual(self.store.get(question).state, "open")
        self.executor.resolve(question, "answer", note="保留，柱距不动")
        self.assertEqual(self.chats.posts[-1][1].content, "保留，柱距不动")

    def test_notices_are_read_and_put_away(self):
        failed = self.card(AttentionDraft.model_validate({
            "channel": "notice", "kind": "info", "title": "改模型没有完成", "dedupeKey": "op:o1:failed",
            "options": [option("ok", "知道了", "attention.acknowledge", operationId="o1")]}))
        self.executor.resolve(failed, "ok")
        self.assertEqual(self.hub.acknowledged, ["o1"])
        plain = self.card(AttentionDraft.model_validate({
            "channel": "notice", "kind": "info", "title": "出图完成", "dedupeKey": "info:1",
            "options": [option("ok", "知道了", "attention.acknowledge")]}))
        self.executor.resolve(plain, "ok")
        self.assertEqual(self.executor.undo(plain).item.state, "open")

        put_off = self.card(ab_card("s2"))
        self.store.dismiss(put_off)
        self.assertEqual(self.executor.undo(put_off).item.state, "open")

    def test_only_known_answers_run(self):
        card = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "重置画布吗", "dedupeKey": "x:1",
            "options": [option("go", "重置", "board.reset_canvas")]}))
        with self.assertRaises(HubFailure) as unknown:
            self.executor.resolve(card, "go")
        self.assertEqual((unknown.exception.status, unknown.exception.error.code), (422, "ATTENTION_ACTION_UNKNOWN"))
        self.assertEqual(self.store.get(card).history[-1].to, "open", "nothing started")
        with self.assertRaises(HubFailure) as missing:
            self.executor.resolve(card, "nope")
        self.assertEqual(missing.exception.error.code, "ATTENTION_OPTION_UNKNOWN")
        self.assertTrue(all(re.fullmatch(COMMAND_ID, command) for command in COMMANDS))

    def test_all_as_recommended_runs_once_and_is_taken_back_together(self):
        first = self.card(ab_card("s1", recommend="b"))
        second = self.card(ab_card("s2", recommend="a"))
        none = self.card(ab_card("s3"))
        result = self.executor.bulk([first, second, none])
        self.assertEqual([item.id for item in result.items], [first, second])
        self.assertEqual([(row.id, row.code) for row in result.skipped], [(none, "ATTENTION_NO_RECOMMENDATION")])
        self.assertTrue(all(item.history[-1].by == "bulk" for item in result.items))
        self.assertEqual(self.studio.current["runId"], "run-v16")

        back = self.executor.undo_bulk(result.bulkId)
        self.assertEqual([item.id for item in back.items], [second, first], "newest answer first")
        self.assertEqual(self.studio.current["runId"], "run-v16")
        self.assertEqual({self.store.get(first).state, self.store.get(second).state}, {"open"})
        with self.assertRaises(HubFailure):
            self.executor.undo_bulk(result.bulkId)

    def test_answers_over_http(self):
        app = FastAPI()

        @app.exception_handler(HubFailure)
        async def handle_hub_error(request, exc: HubFailure):
            return JSONResponse(exc.error.model_dump(), status_code=exc.status)

        app.include_router(action_router(self.executor))
        client = TestClient(app)
        card = self.card(ab_card())
        answered = client.post(f"/api/attention/{card}/resolve", json={"optionId": "b"})
        self.assertEqual(answered.json()["item"]["state"], "done")
        self.assertEqual(client.post(f"/api/attention/{card}/undo").json()["item"]["state"], "open")
        self.assertEqual(client.post(f"/api/attention/{card}/resolve", json={"optionId": "x"}).status_code, 422)
        self.assertEqual(client.post("/api/attention/bulk", json={"ids": [card]}).json()["skipped"][0]["code"],
                         "ATTENTION_NO_RECOMMENDATION")


if __name__ == "__main__":
    unittest.main()
