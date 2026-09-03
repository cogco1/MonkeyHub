from __future__ import annotations

from dataclasses import replace
import json
import unittest

from archive.archflow.capabilities.geometry_producer import (
    GeometryProducerError,
    ProducerContext,
    YUpPlacement,
    merge_produced_assemblies,
)
from archive.archflow.capabilities.portico_geometry import (
    PorticoColumnArraySpec,
    PorticoColumnSection,
    PorticoComponentIds,
    PorticoDetailCourse,
    PorticoEntablatureSpec,
    PorticoGeometryError,
    PorticoGeometrySpec,
    PorticoLandingSpec,
    PorticoMaturity,
    PorticoOperationIdentity,
    PorticoPedimentRoofSpec,
    PorticoTreadSpec,
    produce_portico_geometry,
)
from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import ProjectVersionRef
from archflow.state.geometry_program import LengthUnit


BASE_SHA = "2aa733c5fb565428a4c08aed1d278135d571bded474b64f89b6039a6ee44f266"
STATE_SHA = "878b390f0545974e68565e6d88f80e19f78944f54df8a0dc3de1ddade78ef747"
STAGE_1_PORTICO_OPERATIONS_SHA = (
    "95657726c3a7351b4f8eeabbdf219de4dbbbb3e1fb6a3340d5b78659bf09b4c5"
)
STAGE_2_PORTICO_PRODUCER_SHA = (
    "85986d71695fc0334f005e76a863a6d4d39ae27d2dd23e4d2dc489c4d8be176f"
)
STAGE_3_PORTICO_OPERATIONS_SHA = (
    "243b7fc08969c17c61f4144d052d8782b276f2317dc1bb1a5ece78cda8526d65"
)
STAGE_4_PORTICO_OPERATIONS_SHA = (
    "b37e9598dea7435990a3d18c1a5f69ad91918e01e2af95a51a9353072b2eed3c"
)
VILLA_FACADES = ("north", "east", "south", "west")


def _context() -> ProducerContext:
    return ProducerContext(
        project_id="villa-rotonda-reconstruction",
        run_id="reconstruction-012",
        base=ProjectVersionRef(
            project_id="villa-rotonda-reconstruction",
            version=0,
            state_sha256=BASE_SHA,
        ),
        design_state_digest=STATE_SHA,
        frame_id="world",
        length_unit=LengthUnit.METER,
        commitment_refs=("commitment:as-built-current-massing",),
        evidence_refs=(
            "evidence:human-authorized-soft-proxy-20260831",
            "evidence:official-villa-page-ab23feef",
        ),
    )


def _components() -> PorticoComponentIds:
    return PorticoComponentIds(
        portico="porticos",
        stair_reservation="exterior-stair-reservations",
        columns="portico-columns",
        capitals="portico-capitals",
        entablature="portico-entablature",
        entablature_detail="portico-entablature-detail",
        pediment="portico-pediments",
        roof="portico-roofs",
        roof_abutment="portico-roof-abutments",
        stairs="exterior-stairs",
        landing="portico-landings",
        landing_supports="stair-support-assemblies",
    )


def _treads() -> tuple[PorticoTreadSpec, ...]:
    stair_going = 7.854
    service_top = 3.57
    risers = 23
    tread_depth = stair_going / risers
    riser_height = service_top / risers
    return tuple(
        PorticoTreadSpec(
            ordinal=ordinal,
            local_run_start=ordinal * tread_depth,
            run_depth=tread_depth,
            width=10.71,
            walking_datum=(ordinal + 1) * riser_height,
        )
        for ordinal in range(risers - 1)
    )


def _villa_spec(
    facade_id: str,
    side_index: int,
    maturity: PorticoMaturity,
) -> PorticoGeometrySpec:
    column_height = 6.426
    treads = _treads()
    return PorticoGeometrySpec(
        assembly_id=f"portico-{facade_id}",
        identity=PorticoOperationIdentity(
            facade_id=facade_id,
            massing_operation_id=f"portico-envelope-{side_index}",
            reservation_operation_id=f"portico-reservation-{side_index}",
            stair_reservation_operation_id=f"stair-reservation-{side_index}",
        ),
        placement=YUpPlacement(
            origin_x=0.0,
            origin_y=0.0,
            origin_z=0.0,
            rotation_degrees=float(side_index * 90),
        ),
        maturity=maturity,
        components=_components(),
        host_face_offset=21.42 / 2.0,
        width=10.71,
        depth=4.284,
        service_top=3.57,
        stair_envelope_going=7.854,
        stair_run_going=sum(tread.run_depth for tread in treads),
        columns=PorticoColumnArraySpec(
            count=6,
            center_spacing=2.25 * 0.714,
            front_inset=0.62,
            radial_segments=24,
            sections=tuple(
                PorticoColumnSection(height_offset=height, radius=radius)
                for height, radius in (
                    (0.00, 0.49),
                    (0.12, 0.52),
                    (0.22, 0.46),
                    (0.34, 0.40),
                    (0.46, 0.36),
                    (0.64, 0.34),
                    (column_height - 0.52, 0.29),
                    (column_height - 0.34, 0.32),
                    (column_height - 0.22, 0.42),
                    (column_height - 0.10, 0.50),
                    (column_height, 0.54),
                )
            ),
            abacus_half_extent=0.56,
            abacus_bottom_overlap=0.02,
            abacus_thickness=0.18,
        ),
        entablature=PorticoEntablatureSpec(
            height=1.33875,
            front_width_extra=0.72,
            front_depth=0.92,
            return_center_extra=0.18,
            return_width=0.48,
            return_depth_extra=0.45,
            detail_front_offset=0.50,
            detail_depth=0.16,
            detail_courses=tuple(
                PorticoDetailCourse(
                    bottom_offset=bottom_offset,
                    height=height,
                    width_extra=width_extra,
                )
                for bottom_offset, height, width_extra in (
                    (0.00, 0.28, 0.10),
                    (0.31, 0.24, 0.18),
                    (0.61, 0.37, 0.10),
                    (1.03, 0.25, 0.22),
                )
            ),
        ),
        pediment_roof=PorticoPedimentRoofSpec(
            pediment_front_extra=0.10,
            pediment_half_width_extra=0.22,
            pediment_rise=1.78,
            pediment_thickness=0.42,
            rear_inset=0.52,
            surface_vertical_inset=0.015,
            rear_rise=0.58,
            roof_thickness=0.20,
            abutment_depth=0.56,
        ),
        landing=PorticoLandingSpec(
            slab_thickness=0.24,
            support_side_inset=0.58,
            support_face_offset=0.62,
            support_width=0.72,
            support_depth=0.72,
            support_top_clearance=0.20,
            tread_overlap=0.012,
        ),
        treads=treads,
        knowledge_refs=(
            "evidence:human-authorized-soft-proxy-20260831",
            "evidence:official-villa-page-ab23feef",
        ),
        interface_refs=("interface:portico-to-main-block",),
        obligation_refs=("obligation:portico-stair-continuity",),
    )


def _bundle(maturity: PorticoMaturity):
    context = _context()
    assemblies = tuple(
        produce_portico_geometry(
            context,
            _villa_spec(facade_id, side_index, maturity),
        )
        for side_index, facade_id in enumerate(VILLA_FACADES)
    )
    return merge_produced_assemblies(context, assemblies)


class PorticoGeometryTests(unittest.TestCase):
    def test_portico_accepts_project_owned_non_cardinal_placement(self) -> None:
        context = _context()
        spec = _villa_spec("north", 0, PorticoMaturity.MASSING)
        placement = YUpPlacement(
            origin_x=4.5,
            origin_y=1.25,
            origin_z=-3.0,
            rotation_degrees=37.0,
        )
        spec = replace(
            spec,
            assembly_id="portico-garden",
            identity=PorticoOperationIdentity(
                facade_id="garden",
                massing_operation_id="garden-portico-envelope",
                reservation_operation_id="garden-portico-reservation",
                stair_reservation_operation_id="garden-stair-reservation",
            ),
            placement=placement,
            host_face_offset=0.0,
        )
        assembly = produce_portico_geometry(context, spec)
        envelope = next(
            item
            for item in assembly.operations
            if item.op_id == "garden-portico-envelope"
        )
        profile = next(
            item for item in envelope.parameters if item.name == "profile"
        )
        points = json.loads(profile.value_json)
        self.assertEqual(
            points[0],
            placement.transform_point(
                [-spec.width / 2.0, spec.service_top, 0.0]
            ),
        )

    def test_stage_1_and_2_keep_only_explicit_reservations(self) -> None:
        for maturity, prefix, digest in (
            (
                PorticoMaturity.MASSING,
                "portico-envelope",
                STAGE_1_PORTICO_OPERATIONS_SHA,
            ),
            (
                PorticoMaturity.RESERVATION,
                "portico-reservation",
                STAGE_2_PORTICO_PRODUCER_SHA,
            ),
        ):
            with self.subTest(maturity=maturity):
                bundle = _bundle(maturity)
                self.assertEqual(len(bundle.operations), 8)
                self.assertEqual(
                    {item.op_id for item in bundle.operations},
                    {
                        *(f"{prefix}-{index}" for index in range(4)),
                        *(f"stair-reservation-{index}" for index in range(4)),
                    },
                )
                self.assertEqual(len(bundle.semantic_bindings), 2)
                # The historical Stage 2 compiler later added
                # responds_to_binding_ids for exact-base continuity. That
                # lifecycle annotation is intentionally outside this pure
                # geometry producer; all geometric operation payloads match
                # after that post-producer field is normalized to empty.
                self.assertEqual(
                    canonical_digest(
                        [operation.to_dict() for operation in bundle.operations]
                    ),
                    digest,
                )

    def test_stage_3_and_4_match_historical_operation_goldens(self) -> None:
        expected = {
            PorticoMaturity.DEVELOPED: (
                184,
                STAGE_3_PORTICO_OPERATIONS_SHA,
            ),
            PorticoMaturity.DETAILED: (
                200,
                STAGE_4_PORTICO_OPERATIONS_SHA,
            ),
        }
        for maturity, (count, digest) in expected.items():
            with self.subTest(maturity=maturity):
                bundle = _bundle(maturity)
                self.assertEqual(len(bundle.operations), count)
                self.assertEqual(
                    canonical_digest(
                        [operation.to_dict() for operation in bundle.operations]
                    ),
                    digest,
                )

    def test_stage_4_adds_only_four_detail_courses_per_side(self) -> None:
        stage_3 = _bundle(PorticoMaturity.DEVELOPED)
        stage_4 = _bundle(PorticoMaturity.DETAILED)
        stage_3_ids = {item.op_id for item in stage_3.operations}
        added_ids = {
            item.op_id for item in stage_4.operations if item.op_id not in stage_3_ids
        }
        self.assertEqual(len(added_ids), 16)
        self.assertTrue(
            all(item.startswith("entablature-course-") for item in added_ids)
        )

    def test_bundle_has_exact_binding_coverage_and_no_authority(self) -> None:
        context = _context()
        assemblies = tuple(
            produce_portico_geometry(
                context,
                _villa_spec(facade_id, side_index, PorticoMaturity.DETAILED),
            )
            for side_index, facade_id in enumerate(VILLA_FACADES)
        )
        bundle = merge_produced_assemblies(context, assemblies)
        output_ids = {
            object_id
            for operation in bundle.operations
            for object_id in operation.output_object_ids
        }
        bound_ids = {
            object_id
            for binding in bundle.semantic_bindings
            for object_id in binding.object_ids
        }
        self.assertEqual(bound_ids, output_ids)
        self.assertTrue(all(not operation.input_object_ids for operation in bundle.operations))
        self.assertTrue(all(operation.frame_id == "world" for operation in bundle.operations))
        for payload in (
            context.to_dict(),
            *(item.to_dict() for item in assemblies),
            bundle.to_dict(),
        ):
            for field, value in payload.items():
                if field.endswith("authority"):
                    self.assertFalse(value)

    def test_merge_fails_closed_on_duplicate_assembly(self) -> None:
        context = _context()
        assembly = produce_portico_geometry(
            context,
            _villa_spec("north", 0, PorticoMaturity.DEVELOPED),
        )
        with self.assertRaisesRegex(GeometryProducerError, "assembly ids collide"):
            merge_produced_assemblies(context, (assembly, assembly))

    def test_developed_portico_requires_solved_treads(self) -> None:
        spec = _villa_spec("north", 0, PorticoMaturity.DEVELOPED)
        with self.assertRaisesRegex(PorticoGeometryError, "solved stair run"):
            replace(spec, treads=(), stair_run_going=0.0)


if __name__ == "__main__":
    unittest.main()
