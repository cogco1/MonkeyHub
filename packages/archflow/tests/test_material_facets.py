"""A material's base colour belongs to the design state (#560): ``material.color`` beside ``material.name``.

The colour is an sRGB ``#RRGGBB`` written in either case and kept in upper
case. The State Record refuses a colour without a material name, any other
form, and two colours for one material, naming the components; a record
without colours, or without facets, stays valid. ``declared_materials`` is
what an export reads: each declaring component's material by id, each part's
material where a part states its own (#580), and each material's declared
colour as channels.
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
from archflow.state.state_record import (
    Entity,
    StateRecord,
    StateRecordError,
    component_part_facets,
    declared_materials,
    part_of_object,
)


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
            ({"c0": "timber", "c1": "timber", "c2": "hemp-lime", "c3": "glass"}, {},
             {"timber": (139, 90, 43), "hemp-lime": (139, 90, 43)}),
        )

    def test_records_without_colours_or_facets_stay_valid_and_declare_nothing(self) -> None:
        self.assertEqual(declared_materials(_record(None, {}, {"architectural.role": "wall"})), ({}, {}, {}))
        self.assertEqual(declared_materials(_record({"material.name": "brick"})), ({"c0": "brick"}, {}, {}))

    def test_an_edit_that_would_give_a_material_a_second_colour_is_refused(self) -> None:
        record = _record({"material.name": "timber", "material.color": "#8B5A2B"}, {"material.name": "oak"})
        renamed = replace(record.entity("c1"), fields={**record.entity("c1").fields,
                                                         "facets": {"material.name": "timber", "material.color": "#000000"}})
        with self.assertRaises(StateRecordError):
            replace(record, entities=(record.entity("c0"), renamed))


def _parted(**declared: dict) -> StateRecord:
    """The two mixed components of #580: a plinth of brick and block, a floor finish inside and paving outside.

    ``declared`` gives a component's ``facets`` (key ``<component>``) or its
    ``part_facets`` (key ``<component>_parts``).
    """

    parts = {"plinth": ("plinth-outer-brick", "plinth-inner-block"),
             "floor_finish": ("floor-porcelain-1", "floor-porcelain-2", "external-paving")}
    entities = []
    for component, elements in parts.items():
        fields: dict[str, object] = {"intent": component}
        if component in declared:
            fields["facets"] = declared[component]
        if f"{component}_parts" in declared:
            fields["part_facets"] = declared[f"{component}_parts"]
        entities.append(Entity(component, "Component@1", fields))
        entities.extend(Entity(element, "Element@1", {"producer": "prism", "component_id": component}, parent_id=component)
                        for element in elements)
    return StateRecord(project_id="demo", run_id="run-1", entities=tuple(entities))


class PartMaterialRecordTests(unittest.TestCase):
    """Part-level materials (#580 gap 2): a part's own material, else its component's, else none."""

    def test_two_parts_of_one_component_declare_different_materials(self) -> None:
        record = _parted(plinth_parts={"plinth-outer-brick": {"material.name": "Brick", "material.color": "#9E4B32"},
                                       "plinth-inner-block": {"material.name": "Concrete block"}})
        reloaded = StateRecord.from_dict(record.to_dict())
        self.assertEqual(reloaded.digest, record.digest)
        self.assertEqual(component_part_facets(reloaded.entity("plinth")),
                         {"plinth-outer-brick": {"material.name": "Brick", "material.color": "#9E4B32"},
                          "plinth-inner-block": {"material.name": "Concrete block"}})
        declared = declared_materials(record)
        self.assertEqual(declared.by_component, {})
        self.assertEqual(declared.by_part, {"plinth": {"plinth-outer-brick": "Brick", "plinth-inner-block": "Concrete block"}})
        self.assertEqual(declared.colors, {"Brick": (158, 75, 50)})

    def test_a_part_overrides_its_components_material_and_the_other_parts_keep_it(self) -> None:
        record = _parted(plinth={"material.name": "Brick", "material.color": "#9E4B32"},
                         plinth_parts={"plinth-inner-block": {"material.name": "Concrete block", "material.color": "#B4B2AA"}})
        by_component, by_part, colors = declared_materials(record)
        self.assertEqual(by_component, {"plinth": "Brick"})
        self.assertEqual(by_part, {"plinth": {"plinth-outer-brick": "Brick", "plinth-inner-block": "Concrete block"}})
        self.assertEqual(colors, {"Brick": (158, 75, 50), "Concrete block": (180, 178, 170)})

    def test_one_part_declared_leaves_the_others_of_an_undeclared_component_undeclared(self) -> None:
        record = _parted(floor_finish_parts={"floor-porcelain-1": {"material.name": "Porcelain tile"},
                                             "floor-porcelain-2": {"material.name": "Porcelain tile"}})
        declared = declared_materials(record)
        self.assertEqual(declared.by_component, {})
        self.assertEqual(declared.by_part, {"floor_finish": {"floor-porcelain-1": "Porcelain tile",
                                                             "floor-porcelain-2": "Porcelain tile",
                                                             "external-paving": None}})
        # a component none of whose parts states a material is not listed by part at all
        self.assertNotIn("plinth", declared.by_part)

    def test_a_part_its_component_does_not_have_is_refused_naming_its_parts(self) -> None:
        with self.assertRaises(StateRecordError) as raised:
            _parted(plinth_parts={"plinth-footing": {"material.name": "Concrete"}})
        message = str(raised.exception)
        self.assertIn("component plinth", message)
        self.assertIn("'plinth-footing'", message)
        self.assertIn("plinth-outer-brick, plinth-inner-block", message)
        # another component's part is not this component's either
        with self.assertRaises(StateRecordError) as raised:
            _parted(plinth_parts={"external-paving": {"material.name": "Concrete"}})
        self.assertIn("external-paving is a part of floor_finish", str(raised.exception))

    def test_a_part_takes_material_facets_only_and_states_at_least_one(self) -> None:
        for parts, said in (
            ({"plinth-inner-block": {"architectural.role": "wall"}}, "a part takes material.name and material.color"),
            ({"plinth-inner-block": {}}, "states no facet"),
            ({"plinth-inner-block": {"material.name": ""}}, "material.name must be 1-120 characters"),
            ({"plinth-inner-block": {"material.name": "Block", "material.color": "#b4b2aa"}}, "write it #B4B2AA"),
            ({"plinth-inner-block": {"material.color": "#B4B2AA"}}, "names no material"),
            (["plinth-inner-block"], "part_facets must map"),
        ):
            with self.subTest(parts=parts), self.assertRaises(StateRecordError) as raised:
                _parted(plinth_parts=parts)
            self.assertIn(said, str(raised.exception))
            self.assertIn("plinth", str(raised.exception))

    def test_one_material_keeps_one_colour_across_parts_and_components(self) -> None:
        # a part's colour for a material a component colours otherwise
        with self.assertRaises(StateRecordError) as raised:
            _parted(plinth={"material.name": "Brick", "material.color": "#9E4B32"},
                    floor_finish_parts={"external-paving": {"material.name": "Brick", "material.color": "#000000"}})
        message = str(raised.exception)
        self.assertIn("'Brick'", message)
        self.assertIn("#000000 on part external-paving of floor_finish", message)
        self.assertIn("#9E4B32 on plinth", message)
        # two parts colouring one material differently
        with self.assertRaises(StateRecordError) as raised:
            _parted(plinth_parts={"plinth-outer-brick": {"material.name": "Brick", "material.color": "#9E4B32"},
                                  "plinth-inner-block": {"material.name": "Brick", "material.color": "#9E4B33"}})
        self.assertIn("more than one material.color", str(raised.exception))
        # the same colour, or none, is one material
        record = _parted(plinth={"material.name": "Brick", "material.color": "#9E4B32"},
                         floor_finish_parts={"external-paving": {"material.name": "Brick"}})
        self.assertEqual(declared_materials(record).colors, {"Brick": (158, 75, 50)})

    def test_an_object_belongs_to_the_part_whose_name_it_carries(self) -> None:
        parts = ("portico", "portico-base", "plinth-inner-block")
        for name, part in (("obj-portico", "portico"), ("obj-portico-base", "portico-base"),
                           ("obj-portico-base-0", "portico-base"), ("obj-portico-cornice", "portico"),
                           ("obj-plinth-inner-block", "plinth-inner-block"), ("obj-plinth", None),
                           ("portico-base", None), ("imported-portico", None), ("", None), (None, None)):
            with self.subTest(name=name):
                self.assertEqual(part_of_object(name, parts), part)


if __name__ == "__main__":
    unittest.main()
