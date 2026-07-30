from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, fields, replace

from archflow.runtime import initial_state
from archflow.state import (
    BuildingProgram,
    BuildingProgramError,
    ProgramErrorCode,
)
from archflow.validation import compile_building_program


TEST_ONLY_BRIEF = {
    "use": "test_use",
    "width_blocks": 23,
    "depth_blocks": 31,
    "dimension_tolerance_blocks": 1,
    "required_spaces": [
        "entry",
        "primary_space",
        "support_space",
        "circulation",
    ],
    "minimum_clear_height": 4,
    "entrance_count": 1,
    "circulation_min_width": 2,
    "hard_requirements": [
        "all required spaces are reachable from the entrance",
    ],
    "soft_preferences": [
        "primary space receives daylight",
    ],
    "prohibitions": [
        "floating occupied rooms",
    ],
}


class BuildingProgramTests(unittest.TestCase):
    def test_brief_compiles_into_compact_canonical_program(self) -> None:
        program = compile_building_program(TEST_ONLY_BRIEF)
        state = replace(
            initial_state("A test-only building request"),
            legacy_program_view=program,
        )

        self.assertEqual(program.SCHEMA, "BuildingProgram@1")
        self.assertEqual(program.use, "test_use")
        self.assertEqual(program.footprint.width_blocks, 23)
        self.assertEqual(program.footprint.depth_blocks, 31)
        self.assertEqual(program.minimum_clear_height, 4)
        self.assertIn("primary_space", program.required_spaces)
        self.assertIs(state.legacy_program_view, program)
        self.assertFalse(
            {"transcript", "tool_calls", "layout", "expert_sequence"}
            & {item.name for item in fields(BuildingProgram)}
        )

    def test_hard_soft_and_prohibited_clauses_remain_distinct(self) -> None:
        program = compile_building_program(TEST_ONLY_BRIEF)

        self.assertEqual(
            program.hard_requirements,
            ("all required spaces are reachable from the entrance",),
        )
        self.assertEqual(
            program.soft_preferences,
            ("primary space receives daylight",),
        )
        self.assertEqual(program.prohibitions, ("floating occupied rooms",))

    def test_program_is_frozen_and_serialization_is_deterministic(self) -> None:
        program = compile_building_program(TEST_ONLY_BRIEF)
        encoded = program.to_json()

        with self.assertRaises(FrozenInstanceError):
            program.use = "office"  # type: ignore[misc]
        self.assertEqual(encoded, program.to_json())
        self.assertEqual(BuildingProgram.from_json(encoded), program)
        self.assertEqual(json.loads(encoded)["schema"], "BuildingProgram@1")

    def test_conflicting_clause_has_stable_reason_code(self) -> None:
        brief = dict(TEST_ONLY_BRIEF)
        brief["prohibitions"] = [
            "all required spaces are reachable from the entrance",
        ]

        with self.assertRaises(BuildingProgramError) as caught:
            compile_building_program(brief)

        self.assertEqual(caught.exception.code, ProgramErrorCode.CLAUSE_CONFLICT)
        self.assertEqual(caught.exception.field, "prohibitions")

    def test_invalid_tolerance_has_stable_reason_code(self) -> None:
        brief = dict(TEST_ONLY_BRIEF)
        brief["dimension_tolerance_blocks"] = 23

        with self.assertRaises(BuildingProgramError) as caught:
            compile_building_program(brief)

        self.assertEqual(
            caught.exception.code,
            ProgramErrorCode.INVALID_TOLERANCE,
        )

    def test_impossible_circulation_width_is_rejected(self) -> None:
        brief = dict(TEST_ONLY_BRIEF)
        brief["circulation_min_width"] = 23

        with self.assertRaises(BuildingProgramError) as caught:
            compile_building_program(brief)

        self.assertEqual(
            caught.exception.code,
            ProgramErrorCode.IMPOSSIBLE_CONSTRAINT,
        )

    def test_raw_history_field_is_not_accepted(self) -> None:
        brief = dict(TEST_ONLY_BRIEF)
        brief["tool_calls"] = ["minecraft_execute_build_plan"]

        with self.assertRaises(BuildingProgramError) as caught:
            compile_building_program(brief)

        self.assertEqual(
            caught.exception.code,
            ProgramErrorCode.UNSUPPORTED_FIELD,
        )


if __name__ == "__main__":
    unittest.main()
