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
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.inputs import load_authored_record
from archflow.project.layout import PROGRAM_SHEET_PATH
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import RunRef
from archflow.state.program_sheet import PROGRAM_SHEET_SCHEMA

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PORTICO_RECORD_PAYLOAD,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    advance_head,
    make_portico_project,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
    write_runner_record,
)
from .test_working_copies import register_model

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
        "recordDigest": None,
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
        authored_record = load_authored_record(self.repository).record
        bound_record = authored_record.bound_to(
            RunRef(PROJECT_ID, REFERENCE_RUN_ID, self.repository.read_head())
        )
        self.record_digest = bound_record.digest
        self.sheet_state_digest = bound_record.state_digest
        settings = (
            StudioSettings(cad_export="off", project_dir=self.repository.layout.root)
            if self.mode == "local"
            else StudioSettings(
                cad_export="off", project_dir=self.repository.layout.root,
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
        request_digest = digest if digest is not None else self.state_digest
        sheet = dict(sheet)
        if sheet.get("recordDigest") is None:
            sheet["recordDigest"] = self.record_digest
        if sheet.get("stateDigest") is None:
            sheet["stateDigest"] = self.sheet_state_digest
        response = self.client.post(
            "/api/program",
            json={
                "stateDigest": request_digest,
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
        self.assertEqual(answer["sheet"]["recordDigest"], self.record_digest)
        self.assertEqual(answer["sheet"]["stateDigest"], self.sheet_state_digest)
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
        self.assertTrue(
            any("GET /api/candidates/{id}" in line for line in answer["honesty"]),
            answer["honesty"],
        )
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

    def test_worker_refuses_when_head_moves_after_program_preflight(self) -> None:
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.candidate import run_operator
        from archflow_studio_api.application.program import operator_for
        from archflow_studio_api.application.projection import project_state
        from archflow_studio_api.transport.errors import StudioError
        from archflow_studio_api.transport.program import ProgramSheetDto, sheet_payload

        binding = bound_project(self.client.app.state)
        projection = project_state(binding)
        wire_sheet = sheet_adding("store", zone="zone-hall")
        wire_sheet["recordDigest"] = self.record_digest
        wire_sheet["stateDigest"] = self.sheet_state_digest
        operator = operator_for(
            sheet_payload(ProgramSheetDto.model_validate(wire_sheet)), projection
        )

        advance_head(self.repository, run_id="promotion-before-program-worker")

        with self.assertRaisesRegex(StudioError, "HEAD is version 1"):
            run_operator(
                binding,
                self.client.app.state.settings,
                operator,
                "studio-cand-stale-program-worker",
            )
        self.assertFalse(
            self.repository.layout.run("studio-cand-stale-program-worker").manifest.exists()
        )

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

    def test_a_stale_saved_sheet_is_not_silently_rebound_or_returned(self) -> None:
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

        first = self.get("/api/program")
        second = self.get("/api/program")

        for read in (first, second):
            self.assertEqual(read["source"], "derived")
            self.assertEqual(read["sheet"]["recordDigest"], self.record_digest)
            self.assertEqual(read["sheet"]["stateDigest"], self.sheet_state_digest)
            self.assertEqual(
                [d["departmentId"] for d in read["sheet"]["departments"]],
                ["public"],
            )
            self.assertTrue(
                any("was not returned or silently rebound" in line for line in read["sheet"]["honesty"]),
                read["sheet"]["honesty"],
            )

        status, answer = self.apply(second["sheet"], digest=second["stateDigest"])
        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])

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

        status, answer = self.apply(sheet, digest=self.state_digest)

        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])

    def test_an_inner_sheet_digest_cannot_be_hidden_by_a_current_outer_digest(self) -> None:
        sheet = self.get("/api/program")["sheet"]
        sheet["stateDigest"] = "b" * 64

        status, answer = self.apply(sheet, digest=self.state_digest)

        self.assertEqual(status, 409, answer)
        self.assertEqual(answer["code"], "STALE_BASE")

    def test_authored_wip_drift_does_not_replace_the_retained_program_base(self) -> None:
        sheet = self.get("/api/program")["sheet"]
        changed = json.loads(json.dumps(self.payload))
        for entity in changed["entities"]:
            if entity["entity_id"] == "portico-base":
                entity["fields"]["author_note"] = "content changed"
        write_runner_record(self.repository, changed)
        self.authored = self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).read_bytes()
        current = self.get("/api/state")
        self.assertEqual(current["stateDigest"], self.state_digest)
        self.assertEqual(current["recordDigest"], self.record_digest)

        status, answer = self.apply(sheet)

        self.assertEqual(status, 202, answer)
        self.assertEqual(self.finished(answer["jobId"])["status"], "succeeded")

    def test_saved_sheet_stays_bound_to_the_retained_run_when_wip_drifts(self) -> None:
        sheet = self.get("/api/program")["sheet"]
        status, answer = self.apply(sheet, save=True)
        self.assertEqual(status, 202, answer)
        self.finished(answer["jobId"])
        changed = json.loads(json.dumps(self.payload))
        for entity in changed["entities"]:
            if entity["entity_id"] == "portico-base":
                entity["fields"]["author_note"] = "content changed"
        write_runner_record(self.repository, changed)
        self.authored = self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).read_bytes()
        current = self.get("/api/state")
        self.assertEqual(current["stateDigest"], self.state_digest)

        read = self.get("/api/program")

        self.assertEqual(read["source"], "input")
        self.assertEqual(read["sheet"]["recordDigest"], self.record_digest)
        saved = json.loads(
            self.repository.layout.program_sheet.read_text(encoding="utf-8")
        )
        self.assertEqual(saved["record_digest"], self.record_digest)


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


class SelectedProgramTests(ProgramTestCase):
    """Continue an explicit retained run, without rebinding authored WIP."""

    def setUp(self) -> None:
        super().setUp()
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(
            cad_export="off", project_dir=self.repository.layout.root,
            reference_run=REFERENCE_RUN_ID,
        )))
        self.addCleanup(self.client.close)
        self.source_run_id = "selected-program-source"
        payload = deepcopy(self.payload)
        for entity in payload["entities"]:
            if entity["entity_id"] == "zone-hall":
                entity["fields"]["program_node_refs"] = ["program:selected/meeting"]
            elif entity["entity_id"] == "portico-base":
                entity["fields"]["params"]["height"] = 1.2
        source = self.repository.create_run(self.source_run_id)
        retain_runner_receipt(
            self.repository, source, record_payload=payload,
            design_state_digest=runner_state_digest(self.repository, self.source_run_id, payload),
        )
        self.source_state = self.get(f"/api/state?run={self.source_run_id}")

    def source_sheet(self) -> dict:
        from archflow.state.program_sheet import sheet_from_record
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.projection import project_state
        from archflow_studio_api.transport.program import sheet_dto

        projection = project_state(bound_project(self.client.app.state), self.source_run_id)
        return sheet_dto(sheet_from_record(projection.record)).model_dump(by_alias=True)

    def register_source_model(self, fixture: str = "a") -> dict:
        data = (Path(__file__).parent / f"fixtures/model-source-{fixture}.3dm").read_bytes()
        return register_model(
            self.client, self.source_run_id, self.source_state["stateDigest"], data,
        )["modelSource"]

    def test_selected_apply_passes_the_exact_asset_to_the_worker(self) -> None:
        source = self.register_source_model("a")
        other = self.register_source_model("b")
        self.assertNotEqual(source["assetSha256"], other["assetSha256"])
        self.assertEqual(
            self.client.get(f"/api/artifacts/{other['assetSha256']}/bytes").status_code, 200,
        )

        with patch("archflow_studio_api.routes.program.run_operator", return_value={}) as worker:
            response = self.client.post("/api/program", json={
                "sourceRunId": self.source_run_id,
                "stateDigest": self.source_state["stateDigest"],
                "sheet": self.source_sheet(),
                "modelSource": source,
            })
            self.assertEqual(response.status_code, 202, response.text)
            job = self.finished(response.json()["jobId"])
            self.assertEqual(job["status"], "succeeded", job)

        worker.assert_called_once()
        self.assertEqual(worker.call_args.kwargs["source_run_id"], self.source_run_id)
        self.assertEqual(worker.call_args.kwargs["model_source"].to_dict(), source)

    def test_invalid_model_source_is_refused_before_a_program_run(self) -> None:
        source = self.register_source_model()
        request = {
            "sourceRunId": self.source_run_id,
            "stateDigest": self.source_state["stateDigest"],
            "sheet": self.source_sheet(),
            "modelSource": source,
        }
        runs_before = sorted(path.name for path in self.repository.layout.runs.iterdir())
        for change, code in (
            ({"modelSource": {**source, "runId": REFERENCE_RUN_ID}}, "MODEL_SOURCE_MISMATCH"),
            ({"modelSource": {**source, "stateDigest": self.state_digest}}, "MODEL_SOURCE_MISMATCH"),
            ({"modelSource": {**source, "assetSha256": "0" * 64}}, "MODEL_SOURCE_UNREGISTERED"),
            ({"stateDigest": self.state_digest}, "STALE_BASE"),
            ({"sourceRunId": REFERENCE_RUN_ID, "stateDigest": self.state_digest,
              "sheet": self.get("/api/program")["sheet"]}, "MODEL_SOURCE_MISMATCH"),
        ):
            with self.subTest(change=change), patch(
                "archflow_studio_api.routes.program.run_operator", return_value={},
            ) as worker:
                response = self.client.post("/api/program", json={**request, **change})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["code"], code)
                worker.assert_not_called()
                self.assertEqual(
                    sorted(path.name for path in self.repository.layout.runs.iterdir()), runs_before,
                )

    def test_selected_read_derives_its_record_instead_of_the_authored_sheet(self) -> None:
        from archflow_studio_api.transport.program import ProgramSheetDto, sheet_payload

        sheet = self.source_sheet()
        sheet["departments"][0]["name"] = "Unapplied authored brief"
        path = self.repository.layout.resolve_relative(PROGRAM_SHEET_PATH)
        path.write_text(json.dumps(sheet_payload(ProgramSheetDto.model_validate(sheet))), encoding="utf-8")
        before = path.read_bytes()

        answer = self.get(f"/api/program?run={self.source_run_id}")

        self.assertEqual(answer["source"], "derived")
        self.assertEqual(answer["sourceRunId"], self.source_run_id)
        self.assertEqual(answer["stateDigest"], self.source_state["stateDigest"])
        self.assertEqual(answer["sheet"], self.source_sheet())
        self.assertEqual(path.read_bytes(), before)
        default = self.get("/api/program")
        self.assertIsNone(default["sourceRunId"])
        self.assertEqual(default["stateDigest"], self.state_digest)
        self.assertEqual(default["sheet"]["departments"][0]["departmentId"], "public")

    def test_selected_apply_keeps_the_sources_existing_changes(self) -> None:
        sheet = self.source_sheet()
        addition = sheet_adding("store", zone="zone-hall")
        sheet["departments"].extend(addition["departments"])
        sheet["adjacencies"] = addition["adjacencies"]
        before_head = self.repository.read_head()
        response = self.client.post("/api/program", json={
            "sourceRunId": self.source_run_id,
            "stateDigest": self.source_state["stateDigest"],
            "sheet": sheet,
            "modelSource": None,
        })
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        continued = self.get(f"/api/state?run={accepted['candidateId']}")
        self.assertNotEqual(continued["recordDigest"], self.source_state["recordDigest"])
        retained = [
            self.repository.load_json(ref)
            for ref in self.repository.list_json(
                run=self.repository.load_run(accepted["candidateId"]),
                destination=run_records(accepted["candidateId"]),
            )
            if ref.relative_path.split("/")[-1].startswith(f"{STATE_RECORD}-")
        ]
        entities = {item["entity_id"]: item for item in retained[0]["entities"]}
        self.assertEqual(entities["portico-base"]["fields"]["params"]["height"], 1.2)
        self.assertEqual(entities["zone-hall"]["fields"]["program_node_refs"], ["program:selected/meeting"])
        self.assertEqual(entities["store"]["schema"], "Space@1")
        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(self.get("/api/program")["stateDigest"], self.state_digest)
        self.assertEqual(self.get(f"/api/program?run={self.source_run_id}")["sheet"], self.source_sheet())

    def test_invalid_source_or_digest_is_refused_without_falling_back(self) -> None:
        missing = self.client.get("/api/program", params={"run": "missing-source"})
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["code"], "RUN_NOT_FOUND")
        for change, status, code in (
            ({"sourceRunId": "missing-source"}, 404, "RUN_NOT_FOUND"),
            ({"stateDigest": self.state_digest}, 409, "STALE_BASE"),
        ):
            with self.subTest(code=code):
                response = self.client.post("/api/program", json={
                    "sourceRunId": self.source_run_id,
                    "stateDigest": self.source_state["stateDigest"],
                    "sheet": self.source_sheet(),
                    **change,
                })
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["code"], code)

    def test_an_inexact_selected_run_does_not_return_authored_wip(self) -> None:
        self.repository.create_run("incomplete-program-source")
        response = self.client.get("/api/program", params={"run": "incomplete-program-source"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "REFERENCE_STATE_NOT_EXACT")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
