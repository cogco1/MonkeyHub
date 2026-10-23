"""The first guess rule: the same field changed the same way three times running becomes one question.

The steps are the Runtime's edit log as ``GET /api/edit-log`` answers it,
nearest first; the cards go through the attention store in memory, so setting
one aside and a streak that ends are both checked where they take effect.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from monkeyhub_api.attention.models import Suppression
from monkeyhub_api.attention.rules import PREFIX, SAME_DIRECTION, guesses, refresh_guesses
from monkeyhub_api.attention.store import AttentionStore

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
CORNICE = "entity:portico-cornice#params.height"


def edit(before: float, after: float, target: str = "entity:portico-cornice", field: str = "params.height",
         component: str | None = "portico") -> dict:
    return {"targetRef": target, "componentId": component, "op": "set", "field": field, "before": before,
            "after": after, "delta": after - before, "direction": (after > before) - (after < before)}


def step(run_id: str, *edits: dict) -> dict:
    return {"runId": run_id, "sourceRunId": None, "kind": "set_scalar", "request": None, "edits": list(edits)}


def thinner_three_times() -> list[dict]:
    """Nearest first: 0.30 -> 0.25 -> 0.20 -> 0.15, with an unrelated parameter step between."""

    return [step("run-5", edit(0.20, 0.15)), step("run-4", edit(0.25, 0.20)),
            step("run-3", edit(1.2, 1.25, "parameter:module", "value", None)), step("run-2", edit(0.30, 0.25))]


class GuessTests(unittest.TestCase):
    def test_three_same_way_changes_ask_whether_to_go_on_or_go_back(self) -> None:
        [card] = guesses(thinner_three_times(), [], NOW)
        self.assertEqual((card.channel, card.kind, card.dedupeKey), ("guess", "ask", PREFIX + CORNICE))
        self.assertEqual((card.source.ruleId, card.source.targetRef), (SAME_DIRECTION, CORNICE))
        self.assertEqual(card.source.runIds, ["run-2", "run-4", "run-5"])
        self.assertEqual(card.title, "连着 3 次把 portico-cornice（portico） 的 height 调小了，还要再调小吗？")
        self.assertIn("0.3 → 0.25 → 0.2 → 0.15", card.summary)
        again, back = card.options
        self.assertEqual((again.id, again.action.commandId), ("again", "chat.send_message"))
        self.assertEqual(again.action.args["content"], "把 portico-cornice 的 params.height 从 0.15 再调小到 0.1")
        self.assertEqual(back.action.args["content"], "把 portico-cornice 的 params.height 改回 0.3")
        self.assertIsNone(card.recommendation, "a repeated direction is a reason to ask, not to prefer an answer")
        self.assertFalse(any(option.recommended for option in card.options))

    def test_two_changes_or_one_the_other_way_are_no_streak(self) -> None:
        self.assertEqual(guesses(thinner_three_times()[:3], [], NOW), [])
        back_up = [step("run-6", edit(0.15, 0.18)), *thinner_three_times()[:3]]
        self.assertEqual(guesses(back_up, [], NOW), [], "made thicker in between: not one direction")

    def test_only_the_last_five_steps_count(self) -> None:
        later = [step(f"run-{n}", edit(1.0, 2.0, "parameter:other", "value", None)) for n in (9, 8)]
        self.assertEqual(guesses(later + thinner_three_times(), [], NOW), [], "the oldest change fell out of the window")
        self.assertEqual(len(guesses(later[:1] + thinner_three_times(), [], NOW)), 1)

    def test_a_quiet_field_is_left_out_until_its_quiet_ends(self) -> None:
        quiet = Suppression(ruleId=SAME_DIRECTION, targetRef=CORNICE, until=(NOW + timedelta(days=7)).isoformat())
        self.assertEqual(guesses(thinner_three_times(), [quiet], NOW), [])
        self.assertEqual(len(guesses(thinner_three_times(), [quiet], NOW + timedelta(days=8))), 1)
        other = Suppression(ruleId="another.rule", targetRef=CORNICE, until=(NOW + timedelta(days=7)).isoformat())
        self.assertEqual(len(guesses(thinner_three_times(), [other], NOW)), 1)

    def test_each_field_is_its_own_target(self) -> None:
        widths = [step(f"run-{n}", edit(w, w + 0.1, field="params.width"), edit(h, h - 0.05))
                  for n, w, h in ((5, 1.2, 0.2), (4, 1.1, 0.25), (3, 1.0, 0.3))]
        cards = guesses(widths, [], NOW)
        self.assertEqual([card.source.targetRef for card in cards],
                         ["entity:portico-cornice#params.height", "entity:portico-cornice#params.width"])
        self.assertIn("调大", cards[1].title)


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = lambda: NOW
        self.store = AttentionStore(None, clock=self.clock)

    def refresh(self, steps) -> list[str]:
        return refresh_guesses(self.store, "demo-project", "C:/projects/demo-project", steps, NOW)

    def test_a_streak_writes_one_card_and_its_end_retires_it(self) -> None:
        [card_id] = self.refresh(thinner_three_times())
        self.assertEqual(self.refresh(thinner_three_times()), [], "the same streak is the same card")
        self.assertEqual(self.store.get(card_id).state, "open")
        self.refresh([step("run-6", edit(0.15, 0.18)), *thinner_three_times()])
        self.assertEqual(self.store.get(card_id).state, "superseded")

    def test_a_card_set_aside_quiets_its_field_for_a_week(self) -> None:
        [card_id] = self.refresh(thinner_three_times())
        self.store.dismiss(card_id)
        self.assertEqual(self.refresh([step("run-6", edit(0.15, 0.1)), *thinner_three_times()]), [])
        self.assertEqual(self.store.get(card_id).state, "dismissed")
        self.assertEqual(guesses(thinner_three_times(), self.store.suppressions("C:/projects/demo-project"), NOW), [])


if __name__ == "__main__":
    unittest.main()
