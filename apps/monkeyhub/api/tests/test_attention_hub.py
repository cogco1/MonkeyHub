"""Joined to the Hub, a card's answer is the person's own request, and the Hub's events bring the cards up to date."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from project_runtime.events import StudioEvents

from monkeyhub_api.attention.hub import AttentionHub
from monkeyhub_api.attention.store import AttentionStore, runtime_id
from monkeyhub_api.models import ChatDetail, HubFailure
from monkeyhub_api.runtime.models import OperationRecord
from monkeyhub_api.runtime.worker_http import HttpResult
from monkeyhub_api.updates.models import UpdateStatus

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def answer(status: int, value: dict) -> HttpResult:
    return HttpResult(status, json.dumps(value).encode("utf-8"), {"content-type": "application/json"})


class FakeRuntimes:
    """The parts of ProjectRuntimeManager the queue uses."""

    def __init__(self, project_dir: str):
        self.events = StudioEvents(buffer_size=64)
        self.runtime = SimpleNamespace(project_id="thi-hemp", project_dir=project_dir,
                                       runtime_id=runtime_id("thi-hemp", project_dir),
                                       operations=SimpleNamespace(records=lambda: list(self.records)))
        self.records: list[OperationRecord] = []
        self.graph: dict | None = None
        self.current = {"runId": "run-v16", "branchId": "main"}
        self.forwarded: list[tuple] = []
        self.acknowledged: list[str] = []
        self.sources: list[str | None] = []

    def open(self, project_id, project_dir):
        return self.runtime

    def get(self, key, project_id=None):
        if key != self.runtime.runtime_id:
            raise HubFailure(404, "RUNTIME_NOT_FOUND", "Open the project's runtime first.")
        return self.runtime

    def acknowledge(self, runtime, operation_id):
        self.acknowledged.append(operation_id)

    def forward(self, runtime, path, method, body, headers, *, source=None):
        self.forwarded.append((method, path, json.loads(body) if body else None, dict(headers)))
        self.sources.append(source)
        if path == "/api/worktrees":
            if self.graph is None:
                raise HubFailure(503, "WORKER_UNAVAILABLE", "The project's Studio is not ready.")
            return answer(200, self.graph)
        if path == "/api/working-draft" and method == "GET":
            return answer(200, {"projectId": "thi-hemp", "revisionSha256": "rev-0", "current": self.current})
        if path == "/api/working-draft" and method == "PUT":
            self.current = {"runId": json.loads(body)["runId"], "branchId": None}
            return answer(200, {"projectId": "thi-hemp"})
        return answer(409, {"code": "CANDIDATE_REVIEW_INVALID", "detail": "Choose an exact Candidate."})


class FakeChats:
    def __init__(self, sessions: list[ChatDetail]):
        self.sessions = sessions
        self.created = []

    def providers(self):
        return [SimpleNamespace(id="codex", available=False), SimpleNamespace(id="claude", available=True)]

    def create(self, request):
        self.created.append(request)
        session = self.sessions[0].model_copy(update={"id": f"c-new-{len(self.created)}", "title": request.title,
                                                      "provider": request.provider, "archived": False, "messages": []})
        self.sessions.append(session)
        return session

    def list(self, project_id=None, *, archived=False):
        return [row for row in self.sessions if project_id in (None, row.projectId)]

    def get(self, session_id):
        return next(row for row in self.sessions if row.id == session_id)

    def presentation_token(self, session_id):
        return f"token-{session_id}"

    def resolve_permission(self, session_id, permission_id, request):
        self.answered = (session_id, permission_id, request.optionId)

    def post(self, session_id, request):
        self.posted = (session_id, request)


def waiting_chat(project_dir: str) -> ChatDetail:
    return ChatDetail.model_validate({
        "id": "c1", "projectId": "thi-hemp", "projectDir": project_dir, "title": "改楼板", "provider": "claude",
        "status": "running", "createdAt": NOW.isoformat(), "updatedAt": NOW.isoformat(), "attention": "permission",
        "messages": [{"id": "m1", "role": "tool", "content": "", "createdAt": NOW.isoformat(), "permission": {
            "id": "p1", "title": "写入 runs/", "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}]}}]})


def result_line(run: str) -> dict:
    return {"kind": "result", "runId": run, "status": "ready", "admission": "none", "relation": "diverged",
            "reconcile": "unknown", "studyId": "s1", "updatedAt": NOW.isoformat()}


class AttentionHubTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_dir = str(Path(temporary.name) / "thi-hemp")
        Path(self.project_dir).mkdir()
        self.store = AttentionStore(Path(temporary.name) / "attention")
        self.runtimes = FakeRuntimes(self.project_dir)
        self.chats = FakeChats([waiting_chat(self.project_dir)])
        self.hub = AttentionHub(self.store, self.runtimes, self.chats, clock=lambda: NOW)

    def test_a_project_answer_is_the_persons_own_request(self):
        self.hub.studio("thi-hemp", self.project_dir, "PUT", "/api/working-draft", {"runId": "run-b"},
                        operation_id="0b8f2b49-6f4e-4d6a-9d36-3c1b5a1e2f10")
        method, path, body, headers = self.runtimes.forwarded[-1]
        self.assertEqual((method, path, body), ("PUT", "/api/working-draft", {"runId": "run-b"}))
        self.assertEqual(headers["idempotency-key"], "0b8f2b49-6f4e-4d6a-9d36-3c1b5a1e2f10")
        self.assertNotIn("x-monkey-chat", headers)
        self.assertEqual(self.runtimes.sources[-1], "attention", "the journal says it was a card's answer")
        with self.assertRaises(HubFailure) as refused:
            self.hub.studio("thi-hemp", self.project_dir, "POST", "/api/candidate-reviews", {})
        self.assertEqual((refused.exception.status, refused.exception.error.code), (409, "CANDIDATE_REVIEW_INVALID"))

    def test_a_refresh_writes_what_the_hub_has_as_cards(self):
        self.runtimes.records = [OperationRecord(operationId="o1", projectId="thi-hemp", kind="POST /api/revisions",
                                                 source="studio", status="failed", reason="构件 seat 缺少面")]
        self.runtimes.graph = {"head": {"runId": "run-v16"}, "lines": [result_line("run-a"), result_line("run-b")]}
        self.hub.refresh(self.runtimes.runtime.runtime_id)
        keys = sorted(item.dedupeKey for item in self.store.items())
        self.assertEqual(keys, ["op:o1:failed", "perm:c1:p1", "result:study:s1"])

        # A Studio that stopped says nothing about results: their cards stay as they were.
        self.runtimes.graph = None
        self.chats.sessions = []
        self.hub.refresh(self.runtimes.runtime.runtime_id)
        self.assertEqual(sorted(item.dedupeKey for item in self.store.items()), ["op:o1:failed", "result:study:s1"])
        self.hub.refresh("not-open")  # a project that is not open is left alone

    def test_answers_run_through_the_hub(self):
        self.runtimes.graph = {"head": {"runId": "run-v16"}, "lines": [result_line("run-a"), result_line("run-b")]}
        self.hub.refresh(self.runtimes.runtime.runtime_id)
        card = next(item for item in self.store.items() if item.kind == "ab")
        self.hub.executor.resolve(card.id, "b")
        self.assertEqual(self.runtimes.current["runId"], "run-b")
        permission = next(item for item in self.store.items() if item.dedupeKey == "perm:c1:p1")
        self.hub.executor.resolve(permission.id, "o0")
        self.assertEqual(self.chats.answered, ("c1", "p1", "allow"))

    def test_a_design_loop_question_is_a_card_until_the_architect_writes_again(self):
        chat = self.chats.sessions[0]
        chat.messages.insert(0, chat.messages[0].model_copy(update={"id": "u1", "role": "user", "permission": None}))
        with self.assertRaises(HubFailure) as stranger:
            self.hub.ask("c1", "u1", "西门廊要保留原来的柱距吗", "柱距和新楼梯冲突", token="guessed")
        self.assertEqual((stranger.exception.status, stranger.exception.error.code), (403, "ATTENTION_QUESTION_UNBOUND"))
        with self.assertRaises(HubFailure) as unbound:
            self.hub.ask("c1", "m1", "西门廊要保留原来的柱距吗", "柱距和新楼梯冲突", token="token-c1")
        self.assertEqual(unbound.exception.error.code, "ATTENTION_QUESTION_UNBOUND")
        card = self.hub.ask("c1", "u1", "西门廊要保留原来的柱距吗", "柱距和新楼梯冲突", token="token-c1")
        self.assertEqual((card.dedupeKey, card.projectId), ("dlq:c1:u1", "thi-hemp"))
        self.hub.refresh(self.runtimes.runtime.runtime_id)
        self.assertEqual(self.store.get(card.id).state, "open")

        chat.messages.append(chat.messages[0].model_copy(update={"id": "u2"}))
        self.hub.refresh(self.runtimes.runtime.runtime_id)
        self.assertEqual(self.store.get(card.id).state, "superseded")

    def test_a_prepared_update_waits_in_the_hubs_own_scope(self):
        status = UpdateStatus.model_validate({
            "currentVersion": "0.1.980", "currentRevision": None, "mode": "local", "state": "ready", "canApply": True,
            "prepared": {"targetVersion": "11230e0e1234-desktop", "targetRevision": "a" * 40, "changedBytes": 1,
                         "changedFiles": 1, "removedFiles": 0, "reusedFiles": 0}})
        updates = SimpleNamespace(status=lambda: status, apply=lambda: None)
        hub = AttentionHub(self.store, self.runtimes, self.chats, updates, hub_dir=self.project_dir + "-hub")
        hub.refresh_hub()
        summary = self.store.summary()
        self.assertEqual((summary.hub.open, summary.projects), (1, []))
        card, = self.store.items()
        self.assertEqual((card.projectId, card.dedupeKey), ("monkeyhub", "update:11230e0e1234-desktop"))
        self.assertIs(self.store.check.__name__, "check_card", "the Hub keeps only cards it runs as written")

    def test_the_projects_attention_conversation_is_opened_once_and_kept(self):
        settings = SimpleNamespace(chat_provider="codex", chat_model="gpt-x", language="zh-CN")
        hub = AttentionHub(self.store, self.runtimes, self.chats, user_settings=lambda: settings)
        first = hub.conversation("thi-hemp", self.project_dir)
        self.assertEqual(hub.conversation("thi-hemp", self.project_dir), first, "answered again, not opened again")
        made, = self.chats.created
        self.assertEqual((made.provider, made.model, made.title), ("claude", None, "关注"),
                         "the default provider was unavailable, so the first available one opens it")
        self.assertEqual(self.store.conversation(self.project_dir), first)

        next(row for row in self.chats.sessions if row.id == first).archived = True
        settings.language = "en"
        second = hub.conversation("thi-hemp", self.project_dir)
        self.assertNotEqual(second, first, "an archived one is replaced")
        self.assertEqual(self.chats.created[-1].title, "Attention")

    def test_the_hubs_events_bring_cards_up_to_date_and_say_so(self):
        seen = []
        self.hub.start()
        self.addCleanup(self.hub.stop)
        with self.runtimes.events.subscribe() as (_, inbox):
            self.runtimes.events.publish(event={"kind": "operation/failed", "runtimeId": self.runtimes.runtime.runtime_id})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not seen:
                try:
                    event = inbox.get(True, 0.2)
                except Exception:
                    continue
                if event["kind"] == "attention/changed":
                    seen.append(event)
        self.assertEqual(seen[0]["runtimeId"], self.runtimes.runtime.runtime_id)
        self.assertEqual([item.dedupeKey for item in self.store.items()], ["perm:c1:p1"])



class AttentionCompositionTests(unittest.TestCase):
    def test_the_hub_serves_the_queue_from_its_own_runtime_root(self):
        from fastapi.testclient import TestClient
        from monkeyhub_api.app.composition import HubSettings, create_app

        with tempfile.TemporaryDirectory() as root:
            app = create_app(HubSettings(runtime_root=Path(root)))
            self.assertEqual(app.state.attention.store.root, Path(root) / "attention")
            client = TestClient(app, base_url="http://127.0.0.1:8790")
            self.assertEqual(client.get("/api/attention/summary").json(),
                             {"projects": [], "hub": None, "open": 0, "later": 0, "working": 0, "invalid": []})
            self.assertEqual(app.state.attention.hub_dir, str(Path(root)))
            self.assertEqual(client.post("/api/attention/att_missing/resolve", json={"optionId": "a"}).json()["code"],
                             "ATTENTION_NOT_FOUND")
            paths = app.openapi()["paths"]
            self.assertTrue({"/api/attention", "/api/attention/{item_id}/undo", "/api/attention/questions"} <= set(paths))

if __name__ == "__main__":
    unittest.main()
