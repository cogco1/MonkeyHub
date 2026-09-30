"""The projection check's verdict, on fabricated sides and once for real (GH-376)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from .synthetic_project import MIN_RUNS, build_synthetic_project

REPOSITORY = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location("projection_check", REPOSITORY / "tools" / "projection_check.py")
projection_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projection_check)

ROUTES = projection_check.ROUTES
HISTORY = projection_check.DESIGN_HISTORY


def reads(sha_prefix: str, *, etag: bool = False, ms: float = 10.0) -> list[dict]:
    return [{"route": route, "status": 200, "sha256": f"{sha_prefix}{index}", "bytes": 1, "ms": ms,
             "etag": f'"{sha_prefix}{index}"' if etag else None, **({"revalidateStatus": 304} if etag else {})}
            for index, route in enumerate(ROUTES)]


def candidate(*, etag: bool = False, cold_ms: float = 10.0) -> dict:
    side = {"cold": reads("before", etag=etag, ms=cold_ms), "after": reads("after", etag=etag),
            "supportsConditional": etag, "write": {"subjectRef": "studio-cand-001", "status": 201}}
    if etag:
        side["afterWrite"] = {"etagBefore": '"before0"', "etagAfter": '"after0"', "reviewVisible": True}
        side["artifactBytes"] = {"sha256": "a" * 64, "status": 200, "bodySha256": "a" * 64,
                                 "cacheControl": "private, max-age=31536000, immutable"}
    return side


class VerdictTests(unittest.TestCase):
    def judge(self, side: dict) -> dict:
        return projection_check.evaluate(reads("before"), reads("after"), side)

    def test_equal_bodies_pass(self) -> None:
        verdict = self.judge(candidate())
        self.assertTrue(verdict["passed"], verdict["failures"])
        self.assertFalse(verdict["supportsConditional"])
        self.assertTrue(all(row["parityBefore"] and row["parityAfter"] for row in verdict["routes"]))

    def test_a_tagging_candidate_that_answers_everything_passes(self) -> None:
        verdict = self.judge(candidate(etag=True))
        self.assertTrue(verdict["passed"], verdict["failures"])
        self.assertTrue(verdict["conditional"])

    def test_one_differing_body_fails_and_names_its_route(self) -> None:
        for phase in ("cold", "after"):
            with self.subTest(phase=phase):
                side = candidate()
                side[phase][3]["sha256"] = "seeded difference"
                verdict = self.judge(side)
                self.assertFalse(verdict["passed"])
                self.assertEqual(len(verdict["failures"]), 1, verdict["failures"])
                self.assertIn(ROUTES[3], verdict["failures"][0])
                row = next(row for row in verdict["routes"] if row["route"] == ROUTES[3])
                self.assertFalse(row["parityBefore"] if phase == "cold" else row["parityAfter"])

    def test_a_different_status_is_a_difference(self) -> None:
        side = candidate()
        side["cold"][0]["status"] = 500
        verdict = self.judge(side)
        self.assertFalse(verdict["passed"])
        self.assertTrue(any(HISTORY in failure for failure in verdict["failures"]))

    def test_a_missing_etag_on_a_candidate_that_tags_fails(self) -> None:
        side = candidate(etag=True)
        side["cold"][2]["etag"] = None
        verdict = self.judge(side)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["failures"], [f"conditional: {ROUTES[2]} carries an ETag"])

    def test_each_conditional_promise_is_checked(self) -> None:
        breaks = {
            "304": lambda side: side["cold"][1].update(revalidateStatus=200),
            "tag": lambda side: side["afterWrite"].update(etagAfter='"before0"'),
            "review": lambda side: side["afterWrite"].update(reviewVisible=False),
            "immutable": lambda side: side["artifactBytes"].update(cacheControl="no-cache"),
            "hash": lambda side: side["artifactBytes"].update(bodySha256="b" * 64),
            "artifact": lambda side: side.update(artifactBytes={"sha256": None}),
        }
        for name, damage in breaks.items():
            with self.subTest(name):
                side = candidate(etag=True)
                damage(side)
                verdict = self.judge(side)
                self.assertFalse(verdict["passed"])
                self.assertEqual(len(verdict["failures"]), 1, verdict["failures"])

    def test_a_failed_review_write_fails(self) -> None:
        side = candidate()
        side["write"] = {"subjectRef": "studio-cand-001", "status": 409, "detail": "conflict"}
        self.assertFalse(self.judge(side)["passed"])
        side["write"] = {"subjectRef": None}
        self.assertFalse(self.judge(side)["passed"])

    def test_timing_fails_only_when_twice_as_slow_and_200_ms_slower(self) -> None:
        self.assertFalse(projection_check.slower(10.0, 150.0))    # 15x, 140 ms
        self.assertFalse(projection_check.slower(400.0, 700.0))   # 1.75x, 300 ms
        self.assertTrue(projection_check.slower(100.0, 350.0))    # 3.5x, 250 ms
        verdict = self.judge(candidate(cold_ms=300.0))
        self.assertFalse(verdict["passed"])
        self.assertTrue(all(row["slower"] for row in verdict["routes"]))

    def test_the_summary_marks_a_difference(self) -> None:
        side = candidate()
        side["after"][4]["sha256"] = "seeded difference"
        result = {"platform": "test", "base": {"revision": "base"}, "candidate": {"revision": "head"},
                  "project": {"name": "synthetic-bench", "runs": 30, "jsonFiles": 494},
                  "write": side["write"], "verdict": self.judge(side), "differences": {}}
        text = projection_check.summary_markdown(result)
        self.assertIn("FAILED", text)
        row = next(line for line in text.splitlines() if ROUTES[4] in line and line.startswith("|"))
        self.assertEqual(row.count("❌"), 1)

    def test_first_difference_points_at_the_byte(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "a").write_bytes(b'{"label":"S1"}')
        (root / "b").write_bytes(b'{"label":"S2"}')
        self.assertIn("byte 11", projection_check.first_difference(root / "a", root / "b"))
        self.assertIsNone(projection_check.first_difference(root / "a", root / "a"))


class LayoutTests(unittest.TestCase):
    """A base from before #489 keeps archflow at its root; the check reads either layout."""

    def test_each_side_imports_archflow_from_where_it_keeps_it(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        old, new = root / "old", root / "new"
        for kernel in (old / "archflow", new / "packages" / "archflow" / "src" / "archflow"):
            kernel.mkdir(parents=True)
            (kernel / "__init__.py").write_text("", encoding="utf-8")
        self.assertEqual(projection_check._kernel_source(old), old)
        self.assertEqual(projection_check._kernel_source(new), new / "packages" / "archflow" / "src")
        self.assertEqual(projection_check._kernel_source(REPOSITORY), REPOSITORY / "packages" / "archflow" / "src")


class EndToEndTests(unittest.TestCase):
    """One real run: this checkout against itself, on the smallest scenario."""

    def test_this_checkout_matches_itself(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="projection-check-"))
        self.addCleanup(shutil.rmtree, root, True)
        project = build_synthetic_project(root / "projects", runs=MIN_RUNS)
        before = sorted(path.relative_to(project) for path in project.rglob("*"))

        result = projection_check.check(REPOSITORY, REPOSITORY, project, root / "work")

        verdict = result["verdict"]
        self.assertTrue(verdict["passed"], verdict["failures"])
        self.assertTrue(verdict["supportsConditional"])
        self.assertEqual(result["write"]["status"], 201)
        for side in ("baseBefore", "candidate", "baseAfter"):
            self.assertTrue(Path(result["sides"][side]["archflow"]).is_relative_to(REPOSITORY))
        # The check wrote into its copy, never into the project it was given.
        self.assertEqual(sorted(path.relative_to(project) for path in project.rglob("*")), before)
        json.dumps(result)  # plain data, as --out writes it


if __name__ == "__main__":
    unittest.main()
