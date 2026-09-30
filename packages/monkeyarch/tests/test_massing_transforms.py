"""The six massing moves, on a pack read from a record built in place (#519).

The Project Runtime makes, measures and retains options with these moves; its
HTTP contract stays in its own suite (``POST /api/options``). Here each move is
arithmetic on a ``SchematicPack@1`` and each refusal names its code.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from archflow.state.state_record import Entity, StateRecord, schematic_pack_of
from monkeyarch.domain.massing_transforms import (
    ADD_FLOOR,
    PACK,
    REMOVE_FLOOR,
    SCALE_VOLUME,
    SHIFT_VOLUME,
    SPLIT_VOLUME,
    TRANSFORMS,
    MassingTransformError,
    require_transform,
    transformed,
)

EVIDENCE = "fixture:massing-transforms"


def _pack(*, roof_level: bool = False):
    """A 6 x 4 cell, one-storey block owned by the building, in one zone."""

    levels = [Entity("ground", "MassingLevel@1", {"base_y": 0, "height": 3}, basis_refs=(EVIDENCE,))]
    if roof_level:
        levels.append(Entity("roof", "MassingLevel@1", {"base_y": 3, "height": 3}, basis_refs=(EVIDENCE,)))
    record = StateRecord(
        "massing-fixture", "authored", (
            Entity("building", "Component@1", {"semantic_kind": "building", "intent": "massing moves",
                                              "volume_ids": ["block"], "source_refs": [EVIDENCE]}),
            *levels,
            Entity("block", "Volume@1", {"min": [0, 0, 0], "max": [5, 2, 3], "level_ids": ["ground"]},
                   basis_refs=(EVIDENCE,)),
            Entity("room", "Space@1", {"program_node_refs": ["program-node:study"], "level_ids": ["ground"],
                                       "volume_ids": ["block"]}, basis_refs=(EVIDENCE,)),
        ), evidence_refs=(EVIDENCE,),
        option={"option_id": "study", "label": "Study", "typology": "declared massing", "rationale": "fixture",
                "footprint_cells": [], "assumption_refs": []},
    )
    return schematic_pack_of(record)


def _volume(pack, volume_id: str) -> dict:
    return next(dict(volume) for volume in pack.volumes if volume["volume_id"] == volume_id)


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_is_six_moves_and_nothing_else(self) -> None:
        self.assertEqual(TRANSFORMS, ("add_floor", "remove_floor", "shift_volume", "scale_volume",
                                      "split_volume", "pack"))
        for name in TRANSFORMS:
            require_transform(name)
        with self.assertRaises(MassingTransformError) as raised:
            require_transform("rotate_volume")
        self.assertEqual(raised.exception.code, "UNKNOWN_TRANSFORM")
        self.assertEqual(str(raised.exception), "'rotate_volume' is not a massing transform. The whole vocabulary "
                         "is add_floor, remove_floor, shift_volume, scale_volume, split_volume, pack.")

    def test_a_move_names_the_option_and_says_what_made_it(self) -> None:
        pack, honesty = transformed(_pack(), ADD_FLOOR, {}, "option-007")
        self.assertEqual((pack.option_id, pack.label), ("option-007", "Study (add_floor)"))
        self.assertEqual(honesty, ())


class FloorTests(unittest.TestCase):
    def test_add_floor_carries_the_top_floors_volumes_and_zones(self) -> None:
        pack, _ = transformed(_pack(), ADD_FLOOR, {}, "option-001")
        self.assertEqual(pack.levels[-1], {"level_id": "massing-level", "base_y": 3, "height": 3})
        block = _volume(pack, "block")
        self.assertEqual(block["max"], [5, 5, 3])
        self.assertEqual(block["level_ids"], ["ground", "massing-level"])
        self.assertEqual(pack.zones[0]["level_ids"], ["ground", "massing-level"])

    def test_remove_floor_undoes_add_floor(self) -> None:
        raised, _ = transformed(_pack(), ADD_FLOOR, {}, "option-001")
        lowered, _ = transformed(raised, REMOVE_FLOOR, {}, "option-002")
        self.assertEqual([level["level_id"] for level in lowered.levels], ["ground"])
        self.assertEqual(_volume(lowered, "block"), {**_volume(_pack(), "block"), "max": [5, 2, 3]})

    def test_the_floors_a_move_cannot_make_are_refused_by_name(self) -> None:
        on_the_roof = _pack(roof_level=True)
        on_the_roof = replace(on_the_roof, volumes=({**_volume(on_the_roof, "block"), "level_ids": ["roof"]},))
        for pack, move, code in (
            (_pack(), REMOVE_FLOOR, "LAST_FLOOR"),
            (_pack(roof_level=True), ADD_FLOOR, "NOTHING_TO_RAISE"),
            (on_the_roof, REMOVE_FLOOR, "NOTHING_LEFT"),
            (replace(_pack(), levels=()), ADD_FLOOR, "NO_MASSING"),
        ):
            with self.subTest(move=move, code=code), self.assertRaises(MassingTransformError) as raised:
                transformed(pack, move, {}, "option-001")
            self.assertEqual(raised.exception.code, code)


class PlanTests(unittest.TestCase):
    def test_shift_moves_one_volume_by_whole_cells(self) -> None:
        pack, honesty = transformed(_pack(), SHIFT_VOLUME, {"volume_id": "block", "dx": 2, "dz": -1}, "option-001")
        block = _volume(pack, "block")
        self.assertEqual((block["min"], block["max"]), ([2, 0, -1], [7, 2, 2]))
        self.assertEqual(honesty, ("the declared option.footprint_cells are carried unchanged; the plan the "
                                   "volumes actually cover is footprintM2",))

    def test_scale_keeps_whole_cells_about_the_centre(self) -> None:
        pack, _ = transformed(_pack(), SCALE_VOLUME, {"volume_id": "block", "sx": 0.5, "sz": 2}, "option-001")
        block = _volume(pack, "block")
        # 6 cells to 3 about x = 2.5; 4 cells to 8 about z = 1.5.
        self.assertEqual((block["min"], block["max"]), ([1, 0, -2], [3, 2, 5]))
        tiny, _ = transformed(_pack(), SCALE_VOLUME, {"volume_id": "block", "sx": 0.01, "sz": 1}, "option-002")
        self.assertEqual(_volume(tiny, "block")["max"][0] - _volume(tiny, "block")["min"][0], 0)

    def test_split_gives_the_far_part_to_the_same_zone_and_owner(self) -> None:
        pack, honesty = transformed(_pack(), SPLIT_VOLUME, {"volume_id": "block", "along": "x", "at": 3},
                                    "option-001")
        self.assertEqual(_volume(pack, "block")["max"], [2, 2, 3])
        self.assertEqual(_volume(pack, "block-far")["min"], [3, 0, 0])
        self.assertEqual(pack.zones[0]["volume_ids"], ["block", "block-far"])
        self.assertEqual(pack.components[0].volume_ids, ("block", "block-far"))
        self.assertEqual(honesty, ("the declared option.footprint_cells are carried through a split unchanged; "
                                   "the measured footprint is footprintM2",))

    def test_the_plan_moves_a_request_cannot_make_are_refused_by_name(self) -> None:
        for move, parameters, code in (
            (SHIFT_VOLUME, {"volume_id": "block", "dx": 1.5, "dz": 0}, "WHOLE_CELLS"),
            (SHIFT_VOLUME, {"volume_id": "tower", "dx": 1, "dz": 0}, "UNKNOWN_VOLUME"),
            (SCALE_VOLUME, {"volume_id": "block", "sx": 0, "sz": 1}, "POSITIVE_FACTOR"),
            (SPLIT_VOLUME, {"volume_id": "block", "along": "y", "at": 1}, "UNKNOWN_AXIS"),
            (SPLIT_VOLUME, {"volume_id": "block", "along": "x", "at": 0}, "SPLIT_OUTSIDE_VOLUME"),
        ):
            with self.subTest(code=code), self.assertRaises(MassingTransformError) as raised:
                transformed(_pack(), move, parameters, "option-001")
            self.assertEqual(raised.exception.code, code)


class PackTests(unittest.TestCase):
    def test_a_whole_pack_is_the_kernels_to_read(self) -> None:
        sent = _pack().to_dict()
        pack, honesty = transformed(_pack(), PACK, {"pack": sent}, "option-001")
        self.assertEqual(pack.option_id, "option-001")
        self.assertEqual(pack.volumes, _pack().volumes)
        self.assertEqual(honesty, ("this pack was sent by the client and validated by the kernel; the studio "
                                   "derived none of it",))

    def test_a_missing_or_malformed_pack_is_refused_by_name(self) -> None:
        for parameters, code in (({}, "PACK_REQUIRED"), ({"pack": {"schema": "Nothing@1"}}, "PACK_INVALID")):
            with self.subTest(code=code), self.assertRaises(MassingTransformError) as raised:
                transformed(_pack(), PACK, parameters, "option-001")
            self.assertEqual(raised.exception.code, code)


if __name__ == "__main__":
    unittest.main()
