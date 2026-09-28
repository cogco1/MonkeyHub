"""The hosted-opening capability (#419 Stage C, spec §3.5): doors and windows come with meaning.

An agent never chooses how a block is realised. It gives a block the meaning
``architectural.role = wall`` (``POST /api/proposals/facets``) and then asks for a
door or a window (``POST /api/proposals/hosted-opening``); the runtime realises the
block as a wall in place, under the same identity and the same delivered object,
and hosts the opening in it. Without that meaning the route answers which facet
to add; a block that cannot be a wall as drawn is refused with the reason. An
opening serves the connection between spaces it names, and none when it names none.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeyarch.construction.vocabulary import layer_rule_violations

from .support import EVIDENCE, PROJECT_ID, RECORD_PAYLOAD, make_empty_project, write_runner_record
from .test_construction_routes import ConstructionTestCase, _exported, _texts

BLOCK = "block = extrude(rect(0, 0, 6, 0.3), 3)"
ON_TOP = "shelf = extrude(rect(4, 0, 1.5, 0.3), 0.2, at=top(block))"
DOOR = {"frame_width": 0.06, "frame_depth": 0.12, "frame_projection": 0.0, "leaf_thickness": 0.04,
        "leaf_offset": 0.04, "leaf_count": 1, "leaf_gap": 0.0, "clearance_bottom": 0.01, "clearance_top": 0.005}
WINDOW = {"frame_width": 0.06, "frame_depth": 0.1, "frame_projection": 0.02, "glazing_thickness": 0.024,
          "glazing_offset": 0.04}
ENRICHMENT = ("block needs architectural.role = wall before it can host a door or window; "
              "add it with POST /api/proposals/facets")
# rect() winds counter-clockwise, so the block's line lies on its face at z = 0.3 and along runs from x = 0.
ALONG_LINE = {"start": [0, 0.3], "end": [6, 0.3]}
LINE = {"from": {"point": [0, 0.3]}, "to": {"point": [6, 0.3]}, "inward": [0, -1]}
INTERFACE = "relation:inside-to-outside"


class HostedOpeningTestCase(ConstructionTestCase):
    """A block made by a script, then a door or a window asked for on it."""

    def open(self, host: str, *, expect: int = 201, **body: object) -> dict:
        run = body.get("sourceRunId")
        payload = {"stateDigest": self.digest(run if isinstance(run, str) else None), "host": host, "kind": "door",
                   "along": 4.5, "width": 0.9, "sill": 0, "head": 2.1, **body}
        response = self.client.post("/api/proposals/hosted-opening", json=payload)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def walled(self, script: str = BLOCK, role: str = "wall") -> dict:
        """The script's shapes with ``block`` given ``architectural.role``, as one unexecuted chain."""

        made = self.construct(script)
        return self.facets([{"id": "block", "set": {"architectural.role": role}}],
                           stateDigest=made["baseStateDigest"], sourceProposalId=made["proposalId"])

    def after(self, proposal: dict, **body: object) -> dict:
        """What continues ``proposal``: its base state and its id."""

        return {"stateDigest": proposal["baseStateDigest"], "sourceProposalId": proposal["proposalId"], **body}

    def objects(self, run: str) -> set[str]:
        candidate = self.client.get(f"/api/candidates/{run}").json()
        self.assertIsNone(candidate["objectReadbackError"], candidate)
        return {row["name"] for row in candidate["objects"]}


def delivered_assemblies(case: unittest.TestCase, client: TestClient, repository, run: str) -> list[dict]:
    """The hosted assemblies of the program a candidate's one seat delivered, read from the run's own record."""

    [seat] = client.get(f"/api/candidates/{run}").json()["seatResults"]
    case.assertEqual(seat["status"], "proposal_accepted", seat)
    program = repository.load_json(record_ref_from_uri(seat["programRef"], PROJECT_ID))
    return list(program["proposal"]["assemblies"])


class EnrichmentRequiredTestCase(HostedOpeningTestCase):
    def test_without_the_wall_meaning_the_route_names_the_facet_to_add(self) -> None:
        made = self.construct(BLOCK)
        held = self.client.app.state.proposals.for_state(made["baseStateDigest"])
        body = self.open("block", expect=409, **self.after(made))
        self.assertEqual(body, {"code": "ENRICHMENT_REQUIRED", "detail": ENRICHMENT, "message": ENRICHMENT,
                                "entity": "block", "facet": "architectural.role", "value": "wall"})
        self.assertEqual(self.client.app.state.proposals.for_state(made["baseStateDigest"]), held,
                         "a refusal keeps nothing")
        other = self.walled(role="column")
        self.assertEqual(self.open("block", expect=409, **self.after(other))["code"], "ENRICHMENT_REQUIRED")


class HostedDoorTestCase(HostedOpeningTestCase):
    cad_export = "occt"

    def test_a_door_with_its_family_realises_the_block_as_a_wall_in_place(self) -> None:
        first = self.run_candidate(self.walled(BLOCK + "\n" + ON_TOP)["proposalId"])
        before = self.objects(first)
        self.assertIn("obj-block-body", before)
        rows = {row["id"]: row for row in self.model(first)["entities"]}
        self.assertEqual(rows["block"]["alongLine"], ALONG_LINE)
        self.assertEqual(rows["block"]["openings"], [])
        self.assertIsNone(rows["shelf"]["alongLine"])

        proposal = self.open("block", sourceRunId=first, along=1.5, family=DOOR)
        self.assertEqual(proposal["utterance"], "hosted opening: door opening-1 in block")
        [row] = proposal["change"]["edits"]["entities"]
        self.assertEqual((row["entity_id"], row["schema"], row["parent_id"]), ("block-body", "Element@1", "block"))
        fields = row["fields"]
        self.assertEqual(fields["producer"], "wall")
        self.assertEqual(fields["references"], {"base": {"level": "level-ground"}, "line": LINE})
        self.assertEqual(fields["params"], {
            "height": 3, "thickness": 0.3,
            "openings": [{"opening_id": "opening-1", "kind": "door", "along": 1.5, "width": 0.9, "sill": 0,
                          "head": 2.1, "type_id": "opening-1-type"}],
            "types": [{"schema": "DoorType@1", "type_id": "opening-1-type", **DOOR}],
        })

        second = self.run_candidate(proposal["proposalId"])
        after = self.objects(second)
        # The block is delivered under the object it always had, now with the door in it.
        self.assertIn("obj-block-body", after)
        self.assertLessEqual({"obj-door-frame-block-body-opening-1-left", "obj-door-frame-block-body-opening-1-right",
                              "obj-door-frame-block-body-opening-1-top", "obj-door-leaf-block-body-opening-1-0"}, after)
        self.assertLessEqual(before, after)
        self.assertEqual(after - before, {name for name in after if "opening-1" in name})
        exported_before, exported_after = _exported(self.project, first), _exported(self.project, second)
        self.assertEqual(exported_after["obj-shelf-body"], exported_before["obj-shelf-body"],
                         "what stood on the block still stands where it stood")
        self.assertEqual(exported_after["obj-block-body"], exported_before["obj-block-body"])
        # The door sits where along says: centred 1.5 from the line's start (0, 0.3), toward its end.
        leaf = exported_after["obj-door-leaf-block-body-opening-1-0"]
        self.assertAlmostEqual((leaf[0][0] + leaf[1][0]) / 2, 1.5, places=6)
        # It names no connection between spaces, so its assembly cites none.
        [assembly] = delivered_assemblies(self, self.client, self.repository, second)
        self.assertEqual((assembly["assembly_id"], assembly["interface_refs"]), ("block-body-opening-1-assembly", []))

        rows = {row["id"]: row for row in self.model(second)["entities"]}
        self.assertEqual(rows["block"]["openings"],
                         [{"id": "opening-1", "kind": "door", "along": 1.5, "width": 0.9, "sill": 0, "head": 2.1}])
        self.assertEqual(rows["block"]["capabilities"],
                         [{"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}])
        self.assertEqual((rows["block"]["form"], rows["block"]["facets"]), ("solid", {"architectural.role": "wall"}))
        self.assertEqual(rows["block"]["alongLine"], ALONG_LINE)
        self.assertEqual(rows["block"]["bounds"], [[0, 0, 0], [6, 3, 0.3]], "a host with a door keeps its bounds")

        # What stands on the block keeps its drawing controls: the block's top is published as it was.
        state = self.client.get(f"/api/state?run={second}").json()
        shelf = next(element for element in state["elements"] if element["elementId"] == "shelf-body")
        self.assertIsNone(shelf["drawnShapeReason"], shelf)
        self.assertIsNotNone(shelf["drawnShape"], shelf)
        self.assertEqual(shelf["elevation"]["baseReference"], {"kind": "element-top", "id": "block-body", "offset": 0})
        self.assertAlmostEqual(shelf["elevation"]["base"], 3.0)


class HostedOpeningChainTestCase(HostedOpeningTestCase):
    def test_a_second_opening_on_the_same_host_appends(self) -> None:
        chain = self.walled()
        door = self.open("block", family=DOOR, **self.after(chain))
        window = self.open("block", kind="window", along=1.5, width=1.2, sill=0.9, head=2.1, family=WINDOW,
                           summary="a window beside the door", **self.after(door))
        self.assertEqual(window["utterance"], "a window beside the door")
        self.assertEqual(window["baseStateDigest"], chain["baseStateDigest"])
        [row] = [row for row in window["change"]["edits"]["entities"] if row["entity_id"] == "block-body"]
        params = row["fields"]["params"]
        self.assertEqual([(opening["opening_id"], opening["kind"], opening.get("type_id")) for opening in params["openings"]],
                         [("opening-1", "door", "opening-1-type"), ("opening-2", "window", "opening-2-type")])
        self.assertEqual(params["types"][1], {"schema": "WindowType@1", "type_id": "opening-2-type", **WINDOW})
        # An opening with no family is an empty passage; ids skip what is taken.
        passage = self.open("block", along=3.0, width=0.8, head=2.0, **self.after(window))
        [row] = [row for row in passage["change"]["edits"]["entities"] if row["entity_id"] == "block-body"]
        [third] = [opening for opening in row["fields"]["params"]["openings"] if opening["opening_id"] == "opening-3"]
        self.assertNotIn("type_id", third)
        self.assertEqual(len(row["fields"]["params"]["types"]), 2)

    def test_the_next_id_skips_opening_and_type_ids_already_in_use(self) -> None:
        chain = self.walled()
        # A host whose one opening is opening-2 and which keeps a type called opening-3-type.
        authored = self.client.post("/api/proposals", json=self.after(chain, semanticEdit={
            "summary": "a host whose ids are out of order", "entities": [{
                "entity_id": "block-body", "schema": "Element@1", "parent_id": "block", "fields": {
                    "component_id": "block", "producer": "wall",
                    "references": {"base": {"level": "level-ground"}, "line": LINE},
                    "params": {"height": 3, "thickness": 0.3,
                               "openings": [{"opening_id": "opening-2", "kind": "window", "along": 3, "width": 1,
                                             "sill": 0.9, "head": 2}],
                               "types": [{"schema": "DoorType@1", "type_id": "opening-3-type", **DOOR}]}}}]}))
        self.assertEqual(authored.status_code, 201, authored.text)
        door = self.open("block", along=1, family=DOOR, **self.after(authored.json()))
        [row] = [row for row in door["change"]["edits"]["entities"] if row["entity_id"] == "block-body"]
        self.assertEqual([opening["opening_id"] for opening in row["fields"]["params"]["openings"]],
                         ["opening-2", "opening-4"])
        self.assertEqual([item["type_id"] for item in row["fields"]["params"]["types"]],
                         ["opening-3-type", "opening-4-type"])
        self.assertEqual(door["utterance"], "hosted opening: door opening-4 in block")

    def test_keeping_the_host_keeps_its_geometry(self) -> None:
        # #419 C7 round 2: keep entity:<geometry id> protects its parts on this route too.
        chain = self.walled()
        body = self.open("block", expect=409, keep=["entity:block"], **self.after(chain))
        self.assertEqual(body["code"], "PROPOSAL_CHAIN_CONFLICT")
        self.assertIn("entity:block-body", body["detail"])

    def test_an_arched_passage_takes_its_spring_height(self) -> None:
        chain = self.walled()
        arched = self.open("block", along=3.0, width=1.2, head=2.4, shape="semicircular_arch", springHeight=1.8,
                           **self.after(chain))
        [row] = [row for row in arched["change"]["edits"]["entities"] if row["entity_id"] == "block-body"]
        self.assertEqual(row["fields"]["params"]["openings"], [{
            "opening_id": "opening-1", "kind": "door", "along": 3.0, "width": 1.2, "sill": 0, "head": 2.4,
            "shape": "semicircular_arch", "spring_height": 1.8}])


class HostedOpeningRefusalTestCase(HostedOpeningTestCase):
    def test_a_block_that_cannot_be_a_wall_as_drawn_is_refused_with_the_reason(self) -> None:
        for script, reason in (
            ("block = extrude(polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]), 3)",
             "the block's footprint is not a rectangle"),
            ("block = extrude(rect(0, 0, 6, 0.3), 3, at=0.5)",
             "the block is lifted off its base; place it on a level or another solid's top first"),
        ):
            with self.subTest(reason=reason):
                chain = self.walled(script)
                body = self.open("block", expect=422, **self.after(chain))
                self.assertEqual(body["code"], "HOST_NOT_WALL_SHAPED", body)
                self.assertEqual(body["detail"], f"block cannot take a door or window as it is drawn: {reason}")

    def test_what_is_not_a_host_is_refused_by_name(self) -> None:
        chain = self.walled(BLOCK + "\ncutter = extrude(rect(1, -0.1, 1, 0.5), 1)\ncut(block, cutter)")
        cutter = self.facets([{"id": "cutter", "set": {"architectural.role": "wall"}}], **self.after(chain))
        for host, status, code, said in (
            ("nothing-here", 404, "ENTITY_UNKNOWN", "nothing-here"),
            ("level-ground", 422, "HOST_INVALID", "Level@1"),
            ("block-body", 422, "HOST_INVALID", "block"),
            ("portico", 422, "HOST_INVALID", "portico-base"),
            ("cutter", 422, "HOST_INVALID", "cuts block"),
        ):
            with self.subTest(host=host):
                body = self.open(host, expect=status, **self.after(cutter))
                self.assertEqual(body["code"], code, body)
                self.assertIn(said, body["detail"])

    def test_a_family_an_interface_or_an_opening_the_host_cannot_take_is_refused(self) -> None:
        chain = self.walled()
        for body, code, said in (
            ({"family": {**DOOR, "glazing_thickness": 0.02}}, "FAMILY_INVALID", "glazing_thickness"),
            ({"family": {key: value for key, value in DOOR.items() if key != "leaf_gap"}}, "FAMILY_INVALID", "leaf_gap"),
            ({"family": {**DOOR, "leaf_count": 3}}, "FAMILY_INVALID", "leaf_count"),
            ({"kind": "window", "family": DOOR}, "FAMILY_INVALID", "glazing_thickness"),
            ({"interfaceRef": INTERFACE}, "INTERFACE_UNKNOWN",
             f"no connection of this project declares {INTERFACE}; it declares no connection between spaces yet"),
            # The record's own sentence, in construction words and about the geometry id, not its part.
            ({"width": 9.0}, "OPENING_INVALID",
             "block cannot take this door: opening opening-1 lies outside the host's length"),
            ({"head": 3.5}, "OPENING_INVALID",
             "block cannot take this door: opening opening-1 head is above the host's top"),
        ):
            with self.subTest(body=sorted(body)):
                refused = self.open("block", expect=422, **self.after(chain, **body))
                self.assertEqual(refused["code"], code, refused)
                self.assertIn(said, refused["detail"])
        stale = self.open("block", expect=409, **self.after(chain, stateDigest="0" * 64))
        self.assertEqual(stale["code"], "STALE_BASE")
        for invalid in ({"kind": "skylight"}, {"along": "near the end"}, {"width": 0}, {"producer": "wall"},
                        {"interfaceRef": ""}):
            with self.subTest(invalid=sorted(invalid)):
                self.assertEqual(self.open("block", expect=422, **self.after(chain, **invalid))["code"],
                                 "REQUEST_INVALID")


class HostedOpeningContractTestCase(HostedOpeningTestCase):
    def test_the_route_says_wall_only_as_the_facet_value(self) -> None:
        schema = self.client.get("/openapi.json").json()
        route = schema["paths"]["/api/proposals/hosted-opening"]
        texts = list(_texts(route, schema["components"]["schemas"], set()))
        self.assertTrue(texts)
        self.assertTrue(any("architectural.role = wall" in text for text in texts))
        # wall is said only as the value of the facet the route needs; every other layer-rule word is absent.
        self.assertEqual([text for text in texts if layer_rule_violations(text.replace("architectural.role = wall", ""))],
                         [])
        for field in ("host", "kind", "along", "width", "sill", "head", "family", "springHeight", "interfaceRef",
                      "stateDigest"):
            self.assertIn(field, texts)


# The demo record, with what a door's interface is a fact about: one block of massing, two
# zones in it and the connection between them, whose relationship ref names a declared
# relation (the same shape as the CAD export fixture's; it binds no validator).
SPACES_RECORD: dict[str, object] = {
    **{key: value for key, value in RECORD_PAYLOAD.items() if key not in ("option", "entities", "relations")},
    "option": {**RECORD_PAYLOAD["option"], "footprint_cells": [[0, 0]]},  # type: ignore[dict-item]
    "entities": [
        *[{**entity, "fields": {**entity["fields"], "volume_ids": ["massing"]}} if entity["entity_id"] == "building"
          else entity for entity in RECORD_PAYLOAD["entities"]],  # type: ignore[index, union-attr]
        {"entity_id": "massing-ground", "schema": "MassingLevel@1", "fields": {"base_y": 0, "height": 4},
         "basis_refs": [EVIDENCE]},
        {"entity_id": "massing", "schema": "Volume@1",
         "fields": {"min": [-2, 0, -1], "max": [3, 3, 5], "level_ids": ["massing-ground"]}, "basis_refs": [EVIDENCE]},
        *[{"entity_id": f"zone-{side}", "schema": "Space@1",
           "fields": {"program_node_refs": [f"program-node:{side}"], "level_ids": ["massing-ground"],
                      "volume_ids": ["massing"]}, "basis_refs": [EVIDENCE]} for side in ("inside", "outside")],
        {"entity_id": "connection-inside-to-outside", "schema": "Connection@1",
         "fields": {"source_zone_id": "zone-outside", "target_zone_id": "zone-inside", "relationship_refs": [INTERFACE],
                    "directed": False}, "basis_refs": [EVIDENCE]},
    ],
    "relations": [
        *RECORD_PAYLOAD["relations"],  # type: ignore[misc]
        {"relation_id": "inside-to-outside", "kind": "interface", "subject": "zone-outside", "object": "zone-inside",
         "propagation": "revalidate", "basis_refs": [EVIDENCE]},
    ],
}


class DeclaredInterfaceTestCase(HostedOpeningTestCase):
    """A door serves the connection it names, and only one the record declares."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository = make_empty_project(self.root)
        write_runner_record(self.repository, SPACES_RECORD)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export=self.cad_export, project_dir=self.project)))
        self.addCleanup(self.client.close)

    def test_a_door_naming_a_declared_connection_carries_it(self) -> None:
        chain = self.walled()
        refused = self.open("block", expect=422, interfaceRef="relation:elsewhere", **self.after(chain))
        self.assertEqual((refused["code"], refused["detail"]), (
            "INTERFACE_UNKNOWN",
            f"no connection of this project declares relation:elsewhere; its connections declare {INTERFACE}"))
        door = self.open("block", along=1.5, family=DOOR, interfaceRef=INTERFACE, **self.after(chain))
        [row] = [row for row in door["change"]["edits"]["entities"] if row["entity_id"] == "block-body"]
        self.assertEqual(row["fields"]["params"]["openings"][0]["interface_ref"], INTERFACE)
        started = self.client.post(f"/api/proposals/{door['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started.json()['jobId']}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded", job)
        [assembly] = delivered_assemblies(self, self.client, self.repository, job["candidateId"])
        self.assertEqual(assembly["interface_refs"], [INTERFACE])


# The demo record with one wall row the record accepts but no producer can read: its inward has one component.
MALFORMED_WALL_RECORD: dict[str, object] = {
    **{key: value for key, value in RECORD_PAYLOAD.items() if key != "entities"},
    "entities": [
        *RECORD_PAYLOAD["entities"],  # type: ignore[misc]
        {"entity_id": "screen-body", "schema": "Element@1", "parent_id": "portico", "fields": {
            "component_id": "portico", "producer": "wall",
            "references": {"base": {"level": "level-ground"},
                           "line": {"from": {"point": [0, 3]}, "to": {"point": [4, 3]}, "inward": [1]}},
            "params": {"thickness": 0.2, "height": 2.5}}, "basis_refs": [EVIDENCE]},
    ],
}


class MalformedWallTestCase(unittest.TestCase):
    """A wall row no producer can read does not break reading the state: it just has no drawing controls."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository = make_empty_project(self.root)
        write_runner_record(self.repository, MALFORMED_WALL_RECORD)
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)))
        self.addCleanup(self.client.close)

    def test_the_state_and_the_elevation_route_still_answer(self) -> None:
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        elements = {element["elementId"]: element for element in state["elements"]}
        self.assertIsNone(elements["screen-body"]["drawnShape"])
        self.assertEqual(elements["screen-body"]["drawnShapeReason"],
                         "Direct push/pull supports drawn faces and prisms, not wall.")
        self.assertIsNotNone(elements["portico-base"]["drawnShape"], elements["portico-base"])
        for body in ({"elementId": "screen-body", "action": "set-height", "value": 1},
                     {"levelId": "level-upper", "action": "set-datum", "value": 3}):
            with self.subTest(action=body["action"]):
                refused = self.client.post("/api/proposals/elevation", json={"stateDigest": state["stateDigest"], **body})
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertEqual(refused.json()["code"], "ELEVATION_EDIT_INVALID")
