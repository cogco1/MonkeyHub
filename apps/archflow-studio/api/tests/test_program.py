"""The program sheet over the wire: read it, apply it, and keep it or not.

Three records answer three different questions here.

The portico fixture declares no ``Space@1`` at all, so the derivation has
nothing to read and has to say so rather than answering with an empty table
that looks like a building with no rooms. The zoned record adds one program
zone — and no massing — so a sheet applied to it becomes a real candidate run
through the same path a proposal's candidate takes. The massing record draws
volumes and levels, and there a zone the sheet would add carries no volume,
which the kernel refuses to view; that refusal is a fact about the record and
is asserted, not worked around.

Nothing here is villa data, and the authored ``input/runner/state-record.json``
is compared byte for byte in every test that runs anything.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.project.layout import PROGRAM_SHEET_PATH
from archflow.project.record_kinds import STATE_RECORD
from archflow.state.program_sheet import PROGRAM_SHEET_SCHEMA

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PORTICO_RECORD_PAYLOAD,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    make_portico_project,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
    write_runner_record,
)

JOB_DEADLINE = 120.0
TERMINAL = ("succeeded", "failed")

# The demo record — the one whose seat pack the runner can actually execute —
# with one program zone under its portico, and no massing: no ``Volume@1`` and
# no ``MassingLevel@1``, so the kernel's design view takes the block path and a
# zone that occupies nothing is not a contradiction.
ZONED_RECORD_PAYLOAD: dict[str, object] = {
    **{k: v for k, v in RECORD_PAYLOAD.items() if k != "entities"},
    "entities": [
        *RECORD_PAYLOAD["entities"],  # type: ignore[misc]
        {
            "entity_id": "zone-hall",
            "schema": "Space@1",
            "parent_id": "portico",
            "fields": {
                "program_node_refs": ["program:public/hall"],
                "level_ids": ["level-ground"],
                "volume_ids": [],
            },
        },
    ],
}

# The same record with massing drawn: one massing level, one box owned by the
# portico component, and the zone standing on it. This is the record a
# program-only zone cannot enter.
MASSING_RECORD_PAYLOAD: dict[str, object] = {
    **{k: v for k, v in RECORD_PAYLOAD.items() if k not in ("entities", "option")},
    "option": {
        **RECORD_PAYLOAD["option"],  # type: ignore[misc]
        "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]],
    },
    "entities": [
        *(
            entity
            if entity["entity_id"] != "portico"
            else {
                **entity,
                # Every massing volume needs one semantic owner: the record
                # says which component the box belongs to.
                "fields": {**entity["fields"], "volume_ids": ["volume-hall"]},
            }
            for entity in RECORD_PAYLOAD["entities"]  # type: ignore[misc]
        ),
        {
            "entity_id": "massing-ground",
            "schema": "MassingLevel@1",
            "fields": {"base_y": 0, "height": 4},
        },
        {
            "entity_id": "volume-hall",
            "schema": "Volume@1",
            "fields": {"min": [0, 0, 0], "max": [3, 3, 5], "level_ids": ["massing-ground"]},
        },
        {
            "entity_id": "zone-hall",
            "schema": "Space@1",
            "parent_id": "portico",
            "fields": {
                "program_node_refs": ["program:public/hall"],
                "level_ids": ["massing-ground"],
                "volume_ids": ["volume-hall"],
            },
        },
    ],
}


def sheet_adding(space_id: str, *, zone: str, requirement: str = "adjacent") -> dict:
    """A sheet that adds one space and one requirement against a zone."""

    return {
        "schema": PROGRAM_SHEET_SCHEMA,
        "projectId": PROJECT_ID,
        "stateDigest": None,
        "departments": [
            {
                "departmentId": "service",
                "name": "Service",
                "spaces": [
                    {
                        "spaceId": space_id,
                        "name": "Store",
                        "function": "storage",
                        "targetAreaM2": 18.0,
                        "count": 2,
                        "clearHeightM": 2.4,
                        "levelIds": ["level-ground"],
                        "zoneId": None,
                        "mappedAreaM2": None,
                    }
                ],
            }
        ],
        "adjacencies": [
            {
                "fromSpaceId": space_id,
                "toSpaceId": zone,
                "requirement": requirement,
                "relationId": None,
            }
        ],
        "totals": {"targetAreaM2": 0.0, "mappedAreaM2": 0.0, "unmappedSpaces": []},
        "honesty": [],
    }


class ProgramTestCase(unittest.TestCase):
    """One project, one client, and the authored record watched throughout."""

    payload: dict[str, object] = ZONED_RECORD_PAYLOAD
    mode: str = "local"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        if self.payload is PORTICO_RECORD_PAYLOAD:
            # The reproduction fixture: read only. Its seat pack owns a
            # component with no element, which the runner refuses — so nothing
            # in the class that uses it runs a candidate.
            self.repository, _ = make_portico_project(self.root)
        else:
            self.repository, _ = make_project(self.root)
            write_runner_record(self.repository, self.payload)
            self.repository.put_json(
                run=self.repository.load_run(REFERENCE_RUN_ID),
                destination=run_records(REFERENCE_RUN_ID),
                record_kind=STATE_RECORD,
                payload=self.payload,
            )
            retain_runner_receipt(
                self.repository,
                self.repository.load_run(REFERENCE_RUN_ID),
                design_state_digest=runner_state_digest(
                    self.repository, REFERENCE_RUN_ID, self.payload
                ),
            )
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID, self.payload
        )
        settings = (
            StudioSettings(project_dir=self.repository.layout.root)
            if self.mode == "local"
            else StudioSettings(
                project_dir=self.repository.layout.root,
                mode="remote",
                api_token="t" * 32,
                origins=("http://localhost:5173",),
            )
        )
        self.client = TestClient(create_app(settings))
        self.addCleanup(self.client.close)
        self.authored = self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).read_bytes()

    def tearDown(self) -> None:
        self.assertEqual(
            self.repository.layout.resolve_relative(
                RUNNER_RECORD_PATH
            ).read_bytes(),
            self.authored,
            "the authored state record was rewritten",
        )

    def headers(self) -> dict[str, str]:
        return (
            {}
            if self.mode == "local"
            else {"Authorization": "Bearer " + "t" * 32}
        )

    def get(self, path: str) -> dict:
        response = self.client.get(path, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def apply(self, sheet: dict, *, save: bool = False, digest: str | None = None) -> tuple[int, dict]:
        response = self.client.post(
            "/api/program",
            json={
                "stateDigest": digest if digest is not None else self.state_digest,
                "sheet": sheet,
                "saveInput": save,
            },
            headers=self.headers(),
        )
        return response.status_code, response.json()

    def finished(self, job_id: str) -> dict:
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            payload = self.get(f"/api/jobs/{job_id}")
            if payload["status"] in TERMINAL:
                return payload
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} never finished")


class DeriveTests(ProgramTestCase):
    """``GET /api/program`` where the project holds no authored sheet."""

    payload = PORTICO_RECORD_PAYLOAD

    def test_a_record_with_no_zone_answers_an_empty_sheet_that_says_why(self) -> None:
        answer = self.get("/api/program")

        self.assertEqual(answer["source"], "derived")
        self.assertEqual(answer["sheet"]["departments"], [])
        self.assertEqual(answer["sheet"]["adjacencies"], [])
        self.assertTrue(
            any("no Space@1" in line for line in answer["sheet"]["honesty"]),
            answer["sheet"]["honesty"],
        )

    def test_the_derived_sheet_names_the_state_it_was_read_from(self) -> None:
        answer = self.get("/api/program")

        self.assertEqual(answer["stateDigest"], self.state_digest)
        self.assertEqual(answer["sheet"]["stateDigest"], self.state_digest)
        self.assertEqual(answer["sheet"]["projectId"], PROJECT_ID)

    def test_the_semantic_vocabulary_is_served_rather_than_copied(self) -> None:
        answer = self.get("/api/semantics")

        ids = {term["id"] for term in answer["roles"]}
        self.assertIn("role.storage", ids)
        self.assertIn("storage", {a for t in answer["roles"] for a in t["aliases"]})
        self.assertIn(
            "condition.threshold", {t["id"] for t in answer["conditions"]}
        )

    def test_the_program_is_a_declared_capability(self) -> None:
        self.assertIn(
            "program", self.get("/api/protocol")["capabilities"]
        )


class ZonedDeriveTests(ProgramTestCase):
    """``GET /api/program`` reading the zones a record does declare."""

    def test_one_row_per_zone_under_the_department_its_ref_names(self) -> None:
        sheet = self.get("/api/program")["sheet"]

        self.assertEqual(
            [d["departmentId"] for d in sheet["departments"]], ["public"]
        )
        space = sheet["departments"][0]["spaces"][0]
        self.assertEqual(space["spaceId"], "hall")
        self.assertEqual(space["zoneId"], "zone-hall")
        # No box is drawn for it, so it claims no area at all.
        self.assertIsNone(space["targetAreaM2"])
        self.assertIsNone(space["mappedAreaM2"])
        self.assertIsNone(space["clearHeightM"])

    def test_the_function_is_the_component_the_zone_hangs_under(self) -> None:
        sheet = self.get("/api/program")["sheet"]

        self.assertEqual(
            sheet["departments"][0]["spaces"][0]["function"],
            "controlled-entry",
        )


class ApplyTests(ProgramTestCase):
    """``POST /api/program``: a sheet becomes a candidate, and nothing else."""

    def test_applying_a_sheet_queues_a_candidate_and_totals_it(self) -> None:
        status, answer = self.apply(sheet_adding("store", zone="zone-hall"))

        self.assertEqual(status, 202, answer)
        self.assertTrue(answer["candidateId"].startswith("studio-cand-"))
        self.assertEqual(answer["status"], "queued")
        # Two stores of 18 m² each: the server's own sum, not the client's zero.
        self.assertEqual(answer["totals"]["targetAreaM2"], 36.0)
        self.assertEqual(answer["totals"]["unmappedSpaces"], ["store"])
        self.assertFalse(answer["savedInput"])
        self.finished(answer["jobId"])

    def test_the_candidate_run_holds_the_record_the_sheet_made(self) -> None:
        status, answer = self.apply(sheet_adding("store", zone="zone-hall"))
        self.assertEqual(status, 202, answer)
        job = self.finished(answer["jobId"])
        self.assertEqual(job["status"], "succeeded", job)

        retained = [
            self.repository.load_json(ref)
            for ref in self.repository.list_json(
                run=self.repository.load_run(answer["candidateId"]),
                destination=run_records(answer["candidateId"]),
            )
            if ref.relative_path.split("/")[-1].startswith(f"{STATE_RECORD}-")
        ]
        self.assertEqual(len(retained), 1, retained)
        entities = {e["entity_id"]: e for e in retained[0]["entities"]}
        self.assertEqual(entities["store"]["schema"], "Space@1")
        self.assertEqual(
            entities["store"]["fields"]["program_node_refs"],
            ["program:service/store"],
        )
        relation = "program-adjacent-store-zone-hall"
        self.assertEqual(
            entities[f"connection-{relation}"]["fields"]["relationship_refs"],
            [f"relation:{relation}"],
        )
        kinds = {r["relation_id"]: r["kind"] for r in retained[0]["relations"]}
        self.assertEqual(kinds[relation], "adjacent")

    def test_a_sheet_written_against_another_state_is_refused(self) -> None:
        status, answer = self.apply(
            sheet_adding("store", zone="zone-hall"), digest="a" * 64
        )

        self.assertEqual(status, 409, answer)
        self.assertEqual(answer["code"], "STALE_BASE")

    def test_an_unregistered_function_is_refused_by_the_kernel_sentence(self) -> None:
        sheet = sheet_adding("store", zone="zone-hall")
        sheet["departments"][0]["spaces"][0]["function"] = "brooding-nook"

        status, answer = self.apply(sheet)

        self.assertEqual(status, 422, answer)
        self.assertEqual(answer["code"], "PROGRAM_SHEET_INVALID")
        self.assertIn("brooding-nook", answer["detail"])

    def test_a_requirement_with_no_kernel_kind_names_the_vocabulary(self) -> None:
        status, answer = self.apply(
            sheet_adding("store", zone="zone-hall", requirement="visual")
        )

        self.assertEqual(status, 422, answer)
        self.assertIn("visual", answer["detail"])
        self.assertIn("adjacent", answer["detail"])

    def test_saving_the_sheet_writes_the_authored_input_and_says_so(self) -> None:
        status, answer = self.apply(
            sheet_adding("store", zone="zone-hall"), save=True
        )

        self.assertEqual(status, 202, answer)
        self.assertTrue(answer["savedInput"])
        written = json.loads(
            self.repository.layout.program_sheet.read_text(encoding="utf-8")
        )
        self.assertEqual(written["schema"], PROGRAM_SHEET_SCHEMA)
        self.assertEqual(
            written["departments"][0]["spaces"][0]["space_id"], "store"
        )
        self.finished(answer["jobId"])

    def test_a_saved_sheet_is_what_the_next_read_answers_with(self) -> None:
        status, answer = self.apply(
            sheet_adding("store", zone="zone-hall"), save=True
        )
        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])

        read = self.get("/api/program")

        self.assertEqual(read["source"], "input")
        self.assertEqual(
            [d["departmentId"] for d in read["sheet"]["departments"]],
            ["service"],
        )
        self.assertTrue(
            any(PROGRAM_SHEET_PATH in line for line in read["sheet"]["honesty"]),
            read["sheet"]["honesty"],
        )
        # The totals are the server's sum of the rows, never the file's claim.
        self.assertEqual(read["sheet"]["totals"]["targetAreaM2"], 36.0)

    def test_a_saved_sheet_written_against_another_state_is_flagged(self) -> None:
        sheet = sheet_adding("store", zone="zone-hall")
        status, answer = self.apply(sheet, save=True)
        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])
        # Rewrite the sheet file as if it had been authored against an old state.
        stale = json.loads(
            self.repository.layout.program_sheet.read_text(encoding="utf-8")
        )
        stale["state_digest"] = "b" * 64
        self.repository.layout.program_sheet.write_text(
            json.dumps(stale, indent=2, sort_keys=True), encoding="utf-8"
        )

        honesty = self.get("/api/program")["sheet"]["honesty"]

        self.assertTrue(
            any("may have moved" in line for line in honesty), honesty
        )

    def test_a_file_at_that_path_that_is_not_a_sheet_is_named_not_ignored(self) -> None:
        self.repository.layout.program_sheet.parent.mkdir(
            parents=True, exist_ok=True
        )
        self.repository.layout.program_sheet.write_text(
            json.dumps({"schema": "RunnerSeats@1"}), encoding="utf-8"
        )

        response = self.client.get("/api/program", headers=self.headers())

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "PROGRAM_SHEET_INVALID")


class MassingRecordTests(ProgramTestCase):
    """A record that draws massing: a zone with no volume cannot enter it."""

    payload = MASSING_RECORD_PAYLOAD

    def test_the_derived_sheet_reads_the_area_off_the_volume_box(self) -> None:
        space = self.get("/api/program")["sheet"]["departments"][0]["spaces"][0]

        # 4 x 6 cells at one square metre each.
        self.assertEqual(space["targetAreaM2"], 24.0)
        self.assertEqual(space["mappedAreaM2"], 24.0)

    def test_a_program_only_space_is_refused_before_any_run_is_made(self) -> None:
        status, answer = self.apply(sheet_adding("store", zone="zone-hall"))

        self.assertEqual(status, 422, answer)
        self.assertEqual(answer["code"], "PROGRAM_SHEET_NOT_APPLICABLE")
        self.assertIn("volume_ids", answer["detail"])
        # Refused before anything was queued: only the reference run exists.
        self.assertEqual(
            sorted(p.name for p in self.repository.layout.runs.iterdir()),
            [REFERENCE_RUN_ID],
        )

    def test_a_sheet_that_only_maps_existing_zones_still_applies(self) -> None:
        sheet = self.get("/api/program")["sheet"]
        sheet["departments"][0]["departmentId"] = "renamed"
        sheet["adjacencies"] = []

        status, answer = self.apply(sheet, digest=sheet["stateDigest"])

        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])


class RemoteModeTests(ProgramTestCase):
    """Saving the architect's file is a local studio's act and no other's."""

    mode = "remote"

    def test_reading_and_applying_work_the_same_on_a_remote_server(self) -> None:
        status, answer = self.apply(sheet_adding("store", zone="zone-hall"))

        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])

    def test_saving_is_refused_and_the_candidate_is_still_made(self) -> None:
        status, answer = self.apply(
            sheet_adding("store", zone="zone-hall"), save=True
        )

        self.assertEqual(status, 409, answer)
        self.assertEqual(answer["code"], "WIP_WRITE_REMOTE")
        self.assertIn(PROGRAM_SHEET_PATH, answer["detail"])
        self.assertFalse(self.repository.layout.program_sheet.exists())
        # The run it named was made, and finishes.
        candidate = answer["detail"].split("candidate ")[1].split(",")[0]
        job = answer["detail"].split("job ")[1].split(".")[0]
        self.assertTrue(candidate.startswith("studio-cand-"))
        self.finished(job)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
