"""A material's base colour belongs to the design state (#560): ``material.color`` beside ``material.name``.

The colour is an sRGB ``#RRGGBB`` written in either case and kept in upper
case. The State Record refuses a colour without a material name, any other
form, and two colours for one material, naming the components; a record
without colours, or without facets, stays valid. ``declared_materials`` is
what an export reads: each declaring component's material by id, and each
material's declared colour as channels.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.semantics.facets import (
    FACET_KEYS,
    FACET_REASONS,
    canonical_facet_value,
    facet_format,
    material_color_rgb,
    suggest_facet_key,
)
from archflow.state.state_record import Entity, StateRecord, StateRecordError, declared_materials


def _record(*facets: dict | None) -> StateRecord:
    """One Component@1 per argument, ``c0``, ``c1``, ...; None leaves that component without facets."""

    entities = []
    for index, value in enumerate(facets):
        fields: dict[str, object] = {"intent": f"part {index}"}
        if value is not None:
            fields["facets"] = value
        entities.append(Entity(f"c{index}", "Component@1", fields))
    return StateRecord(project_id="demo", run_id="run-1", entities=tuple(entities))


class MaterialColorFacetTests(unittest.TestCase):
    def test_the_key_is_registered_with_its_reason_and_its_form(self) -> None:
        self.assertIn("material.color", FACET_KEYS)
        self.assertIn("material.name names what a material is, not how it looks", FACET_REASONS["material.color"])
        form = facet_format("material.color")
        self.assertIsNotNone(form)
        self.assertTrue(form.holds("#A0522D"))
        for other in ("#a0522d", "A0522D", "#A0522", "#A0522DD", "#GGGGGG", "red", " #A0522D"):
            self.assertFalse(form.holds(other), other)
        self.assertIsNone(facet_format("material.name"))
        self.assertIn("material.color", suggest_facet_key("material.colour"))

    def test_the_canonical_spelling_upper_cases_a_colour_and_guesses_nothing(self) -> None:
        self.assertEqual(canonical_facet_value("material.color", " #a0522d "), "#A0522D")
        self.assertEqual(canonical_facet_value("material.color", "#A0522D"), "#A0522D")
        # anything that is not already a colour comes back as written, for the record to refuse
        for written in ("red", "a0522d", "#abc", "rgb(160, 82, 45)"):
            self.assertEqual(canonical_facet_value("material.color", f" {written} "), written)
        # no other key is re-cased
        self.assertEqual(canonical_facet_value("material.name", "  Lime Plaster "), "Lime Plaster")

    def test_the_channels_are_read_from_the_stored_colour_only(self) -> None:
        self.assertEqual(material_color_rgb("#A0522D"), (160, 82, 45))
        self.assertEqual(material_color_rgb("#000000"), (0, 0, 0))
        for invalid in ("#a0522d", "A0522D", "red"):
            with self.assertRaises(ValueError):
                material_color_rgb(invalid)


class MaterialColorRecordTests(unittest.TestCase):
    def test_a_colour_with_its_material_is_accepted_and_round_trips(self) -> None:
        record = _record({"material.name": "hemp-lime", "material.color": "#C8B98F"})
        reloaded = StateRecord.from_dict(record.to_dict())
        self.assertEqual(reloaded.entity("c0").fields["facets"], {"material.name": "hemp-lime", "material.color": "#C8B98F"})
        self.assertEqual(reloaded.digest, record.digest)

    def test_another_form_is_refused_and_a_well_formed_one_is_named_in_its_spelling(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _record({"material.name": "hemp-lime", "material.color": "#c8b98f"})
        self.assertIn("component c0", str(raised.exception))
        self.assertIn("write it #C8B98F", str(raised.exception))
        for invalid in ("beige", "#C8B98", "C8B98F", "#C8B98FF", "rgb(200,185,143)"):
            with self.subTest(value=invalid), self.assertRaises(StateRecordError) as raised:
                _record({"material.name": "hemp-lime", "material.color": invalid})
            self.assertIn("an sRGB hex colour #RRGGBB", str(raised.exception))
            self.assertNotIn("write it", str(raised.exception))

    def test_a_colour_without_a_material_name_is_refused(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _record({"material.name": "timber"}, {"material.color": "#8B5A2B"})
        message = str(raised.exception)
        self.assertIn("component c1", message)
        self.assertIn("names no material", message)
        self.assertIn("material.name", message)

    def test_two_colours_for_one_material_are_refused_naming_the_components(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _record(
                {"material.name": "timber", "material.color": "#8B5A2B"},
                {"material.name": "timber", "material.color": "#8B5A2B"},
                {"material.name": "timber", "material.color": "#3B2A1A"},
                {"material.name": "hemp-lime", "material.color": "#3B2A1A"},
            )
        message = str(raised.exception)
        self.assertIn("'timber'", message)
        self.assertIn("#3B2A1A on c2", message)
        self.assertIn("#8B5A2B on c0, c1", message)
        self.assertNotIn("c3", message)

    def test_one_material_carries_one_colour_or_none_and_other_materials_their_own(self) -> None:
        record = _record(
            {"material.name": "timber", "material.color": "#8B5A2B"},
            {"material.name": "timber"},
            {"material.name": "hemp-lime", "material.color": "#8B5A2B"},
            {"material.name": "glass"},
            {"architectural.role": "column"},
            None,
        )
        self.assertEqual(
            declared_materials(record),
            ({"c0": "timber", "c1": "timber", "c2": "hemp-lime", "c3": "glass"},
             {"timber": (139, 90, 43), "hemp-lime": (139, 90, 43)}),
        )

    def test_records_without_colours_or_facets_stay_valid_and_declare_nothing(self) -> None:
        self.assertEqual(declared_materials(_record(None, {}, {"architectural.role": "wall"})), ({}, {}))
        self.assertEqual(declared_materials(_record({"material.name": "brick"})), ({"c0": "brick"}, {}))

    def test_an_edit_that_would_give_a_material_a_second_colour_is_refused(self) -> None:
        record = _record({"material.name": "timber", "material.color": "#8B5A2B"}, {"material.name": "oak"})
        renamed = replace(record.entity("c1"), fields={**record.entity("c1").fields,
                                                         "facets": {"material.name": "timber", "material.color": "#000000"}})
        with self.assertRaises(StateRecordError):
            replace(record, entities=(record.entity("c0"), renamed))


if __name__ == "__main__":
    unittest.main()
