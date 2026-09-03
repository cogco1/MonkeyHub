"""The intent grammar: four forms, one optional ``keep``, and nothing else.

The parser is a pure function, so it is tested as a table. What it accepts is
the whole of what the Studio can be told to do in round 1; everything else is a
question for a human, and the second half of this file is the table of those
questions asked against a real record.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.application.intent import (
    DeterministicIntentProvider,
    ParsedIntent,
    parse_utterance,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import BlockedNeedsHuman

from .support import PROJECT_ID, make_project

# Every accepted utterance, with the parse it must produce. Keywords are
# case-insensitive and whitespace is tolerated; the field text is kept
# verbatim because it names a key in the record.
ACCEPTED: tuple[tuple[str, ParsedIntent], ...] = (
    (
        "set height to 2.2",
        ParsedIntent("set", "height", 2.2, None, ()),
    ),
    (
        "SET height TO 2.2",
        ParsedIntent("set", "height", 2.2, None, ()),
    ),
    (
        "   set    height   to    2.2   ",
        ParsedIntent("set", "height", 2.2, None, ()),
    ),
    (
        "set height = 2.2",
        ParsedIntent("set", "height", 2.2, None, ()),
    ),
    (
        "set height=2.2",
        ParsedIntent("set", "height", 2.2, None, ()),
    ),
    (
        "set height to 3",
        ParsedIntent("set", "height", 3, None, ()),
    ),
    (
        "set height to -1.5",
        ParsedIntent("set", "height", -1.5, None, ()),
    ),
    (
        "set module to 1.5 m",
        ParsedIntent("set", "module", 1.5, "m", ()),
    ),
    (
        "set module to 1500mm",
        ParsedIntent("set", "module", 1500, "mm", ()),
    ),
    (
        "set params.height to 2.2",
        ParsedIntent("set", "params.height", 2.2, None, ()),
    ),
    (
        "set parameter:module to 1.5",
        ParsedIntent("set", "parameter:module", 1.5, None, ()),
    ),
    (
        "increase height by 20 %",
        ParsedIntent("increase", "height", 20, None, ()),
    ),
    (
        "increase height by 20%",
        ParsedIntent("increase", "height", 20, None, ()),
    ),
    (
        "DECREASE bay BY 12.5 %",
        ParsedIntent("decrease", "bay", 12.5, None, ()),
    ),
    (
        "set height to 2.2 keep parameter:span",
        ParsedIntent("set", "height", 2.2, None, ("parameter:span",)),
    ),
    (
        "set height to 2.2 keep parameter:span, entity:portico-base",
        ParsedIntent(
            "set",
            "height",
            2.2,
            None,
            ("parameter:span", "entity:portico-base"),
        ),
    ),
    (
        "increase bay by 10 % keep parameter:span",
        ParsedIntent("increase", "bay", 10, None, ("parameter:span",)),
    ),
)

# Utterances the grammar does not accept. Each one is a shape a person plausibly
# types, and each one has to become a question rather than a guess.
REFUSED: tuple[str, ...] = (
    "",
    "   ",
    "make the portico taller",
    "set height",
    "set height to",
    "set height to two point two",
    "set height to 2.2 and width to 3",
    "increase height by 20",
    "increase height to 20 %",
    "raise height by 20 %",
    "move the cornice up 300mm",
    "delete portico-base",
    "keep parameter:span",
    "set height to 2.2 keep",
    "set height to 2.2 keep parameter:span,",
    "set height to 2.2 %",
)


class GrammarTableTests(unittest.TestCase):
    def test_every_accepted_form_parses_to_exactly_one_intent(self) -> None:
        for utterance, expected in ACCEPTED:
            with self.subTest(utterance=utterance):
                self.assertEqual(parse_utterance(utterance), expected)

    def test_the_number_keeps_the_type_it_was_written_with(self) -> None:
        # ``3`` is an integer in the record and must not come back as ``3.0``.
        parsed = parse_utterance("set height to 3")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIsInstance(parsed.number, int)
        self.assertIsInstance(parse_utterance("set height to 3.0").number, float)

    def test_nothing_outside_the_grammar_parses(self) -> None:
        for utterance in REFUSED:
            with self.subTest(utterance=utterance):
                self.assertIsNone(parse_utterance(utterance))

    def test_keep_is_a_keyword_and_never_a_unit(self) -> None:
        # ``set height to 2.2 keep`` must not read ``keep`` as a unit word and
        # quietly become a proposal with no protected refs.
        self.assertIsNone(parse_utterance("set height to 2.2 keep"))


# Every refusal the provider can reach against the fixture record, as a table
# of (element selected, utterance, what the question has to contain). The point
# of the table is that no case answers with a shrug: each question names
# something a person can act on.
BLOCKED: tuple[tuple[str | None, str, tuple[str, ...]], ...] = (
    (
        "portico-base",
        "make it taller",
        ("height", "portico-base"),
    ),
    (
        "portico-base",
        "set width to 3",
        ("width", "height"),
    ),
    (
        "portico-base",
        "set params.profile to 3",
        ("profile", "height"),
    ),
    (
        None,
        "set params.height to 3",
        ("params.height", "elementId"),
    ),
    (
        None,
        "set module to 1.5",
        ("module", "client"),
    ),
    (
        None,
        "set parameter:module to 1.5",
        ("module", "client"),
    ),
    (
        None,
        "set column-spacing to 3",
        ("column-spacing", "bay"),
    ),
    (
        None,
        "set bay to 3000 mm",
        ("bay", "mm", "m"),
    ),
    (
        None,
        "set bay to 3 keep parameter:column-spacing",
        ("column-spacing",),
    ),
    (
        None,
        "set bay to 3 keep east-loggia",
        ("east-loggia",),
    ),
)


class ProviderRefusalTests(unittest.TestCase):
    """The questions the seam asks, against the record that has to answer."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.projection = project_state(
            ProjectBinding.open(
                StudioSettings(project_dir=self.root / PROJECT_ID)
            )
        )
        self.provider = DeterministicIntentProvider(self.projection)

    def propose(self, utterance: str, element_id: str | None) -> None:
        refs = [
            f"state:{self.projection.state_digest}",
            "component:portico",
        ]
        if element_id is not None:
            refs.append(f"element:{element_id}")
        self.provider.propose(
            session_ref=f"project:{PROJECT_ID}",
            message=utterance,
            context_refs=refs,
        )

    def test_every_refusal_asks_a_question_naming_the_records_own_state(
        self,
    ) -> None:
        for element_id, utterance, expected in BLOCKED:
            with self.subTest(utterance=utterance, element=element_id):
                with self.assertRaises(BlockedNeedsHuman) as raised:
                    self.propose(utterance, element_id)
                question = raised.exception.question
                for fragment in expected:
                    self.assertIn(fragment, question)

    def test_only_an_unparseable_utterance_repeats_the_grammar(self) -> None:
        with self.assertRaises(BlockedNeedsHuman) as raised:
            self.propose("make it taller", "portico-base")
        # The four typeable forms, and no fifth entry that is not an utterance.
        self.assertEqual(len(raised.exception.accepted_forms), 4)
        self.assertTrue(
            all(
                form.startswith(("set ", "increase ", "decrease "))
                for form in raised.exception.accepted_forms
            )
        )
        # The optional suffix is not lost: it is said in the sentence.
        self.assertIn("keep <ref>", raised.exception.question)

        with self.assertRaises(BlockedNeedsHuman) as raised:
            self.propose("set width to 3", "portico-base")
        self.assertEqual(raised.exception.accepted_forms, ())


if __name__ == "__main__":
    unittest.main()
