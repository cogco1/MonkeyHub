"""The change list: an agent's reading of Board marks, checked against what the marks lie on, before anything runs.

The readings are the Runtime's (``MarkReading.to_dict``) for the floor-section
case: a circle round the upper layers names the finish and two joists, one
mark only touches the plywood and the finish at its rim, one sits on blank
paper. The list and its question cards go through the attention store in
memory, without the executor's check of which commands it runs.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from pydantic import ValidationError

from monkeyhub_api.attention.change_list import (
    ChangeList, applies_without_asking, approval, check_against, unsure_cards,
)
from monkeyhub_api.attention.store import AttentionStore

CATALOG = ["finish", "joist-1", "joist-2", "plywood", "skirting"]
# What the architect wrote with the marks on Board, as the Hub kept it.
WORDS = ["楼板只留一层胶合板，龙骨和面层不要", "这里也薄一点"]


def hit(element: str | None, *, component: str = "floor", target: bool = True, share: float = 0.3,
        borderline: bool = False) -> dict:
    return {"componentId": component, "elementId": element, "objectIds": [f"obj-{element or component}"],
            "coverage": 0.9 if target else 0.04, "share": share, "central": target, "target": target,
            "borderline": borderline}


READINGS = [
    {"markId": "m-upper", "kind": "circle", "zone": "area", "ambiguous": False, "reason": None,
     "hits": [hit("finish", share=0.5), hit("joist-1"), hit("joist-2")]},
    {"markId": "m-edge", "kind": "circle", "zone": "area", "ambiguous": True, "reason": "partial",
     "hits": [hit("plywood", target=False, share=0.5, borderline=True),
              hit("finish", target=False, share=0.4, borderline=True)]},
    {"markId": "m-blank", "kind": "circle", "zone": "area", "ambiguous": True, "reason": "nothing", "hits": []},
]


def change_list(**changes) -> ChangeList:
    items = [
        {"n": 1, "markIds": ["m-upper"], "note": "楼板只留一层胶合板，龙骨和面层不要",
         "understanding": "Remove the finish and both joists", "understandingZh": "删掉面层和两根龙骨",
         "targets": ["finish", "joist-1", "joist-2"], "protected": ["plywood"],
         "crop": {"kind": "crop", "frameId": "frame-cc", "markIds": ["m-upper"]}},
        {"n": 2, "markIds": ["m-edge"], "note": "这里也薄一点", "understanding": "Plywood or finish, not sure which",
         "unsure": True},
        {"n": 3, "markIds": [], "note": "楼板只留一层胶合板，龙骨和面层不要",
         "understanding": "The skirting goes with the finish", "targets": ["skirting"], "extra": True},
    ]
    for n, fields in changes.items():
        items[int(n.removeprefix("item")) - 1].update(fields)
    return ChangeList.model_validate({"frameId": "frame-cc", "boardRevision": "b" * 64, "runId": "run-v15",
                                      "items": items})


class CheckTests(unittest.TestCase):
    def test_a_list_the_marks_and_the_record_bear_out_has_no_problems(self) -> None:
        self.assertEqual(check_against(change_list(), READINGS, WORDS, CATALOG), [])

    def test_a_note_is_the_architects_only_as_they_wrote_it(self) -> None:
        reworded = change_list(item1={"note": "楼板只保留胶合板"})
        [problem] = check_against(reworded, READINGS, WORDS, CATALOG)
        self.assertIn("Item 1's words are not the architect's", problem)
        with self.assertRaises(ValueError):
            approval("att_x", reworded, WORDS)
        self.assertFalse(applies_without_asking(change_list(item1={"note": "楼板只保留胶合板"}, item2={"keep": False}),
                                                READINGS, WORDS, CATALOG))
        part = change_list(item1={"note": "龙骨和面层不要"})
        self.assertEqual(check_against(part, READINGS, WORDS, CATALOG), [], "a part of what they wrote, as written")

    def test_only_an_extra_may_touch_what_no_cited_mark_lies_on(self) -> None:
        [problem] = check_against(change_list(item1={"targets": ["finish", "skirting"]}), READINGS, WORDS, CATALOG)
        self.assertIn("Item 1 touches skirting", problem)
        self.assertIn("an extra", problem)
        [problem] = check_against(change_list(item3={"targets": ["wall-east"]}), READINGS, WORDS, CATALOG)
        self.assertIn("record does not hold", problem, "an extra still names something the record holds")

    def test_a_sure_item_cannot_cite_a_mark_that_was_not_read_clearly(self) -> None:
        [problem] = check_against(change_list(item2={"unsure": False, "targets": ["plywood"]}), READINGS, WORDS, CATALOG)
        self.assertIn("Item 2 cites m-edge", problem)
        self.assertIn("unsure", problem)
        [problem] = check_against(change_list(item1={"markIds": ["m-gone"], "extra": True}), READINGS, WORDS, CATALOG)
        self.assertIn("m-gone", problem)

    def test_items_say_what_they_touch_unless_unsure_and_never_both_change_and_keep(self) -> None:
        for fields, message in (({"targets": []}, "names no component"),
                                ({"protected": ["finish"]}, "both change and keep"),
                                ({"crop": {"kind": "model_view", "runId": "run-v15"}}, "crop")):
            with self.subTest(message=message), self.assertRaises(ValidationError) as caught:
                change_list(item1=fields)
            self.assertIn(message, str(caught.exception))
        with self.assertRaises(ValidationError):
            ChangeList.model_validate({"items": [{**change_list().items[0].model_dump(), "n": 2}]})


class QuestionTests(unittest.TestCase):
    def test_an_unsure_item_asks_which_part_with_the_parts_under_its_marks(self) -> None:
        card_id = "att_" + "0" * 32
        [card] = unsure_cards(card_id, change_list(), READINGS, labels={"plywood": "Plywood deck"})
        self.assertEqual((card.channel, card.kind, card.dedupeKey), ("decide", "choice", f"unsure:{card_id}:2"))
        self.assertEqual((card.title, card.titleZh), ("Which part did you mean in item 2?", "第 2 条你指的是哪个构件？"))
        self.assertEqual(card.summaryZh, "你写的：这里也薄一点")
        self.assertEqual([option.label for option in card.options], ["Plywood deck", "finish", "Say it in words"])
        self.assertEqual(card.options[0].action.args, {"cardId": card_id, "n": 2, "target": "plywood"})
        self.assertEqual((card.options[-1].action.commandId, card.options[-1].action.args), ("chat.send_message", {}))
        self.assertEqual(card.source.markIds, ["m-edge"])
        self.assertEqual(unsure_cards("att_x", change_list(item2={"keep": False}), READINGS), [],
                         "an unticked item asks nothing")

    def test_a_mark_on_blank_paper_can_still_be_answered_in_words(self) -> None:
        [card] = unsure_cards("att_x", change_list(item2={"markIds": ["m-blank"]}), READINGS)
        self.assertEqual([option.id for option in card.options], ["say"])

    def test_question_cards_live_in_the_attention_store(self) -> None:
        store = AttentionStore(None, clock=lambda: datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
        [card] = unsure_cards("att_x", change_list(), READINGS)
        item = store.upsert("demo-project", "C:/projects/demo-project", card)
        self.assertEqual((item.state, item.channel), ("open", "decide"))


class ApprovalTests(unittest.TestCase):
    def test_the_approval_is_the_architects_words_and_items_sharing_words_are_one_submission(self) -> None:
        self.assertEqual(approval("att_x", change_list(), WORDS), {
            "cardId": "att_x", "items": [1, 3], "content": "楼板只留一层胶合板，龙骨和面层不要",
            "quotes": [{"items": [1, 3], "feedbackQuote": "楼板只留一层胶合板，龙骨和面层不要"}]})
        self.assertEqual(approval("att_x", change_list(item3={"keep": False}), WORDS)["items"], [1])
        with self.assertRaises(ValueError):
            approval("att_x", change_list(item1={"keep": False}, item3={"keep": False}), WORDS)

    def test_words_found_inside_another_line_cannot_be_quoted_alone(self) -> None:
        listed = change_list(item2={"unsure": False, "markIds": ["m-upper"], "targets": ["finish"],
                                    "note": "薄一点"},
                             item3={"note": "踢脚板也再薄一点"})
        result = approval("att_x", listed, ["楼板只留一层胶合板，龙骨和面层不要", "薄一点", "踢脚板也再薄一点"])
        self.assertEqual(result["content"].splitlines(), ["楼板只留一层胶合板，龙骨和面层不要", "薄一点", "踢脚板也再薄一点"])
        self.assertEqual([(row["items"], row["feedbackQuote"]) for row in result["quotes"]],
                         [([1], "楼板只留一层胶合板，龙骨和面层不要"), ([2], None), ([3], "踢脚板也再薄一点")])
        self.assertNotIn("Remove", result["content"], "the agent's reading never travels as the person's words")

    def test_small_sure_lists_borne_out_by_their_marks_apply_first(self) -> None:
        self.assertFalse(applies_without_asking(change_list(), READINGS, WORDS, CATALOG), "an unsure item is ticked")
        self.assertTrue(applies_without_asking(change_list(item2={"keep": False}), READINGS, WORDS, CATALOG))
        many = change_list(item1={"targets": ["finish", "joist-1", "joist-2", "joist-3", "joist-4"], "extra": True},
                           item2={"keep": False})
        self.assertFalse(applies_without_asking(many, READINGS, WORDS, CATALOG + ["joist-3", "joist-4"]), "six components")
        stray = change_list(item1={"targets": ["finish", "skirting"]}, item2={"keep": False})
        self.assertFalse(applies_without_asking(stray, READINGS, WORDS, CATALOG), "what the marks do not bear out is shown")


if __name__ == "__main__":
    unittest.main()
