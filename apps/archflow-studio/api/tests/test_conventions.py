"""PROJECT.md: names and a compass, nothing that belongs to the record."""

from __future__ import annotations

import unittest

from archflow_studio_api.application.conventions import parse_conventions


class ParseTests(unittest.TestCase):
    def test_names_and_compass_are_read_and_undeclared_names_are_ignored(self) -> None:
        text = """# Project

## Names
- 柱子, columns => portico-columns
- 塔 => tower

## Compass
- north: +y
- east: +x

state digest: """ + "a" * 64
        conventions = parse_conventions(text, declared_components={"portico-columns"})
        self.assertEqual(conventions.aliases, {"柱子": "portico-columns", "columns": "portico-columns"})
        self.assertEqual(conventions.compass, {"north": (0.0, 1.0), "east": (1.0, 0.0), "south": (0.0, -1.0), "west": (-1.0, 0.0)})
        self.assertEqual(conventions.state_digest, "a" * 64)
        self.assertTrue(any("tower" in line and "ignored" in line for line in conventions.honesty))

    def test_a_compass_with_only_north_is_completed_from_north(self) -> None:
        conventions = parse_conventions("## Compass\n- north: -x\n")
        self.assertEqual(conventions.compass["north"], (-1.0, 0.0))
        self.assertEqual(conventions.compass["south"], (1.0, 0.0))
        self.assertEqual(conventions.compass["east"], (0.0, 1.0))
        self.assertEqual(conventions.compass["west"], (0.0, -1.0))

    def test_a_malformed_line_is_said_not_guessed(self) -> None:
        conventions = parse_conventions("## Names\n- roof portico-roofs\n## Compass\n- up: +z\n")
        self.assertEqual(conventions.aliases, {})
        self.assertIsNone(conventions.compass)
        self.assertEqual(len(conventions.honesty), 2)

    def test_no_file_means_no_conventions(self) -> None:
        conventions = parse_conventions("")
        self.assertEqual(conventions.aliases, {})
        self.assertIsNone(conventions.compass)


if __name__ == "__main__":
    unittest.main()
