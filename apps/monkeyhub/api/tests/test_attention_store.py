"""The attention queue keeps one card per thing to look at, and a person's handling outlasts a Hub restart."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
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
from pydantic import ValidationError

from monkeyhub_api.attention.models import AttentionDraft, Resolution
from monkeyhub_api.attention.routes import attention_router
from monkeyhub_api.attention.store import HUB_PROJECT_ID, RETAIN_CLOSED, AttentionStore, runtime_id
from monkeyhub_api.models import HubFailure


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


def option(option_id: str, label: str, command: str = "design.go_to_step", **args) -> dict:
    return {"id": option_id, "label": label, "consequence": f"{label}，可以撤销",
            "action": {"commandId": command, "args": args}, "undoable": True}


def ab_draft(study: str = "s1", *, title: str = "V16 和斜撑版，留哪个", runs=("run-a", "run-b"), **extra) -> AttentionDraft:
    return AttentionDraft.model_validate({
        "channel": "decide", "kind": "ab", "title": title,
        "source": {"studyId": study, "runIds": list(runs)}, "dedupeKey": f"ab:study:{study}",
        "options": [option("a", "留 A", runId=runs[0]), option("b", "换成 B", runId=runs[1])], **extra})


def rule_draft(target: str = "component:beam-3") -> AttentionDraft:
    return AttentionDraft.model_validate({
        "channel": "guess", "kind": "ask", "title": "梁高连着改了三次，要定成规则吗",
        "source": {"ruleId": "same-direction", "targetRef": target}, "dedupeKey": f"rule:same-direction:{target}",
        "options": [option("yes", "定成规则", "attention.answer_guess")]})


class AttentionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime_root = Path(self.temporary.name)
        self.project_dir = str(self.runtime_root / "projects" / "thi-hemp")
        Path(self.project_dir).mkdir(parents=True)
        self.clock = Clock()
        self.store = self.open_store()

    def open_store(self) -> AttentionStore:
        return AttentionStore(self.runtime_root / "attention", clock=self.clock)

    def test_one_thing_to_look_at_is_one_card(self):
        heard = []
        self.store.subscribe(lambda project_id, project_dir, ids: heard.append(ids))
        first = self.store.upsert("thi-hemp", self.project_dir, ab_draft())
        again = self.store.upsert("thi-hemp", self.project_dir, ab_draft())
        self.assertEqual(first.id, again.id)
        self.assertEqual(first.state, "open")
        self.assertEqual(heard, [(first.id,)], "an unchanged draft is not a change")

        self.clock.advance(minutes=1)
        reworded = self.store.upsert("thi-hemp", self.project_dir, ab_draft(title="斜撑版检查也过了，留哪个"))
        self.assertEqual(reworded.id, first.id)
        self.assertEqual(reworded.title, "斜撑版检查也过了，留哪个")
        self.assertEqual(len(self.store.items()), 1)
        self.assertEqual(self.store.summary().open, 1)

    def test_a_change_list_waits_for_review(self):
        draft = AttentionDraft.model_validate({
            "channel": "annotate", "kind": "change_list", "title": "我理解为这 3 处修改",
            "dedupeKey": "changes:frame-cc"})
        card = self.store.upsert("thi-hemp", self.project_dir, draft)
        self.assertEqual(card.state, "review")
        self.assertEqual(self.store.summary().projects[0].open, 1)

    def test_put_off_and_set_aside_outlast_a_restart(self):
        later = self.store.upsert("thi-hemp", self.project_dir, ab_draft("s1")).id
        aside = self.store.upsert("thi-hemp", self.project_dir, ab_draft("s2")).id
        self.store.upsert("thi-hemp", self.project_dir, ab_draft("s3"))
        self.store.later(later)
        self.store.dismiss(aside)

        restarted = self.open_store()
        self.assertEqual(restarted.get(later).state, "later")
        self.assertEqual(restarted.get(aside).state, "dismissed")
        summary = restarted.summary()
        self.assertEqual((summary.open, summary.later, summary.working), (1, 1, 0))
        self.assertEqual(summary.projects[0].projectId, "thi-hemp")
        self.assertEqual([item.id for item in restarted.items(states=["dismissed"])], [aside])
        history = restarted.get(aside).history
        self.assertEqual([(row.from_, row.to, row.by) for row in history],
                         [(None, "open", "machine"), ("open", "dismissed", "person")])

        restarted.reopen(aside)
        self.assertEqual(self.open_store().get(aside).state, "open")

    def test_set_aside_for_days_comes_back_after_them(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.dismiss(card, days=2)
        self.clock.advance(days=1)
        self.assertEqual(self.store.get(card).state, "dismissed")
        self.clock.advance(days=1, seconds=1)
        self.assertEqual(self.store.get(card).state, "open")
        self.assertEqual(self.store.summary().open, 1)
        # The wake is kept once anything is written for this project.
        self.store.upsert("thi-hemp", self.project_dir, ab_draft("s2"))
        woken = self.open_store().get(card)
        self.assertEqual(woken.state, "open")
        self.assertEqual(woken.history[-1].by, "machine")

    def test_a_rule_card_set_aside_quiets_its_rule_for_a_week(self):
        card = self.store.upsert("thi-hemp", self.project_dir, rule_draft()).id
        self.store.dismiss(card)
        quiet = self.open_store().suppressions(self.project_dir)
        self.assertEqual([(row.ruleId, row.targetRef) for row in quiet], [("same-direction", "component:beam-3")])
        self.assertEqual(datetime.fromisoformat(quiet[0].until), self.clock.now + timedelta(days=7))
        self.clock.advance(days=7, seconds=1)
        self.assertEqual(self.store.suppressions(self.project_dir), [])

        self.store.upsert("thi-hemp", self.project_dir, rule_draft("component:beam-4"))
        other = self.store.items(states=["open"])[0].id
        self.store.dismiss(other, days=3)
        self.assertEqual(len(self.store.suppressions(self.project_dir)), 1)
        self.store.reopen(other)
        self.assertEqual(self.store.suppressions(self.project_dir), [])

    def test_a_producer_retires_what_it_no_longer_offers(self):
        kept = self.store.upsert("thi-hemp", self.project_dir, ab_draft("s1")).id
        gone = self.store.upsert("thi-hemp", self.project_dir, ab_draft("s2")).id
        put_off = self.store.upsert("thi-hemp", self.project_dir, ab_draft("s3")).id
        self.store.later(put_off)
        other = self.store.upsert("thi-hemp", self.project_dir, rule_draft()).id
        changed = self.store.reconcile("thi-hemp", self.project_dir, "ab:", [ab_draft("s1")])
        self.assertEqual(sorted(changed), sorted([gone, put_off]))
        self.assertEqual(self.store.get(kept).state, "open")
        self.assertEqual(self.store.get(gone).state, "superseded")
        self.assertEqual(self.store.get(put_off).state, "superseded")
        self.assertEqual(self.store.get(other).state, "open")
        with self.assertRaises(ValueError):
            self.store.reconcile("thi-hemp", self.project_dir, "ab:", [rule_draft()])

        answered = self.store.retire("thi-hemp", self.project_dir, "ab:study:s1", "expired")
        self.assertEqual(answered.state, "expired")
        self.assertIsNone(self.store.retire("thi-hemp", self.project_dir, "ab:study:s1"))

    def test_a_closed_card_opens_again_only_when_what_it_asks_changes(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.dismiss(card)
        reworded = self.store.upsert("thi-hemp", self.project_dir, ab_draft(title="斜撑版的检查也过了"))
        self.assertEqual(reworded.state, "dismissed")
        self.assertEqual(reworded.title, "V16 和斜撑版，留哪个")

        third = self.store.upsert("thi-hemp", self.project_dir, ab_draft(runs=("run-a", "run-c")))
        self.assertEqual(third.id, card)
        self.assertEqual(third.state, "open")
        self.assertEqual(third.history[-1].by, "machine")

    def test_a_card_its_source_asks_for_again_comes_back(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.reconcile("thi-hemp", self.project_dir, "ab:", [])  # one read without it
        self.assertEqual(self.store.get(card).state, "superseded")
        self.store.reconcile("thi-hemp", self.project_dir, "ab:", [ab_draft()])
        back = self.store.get(card)
        self.assertEqual(back.state, "open")
        self.assertEqual(back.history[-1].note, "Its source asks again.")

    def test_an_answered_card_keeps_its_undo_when_a_new_question_comes(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.begin(card, "b")
        self.store.settle(card, Resolution(optionId="b", by="person", at=self.clock.now.isoformat(), undo={
            "action": {"commandId": "design.go_to_step", "args": {"runId": "run-a"}}}))
        self.assertEqual(self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id, card, "the same question")
        third = self.store.upsert("thi-hemp", self.project_dir, ab_draft(runs=("run-a", "run-c")))
        self.assertNotEqual(third.id, card)
        self.assertEqual(third.state, "open")
        kept = self.store.get(card)
        self.assertEqual((kept.state, kept.resolution.undo.action.args), ("done", {"runId": "run-a"}))
        self.assertEqual(self.open_store().upsert("thi-hemp", self.project_dir, ab_draft(runs=("run-a", "run-c"))).id,
                         third.id, "after a restart the newer card answers for the key")

    def test_a_card_being_carried_out_keeps_its_content(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.begin(card, "b")
        self.store.reconcile("thi-hemp", self.project_dir, "ab:", [ab_draft(runs=("run-x", "run-y"))])
        self.assertEqual(self.store.get(card).options[1].action.args, {"runId": "run-b"})
        self.store.reconcile("thi-hemp", self.project_dir, "ab:", [])
        self.assertEqual(self.store.get(card).state, "working")

    def test_an_answer_a_restart_cut_short_asks_again_with_what_was_seen(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        until = self.clock.now + timedelta(hours=3)
        self.store.later(card, until=until)
        self.store.begin(card, "b")
        self.store.progress(card, {"previousRunId": "run-v16"})
        restarted = self.open_store().get(card)
        self.assertEqual((restarted.state, restarted.snoozedUntil), ("later", until.isoformat()))
        self.assertEqual(restarted.history[-2].facts, {"previousRunId": "run-v16"})
        self.assertIn("Hub stopped", restarted.history[-1].note)

        self.assertEqual(self.store.release(card, "worker unavailable").snoozedUntil, until.isoformat(),
                         "a failed answer keeps the time it was put off until")
        with self.assertRaises(HubFailure):
            self.store.progress(card, {"x": 1})

    def test_the_summary_names_unreadable_projects_and_keeps_the_hub_apart(self):
        self.store.upsert("thi-hemp", self.project_dir, ab_draft())
        self.store.upsert(HUB_PROJECT_ID, str(self.runtime_root), AttentionDraft.model_validate({
            "channel": "decide", "kind": "choice", "title": "Version 0.1.990 is ready", "dedupeKey": "update:v1"}))
        broken = self.runtime_root / "attention" / f"{runtime_id('housing', self.project_dir + '-h')}.json"
        broken.write_text("{", encoding="utf-8")
        summary = self.open_store().summary()
        self.assertEqual([row.projectId for row in summary.projects], ["thi-hemp"])
        self.assertEqual((summary.hub.projectId, summary.hub.open, summary.open), (HUB_PROJECT_ID, 1, 2))
        self.assertEqual(summary.invalid, [broken.stem])

    def test_the_executor_refuses_a_card_before_it_is_kept(self):
        def check(draft):
            if any(option.undoable for option in draft.options):
                raise HubFailure(422, "ATTENTION_CARD_INVALID", "no")

        store = AttentionStore(None, clock=self.clock, check=check)
        with self.assertRaises(HubFailure):
            store.upsert("thi-hemp", self.project_dir, ab_draft())
        with self.assertRaises(HubFailure):
            store.reconcile("thi-hemp", self.project_dir, "rule:", [rule_draft()])
        self.assertEqual(store.items(), [])

    def test_only_a_card_in_the_queue_can_be_put_off(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.dismiss(card)
        with self.assertRaises(HubFailure) as refused:
            self.store.later(card)
        self.assertEqual((refused.exception.status, refused.exception.error.code), (409, "ATTENTION_STATE"))
        with self.assertRaises(HubFailure) as missing:
            self.store.get("att_" + "0" * 32)
        self.assertEqual(missing.exception.error.code, "ATTENTION_NOT_FOUND")

    def test_an_answer_is_carried_out_once_and_can_be_taken_back(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.later(card)
        self.assertEqual(self.store.begin(card, "b").state, "working")
        with self.assertRaises(HubFailure, msg="a second click finds it working"):
            self.store.begin(card, "a")
        self.assertEqual(self.store.summary().working, 1)
        self.assertEqual(self.store.release(card, "工作区还有没记录的修改").state, "later",
                         "a failed answer leaves the card where it was")

        self.store.begin(card, "b")
        undo = {"action": {"commandId": "design.go_to_step", "args": {"runId": "run-a"}},
                "precondition": {"currentRunId": "run-b"}}
        done = self.store.settle(card, Resolution(optionId="b", by="person", at=self.clock.now.isoformat(),
                                                  receipt={"runId": "run-b"}, undo=undo))
        self.assertEqual((done.state, done.resolution.optionId), ("done", "b"))
        self.assertEqual(self.open_store().get(card).resolution.undo.action.args, {"runId": "run-a"})
        self.assertEqual([(row.to, row.optionId) for row in done.history[-2:]], [("working", "b"), ("done", "b")])

        again = self.store.undone(card, note="回到 V16")
        self.assertEqual((again.state, again.resolution), ("open", None))
        self.assertEqual(again.history[-1].note, "回到 V16")
        with self.assertRaises(HubFailure):
            self.store.undone(card)

    def test_a_failed_save_changes_nothing(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        heard = []
        self.store.subscribe(lambda project_id, project_dir, ids: heard.append(ids))
        blocked = self.runtime_root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        self.store.root = blocked / "attention"
        with self.assertRaises(HubFailure) as failed:
            self.store.later(card)
        self.assertEqual(failed.exception.error.code, "ATTENTION_LOG_UNAVAILABLE")
        self.assertEqual(self.store.get(card).state, "open")
        self.assertEqual(heard, [])

    def test_rows_this_hub_cannot_read_are_kept(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        path = self.runtime_root / "attention" / f"{runtime_id('thi-hemp', self.project_dir)}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        future = {**saved["items"][0], "id": "att_" + "f" * 32, "dedupeKey": "x:1", "kind": "from-a-newer-hub"}
        saved["items"].append(future)
        path.write_text(json.dumps(saved), encoding="utf-8")

        restarted = self.open_store()
        restarted.later(card)
        rows = json.loads(path.read_text(encoding="utf-8"))["items"]
        self.assertIn(future, rows)
        self.assertEqual(len(restarted.items(states=["later", "open"])), 1)

    def test_an_unreadable_file_blocks_only_its_project(self):
        self.store.upsert("thi-hemp", self.project_dir, ab_draft())
        other_dir = str(self.runtime_root / "projects" / "housing")
        Path(other_dir).mkdir(parents=True)
        path = self.runtime_root / "attention" / f"{runtime_id('housing', other_dir)}.json"
        path.write_text("{not json", encoding="utf-8")

        restarted = self.open_store()
        self.assertEqual(len(restarted.items()), 1)
        with self.assertRaises(HubFailure) as invalid:
            restarted.upsert("housing", other_dir, ab_draft())
        self.assertEqual((invalid.exception.status, invalid.exception.error.code), (503, "ATTENTION_LOG_INVALID"))
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_closed_cards_are_kept_up_to_a_bound(self):
        store = AttentionStore(None, clock=self.clock)
        for index in range(RETAIN_CLOSED + 3):
            self.clock.advance(seconds=1)
            card = store.upsert("thi-hemp", self.project_dir, ab_draft(f"s{index}")).id
            store.dismiss(card)
        store.upsert("thi-hemp", self.project_dir, ab_draft("open"))
        self.assertEqual(len(store.items(states=["dismissed"])), RETAIN_CLOSED)
        self.assertEqual(len(store.items()), 1)
        self.assertNotIn("ab:study:s0", {item.dedupeKey for item in store.items(states=["dismissed"])})

    def test_a_card_says_what_it_recommends_and_why(self):
        with self.assertRaises(ValidationError, msg="an A/B card is a decision"):
            AttentionDraft.model_validate({**ab_draft().model_dump(), "channel": "notice"})
        recommended = {**ab_draft().model_dump(), "recommendation": {"optionId": "b", "reason": "检查相同，构件少 4 个"}}
        with self.assertRaises(ValidationError, msg="the recommended option must be marked"):
            AttentionDraft.model_validate(recommended)
        recommended["options"][1]["recommended"] = True
        self.assertEqual(AttentionDraft.model_validate(recommended).recommendation.optionId, "b")
        with self.assertRaises(ValidationError, msg="no grounds, nothing preselected"):
            AttentionDraft.model_validate({**ab_draft().model_dump(), "options": recommended["options"]})
        with self.assertRaises(ValidationError, msg="a button is one command of the tree"):
            AttentionDraft.model_validate({**ab_draft().model_dump(), "options": [
                option("a", "留 A", "PUT /api/working-draft")]})


class AttentionRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_dir = str(Path(temporary.name) / "thi-hemp")
        Path(self.project_dir).mkdir()
        self.store = AttentionStore(Path(temporary.name) / "attention")
        app = FastAPI()

        @app.exception_handler(HubFailure)
        async def handle_hub_error(request, exc: HubFailure):
            return JSONResponse(exc.error.model_dump(), status_code=exc.status)

        app.include_router(attention_router(self.store))
        self.client = TestClient(app)

    def test_the_queue_is_read_and_handled_over_http(self):
        card = self.store.upsert("thi-hemp", self.project_dir, ab_draft()).id
        self.store.upsert("thi-hemp", self.project_dir, rule_draft())
        listed = self.client.get("/api/attention", params={"projectDir": self.project_dir, "channel": "decide"})
        self.assertEqual([row["id"] for row in listed.json()], [card])
        self.assertEqual(listed.json()[0]["history"][0]["from"], None)
        self.assertEqual(self.client.get("/api/attention/summary").json()["open"], 2)

        self.assertEqual(self.client.post(f"/api/attention/{card}/later").json()["state"], "later")
        self.assertEqual(self.client.post(f"/api/attention/{card}/reopen").json()["state"], "open")
        dismissed = self.client.post(f"/api/attention/{card}/dismiss", json={"days": 3}).json()
        self.assertEqual(dismissed["state"], "dismissed")
        self.assertIsNotNone(dismissed["snoozedUntil"])
        self.assertEqual(self.client.get(f"/api/attention/{card}").json()["state"], "dismissed")
        self.assertEqual(self.client.get("/api/attention", params={"state": ["dismissed", "open"]}).status_code, 200)

        refused = self.client.post(f"/api/attention/{card}/later")
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "ATTENTION_STATE"))
        self.assertEqual(self.client.get("/api/attention/att_missing").json()["code"], "ATTENTION_NOT_FOUND")
        self.assertEqual(self.client.post(f"/api/attention/{card}/dismiss", json={"days": 0}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
