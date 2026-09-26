"""The hidden reference reaches the evaluator only; strategies and allocators see what they are given."""

import ast
import unittest
from pathlib import Path

from .environment import Environment
from .reference import FLAGS, REFERENCES
from .strategies import STRATEGIES

LAB = Path(__file__).resolve().parent


def imported(path: Path) -> set[str]:
    """Module names a file imports, relative ones without their leading dots."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


class IsolationTests(unittest.TestCase):
    def test_only_the_evaluator_imports_the_hidden_reference(self):
        importers = {path.name for path in LAB.glob("*.py")
                     if not path.name.startswith("test_") and "reference" in imported(path)}
        self.assertEqual(importers, {"evaluator.py"})

    def test_the_strategy_side_cannot_reach_evaluation(self):
        for name in ("strategies.py", "environment.py", "state_snapshot.py"):
            with self.subTest(name=name):
                self.assertFalse(imported(LAB / name) & {"reference", "evaluator", "rollout", "benchmark", "allocator"})

    def test_the_allocator_sees_statistics_only(self):
        lab_imports = {name for name in imported(LAB / "allocator.py") if name.startswith("labs") or "." not in name}
        self.assertFalse(lab_imports & {"reference", "evaluator", "rollout", "strategies", "environment", "benchmark"})

    def test_no_prompt_carries_reference_wording(self):
        env = Environment()
        hidden = {flag.lower() for flag in FLAGS} | {reference.goal_text.lower() for reference in REFERENCES.values()}
        hidden |= {"optimal", "pruning", "forbidden", "wrong target"}
        for case_id in env.case_ids:
            snapshot = env.reset(case_id)
            for strategy in STRATEGIES.values():
                prompt = strategy.request(env, snapshot, horizon=3).prompt.lower()
                for word in hidden:
                    with self.subTest(case_id=case_id, strategy=strategy.strategy_id, word=word):
                        self.assertNotIn(word, prompt)


if __name__ == "__main__":
    unittest.main()
