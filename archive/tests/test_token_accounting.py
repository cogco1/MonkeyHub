import unittest
from dataclasses import replace

from archive.archflow.runtime.token_accounting import (
    AccountingLane,
    AccountingPhase,
    ComparisonStatus,
    IncomparableReason,
    TokenAccountingError,
    TokenAccountingReceipt,
    compare_lane_token_usage,
    compute_failed_token_share,
)


def _receipt(
    lane: AccountingLane,
    *,
    phase: AccountingPhase = AccountingPhase.INITIAL,
    input_tokens: int | None = 100,
    output_tokens: int | None = 20,
    cached_tokens: int | None = 10,
    accepted: bool = True,
    task_identity: str = "task:modern-pavilion",
    input_identity: str = "input:sha256:aaa",
    provider_id: str = "provider:codex",
    model_id: str = "model:gpt-5.6-sol",
    acceptance_basis_identity: str = "acceptance-basis:sha256:bbb",
    acceptance_identity: str = "acceptance:sha256:ccc",
    fidelity_identity: str = "fidelity:sha256:ddd",
    retries: int = 0,
    rejections: int = 0,
) -> TokenAccountingReceipt:
    return TokenAccountingReceipt(
        lane=lane,
        phase=phase,
        task_identity=task_identity,
        input_identity=input_identity,
        provider_id=provider_id,
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        request_bytes=8_146,
        response_bytes=5_099,
        tool_calls=1 if lane is AccountingLane.DIRECT_MCP else 4,
        retries=retries,
        rejections=rejections,
        acceptance_basis_identity=acceptance_basis_identity,
        acceptance_identity=acceptance_identity,
        fidelity_identity=fidelity_identity,
        accepted=accepted,
    )


class TokenAccountingTests(unittest.TestCase):
    def test_one_shot_direct_mcp_uses_fewer_reported_tokens(self) -> None:
        direct = _receipt(
            AccountingLane.DIRECT_MCP,
            input_tokens=80,
            output_tokens=20,
            cached_tokens=0,
        )
        archflow = _receipt(
            AccountingLane.ARCHFLOW,
            input_tokens=180,
            output_tokens=40,
            cached_tokens=30,
        )

        result = compare_lane_token_usage(direct, archflow)

        self.assertIs(result.status, ComparisonStatus.COMPARABLE)
        self.assertIs(result.phase, AccountingPhase.INITIAL)
        self.assertIs(result.lower_token_lane, AccountingLane.DIRECT_MCP)
        self.assertEqual(100, result.direct_total_tokens)
        self.assertEqual(220, result.archflow_total_tokens)
        self.assertEqual(120, result.archflow_minus_direct_tokens)

    def test_revision_archflow_uses_fewer_reported_tokens(self) -> None:
        direct = _receipt(
            AccountingLane.DIRECT_MCP,
            phase=AccountingPhase.REVISION,
            input_tokens=280,
            output_tokens=70,
            cached_tokens=0,
        )
        archflow = _receipt(
            AccountingLane.ARCHFLOW,
            phase=AccountingPhase.REVISION,
            input_tokens=90,
            output_tokens=30,
            cached_tokens=50,
        )

        result = compare_lane_token_usage(direct, archflow)

        self.assertIs(result.status, ComparisonStatus.COMPARABLE)
        self.assertIs(result.phase, AccountingPhase.REVISION)
        self.assertIs(result.lower_token_lane, AccountingLane.ARCHFLOW)
        self.assertEqual(-230, result.archflow_minus_direct_tokens)
        self.assertEqual(70, result.archflow_uncached_token_load)

    def test_missing_direct_telemetry_is_explicitly_incomparable(self) -> None:
        direct = _receipt(
            AccountingLane.DIRECT_MCP,
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
        )
        archflow = _receipt(AccountingLane.ARCHFLOW)

        result = compare_lane_token_usage(direct, archflow)

        self.assertIs(result.status, ComparisonStatus.INCOMPARABLE)
        self.assertIs(
            result.reason,
            IncomparableReason.MISSING_DIRECT_TELEMETRY,
        )
        self.assertIsNone(result.lower_token_lane)
        self.assertIsNone(result.direct_total_tokens)
        self.assertEqual(13_245, direct.payload_bytes)

    def test_identity_or_phase_mismatch_is_incomparable(self) -> None:
        direct = _receipt(AccountingLane.DIRECT_MCP)
        variants = (
            (
                "task",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    task_identity="task:different",
                ),
                IncomparableReason.MISMATCHED_TASK,
            ),
            (
                "input",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    input_identity="input:sha256:different",
                ),
                IncomparableReason.MISMATCHED_INPUT,
            ),
            (
                "model",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    model_id="model:different",
                ),
                IncomparableReason.MISMATCHED_MODEL,
            ),
            (
                "provider",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    provider_id="provider:different",
                ),
                IncomparableReason.MISMATCHED_MODEL,
            ),
            (
                "acceptance_basis",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    acceptance_basis_identity="acceptance-basis:different",
                ),
                IncomparableReason.MISMATCHED_ACCEPTANCE_BASIS,
            ),
            (
                "acceptance_outcome",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    accepted=False,
                ),
                IncomparableReason.MISMATCHED_ACCEPTANCE_OUTCOME,
            ),
            (
                "acceptance_identity",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    acceptance_identity="acceptance:different",
                ),
                IncomparableReason.MISMATCHED_ACCEPTANCE_IDENTITY,
            ),
            (
                "fidelity",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    fidelity_identity="fidelity:different",
                ),
                IncomparableReason.MISMATCHED_FIDELITY,
            ),
            (
                "phase",
                replace(
                    _receipt(AccountingLane.ARCHFLOW),
                    phase=AccountingPhase.REVISION,
                ),
                IncomparableReason.MISMATCHED_PHASE,
            ),
        )
        for label, archflow, reason in variants:
            with self.subTest(label):
                result = compare_lane_token_usage(direct, archflow)
                self.assertIs(result.status, ComparisonStatus.INCOMPARABLE)
                self.assertIs(result.reason, reason)

    def test_failed_token_share_uses_rejected_receipt_tokens(self) -> None:
        receipts = (
            _receipt(
                AccountingLane.ARCHFLOW,
                input_tokens=100,
                output_tokens=20,
                cached_tokens=10,
            ),
            _receipt(
                AccountingLane.ARCHFLOW,
                input_tokens=50,
                output_tokens=10,
                cached_tokens=0,
                accepted=False,
                rejections=1,
            ),
            _receipt(
                AccountingLane.ARCHFLOW,
                input_tokens=30,
                output_tokens=10,
                cached_tokens=5,
                accepted=False,
                retries=1,
                rejections=1,
            ),
        )

        result = compute_failed_token_share(receipts)

        self.assertIs(result.status, ComparisonStatus.COMPARABLE)
        self.assertEqual(3, result.receipt_count)
        self.assertEqual(2, result.failed_receipt_count)
        self.assertEqual(220, result.total_tokens)
        self.assertEqual(100, result.failed_tokens)
        self.assertAlmostEqual(100 / 220, result.share or 0.0)

    def test_failed_share_refuses_missing_token_telemetry(self) -> None:
        direct = _receipt(
            AccountingLane.DIRECT_MCP,
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            accepted=False,
        )

        result = compute_failed_token_share(
            (direct, _receipt(AccountingLane.ARCHFLOW))
        )

        self.assertIs(result.status, ComparisonStatus.INCOMPARABLE)
        self.assertIs(
            result.reason,
            IncomparableReason.MISSING_DIRECT_TELEMETRY,
        )
        self.assertIsNone(result.share)

    def test_cached_tokens_are_a_subset_of_input_tokens(self) -> None:
        with self.assertRaisesRegex(TokenAccountingError, "cannot exceed"):
            _receipt(
                AccountingLane.ARCHFLOW,
                input_tokens=10,
                output_tokens=2,
                cached_tokens=11,
            )


if __name__ == "__main__":
    unittest.main()
