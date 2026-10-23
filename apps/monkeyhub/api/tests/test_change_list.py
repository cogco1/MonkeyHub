"""The change list: an agent's reading of Board marks, checked against what the marks lie on, before anything runs.

The readings are the Runtime's (``MarkReading.to_dict``) for the floor-section
case: a circle round the upper layers names the finish and two joists, one
mark sits on blank paper. The list and its question cards go through the
attention store in memory.
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
    ChangeList, applies_without_asking, check_against, handoff_message, unsure_cards,
)
from monkeyhub_api.attention.store import AttentionStore


def hit(element: str | None, *, component: str = "floor", target: bool = True, share: float = 0.3,
        borderline: bool = False) -> dict:
    return {"componentId": component, "elementId": element, "objectIds": [f"obj-{element or component}"],
            "coverage": 0.9 if target else 0.4, "share": share, "target": target, "borderline": borderline}


READINGS = [
    {"markId": "m-upper", "kind": "circle", "zone": "area", "ambiguous": False, "reason": None,
     "hits": [hit("finish", share=0.5), hit("joist-1"), hit("joist-2")]},
    {"markId": "m-edge", "kind": "circle", "zone": "area", "ambiguous": True, "reason": "partial",
     "hits": [hit("plywood", target=False, share=0.5, borderline=True), hit("finish", target=False, share=0.4)]},
    {"markId": "m-blank", "kind": "circle", "zone": "area", "ambiguous": True, "reason": "nothing", "hits": []},
]


def change_list(**changes) -> ChangeList:
    items = [
        {"n": 1, "markIds": ["m-upper"], "note": "楼板只留一层胶合板，龙骨和面层不要",
         "understanding": "删掉面层和两根龙骨", "targets": ["finish", "joist-1", "joist-2"], "protected": ["plywood"],
         "crop": {"kind": "crop", "frameId": "frame-cc", "markIds": ["m-upper"]}},
        {"n": 2, "markIds": ["m-edge"], "note": "这里也薄一点", "understanding": "不确定指胶合板还是面层",
         "unsure": True},
        {"n": 3, "markIds": [], "note": "楼板只留一层胶合板，龙骨和面层不要", "understanding": "踢脚板随面层一起删",
         "targets": ["skirting"], "extra": True},
    ]
    for n, fields in changes.items():
        items[int(n.removeprefix("item")) - 1].update(fields)
    return ChangeList.model_validate({"frameId": "frame-cc", "boardRevision": "b" * 64, "runId": "run-v15",
                                      "items": items})


class ChangeListTests(unittest.TestCase):
    def test_a_list_the_marks_bear_out_has_no_problems(self) -> None:
        self.assertEqual(check_against(change_list(), READINGS, catalog=["skirting"]), [])

    def test_a_component_no_mark_lies_on_and_the_record_lacks_is_named(self) -> None:
        [problem] = check_against(change_list(item1={"targets": ["finish", "wall-east"]}), READINGS, ["skirting"])
        self.assertIn("Item 1 touches wall-east", problem)
        [problem] = check_against(change_list(), READINGS)
        self.assertIn("skirting", problem, "an extra by name needs the record to hold it")
        [problem] = check_against(change_list(item1={"markIds": ["m-gone"]}), READINGS, ["skirting", "finish",
                                                                                         "joist-1", "joist-2"])
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

    def test_an_unsure_item_becomes_a_question_whose_answers_are_what_its_marks_lie_on(self) -> None:
        [card] = unsure_cards("att_" + "0" * 32, change_list(), READINGS)
        self.assertEqual((card.channel, card.kind, card.dedupeKey), ("decide", "choice", "unsure:att_" + "0" * 32 + ":2"))
        self.assertEqual(card.title, "第 2 条你指的是哪个构件？")
        self.assertEqual(card.summary, "你写的：这里也薄一点")
        self.assertEqual([option.label for option in card.options], ["plywood", "finish"], "borderline first")
        self.assertEqual(card.options[0].action.args, {"cardId": "att_" + "0" * 32, "n": 2, "target": "plywood"})
        self.assertEqual(card.source.markIds, ["m-edge"])
        self.assertEqual(unsure_cards("att_x", change_list(item2={"keep": False}), READINGS), [],
                         "an unticked item asks nothing")

    def test_a_mark_on_blank_paper_still_asks_with_no_answers_to_pick(self) -> None:
        [card] = unsure_cards("att_x", change_list(item2={"markIds": ["m-blank"]}), READINGS)
        self.assertEqual(card.options, [])

    def test_the_handoff_carries_the_ticked_sure_items_with_the_architects_words(self) -> None:
        message = handoff_message(change_list(item3={"keep": False}))
        self.assertEqual(message.splitlines(), [
            "按这份清单改（只改勾选的条目）：",
            "1. 删掉面层和两根龙骨（构件：finish、joist-1、joist-2；不动：plywood）。原话：「楼板只留一层胶合板，龙骨和面层不要」",
        ])
        with self.assertRaises(ValueError):
            handoff_message(change_list(item1={"keep": False}, item3={"keep": False}))

    def test_small_sure_lists_apply_first_and_anything_else_is_shown_first(self) -> None:
        self.assertFalse(applies_without_asking(change_list()), "an unsure item is ticked")
        self.assertTrue(applies_without_asking(change_list(item2={"keep": False})))
        many = change_list(item1={"targets": ["finish", "joist-1", "joist-2", "joist-3", "joist-4"]},
                           item2={"keep": False})
        self.assertFalse(applies_without_asking(many), "six components")

    def test_question_cards_live_in_the_attention_store(self) -> None:
        store = AttentionStore(None, clock=lambda: datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
        [card] = unsure_cards("att_x", change_list(), READINGS)
        item = store.upsert("demo-project", "C:/projects/demo-project", card)
        self.assertEqual((item.state, item.channel), ("open", "decide"))


if __name__ == "__main__":
    unittest.main()
