"""What a massing measures, against boxes small enough to add up by hand.

Every expected number here is computed in the test's own docstring from the
inclusive-cell frame the module documents: a box from ``min`` to ``max``
covers ``max - min + 1`` cells on each axis, one plan cell is one square
metre, and a level of height ``h`` is ``h`` metres tall.
"""

from __future__ import annotations

import unittest

from monkeyarch.capabilities.massing_metrics import (
    FAR_EXCEEDED,
    HEIGHT_EXCEEDED,
    VOLUME_OUTSIDE_ENVELOPE,
    MassingMetricsError,
    envelope_check,
    massing_metrics,
)
from archflow.state.state_record import Entity, StateRecord, volume_boxes_of

from tests.support import authored_record

EVIDENCE = "evidence:massing"


def _level(level_id: str, base_y: int, height: int) -> Entity:
    return Entity(level_id, "MassingLevel@1", {"base_y": base_y, "height": height}, basis_refs=(EVIDENCE,))


def _volume(volume_id: str, low, high, level_ids) -> Entity:
    return Entity(volume_id, "Volume@1", {"min": list(low), "max": list(high), "level_ids": list(level_ids)}, basis_refs=(EVIDENCE,))


def _zone(zone_id: str, level_ids, volume_ids) -> Entity:
    return Entity(zone_id, "Space@1", {"program_node_refs": [f"program-node:{zone_id}"], "level_ids": list(level_ids), "volume_ids": list(volume_ids)},
                  basis_refs=(EVIDENCE,))


def _record(*entities: Entity) -> StateRecord:
    return StateRecord(project_id="demo", run_id="run-1", entities=tuple(entities), evidence_refs=(EVIDENCE,))


class VolumeBoxTests(unittest.TestCase):
    """The one reader of the ``Volume@1`` box, which the runner shares."""

    def test_boxes_are_read_as_declared(self) -> None:
        record = _record(_level("ground", 0, 3), _volume("block", (0, 0, 0), (3, 2, 2), ("ground",)))

        self.assertEqual(
            volume_boxes_of(record),
            {"block": ((0.0, 0.0, 0.0), (3.0, 2.0, 2.0))},
        )


class MetricsTests(unittest.TestCase):
    def test_one_volume_on_one_level(self) -> None:
        """x 0..3 is 4 cells, z 0..2 is 3: 12 m2 of ground, 12 of floor, 3 m tall."""

        record = _record(_level("ground", 0, 3), _volume("block", (0, 0, 0), (3, 2, 2), ("ground",)))

        metrics = massing_metrics(record)

        self.assertEqual(metrics.footprint_m2, 12.0)
        self.assertEqual(metrics.gross_floor_area_m2, 12.0)
        self.assertEqual(metrics.floor_count, 1)
        self.assertEqual(metrics.height_m, 3.0)
        self.assertIsNone(metrics.efficiency)
        self.assertEqual([level.level_id for level in metrics.per_level], ["ground"])
        self.assertEqual(metrics.per_level[0].footprint_m2, 12.0)
        self.assertEqual(metrics.honesty, ())

    def test_a_volume_spanning_two_levels_yields_two_floors_of_area(self) -> None:
        """The same 12 m2 plate on two levels: 12 of ground, 24 of floor, 6 m."""

        record = _record(
            _level("ground", 0, 3),
            _level("upper", 3, 3),
            _volume("block", (0, 0, 0), (3, 5, 2), ("ground", "upper")),
        )

        metrics = massing_metrics(record)

        self.assertEqual(metrics.footprint_m2, 12.0)
        self.assertEqual(metrics.gross_floor_area_m2, 24.0)
        self.assertEqual(metrics.floor_count, 2)
        self.assertEqual(metrics.height_m, 6.0)
        self.assertEqual([level.footprint_m2 for level in metrics.per_level], [12.0, 12.0])

    def test_overlapping_volumes_cover_their_ground_once(self) -> None:
        """4x3 and 4x3 sharing 2x3: the union is 18 m2, not the 24 of the sum."""

        record = _record(
            _level("ground", 0, 3),
            _volume("west", (0, 0, 0), (3, 2, 2), ("ground",)),
            _volume("east", (2, 0, 0), (5, 2, 2), ("ground",)),
        )

        metrics = massing_metrics(record)

        self.assertEqual(metrics.footprint_m2, 18.0)
        self.assertEqual(metrics.gross_floor_area_m2, 18.0)

    def test_disjoint_volumes_add_up(self) -> None:
        """Nothing shared: 12 + 12 = 24 m2 of ground on one floor."""

        record = _record(
            _level("ground", 0, 3),
            _volume("west", (0, 0, 0), (3, 2, 2), ("ground",)),
            _volume("east", (10, 0, 0), (13, 2, 2), ("ground",)),
        )

        self.assertEqual(massing_metrics(record).footprint_m2, 24.0)

    def test_levels_are_ordered_from_the_bottom(self) -> None:
        record = _record(
            _level("upper", 3, 3),
            _level("ground", 0, 3),
            _volume("block", (0, 0, 0), (3, 5, 2), ("ground", "upper")),
        )

        self.assertEqual([level.level_id for level in massing_metrics(record).per_level], ["ground", "upper"])

    def test_efficiency_is_the_program_share_of_the_floor_area(self) -> None:
        """6 + 3 = 9 m2 of program against 12 m2 of floor: 0.75."""

        record = _record(_level("ground", 0, 3), _volume("block", (0, 0, 0), (3, 2, 2), ("ground",)))

        metrics = massing_metrics(record, program_targets={"hall": 6.0, "store": 3.0})

        self.assertEqual(metrics.efficiency, 0.75)

    def test_no_program_is_no_efficiency_rather_than_zero(self) -> None:
        record = _record(_level("ground", 0, 3), _volume("block", (0, 0, 0), (3, 2, 2), ("ground",)))

        self.assertIsNone(massing_metrics(record).efficiency)

    def test_a_record_with_no_massing_says_so_instead_of_measuring_zero(self) -> None:
        metrics = massing_metrics(_record(Entity("level-ground", "Level@1", {"role": "ground", "elevation": 0.0}, basis_refs=(EVIDENCE,))))

        self.assertEqual(metrics.floor_count, 0)
        self.assertEqual(metrics.gross_floor_area_m2, 0.0)
        self.assertEqual(len(metrics.honesty), 2)
        self.assertIn("no MassingLevel@1", metrics.honesty[0])
        self.assertIn("no Volume@1", metrics.honesty[1])

    def test_a_volume_off_the_lattice_is_named_not_rounded(self) -> None:
        record = _record(_level("ground", 0, 3), _volume("block", (0.0, 0.0, 0.0), (3.5, 2.0, 2.0), ("ground",)))

        metrics = massing_metrics(record)

        self.assertEqual(metrics.footprint_m2, 0.0)
        self.assertTrue(any("not whole cells" in line for line in metrics.honesty))

    def test_a_volume_on_a_project_level_adds_no_floor_area(self) -> None:
        """A ``Level@1`` is a datum, not a floor: the volume is ground, not GFA."""

        record = _record(
            Entity("level-ground", "Level@1", {"role": "ground", "elevation": 0.0}, basis_refs=(EVIDENCE,)),
            _level("ground", 0, 3),
            _volume("block", (0, 0, 0), (3, 2, 2), ("level-ground",)),
        )

        metrics = massing_metrics(record)

        self.assertEqual(metrics.footprint_m2, 12.0)
        self.assertEqual(metrics.gross_floor_area_m2, 0.0)
        self.assertTrue(any("is no MassingLevel@1" in line for line in metrics.honesty))

    def test_a_volume_naming_no_level_is_named(self) -> None:
        record = _record(_level("ground", 0, 3), _volume("block", (0, 0, 0), (3, 2, 2), ()))

        self.assertTrue(any("names no level" in line for line in massing_metrics(record).honesty))

    def test_the_shared_fixture_record_measures_its_own_block(self) -> None:
        """0..12 on x and z is 13x13 = 169 m2, one level 12 m tall."""

        metrics = massing_metrics(authored_record())

        self.assertEqual(metrics.footprint_m2, 169.0)
        self.assertEqual(metrics.gross_floor_area_m2, 169.0)
        self.assertEqual(metrics.floor_count, 1)
        self.assertEqual(metrics.height_m, 12.0)


class EnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = _record(
            _level("ground", 0, 3),
            _level("upper", 3, 3),
            _volume("block", (0, 0, 0), (3, 5, 2), ("ground", "upper")),
        )

    def test_an_envelope_that_says_nothing_finds_nothing(self) -> None:
        self.assertEqual(envelope_check(self.record, {}), ())

    def test_a_massing_inside_its_envelope_is_clean(self) -> None:
        self.assertEqual(
            envelope_check(
                self.record,
                {"min": [-1, 0, -1], "max": [10, 20, 10], "max_height_m": 20.0, "far": 3.0, "site_area_m2": 100.0},
            ),
            (),
        )

    def test_a_volume_past_the_envelope_is_reported_with_both_numbers(self) -> None:
        findings = envelope_check(self.record, {"min": [0, 0, 0], "max": [2, 20, 20]})

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, VOLUME_OUTSIDE_ENVELOPE)
        self.assertEqual(findings[0].subject, "block")
        self.assertEqual(findings[0].measured, 3.0)
        self.assertEqual(findings[0].limit, 2.0)

    def test_height_over_the_limit_is_the_measured_height(self) -> None:
        findings = envelope_check(self.record, {"max_height_m": 4.5})

        self.assertEqual([f.code for f in findings], [HEIGHT_EXCEEDED])
        self.assertEqual(findings[0].measured, 6.0)
        self.assertEqual(findings[0].limit, 4.5)

    def test_far_is_checked_against_the_gross_floor_area(self) -> None:
        """24 m2 of floor on 100 m2 of site at 0.2 allows 20: over by 4."""

        findings = envelope_check(self.record, {"far": 0.2, "site_area_m2": 100.0})

        self.assertEqual([f.code for f in findings], [FAR_EXCEEDED])
        self.assertEqual(findings[0].measured, 24.0)
        self.assertEqual(findings[0].limit, 20.0)

    def test_far_without_a_site_area_is_not_a_guess(self) -> None:
        self.assertEqual(envelope_check(self.record, {"far": 0.2}), ())

    def test_a_malformed_envelope_is_refused_by_name(self) -> None:
        with self.assertRaises(MassingMetricsError):
            envelope_check(self.record, {"min": [0, 0], "max": [1, 1, 1]})
        with self.assertRaises(MassingMetricsError):
            envelope_check(self.record, {"max_height_m": "tall"})


if __name__ == "__main__":
    unittest.main()
