"""Construction first, end to end (#419): one block goes from a Stage A script to a wall with a door.

The story an agent lives through, driven only by the routes it is given, each
step continuing from the candidate the previous one produced:

1. Stage A: one construction script makes ``mass`` and, elsewhere, ``block``;
   both are delivered and the model view lists them with no meaning and no
   capability (``POST /api/proposals/construction``, ``GET /api/construction/model``).
2. A second script moves ``block`` into ``mass`` as a niche and cuts it: the
   delivered mass loses exactly the niche's volume, ``block`` stays in the
   model hidden, and no id changes.
3. Deleting ``block`` while it still cuts ``mass`` is refused at the script
   line and saves nothing.
4. Stage C: the facet ``architectural.role = wall`` on ``mass`` changes no
   geometry (``POST /api/proposals/facets``) and unlocks ``hosted-opening``.
5. A door on ``mass`` (``POST /api/proposals/hosted-opening``) realises it as a
   wall in place: same delivered object, the door's frame and leaf added, the
   niche still cut.
6. Stage D: the structure domain asks for the facets it needs
   (``GET /api/domains/structure/readiness``) and reads ``mass`` once it has
   them; it never asks what the hidden cutter is.
7. ``uncut`` on the wall-realised ``mass`` shows ``block`` again, gives the
   niche's volume back, and makes ``block`` something the domain asks about.

Every volume and visibility is read by OCCT from the exact STEP the candidate
delivered; the 3dm preview shows the same objects.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from monkeycad import occt_backend
from monkeycad.three_dm_inspector import inspect_three_dm_index
from archflow.contracts.canonical import canonical_json_bytes
from archflow.state.state_record import StateRecord, component_facets
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from monkeyarch.authoring.construction.vocabulary import layer_rule_violations

from .support import PROJECT_ID, RECORD_PAYLOAD, make_empty_project, write_runner_record
from .test_construction_routes import _exported
from .test_hosted_opening import DOOR, HostedOpeningTestCase

# The demo project with none of its own geometry: its components, level, grid and
# parameters, no Element@1 (and so no relation between elements). The only geometry
# a domain can ask about is then what the scripts make; new geometry still goes
# under ``portico``, the component the project's one seat builds.
RECORD: dict[str, object] = {
    **{key: value for key, value in RECORD_PAYLOAD.items() if key != "relations"},
    "entities": [entity for entity in RECORD_PAYLOAD["entities"]  # type: ignore[union-attr]
                 if entity["schema"] != "Element@1"],
    "relations": [],
}

STAGE_A = "\n".join([
    "mass = extrude(rect(0, 0, 4, 0.3), 3)",
    "block = extrude(rect(6, 0, 1, 0.2), 1.2)",
])
# block moves to x 1.5..2.5, y 0.9..2.1, z 0.1..0.3: a recess 0.2 deep into the 0.3 of
# mass, open to its face at z = 0.3 and not through.
NICHE = "\n".join([
    'block = get("block")',
    "move(block, dx=-4.5, dy=0.9, dz=0.1)",
    'cut(get("mass"), block)',
])
DELETE = 'delete(get("block"))'
UNCUT = 'uncut(get("mass"), get("block"))'

MASS_VOLUME = 4 * 0.3 * 3
NICHE_VOLUME = 1 * 0.2 * 1.2
ALONG, WIDTH, SILL, HEAD = 3.3, 0.9, 0.0, 2.1
DOOR_HOLE = WIDTH * (HEAD - SILL) * 0.3
FRAME_AND_LEAF = {"obj-door-frame-mass-body-opening-1-left", "obj-door-frame-mass-body-opening-1-right",
                  "obj-door-frame-mass-body-opening-1-top", "obj-door-leaf-mass-body-opening-1-0"}
APERTURE = "obj-mass-body-aperture-opening-1"  # the door hole's inspection witness, kept hidden like a cutter
HOSTED_OPENING = [{"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}]


def _identities(record: StateRecord) -> list[tuple[str, str, str | None]]:
    """Every component and element of a record: schema, id and parent."""

    return sorted((entity.schema, entity.entity_id, entity.parent_id) for entity in record.entities
                  if entity.schema in ("Component@1", "Element@1"))


def _element_rows(record: StateRecord) -> list[bytes]:
    """Every Element@1 row of a record, serialized canonically, in record order."""

    return [canonical_json_bytes(entity.to_dict()) for entity in record.entities_of("Element@1")]


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class ConstructionFirstAuthoringTestCase(HostedOpeningTestCase):
    """One project, authored only through the agent's routes, from Stage A to Stage E."""

    cad_export = "occt"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository = make_empty_project(self.root)
        write_runner_record(self.repository, RECORD)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export=self.cad_export, project_dir=self.project)))
        self.addCleanup(self.client.close)

    def digest(self, run: str | None = None) -> str:
        """The state a request is made against, read where the agent reads it: the construction model view."""

        return self.model(run)["stateDigest"]

    def entities(self, run: str) -> dict[str, dict]:
        return {row["id"]: row for row in self.model(run)["entities"]}

    def readiness(self, run: str) -> dict:
        response = self.client.get(f"/api/domains/structure/readiness?run={run}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def delivered(self, run: str) -> tuple[dict[str, float | None], dict[str, bool]]:
        """What a candidate delivered, read back from its own artifacts: each named shape's volume as OCCT
        measures it in the exact STEP, and whether the STEP shows it, which the 3dm preview must say too."""

        candidate = self.client.get(f"/api/candidates/{run}")
        self.assertEqual(candidate.status_code, 200, candidate.text)
        artifacts = candidate.json()["artifacts"]
        self.assertEqual(sorted(artifact["format"] for artifact in artifacts), ["3dm", "step"], artifacts)
        step, preview = (next(artifact for artifact in artifacts if artifact["format"] == kind) for kind in ("step", "3dm"))
        path = self.root / f"{run}.step"
        path.write_bytes(self.artifact_bytes(step))
        entries = occt_backend.read_step(path, length_unit=step["lengthUnit"])
        volumes = {entry.name: occt_backend.measure_shape(entry.shape).volume for entry in entries}
        visible = {entry.name: entry.visible for entry in entries}
        shown = {row["name"]: row["visible"] for row in inspect_three_dm_index(self.artifact_bytes(preview))["objects"]}
        self.assertEqual(shown, visible, "the preview shows what the exact STEP shows")
        return volumes, visible

    def artifact_bytes(self, artifact: dict) -> bytes:
        response = self.client.get(f"/api/artifacts/{artifact['sha256']}/bytes")
        self.assertEqual(response.status_code, 200, response.text)
        return response.content

    def assert_volume(self, measured: float | None, expected: float) -> None:
        self.assertIsNotNone(measured)
        self.assertTrue(math.isclose(measured, expected, rel_tol=1e-6), f"{measured} is not {expected}")

    def test_one_block_goes_from_a_script_to_a_wall_with_a_door(self) -> None:
        first = self.stage_a()
        niche = self.cut_a_niche(first)
        self.delete_is_refused(niche)
        walled = self.mean_a_wall(niche)
        door, door_volume = self.hang_a_door(walled)
        ready = self.answer_the_structure_domain(door)
        self.uncut_the_niche(ready, door_volume)

    # ---- the steps, each continuing from the candidate the previous one produced
    def stage_a(self) -> str:
        """1. One script makes two blocks; both are delivered, and neither means anything yet."""

        proposal = self.construct(STAGE_A)
        self.assertEqual([(row["id"], row["status"]) for row in proposal["construction"]["report"]],
                         [("mass", "created"), ("block", "created")])
        run = self.run_candidate(proposal["proposalId"])
        self.assertEqual(self.objects(run), {"obj-mass-body", "obj-block-body"})
        volumes, visible = self.delivered(run)
        self.assert_volume(volumes["obj-mass-body"], MASS_VOLUME)
        self.assert_volume(volumes["obj-block-body"], NICHE_VOLUME)
        self.assertEqual(visible, {"obj-mass-body": True, "obj-block-body": True})

        model = self.model(run)
        self.assertEqual(layer_rule_violations(json.dumps(model)), (), "the model view speaks construction words")
        rows = {row["id"]: row for row in model["entities"]}
        self.assertEqual(sorted(rows), ["block", "mass"])
        self.assertEqual(rows["mass"]["bounds"], [[0, 0, 0], [4, 3, 0.3]])
        self.assertEqual(rows["block"]["bounds"], [[6, 0, 0], [7, 1.2, 0.2]])
        for identifier, row in rows.items():
            self.assertEqual({key: row[key] for key in ("form", "facets", "capabilities", "cuts", "cutBy", "hidden")},
                             {"form": "solid", "facets": {}, "capabilities": [], "cuts": [], "cutBy": [],
                              "hidden": False}, identifier)
        return run

    def cut_a_niche(self, first: str) -> str:
        """2. The second script moves block into mass and cuts it: a niche, under the same ids."""

        proposal = self.construct(NICHE, sourceRunId=first)
        report = {row["id"]: row for row in proposal["construction"]["report"]}
        self.assertEqual({identifier: (row["status"], row["cuts"]) for identifier, row in report.items()},
                         {"block": ("updated", []), "mass": ("updated", ["block"])})
        self.assertEqual(report["block"]["bounds"], [[1.5, 0.9, 0.1], [2.5, 2.1, 0.3]])
        # Both rows are the existing elements rewritten in place; no component is made.
        rows = {row["entity_id"]: row for row in proposal["change"]["edits"]["entities"]}
        self.assertEqual(sorted(rows), ["block-body", "mass-body"])
        self.assertEqual(rows["mass-body"]["fields"]["references"]["voids"], ["block-body"])

        run = self.run_candidate(proposal["proposalId"])
        self.assertEqual(_identities(self.record(run)), _identities(self.record(first)))
        self.assertEqual(self.objects(run), {"obj-mass-body", "obj-block-body"})
        volumes, visible = self.delivered(run)
        self.assert_volume(volumes["obj-mass-body"], MASS_VOLUME - NICHE_VOLUME)
        # The cutter keeps its object, kept for inspection and invisible: the STEP read alone shows the niche open.
        self.assertEqual(visible, {"obj-mass-body": True, "obj-block-body": False})
        rows = self.entities(run)
        self.assertEqual({identifier: (row["cuts"], row["cutBy"], row["hidden"]) for identifier, row in rows.items()},
                         {"mass": (["block"], [], False), "block": ([], ["mass"], True)})
        return run

    def delete_is_refused(self, niche: str) -> None:
        """3. What still cuts mass cannot be deleted: refused at the script line, and nothing is saved."""

        before = self.model(niche)
        held = self.client.app.state.proposals.for_state(before["stateDigest"])
        body = self.construct(DELETE, expect=422, sourceRunId=niche)
        self.assert_refused_at(body, 1, DELETE)
        self.assertEqual(body["message"], "block still cuts mass; uncut it first")
        after = self.model(niche)
        self.assertEqual((after["stateDigest"], after["recordDigest"]), (before["stateDigest"], before["recordDigest"]))
        self.assertEqual(self.client.app.state.proposals.for_state(before["stateDigest"]), held, "nothing was saved")
        self.assertEqual(after["entities"], before["entities"])

    def mean_a_wall(self, niche: str) -> str:
        """4. Stage C: mass means a wall. No geometry changes; the capability that meaning unlocks appears."""

        proposal = self.facets([{"id": "mass", "set": {"architectural.role": "wall"}}], sourceRunId=niche)
        [row] = proposal["change"]["edits"]["entities"]
        self.assertEqual((row["entity_id"], row["schema"]), ("mass", "Component@1"))
        run = self.run_candidate(proposal["proposalId"])

        before, after = self.record(niche), self.record(run)
        self.assertEqual(_element_rows(after), _element_rows(before), "every element row is byte-identical")
        self.assertEqual(after.dependency_edges(), before.dependency_edges())
        was, now = before.entity("mass"), after.entity("mass")
        self.assertEqual(component_facets(now), {"architectural.role": "wall"})
        self.assertEqual({**now.to_dict(), "fields": {**now.fields, "facets": None}},
                         {**was.to_dict(), "fields": {**was.fields, "facets": None}}, "the component gained the facet only")
        self.assertEqual(_exported(self.project, run), _exported(self.project, niche), "the same objects, where they were")

        rows = self.entities(run)
        self.assertEqual({identifier: row["capabilities"] for identifier, row in rows.items()},
                         {"mass": HOSTED_OPENING, "block": []})
        self.assertEqual(rows["mass"]["facets"], {"architectural.role": "wall"})
        return run

    def hang_a_door(self, walled: str) -> tuple[str, float]:
        """5. A door in mass, clear of the niche: realised as a wall in place, under the object it always had."""

        rows = self.entities(walled)
        # along runs on mass's face at z = 0.3 from x = 0; the door (3.3 +/- 0.45) stays clear of the niche.
        self.assertEqual(rows["mass"]["alongLine"], {"start": [0, 0.3], "end": [4, 0.3]})
        self.assertGreater(ALONG - WIDTH / 2, rows["block"]["bounds"][1][0])
        proposal = self.open("mass", sourceRunId=walled, along=ALONG, width=WIDTH, sill=SILL, head=HEAD, family=DOOR)
        self.assertEqual(proposal["utterance"], "hosted opening: door opening-1 in mass")
        [row] = proposal["change"]["edits"]["entities"]
        self.assertEqual((row["entity_id"], row["parent_id"]), ("mass-body", "mass"))
        self.assertEqual(row["fields"]["references"]["voids"], ["block-body"], "the niche stays cut")

        run = self.run_candidate(proposal["proposalId"])
        before, after = self.objects(walled), self.objects(run)
        self.assertIn("obj-mass-body", after)
        self.assertLessEqual(FRAME_AND_LEAF, after)
        self.assertLessEqual(before, after)
        self.assertEqual(after - before, {name for name in after if "opening-1" in name})
        volumes, visible = self.delivered(run)
        # Both the niche and the door's hole are cut from the same solid. What made them, the cutter and the
        # hole's witness, is invisible, so the STEP read alone shows both open, with the door in its hole.
        self.assert_volume(volumes["obj-mass-body"], MASS_VOLUME - NICHE_VOLUME - DOOR_HOLE)
        self.assertEqual(visible, {"obj-mass-body": True, "obj-block-body": False, APERTURE: False,
                                   **dict.fromkeys(FRAME_AND_LEAF, True)})

        rows = self.entities(run)
        self.assertEqual({identifier: (row["cuts"], row["cutBy"], row["hidden"]) for identifier, row in rows.items()},
                         {"mass": (["block"], [], False), "block": ([], ["mass"], True)})
        self.assertEqual(rows["mass"]["openings"], [{"id": "opening-1", "kind": "door", "along": ALONG,
                                                     "width": WIDTH, "sill": SILL, "head": HEAD}])
        self.assertEqual(rows["mass"]["bounds"], [[0, 0, 0], [4, 3, 0.3]])
        return run, volumes["obj-mass-body"]  # type: ignore[return-value]

    def answer_the_structure_domain(self, door: str) -> str:
        """6. Stage D: the structure domain asks what it needs, and reads mass once mass says it."""

        asked = self.readiness(door)
        # Only mass is asked about: block, hidden in it as a cutter, is a construction helper, not something to name.
        self.assertEqual((asked["status"], asked["reads"]), ("enrichment_required", []))
        self.assertEqual(asked["requests"], [
            {"id": "mass", "missing": ["material.name", "structural.role"],
             "reason": "structure reads wall and needs material.name and structural.role to evaluate it"},
        ])

        enriched = self.facets([{"id": "mass", "set": {"structural.role": "load_bearing", "material.name": "brick"}}],
                               sourceRunId=door)
        run = self.run_candidate(enriched["proposalId"])
        ready = self.readiness(run)
        self.assertEqual((ready["status"], ready["reads"], ready["requests"]), ("ready", ["mass"], []))
        return run

    def uncut_the_niche(self, ready: str, door_volume: float) -> None:
        """7. uncut on the wall-realised mass: block shows again and mass gets the niche's volume back."""

        proposal = self.construct(UNCUT, sourceRunId=ready)
        self.assertEqual([(row["id"], row["status"], row["cuts"]) for row in proposal["construction"]["report"]],
                         [("mass", "updated", [])])
        run = self.run_candidate(proposal["proposalId"])
        volumes, visible = self.delivered(run)
        self.assert_volume(volumes["obj-mass-body"], door_volume + NICHE_VOLUME)
        # block shows again; the door stays in its hole.
        self.assertEqual(visible, {"obj-mass-body": True, "obj-block-body": True, APERTURE: False,
                                   **dict.fromkeys(FRAME_AND_LEAF, True)})
        rows = self.entities(run)
        self.assertEqual({identifier: (row["cuts"], row["cutBy"], row["hidden"]) for identifier, row in rows.items()},
                         {"mass": ([], [], False), "block": ([], [], False)})
        self.assertEqual([opening["id"] for opening in rows["mass"]["openings"]], ["opening-1"])
        # Uncut, block is ordinary geometry again, so the structure domain asks what it is.
        asked = self.readiness(run)
        self.assertEqual((asked["status"], asked["reads"]), ("enrichment_required", ["mass"]))
        self.assertEqual(asked["requests"], [{
            "id": "block", "missing": ["architectural.role"],
            "reason": "no architectural.role: the structure domain cannot tell whether block carries load",
        }])


if __name__ == "__main__":
    unittest.main()
