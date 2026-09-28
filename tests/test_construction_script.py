"""#419: the construction script is a small bounded language, interpreted and never executed.

These tests pin the language itself: what it evaluates, what it refuses (always at a line),
its limits and its print log. Geometry lowering is pinned in test_construction_lowering.py.
"""
from __future__ import annotations

import unittest

from archflow.project.refs import ProjectVersionRef
from archflow.state.state_record import Entity, StateRecord
from monkeyarch.construction import ConstructionError, compile_construction_script, vocabulary
from monkeyarch.construction.script import IMPLEMENTED_VERBS
from monkeyarch.construction.vocabulary import LAYER_RULE_TOKENS, layer_rule_violations

EVIDENCE = "input:monkeyarch-modeling-setup"


def _record() -> StateRecord:
    """What ``initialize_modeling`` seeds: a modelling root and a ground level at zero."""

    return StateRecord(
        project_id="demo", run_id="authored",
        entities=(
            Entity("model", "Component@1", {"intent": "Root for candidate modeling", "source_refs": [EVIDENCE]}),
            Entity("ground", "Level@1", {"role": "ground", "elevation": 0.0}, basis_refs=(EVIDENCE,)),
        ),
        evidence_refs=(EVIDENCE,), option={"option_id": "modeling"},
        base=ProjectVersionRef("demo", 0, "0" * 64),
    )


def run(script: str):
    return compile_construction_script(script, _record(), root_component_id="model")


class ConstructionTestCase(unittest.TestCase):
    def refused(self, script: str) -> ConstructionError:
        """The script is refused; the refusal says one plain sentence that passes the layer rule."""

        with self.assertRaises(ConstructionError) as caught:
            run(script)
        error = caught.exception
        self.assertEqual(layer_rule_violations(error.message), (), error.message)
        self.assertEqual(error.to_dict()["code"], "CONSTRUCTION_INVALID")
        return error

    def log(self, script: str) -> list[str]:
        return list(run(script).log)


class EvaluationTests(ConstructionTestCase):
    def test_arithmetic_follows_python(self) -> None:
        self.assertEqual(self.log("print(1 + 2 * 3, 7 / 2, 7 // 2, 7 % 3, 2 ** 10, -3 + 1, abs(-2.5), round(3.14159, 2))"),
                         ["7 3.5 3 1 1024 -2 2.5 3.14"])

    def test_loops_functions_and_comprehensions(self) -> None:
        script = "\n".join([
            "def area(w, d=2):",
            "    return w * d",
            "",
            "total = 0",
            "for i in range(5):",
            "    if i == 1:",
            "        continue",
            "    elif i == 4:",
            "        break",
            "    total += area(i)",
            "squares = [x * x for x in range(6) if x % 2 == 0]",
            "pairs = [(i, label) for i, label in enumerate(['a', 'b'])]",
            "a, b = 2, 3",
            "print(total, squares, area(3, d=4), pairs, a + b)",
            "print(min(4, 1), max([2, 9]), sum([1, 2, 3]), len('abc'), list(zip([1, 2], [3, 4])))",
            "print(round(sqrt(16)), round(degrees(pi)), floor(2.7), ceil(2.1), float(3), int(2.9))",
            "print(1 if 2 > 1 else 0, None is None, 'a' + 'b', [1, 2][-1], tuple([1, 2]), 3 in [1, 3])",
        ])
        self.assertEqual(self.log(script), [
            "10 [0, 4, 16] 12 [(0, 'a'), (1, 'b')] 5",
            "1 9 6 3 [(1, 3), (2, 4)]",
            "4 180 2 3 3.0 2",
            "1 True ab 2 (1, 2) True",
        ])

    def test_recursion_within_the_call_depth(self) -> None:
        script = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\nprint(fact(10))"
        self.assertEqual(self.log(script), ["3628800"])

    def test_a_function_reads_module_names_and_keeps_its_own(self) -> None:
        script = "\n".join([
            "gap = 2",
            "def shifted(x):",
            "    step = x + gap",
            "    return step",
            "step = 100",
            "print(shifted(1), step)",
        ])
        self.assertEqual(self.log(script), ["3 100"])

    def test_print_shows_handles_by_their_names_in_order(self) -> None:
        script = "mass = extrude(rect(0, 0, 4, 3), 3)\nprint('mass is', mass)\nprint(bounds(mass))"
        self.assertEqual(self.log(script), ["mass is <solid mass>", "((0.0, 0.0, 0.0), (4.0, 3.0, 3.0))"])


class RefusalTests(ConstructionTestCase):
    def test_every_refused_construct_names_its_line(self) -> None:
        # (snippet, line of the refused node within the snippet, words the refusal names)
        cases = [
            ("import math", 1, "import"),
            ("from math import sqrt", 1, "import"),
            ("y = x.real", 1, "attribute"),
            ("while x:\n    pass", 1, "while"),
            ("with x:\n    pass", 1, "with"),
            ("try:\n    pass\nexcept ValueError:\n    pass", 1, "try"),
            ("f = lambda v: v", 1, "lambda"),
            ("global x", 1, "global"),
            ("class Box:\n    pass", 1, "class"),
            ("s = f'{x}'", 1, "f-string"),
            ("d = {'a': 1}", 1, "dict"),
            ("s = {1, 2}", 1, "set"),
            ("g = (v for v in range(3))", 1, "generator"),
            ("xs = [1]\nxs[0] = 2", 2, "item"),
            ("del x", 1, "del"),
            ("assert x", 1, "assert"),
            ("raise ValueError()", 1, "raise"),
            ("y = __name__", 1, "__"),
            ("print(*[1, 2])", 1, "unpacking"),
            ("if (n := 3):\n    pass", 1, ":="),
            ("for i in range(2):\n    pass\nelse:\n    pass", 1, "else"),
            ("y = x @ x", 1, "@"),
            ("y = x | 2", 1, "bitwise"),
            ("def f(*args):\n    pass", 1, "*args"),
            ("def f(**kw):\n    pass", 1, "**"),
            ("def f():\n    def g():\n        pass", 2, "inside a function"),
            ("y: int = 1", 1, "annotat"),
            ("return 3", 1, "outside a function"),
            ("break", 1, "outside a loop"),
            ("async def f():\n    pass", 1, "async"),
        ]
        for snippet, line, words in cases:
            with self.subTest(snippet=snippet):
                error = self.refused("x = 1\n" + snippet)
                self.assertEqual(error.line, 1 + line, error.message)
                self.assertIn(words, error.message)
                self.assertIsNotNone(error.column)
                self.assertIsNotNone(error.source_line)

    def test_a_disallowed_construct_is_refused_before_anything_runs(self) -> None:
        error = self.refused("print('never')\nfor i in range(2):\n    pass\nimport os")
        self.assertEqual((error.line, error.column, error.source_line), (4, 1, "import os"))
        self.assertIn("is not part of the construction language", error.message)

    def test_string_formatting_is_refused_at_its_line(self) -> None:
        error = self.refused("x = 1\ny = 'a%d' % x")
        self.assertEqual(error.line, 2)
        self.assertIn("+", error.message)

    def test_verbs_builtins_and_math_names_are_read_only(self) -> None:
        cases = [
            ("rect = 3", "rect is a construction verb; choose another name"),
            ("len = 3", "len is a builtin; choose another name"),
            ("pi = 3", "pi is a math name; choose another name"),
            ("def extrude(a):\n    return a", "extrude is a construction verb; choose another name"),
            ("for print in range(2):\n    pass", "print is a builtin; choose another name"),
            ("a, cut = 1, 2", "cut is a construction verb; choose another name"),
        ]
        for script, message in cases:
            with self.subTest(script=script):
                error = self.refused(script)
                self.assertEqual(error.line, 1)
                self.assertEqual(error.message, message)

    def test_errors_carry_line_column_source_and_one_sentence(self) -> None:
        error = self.refused("x = 1\ny = x / 0")
        self.assertEqual(error.to_dict(), {"code": "CONSTRUCTION_INVALID", "line": 2, "column": 5,
                                           "sourceLine": "y = x / 0", "message": "division by zero"})
        self.assertEqual(str(error), "line 2, column 5: division by zero")

    def test_a_syntax_error_names_its_line(self) -> None:
        error = self.refused("x = 1\ny = (")
        self.assertEqual(error.line, 2)

    def test_an_unknown_name_is_named(self) -> None:
        error = self.refused("x = 1\nprint(size)")
        self.assertEqual(error.line, 2)
        self.assertIn("size is not defined", error.message)

    def test_an_unknown_keyword_names_the_verb_signature(self) -> None:
        error = self.refused("p = rect(0, 0, 4, 3, w=2)")
        self.assertEqual(error.line, 1)
        self.assertIn("rect(x, z, width, depth)", error.message)
        error = self.refused("p = rect(0, 0, 4)")
        self.assertIn("rect(x, z, width, depth)", error.message)

    def test_a_user_function_checks_its_arguments(self) -> None:
        self.assertEqual(self.refused("def f(a):\n    return a\nf(1, 2)").line, 3)
        self.assertEqual(self.refused("def f(a):\n    return a\nf(b=1)").line, 3)

    def test_true_and_false_are_not_lengths(self) -> None:
        error = self.refused("x = 1\nb = extrude(rect(0, 0, True, 3), 1)")
        self.assertEqual(error.line, 2)
        self.assertIn("number", error.message)

    def test_values_that_are_not_finite_numbers_are_refused(self) -> None:
        self.assertEqual(self.refused("x = 1e308 * 10").line, 1)
        self.assertEqual(self.refused("x = 1\ny = sqrt(-1)").line, 2)
        self.assertEqual(self.refused("x = (-8) ** 0.5").line, 1)
        self.assertEqual(self.refused("x = 10 ** 1000").line, 1)
        self.assertEqual(self.refused("x = 1\ny = 1e400").line, 2)

    def test_text_is_only_for_ids_and_joins_with_plus(self) -> None:
        self.assertEqual(self.log("print('block' + '-' + 'a')"), ["block-a"])
        self.assertEqual(self.refused("x = float('inf')").line, 1)
        self.assertEqual(self.refused("x = 'a' * 3").line, 1)


class LimitTests(ConstructionTestCase):
    def test_the_script_length_is_bounded(self) -> None:
        error = self.refused("x = 1\n" * 3334)
        self.assertIsNone(error.line)
        self.assertIn("20 000 characters", error.message)

    def test_evaluation_steps_are_bounded(self) -> None:
        error = self.refused("x = 0\nfor i in range(1000): x = sum([j for j in range(1000)])")
        self.assertEqual(error.line, 2)
        self.assertIn("20 000 evaluation steps", error.message)

    def test_each_loop_is_bounded(self) -> None:
        error = self.refused("x = 0\nfor i in range(1001):\n    pass")
        self.assertEqual(error.line, 2)
        self.assertIn("1 000", error.message)
        error = self.refused("x = [i for i in range(1001)]")
        self.assertEqual(error.line, 1)
        self.assertIn("1 000", error.message)
        self.assertEqual(self.log("x = 0\nfor i in range(1000):\n    x += 1\nprint(x)"), ["1000"])

    def test_call_depth_is_bounded(self) -> None:
        error = self.refused("def f(n):\n    return f(n + 1)\nf(0)")
        self.assertEqual(error.line, 2)
        self.assertIn("16", error.message)

    def test_geometry_results_are_bounded(self) -> None:
        error = self.refused("for i in range(301):\n    extrude(rect(i, 0, 1, 1), 1)")
        self.assertEqual(error.line, 2)
        self.assertIn("300 geometry results", error.message)

    def test_sequences_are_bounded(self) -> None:
        self.assertEqual(self.refused("x = [0] * 100001").line, 1)

    def test_nesting_is_bounded_and_never_escapes_as_a_python_error(self) -> None:
        error = self.refused("x = 1\ny = " + "[" * 120 + "]" * 120)
        self.assertEqual(error.line, 2)
        self.assertIn("100 levels", error.message)
        self.assertIn("too deeply", self.refused("x = " + "+".join(["1"] * 5000)).message)
        self.assertEqual(self.log("x = " + "+".join(["1"] * 80) + "\nprint(x)"), ["80"])


class PrintLogTests(ConstructionTestCase):
    def test_print_appends_one_line_per_call_in_order(self) -> None:
        result = run("for i in range(3):\n    print('step', i)\nprint()")
        self.assertEqual(result.log, ("step 0", "step 1", "step 2", ""))

    def test_a_script_that_makes_nothing_says_so(self) -> None:
        result = run("print('hello')")
        self.assertEqual((result.entities, result.remove_entity_ids, result.report), ((), (), ()))
        self.assertEqual(result.summary, "construction: no change")


class VocabularyContractTests(ConstructionTestCase):
    def test_the_interpreter_and_the_vocabulary_list_the_same_verbs(self) -> None:
        contract = vocabulary()
        self.assertEqual(contract["schema"], "ConstructionVocabulary@1")
        self.assertEqual({verb["name"] for verb in contract["verbs"]}, set(IMPLEMENTED_VERBS))
        for verb in contract["verbs"]:
            self.assertEqual(set(verb), {"name", "signature", "returns", "description"})
            self.assertTrue(verb["signature"].startswith(verb["name"] + "("), verb)
        self.assertEqual(contract["limits"], {"characters": 20000, "steps": 20000, "loopIterations": 1000,
                                              "callDepth": 16, "geometryResults": 300})

    def test_the_layer_rule_is_the_controllers_token_list(self) -> None:
        self.assertEqual(LAYER_RULE_TOKENS, ("prism", "planar-surface", "column-array", "producer", "semantickind",
                                             "semantic_kind", "boolean", "aperture", "topology", "occt", "wall"))
        self.assertEqual(layer_rule_violations("extrude a profile, then loft two sections along a curve and a path"), ())
        found = layer_rule_violations("the Prism and the Walls, a planar-surface and a column-array from a producer; "
                                      "OCCT, semantic_kind, semanticKind, Boolean ops, an aperture, its topology")
        self.assertEqual(found, LAYER_RULE_TOKENS)
        self.assertEqual(layer_rule_violations("a firewall"), ("wall",))  # a substring match, deliberately strict


if __name__ == "__main__":
    unittest.main()
