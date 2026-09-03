"""Where the seconds went, on the wire.

Every number here is read off a run receipt's own fields — ``wall_time_s``,
the seat rows' ``wall_time_s`` and their ``cad`` blocks — and the one derived
number (the rebuild ratio) is derived only when the receipt carries both counts.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.candidate import (
    CandidateRun,
    RelationTotals,
    SeatOutcome,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.candidate import timings_dto, to_dto

from archflow.project.refs import ProjectVersionRef

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest


def a_candidate(*seats: SeatOutcome, wall_time_s: float | None = 79.435) -> CandidateRun:
    return CandidateRun(
        candidate_id="studio-cand-test",
        proposal_id="studio-p",
        job_id="job-1",
        status="succeeded",
        base=ProjectVersionRef(PROJECT_ID, 0, "0" * 64),
        state_digest=None,
        record_digest=None,
        changed_vs_projection=None,
        receipt_ref="project://x/records/runner-run-receipt-abc.json",
        seat_execution_complete=True,
        seat_results=seats,
        relation_checks=RelationTotals(1, 0, 0, True, True),
        artifacts=(),
        skipped_runs=(),
        wall_time_s=wall_time_s,
        honesty=(),
    )


def a_seat(seat_id: str, *, cad: dict | None, wall_time_s: float | None = None) -> SeatOutcome:
    return SeatOutcome(
        seat_id=seat_id,
        status="proposal_accepted",
        program_ref=None,
        program_digest=None,
        objects=1,
        relation_check_ref=None,
        cad=cad,
        wall_time_s=wall_time_s,
    )


class TimingsFromReceiptTests(unittest.TestCase):
    def test_a_run_that_exported_nothing_has_no_export_timings(self) -> None:
        timings = timings_dto(a_candidate(a_seat("seat-structure", cad=None, wall_time_s=0.1)))
        self.assertEqual(timings.exports, [])
        self.assertEqual(timings.run_s, 79.435)
        self.assertEqual(timings.seats[0].seat_id, "seat-structure")
        self.assertEqual(timings.seats[0].wall_time_s, 0.1)

    def test_a_full_rebuild_carries_its_seconds_and_no_ratio(self) -> None:
        # the shape the runner wrote on 2026-09-03 for the villa envelope seat
        cad = {
            "execution_ref": "project://villa/runs/r/records/seat-rhino-execution-e8e8.json",
            "failures": [],
            "inspection_ref": "project://villa/runs/r/records/seat-3dm-inspection-abeb.json",
            "model": "C:/tmp/x.3dm",
            "path": "rebuild",
            "readback_verified": True,
            "seconds": 39.664,
            "status": "succeeded",
        }
        timings = timings_dto(a_candidate(a_seat("seat-envelope", cad=cad, wall_time_s=39.7)))
        [export] = timings.exports
        self.assertEqual(export.seat_id, "seat-envelope")
        self.assertEqual(export.path, "rebuild")
        self.assertEqual(export.seconds, 39.664)
        self.assertEqual(export.status, "succeeded")
        self.assertIsNone(export.rebuilt_objects)
        self.assertIsNone(export.kept_objects)
        self.assertIsNone(export.rebuild_ratio)

    def test_a_patch_carries_its_counts_and_the_ratio(self) -> None:
        cad = {"path": "patch", "seconds": 4.2, "status": "succeeded", "rebuilt_objects": 1, "kept_objects": 59}
        timings = timings_dto(a_candidate(a_seat("seat-envelope", cad=cad)))
        [export] = timings.exports
        self.assertEqual(export.path, "patch")
        self.assertEqual(export.rebuilt_objects, 1)
        self.assertEqual(export.kept_objects, 59)
        self.assertAlmostEqual(export.rebuild_ratio or 0, 1 / 60)

    def test_a_block_missing_fields_answers_null_not_a_guess(self) -> None:
        timings = timings_dto(a_candidate(a_seat("seat-x", cad={"status": "failed"})))
        [export] = timings.exports
        self.assertIsNone(export.path)
        self.assertIsNone(export.seconds)
        self.assertEqual(export.status, "failed")
        self.assertIsNone(export.rebuild_ratio)

    def test_the_candidate_dto_carries_the_timings(self) -> None:
        candidate = a_candidate(a_seat("seat-structure", cad={"path": "rebuild", "seconds": 39.4, "status": "succeeded"}))
        dto = to_dto(candidate)
        self.assertEqual(dto.timings.run_s, 79.435)
        self.assertEqual(dto.timings.exports[0].seconds, 39.4)
        self.assertEqual(to_dto(replace(candidate, wall_time_s=None)).timings.run_s, None)


class IntentTimingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID)))
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def test_the_intent_answer_times_its_two_halves(self) -> None:
        response = self.client.post(
            "/api/intents",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
                "elementId": "portico-base",
                "utterance": "set height to 0.8",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        timings = response.json()["timings"]
        self.assertEqual(timings["compileMs"], 0)  # the grammar short-circuit asks no agent
        self.assertGreaterEqual(timings["typeMs"], 0)
        self.assertLess(timings["typeMs"], 5000)
