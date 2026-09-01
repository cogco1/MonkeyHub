from __future__ import annotations

from hashlib import sha256
import unittest

import archflow.capabilities.stage_component_coverage as legacy_component_lineage
import archflow.validation.component_lineage as canonical_component_lineage
from archflow.validation.component_lineage import (
    ComponentCoverageSummary,
    OperationDisposition,
    OperationLineageResolution,
    PredecessorOperationDisposition,
    StageComponentCoverageError,
    StageComponentCoverageStatus,
    StageOperation,
    StageOperationRef,
    compile_stage_component_coverage,
)


def fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def operation(component_id: str, operation_id: str, value: str) -> StageOperation:
    return StageOperation(
        ref=StageOperationRef(component_id, operation_id),
        fingerprint=fingerprint(value),
    )


def compile_one(
    *,
    predecessor: StageOperation,
    successor: StageOperation | None,
    disposition: PredecessorOperationDisposition,
    lineage: OperationLineageResolution | None,
):
    return compile_stage_component_coverage(
        predecessor_stage_id="stage-3",
        successor_stage_id="stage-4",
        predecessor_operations=(predecessor,),
        successor_operations=(() if successor is None else (successor,)),
        lineage_resolutions=(() if lineage is None else (lineage,)),
        dispositions=(disposition,),
    )


class StageComponentCoverageTests(unittest.TestCase):
    def test_legacy_facade_reexports_canonical_objects_by_identity(self) -> None:
        for name in legacy_component_lineage.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(canonical_component_lineage, name),
                    getattr(legacy_component_lineage, name),
                )

    def test_reference_fixture_preserves_schema_behavior_and_digests(self) -> None:
        predecessor = operation("roof", "tile-layout", "same")
        receipt = compile_one(
            predecessor=predecessor,
            successor=predecessor,
            disposition=PredecessorOperationDisposition(
                predecessor_ref=predecessor.ref,
                disposition=OperationDisposition.VERIFIED_UNCHANGED,
                relational_revalidation_refs=(
                    "revalidation:roof/tile-layout",
                ),
            ),
            lineage=OperationLineageResolution(
                predecessor_ref=predecessor.ref,
                successor_refs=(predecessor.ref,),
                lineage_refs=("lineage:roof/tile-layout",),
            ),
        )

        self.assertEqual(
            "4e2fcb96c7a661b78cf0453e2e538c19c9672c52a50b8bc03a1c3c1c6e29902c",
            receipt.predecessor_denominator_digest,
        )
        self.assertEqual(
            "2643bc301cb186e3ac7070b9de927d65c740a8e714323f0bf3d86ea6722776c5",
            receipt.receipt_digest,
        )
        self.assertEqual("StageComponentCoverageReceipt@1", receipt.SCHEMA)
        self.assertEqual(
            "StageComponentCoverageReceipt@1",
            receipt.to_dict()["schema"],
        )
        self.assertIs(receipt.status, StageComponentCoverageStatus.PASS)
        self.assertEqual(
            ("tile-layout",),
            receipt.component_summaries[0].verified_unchanged_operation_ids,
        )

    def test_parthenon_shape_covers_all_11_components_and_271_operations(
        self,
    ) -> None:
        predecessors: list[StageOperation] = []
        successors: list[StageOperation] = []
        lineages: list[OperationLineageResolution] = []
        dispositions: list[PredecessorOperationDisposition] = []
        operation_index = 0

        for component_index in range(11):
            operation_total = 25 if component_index < 7 else 24
            component_id = f"component-{component_index:02d}"
            for _ in range(operation_total):
                operation_id = f"operation-{operation_index:03d}"
                ref = StageOperationRef(component_id, operation_id)
                predecessor = StageOperation(
                    ref=ref,
                    fingerprint=fingerprint(f"stage-3:{operation_index}"),
                )
                predecessors.append(predecessor)

                if operation_index == 270:
                    dispositions.append(
                        PredecessorOperationDisposition(
                            predecessor_ref=ref,
                            disposition=(
                                OperationDisposition.PARKED_WITH_REASON
                            ),
                            evidence_refs=(
                                "evidence:stage-4/sculpture-deferred",
                            ),
                            parked_reason=(
                                "Sculptural carving is explicitly outside this pass."
                            ),
                            blocking=False,
                        )
                    )
                    operation_index += 1
                    continue

                refined = operation_index % 9 == 0
                successor = StageOperation(
                    ref=ref,
                    fingerprint=(
                        fingerprint(f"stage-4:{operation_index}")
                        if refined
                        else predecessor.fingerprint
                    ),
                )
                successors.append(successor)
                lineages.append(
                    OperationLineageResolution(
                        predecessor_ref=ref,
                        successor_refs=(ref,),
                        lineage_refs=(
                            f"lineage:stage-3-to-4/{operation_index:03d}",
                        ),
                    )
                )
                dispositions.append(
                    PredecessorOperationDisposition(
                        predecessor_ref=ref,
                        disposition=(
                            OperationDisposition.REFINED
                            if refined
                            else OperationDisposition.VERIFIED_UNCHANGED
                        ),
                        evidence_refs=(
                            (f"evidence:stage-4/detail/{operation_index:03d}",)
                            if refined
                            else ()
                        ),
                        relational_revalidation_refs=(
                            ()
                            if refined
                            else (
                                f"revalidation:stage-4/relations/{operation_index:03d}",
                            )
                        ),
                    )
                )
                operation_index += 1

        first = compile_stage_component_coverage(
            predecessor_stage_id="stage-3",
            successor_stage_id="stage-4",
            predecessor_operations=predecessors,
            successor_operations=successors,
            lineage_resolutions=lineages,
            dispositions=dispositions,
        )
        second = compile_stage_component_coverage(
            predecessor_stage_id="stage-3",
            successor_stage_id="stage-4",
            predecessor_operations=reversed(predecessors),
            successor_operations=reversed(successors),
            lineage_resolutions=reversed(lineages),
            dispositions=reversed(dispositions),
        )

        self.assertEqual(11, first.component_count)
        self.assertEqual(271, first.operation_count)
        self.assertEqual(
            271,
            sum(item.operation_count for item in first.component_summaries),
        )
        self.assertIs(first.status, StageComponentCoverageStatus.PASS)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.receipt_digest, second.receipt_digest)
        self.assertEqual(64, len(first.predecessor_denominator_digest))
        self.assertEqual(64, len(first.receipt_digest))
        self.assertFalse(first.to_dict()["stage_acceptance_authority"])
        self.assertFalse(first.to_dict()["canonical_write_authority"])

    def test_missing_and_unknown_dispositions_fail_exact_denominator(self) -> None:
        first = operation("roof", "tile-layout", "a")
        second = operation("eaves", "drip-edge", "b")
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "exact predecessor denominator.*missing=eaves/drip-edge.*unknown=wall/paint",
        ):
            compile_stage_component_coverage(
                predecessor_stage_id="stage-3",
                successor_stage_id="stage-4",
                predecessor_operations=(first, second),
                successor_operations=(),
                lineage_resolutions=(),
                dispositions=(
                    PredecessorOperationDisposition(
                        predecessor_ref=first.ref,
                        disposition=OperationDisposition.PARKED_WITH_REASON,
                        evidence_refs=("evidence:park/roof",),
                        parked_reason="Roof detailing deferred with evidence.",
                        blocking=False,
                    ),
                    PredecessorOperationDisposition(
                        predecessor_ref=StageOperationRef("wall", "paint"),
                        disposition=OperationDisposition.PARKED_WITH_REASON,
                        evidence_refs=("evidence:park/wall",),
                        parked_reason="Unknown operation must not enter coverage.",
                        blocking=False,
                    ),
                ),
            )

    def test_duplicate_disposition_is_rejected(self) -> None:
        predecessor = operation("roof", "tile-layout", "a")
        parked = PredecessorOperationDisposition(
            predecessor_ref=predecessor.ref,
            disposition=OperationDisposition.PARKED_WITH_REASON,
            evidence_refs=("evidence:park/roof",),
            parked_reason="Deferred with recorded evidence.",
            blocking=False,
        )
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "dispositions contains duplicates",
        ):
            compile_stage_component_coverage(
                predecessor_stage_id="stage-3",
                successor_stage_id="stage-4",
                predecessor_operations=(predecessor,),
                successor_operations=(),
                lineage_resolutions=(),
                dispositions=(parked, parked),
            )

    def test_refined_requires_lineage_successor_and_evidence(self) -> None:
        predecessor = operation("roof", "tile-layout", "before")
        successor = operation("roof", "tile-layout", "after")
        refined = PredecessorOperationDisposition(
            predecessor_ref=predecessor.ref,
            disposition=OperationDisposition.REFINED,
            evidence_refs=("evidence:detail/roof",),
        )
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "REFINED requires lineage resolution",
        ):
            compile_one(
                predecessor=predecessor,
                successor=None,
                disposition=refined,
                lineage=None,
            )

        unknown_ref = StageOperationRef("roof", "unknown-successor")
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "unknown successors",
        ):
            compile_one(
                predecessor=predecessor,
                successor=successor,
                disposition=refined,
                lineage=OperationLineageResolution(
                    predecessor_ref=predecessor.ref,
                    successor_refs=(unknown_ref,),
                    lineage_refs=("lineage:roof/refinement",),
                ),
            )

        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "REFINED requires evidence or revalidation refs",
        ):
            PredecessorOperationDisposition(
                predecessor_ref=predecessor.ref,
                disposition=OperationDisposition.REFINED,
            )

    def test_verified_unchanged_requires_equal_fingerprint_and_relation_check(
        self,
    ) -> None:
        predecessor = operation("eaves", "projection", "before")
        changed_successor = operation("eaves", "projection", "after")
        lineage = OperationLineageResolution(
            predecessor_ref=predecessor.ref,
            successor_refs=(predecessor.ref,),
            lineage_refs=("lineage:eaves/projection",),
        )
        unchanged = PredecessorOperationDisposition(
            predecessor_ref=predecessor.ref,
            disposition=OperationDisposition.VERIFIED_UNCHANGED,
            relational_revalidation_refs=(
                "revalidation:eaves/roof-wall-relation",
            ),
        )
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "unchanged fingerprint",
        ):
            compile_one(
                predecessor=predecessor,
                successor=changed_successor,
                disposition=unchanged,
                lineage=lineage,
            )

        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "requires relational revalidation refs",
        ):
            PredecessorOperationDisposition(
                predecessor_ref=predecessor.ref,
                disposition=OperationDisposition.VERIFIED_UNCHANGED,
                evidence_refs=("evidence:model/file-exists",),
            )

    def test_unbound_successor_operation_is_rejected(self) -> None:
        predecessor = operation("roof", "tile-layout", "same")
        bound_successor = operation("roof", "tile-layout", "same")
        unbound_successor = operation("roof", "unbound-geometry", "new")
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "unbound operations: roof/unbound-geometry",
        ):
            compile_stage_component_coverage(
                predecessor_stage_id="stage-3",
                successor_stage_id="stage-4",
                predecessor_operations=(predecessor,),
                successor_operations=(bound_successor, unbound_successor),
                lineage_resolutions=(
                    OperationLineageResolution(
                        predecessor_ref=predecessor.ref,
                        successor_refs=(bound_successor.ref,),
                        lineage_refs=("lineage:roof/tile-layout",),
                    ),
                ),
                dispositions=(
                    PredecessorOperationDisposition(
                        predecessor_ref=predecessor.ref,
                        disposition=(
                            OperationDisposition.VERIFIED_UNCHANGED
                        ),
                        relational_revalidation_refs=(
                            "revalidation:roof/tile-layout",
                        ),
                    ),
                ),
            )

    def test_blocking_park_forces_component_and_overall_fail(self) -> None:
        predecessor = operation("roof", "weathering", "before")
        receipt = compile_one(
            predecessor=predecessor,
            successor=None,
            disposition=PredecessorOperationDisposition(
                predecessor_ref=predecessor.ref,
                disposition=OperationDisposition.PARKED_WITH_REASON,
                evidence_refs=("evidence:park/weathering",),
                parked_reason="Weathering evidence is unavailable.",
                blocking=True,
            ),
            lineage=None,
        )

        self.assertIs(receipt.status, StageComponentCoverageStatus.FAIL)
        self.assertIs(
            receipt.component_summaries[0].status,
            StageComponentCoverageStatus.FAIL,
        )
        self.assertEqual(
            ("weathering",),
            receipt.component_summaries[0].blocking_parked_operation_ids,
        )

    def test_park_requires_reason_evidence_and_explicit_blocking(self) -> None:
        ref = StageOperationRef("sculpture", "carving")
        for kwargs, message in (
            (
                {
                    "evidence_refs": ("evidence:park/sculpture",),
                    "blocking": False,
                },
                "parked_reason",
            ),
            (
                {
                    "parked_reason": "Deferred sculpture.",
                    "blocking": False,
                },
                "requires evidence_refs",
            ),
            (
                {
                    "evidence_refs": ("evidence:park/sculpture",),
                    "parked_reason": "Deferred sculpture.",
                },
                "explicit blocking boolean",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                (TypeError, StageComponentCoverageError),
                message,
            ):
                PredecessorOperationDisposition(
                    predecessor_ref=ref,
                    disposition=OperationDisposition.PARKED_WITH_REASON,
                    **kwargs,
                )

    def test_parked_operation_cannot_hide_successor_lineage(self) -> None:
        predecessor = operation("sculpture", "carving", "before")
        parked = PredecessorOperationDisposition(
            predecessor_ref=predecessor.ref,
            disposition=OperationDisposition.PARKED_WITH_REASON,
            evidence_refs=("evidence:park/sculpture",),
            parked_reason="Sculpture intentionally deferred.",
            blocking=False,
        )
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "cannot carry lineage resolution",
        ):
            compile_one(
                predecessor=predecessor,
                successor=predecessor,
                disposition=parked,
                lineage=OperationLineageResolution(
                    predecessor_ref=predecessor.ref,
                    successor_refs=(predecessor.ref,),
                    lineage_refs=("lineage:sculpture/carving",),
                ),
            )

    def test_component_summary_rejects_omission_or_unknown_operation(self) -> None:
        with self.assertRaisesRegex(
            StageComponentCoverageError,
            "omits or introduces",
        ):
            ComponentCoverageSummary(
                component_id="roof",
                predecessor_operation_ids=("ridge", "tiles"),
                refined_operation_ids=("ridge",),
                verified_unchanged_operation_ids=("unknown",),
                parked_operation_ids=(),
                blocking_parked_operation_ids=(),
                status=StageComponentCoverageStatus.PASS,
            )


if __name__ == "__main__":
    unittest.main()
