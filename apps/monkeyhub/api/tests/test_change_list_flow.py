"""Circle it, say it, it is changed: the change list from the Agent's reading of the marks to the items it builds (#300 3-3).

The Runtime is a stand-in that answers as GET /api/document-annotations, POST
/api/markup/resolve and GET /api/state do; the chat store is one conversation
whose user message names its marks. The list is checked by the Hub against its
own reading, put on one card, an unsure item asks which part was meant, and
running the card sends only the architect's words with the ticked items.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.events import StudioEvents

from monkeyhub_api.attention.hub import AttentionHub
from monkeyhub_api.attention.models import ChangeListItem
from monkeyhub_api.attention.store import AttentionStore
from monkeyhub_api.chat import store as chat, turn_context
from monkeyhub_api.models import ChatCreateRequest, ChatDetail, ChatPostRequest, HubFailure
from monkeyhub_api.runtime.worker_http import HttpResult

from test_attention_hub import no_settings
from test_chat import FAKE_CLI, wait_for

NOW = datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)
PAGE = {"runId": "run-7", "assetSha256": "a" * 64, "pageIndex": 0, "revisionSha256": "b" * 64}
WORDS = "东墙换成砖。这两根龙骨加高到 0.3"
COMMENT = "除了外墙，其它都刷白"


def answer(status: int, value: dict) -> HttpResult:
    return HttpResult(status, json.dumps(value).encode("utf-8"), {"content-type": "application/json"})


def hit(component: str, element: str | None = None, *, target: bool, share: float, borderline: bool = False) -> dict:
    return {"componentId": component, "elementId": element, "objectIds": [f"obj-{element or component}"],
            "coverage": 1.0 if target else 0.4, "share": share, "central": target, "target": target,
            "borderline": borderline}


READINGS = [
    {"markId": "east", "kind": "circle", "zone": "area", "hits": [hit("wall-east", target=True, share=0.9)],
     "ambiguous": False, "reason": None},
    {"markId": "joists", "kind": "circle", "zone": "area", "ambiguous": True, "reason": "partial",
     "hits": [hit("floor", "joist-2", target=False, share=0.5, borderline=True),
              hit("floor", "joist-1", target=False, share=0.3, borderline=True)]},
]


class FakeRuntimes:
    """ProjectRuntimeManager as the queue reaches a project's Studio: it answers the three reads a list needs."""

    def __init__(self, project_dir: str):
        self.runtime = type("Runtime", (), {"project_id": "thi-hemp", "project_dir": project_dir})()
        self.events = StudioEvents(buffer_size=64)
        self.sent: list[tuple] = []

    def open(self, project_id, project_dir):
        return self.runtime

    def forward(self, runtime, path, method, body, headers, *, source=None, wait=True):
        self.sent.append((method, path, json.loads(body) if body else None))
        route = urlsplit(path)
        if route.path == "/api/document-annotations":
            query = {key: values[0] for key, values in parse_qs(route.query).items()}
            assert query["revisionSha256"] == PAGE["revisionSha256"], "the message's own revision"
            return answer(200, {"annotations": [
                {"id": "east", "kind": "circle", "points": [[0.1, 0.1]], "color": "#ff0000", "lineWidth": 0.003},
                {"id": "joists", "kind": "circle", "points": [[0.5, 0.5]], "color": "#ff0000", "lineWidth": 0.003},
                {"id": "note", "kind": "text", "points": [[0.8, 0.1]], "color": "#000000", "lineWidth": 0.003,
                 "label": "踢脚线也换", "fontSize": 0.02}], "comment": COMMENT})
        if route.path == "/api/markup/resolve":
            return answer(200, {"source": "drawing", "stateDigest": "c" * 64, "readings": READINGS})
        if route.path == "/api/state":
            return answer(200, {"catalog": {"components": [
                {"componentId": "wall-east", "elementIds": []}, {"componentId": "floor", "elementIds": ["joist-1", "joist-2"]},
                {"componentId": "skirting", "elementIds": []}]}})
        return answer(404, {"code": "NOT_FOUND", "detail": path})


class FakeChats:
    def __init__(self, session: ChatDetail):
        self.session = session
        self.posted: list[tuple[str, ChatPostRequest]] = []

    def get(self, session_id):
        if session_id != self.session.id:
            raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
        return self.session

    def presentation_token(self, session_id):
        return f"token-{session_id}"

    def post(self, session_id, request):
        self.posted.append((session_id, request))


def item(n: int, marks: list[str], note: str, understanding: str, targets: list[str] = (), **flags) -> ChangeListItem:
    return ChangeListItem(n=n, markIds=marks, note=note, understanding=understanding, targets=list(targets), **flags)


class ChangeListFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_dir = str(Path(temporary.name) / "thi-hemp")
        Path(self.project_dir).mkdir()
        self.store = AttentionStore(Path(temporary.name) / "attention")
        self.runtimes = FakeRuntimes(self.project_dir)
        self.chats = FakeChats(ChatDetail.model_validate({
            "id": "c1", "projectId": "thi-hemp", "projectDir": self.project_dir, "title": "改东墙", "provider": "claude",
            "status": "running", "createdAt": NOW.isoformat(), "updatedAt": NOW.isoformat(),
            "messages": [{"id": "u1", "role": "user", "content": WORDS, "createdAt": NOW.isoformat(), "markup": PAGE},
                         {"id": "u0", "role": "user", "content": "看看", "createdAt": NOW.isoformat()}]}))
        self.hub = AttentionHub(self.store, self.runtimes, self.chats, clock=lambda: NOW, user_settings=no_settings)

    def propose(self, *items, message="u1", token="token-c1"):
        return self.hub.propose_changes("c1", message, "我这样读你的批注，对吗？", items, token=token)

    def listed(self):
        return [
            item(1, ["east"], "东墙换成砖", "east wall in brick", ["wall-east"]),
            item(2, ["joists"], "这两根龙骨加高到 0.3", "raise the circled joists to 0.3 m", unsure=True),
            item(3, ["east"], "踢脚线也换", "the skirting beside it too", ["skirting"], extra=True),
        ]

    def test_a_list_the_marks_and_words_bear_out_rides_on_one_card_and_an_unsure_item_asks(self):
        card = self.propose(*self.listed())
        self.assertEqual((card.kind, card.state, card.channel), ("change_list", "review", "annotate"))
        self.assertEqual([row.n for row in card.changeList.items], [1, 2, 3])
        self.assertEqual(card.changeWords, [WORDS, COMMENT, "踢脚线也换"], "the message, the page comment, a text mark")
        self.assertEqual(card.markReadings, READINGS)
        self.assertEqual((card.source.chatSessionId, card.source.chatMessageId), ("c1", "u1"))
        question = next(row for row in self.store.items() if row.dedupeKey == f"unsure:{card.id}:2")
        self.assertEqual([option.action.args.get("target") for option in question.options[:2]], ["joist-2", "joist-1"])
        reads = [(method, urlsplit(path).path) for method, path, _ in self.runtimes.sent]
        self.assertEqual(reads, [("GET", "/api/document-annotations"), ("POST", "/api/markup/resolve"), ("GET", "/api/state")])
        self.assertEqual(self.runtimes.sent[1][2]["marks"][0]["id"], "east", "the saved ink is what is read")

    def test_a_list_the_marks_or_words_do_not_bear_out_is_refused_and_nothing_is_kept(self):
        cases = {
            "reworded": item(1, ["east"], "把东墙改成砖墙", "east wall in brick", ["wall-east"]),
            "unread mark": item(1, ["west"], "东墙换成砖", "east wall in brick", ["wall-east"]),
            "sure on an unclear mark": item(1, ["joists"], "这两根龙骨加高到 0.3", "raise joists", ["joist-1"]),
            "a part no mark lies on": item(1, ["east"], "东墙换成砖", "east wall in brick", ["roof"]),
        }
        for name, row in cases.items():
            with self.subTest(name), self.assertRaises(HubFailure) as refused:
                self.propose(row)
            self.assertEqual((refused.exception.status, refused.exception.error.code), (422, "CHANGE_LIST_REFUSED"))
        self.assertEqual(self.store.items(), [])
        with self.assertRaises(HubFailure) as unbound:
            self.propose(self.listed()[0], message="u0")
        self.assertEqual(unbound.exception.error.code, "CHANGE_LIST_UNBOUND", "a message without marks asks plainly")
        with self.assertRaises(HubFailure) as foreign:
            self.propose(self.listed()[0], token="guessed")
        self.assertEqual(foreign.exception.status, 403)

    def test_running_the_card_sends_only_the_architects_words_with_the_ticked_sure_items(self):
        card = self.propose(*self.listed())
        result = self.hub.executor.resolve(card.id, "run", items=[1, 3])
        self.assertEqual(result.item.state, "done")
        session, request = self.chats.posted[-1]
        self.assertEqual(session, "c1")
        self.assertEqual(request.content, f"{WORDS}\n踢脚线也换", "whole passages the items were taken from, nothing else")
        approval = request.changeListApproval
        self.assertEqual((approval.cardId, [row.n for row in approval.items]), (card.id, [1, 3]))
        self.assertEqual([row.targets for row in approval.items], [["wall-east"], ["skirting"]])
        self.assertEqual([quote.items for quote in approval.quotes], [[1], [3]])
        self.assertEqual(approval.quotes[0].feedbackQuote, WORDS)
        question = next(row for row in self.store.items(states=["superseded"]) if row.dedupeKey == f"unsure:{card.id}:2")
        self.assertEqual(question.state, "superseded", "the list ran without its unsure item")

    def test_answering_an_unsure_item_makes_it_one_the_list_can_run(self):
        card = self.propose(*self.listed())
        question = next(row for row in self.store.items() if row.dedupeKey == f"unsure:{card.id}:2")
        self.hub.executor.resolve(question.id, "target-1")
        changed = self.store.get(card.id)
        self.assertEqual(changed.state, "review", "the list still waits for the architect to run it")
        second = changed.changeList.items[1]
        self.assertEqual((second.targets, second.unsure), (["joist-2"], False))
        self.hub.executor.resolve(card.id, "run", items=[2])
        approval = self.chats.posted[-1][1].changeListApproval
        self.assertEqual([(row.n, row.targets) for row in approval.items], [(2, ["joist-2"])])
        self.assertEqual(self.chats.posted[-1][1].content, WORDS)

    def test_a_list_with_nothing_sure_ticked_is_not_run(self):
        card = self.propose(*self.listed())
        with self.assertRaises(HubFailure) as refused:
            self.hub.executor.resolve(card.id, "run", items=[2])
        self.assertEqual(refused.exception.error.code, "CHANGE_LIST_NOT_RUNNABLE")
        self.assertEqual(self.store.get(card.id).state, "review", "the card waits as it was")
        self.assertEqual(self.chats.posted, [])



class ChatMarkupTests(unittest.TestCase):
    """A message keeps the marks it is about and the items it runs, and its turn is told what to do with them."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub marks ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.project = root / "chat-project"
        FilesystemProjectRepository.initialize(self.project, project_id="chat-project",
                                               initial_state={"project_id": "chat-project", "version": 0})
        environment = patch.dict(os.environ, {"CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude"),
                                              "APPDATA": str(root / "roaming"), "LOCALAPPDATA": str(root / "local")})
        environment.start()
        self.addCleanup(environment.stop)
        fake = root / "fake cli.py"
        fake.write_text(FAKE_CLI, encoding="utf-8")
        self.log = root / "calls.jsonl"
        self.store = chat.ChatStore(root / "runtime", "http://127.0.0.1:8790",
                                    commands={name: (sys.executable, str(fake), str(self.log)) for name in ("codex", "claude")})
        self.addCleanup(self.store.shutdown)
        memory = patch.object(turn_context, "_project_memory", return_value=[])
        memory.start()
        self.addCleanup(memory.stop)
        self.session = self.store.create(ChatCreateRequest(projectDir=str(self.project), provider="claude"))

    def prompt(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()][-1]["prompt"]

    def test_a_message_keeps_its_marks_and_its_turn_reads_them(self):
        posted = self.store.post(self.session.id, ChatPostRequest(projectId="chat-project", content=WORDS,
                                                                  markupContext=PAGE))
        wait_for(lambda: self.store.get(self.session.id), lambda row: row.status != "running")
        self.assertEqual(posted.messages[0].markup.model_dump(exclude_none=True), PAGE)
        prompt = self.prompt()
        self.assertIn(turn_context.markup_note(False), prompt)
        self.assertIn("POST /api/markup/resolve", prompt)
        self.assertEqual(json.loads(prompt.split(turn_context.markup_note(False), 1)[1].strip().splitlines()[0]), PAGE)
        self.assertIn("design_read part=marks", turn_context.markup_note(True))

    def test_a_run_change_list_reaches_its_turn_as_the_architects_words_and_the_items(self):
        approval = {"cardId": "att_" + "1" * 32, "items": [{"n": 1, "understanding": "east wall in brick",
                                                            "targets": ["wall-east"]}],
                    "quotes": [{"items": [1], "feedbackQuote": WORDS, "clauses": ["东墙换成砖"]}]}
        posted = self.store.post(self.session.id, ChatPostRequest(projectId="chat-project", content=WORDS,
                                                                  changeListApproval=approval))
        wait_for(lambda: self.store.get(self.session.id), lambda row: row.status != "running")
        self.assertEqual(posted.messages[0].content, WORDS, "the message is the architect's own words")
        self.assertEqual(posted.messages[0].changeListApproval.cardId, approval["cardId"])
        facts = json.loads(self.prompt().split(turn_context._APPROVAL_NOTE, 1)[1].strip().splitlines()[0])
        self.assertEqual(facts["quotes"][0]["feedbackQuote"], WORDS)

    def test_a_running_turn_takes_no_marks(self):
        with patch.object(self.store, "_run"):
            self.store.post(self.session.id, ChatPostRequest(projectId="chat-project", content="first"))
            self.store._running[self.session.id] = chat._Running()
            try:
                with self.assertRaises(HubFailure) as refused:
                    self.store.post(self.session.id, ChatPostRequest(projectId="chat-project", content=WORDS,
                                                                     markupContext=PAGE))
            finally:
                self.store._running.pop(self.session.id, None)
        self.assertEqual(refused.exception.error.code, "CHAT_INTERJECTION_MARKS")
        self.assertEqual([row.content for row in self.store.get(self.session.id).messages], ["first"])


if __name__ == "__main__":
    unittest.main()
