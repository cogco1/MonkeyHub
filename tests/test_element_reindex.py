"""Re-index: element identity recovered from boxes, proven by re-production, ambiguity named."""

from __future__ import annotations

import unittest

from archflow.capabilities.element_reindex import (
    ALTERNATE,
    AMBIGUOUS,
    BOUND,
    COVERED,
    DRAFT,
    MODEL_VISIBLE_CATALOG_MISSING,
    SUPERSEDED,
    family_of,
    opening_group_of,
    place_objects,
    objects_of,
    reindex,
    side_of,
)
from archflow.state.state_record import Entity, StateRecord

EVIDENCE = "evidence:fixture"


def component(entity_id: str, parent: str | None) -> Entity:
    return Entity(entity_id, "Component@1", {"schema": "DesignComponent@1", "semantic_kind": "storage-and-roof-support", "intent": "", "maturity": "schematic", "revision": 0, "volume_ids": [], "unresolved_child_roles": [], "source_refs": [EVIDENCE]}, parent, (EVIDENCE,))


def fixture_record() -> StateRecord:
    entities = [
        component("building", None), component("porticos", "building"),
        component("portico-columns", "porticos"), component("portico-capitals", "porticos"), component("portico-entablature", "porticos"),
        component("portico-pediments", "porticos"), component("portico-roof-abutments", "porticos"), component("main-block", "building"),
        Entity("level-ground", "Level@1", {"role": "ground", "elevation": 0.0}, None, (EVIDENCE,)),
        Entity("level-piano", "Level@1", {"role": "piano", "elevation": 3.57}, None, (EVIDENCE,)),
        Entity("level-cornice", "Level@1", {"role": "cornice", "elevation": 11.335}, None, (EVIDENCE,)),
        Entity("axis-a", "GridAxis@1", {"role": "A", "origin": [0.0, 0.0, -1.6], "direction": [1.0, 0.0, 0.0]}, None, (EVIDENCE,)),
        Entity("axis-b", "GridAxis@1", {"role": "B", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, None, (EVIDENCE,)),
        Entity("axis-c", "GridAxis@1", {"role": "C", "origin": [0.0, 0.0, 1.6], "direction": [1.0, 0.0, 0.0]}, None, (EVIDENCE,)),
        Entity("axis-wf", "GridAxis@1", {"role": "WF", "origin": [-10.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, None, (EVIDENCE,)),
    ]
    return StateRecord("fixture", "runner", tuple(entities), option={"option_id": "fixture-option"}, evidence_refs=(EVIDENCE,))


def box(name: str, component: str, op: str, lo, hi, faces=6, sha="ab" * 32):
    return {"bbox": {"name": name, "bbox": {"min": list(lo), "max": list(hi)}, "mesh_face_count": faces, "type": "Brep"},
            "strings": {"name": name, "attributes": [{"key": "archflow:component", "value": component}, {"key": "archflow:producer_op", "value": op}]},
            "sha": {"name": name, "geometry_sha256": sha}}


def inspection(objects) -> dict:
    return {"schema": "ThreeDmInspectionSummary@4", "file_sha256": "f" * 64, "object_count": len(objects),
            "named_object_bboxes": [o["bbox"] for o in objects], "object_user_strings": [o["strings"] for o in objects], "object_geometry_sha256": [o["sha"] for o in objects]}


R = 0.54
X = -14.374
COLUMN_TOP = 9.798


def west_portico():
    objs = []
    for k, y in enumerate((-1.6, 0.0, 1.6)):
        objs.append(box(f"obj-column-west-{k}", "portico-columns", f"column-west-{k}", (X - R, y - R, 3.57), (X + R, y + R, COLUMN_TOP), faces=26))
        objs.append(box(f"obj-abacus-west-{k}", "portico-capitals", f"abacus-west-{k}", (X - 0.7135, y - 0.7135, COLUMN_TOP), (X + 0.7135, y + 0.7135, 9.98)))
    objs.append(box("obj-entablature-front-west", "portico-entablature", "entablature-front-west", (X - 0.552, -3.3, 9.98), (X + 0.552, 3.3, 11.335)))
    objs.append(box("obj-entablature-return-west-left", "portico-entablature", "entablature-return-west-left", (X - 0.845, -3.588, 9.98), (-9.9, -3.012, 11.335)))
    objs.append(box("obj-portico-roof-abutment-west", "portico-roof-abutments", "portico-roof-abutment-west", (-10.75, -5.575, 11.335), (-10.19, 5.575, 13.115)))
    objs.append(box("obj-pediment-tympanum-west", "portico-pediments", "pediment-tympanum-west", (-15.094, -5.575, 11.335), (-14.674, 5.575, 13.115), faces=5))
    return objs


def ring(component: str, family: str, n: int = 16, r_in: float = 4.9, r_out: float = 5.55, z0: float = 13.3, z1: float = 13.94):
    import math
    objs = []
    for k in range(n):
        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
        pts = [(r * math.cos(a), r * math.sin(a)) for r in (r_in, r_out) for a in (a0, (a0 + a1) / 2, a1)]
        lo = (min(x for x, _ in pts), min(y for _, y in pts), z0)
        hi = (max(x for x, _ in pts), max(y for _, y in pts), z1)
        objs.append(box(f"obj-{family}-{k:02d}", component, f"{family}-{k:02d}", [round(v, 6) for v in lo], [round(v, 6) for v in hi]))
    return objs


def east_wall():
    """Three wall pieces on the east face x=10.29..10.71 between SE/NE, one window of four frames + glass, one door."""

    return [
        box("obj-wall-east-000", "main-block", "wall-east-000", (10.29, -10.71, 0.0), (10.71, -1.25, 11.335)),
        box("obj-wall-east-001", "main-block", "wall-east-001", (10.29, 1.25, 0.0), (10.71, 10.71, 11.335)),
        box("obj-wall-east-002", "main-block", "wall-east-002", (10.29, -1.25, 8.1), (10.71, 1.25, 11.335)),
        box("obj-frame-east-window-left-bottom", "portico-pediments", "frame-east-window-left-bottom", (10.63, 6.89, 5.1), (10.81, 8.21, 5.19)),
        box("obj-frame-east-window-left-top", "portico-pediments", "frame-east-window-left-top", (10.63, 6.89, 8.06), (10.81, 8.21, 8.15)),
        box("obj-frame-east-window-left-left", "portico-pediments", "frame-east-window-left-left", (10.63, 6.89, 5.1), (10.81, 6.98, 8.15)),
        box("obj-frame-east-window-left-right", "portico-pediments", "frame-east-window-left-right", (10.63, 8.12, 5.1), (10.81, 8.21, 8.15)),
        box("obj-glass-east-window", "portico-pediments", "glass-east-window", (10.675, 6.98, 5.19), (10.7, 8.12, 8.06)),
        box("obj-door-frame-east-left", "main-block", "door-frame-east-left", (10.63, -1.25, 3.57), (10.81, -1.13, 8.1)),
        box("obj-door-frame-east-right", "main-block", "door-frame-east-right", (10.63, 1.13, 3.57), (10.81, 1.25, 8.1)),
        box("obj-door-frame-east-top", "main-block", "door-frame-east-top", (10.63, -1.25, 7.98), (10.81, 1.25, 8.1)),
    ]


WEST_WALL_ROW = Entity("wall-west", "Element@1", {"component_id": "main-block", "producer": "wall",
    "references": {"line": {"from": {"grid": ["WF", "SE"]}, "to": {"grid": ["WF", "NE"]}, "face": "exterior", "inward": [1, 0]}, "base": {"level": "level-ground"}, "top": {"level": "level-cornice"}},
    "params": {"thickness": 0.42, "types": [{"schema": "WindowType@1", "type_id": "window-type-2", "frame_width": 0.09, "frame_depth": 0.18, "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01},
                                            {"schema": "DoorType@1", "type_id": "door-type-1", "frame_width": 0.12, "frame_depth": 0.18, "frame_projection": 0.1, "leaf_thickness": 0.08, "leaf_offset": -0.025, "leaf_count": 2, "leaf_gap": 0.03, "clearance_bottom": 0.02, "clearance_top": 0.02}],
               "openings": [{"opening_id": "window-left", "kind": "window", "at": {"host": {"element": "wall-west", "along": 3.16}}, "width": 1.32, "sill": {"offset_from": {"level": "level-ground", "offset": 5.1}}, "head": {"offset_from": {"level": "level-ground", "offset": 8.15}}, "component_id": "portico-pediments", "type_id": "window-type-2"},
                            {"opening_id": "door", "kind": "door", "at": {"host": {"element": "wall-west", "along": 10.71}}, "width": 2.5, "sill": {"offset_from": {"level": "level-ground", "offset": 3.57}}, "head": {"offset_from": {"level": "level-ground", "offset": 8.1}}, "component_id": "main-block", "type_id": "door-type-1"}]}},
    "main-block", (EVIDENCE,))


def record_with_edges() -> StateRecord:
    from dataclasses import replace
    base = fixture_record()
    extra = (
        Entity("axis-se", "GridAxis@1", {"role": "SE", "origin": [0.0, 0.0, -10.71], "direction": [1.0, 0.0, 0.0]}, None, (EVIDENCE,)),
        Entity("axis-ne", "GridAxis@1", {"role": "NE", "origin": [0.0, 0.0, 10.71], "direction": [1.0, 0.0, 0.0]}, None, (EVIDENCE,)),
        WEST_WALL_ROW,
    )
    return replace(base, entities=base.entities + extra)


class RingAndWallTests(unittest.TestCase):
    def test_sectors_become_a_ring_around_drafted_centre_axes(self) -> None:
        result = reindex(fixture_record(), [(inspection(ring("main-block", "drum-bearing-ring")), "record:base", 0)])
        draft = {d.element_id: d for d in result.drafts}["main-block"]
        self.assertEqual(draft.producer, "ring", draft.notes)
        self.assertEqual(draft.status, DRAFT, draft.notes)
        self.assertEqual(draft.references["at"], {"grid": ["CENTRE-X", "B"]})  # y=0 is axis B already
        self.assertAlmostEqual(draft.params["inner_radius"], 4.9, places=5)
        self.assertAlmostEqual(draft.params["outer_radius"], 5.55, places=5)
        self.assertEqual(draft.params["pieces"], 16)
        self.assertLessEqual(draft.residual_m, 0.01)
        self.assertEqual({a.role for a in result.frame.drafted}, {"CENTRE-X"})

    def test_wall_pieces_and_frames_become_one_wall_with_openings_typed_by_the_taught_row(self) -> None:
        result = reindex(record_with_edges(), [(inspection(east_wall()), "record:base", 0)])
        drafts = {d.element_id: d for d in result.drafts}
        wall = drafts["main-block-east"]
        self.assertEqual(wall.status, DRAFT, wall.notes)
        self.assertEqual(wall.producer, "wall")
        line = wall.references["line"]
        # origin at NE so the right-hand normal of NE->SE points inward (-x)
        self.assertEqual(line["from"], {"grid": ["MAIN-BLOCK-EAST-FACE", "NE"]})
        self.assertEqual(line["to"], {"grid": ["MAIN-BLOCK-EAST-FACE", "SE"]})
        self.assertEqual(line["inward"], [-1, 0])
        self.assertEqual(wall.references["base"], {"level": "level-ground"})
        self.assertEqual(wall.references["top"], {"level": "level-cornice"})
        self.assertAlmostEqual(wall.params["thickness"], 0.42, places=6)
        openings = {o["opening_id"]: o for o in wall.params["openings"]}
        self.assertEqual(set(openings), {"east-window-left", "east-door"})
        self.assertAlmostEqual(openings["east-window-left"]["width"], 1.32, places=6)
        self.assertAlmostEqual(openings["east-window-left"]["at"]["host"]["along"], 10.71 - 7.55, places=6)
        self.assertEqual(openings["east-window-left"]["sill"], {"offset_from": {"level": "level-ground", "offset": 5.1}})
        self.assertEqual(openings["east-window-left"]["head"], {"offset_from": {"level": "level-ground", "offset": 8.15}})
        self.assertEqual(openings["east-window-left"]["type_id"], "window-type-2")
        self.assertEqual(openings["east-window-left"]["component_id"], "portico-pediments")
        self.assertAlmostEqual(openings["east-door"]["width"], 2.5, places=6)
        self.assertAlmostEqual(openings["east-door"]["at"]["host"]["along"], 10.71, places=6)
        self.assertEqual(openings["east-door"]["type_id"], "door-type-1")
        self.assertEqual(len(wall.params["types"]), 2)
        hosted = sorted(o.name for o in wall.hosted)
        self.assertEqual(hosted, sorted(o["bbox"]["name"] for o in east_wall() if "frame" in o["bbox"]["name"] or "glass" in o["bbox"]["name"]))
        catalog = result.catalog(run_id="r")
        status = {c["component_id"]: c for c in catalog["components"]}
        self.assertEqual(status["portico-pediments"]["catalog_status"], COVERED)
        self.assertEqual(status["portico-pediments"]["hosted_by"], ["main-block-east"])
        self.assertEqual({o["name"]: o["element_id"] for o in catalog["objects"]}["obj-glass-east-window"], "main-block-east")
        self.assertIsNotNone(wall.residual_m)
        self.assertLessEqual(wall.residual_m, 0.2, wall.notes)   # frames project 0.1 m outside the face; the union is compared


class NamingTests(unittest.TestCase):
    def test_family_and_side_come_off_the_producer_op(self) -> None:
        self.assertEqual(family_of("column-west-3"), "column")
        self.assertEqual(side_of("column-west-3"), "west")
        self.assertEqual(family_of("entablature-return-west-left"), "entablature-return-left")
        self.assertEqual(family_of("obj-portico-roof-abutment-north"), "portico-roof-abutment")
        self.assertIsNone(side_of("drum-cornice-upper-23"))
        self.assertEqual(opening_group_of("frame-east-window-left-bottom"), "window-left")
        self.assertEqual(opening_group_of("frame-east-attic-a-top"), "attic-a")
        self.assertEqual(opening_group_of("door-frame-east-left"), "door")
        self.assertEqual(opening_group_of("glass-east-window"), "window")
        self.assertIsNone(opening_group_of("column-east-1"))


class DraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = fixture_record()
        self.result = reindex(self.record, [(inspection(west_portico()), "record:base", 0)])
        self.drafts = {d.element_id: d for d in self.result.drafts}

    def test_columns_become_an_array_on_the_declared_axes_and_a_drafted_facade_line(self) -> None:
        columns = self.drafts["portico-columns-west"]
        self.assertEqual(columns.status, DRAFT)
        self.assertEqual(columns.producer, "column-array")
        self.assertEqual(columns.references["axes"], ["A", "B", "C"])
        self.assertEqual(columns.references["base"], {"level": "level-piano"})
        self.assertAlmostEqual(columns.params["radius"], R, places=6)
        self.assertAlmostEqual(columns.params["height"], COLUMN_TOP - 3.57, places=6)
        facade = columns.references["facade"]
        drafted = {a.role: a for a in self.result.frame.drafted}
        self.assertIn(facade, drafted)
        self.assertAlmostEqual(drafted[facade].value, X, places=6)
        self.assertIsNotNone(columns.residual_m)
        self.assertLessEqual(columns.residual_m, 1e-6)

    def test_capitals_sit_on_the_columns_top_and_the_beam_overhangs_the_outer_axes(self) -> None:
        capitals = self.drafts["portico-capitals-west"]
        self.assertEqual(capitals.producer, "capitals")
        self.assertEqual(capitals.references["columns"], "portico-columns-west")
        self.assertEqual(capitals.references["base"], {"datum": "portico-columns-west-top"})
        self.assertLessEqual(capitals.residual_m, 1e-6)
        beam = self.drafts["portico-entablature-front-west"]
        self.assertEqual(beam.producer, "beam")
        self.assertEqual(beam.references["from"], {"grid": ["A", capitals.references["facade"]]})
        self.assertEqual(beam.references["to"], {"grid": ["C", capitals.references["facade"]]})
        self.assertAlmostEqual(beam.params["end_overhang"], 1.7, places=6)
        self.assertEqual(beam.references["base"], {"datum": "portico-capitals-west-top"})
        self.assertLessEqual(beam.residual_m, 1e-6)

    def test_a_box_is_a_prism_with_its_footprint_and_a_wedge_is_ambiguous(self) -> None:
        abutment = self.drafts["portico-roof-abutments-west"]
        self.assertEqual(abutment.producer, "prism")
        self.assertEqual(abutment.references["base"], {"level": "level-cornice"})
        self.assertAlmostEqual(abutment.params["height"], 1.78, places=6)
        self.assertLessEqual(abutment.residual_m, 1e-6)
        pediment = self.drafts["portico-pediments-west"]
        self.assertEqual(pediment.status, AMBIGUOUS)
        self.assertIsNone(pediment.producer)
        self.assertTrue(any("no producer carries this form" in n for n in pediment.notes))

    def test_support_relations_follow_contact(self) -> None:
        ids = {r.relation_id for r in self.result.relations}
        self.assertIn("portico-columns-west-supports-portico-capitals-west", ids)
        self.assertIn("portico-capitals-west-supports-portico-entablature-front-west", ids)
        self.assertTrue(all(r.epistemic_status == "derived" and r.kind == "support" for r in self.result.relations))

    def test_the_successor_is_a_valid_record_that_adds_and_replaces_nothing(self) -> None:
        before = self.record.digest
        successor = self.result.successor(run_id="reindex-001", basis_refs=["record:base"])
        self.assertEqual(self.record.digest, before)
        self.assertEqual(successor.run_id, "reindex-001")
        self.assertEqual(successor.predecessor_ref, f"record:{before}")
        rows = {e.entity_id: e for e in successor.entities_of("Element@1")}
        self.assertEqual(set(rows), {"portico-columns-west", "portico-capitals-west", "portico-entablature-front-west", "portico-entablature-return-left-west", "portico-roof-abutments-west"})
        self.assertEqual(rows["portico-columns-west"].parent_id, "portico-columns")
        self.assertEqual(rows["portico-columns-west"].fields["epistemic_status"], "derived")
        self.assertEqual(len(successor.entities_of("GridAxis@1")), 5)
        self.assertTrue(any(r.relation_id == "portico-columns-west-supports-portico-capitals-west" for r in successor.relations))
        StateRecord.from_dict(successor.to_dict())  # round-trips

    def test_the_catalog_names_coverage_per_component(self) -> None:
        catalog = self.result.catalog(run_id="reindex-001")
        status = {c["component_id"]: c["catalog_status"] for c in catalog["components"]}
        self.assertEqual(status["portico-columns"], COVERED)
        self.assertEqual(status["portico-pediments"], MODEL_VISIBLE_CATALOG_MISSING)
        self.assertEqual(status["main-block"], "EMPTY")
        objects = {o["name"]: o for o in catalog["objects"]}
        self.assertEqual(objects["obj-column-west-1"]["element_id"], "portico-columns-west")
        self.assertIsNone(objects["obj-pediment-tympanum-west"]["element_id"])
        self.assertEqual(catalog["summary"]["elements_ambiguous"], 1)
        self.assertIn("portico-entablature-return-left-west", {e["element_id"] for e in catalog["elements"]})
        self.assertEqual(catalog["schema"], "ComponentCatalog@1")


class PrecedenceTests(unittest.TestCase):
    def test_a_patch_supersedes_the_base_and_equal_patches_are_alternates(self) -> None:
        base = objects_of(inspection(west_portico()), "record:base", 0)
        patch = objects_of(inspection([box("obj-column-west-0", "portico-columns", "column-west-0", (X - R, -1.6 - R, 3.57), (X + R, -1.6 + R, 9.598), faces=26)]), "record:patch-a", 1)
        placed = {p.obj.name + "@" + p.obj.source_ref: p for p in place_objects(base + patch)}
        self.assertEqual(placed["obj-column-west-0@record:base"].status, SUPERSEDED)
        self.assertEqual(placed["obj-column-west-0@record:patch-a"].status, BOUND)
        self.assertEqual(placed["obj-abacus-west-0@record:base"].status, BOUND)
        # the patch covers one column only: the base's other columns stand, and say why
        self.assertEqual(placed["obj-column-west-1@record:base"].status, BOUND)
        self.assertIn("outside the extent", placed["obj-column-west-1@record:base"].note)
        other = objects_of(inspection([box("obj-column-west-0", "portico-columns", "column-west-0", (X - R, -1.6 - R, 3.57), (X + R, -1.6 + R, 9.9), faces=26)]), "record:patch-b", 1)
        placed = {p.obj.name + "@" + p.obj.source_ref: p for p in place_objects(base + patch + other)}
        self.assertEqual(placed["obj-column-west-0@record:patch-a"].status, ALTERNATE)
        self.assertEqual(placed["obj-column-west-0@record:patch-b"].status, ALTERNATE)
        self.assertEqual(placed["obj-column-west-0@record:base"].status, BOUND)
        self.assertIn("contend", placed["obj-column-west-0@record:base"].note)

    def test_a_witness_object_is_identified_and_never_drafted(self) -> None:
        record = fixture_record()
        witness = box("obj-portal-witness", "main-block", "portal-witness", (0, 0, 0), (1, 1, 1))
        witness["strings"]["attributes"].append({"key": "archflow:inspection_witness", "value": "portal"})
        result = reindex(record, [(inspection(west_portico() + [witness]), "record:base", 0)])
        catalog = result.catalog(run_id="r")
        self.assertEqual({o["name"]: o["status"] for o in catalog["objects"]}["obj-portal-witness"], "witness")
        self.assertFalse(any(o.name == "obj-portal-witness" for d in result.drafts for o in d.all_objects))
        self.assertEqual(catalog["summary"]["witness"], 1)

    def test_an_undeclared_component_is_named_not_drafted(self) -> None:
        record = fixture_record()
        objs = west_portico() + [box("obj-tower-0", "tower", "tower-0", (0, 0, 0), (1, 1, 1))]
        result = reindex(record, [(inspection(objs), "record:base", 0)])
        catalog = result.catalog(run_id="r")
        self.assertEqual(catalog["unknown_components"], ["tower"])
        self.assertEqual({o["name"]: o["status"] for o in catalog["objects"]}["obj-tower-0"], "UNKNOWN_COMPONENT")
        self.assertFalse(any(d.component_id == "tower" for d in result.drafts))


if __name__ == "__main__":
    unittest.main()
