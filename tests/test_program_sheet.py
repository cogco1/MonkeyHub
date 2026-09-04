"""The program sheet, both ways, against a record the kernel accepts.

The synthetic record here is a two-zone spatial option: a hall and a portico,
each with one ``Volume@1`` box on one ``MassingLevel@1``, joined by a
``Connection@1`` carrying an ``adjacent`` relation. That is enough for every
question this module answers — what a zone's area is, which component gives it
its function, which connection is a program adjacency — and small enough that
a failure names the row it broke.

Nothing here is villa data; every id is invented for this file.
"""
from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.project.refs import ProjectVersionRef
from archflow.state.program_sheet import (
    PROGRAM_SHEET_SCHEMA,
    ProgramSheetError,
    apply_sheet,
    sheet_from_record,
)
from archflow.state.state_record import Entity, Relation, StateRecord

EVIDENCE = "evidence:program-sheet-test"


def record() -> StateRecord:
    """A hall and a portico: two zones, two boxes, one declared adjacency."""

    return StateRecord(
        "demo",
        "run-1",
        (
            Entity("building", "Component@1", {"semantic_kind": "whole-building", "intent": "the building", "source_refs": [EVIDENCE], "volume_ids": ["volume-hall", "volume-portico"]}),
            Entity("hall-use", "Component@1", {"semantic_kind": "principal-use", "intent": "the hall", "source_refs": [EVIDENCE]}, parent_id="building"),
            Entity("portico-use", "Component@1", {"semantic_kind": "controlled-entry", "intent": "the portico", "source_refs": [EVIDENCE]}, parent_id="building"),
            Entity("massing-ground", "MassingLevel@1", {"base_y": 0, "height": 4}),
            # A 4 x 6 cell hall and a 4 x 2 cell portico, counted the way
            # ``SiteBounds`` counts: inclusive of both ends.
            Entity("volume-hall", "Volume@1", {"min": [0, 0, 0], "max": [3, 3, 5], "level_ids": ["massing-ground"]}),
            Entity("volume-portico", "Volume@1", {"min": [0, 0, 6], "max": [3, 3, 7], "level_ids": ["massing-ground"]}),
            Entity("zone-hall", "Space@1", {"program_node_refs": ["program:public/hall"], "level_ids": ["massing-ground"], "volume_ids": ["volume-hall"], "component_id": "hall-use"}),
            Entity("zone-portico", "Space@1", {"program_node_refs": ["program:public/portico"], "level_ids": ["massing-ground"], "volume_ids": ["volume-portico"]}, parent_id="portico-use"),
            Entity("connection-hall-portico", "Connection@1", {"source_zone_id": "zone-portico", "target_zone_id": "zone-hall", "relationship_refs": ["relation:rel-portico-to-hall"]}),
        ),
        relations=(
            Relation("rel-portico-to-hall", ArchitecturalRelationKind.ADJACENT.value, "zone-portico", "zone-hall"),
        ),
        evidence_refs=(EVIDENCE,),
        option={"option_id": "program-sheet-fixture"},
    )


def bound_record() -> StateRecord:
    return replace(record(), base=ProjectVersionRef("demo", 0, "0" * 64))


def sheet_with(**overrides: object) -> dict:
    """The smallest well-formed sheet, with whatever a test changes."""

    base = {
        "schema": PROGRAM_SHEET_SCHEMA,
        "project_id": "demo",
        "record_digest": bound_record().digest,
        "state_digest": bound_record().state_digest,
        "departments": [
            {
                "department_id": "service",
                "name": "Service",
                "spaces": [
                    {
                        "space_id": "store",
                        "name": "Store",
                        "function": "storage",
                        "target_area_m2": 18.0,
                        "count": 1,
                        "clear_height_m": 2.4,
                        "level_ids": ["massing-ground"],
                        "zone_id": None,
                    }
                ],
            }
        ],
        "adjacencies": [
            {
                "from_space_id": "store",
                "to_space_id": "zone-hall",
                "requirement": "adjacent",
                "relation_id": None,
            }
        ],
        "totals": {},
        "honesty": [],
    }
    base.update(overrides)
    return base


class SheetFromRecordTests(unittest.TestCase):
    """What the record already says, and what it says it cannot say."""

    def test_one_row_per_zone_under_the_department_its_ref_names(self) -> None:
        sheet = sheet_from_record(record())
        self.assertEqual(sheet["schema"], PROGRAM_SHEET_SCHEMA)
        self.assertEqual([d["department_id"] for d in sheet["departments"]], ["public"])
        spaces = sheet["departments"][0]["spaces"]
        self.assertEqual([s["space_id"] for s in spaces], ["hall", "portico"])
        self.assertEqual([s["zone_id"] for s in spaces], ["zone-hall", "zone-portico"])

    def test_area_is_the_footprint_of_the_zone_boxes(self) -> None:
        spaces = {
            space["space_id"]: space
            for space in sheet_from_record(record())["departments"][0]["spaces"]
        }
        # 4 x 6 cells and 4 x 2 cells, one square metre per cell.
        self.assertEqual(spaces["hall"]["target_area_m2"], 24.0)
        self.assertEqual(spaces["hall"]["mapped_area_m2"], 24.0)
        self.assertEqual(spaces["portico"]["target_area_m2"], 8.0)

    def test_stacked_boxes_of_one_zone_are_one_footprint(self) -> None:
        """Two levels of one zone are one floor plate, not two."""

        stacked = record()
        entities = tuple(
            entity
            if entity.entity_id != "zone-hall"
            else Entity(
                "zone-hall",
                "Space@1",
                {
                    "program_node_refs": ["program:public/hall"],
                    "level_ids": ["massing-ground"],
                    "volume_ids": ["volume-hall", "volume-hall-upper"],
                    "component_id": "hall-use",
                },
            )
            for entity in stacked.entities
        ) + (
            Entity("volume-hall-upper", "Volume@1", {"min": [0, 4, 0], "max": [3, 7, 5], "level_ids": ["massing-ground"]}),
        )
        from dataclasses import replace

        spaces = {
            space["space_id"]: space
            for space in sheet_from_record(replace(stacked, entities=entities))["departments"][0]["spaces"]
        }
        self.assertEqual(spaces["hall"]["target_area_m2"], 24.0)

    def test_a_zone_without_a_box_has_no_area_and_says_so(self) -> None:
        from dataclasses import replace

        bare = record()
        entities = tuple(
            entity
            if entity.entity_id != "zone-portico"
            else Entity("zone-portico", "Space@1", {"program_node_refs": ["program:public/portico"], "level_ids": ["massing-ground"], "volume_ids": []})
            for entity in bare.entities
        )
        sheet = sheet_from_record(replace(bare, entities=entities))
        spaces = {s["space_id"]: s for s in sheet["departments"][0]["spaces"]}
        self.assertIsNone(spaces["portico"]["target_area_m2"])
        self.assertIsNone(spaces["portico"]["mapped_area_m2"])
        self.assertTrue(
            any("zone-portico" in line for line in sheet["honesty"]),
            sheet["honesty"],
        )

    def test_function_is_the_component_semantic_kind_the_zone_names(self) -> None:
        spaces = {
            space["space_id"]: space
            for space in sheet_from_record(record())["departments"][0]["spaces"]
        }
        # One zone names its component by field, the other by parent.
        self.assertEqual(spaces["hall"]["function"], "principal-use")
        self.assertEqual(spaces["portico"]["function"], "controlled-entry")

    def test_no_clear_height_is_read_off_a_massing_level(self) -> None:
        sheet = sheet_from_record(record())
        for space in sheet["departments"][0]["spaces"]:
            self.assertIsNone(space["clear_height_m"])
        self.assertTrue(any("clear height" in line for line in sheet["honesty"]))

    def test_an_adjacency_row_comes_from_the_connection_and_its_relation(self) -> None:
        sheet = sheet_from_record(record())
        self.assertEqual(sheet["adjacencies"], [{
            "from_space_id": "portico",
            "to_space_id": "hall",
            "requirement": "adjacent",
            "relation_id": "rel-portico-to-hall",
        }])

    def test_a_connection_of_another_kind_is_named_not_squeezed_into_one(self) -> None:
        from dataclasses import replace

        other = record()
        relations = (Relation("rel-portico-to-hall", ArchitecturalRelationKind.ACCESS.value, "zone-portico", "zone-hall"),)
        sheet = sheet_from_record(replace(other, relations=relations))
        self.assertEqual(sheet["adjacencies"], [])
        self.assertTrue(
            any("rel-portico-to-hall" in line for line in sheet["honesty"]),
            sheet["honesty"],
        )

    def test_totals_add_only_the_rows_that_state_a_number(self) -> None:
        totals = sheet_from_record(record())["totals"]
        self.assertEqual(totals["target_area_m2"], 32.0)
        self.assertEqual(totals["mapped_area_m2"], 32.0)
        self.assertEqual(totals["unmapped_spaces"], [])

    def test_an_unbound_record_names_no_state_digest_and_says_why(self) -> None:
        sheet = sheet_from_record(record())
        self.assertEqual(sheet["record_digest"], record().digest)
        self.assertIsNone(sheet["state_digest"])
        self.assertTrue(any("bound to no run" in line for line in sheet["honesty"]))


class ApplySheetTests(unittest.TestCase):
    """What applying a sheet adds, and everything it leaves alone."""

    def test_a_space_without_a_zone_becomes_one_space_entity(self) -> None:
        applied = apply_sheet(bound_record(), sheet_with())
        store = applied.entity("store")
        self.assertEqual(store.schema, "Space@1")
        self.assertEqual(store.fields["program_node_refs"], ["program:service/store"])
        self.assertEqual(store.fields["level_ids"], ["massing-ground"])
        self.assertEqual(store.fields["volume_ids"], [])

    def test_a_sheet_without_a_state_digest_cannot_be_applied(self) -> None:
        with self.assertRaisesRegex(ProgramSheetError, "state_digest is required"):
            apply_sheet(bound_record(), sheet_with(state_digest=None))

    def test_a_sheet_without_a_record_digest_cannot_be_applied(self) -> None:
        with self.assertRaisesRegex(ProgramSheetError, "record_digest is required"):
            apply_sheet(bound_record(), sheet_with(record_digest=None))

    def test_a_sheet_from_an_older_state_is_stale(self) -> None:
        before = bound_record()
        sheet = sheet_with(state_digest=before.state_digest)
        changed = replace(
            before,
            option={**before.option, "label": "changed after sheet creation"},
        )
        self.assertNotEqual(changed.state_digest, before.state_digest)

        with self.assertRaisesRegex(ProgramSheetError, "exact base is stale"):
            apply_sheet(changed, sheet)

    def test_a_sheet_from_different_content_is_stale_even_when_state_matches(self) -> None:
        before = bound_record()
        sheet = sheet_from_record(before)
        changed = replace(
            before,
            entities=tuple(
                Entity(
                    entity.entity_id,
                    entity.schema,
                    {**entity.fields, "author_note": "content changed"},
                    parent_id=entity.parent_id,
                    basis_refs=entity.basis_refs,
                )
                if entity.entity_id == "building"
                else entity
                for entity in before.entities
            ),
        )
        self.assertEqual(changed.state_digest, before.state_digest)
        self.assertNotEqual(changed.digest, before.digest)

        with self.assertRaisesRegex(ProgramSheetError, "exact base is stale"):
            apply_sheet(changed, sheet)

    def test_an_adjacency_becomes_one_relation_and_the_connection_that_carries_it(self) -> None:
        applied = apply_sheet(bound_record(), sheet_with())
        added = applied.relations[-1]
        self.assertEqual(added.kind, ArchitecturalRelationKind.ADJACENT.value)
        self.assertEqual((added.subject, added.object), ("store", "zone-hall"))
        connection = applied.entity(f"connection-{added.relation_id}")
        self.assertEqual(connection.schema, "Connection@1")
        self.assertEqual(
            connection.fields["relationship_refs"],
            [f"relation:{added.relation_id}"],
        )

    def test_the_record_it_was_given_is_not_touched(self) -> None:
        before = bound_record()
        digest = before.digest
        apply_sheet(before, sheet_with())
        self.assertEqual(before.digest, digest)

    def test_existing_entities_keep_every_field_they_had(self) -> None:
        before = bound_record()
        applied = apply_sheet(before, sheet_with())
        for entity in before.entities:
            self.assertEqual(applied.entity(entity.entity_id).to_dict(), entity.to_dict())

    def test_a_mapped_space_only_gains_the_program_ref_it_lacks(self) -> None:
        sheet = sheet_with(
            departments=[{
                "department_id": "public",
                "name": "Public",
                "spaces": [{
                    "space_id": "hall",
                    "name": "Hall",
                    "function": None,
                    "target_area_m2": None,
                    "count": 1,
                    "clear_height_m": None,
                    "level_ids": [],
                    "zone_id": "zone-hall",
                }],
            }],
            adjacencies=[],
        )
        applied = apply_sheet(bound_record(), sheet)
        hall = applied.entity("zone-hall")
        self.assertEqual(
            hall.fields["program_node_refs"],
            ["program:public/hall"],
        )
        # The rest of the zone is untouched.
        self.assertEqual(hall.fields["volume_ids"], ["volume-hall"])
        self.assertEqual(len(applied.entities), len(bound_record().entities))

    def test_applying_a_derived_sheet_changes_nothing(self) -> None:
        """The round trip: what the record already says adds nothing to it."""

        before = bound_record()
        applied = apply_sheet(before, sheet_from_record(before))
        self.assertEqual(applied.digest, before.digest)

    def test_a_sheet_derived_from_the_applied_record_holds_the_new_space(self) -> None:
        before = bound_record()
        applied = apply_sheet(before, sheet_with())
        derived = sheet_from_record(applied, state_digest=before.state_digest)
        departments = {d["department_id"]: d for d in derived["departments"]}
        self.assertEqual(
            [s["space_id"] for s in departments["service"]["spaces"]],
            ["store"],
        )
        self.assertIn(
            {
                "from_space_id": "store",
                "to_space_id": "hall",
                "requirement": "adjacent",
                "relation_id": "program-adjacent-store-zone-hall",
            },
            derived["adjacencies"],
        )

    def test_an_unregistered_function_is_refused_with_the_nearest_ids(self) -> None:
        sheet = sheet_with()
        sheet["departments"][0]["spaces"][0]["function"] = "brooding-nook"
        with self.assertRaises(ProgramSheetError) as raised:
            apply_sheet(bound_record(), sheet)
        self.assertIn("brooding-nook", str(raised.exception))
        self.assertIn("nearest", str(raised.exception))

    def test_a_requirement_with_no_kernel_kind_is_refused_by_name(self) -> None:
        for requirement in ("near", "visual"):
            with self.subTest(requirement=requirement):
                sheet = sheet_with()
                sheet["adjacencies"][0]["requirement"] = requirement
                with self.assertRaises(ProgramSheetError) as raised:
                    apply_sheet(bound_record(), sheet)
                message = str(raised.exception)
                self.assertIn(requirement, message)
                # The vocabulary it does have, named rather than invented.
                self.assertIn(ArchitecturalRelationKind.ADJACENT.value, message)
                self.assertIn(ArchitecturalRelationKind.CLEARANCE.value, message)

    def test_apart_becomes_the_kernel_clearance_kind(self) -> None:
        sheet = sheet_with()
        sheet["adjacencies"][0]["requirement"] = "apart"
        applied = apply_sheet(bound_record(), sheet)
        self.assertEqual(
            applied.relations[-1].kind,
            ArchitecturalRelationKind.CLEARANCE.value,
        )

    def test_a_space_id_that_names_an_existing_entity_is_refused(self) -> None:
        sheet = sheet_with()
        sheet["departments"][0]["spaces"][0]["space_id"] = "zone-hall"
        with self.assertRaises(ProgramSheetError) as raised:
            apply_sheet(bound_record(), sheet)
        self.assertIn("zone-hall", str(raised.exception))

    def test_a_zone_id_that_names_no_zone_is_refused(self) -> None:
        sheet = sheet_with()
        sheet["departments"][0]["spaces"][0]["zone_id"] = "zone-nowhere"
        with self.assertRaises(ProgramSheetError):
            apply_sheet(bound_record(), sheet)

    def test_a_level_the_record_does_not_hold_is_refused(self) -> None:
        sheet = sheet_with()
        sheet["departments"][0]["spaces"][0]["level_ids"] = ["massing-attic"]
        with self.assertRaises(ProgramSheetError) as raised:
            apply_sheet(bound_record(), sheet)
        self.assertIn("massing-attic", str(raised.exception))

    def test_a_payload_that_does_not_claim_the_schema_is_refused(self) -> None:
        with self.assertRaises(ProgramSheetError):
            apply_sheet(bound_record(), sheet_with(schema="Something@1"))

    def test_the_same_space_twice_is_refused(self) -> None:
        sheet = sheet_with()
        sheet["departments"][0]["spaces"].append(
            dict(sheet["departments"][0]["spaces"][0])
        )
        with self.assertRaises(ProgramSheetError):
            apply_sheet(bound_record(), sheet)

    def test_an_adjacency_end_that_is_neither_space_nor_zone_is_refused(self) -> None:
        sheet = sheet_with()
        sheet["adjacencies"][0]["to_space_id"] = "nowhere"
        with self.assertRaises(ProgramSheetError):
            apply_sheet(bound_record(), sheet)

    def test_the_applied_record_still_satisfies_the_record_contracts(self) -> None:
        """Not a schema assertion: the record's own constructor re-validates."""

        applied = apply_sheet(bound_record(), sheet_with())
        StateRecord.from_dict(applied.to_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
