"""#419: the construction script is a small bounded language, interpreted and never executed.

These tests pin the language itself: what it evaluates, what it refuses (always at a line),
its limits and its print log. Geometry lowering is pinned in test_construction_lowering.py.
"""
from __future__ import annotations

import time
import unittest

from archflow.project.refs import ProjectVersionRef
from archflow.state.state_record import Entity, StateRecord
from monkeyarch.authoring.construction.lowering import compile_construction_script
from monkeyarch.authoring.construction.script import IMPLEMENTED_VERBS, ConstructionError
from monkeyarch.authoring.construction.vocabulary import LAYER_RULE_TOKENS, layer_rule_violations, vocabulary

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

    def test_nested_lists_and_tuples_compare_as_in_python(self) -> None:
        script = "\n".join([
            "pts = [(0, 0), (1, 0), (1, 1)]",
            "a = extrude(rect(0, 0, 1, 1), 1)",
            "b = copy(a)",
            "print((1, 0) in pts, (2, 0) in pts, pts == [(0, 0), (1, 0), (1, 1)], [[1], [2]] != [[1], [3]])",
            "print(bounds(a) == bounds(b), bounds(a) == bounds(move(b, dx=1)))",
        ])
        self.assertEqual(self.log(script), ["True False True True", "True False"])
        error = self.refused("a = extrude(rect(0, 0, 1, 1), 1)\nprint([top(a)] == [top(a)])")
        self.assertEqual(error.line, 2)
        self.assertIn("an anchor cannot be compared", error.message)
        self.assertEqual(self.refused("x = 1\nprint(min([[1], [2]]))").line, 2)  # min, max and sum take numbers

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
        # The same refusal whether the platform's parser or the nesting limit meets it first.
        self.assertIn("more than 100 levels deep", self.refused("x = " + "+".join(["1"] * 5000)).message)
        self.assertEqual(self.log("x = " + "+".join(["1"] * 80) + "\nprint(x)"), ["80"])


class GrowingListTests(ConstructionTestCase):
    """``xs += [...]`` extends the list xs in place and counts only what it adds; ``+`` makes and counts a new list."""

    PATH = "\n".join([
        "pts = []",
        "for i in range(512):",
        "    pts += [(i * 0.1, 1, sin(i * 0.1))]",
        "p = path(pts)",
    ])

    def test_a_512_point_path_grown_with_plus_equals_compiles(self) -> None:
        body = next(row for row in run(self.PATH).entities if row["entity_id"] == "p-body")
        self.assertEqual(len(body["fields"]["params"]["profile"]), 512)
        error = self.refused(self.PATH.replace("pts += [", "pts = pts + ["))  # a whole new list every time
        self.assertEqual(error.line, 3)
        self.assertIn("200 000", error.message)

    def test_a_loop_that_grows_one_list_past_its_limit_is_refused_at_its_line(self) -> None:
        error = self.refused("xs = []\nfor i in range(1000):\n    xs += [0] * 20")
        self.assertEqual((error.line, error.source_line), (3, "xs += [0] * 20"))
        self.assertIn("10 000 elements", error.message)

    def test_plus_equals_extends_the_same_list_the_way_python_does(self) -> None:
        script = "\n".join([
            "a = [1]",
            "b = a",
            "a += (2, 3)",
            "rows = [[], []]",
            "for row in rows:",
            "    row += [0]",
            "t = (1, 2)",
            "u = t",
            "t += (3,)",
            "def add(xs, x):",
            "    xs += [x]",
            "add(a, 4)",
            "print(a, b, rows, t, u)",
        ])
        self.assertEqual(self.log(script), ["[1, 2, 3, 4] [1, 2, 3, 4] [[0], [0]] (1, 2, 3) (1, 2)"])

    def test_a_list_grown_inside_others_counts_for_each_of_them(self) -> None:
        cases = [
            ("xs = []\nouter = [xs] * 5000\nxs += [0]\nxs += [0]", 4, "would then hold more than 10 000 elements"),
            ("xs = [1]\nxs += [xs]", 2, "inside itself"),
            ("xs = []\nt = (xs,)\nxs += [t]", 3, "inside itself"),
            ("x = []\nroot = x\nfor i in range(200):\n    y = []\n    x += [y]\n    x = y", 5, "100 levels"),
            # collected while empty, grown while the comprehension runs: measured again when it ends
            ("xs = []\ndef grown(i):\n    if i == 1:\n        xs += [0] * 6000\n    return xs\n"
             "ys = [grown(i) for i in range(2)]", 6, "10 000 elements"),
        ]
        for script, line, words in cases:
            with self.subTest(script=script):
                error = self.refused(script)
                self.assertEqual(error.line, line, error.message)
                self.assertIn(words, error.message)


RING = "pts = [(cos(2 * pi * i / 256) * (1 + 0.001 * (i % 2)), sin(2 * pi * i / 256)) for i in range(256)]\n"
COMB = "pts = [(i, (i % 2) * 0.5) for i in range(254)] + [(253, -5)]\n"
CIRCLE = "pts = [(cos(2 * pi * i / 256), sin(2 * pi * i / 256)) for i in range(256)]\np = polygon(pts)\n"
STACK = "b = extrude(rect(0, 0, 1, 1), 1)\nfor i in range({height}):\n    b = extrude(rect(0, 0, 1, 1), 1, at=top(b))\n"
PROFILES_COMPARED = ("c1 = circle(0, 0, 1, 128)\nc2 = circle(0, 0, 1, 128)\nA = [c1] * 10000\nB = [c2] * 10000\n"
                     "x = A == B")
HOSTILE = {
    # one step that would walk a huge range
    "in a huge range": "print(0.5 in range(10 ** 18))",
    "text in a range": "print('a' in range(10000000))",
    "max of a huge range": "print(max(range(10 ** 18)))",
    "min of a huge range": "print(min(range(10 ** 18)))",
    "sum of a long range": "print(sum(range(10000000)))",
    "a huge list of a range": "x = list(range(10 ** 18))",
    # numbers whose one operation is huge
    "round to a huge negative digit": "x = round(1, -10000000)",
    "round a float likewise": "print(round(1.0, -1000000000))",
    "a tower of powers": "x = 10 ** 10 ** 10",
    # lists that share one list many times
    "a list of a long list, many times": "a = [0] * 10000\nb = [a] * 10000",
    "a list of a list, a thousand times": "a = [0] * 1000\nb = [a] * 1000",
    "== over shared lists": "a = [0] * 10000\nc = [0] * 10000\nprint([a] * 10000 == [c] * 10000)",
    "== over three levels": "a = [0] * 10000\nc = [0] * 10000\nA = [[a] * 1000] * 1000\n"
                            "C = [[c] * 1000] * 1000\nprint(A == C)",
    "print a shared list": "print([[0] * 10000] * 1000)",
    "a message about a shared list": "level([[0] * 10000] * 1000)",
    "a message about a shared list, again": "circle(0, 0, 1, [[0] * 10000] * 1000)",
    "doubling a list": "s = [0]\nfor i in range(40):\n    s = s + s",
    "doubling text": "s = 'ab'\nfor i in range(40):\n    s = s + s",
    "a list nested a thousand deep": "a = 0\nfor i in range(999):\n    a = [a]\nprint(a)",
    "many lists in a loop": "for i in range(1000):\n    x = [0] * 5000",
    # a list grown in place (+=) while other lists hold it
    "growing a list held thousands of times": "a = []\nb = [a] * 5000\nfor i in range(1000):\n    a += [0] * 10",
    "growing a list with a new holder every time": "xs = []\nfor i in range(1000):\n    keep = [xs]\n    xs += [0]",
    "growing a chain in place": "x = []\nroot = x\nfor i in range(1000):\n    y = []\n    x += [y]\n    x = y",
    "a nested comprehension": "x = [0 for a in range(1000) for b in range(1000) for c in range(1000)]",
    # verbs whose one call walks a lot
    "cutting with a long list, five times": "\n".join([
        "m = extrude(rect(0, 0, 400, 1), 3)",
        "cs = [extrude(rect(i + 0.1, 0.2, 0.5, 0.5), 1) for i in range(298)]",
        "cut(m, cs)",
        "big = cs * 33",
        "for k in range(5):",
        "    cut(m, big)",
    ]),
    "a loft through ten thousand sections": "\n".join([
        "s = section(circle(0, 0, 1, 128), 0)",
        "t = section(circle(0, 0, 1, 128), 1)",
        "L = [s, t] * 5000",
        "for i in range(3):",
        "    loft(L)",
    ]),
    "the same large polygon six thousand times": COMB.replace("[(253, -5)]", "[(253, -5), (0, -5)]")
                                                 + "for i in range(6000):\n    polygon(pts)",
    "a new large polygon every time": COMB + "for i in range(1000):\n    polygon(pts + [(0, -5 - i * 0.001)])",
    "a large offset every time": RING + "p = polygon(pts)\nfor i in range(100):\n    q = offset(p, 0.0001 * (i + 1))",
    "a large polygon a hundred times": RING + "for i in range(100):\n    p = polygon(pts)",
    "recursion": "def f(n):\n    return f(n + 1)\nf(0)",
    # one call on a large shape
    "moving a large loft many times": "s = [section(circle(0, 0, 1, 128), i * 0.1) for i in range(64)]\n"
                                      "l = loft(s)\nfor i in range(1000):\n    move(l, dx=0.001)",
    "measuring a large loft many times": "s = [section(circle(0, 0, 1, 128), i * 0.1) for i in range(64)]\n"
                                         "l = loft(s)\nfor i in range(1000):\n    b = bounds(l)",
    "copying a large loft": "s = [section(circle(0, 0, 1, 128), i * 0.1) for i in range(64)]\n"
                            "l = loft(s)\nrow = array(l, 300, dx=3)",
    "a tall stack": "b = extrude(rect(0, 0, 1, 1), 1)\nfor i in range(298):\n"
                    "    b = extrude(rect(0, 0, 1, 1), 1, at=top(b))\nprint(bounds(b))",
    # lowering: every shape is lowered under the same deadline, and the shapes hold 100 000 points at most
    "ninety large lofts": CIRCLE + "s = section(p, 0)\nt = section(p, 1)\nL = [s, t] * 32\nfor i in range(90):\n"
                                   "    loft(L)",
    "eighty large lofts on a tall stack": CIRCLE + STACK.format(height=200)
    + "s = section(p, top(b))\nt = section(p, top(b) + 1)\nL = [s, t] * 32\nfor i in range(80):\n    loft(L)",
    "one large loft on a taller stack, measured": CIRCLE + STACK.format(height=296)
    + "s = section(p, top(b))\nt = section(p, top(b) + 1)\nl = loft([s, t] * 32)\nx = bounds(l)",
    "a tall stack measured two thousand times": STACK.format(height=298)
    + "for i in range(1000):\n    x = bounds(b)\nfor i in range(1000):\n    x = bounds(b)",
    "equal profiles compared many times": PROFILES_COMPARED.replace("x = A == B", "for i in range(1000):\n    x = A == B"),
}


class BoundedWorkTests(ConstructionTestCase):
    def test_no_script_can_make_one_step_do_unbounded_work(self) -> None:
        for title, script in HOSTILE.items():
            with self.subTest(title):
                started = time.perf_counter()
                try:
                    result = run(script)
                    outcome = f"completed with {len(result.log)} printed lines"
                except ConstructionError as exc:
                    outcome = exc.message
                    self.assertEqual(layer_rule_violations(exc.message), ())
                    self.assertLessEqual(len(exc.message), 250, exc.message)
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 2.0, f"{title}: {outcome} after {elapsed:.2f} s")

    def test_the_bounds_are_refused_by_name_at_their_line(self) -> None:
        cases = [
            ("x = 1\ny = range(10001)", 2, "10 000 numbers"),
            ("x = 1\ny = round(2.5, 13)", 2, "-12 to 12"),
            ("x = [0] * 10000\ny = [x, 1]", 2, "10 000 elements"),
            ("for i in range(100):\n    x = [0] * 5000", 2, "200 000"),
            (PROFILES_COMPARED, 5, "20 000 evaluation steps"),  # each profile compares point by point
            ("s = section(circle(0, 0, 1, 128), 0)\nt = section(circle(0, 0, 1, 128), 1)\nL = [s, t] * 32\n"
             "for i in range(20):\n    loft(L)", 5, "100 000 points"),
            ("s = section(rect(0, 0, 1, 1), 0)\nl = loft([s] * 65)", 2, "64 sections"),
            (COMB + "for i in range(40):\n    polygon(pts + [(0, -5 - i * 0.001)])", 3, "1 000 000 pairs"),
        ]
        for script, line, words in cases:
            with self.subTest(script=script[:60]):
                error = self.refused(script)
                self.assertEqual(error.line, line, error.message)
                self.assertIn(words, error.message)

    def test_refusals_never_repeat_a_script_value_at_length(self) -> None:
        for script in ("level([[0] * 100] * 90)", "circle(0, 0, 1, [[0] * 100] * 90)",
                       "get('" + "a" * 5000 + "')", "b" * 5000 + " = extrude(rect(0, 0, 1, 1), 1)",
                       "name(extrude(rect(0, 0, 1, 1), 1), '" + "c" * 5000 + "')", "print(" + "d" * 5000 + ")"):
            with self.subTest(script=script[:40]):
                self.assertLessEqual(len(self.refused(script).message), 250)
        self.assertLessEqual(max(len(line) for line in self.log("print([[0] * 100] * 90)")), 2003)

    def test_a_wall_clock_deadline_is_checked_with_every_step(self) -> None:
        from monkeyarch.authoring.construction import script as interpreter

        original = interpreter.LIMITS["seconds"]
        interpreter.LIMITS["seconds"] = 0
        try:
            error = self.refused("x = 1\ny = 2")
        finally:
            interpreter.LIMITS["seconds"] = original
        self.assertIn("longer than 5 s", error.message)

    def test_lowering_runs_under_the_same_deadline(self) -> None:
        from monkeyarch.authoring.construction.lowering import _Lowering
        from monkeyarch.authoring.construction.script import run_script

        session, lines = run_script("x = 1\na = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(2, 0, 1, 1), 1)",
                                    _record())
        session.deadline = time.monotonic() - 1  # the script used its time up; lowering must not go on
        lowering = _Lowering(session, lines, "model")
        with self.assertRaises(ConstructionError) as caught:
            lowering.assign_ids()
            lowering.lower()
        self.assertEqual((caught.exception.line, caught.exception.message),
                         (2, "the script ran longer than 5 s; split it"))


class CallChainTests(ConstructionTestCase):
    def test_a_refusal_inside_a_function_names_the_calls(self) -> None:
        error = self.refused("def block(width):\n    return rect(0, 0, width, 1)\nx = 1\np = block(0)")
        self.assertEqual(error.line, 2)
        self.assertTrue(error.message.endswith("(called from line 4)"), error.message)
        error = self.refused("\n".join(["def inner(w):", "    return rect(0, 0, w, 1)",
                                        "def outer(w):", "    return inner(w)", "p = outer(0)"]))
        self.assertEqual(error.line, 2)
        self.assertTrue(error.message.endswith("(called from line 4, from line 5)"), error.message)

    def test_a_recursion_too_deep_for_the_interpreter_names_a_line(self) -> None:
        script = "def f(n):\n    return 0 if n == 0 else " + "1 + (" * 90 + "f(n - 1)" + ")" * 90 + "\nx = f(15)"
        error = self.refused(script)
        self.assertIsNotNone(error.line)
        self.assertIn("too deeply", error.message)


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
        self.assertEqual(contract["limits"], {
            "characters": 20000, "steps": 20000, "seconds": 5, "loopIterations": 1000, "callDepth": 16,
            "nesting": 100, "geometryResults": 300, "rangeItems": 10000, "listElements": 10000,
            "createdElements": 200000, "textCharacters": 10000, "printLines": 1000, "roundDigits": 12,
            "profilePoints": 256, "profileEdgePairs": 1000000, "pathPoints": 512, "totalPoints": 100000,
            "loftSections": 64,
            "cuttersPerShape": 300, "idLength": 90, "coordinateRange": 100000, "minimumLength": 0.000001})
        side = next(item for item in contract["verbs"] if item["name"] == "side")
        self.assertIn("opposite", side["description"])
        self.assertIn("name what you will edit later", contract["conventions"]["identity"])
        self.assertIn("keeps what it cuts", contract["conventions"]["identity"])

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
