from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertRegistry,
    ExpertSpec,
)
from archflow.runtime import (
    PlanBinding,
    build_environment_expert_snapshot,
    compile_environment_obligations,
    guard_identical_retry,
    load_environment_feedback_trace,
    load_tool_environment_observation,
    write_environment_feedback_trace,
    write_tool_failure_link,
    write_tool_invocation_intent,
)
from archflow.state import StateRef


PLAN = {
    "version": 1,
    "operation": "preview-current-candidate",
    "candidate_ref": "candidate://current",
}


class EnvironmentFeedbackTests(unittest.TestCase):
    def test_exact_failure_becomes_obligation_and_open_expert_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = StateRef("run-environment-feedback", 0)
            intent = write_tool_invocation_intent(
                root / "tool-invocation-intent.json",
                base_state=base,
                workspace_id="workspace-1",
                capability_id="tool.preview",
                plan=PLAN,
            )
            failure = _write_failure(root, base)
            link = write_tool_failure_link(
                root / "tool-invocation-failure-link.json",
                invocation_intent_path=intent,
                failure_receipt_path=failure,
            )
            observation = load_tool_environment_observation(
                failure,
                invocation_intent_path=intent,
                failure_link_path=link,
            )
            obligations = compile_environment_obligations(observation)
            snapshot = build_environment_expert_snapshot(
                observation,
                obligations,
                program_json='{"schema":"DesignProgram@1","project":"current"}',
            )
            registry = ExpertRegistry()
            for expert_id, topics in (
                ("expert.site_relationship", {"support", "terrain"}),
                ("expert.support", {"support"}),
                ("expert.unrelated", {"circulation"}),
            ):
                registry.register(
                    ExpertSpec(
                        expert_id=expert_id,
                        description="Read-only test capability.",
                        topics=frozenset(topics),
                        required_evidence_kinds=(
                            frozenset({"environment_observation"})
                            if "support" in topics
                            else frozenset()
                        ),
                    ),
                    _advice,
                )

            discovered = registry.discover(snapshot)

            self.assertEqual(observation.plan_binding, PlanBinding.EXACT)
            self.assertTrue(observation.actionable)
            self.assertEqual(
                [item.finding_code for item in obligations],
                ["environment.terrain.support_unresolved"],
            )
            self.assertEqual(
                {item.expert_id for item in discovered},
                {"expert.site_relationship", "expert.support"},
            )
            self.assertNotIn("foundation", obligations[0].statement.lower())
            self.assertNotIn("hard", obligations[0].statement.lower())

    def test_unbound_receipt_is_archival_not_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            failure = _write_failure(
                root,
                StateRef("run-environment-feedback", 0),
            )

            observation = load_tool_environment_observation(failure)

            self.assertEqual(observation.plan_binding, PlanBinding.UNPROVEN)
            self.assertFalse(observation.actionable)
            self.assertEqual(compile_environment_obligations(observation), ())

    def test_identical_retry_stops_without_design_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = StateRef("run-environment-feedback", 0)
            intent = write_tool_invocation_intent(
                root / "tool-invocation-intent.json",
                base_state=base,
                workspace_id="workspace-1",
                capability_id="tool.preview",
                plan=PLAN,
            )
            failure = _write_failure(root, base)
            observation = load_tool_environment_observation(
                failure,
                invocation_intent_path=intent,
                failure_link_path=write_tool_failure_link(
                    root / "tool-invocation-failure-link.json",
                    invocation_intent_path=intent,
                    failure_receipt_path=failure,
                ),
            )
            obligations = compile_environment_obligations(observation)
            stop = guard_identical_retry(
                (observation,),
                base_state=base,
                plan=PLAN,
            )
            self.assertIsNotNone(stop)
            assert stop is not None
            trace_path = write_environment_feedback_trace(
                root / "feedback-trace.json",
                observation=observation,
                obligations=obligations,
                discovered_expert_ids=(
                    "expert.site_relationship",
                    "expert.support",
                ),
                architect_action="unresolved",
                architect_rationale=(
                    "Preserve alternatives until evidence distinguishes them."
                ),
                retry_stop=stop,
            )

            trace = load_environment_feedback_trace(trace_path)

            self.assertIsNone(trace["hard_usability_verdict"])
            self.assertIsNone(trace["canonical_state_transition"])
            self.assertEqual(
                trace["retry_stop"]["code"],
                "environment.retry.identical_failed_plan",
            )
            self.assertEqual(
                trace["architect_decision"]["allowed_actions"],
                ["revise", "replace", "unresolved"],
            )


def _write_failure(root: Path, base: StateRef) -> Path:
    payload = {
        "schema": "MinecraftMcpFailureReceipt@1",
        "code": "MCP_ADAPTER_FAILED",
        "message": "preview rejected before execution",
        "phase": "preview",
        "base_state": {"run_id": base.run_id, "version": base.version},
        "workspace_id": "workspace-1",
        "canonical_state_mutated": False,
        "world_may_have_changed": False,
        "structured": {
            "success": False,
            "error": "Preview reports unresolved support.",
            "issues": [
                {
                    "cuboid": "candidate-base",
                    "issue": "floating",
                    "gapBelow": 2,
                    "suggestedY": 0,
                }
            ],
        },
    }
    path = root / "tool-failure.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _advice(_snapshot) -> ExpertAdvice:
    return ExpertAdvice(
        summary="Compare current project alternatives without editing state.",
    )


if __name__ == "__main__":
    unittest.main()
