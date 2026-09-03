from __future__ import annotations

import time
import unittest
from dataclasses import FrozenInstanceError

from archive.archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertEvidence,
    ExpertReceiptStatus,
    ExpertRegistry,
    ExpertSnapshot,
    ExpertSpec,
    initial_expert_specs,
)
from archflow.state.model import CanonicalState, GoalContract, Obligation, StateRef
from archflow.state.program import BuildingProgram, FootprintTarget


def _program() -> BuildingProgram:
    return BuildingProgram(
        use="test use",
        footprint=FootprintTarget(width_blocks=12, depth_blocks=10),
        required_spaces=("primary space", "support space"),
        minimum_clear_height=3,
        entrance_count=1,
        circulation_min_width=2,
    )


def _state(obligation_id: str, statement: str, *, version: int = 1) -> CanonicalState:
    return CanonicalState(
        ref=StateRef("run-experts", version),
        goal=GoalContract(prompt="Build a usable test building", must=("usable",)),
        legacy_program_view=_program(),
        open_obligations=(
            Obligation(
                obligation_id=obligation_id,
                statement=statement,
                source_ref="receipt:test",
            ),
        ),
    )


def _evidence() -> tuple[ExpertEvidence, ...]:
    return (
        ExpertEvidence(
            kind="voxel_observation",
            evidence_ref="observation:sha256:abc",
            summary="Detached occupancy and connectivity metrics.",
        ),
    )


def _registry(*, reverse: bool = False) -> ExpertRegistry:
    registry = ExpertRegistry()
    specs = initial_expert_specs()
    if reverse:
        specs = tuple(reversed(specs))
    for spec in specs:
        registry.register(spec, lambda snapshot: ExpertAdvice(summary="reviewed"))
    return registry


class ExpertCapabilityTests(unittest.TestCase):
    def test_different_states_discover_different_experts(self) -> None:
        cases = (
            ("o-use", "use_zones", ("expert.program_use",)),
            ("o-route", "connectivity", ("expert.circulation",)),
            ("o-support", "support", ("expert.constructibility",)),
        )
        for obligation_id, topic, expected in cases:
            with self.subTest(topic=topic):
                state = _state(obligation_id, f"Resolve {topic}")
                snapshot = ExpertSnapshot.detach(
                    state,
                    obligation_topics={obligation_id: topic},
                    evidence=_evidence(),
                )

                discovered = _registry().discover(snapshot)

                self.assertEqual(
                    tuple(item.expert_id for item in discovered),
                    expected,
                )

    def test_snapshot_is_detached_and_handler_cannot_mutate_state(self) -> None:
        state = _state("o-route", "Connect entrance to required spaces")
        topics = {"o-route": "connectivity"}
        evidence = list(_evidence())
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics=topics,
            evidence=evidence,
        )
        before = state
        topics["o-route"] = "support"
        evidence.clear()

        def handler(detached: ExpertSnapshot) -> ExpertAdvice:
            with self.assertRaises(FrozenInstanceError):
                detached.program_json = "{}"  # type: ignore[misc]
            self.assertFalse(hasattr(detached, "committer"))
            self.assertFalse(hasattr(detached, "mcp"))
            self.assertFalse(hasattr(detached, "canonical_state"))
            return ExpertAdvice(summary="Route remains disconnected")

        registry = ExpertRegistry()
        spec = initial_expert_specs()[1]
        registry.register(spec, handler)
        receipt = registry.invoke(spec.expert_id, snapshot)

        self.assertEqual(state, before)
        self.assertEqual(snapshot.obligations[0].topic, "connectivity")
        self.assertEqual(len(snapshot.evidence), 1)
        self.assertIs(receipt.status, ExpertReceiptStatus.ADVICE)
        self.assertEqual(receipt.base_state, state.ref)

    def test_missing_evidence_is_not_fabricated(self) -> None:
        state = _state("o-route", "Connect entrance to required spaces")
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={"o-route": "connectivity"},
        )
        registry = _registry()

        self.assertEqual(registry.discover(snapshot), ())
        receipt = registry.invoke("expert.circulation", snapshot)

        self.assertIs(receipt.status, ExpertReceiptStatus.MISSING_EVIDENCE)
        self.assertIsNone(receipt.advice)
        self.assertEqual(receipt.attempts, 0)
        self.assertEqual(receipt.error_code, "expert.missing_evidence")

    def test_exception_is_fail_open_and_retries_are_bounded(self) -> None:
        state = _state("o-use", "Resolve program fit")
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={"o-use": "program"},
        )
        calls = 0

        def failing_handler(_: ExpertSnapshot) -> ExpertAdvice:
            nonlocal calls
            calls += 1
            raise RuntimeError("x" * 2_000)

        registry = ExpertRegistry()
        registry.register(
            ExpertSpec(
                expert_id="expert.failing",
                description="Test failure isolation.",
                topics=frozenset({"program"}),
                max_attempts=2,
            ),
            failing_handler,
        )

        receipt = registry.invoke("expert.failing", snapshot)

        self.assertEqual(calls, 2)
        self.assertIs(receipt.status, ExpertReceiptStatus.ERROR)
        self.assertEqual(receipt.attempts, 2)
        self.assertIsNone(receipt.advice)
        self.assertIsNotNone(receipt.message)
        self.assertLessEqual(len(receipt.message or ""), 500)

    def test_timeout_returns_without_waiting_for_handler_completion(self) -> None:
        state = _state("o-use", "Resolve program fit")
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={"o-use": "program"},
        )

        def slow_handler(_: ExpertSnapshot) -> ExpertAdvice:
            time.sleep(0.2)
            return ExpertAdvice(summary="too late")

        registry = ExpertRegistry()
        registry.register(
            ExpertSpec(
                expert_id="expert.slow",
                description="Test bounded timeout.",
                topics=frozenset({"program"}),
                timeout_seconds=0.01,
            ),
            slow_handler,
        )
        started = time.monotonic()

        receipt = registry.invoke("expert.slow", snapshot)

        self.assertLess(time.monotonic() - started, 0.15)
        self.assertIs(receipt.status, ExpertReceiptStatus.TIMEOUT)
        self.assertEqual(receipt.attempts, 1)
        self.assertIsNone(receipt.advice)

    def test_oversized_output_becomes_bounded_receipt(self) -> None:
        state = _state("o-use", "Resolve program fit")
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={"o-use": "program"},
        )
        registry = ExpertRegistry()
        registry.register(
            ExpertSpec(
                expert_id="expert.verbose",
                description="Test output bound.",
                topics=frozenset({"program"}),
                max_output_chars=128,
            ),
            lambda _: ExpertAdvice(summary="x" * 1_000),
        )

        receipt = registry.invoke("expert.verbose", snapshot)

        self.assertIs(receipt.status, ExpertReceiptStatus.OVERSIZED)
        self.assertIsNone(receipt.advice)
        self.assertEqual(receipt.message, "output exceeds 128 characters")

    def test_registration_order_is_not_execution_order(self) -> None:
        state = CanonicalState(
            ref=StateRef("run-experts", 3),
            goal=GoalContract(prompt="Build a usable test building", must=("usable",)),
            legacy_program_view=_program(),
            open_obligations=(
                Obligation("o-use", "Resolve use zones", "receipt:use"),
                Obligation("o-route", "Resolve route", "receipt:route"),
                Obligation("o-support", "Resolve support", "receipt:support"),
            ),
        )
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={
                "o-use": "use_zones",
                "o-route": "connectivity",
                "o-support": "support",
            },
            evidence=_evidence(),
        )

        forward = tuple(item.expert_id for item in _registry().discover(snapshot))
        reverse = tuple(
            item.expert_id for item in _registry(reverse=True).discover(snapshot)
        )

        self.assertEqual(forward, reverse)
        self.assertEqual(forward, tuple(sorted(forward)))

    def test_new_expert_requires_only_registration(self) -> None:
        state = _state("o-light", "Resolve daylight quality")
        snapshot = ExpertSnapshot.detach(
            state,
            obligation_topics={"o-light": "daylight"},
        )
        registry = ExpertRegistry()
        registry.register(
            ExpertSpec(
                expert_id="expert.daylight",
                description="Reviews daylight evidence.",
                topics=frozenset({"daylight"}),
            ),
            lambda _: ExpertAdvice(summary="Add a daylight observation"),
        )

        self.assertEqual(
            tuple(item.expert_id for item in registry.discover(snapshot)),
            ("expert.daylight",),
        )


if __name__ == "__main__":
    unittest.main()
