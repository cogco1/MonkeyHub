"""P071: deterministic CAD translation and analytic equivalence bounds."""

import json
import unittest
from types import SimpleNamespace

from archflow.adapters.cad_program import (
    CadTranslationError,
    expected_object_bounds,
    expected_object_semantics,
    translate_to_rhino_python,
)


def op(op_id, kind, outputs, inputs=(), bindings=(), **params):
    return SimpleNamespace(
        op_id=op_id,
        kind=SimpleNamespace(value=kind),
        output_object_ids=tuple(outputs),
        input_object_ids=tuple(inputs),
        semantic_binding_ids=tuple(bindings),
        parameters=tuple(
            SimpleNamespace(name=name, value_json=json.dumps(value))
            for name, value in sorted(params.items())
        ),
    )


def binding(binding_id, component_id, object_ids, commitments=(), evidence=()):
    return SimpleNamespace(
        binding_id=binding_id,
        component_id=component_id,
        object_ids=tuple(object_ids),
        commitment_refs=tuple(commitments),
        evidence_refs=tuple(evidence),
    )


def program(*operations, bindings=()):
    return SimpleNamespace(
        proposal=SimpleNamespace(
            operations=tuple(operations),
            semantic_bindings=tuple(bindings),
        ),
        operation_order=tuple(item.op_id for item in operations),
    )


class TranslateTest(unittest.TestCase):
    def test_curve_can_be_retained_as_a_saved_inspection_witness(self):
        build = program(
            op(
                "passage-path",
                "curve",
                ["passage-path-object"],
                basis="polyline",
                points=[
                    [0.0, 0.9, 0.0],
                    [1.0, 0.9, 0.0],
                    [1.0, 0.9, -1.0],
                ],
                retain_for_inspection=True,
                hidden_for_inspection=True,
            )
        )

        translation = translate_to_rhino_python(build)

        self.assertEqual(
            ("passage-path-object",), translation.physical_object_ids
        )
        self.assertIn("rs.AddPolyline", translation.script)
        self.assertIn("archflow:object_ref", translation.script)
        self.assertIn("cad-operation:passage-path", translation.script)
        self.assertIn("rs.HideObject", translation.script)
        self.assertFalse(
            expected_object_semantics(build)["objects"]
            ["passage-path-object"]["visible"]
        )
        self.assertEqual(
            "hidden",
            expected_object_semantics(build)["objects"]
            ["passage-path-object"]["user_text"]
            ["archflow:inspection_witness"],
        )
        self.assertEqual((), translation.losses)
        self.assertEqual(
            {
                "bbox_min": [0.0, 0.9, -1.0],
                "bbox_max": [1.0, 0.9, 0.0],
                "brep_count": 1,
            },
            expected_object_bounds(build)["passage-path-object"],
        )

    def test_loft_cap_ends_is_explicit_and_backward_compatible(self):
        parameters = {
            "profile_size": 4,
            "profiles": [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.0, 0.0, 1.0],
                [0.0, 0.0, 1.0],
                [0.0, 2.0, 0.0],
                [1.0, 2.0, 0.0],
                [1.0, 2.0, 1.0],
                [0.0, 2.0, 1.0],
            ],
        }
        capped = translate_to_rhino_python(
            program(op("capped", "loft", ["capped-object"], **parameters))
        )
        open_loft = translate_to_rhino_python(
            program(
                op(
                    "open",
                    "loft",
                    ["open-object"],
                    cap_ends=False,
                    **parameters,
                )
            )
        )

        self.assertIn("rs.CapPlanarHoles(_srf[0])", capped.script)
        self.assertNotIn("rs.CapPlanarHoles(_srf[0])", open_loft.script)
        self.assertIn("rs.AddLoftSrf(_rings)", open_loft.script)

    def test_loft_type_straight_is_typed_and_invalid_values_fail(self):
        parameters = {
            "profile_size": 3,
            "profiles": [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                [0.0, 2.0, 0.0],
                [1.0, 2.0, 0.0],
                [0.0, 2.0, 1.0],
            ],
        }
        straight = translate_to_rhino_python(
            program(
                op(
                    "straight",
                    "loft",
                    ["straight-object"],
                    loft_type="straight",
                    **parameters,
                )
            )
        )

        self.assertIn("rs.AddLoftSrf(_rings, loft_type=2)", straight.script)
        with self.assertRaisesRegex(CadTranslationError, "unsupported loft_type"):
            translate_to_rhino_python(
                program(
                    op(
                        "invalid",
                        "loft",
                        ["invalid-object"],
                        loft_type="smooth-ish",
                        **parameters,
                    )
                )
            )

    def test_loft_interpolated_profile_emits_true_nurbs_curve(self):
        parameters = {
            "profile_size": 5,
            "profiles": [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [1.5, 0.0, 1.0],
                [0.5, 0.0, 1.5],
                [0.0, 0.0, 1.0],
                [0.0, 2.0, 0.0],
                [1.0, 2.0, 0.0],
                [1.5, 2.0, 1.0],
                [0.5, 2.0, 1.5],
                [0.0, 2.0, 1.0],
            ],
        }
        translation = translate_to_rhino_python(
            program(
                op(
                    "curved",
                    "loft",
                    ["curved-object"],
                    profile_basis="interpolated",
                    **parameters,
                )
            )
        )

        self.assertIn("rs.AddInterpCurve", translation.script)
        self.assertNotIn("rs.AddPolyline", translation.script)

        with self.assertRaisesRegex(CadTranslationError, "unsupported profile_basis"):
            translate_to_rhino_python(
                program(
                    op(
                        "invalid-basis",
                        "loft",
                        ["invalid-object"],
                        profile_basis="faceted-ish",
                        **parameters,
                    )
                )
            )

    def test_script_is_deterministic_and_names_physical_objects(self):
        build = program(
            op(
                "base",
                "solid",
                ["base-object"],
                origin=[0.0, 0.0, 0.0],
                size=[4.0, 2.0, 6.0],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[10.0, 0.0, 0.0],
                size=[1.0, 3.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=3,
                step=[2.0, 0.0, 0.0],
            ),
        )
        first = translate_to_rhino_python(build)
        second = translate_to_rhino_python(build)
        self.assertEqual(first.script, second.script)
        self.assertEqual(
            ("base-object", "row-object"), first.physical_object_ids
        )
        self.assertEqual((), first.losses)
        self.assertEqual(first.layer_colors, second.layer_colors)
        self.assertEqual(
            tuple(sorted(first.layer_colors)),
            first.layer_colors,
        )
        self.assertIn("rs.AddBox", first.script)
        self.assertIn("CAD_MEASURES=", first.script)

    def test_curve_and_transform_become_typed_losses(self):
        build = program(
            op(
                "guide",
                "curve",
                ["guide-object"],
                points=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[0.0, 0.0, 0.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "moved",
                "transform",
                ["moved-object"],
                ["seed-object"],
                translation=[5.0, 0.0, 0.0],
            ),
        )
        translation = translate_to_rhino_python(build)
        codes = {loss["code"] for loss in translation.losses}
        self.assertEqual(
            {"cad.curve_reference_only", "cad.transform_copy_only"},
            codes,
        )
        self.assertNotIn("guide-object", translation.physical_object_ids)


class ExpectedBoundsTest(unittest.TestCase):
    def test_solid_and_array_bounds(self):
        build = program(
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[1.0, 2.0, 3.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=4,
                step=[2.0, 0.0, 0.0],
            ),
        )
        bounds = expected_object_bounds(build)
        self.assertEqual(["row-object"], list(bounds))
        row = bounds["row-object"]
        self.assertEqual([1.0, 2.0, 3.0], row["bbox_min"])
        self.assertEqual([8.0, 3.0, 4.0], row["bbox_max"])
        self.assertEqual(4, row["brep_count"])

    def test_full_radial_ring_is_symmetric_about_center(self):
        build = program(
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[9.0, 0.0, -0.5],
                size=[1.0, 2.0, 1.0],
            ),
            op(
                "ring",
                "radial_array",
                ["ring-object"],
                ["seed-object"],
                count=4,
                center=[0.0, 0.0, 0.0],
                axis=[0.0, 1.0, 0.0],
                angle_step_degrees=90.0,
                start_angle_degrees=0.0,
            ),
        )
        ring = expected_object_bounds(build)["ring-object"]
        for axis in (0, 2):
            self.assertAlmostEqual(
                -ring["bbox_min"][axis], ring["bbox_max"][axis]
            )
        self.assertAlmostEqual(10.0, ring["bbox_max"][0])
        self.assertEqual(4, ring["brep_count"])

    def test_boolean_difference_keeps_base_bounds(self):
        build = program(
            op(
                "big",
                "solid",
                ["big-object"],
                origin=[0.0, 0.0, 0.0],
                size=[10.0, 10.0, 10.0],
            ),
            op(
                "cut",
                "solid",
                ["cut-object"],
                origin=[4.0, 4.0, 4.0],
                size=[2.0, 2.0, 2.0],
            ),
            op(
                "shell",
                "boolean_difference",
                ["shell-object"],
                ["big-object", "cut-object"],
                base_index=0,
            ),
        )
        shell = expected_object_bounds(build)["shell-object"]
        self.assertEqual([0.0, 0.0, 0.0], shell["bbox_min"])
        self.assertEqual([10.0, 10.0, 10.0], shell["bbox_max"])
        self.assertEqual(1, shell["brep_count"])

    def test_revolve_bounds_span_both_radii(self):
        build = program(
            op(
                "cone",
                "revolve",
                ["cone-object"],
                axis_start=[5.0, 0.0, 5.0],
                axis_end=[5.0, 8.0, 5.0],
                start_radius=3.0,
                end_radius=1.0,
            ),
        )
        cone = expected_object_bounds(build)["cone-object"]
        self.assertEqual([2.0, 0.0, 2.0], cone["bbox_min"])
        self.assertEqual([8.0, 8.0, 8.0], cone["bbox_max"])

    def test_revolve_bounds_are_exact_for_horizontal_axis(self):
        build = program(
            op(
                "beam",
                "revolve",
                ["beam-object"],
                axis_start=[0.0, 2.0, 3.0],
                axis_end=[10.0, 2.0, 3.0],
                start_radius=2.0,
                end_radius=1.0,
            ),
        )
        beam = expected_object_bounds(build)["beam-object"]
        self.assertEqual([0.0, 0.0, 1.0], beam["bbox_min"])
        self.assertEqual([10.0, 4.0, 5.0], beam["bbox_max"])

    def test_box_keeps_its_bounds_under_a_through_cut(self):
        # P092: a notch or a through-hole strictly inside a box on one
        # axis cannot remove a whole face, so the box's bounds survive.
        build = program(
            op(
                "base",
                "solid",
                ["base-object"],
                origin=[0.0, 0.0, 0.0],
                size=[10.0, 10.0, 10.0],
            ),
            op(
                "cut",
                "solid",
                ["cut-object"],
                origin=[4.0, 4.0, 9.0],
                size=[2.0, 2.0, 2.0],
            ),
            op(
                "difference",
                "boolean_difference",
                ["result-object"],
                ["base-object", "cut-object"],
                base_index=0,
            ),
        )
        result = expected_object_bounds(build)["result-object"]
        self.assertEqual([0.0, 0.0, 0.0], result["bbox_min"])
        self.assertEqual([10.0, 10.0, 10.0], result["bbox_max"])

    def test_boolean_difference_that_can_change_extrema_fails_closed(self):
        # a triangular prism holds its +Z extremum along one edge: a cutter
        # reaching that edge can alter the extremum, so bounds fail closed
        build = program(
            op(
                "base",
                "extrusion",
                ["base-object"],
                profile=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 0.0, 10.0]],
                vector=[0.0, 10.0, 0.0],
            ),
            op(
                "cut",
                "solid",
                ["cut-object"],
                origin=[4.0, 4.0, 9.0],
                size=[2.0, 2.0, 2.0],
            ),
            op(
                "difference",
                "boolean_difference",
                ["result-object"],
                ["base-object", "cut-object"],
                base_index=0,
            ),
        )
        with self.assertRaisesRegex(
            CadTranslationError,
            "can alter a base extremum",
        ):
            expected_object_bounds(build)

    def test_bounds_cover_exactly_the_physical_set(self):
        build = program(
            op(
                "guide",
                "curve",
                ["guide-object"],
                points=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[0.0, 0.0, 0.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=2,
                step=[0.0, 0.0, 3.0],
            ),
        )
        translation = translate_to_rhino_python(build)
        bounds = expected_object_bounds(build)
        self.assertEqual(
            sorted(translation.physical_object_ids), sorted(bounds)
        )


def semantic_build():
    return program(
        op(
            "seed",
            "solid",
            ["seed-object"],
            bindings=["ring-binding"],
            origin=[9.0, 0.0, -0.5],
            size=[1.0, 2.0, 1.0],
        ),
        op(
            "ring",
            "radial_array",
            ["ring-object"],
            ["seed-object"],
            bindings=["ring-binding"],
            count=6,
            center=[0.0, 0.0, 0.0],
            axis=[0.0, 1.0, 0.0],
            angle_step_degrees=60.0,
            start_angle_degrees=0.0,
        ),
        op(
            "slab",
            "solid",
            ["slab-object"],
            origin=[-12.0, -1.0, -12.0],
            size=[24.0, 1.0, 24.0],
        ),
        bindings=[
            binding(
                "ring-binding",
                "colonnade",
                ["ring-object", "seed-object"],
                commitments=["commitment:preserve-envelope"],
                evidence=["brief-claim:claim.occupancy"],
            )
        ],
    )


class SemanticEmissionTest(unittest.TestCase):
    def test_expected_semantics_come_from_bindings_only(self):
        semantics = expected_object_semantics(semantic_build())
        ring = semantics["objects"]["ring-object"]
        self.assertEqual("ring-object", ring["name"])
        self.assertEqual("archflow::colonnade", ring["layer"])
        self.assertEqual(
            {
                "archflow:producer_op": "ring",
                "archflow:object_ref": "cad-object:ring-object",
                "archflow:operation_ref": "cad-operation:ring",
                "archflow:bindings": "ring-binding",
                "archflow:component": "colonnade",
                "archflow:commitments": "commitment:preserve-envelope",
                "archflow:evidence": "brief-claim:claim.occupancy",
            },
            ring["user_text"],
        )
        self.assertEqual({"archflow-family-ring": 6}, semantics["blocks"])

    def test_unbound_object_stays_on_root_layer_without_invention(self):
        semantics = expected_object_semantics(semantic_build())
        slab = semantics["objects"]["slab-object"]
        self.assertEqual("archflow", slab["layer"])
        self.assertEqual(
            {
                "archflow:producer_op": "slab",
                "archflow:object_ref": "cad-object:slab-object",
                "archflow:operation_ref": "cad-operation:slab",
            },
            slab["user_text"],
        )

    def test_script_emits_native_semantic_carriers(self):
        translation = translate_to_rhino_python(
            semantic_build(),
            provenance={"proposal_id": "prop-1"},
        )
        script = translation.script
        self.assertIn("rs.AddLayer('archflow::colonnade'", script)
        self.assertIn(
            "rs.AddBlock(_seed, (0.0,0.0,0.0), 'archflow-family-ring'",
            script,
        )
        self.assertIn("rs.InsertBlock('archflow-family-ring'", script)
        self.assertIn("rs.ObjectName(_g, _oid)", script)
        self.assertIn("rs.SetUserText", script)
        self.assertIn(
            "rs.SetDocumentUserText('archflow:proposal_id', 'prop-1')",
            script,
        )
        self.assertIn("SEMANTICS=", script)

    def test_semantic_translation_is_deterministic(self):
        first = translate_to_rhino_python(semantic_build())
        second = translate_to_rhino_python(semantic_build())
        self.assertEqual(first.script, second.script)
        self.assertEqual(first.layer_colors, second.layer_colors)

    def test_layer_color_contract_drives_add_layer_material_override(self):
        translation = translate_to_rhino_python(
            semantic_build(),
            material_by_component={"colonnade": "limestone"},
            material_colors={"limestone": (11, 22, 33)},
        )
        colors = dict(translation.layer_colors)
        self.assertEqual(
            ("archflow", "archflow::colonnade"),
            tuple(layer for layer, _ in translation.layer_colors),
        )
        self.assertEqual((11, 22, 33), colors["archflow::colonnade"])
        for layer_path, color in translation.layer_colors:
            self.assertIn(
                f"rs.AddLayer({layer_path!r}, {color!r})",
                translation.script,
            )

    def test_layer_color_contract_has_deterministic_fallback(self):
        default = translate_to_rhino_python(semantic_build())
        missing_material_color = translate_to_rhino_python(
            semantic_build(),
            material_by_component={"colonnade": "limestone"},
            material_colors={},
        )
        self.assertEqual(default.layer_colors, missing_material_color.layer_colors)
        self.assertIn("archflow", dict(default.layer_colors))




class LayerSchemeTests(unittest.TestCase):
    def test_a_caller_supplied_scheme_renames_the_category_and_keeps_identity(self) -> None:
        """P108 numbered layers: <category>::<component>; unmapped components stay on the historical path."""

        from archflow.adapters.cad_program import _component_layer, expected_object_semantics

        self.assertEqual(_component_layer((), None), "archflow")
        self.assertEqual(_component_layer(("portico-columns",), None), "archflow::portico-columns")
        scheme = {"portico-columns": "20_STRUCTURE", "exterior-walls": "30_ENVELOPE"}
        self.assertEqual(_component_layer(("portico-columns",), scheme), "20_STRUCTURE::portico-columns")
        self.assertEqual(_component_layer(("landscape",), scheme), "archflow::landscape")               # never guessed into a bucket
        self.assertEqual(_component_layer(("exterior-walls", "portico-columns"), scheme), "archflow::exterior-walls+portico-columns")  # two categories: no single answer, historical path
        with self.assertRaises(ValueError):
            _component_layer(("portico-columns",), {"portico-columns": "20::STRUCTURE"})


BASIS = ("reading:plate",)


def _reference_context():
    from archflow.capabilities.reference_resolver import ReferenceContext
    from archflow.state.geometry_program import (
        ProjectGridAxis,
        ProjectGrids,
        ProjectLevel,
        ProjectLevels,
    )

    return ReferenceContext(
        grids=ProjectGrids(
            project_id="demo",
            published_by="seat-coordination",
            axes=(ProjectGridAxis("axis-w", "W", (0.0, 0.0, -13.85), (1.0, 0.0, 0.0), BASIS),),
        ),
        levels=ProjectLevels(
            project_id="demo",
            published_by="seat-coordination",
            levels=(ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS),),
        ),
    )


def _produced_program(*rows):
    """The real producers' operations, wrapped as the program the translator reads."""

    from archflow.capabilities.element_producers import ProductionContext, produce_rows

    context = ProductionContext(references=_reference_context(), published={})
    operations = [
        operation
        for element in produce_rows(rows, context)
        for operation in element.operations
    ]
    return program(
        *operations,
        bindings=tuple(
            binding(row.binding_id, row.component_id, [f"obj-{row.element_id}"])
            for row in rows
        ),
    )


def _wedge_row(**params):
    from archflow.capabilities.element_producers import ElementRow

    return ElementRow(
        "abutment-north",
        "roof-abutments",
        "wedge",
        {
            "from": {"axis_point": {"axis": "W", "along": 0.0}},
            "to": {"axis_point": {"axis": "W", "along": 4.0}},
            "base": {"level": "level-ground"},
        },
        {"depth": 2.0, "low": 0.5, "high": 2.5, **params},
        BASIS,
    )


def _shell_row(**params):
    from archflow.capabilities.element_producers import ElementRow

    return ElementRow(
        "rotunda-shell",
        "rotunda-wall",
        "shell",
        {
            "at": {"axis_point": {"axis": "W", "along": 0.0}},
            "base": {"level": "level-ground"},
        },
        {
            "outer_radius": 5.0,
            "thickness": 0.6,
            "height": 4.0,
            "kind": "cylinder",
            "segments": 8,
            **params,
        },
        BASIS,
    )


def _prism_row():
    from archflow.capabilities.element_producers import ElementRow

    return ElementRow(
        "plinth",
        "plinth-block",
        "prism",
        {
            "at": {"axis_point": {"axis": "W", "along": 0.0}},
            "base": {"level": "level-ground"},
        },
        {"profile": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], "height": 0.4},
        BASIS,
    )


class ProducerParameterUserTextTests(unittest.TestCase):
    """W3-B: what a saved solid cannot show about itself, the object says in user text.

    A wedge's slope and a shell's wall are not in the bounding box, and the
    mirrored wedge leaves the box unchanged, so the re-index reads them off
    ``archflow:wedge_*`` / ``archflow:shell_*`` instead. The values here are
    the row's own numbers, carried through the real producers - the
    translator reads the operation's parameters and never measures geometry.
    """

    def _user_text(self, *rows):
        semantics = expected_object_semantics(_produced_program(*rows))
        return {
            object_id: row["user_text"]
            for object_id, row in semantics["objects"].items()
        }

    def test_a_wedge_carries_its_low_high_axis_and_sense_and_nothing_more(self):
        text = self._user_text(_wedge_row())

        self.assertEqual(
            text["obj-abutment-north"],
            {
                "archflow:producer_op": "abutment-north",
                "archflow:object_ref": "cad-object:obj-abutment-north",
                "archflow:operation_ref": "cad-operation:abutment-north",
                "archflow:bindings": "binding-roof-abutments",
                "archflow:component": "roof-abutments",
                "archflow:wedge_low": "0.5",
                "archflow:wedge_high": "2.5",
                "archflow:wedge_axis": "along",
                "archflow:wedge_sense": "from",
            },
        )

    def test_a_wedge_sloping_across_its_run_says_across(self):
        text = self._user_text(_wedge_row(slope_across=True))

        self.assertEqual(text["obj-abutment-north"]["archflow:wedge_axis"], "across")
        self.assertEqual(text["obj-abutment-north"]["archflow:wedge_sense"], "from")

    def test_a_shell_carries_its_thickness_and_kind(self):
        cylinder = self._user_text(_shell_row())["obj-rotunda-shell"]
        dome = self._user_text(_shell_row(kind="dome", rings=4))["obj-rotunda-shell"]

        self.assertEqual(
            cylinder,
            {
                "archflow:producer_op": "rotunda-shell",
                "archflow:object_ref": "cad-object:obj-rotunda-shell",
                "archflow:operation_ref": "cad-operation:rotunda-shell",
                "archflow:bindings": "binding-rotunda-wall",
                "archflow:component": "rotunda-wall",
                "archflow:shell_thickness": "0.6",
                "archflow:shell_kind": "cylinder",
            },
        )
        self.assertEqual(dome["archflow:shell_kind"], "dome")
        self.assertEqual(dome["archflow:shell_thickness"], "0.6")

    def test_a_prism_carries_none_of_them(self):
        text = self._user_text(_prism_row())

        self.assertEqual(
            sorted(text["obj-plinth"]),
            [
                "archflow:bindings",
                "archflow:component",
                "archflow:object_ref",
                "archflow:operation_ref",
                "archflow:producer_op",
            ],
        )

    def test_the_strings_travel_into_the_build_script(self):
        script = translate_to_rhino_python(
            _produced_program(_wedge_row(), _shell_row(), _prism_row())
        ).script

        for key, value in (
            ("archflow:wedge_low", "0.5"),
            ("archflow:wedge_high", "2.5"),
            ("archflow:wedge_axis", "along"),
            ("archflow:wedge_sense", "from"),
            ("archflow:shell_thickness", "0.6"),
            ("archflow:shell_kind", "cylinder"),
        ):
            self.assertIn(f'"{key}": "{value}"', script)

    def test_metres_are_canonical_decimal_text_not_a_locale_or_a_python_object(self):
        """A stated length reparses to the same float; a value the map cannot carry fails closed."""

        text = self._user_text(_wedge_row(low=0.0, high=1.0 / 3.0))["obj-abutment-north"]
        self.assertEqual(text["archflow:wedge_low"], "0.0")
        self.assertEqual(float(text["archflow:wedge_high"]), round(1.0 / 3.0, 9))
        self.assertNotIn(",", text["archflow:wedge_high"])

        with self.assertRaises(CadTranslationError):
            expected_object_semantics(
                program(
                    op(
                        "bad-shell",
                        "extrusion",
                        ["obj-bad-shell"],
                        profile=[[0.0, 0.0, 0.0]],
                        shell_kind=["cylinder"],
                        vector=[0.0, 1.0, 0.0],
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
