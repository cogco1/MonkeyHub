"""A card's answer runs as the person's own request, once, and can be taken back while the project still stands there."""

from datetime import datetime, timezone
import json
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

from monkeyhub_api.attention.actions import COMMANDS, OPENS, AttentionExecutor, action_router, check_card
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
        self.unrestorable: set[str] = set()
        self.decisions: list[dict] = []
        self.revoked: list[dict] = []
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
            if body["subjectRef"] in self.unrestorable and body["action"] == "restore":
                raise HubFailure(503, "WORKER_UNAVAILABLE", "The project's Studio is not ready.")
            self.reviews[body["subjectRef"]] = "archived" if body["action"] == "archive" else "unreviewed"
            return {"disposition": self.reviews[body["subjectRef"]]}
        if path == "/api/candidates/combine":
            return {"candidateId": "run-combined"}
        if path == "/api/decisions":
            self.decisions.append(dict(body))
            return {"decisionId": "dec-1", "revisionRef": "project://p/runs/studio-decisions/rev-1"}
        if path == "/api/decisions/dec-1/revisions":
            self.revoked.append(dict(body))
            return {"decisionId": "dec-1"}
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

    def conversation(self, project_dir):
        return "c-attention"

    def acknowledge_operation(self, project_id, project_dir, operation_id):
        self.acknowledged.append(operation_id)

    def apply_update(self):
        self.applied += 1

    def restart_project(self, project_id, project_dir):
        self.restarted = (project_id, project_dir)


def option(option_id, label, command, *, undoable=False, recommended=False, **args):
    return {"id": option_id, "label": label, "consequence": label + "，看后果", "undoable": undoable,
            "recommended": recommended, "action": {"commandId": command, "args": args}}


def ab_card(study="s1", recommend=None):
    options = [option("a", "留 A", "design.go_to_step.continue", undoable=True, recommended=recommend == "a", runId="run-v16"),
               option("b", "换成 B", "design.go_to_step.continue", undoable=True, recommended=recommend == "b",
                      runId=f"run-b-{study}", branchId=None),
               option("archive", "都不要", "candidate.review.archive", undoable=True, runIds=["run-a", "run-b"])]
    card = {"channel": "decide", "kind": "ab", "title": "V16 和斜撑版，留哪个", "dedupeKey": f"result:study:{study}",
            "source": {"studyId": study}, "options": options}
    if recommend:
        card["recommendation"] = {"optionId": recommend, "reason": "检查相同，构件少 4 个"}
    return AttentionDraft.model_validate(card)


class CommandTreeTests(unittest.TestCase):
    def test_what_a_card_may_open_the_command_tree_marks_as_opening(self):
        registry = json.loads((ROOT / "governance" / "module_registry.json").read_text(encoding="utf-8"))
        commands = {command["id"]: command for capability in registry.get("capabilities", ())
                    for command in (capability.get("ui") or {}).get("commands", ())}
        # The tree marks every command that only opens something; a card offers a few of them.
        self.assertEqual({key for key in set(OPENS) & set(commands) if not commands[key].get("opens")}, set(),
                         "a card opens only what the command tree marks as opening")
        self.assertEqual({key for key in set(COMMANDS) & set(commands) if commands[key].get("opens")}, set(),
                         "an answer is never a command that only opens something")


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
        self.assertEqual(asked.followUp.dedupeKey, f"undo:{card}:1")
        self.assertEqual([row.id for row in asked.followUp.options], ["back", "keep"])
        self.executor.resolve(asked.followUp.id, "keep")
        again = self.executor.undo(card)
        self.assertNotEqual(again.followUp.id, asked.followUp.id, "a second undo asks on a card of its own")
        self.assertEqual(again.followUp.state, "open")
        self.executor.resolve(again.followUp.id, "back")
        self.assertEqual(self.studio.current["runId"], "run-v16")

    def test_an_answer_a_restart_cut_short_is_finished_without_writing_again(self):
        card = self.card(ab_card())
        self.store.begin(card, "b")
        self.store.progress(card, {"targetRunId": "run-b-s1", "previousRunId": "run-v16", "previousBranchId": "main"})
        self.studio.current = {"runId": "run-b-s1", "branchId": None}  # the PUT landed, the Hub stopped
        restarted = AttentionStore(self.store.root)
        executor = AttentionExecutor(restarted, studio=self.studio, chats=self.chats, hub=self.hub)
        self.assertEqual(restarted.get(card).state, "open")
        writes = len(self.studio.writes())
        done = executor.resolve(card, "b").item
        self.assertEqual(len(self.studio.writes()), writes, "nothing is written again")
        self.assertEqual(done.resolution.undo.action.args, {"runId": "run-v16", "branchId": "main"})
        executor.undo(card)
        self.assertEqual(self.studio.current["runId"], "run-v16")

    def test_a_card_is_held_to_what_its_buttons_do(self):
        store = AttentionStore(None, check=check_card)
        restart = option("go", "重启", "hub.update_apply", undoable=True)
        for draft in (
            {"options": [restart]},
            {"options": [option("go", "重置", "board.reset_canvas")]},
            {"opens": [{"commandId": "design.go_to_step.continue", "args": {"runId": "run-x"}}]},
            {"opens": [{"commandId": "candidate.compare.decide", "args": {}}]},
            {"opens": [{"commandId": "chat.open_conversation", "args": {"sessionId": "c1", "messageId": "m1"}}]},
            # The guess cards' first draft: a sentence under another name, and no conversation to send it to.
            {"options": [option("go", "再调小一步", "chat.composer.send", text="梁高再降 50")]},
            {"options": [option("go", "换成它", "design.go_to_step.continue")]},
        ):
            with self.assertRaises(HubFailure) as refused:
                store.upsert("thi-hemp", self.project_dir, AttentionDraft.model_validate({
                    "channel": "decide", "kind": "choice", "title": "t", "dedupeKey": "x:1", **draft}))
            self.assertEqual(refused.exception.error.code, "ATTENTION_CARD_INVALID")
        self.assertEqual(store.items(), [])

        # A card written past the check still cannot make "all as recommended" restart the Hub.
        unchecked = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "t", "dedupeKey": "x:2",
            "options": [{**restart, "recommended": True}], "recommendation": {"optionId": "go", "reason": "r"}}))
        result = self.executor.bulk([unchecked])
        self.assertEqual([(row.id, row.code) for row in result.skipped], [(unchecked, "ATTENTION_NOT_UNDOABLE")])
        self.assertEqual(self.hub.applied, 0)

    def test_restarting_from_a_card_starts_its_own_projects_service(self):
        card = self.card(AttentionDraft.model_validate({
            "channel": "notice", "kind": "info", "title": "项目服务停着", "dedupeKey": "repair:r9",
            "options": [option("restart", "重新启动", "attention.queue.restart_service")]}))
        self.assertEqual(self.executor.resolve(card, "restart").item.state, "done")
        self.assertEqual(self.hub.restarted, ("thi-hemp", self.project_dir))

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

        self.studio.missing = {"run-b"}
        self.studio.unrestorable = {"run-a"}
        with self.assertRaises(HubFailure) as partly:
            self.executor.resolve(card, "archive")
        self.assertEqual(partly.exception.error.code, "ATTENTION_PARTLY_DONE")
        self.assertIn("run-a", partly.exception.error.detail)
        left = next(item for item in self.store.items() if item.dedupeKey.startswith("partly:"))
        self.assertEqual(left.options[0].action.args, {"runIds": ["run-a"]})
        self.assertIn("ATTENTION_PARTLY_DONE", self.store.get(card).history[-1].note)

        self.studio.missing, self.studio.unrestorable = set(), set()
        self.executor.resolve(card, "archive")
        self.assertEqual(self.studio.reviews, {"run-a": "archived", "run-b": "archived"})
        self.executor.undo(card)
        self.assertEqual(self.studio.reviews, {"run-a": "unreviewed", "run-b": "unreviewed"})

    def test_conversation_answers_go_to_the_chat(self):
        permission = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "「改楼板」请求：写入 runs/", "dedupeKey": "perm:c1:p1",
            "options": [option("o0", "允许这一次", "attention.queue.answer_permission", sessionId="c1", permissionId="p1",
                               optionId="allow")]}))
        self.executor.resolve(permission, "o0")
        self.assertEqual(self.chats.permissions, [("c1", "p1", "allow")])
        with self.assertRaises(HubFailure):
            self.executor.undo(permission)

        guess = self.card(AttentionDraft.model_validate({
            "channel": "guess", "kind": "suggestion", "title": "出一套期中评图的图", "dedupeKey": "sug:m1",
            "options": [option("go", "就这样做", "attention.queue.answer_guess", sessionId="c1",
                               messageId="0b8f2b49-6f4e-4d6a-9d36-3c1b5a1e2f10", revision=2)]}))
        self.executor.resolve(guess, "go")
        session, request = self.chats.posts[-1]
        self.assertEqual((session, request.suggestionSelection.revision, request.content), ("c1", 2, ""))

        question = self.card(AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "西门廊要保留原来的柱距吗", "dedupeKey": "dlq:c1:m2",
            "options": [option("answer", "回答", "chat.composer.send", sessionId="c1")]}))
        with self.assertRaises(HubFailure) as empty:
            self.executor.resolve(question, "answer")
        self.assertEqual(empty.exception.error.code, "ATTENTION_ACTION_INVALID")
        self.assertEqual(self.store.get(question).state, "open")
        self.executor.resolve(question, "answer", note="保留，柱距不动")
        self.assertEqual(self.chats.posts[-1][1].content, "保留，柱距不动")

        guess = self.card(AttentionDraft.model_validate({
            "channel": "guess", "kind": "ask", "title": "Lower the beam again?", "dedupeKey": "rule:edit:beam-3",
            "options": [option("go", "再调小一步", "chat.composer.send", content="梁高再降 50")]}))
        self.executor.resolve(guess, "go")
        self.assertEqual((self.chats.posts[-1][0], self.chats.posts[-1][1].content), ("c-attention", "梁高再降 50"),
                         "a card that names no conversation answers into the project's own")

    def test_a_recipe_guess_keeps_the_value_as_the_project_recipe_and_can_be_taken_back(self):
        page = {"runId": "run-draw", "assetSha256": "a" * 64, "revisionRef": "project://p/drawings/r1", "pageIndex": 0}
        card = self.card(AttentionDraft.model_validate({
            "channel": "guess", "kind": "ask", "title": "Keep 0.35 mm cut lines?", "dedupeKey": "recipe:cut",
            "options": [{"id": "save", "label": "Keep it", "consequence": "Cut lines are 0.35 mm on every drawing",
                         "consequenceZh": "以后所有图纸剖切线都是 0.35 mm", "undoable": True,
                         "action": {"commandId": "attention.answer_guess.save",
                                    "args": {"field": "cutLineMm", "value": 0.35, **page}}}]}))
        done = self.executor.resolve(card, "save").item
        kept, = self.studio.decisions
        self.assertEqual((kept["targetRef"], kept["typedBinding"], kept["source"]["pageIndex"], kept["rawLanguage"]),
                         ("drawing:lineweight", {"kind": "recipe", "graphics": {"cutLineMm": 0.35}}, 0,
                          "以后所有图纸剖切线都是 0.35 mm"))
        self.assertEqual(done.resolution.undo.action.args["expectedRevisionRef"], "project://p/runs/studio-decisions/rev-1")
        self.executor.undo(card)
        self.assertEqual((self.studio.revoked[0]["action"], self.studio.revoked[0]["expectedRevisionRef"]),
                         ("revoke", "project://p/runs/studio-decisions/rev-1"))

        wrong = self.card(AttentionDraft.model_validate({
            "channel": "guess", "kind": "ask", "title": "t", "dedupeKey": "recipe:x",
            "options": [option("save", "Keep", "attention.answer_guess.save", field="wallThickness", value=1, **page)]}))
        with self.assertRaises(HubFailure) as refused:
            self.executor.resolve(wrong, "save")
        self.assertEqual(refused.exception.error.code, "ATTENTION_ACTION_INVALID")

    def test_notices_are_read_and_put_away(self):
        failed = self.card(AttentionDraft.model_validate({
            "channel": "notice", "kind": "info", "title": "改模型没有完成", "dedupeKey": "op:o1:failed",
            "options": [option("ok", "知道了", "attention.queue.acknowledge", operationId="o1")]}))
        self.executor.resolve(failed, "ok")
        self.assertEqual(self.hub.acknowledged, ["o1"])
        plain = self.card(AttentionDraft.model_validate({
            "channel": "notice", "kind": "info", "title": "出图完成", "dedupeKey": "info:1",
            "options": [option("ok", "知道了", "attention.queue.acknowledge")]}))
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
        self.assertEqual({command for command, row in COMMANDS.items() if row.undoable},
                         {"design.go_to_step.continue", "candidate.review.archive", "candidate.review.restore",
                          "attention.answer_guess.save"})

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
