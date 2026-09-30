"""The projection check's synthetic project: its size, its shape and its repeatability (GH-376).

Clock stamps, event ids and measured durations reach the content digests of
the records that cite them, so one of them left to the machine changes the
names and bytes of much of the project; two generations must therefore match
file for file, byte for byte.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .synthetic_project import FORK_BRANCH, MIN_RUNS, build_synthetic_project

RECORD_NAME = re.compile(r"^(?P<kind>.+)-[0-9a-f]{64}\.json$")


def record_kinds(project: Path) -> dict[str, Counter]:
    """Each run's retained records, counted by kind."""

    kinds: dict[str, Counter] = {}
    for run in sorted((project / "runs").iterdir()):
        counter: Counter = Counter()
        for area in ("records", "reviews"):
            for path in (run / area).glob("*.json") if (run / area).is_dir() else ():
                match = RECORD_NAME.match(path.name)
                if match:
                    counter[f"{area}/{match['kind']}"] += 1
        kinds[run.name] = counter
    return kinds


def files(project: Path) -> dict[str, bytes]:
    """Every file in the project, by its path inside it."""

    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}


def stage_chains(project: Path) -> dict[str, list[tuple[str, str]]]:
    """Every branch's Stages, as the design history lists them."""

    with TestClient(create_app(StudioSettings(project_dir=project, cad_export="off"))) as client:
        branches = client.get("/api/design-history", params={"branchId": "main"}).json()["branches"]
        chains = {}
        for branch in sorted(row["branchId"] for row in branches):
            history = client.get("/api/design-history", params={"branchId": branch}).json()
            chains[branch] = [(stage["label"], stage["candidateId"]) for stage in history["stages"]]
        return chains


class SyntheticProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="synthetic-project-"))
        cls.first = build_synthetic_project(cls.root / "first")
        cls.second = build_synthetic_project(cls.root / "second")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_project_is_named_by_its_folder_and_shaped_like_a_real_one(self) -> None:
        self.assertEqual(self.first.name, "synthetic-bench")
        runs = [path for path in (self.first / "runs").iterdir() if path.is_dir()]
        self.assertEqual(len(runs), 30)
        kinds = sum(record_kinds(self.first).values(), Counter())
        records = sum(kinds.values())
        self.assertGreaterEqual(records, 400)
        self.assertLessEqual(records, 500)
        candidates = [run.name for run in runs if run.name.startswith("studio-cand-")]
        self.assertEqual(len(candidates), 20)
        # Most candidates carry an OCCT receipt and a retained 3dm preview.
        self.assertGreater(kinds["records/seat-occt-execution"], len(candidates) * 3 // 4)
        previews = list((self.first / "runs").glob("studio-cand-*/workspaces/*/*.preview.3dm"))
        self.assertEqual(len(previews), kinds["records/seat-occt-execution"])
        self.assertGreaterEqual(kinds["reviews/candidate-review"], 3)
        self.assertGreaterEqual(kinds["reviews/candidate-admission"], 1)
        self.assertGreaterEqual(kinds["records/studio-source-document"], 2)
        self.assertEqual(kinds["records/studio-board-scene"], 1)

    def test_main_has_three_accepted_stages_and_the_fork_its_own(self) -> None:
        chains = stage_chains(self.first)
        self.assertEqual(set(chains), {"main", FORK_BRANCH})
        self.assertEqual(len(chains["main"]), 4)  # S0 and three accepted Stages
        fork = chains[FORK_BRANCH]
        self.assertEqual(fork[:2], chains["main"][:2])
        self.assertNotIn(fork[-1], chains["main"])

    def test_the_working_source_resolves_to_the_adopted_candidate(self) -> None:
        with TestClient(create_app(StudioSettings(project_dir=self.first, cad_export="off"))) as client:
            response = client.get("/api/working-source", params={"workspace": "modeling"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["head"]["runId"].startswith("studio-cand-"))

    def test_two_generations_are_identical_byte_for_byte(self) -> None:
        first, second = files(self.first), files(self.second)
        differences = {
            "only in the first": sorted(set(first) - set(second)),
            "only in the second": sorted(set(second) - set(first)),
            "different bytes": sorted(name for name in set(first) & set(second) if first[name] != second[name]),
        }
        self.assertEqual(differences, {key: [] for key in differences})

    def test_a_project_below_the_scenario_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_synthetic_project(self.root / "small", runs=MIN_RUNS - 1)


if __name__ == "__main__":
    unittest.main()
