from __future__ import annotations

import copy
import unittest

from archflow.control.function_diagnostics import (
    FUNCTION_DIAGNOSTIC_PALETTE,
    NO_FUNCTION_CONTRACT,
    FunctionDiagnosticColor,
    FunctionDiagnosticEntry,
    FunctionDiagnosticError,
    FunctionDiagnosticProjection,
    FunctionStatus,
    compile_function_diagnostic_projection,
)


def entry(
    component: str,
    object_id: str,
    status: FunctionStatus,
) -> FunctionDiagnosticEntry:
    contract_ref = (
        NO_FUNCTION_CONTRACT
        if status is FunctionStatus.FUNCTION_ORPHAN
        else f"function-contract:{component}"
    )
    color = FUNCTION_DIAGNOSTIC_PALETTE[status]
    return FunctionDiagnosticEntry(
        component_ref=f"design-component:{component}",
        geometry_object_ids=(object_id,),
        function_ledger_ref="function-ledger:stage-2-fixture",
        function_contract_ref=contract_ref,
        stage_claim_ref="stage-claim:stage-2-hold",
        status=status,
        diagnostic_color=None if color is None else color.value,
    )


class FunctionDiagnosticProjectionTests(unittest.TestCase):
    def test_palette_and_round_trip_are_deterministic_and_authority_free(
        self,
    ) -> None:
        self.assertEqual(
            FunctionDiagnosticColor.FUNCTION_ORPHAN,
            FUNCTION_DIAGNOSTIC_PALETTE[FunctionStatus.FUNCTION_ORPHAN],
        )
        self.assertEqual(
            FunctionDiagnosticColor.OPEN,
            FUNCTION_DIAGNOSTIC_PALETTE[FunctionStatus.OPEN],
        )
        self.assertEqual(
            FunctionDiagnosticColor.FAIL,
            FUNCTION_DIAGNOSTIC_PALETTE[FunctionStatus.FAIL],
        )
        self.assertIsNone(
            FUNCTION_DIAGNOSTIC_PALETTE[FunctionStatus.SATISFIED]
        )

        projection = compile_function_diagnostic_projection(
            projection_id="function-status-stage-2",
            entries=(
                entry("stairs", "stairs-obj", FunctionStatus.OPEN),
                entry(
                    "pediment-support",
                    "pediment-support-obj",
                    FunctionStatus.FUNCTION_ORPHAN,
                ),
                entry("wall", "wall-obj", FunctionStatus.SATISFIED),
            ),
        )
        restored = FunctionDiagnosticProjection.from_dict(projection.to_dict())
        self.assertEqual(projection, restored)
        self.assertEqual(projection.projection_digest, restored.projection_digest)
        payload = projection.to_dict()
        self.assertEqual("PRESENTATION_ONLY", payload["projection_mode"])
        for field in (
            "material_override",
            "readback_authority",
            "stage_acceptance_authority",
            "canonical_write_authority",
        ):
            self.assertFalse(payload[field])
        self.assertEqual(
            [
                "design-component:pediment-support",
                "design-component:stairs",
                "design-component:wall",
            ],
            [item["component_ref"] for item in payload["entries"]],
        )

    def test_duplicate_component_and_object_ownership_are_rejected(self) -> None:
        first = entry("stairs", "shared-obj", FunctionStatus.OPEN)
        duplicate_component = entry("stairs", "other-obj", FunctionStatus.FAIL)
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "duplicate functional diagnostic component ownership",
        ):
            compile_function_diagnostic_projection(
                projection_id="duplicate-component",
                entries=(first, duplicate_component),
            )

        duplicate_object = entry("landing", "shared-obj", FunctionStatus.FAIL)
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "duplicate functional diagnostic object ownership",
        ):
            compile_function_diagnostic_projection(
                projection_id="duplicate-object",
                entries=(first, duplicate_object),
            )

    def test_status_colour_and_orphan_contract_policy_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "SATISFIED functional diagnostics cannot carry a colour",
        ):
            FunctionDiagnosticEntry(
                component_ref="design-component:wall",
                geometry_object_ids=("wall-obj",),
                function_ledger_ref="function-ledger:fixture",
                function_contract_ref="function-contract:wall",
                stage_claim_ref="stage-claim:stage-2",
                status=FunctionStatus.SATISFIED,
                diagnostic_color="#FF00FF",
            )

        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "not the fixed palette value",
        ):
            FunctionDiagnosticEntry(
                component_ref="design-component:stairs",
                geometry_object_ids=("stairs-obj",),
                function_ledger_ref="function-ledger:fixture",
                function_contract_ref="function-contract:stairs",
                stage_claim_ref="stage-claim:stage-2",
                status=FunctionStatus.OPEN,
                diagnostic_color="#123456",
            )

        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "FUNCTION_ORPHAN contract must be NONE",
        ):
            FunctionDiagnosticEntry(
                component_ref="design-component:pediment-support",
                geometry_object_ids=("support-obj",),
                function_ledger_ref="function-ledger:fixture",
                function_contract_ref="function-contract:invented",
                stage_claim_ref="stage-claim:stage-2",
                status=FunctionStatus.FUNCTION_ORPHAN,
                diagnostic_color="#FF00FF",
            )

    def test_compiler_revalidates_forged_frozen_entries(self) -> None:
        forged = entry("stairs", "stairs-obj", FunctionStatus.OPEN)
        object.__setattr__(forged, "diagnostic_color", "#123456")
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "not the fixed palette value",
        ):
            compile_function_diagnostic_projection(
                projection_id="forged-entry",
                entries=(forged,),
            )

        forged_orphan = entry(
            "pediment-support",
            "support-obj",
            FunctionStatus.FUNCTION_ORPHAN,
        )
        object.__setattr__(
            forged_orphan,
            "function_contract_ref",
            "function-contract:invented",
        )
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "FUNCTION_ORPHAN contract must be NONE",
        ):
            compile_function_diagnostic_projection(
                projection_id="forged-orphan-contract",
                entries=(forged_orphan,),
            )

    def test_round_trip_rejects_authority_colour_contract_and_digest_drift(
        self,
    ) -> None:
        projection = compile_function_diagnostic_projection(
            projection_id="function-status-stage-2",
            entries=(
                entry(
                    "pediment-support",
                    "support-obj",
                    FunctionStatus.FUNCTION_ORPHAN,
                ),
            ),
        )

        authority = copy.deepcopy(projection.to_dict())
        authority["material_override"] = True
        with self.assertRaisesRegex(FunctionDiagnosticError, "acquired authority"):
            FunctionDiagnosticProjection.from_dict(authority)

        colour = copy.deepcopy(projection.to_dict())
        colour["entries"][0]["diagnostic_color"] = "#00FF00"
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "not the fixed palette value",
        ):
            FunctionDiagnosticProjection.from_dict(colour)

        contract = copy.deepcopy(projection.to_dict())
        contract["entries"][0]["function_contract_ref"] = (
            "function-contract:invented"
        )
        with self.assertRaisesRegex(
            FunctionDiagnosticError,
            "FUNCTION_ORPHAN contract must be NONE",
        ):
            FunctionDiagnosticProjection.from_dict(contract)

        digest = copy.deepcopy(projection.to_dict())
        digest["projection_digest"] = "0" * 64
        with self.assertRaisesRegex(FunctionDiagnosticError, "digest changed"):
            FunctionDiagnosticProjection.from_dict(digest)


if __name__ == "__main__":
    unittest.main()
