"""Producers write the facts the Hub already has as cards, one per thing to look at, and take them back when the facts go."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from monkeyhub_api.attention import producers
from monkeyhub_api.attention.actions import COMMANDS, check_card
from monkeyhub_api.attention.store import AttentionStore
from monkeyhub_api.models import ChatDetail
from monkeyhub_api.runtime.models import OperationRecord
from monkeyhub_api.updates.models import UpdateStatus

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
OPENED = {producers.OPEN_CONVERSATION, producers.COMPARE}


def line(run, **fields):
    row = {"lineId": f"result:{run}", "kind": "result", "runId": run, "status": "ready", "admission": "none",
           "relation": "diverged", "reconcile": "unknown", "label": None, "detail": None, "studyId": None,
           "branchId": None, "updatedAt": (NOW - timedelta(hours=1)).isoformat()}
    return {**row, **fields}


def graph(*lines, head="run-v16"):
    return {"projectId": "thi-hemp", "head": {"runId": head} if head else None, "lines": list(lines)}


def chat(messages, **fields):
    row = {"id": "c1", "projectId": "thi-hemp", "projectDir": "D:/p/thi-hemp", "title": "改楼板", "provider": "claude",
           "status": "idle", "createdAt": NOW.isoformat(), "updatedAt": NOW.isoformat(), "messages": messages}
    return ChatDetail.model_validate({**row, **fields})


def message(message_id, role, **fields):
    return {"id": message_id, "role": role, "content": "", "createdAt": NOW.isoformat(), **fields}


SUGGESTION = {"title": "出一套期中评图的图", "outcome": "平立剖各一张，排进 Board", "capability": "available",
              "rationale": "评图周一", "prompt": "出平立剖"}


class ResultProducerTests(unittest.TestCase):
    def test_two_results_of_one_study_are_one_ab_card(self):
        cards = producers.results(graph(line("run-a", studyId="s1", label="V16 去掉斜撑", admission="admitted"),
                                        line("run-b", studyId="s1", label="V16 加厚楼板", admission="admitted")), now=NOW)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual((card.kind, card.dedupeKey), ("ab", "result:study:s1"))
        self.assertEqual([row.id for row in card.options], ["a", "b", "archive"])
        self.assertEqual(card.options[1].action.args, {"runId": "run-b", "branchId": None})
        self.assertEqual(card.opens[0].args, {"runIds": ["run-a", "run-b"]})
        self.assertIsNone(card.recommendation, "no grounds, nothing recommended")

    def test_a_single_result_asks_to_switch_or_combine(self):
        combine, conflict = producers.results(graph(line("run-c", reconcile="can-combine", label="东入口加宽"),
                                                    line("run-d", reconcile="conflict")), now=NOW)
        self.assertEqual([row.id for row in combine.options], ["combine", "switch", "keep"])
        self.assertEqual(combine.options[0].action.args, {"runIds": ["run-v16", "run-c"]})
        self.assertIn("东入口加宽", combine.titleZh)
        self.assertIn("东入口加宽", combine.title)
        self.assertEqual([row.id for row in conflict.options], ["switch", "keep"],
                         "a run nobody admitted cannot be archived, so the card does not offer it")
        candidate, = producers.results(graph(line("run-e", admission="admitted")), now=NOW)
        self.assertEqual([row.id for row in candidate.options], ["switch", "keep", "archive"])
        self.assertIn("同一处", conflict.summaryZh)
        self.assertIn("same part", conflict.summary)

    def test_a_result_already_answered_for_is_not_asked_about_alone(self):
        after = graph(line("run-b", studyId="s1", admission="admitted"), head="run-a")
        self.assertEqual([card.dedupeKey for card in producers.results(after, now=NOW)], ["result:run:run-b"])
        self.assertEqual(producers.results(after, now=NOW, handled={"run-a", "run-b"}), [],
                         "B was on the A/B card the person answered")
        third = graph(line("run-b", studyId="s1", admission="admitted"), line("run-c", studyId="s1", admission="admitted"),
                      head="run-a")
        card, = producers.results(third, now=NOW, handled={"run-a", "run-b"})
        self.assertEqual(card.source.runIds, ["run-b", "run-c"], "compared again with a new one")

    def test_a_large_study_shows_its_newest_and_archives_them_all(self):
        lines = [line(f"run-{n}", studyId="s1", admission="admitted",
                      updatedAt=(NOW - timedelta(minutes=10 - n)).isoformat()) for n in range(8)]
        card, = producers.results(graph(*lines), now=NOW)
        self.assertEqual([row.id for row in card.options], ["a", "b", "c", "d", "e", "f", "archive"])
        self.assertEqual(card.options[0].action.args["runId"], "run-2", "the newest six, oldest of them first")
        self.assertEqual(len(card.options[-1].action.args["runIds"]), 8)
        self.assertIn("6", card.titleZh)
        self.assertIn("8", card.title)

    def test_decided_results_ask_nothing_and_old_ones_are_offered_for_archiving(self):
        cards = producers.results(graph(
            line("run-rejected", admission="rejected"), line("run-withdrawn", admission="superseded"),
            line("run-superseded", relation="superseded"), line("run-archived", admission="admitted", review="archived"),
            line("run-running", status="running"),
            line("run-old1", admission="admitted", updatedAt=(NOW - timedelta(days=9)).isoformat()),
            line("run-old2", studyId="s9", admission="admitted", updatedAt=(NOW - timedelta(days=8)).isoformat()),
            line("run-old-unadmitted", updatedAt=(NOW - timedelta(days=10)).isoformat())), now=NOW)
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0].dedupeKey.startswith("result:stale:"))
        self.assertEqual(cards[0].options[0].action.args, {"runIds": ["run-old1", "run-old2"]})

    def test_a_card_goes_once_its_result_is_decided(self):
        with tempfile.TemporaryDirectory() as root:
            store = AttentionStore(Path(root) / "attention")
            project = str(Path(root))
            first = producers.results(graph(line("run-a", studyId="s1", admission="admitted"),
                                            line("run-b", studyId="s1", admission="admitted")), now=NOW)
            store.reconcile("thi-hemp", project, producers.RESULTS, first)
            # A was continued: it is the head now, not a result off its line.
            after = producers.results(graph(line("run-b", studyId="s1", admission="admitted"), head="run-a"), now=NOW)
            store.reconcile("thi-hemp", project, producers.RESULTS, after)
            self.assertEqual(sorted((row.dedupeKey, row.state) for row in store.items(states=["open", "superseded"])),
                             [("result:run:run-b", "open"), ("result:study:s1", "superseded")])


class ConversationProducerTests(unittest.TestCase):
    def test_a_waiting_permission_is_a_choice_with_the_conversations_answers(self):
        waiting = chat([message("m1", "tool", permission={"id": "p1", "title": "写入 runs/", "options": [
            {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "never", "name": "Reject", "kind": "reject_always"},
            {"optionId": "x", "name": "Something new and quite long", "kind": "other"}]})], status="running")
        answered = chat([message("m1", "tool")], id="c2")
        cards = producers.permissions([waiting, answered])
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.dedupeKey, "perm:c1:p1")
        self.assertEqual([row.labelZh for row in card.options], ["允许这一次", "一直拒绝", "Something new and q…"])
        self.assertEqual([row.label for row in card.options][:2], ["Allow once", "Always reject"])
        self.assertEqual(card.options[0].action.args, {"sessionId": "c1", "permissionId": "p1", "optionId": "allow"})
        self.assertEqual(card.opens[0].args, {"sessionId": "c1"})

    def test_only_the_suggestion_that_would_still_continue_the_turn_is_a_card(self):
        messages = [message("u1", "user"), message("a1", "assistant", sourceTurnId="u1", suggestion=SUGGESTION,
                                                     presentationRevision=3)]
        card, = producers.suggestions([chat(messages)])
        self.assertEqual((card.kind, card.channel, card.dedupeKey), ("suggestion", "guess", "sug:a1"))
        self.assertEqual(card.options[0].action.args, {"sessionId": "c1", "messageId": "a1", "revision": 3})
        self.assertEqual(producers.suggestions([chat(messages, status="running")]), [])
        chosen = [*messages, message("u2", "user", suggestionSelection={
            "messageId": "0b8f2b49-6f4e-4d6a-9d36-3c1b5a1e2f10", "revision": 3})]
        self.assertEqual(producers.suggestions([chat(chosen)]), [], "a newer turn makes it stale")

    def test_a_design_loop_question_is_answered_on_the_card(self):
        card, = producers.questions([producers.AskedQuestion("c1", "u1", "西门廊要保留原来的柱距吗", "柱距和新楼梯冲突")])
        self.assertEqual(card.dedupeKey, "dlq:c1:u1")
        self.assertEqual(card.options[0].action.commandId, "chat.composer.send")
        self.assertNotIn("content", card.options[0].action.args)


class HubProducerTests(unittest.TestCase):
    def record(self, operation_id, status, **fields):
        return OperationRecord(operationId=operation_id, projectId="thi-hemp", kind="POST /api/revisions",
                               source="studio", status=status, **fields)

    def test_failed_operations_are_notices_until_read(self):
        cards = producers.operations([
            self.record("o1", "failed", reason="构件 seat 缺少面"),
            self.record("o2", "failed", acknowledgedAt=NOW.isoformat()),
            self.record("o3", "needs_recovery", recoverable=True),
            self.record("o4", "needs_recovery", recoverable=False, sessionId="c1"),
            self.record("o5", "completed")])
        self.assertEqual([card.dedupeKey for card in cards], ["op:o1:failed", "op:o4:needs_recovery"])
        self.assertEqual((cards[0].title, cards[0].titleZh), ("The model change did not finish", "改模型没有完成"))
        self.assertEqual(cards[0].options[0].action.args, {"operationId": "o1"})
        self.assertEqual(cards[1].opens[0].args, {"sessionId": "c1"})

    def test_a_prepared_update_asks_to_restart(self):
        ready = UpdateStatus.model_validate({
            "currentVersion": "0.1.980", "currentRevision": None, "mode": "local", "state": "ready", "canApply": True,
            "prepared": {"targetVersion": "11230e0e1234-desktop", "targetRevision": "a" * 40, "changedBytes": 1,
                         "changedFiles": 1, "removedFiles": 0, "reusedFiles": 0, "releaseVersion": "0.1.990"}})
        card, = producers.updates(ready)
        self.assertEqual((card.titleZh, card.options[0].action.commandId), ("新版本 0.1.990 已经准备好", "hub.update_apply"))
        self.assertEqual(producers.updates(ready.model_copy(update={"state": "idle"})), [])


class EveryButtonIsKnownTests(unittest.TestCase):
    def test_producers_write_only_answers_the_executor_runs(self):
        cards = [
            *producers.results(graph(line("run-a", studyId="s1", admission="admitted"),
                                     line("run-b", studyId="s1", admission="admitted"),
                                     line("run-c", reconcile="can-combine"), line("run-d", admission="admitted"),
                                     line("run-old", admission="admitted",
                                          updatedAt=(NOW - timedelta(days=9)).isoformat())), now=NOW),
            *producers.permissions([chat([message("m1", "tool", permission={"id": "p1", "title": "t", "options": [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"}]})])]),
            *producers.questions([producers.AskedQuestion("c1", "u1", "q", "r")]),
            *producers.operations([OperationRecord(operationId="o1", projectId="p", kind="candidate",
                                                   source="retained", status="failed")]),
        ]
        self.assertEqual({option.action.commandId for card in cards for option in card.options} - set(COMMANDS), set())
        for card in cards:
            check_card(card)  # what a producer writes, the executor runs as written
        self.assertEqual({command.commandId for card in cards for command in card.opens} - OPENED, set())


if __name__ == "__main__":
    unittest.main()
