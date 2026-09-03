from __future__ import annotations

import sys
import unittest
from pathlib import Path

from archive.archflow.adapters.v3_legacy_cli import (
    V3LegacyBoundaryError,
    V3LegacyCapabilityRequest,
    V3LegacyCliBridge,
    V3LegacyProviderSpec,
    V3LegacyStatus,
    missing_v3_provider_receipt,
)
from archflow.project.refs import ProjectVersionRef
from archflow.state.model import CanonicalState


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "v3_legacy"
    / "fake_v3_cli.py"
)
PROVIDER_ID = "provider.v3-fixture"
CAPABILITY_ID = "v3.gate.load_path_analysis"
V3_FINGERPRINT = "f" * 64


def _state() -> CanonicalState:
    return CanonicalState(
        ref=ProjectVersionRef(
            "bridge-project",
            4,
            "a" * 64,
        )
    )


def _request(
    state: CanonicalState,
    *,
    capability_id: str = CAPABILITY_ID,
    extra_text: str = "",
) -> V3LegacyCapabilityRequest:
    return V3LegacyCapabilityRequest.create(
        request_id="request-001",
        project_id=state.ref.project_id,
        run_id="run-001",
        workspace_id="candidate-workspace",
        base=state.ref,
        capability_id=capability_id,
        detached_snapshot={
            "phase": "structure",
            "components": [],
            "notes": extra_text,
        },
        obligation={
            "obligation_id": "check-load-path",
            "statement": "Check the detached support relationships.",
        },
        evidence_refs=("project://bridge-project/input/structure.json",),
    )


def _spec(
    mode: str,
    *,
    command: tuple[str, ...] | None = None,
    timeout_seconds: float = 1.0,
    max_input_bytes: int = 64_000,
    max_output_bytes: int = 64_000,
) -> V3LegacyProviderSpec:
    return V3LegacyProviderSpec(
        provider_id=PROVIDER_ID,
        provider_version="fixture-1",
        capability_id=CAPABILITY_ID,
        v3_fingerprint=V3_FINGERPRINT,
        command=command or (sys.executable, str(FIXTURE), mode),
        timeout_seconds=timeout_seconds,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )


class V3LegacyCliIntegrationTests(unittest.TestCase):
    def test_fake_success_is_exact_base_detached_and_nonwriting(self) -> None:
        state = _state()
        before = state
        request = _request(state)

        receipt = V3LegacyCliBridge(_spec("success")).invoke(request)

        self.assertIs(receipt.status, V3LegacyStatus.SUCCESS)
        self.assertEqual(receipt.request.base, state.ref)
        self.assertEqual(receipt.provider_id, PROVIDER_ID)
        self.assertEqual(receipt.capability_id, CAPABILITY_ID)
        self.assertEqual(receipt.v3_fingerprint, V3_FINGERPRINT)
        self.assertRegex(receipt.output_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(receipt.output.kind, "observation")
        self.assertEqual(
            receipt.output.payload["schema"],
            "DetachedLoadPathObservation@1",
        )
        self.assertEqual(state, before)
        payload = receipt.to_dict()
        self.assertFalse(payload["fallback_attempted"])
        self.assertFalse(payload["canonical_write_authority"])
        self.assertFalse(payload["live_world_authority"])
        self.assertFalse(payload["persistence_authority"])

    def test_named_process_failures_preserve_canonical_state(self) -> None:
        cases = (
            (
                _spec("timeout", timeout_seconds=0.05),
                V3LegacyStatus.TIMEOUT,
            ),
            (
                _spec(
                    "success",
                    command=("definitely-missing-v3-provider",),
                ),
                V3LegacyStatus.OFFLINE,
            ),
            (_spec("exit"), V3LegacyStatus.EXIT_ERROR),
            (_spec("malformed"), V3LegacyStatus.MALFORMED),
            (_spec("mismatch"), V3LegacyStatus.MALFORMED),
        )
        for spec, expected in cases:
            with self.subTest(status=expected):
                state = _state()
                before = state
                receipt = V3LegacyCliBridge(spec).invoke(_request(state))
                self.assertIs(receipt.status, expected)
                self.assertIsNone(receipt.output)
                self.assertIsNotNone(receipt.error_code)
                self.assertFalse(receipt.to_dict()["fallback_attempted"])
                self.assertEqual(state, before)

    def test_input_and_output_bounds_fail_before_acceptance(self) -> None:
        state = _state()
        oversized_input = V3LegacyCliBridge(
            _spec(
                "success",
                command=("definitely-missing-v3-provider",),
                max_input_bytes=256,
            )
        ).invoke(_request(state, extra_text="x" * 1_000))
        oversized_output = V3LegacyCliBridge(
            _spec("oversized", max_output_bytes=256)
        ).invoke(_request(state))

        self.assertIs(
            oversized_input.status,
            V3LegacyStatus.INPUT_OVERSIZED,
        )
        self.assertIs(
            oversized_output.status,
            V3LegacyStatus.OUTPUT_OVERSIZED,
        )
        self.assertEqual(state, _state())

    def test_capability_and_missing_provider_never_fallback(self) -> None:
        state = _state()
        request = _request(
            state,
            capability_id="v3.gate.other",
        )

        mismatch = V3LegacyCliBridge(_spec("success")).invoke(request)
        missing = missing_v3_provider_receipt(
            request,
            provider_id="provider.requested",
        )

        self.assertIs(
            mismatch.status,
            V3LegacyStatus.CAPABILITY_MISMATCH,
        )
        self.assertIs(
            missing.status,
            V3LegacyStatus.MISSING_PROVIDER,
        )
        self.assertEqual(missing.command, ())
        self.assertFalse(missing.to_dict()["fallback_attempted"])
        self.assertEqual(state, _state())

    def test_writer_or_live_world_handles_are_rejected_as_input(self) -> None:
        state = _state()
        for forbidden in (
            {"canonical_store": "do-not-cross"},
            {"nested": {"world_handle": "do-not-cross"}},
            {"credentials": {"token": "private"}},
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(
                    V3LegacyBoundaryError,
                    "forbidden handle",
                ):
                    V3LegacyCapabilityRequest.create(
                        request_id="request-unsafe",
                        project_id=state.ref.project_id,
                        run_id="run-001",
                        workspace_id="candidate-workspace",
                        base=state.ref,
                        capability_id=CAPABILITY_ID,
                        detached_snapshot=forbidden,
                        obligation={
                            "obligation_id": "unsafe",
                        },
                    )

    def test_generic_v3_cli_and_v2_entrypoints_are_rejected(self) -> None:
        forbidden_commands = (
            (sys.executable, "-m", "archflow.cli"),
            (
                sys.executable,
                "-c",
                "from archflow.examples import example_planner",
            ),
            (sys.executable, "archflow/decision/v2_stages.py"),
            (sys.executable, "wrapper.py", "compose_building"),
        )
        for command in forbidden_commands:
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    V3LegacyBoundaryError,
                    "forbidden V2, Pack, composer, or generic V3",
                ):
                    _spec("success", command=command)


if __name__ == "__main__":
    unittest.main()
