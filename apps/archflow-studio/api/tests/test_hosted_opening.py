"""The hosted-opening capability (#419 Stage C, spec §3.5): doors and windows come with meaning.

An agent never chooses how a block is realised. It gives a block the meaning
``architectural.role = wall`` (``POST /api/proposals/facets``) and then asks for a
door or a window (``POST /api/proposals/hosted-opening``); the runtime realises the
block as a wall in place, under the same identity and the same delivered object,
and hosts the opening in it. Without that meaning the route answers which facet
to add; a block that cannot be a wall as drawn is refused with the reason.
"""

from __future__ import annotations

from monkeyarch.construction.vocabulary import layer_rule_violations

from .test_construction_routes import ConstructionTestCase, _exported, _texts

BLOCK = "block = extrude(rect(0, 0, 6, 0.3), 3)"
ON_TOP = "shelf = extrude(rect(4, 0, 1.5, 0.3), 0.2, at=top(block))"
DOOR = {"frame_width": 0.06, "frame_depth": 0.12, "frame_projection": 0.0, "leaf_thickness": 0.04,
        "leaf_offset": 0.04, "leaf_count": 1, "leaf_gap": 0.0, "clearance_bottom": 0.01, "clearance_top": 0.005}
WINDOW = {"frame_width": 0.06, "frame_depth": 0.1, "frame_projection": 0.02, "glazing_thickness": 0.024,
          "glazing_offset": 0.04}
ENRICHMENT = ("block needs architectural.role = wall before it can host a door or window; "
              "add it with POST /api/proposals/facets")


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
        self.assertEqual(rows["block"]["alongLine"], {"start": [6, 0], "end": [0, 0]})
        self.assertEqual(rows["block"]["openings"], [])
        self.assertIsNone(rows["shelf"]["alongLine"])

        proposal = self.open("block", sourceRunId=first, family=DOOR)
        self.assertEqual(proposal["utterance"], "hosted opening: door opening-1 in block")
        [row] = proposal["change"]["edits"]["entities"]
        self.assertEqual((row["entity_id"], row["schema"], row["parent_id"]), ("block-body", "Element@1", "block"))
        fields = row["fields"]
        self.assertEqual(fields["producer"], "wall")
        self.assertEqual(fields["references"], {"base": {"level": "level-ground"}, "line": {
            "from": {"point": [0, 0]}, "to": {"point": [6, 0]}, "inward": [0, 1]}})
        self.assertEqual(fields["params"], {
            "height": 3, "thickness": 0.3,
            "openings": [{"opening_id": "opening-1", "kind": "door", "along": 4.5, "width": 0.9, "sill": 0,
                          "head": 2.1, "type_id": "opening-1-type"}],
            "types": [{"schema": "DoorType@1", "type_id": "opening-1-type", **DOOR}],
        })

        second = self.run_candidate(proposal["proposalId"])
        after = self.objects(second)
        # The block is delivered under the object it always had, now with the door in it.
        self.assertIn("obj-block-body", after)
        self.assertLessEqual({"obj-door-frame-block-body-opening-1-left", "obj-door-frame-block-body-opening-1-right",
                              "obj-door-frame-block-body-opening-1-top", "obj-door-leaf-block-body-opening-1-0"}, after)
        self.assertEqual(after - before, {name for name in after if "opening-1" in name})
        exported_before, exported_after = _exported(self.project, first), _exported(self.project, second)
        self.assertEqual(exported_after["obj-shelf-body"], exported_before["obj-shelf-body"],
                         "what stood on the block still stands where it stood")
        self.assertEqual(exported_after["obj-block-body"], exported_before["obj-block-body"])
        # The door sits where along says: centred 4.5 from the line's start (6, 0), toward its end.
        leaf = exported_after["obj-door-leaf-block-body-opening-1-0"]
        self.assertAlmostEqual((leaf[0][0] + leaf[1][0]) / 2, 1.5, places=6)

        rows = {row["id"]: row for row in self.model(second)["entities"]}
        self.assertEqual(rows["block"]["openings"],
                         [{"id": "opening-1", "kind": "door", "along": 4.5, "width": 0.9, "sill": 0, "head": 2.1}])
        self.assertEqual(rows["block"]["capabilities"],
                         [{"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}])
        self.assertEqual((rows["block"]["form"], rows["block"]["facets"]), ("solid", {"architectural.role": "wall"}))
        self.assertEqual(rows["block"]["alongLine"], {"start": [6, 0], "end": [0, 0]})


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

    def test_a_family_or_an_opening_the_host_cannot_take_is_refused(self) -> None:
        chain = self.walled()
        for body, code, said in (
            ({"family": {**DOOR, "glazing_thickness": 0.02}}, "FAMILY_INVALID", "glazing_thickness"),
            ({"family": {key: value for key, value in DOOR.items() if key != "leaf_gap"}}, "FAMILY_INVALID", "leaf_gap"),
            ({"family": {**DOOR, "leaf_count": 3}}, "FAMILY_INVALID", "leaf_count"),
            ({"kind": "window", "family": DOOR}, "FAMILY_INVALID", "glazing_thickness"),
            ({"width": 9.0}, "OPENING_INVALID", "outside"),
            ({"head": 3.5}, "OPENING_INVALID", "above"),
        ):
            with self.subTest(body=sorted(body)):
                refused = self.open("block", expect=422, **self.after(chain, **body))
                self.assertEqual(refused["code"], code, refused)
                self.assertIn(said, refused["detail"])
        stale = self.open("block", expect=409, **self.after(chain, stateDigest="0" * 64))
        self.assertEqual(stale["code"], "STALE_BASE")
        for invalid in ({"kind": "skylight"}, {"along": "near the end"}, {"width": 0}, {"producer": "wall"}):
            with self.subTest(invalid=sorted(invalid)):
                self.assertEqual(self.open("block", expect=422, **self.after(chain, **invalid))["code"],
                                 "REQUEST_INVALID")


class HostedOpeningContractTestCase(HostedOpeningTestCase):
    def test_the_route_says_wall_only_as_the_facet_value(self) -> None:
        schema = self.client.get("/openapi.json").json()
        route = schema["paths"]["/api/proposals/hosted-opening"]
        texts = list(_texts(route, schema["components"]["schemas"], set()))
        self.assertTrue(texts)
        self.assertEqual([text for text in texts if set(layer_rule_violations(text)) - {"wall"}], [])
        for field in ("host", "kind", "along", "width", "sill", "head", "family", "springHeight", "stateDigest"):
            self.assertIn(field, texts)
