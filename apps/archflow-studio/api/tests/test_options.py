"""Several massings on the table, measured, and one of them run.

The fixture is ``support``'s project authored with a record that declares a
massing: two massing levels 4 cells tall, one 6x4 volume standing on both, and
one zone owning it. Every number asserted here is computed from that in the
test's own words — 6 x 4 = 24 m2 of ground, twice over = 48 m2 of floor, 8 m
tall — so a failure says which arithmetic moved rather than which constant.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import record_kind
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
    write_runner_record,
)

JOB_DEADLINE = 180.0
TERMINAL = ("succeeded", "failed")

# The record ``support`` authors, plus a massing: two levels of four cells,
# one 6x4 volume on both of them, one zone that owns it, and the portico as
# that volume's single semantic owner — the kernel requires exactly one.
MASSING_ENTITIES = [
    {
        "entity_id": "massing-ground",
        "schema": "MassingLevel@1",
        "fields": {"base_y": 0, "height": 4},
        "basis_refs": [EVIDENCE],
    },
    {
        "entity_id": "massing-upper",
        "schema": "MassingLevel@1",
        "fields": {"base_y": 4, "height": 4},
        "basis_refs": [EVIDENCE],
    },
    {
        "entity_id": "volume-main",
        "schema": "Volume@1",
        "fields": {
            "min": [0, 0, 0],
            "max": [5, 7, 3],
            "level_ids": ["massing-ground", "massing-upper"],
        },
        "basis_refs": [EVIDENCE],
    },
    {
        "entity_id": "zone-main",
        "schema": "Space@1",
        "fields": {
            "program_node_refs": ["program-node:main"],
            "level_ids": ["massing-ground", "massing-upper"],
            "volume_ids": ["volume-main"],
        },
        "basis_refs": [EVIDENCE],
    },
]


def _massing_payload(*, levels: int = 2) -> dict:
    """The authored record with a massing; ``levels=1`` leaves only the ground."""

    entities = [
        {**entity, "fields": {**entity["fields"], "volume_ids": ["volume-main"]}}
        if entity["entity_id"] == "portico"
        else entity
        for entity in RECORD_PAYLOAD["entities"]  # type: ignore[union-attr]
    ]
    massing = [dict(entity) for entity in MASSING_ENTITIES]
    if levels == 1:
        massing = [
            entity
            for entity in massing
            if entity["entity_id"] != "massing-upper"
        ]
        for entity in massing:
            fields = dict(entity["fields"])
            if "level_ids" in fields:
                fields["level_ids"] = ["massing-ground"]
            if entity["entity_id"] == "volume-main":
                fields["max"] = [5, 3, 3]
            entity["fields"] = fields
    return {
        **RECORD_PAYLOAD,
        "entities": entities + massing,
        "option": {
            "option_id": "option-massing",
            "label": "the demo massing",
            "typology": "villa",
            "rationale": "fixture",
            "footprint_cells": [[0, 0], [1, 0]],
            "assumption_refs": [],
        },
    }


class OptionsTestCase(unittest.TestCase):
    """One project whose record declares a massing, and a client for it."""

    LEVELS = 2

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.payload = _massing_payload(levels=self.LEVELS)
        write_runner_record(self.repository, self.payload)
        retain_runner_receipt(
            self.repository,
            self.repository.load_run(REFERENCE_RUN_ID),
            design_state_digest=runner_state_digest(
                self.repository, REFERENCE_RUN_ID, self.payload
            ),
        )
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID, self.payload
        )

    # ---- the two steps an option takes

    def option(self, transform: str, **body: object) -> dict:
        body.setdefault("stateDigest", self.state_digest)
        body["transform"] = transform
        response = self.client.post("/api/options", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def refused(self, transform: str, **body: object) -> dict:
        body.setdefault("stateDigest", self.state_digest)
        body["transform"] = transform
        response = self.client.post("/api/options", json=body)
        self.assertEqual(response.status_code, 422, response.text)
        return response.json()

    def finished(self, job_id: str) -> dict:
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            if payload["status"] in TERMINAL:
                return payload
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} never finished")

    def kinds_of(self, run_id: str) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for ref in self.repository.list_json(
            run=self.repository.load_run(run_id),
            destination=run_records(run_id),
        ):
            kind = record_kind(ref)
            if kind is not None:
                kinds[kind] = kinds.get(kind, 0) + 1
        return kinds


class VolumeReadingTests(OptionsTestCase):
    def test_the_record_volumes_are_served_with_their_own_plan_area(self) -> None:
        """0..5 on x is 6 cells and 0..3 on z is 4: 24 m2 under one volume."""

        response = self.client.get("/api/state/volumes")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([v["volumeId"] for v in body["volumes"]], ["volume-main"])
        self.assertEqual(body["volumes"][0]["footprintM2"], 24.0)
        self.assertEqual(body["volumes"][0]["min"], [0.0, 0.0, 0.0])
        self.assertEqual(
            body["volumes"][0]["levelIds"], ["massing-ground", "massing-upper"]
        )
        self.assertEqual(body["metrics"]["footprintM2"], 24.0)
        self.assertEqual(body["metrics"]["grossFloorAreaM2"], 48.0)
        self.assertEqual(body["metrics"]["floorCount"], 2)
        self.assertEqual(body["metrics"]["heightM"], 8.0)
        self.assertEqual(body["metrics"]["honesty"], [])


class MakingOptionsTests(OptionsTestCase):
    def test_a_restarted_process_does_not_reuse_an_existing_option_run(self) -> None:
        first = self.option("add_floor")
        self.client.close()
        restarted = create_app(
            StudioSettings(project_dir=self.app.state.settings.project_dir)
        )
        with TestClient(restarted) as client:
            response = client.post(
                "/api/options",
                json={"stateDigest": self.state_digest, "transform": "remove_floor"},
            )

        self.assertEqual(response.status_code, 201, response.text)
        second = response.json()
        self.assertEqual(first["optionId"], "option-001")
        self.assertEqual(second["optionId"], "option-002")
        self.assertEqual(second["runId"], "option-002")
        self.assertEqual(self.kinds_of("option-001"), {"selected-spatial-option": 1})
        self.assertEqual(self.kinds_of("option-002"), {"selected-spatial-option": 1})

    def test_a_floor_added_is_a_floor_and_a_footprint_more(self) -> None:
        """3 levels of the same 24 m2 plate: 72 m2 of floor, 12 m tall."""

        option = self.option("add_floor")

        self.assertEqual(option["optionId"], "option-001")
        self.assertEqual(option["runId"], "option-001")
        self.assertEqual(option["transform"], "add_floor")
        self.assertEqual(option["metrics"]["floorCount"], 3)
        self.assertEqual(option["metrics"]["grossFloorAreaM2"], 72.0)
        self.assertEqual(option["metrics"]["footprintM2"], 24.0)
        self.assertEqual(option["metrics"]["heightM"], 12.0)
        self.assertEqual(option["stateDigest"], self.state_digest)

    def test_an_option_is_retained_as_the_kernels_own_spatial_option(self) -> None:
        option = self.option("add_floor")

        self.assertEqual(
            self.kinds_of(option["runId"]), {"selected-spatial-option": 1}
        )
        retained = self.repository.load_json(
            next(
                ref
                for ref in self.repository.list_json(
                    run=self.repository.load_run(option["runId"]),
                    destination=run_records(option["runId"]),
                )
            )
        )
        self.assertEqual(retained["schema"], "SpatialOptionProposal@2")
        self.assertEqual(retained["option_id"], "option-001")
        self.assertEqual(len(retained["levels"]), 3)
        self.assertEqual(option["recordRef"].startswith("project://"), True)

    def test_a_shifted_volume_keeps_its_floor_area(self) -> None:
        """Moving a plate does not change how much plate there is."""

        option = self.option("shift_volume", volumeId="volume-main", dx=3, dz=-2)

        self.assertEqual(option["metrics"]["grossFloorAreaM2"], 48.0)
        self.assertEqual(option["metrics"]["footprintM2"], 24.0)
        self.assertEqual(option["metrics"]["floorCount"], 2)

    def test_scaling_a_volume_in_plan_scales_the_area(self) -> None:
        """6 cells on x doubled is 12: 12 x 4 = 48 m2 of ground, 96 of floor."""

        option = self.option(
            "scale_volume", volumeId="volume-main", sx=2.0, sz=1.0
        )

        self.assertEqual(option["metrics"]["footprintM2"], 48.0)
        self.assertEqual(option["metrics"]["grossFloorAreaM2"], 96.0)

    def test_a_split_keeps_the_ground_and_makes_a_second_volume(self) -> None:
        """3 + 3 cells on x, still 4 on z: the same 24 m2, in two volumes."""

        option = self.option(
            "split_volume", volumeId="volume-main", along="x", at=3
        )

        self.assertEqual(option["metrics"]["footprintM2"], 24.0)
        self.assertEqual(option["metrics"]["grossFloorAreaM2"], 48.0)
        retained = self.repository.load_json(
            next(
                ref
                for ref in self.repository.list_json(
                    run=self.repository.load_run(option["runId"]),
                    destination=run_records(option["runId"]),
                )
            )
        )
        self.assertEqual(
            sorted(v["volume_id"] for v in retained["volumes"]),
            ["volume-main", "volume-main-far"],
        )

    def test_the_table_holds_the_baseline_and_every_option_beside_it(self) -> None:
        empty = self.client.get("/api/options").json()
        self.assertEqual(empty["baseline"]["grossFloorAreaM2"], 48.0)
        self.assertEqual(empty["options"], [])

        first, second = self.option("add_floor"), self.option("remove_floor")
        table = self.client.get("/api/options").json()

        self.assertEqual(
            [o["optionId"] for o in table["options"]],
            [first["optionId"], second["optionId"]],
        )
        self.assertEqual(
            [o["metrics"]["grossFloorAreaM2"] for o in table["options"]],
            [72.0, 24.0],
        )
        # The baseline is the record's own massing, unmoved by any option.
        self.assertEqual(table["baseline"]["grossFloorAreaM2"], 48.0)
        self.assertEqual(
            sorted(table["transforms"]),
            sorted(["add_floor", "remove_floor", "shift_volume", "scale_volume", "split_volume", "pack"]),
        )

    def test_a_pack_the_client_sends_is_read_by_the_kernel(self) -> None:
        """The socket a generative agent plugs into: a whole pack, validated.

        The pack sent here is the record's own, with one volume two cells
        wider on z — 6 x 6 = 36 m2 of ground, 72 of floor — so the assertion
        is that the studio ran the client's geometry and not its own.
        """

        base = self.client.get("/api/state/volumes").json()
        sent = self.option(
            "pack",
            pack=self._pack_payload(max_z=5),
            label="a pack from outside",
        )

        self.assertEqual(base["metrics"]["footprintM2"], 24.0)
        self.assertEqual(sent["metrics"]["footprintM2"], 36.0)
        self.assertEqual(sent["metrics"]["grossFloorAreaM2"], 72.0)
        self.assertEqual(sent["label"], "a pack from outside")
        self.assertTrue(
            any("sent by the client" in line for line in sent["honesty"])
        )

    def _pack_payload(self, *, max_z: int) -> dict:
        """The record's own pack, widened on z: a client-shaped SchematicPack@1."""

        from archflow.state.state_record import StateRecord, schematic_pack_of

        pack = schematic_pack_of(StateRecord.from_dict(self.payload))
        assert pack is not None
        payload = pack.to_dict()
        for volume in payload["volumes"]:               # type: ignore[union-attr]
            volume["max"] = [volume["max"][0], volume["max"][1], max_z]
        return payload

    def test_efficiency_is_the_program_share_of_the_floor_area(self) -> None:
        """12 + 12 = 24 m2 of program against 48 m2 of floor: 0.5."""

        option = self.option(
            "shift_volume",
            volumeId="volume-main",
            dx=0,
            dz=0,
            programTargets={"hall": 12.0, "store": 12.0},
        )

        self.assertEqual(option["metrics"]["efficiency"], 0.5)

    def test_no_program_target_is_no_efficiency(self) -> None:
        self.assertIsNone(self.option("add_floor")["metrics"]["efficiency"])

    def test_the_envelope_findings_travel_with_the_option(self) -> None:
        """72 m2 of floor on 100 m2 of site at 0.5 allows 50, and 12 m over 9."""

        option = self.option(
            "add_floor",
            envelope={"maxHeightM": 9.0, "far": 0.5, "siteAreaM2": 100.0},
        )

        codes = [f["code"] for f in option["envelopeFindings"]]
        self.assertEqual(codes, ["height_exceeded", "far_exceeded"])
        self.assertEqual(option["envelopeFindings"][0]["measured"], 12.0)
        self.assertEqual(option["envelopeFindings"][0]["limit"], 9.0)
        self.assertEqual(option["envelopeFindings"][1]["limit"], 50.0)

    def test_an_option_says_where_it_lives(self) -> None:
        option = self.option("add_floor")

        self.assertIn("in-memory", option["persistence"])
        self.assertIn("retained in its own run", option["persistence"])


class RefusalTests(OptionsTestCase):
    def test_a_volume_the_massing_does_not_carry_is_named(self) -> None:
        body = self.refused("shift_volume", volumeId="nowhere", dx=1, dz=0)

        self.assertEqual(body["code"], "UNKNOWN_VOLUME")
        self.assertIn("volume-main", body["detail"])

    def test_a_shift_of_half_a_cell_is_refused(self) -> None:
        response = self.client.post(
            "/api/options",
            json={
                "stateDigest": self.state_digest,
                "transform": "shift_volume",
                "volumeId": "volume-main",
                "dx": 1,
                "dz": 0,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = self.refused("scale_volume", volumeId="volume-main", sx=0.0, sz=1.0)
        self.assertEqual(body["code"], "POSITIVE_FACTOR")

    def test_a_split_outside_the_volume_says_where_the_cut_could_go(self) -> None:
        body = self.refused(
            "split_volume", volumeId="volume-main", along="x", at=9
        )

        self.assertEqual(body["code"], "SPLIT_OUTSIDE_VOLUME")
        self.assertIn("1..5", body["detail"])

    def test_a_transform_outside_the_vocabulary_never_reaches_the_kernel(self) -> None:
        response = self.client.post(
            "/api/options",
            json={"stateDigest": self.state_digest, "transform": "rotate"},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "REQUEST_INVALID")

    def test_a_pack_that_is_not_one_is_refused_by_name(self) -> None:
        body = self.refused("pack", pack={"schema": "Nonsense@1"})

        self.assertEqual(body["code"], "PACK_INVALID")

    def test_an_option_against_another_state_is_stale(self) -> None:
        response = self.client.post(
            "/api/options",
            json={"stateDigest": "0" * 64, "transform": "add_floor"},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "STALE_BASE")

    def test_an_option_this_process_never_made_is_named(self) -> None:
        response = self.client.post("/api/options/option-404/select")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "OPTION_NOT_FOUND")


class OneFloorTests(OptionsTestCase):
    LEVELS = 1

    def test_the_last_floor_is_never_removed(self) -> None:
        body = self.refused("remove_floor")

        self.assertEqual(body["code"], "LAST_FLOOR")

    def test_one_floor_measures_one_floor(self) -> None:
        """One 6x4 level, 4 cells tall: 24 m2 of floor, 4 m."""

        body = self.client.get("/api/state/volumes").json()

        self.assertEqual(body["metrics"]["grossFloorAreaM2"], 24.0)
        self.assertEqual(body["metrics"]["heightM"], 4.0)


class RemoveFloorTests(OptionsTestCase):
    def test_removing_a_floor_takes_its_area_and_its_height(self) -> None:
        """Two levels down to one: 24 m2 of floor, 4 m tall."""

        option = self.option("remove_floor")

        self.assertEqual(option["metrics"]["floorCount"], 1)
        self.assertEqual(option["metrics"]["grossFloorAreaM2"], 24.0)
        self.assertEqual(option["metrics"]["heightM"], 4.0)
        self.assertEqual(option["metrics"]["footprintM2"], 24.0)


class SelectionTests(OptionsTestCase):
    def _change_element_without_changing_massing_state(self) -> dict:
        path = self.repository.layout.resolve_relative(RUNNER_RECORD_PATH)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entity in payload["entities"]:
            if entity["entity_id"] == "portico-cornice":
                entity["fields"]["params"]["height"] = 0.45
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_proposal_refuses_record_drift_hidden_by_the_state_digest(self) -> None:
        proposal_response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
                "elementId": "portico-base",
                "utterance": "set height to 2.2",
            },
        )
        self.assertEqual(proposal_response.status_code, 201, proposal_response.text)
        proposal = proposal_response.json()
        runs_before = {path.name for path in self.repository.layout.runs.iterdir()}

        current = self._change_element_without_changing_massing_state()
        self.assertEqual(current["stateDigest"], proposal["baseStateDigest"])
        self.assertNotEqual(current["recordDigest"], proposal["recordDigest"])

        response = self.client.post(
            f"/api/proposals/{proposal['proposalId']}/candidate"
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "STALE_BASE")
        self.assertEqual(
            {path.name for path in self.repository.layout.runs.iterdir()},
            runs_before,
        )

    def test_an_option_refuses_record_drift_hidden_by_the_state_digest(self) -> None:
        option = self.option("add_floor")
        runs_before = {path.name for path in self.repository.layout.runs.iterdir()}

        current = self._change_element_without_changing_massing_state()
        self.assertEqual(current["stateDigest"], option["stateDigest"])

        response = self.client.post(f"/api/options/{option['optionId']}/select")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "STALE_BASE")
        self.assertEqual(
            {path.name for path in self.repository.layout.runs.iterdir()},
            runs_before,
        )

    def test_selecting_an_option_runs_it_as_a_candidate(self) -> None:
        option = self.option("add_floor")

        response = self.client.post(f"/api/options/{option['optionId']}/select")

        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.assertTrue(accepted["jobId"])
        self.assertTrue(accepted["candidateId"].startswith("studio-opt-"))
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        kinds = self.kinds_of(accepted["candidateId"])
        # The runner retains the option it executed; the studio writes no
        # second statement of the same fact.
        self.assertEqual(kinds.get("selected-spatial-option"), 1)
        self.assertEqual(kinds.get("state-record"), 1)
        self.assertEqual(kinds.get("runner-run-receipt"), 1)

    def test_the_selected_massing_is_the_one_the_run_executed(self) -> None:
        option = self.option("add_floor")

        accepted = self.client.post(
            f"/api/options/{option['optionId']}/select"
        ).json()
        self.assertEqual(self.finished(accepted["jobId"])["status"], "succeeded")

        retained = None
        for ref in self.repository.list_json(
            run=self.repository.load_run(accepted["candidateId"]),
            destination=run_records(accepted["candidateId"]),
        ):
            if record_kind(ref) == "selected-spatial-option":
                retained = self.repository.load_json(ref)
        assert retained is not None
        self.assertEqual(len(retained["levels"]), 3)
        self.assertEqual(retained["option_id"], "option-001")

    def test_the_candidate_reads_back_without_a_proposal(self) -> None:
        option = self.option("add_floor")
        accepted = self.client.post(
            f"/api/options/{option['optionId']}/select"
        ).json()
        self.assertEqual(self.finished(accepted["jobId"])["status"], "succeeded")

        response = self.client.get(f"/api/candidates/{accepted['candidateId']}")

        self.assertEqual(response.status_code, 200, response.text)
        candidate = response.json()
        self.assertEqual(candidate["proposalId"], option["optionId"])
        self.assertTrue(
            any(
                "not made from a proposal" in line
                for line in candidate["honesty"]
            )
        )

    def test_the_authored_record_is_byte_identical_afterwards(self) -> None:
        authored = self.repository.layout.resolve_relative(RUNNER_RECORD_PATH)
        before = authored.read_bytes()
        head_before = self.repository.read_head()

        option = self.option("add_floor")
        accepted = self.client.post(
            f"/api/options/{option['optionId']}/select"
        ).json()
        self.assertEqual(self.finished(accepted["jobId"])["status"], "succeeded")

        self.assertEqual(authored.read_bytes(), before)
        self.assertEqual(self.repository.read_head(), head_before)


if __name__ == "__main__":
    unittest.main()
